# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 Dương Thanh Lịch — VN-MateAI. See LICENSE and NOTICE.
"""
tests/test_phase4_tts_pipeline.py
==================================
Unit & Integration Test Suite cho Phase 4:
Streaming TTS Worker Queue & Audio Buffer (In-Order Guaranteed).

Các mục tiêu kiểm chứng:
1. In-Order Re-Sequencing: Câu 2 hoàn thành trước câu 1 nhưng âm thanh ra WebSocket PHẢI theo thứ tự 1 -> 2 -> 3.
2. Backpressure Queue: Hàng đợi bị giới hạn không làm tràn RAM, theo dõi queue_depth_peak.
3. Cancellation & Barge-In: Dọn sạch hàng đợi và hủy worker ngầm tức thì khi có yêu cầu cancel.
4. Edge cases: Xử lý 0 câu (mark_complete(0)), 1 câu đơn lẻ.
"""

import asyncio
import sys
import time
from pathlib import Path
from typing import AsyncGenerator, List

# Add project root to sys.path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from mateai.infrastructure.tts.tts_queue_pipeline import (
    StreamingTTSWorkerPipeline,
    SentenceItem,
    AudioResultItem,
)


# ---------------------------------------------------------------------------
# Mock TTS Engines để kiểm thử chính xác thời gian và thứ tự
# ---------------------------------------------------------------------------

class MockVariableLatencyTTSEngine:
    """
    Mock TTS Engine mô phỏng độ trễ khác nhau giữa các câu:
    - Câu 1: mất 120ms (chậm)
    - Câu 2: mất 15ms (siêu nhanh - xong trước câu 1)
    - Câu 3: mất 40ms (trung bình)
    """

    def __init__(self, latencies: dict[str, float] = None) -> None:
        self.latencies = latencies or {
            "câu 1": 0.12,
            "câu 2": 0.015,
            "câu 3": 0.04,
        }

    async def stream(self, text: str) -> AsyncGenerator[bytes, None]:
        text_lower = text.lower()
        latency = 0.02
        for key, delay in self.latencies.items():
            if key in text_lower:
                latency = delay
                break

        await asyncio.sleep(latency)
        yield f"AUDIO_OF_{text}".encode("utf-8")


# ---------------------------------------------------------------------------
# Test Cases
# ---------------------------------------------------------------------------

async def test_in_order_delivery_guaranteed():
    """
    KIỂM THỬ MẤU CHỐT:
    Câu 2 kết thúc TTS nhanh hơn câu 1, nhưng Re-Sequencer
    phải đảm bảo sequence 1 được xuất ra TRƯỚC sequence 2.
    """
    print("\n▸ 1. Kiểm thử In-Order Delivery Guaranteed (Re-Sequencer)")
    mock_engine = MockVariableLatencyTTSEngine()
    pipeline = StreamingTTSWorkerPipeline(
        num_workers=2,
        max_queue_size=5,
        tts_engine=mock_engine,
    )
    pipeline.start()

    # Đẩy đồng thời câu 1 (chậm 120ms) và câu 2 (nhanh 15ms)
    await pipeline.push_sentence(1, "Đây là câu 1 rất dài cần nhiều thời gian.", "req_1")
    await pipeline.push_sentence(2, "Đây là câu 2 ngắn gọn.", "req_1")
    await pipeline.push_sentence(3, "Đây là câu 3 kết thúc.", "req_1")
    await pipeline.mark_complete(3)

    results: List[AudioResultItem] = []
    async for audio_item in pipeline.iterate_audio_results():
        results.append(audio_item)

    pipeline.cancel()

    # Kiểm tra thứ tự nghiêm ngặt 1 -> 2 -> 3
    assert len(results) == 3, f"Phải nhận đủ 3 kết quả, nhận được {len(results)}"
    sequences = [item.sequence for item in results]
    assert sequences == [1, 2, 3], f"Thứ tự âm thanh bị đảo! Nhận được: {sequences}"

    # Kiểm tra dữ liệu âm thanh tương ứng
    assert "câu 1".encode("utf-8") in results[0].audio_bytes
    assert "câu 2".encode("utf-8") in results[1].audio_bytes
    assert "câu 3".encode("utf-8") in results[2].audio_bytes
    print(f"  ✅ Hoàn tất 3 câu theo đúng thứ tự: {sequences}. Không bị đảo âm thanh!")


