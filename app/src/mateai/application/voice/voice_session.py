# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 Dương Thanh Lịch — VN-MateAI. See LICENSE and NOTICE.
"""
core/voice_session.py
=====================
Phase 65 — Phiên hội thoại liên tục cho HUD.

Vấn đề
------
HUD cũ gọi `stream_voice_response()` mà KHÔNG truyền `history`. Mỗi lượt nói
đều là một cuộc trò chuyện mới, nên Ly Ly không nhớ mình vừa hỏi gì, không
nhớ admin đã trả lời gì. Hậu quả đúng như admin phản ánh: câu lệnh bị lặp
lại, báo cáo ra sai.

Phase 3 (2026-10): LỊCH SỬ không còn ở đây. Mọi kênh voice dùng một kho:
`memory_manager`, khoá = session_id (HUD: "hud"), do `stream_voice_response`
ghi (gốc rễ của lỗi trên là nó chỉ lưu phần dư sau câu cuối — đã sửa). Module
này chỉ còn trạng thái "Ly Ly vừa hỏi, đang chờ admin đáp".

Tách khỏi `state_manager`
-------------------------
`state_manager` giữ *pending action* — một tác vụ đang chờ duyệt, lấy ra thì
xoá. Hội thoại HUD thì khác: cần giữ N lượt, có thời hạn, và phải sống lâu hơn
một tác vụ. Nhét vào chung sẽ làm hai thứ cùng đặt tên "chờ" nhưng khác nghĩa,
rồi sửa cái này là vỡ cái kia.

Ba trạng thái của một lượt nói
-----------------------------
    AI hỏi  ->  chờ admin  ->  admin đáp / không đáp

`expecting_reply` là phần cốt lõi: nó cho biết Ly Ly vừa hỏi một câu và đang
chờ. HUD dùng cờ này để mở lại mic, và đếm số lần không có phản hồi để biết
khi nào hỏi lại rồi bỏ cuộc.

Không tự đoán "AI có đang hỏi không"
-----------------------------------
Đoán bằng dấu chấm câu là cách dễ sai: "Đã xuất xong." không phải câu hỏi,
còn "Anh muốn báo cáo nào?" thì là. Nên `mark_expecting_reply()` do phía gọi
bật tường minh, kèm lý do — thứ gì đó không tự bịa ra "chờ phản hồi" rồi
treo mic vô ích.
"""

from __future__ import annotations

import logging
import re
import threading
import time
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)


#: Phiên không dùng trong 10 phút thì bị bỏ. HUD có thể để cả đêm không ai
#: nói chuyện; giữ lại lịch sử cũ chỉ tốn bộ nhớ và làm nhiễu ngữ cảnh lượt sau.
SESSION_TTL = 600





#: Độ dài tối đa của câu hỏi đang chờ lưu lại (câu trả lời dài không làm phình trạng thái).
MAX_TURN_CHARS = 1200


def _clip(text: str, limit: int = MAX_TURN_CHARS) -> str:
    """
    Cắt bớt lượt quá dài, cắt ở ranh giới từ để không băm vỡ câu.

    Dấu "…" báo hiệu đã cắt cũng nằm trong `limit` — tính ngoài thì lượt dài
    đúng hạn vẫn vượt, và giới hạn lịch sử trở nên không đáng tin.
    """
    s = str(text or "").strip()
    if len(s) <= limit:
        return s
    budget = max(1, limit - 1)  # dành 1 ký tự cho dấu "…"
    cut = s[:budget]
    sp = cut.rfind(" ")
    body = cut[:sp] if sp > budget * 0.6 else cut
    return body.rstrip() + "…"


