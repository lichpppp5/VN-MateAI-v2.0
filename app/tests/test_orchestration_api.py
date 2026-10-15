# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 Dương Thanh Lịch — VN-MateAI. See LICENSE and NOTICE.
"""Lõi điều phối: lớp HTTP (phân quyền, mã lỗi), công cụ AI (danh tính người được phục vụ, quyền giao việc), Dev Fleet làm agent,
và AI thẩm định (định dạng, lỗi không bao giờ thành đạt)."""
from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).parent))
from test_orchestration import _clean, boss, done, llm, local, order, report, review  # noqa: E402,F401  (fixture dùng chung)

from mateai.application.orchestration import contracts as C  # noqa: E402
from mateai.application.orchestration import registry as R  # noqa: E402
from mateai.application.orchestration import service as S  # noqa: E402
from mateai.application.orchestration import verify as V  # noqa: E402
from mateai.application.security.risk_engine import assess_risk  # noqa: E402
from mateai.application.security.security_guard import CURRENT_PRINCIPAL  # noqa: E402
from mateai.application.tasks import ledger  # noqa: E402
from mateai.interfaces.http.auth_dependencies import get_current_user  # noqa: E402
from mateai.interfaces.http.routers.orchestration import router  # noqa: E402
from skills import orchestration_tools as T  # noqa: E402


def client(role="admin", name="boss"):
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_current_user] = lambda: {"role": role, "username": name}
    return TestClient(app)


@pytest.fixture
def no_dispatch(monkeypatch):
    """TestClient chạy mỗi request trong vòng lặp riêng — không giao thật (vòng đời phiếu đã có test riêng)."""
    monkeypatch.setattr(S, "pump", lambda: 0)


def test_viewer_sees_nothing_manager_dispatches_only_admin_configures(no_dispatch):
    assert client("viewer").get("/api/v1/orchestration/executors").status_code == 403
    m = client("manager")
    assert m.get("/api/v1/orchestration/executors").status_code == 200
    for method, path, body in (("put", "/api/v1/orchestration/executors/dba", {"kind": "local"}),
                               ("post", "/api/v1/orchestration/executors/dba/status", {"status": "suspended"}),
                               ("delete", "/api/v1/orchestration/executors/dba", None), ("get", "/api/v1/orchestration/tools", None),
                               ("post", "/api/v1/orchestration/orders/WO-x/confirm", {"accept": True})):
        r = getattr(m, method)(path, **({"json": body} if body is not None else {}))
        assert r.status_code == 403, (method, path)


def test_executor_crud_reports_what_is_wrong_and_protects_open_work(no_dispatch):
    a = client()
    bad = a.put("/api/v1/orchestration/executors/dba", json={"kind": "local", "capabilities": ["dba"], "tools": ["cong_cu_ma"]})
    assert bad.status_code == 422 and "Không có công cụ" in bad.json()["detail"]
    ok = a.put("/api/v1/orchestration/executors/dba", json={"kind": "local", "name": "DBA", "capabilities": ["dba"],
                                                            "tools": ["run_local_sql_check"]})
    assert ok.status_code == 200 and ok.json()["version"] == 1
    assert "run_local_sql_check" in a.get("/api/v1/orchestration/tools").json()["tools"]
    assert "dispatch_work_order" not in a.get("/api/v1/orchestration/tools").json()["tools"]
    created = a.post("/api/v1/orchestration/orders", json=order())
    assert created.status_code == 200 and created.json()["status"] == "PLANNED"
    assert a.delete("/api/v1/orchestration/executors/dba").status_code == 409          # còn phiếu chưa xong
    ex = a.get("/api/v1/orchestration/executors").json()["executors"][0]
    assert ex["available"] is True and ex["scorecard"]["open"] == 1


def test_orders_api_errors_map_to_clear_status_codes(no_dispatch, boss):
    local()
    m = client("manager", boss)
    assert m.post("/api/v1/orchestration/orders", json=order(done_criteria=[])).status_code == 422
    assert m.post("/api/v1/orchestration/orders", json=order(capability="ba")).status_code == 422
    assert m.post("/api/v1/orchestration/orders", json=order(risk=5)).status_code == 403
    pv = m.post("/api/v1/orchestration/orders/preview", json=order()).json()
    assert pv["routing"]["executor_id"] == "dba" and pv["needs_approval"] is False and "PHIẾU GIAO VIỆC" in pv["message"]
    o = m.post("/api/v1/orchestration/orders", json=order()).json()
    assert m.get(f"/api/v1/orchestration/orders/{o['order_id']}").json()["spec"]["capability"] == "dba"
    assert m.get("/api/v1/orchestration/orders/WO-khong-co").status_code == 404
    other = client("manager", "nguoi-khac")
    assert other.post(f"/api/v1/orchestration/orders/{o['order_id']}/cancel").status_code == 403   # phiếu của người khác
    assert m.post(f"/api/v1/orchestration/orders/{o['order_id']}/retry").status_code == 409          # đang chờ giao
    assert m.post(f"/api/v1/orchestration/orders/{o['order_id']}/cancel").json()["status"] == "cancelled"
    assert client().post(f"/api/v1/orchestration/orders/{o['order_id']}/confirm", json={}).status_code == 409


