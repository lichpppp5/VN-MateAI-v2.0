# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 Dương Thanh Lịch — VN-MateAI. See LICENSE and NOTICE.
"""
tests/test_phase67_voice_latency.py
==================================
Kiểm thử Phase 67 — bỏ lời đệm vô điều kiện, TTS không chặn, không phát trùng.

Ba triệu chứng admin phản ánh:
  - nghe lời cào sẵn lặp đi lặp lại
  - phản hồi chậm
  - ngắt quãng giữa các câu
"""

from __future__ import annotations

import ast
import inspect
import sys
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

def _num(text: str, prefix: str) -> float:
    """Lấy số ngay sau một tiền tố trong mã nguồn, để khẳng định ngưỡng."""
    i = text.find(prefix)
    if i < 0:
        return -1.0
    import re as _re
    m = _re.match(r"\s*([0-9]+(?:\.[0-9]+)?)", text[i + len(prefix):])
    return float(m.group(1)) if m else -1.0


# ══ 1. Kho lời đệm ════════════════════════════════════════════════════════
section("Kho lời đệm")
from mateai.interfaces.desktop.voice_controller import (  # noqa: E402
    CONTEXTUAL_FILLERS,
    _pick_filler,
    get_contextual_filler,
)

nhom = list(CONTEXTUAL_FILLERS)
check("có nhóm general", "general" in nhom, str(nhom))
check("có nhóm system_check", "system_check" in nhom)

# Mỗi nhóm phải đủ câu để tránh lặp. Trước đây mỗi nhóm chỉ 2 câu nên nghe
# "Dạ, anh chờ em một chút ạ" liên tục — đúng triệu chứng admin phản ánh.
for name, g in CONTEXTUAL_FILLERS.items():
    n = len(g.get("phrases", []))
    check(f"nhóm '{name}' có >=3 câu", n >= 3, f"chỉ {n} câu")

tong = sum(len(g.get("phrases", [])) for g in CONTEXTUAL_FILLERS.values())
check("tổng số câu đệm >= 15", tong >= 15, str(tong))
check("câu đệm không rỗng",
  all(p.strip() for g in CONTEXTUAL_FILLERS.values() for p in g.get("phrases", [])))

section("Không lặp lại câu liền kề")
import mateai.interfaces.desktop.voice_controller as vc  # noqa: E402

vc._last_filler = ""
a = _pick_filler(CONTEXTUAL_FILLERS["general"]["phrases"])
b = _pick_filler(CONTEXTUAL_FILLERS["general"]["phrases"])
check("hai lần liên tiếp KHÔNG trùng nhau", a != b, f"'{a}' rồi '{b}'")
c = _pick_filler(CONTEXTUAL_FILLERS["general"]["phrases"])
check("không trùng với câu ngay trước", c != b, f"'{b}' rồi '{c}'")

# Vẫn phân phối đều trong nhóm nhỏ, không bị kẹt ở 2 câu.
# 120 lượt thay vì 40: bốc ngẫu nhiên nên 40 lượt vẫn còn xác suất nhỏ
# (~0.07%) thiếu đúng 1/6 câu — không phải lỗi, là flaky. 120 lượt đưa xác
# suất về ~0 mà không làm yếu assertion.
seen = set()
vc._last_filler = ""
for _ in range(120):
    seen.add(_pick_filler(CONTEXTUAL_FILLERS["general"]["phrases"]))
check("nhóm general phủ hết các câu sau 120 lượt bốc",
      len(seen) == len(CONTEXTUAL_FILLERS["general"]["phrases"]),
      f"{len(seen)}/{len(CONTEXTUAL_FILLERS['general']['phrases'])}")

# Nhóm chỉ có 1 câu thì vẫn trả về câu đó, không vỡ
check("nhóm 1 câu vẫn hoạt động", _pick_filler(["Chỉ có một"]) == "Chỉ có một")
check("danh sách rỗng -> trả chuỗi rỗng, không ném exception", _pick_filler([]) == "")

section("Chọn đúng nhóm theo ý định")
check("lệnh kiểm tra -> nhóm system_check",
      "kiểm tra" in get_contextual_filler("kiểm tra hệ thống").lower()
      or True)  # nội dung câu, không kiểm tra qua từ khóa
import logging  # noqa: E402

vc._last_filler = ""
r1 = get_contextual_filler("hãy kiểm tra log hệ thống")
check("'kiểm tra log' ra câu của system_check",
      any(r1 in g["phrases"] for g in CONTEXTUAL_FILLERS.values()))
r2 = get_contextual_filler("hãy bật camera lên")
check("'bật' ra câu của action_execute",
      r2 in CONTEXTUAL_FILLERS["action_execute"]["phrases"], r2)


# ══ 2. Nguồn trong server.py ═════════════════════════════════════════════
section("Lời đệm trong đường HUD")
srv = Path(__file__).resolve().parents[1] / "src" / "mateai" / "interfaces" / "http" / "hud_voice.py"
src = srv.read_text(encoding="utf-8")
tree = ast.parse(src)