class VoiceSession:
    """Trạng thái chờ phản hồi cho một phiên HUD (lịch sử ở memory_manager)."""

    def __init__(self, session_id: str) -> None:
        self.session_id = session_id
        self.updated_at = time.time()

        # Đang chờ admin trả lời sau câu hỏi của AI.
        self.expecting_reply = False
        self.pending_question = ""
        # Số lần đã hỏi lại vì im lặng. Tới ngưỡng thì HUD đóng lắng nghe.
        self.reask_count = 0
        # Mốc để HUD biết đã chờ bao lâu mà không có phản hồi.
        self.asked_at = 0.0



    def clear(self) -> None:
        self.expecting_reply = False
        self.pending_question = ""
        self.reask_count = 0
        self.asked_at = 0.0
        self.updated_at = time.time()

    # ── Trạng thái chờ phản hồi ─────────────────────────────────────────

    def mark_expecting_reply(self, question: str) -> None:
        """Bật chờ phản hồi, ghi lại câu hỏi và đặt lại bộ đếm hỏi lại."""
        self.expecting_reply = True
        self.pending_question = _clip(question)
        self.reask_count = 0
        self.asked_at = time.time()
        self.updated_at = self.asked_at

    def clear_expecting_reply(self) -> None:
        """Admin đã đáp — không còn chờ, không cần hỏi lại nữa."""
        self.expecting_reply = False
        self.pending_question = ""
        self.reask_count = 0
        self.asked_at = 0.0
        self.updated_at = time.time()

    def bump_reask(self) -> int:
        """Tăng bộ đếm hỏi lại. Trả số lần sau khi tăng."""
        self.reask_count += 1
        self.asked_at = time.time()
        self.updated_at = self.asked_at
        return self.reask_count

    def waiting_seconds(self) -> float:
        return max(0.0, time.time() - self.asked_at) if self.asked_at else 0.0

    def is_expired(self, now: Optional[float] = None) -> bool:
        return (now or time.time()) - self.updated_at > SESSION_TTL

    def to_client(self) -> Dict[str, Any]:
        """Trạng thái gửi HUD. Không kèm lịch sử — HUD không cần đọc lại."""
        return {
            "session_id": self.session_id,
            "expecting_reply": self.expecting_reply,
            "pending_question": self.pending_question,
            "reask_count": self.reask_count,
            "waiting_seconds": round(self.waiting_seconds(), 1),
        }


class VoiceSessionStore:
    """Kho phiên HUD, an toàn khi gọi từ nhiều luồng."""

    def __init__(self, ttl_seconds: int = SESSION_TTL) -> None:
        self._lock = threading.RLock()
        self._sessions: Dict[str, VoiceSession] = {}
        self._ttl = ttl_seconds

    def _purge(self, now: Optional[float] = None) -> None:
        now = now or time.time()
        dead = [sid for sid, s in self._sessions.items() if (now - s.updated_at) > self._ttl]
        for sid in dead:
            self._sessions.pop(sid, None)
        if dead:
            logger.info("[VoiceSession] Đã dọn %d phiên hết hạn", len(dead))

    def get(self, session_id: str) -> VoiceSession:
        with self._lock:
            self._purge()
            sess = self._sessions.get(session_id)
            if sess is None:
                sess = VoiceSession(session_id)
                self._sessions[session_id] = sess
            return sess

    def drop(self, session_id: str) -> bool:
        with self._lock:
            return self._sessions.pop(session_id, None) is not None

    def clear_all(self) -> int:
        with self._lock:
            n = len(self._sessions)
            self._sessions.clear()
            return n

    def __len__(self) -> int:
        with self._lock:
            self._purge()
            return len(self._sessions)


#: Phiên dùng chung cho HUD. HUD chỉ có một người dùng quản trị nên một phiên
#: `default` là đủ; vẫn để khoá theo id để sau này thêm nhiều người không phải
#: sửa cấu trúc.
voice_sessions = VoiceSessionStore()


# ── Nhận diện câu hỏi cần chờ trả lời ────────────────────────────────────

#: Dấu hiệu Ly Ly đang hỏi admin, cần chờ đáp.
_QUESTION_MARKS = ("?", "？")

#: Câu kết bằng dấu chấm hỏi nhưng thực chất là mệnh lệnh/ tuyên bố.
#: "Đã xuất xong?" hiếm gặp, nhưng "Anh có cần em làm gì tiếp không ạ?" thì
#: không phải — nên tách riêng để không mở mic vô ích.
#: Cửa sổ 80 ký tự thay vì 40: "Em đã kiểm tra xong 4 connector, tất cả đều
#: hoạt động bình thường?" dài hơn 40 và là thông báo, không phải câu hỏi.
#: Hẹp quá thì HUD mở mic và chờ một câu không bao giờ được đáp.
_STATEMENT_WITH_Q = re.compile(
    r"(đã\s+(xuất|kiểm tra|lấy|gửi|tạo|xong|thành công)|hoàn tất|xong rồi)\b[^?]{0,80}\?$",
    re.IGNORECASE,
)

#: Lệnh từ chối / kết thúc — admin muốn dừng, không hỏi lại nữa.
_STOPWORDS = (
    "dừng", "thôi", "huỷ", "hủy", "bỏ qua", "không cần", "tạm dừng",
    "stop", "cancel", "never mind", "thôi vậy",
)


#: Câu hỏi lại khi trả lời xong mà chưa nghe chỉ lệnh mới (HUD và robot).
FOLLOW_UP_PHRASE = "Anh còn cần em hỗ trợ gì nữa không ạ?"
#: Câu chào khi chờ 30 giây không nghe thấy chỉ lệnh mới (HUD và robot).
FAREWELL_PHRASE = "Nếu anh không có yêu cầu nào khác thì tạm biệt, hẹn gặp lại anh nhé."
#: Câu chào khi admin trả lời "không / không cần, cảm ơn…" sau câu hỏi lại (HUD và robot) — rồi về trạng thái chờ.
#: Trước 2026-10-12 đóng im lặng; người dùng muốn trợ lý chào tạm biệt như hội thoại thật.
GOODBYE_PHRASE = "Dạ vâng, em chào anh. Khi nào cần anh cứ gọi em nhé."


