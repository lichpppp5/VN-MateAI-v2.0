# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 Dương Thanh Lịch — VN-MateAI. See LICENSE and NOTICE.
"""
mateai/interfaces/http/routers/security.py
==========================================
Bảo mật: blacklist Zero-Trust, sandbox kiểm tra lệnh, token thiết bị, audit log,
hàng đợi tác vụ chờ duyệt và phê duyệt (HITL của hội thoại / portal).
"""
from __future__ import annotations

import logging
import re
from functools import partial
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel, Field

from core.plugin_manager import run_blocking
from mateai.interfaces.http import enrollment
from mateai.interfaces.http.auth_dependencies import get_current_user, require_roles
from mateai.interfaces.websocket.realtime_hub import broadcast_portal_ui

logger = logging.getLogger(__name__)

router = APIRouter()


class BlacklistUpdateRequest(BaseModel):
    """Payload for POST /api/v1/security/blacklist."""
    action: str = Field(..., description="'add' hoặc 'remove'")
    keyword: str = Field(..., min_length=1, description="Từ khóa hoặc hành động hoặc đường dẫn")
    category: Optional[str] = Field(default="blacklist", description="'blacklist', 'confirm_actions', hoặc 'protected_dirs'")


class SecurityInspectRequest(BaseModel):
    """Payload for POST /api/v1/security/inspect."""
    type: str = Field(default="code", description="'code', 'action', hoặc 'text'")
    content: str = Field(..., min_length=1, description="Nội dung mã nguồn, lệnh hoặc prompt")
    params: Optional[Dict[str, Any]] = Field(default=None, description="Tham số đi kèm nếu là action")


class ConfirmActionRequest(BaseModel):
    """Payload for POST /api/v1/security/confirm-action."""
    approved: bool = Field(..., description="Phê duyệt (true) hoặc Hủy bỏ (false)")
    # Optional — can be auto-resolved from StateManager if not provided
    action_id: Optional[str] = Field(default=None, description="Mã định danh tác vụ pending (tùy chọn)")
    client_id: Optional[str] = Field(default=None, description="ID máy trạm thực thi hoặc 'master' (auto-resolved nếu bỏ trống)")
    skill_name: Optional[str] = Field(default=None, description="Tên kỹ năng (auto-resolved từ StateManager nếu bỏ trống)")
    args: Optional[Dict[str, Any]] = Field(default=None, description="Tham số kỹ năng (auto-resolved nếu bỏ trống)")
    user_id: Optional[str] = Field(default=None, description="User ID để tra StateManager (mặc định: 'admin')")


@router.get(
    "/api/v1/security/blacklist",
    summary="Get current blacklist keywords",
    tags=["Security"],
)
async def get_blacklist(user: dict = Depends(require_roles(["admin"]))) -> Dict[str, Any]:
    """Retrieve forbidden keywords from security config."""
    from mateai.config.loader import settings
    return {
        "status": "success",
        "forbidden_keywords": getattr(settings.security, "forbidden_keywords", []),
        "require_confirmation_actions": getattr(settings.security, "require_confirmation_actions", []),
        "protected_directories": getattr(settings.security, "protected_directories", []),
    }


