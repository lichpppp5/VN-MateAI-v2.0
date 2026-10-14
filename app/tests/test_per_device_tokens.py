# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 Dương Thanh Lịch — VN-MateAI. See LICENSE and NOTICE.
"""
tests/test_per_device_tokens.py
===============================
Token RIÊNG cho từng thiết bị IoT, ràng buộc với đúng device_id.

Trước đây mọi robot dùng CHUNG một device secret, còn device_id do thiết bị tự
đặt trên URL → lộ secret của một robot = giả được mọi robot. Token chung vẫn
được nhận để không ngắt robot cũ, trừ khi bật security.require_per_device_token.
DB tạm, không gọi mạng.
"""
from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import mateai.infrastructure.database.db_manager as dbm  # noqa: E402
import mateai.interfaces.http.server as server  # noqa: E402
from mateai.interfaces.http import ws_auth  # noqa: E402
from mateai.interfaces.http import enrollment  # noqa: E402


@pytest.fixture
def store(tmp_path, monkeypatch):
    monkeypatch.setattr(dbm, "USERS_JSON_PATH", tmp_path / "none.json")
    s = dbm.DatabaseManager(db_path=tmp_path / "t.db")
    monkeypatch.setattr(dbm, "db_manager", s)
    monkeypatch.setattr(enrollment, "get_device_enrollment_secret", lambda: "shared-secret-xyz")
    return s


def _ws(token):
    return SimpleNamespace(query_params={"token": token}, headers={}, client=SimpleNamespace(host="192.168.1.9"))


def test_token_is_bound_to_its_device(store):
    tok_a = store.issue_device_token("robot_a")
    store.issue_device_token("robot_b")
    assert ws_auth.authenticate_device(_ws(tok_a), "robot_a") is True
    assert ws_auth.authenticate_device(_ws(tok_a), "robot_b") is False, "token của A không được mở B"
    assert ws_auth.authenticate_device(_ws(tok_a), "esp32-default") is False


def test_rotate_and_revoke(store):
    old = store.issue_device_token("robot_a")
    new = store.issue_device_token("robot_a")
    assert ws_auth.authenticate_device(_ws(old), "robot_a") is False
    assert ws_auth.authenticate_device(_ws(new), "robot_a") is True
    assert store.revoke_device_token("robot_a") is True
    assert ws_auth.authenticate_device(_ws(new), "robot_a") is False
    assert "token" not in str(store.list_device_tokens()).lower().replace("device_tokens", "")


def test_shared_secret_can_be_switched_off(store, monkeypatch):
    # Chế độ tương thích (token chung còn nhận) đặt TƯỜNG MINH — không phụ thuộc config.json của máy.
    monkeypatch.setattr("mateai.config.loader.get_config_section",
                        lambda name: {"require_per_device_token": False} if name == "security" else {})
    assert ws_auth.authenticate_device(_ws("shared-secret-xyz"), "robot_x") is True
    monkeypatch.setattr("mateai.config.loader.get_config_section",
                        lambda name: {"require_per_device_token": True} if name == "security" else {})
    assert ws_auth.authenticate_device(_ws("shared-secret-xyz"), "robot_x") is False
    tok = store.issue_device_token("robot_x")
    assert ws_auth.authenticate_device(_ws(tok), "robot_x") is True


def test_only_hash_is_stored(store):
    tok = store.issue_device_token("robot_h")
    from mateai.infrastructure.database.erp_database import open_sqlite
    with open_sqlite(store.db_path) as conn:
        raw = [tuple(r) for r in conn.execute("SELECT * FROM device_tokens").fetchall()]
    assert tok not in str(raw)


async def test_admin_api_issue_list_revoke(store):
    import mateai.interfaces.http.routers.security as sec
    from fastapi import HTTPException

    admin = {"username": "admin", "role": "admin"}
    res = await sec.issue_device_token_endpoint(sec.DeviceTokenRequest(device_id="kitchen_bot"), user=admin)
    assert res["ws_path"] == "/api/v1/xiaozhi/ws/kitchen_bot" and len(res["device_token"]) > 30
    listed = await sec.list_device_tokens_endpoint(user=admin)
    assert [d["device_id"] for d in listed["devices"]] == ["kitchen_bot"]
    assert res["device_token"] not in str(listed), "danh sách không được lộ token"
    await sec.revoke_device_token_endpoint("kitchen_bot", user=admin)
    with pytest.raises(HTTPException):
        await sec.revoke_device_token_endpoint("kitchen_bot", user=admin)
    with pytest.raises(HTTPException):
        await sec.issue_device_token_endpoint(sec.DeviceTokenRequest(device_id="../etc"), user=admin)
