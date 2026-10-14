# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 Dương Thanh Lịch — VN-MateAI. See LICENSE and NOTICE.
"""
tests/test_confirm_pending_action.py
====================================
Luồng "Đồng ý" tiếp tục một tác vụ đang chờ phê duyệt (LLMEngine.ask_async).

Trước Phase 6, tác vụ được duyệt chạy bằng
`asyncio.to_thread(plugin_manager.execute_skill, ...)` — nhưng execute_skill là
hàm async, nên to_thread trả về một coroutine chưa chạy và dòng sau gọi .get()
trên nó: tác vụ đã duyệt KHÔNG BAO GIỜ chạy. Nay tác vụ đi qua cổng chung
run_tool_with_policy (RBAC + audit), kèm approved=True, qua hàng đợi HITL duy
nhất. Không gọi mạng, không gửi Telegram.
"""
from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from mateai.application.agent.llm_engine import llm_engine  # noqa: E402
import pytest  # noqa: E402

import mateai.application.security.zero_trust as zt  # noqa: E402


@pytest.fixture
def queue(monkeypatch):
    import mateai.application.agent.tool_gate as avl
    q = zt.HumanInTheLoopManager()
    q.register_executor(avl.TOOL_KIND, avl.execute_approved_tool)
    monkeypatch.setattr(zt, "hitl_manager", q)
    monkeypatch.setattr(avl, "hitl_manager", q)
    return q


def _queue_tool(q, caller, tool, params, query, source_device):
    return q.request_approval(action_name=tool, params=params, requested_by=caller, kind="tool",
                              context={"target_client": "master", "query": query, "source_device": source_device})


async def test_confirm_runs_the_approved_tool_through_the_gate(monkeypatch, queue):
    import mateai.application.agent.tool_gate as avl

    gate_calls = []

    async def fake_gate(fn_name, fn_args, **kw):
        gate_calls.append((fn_name, dict(fn_args), kw))
        return {"target_client": "master", "args": fn_args,
                "result": {"success": True, "data": {"status": "ok"}, "error": None}}

    async def fake_call_llm(messages, tools=None, brain_role="controller"):
        msg = SimpleNamespace(content="Dạ, em đã dừng tiến trình theo yêu cầu.", tool_calls=None,
                              reasoning="", reasoning_content="")
        return SimpleNamespace(model="fake", choices=[SimpleNamespace(message=msg, finish_reason="stop")])

    monkeypatch.setattr(avl, "run_tool_with_policy", fake_gate)
    monkeypatch.setattr(llm_engine, "_call_llm", fake_call_llm)

    caller = "test-confirm-device"
    _queue_tool(queue, caller, "kill_process", {"pid": 4242}, "dừng tiến trình 4242", caller)
    res = await llm_engine.ask_async("đồng ý", source_device=caller, session_id="t-confirm")

    assert gate_calls, f"tác vụ đã duyệt không được thực thi: {res}"
    name, args, kw = gate_calls[0]
    assert name == "kill_process" and args["pid"] == 4242 and kw["approved"] is True
    assert kw["caller"] == caller


async def test_confirm_finds_action_saved_under_logged_in_caller(monkeypatch, queue):
    """Portal: cổng lưu pending theo username (caller), source_device="portal".

    Trước đây ask_async tra pending theo source_device nên "Đồng ý" không bao
    giờ tìm thấy tác vụ của người dùng đã đăng nhập.
    """
    import mateai.application.agent.tool_gate as avl

    gate_calls = []

    async def fake_gate(fn_name, fn_args, **kw):
        gate_calls.append((fn_name, kw))
        return {"target_client": "master", "args": fn_args,
                "result": {"success": True, "data": {}, "error": None}}

    async def fake_call_llm(messages, tools=None, brain_role="controller"):
        msg = SimpleNamespace(content="Dạ xong.", tool_calls=None, reasoning="", reasoning_content="")
        return SimpleNamespace(model="fake", choices=[SimpleNamespace(message=msg, finish_reason="stop")])

    monkeypatch.setattr(avl, "run_tool_with_policy", fake_gate)
    monkeypatch.setattr(llm_engine, "_call_llm", fake_call_llm)

    _queue_tool(queue, "alice-portal", "kill_process", {"pid": 7}, "dừng 7", "portal")
    await llm_engine.ask_async("đồng ý", source_device="portal", session_id="t-c2",
                               caller="alice-portal")
    assert gate_calls and gate_calls[0][0] == "kill_process"
    assert gate_calls[0][1]["caller"] == "alice-portal"


async def test_rest_voice_command_uses_logged_in_user_not_source_device(monkeypatch):
    """source_device do client tự khai ("hud") không được quyết định RBAC."""
    import mateai.interfaces.http.routers.voice as voice
    from mateai.interfaces.http import hud_voice

    import mateai.application.voice.voice_turn as vt
    seen = {}

    async def fake_turn(query, **kw):
        seen.update(kw)
        return vt.VoiceTurnResult(reply_text="ok", display_text="ok")

    async def _noop(*_a, **_k):
        return None

    # REST dùng chung lõi thoại (realtime P5) — danh tính RBAC truyền vào đó.
    monkeypatch.setattr(vt, "process_voice_turn", fake_turn)
    monkeypatch.setattr(voice, "broadcast_hud", _noop)
    monkeypatch.setattr(hud_voice, "broadcast_thinking", _noop)

    payload = voice.VoiceCommandRequest(query="tắt máy", source_device="hud", include_audio=False)
    await voice.voice_command(payload, user={"username": "bob-manager", "role": "manager"})
    assert seen["caller"] == "bob-manager"
    assert seen["source_device"] == "hud"


async def test_reject_in_chat_cancels_without_running(monkeypatch, queue):
    import mateai.application.agent.tool_gate as avl
    ran = []

    async def fake_gate(fn_name, fn_args, **kw):
        ran.append(fn_name)
        return {"target_client": "master", "args": fn_args, "result": {}}

    monkeypatch.setattr(avl, "run_tool_with_policy", fake_gate)
    req = _queue_tool(queue, "carol", "delete_item", {"path": "x"}, "xoá x", "web-widget")
    res = await llm_engine.ask_async("hủy", source_device="web-widget", session_id="t-rej", caller="carol")
    assert ran == [] and queue.get_pending(req["id"]) is None
    assert "hủy" in res["reply"].lower()


def test_completed_action_memory_is_per_user():
    """Trước đây không khớp người hỏi thì trả tác vụ (kèm kết quả) của người khác."""
    from mateai.application.agent.state_manager import StateManager
    sm = StateManager()
    sm.record_completed_action({"id": "1", "tool_name": "read_file", "requested_by": "alice",
                                "source_device": "portal", "arguments": {}}, {"secret": 1})
    assert sm.get_recent_completed_action("alice")["id"] == "1"
    assert sm.get_recent_completed_action("bob") is None
    assert sm.get_recent_completed_action(None) is None
