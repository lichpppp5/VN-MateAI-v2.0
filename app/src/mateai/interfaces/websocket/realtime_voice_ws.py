# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 Dương Thanh Lịch — VN-MateAI. See LICENSE and NOTICE.
"""
core/realtime_voice_ws.py
=========================
Phase 1: Realtime Voice WebSocket Foundation (Jarvis / XiaoZhi Architecture).

Chức năng:
  - Cung cấp kênh WebSocket hai chiều chuẩn hóa cho Voice Session (/ws/voice & /ws/v1/voice-stream).
  - Định nghĩa Event Protocol có cấu trúc chặt chẽ (session_started, status, text_delta, sentence_ready, tool_start, tool_result, audio_start, session_ended...).
  - Quản lý vòng đời kết nối (Handshake, Ping/Pong, Disconnect cleanup).
  - Hỗ trợ truyền âm thanh nhị phân (Binary Frame) giảm tải Base64, đồng thời giữ chế độ tương thích ngược Base64 (legacy).
  - Quản lý hủy tác vụ (Task Cancellation / Barge-in) tức thời khi người dùng ngắt lời hoặc phát lệnh mới.
  - Đo lường và gắn nhãn độ trễ chi tiết (Trace ID, TTFD, TTFT, TTFA, TTL).

LƯU Ý: Phase 1 CHỈ triển khai nền tảng WebSocket, KHÔNG can thiệp chỉnh sửa logic LLM hay TTS.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
import uuid
from typing import Any, Dict, List, Optional

from fastapi import WebSocket, WebSocketDisconnect

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Session Manager & Active Task Cancellation Registry
# ---------------------------------------------------------------------------

def tool_summary(calls: Any) -> List[Dict[str, Any]]:
    """Công cụ đã chạy trong lượt cho Portal: tên, máy, trạng thái, mã phê duyệt (nếu
    phải chờ duyệt) — KHÔNG gửi tham số / dữ liệu kết quả (có thể nhạy cảm)."""
    from mateai.application.tasks.verification import tool_outcome  # một cách đọc trạng thái tool
    out: List[Dict[str, Any]] = []
    for c in calls or []:
        res = c.get("result") if isinstance(c, dict) else None
        o = tool_outcome(res)
        item = {"skill": str(c.get("skill") or "?"), "target": str(c.get("target_client") or "master"),
                "status": o["status"]}
        if o["approval_id"]:
            item["approval_id"] = str(o["approval_id"])
        if o["status"] not in ("success", "done") and o["message"]:
            item["message"] = o["message"][:200]
        ver = (res or {}).get("verification") if isinstance(res, dict) else None
        if isinstance(ver, dict) and ver.get("status"):
            item["verification"] = str(ver["status"])
        out.append(item)
    return out


class RealtimeVoiceSession:
    """Đại diện cho một kết nối WebSocket Realtime của người dùng."""

    def __init__(self, websocket: WebSocket, user_info: dict) -> None:
        self.websocket = websocket
        self.user_info = user_info
        self.session_id: str = str(user_info.get("sub") or user_info.get("username") or f"sess_{uuid.uuid4().hex[:8]}")
        self.active_task: Optional[asyncio.Task] = None
        self.is_connected: bool = True
        self.legacy_base64: bool = False
        self._lock = asyncio.Lock()

    async def send_event(self, event_type: str, data: Optional[Dict[str, Any]] = None) -> bool:
        """Gửi gói tin sự kiện JSON (Text Frame) tới client an toàn."""
        if not self.is_connected:
            return False
        payload = {"type": event_type, "timestamp": time.time()}
        if data:
            payload.update(data)
        try:
            async with self._lock:
                await self.websocket.send_text(json.dumps(payload, ensure_ascii=False))
            return True
        except (WebSocketDisconnect, ConnectionResetError):
            self.is_connected = False
            return False
        except Exception as exc:
            logger.debug("[RealtimeVoiceWS] Lỗi gửi event %s: %s", event_type, exc)
            return False

    async def send_binary(self, binary_data: bytes) -> bool:
        """Gửi frame dữ liệu nhị phân (Binary Frame - Raw Audio) tới client."""
        if not self.is_connected or not binary_data:
            return False
        try:
            async with self._lock:
                await self.websocket.send_bytes(binary_data)
            return True
        except (WebSocketDisconnect, ConnectionResetError):
            self.is_connected = False
            return False
        except Exception as exc:
            logger.debug("[RealtimeVoiceWS] Lỗi gửi binary frame: %s", exc)
            return False

    def cancel_active_turn(self, reason: str = "barge_in") -> bool:
        """Hủy tác vụ đang chạy nếu người dùng ngắt lời hoặc gửi lệnh mới."""
        if self.active_task and not self.active_task.done():
            logger.info("[RealtimeVoiceWS] Hủy tác vụ hiện tại [%s] do: %s", self.session_id, reason)
            self.active_task.cancel()
            return True
        return False


class RealtimeVoiceRegistry:
    """Quản lý danh sách các phiên WebSocket Voice đang hoạt động."""

    def __init__(self) -> None:
        self._sessions: Dict[str, RealtimeVoiceSession] = {}
        self._lock = asyncio.Lock()

    async def register(self, session: RealtimeVoiceSession) -> None:
        async with self._lock:
            # Nếu session_id cũ còn task đang chạy, hủy trước khi mở kết nối mới
            old = self._sessions.get(session.session_id)
            if old:
                old.cancel_active_turn(reason="new_session_connected")
            self._sessions[session.session_id] = session
        logger.info("[RealtimeVoiceWS] Đã đăng ký session: %s (Tổng: %d)", session.session_id, len(self._sessions))

    async def unregister(self, session_id: str) -> None:
        async with self._lock:
            session = self._sessions.pop(session_id, None)
            if session:
                session.cancel_active_turn(reason="session_disconnected")
                session.is_connected = False
        logger.info("[RealtimeVoiceWS] Đã hủy đăng ký session: %s", session_id)

    def get(self, session_id: str) -> Optional[RealtimeVoiceSession]:
        return self._sessions.get(session_id)


# Global Singleton Registry
voice_ws_registry = RealtimeVoiceRegistry()


# ---------------------------------------------------------------------------
# WebSocket Main Loop Handler (Phase 1 Foundation)
# ---------------------------------------------------------------------------

async def handle_realtime_voice_endpoint(
    websocket: WebSocket,
    user: dict,
) -> None:
    """
    Điểm vào chính của WebSocket Realtime Voice (/ws/voice và /ws/v1/voice-stream).
    Thực hiện:
      1. Khởi tạo phiên kết nối chuẩn hóa.
      2. Lắng nghe thông điệp, điều phối sự kiện theo Protocol chuẩn.
      3. Xử lý Ping/Pong, Barge-in Cancellation, và gọi Voice Engine.
      4. Thu thập số liệu đo lường TTFD, TTFT, TTFA, TTL.
    """
    await websocket.accept()
    session = RealtimeVoiceSession(websocket=websocket, user_info=user)
    await voice_ws_registry.register(session)

    # Báo hiệu phiên đã sẵn sàng
    await session.send_event("session_started", {
        "session_id": session.session_id,
        "protocol_version": "1.0",
        "supported_features": ["binary_audio", "base64_fallback", "cancellation", "latency_metrics"],
    })
    await session.send_event("status", {"status": "idle"})

    try:
        while session.is_connected:
            try:
                # Nhận thông điệp dạng text hoặc JSON từ client
                raw_text = await websocket.receive_text()
            except WebSocketDisconnect:
                break
            except Exception as e:
                logger.debug("[RealtimeVoiceWS] Disconnect hoặc lỗi đọc socket: %s", e)
                break

            if not raw_text.strip():
                continue

            try:
                msg = json.loads(raw_text)
            except json.JSONDecodeError:
                await session.send_event("error", {"code": "INVALID_JSON", "message": "Payload JSON không hợp lệ."})
                continue

            msg_type = (msg.get("type") or "query").lower()

            # ── 1. Heartbeat Ping / Pong ──
            if msg_type == "ping":
                await session.send_event("pong", {"client_time": msg.get("time")})
                continue

            # ── 2. Barge-in / Cancel Request ──
            if msg_type in ("cancel_request", "barge_in", "stop"):
                req_id = msg.get("request_id")
                session.cancel_active_turn(reason=f"client_{msg_type}")
                await session.send_event("cancelled", {
                    "request_id": req_id,
                    "reason": "Yêu cầu đã được hủy theo lệnh người dùng.",
                })
                await session.send_event("status", {"status": "idle"})
                continue

            # ── 3. Voice Query Request ──
            query = (msg.get("query") or msg.get("text") or "").strip()
            if not query:
                await session.send_event("error", {"code": "EMPTY_QUERY", "message": "Trường 'query' không được để trống."})
                continue

            request_id = str(msg.get("request_id") or uuid.uuid4().hex[:10])
            if msg.get("format") == "base64" or msg.get("legacy_audio") is True:
                session.legacy_base64 = True

            # Hủy câu lệnh trước đó nếu người dùng đang nói tiếp câu mới (Barge-In)
            session.cancel_active_turn(reason="new_query_arrived")

            # Chạy pipeline xử lý câu lệnh trong một async Task riêng biệt
            # để vòng lặp WebSocket vẫn tiếp nhận được lệnh 'cancel_request' hoặc 'ping'
            session.active_task = asyncio.create_task(
                _execute_voice_turn(session, query, msg, request_id)
            )

    finally:
        await voice_ws_registry.unregister(session.session_id)


class _RealtimeWsSink:
    """Đầu ra của portal: sự kiện WebSocket theo giao thức /ws/v1/voice-stream."""

    def __init__(self, session: "RealtimeVoiceSession", request_id: str) -> None:
        self.session = session
        self.request_id = request_id
        self._shown = ""  # phần chữ hiển thị đã gửi qua text_delta

    async def on_status(self, status: str, **info: Any) -> None:
        if status == "done":
            return  # báo "done" sau khi tính metrics
        payload = {"status": status, "request_id": self.request_id}
        if info.get("fast_path"):
            payload["fast_path"] = True
        await self.session.send_event("status", payload)

    async def on_sentence(self, seq: int, text: str, display_text: str, **info: Any) -> None:
        # Portal hiển thị chữ GỐC (Markdown) — gửi phần mới so với lần trước.
        if display_text.startswith(self._shown):
            delta = display_text[len(self._shown):]
        else:
            delta = (" " if self._shown else "") + text
        self._shown = display_text if display_text.startswith(self._shown) else self._shown + delta
        if delta:
            event = {"request_id": self.request_id, "text": delta, "content": delta}
            if info.get("fast_path"):
                event["is_fast_path"] = True
            await self.session.send_event("text_delta", event)
        await self.session.send_event("sentence_ready", {
            "request_id": self.request_id, "sequence": seq, "text": text,
        })

    async def flush_display(self, display_text: str) -> None:
        """Chữ hiển thị cuối cùng dài hơn phần đã gửi (vd. vòng agent) -> gửi nốt."""
        if display_text and display_text.startswith(self._shown) and len(display_text) > len(self._shown):
            delta = display_text[len(self._shown):]
            self._shown = display_text
            await self.session.send_event("text_delta", {
                "request_id": self.request_id, "text": delta, "content": delta,
            })

    async def on_audio(self, seq: int, audio: bytes, text: str, kind: str, **info: Any) -> None:
        if not audio:
            return  # TTS câu này lỗi — portal đã có chữ qua text_delta
        event = {
            "sequence": seq, "request_id": self.request_id, "format": "mp3", "text": text,
        }
        if kind == "ack":
            event["is_ack"] = True
        if kind == "filler":
            event["is_filler"] = True
        if info.get("fast_path"):
            event["is_fast_path"] = True
        if info.get("tts_latency_ms") is not None:
            event["tts_latency_ms"] = info["tts_latency_ms"]
        await self.session.send_event("audio_start", event)
        from mateai.infrastructure.websocket.binary_transport import dispatch_binary_audio
        await dispatch_binary_audio(
            session=self.session,
            audio_bytes=audio,
            sequence=seq,
            is_ack=(kind == "ack"),
            request_id=self.request_id,
        )


async def _execute_voice_turn(
    session: RealtimeVoiceSession,
    query: str,
    payload: Dict[str, Any],
    request_id: str,
) -> None:
    """
    Một lượt nói của portal. Nghiệp vụ ở mateai.application.voice.voice_turn.process_voice_turn
    (dùng chung mọi kênh, kể cả đo latency); hàm này chỉ là transport.
    """
    from mateai.application.voice.voice_turn import process_voice_turn

    sink = _RealtimeWsSink(session, request_id)
    try:
        await session.send_event("status", {"status": "routing", "request_id": request_id})

        result = await process_voice_turn(
            query,
            sink=sink,
            session_id=session.session_id,
            source_device="portal",
            caller=str(session.user_info.get("sub") or session.user_info.get("username") or "anonymous"),
            history=payload.get("history"),
            request_id=request_id,
        )
        await sink.flush_display(result.display_text)

        await session.send_event("status", {"status": "done", "request_id": request_id})
        metrics = dict(result.trace)
        if result.fast_command:
            metrics["fast_path"] = {"command": result.fast_command}
        else:
            metrics["pipeline"] = result.pipeline_metrics
            metrics["used_agent"] = result.used_agent

        await session.send_event("audio_stream_complete", {
            "request_id": request_id, "metrics": metrics, "ttfa_ms": metrics.get("ttfa_ms"),
            "tools": tool_summary(result.tool_calls_made),
            "requires_confirmation": result.requires_confirmation,
        })
        await session.send_event("session_ended", {"request_id": request_id, "metrics": metrics})
        logger.info(
            "[RealtimeVoiceWS] Lượt %s xong (TTFT=%sms, TTFA=%sms, TTL=%sms, câu=%d, agent=%s)",
            request_id, metrics.get("ttft_ms"), metrics.get("ttfa_ms"), metrics.get("ttl_ms"),
            len(result.sentences), result.used_agent,
        )

    except asyncio.CancelledError:
        logger.info("[RealtimeVoiceWS] Tác vụ lượt [%s] đã bị hủy (Barge-In).", request_id)
        await session.send_event("cancelled", {"request_id": request_id})
    except Exception as exc:
        logger.error("[RealtimeVoiceWS] Lỗi xử lý lượt [%s]: %s", request_id, exc, exc_info=True)
        await session.send_event("error", {
            "code": "INTERNAL_ERROR",
            "message": f"Lỗi xử lý giọng nói: {str(exc)[:150]}",
            "request_id": request_id,
        })
    finally:
        await session.send_event("status", {"status": "idle"})

