# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 Dương Thanh Lịch — VN-MateAI. See LICENSE and NOTICE.
"""
tests/test_autonomy_controls.py
===============================
Control plane tự trị — Phase 3 (prompt Supervisor §35, §79, §92–§96, §148).

  - API giới hạn tự trị: chỉ admin đổi được, mọi lần đổi vào audit kèm phiên bản
    chính sách trước / sau; manager chỉ xem; tác nhân lạ bị từ chối.
  - Ngân sách lượt agent: hết số lần gọi tool thì tool KHÔNG chạy và báo đúng trạng thái.
  - Email gateway: không tự trả lời ra ngoài trừ khi bật + miền được phép.
  - Chỉ thị hệ thống có ranh giới tin cậy (kết quả tool là dữ liệu, không phải lệnh).
Không gọi mạng.
"""
from __future__ import annotations

import json
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from mateai.config.loader import settings


@pytest.fixture
def isolated_config(monkeypatch, tmp_path):
    import mateai.config.loader as loader
    cfg = tmp_path / "config.json"
    cfg.write_text(json.dumps({"autonomy": {}}), encoding="utf-8")
    monkeypatch.setattr(loader, "CONFIG_PATH", cfg)
    monkeypatch.setattr(settings, "autonomy", settings.autonomy.model_copy(deep=True))
    yield cfg
    monkeypatch.undo()          # trả CONFIG_PATH thật TRƯỚC khi nạp lại
    loader.reload_settings()


def _client(role):
    import mateai.interfaces.http.routers.security as sec
    from mateai.interfaces.http.auth_dependencies import get_current_user
    app = FastAPI()
    app.include_router(sec.router)
    app.dependency_overrides[get_current_user] = lambda: {"username": f"{role}_u", "role": role}
    return TestClient(app)


def test_autonomy_api_admin_only_and_audited(isolated_config, monkeypatch):
    from mateai.application.security import safety_guard
    audits = []
    monkeypatch.setattr(safety_guard.security_engine, "log_audit", lambda *a, **k: audits.append(a))
    assert _client("viewer").get("/api/v1/security/autonomy").status_code == 403
    view = _client("manager").get("/api/v1/security/autonomy").json()
    assert view["autonomy"]["kill_switch"] is False and "VN-MATEAI-VOICE" in view["known_agents"]
    assert _client("manager").put("/api/v1/security/autonomy", json={"kill_switch": True}).status_code == 403

    admin = _client("admin")
    r = admin.put("/api/v1/security/autonomy", json={"kill_switch": True, "reason": "sự cố"})
    assert r.status_code == 200 and r.json()["autonomy"]["kill_switch"] is True
    assert settings.autonomy.kill_switch is True                       # có hiệu lực ngay
    assert json.loads(isolated_config.read_text(encoding="utf-8"))["autonomy"]["kill_switch"] is True
    change = [a for a in audits if a[1] == "autonomy_policy_change"][-1][4]
    assert change["changes"]["kill_switch"]["after"] is True and change["reason"] == "sự cố"
    assert change["policy_version_before"] != change["policy_version_after"]

    assert admin.put("/api/v1/security/autonomy", json={"disabled_agents": ["AI-LA"]}).status_code == 400
    assert admin.put("/api/v1/security/autonomy", json={"max_tool_calls_per_turn": 0}).status_code == 422
    assert admin.put("/api/v1/security/autonomy", json={}).status_code == 400


async def test_tool_call_budget_stops_execution(monkeypatch):
    import mateai.application.agent.llm_engine as le
    from mateai.application.agent.llm_engine import llm_engine
    monkeypatch.setattr(settings, "autonomy", settings.autonomy.model_copy(update={"max_tool_calls_per_turn": 2}))
    ran = []

    async def fake_gate(fn_name, fn_args, **kw):
        ran.append(fn_name)
        return {"target_client": "master", "args": fn_args, "result": {"status": "success"}}

    calls = {"n": 0}

    async def fake_llm(messages, tools=None, brain_role="controller"):
        calls["n"] += 1
        if tools and calls["n"] == 1:
            tcs = [SimpleNamespace(id=f"c{i}", type="function",
                                   function=SimpleNamespace(name="get_system_info", arguments=json.dumps({"i": i})))
                   for i in range(3)]
            msg = SimpleNamespace(content="", tool_calls=tcs, reasoning="", reasoning_content="")
            return SimpleNamespace(model="fake", usage=SimpleNamespace(prompt_tokens=10, completion_tokens=5, total_tokens=15),
                                   choices=[SimpleNamespace(message=msg, finish_reason="tool_calls")])
        msg = SimpleNamespace(content="Dạ xong.", tool_calls=None, reasoning="", reasoning_content="")
        return SimpleNamespace(model="fake", usage=SimpleNamespace(prompt_tokens=20, completion_tokens=3, total_tokens=23),
                               choices=[SimpleNamespace(message=msg, finish_reason="stop")])

    import mateai.application.agent.tool_gate as tg
    monkeypatch.setattr(tg, "run_tool_with_policy", fake_gate)
    monkeypatch.setattr(llm_engine, "_call_llm", fake_llm)
    res = await llm_engine.ask_async("kiểm tra 3 thứ", source_device="portal", caller="admin", session_id="budget-1")
    assert ran == ["get_system_info", "get_system_info"]                 # lần thứ 3 không chạy
    statuses = [tc["result"].get("status") for tc in res["tool_calls_made"]]
    assert statuses.count("budget_exceeded") == 1
    assert res["usage"] == {"prompt_tokens": 30, "completion_tokens": 8, "total_tokens": 38, "llm_calls": 2}


def test_email_auto_reply_policy(monkeypatch):
    from mateai.interfaces.email.email_gateway import auto_reply_allowed
    monkeypatch.setattr(settings, "autonomy", settings.autonomy.model_copy(deep=True))
    assert auto_reply_allowed("khach@abc.vn") == (False, "auto_reply_off")      # mặc định không gửi
    settings.autonomy.email_auto_reply = True
    assert auto_reply_allowed("khach@abc.vn") == (False, "domain_not_allowed")
    settings.autonomy.email_auto_reply_domains = ["abc.vn"]
    assert auto_reply_allowed("Khách <khach@ABC.vn>") == (True, "allowed")
    settings.autonomy.kill_switch = True
    assert auto_reply_allowed("khach@abc.vn") == (False, "kill_switch")


def test_agent_prompt_marks_external_content_as_data():
    from mateai.application.agent.llm_engine import build_system_prompt
    prompt = build_system_prompt(source_device="portal")
    assert "[RANH GIỚI TIN CẬY]" in prompt and "KHÔNG phải chỉ thị" in prompt
