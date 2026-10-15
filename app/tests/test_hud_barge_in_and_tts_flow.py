#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 Dương Thanh Lịch — VN-MateAI. See LICENSE and NOTICE.
"""
Phase 81: HUD phải NGẮT được lời đang nói, và TTS không được đọc vụn vặt.

Hai lỗi người dùng phản ánh:
  1. "Ra lệnh mới nhưng AI phải đọc hết câu cũ rồi mới dừng" — mất cảm giác
     tương tác.
  2. "LLM đọc bị ngắt quãng, cứ đọc 2 3 chữ một".

Lỗi 2 không phải độ trễ, mà là CÁCH CHIA CÂU. Bản cũ cắt theo dấu phẩy và
chỉ đòi mảnh dài tối thiểu 4 ký tự, nên câu tiếng Việt bị vỡ thành 3-5 mảnh;
mỗi mảnh là một file mp3 riêng, có khoảng lặng riêng, phát nối tiếp nhau.
Tệ hơn: dấu phẩy trong SỐ bị hiểu là ranh giới, nên "2,4 tỷ đồng" bị tách
làm "2," + "4 tỷ" — đọc lên sai số.
"""

import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from mateai.application.voice.sentence_buffer import SentenceBuffer  # noqa: E402

PASS = 0
FAIL = 0


def check(label: str, condition: bool, detail: str = "") -> None:
    global PASS, FAIL
    if condition:
        PASS += 1
        print(f"  ok   {label}")
    else:
        FAIL += 1
        print(f"  FAIL {label}" + (f" — {detail}" if detail else ""))


def stream_chunks(text: str) -> list:
    """Đo đúng như vòng lặp thật: đổ dần từng token vào rồi lấy câu ra.

    Phase 5: bộ tách câu của đường voice là SentenceBuffer (cấu hình như trong
    LLMEngine.stream_voice_response); `LLMEngine._extract_sentences` đã gỡ.
    """
    buf = SentenceBuffer(min_chars=1, min_words=8, max_words=30)
    out = []
    for tok in text.split(" "):
        out.extend(buf.add_token(tok + " "))
    out.extend(buf.flush())
    return out


# ══ 1. KHÔNG được vỡ thành mảnh 2-3 từ ══════════════════════════════════
print("\n▸ Câu tiếng Việt dài — không được cắt vụn")
CAU = ("Dạ anh, hệ thống đã xử lý xong 15 hồ sơ tháng này, tổng giá trị "
       "2,4 tỷ đồng. Tuy nhiên còn 3 hồ sơ bị treo, nguyên nhân là thiếu "
       "chữ ký số. Em đề xuất anh kiểm tra lại phần quyền ký, sau đó chạy lại "
       "batch thứ hai nhé.")
chunks = stream_chunks(CAU)
check("số mảnh vừa phải (không vụn)", len(chunks) <= 4, f"{len(chunks)} mảnh: {chunks}")
check("không mảnh nào dưới 5 từ",
      all(len(c.split()) >= 5 for c in chunks),
      str([(len(c.split()), c[:40]) for c in chunks]))
check("không mất chữ nào",
      re.sub(r"\s+", "", " ".join(chunks)) == re.sub(r"\s+", "", CAU))

print("\n▸ Câu cực ngắn phải giữ nguyên, không bị bỏ")
check("'Vâng ạ.' vẫn ra", stream_chunks("Vâng ạ.") == ["Vâng ạ."],
      str(stream_chunks("Vâng ạ.")))

print("\n▸ Rỗng")
check("chuỗi rỗng -> không mảnh nào", stream_chunks("   ") == [])
_empty = SentenceBuffer(min_chars=1, min_words=8, max_words=30)
check("rỗng -> không câu nào", _empty.add_token("") == [] and _empty.flush() == [])


# ══ 2. Số thập phân KHÔNG được vỡ ═════════════════════════════════════════
print("\n▸ Dấu phẩy trong số không phải ranh giới câu")
out = stream_chunks("Tổng chi là 2,4 tỷ đồng. Chi tiêu tăng 15,7 phần trăm.")
check("giữ nguyên 2,4", any("2,4 tỷ đồng" in c for c in out), str(out))
check("giữ nguyên 15,7", any("15,7 phần trăm" in c for c in out), str(out))
check("không cắt đôi số", not any(re.search(r"\b\d+,\s*$", c) for c in out), str(out))