@router.post(
    "/api/v1/security/blacklist",
    summary="Add or remove keywords from blacklist, confirm actions, or protected dirs",
    tags=["Security"],
)
async def update_blacklist(
    payload: BlacklistUpdateRequest,
    user: dict = Depends(require_roles(["admin"])),
) -> Dict[str, Any]:
    """Add or remove an item from the active security policy (blacklist, confirm_actions, protected_dirs)."""
    from mateai.config.loader import settings
    from mateai.application.security.safety_guard import security_engine

    actor = str(user.get("username") or "admin")

    category = payload.category or "blacklist"
    kw = payload.keyword.strip()

    key = {"confirm_actions": "require_confirmation_actions",
           "protected_dirs": "protected_directories"}.get(category, "forbidden_keywords")
    changed: List[str] = []

    def _apply(cfg: Dict[str, Any]) -> None:
        sec = cfg.setdefault("security", {})
        current_list = sec.setdefault(key, list(getattr(settings.security, key, [])))
        if payload.action == "add" and kw and kw not in current_list:
            current_list.append(kw)
            changed.append("add")
        elif payload.action == "remove" and kw in current_list:
            current_list.remove(kw)
            changed.append("remove")

    try:
        from mateai.application.administration import config_governance as gov
        from mateai.interfaces.http.secret_masking import _mask_secrets
        # Luật bảo mật là chính sách: lịch sử phiên bản + audit (§70, §128).
        gov.save_config(actor, _apply, f"Chính sách bảo mật: {payload.action} '{kw}' ({category})",
                        _mask_secrets, audit=False)
        if changed:
            security_engine.log_audit(actor, f"update_{category}", "POLICY", "SUCCESS",
                                      {"action": changed[0], "item": kw, "category": category})
    except Exception as exc:
        logger.error("Lỗi khi lưu cấu hình bảo mật vào config.json: %s", exc)
        raise HTTPException(status_code=500, detail=f"Lỗi lưu cấu hình: {exc}")

    return {
        "status": "success",
        "action": payload.action,
        "category": category,
        "keyword": kw,
        "forbidden_keywords": getattr(settings.security, "forbidden_keywords", []),
        "require_confirmation_actions": getattr(settings.security, "require_confirmation_actions", []),
        "protected_directories": getattr(settings.security, "protected_directories", []),
    }


@router.post(
    "/api/v1/security/inspect",
    summary="Interactive Zero-Trust AST & Security Sandbox Inspector",
    tags=["Security"],
)
async def inspect_security_sandbox(
    payload: SecurityInspectRequest,
    user: dict = Depends(require_roles(["manager", "admin"])),
) -> Dict[str, Any]:
    """
    Test and analyze Python code, shell commands, or user queries against Zero-Trust AST & Blacklist rules.
    """
    from mateai.application.security.safety_guard import security_engine
    from mateai.config.loader import settings

    inspect_type = (payload.type or "code").lower()
    content = payload.content.strip()

    # 1. Masking evaluation
    masked = security_engine.mask_sensitive_data(content)
    has_sensitive = (masked != content)

    # 2. Risk & Violations evaluation
    violations: List[str] = []
    if inspect_type == "code":
        is_safe, message = security_engine.inspect_generated_code(content)
        risk = "SAFE" if is_safe else "BLOCKED"
        if not is_safe:
            violations.append(message)
    elif inspect_type == "action":
        risk = security_engine.evaluate_action_risk(content, payload.params or {})
        is_safe = (risk == "SAFE")
        if risk == "BLOCKED":
            message = "Tác vụ chứa từ khóa cấm hoặc hành vi bị từ chối tức thì."
            violations.append(message)
        elif risk == "NEED_CONFIRM":
            message = "Tác vụ thuộc danh mục nhạy cảm, yêu cầu Người quản trị bấm duyệt (HITL)."
            violations.append(message)
        else:
            message = "Tác vụ an toàn, được phép thực thi tự động (Auto-Execute)."
    else:  # Text / Prompt
        forbidden = getattr(settings.security, "forbidden_keywords", [])
        for kw in forbidden:
            if kw.lower() in content.lower():
                violations.append(f"Chứa từ khóa cấm trong Blacklist: '{kw}'")
        if violations:
            risk = "BLOCKED"
            is_safe = False
            message = "Phát hiện nội dung vi phạm chính sách bảo mật Blacklist."
        else:
            risk = "SAFE"
            is_safe = True
            message = "Văn bản an toàn theo tiêu chuẩn Zero-Trust."

    return {
        "status": "success",
        "type": inspect_type,
        "risk": risk,
        "is_safe": is_safe,
        "message": message,
        "violations": violations,
        "has_sensitive_data": has_sensitive,
        "masked_content": masked,
    }


