# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 Dương Thanh Lịch — VN-MateAI. See LICENSE and NOTICE.
"""
tests/architecture/test_core_rules.py
=====================================
Quy tắc chống trùng lặp RULE-011…015 (docs/architecture/dependency-rules.md §IV)
và quy tắc kiểm soát tự trị RULE-017, 024, 025, 026 (§VI) áp lên CODE ĐANG CHẠY:
core/, src/mateai/, skills/, workers/, main.py.

Cơ chế "bánh cóc":
  * Vi phạm đang có được ghi ở `core_rules_baseline.json` (file -> số lần).
  * Vi phạm MỚI (file mới, hoặc số lần tăng) -> FAIL.
  * Số lần GIẢM so với baseline -> FAIL kèm lời nhắc hạ baseline, để mức tốt
    hơn được khoá lại và không thể lùi.

Cập nhật baseline sau khi đã xoá bớt vi phạm:
    python tests/architecture/test_core_rules.py --update-baseline
"""
from __future__ import annotations

import ast
import json
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
BASELINE_PATH = Path(__file__).with_name("core_rules_baseline.json")

SCAN_GLOBS = ["core/**/*.py", "src/mateai/**/*.py", "skills/*.py", "workers/*.py", "main.py"]

#: Nơi duy nhất được phép làm việc đó (bản canonical).
ALLOWED = {
    # audio_processor dùng SDK OpenAI cho Whisper (STT), không phải gọi LLM.
    "RULE-011": {"src/mateai/infrastructure/llm/llm_provider.py", "src/mateai/infrastructure/audio/audio_processor.py"},
    "RULE-012": {"src/mateai/infrastructure/tts/tts_stream_engine.py"},
    "RULE-013": {"src/mateai/config/loader.py"},
    # sql_connector: mở CSDL SQLite CỦA KHÁCH (nguồn dữ liệu ngoài, chỉ-đọc) — không phải CSDL của hệ thống.
    "RULE-014": {"src/mateai/infrastructure/database/erp_database.py", "src/mateai/infrastructure/connectors/sql_connector.py"},
    "RULE-015": set(),
    # Chỉ cổng chính sách được gọi thực thi tool. routers/skills: executor nằm trong
    # execute_with_hitl (policy_engine.authorize) — gọi qua cổng.
    "RULE-017": {"src/mateai/application/agent/tool_gate.py", "src/mateai/interfaces/http/routers/skills.py"},
    "RULE-024": set(),
    "RULE-025": set(),
    "RULE-026": set(),
    "RULE-027": set(),
}

DESCRIPTIONS = {
    "RULE-011": "tạo client OpenAI / gọi chat.completions.create ngoài provider LLM",
    "RULE-012": "tổng hợp TTS (edge_tts.Communicate, gTTS, /audio/speech) ngoài TTS engine",
    "RULE-013": "tự mở config.json ngoài config_loader",
    "RULE-014": "sqlite3.connect ngoài tầng persistence",
    "RULE-015": "module lõi import ngược mateai.interfaces.http.server",
    "RULE-017": "gọi execute_skill / execute_tool ngoài cổng chính sách (tool_gate)",
    "RULE-024": "application truy vấn SQL trực tiếp (get_connection / .execute)",
    "RULE-025": "subprocess với shell=True",
    "RULE-026": "hàm async gọi API chặn (time.sleep, requests.*, psutil.cpu_percent(interval>0))",
    "RULE-027": "router HTTP tự ghi tệp / cấu hình (phải qua use case ở tầng application — Phase 10)",
}

_ROUTER_WRITE_CALLS = ("write_raw_config", "update_config_section")

_BLOCKING_IN_ASYNC = ("time.sleep", "requests.get", "requests.post", "requests.put", "requests.delete", "requests.request",
                      # P10: routers/config.py gọi urlopen trong hàm async — mỗi lần Lưu chặn loop tới 5 s
                      "urllib.request.urlopen", "urlopen")


def _own_calls(fn):
    """Lời gọi nằm trực tiếp trong thân hàm (không tính hàm lồng)."""
    stack = list(fn.body)
    while stack:
        node = stack.pop()
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
            continue
        if isinstance(node, ast.Call):
            yield node
        stack.extend(ast.iter_child_nodes(node))


def _files():
    seen = set()
    for pattern in SCAN_GLOBS:
        for p in sorted(ROOT.glob(pattern)):
            rel = p.relative_to(ROOT).as_posix()
            if rel not in seen and "__pycache__" not in rel:
                seen.add(rel)
                yield rel, p


def _call_name(node: ast.Call) -> str:
    return ast.unparse(node.func)