# ══ 3. Câu không dấu kết vẫn phải cắt, không tràn ════════════════════════
print("\n▸ Câu dài không dấu kết")
run_on = ("hệ thống đã xử lý xong toàn bộ hồ sơ tháng này và đang chờ anh phê "
          "duyệt kết quả tổng hợp trước khi chuyển sang kỳ báo cáo tiếp theo")
out = stream_chunks(run_on)
check("vẫn cắt thành mảnh", len(out) >= 1)
check("không mảnh nào vượt 35 từ",
      all(len(c.split()) <= 35 for c in out), str([len(c.split()) for c in out]))
check("không cắt giữa từ",
      all(" ".join(stream_chunks(run_on)).replace(" ", "").count(w) >=
          run_on.replace(" ", "").count(w) for w in ["không", "báo"]))

print("\n▸ Câu dài có dấu phẩy — cắt ở chỗ thở tự nhiên")
dai = "Anh ạ, " + ", ".join(f"mục {i} đã hoàn tất và kiểm tra xong" for i in range(1, 9)) + ". Xong rồi ạ."
out = stream_chunks(dai)
check("chia thành nhiều mảnh", len(out) >= 2, str(len(out)))
check("không mảnh nào quá dài", all(len(c.split()) <= 32 for c in out),
      str([len(c.split()) for c in out]))
check("câu cuối giữ nguyên", out[-1].strip().startswith("Xong rồi"), out[-1])


# ══ 4. Ngắt lời phía máy chủ ═════════════════════════════════════════════
print("\n▸ Máy chủ huỷ lượt thoại cũ khi có lệnh mới")
# Luồng lượt thoại ở hud_voice.py; kênh /ws/hud (nhận lệnh, huỷ lượt cũ) ở tầng HTTP.
_http = Path(__file__).resolve().parent.parent / "src" / "mateai" / "interfaces" / "http"
server_py = "\n\n".join(p.read_text(encoding="utf-8") for p in [_http / "hud_voice.py", _http / "server.py", *sorted((_http / "routers").glob("*.py"))])
check("có sổ theo dõi task theo phiên", "active_tasks" in server_py)
check("có hàm huỷ lượt đang chạy", "def cancel_task" in server_py)
check("lệnh mới gọi huỷ trước khi tạo task mới",
      server_py.index("hud_voice.cancel_task(\"hud\")")
      < server_py.index("_spawn(hud_voice.process_command("))
# Dọn sổ phải ở `finally` — dọn ở từng nhánh return là dễ sót, và hàm thân có
# nhiều nhánh return sớm.
wrapper = server_py.split("async def process_command(", 1)[-1].split("async def process_command_body(", 1)[0]
check("dọn sổ task nằm trong finally", "finally:" in wrapper and "active_tasks.pop" in wrapper)
check("báo HUD đã bị ngắt", '"interrupted": True' in server_py)

print("\n▸ HUD dừng phát ngay khi có lệnh mới")
hud_js = (Path(__file__).resolve().parent.parent / "web" / "hud.js").read_text(encoding="utf-8")
check("có hàm dừng nói", "function hudStopSpeaking" in hud_js)
check("xoá cả hàng đợi chứ không chỉ câu đang phát",
      "hudSpeechQueue = [];" in hud_js)
# Dừng mà để lại draining=true thì hàng đợi kẹt vĩnh viễn, mọi lượt sau im
# luôn — phải có chỗ reset cờ này trong hàm dừng.
stop_body = hud_js.split("function hudStopSpeaking()", 1)[-1].split("function toggleHudAudio", 1)[0]
check("trả cờ draining về false", "hudSpeechDraining = false;" in stop_body)
check("bỏ handler onended trước khi dừng",
      stop_body.index("onended = null") < stop_body.index(".pause()"))
check("lệnh mới gọi hàm dừng", "hudStopSpeaking();" in hud_js)
send_body = hud_js.split("async function sendHudVoiceCommand(query)", 1)[-1][:900]
check("dừng ngay khi nhận lệnh mới", "hudStopSpeaking()" in send_body)

