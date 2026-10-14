# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 Dương Thanh Lịch — VN-MateAI. See LICENSE and NOTICE.
"""
mateai/interfaces/http/routers/pairing.py
==========================================
Ghép đôi robot (mã ghép, trạng thái, huỷ ghép).
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


class PairingVerifyRequest(BaseModel):
    code: str = Field(..., min_length=4, max_length=10, description="Mã 6 số hiển thị trên màn hình Robot")


class PairingUnpairRequest(BaseModel):
    device_id: str


@router.post(
    "/api/v1/pairing/verify",
    summary="Xác nhận mã 6 số để ghép đôi Robot với Web Portal/HUD",
    tags=["Robotics Pairing"],
)
async def verify_pairing_code(
    payload: PairingVerifyRequest,
    user: dict = Depends(get_current_user),
) -> Dict[str, Any]:
    from mateai.interfaces.websocket.xiaozhi_gateway import xiaozhi_gateway, pairing_registry

    clean_code = payload.code.strip()
    device_id = pairing_registry.lookup(clean_code)
    if not device_id:
        for d_id, node in xiaozhi_gateway.get_all_nodes().items():
            if pairing_registry.get_code_for_device(d_id) == clean_code:
                device_id = d_id
                break

    if not device_id:
        raise HTTPException(
            status_code=404,
            detail=f"Mã '{clean_code}' không hợp lệ hoặc robot chưa trực tuyến. Hãy kiểm tra màn hình OLED của Robot!",
        )

    node = xiaozhi_gateway.get_node(device_id)
    # Gửi tín hiệu xác nhận thành công tới Robot để hiển thị trên OLED
    await xiaozhi_gateway.send_ui_payload(
        device_id=device_id,
        state="idle",
        emotion="happy",
        text="Ghep doi thanh cong!",
    )

    # Thông báo cho HUD và Portal UI
    try:
        await broadcast_portal_ui("robot_paired", {
            "device_id": device_id,
            "pairing_code": clean_code,
            "user": user.get("username"),
            "timestamp": datetime.utcnow().isoformat(),
        })
        await broadcast_hud({
            "type": "robot_status",
            "status": "paired",
            "device_id": device_id,
            "pairing_code": clean_code,
            "timestamp": datetime.utcnow().isoformat(),
        })
    except Exception:
        pass

    return {
        "success": True,
        "device_id": device_id,
        "pairing_code": clean_code,
        "message": f"Ghép đôi robot [{device_id}] thành công!",
        "telemetry": node.to_dict() if node else None,
    }


@router.get(
    "/api/v1/pairing/status",
    summary="Kiểm tra trạng thái các robot và mã pairing đang hoạt động",
    tags=["Robotics Pairing"],
)
async def get_pairing_status(
    user: dict = Depends(get_current_user),
) -> Dict[str, Any]:
    from mateai.interfaces.websocket.xiaozhi_gateway import xiaozhi_gateway, pairing_registry

    active_codes = pairing_registry.list_all()
    nodes_telemetry = xiaozhi_gateway.get_nodes_telemetry()

    for node_info in nodes_telemetry:
        d_id = node_info.get("device_id")
        node_info["pairing_code"] = pairing_registry.get_code_for_device(d_id) if d_id else None

    return {
        "active_codes": active_codes,
        "connected_robots": nodes_telemetry,
        "robot_count": len(nodes_telemetry),
    }


@router.post(
    "/api/v1/pairing/unpair",
    summary="Huỷ ghép đôi robot",
    tags=["Robotics Pairing"],
)
async def unpair_robot(
    payload: PairingUnpairRequest,
    user: dict = Depends(require_roles(["manager", "admin"])),
) -> Dict[str, Any]:
    from mateai.interfaces.websocket.xiaozhi_gateway import xiaozhi_gateway, pairing_registry

    await pairing_registry.unregister(payload.device_id)
    await xiaozhi_gateway.send_ui_payload(
        payload.device_id,
        state="idle",
        emotion="sleeping",
        text="Da huy ket noi.",
    )
    return {"success": True, "message": f"Đã huỷ ghép đôi robot [{payload.device_id}]."}
