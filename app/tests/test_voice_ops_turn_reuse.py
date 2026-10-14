# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 Dương Thanh Lịch — VN-MateAI. See LICENSE and NOTICE.
"""
tests/test_voice_ops_turn_reuse.py
==================================
Realtime P3: lượt thoại cần tool không hỏi model hai lần.

Trước: lần stream (5 tool) thấy model gọi tool -> bỏ stream -> vòng agent gọi
lại model với CẢ danh mục (82 tool, ~74k ký tự schema) để model chọn lại đúng
tool đó, rồi thêm một vòng nữa để trả lời. Bench Phase 1: TTFA-answer p50 15 s.
Nay lời gọi tool đã stream được chạy luôn, và vòng agent chỉ thấy các tool đã
đưa cho lần stream (+ công cụ quản lý kỹ năng). Không gọi mạng.
"""
from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace

import pytest

import mateai.application.agent.llm_engine as le
from mateai.application.agent.llm_engine import llm_engine


def _tool(name):
    return {"type": "function", "function": {"name": name, "description": name,
                                             "parameters": {"type": "object", "properties": {}}}}


def _chunk(content="", tool_calls=None):
    return SimpleNamespace(content=content, tool_calls=tool_calls, reasoning=None, model="fake")


class _Stream:
    """Async generator giả của provider.stream; ghi lại việc bị đóng."""

    def __init__(self, chunks, hang=False):
        self.chunks, self.hang, self.closed = chunks, hang, False

    def __aiter__(self):
        return self._gen()

    async def _gen(self):
        try:
            for c in self.chunks:
                yield c
            if self.hang:
                await asyncio.sleep(60)
        finally:
            self.closed = True


async def _drain(chunks, hang=False):
    s = _Stream(chunks, hang)
    gen = s.__aiter__()
    acc = {}
    first = await gen.__anext__()           # như stream_voice_response: thấy tool call đầu rồi dừng
    le._accumulate_tool_calls(acc, first.tool_calls)
    return await le._finish_streamed_tool_calls(gen, acc), s


async def test_tool_call_assembled_from_stream_pieces():
    calls, s = await _drain([
        _chunk(tool_calls=[{"index": 0, "id": "c1", "name": "get_system_info", "arguments": '{"de'}]),
        _chunk(tool_calls=[{"index": 0, "id": "", "name": "", "arguments": 'tail": true}'}]),
        _chunk(tool_calls=[{"index": 1, "id": "c2", "name": "get_uptime", "arguments": ""}]),
    ])
    assert calls == [{"id": "c1", "name": "get_system_info", "arguments": '{"detail": true}'},
                     {"id": "c2", "name": "get_uptime", "arguments": "{}"}]
    assert s.closed


async def test_broken_arguments_fall_back_to_asking_model():
    calls, s = await _drain([_chunk(tool_calls=[{"index": 0, "id": "c1", "name": "x", "arguments": '{"a": '}])])
    assert calls is None and s.closed


async def test_stalled_stream_falls_back_after_deadline(monkeypatch):
    monkeypatch.setattr(le, "STREAMED_TOOL_CALL_WAIT_S", 0.05)
    calls, s = await _drain([_chunk(tool_calls=[{"index": 0, "id": "c1", "name": "x", "arguments": "{}"}])], hang=True)
    assert calls is None and s.closed


async def test_voice_turn_hands_streamed_call_and_offered_tools_to_agent(monkeypatch):
    offered = [_tool("get_system_info"), _tool("list_processes")]
    stream = _Stream([
        _chunk(tool_calls=[{"index": 0, "id": "c1", "name": "get_system_info", "arguments": ""}]),
        _chunk(tool_calls=[{"index": 0, "id": "", "name": "", "arguments": "{}"}]),
    ])
    provider = SimpleNamespace(stream=lambda **kw: stream.__aiter__())
    seen = {}

    async def fake_ask(**kw):
        seen.update(kw)
        return {"reply": "Máy chủ chạy Windows.", "speech_reply": "Máy chủ chạy Windows.", "success": True}

    async def no_pool(self):
        return None

    monkeypatch.setattr(type(llm_engine), "_ensure_shared_client", no_pool)
    monkeypatch.setattr(llm_engine, "get_provider", lambda brain_role=None: provider)
    monkeypatch.setattr(llm_engine, "classify_intent", staticmethod(lambda q: {"type": "operation", "target_brain": "ops"}))
    monkeypatch.setattr(le, "_may_create_skills", lambda c: False)
    import mateai.application.skills.skill_router as sr
    monkeypatch.setattr(sr.dynamic_skill_router, "get_tools_for_query", lambda q, max_tools=5: offered)
    monkeypatch.setattr(llm_engine, "ask_async", fake_ask)

    turn = {}
    out = [s async for s in llm_engine.stream_voice_response("thông tin máy chủ", history=[], turn=turn,
                                                              source_device="portal", session_id="t-p3")]
    assert seen["first_tool_calls"] == [{"id": "c1", "name": "get_system_info", "arguments": "{}"}]
    assert set(seen["tool_names"]) == {"get_system_info", "list_processes"}
    assert turn["agent_prefetched"] is True and stream.closed
    assert out[-1] == "Máy chủ chạy Windows."