def scan() -> dict:
    """Trả về {rule: Counter({file: số lần})}."""
    found = {rule: Counter() for rule in ALLOWED}
    for rel, path in _files():
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=rel)
        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                name = _call_name(node)
                if name.split(".")[-1] in ("OpenAI", "AsyncOpenAI") or name.endswith("chat.completions.create"):
                    found["RULE-011"][rel] += 1
                if name.split(".")[-1] in ("Communicate", "gTTS"):
                    found["RULE-012"][rel] += 1
                if name in ("sqlite3.connect",):
                    found["RULE-014"][rel] += 1
                # `CONFIG_PATH.read_text/write_text/open` hay `open(CONFIG_PATH)` cũng là
                # tự mở config.json — bỏ qua ghi nguyên tử + khoá của loader.
                if name.endswith(("CONFIG_PATH.read_text", "CONFIG_PATH.write_text", "CONFIG_PATH.open")) or (
                    name == "open" and node.args and isinstance(node.args[0], ast.Name) and node.args[0].id == "CONFIG_PATH"
                ):
                    found["RULE-013"][rel] += 1
                if name.split(".")[-1] in ("execute_skill", "execute_tool") and "." in name:
                    found["RULE-017"][rel] += 1
                if rel.startswith("src/mateai/application/") and (
                        name.endswith(".get_connection") or name in ("cursor.execute", "conn.execute", "cursor.executemany")):
                    found["RULE-024"][rel] += 1
                if rel.startswith("src/mateai/interfaces/http/routers/") and (
                        name.split(".")[-1] in _ROUTER_WRITE_CALLS or name.endswith((".write_bytes", ".write_text"))):
                    found["RULE-027"][rel] += 1
                if name.startswith("subprocess.") and any(
                        k.arg == "shell" and isinstance(k.value, ast.Constant) and k.value.value is True
                        for k in node.keywords):
                    found["RULE-025"][rel] += 1
            elif isinstance(node, ast.Constant) and isinstance(node.value, str):
                if "/audio/speech" in node.value and not node.value.strip().startswith(("\n", "Tạo", "9Router")):
                    found["RULE-012"][rel] += 1
            elif isinstance(node, ast.BinOp) and isinstance(node.op, ast.Div):
                if isinstance(node.right, ast.Constant) and node.right.value == "config.json":
                    found["RULE-013"][rel] += 1
            elif isinstance(node, (ast.Import, ast.ImportFrom)):
                mods = [a.name for a in node.names] if isinstance(node, ast.Import) else [node.module or ""]
                if any(m == "mateai.interfaces.http.server" or m.startswith("mateai.interfaces.http.server.") for m in mods):
                    if rel != "src/mateai/interfaces/http/server.py" and rel != "main.py":
                        found["RULE-015"][rel] += 1
        for fn in ast.walk(tree):
            if not isinstance(fn, ast.AsyncFunctionDef):
                continue
            for call in _own_calls(fn):
                name = _call_name(call)
                if name in _BLOCKING_IN_ASYNC:
                    found["RULE-026"][rel] += 1
                elif name.endswith("psutil.cpu_percent") or name == "cpu_percent":
                    interval = next((k.value for k in call.keywords if k.arg == "interval"),
                                    call.args[0] if call.args else None)
                    if isinstance(interval, ast.Constant) and isinstance(interval.value, (int, float)) and interval.value > 0:
                        found["RULE-026"][rel] += 1
    for rule, allowed in ALLOWED.items():
        for rel in allowed:
            found[rule].pop(rel, None)
    return found


def _load_baseline() -> dict:
    if not BASELINE_PATH.exists():
        return {rule: {} for rule in ALLOWED}
    return json.loads(BASELINE_PATH.read_text(encoding="utf-8"))


def _check(rule: str) -> None:
    actual = scan()[rule]
    baseline = _load_baseline().get(rule, {})
    new, improved = [], []
    for rel, n in sorted(actual.items()):
        b = baseline.get(rel, 0)
        if n > b:
            new.append(f"{rel}: {n} (baseline {b})")
        elif n < b:
            improved.append(f"{rel}: {n} (baseline {b})")
    for rel, b in sorted(baseline.items()):
        if rel not in actual:
            improved.append(f"{rel}: 0 (baseline {b})")
    msg = []
    if new:
        msg.append(f"{rule} — vi phạm MỚI ({DESCRIPTIONS[rule]}):\n  " + "\n  ".join(new))
    if improved:
        msg.append(
            f"{rule} — đã giảm so với baseline, hãy khoá mức mới bằng "
            f"`python tests/architecture/test_core_rules.py --update-baseline`:\n  "
            + "\n  ".join(improved)
        )
    assert not msg, "\n".join(msg)


def test_rule_011_llm_client_only_in_provider():
    _check("RULE-011")


def test_rule_012_tts_only_in_tts_engine():
    _check("RULE-012")


def test_rule_013_config_only_via_loader():
    _check("RULE-013")


def test_rule_014_sqlite_only_in_persistence():
    _check("RULE-014")


def test_rule_015_core_does_not_import_server():
    _check("RULE-015")


def test_rule_017_tools_only_through_the_policy_gate():
    _check("RULE-017")


def test_rule_024_application_has_no_raw_sql():
    _check("RULE-024")


def test_rule_025_no_shell_true():
    _check("RULE-025")


def test_rule_026_async_code_does_not_block_the_loop():
    _check("RULE-026")


def test_rule_027_routers_do_not_write_files_or_config():
    _check("RULE-027")


if __name__ == "__main__":
    if "--update-baseline" in sys.argv:
        data = {rule: dict(sorted(c.items())) for rule, c in scan().items()}
        BASELINE_PATH.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(f"Đã ghi {BASELINE_PATH.relative_to(ROOT)}")
    for rule, counter in scan().items():
        print(f"{rule} ({DESCRIPTIONS[rule]}): {sum(counter.values())} lần / {len(counter)} file")
        for rel, n in sorted(counter.items()):
            print(f"    {n:3d}  {rel}")
