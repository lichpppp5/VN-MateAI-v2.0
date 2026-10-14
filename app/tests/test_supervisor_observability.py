# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 Dương Thanh Lịch — VN-MateAI. See LICENSE and NOTICE.
"""
tests/test_supervisor_observability.py
======================================
Phase 7–9 của prompt Supervisor:
  - trace thoại lưu bền, nạp lại sau khởi động (trước: chỉ RAM — /api/v1/voice/metrics
    trống sau mỗi lần khởi động lại, đã gặp ngày 2026-10-05);
  - bảng giám sát AI Supervisor: số liệu thật từ sổ tác vụ / cổng chính sách;
  - giao việc có Idempotency-Key: gửi lại cùng khoá không bật popup lần hai;
  - tắt máy dừng cả luồng đôn đốc, email gateway và đóng pool HTTP.
Không gọi mạng; DB tạm của conftest.
"""
from __future__ import annotations

from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient

import mateai.application.voice.voice_turn as vt
from mateai.application.tasks import ledger


def _client(router, role="admin"):
    from mateai.interfaces.http.auth_dependencies import get_current_user
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_current_user] = lambda: {"username": f"{role}_u", "role": role}
    return TestClient(app)


def test_voice_traces_survive_restart(monkeypatch):
    trace = vt.VoiceTurnTrace(session_id="s-persist", channel="portal")
    trace.mark("first_status")
    data = trace.finish("llm")
    # Giả lập khởi động lại: bộ đệm RAM trống, chưa nạp.
    monkeypatch.setattr(vt, "_RECENT_TRACES", vt.deque(maxlen=500))
    monkeypatch.setattr(vt, "_TRACES_LOADED", False)
    ids = [t["trace_id"] for t in vt.recent_traces(500)]
    assert data["trace_id"] in ids
    assert vt.trace_stats()["turns"] >= 1


def test_supervisor_overview_counts_real_ledger_data():
    import mateai.interfaces.http.routers.tasks as tasks
    ok = ledger.open_task("đọc CPU", agent_id="VN-MATEAI-VOICE", status=ledger.EXECUTING)
    ledger.record_step(ok, tool="get_system_info", target="master", args={},
                       decision=SimpleNamespace(effect="allow", rule="auto", policy_version="v", risk=1, level="L0"),
                       result={"status": "success"}, verification={"level": "NONE", "status": "passed", "checks": ["ok"]})
    ledger.add_usage(ok, {"llm_calls": 2, "total_tokens": 321})
    ledger.settle(ok)
    bad = ledger.open_task("xoá db", agent_id="VN-MATEAI-VOICE", status=ledger.EXECUTING)
    ledger.record_step(bad, tool="drop_database", target="master", args={},
                       decision=SimpleNamespace(effect="deny", rule="never_autonomous", policy_version="v", risk=5, level="L5"),
                       result={"status": "denied"}, verification={"level": "NONE", "status": "failed", "checks": ["L5"]})
    ledger.settle(bad)
    d = _client(tasks.router).get("/api/v1/ops/overview?hours=1").json()
    assert d["tasks"]["by_status"].get("COMPLETED", 0) >= 1 and d["tasks"]["by_status"].get("BLOCKED", 0) >= 1
    assert d["actions"]["by_decision"].get("deny", 0) >= 1 and d["actions"]["denial_rate"] is not None
    assert d["cost"]["total_tokens"] >= 321
    # Không bịa tiền: tác vụ này không có giá (không by_model) -> không góp tiền; tổng tiền (nếu có)
    # đúng bằng phần đã ghi từ model có giá trong cùng khung giờ.
    assert ledger.get_task(ok)["llm_cost"] is None
    from datetime import datetime, timedelta
    from mateai.infrastructure.database.db_manager import db_manager
    recorded = db_manager.op_stats((datetime.now() - timedelta(hours=1)).strftime("%Y-%m-%d %H:%M:%S"))["llm_cost"]
    assert d["cost"]["currency_cost"] == (None if recorded is None else round(float(recorded), 4))
    assert "kill_switch" in d["ai_status"] and "policy_version" in d["ai_status"]
    # Danh sách "Cần chú ý" cắt ở 20 mục mới nhất; tổng số thật đi kèm để giao diện nói rõ phần bị cắt.
    assert d["tasks"]["attention_total"] >= len(d["tasks"]["attention"])
    assert len(d["tasks"]["attention"]) <= 20
    assert _client(tasks.router, "viewer").get("/api/v1/ops/overview").status_code == 403


def test_task_dispatch_idempotency_key(monkeypatch):
    import mateai.interfaces.http.routers.tasks as tasks
    from mateai.application.devices.task_manager import task_manager
    sent = []

    async def fake_dispatch(**kw):
        sent.append(kw["client_id"])
        return {"status": "success", "task_id": f"task_{len(sent)}", "client_id": kw["client_id"]}

    monkeypatch.setattr(task_manager, "dispatch_task", fake_dispatch)
    monkeypatch.setattr(tasks, "_IDEMPOTENT", {})
    c = _client(tasks.router, "manager")
    body = {"client_id": "pc-01", "message": "Nộp báo cáo"}
    first = c.post("/api/v1/tasks/send", json=body, headers={"Idempotency-Key": "k-1"}).json()
    again = c.post("/api/v1/tasks/send", json=body, headers={"Idempotency-Key": "k-1"}).json()
    assert sent == ["pc-01"] and again["idempotent_replay"] is True and again["task_id"] == first["task_id"]
    c.post("/api/v1/tasks/send", json=body, headers={"Idempotency-Key": "k-2"})
    c.post("/api/v1/tasks/send", json=body)                                   # không khoá: gửi như thường
    assert sent == ["pc-01", "pc-01", "pc-01"]


async def test_shutdown_stops_background_services_and_pools(monkeypatch):
    import mateai.interfaces.http.server as server
    from mateai.application.skills.builtin.proactive_manager import proactive_manager
    from mateai.infrastructure.http.connection_pool import connection_pool_manager
    from mateai.interfaces.email.email_gateway import email_gateway
    calls = []
    monkeypatch.setattr(proactive_manager, "stop", lambda: calls.append("proactive"))
    monkeypatch.setattr(email_gateway, "stop", lambda: calls.append("email"))

    async def close_all():
        calls.append("pools")

    monkeypatch.setattr(connection_pool_manager, "close_all", close_all)
    # Không dừng worker manager THẬT (singleton) — test chạy sau sẽ không submit được.
    from mateai.application.operations.background_workers import background_worker_manager
    from mateai.interfaces.http import lifecycle

    async def stop_workers(timeout=10.0):
        calls.append("workers")

    monkeypatch.setattr(background_worker_manager, "stop", stop_workers)
    monkeypatch.setattr(lifecycle, "STATE", lifecycle.LifecycleState())
    await server._on_shutdown()
    await server._on_shutdown()          # listener thứ hai: không dừng lại lần nữa
    assert sorted(calls) == ["email", "pools", "proactive", "workers"]
