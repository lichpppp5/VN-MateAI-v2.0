# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 Dương Thanh Lịch — VN-MateAI. See LICENSE and NOTICE.
"""
mateai/interfaces/websocket/audio_announce.py
=============================================
Phát thông báo bằng giọng nói tới mọi mạch Xiaozhi đang online (báo cáo KPI của
`task_manager`). Chuyển từ `server.py` (Supervisor Phase 10, §198).

Lỗi cũ đã sửa: bản trong `server.py` gọi `asyncio.get_event_loop()` — gọi từ thread
không có event loop thì ném lỗi và bị `except: pass` nuốt, thông báo mất im lặng.
Nay giữ event loop của máy chủ (`bind_loop` lúc khởi động) và đẩy việc về loop đó
từ bất kỳ thread nào.
"""
from __future__ import annotations

import asyncio
import json
import logging
from typing import Optional, Set

from mateai.application.voice.speech_text import sanitise_for_tts, shorten_for_speech
from mateai.interfaces.websocket.realtime_hub import active_audio_nodes

logger = logging.getLogger(__name__)

_LOOP: Optional[asyncio.AbstractEventLoop] = None
_PENDING: Set["asyncio.Task[None]"] = set()


def bind_loop(loop: asyncio.AbstractEventLoop) -> None:
    global _LOOP
    _LOOP = loop


async def _broadcast(announcement_text: str) -> None:
    if not active_audio_nodes:
        logger.info("Không có mạch Xiaozhi nào online để phát: '%s'", announcement_text)
        return
    logger.info("Phát thanh TTS thông báo tới %d mạch Xiaozhi: '%s'", len(active_audio_nodes), announcement_text)
    try:
        from mateai.infrastructure.tts.tts_stream_engine import get_tts_engine
        chunks = [c async for c in get_tts_engine().stream(shorten_for_speech(sanitise_for_tts(announcement_text)))]
        for dev_id, info in list(active_audio_nodes.items()):
            ws = info.get("websocket")
            if not ws:
                continue
            try:
                await ws.send_text(json.dumps({"type": "tts_start", "text": announcement_text,
                                               "source": "kpi_notification"}))
                for chunk in chunks:
                    await ws.send_bytes(chunk)
                await ws.send_text(json.dumps({"type": "tts_end", "chunk_count": len(chunks)}))
            except Exception as err:  # noqa: BLE001
                logger.warning("Không thể stream TTS tới mạch [%s]: %s", dev_id, err)
    except Exception as e:  # noqa: BLE001
        logger.error("Lỗi stream TTS phát thanh thông báo: %s", e)


def broadcast_tts_notification(announcement_text: str) -> None:
    """Gọi được từ event loop hoặc từ thread bất kỳ; không chặn người gọi."""
    try:
        running = asyncio.get_running_loop()
    except RuntimeError:
        running = None
    if running is not None:
        task = running.create_task(_broadcast(announcement_text))
        _PENDING.add(task)                      # asyncio chỉ giữ tham chiếu yếu tới task
        task.add_done_callback(_PENDING.discard)
        return
    if _LOOP is None or _LOOP.is_closed():
        logger.warning("Chưa có event loop máy chủ — bỏ thông báo: '%s'", announcement_text)
        return
    asyncio.run_coroutine_threadsafe(_broadcast(announcement_text), _LOOP)
