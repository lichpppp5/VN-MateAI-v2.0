# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 Dương Thanh Lịch — VN-MateAI. See LICENSE and NOTICE.
"""
tests/test_rate_limits.py
=========================
Prompt cuối §97: giới hạn tần suất cho voice, lượt agent / LLM, phiên WebSocket thoại.

Một bộ đếm dùng chung (`application/security/rate_limit`) áp ở các cửa DUY NHẤT:
`process_voice_turn` (mọi kênh thoại), `llm_engine.ask_async` (mọi lượt agent) và
`/ws/v1/voice-stream` (số phiên đồng thời mỗi người). Quá giới hạn: KHÔNG gọi LLM / TTS,
báo rõ cho người dùng, có thời gian chờ. Ngưỡng ở `security.rate_limits` (0 = tắt).
"""
from __future__ import annotations

import pytest

from mateai.config.loader import settings


@pytest.fixture(autouse=True)
def fresh(monkeypatch):
    from mateai.application.security import rate_limit
    monkeypatch.setattr(settings, "security", settings.security.model_copy(deep=True))
    rate_limit.reset()
    yield
    rate_limit.reset()


def test_sliding_window_and_disable():
    from mateai.application.security import rate_limit
    assert [rate_limit.hit("k", 2, 60) for _ in range(3)][:2] == [0.0, 0.0]
    assert rate_limit.hit("k", 2, 60) > 0                       # lần 3, 4: phải chờ
    assert all(rate_limit.hit("z", 0, 60) == 0.0 for _ in range(100))   # 0 = tắt


async def test_voice_turn_limited_without_llm(monkeypatch):
    from mateai.application.voice import voice_turn as vt
    settings.security.rate_limits["voice_turns_per_min"] = 1
    ran, said = [], []

    async def fake_run(query, **kw):
        ran.append(query)
        return vt.VoiceTurnResult(reply_text="ok")

    class Sink:
        async def on_status(self, status, **i): pass
        async def on_sentence(self, seq, text, display_text, **i): said.append(text)
        async def on_audio(self, *a, **k): pass

    monkeypatch.setattr(vt, "_run_voice_turn", fake_run)
    await vt.process_voice_turn("câu 1", sink=Sink(), session_id="s", caller="u1")
    res = await vt.process_voice_turn("câu 2", sink=Sink(), session_id="s", caller="u1")
    assert ran == ["câu 1"]                                     # lượt 2 không chạy pipeline
    assert "nhiều yêu cầu" in res.reply_text and said and res.trace["outcome"] == "rate_limited"
    await vt.process_voice_turn("người khác", sink=Sink(), session_id="s2", caller="u2")
    assert ran[-1] == "người khác"                              # giới hạn theo người


async def test_agent_turn_limited_before_any_llm_call(monkeypatch):
    from mateai.application.agent.llm_engine import llm_engine
    settings.security.rate_limits["agent_turns_per_min"] = 1
    calls = []

    async def fake_llm(*a, **k):
        calls.append(1)
        raise RuntimeError("dừng sau khi đếm")

    monkeypatch.setattr(llm_engine, "_call_llm", fake_llm)
    try:
        await llm_engine.ask_async("a", source_device="portal", caller="u9", session_id="rl")
    except Exception:
        pass
    n = len(calls)
    res = await llm_engine.ask_async("b", source_device="portal", caller="u9", session_id="rl")
    assert len(calls) == n and res["success"] is False and "nhiều yêu cầu" in res["reply"]


def test_ws_voice_sessions_per_user(monkeypatch):
    from mateai.application.security import rate_limit
    settings.security.rate_limits["ws_voice_sessions_per_user"] = 2
    a = rate_limit.open_session("ws_voice", "u1", 2)
    b = rate_limit.open_session("ws_voice", "u1", 2)
    assert a and b and rate_limit.open_session("ws_voice", "u1", 2) is False
    rate_limit.close_session("ws_voice", "u1")
    assert rate_limit.open_session("ws_voice", "u1", 2) is True
