# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 Dương Thanh Lịch — VN-MateAI. See LICENSE and NOTICE.
"""
tests/test_visual_broadcast_no_deadlock.py
==========================================
/api/v1/visual/broadcast không tự khoá event loop.

Endpoint async từng gọi thẳng skill đồng bộ display_visual_data; skill này chờ
coroutine gửi visual trên loop của server (run_coroutine_threadsafe(...).result)
— chính loop đang bị nó chặn — nên mỗi máy trạm online làm server đứng tới
12 s và visual không bao giờ tới. Không gọi mạng, không mở cửa sổ overlay.
"""
from __future__ import annotations

import asyncio
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import mateai.interfaces.http.routers.clients as clients  # noqa: E402
import skills.visual_skills as vs  # noqa: E402
from mateai.interfaces.websocket.client_orchestrator import orchestrator  # noqa: E402


async def test_broadcast_reaches_online_client_without_blocking(monkeypatch):
    sent = []

    async def fake_send(client_id, visual_type, data, title, duration, timeout=10.0):
        sent.append(client_id)
        return {"status": "success", "client_id": client_id}

    monkeypatch.setattr(orchestrator, "get_client_ids", lambda: ["pc-01"])
    monkeypatch.setattr(orchestrator, "send_visual_to_client", fake_send)
    monkeypatch.setattr(orchestrator, "_loop", asyncio.get_running_loop())
    monkeypatch.setattr(vs, "_spawn_local_overlay", lambda *a, **k: None)

    payload = clients.ClientVisualRequest(type="alert", data={"message": "test"}, title="T", duration=3)
    t0 = time.perf_counter()
    res = await asyncio.wait_for(
        clients.broadcast_visual_endpoint(payload, user={"username": "u", "role": "admin"}), timeout=8)
    assert time.perf_counter() - t0 < 3, "endpoint bị khoá chờ chính event loop"
    assert sent == ["pc-01"], res
