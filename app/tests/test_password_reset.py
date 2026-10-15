# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 Dương Thanh Lịch — VN-MateAI. See LICENSE and NOTICE.
"""
Quên mật khẩu qua quản trị viên: câu trả lời trung tính (không dò được tài khoản), một yêu cầu mở / tài khoản, giới hạn theo
IP, chỉ admin xem / đóng, admin cấp mật khẩu tạm → yêu cầu tự đóng + người dùng phải đổi; lệnh khẩn cấp trên máy chủ.
"""
from __future__ import annotations

import uuid

import pytest
from fastapi.testclient import TestClient

from mateai.application.security.auth_manager import auth_manager
from mateai.infrastructure.database.db_manager import db_manager
from mateai.interfaces.http.server import app

PWD = "Batdau#2026x"


@pytest.fixture
def client():
    from mateai.interfaces.http.routers.auth import _store
    _store().events_clear("pwreset:ip:testclient")
    yield TestClient(app)
    _store().events_clear("pwreset:ip:testclient")


def _user(role="viewer"):
    name = f"fp-{uuid.uuid4().hex[:8]}"
    db_manager.create_user({"username": name, "password": PWD, "role": role, "full_name": "Người quên mật khẩu"})
    return name


def _auth(role="admin"):
    name = _user(role)
    return name, {"Authorization": "Bearer " + auth_manager.create_access_token({"sub": name, "role": role})}


def _forgot(c, username, note="0901 234 567"):
    return c.post("/api/v1/login/forgot-password", json={"username": username, "note": note})


def _open_for(name):
    from mateai.application.security import password_reset
    return [r for r in password_reset.list_open(500) if r["username"] == name]


def test_same_answer_for_known_and_unknown_user(client):
    known = _forgot(client, _user())
    unknown = _forgot(client, f"khong-co-{uuid.uuid4().hex[:6]}")
    assert known.status_code == unknown.status_code == 200
    assert known.json() == unknown.json()
    assert "created" not in known.json() and "username" not in known.json()


def test_one_open_request_per_account(client):
    name = _user()
    _forgot(client, name)
    _forgot(client, name, note="gọi lại giúp")
    rows = _open_for(name)
    assert len(rows) == 1 and rows[0]["note"] == "0901 234 567" and rows[0]["source_ip"] == "testclient"


def test_rate_limited_per_ip(client):
    for _ in range(5):
        assert _forgot(client, "ai-do").status_code == 200
    assert _forgot(client, "ai-do").status_code == 429


def test_only_admin_lists_and_closes(client):
    name = _user()
    _forgot(client, name)
    assert client.get("/api/v1/auth/password-reset/requests").status_code == 401
    _, viewer = _auth("viewer")
    assert client.get("/api/v1/auth/password-reset/requests", headers=viewer).status_code == 403
    _, admin = _auth("admin")
    rows = [r for r in client.get("/api/v1/auth/password-reset/requests", headers=admin).json()["requests"] if r["username"] == name]
    assert len(rows) == 1 and rows[0]["full_name"] == "Người quên mật khẩu"
    rid = rows[0]["id"]
    assert client.post(f"/api/v1/auth/password-reset/requests/{rid}/close", json={"status": "rejected"}, headers=viewer).status_code == 403
    assert client.post(f"/api/v1/auth/password-reset/requests/{rid}/close", json={"status": "rejected"}, headers=admin).status_code == 200
    assert _open_for(name) == []
    assert client.post(f"/api/v1/auth/password-reset/requests/{rid}/close", json={"status": "done"}, headers=admin).status_code == 404


def test_admin_grant_closes_request_and_forces_change(client):
    name = _user()
    _forgot(client, name)
    _, admin = _auth("admin")
    r = client.put(f"/api/v1/users/{name}/password", json={"new_password": "TamThoi2026x"}, headers=admin)
    assert r.status_code == 200
    assert _open_for(name) == []
    login = client.post("/api/v1/login", json={"username": name, "password": "TamThoi2026x"}).json()
    assert login["status"] == "password_change_required" and "access_token" not in login


def test_unknown_user_creates_nothing(client):
    ghost = f"ma-{uuid.uuid4().hex[:6]}"
    _forgot(client, ghost)
    assert _open_for(ghost) == []


def test_emergency_cli_resets_with_forced_change(client, capsys):
    import importlib.util
    from pathlib import Path
    spec = importlib.util.spec_from_file_location("reset_password", Path(__file__).resolve().parents[1] / "scripts" / "reset_password.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    name = _user("admin")
    _forgot(client, name)
    assert mod.main([name]) == 0
    out = capsys.readouterr().out
    pwd = out.split("\n\n")[1].strip()
    assert len(pwd) == 14 and any(c.isdigit() for c in pwd) and any(c.isalpha() for c in pwd)
    assert _open_for(name) == []
    login = client.post("/api/v1/login", json={"username": name, "password": pwd}).json()
    assert login["status"] == "password_change_required"
    assert client.post("/api/v1/login", json={"username": name, "password": PWD}).status_code == 401
    assert mod.main(["khong-ton-tai-xyz"]) == 1
