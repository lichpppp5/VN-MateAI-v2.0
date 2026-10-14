# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 Dương Thanh Lịch — VN-MateAI. See LICENSE and NOTICE.
"""
tests/test_task_ledger.py
=========================
Sổ tác vụ + kiểm chứng + bằng chứng (prompt Supervisor §21–§30, §122, §151, §153).

Bất biến:
  - không tác vụ nào COMPLETED khi kiểm chứng chưa đạt;
  - tool thất bại (kể cả thất bại nằm trong lớp bọc "success") -> FAILED, không báo xong;
  - tool rủi ro cao không có bộ kiểm chứng -> ESCALATED (người xác nhận), không báo xong;
  - bị chính sách từ chối -> BLOCKED; chờ duyệt -> WAITING_AUTHORIZATION;
  - sự cố của sentinel -> một tác vụ incident đang mở cho mỗi nguồn, không tự xử lý.
Không gọi mạng; DB tạm của conftest.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from types import SimpleNamespace

import pytest

import mateai.application.agent.tool_gate as tg
from mateai.application.tasks import ledger
from mateai.application.tasks.verification import verify
from mateai.config.loader import settings


@pytest.fixture
def gate_env(monkeypatch):
    import core.plugin_manager as pm
    import mateai.application.security.security_guard as sg
    import mateai.application.skills.plugin_registry as pr
    out = {"result": {"success": True, "data": {"status": "success"}, "error": None}}

    async def fake_exec(name, args):
        return out["result"]

    monkeypatch.setattr(sg.security_guard, "check_permission", lambda **kw: (True, "ok"))
    monkeypatch.setattr(pm.plugin_manager, "execute_skill", fake_exec)
    monkeypatch.setattr(pr.plugin_registry, "get_tool_names", lambda: [])
    monkeypatch.setattr(tg.hitl_manager, "request_approval", lambda **kw: {"id": "HITL-L"})
    monkeypatch.setattr(settings, "autonomy", settings.autonomy.model_copy(deep=True))
    return out


async def _turn(*calls, **kw):
    tid = ledger.open_task("lượt thử", created_by="boss", agent_id="VN-MATEAI-VOICE", status=ledger.EXECUTING)
    token = ledger.CURRENT_TASK.set(tid)
    try:
        for name, args in calls:
            await tg.run_tool_with_policy(name, args, caller="boss", source_device="portal", **kw)
    finally:
        ledger.CURRENT_TASK.reset(token)
    return tid, ledger.settle(tid)


def test_state_machine_rejects_invalid_and_unverified_completion():
    tid = ledger.open_task("x")
    with pytest.raises(ledger.InvalidTransition):
        ledger.transition(tid, ledger.COMPLETED, verification_status="passed")   # NEW -> COMPLETED
    ledger.transition(tid, ledger.EXECUTING)
    ledger.transition(tid, ledger.VERIFYING)
    with pytest.raises(ledger.InvalidTransition):
        ledger.transition(tid, ledger.COMPLETED)                                  # chưa kiểm chứng
    ledger.transition(tid, ledger.COMPLETED, verification_status="passed")
    with pytest.raises(ledger.InvalidTransition):
        ledger.transition(tid, ledger.EXECUTING)                                  # đã kết thúc


async def test_low_risk_success_completes_with_evidence(gate_env):
    tid, status = await _turn(("get_system_info", {}))
    task = ledger.get_task(tid)
    assert status == "COMPLETED" and task["verification_status"] == "passed"
    st = task["steps"][0]
    assert st["decision"] == "allow" and st["verification_level"] == "NONE" and st["policy_version"]
    assert task["evidence"] and task["evidence"][0]["kind"] == "FACT"


async def test_wrapped_tool_failure_is_failed_not_completed(gate_env):
    gate_env["result"] = {"success": True, "error": None,
                          "data": {"success": False, "error": "Không tìm thấy tiến trình với PID=999999."}}
    tid, status = await _turn(("get_system_info", {}))
    task = ledger.get_task(tid)
    assert status == "FAILED" and "PID=999999" in task["result_summary"]


async def test_denied_and_pending_states(gate_env):
    _, status = await _turn(("drop_database", {}))
    assert status == "BLOCKED"
    tid, status = await _turn(("restart_service", {"name": "x"}))
    task = ledger.get_task(tid)
    assert status == "WAITING_AUTHORIZATION" and task["approval_id"] == "HITL-L"


async def test_high_risk_without_verifier_escalates_then_human_confirms(gate_env):
    tid, status = await _turn(("restart_service", {"name": "spooler"}), approved=True)
    assert status == "ESCALATED"
    assert ledger.get_task(tid)["verification_status"] == "not_verifiable"
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    import mateai.interfaces.http.routers.tasks as tasks
    from mateai.interfaces.http.auth_dependencies import get_current_user
    app = FastAPI()
    app.include_router(tasks.router)
    app.dependency_overrides[get_current_user] = lambda: {"username": "boss", "role": "admin"}
    c = TestClient(app)
    assert c.get(f"/api/v1/ops/tasks/{tid}").json()["task"]["steps"][0]["tool"] == "restart_service"
    r = c.post(f"/api/v1/ops/tasks/{tid}/confirm", json={"note": "đã kiểm tra dịch vụ chạy"})
    assert r.status_code == 200 and r.json()["task"]["status"] == "COMPLETED"
    assert any(e["source"] == "human:boss" for e in r.json()["task"]["evidence"])
    assert c.post(f"/api/v1/ops/tasks/{tid}/confirm", json={}).status_code == 409
    listing = c.get("/api/v1/ops/tasks?days=1").json()
    assert listing["by_status"].get("COMPLETED", 0) >= 1


def test_standard_verifiers_read_real_state(tmp_path, monkeypatch):
    ok = {"status": "success"}
    assert verify("kill_process", {"pid": 2 ** 22 + 12345}, ok, 4)["status"] == "passed"   # PID không còn
    assert verify("kill_process", {"pid": os.getpid()}, ok, 4)["status"] == "failed"      # vẫn sống
    target = Path(settings.PROJECT_ROOT) / "reports" / "_verify_probe.txt"
    try:
        target.parent.mkdir(exist_ok=True)
        target.write_text("xin chào", encoding="utf-8")
        args = {"file_path": "reports/_verify_probe.txt", "content": "xin chào"}
        assert verify("write_file", args, ok, 3)["status"] == "passed"
        assert verify("write_file", {**args, "content": "khác"}, ok, 3)["status"] == "failed"
    finally:
        target.unlink(missing_ok=True)
    assert verify("kill_process", {"pid": 1}, ok, 4, target="pc-01")["status"] == "not_verifiable"
    assert verify("x", {}, {"status": "need_confirm"}, 4)["status"] == "pending"


def test_sentinel_incident_is_one_open_task_per_source():
    a = ledger.open_incident("cpu", "CPU cao", "CPU 97% trong 5 phút")
    b = ledger.open_incident("cpu", "CPU cao", "CPU 98%")
    task = ledger.get_task(a)
    assert a == b and task["kind"] == "incident" and task["status"] == "ESCALATED"
    assert task["priority"] == "CRITICAL" and len(task["evidence"]) == 2 and task["steps"] == []


async def test_agent_turn_opens_and_settles_a_task(monkeypatch):
    from mateai.application.agent.llm_engine import llm_engine

    async def fake_gate(fn_name, fn_args, **kw):
        tid = ledger.CURRENT_TASK.get()
        ledger.record_step(tid, tool=fn_name, target="master", args=fn_args,
                           decision=SimpleNamespace(effect="allow", rule="auto", policy_version="v", risk=1, level="L0"),
                           result={"status": "success"}, verification=verify(fn_name, fn_args, {"status": "success"}, 1))
        return {"target_client": "master", "args": fn_args, "result": {"status": "success"}}

    n = {"i": 0}

    async def fake_llm(messages, tools=None, brain_role="controller"):
        n["i"] += 1
        if n["i"] == 1:
            tc = SimpleNamespace(id="c1", type="function",
                                 function=SimpleNamespace(name="get_system_info", arguments=json.dumps({})))
            msg = SimpleNamespace(content="", tool_calls=[tc], reasoning="", reasoning_content="")
            return SimpleNamespace(model="f", choices=[SimpleNamespace(message=msg, finish_reason="tool_calls")])
        msg = SimpleNamespace(content="Dạ, máy chủ ổn.", tool_calls=None, reasoning="", reasoning_content="")
        return SimpleNamespace(model="f", choices=[SimpleNamespace(message=msg, finish_reason="stop")])

    monkeypatch.setattr(tg, "run_tool_with_policy", fake_gate)
    monkeypatch.setattr(llm_engine, "_call_llm", fake_llm)
    res = await llm_engine.ask_async("kiểm tra máy chủ", source_device="portal", caller="admin", session_id="led-1")
    assert res["op_task"]["status"] == "COMPLETED"
    task = ledger.get_task(res["op_task"]["task_id"])
    assert task["agent_id"] == "VN-MATEAI-VOICE" and task["created_by"] == "admin" and len(task["steps"]) == 1
    assert ledger.CURRENT_TASK.get() is None            # không lan sang lượt sau
