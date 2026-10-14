# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 Dương Thanh Lịch — VN-MateAI. See LICENSE and NOTICE.
"""
mateai/interfaces/http/routers/wake_word.py
============================================
Micro / wake-word trên máy chủ: trạng thái và bật/tắt.
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


@router.get(
    "/api/v1/voice/mic-status",
    summary="Truy vấn trạng thái phần cứng Microphone",
    tags=["Voice"],
)
@router.get(
    "/api/v1/wake-word/status",
    summary="Truy vấn trạng thái phần cứng Microphone (Wake Word)",
    tags=["Voice"],
)
async def get_mic_status(
    current_user: Dict[str, Any] = Depends(get_current_user),
) -> Dict[str, Any]:
    """Trả về trạng thái bật/tắt của Microphone background listening."""
    try:
        from mateai.infrastructure.audio.wake_word_engine import is_mic_enabled
        enabled = is_mic_enabled()
    except Exception:
        enabled = False
    # Tên gọi đánh thức THẬT (cùng bộ nhận câu gọi của robot / HUD) và giọng đọc đang
    # cấu hình — trước đây thẻ trên Portal ghi cứng "Hey Lyly" / "Hoài My Neural".
    try:
        from mateai.infrastructure.audio.wake_word_engine import wake_names
        names = wake_names()
    except Exception:  # noqa: BLE001
        names = []
    try:
        from mateai.config.loader import get_config_section, settings
        audio = get_config_section("audio")
        voice = audio.get("tts_voice") or getattr(settings, "TTS_VOICE", "") or ""
        engine = {"elevenlabs": "ElevenLabs", "vbee": "Vbee AIVoice → 9Router / Edge-TTS dự phòng"}.get(
            audio.get("tts_engine"), "9Router TTS → Edge-TTS dự phòng")
    except Exception:  # noqa: BLE001
        voice, engine = "", ""
    return {
        "status": "success",
        "mic_enabled": enabled,
        "is_listening": enabled,
        "hardware_state": "listening" if enabled else "released",
        "message": "Microphone đang lắng nghe ngầm." if enabled else "Microphone đã tắt hoàn toàn (phần cứng giải phóng).",
        "wake_names": names,
        "tts_voice": voice,
        "tts_engine": engine,
    }


class MicToggleRequest(BaseModel):
    enabled: Optional[bool] = Field(None, description="True = Bật lắng nghe, False = Tắt và giải phóng phần cứng, None = Đảo trạng thái")


@router.post(
    "/api/v1/voice/mic-toggle",
    summary="Bật/Tắt Microphone ở cấp độ phần cứng",
    tags=["Voice"],
)
@router.post(
    "/api/v1/wake-word/toggle",
    summary="Bật/Tắt Microphone Wake Word ở cấp độ phần cứng",
    tags=["Voice"],
)
async def toggle_mic(
    payload: Optional[MicToggleRequest] = Body(default=None),
    current_user: Dict[str, Any] = Depends(get_current_user),
) -> Dict[str, Any]:
    """
    Điều khiển phần cứng Microphone.
    - enabled=true  → Bật lắng nghe ngầm, đèn Mic trên laptop sẽ sáng.
    - enabled=false → Tắt hoàn toàn, giải phóng stream, đèn Mic TẮT HẲN.
    Chỉ Admin và Manager được phép thay đổi.
    """
    if current_user.get("role") == "viewer":
        raise HTTPException(
            status_code=403,
            detail="Tài khoản Viewer không có quyền điều khiển Microphone.",
        )
    try:
        from mateai.infrastructure.audio.wake_word_engine import is_mic_enabled, set_mic_enabled
        if payload is None or payload.enabled is None:
            target_state = not is_mic_enabled()
        else:
            target_state = bool(payload.enabled)
        new_state = set_mic_enabled(target_state)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Lỗi điều khiển Microphone: {exc}")

    state_label = "BẬT" if new_state else "TẮT"
    logger.info(
        "Phase 16: Wake Word Mic toggle by user '%s' -> %s",
        current_user.get("username", "?"),
        state_label,
    )
    return {
        "status": "success",
        "mic_enabled": new_state,
        "is_listening": new_state,
        "hardware_state": "listening" if new_state else "released",
        "message": f"Microphone đã {state_label} theo yêu cầu.",
        "triggered_by": current_user.get("username"),
    }
