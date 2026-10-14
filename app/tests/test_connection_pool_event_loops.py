# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 Dương Thanh Lịch — VN-MateAI. See LICENSE and NOTICE.
"""
tests/test_connection_pool_event_loops.py
=========================================
Mỗi event loop phải có httpx client riêng.

voice_controller và bước prewarm TTS chạy event loop riêng trong thread khác.
Trước Phase 2 pool chỉ có một bộ client nên các luồng đó dùng nhầm client của
loop chính (log khởi động: "9Router TTS exception: Event loop is closed").
"""
from __future__ import annotations

import asyncio
import sys
import threading
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from mateai.infrastructure.http.connection_pool import ConnectionPoolManager  # noqa: E402


def test_each_event_loop_gets_its_own_client():
    pool = ConnectionPoolManager()

    async def get_pair():
        return await pool.get_tts_client(), await pool.get_tts_client()

    main_a, main_b = asyncio.run(get_pair())
    assert main_a is main_b, "cùng một loop phải dùng lại cùng client"

    other: dict = {}

    def worker():
        other["client"] = asyncio.run(pool.get_tts_client())

    t = threading.Thread(target=worker)
    t.start()
    t.join()

    assert other["client"] is not main_a, "loop khác phải có client riêng"


def test_lock_not_bound_to_first_loop():
    """Gọi từ loop thứ hai không được lỗi 'bound to a different event loop'."""
    pool = ConnectionPoolManager()
    asyncio.run(pool.get_llm_client())
    asyncio.run(pool.get_llm_client())  # loop mới — trước đây asyncio.Lock gắn loop cũ


def test_sync_accessor_returns_primary_loop_client():
    pool = ConnectionPoolManager()

    async def first():
        return await pool.get_llm_client()

    primary = asyncio.run(first())
    assert pool.get_sync_llm_client() is primary
