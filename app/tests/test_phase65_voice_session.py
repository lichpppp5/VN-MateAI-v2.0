# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 Dương Thanh Lịch — VN-MateAI. See LICENSE and NOTICE.
"""
tests/test_phase65_voice_session.py
===================================
Kiểm thử Phase 65 — phiên hội thoại liên tục cho HUD.

Ba lỗi admin phản ánh, mỗi lỗi có phần test riêng:
  1. Lệnh lặp lại    → lỗi này ở JS, nhưng server phải truyền history
  2. Phản hồi chậm   → server phải báo trạng thái "đang chờ đáp"
  3. Không hỏi lại    → phải đếm được số lần im lặng, và bỏ cuộc có giới hạn
"""

from __future__ import annotations

import asyncio
import sys
import time
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


from mateai.application.voice.voice_session import (  # noqa: E402
    SESSION_TTL,
    VoiceSession,
    VoiceSessionStore,
    is_stop_reply,
    looks_like_question,
)


# ══ 1. Lịch sử hội thoại ═════════════════════════════════════════════════
# Phase 3: một kho lịch sử cho mọi kênh — memory_manager, khoá phiên "hud".
section("Lịch sử hội thoại (chống lặp lệnh) — memory_manager")
from mateai.application.conversation.memory_manager import memory_manager  # noqa: E402

_sid = "test-phase65-hud"
memory_manager.clear_history(_sid)
check("phiên mới rỗng", memory_manager.get_history(_sid) == [])
memory_manager.add_turn(_sid, "Báo cáo tồn kho ERP", "Anh muốn báo cáo tháng nào ạ?")
h = memory_manager.get_history(_sid)
check("lưu đủ lượt user + assistant", len(h) == 2, str(len(h)))
check("đúng vai trò từng lượt", [t["role"] for t in h] == ["user", "assistant"], str(h))
check("history trả dict role/content", all({"role", "content"} <= set(t) for t in h), str(h[0]))
check("VoiceSession không còn kho lịch sử riêng", not hasattr(VoiceSession("hud"), "turns"))
memory_manager.clear_history(_sid)


# ══ 2. Nhận diện câu hỏi ══════════════════════════════════════════════════
section("Nhận diện câu cần chờ trả lời")
check("câu hỏi có dấu ?", looks_like_question("Anh muốn báo cáo tháng nào ạ?"))
check("câu hỏi tiếng Việt dấu ?", looks_like_question("Báo cáo nào ạ?"))
check("câu kết bằng dấu chấm -> không hỏi", not looks_like_question("Đã xuất xong."))
check("câu tuyên bố KHÔNG hỏi", not looks_like_question("Tổng giá trị tồn kho là 104 triệu."))

# Câu có dấu ? nhưng thực chất là thông báo -> không mở mic vô ích
check("thông báo có dấu ? vẫn không hỏi",
      not looks_like_question("Đã xuất xong?"),
      "mở mic vô ích sau khi đã xong việc")
check("thông báo dài có dấu ? vẫn không hỏi",
      not looks_like_question("Em đã kiểm tra xong 4 connector, tất cả đều hoạt động bình thường?"))
check("câu hỏi thật vẫn bắt được dù có chữ 'xong'",
      looks_like_question("Anh muốn xử lý tiếp hay để em xử lý xong phần còn lại ạ?"))

check("chuỗi rỗng -> không hỏi", not looks_like_question(""))
check("None -> không hỏi", not looks_like_question(None))

section("Lệnh dừng của admin")
for w in ["dừng", "thôi", "hủy", "huỷ", "bỏ qua", "không cần", "stop", "cancel"]:
    check(f"'{w}' -> dừng", is_stop_reply(w), w)
check("câu hỏi bình thường không phải lệnh dừng", not is_stop_reply("Anh muốn báo cáo tháng nào?"))
# câu GHÉP (2026-10-12): "không cần, cảm ơn bạn" từng bị gửi như lệnh mới
for w in ["không cần , cảm ơn bạn", "dạ không ạ, cảm ơn em nhé", "thôi, cảm ơn em", "hết rồi, cảm ơn", "không cần đâu, cảm ơn nhiều"]:
    check(f"'{w}' -> dừng (câu ghép)", is_stop_reply(w), w)
for w in ["không, kiểm tra ổ đĩa giúp anh", "cảm ơn, giờ xem giúp anh doanh thu", "không biết hôm nay CPU thế nào", "được rồi"]:
    check(f"'{w}' -> KHÔNG dừng (còn yêu cầu)", not is_stop_reply(w), w)


