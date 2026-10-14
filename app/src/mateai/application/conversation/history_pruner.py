# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 Dương Thanh Lịch — VN-MateAI. See LICENSE and NOTICE.
"""
core/history_pruner.py
======================
Phase 9: History & Context Pruning for Realtime Voice Performance.

Mục tiêu cốt lõi:
  - Tối ưu hóa ngữ cảnh hội thoại cho đường truyền giọng nói realtime (Jarvis/XiaoZhi responsiveness).
  - Ngăn chặn triệt để hiện tượng 'Token Bloat' làm suy giảm TTFT khi đàm thoại kéo dài nhiều lượt:
      + Lược bỏ hoàn toàn bảng biểu Markdown, khối mã nguồn (code blocks), URLs dài, và metadata rác khỏi lịch sử.
      + Thu gọn các câu trả lời dài của Assistant thành 1-2 câu tóm tắt cốt lõi (hoặc trích xuất thẻ <!--VOICE:...-->).
      + Duy trì cửa sổ trượt (Sliding Window) 4-6 tin nhắn gần nhất (2-3 lượt thoại) với độ nét cao.
      + Tự động nén các lượt thoại cũ hơn thành một ghi chú ngữ cảnh súc tích [Ngữ cảnh trao đổi trước: ...].
      + Áp đặt trần dung lượng ký tự nghiêm ngặt (Character Ceiling ~1,200 ký tự ~ 300 tokens).
"""

from __future__ import annotations

import logging
import re
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

# Mẫu regex để loại bỏ mã nguồn code blocks
_CODE_BLOCK_RE = re.compile(r"```[\s\S]*?```")
# Mẫu regex để loại bỏ bảng Markdown (| col1 | col2 | ...)
_MD_TABLE_RE = re.compile(r"\|.*\|(?:\n\|.*\|)+")
# Mẫu regex để trích xuất nội dung thẻ giọng nói <!--VOICE: ... -->
_VOICE_TAG_RE = re.compile(r"<!--VOICE:\s*([\s\S]*?)\s*-->")
# Mẫu regex để loại bỏ thẻ HTML còn sót
_HTML_TAG_RE = re.compile(r"<[^>]+>")
# Mẫu regex chuẩn hóa khoảng trắng thừa
_MULTI_WS_RE = re.compile(r"\s+")


def clean_voice_content(text: str, max_chars: int = 180) -> str:
    """
    Làm sạch nội dung một tin nhắn cho kênh giọng nói:
      1. Nếu có thẻ <!--VOICE:...-->, ưu tiên lấy nội dung đó vì đây là câu tóm tắt phát ra loa.
      2. Loại bỏ toàn bộ code blocks, bảng biểu Markdown, thẻ HTML.
      3. Rút gọn văn bản về tối đa max_chars ký tự giữ trọn vẹn câu kết thúc gần nhất.
    """
    if not text:
        return ""

    text = text.strip()

    # 1. Trích xuất thẻ <!--VOICE:...--> nếu có
    voice_match = _VOICE_TAG_RE.search(text)
    if voice_match:
        extracted = voice_match.group(1).strip()
        if extracted:
            return _MULTI_WS_RE.sub(" ", extracted)[:max_chars]

    # 2. Xóa bỏ Code blocks (```...```)
    cleaned = _CODE_BLOCK_RE.sub(" [đoạn mã đã lược bỏ] ", text)

    # 3. Xóa bỏ bảng biểu Markdown (|...|)
    cleaned = _MD_TABLE_RE.sub(" [bảng số liệu đã lược bỏ] ", cleaned)

    # 4. Xóa bỏ thẻ HTML
    cleaned = _HTML_TAG_RE.sub("", cleaned)

    # 5. Chuẩn hóa khoảng trắng
    cleaned = _MULTI_WS_RE.sub(" ", cleaned).strip()

    # 6. Cắt gọt độ dài nếu vượt quá max_chars
    if len(cleaned) <= max_chars:
        return cleaned

    truncated = cleaned[:max_chars]
    # Cố gắng cắt tại dấu chấm câu gần nhất
    last_punct = max(truncated.rfind(". "), truncated.rfind("! "), truncated.rfind("? "), truncated.rfind(", "))
    if last_punct > int(max_chars * 0.6):
        return truncated[:last_punct + 1].strip()

    return truncated.rstrip() + "..."


