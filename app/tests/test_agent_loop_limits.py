# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 Dương Thanh Lịch — VN-MateAI. See LICENSE and NOTICE.
"""
tests/test_agent_loop_limits.py
===============================
Vòng agent không được kết thúc bằng "đã đạt giới hạn vòng lặp xử lý" mà không
có câu trả lời.

Trước đây: model gọi tool đủ 4 vòng → trả câu cố định, bỏ hết kết quả tool đã
thu được. Và model gọi lại đúng tool + tham số cũ là một nguyên nhân hay gặp
làm hết vòng. Không gọi mạng, không chạy tool thật.
"""
from __future__ import annotations

import json
from types import SimpleNamespace

import mateai.application.agent.llm_engine as le
from mateai.application.agent.llm_engine import llm_engine


def _resp(content="", tool_calls=None):
    msg = SimpleNamespace(content=content, tool_calls=tool_calls, reasoning="", reasoning_content="")
    return SimpleNamespace(model="fake", choices=[SimpleNamespace(
        message=msg, finish_reason="tool_calls" if tool_calls else "stop")])


def _call(name, args, i):
    return SimpleNamespace(id=f"call_{i}", type="function",
                           function=SimpleNamespace(name=name, arguments=json.dumps(args)))


def _patch_gate(monkeypatch, ran):
    import mateai.application.agent.tool_gate as tg

    async def fake_gate(fn_name, fn_args, **kw):
        ran.append((fn_name, fn_args))
        return {"target_client": "master", "args": fn_args,
                "result": {"success": True, "data": {"status": "success", "value": len(ran)}, "error": None}}

    monkeypatch.setattr(tg, "run_tool_with_policy", fake_gate)


async def test_exhausted_rounds_still_produce_an_answer(monkeypatch):
    ran, calls = [], []

    async def fake_call_llm(messages, tools=None, **kw):
        calls.append(tools)
        if tools is None:  # lần tổng hợp cuối — không kèm tool
            return _resp("Dạ, CPU đang ở mức 30%.")
        return _resp(tool_calls=[_call("get_system_info", {"i": len(calls)}, len(calls))])

    _patch_gate(monkeypatch, ran)
    monkeypatch.setattr(llm_engine, "_call_llm", fake_call_llm)
    res = await llm_engine.ask_async("kiểm tra CPU", source_device="portal", caller="admin", session_id="lim1")
    assert len(ran) == le.MAX_TOOL_ROUNDS
    assert calls[-1] is None, "lần cuối phải gọi KHÔNG kèm tool"
    assert res["reply"] == "Dạ, CPU đang ở mức 30%."
    assert "giới hạn vòng lặp" not in res["reply"]


async def test_exhausted_rounds_fallback_when_synthesis_fails(monkeypatch):
    ran = []

    async def fake_call_llm(messages, tools=None, **kw):
        if tools is None:
            raise RuntimeError("model lỗi")
        return _resp(tool_calls=[_call("get_system_info", {"n": len(ran)}, len(ran))])

    _patch_gate(monkeypatch, ran)
    monkeypatch.setattr(llm_engine, "_call_llm", fake_call_llm)
    res = await llm_engine.ask_async("kiểm tra CPU", source_device="portal", caller="admin", session_id="lim2")
    assert "get_system_info" in res["reply"] and "giới hạn vòng lặp" not in res["reply"]


async def test_identical_tool_call_is_not_executed_twice(monkeypatch):
    ran, script = [], iter([
        _resp(tool_calls=[_call("get_system_info", {"x": 1}, 1)]),
        _resp(tool_calls=[_call("get_system_info", {"x": 1}, 2)]),
        _resp("Dạ xong."),
    ])

    async def fake_call_llm(messages, tools=None, **kw):
        return next(script)

    _patch_gate(monkeypatch, ran)
    monkeypatch.setattr(llm_engine, "_call_llm", fake_call_llm)
    res = await llm_engine.ask_async("kiểm tra CPU", source_device="portal", caller="admin", session_id="lim3")
    assert ran == [("get_system_info", {"x": 1})]
    assert res["reply"] == "Dạ xong."
