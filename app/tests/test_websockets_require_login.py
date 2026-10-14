# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 Dương Thanh Lịch — VN-MateAI. See LICENSE and NOTICE.
"""
tests/test_websockets_require_login.py
======================================
/ws/voice, /ws/v1/voice-stream và /ws/topology từ chối kết nối chưa đăng nhập.

Trước đây: voice ẩn danh chạy dưới tên "web_user" (viewer — vẫn đọc được dữ
liệu tổ chức qua tool); topology cho ai cũng xem luồng tool và bơm sự kiện giả.
Không chạy startup, không gọi LLM.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest
from starlette.websockets import WebSocketDisconnect

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fastapi.testclient import TestClient  # noqa: E402

import mateai.interfaces.http.server as server  # noqa: E402
from mateai.interfaces.http import ws_auth  # noqa: E402


@pytest.mark.parametrize("path", ["/ws/v1/voice-stream", "/ws/topology"])
def test_rejects_without_token(path):
    client = TestClient(server.app)
    with pytest.raises(WebSocketDisconnect) as exc:
        with client.websocket_connect(path) as ws:
            ws.receive_text()
    assert exc.value.code == 1008


def test_topology_accepts_logged_in_user(monkeypatch):
    monkeypatch.setattr(ws_auth, "authenticate_websocket", lambda _ws: {"username": "dan", "role": "admin"})
    with TestClient(server.app).websocket_connect("/ws/topology") as ws:
        first = json.loads(ws.receive_text())
        # Gói đầu là trạng thái THẬT (trước đây "connected" + "active_nodes": 11 viết cứng).
        assert first["event"] == "snapshot" and first["nodes"]


def test_voice_runs_as_logged_in_user(monkeypatch):
    seen = {}

    async def fake_handler(websocket, user):
        seen["user"] = user
        await websocket.accept()
        await websocket.close()

    import mateai.interfaces.websocket.realtime_voice_ws as rv
    monkeypatch.setattr(rv, "handle_realtime_voice_endpoint", fake_handler)
    monkeypatch.setattr(ws_auth, "authenticate_websocket", lambda _ws: {"username": "dan", "sub": "dan"})
    with TestClient(server.app).websocket_connect("/ws/v1/voice-stream"):
        pass
    assert seen["user"]["username"] == "dan"
