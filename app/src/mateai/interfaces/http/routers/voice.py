# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 Dương Thanh Lịch — VN-MateAI. See LICENSE and NOTICE.
"""
mateai/interfaces/http/routers/voice.py
========================================
Lệnh thoại qua REST (/api/v1/voice-command) và phiên hội thoại HUD (xem, hỏi lại, đóng).
"""
from __future__ import annotations

import asyncio
import base64
import json
import logging
import time
import traceback
from datetime import datetime
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from pydantic import BaseModel, Field

from mateai.application.voice.speech_text import sanitise_for_tts
from mateai.config.loader import get_assistant_name, settings
from mateai.interfaces.http import hud_voice
from mateai.interfaces.http.auth_dependencies import get_current_user, require_roles
from mateai.interfaces.websocket.realtime_hub import broadcast_hud, broadcast_hud_binary, broadcast_portal_ui

logger = logging.getLogger(__name__)

router = APIRouter()


class VoiceCommandRequest(BaseModel):
    """Payload for POST /api/v1/voice-command (text-based REST)."""
    query: str = Field(..., min_length=1, max_length=2000,
                       description="Câu lệnh giọng nói đã bóc băng (STT output).",
                       examples=["Khởi động lại dịch vụ IIS"])
    session_id: Optional[str] = Field(default=None)
    source_device: Optional[str] = Field(
        default="web",
        description="Định danh nguồn gửi lệnh: 'web', 'telegram:<chat_id>', hoặc tên thiết bị.",
    )
    history: Optional[List[Dict[str, Any]]] = Field(
        default=None,
        description="Lịch sử hội thoại trước (list các message dict role/content) để duy trì ngữ cảnh đa lượt.",
    )
    include_audio: bool = Field(
        default=False,
        description="Nếu true, trả về audio TTS dạng base64 trong response JSON.",
    )


class VoiceCommandResponse(BaseModel):
    """Response for POST /api/v1/voice-command."""
    success: bool
    reply: str = Field(description="Văn bản hiển thị chi tiết (Markdown) trên giao diện.")
    speech_reply: Optional[str] = Field(default=None, description="Văn bản tóm tắt tự nhiên để phát qua giọng nói TTS (Phase 34).")
    tool_calls_made: list = Field(default_factory=list)
    requires_confirmation: bool = Field(default=False)
    error: Optional[str] = None
    session_id: Optional[str] = None
    audio_base64: Optional[str] = Field(
        default=None,
        description="Base64-encoded MP3 audio (chỉ khi include_audio=true).",
    )
    reasoning: Optional[str] = Field(
        default=None,
        description="Phase 87: quá trình suy nghĩ của model (đã gọn), tách khỏi câu trả lời.",
    )


class _CollectSink:
    """Gom audio câu trả lời của một lượt cho phản hồi REST (bỏ câu xác nhận / lời đệm)."""

    def __init__(self) -> None:
        self.audio: List[bytes] = []

    async def on_status(self, status: str, **info: Any) -> None:
        return None

    async def on_sentence(self, seq: int, text: str, display_text: str, **info: Any) -> None:
        return None

    async def on_audio(self, seq: int, audio: bytes, text: str, kind: str, **info: Any) -> None:
        if kind == "speech" and audio:
            self.audio.append(audio)


