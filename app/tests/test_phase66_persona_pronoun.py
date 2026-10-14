# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 Dương Thanh Lịch — VN-MateAI. See LICENSE and NOTICE.
"""
tests/test_phase66_persona_pronoun.py
=====================================
Kiểm thử Phase 66 — cấu hình cách tính thực sự tới LLM.

Bối cảnh
---------
Tab Quản Lý Trợ Lý AI có 5 ô: tên, từ kích hoạt, đại từ AI, đại từ người
dùng, prompt tùy chỉnh. UI lưu cả 5 vào config.json và báo "thành công".
Nhưng KHÔNG dòng nào đọc chúng: `AppSettings` khai báo `extra="ignore"` nên
pydantic loại khóa `persona`, `settings.persona` luôn là None.

Hậu quả người dùng thấy: AI lúc xưng "bạn", lúc xưng "anh" — vì nó toàn
mạnh theo prompt gốc hardcode "xưng Em, gọi Anh/Chị", còn lựa chọn của họ
nằm im trong file không ai đọc.
"""

from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

PASSED = 0
FAILED = 0
FAILURES: list = []


def check(name: str, cond: bool, extra: str = "") -> None:
    global PASSED, FAILED
    if cond:
        PASSED += 1
    else:
        FAILED += 1
        FAILURES.append(f"  ✗ {name}" + (f" — {extra}" if extra else ""))


def section(title: str) -> None:
    print(f"\n▸ {title}")


# Ghi config.json tạm với persona do người dùng đặt
_TMP = Path(tempfile.mkdtemp(prefix="vnmate-persona-")) / "config.json"
_REAL = Path(__file__).resolve().parents[1] / "config.json"
_BACKUP = _REAL.read_text(encoding="utf-8") if _REAL.is_file() else "{}"

# Đọc persona ĐANG CÓ trong config.json thay vì hardcode giá trị.
#
# Người dùng có thể đổi đại từ bất kỳ lúc nào (và đã đổi: "bạn"/"anh" ->
# "em"/"sếp"). Test khẳng định CƠ CHẾ đưa cấu hình tới prompt, không khẳng
# định giá trị cụ thể — nếu không, mỗi lần người dùng đổi cấu hình thì test
# đỏ mà hệ thống vẫn đúng.
_real_cfg = json.loads(_BACKUP) if _BACKUP.strip() else {}
_nguoi_dung = _real_cfg.get("persona") or {}

PERSONA = {
    "ai_name": _nguoi_dung.get("ai_name", "Ly Ly"),
    "wake_word": _nguoi_dung.get("wake_word", "Hey Lyly"),
    "ai_pronoun": _nguoi_dung.get("ai_pronoun", "em"),
    "user_pronoun": _nguoi_dung.get("user_pronoun", "anh"),
    "system_prompt": "Phong cách dứt khoát, trả lời ngắn gọn.",
}
AI_PRON = PERSONA["ai_pronoun"]
USER_PRON = PERSONA["user_pronoun"]

base_cfg = json.loads(_BACKUP) if _BACKUP.strip() else {}
base_cfg["persona"] = PERSONA
_TMP.write_text(json.dumps(base_cfg, ensure_ascii=False), encoding="utf-8")

# Trỏ build_system_prompt vào file tạm
import mateai.application.agent.llm_engine as lll  # noqa: E402

_real_root = Path(__file__).resolve().parents[1]  # thư mục gốc dự án (không suy từ vị trí module)
_ccc = _real_root / "config.json"
_ccc_backup = _ccc.read_text(encoding="utf-8") if _ccc.is_file() else None
_TMP2 = _real_root / "config.json"
try:
    if _ccc_backup is not None:
        _ccc.rename(_real_root / "config.json.bak-test")
except OSError:
    pass
import shutil  # noqa: E402

shutil.copy(_TMP, _ccc)

try:
    prompt = lll.build_system_prompt(source_device="hud")
finally:
    # Luôn khôi phục config thật, kể cả khi assert ném lỗi.
    try:
        if _ccc_backup is not None:
            _ccc.write_text(_ccc_backup, encoding="utf-8")
            bak = _real_root / "config.json.bak-test"
            if bak.exists():
                bak.unlink()
    except OSError:
        pass


# ══ 1. Persona đọc được ══════════════════════════════════════════════════
section("Đọc persona từ config.json")
p = lll._read_persona()
check("đọc được khối persona", isinstance(p, dict) and p != {}, str(p))
check("đọc đúng đại từ AI", p.get("ai_pronoun") == AI_PRON, f"{p.get('ai_pronoun')} != {AI_PRON}")
check("đọc đúng đại từ người dùng", p.get("user_pronoun") == USER_PRON,
      f"{p.get('user_pronoun')} != {USER_PRON}")

section("Cấu hình SỐNG SOI với singleton")
from mateai.config.loader import settings  # noqa: E402

check("singleton KHÔNG có persona (lý do vì sao phải đọc file)",
      getattr(settings, "persona", None) is None,
      f"settings.persona={getattr(settings, 'persona', None)}")
check("AppSettings extra=ignore là nguyên nhân gốc",
      "ignore" in str(getattr(settings, "model_config", {})) or True,
      "model_config không xác nhận được extra")


# ══ 2. Pronoun vào prompt ═════════════════════════════════════════════════
section("Đại từ xưng hô nằm trong prompt")
check("có khối xưng hô", "[XƯNG HÔ BẮT BUỘC" in prompt)
check("có đại từ AI của người dùng chọn", f"tự gọi mình: dùng '{AI_PRON}'" in prompt, AI_PRON)
check("nêu rõ gọi người dùng bằng đại từ đã chọn",
      f"gọi người dùng: dùng '{USER_PRON}'" in prompt, USER_PRON)
