# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 Dương Thanh Lịch — VN-MateAI. See LICENSE and NOTICE.
"""
mateai/interfaces/http/routers/tts.py
======================================
TTS một lần (MP3) và danh sách giọng Edge-TTS.
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
from mateai.application.voice.speech_text import sanitise_for_tts, shorten_for_speech
from mateai.interfaces.http.auth_dependencies import get_current_user, require_roles
from mateai.interfaces.websocket.realtime_hub import broadcast_hud, broadcast_portal_ui

logger = logging.getLogger(__name__)

router = APIRouter()


class TTSRequest(BaseModel):
    """Payload for POST /api/v1/tts."""
    text: str = Field(..., min_length=1, max_length=2000)
    voice: Optional[str] = Field(default="vi-VN-HoaiMyNeural")
    rate: Optional[str] = Field(default=None)


@router.post(
    "/api/v1/tts",
    summary="Synthesise Vietnamese TTS audio (REST, full buffer)",
    tags=["Audio"],
    response_class=Response,
)
async def tts_endpoint(
    payload: TTSRequest,
    user: dict = Depends(require_roles(["viewer", "manager", "admin"])),
) -> Response:
    """
    Convert text to speech using edge-tts.
    Returns raw MP3 bytes (Content-Type: audio/mpeg).
    Assembles entire audio in-memory (io.BytesIO) — no disk I/O.
    """

    try:
        from mateai.infrastructure.tts.tts_stream_engine import get_tts_engine
        audio_bytes = await get_tts_engine().synthesise(
            shorten_for_speech(sanitise_for_tts(payload.text)), voice=payload.voice, rate=payload.rate
        )
        if not audio_bytes:
            raise HTTPException(status_code=500, detail="TTS engine returned empty audio.")
        return Response(content=audio_bytes, media_type="audio/mpeg")
    except Exception as exc:  # pylint: disable=broad-except
        logger.error("TTS endpoint error: %s", exc)
        raise HTTPException(status_code=500, detail=f"TTS error: {exc}")


_TTS_VOICES_CACHE: list = []


@router.get(
    "/api/v1/tts/voices",
    summary="Lấy danh sách toàn bộ giọng đọc Edge-TTS (322 giọng, 70+ ngôn ngữ)",
    tags=["Audio"],
)
async def tts_voices_endpoint(user: dict = Depends(require_roles(["viewer", "manager", "admin"]))) -> Dict[str, Any]:
    """
    Trả về danh sách đầy đủ các giọng đọc Edge-TTS từ Microsoft.
    Kết quả được cache trong RAM — chỉ gọi edge_tts.list_voices() một lần duy nhất.
    """
    global _TTS_VOICES_CACHE
    if not _TTS_VOICES_CACHE:
        try:
            import edge_tts as _edge_tts
            raw = await _edge_tts.list_voices()
            _TTS_VOICES_CACHE = [
                {
                    "short_name": v["ShortName"],
                    "friendly_name": v["FriendlyName"],
                    "locale": v["Locale"],
                    "gender": v.get("Gender", ""),
                }
                for v in raw
            ]
            logger.info("Đã tải %d giọng Edge-TTS vào cache.", len(_TTS_VOICES_CACHE))
        except Exception as exc:  # pylint: disable=broad-except
            logger.error("Lỗi tải danh sách giọng Edge-TTS: %s", exc)
            return {"success": False, "error": str(exc), "voices": []}

    return {"success": True, "total": len(_TTS_VOICES_CACHE), "voices": _TTS_VOICES_CACHE}


# ── Vbee AIVoice (studio.vbee.vn) ────────────────────────────────────────────
class VbeeCredentials(BaseModel):
    """App ID + Token Vbee. Bỏ trống / còn ký hiệu che → dùng thông tin ĐÃ LƯU (token không bao giờ gửi ngược về trình duyệt)."""
    app_id: Optional[str] = Field(default=None, max_length=200)
    token: Optional[str] = Field(default=None, max_length=4000)
    voice: Optional[str] = Field(default=None, max_length=200)
    text: Optional[str] = Field(default=None, max_length=300)


def _vbee_creds(body: VbeeCredentials) -> tuple:
    from mateai.infrastructure.tts.vbee_tts import audio_cfg
    saved = audio_cfg()
    app_id = (body.app_id or "").strip() or str(saved.get("vbee_app_id") or "").strip()
    token = (body.token or "").strip()
    if not token or set(token) == {"•"}:
        token = str(saved.get("vbee_token") or "").strip()
    if not app_id or not token:
        raise HTTPException(status_code=400, detail="Cần App ID và Token Vbee (tạo ở studio.vbee.vn/apps → Tạo ứng dụng).")
    return app_id, token


@router.post("/api/v1/tts/vbee/voices", summary="Danh sách giọng Vbee AIVoice của tài khoản", tags=["Audio"])
async def vbee_voices_endpoint(body: VbeeCredentials = Body(default_factory=VbeeCredentials),
                               user: dict = Depends(require_roles(["admin"]))) -> Dict[str, Any]:
    """Giọng Vbee (tiếng Việt) + giọng cá nhân; giọng hỗ trợ Realtime (dùng được cho hội thoại) xếp trước."""
    from mateai.infrastructure.tts.vbee_tts import list_vbee_voices
    app_id, token = _vbee_creds(body)
    try:
        return await list_vbee_voices(app_id, token)
    except Exception as exc:  # noqa: BLE001
        logger.warning("[TTS/Vbee] lỗi tải danh sách giọng: %s", exc)
        return {"ok": False, "error": f"Không tải được danh sách giọng Vbee: {exc}", "voices": []}


@router.post("/api/v1/tts/vbee/test", summary="Đọc thử bằng Vbee (kiểm tra App ID / Token / giọng trước khi lưu)",
             tags=["Audio"], response_class=Response)
async def vbee_test_endpoint(body: VbeeCredentials, user: dict = Depends(require_roles(["admin"]))) -> Response:
    """Trả MP3 nếu đọc được; lỗi (token hết hạn, hết điểm, giọng không hỗ trợ realtime…) → 400 kèm lý do dễ hiểu."""
    from mateai.infrastructure.tts.vbee_tts import DEFAULT_VBEE_VOICE, audio_cfg, is_vbee_voice, speed_from_rate, vbee_try
    app_id, token = _vbee_creds(body)
    voice = body.voice if is_vbee_voice(body.voice) else DEFAULT_VBEE_VOICE
    text = (body.text or "").strip() or f"Xin chào, em là {get_assistant_name()}. Đây là giọng đọc Vbee."
    res = await vbee_try(text, voice, speed_from_rate(audio_cfg().get("speech_rate", 0)), app_id, token)
    if not res["ok"]:
        raise HTTPException(status_code=400, detail=res["error"])
    return Response(content=res["audio"], media_type="audio/mpeg",
                    headers={"X-Vbee-Ms": str(res["ms"]), "X-Vbee-Mode": res.get("mode", "")})
