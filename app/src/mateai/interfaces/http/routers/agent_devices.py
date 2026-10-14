# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 Dương Thanh Lịch — VN-MateAI. See LICENSE and NOTICE.
"""
mateai/interfaces/http/routers/agent_devices.py
===============================================
API cho Agent máy trạm:

  Agent gọi (không có tài khoản người dùng — route tự xác thực):
    POST /api/v1/agent/enroll                 mã đăng ký dùng một lần -> khoá thiết bị riêng
    GET  /api/v1/agent/update/manifest        phiên bản + SHA-256 gói mới (khoá thiết bị)
    GET  /api/v1/agent/update/package         tải gói cập nhật (khoá thiết bị)

  Quản trị (Portal):
    GET    /api/v1/agent/devices                    máy trạm đã đăng ký + trực tuyến + mã đăng ký
    POST   /api/v1/agent/devices/{client_id}/revoke thu hồi khoá một máy, cắt kết nối ngay
    POST   /api/v1/agent/enroll-codes               tạo mã đăng ký (hiện MỘT lần)
    DELETE /api/v1/agent/enroll-codes/{code_ref}    huỷ mã chưa dùng
    GET    /api/v1/agent/packages                   gói nào có sẵn (source / Windows .exe / macOS)
"""
from __future__ import annotations

import logging
import time
from collections import defaultdict, deque
from typing import Any, Deque, Dict

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from pydantic import BaseModel, Field

from functools import partial

from core.plugin_manager import run_blocking
from mateai.interfaces.http import agent_packages, ws_auth
from mateai.interfaces.http.auth_dependencies import require_roles

logger = logging.getLogger(__name__)
router = APIRouter(tags=["Agent máy trạm"])

#: Chống dò mã: tối đa 10 lần đăng ký SAI / 10 phút / IP.
_FAILS: Dict[str, Deque[float]] = defaultdict(deque)
_FAIL_LIMIT, _FAIL_WINDOW = 10, 600.0


class EnrollRequest(BaseModel):
    code: str = Field(..., max_length=128)
    hostname: str = Field("", max_length=120)
    platform: str = Field("", max_length=120)
    package: str = Field("source", max_length=40)
    agent_version: str = Field("", max_length=20)


class CodeRequest(BaseModel):
    label: str = Field("", max_length=80)
    ttl_days: int = Field(7, ge=1, le=90)


def _audit(client: str, action: str, status: str, details: Dict[str, Any]) -> None:
    try:
        from mateai.application.security.safety_guard import security_engine
        security_engine.log_audit(client, action, "AGENT", status, details)
    except Exception as exc:  # noqa: BLE001
        logger.warning("Không ghi được audit %s: %s", action, exc)


@router.post("/api/v1/agent/enroll", summary="Agent đổi mã đăng ký dùng một lần lấy khoá thiết bị")
async def enroll_agent(payload: EnrollRequest, request: Request) -> Dict[str, Any]:
    from mateai.application.devices import worker_enrollment
    ip = request.client.host if request.client else "?"
    q = _FAILS[ip]
    now = time.time()
    while q and now - q[0] > _FAIL_WINDOW:
        q.popleft()
    if len(q) >= _FAIL_LIMIT:
        raise HTTPException(status_code=429, detail="Thử sai quá nhiều lần — đợi 10 phút.")
    try:
        res = await run_blocking(partial(worker_enrollment.enroll, payload.code, payload.hostname, payload.platform,
                                 payload.package, payload.agent_version))
    except worker_enrollment.EnrollError as exc:
        q.append(now)
        _audit(f"ip:{ip}", "agent_enroll", "REJECTED", {"hostname": payload.hostname})
        raise HTTPException(status_code=403, detail=str(exc))
    _audit(res["client_id"], "agent_enroll", "SUCCESS",
           {"hostname": payload.hostname, "platform": payload.platform, "ip": ip})
    logger.info("Máy trạm mới đăng ký: %s (%s, %s)", res["client_id"], payload.hostname, ip)
    return {"status": "success", **res}


def _device_or_401(request: Request) -> Dict[str, Any]:
    auth = request.headers.get("authorization") or ""
    token = auth[7:].strip() if auth.lower().startswith("bearer ") else ""
    principal = ws_auth.worker_principal(token, request.client.host if request.client else "")
    if principal is None:
        raise HTTPException(status_code=401, detail="Khoá máy trạm sai hoặc đã bị thu hồi.")
    return principal