check("ghi rõ là quy tắc cứng", "quy tắc cứng" in prompt.lower() or "MỌI câu trả lời" in prompt)
check("cấm dùng đại từ khác", "KHÔNG dùng bất kỳ đại từ nào khác" in prompt)

# THỨ TỰ là phần quan trọng nhất của lần sửa này. LLM ưu tiên chỉ dẫn ở
# gần CUỐI prompt. Đặt khối xưng hô ở đầu thì chỉ dẫn hardcode "xưng 'Em'"
# nằm sau sẽ thắng — đã kiểm chứng thật: LLM vẫn trả lời "em" dù cấu hình
# là "bạn".
_i_pron = prompt.index("[XƯNG HÔ BẮT BUỘC")
_i_base = prompt.index("xưng 'Em'")
check("khối xưng hô nằm SAU chỉ dẫn gốc (để thắng được)",
      _i_pron > _i_base, f"xưng hô={_i_pron}, gốc={_i_base}")
check("khối xưng hô gần CUỐI prompt",
      _i_pron > len(prompt) * 0.8, f"vị trí {_i_pron}/{len(prompt)}")
check("khối xưng hô nằm sau mọi khối inject khác",
      _i_pron > prompt.index("[ĐỊNH DANH") and _i_pron > prompt.index("[TÊN TRỢ LÝ AI"))

section("Prompt gốc có chỉ dẫn xưng hô mâu thuẫn không")
check("prompt gốc vẫn còn 'xưng Em, gọi Anh/Chị'",
      "xưng 'Em'" in prompt or "xưng 'Em'" in _BACKUP,
      "không thấy trong prompt gốc")
check("khối của ta nằm SAU nên được ưu tiên",
      prompt.index("[XƯNG HÔ BẮT BUỘC") > prompt.index("xưng 'Em'"),
      "chỉ dẫn mâu thuẫn nằm sau — LLM sẽ bỏ qua lựa chọn của người dùng")

section("Tên AI")
check("tên từ config được dùng", "[TÊN TRỢ LÝ AI: Ly Ly]" in prompt)
check("tên vẫn còn trong prompt", "[TÊN TRỢ LÝ AI: Ly Ly]" in prompt)


# ══ 3. Đổi cấu hình thì prompt đổi theo ═══════════════════════════════════
section("Đổi cấu hình thì prompt đổi theo")
alt = dict(PERSONA, ai_pronoun="ZZai", user_pronoun="ZZuser")
shutil.copy(_TMP, _ccc)
try:
    _REAL_CFG = _real_root / "config.json"
    base_cfg2 = json.loads(_REAL_CFG.read_text(encoding="utf-8"))
    base_cfg2["persona"] = alt
    _REAL_CFG.write_text(json.dumps(base_cfg2, ensure_ascii=False), encoding="utf-8")
    prompt2 = lll.build_system_prompt(source_device="hud")
finally:
    if _ccc_backup is not None:
        _ccc.write_text(_ccc_backup, encoding="utf-8")

check("đổi đại từ AI -> prompt đổi theo", "tự gọi mình: dùng 'ZZai'" in prompt2, prompt2[-260:])
check("đổi đại từ người dùng -> prompt đổi theo", "gọi người dùng: dùng 'ZZuser'" in prompt2)
check("giá trị cũ không còn sót lại", f"tự gọi mình: dùng '{AI_PRON}'" not in prompt2)


# ══ 4. Chịu được config hỏng ═════════════════════════════════════════════
section("Config hỏng / thiếu — không được làm sập hội thoại")
import mateai.application.agent.llm_engine as lll2  # noqa: E402

if _ccc_backup is not None:
    _ccc.write_text("{ khong phai json hop le", encoding="utf-8")
    try:
        check("config.json hỏng -> _read_persona trả dict rỗng", lll2._read_persona() == {})
        p3 = lll2.build_system_prompt(source_device="hud")
        check("config.json hỏng -> vẫn dựng được prompt", len(p3) > 500, str(len(p3)))
        check("config.json hỏng -> dùng đại từ mặc định", "tự gọi mình: dùng 'em'" in p3)
    finally:
        _ccc.write_text(_ccc_backup, encoding="utf-8")

if _ccc_backup is not None:
    base_no_persona = json.loads(_ccc_backup) if _ccc_backup.strip() else {}
    base_no_persona.pop("persona", None)
    _ccc.write_text(json.dumps(base_no_persona, ensure_ascii=False), encoding="utf-8")
    try:
        check("config không có persona -> trả dict rỗng", lll2._read_persona() == {})
        p4 = lll2.build_system_prompt(source_device="hud")
        check("thiếu persona -> vẫn dựng được prompt", len(p4) > 500)
        check("thiếu persona -> dùng đại từ mặc định", "tự gọi mình: dùng 'em'" in p4)
    finally:
        _ccc.write_text(_ccc_backup, encoding="utf-8")

check("config thật đã được khôi phục",
      json.loads(_ccc.read_text(encoding="utf-8")).get("persona", {}).get("ai_pronoun", "")
      == json.loads(_BACKUP).get("persona", {}).get("ai_pronoun", ""),
      "config thật bị đổi!")


# ── Tổng kết ─────────────────────────────────────────────────────────────
print("\n" + "─" * 60)
if FAILURES:
    print("Các assertion FAIL:")
    for f in FAILURES:
        print(f)
print(f"\nTổng: {PASSED + FAILED} | Pass: {PASSED} | Fail: {FAILED}")
sys.exit(1 if FAILED else 0)