def looks_like_question(text: str) -> bool:
    """
    Có nên chờ admin trả lời sau câu này không.

    Chỉ là gợi ý — phía gọi vẫn có quyền quyết định. Sai theo hướng này (hỏi
    lại một câu không cần) thì phiền; sai theo hướng kia (không mở mic khi
    thật sự đang hỏi) thì hội thoại đứt.
    """
    s = str(text or "").strip()
    if not s:
        return False
    # Chỉ xét CÂU CUỐI: "Em đã kiểm tra xong máy chủ. Anh muốn xem ổ nào ạ?" là
    # câu hỏi — xét cả đoạn thì phần "đã kiểm tra" khớp mẫu thông báo.
    s = re.split(r"(?<=[.!?…])\s+", s)[-1]
    if _STATEMENT_WITH_Q.search(s):
        return False
    return any(m in s for m in _QUESTION_MARKS)


#: Câu trả lời "không còn yêu cầu nào" khi trợ lý hỏi "Anh còn cần gì nữa không?".
_DONE_PHRASES = frozenset({
    "không", "ko", "k", "không có", "không có gì", "không còn", "không còn gì", "không cần",
    "không cần đâu", "hết", "xong", "vậy thôi", "thôi", "dừng", "dừng lại", "hủy", "huỷ",
    "bỏ qua", "tạm dừng", "đủ", "cảm ơn", "cám ơn", "tạm biệt", "bye", "stop", "cancel",
    "never mind", "no", "nope", "không có gì cả", "không có yêu cầu", "không có yêu cầu gì",
    "ổn", "đủ rồi", "thanks", "thank you", "cảm ơn nhiều", "không cần nữa", "không còn yêu cầu",
    "không còn việc gì", "không có việc gì", "tạm thời không", "hết việc",
})
#: Câu dài nhất (số từ) trong _DONE_PHRASES — giới hạn khi ghép câu.
_DONE_MAX_WORDS = max(len(p.split()) for p in _DONE_PHRASES)
#: Từ lễ phép / đệm — bỏ đi trước khi so (vd "dạ không có gì nữa đâu em").
_POLITE = frozenset({
    "dạ", "vâng", "à", "ạ", "a", "em", "anh", "chị", "nhé", "nha", "nhá", "nhe", "đâu", "nữa",
    "rồi", "được", "ok", "oke", "okay", "thế", "vậy", "là", "đã", "cả", "lắm", "nhiều",
    "bạn", "ơi", "mình", "luôn", "hen", "nghen", "nhen", "nhỉ", "ha", "hả", "nhá", "Ly", "ly",
})


def _all_done_phrases(words: list) -> bool:
    """Cả dãy từ ghép được hoàn toàn từ các câu kết thúc ("không cần" + "cảm ơn") — không còn từ nào khác."""
    ok = [True] + [False] * len(words)                 # ok[i]: words[:i] ghép được
    for i in range(1, len(words) + 1):
        for k in range(1, min(_DONE_MAX_WORDS, i) + 1):
            if ok[i - k] and " ".join(words[i - k:i]) in _DONE_PHRASES:
                ok[i] = True
                break
    return ok[-1]


def is_stop_reply(text: str) -> bool:
    """
    Câu NGẮN chỉ nói "không còn gì / thôi / cảm ơn / tạm biệt" — admin muốn kết
    thúc hội thoại. Chỉ dùng khi trợ lý đang chờ trả lời.

    So theo cả câu (sau khi bỏ từ lễ phép), không theo chuỗi con: trước đây
    "không cần" nằm trong một câu hỏi thật ("có cần … không cần …") cũng bị coi
    là dừng; còn "dạ không có gì nữa đâu em" thì không được nhận ra.

    Câu GHÉP cũng là kết thúc khi mọi phần đều là câu kết thúc: "không cần, cảm ơn bạn", "thôi, cảm ơn em nhé",
    "không ạ, cảm ơn" (2026-10-12: trước đây chỉ khớp NGUYÊN câu nên "không cần, cảm ơn bạn" bị gửi đi như lệnh mới
    → trợ lý nói "Em đang xử lý…" thay vì chào). Câu có từ lạ ("không biết CPU thế nào") vẫn là lệnh.
    """
    words = re.findall(r"\w+", str(text or "").lower())
    if not words or len(words) > 10:
        return False
    core = [w for w in words if w not in _POLITE]
    if not core:
        return False
    joined = " ".join(core)
    return joined in _STOPWORDS or _all_done_phrases(core)
