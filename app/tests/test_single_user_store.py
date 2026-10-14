# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 Dương Thanh Lịch — VN-MateAI. See LICENSE and NOTICE.
"""
tests/test_single_user_store.py
===============================
Bảng users (SQLite) là kho tài khoản DUY NHẤT.

Trước đây users.json là kho thứ hai, vừa đồng bộ ghi vừa làm dự phòng khi đọc:
- xoá user ở SQLite mà bước xoá trong users.json lỗi (lỗi bị nuốt) → user đã
  xoá vẫn đăng nhập được; mỗi lần khởi động users.json còn nạp lại → sống lại;
- DB mới: tạo admin/admin123 TRƯỚC rồi mới nhập users.json → mật khẩu người
  dùng đã đặt bị thay bằng mặc định; biến VNMATEAI_DEFAULT_*_PASSWORD bị bỏ qua;
- user trong users.json thiếu hash được gán mật khẩu "123456".
Dùng DB và users.json tạm, không đụng dữ liệu thật.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import mateai.infrastructure.database.db_manager as dbm  # noqa: E402
from mateai.application.security.auth_manager import AuthManager  # noqa: E402


@pytest.fixture
def fresh(tmp_path, monkeypatch):
    users_json = tmp_path / "users.json"
    monkeypatch.setattr(dbm, "USERS_JSON_PATH", users_json)
    for role in ("ADMIN", "MANAGER", "VIEWER"):
        monkeypatch.delenv(f"VNMATEAI_DEFAULT_{role}_PASSWORD", raising=False)

    def make():
        return dbm.DatabaseManager(db_path=tmp_path / "t.db")
    return users_json, make


def _ok(store, user, pwd):
    row = store.get_user_by_username_or_id(user)
    return bool(row) and AuthManager.verify_password(pwd, row["password_hash"])


def test_existing_users_json_password_survives_first_start(fresh):
    users_json, make = fresh
    users_json.write_text(json.dumps({"admin": {
        "username": "admin", "role": "admin",
        "password_hash": AuthManager.get_password_hash("MatKhauRieng!2026")}}), encoding="utf-8")
    store = make()
    assert _ok(store, "admin", "MatKhauRieng!2026")
    assert not _ok(store, "admin", "admin123"), "mật khẩu mặc định không được thay mật khẩu người dùng đã đặt"


def test_env_default_password_is_honoured(fresh, monkeypatch):
    _, make = fresh
    monkeypatch.setenv("VNMATEAI_DEFAULT_ADMIN_PASSWORD", "Prod-Only-Secret-9")
    store = make()
    assert _ok(store, "admin", "Prod-Only-Secret-9")
    assert not _ok(store, "admin", "admin123")


def test_users_without_hash_are_not_given_a_password(fresh):
    users_json, make = fresh
    users_json.write_text(json.dumps({
        "admin": {"username": "admin", "role": "admin",
                  "password_hash": AuthManager.get_password_hash("x-123456789")},
        "ghost": {"username": "ghost", "role": "admin"}}), encoding="utf-8")
    store = make()
    assert store.get_user_by_username_or_id("ghost") is None


def test_deleted_user_stays_deleted_and_cannot_log_in(fresh, monkeypatch):
    users_json, make = fresh
    users_json.write_text(json.dumps({
        "admin": {"username": "admin", "role": "admin", "password_hash": AuthManager.get_password_hash("a-123456789")},
        "bob": {"username": "bob", "role": "manager", "password_hash": AuthManager.get_password_hash("b-123456789")},
    }), encoding="utf-8")
    store = make()
    assert _ok(store, "bob", "b-123456789")
    store.delete_user("bob")

    import mateai.application.security.auth_manager as am
    monkeypatch.setattr(am, "db_manager", store)
    assert AuthManager().authenticate_user("bob", "b-123456789") is None, "users.json không được là kho dự phòng"
    store2 = make()  # khởi động lại: users.json vẫn còn bob
    assert store2.get_user_by_username_or_id("bob") is None, "tài khoản đã xoá không được sống lại"
