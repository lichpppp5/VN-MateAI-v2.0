# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 Dương Thanh Lịch — VN-MateAI. See LICENSE and NOTICE.
"""
tests/test_tts_pipeline_backpressure.py
=======================================
Realtime P4: hàng đợi audio ra của StreamingTTSWorkerPipeline có giới hạn.

Trước đây không giới hạn: client nhận chậm (mạng yếu, robot) thì audio của cả
lượt dồn trong RAM. Nay worker TTS chờ khi hàng đợi đầy, và vẫn đủ câu, đúng
thứ tự, không tắc khi kết thúc / huỷ. Không gọi mạng.
"""
from __future__ import annotations

import asyncio

from mateai.infrastructure.tts.tts_queue_pipeline import StreamingTTSWorkerPipeline


class _FastTTS:
    async def stream(self, text, *a, **k):
        await asyncio.sleep(0)
        yield f"AUDIO:{text}".encode()


async def _run(n, consumer_delay, max_buffered=4):
    p = StreamingTTSWorkerPipeline(tts_engine=_FastTTS(), num_workers=2, max_audio_buffered=max_buffered)
    p.start()
    got, peak = [], 0

    async def produce():
        for i in range(1, n + 1):
            await p.push_sentence(i, f"câu {i}", "req")
        await p.mark_complete(n)

    async def consume():
        nonlocal peak
        async for item in p.iterate_audio_results():
            peak = max(peak, p._audio_out_queue.qsize())
            got.append(item.text)
            await asyncio.sleep(consumer_delay)

    await asyncio.wait_for(asyncio.gather(produce(), consume()), 5)
    p.cancel()
    return got, peak


async def test_slow_consumer_bounds_buffered_audio_and_keeps_order():
    got, peak = await _run(12, consumer_delay=0.02)
    assert got == [f"câu {i}" for i in range(1, 13)]
    assert peak <= 4


async def test_fast_consumer_unaffected():
    got, _ = await _run(5, consumer_delay=0)
    assert got == [f"câu {i}" for i in range(1, 6)]


async def test_cancel_with_full_queue_does_not_hang():
    p = StreamingTTSWorkerPipeline(tts_engine=_FastTTS(), num_workers=2, max_audio_buffered=2)
    p.start()
    for i in range(1, 5):
        await p.push_sentence(i, f"câu {i}", "req")
    await asyncio.sleep(0.05)          # hàng đợi audio đầy, worker đang chờ
    p.cancel()
    await asyncio.sleep(0.05)
    assert all(w.done() for w in p._workers)