@router.get("/api/v1/agent/update/manifest", summary="Phiên bản + SHA-256 gói Agent mới nhất")
async def update_manifest(request: Request, package: str = Query("source")) -> Dict[str, Any]:
    _device_or_401(request)
    if package not in agent_packages.PACKAGES:
        raise HTTPException(status_code=400, detail=f"package không hợp lệ: {package}")
    m = await run_blocking(partial(agent_packages.manifest, package))
    if not m:
        raise HTTPException(status_code=404, detail=f"Máy chủ chưa có gói '{package}'.")
    return m


@router.get("/api/v1/agent/update/package", summary="Tải gói cập nhật Agent")
async def update_package(request: Request, package: str = Query("source")) -> Response:
    principal = _device_or_401(request)
    if package not in agent_packages.PACKAGES:
        raise HTTPException(status_code=400, detail=f"package không hợp lệ: {package}")
    data = await run_blocking(partial(agent_packages.package_bytes, package))
    if not data:
        raise HTTPException(status_code=404, detail=f"Máy chủ chưa có gói '{package}'.")
    import hashlib
    _audit(principal.get("client_id") or principal["kind"], "agent_update_download", "SUCCESS",
           {"package": package, "version": agent_packages.latest_version(package)})
    return Response(content=data, media_type="application/octet-stream",
                    headers={"X-Agent-SHA256": hashlib.sha256(data).hexdigest(),
                             "X-Agent-Version": agent_packages.latest_version(package) or ""})


@router.get("/api/v1/agent/devices", summary="Máy trạm đã đăng ký + mã đăng ký")
async def list_agent_devices(user: dict = Depends(require_roles(["manager", "admin"]))) -> Dict[str, Any]:
    from mateai.application.devices import worker_enrollment
    from mateai.interfaces.websocket.client_orchestrator import orchestrator
    online = {c["client_id"]: c for c in orchestrator.get_connected_clients()}
    devices = await run_blocking(worker_enrollment.list_devices)
    for d in devices:
        live = online.get(d["client_id"])
        d["online"] = live is not None
        if live:
            d["metrics"] = live.get("metrics")
            d["agent_version"] = live.get("agent_version") or d.get("agent_version")
    return {"status": "success", "devices": devices,
            "codes": await run_blocking(worker_enrollment.list_codes),
            "packages": await run_blocking(agent_packages.available)}


@router.post("/api/v1/agent/devices/{client_id}/revoke", summary="Thu hồi khoá một máy trạm")
async def revoke_agent_device(client_id: str, user: dict = Depends(require_roles(["admin"]))) -> Dict[str, Any]:
    from mateai.application.devices import worker_enrollment
    from mateai.interfaces.websocket.client_orchestrator import orchestrator
    if not await run_blocking(partial(worker_enrollment.revoke, client_id, str(user.get("username")))):
        raise HTTPException(status_code=404, detail=f"Không có máy '{client_id}' (hoặc đã thu hồi).")
    cut = await orchestrator.disconnect_client(client_id, "Khoá máy trạm đã bị thu hồi")
    _audit(client_id, "agent_revoke", "SUCCESS", {"by": user.get("username"), "disconnected": cut})
    return {"status": "success", "client_id": client_id, "disconnected": cut}


@router.post("/api/v1/agent/enroll-codes", summary="Tạo mã đăng ký dùng một lần")
async def create_agent_enroll_code(payload: CodeRequest,
                                   user: dict = Depends(require_roles(["admin"]))) -> Dict[str, Any]:
    from mateai.application.devices import worker_enrollment
    code, info = await run_blocking(partial(worker_enrollment.create_enroll_code, str(user.get("username")),
                                    payload.label, payload.ttl_days))
    _audit(f"code:{info['code_ref']}", "agent_enroll_code", "CREATED",
           {"by": user.get("username"), "label": payload.label, "expires_at": info["expires_at"]})
    return {"status": "success", "code": code, **info,
            "note": "Mã chỉ hiện MỘT lần. Dùng một lần cho một máy (điền 'enroll_code' trong config.json)."}


@router.delete("/api/v1/agent/enroll-codes/{code_ref}", summary="Huỷ mã đăng ký chưa dùng")
async def delete_agent_enroll_code(code_ref: str, user: dict = Depends(require_roles(["admin"]))) -> Dict[str, Any]:
    from mateai.application.devices import worker_enrollment
    if not await run_blocking(partial(worker_enrollment.delete_code, code_ref)):
        raise HTTPException(status_code=404, detail="Không có mã chưa dùng với mã tham chiếu này.")
    return {"status": "success"}


@router.get("/api/v1/agent/packages", summary="Gói Agent có sẵn để tải / cập nhật")
async def list_agent_packages(user: dict = Depends(require_roles(["manager", "admin"]))) -> Dict[str, Any]:
    return {"status": "success", "packages": await run_blocking(agent_packages.available)}
