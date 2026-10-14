# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 Dương Thanh Lịch — VN-MateAI. See LICENSE and NOTICE.
"""
tests/test_client_execute_gate.py
=================================
`POST /api/v1/clients/{id}/execute` chạy qua cổng tool chung.

Trước đây endpoint tự đánh giá rủi ro rồi gọi thẳng máy trạm, và
`args.confirmed: true` là đủ để bỏ qua bước duyệt. Không gọi mạng.
"""
from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import mateai.interfaces.http.routers.clients as clients
from mateai.interfaces.http.auth_dependencies import get_current_user
from mateai.interfaces.websocket.client_orchestrator import orchestrator


def _client(role: str) -> TestClient:
    app = FastAPI()
    app.include_router(clients.router)
    app.dependency_overrides[get_current_user] = lambda: {"username": f"{role}_u", "role": role}
    return TestClient(app)


@pytest.fixture
def gate_calls(monkeypatch):
    calls = []

    async def fake_gate(tool, args, **kw):
        calls.append((tool, args, kw))
        return {"target_client": args.get("target_client"), "args": args, "result": {"status": "need_confirm"}}

    monkeypatch.setattr(clients, "run_tool_with_policy", fake_gate)
    monkeypatch.setattr(orchestrator, "is_client_online", lambda cid: cid == "pc-01")
    return calls


def test_execute_goes_through_gate_and_args_cannot_self_confirm(gate_calls):
    r = _client("admin").post("/api/v1/clients/pc-01/execute",
                              json={"skill_name": "kill_process", "args": {"pid": 4, "confirmed": True}, "timeout": 20})
    assert r.status_code == 200 and r.json()["status"] == "need_confirm"
    tool, args, kw = gate_calls[0]
    assert tool == "kill_process" and args["target_client"] == "pc-01"
    assert kw.get("approved", False) is False
    assert kw["caller"] == "admin_u" and kw["client_timeout"] == 20


@pytest.mark.parametrize("role", ["viewer", "manager"])
def test_non_admin_forbidden(gate_calls, role):
    assert _client(role).post("/api/v1/clients/pc-01/execute", json={"skill_name": "get_cpu"}).status_code == 403
    assert gate_calls == []


def test_offline_client_is_404(gate_calls):
    assert _client("admin").post("/api/v1/clients/pc-99/execute", json={"skill_name": "get_cpu"}).status_code == 404
    assert gate_calls == []
