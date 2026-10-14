# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 Dương Thanh Lịch — VN-MateAI. See LICENSE and NOTICE.
"""
mateai/interfaces/http/routers/domain.py
=========================================
Đồng bộ Active Directory: cấu hình, bật/tắt, đồng bộ, thống kê, danh sách nhân viên/máy.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel, Field

from core.plugin_manager import run_blocking
from mateai.interfaces.http.auth_dependencies import get_current_user, require_roles

logger = logging.getLogger(__name__)

router = APIRouter()


class DomainToggleRequest(BaseModel):
    enabled: bool = Field(..., description="Bật/Tắt tính năng đồng bộ Active Directory")


@router.get(
    "/api/v1/domain/config",
    summary="Phase 18: Lấy trạng thái Bật/Tắt Đồng bộ AD",
    tags=["Domain"],
)
async def get_domain_config(
    current_user: Dict[str, Any] = Depends(get_current_user),
) -> Dict[str, Any]:
    """Return enabled status for Active Directory synchronization."""
    try:
        from mateai.config.loader import get_config_section
        enabled = get_config_section("ad_sync").get("enabled", False)
        return {"status": "success", "enabled": enabled}
    except Exception as exc:
        return {"status": "error", "enabled": False, "message": str(exc)}


@router.post(
    "/api/v1/domain/toggle",
    summary="Phase 18: Bật hoặc Tắt tính năng Đồng bộ AD & Sentinel Check",
    tags=["Domain"],
)
async def toggle_domain_sync(
    payload: DomainToggleRequest,
    current_user: Dict[str, Any] = Depends(get_current_user),
) -> Dict[str, Any]:
    """Enable or disable AD sync and avoid flooding logs when not used."""
    if current_user.get("role") not in ("admin", "manager"):
        raise HTTPException(status_code=403, detail="Chỉ Admin hoặc Manager mới có quyền thay đổi trạng thái AD.")
    try:
        from mateai.application.administration import config_governance as gov
        gov.save_config(str(current_user.get("username", "?")),
                        lambda c: c.setdefault("ad_sync", {}).update({"enabled": bool(payload.enabled)}),
                        "Bật/tắt đồng bộ Active Directory")

        status_text = "ĐÃ BẬT" if payload.enabled else "ĐÃ TẮT"
        logger.info("Active Directory sync feature has been %s by %s", status_text, current_user.get("username", "?"))
        return {
            "status": "success",
            "enabled": payload.enabled,
            "message": f"Tính năng đồng bộ Active Directory {status_text} thành công."
        }
    except Exception as exc:
        logger.error("Failed to toggle AD sync: %s", exc)
        raise HTTPException(status_code=500, detail=f"Lỗi cập nhật cấu hình: {exc}")


@router.post(
    "/api/v1/domain/sync",
    summary="Phase 18: Đồng bộ dữ liệu Active Directory (Users + Computers)",
    tags=["Domain"],
)
async def sync_domain(
    current_user: Dict[str, Any] = Depends(get_current_user),
) -> Dict[str, Any]:
    """
    Trigger full Active Directory synchronization via native PowerShell (Get-ADUser + Get-ADComputer).
    Requires RSAT: Active Directory Domain Services Tools installed on server.
    """
    if current_user.get("role") not in ("admin", "manager"):
        raise HTTPException(status_code=403, detail="Chỉ Admin hoặc Manager mới có quyền đồng bộ AD.")

    try:
        from mateai.infrastructure.directory.domain_sync import domain_manager
        loop = asyncio.get_event_loop()
        result = await loop.run_in_executor(None, domain_manager.sync_all)
        logger.info(
            "Phase 18: Domain sync triggered by '%s' — users=%d, computers=%d",
            current_user.get("username", "?"),
            result.get("total_users", 0),
            result.get("total_computers", 0),
        )
        return result
    except Exception as exc:
        logger.error("Phase 18: Domain sync error: %s", exc)
        raise HTTPException(status_code=500, detail=f"Lỗi đồng bộ AD: {exc}")


@router.get(
    "/api/v1/domain/stats",
    summary="Phase 18: Trả về thống kê số lượng từ AD cache",
    tags=["Domain"],
)
async def domain_stats(
    current_user: Dict[str, Any] = Depends(get_current_user),
) -> Dict[str, Any]:
    """Return employee and computer counts from local SQLite AD cache."""
    try:
        from mateai.infrastructure.directory.domain_sync import domain_manager
        stats = domain_manager.get_stats()
        return {"status": "success", **stats}
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Lỗi truy vấn thống kê: {exc}")


@router.get(
    "/api/v1/domain/employees",
    summary="Phase 18: Lấy danh sách nhân viên từ AD cache",
    tags=["Domain"],
)
async def list_employees(
    limit: int = 100,
    current_user: Dict[str, Any] = Depends(get_current_user),
) -> Dict[str, Any]:
    """Return a list of employees from the local SQLite AD cache."""
    try:
        from mateai.infrastructure.directory.domain_sync import domain_manager
        employees = domain_manager.get_employees(limit=limit)
        return {"status": "success", "count": len(employees), "data": employees}
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Lỗi truy vấn danh sách nhân viên: {exc}")


@router.get(
    "/api/v1/domain/computers",
    summary="Phase 18: Lấy danh sách máy tính từ AD cache",
    tags=["Domain"],
)
async def list_computers(
    limit: int = 100,
    current_user: Dict[str, Any] = Depends(get_current_user),
) -> Dict[str, Any]:
    """Return a list of computers from the local SQLite AD cache."""
    try:
        from mateai.infrastructure.directory.domain_sync import domain_manager
        computers = domain_manager.get_computers(limit=limit)
        out: Dict[str, Any] = {"status": "success", "count": len(computers), "data": computers}
        # Trạng thái Agent từng máy (đã cài / trực tuyến / chưa cài) + tóm tắt trên TOÀN BỘ máy AD.
        # Lỗi ở đây không được làm mất danh sách máy.
        try:
            from mateai.application.devices import worker_enrollment
            from mateai.application.devices import workstation_directory as wd
            from mateai.interfaces.websocket.client_orchestrator import orchestrator
            index = wd.build_index(orchestrator.get_connected_clients(), worker_enrollment.list_devices())
            for c in computers:
                c["agent"] = wd.agent_status(c.get("hostname"), index)
            everyone = domain_manager.get_computers(limit=1_000_000)
            out["agent_coverage"] = wd.coverage([c.get("hostname") for c in everyone], index)
        except Exception as exc:  # noqa: BLE001
            logging.getLogger(__name__).warning("Không tính được trạng thái Agent cho máy AD: %s", exc)
        return out
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Lỗi truy vấn danh sách máy tính: {exc}")
