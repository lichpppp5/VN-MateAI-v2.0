# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 Dương Thanh Lịch — VN-MateAI. See LICENSE and NOTICE.
"""
tests/test_fs_routes_policy.py
==============================
`/api/v1/fs/*` (routers/files.py) và tool `read_file`.

Trước đây: mọi người dùng đã đăng nhập (kể cả viewer) đọc được bất kỳ tệp nào
trên máy chủ — gồm `config.json` chứa khoá LLM và token Telegram — và trường
`confirmed` trong body cho phép ghi/xoá mà không qua phê duyệt.
"""
from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import mateai.interfaces.http.routers.files as files
from mateai.application.skills.builtin import file_system
from mateai.interfaces.http.auth_dependencies import get_current_user


def _client(role: str) -> TestClient:
    app = FastAPI()
    app.include_router(files.router)
    app.dependency_overrides[get_current_user] = lambda: {"username": f"{role}_u", "role": role}
    return TestClient(app)


@pytest.fixture
def gate_calls(monkeypatch):
    calls = []

    async def fake_gate(tool, args, **kw):
        calls.append((tool, args, kw))
        return {"target_client": "master", "args": args, "result": {"status": "need_confirm"}}

    monkeypatch.setattr(files, "run_tool_with_policy", fake_gate)
    return calls


@pytest.mark.parametrize("role", ["viewer", "manager"])
@pytest.mark.parametrize("path,body", [
    ("/api/v1/fs/list", {"path": "."}),
    ("/api/v1/fs/read", {"file_path": "config.json"}),
    ("/api/v1/fs/write", {"file_path": "x.txt", "content": "x"}),
    ("/api/v1/fs/delete", {"path": "x.txt"}),
])
def test_non_admin_is_forbidden(role, path, body, gate_calls):
    assert _client(role).post(path, json=body).status_code == 403
    assert gate_calls == []


def test_admin_write_goes_through_gate_and_body_cannot_self_confirm(gate_calls):
    r = _client("admin").post("/api/v1/fs/write",
                              json={"file_path": "x.txt", "content": "x", "confirmed": True})
    assert r.status_code == 200
    assert r.json()["status"] == "need_confirm"
    tool, args, kw = gate_calls[0]
    assert tool == "write_file"
    assert "confirmed" not in args
    assert kw.get("approved", False) is False
    assert kw["caller"] == "admin_u"


@pytest.mark.parametrize("name", ["config.json", ".env", "server.key", "vnmateai.db", "cert.pem"])
def test_read_file_refuses_secret_files(tmp_path, name):
    f = tmp_path / name
    f.write_text("sk-real-secret", encoding="utf-8")
    out = file_system.read_file(str(f))
    assert out["status"] == "error"
    assert "sk-real-secret" not in str(out)


def test_read_file_still_reads_ordinary_files(tmp_path):
    f = tmp_path / "app.log"
    f.write_text("hello\n", encoding="utf-8")
    out = file_system.read_file(str(f))
    assert out["status"] == "success"


def test_local_skill_envelope_is_unwrapped(monkeypatch):
    async def fake_gate(tool, args, **kw):
        return {"target_client": "master", "args": args,
                "result": {"success": True, "data": {"status": "success", "items": []}, "error": None}}

    monkeypatch.setattr(files, "run_tool_with_policy", fake_gate)
    r = _client("admin").post("/api/v1/fs/list", json={"path": "."})
    assert r.json() == {"status": "success", "items": []}
