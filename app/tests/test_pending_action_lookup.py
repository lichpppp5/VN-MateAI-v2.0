# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 Dương Thanh Lịch — VN-MateAI. See LICENSE and NOTICE.
"""
tests/test_pending_action_lookup.py
===================================
Hàng đợi duyệt DUY NHẤT (`zero_trust.hitl_manager`) và câu "đồng ý"/"huỷ"
trong hội thoại.

- "đồng ý" chỉ chạm được yêu cầu của người khác khi người nói là admin
  (trước đây ai nói "đồng ý" cũng duyệt tác vụ rủi ro cao của người khác);
- tra theo id chỉ khớp đúng id còn chờ (id đã xử lý không rơi sang yêu cầu khác);
- yêu cầu `kind="tool"` khôi phục được từ audit_logs sau khi khởi động lại,
  yêu cầu đã duyệt/huỷ thì không.
Không gửi Telegram (conftest tắt), không chạy tool thật.
"""
from __future__ import annotations

import pytest

import mateai.application.security.zero_trust as zt
from mateai.application.agent import llm_engine as le
from mateai.application.security.security_guard import security_guard


@pytest.fixture
def q(monkeypatch):
    m = zt.HumanInTheLoopManager()
    monkeypatch.setattr(zt, "hitl_manager", m)
    return m


def _req(m, user, tool="write_file", target="master", params=None):
    return m.request_approval(action_name=tool, params=params or {"file_path": f"{user}.txt"},
                              requested_by=user, kind="tool",
                              context={"target_client": target, "query": "q", "source_device": "web-widget"})


def test_get_pending_is_exact(q):
    a = _req(q, "requester_u")
    assert q.get_pending(a["id"])["id"] == a["id"]
    assert q.get_pending("HITL-NOPE") is None
    q.reject(a["id"], rejected_by="admin")
    assert q.get_pending(a["id"]) is None


def test_non_admin_cannot_take_someone_elses_request(q, monkeypatch):
    _req(q, "requester_u")
    monkeypatch.setattr(security_guard, "resolve_role", lambda who: "operator")
    assert le._find_pending_for("viewer_bob") is None


def test_own_request_is_found(q, monkeypatch):
    a = _req(q, "requester_u")
    monkeypatch.setattr(security_guard, "resolve_role", lambda who: "operator")
    assert le._find_pending_for("requester_u")["id"] == a["id"]


def test_admin_may_take_the_latest_request(q, monkeypatch):
    _req(q, "u1")
    b = _req(q, "u2", params={"file_path": "other.txt"})
    monkeypatch.setattr(security_guard, "resolve_role", lambda who: "admin")
    assert le._find_pending_for("admin_alice")["id"] == b["id"]


def test_same_tool_different_target_is_not_deduplicated(q):
    a = _req(q, "u", target="pc-01", params={"pid": 1})
    b = _req(q, "u", target="pc-02", params={"pid": 1})
    assert a["id"] != b["id"]
    assert _req(q, "u", target="pc-01", params={"pid": 1})["id"] == a["id"]


async def test_approval_runs_registered_executor(q):
    seen = []

    async def executor(item):
        seen.append((item["action_name"], item["params"], item["context"]["target_client"]))
        return {"status": "success"}

    q.register_executor("tool", executor)
    a = _req(q, "u", target="pc-09", params={"pid": 3})
    out = await q.approve_async(a["id"], approved_by="admin")
    assert out["status"] == "success" and out["execution_result"] == {"status": "success"}
    assert seen == [("write_file", {"pid": 3}, "pc-09")]
    again = await q.approve_async(a["id"], approved_by="admin")
    assert again["status"] == "error" and len(seen) == 1


def test_tool_requests_are_restored_from_audit(q):
    a = _req(q, "restore_u", target="pc-restore", params={"name": "spooler"})
    b = _req(q, "restore_u", target="pc-restore", params={"name": "other"})
    q.reject(b["id"], rejected_by="admin")

    fresh = zt.HumanInTheLoopManager()
    assert fresh.restore_pending_from_audit() >= 1
    got = fresh.get_pending(a["id"])
    assert got and got["context"]["target_client"] == "pc-restore" and got["requested_by"] == "restore_u"
    assert fresh.get_pending(b["id"]) is None


def test_closure_requests_are_not_restored(q):
    a = q.request_approval(action_name="drop_database", params={"db": "x"}, requested_by="u",
                           action_callback=lambda: None)
    fresh = zt.HumanInTheLoopManager()
    fresh.restore_pending_from_audit()
    assert fresh.get_pending(a["id"]) is None