@router.post(
    "/api/v1/voice-command",
    response_model=VoiceCommandResponse,
    summary="Process a transcribed voice command (REST)",
    tags=["Voice RPA"],
)
async def voice_command(
    payload: VoiceCommandRequest,
    user: dict = Depends(require_roles(["manager", "admin"])),
) -> VoiceCommandResponse:
    """
    Receive a pre-transcribed text query and return the reply (+ base64 audio).

    Realtime P5: cùng lõi với mọi kênh thoại (`voice_turn.process_voice_turn` —
    lệnh nhanh, LLM stream, vòng agent khi cần tool, TTS theo câu). Trước đây
    REST gọi thẳng `ask_async` (không lệnh nhanh, không stream, TTS cả đoạn):
    cùng câu hỏi, khác hành vi tuỳ kênh. Base64 chỉ còn ở biên này.
    """
    from mateai.application.voice.voice_turn import process_voice_turn

    source_device = payload.source_device or "web"

    logger.info(
        "REST voice command [session=%s, device=%s]: '%s'",
        payload.session_id or "anon",
        source_device,
        payload.query[:120],
    )

    # Phase 33: Notify Standby HUD of incoming voice query
    await broadcast_hud({
        "type": "voice_active",
        "status": "listening",
        "text": payload.query,
        "timestamp": datetime.utcnow().isoformat(),
    })
    await broadcast_hud({
        "type": "voice_active",
        "status": "processing",
        "text": "Đang phân tích câu lệnh & truy xuất kỹ năng...",
        "timestamp": datetime.utcnow().isoformat(),
    })
    # Phase 87: báo HUD bắt đầu suy nghĩ. Đường REST này đi qua vòng agentic
    # (ask_async) nên không có suy nghĩ từng bước — chỉ có bản tổng sau cùng.
    await hud_voice.broadcast_thinking("thinking", query=payload.query)

    try:
        sink = _CollectSink()
        result = await process_voice_turn(
            payload.query,
            sink=sink,
            session_id=payload.session_id or source_device,
            source_device=source_device,
            # RBAC theo người đã đăng nhập, KHÔNG theo source_device do client tự
            # khai (gửi source_device="hud" từng đủ để nhận quyền admin).
            caller=str(user.get("username") or user.get("sub") or "anonymous"),
            history=payload.history,
            # Một phản hồi duy nhất: câu xác nhận / lời đệm không có chỗ để phát.
            pre_ack=False,
        )
        display_reply: str = result.display_text or result.reply_text
        # Phase 87: suy nghĩ của chính lượt này (kết quả lượt, không phải thuộc tính chung).
        await hud_voice.broadcast_thinking(
            "done" if result.reasoning else "empty",
            result.reasoning,
            payload.query,
        )
        speech_reply: str = result.reply_text or sanitise_for_tts(display_reply)
        if not speech_reply:
            speech_reply = "Em không thể thực hiện yêu cầu này."
            display_reply = display_reply or speech_reply
        tool_calls_made = result.tool_calls_made
        requires_confirmation = result.requires_confirmation
    except Exception:  # pylint: disable=broad-except
        logger.error("voice_command error:\n%s", traceback.format_exc())
        # Phase 87: lỗi -> tắt vòng xoay suy nghĩ trên HUD, không để nó quay mãi.
        await hud_voice.broadcast_thinking("empty", query=payload.query)
        raise HTTPException(status_code=500, detail="Lỗi xử lý nội bộ.")

    ai_name = get_assistant_name()
    await broadcast_hud({
        "type": "voice_active",
        "status": "processing",
        "text": f"Đang tổng hợp giọng nói {ai_name}...",
        "timestamp": datetime.utcnow().isoformat(),
    })

    # Audio các câu đã tổng hợp trong lượt (đúng thứ tự) ghép thành một MP3.
    audio_b64: Optional[str] = None
    if payload.include_audio and sink.audio:
        audio_b64 = base64.b64encode(b"".join(sink.audio)).decode("utf-8")

    # Broadcast security approval required if action needs confirmation
    if requires_confirmation:
        tool_name = tool_calls_made[0].get("skill", "") if tool_calls_made else "Tác vụ hệ thống"
        await broadcast_hud({
            "type": "security_approval_required",
            "action_id": source_device,
            "skill": tool_name,
            "query": payload.query,
            "message": speech_reply,
            "timestamp": datetime.utcnow().isoformat(),
        })

    # NOW broadcast to Standby HUD with speech_reply, display_reply and audio_base64!
    await broadcast_hud({
        "type": "voice_active",
        "status": "speaking",
        "text": speech_reply,
        "display_text": display_reply,
        "query": payload.query,
        "audio_base64": audio_b64,
        "source_device": source_device,
        "timestamp": datetime.utcnow().isoformat(),
    })

    await broadcast_portal_ui("voice_response", {
        "query": payload.query,
        "reply": display_reply,
        "speech_reply": speech_reply,
        "audio_base64": audio_b64,
        "source_device": source_device,
        "timestamp": datetime.utcnow().isoformat(),
    })

    async def _reset_hud_idle(delay: float = 6.0):
        await asyncio.sleep(delay)
        await broadcast_hud({
            "type": "voice_active",
            "status": "idle",
            "text": "",
            "timestamp": datetime.utcnow().isoformat(),
        })

    # Estimate duration: ~15 chars/second + 2s buffer
    est_duration = max(4.0, (len(speech_reply) / 15.0) + 1.8)
    asyncio.create_task(_reset_hud_idle(est_duration))

    return VoiceCommandResponse(
        success=True,
        reply=display_reply,
        speech_reply=speech_reply,
        tool_calls_made=tool_calls_made,
        requires_confirmation=requires_confirmation,
        session_id=payload.session_id,
        audio_base64=audio_b64,
        # Phase 87: suy nghĩ của lượt này, để client đọc được của đúng lượt
        # thay vì đọc thuộc tính chung (lượt song song ghi đè lẫn nhau).
        reasoning=result.reasoning or None,
    )


@router.get(
    "/api/v1/voice/session/{session_id}",
    summary="Phase 65: Trạng thái phiên hội thoại HUD",
    tags=["Voice Phase 65"],
)
async def api_voice_session(
    session_id: str,
    current_user: Dict[str, Any] = Depends(require_roles(["viewer", "manager", "admin"])),
) -> Dict[str, Any]:
    """HUD gọi để đồng bộ trạng thái sau khi trang bị mất kết nối rồi mở lại."""
    from mateai.application.voice.voice_session import voice_sessions

    return {"status": "success", "session": voice_sessions.get(session_id).to_client()}


