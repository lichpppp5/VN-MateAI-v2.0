# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 Dương Thanh Lịch — VN-MateAI. See LICENSE and NOTICE.
"""
mateai/interfaces/http/routers/branding.py
==========================================
Thương hiệu hiển thị (white-label). GET công khai (màn hình đăng nhập cần trước khi đăng nhập); PUT: admin, ghi nhật ký
kiểm toán. Logic + kiểm tra ở mateai/application/branding.py.
"""
from __future__ import annotations

from typing import Any, Dict

from fastapi import APIRouter, Body, Depends, HTTPException

from mateai.interfaces.http.auth_dependencies import require_roles

router = APIRouter()


@router.get("/api/v1/branding", summary="Tên, mô tả, logo, màu chủ đạo của portal", tags=["Branding"])
async def branding_get() -> Dict[str, Any]:
    from mateai.application import branding
    return branding.get()


@router.put("/api/v1/branding", summary="Đổi thương hiệu hiển thị (admin)", tags=["Branding"])
async def branding_put(changes: Dict[str, Any] = Body(...),
                       user: Dict[str, Any] = Depends(require_roles(["admin"]))) -> Dict[str, Any]:
    from mateai.application import branding
    from mateai.infrastructure.database.erp_database import erp_db
    try:
        data = branding.update(changes)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    erp_db.log_audit_action(action_type="BRANDING_UPDATED", employee_id=str(user.get("username") or "admin"),
                            status="success", payload=", ".join(sorted(changes)))
    return data
