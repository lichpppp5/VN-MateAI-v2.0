# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 Dương Thanh Lịch — VN-MateAI. See LICENSE and NOTICE.
"""
tests/test_forced_password_change.py
====================================
Bắt đổi mật khẩu ở lần đăng nhập đầu (thẩm định thương mại 14/10/2026 P0-10): tài khoản mặc định, mật khẩu admin vừa đặt lại
cho người khác và mật khẩu dev công khai (admin123…) không được dùng để vào hệ thống; token đổi mật khẩu không phải token truy
cập; chính sách mật khẩu mới; đổi xong thì đi tiếp MFA như thường.
"""
from __future__ import annotations

import uuid

import pytest
from fastapi.testclient import TestClient

from mateai.application.security.auth_manager import auth_manager
from mateai.infrastructure.database.db_manager import db_manager
from mateai.interfaces.http.server import app

OLD = "Batdau#2026x"
NEW = "Moi2026-matkhau"


@pytest.fixture
def client():
    return TestClient(app)


def _user(must_change=True, role="viewer"):
    name = f"pw-{uuid.uuid4().hex[:8]}"
    db_manager.create_user({"username": name, "password": OLD, "role": role, "full_name": "Người thử đổi mật khẩu"})
    if must_change:
        db_manager.change_user_password(name, OLD, must_change=True)
    return name


def _login(c, name, pwd=OLD):
    return c.post("/api/v1/login", json={"username": name, "password": pwd}).json()


def test_flagged_account_only_gets_a_change_token(client):
    name = _user()
    r = _login(client, name)
    assert r["status"] == "password_change_required" and "access_token" not in r
    # token đổi mật khẩu KHÔNG mở được API nào
    assert client.get("/api/v1/health-dashboard", headers={"Authorization": f"Bearer {r['change_token']}"}).status_code == 401


def test_change_then_login_normally(client):
    name = _user()
    tok = _login(client, name)["change_token"]
    done = client.post("/api/v1/login/change-password", json={"change_token": tok, "new_password": NEW}).json()
    assert done["status"] == "success" and done["access_token"]
    assert _login(client, name, NEW)["status"] == "success"
    assert _login(client, name, OLD).get("status") != "success"


@pytest.mark.parametrize("bad,why", [("ngan1", "10 ký tự"), ("chichucaichu", "chữ và số"), ("0123456789", "chữ và số"),
                                     (OLD, "khác mật khẩu hiện tại"), ("admin123", "10 ký tự")])
def test_password_policy(client, bad, why):
    name = _user()
    tok = _login(client, name)["change_token"]
    r = client.post("/api/v1/login/change-password", json={"change_token": tok, "new_password": bad})
    assert r.status_code == 400 and why in r.json()["detail"]


def test_password_may_not_contain_username(client):
    name = _user()
    tok = _login(client, name)["change_token"]
    r = client.post("/api/v1/login/change-password", json={"change_token": tok, "new_password": f"{name}2026x"})
    assert r.status_code == 400 and "tên đăng nhập" in r.json()["detail"]


def test_forged_or_access_token_rejected(client):
    name = _user(must_change=False)
    access = _login(client, name)["access_token"]
    r = client.post("/api/v1/login/change-password", json={"change_token": access, "new_password": NEW})
    assert r.status_code == 401


def test_admin_reset_forces_change_for_others_but_not_self(client):
    other = _user(must_change=False)
    admin = _user(must_change=False, role="admin")
    h = {"Authorization": f"Bearer {_login(client, admin)['access_token']}"}
    assert client.put(f"/api/v1/users/{other}/password", json={"new_password": "Tamthoi2026x"}, headers=h).status_code == 200
    assert db_manager.get_user_by_username_or_id(other)["must_change_password"] == 1
    assert client.put(f"/api/v1/users/{admin}/password", json={"new_password": "Tuminh2026xx"}, headers=h).status_code == 200
    assert db_manager.get_user_by_username_or_id(admin)["must_change_password"] == 0


def test_known_dev_passwords_are_flagged():
    name = f"pw-{uuid.uuid4().hex[:8]}"
    db_manager.create_user({"username": name, "password": "viewer123", "role": "viewer", "full_name": "x"})
    db_manager._KNOWN_DEV_PASSWORDS = {**db_manager._KNOWN_DEV_PASSWORDS, name: "viewer123"}
    try:
        assert name in db_manager.flag_known_dev_passwords()
        assert db_manager.get_user_by_username_or_id(name)["must_change_password"] == 1
    finally:
        db_manager._KNOWN_DEV_PASSWORDS = {k: v for k, v in db_manager._KNOWN_DEV_PASSWORDS.items() if k != name}


def test_password_problem_rules():
    assert auth_manager.password_problem("Abcdefgh12", "an") == ""
    assert "10 ký tự" in auth_manager.password_problem("Ab1", "an")
