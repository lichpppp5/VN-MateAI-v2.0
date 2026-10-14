# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 Dương Thanh Lịch — VN-MateAI. See LICENSE and NOTICE.
"""
mateai/interfaces/http/routers/auth.py
=======================================
Đăng nhập (cấp JWT) và thông tin người dùng hiện tại.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from datetime import datetime, timedelta
from functools import partial
from pathlib import Path
from typing import Any, Dict, List, Optional

import psutil
from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from core.plugin_manager import run_blocking
from mateai.application.security import mfa
from mateai.application.security.auth_manager import auth_manager
from mateai.config.loader import get_assistant_name, settings
from mateai.interfaces.http import log_stream
from mateai.interfaces.http.auth_dependencies import get_current_user, http_bearer, require_roles
from mateai.interfaces.websocket.realtime_hub import active_audio_nodes

logger = logging.getLogger(__name__)

router = APIRouter()


LOGIN_MAX_FAILURES = 8
LOGIN_WINDOW_S = 300.0
LOGIN_LOCK_S = 300.0
def _store():
    """Mốc đăng nhập sai ở kho dùng chung (Redis khi có): khoá áp cho MỌI tiến trình (§145)."""
    from mateai.infrastructure.cache import shared_state
    return shared_state.store()


def _login_locked(keys) -> float:
    """Số giây còn phải chờ (0 = được thử). Khoá khi `LOGIN_MAX_FAILURES` lần sai gần
    nhất nằm trong `LOGIN_WINDOW_S`; khoá kéo dài `LOGIN_LOCK_S` kể từ lần sai cuối."""
    now = time.time()
    wait = 0.0
    for k in keys:
        fails = sorted(_store().events_since("login:" + k, now - LOGIN_WINDOW_S - LOGIN_LOCK_S))
        if len(fails) >= LOGIN_MAX_FAILURES and fails[-1] - fails[-LOGIN_MAX_FAILURES] <= LOGIN_WINDOW_S:
            wait = max(wait, LOGIN_LOCK_S - (now - fails[-1]))
    return max(0.0, wait)


def _login_failed(keys) -> None:
    now = time.time()
    for k in keys:
        _store().events_add("login:" + k, now)


def _login_audit(username: str, ip: str, event: str) -> None:
    try:
        from mateai.application.security.safety_guard import security_engine
        security_engine.log_audit(str(username)[:80], "login", "AUTH", "SUCCESS" if event == "LOGIN_OK" else "REJECTED",
                                  {"event": event, "source_ip": ip})
    except Exception:  # noqa: BLE001
        pass


class LoginRequest(BaseModel):
    """Payload for POST /api/v1/login."""
    username: str = Field(..., min_length=1, description="Tên đăng nhập")
    password: str = Field(..., min_length=1, description="Mật khẩu")


@router.post(
    "/api/v1/login",
    summary="Đăng nhập Web Portal và nhận JWT Access Token",
    tags=["Authentication"],
)
async def login_endpoint(payload: LoginRequest, request: Request) -> Dict[str, Any]:
    """Xác thực người dùng và cấp JWT Bearer Token.

    Giới hạn dò mật khẩu (prompt §78): sai quá `LOGIN_MAX_FAILURES` lần trong
    `LOGIN_WINDOW_S` theo IP HOẶC theo tài khoản -> 429 trong `LOGIN_LOCK_S`. Mọi lần
    đăng nhập (đúng / sai / bị khoá) vào audit (§68). bcrypt chạy ngoài event loop."""
    ip = request.client.host if request.client else "?"
    keys = (f"ip:{ip}", f"user:{payload.username.strip().lower()}")
    wait = _login_locked(keys)
    if wait:
        _login_audit(payload.username, ip, "LOGIN_LOCKED")
        raise HTTPException(status_code=429, headers={"Retry-After": str(int(wait) + 1)},
                            detail=f"Đăng nhập sai quá nhiều lần. Thử lại sau {int(wait) + 1} giây.")
    user = await run_blocking(auth_manager.authenticate_user, username=payload.username, password=payload.password)
    if not user:
        _login_failed(keys)
        _login_audit(payload.username, ip, "LOGIN_FAILED")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Tên đăng nhập hoặc mật khẩu không chính xác.",
        )
    # Bước 2 (MFA): đúng mật khẩu CHƯA phải đăng nhập — chỉ cấp token trung gian 5 phút (scope `mfa`, không dùng làm token truy cập).
    if user.get("mfa_enabled"):
        _login_audit(user["username"], ip, "LOGIN_PASSWORD_OK_MFA_PENDING")
        return {"status": "mfa_required", "mfa_token": auth_manager.create_access_token(
            data={"sub": user["username"], "scope": "mfa"}, expires_delta=timedelta(minutes=5))}
    # Vai trò bắt buộc MFA mà chưa bật: chỉ được vào màn cài MFA.
    required = {r.strip().lower() for r in (settings.security.require_mfa_roles or [])}
    if str(user.get("role", "viewer")).lower() in required and (user.get("auth_source") or "local") != "sso":
        _login_audit(user["username"], ip, "LOGIN_PASSWORD_OK_MFA_ENROLL_REQUIRED")
        return {"status": "mfa_setup_required", "setup_token": auth_manager.create_access_token(
            data={"sub": user["username"], "scope": "mfa_enroll"}, expires_delta=timedelta(minutes=15)),
            "detail": "Vai trò của bạn bắt buộc xác thực hai lớp. Hãy cài MFA để tiếp tục."}
    _store().events_clear("login:" + keys[1])
    _login_audit(user["username"], ip, "LOGIN_OK")

    access_token = auth_manager.create_access_token(
        data={"sub": user["username"], "role": user.get("role", "viewer")},
        expires_delta=timedelta(minutes=60 * 24),
    )
    logger.info("Người dùng '%s' (role: %s) đã đăng nhập thành công.", user["username"], user.get("role"))
    return {
        "status": "success",
        "access_token": access_token,
        "token_type": "bearer",
        "user": {
            "username": user["username"],
            "full_name": user.get("full_name", user["username"]),
            "role": user.get("role", "viewer"),
        },
    }


class MfaLoginRequest(BaseModel):
    mfa_token: str = Field(..., min_length=10)
    code: str = Field(..., min_length=6, max_length=16, description="Mã 6 số của ứng dụng xác thực, hoặc một mã khôi phục")


@router.post("/api/v1/login/mfa", summary="Đăng nhập bước 2: nhập mã MFA (TOTP hoặc mã khôi phục)", tags=["Authentication"])
async def login_mfa_endpoint(payload: MfaLoginRequest, request: Request) -> Dict[str, Any]:
    ip = request.client.host if request.client else "?"
    data = auth_manager.decode_access_token(payload.mfa_token, allow_scopes=("mfa",))
    if not data or data.get("scope") != "mfa" or not data.get("sub"):
        raise HTTPException(status_code=401, detail="Phiên xác thực đã hết hạn — hãy đăng nhập lại từ đầu.")
    username = str(data["sub"])
    keys = (f"ip:{ip}", f"user:{username.strip().lower()}")
    wait = _login_locked(keys)
    if wait:
        _login_audit(username, ip, "LOGIN_LOCKED")
        raise HTTPException(status_code=429, headers={"Retry-After": str(int(wait) + 1)},
                            detail=f"Nhập sai quá nhiều lần. Thử lại sau {int(wait) + 1} giây.")
    ok = await run_blocking(partial(mfa.check_login_code, username, payload.code))
    if not ok:
        _login_failed(keys)
        _login_audit(username, ip, "LOGIN_MFA_FAILED")
        raise HTTPException(status_code=401, detail="Mã không đúng hoặc đã dùng rồi.")
    user = auth_manager.get_user(username)
    if not user:
        raise HTTPException(status_code=401, detail="Tài khoản không còn tồn tại.")
    _store().events_clear("login:" + keys[1])
    _login_audit(username, ip, "LOGIN_OK")
    return {"status": "success", "access_token": auth_manager.create_access_token(
        data={"sub": user["username"], "role": user.get("role", "viewer"), "amr": ["pwd", "otp"]},
        expires_delta=timedelta(minutes=60 * 24)), "token_type": "bearer",
        "user": {"username": user["username"], "full_name": user.get("full_name", user["username"]), "role": user.get("role", "viewer")}}


async def _mfa_actor(credentials=Depends(http_bearer)) -> Dict[str, Any]:
    """Người dùng đang gọi các đường MFA: nhận token thường HOẶC token cài MFA (`mfa_enroll`)."""
    raw = credentials.credentials if credentials else ""
    payload = auth_manager.decode_access_token(raw, allow_scopes=("mfa_enroll",)) if raw else None
    user = auth_manager.get_user(str(payload["sub"])) if payload and payload.get("sub") else None
    if not user:
        raise HTTPException(status_code=401, detail="Phiên đăng nhập không hợp lệ hoặc đã hết hạn.", headers={"WWW-Authenticate": "Bearer"})
    return {"username": user["username"], "full_name": user.get("full_name", user["username"]), "role": user.get("role", "viewer"),
            "enroll_only": payload.get("scope") == "mfa_enroll"}


def _mfa_audit(actor: str, action: str, target: str = "") -> None:
    try:
        from mateai.application.security.safety_guard import security_engine
        security_engine.log_audit(actor, action, "AUTH", "SUCCESS", {"target": target or actor})
    except Exception:  # noqa: BLE001
        pass


@router.get("/api/v1/auth/mfa/status", summary="Trạng thái MFA của tôi", tags=["Authentication"])
async def mfa_status(actor: Dict[str, Any] = Depends(_mfa_actor)) -> Dict[str, Any]:
    required = actor["role"].lower() in {r.strip().lower() for r in (settings.security.require_mfa_roles or [])}
    return {"status": "success", **(await run_blocking(partial(mfa.status, actor["username"]))), "required_for_my_role": required}


@router.post("/api/v1/auth/mfa/setup", summary="Bắt đầu cài MFA: sinh mã bí mật (chưa bật cho tới khi xác nhận)", tags=["Authentication"])
async def mfa_setup(actor: Dict[str, Any] = Depends(_mfa_actor)) -> Dict[str, Any]:
    try:
        info = await run_blocking(partial(mfa.begin_setup, actor["username"]))
    except mfa.MfaError as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    return {"status": "success", **info}


class MfaCode(BaseModel):
    code: str = Field(..., min_length=6, max_length=16)


@router.post("/api/v1/auth/mfa/enable", summary="Xác nhận mã đầu tiên để bật MFA — trả 10 mã khôi phục (chỉ hiện một lần)", tags=["Authentication"])
async def mfa_enable(payload: MfaCode, actor: Dict[str, Any] = Depends(_mfa_actor)) -> Dict[str, Any]:
    try:
        codes = await run_blocking(partial(mfa.confirm_enable, actor["username"], payload.code))
    except mfa.MfaError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    _mfa_audit(actor["username"], "mfa_enabled")
    out: Dict[str, Any] = {"status": "success", "recovery_codes": codes,
                           "message": "Hãy lưu 10 mã khôi phục ở nơi an toàn — mỗi mã dùng một lần và sẽ không hiện lại."}
    if actor["enroll_only"]:           # đang bị buộc cài MFA: vừa chứng minh có ứng dụng xác thực -> cấp luôn phiên đăng nhập đầy đủ
        out.update(access_token=auth_manager.create_access_token(
            data={"sub": actor["username"], "role": actor["role"], "amr": ["pwd", "otp"]}, expires_delta=timedelta(minutes=60 * 24)),
            token_type="bearer", user={"username": actor["username"], "full_name": actor["full_name"], "role": actor["role"]})
    return out


class MfaDisable(BaseModel):
    password: str = Field(..., min_length=1)
    code: str = Field(..., min_length=6, max_length=16)


@router.post("/api/v1/auth/mfa/disable", summary="Tắt MFA của tôi (cần mật khẩu + một mã MFA)", tags=["Authentication"])
async def mfa_disable(payload: MfaDisable, actor: Dict[str, Any] = Depends(_mfa_actor)) -> Dict[str, Any]:
    if actor["enroll_only"]:
        raise HTTPException(status_code=403, detail="Chưa đăng nhập đầy đủ.")
    if actor["role"].lower() in {r.strip().lower() for r in (settings.security.require_mfa_roles or [])}:
        raise HTTPException(status_code=403, detail="Vai trò của bạn bắt buộc MFA nên không tự tắt được — nhờ quản trị viên.")
    if not await run_blocking(auth_manager.authenticate_user, username=actor["username"], password=payload.password):
        raise HTTPException(status_code=401, detail="Mật khẩu không đúng.")
    if not await run_blocking(partial(mfa.check_login_code, actor["username"], payload.code)):
        raise HTTPException(status_code=401, detail="Mã MFA không đúng.")
    await run_blocking(partial(mfa.disable, actor["username"]))
    _mfa_audit(actor["username"], "mfa_disabled")
    return {"status": "success"}


@router.post("/api/v1/users/{user_id}/mfa/reset", summary="Quản trị viên gỡ MFA của một tài khoản (mất thiết bị)", tags=["Authentication"])
async def mfa_admin_reset(user_id: str, admin: Dict[str, Any] = Depends(require_roles(["admin"]))) -> Dict[str, Any]:
    target = await run_blocking(partial(auth_manager.get_user, user_id))
    if not target:
        raise HTTPException(status_code=404, detail="Không có tài khoản này.")
    await run_blocking(partial(mfa.disable, target["username"]))
    _mfa_audit(str(admin.get("username")), "mfa_admin_reset", target["username"])
    return {"status": "success", "user": target["username"]}


@router.get(
    "/api/v1/auth/me",
    summary="Lấy thông tin tài khoản người dùng hiện tại",
    tags=["Authentication"],
)
async def get_me_endpoint(current_user: Dict[str, Any] = Depends(get_current_user)) -> Dict[str, Any]:
    """Trả về thông tin và quyền hạn của người dùng đang đăng nhập."""
    return {
        "status": "success",
        "user": current_user,
    }
