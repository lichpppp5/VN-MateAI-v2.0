# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 Dương Thanh Lịch — VN-MateAI. See LICENSE and NOTICE.
"""
core/meta_architect.py
======================
Dynamic Skill Synthesizer — the self-expanding runtime for VN-MateAI.

Responsibilities:
  - Accept a natural-language intent description + failed-execution context.
  - Craft a specialised code-generation prompt and call the LLM.
  - Strip Markdown fences from the response to obtain raw Python source.
  - Validate the exact file content: syntax, @export_skill structure, no code
    running at import, no clash with existing tool names, AST security audit.
  - Write a NEW file under skills/ (never overwrite), hot-reload, confirm the
    tools registered (roll back otherwise). Without auto_execute the code is
    parked in skills/pending/ for an administrator to review.

Design Notes:
  - Uses the same OpenAI-compatible SDK as llm_engine but with a separate
    system prompt tuned for code generation, NOT conversation.
  - PluginManager is imported lazily (inside methods) to break circular deps.
"""

from __future__ import annotations

import ast
import logging
import re
import sys
import textwrap
import unicodedata
from pathlib import Path
from typing import Any, Dict, List, Optional

from mateai.config.loader import settings

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# System prompt template for code synthesis
# ---------------------------------------------------------------------------

_CODE_GEN_SYSTEM_PROMPT = textwrap.dedent("""\
    Bạn là một Python Core Engineer chuyên viết automation cho Windows.
    Nhiệm vụ của bạn là tạo ra một Python module ĐỘC LẬP, hoàn chỉnh, không có placeholder.

    YÊU CẦU BẮT BUỘC:
    1. Module phải import `export_skill` từ `core.plugin_manager`.
    2. Mỗi hàm công khai phải được đánh dấu bằng decorator @export_skill(name, description, parameters_schema).
    3. Sử dụng Type Hinting đầy đủ (typing module).
    4. Xử lý exception đúng cách — không để exception truyền thẳng ra ngoài.
    5. Chỉ dùng thư viện trong requirements.txt hoặc thư viện stdlib của Python.
    6. TUYỆT ĐỐI KHÔNG dùng pyautogui, opencv, screenshot, hay bất kỳ thư viện capture màn hình nào.
    7. Trả về DUY NHẤT một code block markdown: ```python ... ```
    8. Không giải thích, không thêm text ngoài code block.
    9. `description` của @export_skill viết bằng TIẾNG VIỆT CÓ DẤU: nói rõ kỹ năng làm
       gì, rồi liệt kê 3–5 cách người dùng hay nói để yêu cầu việc này (vd: "mở bài hát
       ...", "bật nhạc ...", "phát nhạc trên YouTube") và từ khoá tiếng Anh tương ứng.
       Trợ lý chọn kỹ năng theo mô tả này — mô tả chỉ bằng tiếng Anh thì câu hỏi tiếng
       Việt sẽ không bao giờ tìm thấy kỹ năng.

    LUẬT AN NINH — mã vi phạm bị TỪ CHỐI, không được cài:
    10. CẤM import: ctypes, importlib, marshal, pickle. CẤM gọi: eval, exec, compile, __import__,
        globals, locals, os.system, os.popen, os.spawn*, os.exec*, os.kill, shutil.rmtree,
        và mọi lời gọi có shell=True. Cần chạy chương trình thì dùng subprocess.run([...danh sách...]).
    11. CẤP MODULE (ngoài hàm) CHỈ được có: import, hằng số / biến gán, def, class. KHÔNG có if, try,
        for, while, with hay lời gọi hàm ở cấp module — import tuỳ chọn / theo hệ điều hành đặt BÊN TRONG hàm.
    12. Phím media (phát / tạm dừng / bài tiếp), âm lượng, mở ứng dụng đã có công cụ sẵn (media_control,
        set_system_volume, open_application) — không viết lại.
    13. Kỹ năng chấm điểm / xếp hạng / lọc NGƯỜI (nhân viên, ứng viên) KHÔNG được dùng giới tính, tuổi, dân tộc, tôn giáo,
        quê quán, tình trạng hôn nhân, thai sản, khuyết tật làm tiêu chí; kỹ năng chỉ TRẢ SỐ LIỆU, quyết định do người.

    PLATFORM: Windows 10/11 x64, Python 3.10+
""")

