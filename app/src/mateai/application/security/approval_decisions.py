# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 Dương Thanh Lịch — VN-MateAI. See LICENSE and NOTICE.
"""
mateai/application/security/approval_decisions.py
=================================================
Quyết định của người duyệt trên một tác vụ đang chờ trong hàng đợi HITL duy nhất.
Chuyển từ `routers/security.py::confirm_action_endpoint` (Supervisor Phase 10, §198);
REST và HUD dùng chung qua `interfaces/http/approval_flow.py`.

Chỉ thực thi ĐÚNG tác vụ đang chờ (tên tool, tham số, máy đích lấy từ hàng đợi);
`skill_name` gửi lên chỉ để đối chiếu. Tác vụ đã duyệt chạy qua cổng tool chung với
`approved=True`; RBAC áp theo người YÊU CẦU; audit HITL_APPROVED_* ghi người duyệt.
Kiểm quyền người duyệt (chỉ admin) là việc của tầng giao tiếp trước khi gọi vào đây.
"""
from __future__ import annotations

import json
import logging
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)


class ApprovalDecisionError(Exception):
    def __init__(self, status_code: int, detail: str):
        super().__init__(detail)
        self.status_code = status_code
        self.detail = detail


def find_pending(action_id: Optional[str] = None, skill_name: Optional[str] = None) -> Dict[str, Any]:
    """Tác vụ đang chờ (dạng `pending_view`). Có `action_id` → đúng yêu cầu đó (đã xử lý /
    hết hạn → 404, KHÔNG rơi sang yêu cầu khác). Không có → yêu cầu mới nhất (theo tên
    tool nếu có)."""
    from mateai.application.agent.tool_gate import pending_view
    from mateai.application.security.zero_trust import hitl_manager

    if action_id:
        item = hitl_manager.get_pending(action_id)
    elif not skill_name:
        items = hitl_manager.get_pending_list()
        item = items[-1] if items else None
    else:
        match = [it for it in hitl_manager.get_pending_list() if it.get("action_name") == skill_name]
        item = match[-1] if match else None
    if not item:
        raise ApprovalDecisionError(404, "Không tìm thấy tác vụ đang chờ phê duyệt (có thể đã được xử lý hoặc hết hạn).")
    pending = pending_view(item)
    tool = pending["tool_name"] or ""
    if skill_name and skill_name != tool:
        raise ApprovalDecisionError(409, f"Tác vụ đang chờ là '{tool}', không phải '{skill_name}'.")
    return pending


def reject(pending: Dict[str, Any], approver: str) -> str:
    from mateai.application.security.zero_trust import hitl_manager
    rej = hitl_manager.reject(pending["id"], rejected_by=approver, reason="Từ chối trên Cổng Web")
    if rej.get("status") != "success":
        raise ApprovalDecisionError(409, str(rej.get("message")))
    return f"Tác vụ '{pending['tool_name'] or ''}' đã bị người quản trị hủy bỏ."


async def approve(pending: Dict[str, Any], approver: str) -> Dict[str, Any]:
    """Chạy tác vụ đã duyệt, tổng hợp câu trả lời tự nhiên, ghi hành động đã xong.
    Trả {result, reply, speech_reply, query}."""
    from mateai.application.agent.llm_engine import llm_engine
    from mateai.application.agent.state_manager import state_manager
    from mateai.application.security.safety_guard import security_engine
    from mateai.application.security.zero_trust import hitl_manager

    skill = pending["tool_name"] or ""
    logger.info("[Phase 25] Admin '%s' phê duyệt tác vụ '%s' trên '%s'.", approver, skill, pending["target_client"])
    approval = await hitl_manager.approve_async(pending["id"], approved_by=approver)
    if approval.get("status") == "error":
        raise ApprovalDecisionError(409, str(approval.get("message")))
    res = approval.get("execution_result")
    if res is None:
        res = {"status": "error", "error": approval.get("execution_error") or approval.get("message")}
    query = pending.get("query") or f"Thực thi {skill}"

    masked = security_engine.mask_sensitive_data(json.dumps(res, ensure_ascii=False, default=str))
    try:
        resp = await llm_engine._call_llm(messages=[
            {"role": "system", "content": "Bạn là trợ lý AI Ly Ly (VN-MateAI). Hãy tổng hợp kết quả công cụ để trả lời súc tích, tự nhiên, kính cẩn bằng tiếng Việt cho người dùng."},
            {"role": "user", "content": query},
            {"role": "user", "content": f"Tác vụ đã được phê duyệt qua Web Portal. Kết quả công cụ `{skill}`:\n```json\n{masked}\n```\nHãy thông báo kết quả thực thi một cách rõ ràng."},
        ], tools=None)
        reply = resp.choices[0].message.content or f"Dạ, tác vụ '{skill}' đã được phê duyệt và hoàn tất thành công."
    except Exception as exc:  # noqa: BLE001 — tổng hợp lỗi không làm hỏng kết quả đã chạy
        logger.warning("[Phase 25] Lỗi synthesize câu trả lời sau duyệt: %s", exc)
        reply = f"Dạ, tác vụ '{skill}' đã được phê duyệt và thực thi thành công."

    state_manager.record_completed_action(pending, res, reply)
    return {"result": res, "reply": reply, "speech_reply": llm_engine._make_concise_speech_text(reply),
            "query": query}


def telegram_chat_of(pending: Dict[str, Any]) -> Optional[str]:
    """Chat Telegram đã gửi yêu cầu (để báo kết quả về đúng nơi), nếu có."""
    if pending.get("chat_id"):
        return str(pending["chat_id"])
    src = str(pending.get("source_device") or "")
    if "telegram:" in src:
        parts = src.split(":")
        if len(parts) >= 2 and parts[1].isdigit():
            return parts[1]
    return None
