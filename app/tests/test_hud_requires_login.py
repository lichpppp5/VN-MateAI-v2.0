# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 Dương Thanh Lịch — VN-MateAI. See LICENSE and NOTICE.
"""
tests/test_hud_requires_login.py
================================
/ws/hud chưa đăng nhập: chỉ xem telemetry, KHÔNG nhận lệnh thoại.

Trước đây lệnh thoại từ HUD không token chạy dưới danh tính "hud" — được
security_guard cấp quyền admin — nên ai mở được cổng 443 cũng ra lệnh tool với
quyền admin. Không gọi mạng, không chạy LLM.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fastapi.testclient import TestClient  # noqa: E402

import mateai.interfaces.http.server as server  # noqa: E402
from mateai.interfaces.http import hud_voice  # noqa: E402
from mateai.interfaces.http import ws_auth  # noqa: E402


def _drain_until(ws, wanted: str, limit: int = 10) -> dict:
    for _ in range(limit):
        pkt = json.loads(ws.receive_text())
        if pkt.get("type") == wanted:
            return pkt
    raise AssertionError(f"không nhận được gói {wanted}")


def test_unauthenticated_hud_cannot_run_voice_commands(monkeypatch):
    started = []

    async def fake_voice(cmd_query, session_id="hud", *, caller):
        started.append((cmd_query, caller))

    monkeypatch.setattr(hud_voice, "process_command", fake_voice)
    client = TestClient(server.app)  # không `with` → không chạy startup
    with client.websocket_connect("/ws/hud") as ws:
        welcome = _drain_until(ws, "hud_welcome")
        assert welcome["authenticated"] is False
        _drain_until(ws, "auth_required")

        ws.send_text(json.dumps({"action": "voice_command", "query": "tắt máy chủ"}))
        refused = _drain_until(ws, "auth_required")
        assert "không nhận lệnh thoại" in refused["message"]
        ws.send_text(json.dumps({"action": "ping"}))
        _drain_until(ws, "pong")

    assert started == []


def test_authenticated_hud_runs_voice_as_logged_in_user(monkeypatch):
    started = []

    async def fake_voice(cmd_query, session_id="hud", *, caller):
        started.append((cmd_query, caller))

    monkeypatch.setattr(hud_voice, "process_command", fake_voice)
    monkeypatch.setattr(ws_auth, "authenticate_websocket",
                        lambda _ws: {"username": "carol", "role": "admin"})
    client = TestClient(server.app)
    with client.websocket_connect("/ws/hud") as ws:
        _drain_until(ws, "hud_welcome")
        ws.send_text(json.dumps({"action": "voice_command", "query": "mấy giờ rồi"}))
        ws.send_text(json.dumps({"action": "ping"}))
        _drain_until(ws, "pong")

    assert started == [("mấy giờ rồi", "carol")]


def test_manager_cannot_approve_over_hud_socket(monkeypatch):
    """Duyệt chỉ admin — cùng quy tắc với POST /api/v1/security/confirm-action."""
    from mateai.interfaces.http import approval_flow

    approved = []

    async def fake_confirm(ok, approver, action_id=None, skill_name=None):
        approved.append((action_id, approver))

    monkeypatch.setattr(approval_flow, "confirm", fake_confirm)
    monkeypatch.setattr(ws_auth, "authenticate_websocket",
                        lambda _ws: {"username": "mona", "role": "manager"})
    client = TestClient(server.app)
    with client.websocket_connect("/ws/hud") as ws:
        _drain_until(ws, "hud_welcome")
        ws.send_text(json.dumps({"action": "confirm_action", "action_id": "act_1", "approved": True}))
        refused = _drain_until(ws, "security_approval_rejected")
        assert "admin" in refused["message"]
    assert approved == []


def test_admin_approval_over_hud_socket_reaches_confirm_endpoint(monkeypatch):
    from mateai.interfaces.http import approval_flow

    approved = []

    async def fake_confirm(ok, approver, action_id=None, skill_name=None):
        approved.append((action_id, approver))

    monkeypatch.setattr(approval_flow, "confirm", fake_confirm)
    monkeypatch.setattr(ws_auth, "authenticate_websocket",
                        lambda _ws: {"username": "carol", "role": "admin"})
    client = TestClient(server.app)
    with client.websocket_connect("/ws/hud") as ws:
        _drain_until(ws, "hud_welcome")
        ws.send_text(json.dumps({"action": "confirm_action", "action_id": "act_1", "approved": True}))
        ws.send_text(json.dumps({"action": "ping"}))
        _drain_until(ws, "pong")
    assert approved == [("act_1", "carol")]
