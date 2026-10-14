# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 Dương Thanh Lịch — VN-MateAI. See LICENSE and NOTICE.
"""Dev Fleet: lớp HTTP (phân quyền, dịch lỗi) và công cụ AI (đọc luôn là L0, ghi qua cổng, RBAC)."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).parent))
from fake_master import FakeMaster  # noqa: E402

from mateai.application.devfleet.service import dev_fleet  # noqa: E402
from mateai.application.security import security_guard as sg  # noqa: E402
from mateai.application.security.risk_engine import assess_risk  # noqa: E402
from mateai.config.loader import DevFleetConfig  # noqa: E402
from mateai.infrastructure.database.db_manager import db_manager  # noqa: E402
from mateai.interfaces.http.auth_dependencies import get_current_user  # noqa: E402
from mateai.interfaces.http.routers.dev_fleet import router  # noqa: E402
from skills import dev_fleet_tools as tools  # noqa: E402

PAYLOAD = {"title": "Sửa reconnect", "objective": "Sửa lỗi websocket không tự nối lại sau khi mất mạng",
           "acceptance_criteria": ["test pass"], "verification_steps": ["pytest"], "risk": "low", "require_tests": True,
           "required_capabilities": ["git", "python"]}


@pytest.fixture(scope="module")
def master():
    with FakeMaster() as srv:
        yield srv


@pytest.fixture
def fleet(master, monkeypatch):
    def use(**over):
        cfg = DevFleetConfig(**{"enabled": True, "mode": "controlled", "endpoint": master.url, "api_token": master.token,
                                "cache_ttl_s": 0.0, "timeout_s": 2.0, **over})
        monkeypatch.setattr(dev_fleet, "_config_getter", lambda: cfg)
        dev_fleet.reset()
        return cfg
    with db_manager._get_connection() as conn:
        for table in ("dev_runs", "dev_leases", "dev_events", "dev_projects"):
            conn.execute(f"DELETE FROM {table};")
        conn.commit()
    master.tasks.clear()
    master.idempotent.clear()
    master.dispatch_count = 0
    yield use
    dev_fleet.reset()


def client(role="admin"):
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_current_user] = lambda: {"role": role, "username": "boss"}
    return TestClient(app)


# ── HTTP ────────────────────────────────────────────────────────────────────

def test_status_when_module_is_off_is_cheap_and_clear():
    out = client().get("/api/v1/dev-fleet/status").json()
    assert out["enabled"] is False and out["mode"] == "disabled"


def test_roles_viewer_nothing_manager_reads_admin_writes(fleet):
    fleet()
    assert client("viewer").get("/api/v1/dev-fleet/status").status_code == 403
    assert client("manager").get("/api/v1/dev-fleet/workers").status_code == 200
    assert client("manager").post("/api/v1/dev-fleet/tasks", json=PAYLOAD).status_code == 403
    assert client("manager").post("/api/v1/dev-fleet/mode", json={"mode": "disabled"}).status_code == 403


def test_http_errors_are_translated(fleet):
    fleet(mode="read_only")
    r = client().post("/api/v1/dev-fleet/tasks", json=PAYLOAD)
    assert r.status_code == 409 and r.json()["detail"]["code"] == "read_only"
    fleet()
    r = client().post("/api/v1/dev-fleet/tasks", json={"title": "Sửa lỗi này"})
    assert r.status_code == 422 and "objective" in r.json()["detail"]
    assert client().get("/api/v1/dev-fleet/tasks/OP-khong-co").status_code == 404


def test_create_plan_and_inspect_a_task_over_http(fleet, master):
    fleet()
    c = client()
    plan = c.post("/api/v1/dev-fleet/tasks/plan", json=PAYLOAD).json()
    assert plan["ok"] and master.dispatch_count == 0
    made = c.post("/api/v1/dev-fleet/tasks", json={**PAYLOAD, "idempotency_key": "http-1"})
    assert made.status_code == 200 and made.json()["status"] == "dispatched"
    task = c.get(f"/api/v1/dev-fleet/tasks/{made.json()['task_id']}").json()
    assert task["status"] == "EXECUTING" and task["runs"][0]["worker_id"] == made.json()["worker_id"]
    assert [t["task_id"] for t in c.get("/api/v1/dev-fleet/tasks").json()["tasks"]] == [made.json()["task_id"]]
    assert c.post("/api/v1/dev-fleet/sync").json()["synced"] == 1
    assert c.post(f"/api/v1/dev-fleet/tasks/{made.json()['task_id']}/cancel").json()["status"] == "cancelled"
    assert c.get("/api/v1/dev-fleet/briefing").status_code == 200


def test_no_worker_is_a_409_with_reasons(fleet):
    fleet()
    r = client().post("/api/v1/dev-fleet/tasks", json={**PAYLOAD, "required_capabilities": ["android"]})
    assert r.status_code == 409 and r.json()["rejected"]


def test_project_endpoints_and_config_validation(fleet, monkeypatch):
    fleet()
    c = client()
    prj = c.post("/api/v1/dev-fleet/projects", json={"name": "VN-MateAI", "repository": "vn-mateai"}).json()
    got = c.get(f"/api/v1/dev-fleet/projects/{prj['project_id']}").json()
    assert got["progress"]["percent"] is None
    saved = {}
    monkeypatch.setattr(dev_fleet, "_save_config", lambda actor, updates, reason, mask: saved.update(updates))
    assert c.post("/api/v1/dev-fleet/mode", json={"mode": "read_only"}).status_code == 200 and saved["mode"] == "read_only"
    assert c.post("/api/v1/dev-fleet/mode", json={"mode": "bay-bong"}).status_code == 422
    c.post("/api/v1/dev-fleet/workers/mac-01/disable", json={"disabled": True})
    assert saved["disabled_workers"] == ["mac-01"]


# ── công cụ AI ──────────────────────────────────────────────────────────────

def test_read_tools_are_l0_and_write_tools_are_not():
    for name in ("get_dev_fleet_status", "list_dev_fleet_workers", "get_dev_fleet_task", "get_dev_fleet_briefing"):
        assert assess_risk(name) == 1, name                  # vẫn chạy được khi bật kill switch
    assert assess_risk("create_dev_fleet_task") >= 2 and assess_risk("cancel_dev_fleet_task") >= 2


def test_rbac_only_admin_can_write_support_can_read():
    def allowed(role, tool):
        return sg.security_guard._evaluate_rbac(tool, role, "t")[0]
    for role in ("operator", "viewer", "it_support"):
        assert not allowed(role, "create_dev_fleet_task") and not allowed(role, "cancel_dev_fleet_task"), role
    assert allowed("it_support", "get_dev_fleet_status") and not allowed("viewer", "get_dev_fleet_status")
    assert allowed("admin", "create_dev_fleet_task")


async def test_tools_say_plainly_when_module_is_off():
    out = await tools.get_dev_fleet_status()
    assert out["success"] is False and out["module_state"] == "disabled"
    out = await tools.create_dev_fleet_task(**{k: PAYLOAD[k] for k in ("title", "objective", "acceptance_criteria", "verification_steps")})
    assert out["success"] is False and out["module_state"] == "disabled"


async def test_tools_read_and_create_through_the_gate(fleet, master):
    fleet()
    st = await tools.get_dev_fleet_status()
    assert st["success"] and st["workers"]["total"] == 3 and {w["worker_id"] for w in st["workers_detail"]} >= {"mac-01"}
    listed = await tools.list_dev_fleet_workers(state="offline")
    assert [w["worker_id"] for w in listed["workers"]] == ["mac-03"]
    dry = await tools.create_dev_fleet_task(**{k: PAYLOAD[k] for k in ("title", "objective", "acceptance_criteria", "verification_steps")},
                                            risk="low", dry_run=True)
    assert dry["dry_run"] and master.dispatch_count == 0
    made = await tools.create_dev_fleet_task(**{k: PAYLOAD[k] for k in ("title", "objective", "acceptance_criteria", "verification_steps")},
                                             risk="low", require_tests=True)
    assert made["success"] and made["status"] == "dispatched" and master.dispatch_count == 1
    info = await tools.get_dev_fleet_task(made["task_id"])
    assert info["display_status"] == "EXECUTING" and info["runs"][0]["status"] == "QUEUED"


async def test_tool_refuses_vague_requests_and_asks_for_approval_on_risk(fleet, master):
    fleet()
    vague = await tools.create_dev_fleet_task(title="sửa", objective="sửa lỗi này nhé bạn ơi", acceptance_criteria=[], verification_steps=[])
    assert vague["success"] is False and "acceptance_criteria" in vague["error"]
    risky = await tools.create_dev_fleet_task(title="Nâng cấp thư viện", objective="Nâng cấp websockets lên bản mới và chạy test",
                                              acceptance_criteria=["test pass"], verification_steps=["pytest"],
                                              risk="high", rollback="git revert", require_tests=True)
    assert risky["status"] == "awaiting_approval" and risky["success"] is True and "CHƯA giao" in risky["message"]
    assert master.dispatch_count == 0


async def test_tool_for_unknown_task_reports_not_found(fleet):
    fleet()
    assert (await tools.get_dev_fleet_task("OP-khong-co"))["success"] is False


# ── cấu hình + thử kết nối (màn hình Dev Fleet) ─────────────────────────────

def test_config_view_never_returns_the_token(fleet, master):
    fleet()
    body = client().get("/api/v1/dev-fleet/config").json()
    assert body["has_token"] is True and master.token not in str(body) and body["endpoint"] == master.url
    assert client("manager").get("/api/v1/dev-fleet/config").status_code == 403


def test_save_settings_validates_and_blank_token_keeps_the_saved_one(fleet, monkeypatch):
    fleet()
    saved = {}
    monkeypatch.setattr(dev_fleet, "_save_config", lambda actor, updates, reason, mask: saved.update(updates))
    c = client()
    for bad in ({"endpoint": "ftp://x"}, {"endpoint": "https://169.254.169.254"}, {"endpoint": "https://u:p@host"},
                {"endpoint": "https://host/api"}, {"mode": "bay-bong"}, {"timeout_s": "nhanh"}, {}):
        assert c.post("/api/v1/dev-fleet/config", json=bad).status_code == 422, bad
    ok = c.post("/api/v1/dev-fleet/config", json={"endpoint": "https://master.congty.local:8443/", "api_token": "  ",
                                                  "mode": "read_only", "tls_verify": False})
    assert ok.status_code == 200
    assert saved == {"endpoint": "https://master.congty.local:8443", "tls_verify": False, "mode": "read_only", "enabled": True}
    assert "api_token" not in saved                                   # token trống = giữ nguyên
    c.post("/api/v1/dev-fleet/config", json={"api_token": "TOKEN-MOI"})
    assert saved["api_token"] == "TOKEN-MOI"


def test_cannot_enable_without_an_endpoint(fleet, monkeypatch):
    fleet(endpoint="")
    monkeypatch.setattr(dev_fleet, "_save_config", lambda *a, **k: None)
    r = client().post("/api/v1/dev-fleet/config", json={"mode": "read_only"})
    assert r.status_code == 422 and "địa chỉ Master" in r.json()["detail"]


def test_connection_test_is_real_and_works_while_the_module_is_off(master):
    c = client()
    ok = c.post("/api/v1/dev-fleet/test-connection", json={"endpoint": master.url, "api_token": master.token}).json()
    assert ok["ok"] and ok["api_version"] == "1.0" and ok["workers"] == 3 and ok["master"]["name"] == "Ubuntu-Master"
    bad = c.post("/api/v1/dev-fleet/test-connection", json={"endpoint": master.url, "api_token": "SAI"}).json()
    assert not bad["ok"] and bad["kind"] == "rejected" and "SAI" not in str(bad)
    assert c.post("/api/v1/dev-fleet/test-connection", json={"endpoint": "https://169.254.169.254"}).json()["ok"] is False
    dead = c.post("/api/v1/dev-fleet/test-connection", json={"endpoint": "http://127.0.0.1:1", "api_token": "x"}).json()
    assert not dead["ok"] and dead["kind"] == "unavailable"


def test_connection_test_flags_an_incompatible_master(master):
    master.api_version = "2.0"
    try:
        out = client().post("/api/v1/dev-fleet/test-connection", json={"endpoint": master.url, "api_token": master.token}).json()
    finally:
        master.api_version = "1.0"
    assert out["ok"] is False and out["kind"] == "incompatible" and out["compatible"] is False


def test_bad_project_name_is_a_422_not_a_500(fleet):
    fleet()
    assert client().post("/api/v1/dev-fleet/projects", json={"name": ""}).status_code == 422
