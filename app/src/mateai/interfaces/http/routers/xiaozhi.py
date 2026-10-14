# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 Dương Thanh Lịch — VN-MateAI. See LICENSE and NOTICE.
"""
mateai/interfaces/http/routers/xiaozhi.py
==========================================
Điều khiển robot Xiaozhi/ESP32 qua REST: giao diện, đánh thức, ngắt lời, phát thông báo, trạng thái node.
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

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel, Field

from core.plugin_manager import run_blocking
from mateai.config.loader import settings
from mateai.interfaces.http import speech
from mateai.interfaces.http.auth_dependencies import get_current_user, require_roles

logger = logging.getLogger(__name__)

router = APIRouter()


class XiaozhiUiRequest(BaseModel):
    """Payload for POST /api/v1/xiaozhi/ui."""
    device_id: Optional[str] = Field(default=None, description="Mã thiết bị (để trống để broadcast toàn bộ)")
    state: str = Field(default="listening", description="listening | processing | alert | idle | speaking")
    emotion: Optional[str] = Field(
        default=None,
        description="neutral | happy | sad | cry | wow | excited | angry | love | sleepy (và focused / thinking / alert)",
    )
    text: Optional[str] = Field(default=None, description="Văn bản hiển thị trên màn hình LCD/OLED")


class XiaozhiWakeRequest(BaseModel):
    """Payload for POST /api/v1/xiaozhi/wake."""
    title: str = Field(default="Cảnh Báo Hệ Thống", description="Tiêu đề hiển thị LCD")
    message: str = Field(default="Hệ thống máy chủ vừa phát hiện lỗi cần chú ý.", description="Nội dung giọng nói cảnh báo")
    device_id: Optional[str] = Field(default=None, description="Mã thiết bị Xiaozhi cụ thể nếu có")


class XiaozhiInterruptRequest(BaseModel):
    """Payload for POST /api/v1/xiaozhi/interrupt."""
    device_id: str = Field(..., description="Mã thiết bị Xiaozhi cần ngắt lời")


@router.post(
    "/api/v1/xiaozhi/ui",
    summary="Phase 43: Điều khiển giao diện màn hình LCD/OLED & Biểu cảm Xiaozhi",
    tags=["Xiaozhi Desktop Companion"],
)
async def xiaozhi_ui_endpoint(
    payload: XiaozhiUiRequest,
    user: dict = Depends(require_roles(["viewer", "manager", "admin"])),
) -> Dict[str, Any]:
    """Gửi frame JSON điều khiển màn hình LCD/OLED (ST7789/GC9A01) của mạch Xiaozhi."""
    from mateai.interfaces.websocket.xiaozhi_gateway import xiaozhi_gateway
    if payload.device_id:
        success = await xiaozhi_gateway.send_ui_payload(
            device_id=payload.device_id,
            state=payload.state,
            emotion=payload.emotion,
            text=payload.text,
        )
        return {
            "status": "success" if success else "failed",
            "device_id": payload.device_id,
            "state": payload.state,
            "emotion": payload.emotion,
            "text": payload.text,
        }
    else:
        count = await xiaozhi_gateway.broadcast_ui_payload(
            state=payload.state,
            emotion=payload.emotion,
            text=payload.text,
        )
        return {
            "status": "success",
            "broadcast_count": count,
            "state": payload.state,
            "emotion": payload.emotion,
            "text": payload.text,
        }


@router.post(
    "/api/v1/xiaozhi/wake",
    summary="Phase 43: Đánh thức Robot Xiaozhi và phát âm thanh cảnh báo",
    tags=["Xiaozhi Desktop Companion"],
)
async def xiaozhi_wake_endpoint(
    payload: XiaozhiWakeRequest,
    user: dict = Depends(require_roles(["viewer", "manager", "admin"])),
) -> Dict[str, Any]:
    """Chủ động đánh thức Desktop Robot, chớp mắt đỏ trên LCD và phát âm thanh cảnh báo."""
    from mateai.interfaces.websocket.xiaozhi_gateway import xiaozhi_gateway
    success = await xiaozhi_gateway.wake_and_alert(
        error_title=payload.title,
        detail_message=payload.message,
        device_id=payload.device_id,
    )
    return {
        "status": "success" if success else "no_active_nodes",
        "title": payload.title,
        "message": payload.message,
        "device_id": payload.device_id,
    }


@router.post(
    "/api/v1/xiaozhi/interrupt",
    summary="Phase 43: Kích hoạt ngắt lời Barge-in trên Xiaozhi",
    tags=["Xiaozhi Desktop Companion"],
)
async def xiaozhi_interrupt_endpoint(
    payload: XiaozhiInterruptRequest,
    user: dict = Depends(require_roles(["viewer", "manager", "admin"])),
) -> Dict[str, Any]:
    """Kích hoạt cơ chế ngắt lời ngay lập tức: huỷ LLM task, xóa buffer audio, phát câu đệm 0ms."""
    from mateai.interfaces.websocket.xiaozhi_gateway import xiaozhi_gateway
    await xiaozhi_gateway.handle_barge_in(payload.device_id)
    return {
        "status": "success",
        "action": "barge_in_triggered",
        "device_id": payload.device_id,
        "reflex": "Dạ, anh nói đi em nghe đây.",
    }


class XiaozhiAnnounceRequest(BaseModel):
    text: str = Field(..., min_length=1, max_length=500, description="Nội dung TTS sẽ phát ra loa robot")
    device_id: Optional[str] = Field(None, description="ID robot cụ thể. Bỏ trống = phát tới TẤT CẢ robot đang online")


@router.post(
    "/api/v1/xiaozhi/announce",
    summary="Phát thông báo TTS trực tiếp ra loa Robot (tất cả hoặc robot cụ thể)",
    tags=["Xiaozhi Desktop Companion"],
)
async def xiaozhi_announce_endpoint(
    payload: XiaozhiAnnounceRequest,
    user: dict = Depends(require_roles(["viewer", "manager", "admin"])),
) -> Dict[str, Any]:
    """Stream TTS audio trực tiếp tới loa của robot qua WebSocket — không phát trong browser."""
    from mateai.interfaces.websocket.xiaozhi_gateway import xiaozhi_gateway

    clean_text = payload.text.strip()
    if not clean_text:
        raise HTTPException(status_code=400, detail="Nội dung thông báo không được để trống.")

    # Xác định target nodes
    if payload.device_id:
        node = xiaozhi_gateway.get_node(payload.device_id)
        if not node:
            raise HTTPException(status_code=404, detail=f"Robot [{payload.device_id}] không online.")
        targets = [node]
    else:
        targets = list(xiaozhi_gateway.get_all_nodes().values())

    if not targets:
        raise HTTPException(status_code=503, detail="Không có robot nào đang kết nối.")

    # Sinh audio TTS một lần, chuyển đổi sang PCM 16kHz thuần cho loa MAX98357A
    try:
        raw_mp3 = await speech.tts_bytes(clean_text)
        if not raw_mp3:
            raise HTTPException(status_code=500, detail="Không thể sinh audio TTS.")
        from mateai.interfaces.websocket.xiaozhi_gateway import convert_to_pcm16_16k
        pcm_bytes = convert_to_pcm16_16k(raw_mp3)
    except Exception as tts_err:
        raise HTTPException(status_code=500, detail=f"Lỗi TTS: {tts_err}")

    sent_count = 0
    chunk_size = 2048
    for node in targets:
        try:
            await node.websocket.send_text(json.dumps({
                "type": "tts_start", "format": "audio/pcm",
                "sample_rate": 16000, "channels": 1, "text": clean_text, "source": "portal_announce",
            }))
            await xiaozhi_gateway.send_ui_payload(
                node.device_id, state="speaking", emotion="happy", text=clean_text[:40],
            )
            for offset in range(0, len(pcm_bytes), chunk_size):
                if node.cancel_event.is_set():
                    break
                await node.websocket.send_bytes(pcm_bytes[offset : offset + chunk_size])
                await asyncio.sleep(0.045)

            await node.websocket.send_text(json.dumps({"type": "tts_end"}))
            sent_count += 1
            logger.info("[Announce] Đã phát '%s' tới [%s] (%d bytes PCM)", clean_text[:30], node.device_id, len(pcm_bytes))
        except Exception as send_err:
            logger.warning("[Announce] Không thể gửi tới robot [%s]: %s", node.device_id, send_err)

    return {
        "status": "success",
        "text": clean_text,
        "sent_to": sent_count,
        "total_robots": len(targets),
        "message": f"Đã phát thông báo tới {sent_count}/{len(targets)} robot.",
    }


@router.get(
    "/api/v1/xiaozhi/nodes",
    summary="Phase 43: Danh sách & Telemetry màn hình LCD của mạch Xiaozhi",
    tags=["Xiaozhi Desktop Companion"],
)
async def get_xiaozhi_nodes_telemetry(user: dict = Depends(require_roles(["viewer", "manager", "admin"]))) -> Dict[str, Any]:
    """Trả về danh sách các mạch Xiaozhi Desktop Companion đang online kèm trạng thái LCD và biểu cảm."""
    from mateai.interfaces.websocket.xiaozhi_gateway import xiaozhi_gateway
    nodes = xiaozhi_gateway.get_nodes_telemetry()
    return {
        "status": "success",
        "total_nodes": len(nodes),
        "nodes": nodes,
    }
