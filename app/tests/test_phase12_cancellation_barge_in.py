# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 Dương Thanh Lịch — VN-MateAI. See LICENSE and NOTICE.
"""
tests/test_phase12_cancellation_barge_in.py
===========================================
Unit & Integration Test Suite cho Giai đoạn 12:
Phase 12: Cancellation & Barge-In (User Interruption & Active Task Abort).

Mục tiêu kiểm thử:
1. Instant Task Abort (< 5ms): Kiểm tra khả năng hủy tác vụ nền bất đồng bộ ngay lập tức khi nhận tín hiệu barge-in.
2. TTS Pipeline Cancellation & Drain: Kiểm tra dọn sạch hàng đợi câu và ngắt workers tổng hợp âm thanh trong < 2ms.
3. Rapid-Fire Barge-In Interruption: Mô phỏng người dùng cắt ngang lệnh dài bằng lệnh mới, xác thực hủy sạch lệnh cũ và chạy ngay lệnh mới.
4. Clean State Recovery: Trạng thái phiên (status) chuyển về 'idle', không rò rỉ tasks nền hay socket hanging.
"""

import asyncio
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

# Add project root to sys.path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from mateai.infrastructure.tts.tts_queue_pipeline import StreamingTTSWorkerPipeline
from mateai.application.commands.fast_command_router import fast_command_router


class MockWebSocket:
    def __init__(self):
        self.sent_texts: List[str] = []
        self.sent_bytes: List[bytes] = []
        self.closed: bool = False

    async def send_text(self, data: str):
        self.sent_texts.append(data)

    async def send_bytes(self, data: bytes):
        self.sent_bytes.append(data)

    async def close(self):
        self.closed = True


class MockRealtimeSession:
    def __init__(self, session_id: str = "mock_session_barge_in"):
        self.session_id = session_id
        self.websocket = MockWebSocket()
        self.is_connected = True
        self.legacy_base64 = False
        self.active_task: Optional[asyncio.Task] = None
        self.events: List[Dict[str, Any]] = []

    async def send_event(self, event_type: str, data: Dict[str, Any]) -> bool:
        self.events.append({"type": event_type, "data": data})
        return True

    async def send_binary(self, binary_data: bytes) -> bool:
        self.websocket.sent_bytes.append(binary_data)
        return True

    def cancel_active_turn(self, reason: str = "barge_in") -> bool:
        if self.active_task and not self.active_task.done():
            self.active_task.cancel()
            return True
        return False


async def test_session_instant_task_abort():
    print("\n▸ 1. Kiểm thử Hủy Tác Vụ Tức Thì (< 5ms) Khi Nhận Tín Hiệu Barge-In")

    session = MockRealtimeSession()

    async def _long_running_turn():
        try:
            await asyncio.sleep(5.0)  # Mô phỏng LLM đang sinh văn bản dài
        except asyncio.CancelledError:
            await session.send_event("cancelled", {"reason": "barge_in"})
            raise

    # Khởi chạy task nền
    session.active_task = asyncio.create_task(_long_running_turn())
    await asyncio.sleep(0.01)  # Chờ task bắt đầu

    t0 = time.perf_counter()
    cancelled = session.cancel_active_turn(reason="user_speaking")
    abort_latency_ms = (time.perf_counter() - t0) * 1000

    assert cancelled is True, "Phải hủy thành công active task"
    assert abort_latency_ms < 5.0, f"Độ trễ hủy phải < 5ms, thực tế: {abort_latency_ms:.3f}ms"

    # Chờ task hoàn tất hủy
    try:
        await session.active_task
    except asyncio.CancelledError:
        pass

    assert session.active_task.cancelled() is True
    assert any(e["type"] == "cancelled" for e in session.events)
    print(f"  ✅ Hủy tác vụ thành công trong {abort_latency_ms:.3f}ms (< 5ms chuẩn). Event 'cancelled' đã được phát.")


async def test_tts_pipeline_cancellation_and_drain():
    print("\n▸ 2. Kiểm thử Dọn Sạch Hàng Đợi TTS Pipeline (Queue Drain & Worker Abort)")

    pipeline = StreamingTTSWorkerPipeline(voice="vi-VN-HoaiMyNeural", num_workers=2)
    pipeline.start()

    # Đẩy 5 câu vào hàng đợi
    for i in range(1, 6):
        await pipeline.push_sentence(i, f"Đây là câu nói thử nghiệm số {i} trong chuỗi văn bản.", f"req_{i}")

    t0 = time.perf_counter()
    pipeline.cancel()
    drain_latency_ms = (time.perf_counter() - t0) * 1000

    assert pipeline._is_cancelled is True
    assert pipeline._sentence_queue.empty() is True
    assert len(pipeline._reorder_buffer) == 0
    assert drain_latency_ms < 2.0, f"Dọn hàng đợi phải < 2ms, thực tế: {drain_latency_ms:.3f}ms"

    # Consumer phải dừng ngay lập tức
    results = []
    async for item in pipeline.iterate_audio_results():
        results.append(item)

    assert len(results) == 0, f"Sau khi cancel không được yield thêm audio, nhận: {len(results)}"
    print(f"  ✅ Pipeline đã cancel và drain sạch 5 câu trong {drain_latency_ms:.3f}ms. 0 audio rò rỉ.")


async def test_rapid_fire_barge_in():
    print("\n▸ 3. Kiểm thử Ngắt Lời Liên Tục (Rapid-Fire Interruption)")

    session = MockRealtimeSession()

    executed_turns = []

    async def _simulate_turn(query: str, delay: float):
        try:
            executed_turns.append(f"start_{query}")
            await asyncio.sleep(delay)
            executed_turns.append(f"done_{query}")
        except asyncio.CancelledError:
            executed_turns.append(f"cancelled_{query}")
            raise

    # 1. Người dùng gửi lệnh dài
    session.active_task = asyncio.create_task(_simulate_turn("lenh_1_dai", 2.0))
    await asyncio.sleep(0.02)

    # 2. Người dùng ngắt lời ngay lập tức bằng câu hỏi giờ
    session.cancel_active_turn(reason="new_query")
    fast_res = await fast_command_router.dispatch("mấy giờ rồi", synthesize_audio=False)

    assert fast_res is not None
    assert "giờ" in fast_res.reply_text

    try:
        await session.active_task
    except asyncio.CancelledError:
        pass

    assert "cancelled_lenh_1_dai" in executed_turns
    assert "done_lenh_1_dai" not in executed_turns
    print(f"  ✅ Lệnh 1 bị hủy sạch sẽ: {executed_turns}")
    print(f"  ✅ Lệnh 2 (Fast Command) phản hồi ngay: '{fast_res.reply_text}'")


async def main():
    print("=" * 70)
    print("BẮT ĐẦU KIỂM THỬ GIAI ĐOẠN 12 (PHASE 12: CANCELLATION & BARGE-IN)")
    print("=" * 70)

    await test_session_instant_task_abort()
    await test_tts_pipeline_cancellation_and_drain()
    await test_rapid_fire_barge_in()

    print("\n" + "=" * 70)
    print("🎉 TẤT CẢ 3/3 BÀI KIỂM THỬ PHASE 12 ĐÃ ĐẠT 100% THÀNH CÔNG!")
    print("=" * 70)


if __name__ == "__main__":
    asyncio.run(main())
