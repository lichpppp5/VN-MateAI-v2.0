# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 Dương Thanh Lịch — VN-MateAI. See LICENSE and NOTICE.
"""
tests/test_iot_port_filter.py
=============================
Listener cổng 8000 (không TLS) chỉ phục vụ đường của thiết bị IoT + health probe.

Trước đây cổng này chạy NGUYÊN app: POST /api/v1/login qua HTTP thường trả 200
(đã đo trên server thật) — mật khẩu và JWT đi qua LAN dạng rõ.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest
from starlette.websockets import WebSocketDisconnect

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fastapi.testclient import TestClient  # noqa: E402

import mateai.interfaces.http.server as server  # noqa: E402


def test_login_and_api_not_served_on_iot_port():
    c = TestClient(server.iot_listener_app)
    assert c.post("/api/v1/login", json={"username": "admin", "password": "x"}).status_code == 404
    assert c.get("/api/v1/config").status_code == 404
    assert c.get("/").status_code == 404


def test_probes_served_on_iot_port():
    assert TestClient(server.iot_listener_app).get("/livez").json() == {"status": "ok"}


def test_non_device_websocket_rejected_on_iot_port():
    with pytest.raises(WebSocketDisconnect) as exc:
        with TestClient(server.iot_listener_app).websocket_connect("/ws/portal-ui") as ws:
            ws.receive_text()
    assert exc.value.code == 1008


@pytest.mark.parametrize("path", ["/api/v1/xiaozhi/ws", "/api/v1/xiaozhi/ws/esp32_kitchen",
                                  "/ws/audio-stream", "/ws/audio-stream/esp32-default"])
def test_device_paths_reach_the_app(path):
    assert server._iot_port_allows(path)


@pytest.mark.parametrize("path", ["/api/v1/xiaozhi/wsevil", "/ws/audio-streamX", "/api/v1/xiaozhi"])
def test_lookalike_prefixes_are_rejected(path):
    assert not server._iot_port_allows(path)
