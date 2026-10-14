# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 Dương Thanh Lịch — VN-MateAI. See LICENSE and NOTICE.
"""
mateai/application/enterprise/operations.py
===========================================
Use case doanh nghiệp có tác dụng phụ, gọi từ Portal (người bấm trực tiếp): ghi sổ
quỹ, uỷ quyền đa tác nhân, onboarding nhân viên. Chuyển từ
`interfaces/http/routers/enterprise.py` (Supervisor Phase 10, §198).

Mọi use case đi qua cổng chính sách (`zero_trust.execute_with_hitl` →
`policy_engine.authorize`). Kết quả cổng được đổi thành `UseCaseError` để tầng HTTP
chỉ việc ánh xạ sang mã trạng thái — không có logic nghiệp vụ trong router.
"""
from __future__ import annotations

import logging
from typing import Any, Callable, Dict

logger = logging.getLogger(__name__)


class UseCaseError(Exception):
    """Lỗi nghiệp vụ có mã trạng thái gợi ý cho tầng giao diện."""

    def __init__(self, status_code: int, detail: str) -> None:
        super().__init__(detail)
        self.status_code = status_code
        self.detail = detail


def _actor(actor: Dict[str, Any]) -> str:
    return str(actor.get("username") or "unknown")


async def _through_policy(action: str, params: Dict[str, Any], executor: Callable[[], Any],
                          actor: Dict[str, Any], description: str) -> Dict[str, Any]:
    from mateai.application.security import zero_trust  # đọc lúc gọi (test thay được)
    gate = await zero_trust.execute_with_hitl(
        action_name=action, params=params, executor=executor,
        requested_by=_actor(actor), description=description,
    )
    if gate.get("status") == "denied":
        raise UseCaseError(403, gate["message"])
    if gate.get("status") == "awaiting_approval":
        raise UseCaseError(202, gate["message"])
    return gate


async def record_finance(actor: Dict[str, Any], body: Dict[str, Any]) -> Dict[str, Any]:
    """Ghi thu / chi vào sổ quỹ. Tên tác vụ `record_income` (rủi ro 3) / `record_expense`
    (4, tự nâng 5 khi >= 50 triệu) phải khớp bảng rủi ro.

    Người ghi = tài khoản đăng nhập. Trước đây lấy `created_by` từ body — người gọi ghi
    được giao dịch dưới tên người khác (`test_finance_record_uses_logged_in_user_not_body`)."""
    from mateai.infrastructure.database.erp_database import erp_db

    ftype = str(body.get("type", "expense") or "expense").strip().lower()
    if ftype not in ("income", "expense"):
        raise UseCaseError(400, "Tham số 'type' phải là 'income' hoặc 'expense'.")
    amount = float(body.get("amount", 0))
    category = body.get("category", "Chi phí hoạt động")
    description = body.get("description", "")
    created_by = _actor(actor)
    if body.get("created_by") and str(body["created_by"]) != created_by:
        logger.warning("[Finance] %s gửi created_by=%r — bỏ qua, ghi theo tài khoản đăng nhập.",
                       created_by, body["created_by"])
    action_name = "record_income" if ftype == "income" else "record_expense"

    def _write():
        return erp_db.add_finance_record(finance_type=ftype, amount=amount, category=category,
                                         description=description, created_by=created_by)

    gate = await _through_policy(
        action_name, {"amount": amount, "category": category, "description": description, "type": ftype},
        _write, actor,
        f"{'Ghi thu' if ftype == 'income' else 'Ghi chi'} {amount:,.0f} VNĐ vào mục '{category}'")
    return {"status": "success", "record": gate["result"], "risk_level": gate.get("risk_level")}


async def delegate_multi_agent(actor: Dict[str, Any], query: str) -> Dict[str, Any]:
    """Uỷ quyền câu hỏi cho hệ đa tác nhân (CFO / HR / CTO). `delegate_to_multi_agent` = rủi ro 2."""
    query = str(query or "").strip()
    if not query:
        raise UseCaseError(400, "Thiếu tham số: query")

    def _route():
        from mateai.application.agent.agent_orchestrator import multi_agent_system
        return multi_agent_system.route_and_execute(query=query)

    gate = await _through_policy("delegate_to_multi_agent", {"query": query}, _route, actor,
                                 f"Ủy quyền Multi-Agent xử lý: {query[:200]}")
    return {"status": "success", "result": gate["result"], "risk_level": gate.get("risk_level")}


async def onboard_employee(actor: Dict[str, Any], body: Dict[str, Any]) -> Dict[str, Any]:
    """Onboarding nhân viên mới (`zero_touch_onboard_employee` = rủi ro 4).

    Chống leo thang qua tham số: `role` trong body do client gửi — chỉ ADMIN được chọn;
    người khác bị hạ về "operator" (có log)."""
    name = body.get("name", "")
    position = body.get("position", "")
    if not name or position is None or not str(position).strip():
        raise UseCaseError(400, "Thiếu tham số bắt buộc: name, position")

    caller_role = actor.get("role", "viewer")
    requested_role = str(body.get("role", "operator") or "operator").strip().lower()
    if caller_role == "admin":
        effective_role = requested_role
    else:
        effective_role = "operator"
        if requested_role != "operator":
            logger.warning("[Onboarding] %s (role=%s) yêu cầu tạo nhân viên với role=%r — bị hạ về 'operator'. "
                           "Chỉ admin được cấp quyền đặc biệt.", _actor(actor), caller_role, requested_role)

    department_name = body.get("department_name", "Nhân sự")
    email = body.get("email", "")
    phone = body.get("phone", "")

    def _onboard():
        from mateai.application.skills.builtin.onboarding_workflow import onboarding_workflow
        return onboarding_workflow.onboard_new_employee(
            name=name, position=position, department_name=department_name, email=email, phone=phone,
            role=effective_role, allow_privileged_role=(caller_role == "admin"))

    gate = await _through_policy(
        "zero_touch_onboard_employee",
        {"name": name, "position": position, "department": department_name, "role": effective_role},
        _onboard, actor, f"Onboarding nhân viên mới: {name} - {position} (phòng ban {department_name})")
    return {"status": "success", "result": gate["result"], "risk_level": gate.get("risk_level")}
