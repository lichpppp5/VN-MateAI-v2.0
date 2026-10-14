# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 Dương Thanh Lịch — VN-MateAI. See LICENSE and NOTICE.
"""
mateai/application/ethics/refusals.py
=====================================
Ghi nhận khi AI TỪ CHỐI một yêu cầu vì pháp luật / đạo đức / quyền riêng tư / chính sách (đạo đức AI, điểm 3 — 2026-10-12).

Lệnh bị CỔNG chính sách chặn đã có trong audit_logs (REJECTED + lý do). Còn khi chính MÔ HÌNH từ chối trong hội thoại ("việc này
vi phạm pháp luật…") thì trước đây không để lại dấu vết nào. Nay câu trả lời mang dấu hiệu từ chối + loại lý do được ghi
`AI_REFUSAL` (trạng thái REFUSED) kèm câu hỏi gốc (đã che dữ liệu cá nhân) để kiểm tra sau. Từ chối vì THIẾU KHẢ NĂNG
("em chưa tra được") không ghi — đó không phải vấn đề đạo đức.
"""
from __future__ import annotations

import re
from typing import Optional

#: Dấu hiệu câu trả lời là lời TỪ CHỐI.
_REFUSAL = re.compile(
    r"(?i)(em (xin phép )?(không thể|không được phép|không làm|không hỗ trợ|từ chối|xin từ chối)|"
    r"xin lỗi,? (nhưng )?em không|em không (thể )?(giúp|thực hiện|cung cấp)|i can(no|')t help|i won'?t)")
#: Loại lý do — kiểm theo thứ tự (pháp luật trước).
_CATEGORIES = (
    ("phap_luat", re.compile(r"(?i)(pháp luật|bất hợp pháp|phạm pháp|trái luật|vi phạm (luật|quy định)|lừa đảo|gian lận|illegal)")),
    ("quyen_rieng_tu", re.compile(r"(?i)(quyền riêng tư|dữ liệu cá nhân|thông tin cá nhân|nghị định 13|theo dõi lén|privacy)")),
    ("dao_duc", re.compile(r"(?i)(đạo đức|gây hại|nguy hiểm cho|phân biệt đối xử|xúc phạm|bạo lực|thù ghét|harm)")),
    ("chinh_sach", re.compile(r"(?i)(không có quyền|chính sách|quy định (nội bộ|công ty)|bị chặn|không được phép|cần phê duyệt)")),
)


def refusal_category(reply: str) -> Optional[str]:
    """Loại lý do nếu `reply` là lời từ chối vì pháp luật / quyền riêng tư / đạo đức / chính sách; None nếu không."""
    text = str(reply or "")
    if not text or not _REFUSAL.search(text):
        return None
    for name, pattern in _CATEGORIES:
        if pattern.search(text):
            return name
    return None


def record_refusal(reply: str, query: str, caller: Optional[str], channel: str = "") -> Optional[str]:
    """Ghi `AI_REFUSAL` vào audit_logs nếu câu trả lời là lời từ chối có lý do đạo đức / pháp lý. Trả loại (hoặc None)."""
    category = refusal_category(reply)
    if not category:
        return None
    try:
        from mateai.application.security.pii_guard import mask_text
        from mateai.application.security.safety_guard import security_engine
        security_engine.log_audit(str(caller or "unknown"), "AI_REFUSAL", "0", "REFUSED", {
            "category": category, "channel": channel,
            "query": mask_text(str(query or ""))[0][:300], "reply": mask_text(str(reply or ""))[0][:400],
        })
    except Exception:  # noqa: BLE001 — ghi nhận không được làm hỏng câu trả lời
        pass
    return category
