# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 Dương Thanh Lịch — VN-MateAI. See LICENSE and NOTICE.
"""
mateai/interfaces/http/routers/privacy.py
=========================================
Dữ liệu cá nhân (thẩm định thương mại 14/10/2026 #24): sổ hoạt động xử lý, tra cứu và xoá theo yêu cầu của chủ thể.
Chỉ admin. Xoá cần gõ lại đúng định danh (`confirm`) — thao tác không hoàn tác được.
"""
from __future__ import annotations

from functools import partial
from typing import Any, Dict

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from core.plugin_manager import run_blocking
from mateai.interfaces.http.auth_dependencies import require_roles

router = APIRouter()
_ADMIN = Depends(require_roles(["admin"]))


class SubjectRequest(BaseModel):
    subject: str = Field(..., min_length=3, max_length=200, description="Tên đăng nhập, email, SĐT hoặc họ tên đầy đủ")


class EraseRequest(SubjectRequest):
    confirm: str = Field(..., description="Gõ lại đúng định danh để xác nhận")


@router.get("/api/v1/privacy/register", summary="Sổ hoạt động xử lý dữ liệu cá nhân", tags=["Privacy"])
async def privacy_register(user: Dict[str, Any] = _ADMIN) -> Dict[str, Any]:
    from mateai.application.privacy.compliance import REGISTER
    return {"activities": REGISTER}


@router.post("/api/v1/privacy/subject/lookup", summary="Tra cứu dữ liệu của một chủ thể", tags=["Privacy"])
async def privacy_lookup(body: SubjectRequest, user: Dict[str, Any] = _ADMIN) -> Dict[str, Any]:
    from mateai.application.privacy import compliance
    try:
        out = await run_blocking(partial(compliance.lookup, body.subject))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    await run_blocking(partial(compliance.audit_lookup, body.subject, str(user.get("username") or "admin")))
    return out


@router.post("/api/v1/privacy/subject/erase", summary="Xoá / ẩn danh dữ liệu của một chủ thể", tags=["Privacy"])
async def privacy_erase(body: EraseRequest, user: Dict[str, Any] = _ADMIN) -> Dict[str, Any]:
    from mateai.application.privacy import compliance
    if body.confirm.strip() != body.subject.strip():
        raise HTTPException(status_code=400, detail="Gõ lại đúng định danh vào ô xác nhận để xoá.")
    try:
        return await run_blocking(partial(compliance.erase, body.subject, str(user.get("username") or "admin")))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
