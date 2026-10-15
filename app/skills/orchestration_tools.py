# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 Dương Thanh Lịch — VN-MateAI. See LICENSE and NOTICE.
"""
skills/orchestration_tools.py
=============================
Công cụ cho AI Ly Ly GIAO VIỆC cho các agent khác bằng phiếu chuẩn (docs/integrations/orchestration.md).

- `list_agent_capabilities` (chỉ đọc): năng lực nào có agent nào nhận — gọi trước để chọn đúng `capability`.
- `dispatch_work_order`: tạo phiếu (quản lý / quản trị viên). Rủi ro ≥ 3 → chờ người duyệt (nói rõ CHƯA giao).
- `get_work_order_status` (chỉ đọc): xem phiếu — chỉ COMPLETED mới là xong (đã thẩm định độc lập).
Danh tính áp quyền là NGƯỜI đang được phục vụ (CURRENT_PRINCIPAL), không phải AI.
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from core.plugin_manager import export_skill

logger = logging.getLogger(__name__)

_DISPATCH_ROLES = ("manager", "admin")


def _svc():
    from mateai.application.orchestration import service
    return service


def _caller() -> str:
    from mateai.application.security.security_guard import CURRENT_PRINCIPAL
    return str(CURRENT_PRINCIPAL.get() or "AI_Agent")


def _role(who: str) -> str:
    """Vai trò PORTAL (admin / manager / viewer) — quyền giao việc theo đúng vai trò người dùng thấy trên Portal. (RBAC công cụ của
    security_guard dùng mô hình vai trò khác: manager ↦ it_support.) Agent gọi vào (`agent:<id>`) chỉ giao việc được khi danh bạ đặt
    quyền admin."""
    from mateai.infrastructure.database.db_manager import db_manager
    user = db_manager.get_user_by_username_or_id(str(who or ""))
    if user:
        return str(user.get("role") or "viewer")
    from mateai.application.security.security_guard import AGENT_PRINCIPAL_PREFIX
    if str(who).startswith(AGENT_PRINCIPAL_PREFIX):
        from mateai.application.agent_gateway import directory
        return "admin" if directory.inbound_role(str(who)[len(AGENT_PRINCIPAL_PREFIX):]) == "admin" else "viewer"
    return "viewer"


@export_skill(
    name="list_agent_capabilities",
    description=("Liệt kê NĂNG LỰC mà đội agent đang nhận việc được (vd dba, sysadmin, ba, dev) và agent nào nhận — gọi TRƯỚC khi "
                 "dispatch_work_order để chọn đúng `capability`."),
    parameters_schema={"type": "object", "properties": {}, "required": []},
)
async def list_agent_capabilities() -> Dict[str, Any]:
    from mateai.application.orchestration import registry
    cat = registry.capabilities_catalog()
    return {"success": True, "capabilities": cat,
            "hint": None if cat else "Chưa có agent thực thi nào — quản trị viên thêm ở Cổng Agent → Đội agent."}


@export_skill(
    name="dispatch_work_order",
    description=("GIAO VIỆC cho agent khác bằng phiếu chuẩn (chỉ quản lý / quản trị viên). Cần: mục tiêu cụ thể, năng lực, sản phẩm phải nộp, "
                 "tiêu chí 'thế nào là xong'. VN-MateAI tự thẩm định kết quả — agent báo xong chưa phải là xong. "
                 "Kết quả `WAITING_APPROVAL` = CHƯA giao, nói người dùng duyệt phiếu. Dùng get_work_order_status để theo dõi."),
    parameters_schema={"type": "object", "properties": {
        "objective": {"type": "string", "description": "Mục tiêu cụ thể, đủ để agent không thấy hội thoại vẫn làm đúng (≥ 10 ký tự)"},
        "capability": {"type": "string", "description": "Năng lực cần (lấy từ list_agent_capabilities)"},
        "deliverables": {"type": "array", "items": {"type": "string"}, "description": "Sản phẩm phải nộp"},
        "done_criteria": {"type": "array", "items": {"type": "string"}, "description": "Thế nào là xong — mỗi tiêu chí kiểm được"},
        "context": {"type": "string", "description": "Ngữ cảnh tối thiểu agent cần biết"},
        "risk": {"type": "integer", "description": "1–4: 1–2 chỉ đọc / soạn thảo; 3 trở lên có thay đổi hệ thống (cần duyệt)"},
        "idempotency_key": {"type": "string", "description": "Chống giao trùng khi gọi lại"}},
        "required": ["objective", "capability", "deliverables", "done_criteria"]},
)
async def dispatch_work_order(objective: str, capability: str, deliverables: List[str], done_criteria: List[str],
                              context: str = "", risk: int = 2, idempotency_key: Optional[str] = None) -> Dict[str, Any]:
    svc = _svc()
    who = _caller()
    if _role(who) not in _DISPATCH_ROLES:
        return {"success": False, "error": "Chỉ quản lý hoặc quản trị viên được giao việc cho agent."}
    try:
        out = await svc.create({"objective": objective, "capability": capability, "deliverables": deliverables,
                                "done_criteria": done_criteria, "context": context, "risk": risk},
                               requested_by=who, channel="ai", idempotency_key=idempotency_key)
    except Exception as exc:  # noqa: BLE001
        msg = str(exc) if exc.__class__.__name__ in ("OrderError", "ContractError") else f"{type(exc).__name__}: {exc}"
        return {"success": False, "error": msg}
    res = {"success": True, "order_id": out["order_id"], "status": out["status"], "executor": out.get("executor_name")}
    if out["status"] == svc.WAITING_APPROVAL:
        res["message"] = f"CHƯA giao: phiếu rủi ro {out['risk']} cần người có thẩm quyền duyệt (phiếu duyệt {out.get('approval_id')})."
    else:
        res["message"] = "Đã đưa vào hàng đợi giao việc. Kết quả chỉ tính là xong khi trạng thái COMPLETED (đã thẩm định)."
    return res


@export_skill(
    name="get_work_order_status",
    description=("Xem phiếu giao việc: trạng thái, agent nào làm, kết quả thẩm định từng tiêu chí. COMPLETED = đã thẩm định đạt; "
                 "NEEDS_CONFIRMATION = cần người xác nhận; FAILED / BLOCKED = chưa xong — đừng báo là xong."),
    parameters_schema={"type": "object", "properties": {"order_id": {"type": "string"}}, "required": ["order_id"]},
)
async def get_work_order_status(order_id: str) -> Dict[str, Any]:
    o = _svc().get(order_id)
    who = _caller()
    if not o or (o["requested_by"] != who and _role(who) not in _DISPATCH_ROLES):
        return {"success": False, "error": f"Không có phiếu «{order_id}»"}
    ver = o.get("verification") or {}
    rep = o.get("report") or {}
    return {"success": True, "order_id": order_id, "status": o["status"], "executor": o.get("executor_name"), "attempt": o["attempt"],
            "objective": o["objective"], "error": o.get("error"),
            "result": str(rep.get("result") or "")[:2000] or None,
            "checks": [{"criterion": c["criterion"], "ok": c["ok"], "detail": c["detail"]} for c in ver.get("checks") or []]}
