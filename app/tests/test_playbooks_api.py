# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 Dương Thanh Lịch — VN-MateAI. See LICENSE and NOTICE.
"""Kịch bản vận hành: lớp HTTP (phân quyền, lỗi 422) và công cụ AI (danh tính người được phục vụ, chỉ đọc là L0, run chỉ admin)."""
from __future__ import annotations

import sys
from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).parent))
from test_playbooks import OK_VERIFY, READ, READ_P, _clean, admin, make, tools  # noqa: E402,F401  (fixture dùng chung)

from mateai.application.playbooks import engine as E  # noqa: E402
from mateai.application.security import security_guard as sg  # noqa: E402
from mateai.application.security.risk_engine import assess_risk  # noqa: E402
from mateai.config.loader import settings  # noqa: E402
from mateai.interfaces.http.auth_dependencies import get_current_user  # noqa: E402
from mateai.interfaces.http.routers.playbooks import router  # noqa: E402
from skills import playbook_tools as T  # noqa: E402


def client(role="admin", name=None):
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_current_user] = lambda: {"role": role, "username": name or "boss"}
    return TestClient(app)


DEF = {"id": "api-1", "name": "Thử API", "steps": [READ], "verify": []}


def test_viewer_sees_nothing_and_manager_cannot_change_anything(tools):
    assert client("viewer").get("/api/v1/playbooks").status_code == 403
    m = client("manager")
    assert m.get("/api/v1/playbooks").status_code == 200
    for method, path, body in (("put", "/api/v1/playbooks", DEF), ("post", "/api/v1/playbooks/api-1/run", {}), ("delete", "/api/v1/playbooks/api-1", None),
                               ("post", "/api/v1/playbook-runs/PBR-x/cancel", None), ("post", "/api/v1/playbook-runs/PBR-x/confirm", {}),
                               ("post", "/api/v1/playbooks/api-1/enable", {"enabled": False})):
        r = getattr(m, method)(path, **({"json": body} if body is not None else {}))
        assert r.status_code == 403, (method, path)


def test_save_validates_and_reports_what_is_wrong(tools):
    c = client()
    bad = c.put("/api/v1/playbooks", json={**DEF, "steps": [{"id": "a", "tool": "cong-cu-ma"}]})
    assert bad.status_code == 422 and "không có công cụ" in bad.json()["detail"]
    ok = c.put("/api/v1/playbooks", json=DEF)
    assert ok.status_code == 200 and ok.json()["version"] == 1
    assert c.get("/api/v1/playbooks/api-1").json()["definition"]["steps"][0]["tool"] == "get_system_info"
    assert c.get("/api/v1/playbooks/khong-co").status_code == 404
    assert c.post("/api/v1/playbooks/api-1/enable", json={"enabled": False}).json()["enabled"] is False
    assert c.delete("/api/v1/playbooks/api-1").status_code == 200 and c.delete("/api/v1/playbooks/api-1").status_code == 404


def test_manager_can_dry_run_but_not_run(tools, admin):
    make([READ_P, {"id": "ghi", "tool": "write_file", "args": {"path": "C:/t"}}], pid="thu-nghiem", params={"host": {"type": "string", "required": True}})
    r = client("manager", admin).post("/api/v1/playbooks/thu-nghiem/plan", json={"params": {"host": "srv"}})
    assert r.status_code == 200 and r.json()["needs_approval"] is True and tools.calls == []
    assert client("manager", admin).post("/api/v1/playbooks/thu-nghiem/plan", json={"params": {}}).status_code == 422          # thiếu tham số bắt buộc
    assert client("manager", admin).post("/api/v1/playbooks/khong-co/plan", json={}).status_code == 404