_DEVICE_ID_RE = re.compile(r"^[A-Za-z0-9_.\-]{1,64}$")


class DeviceTokenRequest(BaseModel):
    device_id: str


@router.post("/api/v1/security/devices", summary="Cấp / xoay token riêng cho một thiết bị IoT", tags=["Security"])
async def issue_device_token_endpoint(
    payload: DeviceTokenRequest,
    user: dict = Depends(require_roles(["admin"])),
) -> Dict[str, Any]:
    """Token gốc chỉ trả về MỘT lần (máy chủ chỉ lưu hash). Cấp lại = token cũ hết hiệu lực."""
    device_id = payload.device_id.strip()
    if not _DEVICE_ID_RE.match(device_id):
        raise HTTPException(status_code=422, detail="device_id chỉ gồm chữ, số, '_', '-', '.', tối đa 64 ký tự.")
    from mateai.infrastructure.database.db_manager import db_manager
    token = await run_blocking(db_manager.issue_device_token, device_id=device_id, created_by=str(user.get("username", "")))
    try:
        from mateai.application.security.safety_guard import security_engine
        security_engine.log_audit(str(user.get("username", "")), "issue_device_token", "SAFE", "SUCCESS",
                                  {"device_id": device_id})
    except Exception:  # pylint: disable=broad-except
        pass
    return {
        "status": "success",
        "device_id": device_id,
        "device_token": token,
        "ws_path": f"/api/v1/xiaozhi/ws/{device_id}",
        "instructions": (
            "Dán token vào DEFAULT_DEVICE_TOKEN và đặt DEFAULT_DEVICE_ID = device_id trong "
            "esp32_firmware/src/secrets.h, build và nạp lại. Token chỉ hiện một lần."
        ),
    }


@router.get("/api/v1/security/devices", summary="Danh sách thiết bị có token riêng", tags=["Security"])
async def list_device_tokens_endpoint(user: dict = Depends(require_roles(["admin"]))) -> Dict[str, Any]:
    from mateai.infrastructure.database.db_manager import db_manager
    from mateai.config.loader import get_config_section
    return {
        "status": "success",
        "devices": await run_blocking(db_manager.list_device_tokens),
        "require_per_device_token": bool(get_config_section("security").get("require_per_device_token", False)),
    }


class DeviceRoleRequest(BaseModel):
    #: "admin" | "it_support" | "operator" | "viewer"; null = bỏ, về quy tắc mặc định theo id.
    role: Optional[str] = None


def _audit(user: dict, action: str, details: Dict[str, Any]) -> None:
    try:
        from mateai.application.security.safety_guard import security_engine
        security_engine.log_audit(str(user.get("username", "")), action, "SAFE", "SUCCESS", details)
    except Exception:  # pylint: disable=broad-except
        pass


@router.put("/api/v1/security/devices/{device_id}/role", summary="Đặt quyền cho một thiết bị", tags=["Security"])
async def set_device_role_endpoint(
    device_id: str,
    payload: DeviceRoleRequest,
    user: dict = Depends(require_roles(["admin"])),
) -> Dict[str, Any]:
    """Quyền chỉ áp dụng khi thiết bị kết nối bằng token RIÊNG của nó (danh tính
    "device:<id>" do máy chủ gán) — token dùng chung không mang quyền này.
    Tác vụ rủi ro cao vẫn cần duyệt; duyệt một lần thì thiết bị được nhớ."""
    from mateai.application.security.security_guard import DEVICE_ROLES
    from mateai.infrastructure.database.db_manager import db_manager
    role = (payload.role or "").strip().lower() or None
    if role is not None and role not in DEVICE_ROLES:
        raise HTTPException(status_code=422, detail=f"Quyền phải là một trong: {', '.join(DEVICE_ROLES)}.")
    if not await run_blocking(db_manager.set_device_role, device_id=device_id, role=role):
        raise HTTPException(status_code=404, detail=f"Thiết bị '{device_id}' chưa có token riêng — cấp token trước.")
    _audit(user, "set_device_role", {"device_id": device_id, "role": role})
    return {"status": "success", "device_id": device_id, "role": role}


