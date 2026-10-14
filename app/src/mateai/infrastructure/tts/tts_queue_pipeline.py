# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 Dương Thanh Lịch — VN-MateAI. See LICENSE and NOTICE.
"""
core/audio/tts_queue_pipeline.py
================================
Phase 4: Streaming TTS Worker Queue & Audio Buffer (In-Order Guaranteed).

Kiến trúc:
  LLM Token Stream
         │
         ▼
  SentenceBuffer (Tách câu an toàn, không ngắt số/IP)
         │
         ▼
  asyncio.Queue (Sentence Queue, maxsize=5 có Backpressure)
         │
  ┌──────┴──────┐
  ▼             ▼
TTS Worker 1  TTS Worker 2 (Xử lý gối đầu song song)
  │             │
  └──────┬──────┘
         ▼
  In-Order Re-sequencer (Đảm bảo 100% Sequence 1 → 2 → 3, không đảo audio)
         │
         ▼
  Audio Out Queue → WebSocket (Binary Frame) → Browser

Đặc tính:
  - TTFA tối ưu: Câu #1 bắt đầu tổng hợp TTS ngay khi có câu, trước khi LLM hoàn thành.
  - In-Order Guaranteed: Câu 2 dù hoàn thành trước câu 1 cũng phải đợi câu 1 phát xong.
  - Backpressure: Giới hạn độ sâu hàng đợi, theo dõi queue_depth & wait_time.
  - Cancellation / Barge-In: Dọn dẹp hàng đợi và hủy worker ngay lập tức khi bị ngắt.
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field
from typing import Any, AsyncGenerator, Dict, List, Optional, Set

from mateai.infrastructure.tts.tts_stream_engine import TTSStreamEngine, get_tts_engine

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Data Models
# ---------------------------------------------------------------------------

@dataclass
class SentenceItem:
    """Gói câu văn cần tổng hợp giọng nói."""
    sequence: int
    text: str
    request_id: str
    created_at: float = field(default_factory=time.monotonic)
    is_last: bool = False


@dataclass
class AudioResultItem:
    """Kết quả âm thanh đã tổng hợp kèm số thứ tự sequence."""
    sequence: int
    audio_bytes: bytes
    text: str
    request_id: str
    tts_latency_ms: int
    is_last: bool = False


# ---------------------------------------------------------------------------
# Streaming TTS Worker Pipeline
# ---------------------------------------------------------------------------

class StreamingTTSWorkerPipeline:
    """
    Pipeline điều phối hàng đợi TTS đa luồng gối đầu, bảo đảm nghiêm ngặt thứ tự âm thanh.
    """

    def __init__(
        self,
        voice: Optional[str] = None,
        max_queue_size: int = 5,
        num_workers: int = 2,
        tts_engine: Optional[Any] = None,
        sentence_timeout_s: float = 14.0,
        max_audio_buffered: int = 4,
    ) -> None:
        self.voice = voice
        # Một câu TTS treo không được giữ các câu sau (đang chờ đúng thứ tự)
        # mãi mãi: quá hạn thì câu đó không có tiếng, các câu sau đi tiếp.
        self.sentence_timeout_s = sentence_timeout_s
        self.max_queue_size = max_queue_size
        self.num_workers = num_workers
        self._tts_engine = tts_engine or TTSStreamEngine(voice=voice)

        # Hàng đợi câu đầu vào với backpressure
        self._sentence_queue: asyncio.Queue[Optional[SentenceItem]] = asyncio.Queue(maxsize=max_queue_size)

        # Bộ đệm sắp xếp lại thứ tự (In-Order Re-sequencer)
        self._reorder_buffer: Dict[int, AudioResultItem] = {}
        self._expected_sequence: int = 1
        self._total_sentences: Optional[int] = None
        self._dispatch_lock = asyncio.Lock()

        # Hàng đợi âm thanh đầu ra sẵn sàng phát qua WebSocket. CÓ giới hạn
        # (realtime P4): client nhận chậm thì worker TTS chờ -> hàng đợi câu đầy
        # -> lượt LLM chờ, thay vì audio dồn trong RAM không giới hạn. Bên tiêu
        # thụ (voice_turn._consume) chạy song song nên không tắc.
        self._audio_out_queue: asyncio.Queue[Optional[AudioResultItem]] = asyncio.Queue(
            maxsize=max(2, max_audio_buffered))

        self._workers: List[asyncio.Task] = []
        self._is_cancelled: bool = False
        self._t_start: float = time.monotonic()
        self._queue_depth_peak: int = 0
        self._sentences_processed: int = 0
        self._total_audio_bytes: int = 0

    # ------------------------------------------------------------------
    # Public Control API
    # ------------------------------------------------------------------

    def start(self) -> None:
        """Khởi động các TTS worker ngầm."""
        self._is_cancelled = False
        self._workers = [
            asyncio.create_task(self._worker_loop(w_id))
            for w_id in range(self.num_workers)
        ]

    async def push_sentence(
        self,
        sequence: int,
        text: str,
        request_id: str,
        is_last: bool = False,
    ) -> None:
        """Đẩy câu mới vào hàng đợi tổng hợp giọng nói."""
        if self._is_cancelled or not text.strip():
            return

        item = SentenceItem(
            sequence=sequence,
            text=text.strip(),
            request_id=request_id,
            is_last=is_last,
        )

        current_depth = self._sentence_queue.qsize()
        if current_depth > self._queue_depth_peak:
            self._queue_depth_peak = current_depth

        if is_last:
            self._total_sentences = sequence

        # Đẩy vào queue (tự động áp dụng backpressure nếu queue đầy)
        await self._sentence_queue.put(item)
        logger.debug("[TTSQueue] Pushed sentence #%d (queue_depth=%d): '%s'", sequence, current_depth, text[:30])

    async def mark_complete(self, final_sequence: int) -> None:
        """Báo hiệu đã nạp hết tất cả các câu của lượt tương tác."""
        async with self._dispatch_lock:
            self._total_sentences = final_sequence
            if final_sequence == 0 or self._expected_sequence > final_sequence:
                await self._audio_out_queue.put(None)
                return
        # Gửi tín hiệu Sentinel (None) tới tất cả workers
        for _ in range(self.num_workers):
            await self._sentence_queue.put(None)

    def cancel(self) -> None:
        """Hủy tác vụ tức thì (Barge-In) và dọn sạch hàng đợi."""
        self._is_cancelled = True
        for w in self._workers:
            if not w.done():
                w.cancel()
        self._reorder_buffer.clear()
        # Dọn sạch hàng đợi câu
        while not self._sentence_queue.empty():
            try:
                self._sentence_queue.get_nowait()
                self._sentence_queue.task_done()
            except Exception:
                break
        # Đưa None vào audio queue để ngắt vòng lặp consumer
        try:
            self._audio_out_queue.put_nowait(None)
        except Exception:
            pass

    async def iterate_audio_results(self) -> AsyncGenerator[AudioResultItem, None]:
        """
        Consumer tiêu thụ âm thanh phát ra WebSocket:
        Luôn đảm bảo thứ tự Sequence 1 -> Sequence 2 -> Sequence 3.
        """
        while not self._is_cancelled:
            result = await self._audio_out_queue.get()
            if result is None:
                break
            self._sentences_processed += 1
            self._total_audio_bytes += len(result.audio_bytes)
            yield result
            if self._total_sentences is not None and result.sequence >= self._total_sentences:
                break

    @property
    def metrics(self) -> Dict[str, Any]:
        """Thống kê vận hành của pipeline."""
        return {
            "queue_depth_peak": self._queue_depth_peak,
            "sentences_processed": self._sentences_processed,
            "total_audio_bytes": self._total_audio_bytes,
            "num_workers": self.num_workers,
        }

    # ------------------------------------------------------------------
    # Worker Loop & Re-Sequencer
    # ------------------------------------------------------------------

    async def _worker_loop(self, worker_id: int) -> None:
        """Vòng lặp lấy câu và gọi tổng hợp âm thanh."""
        while not self._is_cancelled:
            try:
                item = await self._sentence_queue.get()
            except asyncio.CancelledError:
                break

            if item is None:
                self._sentence_queue.task_done()
                break

            t0 = time.monotonic()
            wait_time_ms = int((t0 - item.created_at) * 1000)

            # Thu thập audio bytes từ TTSStreamEngine
            audio_bytes_accum = bytearray()

            async def _collect() -> None:
                async for chunk in self._tts_engine.stream(item.text):
                    if self._is_cancelled:
                        break
                    audio_bytes_accum.extend(chunk)

            try:
                await asyncio.wait_for(_collect(), timeout=self.sentence_timeout_s)
            except asyncio.CancelledError:
                break
            except asyncio.TimeoutError:
                logger.warning("[TTSQueue] Câu #%d quá %.0fs — bỏ tiếng câu này, đi tiếp",
                               item.sequence, self.sentence_timeout_s)
                audio_bytes_accum.clear()
            except Exception as exc:
                logger.warning("[TTSQueue] Lỗi tổng hợp câu #%d: %s", item.sequence, exc)

            audio_data = bytes(audio_bytes_accum)
            tts_latency = int((time.monotonic() - t0) * 1000)

            result = AudioResultItem(
                sequence=item.sequence,
                audio_bytes=audio_data,
                text=item.text,
                request_id=item.request_id,
                tts_latency_ms=tts_latency,
                is_last=item.is_last,
            )

            # Đưa vào bộ đệm sắp xếp thứ tự
            await self._dispatch_in_order(result)
            self._sentence_queue.task_done()

    async def _dispatch_in_order(self, result: AudioResultItem) -> None:
        """
        Khâu mấu chốt chống đảo lộn âm thanh (In-Order Dispatch):
        Chỉ đẩy ra output khi sequence khớp với `_expected_sequence`.
        Nếu câu 2 xong trước câu 1 -> lưu tạm vào buffer, đợi câu 1 ra trước.
        """
        async with self._dispatch_lock:
            self._reorder_buffer[result.sequence] = result

            while self._expected_sequence in self._reorder_buffer:
                next_item = self._reorder_buffer.pop(self._expected_sequence)
                await self._audio_out_queue.put(next_item)
                logger.info(
                    "[TTSQueue/InOrder] Dispatched audio #%d (size=%d bytes, tts=%dms) → WebSocket",
                    next_item.sequence, len(next_item.audio_bytes), next_item.tts_latency_ms,
                )
                self._expected_sequence += 1

            # Nếu đã hoàn thành tất cả
            if self._total_sentences is not None and self._expected_sequence > self._total_sentences:
                await self._audio_out_queue.put(None)
