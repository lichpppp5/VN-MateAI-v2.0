# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 Dương Thanh Lịch — VN-MateAI. See LICENSE and NOTICE.
"""
tests/test_skills_run_off_loop.py
=================================
Skill ĐỒNG BỘ chạy ngoài event loop (core.plugin_manager.run_blocking).

Trước đây plugin_manager gọi thẳng func(**args) trên event loop: một skill
chậm (PowerShell, LLM đồng bộ 60 s, hộp thoại) làm đứng mọi kênh voice /
WebSocket của mọi người dùng. Không gọi mạng.
"""
from __future__ import annotations

import asyncio
import sys
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import core.plugin_manager as pm  # noqa: E402


async def test_event_loop_keeps_running_during_sync_skill(monkeypatch):
    seen = {}

    def slow_skill(seconds: float = 0.4):
        seen["thread"] = threading.get_ident()
        time.sleep(seconds)
        return {"slept": seconds}

    pm.plugin_manager._ensure_loaded()
    with pm.plugin_manager._lock:
        monkeypatch.setitem(pm.plugin_manager._registry, "t_slow_sync_skill",
                            {"func": slow_skill, "enabled": True})

    ticks = 0

    async def ticker():
        nonlocal ticks
        while True:
            await asyncio.sleep(0.02)
            ticks += 1

    t = asyncio.create_task(ticker())
    res = await pm.plugin_manager.execute_skill("t_slow_sync_skill", {"seconds": 0.4})
    t.cancel()

    assert res == {"success": True, "data": {"slept": 0.4}, "error": None}
    assert seen["thread"] != threading.get_ident(), "skill đồng bộ phải chạy ở thread khác"
    assert ticks >= 10, f"event loop bị chặn trong lúc skill chạy (chỉ {ticks} tick)"


async def test_com_initialised_per_call_and_balanced(monkeypatch):
    calls = []
    fake = type("FakePythoncom", (), {
        "CoInitialize": staticmethod(lambda: calls.append("init")),
        "CoUninitialize": staticmethod(lambda: calls.append("uninit")),
    })
    monkeypatch.setitem(sys.modules, "pythoncom", fake)

    def boom():
        calls.append("run")
        raise ValueError("x")

    try:
        await pm.run_blocking(boom)
    except ValueError:
        pass
    assert calls == ["init", "run", "uninit"]