@router.get("/api/v1/security/devices/{device_id}/approvals",
            summary="Tác vụ thiết bị đã được duyệt (không hỏi lại)", tags=["Security"])
async def list_device_approvals_endpoint(device_id: str, user: dict = Depends(require_roles(["admin"]))) -> Dict[str, Any]:
    from mateai.application.security.security_guard import DEVICE_PRINCIPAL_PREFIX
    from mateai.infrastructure.database.db_manager import db_manager
    grants = await run_blocking(db_manager.list_approval_grants, principal=f"{DEVICE_PRINCIPAL_PREFIX}{device_id}")
    return {"status": "success", "device_id": device_id, "approvals": grants}


@router.delete("/api/v1/security/devices/{device_id}/approvals",
               summary="Thu hồi phê duyệt đã nhớ của thiết bị", tags=["Security"])
async def revoke_device_approvals_endpoint(
    device_id: str,
    tool_name: Optional[str] = None,
    user: dict = Depends(require_roles(["admin"])),
) -> Dict[str, Any]:
    """tool_name bỏ trống = thu hồi TẤT CẢ (lần sau mọi tác vụ rủi ro cao lại hỏi duyệt)."""
    from mateai.application.security.security_guard import DEVICE_PRINCIPAL_PREFIX
    from mateai.infrastructure.database.db_manager import db_manager
    n = await run_blocking(db_manager.revoke_approval_grant,
                           principal=f"{DEVICE_PRINCIPAL_PREFIX}{device_id}", tool_name=tool_name)
    _audit(user, "revoke_device_approvals", {"device_id": device_id, "tool_name": tool_name, "removed": n})
    return {"status": "success", "device_id": device_id, "removed": n}


@router.delete("/api/v1/security/devices/{device_id}", summary="Thu hồi token của một thiết bị", tags=["Security"])
async def revoke_device_token_endpoint(device_id: str, user: dict = Depends(require_roles(["admin"]))) -> Dict[str, Any]:
    from mateai.infrastructure.database.db_manager import db_manager
    if not await run_blocking(db_manager.revoke_device_token, device_id=device_id):
        raise HTTPException(status_code=404, detail=f"Không có token cho thiết bị '{device_id}'.")
    try:
        from mateai.application.security.safety_guard import security_engine
        security_engine.log_audit(str(user.get("username", "")), "revoke_device_token", "SAFE", "SUCCESS",
                                  {"device_id": device_id})
    except Exception:  # pylint: disable=broad-except
        pass
    return {"status": "success", "device_id": device_id}


@router.get(
    "/api/v1/security/device-enrollment-token",
    summary="Lấy device enrollment token để flash firmware ESP32",
    tags=["Security"],
)
async def get_device_enrollment_token(
    user: dict = Depends(require_roles(["admin"])),
) -> Dict[str, Any]:
    """
    Trả về token cần dán vào DEFAULT_DEVICE_TOKEN trong secrets.h của firmware.

    Token này cho phép thiết bị stream âm thanh vào master. Chỉ admin được xem.
    """
    return {
        "status": "success",
        "device_enrollment_token": enrollment.get_device_enrollment_secret(),
        "instructions": (
            "Dán giá trị trên vào DEFAULT_DEVICE_TOKEN trong "
            "esp32_firmware/src/secrets.h, build và flash lại thiết bị."
        ),
    }


