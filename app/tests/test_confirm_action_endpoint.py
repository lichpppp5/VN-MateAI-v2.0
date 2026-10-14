# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 Dương Thanh Lịch — VN-MateAI. See LICENSE and NOTICE.
"""
tests/test_confirm_action_endpoint.py
=====================================
`POST /api/v1/security/confirm-action` — duyệt tác vụ NEED_CONFIRM.

Trước đây mọi người đã đăng nhập (kể cả viewer) gửi
`{approved: true, skill_name, args}` là chạy thẳng skill bất kỳ trên máy chủ
qua `plugin_manager.execute_skill`, không qua Zero-Trust/RBAC, không cần tác vụ
nào đang chờ. Nay: chỉ admin, chỉ đúng tác vụ trong hàng đợi, chạy qua cổng
tool chung với `approved=True`. Không gọi mạng, không chạy skill thật.
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

import mateai.application.agent.tool_gate as tool_gate
import mateai.interfaces.http.server as server
from mateai.interfaces.http import speech
import mateai.interfaces.http.routers.security as sec  # noqa: F401
from mateai.interfaces.http import approval_flow
from mateai.application.agent.llm_engine import llm_engine
from mateai.application.security.auth_manager import auth_manager
from mateai.interfaces.http.auth_dependencies import get_current_user


@pytest.fixture
def env(monkeypatch):
    calls = []

    async def fake_gate(fn_name, fn_args, **kw):
        calls.append((fn_name, dict(fn_args), kw))
        return {"target_client": "master", "args": fn_args, "result": {"status": "success"}}

    async def no_llm(*a, **k):
        raise RuntimeError("không gọi LLM trong test")

    async def noop(*a, **k):
        return None

    async def no_tts(*a, **k):
        return b""

    monkeypatch.setattr(tool_gate, "run_tool_with_policy", fake_gate)
    monkeypatch.setattr(llm_engine, "_call_llm", no_llm)
    monkeypatch.setattr(approval_flow, "broadcast_hud", noop)
    monkeypatch.setattr(approval_flow, "broadcast_portal_ui", noop)
    monkeypatch.setattr(speech, "tts_bytes", no_tts)
    # Hàng đợi duyệt duy nhất, riêng cho mỗi test.
    import mateai.application.security.zero_trust as zt
    q = zt.HumanInTheLoopManager()
    q.register_executor(tool_gate.TOOL_KIND, tool_gate.execute_approved_tool)
    monkeypatch.setattr(zt, "hitl_manager", q)
    monkeypatch.setattr(tool_gate, "hitl_manager", q)
    yield calls, q
    server.app.dependency_overrides.pop(get_current_user, None)


def _client(role: str) -> TestClient:
    # Middleware xác thực đòi Bearer token thật; vai trò lấy qua override.
    server.app.dependency_overrides[get_current_user] = lambda: {"username": f"{role}_u", "role": role}
    tok = auth_manager.create_access_token(data={"sub": f"{role}_u", "role": role})
    return TestClient(server.app, headers={"Authorization": f"Bearer {tok}"})


def _pending(q, tool="write_file", args=None, user="requester_u"):
    saved = q.request_approval(
        action_name=tool, params=args or {"file_path": "a.txt", "content": "x"}, requested_by=user,
        kind=tool_gate.TOOL_KIND, context={"target_client": "master", "query": "ghi tệp", "source_device": "http:fs"},
    )
    return saved["id"]


@pytest.mark.parametrize("role", ["viewer", "manager"])
def test_non_admin_cannot_approve_or_run_arbitrary_skill(env, role):
    calls, _ = env
    r = _client(role).post("/api/v1/security/confirm-action",
                           json={"approved": True, "skill_name": "delete_item", "args": {"path": "C:/"}})
    assert r.status_code == 403
    assert calls == []


def test_without_pending_action_nothing_runs(env, monkeypatch):
    calls, _ = env  # hàng đợi rỗng
    r = _client("admin").post("/api/v1/security/confirm-action",
                              json={"approved": True, "skill_name": "delete_item", "args": {"path": "C:/"}})
    assert r.status_code == 404
    assert calls == []


def test_approval_runs_exactly_the_queued_action_through_the_gate(env):
    calls, created = env
    act_id = _pending(created)
    r = _client("admin").post("/api/v1/security/confirm-action",
                              json={"approved": True, "action_id": act_id,
                                    "args": {"file_path": "C:/Windows/evil.txt", "content": "pwn"}})
    assert r.status_code == 200, r.text
    name, args, kw = calls[0]
    assert name == "write_file"
    assert args["file_path"] == "a.txt" and args["content"] == "x"
    assert kw["approved"] is True
    assert kw["caller"] == "requester_u"
    assert created.get_pending(act_id) is None


def test_skill_name_mismatch_is_rejected(env):
    calls, created = env
    act_id = _pending(created)
    r = _client("admin").post("/api/v1/security/confirm-action",
                              json={"approved": True, "action_id": act_id, "skill_name": "delete_item"})
    assert r.status_code == 409
    assert calls == []


def test_replayed_action_id_does_not_fall_back_to_another_pending_action(env):
    calls, created = env
    act_id = _pending(created)
    _pending(created, args={"file_path": "b.txt", "content": "y"}, user="other_u")
    c = _client("admin")
    assert c.post("/api/v1/security/confirm-action", json={"approved": True, "action_id": act_id}).status_code == 200
    r = c.post("/api/v1/security/confirm-action", json={"approved": True, "action_id": act_id})
    assert r.status_code == 404
    assert len(calls) == 1


@pytest.mark.parametrize("role", ["viewer", "manager"])
def test_pending_list_is_admin_only(env, role):
    """Tham số tác vụ đang chờ có thể chứa nội dung nhạy cảm (vd nội dung tệp sắp ghi)."""
    assert _client(role).get("/api/v1/security/pending-action").status_code == 403
    assert _client("admin").get("/api/v1/security/pending-action").status_code == 200


@pytest.mark.parametrize("role", ["viewer", "manager"])
def test_audit_logs_are_admin_only(env, role):
    """Payload audit chứa tham số tác vụ (vd nội dung tệp ghi qua fs/write)."""
    assert _client(role).get("/api/v1/security/audit-logs").status_code == 403


def test_single_audit_endpoint_with_normalised_outcome(env):
    """Một endpoint đọc audit (trước có thêm /api/v1/audit-logs đọc cùng bảng, dạng khác)."""
    c = _client("admin")
    assert c.get("/api/v1/audit-logs").status_code == 404
    logs = c.get("/api/v1/security/audit-logs").json()["logs"]
    assert all(l["outcome"] in ("success", "failed", "pending", "blocked") for l in logs)
