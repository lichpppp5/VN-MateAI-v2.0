# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 Dương Thanh Lịch — VN-MateAI. See LICENSE and NOTICE.
"""
tests/test_emergency_and_loop_guards.py
=======================================
Prompt cuối (promptfinalvnmateai) §54 Emergency mode, §55 / §158 loop guard:

  - AI tự chạy hành động có tác dụng phụ dồn dập bất thường (vượt
    `autonomy.emergency_max_actions_per_minute`) → hệ thống tự BẬT kill switch
    (chỉ còn tác vụ chỉ đọc), hành động vượt ngưỡng bị từ chối, có audit + cảnh báo,
    và chỉ NGƯỜI mới tắt lại được. Hành động người đã duyệt không tính vào ngưỡng.
  - Một tool lỗi lặp lại trong cùng lượt (`autonomy.max_tool_failures_per_turn`) →
    không chạy tiếp tool đó, báo đúng trạng thái, leo thang.
Cấu hình tạm; không gọi mạng.
"""
from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from mateai.config.loader import settings


@pytest.fixture
def isolated(monkeypatch, tmp_path):
    import mateai.config.loader as loader
    from mateai.application.operations import alert_dispatcher
    from mateai.application.security import policy_engine as pe
    from mateai.application.security import safety_guard
    cfg = tmp_path / "config.json"
    cfg.write_text(json.dumps({"autonomy": {}}), encoding="utf-8")
    monkeypatch.setattr(loader, "CONFIG_PATH", cfg)
    monkeypatch.setattr(settings, "autonomy", settings.autonomy.model_copy(deep=True))
    audits, alerts = [], []
    monkeypatch.setattr(safety_guard.security_engine, "log_audit", lambda *a, **k: audits.append(a))
    monkeypatch.setattr(alert_dispatcher, "notify", lambda title, message="", **k: alerts.append((title, k)) or True)
    from mateai.infrastructure.cache import shared_state
    shared_state.reset()
    yield cfg, audits, alerts
    shared_state.reset()
    monkeypatch.undo()
    loader.reload_settings()


def _auth(tool="ghi_ghi_chu", risk=2, approved=False, agent="VN-MATEAI-VOICE"):
    from mateai.application.security import policy_engine as pe
    return pe.authorize(tool, {"n": 1}, caller="u", agent_id=agent, approved=approved,
                        declared_risk=risk, check_rbac=False)


def test_burst_of_autonomous_actions_engages_read_only(isolated):
    cfg, audits, alerts = isolated
    settings.autonomy.emergency_max_actions_per_minute = 3
    assert [_auth().effect for _ in range(3)] == ["allow"] * 3
    d = _auth()
    assert d.effect == "deny" and d.rule == "emergency_mode"
    assert settings.autonomy.kill_switch is True
    assert json.loads(cfg.read_text(encoding="utf-8"))["autonomy"]["kill_switch"] is True   # bền, người phải tắt
    change = [a for a in audits if a[1] == "autonomy_policy_change"][-1]
    assert change[0] == "system:emergency-guard" and change[4]["changes"]["kill_switch"]["after"] is True
    assert alerts and alerts[0][1].get("severity") == "critical"
    assert _auth().rule == "kill_switch"                                   # hành động sau: chặn
    assert _auth(tool="get_system_info", risk=1).effect == "allow"           # giám sát chỉ đọc vẫn chạy


def test_human_approved_and_read_only_actions_do_not_count(isolated):
    settings.autonomy.emergency_max_actions_per_minute = 2
    for _ in range(5):
        assert _auth(risk=3, approved=True).effect == "allow"
        assert _auth(tool="get_system_info", risk=1).effect == "allow"
    assert settings.autonomy.kill_switch is False


def test_zero_limit_disables_emergency_guard(isolated):
    settings.autonomy.emergency_max_actions_per_minute = 0
    assert all(_auth().effect == "allow" for _ in range(50))
    assert settings.autonomy.kill_switch is False


