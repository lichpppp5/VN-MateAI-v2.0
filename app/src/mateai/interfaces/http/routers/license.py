# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 Dương Thanh Lịch — VN-MateAI. See LICENSE and NOTICE.
"""
mateai/interfaces/http/routers/license.py
=========================================
Bản quyền + đo lượng sử dụng (thẩm định thương mại #23). Xem: admin / manager. Nạp tệp bản quyền: admin (kiểm chữ ký trước).
"""
from __future__ import annotations

from typing import Any, Dict

from fastapi import APIRouter, Body, Depends, HTTPException

from core.plugin_manager import run_blocking
from mateai.interfaces.http.auth_dependencies import require_roles

router = APIRouter()


@router.get("/api/v1/license", summary="Trạng thái bản quyền", tags=["License"])
async def license_status(user: Dict[str, Any] = Depends(require_roles(["admin", "manager"]))) -> Dict[str, Any]:
    from mateai.application import licensing
    return await run_blocking(licensing.status)


@router.put("/api/v1/license", summary="Nạp tệp bản quyền (license.json) do nhà cung cấp cấp", tags=["License"])
async def license_install(doc: Dict[str, Any] = Body(...), user: Dict[str, Any] = Depends(require_roles(["admin"]))) -> Dict[str, Any]:
    from mateai.application import licensing
    from mateai.infrastructure.database.erp_database import erp_db
    try:
        st = licensing.install(doc)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=f"Không nạp được bản quyền: {exc}")
    erp_db.log_audit_action(action_type="LICENSE_INSTALLED", employee_id=str(user.get("username") or "admin"), status="success",
                            payload=str((st.get("license") or {}).get("license_id") or ""))
    return st


@router.get("/api/v1/license/usage", summary="Lượng sử dụng so với hạn mức", tags=["License"])
async def license_usage(user: Dict[str, Any] = Depends(require_roles(["admin", "manager"]))) -> Dict[str, Any]:
    from mateai.application import licensing
    return await run_blocking(licensing.usage)