async def test_ai_tools_act_as_the_served_person_and_only_managers_dispatch(llm, review, boss):
    local()
    assert assess_risk("dispatch_work_order", {}) == 2 and assess_risk("get_work_order_status", {}) == 1
    tok = CURRENT_PRINCIPAL.set("nhan-vien-khong-co")
    try:
        denied = await T.dispatch_work_order(**{k: order()[k] for k in ("objective", "capability", "deliverables", "done_criteria")})
    finally:
        CURRENT_PRINCIPAL.reset(tok)
    assert denied["success"] is False and "quản lý" in denied["error"]
    tok = CURRENT_PRINCIPAL.set(boss)
    try:
        cat = await T.list_agent_capabilities()
        assert cat["capabilities"][0]["capability"] == "dba"
        out = await T.dispatch_work_order(**{k: order()[k] for k in ("objective", "capability", "deliverables", "done_criteria")})
        assert out["success"] and "COMPLETED" in out["message"]
        await done(out["order_id"])
        st = await T.get_work_order_status(out["order_id"])
        assert st["status"] == "COMPLETED" and st["checks"][0]["ok"] is True
        bad = await T.dispatch_work_order(objective="ngắn", capability="dba", deliverables=["x"], done_criteria=["y"])
        assert bad["success"] is False and "objective" in bad["error"]
    finally:
        CURRENT_PRINCIPAL.reset(tok)
    tok = CURRENT_PRINCIPAL.set("nguoi-la")
    try:
        assert (await T.get_work_order_status(out["order_id"]))["success"] is False      # không lộ phiếu của người khác
    finally:
        CURRENT_PRINCIPAL.reset(tok)


async def test_devfleet_executor_completes_only_on_dev_fleet_verified_evidence(review, boss, monkeypatch):
    from mateai.application.devfleet import service as dfs
    dev_task = ledger.open_task("Dev thử", kind="dev_task", created_by=boss)
    payloads = []

    class Fleet:
        mode = "controlled"

        async def create_task(self, payload, **kw):
            payloads.append((payload, kw))
            ledger.transition(dev_task, ledger.EXECUTING)
            ledger.add_evidence(dev_task, source="devfleet:test", kind="FACT", summary="pytest 42 passed", verified=True)
            ledger.transition(dev_task, ledger.VERIFYING)
            ledger.transition(dev_task, ledger.COMPLETED, verification_status="passed", result_summary="Đã sửa, test đạt")
            return {"status": "dispatched", "task_id": dev_task}
    monkeypatch.setattr(dfs, "dev_fleet", Fleet())
    R.save("fleet", {"kind": "devfleet", "name": "Dev Fleet", "capabilities": ["dev"], "max_risk": 3}, "admin")
    o = await S.create(order(capability="dev", risk=3, done_criteria=[{"kind": "evidence", "min": 1}]), requested_by=boss)
    assert o["status"] == "PLANNED"                                   # Dev Fleet tự xin duyệt — không hỏi hai lần
    o = await done(o["order_id"])
    assert o["status"] == "COMPLETED" and o["verification"]["checks"][0]["kind"] == "FACT"
    p, kw = payloads[0]
    assert p["risk"] == "medium" and p["rollback"] and kw["idempotency_key"] == f"orch:{o['order_id']}:1" and kw["requested_by"] == boss


async def test_ai_review_parses_strictly_and_errors_never_pass(monkeypatch):
    from mateai.application.agent.llm_engine import llm_engine
    replies = ['```json\n{"passed": true, "missing": [], "reason": "đủ"}\n```', "đạt rồi", RuntimeError("9Router sập")]

    class Prov:
        async def complete(self, **kw):
            r = replies.pop(0)
            if isinstance(r, Exception):
                raise r
            return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=r))])

    async def ensure():
        return None
    monkeypatch.setattr(llm_engine, "_ensure_shared_client", ensure)
    monkeypatch.setattr(llm_engine, "get_provider", lambda **kw: Prov())
    o = C.parse_order(order())
    rep = C.parse_report(report())
    assert (await V._ai_review(o, rep))["passed"] is True
    assert (await V._ai_review(o, rep))["passed"] is None                          # sai định dạng
    out = await V._ai_review(o, rep)
    assert out["passed"] is None and "RuntimeError" in out["reason"]               # lỗi gọi model


async def test_local_step_waiting_for_its_own_approval_blocks_the_order(llm, review, boss):
    local()
    llm.replies["Quản trị CSDL"] = [{"reply": "Đang chờ duyệt bước tạo chỉ mục", "success": True, "requires_confirmation": True,
                                     "tool_calls_made": [{"skill": "run_local_sql_check", "result": {"status": "need_confirm"}}]}]
    o = await done((await S.create(order(), requested_by=boss))["order_id"])
    assert o["status"] == "BLOCKED" and ledger.get_task(o["task_id"])["status"] == "BLOCKED"
