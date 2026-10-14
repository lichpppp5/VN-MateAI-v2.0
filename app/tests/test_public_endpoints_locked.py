# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 Dương Thanh Lịch — VN-MateAI. See LICENSE and NOTICE.
"""
tests/test_public_endpoints_locked.py
=====================================
Endpoint ghi/điều khiển không còn gọi được khi chưa đăng nhập.

Trước đây middleware xác thực có danh sách "public" gồm cả
computer-use/dispatch (điều khiển GUI máy chủ), computer-use/screenshot,
topology save/reset, departments/save, cross-report, ephemeral-cache/flush —
ai trong LAN cũng gọi được không cần JWT. Không gọi mạng.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fastapi.testclient import TestClient  # noqa: E402

import mateai.interfaces.http.server as server  # noqa: E402
from mateai.interfaces.http import enrollment  # noqa: E402

LOCKED = [
    ("POST", "/api/v1/computer-use/dispatch"),
    ("GET", "/api/v1/computer-use/screenshot"),
    ("GET", "/api/v1/computer-use/sessions"),
    ("POST", "/api/v1/system/topology/save"),
    ("POST", "/api/v1/system/topology/reset"),
    ("POST", "/api/v1/system/topology/trigger"),
    ("GET", "/api/v1/system/topology"),
    ("POST", "/api/v1/admin/departments/save"),
    ("POST", "/api/v1/admin/cross-report"),
    ("POST", "/api/v1/admin/ephemeral-cache/flush"),
    ("GET", "/api/v1/system/topology/events"),
    ("GET", "/api/v1/clients"),
    ("GET", "/api/v1/worknodes/status"),
]


@pytest.mark.parametrize("method,path", LOCKED)
def test_requires_login(method, path):
    client = TestClient(server.app)  # không `with` → không chạy startup
    res = client.request(method, path, json={"task_goal": "mở notepad"})
    assert res.status_code == 401, f"{method} {path} → {res.status_code}"


def test_pre_login_endpoints_still_public():
    client = TestClient(server.app)
    assert client.get("/api/v1/config/assistant-name").status_code == 200


_HEARTBEAT = {"node_id": "test-node-hb", "ip": "10.0.0.9", "capabilities": ["LOCAL_OCR"],
              "status": "idle", "cpu_percent": 1.0, "ram_percent": 2.0, "active_tasks": 0}


def test_worker_heartbeat_needs_worker_identity(monkeypatch):
    """Heartbeat giao task cho node — chỉ worker đã đăng ký mới được gửi."""
    monkeypatch.setattr(enrollment, "get_worker_enrollment_secret", lambda: "worker-secret-xyz")
    client = TestClient(server.app)
    url = "/api/v1/worknodes/heartbeat"

    assert client.post(url, json=_HEARTBEAT).status_code == 401
    assert client.post(url, json=_HEARTBEAT,
                       headers={"Authorization": "Bearer sai-secret"}).status_code == 401

    # JWT của người dùng thường (viewer) không được giả làm worker.
    from mateai.application.security.auth_manager import auth_manager
    monkeypatch.setattr(auth_manager, "get_user", lambda u: {"username": u, "role": "viewer"})
    viewer = auth_manager.create_access_token(data={"sub": "vera", "role": "viewer"})
    assert client.post(url, json=_HEARTBEAT,
                       headers={"Authorization": f"Bearer {viewer}"}).status_code == 401

    ok = client.post(url, json=_HEARTBEAT, headers={"Authorization": "Bearer worker-secret-xyz"})
    assert ok.status_code == 200, ok.text