def prune_history_for_voice(
    history: Optional[List[Dict[str, Any]]],
    max_turns: int = 4,
    max_total_chars: int = 1200,
) -> List[Dict[str, str]]:
    """
    Cắt tỉa và thu gọn lịch sử hội thoại chuyên biệt cho chế độ Giọng nói Realtime:
      - Giữ tối đa max_turns lượt thoại gần nhất (tương đương max_turns * 2 tin nhắn).
      - Làm sạch và rút gọn từng tin nhắn (loại bỏ code, bảng markdown, log rác).
      - Tóm lược các lượt thoại cũ ngoài cửa sổ trượt thành 1 tin nhắn ngữ cảnh tổng quát.
      - Đảm bảo tổng dung lượng ngữ cảnh lịch sử không vượt quá max_total_chars (~300 tokens).
    """
    if not history:
        return []

    # Lọc bỏ các tin nhắn rỗng hoặc tin nhắn hệ thống khỏi chuỗi đối thoại thô
    valid_msgs = [
        msg for msg in history
        if isinstance(msg, dict) and msg.get("content") and msg.get("role") in ("user", "assistant")
    ]

    if not valid_msgs:
        return []

    max_messages = max_turns * 2
    # Tách thành: tin nhắn cũ (cần tóm tắt) và tin nhắn mới (giữ độ nét)
    older_messages = valid_msgs[:-max_messages] if len(valid_msgs) > max_messages else []
    recent_messages = valid_msgs[-max_messages:]

    pruned: List[Dict[str, str]] = []

    # 1. Tạo tóm tắt ngữ cảnh cho các tin nhắn cũ hơn nếu có
    if older_messages:
        summary_points: List[str] = []
        for i in range(0, len(older_messages), 2):
            u_msg = older_messages[i]
            a_msg = older_messages[i + 1] if i + 1 < len(older_messages) else None
            u_text = clean_voice_content(u_msg.get("content", ""), max_chars=60)
            if a_msg:
                a_text = clean_voice_content(a_msg.get("content", ""), max_chars=60)
                summary_points.append(f"Yêu cầu: '{u_text}' → Đã phản hồi: '{a_text}'")
            else:
                summary_points.append(f"Yêu cầu: '{u_text}'")

        if summary_points:
            summary_content = "[Tóm tắt trao đổi trước: " + "; ".join(summary_points[-3:]) + "]"
            pruned.append({"role": "system", "content": summary_content})

    # 2. Xử lý các tin nhắn gần nhất
    for msg in recent_messages:
        role = str(msg.get("role", "user"))
        content = str(msg.get("content", ""))

        if role == "assistant":
            # Assistant content thường dài -> thu gọn tối đa 180 ký tự
            cleaned = clean_voice_content(content, max_chars=180)
        else:
            # User content giữ nguyên ý, chỉ loại bỏ khoảng trắng rác
            cleaned = clean_voice_content(content, max_chars=150)

        if cleaned:
            pruned.append({"role": role, "content": cleaned})

    # 3. Kiểm tra trần dung lượng tổng max_total_chars
    total_chars = sum(len(m.get("content", "")) for m in pruned)
    while total_chars > max_total_chars and len(pruned) > 2:
        # Loại bỏ tin nhắn cũ nhất (sau tin nhắn system tóm tắt nếu có)
        idx_to_remove = 1 if pruned[0].get("role") == "system" else 0
        removed = pruned.pop(idx_to_remove)
        total_chars -= len(removed.get("content", ""))
        logger.debug("[HistoryPruner] Đã loại bớt 1 tin nhắn cũ để duy trì trần dung lượng: còn %d ký tự", total_chars)

    return pruned


class RollingContextManager:
    """
    Bộ quản lý ngữ cảnh cuộn theo phiên:
    Theo dõi và cập nhật bản tóm tắt tích lũy của phiên thoại dài.
    """

    def __init__(self, max_history_turns: int = 4) -> None:
        self.max_history_turns = max_history_turns
        self._session_summaries: Dict[str, str] = {}

    def get_pruned_context(
        self,
        session_id: str,
        raw_history: List[Dict[str, Any]],
        max_total_chars: int = 1200,
    ) -> List[Dict[str, str]]:
        """Lấy danh sách tin nhắn lịch sử đã được tối ưu hóa cho giọng nói."""
        return prune_history_for_voice(
            raw_history,
            max_turns=self.max_history_turns,
            max_total_chars=max_total_chars,
        )

    def clear_session(self, session_id: str) -> None:
        """Xóa sạch ngữ cảnh của phiên."""
        self._session_summaries.pop(session_id, None)


# Global Singleton Instance
rolling_context_manager = RollingContextManager()