@router.post(
    "/api/v1/voice/session/{session_id}/reask",
    summary="Phase 65: Nhắc Ly Ly hỏi lại khi admin im lặng",
    tags=["Voice Phase 65"],
)
async def api_voice_reask(
    session_id: str,
    current_user: Dict[str, Any] = Depends(require_roles(["viewer", "manager", "admin"])),
) -> Dict[str, Any]:
    """
    Đếm một lần hỏi lại và trả lại câu hỏi đang chờ để HUD phát lại.

    Số lần hỏi lại do HUD quyết định giới hạn (mặc định 2) — máy chủ chỉ đếm và
    báo lại. Đặt ngưỡng ở đây thì muốn đổi cấu hình phải sửa cả hai đầu.
    """
    from mateai.application.voice.voice_session import voice_sessions

    session = voice_sessions.get(session_id)
    if not session.expecting_reply:
        return {"status": "success", "reask": False, "reason": "Không có câu hỏi đang chờ"}

    count = session.bump_reask()
    return {
        "status": "success",
        "reask": True,
        "reask_count": count,
        "question": session.pending_question,
        "waiting_seconds": round(session.waiting_seconds(), 1),
    }


@router.post(
    "/api/v1/voice/session/{session_id}/close",
    summary="Phase 65: Đóng phiên hội thoại (hết lượt hỏi lại)",
    tags=["Voice Phase 65"],
)
async def api_voice_session_close(
    session_id: str,
    current_user: Dict[str, Any] = Depends(require_roles(["viewer", "manager", "admin"])),
) -> Dict[str, Any]:
    """
    Đóng lắng nghe: bỏ cờ chờ, giữ lịch sử để lượt sau còn nhớ.

    Xoá hẳn phiên thì Ly Ly quên mất hết và lại hỏi lại từ đầu — đúng cái
    lỗi đang sửa. Nên chỉ tắt trạng thái chờ, không xoá lịch sử.
    """
    from mateai.application.voice.voice_session import voice_sessions

    session = voice_sessions.get(session_id)
    session.clear_expecting_reply()
    return {"status": "success", "closed": True, "session": session.to_client()}


@router.post(
    "/api/v1/voice/transcribe",
    summary="Nhận dạng giọng nói từ micro trình duyệt bằng bộ nhận dạng của MÁY CHỦ",
    tags=["Voice"],
)
async def api_voice_transcribe(
    request: Request,
    current_user: Dict[str, Any] = Depends(require_roles(["manager", "admin"])),
) -> Dict[str, Any]:
    """Thân yêu cầu: file WAV (PCM 16 kHz mono). Dùng cùng bộ nhận dạng với robot / HUD
    (Whisper cục bộ / Groq / Google theo cấu hình). Trước đây nút micro trên Portal
    dùng Web Speech API của Chrome: âm thanh gửi thẳng lên Google, chỉ chạy trên
    Chrome/Edge, không theo cấu hình nhận dạng của hệ thống."""
    body = await request.body()
    if not body:
        raise HTTPException(status_code=400, detail="Không có âm thanh.")
    if len(body) > 4 * 1024 * 1024:
        raise HTTPException(status_code=413, detail="Đoạn ghi âm quá dài (tối đa ~2 phút).")
    from mateai.infrastructure.audio.audio_processor import audio_engine
    t0 = time.perf_counter()
    text = await audio_engine.transcribe_audio(body)
    return {"status": "success", "text": text or "", "ms": int((time.perf_counter() - t0) * 1000),
            "backend": str(getattr(settings, "ASR_BACKEND", "") or "")}


@router.get(
    "/api/v1/voice/model-stats",
    summary="Hiệu năng theo não + model (p50/p95, lỗi, chuyển dự phòng) — lượt gần nhất",
    tags=["Voice"],
)
async def api_voice_model_stats(
    current_user: Dict[str, Any] = Depends(require_roles(["manager", "admin"])),
) -> Dict[str, Any]:
    """Chỉ số tổng hợp (không có nội dung câu nói / id phiên) — quản lý xem được."""
    from mateai.application.voice.voice_turn import model_stats
    return {"status": "success", **model_stats()}


@router.get(
    "/api/v1/voice/metrics",
    summary="Độ trễ các lượt thoại gần nhất (p50/p95/p99 theo kiểu lượt) — mọi kênh",
    tags=["Voice"],
)
async def api_voice_metrics(
    channel: Optional[str] = Query(default=None, description="portal | hud | <id robot> | server_mic"),
    recent: int = Query(default=20, ge=0, le=500),
    current_user: Dict[str, Any] = Depends(require_roles(["admin"])),
) -> Dict[str, Any]:
    """Số đo từ `VoiceTurnTrace` (bộ đệm vòng 500 lượt trong RAM, mất khi khởi động lại).
    Chỉ admin: trace có session_id / id thiết bị."""
    from mateai.application.voice.voice_turn import recent_traces, trace_stats
    return {
        "status": "success",
        "stats": trace_stats(channel),
        "recent": recent_traces(recent, channel) if recent else [],
    }