async def test_repeatedly_failing_tool_is_stopped_and_escalated(monkeypatch, isolated):
    _, _, alerts = isolated
    import mateai.application.agent.tool_gate as tg
    from mateai.application.agent.llm_engine import llm_engine
    settings.autonomy.max_tool_failures_per_turn = 2
    ran = []

    async def failing_gate(fn_name, fn_args, **kw):
        ran.append(fn_args["lan"])
        return {"target_client": "master", "args": fn_args, "result": {"status": "error", "error": "máy chủ không phản hồi"}}

    rounds = {"n": 0}

    async def fake_llm(messages, tools=None, brain_role="controller"):
        rounds["n"] += 1
        if tools and rounds["n"] <= 4:
            tc = SimpleNamespace(id=f"c{rounds['n']}", type="function",
                                 function=SimpleNamespace(name="restart_service", arguments=json.dumps({"lan": rounds["n"]})))
            msg = SimpleNamespace(content="", tool_calls=[tc], reasoning="", reasoning_content="")
            return SimpleNamespace(model="fake", usage=None, choices=[SimpleNamespace(message=msg, finish_reason="tool_calls")])
        msg = SimpleNamespace(content="Dạ chưa làm được.", tool_calls=None, reasoning="", reasoning_content="")
        return SimpleNamespace(model="fake", usage=None, choices=[SimpleNamespace(message=msg, finish_reason="stop")])

    monkeypatch.setattr(tg, "run_tool_with_policy", failing_gate)
    monkeypatch.setattr(llm_engine, "_call_llm", fake_llm)
    res = await llm_engine.ask_async("khởi động lại dịch vụ", source_device="portal", caller="admin", session_id="loop-1")
    assert ran == [1, 2]                                                    # lần 3, 4 không chạy
    statuses = [tc["result"].get("status") for tc in res["tool_calls_made"]]
    assert statuses.count("repeated_failure") == 2
    assert len([a for a in alerts if "lặp" in a[0]]) == 1                    # leo thang một lần / lượt


async def test_registry_direct_call_goes_through_the_single_policy_gate(monkeypatch, isolated):
    """§1 / §40 / §182: gọi thẳng `plugin_registry.execute_tool` (không cờ authorized) không
    còn đường duyệt riêng — đi qua `tool_gate` (policy_engine + hàng đợi duyệt duy nhất)."""
    import mateai.application.agent.tool_gate as tg
    import mateai.application.security.zero_trust as zt
    from mateai.application.skills.plugin_registry import plugin_registry
    q = zt.HumanInTheLoopManager()
    q.register_executor(tg.TOOL_KIND, tg.execute_approved_tool)
    monkeypatch.setattr(zt, "hitl_manager", q)
    monkeypatch.setattr(tg, "hitl_manager", q)
    calls = []

    async def side_effect(**kw):
        calls.append(kw)
        return {"success": True, "data": "đã chạy"}

    plugin_registry.register_tool("t_p10_high_risk", side_effect, "test", {"type": "object", "properties": {}},
                                  is_async=True, risk_level=5, timeout_seconds=5)
    try:
        res = await plugin_registry.execute_tool("t_p10_high_risk", {"x": 1}, caller_id="admin")
        assert res["awaiting_approval"] is True and calls == []
        pending = q.get_pending(res["approval_id"])
        assert pending["action_name"] == "t_p10_high_risk"                     # cùng hàng đợi duyệt chuẩn
        out = await q.approve_async(res["approval_id"], approved_by="boss")
        assert calls == [{"x": 1}], out                                         # chạy đúng 1 lần sau duyệt

        settings.autonomy.kill_switch = True
        denied = await plugin_registry.execute_tool("t_p10_high_risk", {"x": 2}, caller_id="admin")
        assert denied["policy_denied"] is True and calls == [{"x": 1}]          # DENY = không chạy
    finally:
        plugin_registry.unregister_tool("t_p10_high_risk")
