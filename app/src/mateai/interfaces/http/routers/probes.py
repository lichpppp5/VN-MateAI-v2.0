# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 Dương Thanh Lịch — VN-MateAI. See LICENSE and NOTICE.
"""
mateai/interfaces/http/routers/probes.py
========================================
Health probe kiểu Kubernetes. Chuyển từ `server.py` (Supervisor Phase 10, §198).

Ngoài /api/v1/ nên middleware JWT không chặn; chỉ trả trạng thái, không lộ cấu hình.
/api/v1/health-dashboard là telemetry cho HUD, không phải probe. Chi tiết từng bước
khởi động: `GET /api/v1/health/startup` (admin, routers/health.py).
"""
from __future__ import annotations

import asyncio
from typing import Dict

from fastapi import APIRouter
from fastapi.responses import JSONResponse

from mateai.interfaces.http import lifecycle

router = APIRouter()


@router.get("/livez", include_in_schema=False)
async def livez() -> Dict[str, str]:
    """Tiến trình còn sống và event loop còn phục vụ request."""
    return {"status": "ok"}


@router.get("/startupz", include_in_schema=False)
async def startupz() -> JSONResponse:
    """200 khi lifecycle startup đã chạy xong."""
    done = lifecycle.STATE.complete
    return JSONResponse(status_code=200 if done else 503,
                        content={"status": "ok" if done else "starting"})


def _check_database() -> None:
    from mateai.infrastructure.database.erp_database import erp_db
    erp_db.ping()


@router.get("/readyz", include_in_schema=False)
async def readyz() -> JSONResponse:
    """Sẵn sàng nhận việc: startup xong, DB đọc được, đã nạp skill."""
    checks: Dict[str, str] = {"startup": "ok" if lifecycle.STATE.complete else "starting"}
    try:
        await asyncio.wait_for(asyncio.to_thread(_check_database), timeout=3.0)
        checks["database"] = "ok"
    except Exception as exc:  # pylint: disable=broad-except
        checks["database"] = f"error: {type(exc).__name__}"
    try:
        from core.plugin_manager import plugin_manager
        checks["skills"] = "ok" if plugin_manager.get_skill_count() > 0 else "none loaded"
    except Exception as exc:  # pylint: disable=broad-except
        checks["skills"] = f"error: {type(exc).__name__}"
    ready = all(v == "ok" for v in checks.values())
    return JSONResponse(status_code=200 if ready else 503,
                        content={"status": "ok" if ready else "not_ready", "checks": checks})