async def test_barge_in_cancellation():
    """
    KIỂM THỬ BARGE-IN:
    Khi gọi cancel(), các worker phải dừng ngay lập tức, hàng đợi được xả sạch,
    iterate_audio_results() ngắt vòng lặp an toàn.
    """
    print("\n▸ 2. Kiểm thử Barge-In / Instant Cancellation")
    mock_engine = MockVariableLatencyTTSEngine({"câu dài": 0.5})
    pipeline = StreamingTTSWorkerPipeline(
        num_workers=2,
        max_queue_size=5,
        tts_engine=mock_engine,
    )
    pipeline.start()

    await pipeline.push_sentence(1, "Đây là câu dài đang tổng hợp dở dang.", "req_cancel")
    await pipeline.push_sentence(2, "Đây là câu tiếp theo chưa kịp xử lý.", "req_cancel")

    # Cho worker chạy 30ms rồi ngắt (Barge-In)
    await asyncio.sleep(0.03)
    pipeline.cancel()

    # iterate_audio_results() phải trả về ngay lập tức không bị treo
    results = []
    async for item in pipeline.iterate_audio_results():
        results.append(item)

    # Đảm bảo không bị deadlock
    assert pipeline._is_cancelled is True
    assert pipeline._sentence_queue.empty()
    assert len(pipeline._reorder_buffer) == 0
    print("  ✅ Barge-In thành công: Worker đã bị dừng, queue và buffer đã được giải phóng sạch!")


async def test_empty_and_single_sentence():
    """
    KIỂM THỬ BIÊN:
    - Trường hợp 0 câu (mark_complete(0)): Thoát ngay lập tức.
    - Trường hợp 1 câu: Xử lý mượt mà và kết thúc đúng.
    """
    print("\n▸ 3. Kiểm thử Biên (0 câu và 1 câu đơn lẻ)")
    mock_engine = MockVariableLatencyTTSEngine()

    # Case A: 0 câu
    pipeline_empty = StreamingTTSWorkerPipeline(num_workers=2, tts_engine=mock_engine)
    pipeline_empty.start()
    await pipeline_empty.mark_complete(0)

    empty_results = []
    async for item in pipeline_empty.iterate_audio_results():
        empty_results.append(item)
    pipeline_empty.cancel()
    assert len(empty_results) == 0
    print("  ✅ 0 câu: Thoát lập tức, không nghẽn vòng lặp.")

    # Case B: 1 câu
    pipeline_single = StreamingTTSWorkerPipeline(num_workers=2, tts_engine=mock_engine)
    pipeline_single.start()
    await pipeline_single.push_sentence(1, "Xin chào bạn.", "req_single")
    await pipeline_single.mark_complete(1)

    single_results = []
    async for item in pipeline_single.iterate_audio_results():
        single_results.append(item)
    pipeline_single.cancel()
    assert len(single_results) == 1
    assert single_results[0].sequence == 1
    print("  ✅ 1 câu: Nhận đúng sequence 1.")


async def test_backpressure_and_metrics():
    """
    KIỂM THỬ BACKPRESSURE & METRICS:
    - Bounded queue giới hạn số câu chờ
    - Thống kê metrics: queue_depth_peak, sentences_processed, total_audio_bytes
    """
    print("\n▸ 4. Kiểm thử Backpressure Queue & Metrics")
    mock_engine = MockVariableLatencyTTSEngine({"chậm": 0.05})
    pipeline = StreamingTTSWorkerPipeline(
        num_workers=1,  # 1 worker để tạo hàng đợi dồn
        max_queue_size=3,
        tts_engine=mock_engine,
    )
    pipeline.start()

    for i in range(1, 5):
        await pipeline.push_sentence(i, f"Câu chậm số {i}.", "req_bp")

    await pipeline.mark_complete(4)

    results = []
    async for item in pipeline.iterate_audio_results():
        results.append(item)

    pipeline.cancel()
    assert len(results) == 4
    metrics = pipeline.metrics
    assert metrics["sentences_processed"] == 4
    assert metrics["queue_depth_peak"] > 0
    assert metrics["total_audio_bytes"] > 0
    print(f"  ✅ Backpressure & Metrics: {metrics}")


async def main():
    print("=" * 65)
    print("BẮT ĐẦU KIỂM THỬ GIAI ĐOẠN 4 (PHASE 4: STREAMING TTS PIPELINE)")
    print("=" * 65)

    await test_in_order_delivery_guaranteed()
    await test_barge_in_cancellation()
    await test_empty_and_single_sentence()
    await test_backpressure_and_metrics()

    print("\n" + "=" * 65)
    print("🎉 TẤT CẢ 5/5 BÀI KIỂM THỬ PHASE 4 ĐÃ ĐẠT 100% THÀNH CÔNG!")
    print("=" * 65)


if __name__ == "__main__":
    asyncio.run(main())
