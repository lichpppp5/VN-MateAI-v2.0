# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 Dương Thanh Lịch — VN-MateAI. See LICENSE and NOTICE.
"""
mateai/interfaces/http/speech.py
================================
Tổng hợp lời nói (MP3) cho các đường HTTP/WebSocket không stream: HUD, kết
quả sau phê duyệt, TTS một lần.

Gọi qua module (`speech.tts_bytes(...)`) để test thay được ở một chỗ.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Optional

from mateai.application.voice.speech_text import sanitise_for_tts, shorten_for_speech

logger = logging.getLogger(__name__)


async def tts_bytes(text: str, timeout_s: float = 14.0) -> Optional[bytes]:
    """Đọc NGUYÊN một đoạn lời nói thành MP3, có chặn trên thời gian.

    Hàm bọc duy nhất của tầng HTTP quanh engine TTS canonical
    (`mateai.infrastructure.tts.tts_stream_engine`): làm sạch + rút gọn lời nói như
    `AudioEngine` cũ, rồi tổng hợp. Trả None khi lỗi/timeout — HUD vẫn gửi chữ
    của câu đó, chỉ không có tiếng.
    """
    from mateai.infrastructure.tts.tts_stream_engine import get_tts_engine

    spoken = shorten_for_speech(sanitise_for_tts(text or ""))
    if not spoken:
        return None
    try:
        data = await asyncio.wait_for(get_tts_engine().synthesise(spoken), timeout=timeout_s)
        return data if data and len(data) > 100 else None
    except asyncio.TimeoutError:
        logger.warning("[TTS] quá %ss, bỏ audio — vẫn gửi chữ: %s", timeout_s, spoken[:60])
        return None
    except Exception as exc:
        logger.warning("[TTS] lỗi, bỏ audio — vẫn gửi chữ: %s", exc)
        return None