async def test_run_over_http_then_inspect_and_confirm(tools, admin):
    make([READ], pid="chay-1")
    c = client("admin", admin)
    r = c.post("/api/v1/playbooks/chay-1/run", json={"idempotency_key": "k-http-1"})
    assert r.status_code == 200 and r.json()["status"] == "running"
    run_id = r.json()["run_id"]
    assert c.post("/api/v1/playbooks/chay-1/run", json={"idempotency_key": "k-http-1"}).json()["status"] == "duplicate"
    await E.wait(run_id)
    detail = c.get(f"/api/v1/playbook-runs/{run_id}").json()
    assert detail["status"] == E.UNVERIFIED and detail["steps"][0]["status"] == "ok" and detail["plan"]["plan_hash"]
    assert [x["run_id"] for x in c.get("/api/v1/playbook-runs?playbook_id=chay-1").json()["runs"]] == [run_id]
    assert c.post(f"/api/v1/playbook-runs/{run_id}/confirm", json={"note": "đã xem tay"}).json()["status"] == "confirmed"
    assert c.post(f"/api/v1/playbook-runs/{run_id}/confirm", json={}).status_code == 409
    assert c.get("/api/v1/playbook-runs/PBR-khong-co").status_code == 404
    assert c.post("/api/v1/playbook-runs/PBR-khong-co/cancel").status_code == 404


async def test_risky_run_waits_for_approval_over_http(tools, admin):
    make([{"id": "ghi", "tool": "write_file", "args": {"path": "C:/t"}}], pid="rui-ro")
    out = client("admin", admin).post("/api/v1/playbooks/rui-ro/run", json={}).json()
    assert out["status"] == "awaiting_approval" and out["approval_id"] and tools.calls == []
    assert client("admin", admin).post(f"/api/v1/playbook-runs/{out['run_id']}/cancel").json()["status"] == "cancelled"


# ── công cụ AI ──────────────────────────────────────────────────────────────

async def test_tools_list_plan_run_and_report_honestly(tools, admin):
    make([READ_P], pid="doc", params={"host": {"type": "string", "required": True}}, verify=OK_VERIFY)
    make([{"id": "ghi", "tool": "write_file", "args": {"path": "C:/t"}}], pid="ghi-file")
    make([READ], pid="bi-tat")
    E.set_enabled("bi-tat", False, "t")
    tok = sg.CURRENT_PRINCIPAL.set(admin)
    try:
        listed = await T.list_playbooks()
        assert sorted(p["id"] for p in listed["playbooks"]) == ["doc", "ghi-file"]                   # bản tắt không lộ cho AI
        plan = await T.get_playbook_plan("ghi-file")
        assert plan["success"] and plan["needs_approval"] is True and plan["steps"][0]["tool"] == "write_file" and tools.calls == []
        assert (await T.get_playbook_plan("khong-co"))["success"] is False
        assert "bắt buộc" in (await T.get_playbook_plan("doc", {}))["error"]
        started = await T.run_playbook("doc", {"host": "srv"})
        assert started["success"] and started["status"] == "running"
        await E.wait(started["run_id"])
        info = await T.get_playbook_run(started["run_id"])
        assert info["status"] in (E.SUCCEEDED, E.FAILED) and info["steps"][0]["tool"] == "get_system_info" and tools.calls[0]["human"] is False
        calls_before = len(tools.calls)
        waiting = await T.run_playbook("ghi-file")
        assert waiting["awaiting_approval"] is True and "CHƯA chạy" in waiting["message"] and len(tools.calls) == calls_before
        assert (await T.get_playbook_run("PBR-khong-co"))["success"] is False
    finally:
        sg.CURRENT_PRINCIPAL.reset(tok)


async def test_ai_cannot_use_a_playbook_to_get_around_the_kill_switch(tools, admin, monkeypatch):
    make([{"id": "ghi", "tool": "write_file", "args": {"path": "C:/t"}}], pid="ghi-2")
    monkeypatch.setattr(settings.autonomy, "kill_switch", True)
    tok = sg.CURRENT_PRINCIPAL.set(admin)
    try:
        out = await T.run_playbook("ghi-2")
        assert out["success"] is False and out["status"] == "blocked" and out["denied"][0]["rule"] == "kill_switch" and tools.calls == []
    finally:
        sg.CURRENT_PRINCIPAL.reset(tok)


def test_tool_names_are_l0_for_reads_and_rbac_scoped():
    for name in ("list_playbooks", "get_playbook_plan", "get_playbook_run"):
        assert assess_risk(name) == 1, name
    assert assess_risk("run_playbook") >= 2

    def allowed(role, tool):
        return sg.security_guard._evaluate_rbac(tool, role, "t")[0]
    assert allowed("admin", "run_playbook") and not allowed("it_support", "run_playbook") and not allowed("viewer", "run_playbook")
    assert allowed("it_support", "get_playbook_plan") and not allowed("viewer", "list_playbooks")