# Regex to extract ```python ... ``` blocks (non-greedy, DOTALL)
_CODE_FENCE_PATTERN = re.compile(r"```python\s*\n(.*?)```", re.DOTALL)


# ---------------------------------------------------------------------------
# MetaArchitect
# ---------------------------------------------------------------------------


class MetaArchitect:
    """
    Synthesises new Python skill modules on demand using an LLM.
    """

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def synthesize_skill(
        self,
        intent_description: str,
        failed_context: Optional[Dict[str, Any]] = None,
    ) -> str:
        """
        Ask the LLM to write a new Python skill module.

        Args:
            intent_description: Human-language description of what the skill must do.
            failed_context: Optional dict with keys like {"error", "attempted_skill",
                            "arguments"} to give the LLM correction context.

        Returns:
            Raw Python source code as a string.

        Raises:
            ValueError: If the LLM response contains no parseable code block.
            RuntimeError: If the LLM API call fails.
        """
        user_message = self._build_user_message(intent_description, failed_context)
        logger.info("MetaArchitect: synthesising skill for intent: '%s'", intent_description[:120])

        base_url = (settings.llm.base_url or "http://localhost:20128/v1").strip()
        api_key = (settings.llm.api_key or "sk-dummy").strip()

        # Build prioritized candidate model list: specialist models first (best for code), then active model, then router models
        candidate_models = []
        spec_models = getattr(settings.llm, "specialist_models", []) or []
        for m in spec_models:
            if m and m not in candidate_models:
                candidate_models.append(m)

        active_model = (settings.llm.model_name or "").strip()
        if active_model and active_model not in candidate_models:
            candidate_models.append(active_model)

        router_models = getattr(settings.llm, "router_models", []) or []
        for m in router_models:
            if m and m not in candidate_models:
                candidate_models.append(m)

        if not candidate_models:
            # Trước đây chỗ này điền cứng 3 model của một provider, nên khi
            # provider đó chết, MetaArchitect vẫn "chạy được" rồi hỏng im
            # lặng. Nay nói thẳng ra để lỗi hiện đúng chỗ.
            raise ValueError(
                "MetaArchitect chưa có model nào để dùng. Cấu hình model ở tab "
                "Quản Lý Trợ Lý AI > Bộ Não & Xử Lý Ngôn Ngữ."
            )

        from mateai.infrastructure.llm.llm_provider import complete_text_blocking
        try:
            raw_response, used_model = complete_text_blocking(
                base_url, api_key or "sk-dummy", candidate_models,
                [
                    {"role": "system", "content": _CODE_GEN_SYSTEM_PROMPT},
                    {"role": "user", "content": user_message},
                ],
                temperature=0.2, max_tokens=4096, timeout=60.0,
            )
        except Exception as exc:  # pylint: disable=broad-except
            logger.error("MetaArchitect: Tất cả mô hình sinh mã đều thất bại. Lỗi cuối: %s", exc)
            raise RuntimeError(f"Tất cả mô hình sinh mã qua 9router đều thất bại. Lỗi: {exc}") from exc
        logger.debug("MetaArchitect raw LLM response (model: %s):\n%s", used_model, raw_response[:800])

        code_str = self._extract_code(raw_response)
        if not code_str:
            raise ValueError(
                "LLM response did not contain a valid ```python ... ``` code block. "
                f"Raw response (truncated): {raw_response[:300]}"
            )

        return self._with_user_phrasing(code_str, intent_description)

    def create_skill(
        self,
        intent_description: str,
        skill_name: Optional[str] = None,
        failed_context: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """
        Đường DUY NHẤT tạo kỹ năng mới (công cụ `create_new_skill` và tự tạo khi
        model báo "không có công cụ" đều gọi hàm này).

        Đã có kỹ năng khớp rõ yêu cầu -> trả lại kỹ năng đó, không sinh mã.
        Trả dict: status "success" | "pending_review" | "error", skill_name (tên
        CÔNG CỤ để gọi — lấy từ chính mã đã nạp), skill_names, file, message.
        """
        desc = " ".join(str(intent_description or "").split())
        if not desc:
            return {"status": "error", "message": "Mô tả kỹ năng không được để trống."}

        from mateai.application.skills.skill_router import find_existing_skill
        existing = find_existing_skill(desc)
        if existing:
            logger.info("MetaArchitect: đã có kỹ năng '%s' cho yêu cầu — không tạo mới.", existing)
            return {
                "status": "success",
                "already_exists": True,
                "skill_name": existing,
                "skill_names": [existing],
                "message": f"Đã có kỹ năng '{existing}' làm việc này — hãy gọi nó, không tạo kỹ năng mới.",
            }

        stem = skill_file_stem(skill_name or desc)
        logger.info("MetaArchitect: tạo kỹ năng mới '%s' (yêu cầu: %s)", stem, desc[:80])
        # Mã hỏng (cú pháp, lệnh cấp module, import bị cấm, không nạp được) → gửi lỗi cho model viết lại NGAY trong lần gọi
        # này. Trước 2026-10-12 lỗi trả thẳng về agent: AI gọi create_new_skill lại với tên khác, mỗi lần tốn một lượt
        # hạn mức → hết 3 lượt / 10 phút → người dùng nghe "phải chờ" mà kỹ năng chưa bao giờ được cài.
        import time as _time
        t0, context, result = _time.monotonic(), {**(failed_context or {}), "target_name": stem}, None
        for attempt in range(1, MAX_SYNTH_ATTEMPTS + 1):
            try:
                code_str = self.synthesize_skill(intent_description=desc, failed_context=context)
            except Exception as exc:  # pylint: disable=broad-except
                logger.error("MetaArchitect: sinh mã thất bại: %s", exc)
                return {"status": "error", "message": f"Không sinh được mã kỹ năng: {exc}", "attempts": attempt}
            result = self.install_skill(code_str, stem, intent_description=desc)
            result["attempts"] = attempt
            if result.get("status") != "error" or not result.get("retryable"):
                return result
            logger.warning("MetaArchitect: lần %d — mã kỹ năng '%s' hỏng (%s), cho model sửa lại.",
                           attempt, stem, result.get("message"))
            if _time.monotonic() - t0 > SYNTH_BUDGET_S:
                break
            context = {"target_name": stem, "loi_lan_truoc": result.get("message"),
                       "ma_lan_truoc_bi_tu_choi": code_str[:2500], "yeu_cau_sua": "Viết lại TOÀN BỘ module, sửa đúng lỗi trên."}
        result = dict(result or {})
        result["message"] = (f"{result.get('message', 'Không tạo được kỹ năng')} (đã thử {result.get('attempts', 1)} lần). "
                             "Đừng gọi create_new_skill lại cho cùng việc — báo người dùng lý do này.")
        return result

    def install_skill(
        self,
        code_str: str,
        skill_filename: str,
        intent_description: str = "",
    ) -> Dict[str, Any]:
        """
        Kiểm tra -> ghi file MỚI (không ghi đè) -> nạp nóng -> xác nhận đã đăng ký.

        Kiểm tra trên ĐÚNG nội dung sẽ ghi ra đĩa: cú pháp, cấu trúc (có
        @export_skill, tên hợp lệ, không chạy lệnh khi nạp), không trùng tên công
        cụ có sẵn, kiểm toán an ninh AST. Nạp lỗi (vd thiếu thư viện) -> xoá file,
        nạp lại, báo lỗi — không để file hỏng nằm lại trong skills/.
        """
        from core.plugin_manager import plugin_manager  # lazy: tránh import vòng
        from mateai.application.security.safety_guard import security_engine

        stem = skill_file_stem(skill_filename)
        content = _skill_header(stem, intent_description) + code_str.strip() + "\n"

        problem = _structure_problem(content)
        if problem:
            logger.error("MetaArchitect: mã kỹ năng '%s' không hợp lệ: %s", stem, problem)
            return {"status": "error", "retryable": True, "message": f"Mã kỹ năng không hợp lệ: {problem}"}
        names = exported_skill_names(content)

        taken = sorted(set(names) & set(plugin_manager.get_skill_names()))
        if taken:
            return {"status": "error", "retryable": True,
                    "message": f"Tên kỹ năng trùng công cụ đã có: {', '.join(taken)} — đặt tên khác, không ghi đè công cụ có sẵn."}

        is_safe, sec_reason = security_engine.inspect_generated_code(content)
        if not is_safe:
            logger.error("MetaArchitect: mã kỹ năng '%s' bị từ chối: %s", stem, sec_reason)
            return {"status": "error", "retryable": True, "message": f"Mã kỹ năng bị từ chối khi kiểm tra an ninh: {sec_reason}"}

        skills_dir: Path = settings.SKILLS_DIR  # type: ignore[assignment]
        if not _auto_install_enabled():
            # Không tự cài: lưu chờ duyệt (thư mục con — plugin_manager không nạp).
            # Trước đây hiện hộp thoại Windows trên máy chủ; máy chủ chạy nền và
            # người dùng ra lệnh từ HUD / portal / Telegram nên lượt nói treo chờ
            # một hộp thoại không ai thấy.
            target = _unique_path(skills_dir / PENDING_DIR_NAME, stem)
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(content, encoding="utf-8")
            logger.warning("MetaArchitect: kỹ năng '%s' chờ duyệt tại %s", stem, target)
            return {
                "status": "pending_review",
                "skill_names": names,
                "file": str(target.relative_to(skills_dir.parent)),
                "message": (f"Đã sinh mã kỹ năng {', '.join(names)} nhưng chưa cài vì chưa bật tự cài "
                            f"(auto_execute). Quản trị viên xem tệp {target.name} trong skills/{PENDING_DIR_NAME}/, "
                            f"chuyển vào skills/ rồi chạy reload_all_skills."),
            }

        target = _unique_path(skills_dir, stem)
        try:
            skills_dir.mkdir(parents=True, exist_ok=True)
            target.write_text(content, encoding="utf-8")
        except OSError as exc:
            return {"status": "error", "message": f"Không ghi được tệp kỹ năng: {exc}"}
        logger.info("MetaArchitect: đã ghi %s (%d byte)", target, len(content))

        module_name = f"skills.{target.stem}"
        plugin_manager.load_plugins()
        modules = plugin_manager.get_skill_modules()
        missing = [n for n in names if modules.get(n) != module_name]
        if missing:
            rejected = (getattr(plugin_manager, "rejected_modules", None) or {}).get(target.name)
            reason = (f"bộ nạp từ chối — lệnh chạy ở cấp module: {'; '.join(rejected[:2])}" if rejected
                      else _import_error(module_name) or "module không đăng ký được kỹ năng")
            logger.error("MetaArchitect: nạp '%s' thất bại (%s) — gỡ tệp.", module_name, reason)
            try:
                target.unlink()
            except OSError:
                pass
            sys.modules.pop(module_name, None)
            plugin_manager.load_plugins()
            return {"status": "error", "retryable": True, "message": f"Kỹ năng sinh ra không nạp được: {reason}"}

        return {
            "status": "success",
            "skill_name": names[0],
            "skill_names": names,
            "file": target.name,
            "message": (f"Đã tạo và nạp kỹ năng mới {', '.join(names)} — gọi ngay "
                        f"'{names[0]}' để làm tiếp yêu cầu."),
        }

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _build_user_message(
        self,
        intent_description: str,
        failed_context: Optional[Dict[str, Any]],
    ) -> str:
        """Compose the user-turn message for the code-gen LLM call."""
        lines = [
            "YÊU CẦU:",
            intent_description,
        ]
        if failed_context:
            lines.append("\nBỐI CẢNH THẤT BẠI TRƯỚC ĐÓ (để tránh lặp lại lỗi):")
            for key, value in failed_context.items():
                lines.append(f"  {key}: {value}")
        lines.append("\nViết ngay module Python theo yêu cầu ở trên.")
        return "\n".join(lines)

    @staticmethod
    def _with_user_phrasing(code_str: str, intent_description: str) -> str:
        """
        Gắn câu yêu cầu gốc của người dùng vào `description` của từng
        @export_skill. Trợ lý chọn công cụ theo mô tả; model sinh mã hay viết mô
        tả tiếng Anh ("search for a song…") nên câu tiếng Việt ("mở bài hát…")
        không khớp — skill đã tạo mà trợ lý không dùng tới nếu không được nhắc.
        Chỉ sửa mô tả là chuỗi hằng; mã không hợp lệ thì để nguyên (bước kiểm
        tra cú pháp sau đó sẽ từ chối).
        """
        phrase = " ".join(str(intent_description or "").split())[:200]
        if not phrase:
            return code_str
        try:
            tree = ast.parse(code_str)
        except SyntaxError:
            return code_str
        edits = []
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            fname = getattr(node.func, "id", None) or getattr(node.func, "attr", None)
            if fname != "export_skill":
                continue
            for kw in node.keywords:
                if kw.arg == "description" and isinstance(kw.value, ast.Constant) and isinstance(kw.value.value, str):
                    if phrase.lower() in kw.value.value.lower():
                        continue
                    new_desc = f"{kw.value.value.rstrip()} Ví dụ yêu cầu: «{phrase}»."
                    edits.append((kw.value, repr(new_desc)))
        lines = code_str.splitlines(keepends=True)
        offsets = [0]
        for line in lines:
            offsets.append(offsets[-1] + len(line))
        out = code_str
        for node, literal in sorted(edits, key=lambda e: (e[0].lineno, e[0].col_offset), reverse=True):
            start = offsets[node.lineno - 1] + len(lines[node.lineno - 1].encode("utf-8")[:node.col_offset].decode("utf-8", "ignore"))
            end = offsets[node.end_lineno - 1] + len(lines[node.end_lineno - 1].encode("utf-8")[:node.end_col_offset].decode("utf-8", "ignore"))
            out = out[:start] + literal + out[end:]
        return out

    @staticmethod
    def _extract_code(raw: str) -> str:
        """
        Extract Python source from a Markdown-fenced code block.
        Returns empty string if no block is found.
        """
        match = _CODE_FENCE_PATTERN.search(raw)
        if match:
            return match.group(1).strip()
        # Fallback: if the entire response looks like raw Python (no fences),
        # return it as-is if it starts with recognisable Python tokens.
        stripped = raw.strip()
        if stripped.startswith(("import ", "from ", "def ", "#", '"""', "class ")):
            logger.debug("MetaArchitect: no code fence found, treating entire response as code.")
            return stripped
        return ""

# ---------------------------------------------------------------------------
# Kiểm tra mã kỹ năng sinh ra (dùng trong install_skill)
# ---------------------------------------------------------------------------

#: Tên công cụ hợp lệ: snake_case ASCII — model gọi tool theo tên này.
_SKILL_NAME_RE = re.compile(r"^[a-z][a-z0-9_]{2,63}$")
#: Số lần cho model viết lại mã trong MỘT lần tạo kỹ năng, và trần thời gian (agent cho mỗi lượt tối đa 180 s).
MAX_SYNTH_ATTEMPTS = 3
SYNTH_BUDGET_S = 90.0
#: Mã chờ duyệt (khi không bật auto_execute) — plugin_manager chỉ nạp skills/*.py.
PENDING_DIR_NAME = "pending"
#: Câu lệnh cấp module chạy NGAY khi nạp (mỗi lần nạp lại kỹ năng lại chạy).
_RUNS_AT_IMPORT = (ast.For, ast.AsyncFor, ast.While, ast.With, ast.AsyncWith)


def skill_file_stem(text: str, max_len: int = 40) -> str:
    """Tên tệp kỹ năng ASCII `auto_<slug>` từ tên gợi ý hoặc câu yêu cầu (bỏ dấu)."""
    t = unicodedata.normalize("NFD", str(text or ""))
    t = "".join(ch for ch in t if unicodedata.category(ch) != "Mn").replace("đ", "d").replace("Đ", "D")
    slug = re.sub(r"[^a-z0-9]+", "_", t.lower()).strip("_")[:max_len].rstrip("_")
    if not slug:
        return "auto_skill"
    return slug if slug.startswith("auto_") else f"auto_{slug}"


def exported_skill_names(code_str: str) -> List[Optional[str]]:
    """Tên công cụ khai báo trong @export_skill(name=...), theo thứ tự trong mã;
    None cho tên không phải chuỗi cố định."""
    try:
        tree = ast.parse(code_str)
    except SyntaxError:
        return []
    names = []
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        for dec in node.decorator_list:
            if not isinstance(dec, ast.Call):
                continue
            fname = getattr(dec.func, "id", None) or getattr(dec.func, "attr", None)
            if fname != "export_skill":
                continue
            arg = next((kw.value for kw in dec.keywords if kw.arg == "name"), dec.args[0] if dec.args else None)
            names.append(arg.value if isinstance(arg, ast.Constant) and isinstance(arg.value, str) else None)
    return names


def _structure_problem(content: str) -> Optional[str]:
    """Lý do mã không dùng được làm kỹ năng (None nếu ổn)."""
    try:
        tree = ast.parse(content)
    except SyntaxError as exc:
        return f"sai cú pháp Python dòng {exc.lineno}: {exc.msg}"
    names = exported_skill_names(content)
    if not names:
        return "không có hàm nào gắn @export_skill"
    if None in names:
        return "@export_skill phải có name là chuỗi cố định"
    if len(set(names)) != len(names):
        return "hai hàm trong mã dùng cùng một tên công cụ"
    bad = [n for n in names if not _SKILL_NAME_RE.match(n)]
    if bad:
        return f"tên công cụ không hợp lệ (cần snake_case ASCII): {', '.join(bad)}"
    # CÙNG luật với bộ nạp (core.plugin_manager._is_safe_top_level). Trước 2026-10-12 hai nơi lệch nhau: ở đây cho qua
    # `if sys.platform…` / `try` cấp module, bộ nạp từ chối → tệp bị gỡ, AI chỉ nhận "không nạp được" rồi tạo lại.
    from core.plugin_manager import _is_safe_top_level
    for node in tree.body:
        if not _is_safe_top_level(node):
            snippet = ast.unparse(node).splitlines()[0][:60]
            return (f"dòng {node.lineno} chạy lệnh ngay khi nạp kỹ năng ({type(node).__name__}: {snippet}) — cấp module "
                    "chỉ được import / hằng / def / class; vòng lặp, if, try, with, lời gọi hàm phải đặt trong hàm")
    return None


def _skill_header(stem: str, intent_description: str) -> str:
    """Đầu tệp dạng CHÚ THÍCH (không phải docstring: docstring thứ hai đứng
    trước `from __future__ import` là lỗi cú pháp)."""
    phrase = " ".join(str(intent_description or "").split())[:160]
    lines = [f"# Auto-synthesised skill: {stem}", "# Generated by VN-MateAI MetaArchitect."]
    if phrase:
        lines.append(f"# Yêu cầu gốc: {phrase}")
    return "\n".join(lines) + "\n\n"


def _unique_path(directory: Path, stem: str) -> Path:
    """skills/<stem>.py chưa tồn tại — KHÔNG ghi đè kỹ năng có sẵn (kể cả kỹ năng lõi)."""
    target = directory / f"{stem}.py"
    n = 2
    while target.exists():
        target = directory / f"{stem}_{n}.py"
        n += 1
    return target


def _auto_install_enabled() -> bool:
    return bool(getattr(settings, "auto_execute", False) or getattr(settings, "AUTO_EXECUTE_UNVERIFIED_CODE", False))


def _import_error(module_name: str) -> Optional[str]:
    """Lỗi khi import module kỹ năng (để báo lý do nạp thất bại)."""
    import importlib
    try:
        sys.modules.pop(module_name, None)
        importlib.import_module(module_name)
    except Exception as exc:  # pylint: disable=broad-except
        return f"{type(exc).__name__}: {exc}"
    return None


# ---------------------------------------------------------------------------
# Module-level singleton
# ---------------------------------------------------------------------------

meta_architect = MetaArchitect()