async def test_agent_runs_prefetched_call_without_asking_model_again(monkeypatch):
    import core.plugin_manager as pm
    import mateai.application.agent.tool_gate as tg

    monkeypatch.setattr(pm.plugin_manager, "get_all_tools",
                        lambda: [_tool(n) for n in ("get_system_info", "kill_process", "create_new_skill",
                                                    "list_available_skills", "send_telegram_message")])
    ran, llm_calls = [], []

    async def fake_gate(fn_name, fn_args, **kw):
        ran.append((fn_name, fn_args))
        return {"target_client": "master", "args": fn_args, "result": {"hostname": "SRV-01"}}

    async def fake_call_llm(messages, tools=None, **kw):
        llm_calls.append([t["function"]["name"] for t in tools or []])
        msg = SimpleNamespace(content="Dạ, máy chủ tên SRV-01.", tool_calls=None, reasoning="", reasoning_content="")
        return SimpleNamespace(model="fake", choices=[SimpleNamespace(message=msg, finish_reason="stop")])

    monkeypatch.setattr(tg, "run_tool_with_policy", fake_gate)
    monkeypatch.setattr(llm_engine, "_call_llm", fake_call_llm)
    res = await llm_engine.ask_async(
        "tên máy chủ là gì", source_device="portal", caller="admin", session_id="t-p3-agent",
        first_tool_calls=[{"id": "c1", "name": "get_system_info", "arguments": json.dumps({"detail": True})}],
        tool_names=["get_system_info"],
    )
    assert ran == [("get_system_info", {"detail": True})]
    # Một lần gọi model (trả lời từ kết quả tool), không phải hai.
    assert len(llm_calls) == 1
    # Chỉ thấy tool đã đưa + công cụ quản lý kỹ năng, không phải cả danh mục.
    assert set(llm_calls[0]) == {"get_system_info", "create_new_skill", "list_available_skills"}
    assert "SRV-01" in res["reply"]


async def test_agent_without_hints_still_sees_whole_catalog(monkeypatch):
    """Kênh khác (portal chat, Telegram) gọi ask_async không kèm gợi ý: như cũ."""
    import core.plugin_manager as pm
    names = ("get_system_info", "kill_process", "send_telegram_message")
    monkeypatch.setattr(pm.plugin_manager, "get_all_tools", lambda: [_tool(n) for n in names])
    seen = []

    async def fake_call_llm(messages, tools=None, **kw):
        seen.append({t["function"]["name"] for t in tools or []})
        msg = SimpleNamespace(content="Dạ.", tool_calls=None, reasoning="", reasoning_content="")
        return SimpleNamespace(model="fake", choices=[SimpleNamespace(message=msg, finish_reason="stop")])

    monkeypatch.setattr(llm_engine, "_call_llm", fake_call_llm)
    await llm_engine.ask_async("chào", source_device="portal", caller="admin", session_id="t-p3-all")
    assert seen == [set(names)]


async def test_agent_reply_is_spoken_sentence_by_sentence(monkeypatch):
    """Câu trả lời của vòng agent được tách câu: TTS câu đầu không chờ cả đoạn
    (đo được 5,3 s chờ khi cả đoạn là một câu)."""
    stream = _Stream([_chunk(tool_calls=[{"index": 0, "id": "c1", "name": "get_system_info", "arguments": "{}"}])])
    provider = SimpleNamespace(stream=lambda **kw: stream.__aiter__())
    long_reply = ("Em đã kiểm tra xong, máy chủ tên SRV-01 chạy Windows 10 bản 22H2 và đã hoạt động liên tục mười hai ngày. "
                  "Bộ nhớ còn trống khoảng sáu mươi phần trăm, ổ đĩa hệ thống còn trống hai trăm gigabyte. "
                  "Anh có muốn kiểm tra thêm gì không ạ?")

    async def fake_ask(**kw):
        return {"reply": long_reply, "speech_reply": long_reply, "success": True}

    async def no_pool(self):
        return None

    monkeypatch.setattr(type(llm_engine), "_ensure_shared_client", no_pool)
    monkeypatch.setattr(llm_engine, "get_provider", lambda brain_role=None: provider)
    monkeypatch.setattr(llm_engine, "classify_intent", staticmethod(lambda q: {"type": "operation", "target_brain": "ops"}))
    monkeypatch.setattr(le, "_may_create_skills", lambda c: False)
    monkeypatch.setattr(llm_engine, "ask_async", fake_ask)
    out = [s async for s in llm_engine.stream_voice_response("thông tin máy chủ", history=[], turn={"acked": True},
                                                              source_device="portal", session_id="t-p3-split")]
    assert len(out) >= 3 and " ".join(out).replace("  ", " ") == long_reply