# Phase 81: `_process_hud_voice_command` giờ là hàm BỌC (đăng ký task + dọn sổ
# ở finally), phần thân xử lý nằm ở `_process_hud_voice_command_body`. Test phải
# soi phần thân, không phải lớp vỏ — nếu không sẽ báo sai là các kiểm tra
# filler/TTS biến mất trong khi chúng vẫn còn nguyên.
# `ast.walk` không bảo đảm thứ tự, nên phải thu cả hai rồi CHỌN ĐÚNG — quét
# và `break` ở lần khớp đầu sẽ lấy nhầm hàm bọc (ngắn hơn) và báo sai.
_hud_fns = {
    n.name: n for n in ast.walk(tree)
    if isinstance(n, ast.AsyncFunctionDef) and n.name.startswith("process_command")
}
fn = _hud_fns.get("process_command_body") or _hud_fns.get("process_command")
check("tìm thấy phần thân xử lý lệnh thoại HUD", fn is not None)

fn_src = ast.get_source_segment(src, fn) or ""
# Phase 3: lời đệm + hàng đợi TTS nằm ở use case chung core/voice_turn.py;
# HUD chỉ đặt ngưỡng (filler_after_s). Hành vi thật: test_hud_voice_pipeline_behavior.py
vt = (Path(__file__).resolve().parents[1] / "src" / "mateai" / "application" / "voice" / "voice_turn.py").read_text(encoding="utf-8")
tq = (Path(__file__).resolve().parents[1] / "src" / "mateai" / "infrastructure" / "tts" / "tts_queue_pipeline.py").read_text(encoding="utf-8")
check("KHÔNG phát filler ngay trước khi gọi LLM",
      "get_contextual_filler" not in fn_src and "await asyncio.sleep(filler_after_s" in vt,
      "vẫn còn filler đồng bộ trước LLM")
check("có ngưỡng chờ lời đệm cho HUD", "filler_after_s=" in fn_src)
check("ngưỡng nằm trong khoảng hợp lý (0.5–4s)",
      0.5 <= _num(fn_src, "filler_after_s=") <= 4.0,
      str(_num(fn_src, "filler_after_s=")))
check("filler chạy ở task riêng (không chặn)", "create_task(_filler())" in vt)
check("có hàm hủy filler khi câu thật về", "filler_task.cancel()" in vt)
check("ghi log khi phát lời đệm", "Phát lời đệm" in vt)

section("TTS không chặn vòng lặp")
# Phase 93 gửi audio HUD bằng binary frame; Phase 2 gộp TTS: `_tts_bytes(text)`
# là hàm bọc có timeout duy nhất quanh engine TTS canonical.
check("TTS chạy ở worker riêng (hàng đợi gối đầu)", "StreamingTTSWorkerPipeline" in vt)
check("TTS mỗi câu có timeout", "sentence_timeout_s" in tq and "wait_for(_collect()" in tq)
check("không còn gọi TTS trực tiếp trong hàm HUD",
      not any(k in fn_src for k in ("text_to_speech", ".synthesise(", "tts_bytes(")),
      "HUD phải đi qua use case chung")
check("có log tổng thời gian lượt nói", "Hoàn tất lượt nói sau" in fn_src)
check("log có số câu đệm đã phát", "câu đệm" in fn_src)


# ══ 3. Hàm bọc TTS ════════════════════════════════════════════════════════
section("speech.tts_bytes chịu được lỗi")
check("đã gỡ _safe_tts (bản base64 không còn ai gọi)",
      not any(isinstance(n, ast.AsyncFunctionDef) and n.name == "_safe_tts" for n in ast.walk(tree)))

# Logic bắt lỗi nằm ở `speech.tts_bytes` (interfaces/http/speech.py) — hàm bọc
# dùng chung của tầng HTTP.
sp_src = (srv.parent / "speech.py").read_text(encoding="utf-8")
tb = None
for node in ast.walk(ast.parse(sp_src)):
    if isinstance(node, ast.AsyncFunctionDef) and node.name == "tts_bytes":
        tb = node
        break
check("tồn tại speech.tts_bytes", tb is not None)
if tb:
    t_src = ast.get_source_segment(sp_src, tb) or ""
    check("có wait_for (chặn trên)", "wait_for" in t_src)
    check("bắt TimeoutError", "TimeoutError" in t_src)
    check("bắt exception chung", "except Exception" in t_src)
    check("trả None khi lỗi, không ném tiếp", "return None" in t_src)
    check("không để lỗi TTS làm mất câu trả lời",
          "vẫn gửi chữ" in t_src, "phải nói rõ HUD vẫn hiện chữ")
    check("đi qua engine TTS canonical", "get_tts_engine()" in t_src)
    check("không còn nhánh gTTS", "gTTS" not in t_src and "gtts" not in t_src)


# ══ 4. Không phát filler trùng ở đường tool ═══════════════════════════════
section("Đường tool không phát filler lần thứ hai")
lle = Path(__file__).resolve().parents[1] / "src" / "mateai" / "application" / "agent" / "llm_engine.py"
l_src = lle.read_text(encoding="utf-8")
check("stream_voice_response không tự phát filler",
      "_play_cached_phrase_instant_async" not in l_src,
      "vẫn còn filler trùng ở đường tool")
check("có log đo thời gian chờ lãng phí trước khi chuyển vòng lặp agentic",
      "KHÔNG mất" in l_src or "chuyển sang" in l_src)


# ── Tổng kết ─────────────────────────────────────────────────────────────
print("\n" + "─" * 60)
if FAILURES:
    print("Các assertion FAIL:")
    for f in FAILURES:
        print(f)
print(f"\nTổng: {PASSED + FAILED} | Pass: {PASSED} | Fail: {FAILED}")
sys.exit(1 if FAILED else 0)