@router.get(
    "/api/v1/security/audit-logs",
    summary="Get recent security audit logs",
    tags=["Security"],
)
async def get_audit_logs(
    limit: int = Query(default=50, ge=1, le=200),
    user: dict = Depends(require_roles(["admin"])),
) -> Dict[str, Any]:
    """Fetch structured security audit logs."""
    from mateai.application.security.safety_guard import security_engine
    logs = security_engine.get_recent_audit_logs(limit=limit)
    return {
        "status": "success",
        "count": len(logs),
        "total": len(logs),
        "logs": logs,
    }


@router.get(
    "/api/v1/security/pending-action",
    summary="Phase 25: Query current pending action waiting for admin approval",
    tags=["Security"],
)
async def get_pending_action_endpoint(
    user_id: str = Query(default="admin", description="User/session ID để tra StateManager"),
    current_user: Dict[str, Any] = Depends(require_roles(["admin"])),
) -> Dict[str, Any]:
    """Tác vụ đang chờ duyệt (kèm tham số), từ hàng đợi HITL duy nhất. Chỉ admin —
    người duy nhất duyệt được, và tham số có thể chứa nội dung nhạy cảm (vd nội
    dung tệp sắp ghi). `action` = yêu cầu mới nhất của `user_id`, nếu không có
    thì yêu cầu mới nhất."""
    from mateai.application.agent.tool_gate import pending_view
    from mateai.application.security.zero_trust import hitl_manager

    all_pending = [pending_view(it) for it in hitl_manager.get_pending_list()]
    own = [p for p in all_pending if user_id in (p.get("requested_by"), p.get("source_device"))]
    action = (own or all_pending or [None])[-1]
    return {
        "has_pending": bool(action),
        "count": len(all_pending),
        "action": action,
        "pending_list": list(reversed(all_pending)),
    }


@router.post(
    "/api/v1/security/confirm-action",
    summary="Approve or reject a high-risk action",
    tags=["Security"],
)
async def confirm_action_endpoint(
    payload: ConfirmActionRequest,
    current_user: Dict[str, Any] = Depends(require_roles(["admin"])),
) -> Dict[str, Any]:
    """
    Phase 25: Duyệt / huỷ một tác vụ đang nằm trong hàng đợi HITL duy nhất.

    Chỉ admin. Chỉ thực thi ĐÚNG tác vụ đang chờ (tên tool, tham số, máy đích
    lấy từ hàng đợi) — `skill_name` / `args` / `client_id` trong body chỉ
    để đối chiếu. Trước đây mọi người đã đăng nhập (kể cả viewer) gửi
    `{approved: true, skill_name, args}` là chạy thẳng skill bất kỳ trên máy
    chủ, không qua Zero-Trust/RBAC. Nay tác vụ đã duyệt chạy qua cổng tool
    chung với `approved=True`; RBAC áp theo người YÊU CẦU tác vụ.
    """
    from mateai.interfaces.http import approval_flow

    try:
        return await approval_flow.confirm(payload.approved, str(current_user.get("username") or "admin"),
                                           action_id=payload.action_id, skill_name=payload.skill_name)
    except approval_flow.ApprovalDecisionError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.detail)


@router.get("/api/v1/security/audit-logs/export", summary="Xuất báo cáo kiểm toán CSV / HTML in được (chỉ admin)")
async def export_audit_logs(
    format: str = Query(default="csv", pattern="^(csv|html)$"),
    date_from: Optional[str] = Query(default=None, pattern=r"^\d{4}-\d{2}-\d{2}$"),
    date_to: Optional[str] = Query(default=None, pattern=r"^\d{4}-\d{2}-\d{2}$"),
    user: dict = Depends(require_roles(["admin"])),
):
    """"AI đã làm gì, ai duyệt" cho kiểm toán viên (thẩm định thương mại #17). Việc xuất cũng được ghi audit."""
    from fastapi.responses import HTMLResponse, Response
    from mateai.application.security import audit_export
    from mateai.infrastructure.database.erp_database import erp_db
    rows = await run_blocking(partial(audit_export.fetch, date_from, date_to))
    who = str(user.get("username") or "admin")
    erp_db.log_audit_action(action_type="AUDIT_EXPORT", employee_id=who, status="success",
                            payload=f'{{"format": "{format}", "from": "{date_from or ""}", "to": "{date_to or ""}", "rows": {len(rows)}}}')
    stamp = f"{date_from or 'dau'}_{date_to or 'nay'}"
    if format == "csv":
        return Response(audit_export.to_csv(rows), media_type="text/csv; charset=utf-8",
                        headers={"Content-Disposition": f'attachment; filename="audit_{stamp}.csv"'})
    chain = await run_blocking(erp_db.verify_audit_chain)
    return HTMLResponse(audit_export.to_html(rows, chain=chain, exported_by=who, date_from=date_from, date_to=date_to))


