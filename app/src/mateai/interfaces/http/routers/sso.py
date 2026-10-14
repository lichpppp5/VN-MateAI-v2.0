# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 Dương Thanh Lịch — VN-MateAI. See LICENSE and NOTICE.
"""
mateai/interfaces/http/routers/sso.py
=====================================
Đăng nhập SSO (OpenID Connect). Bốn đường công khai (chạy TRƯỚC khi đăng nhập), logic ở application/security/sso.py.
Token không bao giờ nằm trên URL: callback chỉ đưa về một mã đổi phiên dùng một lần (60 s), trình duyệt đổi bằng POST.
"""
from __future__ import annotations

import logging
from datetime import timedelta
from typing import Any, Dict
from urllib.parse import quote

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import RedirectResponse
from pydantic import BaseModel, Field

from mateai.application.security import sso
from mateai.application.security.auth_manager import auth_manager

logger = logging.getLogger(__name__)
router = APIRouter()
_TAG = ["Authentication"]


def _audit(username: str, ip: str, ok: bool, reason: str = "") -> None:
    try:
        from mateai.application.security.safety_guard import security_engine
        security_engine.log_audit(str(username)[:80] or "sso", "sso_login", "AUTH", "SUCCESS" if ok else "REJECTED",
                                  {"source_ip": ip, "reason": reason, "method": "oidc"})
    except Exception:  # noqa: BLE001
        pass


@router.get("/api/v1/sso/config", summary="SSO có bật không (cho màn đăng nhập)", tags=_TAG)
async def sso_config() -> Dict[str, Any]:
    return sso.public_config()


@router.get("/api/v1/sso/login", summary="Chuyển người dùng sang IdP", tags=_TAG, include_in_schema=True)
async def sso_login():
    try:
        return RedirectResponse(await sso.start(), status_code=302)
    except sso.SsoError as exc:
        return RedirectResponse(f"/?sso_error={quote(str(exc))}", status_code=302)


@router.get("/api/v1/sso/callback", summary="IdP trả về sau khi đăng nhập", tags=_TAG)
async def sso_callback(request: Request, code: str = "", state: str = "", error: str = "", error_description: str = ""):
    ip = request.client.host if request.client else "?"
    if error:
        _audit("sso", ip, False, f"IdP: {error}")
        return RedirectResponse(f"/?sso_error={quote('IdP từ chối đăng nhập: ' + (error_description or error)[:120])}", status_code=302)
    try:
        user = await sso.finish(code, state)
    except sso.SsoError as exc:
        _audit("sso", ip, False, str(exc))
        return RedirectResponse(f"/?sso_error={quote(str(exc))}", status_code=302)
    _audit(user["username"], ip, True, "tạo mới" if user.get("created") else "")
    return RedirectResponse(f"/#sso={sso.issue_exchange_code(user)}", status_code=302)


class ExchangeRequest(BaseModel):
    code: str = Field(..., min_length=10, max_length=200)


@router.post("/api/v1/sso/exchange", summary="Đổi mã một lần lấy phiên đăng nhập", tags=_TAG)
async def sso_exchange(payload: ExchangeRequest) -> Dict[str, Any]:
    user = sso.redeem_exchange_code(payload.code)
    if not user:
        raise HTTPException(status_code=401, detail="Mã đăng nhập SSO không hợp lệ hoặc đã dùng / hết hạn.")
    token = auth_manager.create_access_token(data={"sub": user["username"], "role": user["role"], "amr": ["sso"]},
                                             expires_delta=timedelta(minutes=60 * 24))
    return {"status": "success", "access_token": token, "token_type": "bearer",
            "user": {"username": user["username"], "full_name": user["full_name"], "role": user["role"]}}
