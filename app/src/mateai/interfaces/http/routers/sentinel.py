# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 Dương Thanh Lịch — VN-MateAI. See LICENSE and NOTICE.
"""
mateai/interfaces/http/routers/sentinel.py
===========================================
Sentinel: kiểm tra chủ động và mô phỏng sự cố.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Body, Depends, HTTPException, Query, Request, status
from fastapi.responses import FileResponse, JSONResponse, Response
from pydantic import BaseModel, Field

from core.plugin_manager import run_blocking
from mateai.config.loader import get_assistant_name, settings
from mateai.interfaces.http.auth_dependencies import get_current_user, require_roles
from mateai.interfaces.websocket.realtime_hub import broadcast_hud, broadcast_portal_ui

logger = logging.getLogger(__name__)

router = APIRouter()


class SentinelSimulateRequest(BaseModel):
    """Payload for POST /api/v1/sentinel/simulate."""
    category: str = Field(default="network", description="network | ad_sync | sql_deadlock | hardware")
    title: str = Field(default="IIS Server 503 Error!", description="Tiêu đề sự cố")
    message: str = Field(default="Dịch vụ máy chủ IIS bị dừng hoặc trả về mã lỗi 503 Service Unavailable.", description="Mô tả sự cố chi tiết")


@router.post(
    "/api/v1/sentinel/check",
    summary="Phase 43: Quét kiểm tra sự cố toàn hệ thống qua Autonomous Sentinel",
    tags=["Autonomous Sentinel"],
)
async def sentinel_check_endpoint(
    user: dict = Depends(require_roles(["manager", "admin"])),
) -> Dict[str, Any]:
    """Thực hiện quét tức thời mạng LAN, đồng bộ AD, SQLite DB lock, và tài nguyên phần cứng."""
    from mateai.application.operations.autonomous_sentinel import autonomous_sentinel
    incidents = await autonomous_sentinel.scan_all()
    dispatched = []
    for inc in incidents:
        sent = await autonomous_sentinel.dispatch_incident(
            title=inc["title"],
            message=inc["message"],
            category=inc.get("category", "general"),
            force=True,
        )
        if sent:
            dispatched.append(inc["title"])
    return {
        "status": "success",
        "incidents_found": len(incidents),
        "incidents": incidents,
        "dispatched_to_xiaozhi": dispatched,
    }


@router.post(
    "/api/v1/sentinel/simulate",
    summary="Phase 43: Mô phỏng sự cố để kiểm tra luồng Push Notification tới Xiaozhi",
    tags=["Autonomous Sentinel"],
)
async def sentinel_simulate_endpoint(
    payload: SentinelSimulateRequest,
    user: dict = Depends(require_roles(["admin"])),
) -> Dict[str, Any]:
    """Mô phỏng phát hiện sự cố máy chủ và kích hoạt đánh thức Desktop Robot + Telegram alert."""
    from mateai.application.operations.autonomous_sentinel import autonomous_sentinel
    sent = await autonomous_sentinel.dispatch_incident(
        title=payload.title,
        message=payload.message,
        category=payload.category,
        force=True,
    )
    return {
        "status": "success",
        "simulated_incident": {
            "category": payload.category,
            "title": payload.title,
            "message": payload.message,
        },
        "dispatched": sent,
    }