# ══ 5. Chữ phải bám theo tiếng, không hiện hết trước ══════════════════════
print("\n▸ Chữ và tiếng phải khớp nhau")
check("HUD có chỗ đánh dấu vị trí đang đọc",
      "spokenUpTo" in hud_js,
      "thiếu vị trí đọc để chữ bám theo tiếng")
check("card tô sáng phần đã đọc, mờ phần chưa đọc",
      "opacity-40" in hud_js and "emerald-300" in hud_js)
check("vị trí đọc được cập nhật lúc audio BẮT ĐẦU phát",
      "spokenUpTo" in hud_js.split("player.onplay")[-1][:600],
      "chỉ cập nhật khi nhận gói tin thì chữ vẫn chạy trước tiếng")
# `display_text` là TOÀN BỘ câu trả lời. Nếu card hiện nguyên nó ngay khi gói
# tin tới thì chữ luôn nhanh hơn tiếng — đúng triệu chứng người dùng báo.
check("gói tin mang vị trí đọc xuống",
      "spokenUpTo: packet.display_text" in hud_js)

print("\n▸ Không được bày hiệu ứng nói khi không có tiếng")
# Nhánh không có audio trước đây vẫn bật trạng thái "đang nói" và chạy chữ với
# thời lượng ĐOÁN — nhìn như đang nói trong khi không có tiếng nào.
noaudio = hud_js.split("} else {", 1)[-1].split("isAudioPlaying = false;", 1)[-1][:500]
check("nhánh không có audio không bật hiệu ứng nói",
      "setHudState('speaking'" not in noaudio,
      "vẫn hiệu ứng đang nói dù không có audio")
check("nhánh không có audio nói rõ là không có tiếng",
      "không có tiếng" in hud_js)
check("hiệu ứng chỉ bật trong onplay, không bật sớm",
      "player.onplay" in hud_js and
      hud_js.index("player.onplay") < hud_js.index("isAudioPlaying = true;", hud_js.index("function drainSpeechQueue")) + 200)

print("\n▸ Máy chủ không chờ TTS trong vòng lặp LLM")
# Phase 3: HUD dùng use case chung core/voice_turn.py; hàng đợi TTS gối đầu là
# core/audio/tts_queue_pipeline.py (hành vi được kiểm tra thật trong
# tests/test_hud_voice_pipeline_behavior.py).
_root = Path(__file__).resolve().parents[1]
voice_turn = (_root / "src" / "mateai" / "application" / "voice" / "voice_turn.py").read_text(encoding="utf-8")
tts_queue = (_root / "src" / "mateai" / "infrastructure" / "tts" / "tts_queue_pipeline.py").read_text(encoding="utf-8")
body = server_py.split("async def process_command_body", 1)[-1].split("def get_metrics_payload", 1)[0]
check("HUD đi qua use case chung", "process_voice_turn(" in body)
check("có hàng đợi task TTS", "StreamingTTSWorkerPipeline" in voice_turn)
# BỎ COMMENT trước khi quét: bình luận giải thích lỗi cũ lại nhắc lại đúng dòng
# đã xoá (`await s_task`), quét cả comment sẽ ra kết quả ngược.
body_code = "\n".join(l.split("#", 1)[0] for l in body.split("\n"))
check("TTS được đẩy vào hàng đợi thay vì chờ tại chỗ",
      "pipeline.push_sentence(" in voice_turn)
check("chỉ chờ khi hàng đợi vượt ngưỡng đệm (backpressure)",
      "asyncio.Queue(maxsize=max_queue_size)" in tts_queue)
check("không còn `await s_task` ngay lập tức",
      "await s_task" not in body_code,
      "tạo task rồi chờ ngay = không song song")
check("rải nốt câu cuối sau khi vòng lặp kết thúc",
      "finally:\n            await pipeline.mark_complete(spoken)" in voice_turn,
      "bỏ bước này thì câu cuối không bao giờ phát")

print(f"\nTổng: {PASS + FAIL} | Pass: {PASS} | Fail: {FAIL}")
sys.exit(1 if FAIL else 0)
