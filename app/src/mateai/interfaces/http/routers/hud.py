# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 Dương Thanh Lịch — VN-MateAI. See LICENSE and NOTICE.
"""
mateai/interfaces/http/routers/hud.py
======================================
HUD: mô phỏng sự kiện hiển thị (kiểm thử giao diện).
"""
from __future__ import annotations

import asyncio
import base64
import json
import logging
import time
from datetime import datetime
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from pydantic import BaseModel, Field

from mateai.application.voice.speech_text import sanitise_for_tts, shorten_for_speech
from mateai.config.loader import get_assistant_name, settings
from mateai.interfaces.http import hud_voice, speech
from mateai.interfaces.http.auth_dependencies import get_current_user, require_roles
from mateai.interfaces.websocket.realtime_hub import active_hud_websockets, broadcast_hud, broadcast_hud_binary, broadcast_portal_ui

logger = logging.getLogger(__name__)

router = APIRouter()


class HudSimulateRequest(BaseModel):
    """Payload for POST /api/v1/hud/simulate."""
    type: str = Field(default="voice_active", description="voice_active | system_log | metrics_update")
    status: Optional[str] = Field(default="speaking", description="listening | processing | speaking | idle")
    text: Optional[str] = Field(default="Xin chào, em là Ly Ly. Tất cả các hệ thống phòng thủ và mạng lưới đang hoạt động tối ưu.")
    level: Optional[str] = Field(default="INFO")
    message: Optional[str] = Field(default="Sentinel Guard: Kiểm tra an ninh định kỳ hoàn tất.")
    data: Optional[Dict[str, Any]] = None


@router.post(
    "/api/v1/hud/simulate",
    summary="Phase 33: Mô phỏng phát tín hiệu tới VN-MateAI Standby HUD",
    tags=["Orchestrator"],
)
async def simulate_hud_endpoint(
    payload: HudSimulateRequest,
    user: dict = Depends(require_roles(["manager", "admin"])),
) -> Dict[str, Any]:
    """Phát thử gói tin sự kiện xuống toàn bộ màn hình HUD Standby để kiểm tra visualizer."""
    if payload.type == "voice_active":
        ai_name = get_assistant_name()
        pkt = {
            "type": "voice_active",
            "status": payload.status or "speaking",
            "text": payload.text or f"{ai_name} Audio Stream Active",
            "timestamp": datetime.utcnow().isoformat(),
        }
    elif payload.type == "system_log":
        pkt = {
            "type": "system_log",
            "level": payload.level or "INFO",
            "message": payload.message or "System Log Test",
            "timestamp": datetime.utcnow().isoformat(),
        }
    else:
        pkt = {
            "type": payload.type,
            "data": payload.data or {},
            "timestamp": datetime.utcnow().isoformat(),
        }
    await broadcast_hud(pkt)
    return {"status": "success", "sent": pkt, "active_hud_count": len(active_hud_websockets)}
