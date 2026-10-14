# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 Dương Thanh Lịch — VN-MateAI. See LICENSE and NOTICE.
"""
tests/test_phase1_realtime_ws.py
================================
Kiểm thử Nền tảng Realtime Voice WebSocket Foundation (Phase 1):
1. Định dạng Event Protocol chuẩn hóa (session_started, status, text_delta, audio_start, session_ended).
2. Trace ID và bộ đo độ trễ chuẩn (TTFD, TTFT, TTFA, TTL).
3. Quản lý vòng đời kết nối và Session Registry.
4. Cơ chế ngắt tác vụ tức thì (Barge-In Task Cancellation).
5. Đăng ký thành công endpoint /ws/voice và /ws/v1/voice-stream trên FastAPI app.
"""

import asyncio
import json
import sys
import time
from pathlib import Path

# Add project root to sys.path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from mateai.application.voice.voice_turn import VoiceTurnTrace, recent_traces, trace_stats
from mateai.interfaces.websocket.realtime_voice_ws import (
    RealtimeVoiceSession,
    RealtimeVoiceRegistry,
    voice_ws_registry,
)


def test_metric_trace_tracker():
    """Trace dùng chung mọi kênh (application/voice/voice_turn.VoiceTurnTrace) — thay
    VoiceRequestTrace chỉ có ở portal."""
    print("\n▸ 1. Kiểm thử Bộ Đo Độ Trễ & Tracing (VoiceTurnTrace)")
    trace = VoiceTurnTrace(session_id="user_admin", channel="portal", request_id="req_test_01")
    assert trace.request_id == "req_test_01"
    assert trace.trace_id.startswith("trace_")

    time.sleep(0.02)
    trace.mark_status("thinking")
    time.sleep(0.02)
    trace.mark("first_text")
    time.sleep(0.02)
    trace.mark("first_audio")
    trace.mark("first_answer_audio")
    trace.mark("first_text")  # lần sau không ghi đè lần đầu
    trace.mark_status("speaking")
    trace.mark_status("done")

    summary = trace.finish("llm")
    assert summary["ttfd_ms"] >= 20
    assert summary["ttft_ms"] >= summary["ttfd_ms"] + 15
    assert summary["ttfa_answer_ms"] >= summary["ttft_ms"]
    assert summary["ttl_ms"] >= summary["ttfa_answer_ms"]
    assert len(summary["status_steps"]) == 3
    assert recent_traces(1)[0]["request_id"] == "req_test_01"
    assert trace_stats("portal")["by_outcome"]["llm"]["metrics"]["ttft_ms"]["n"] >= 1
    print(f"  ✅ Trace Summary: TTFD={summary['ttfd_ms']}ms, TTFT={summary['ttft_ms']}ms, TTFA={summary['ttfa_answer_ms']}ms, TTL={summary['ttl_ms']}ms")


async def test_session_lifecycle_and_cancellation():
    print("\n▸ 2. Kiểm thử Vòng Đời Session & Cơ chế Barge-In Cancellation")

    sent_events = []
    sent_binaries = []

    class MockWebSocket:
        async def send_text(self, text: str):
            sent_events.append(json.loads(text))

        async def send_bytes(self, data: bytes):
            sent_binaries.append(data)

    mock_ws = MockWebSocket()
    registry = RealtimeVoiceRegistry()
    session = RealtimeVoiceSession(websocket=mock_ws, user_info={"sub": "test_user_42"})

    # 1. Đăng ký session
    await registry.register(session)
    assert registry.get("test_user_42") is session
    print("  ✅ Đăng ký phiên kết nối thành công.")

    # 2. Gửi event kiểm tra protocol
    await session.send_event("session_started", {"session_id": "test_user_42"})
    assert len(sent_events) == 1
    assert sent_events[0]["type"] == "session_started"
    assert "timestamp" in sent_events[0]
    print("  ✅ Gửi sự kiện 'session_started' đúng chuẩn Protocol.")

    # 3. Gửi Binary Frame
    dummy_audio = b"\xff\xfb\x90\x44" * 10
    await session.send_binary(dummy_audio)
    assert len(sent_binaries) == 1
    assert sent_binaries[0] == dummy_audio
    print("  ✅ Gửi frame nhị phân (Binary Frame) thành công.")

    # 4. Kiểm thử Hủy Tác Vụ (Barge-In)
    async def _long_running_task():
        await asyncio.sleep(10.0)

    task = asyncio.create_task(_long_running_task())
    session.active_task = task

    # Người dùng ngắt lời:
    cancelled = session.cancel_active_turn(reason="barge_in_test")
    assert cancelled is True
    try:
        await task
    except asyncio.CancelledError:
        pass
    assert task.cancelled()
    print("  ✅ Tác vụ đang chạy bị hủy tức thì khi người dùng ngắt lời (Barge-In).")

    # 5. Hủy đăng ký session
    await registry.unregister("test_user_42")
    assert registry.get("test_user_42") is None
    print("  ✅ Hủy đăng ký phiên và dọn dẹp tài nguyên thành công.")


def test_server_routes_registration():
    print("\n▸ 3. Kiểm thử Đăng ký Endpoint WebSocket trên FastAPI Server")
    from mateai.interfaces.http.server import app

    routes = [route.path for route in app.routes]
    assert "/ws/v1/voice-stream" in routes, "Thiếu endpoint /ws/v1/voice-stream trên FastAPI"
    # Bí danh /ws/voice đã gỡ (realtime P6) — không client nào dùng.
    assert "/ws/voice" not in routes
    print("  ✅ Endpoint '/ws/v1/voice-stream' đã được đăng ký.")


def main():
    print("=" * 60)
    print("PHASE 1: REALTIME WEBSOCKET FOUNDATION VERIFICATION")
    print("=" * 60)

    test_metric_trace_tracker()
    asyncio.run(test_session_lifecycle_and_cancellation())
    test_server_routes_registration()

    print("\n" + "─" * 60)
    print("✅ TẤT CẢ TEST PHASE 1 (REALTIME WEBSOCKET FOUNDATION) ĐÃ PASS HOÀN TOÀN!")


if __name__ == "__main__":
    main()
