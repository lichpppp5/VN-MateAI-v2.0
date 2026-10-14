# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 Dương Thanh Lịch — VN-MateAI. See LICENSE and NOTICE.
"""
core/audio/streaming_tts_pipeline.py
=====================================
Âm đệm phản xạ (Acoustic ACK) cho đường voice.

  - get_acoustic_ack_audio():     lấy câu đệm từ cache (0ms) hoặc tổng hợp qua TTS
  - warmup_acoustic_ack_cache():  làm nóng cache câu đệm + câu hệ thống khi khởi động

Phase 2: đã gỡ các lớp tương thích chết (SentenceBoundaryStreamer — "tương
thích api_voice_stream.py", file đó không còn; edge_tts_stream_audio;
get_acoustic_ack_for_query) và các re-export. Tách câu: core/audio/sentence_buffer.py;
tổng hợp giọng: core/audio/tts_stream_engine.py; hàng đợi TTS:
core/audio/tts_queue_pipeline.py.
"""

from __future__ import annotations

import asyncio
import logging
from typing import List, Optional, Sequence

from mateai.infrastructure.tts.tts_stream_engine import get_tts_engine
from mateai.infrastructure.tts.acoustic_ack_catalog import (
    ACOUSTIC_ACK_CATALOG,
    ALL_ACOUSTIC_ACK_PHRASES,
    select_acoustic_ack,
)

logger = logging.getLogger(__name__)

ACOUSTIC_ACK_PHRASES: List[str] = ACOUSTIC_ACK_CATALOG["GENERAL_GENERIC"]
_ack_phrase_index: int = 0


# ---------------------------------------------------------------------------
# Acoustic ACK helpers (Phase 6: Context-Aware & Pre-warmed)
# ---------------------------------------------------------------------------

async def get_acoustic_ack_audio(
    phrase: Optional[str] = None,
    query: Optional[str] = None,
    domain: Optional[str] = None,
) -> Optional[bytes]:
    """
    Trả về bytes âm thanh câu đệm phản xạ (từ RAM cache nếu có — 0ms).
    - Nếu có phrase cụ thể: sử dụng phrase đó.
    - Nếu có query: dùng select_acoustic_ack(query) để chọn câu đệm phù hợp ngữ cảnh.
    - Mặc định: luân phiên trong danh mục GENERAL_GENERIC.
    """
    from mateai.infrastructure.tts.audio_cache import get_cached_audio_bytes

    if phrase is None:
        if query:
            phrase = select_acoustic_ack(query, domain=domain)
        else:
            global _ack_phrase_index
            phrase = ACOUSTIC_ACK_PHRASES[_ack_phrase_index % len(ACOUSTIC_ACK_PHRASES)]
            _ack_phrase_index += 1

    # 1. Tra cứu RAM Cache tức thì (0ms)
    cached_audio = get_cached_audio_bytes(phrase)
    if cached_audio:
        logger.debug("[AcousticACK] Cache HIT (0ms): '%s'", phrase)
        return cached_audio

    # 2. Tổng hợp dự phòng qua TTSStreamEngine nếu chưa cache
    engine = get_tts_engine()
    audio = await engine.synthesise(phrase)

    if audio:
        logger.info("[AcousticACK] Audio sẵn sàng (%d bytes): '%s'", len(audio), phrase)
    return audio


async def warmup_acoustic_ack_cache(extra_phrases: Sequence[str] = ()) -> None:
    """
    Pre-warm TTS cache cho TOÀN BỘ câu ACK theo danh mục ngữ cảnh (Phase 6).
    Đảm bảo 100% câu đệm sẵn sàng trong RAM Cache (0ms TTFA) khi có tác vụ kỹ thuật.
    """
    from mateai.infrastructure.tts.audio_cache import get_cached_audio_bytes

    # Câu hệ thống hay nói (trước đây làm nóng ở `audio_processor.prewarm_tts_cache`
    # bằng một thread + event loop riêng — trùng chức năng và dùng nhầm HTTP
    # client của loop chính).
    from mateai.config.loader import get_assistant_name
    ai_name = get_assistant_name()
    system_phrases = [
        f"Xin chào, em là {ai_name}. Tất cả các hệ thống phòng thủ và mạng lưới đang hoạt động tối ưu.",
        "Đang ở trạng thái sẵn sàng lắng nghe chỉ lệnh của bạn...",
        "Em đã thực hiện xong yêu cầu của bạn.",
        "Xin lỗi, em gặp lỗi xử lý nội bộ.",
        "Tác vụ này yêu cầu phê duyệt bảo mật, vui lòng xác nhận trên màn hình.",
    ]
    # extra_phrases: câu cố định của tầng khác (lệnh nhanh, câu hỏi lại / tạm
    # biệt của HUD) — tầng ghép nối (server) truyền vào, không import ngược.
    phrases = list(dict.fromkeys([*ALL_ACOUSTIC_ACK_PHRASES, *system_phrases, *extra_phrases]))

    total_phrases = len(phrases)
    logger.info(
        "[AcousticACK] Pre-warm TTS cache cho %d câu đệm ngữ cảnh (Phase 6)...",
        total_phrases,
    )
    engine = get_tts_engine()
    ok_count = 0

    for phrase in phrases:
        try:
            cached = get_cached_audio_bytes(phrase)
            if cached and len(cached) > 100:
                ok_count += 1
                logger.debug("[AcousticACK] Đã có trong cache (0ms): '%s'", phrase)
            else:
                audio = await engine.synthesise(phrase)
                if audio and len(audio) > 100:
                    ok_count += 1
                    logger.info("[AcousticACK] Đã tổng hợp & cache: '%s'", phrase)
                await asyncio.sleep(0.1)
        except Exception as exc:
            logger.debug("[AcousticACK] Lỗi pre-warm '%s': %s", phrase, exc)

    logger.info(
        "[AcousticACK] Pre-warm hoàn thành: %d/%d câu đệm đã sẵn sàng trong RAM/Disk Cache (0ms TTFA).",
        ok_count, total_phrases,
    )