# ══ 3. Đếm lượt hỏi lại ══════════════════════════════════════════════════
section("Đếm lượt hỏi lại và đóng lắng nghe")
s5 = VoiceSession("hud")
check("chưa hỏi -> không chờ", s5.expecting_reply is False)
check("chưa hỏi -> số lần hỏi lại = 0", s5.reask_count == 0)

s5.mark_expecting_reply("Anh muốn báo cáo tháng nào ạ?")
check("hỏi -> bật chờ", s5.expecting_reply is True)
check("lưu câu hỏi", s5.pending_question.startswith("Anh muốn báo cáo"))
check("đặt lại bộ đếm về 0", s5.reask_count == 0)

check("bump lần 1 -> 1", s5.bump_reask() == 1)
check("bump lần 2 -> 2", s5.bump_reask() == 2)
check("sau 2 lần -> HUD sẽ đóng lắng nghe", s5.reask_count >= 2)

s5.clear_expecting_reply()
check("admin đáp -> tắt chờ", s5.expecting_reply is False)
check("admin đáp -> xoá câu hỏi", s5.pending_question == "")
check("admin đáp -> reset bộ đếm", s5.reask_count == 0)

# Hỏi mới thì phải reset bộ đếm, không cộng dồn từ câu hỏi cũ
s6 = VoiceSession("hud")
s6.mark_expecting_reply("Câu hỏi một?")
s6.bump_reask()
s6.bump_reask()
check("đã hỏi lại 2 lần", s6.reask_count == 2)
s6.mark_expecting_reply("Câu hỏi hai?")
check("hỏi mới -> reset bộ đếm", s6.reask_count == 0, str(s6.reask_count))
check("hỏi mới -> lưu câu mới", s6.pending_question == "Câu hỏi hai?")

section("Đo thời gian chờ")
s7 = VoiceSession("hud")
s7.mark_expecting_reply("Chờ tôi?")
check("vừa hỏi -> chờ ~0s", s7.waiting_seconds() < 0.5, str(s7.waiting_seconds()))
time.sleep(0.3)
check("sau 0.3s -> chờ >0.2s", s7.waiting_seconds() > 0.2, str(s7.waiting_seconds()))


# ══ 4. Kho phiên ═════════════════════════════════════════════════════════
section("Kho phiên")
store = VoiceSessionStore(ttl_seconds=2)
a = store.get("hud")
check("cùng id -> cùng phiên", store.get("hud") is a)
check("id khác -> phiên khác", store.get("khac") is not a)

check("drop phiên tồn tại", store.drop("hud") is True)
check("drop phiên đã xoá -> False", store.drop("hud") is False)

# Hết hạn
s8 = store.get("hud2")
check("phiên mới chưa hết hạn", not s8.is_expired())
time.sleep(2.1)
# KHÔNG gọi store.get("hud2") ở đây: get() tạo phiên mới nếu chưa có, sẽ
# làm mất ý nghĩa của phép kiểm tra. Nhìn thẳng vào kho.
check("hết hạn -> còn nằm trong kho cho tới lúc purge", "hud2" in store._sessions)
store._purge()
check("purge dọn phiên hết hạn", "hud2" not in store._sessions)

section("Thread safety")
import threading  # noqa: E402

store2 = VoiceSessionStore(ttl_seconds=600)
errs: list = []


def _w(i: int) -> None:
    try:
        sess = store2.get(f"s{i % 5}")
        sess.bump_reask()
    except Exception as exc:  # pragma: no cover
        errs.append(str(exc))


ts = [threading.Thread(target=_w, args=(i,)) for i in range(40)]
for t in ts:
    t.start()
for t in ts:
    t.join()
check("40 luồng ghi song song không lỗi", not errs, str(errs[:2]))

section("Phiên bị chia tay")
s9 = VoiceSession("hud")
s9.mark_expecting_reply("hỏi một?")
s9.bump_reask()
s9.clear_expecting_reply()
check("đóng phiên -> không còn chờ", s9.expecting_reply is False)
check("đóng phiên -> bộ đếm về 0", s9.reask_count == 0)

section("to_client")
s10 = VoiceSession("hud")
s10.mark_expecting_reply("Anh cần gì ạ?")
c = s10.to_client()
check("to_client đủ trường HUD cần",
      all(k in c for k in ("expecting_reply", "question" if False else "pending_question",
                           "reask_count", "waiting_seconds")),
      str(list(c)))
check("to_client KHÔNG kèm lịch sử", "turns" not in c, str(list(c)))


# ── Tổng kết ─────────────────────────────────────────────────────────────
print("\n" + "─" * 60)
if FAILURES:
    print("Các assertion FAIL:")
    for f in FAILURES:
        print(f)
print(f"\nTổng: {PASSED + FAILED} | Pass: {PASSED} | Fail: {FAILED}")
sys.exit(1 if FAILED else 0)
