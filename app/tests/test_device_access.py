# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 Dương Thanh Lịch — VN-MateAI. See LICENSE and NOTICE.
"""
tests/test_device_access.py
===========================
Quyền cho robot / thiết bị IoT (Web Portal → Bảo mật → Thiết bị & Robot).

  - Quyền đặt cho thiết bị chỉ áp dụng với danh tính "device:<id>" — máy chủ chỉ
    gán danh tính này khi thiết bị xác thực bằng token RIÊNG. Token dùng chung
    không mang quyền đó (ai có token chung cũng xưng được mọi device_id).
  - Chọn "A": tác vụ rủi ro cao vẫn cần duyệt; duyệt một lần thì thiết bị được
    nhớ, lần sau không hỏi lại; thu hồi được. Không áp dụng cho tài khoản người.
Dùng CSDL thử của phiên test (conftest). Không gọi mạng.
"""
from __future__ import annotations

import uuid
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from mateai.application.security.security_guard import security_guard
from mateai.infrastructure.database.db_manager import db_manager


@pytest.fixture
def device():
    dev = f"vnmate_test_{uuid.uuid4().hex[:6]}"   # không trùng tiền tố cũ esp32/robot…
    token = db_manager.issue_device_token(dev, created_by="test")
    yield dev, token
    db_manager.revoke_approval_grant(f"device:{dev}")
    db_manager.revoke_device_token(dev)


def test_device_role_applies_only_to_device_principal(device):
    dev, _ = device
    assert security_guard.resolve_role(f"device:{dev}") == "viewer"      # chưa đặt -> quy tắc cũ
    assert db_manager.set_device_role(dev, "admin")
    assert security_guard.resolve_role(f"device:{dev}") == "admin"
    # Cùng id nhưng KHÔNG qua token riêng (token chung / client tự khai) -> không có quyền đó.
    assert security_guard.resolve_role(dev) == "viewer"
    assert db_manager.set_device_role(dev, None)
    assert security_guard.resolve_role(f"device:{dev}") == "viewer"


def test_unknown_device_cannot_get_role():
    assert db_manager.set_device_role("khong_ton_tai", "admin") is False
    assert security_guard.resolve_role("device:khong_ton_tai") == "viewer"


def _ws(token=None):
    return SimpleNamespace(query_params={"token": token} if token else {}, headers={})


def test_auth_method_distinguishes_own_token_from_shared(device, monkeypatch):
    from mateai.interfaces.http import ws_auth, enrollment
    dev, token = device
    monkeypatch.setattr(enrollment, "get_device_enrollment_secret", lambda: "chung-123")
    # Chế độ tương thích (token chung còn nhận) đặt TƯỜNG MINH — không phụ thuộc config.json của máy.
    monkeypatch.setattr("mateai.config.loader.get_config_section",
                        lambda name: {"require_per_device_token": False} if name == "security" else {})
    assert ws_auth.device_auth_method(_ws(token), dev) == ws_auth.DEVICE_TOKEN
    assert ws_auth.device_auth_method(_ws("chung-123"), dev) == ws_auth.SHARED_SECRET
    assert ws_auth.device_auth_method(_ws(token), "thiet_bi_khac") is None   # token gắn đúng id
    assert ws_auth.device_auth_method(_ws(), dev) is None


def _client(role):
    import mateai.interfaces.http.routers.security as sec
    from mateai.interfaces.http.auth_dependencies import get_current_user
    app = FastAPI()
    app.include_router(sec.router)
    app.dependency_overrides[get_current_user] = lambda: {"username": f"{role}_u", "role": role}
    return TestClient(app)


def test_api_set_role_and_list(device):
    dev, _ = device
    c = _client("admin")
    assert c.put(f"/api/v1/security/devices/{dev}/role", json={"role": "admin"}).status_code == 200
    listed = {d["device_id"]: d for d in c.get("/api/v1/security/devices").json()["devices"]}
    assert listed[dev]["role"] == "admin"
    assert c.put(f"/api/v1/security/devices/{dev}/role", json={"role": "root"}).status_code == 422
    assert c.put("/api/v1/security/devices/khong_co/role", json={"role": "admin"}).status_code == 404


@pytest.mark.parametrize("role", ["viewer", "manager"])
def test_api_is_admin_only(device, role):
    dev, _ = device
    c = _client(role)
    assert c.put(f"/api/v1/security/devices/{dev}/role", json={"role": "admin"}).status_code == 403
    assert c.get(f"/api/v1/security/devices/{dev}/approvals").status_code == 403


async def test_approval_remembered_for_device_then_not_asked_again(device, monkeypatch):
    import mateai.application.agent.tool_gate as tg
    dev, _ = device
    db_manager.set_device_role(dev, "admin")
    principal = f"device:{dev}"
    ran = []

    async def fake_execute(*a, **k):
        ran.append(a[0] if a else k.get("skill_name"))
        return {"success": True, "data": {"status": "success"}}

    import core.plugin_manager as pm
    monkeypatch.setattr(pm.plugin_manager, "execute_skill", fake_execute)

    first = await tg.run_tool_with_policy("kill_process", {"pid": 1}, caller=principal,
                                          source_device=dev, query="dừng tiến trình 1")
    assert first["result"]["status"] == "need_confirm" and ran == []

    # Anh bấm "Đồng ý" -> executor chạy tác vụ và ghi nhớ.
    item = {"action_name": "kill_process", "params": {"pid": 1}, "requested_by": principal,
            "reviewed_by": "admin", "context": {"target_client": "master", "source_device": dev}}
    await tg.execute_approved_tool(item)
    assert db_manager.has_approval_grant(principal, "kill_process")

    again = await tg.run_tool_with_policy("kill_process", {"pid": 2}, caller=principal,
                                          source_device=dev, query="dừng tiến trình 2")
    assert again["result"].get("status") != "need_confirm" and len(ran) == 2

    # Thu hồi -> lại phải duyệt.
    db_manager.revoke_approval_grant(principal, "kill_process")
    third = await tg.run_tool_with_policy("kill_process", {"pid": 3}, caller=principal,
                                          source_device=dev, query="dừng tiến trình 3")
    assert third["result"]["status"] == "need_confirm"


def test_approvals_of_people_are_not_remembered():
    import mateai.application.agent.tool_gate as tg
    tg._remember_approval({"action_name": "kill_process", "requested_by": "bob", "reviewed_by": "admin"})
    assert db_manager.list_approval_grants("bob") == []
    from mateai.application.security.policy_engine import has_delegation
    assert has_delegation("bob", "kill_process") is False