@router.get("/api/v1/security/audit-logs/verify", summary="Kiểm tra toàn vẹn chuỗi audit (chỉ admin)")
async def verify_audit_logs(user: dict = Depends(require_roles(["admin"]))) -> Dict[str, Any]:
    """Tính lại chuỗi băm của audit_logs (prompt cuối §73): sửa / xoá một dòng là lộ đúng id. Kèm kiểm các NEO đã ghi ra
    ngoài (audit_anchor): sửa rồi tính lại cả chuỗi vẫn lộ vì neo cũ không còn khớp."""
    from mateai.application.security import audit_anchor
    from mateai.infrastructure.database.erp_database import erp_db
    out = await run_blocking(erp_db.verify_audit_chain)
    out["anchors"] = await run_blocking(audit_anchor.verify)
    return out


# ── Kiểm soát tự trị: kill switch, tắt tác nhân / tool, L5, ngân sách ──────────
# Prompt Supervisor §95–§96, §70: công tắc nằm NGOÀI LLM (policy_engine đọc mỗi lần
# quyết định); chỉ admin đổi được; mọi lần đổi vào audit + lịch sử cấu hình.

class AutonomyUpdate(BaseModel):
    kill_switch: Optional[bool] = None
    disabled_agents: Optional[List[str]] = None
    disabled_tools: Optional[List[str]] = None
    never_autonomous_tools: Optional[List[str]] = None
    approval_grant_ttl_days: Optional[int] = Field(default=None, ge=1, le=365)
    max_agent_seconds: Optional[float] = Field(default=None, ge=10.0, le=3600.0)
    max_tool_calls_per_turn: Optional[int] = Field(default=None, ge=1, le=100)
    max_tool_failures_per_turn: Optional[int] = Field(default=None, ge=1, le=20)
    emergency_max_actions_per_minute: Optional[int] = Field(default=None, ge=0, le=1000)
    email_auto_reply: Optional[bool] = None
    email_auto_reply_domains: Optional[List[str]] = None
    reason: str = Field(default="", max_length=300)


@router.get("/api/v1/security/autonomy", summary="Giới hạn tự trị của AI (kill switch, L5, ngân sách)")
async def get_autonomy(user: dict = Depends(require_roles(["manager", "admin"]))) -> Dict[str, Any]:
    from mateai.application.administration import autonomy_settings
    return autonomy_settings.view()


@router.put("/api/v1/security/autonomy", summary="Đổi giới hạn tự trị (chỉ admin, có audit)")
async def put_autonomy(payload: AutonomyUpdate, user: dict = Depends(require_roles(["admin"]))) -> Dict[str, Any]:
    from mateai.application.administration import autonomy_settings
    from mateai.interfaces.http.secret_masking import _mask_secrets

    updates = payload.model_dump(exclude_none=True)
    reason = updates.pop("reason", "")
    try:
        result = autonomy_settings.update(str(user.get("username") or "admin"), updates, reason, _mask_secrets)
    except autonomy_settings.AutonomyUpdateError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    if "kill_switch" in updates:
        try:
            await broadcast_portal_ui("autonomy_changed", {"kill_switch": bool(updates["kill_switch"])})
        except Exception:  # noqa: BLE001
            pass
    return result
