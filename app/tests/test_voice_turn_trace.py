# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 Dương Thanh Lịch — VN-MateAI. See LICENSE and NOTICE.
"""
tests/test_voice_turn_trace.py
==============================
Đo độ trễ một lượt thoại cho MỌI kênh (`voice_turn.VoiceTurnTrace`) và endpoint
`GET /api/v1/voice/metrics`.

Trước Phase 1 realtime chỉ portal có trace, và hai số chính sai nghĩa: "TTFT" là
lúc câu đầu được đọc (không phải token đầu của LLM), "TTFA" tính cả câu xác
nhận từ cache nên che mất thời gian tới tiếng của câu trả lời. Không gọi mạng.
"""
from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import mateai.application.voice.voice_turn as vt
from mateai.application.agent.llm_engine import llm_engine
from mateai.interfaces.http.auth_dependencies import get_current_user


class _Sink(vt.VoiceSink):
    def __init__(self):
        self.audio = []

    async def on_audio(self, seq, audio, text, kind, **info):
        self.audio.append((kind, text))


@pytest.fixture
def no_network(monkeypatch):
    from mateai.infrastructure.tts.tts_stream_engine import TTSStreamEngine
    import mateai.infrastructure.tts.audio_cache as audio_cache

    async def fake_synth(self, text, *a, **k):
        return b"A" * 400

    async def fake_stream(self, text, *a, **k):
        yield b"A" * 400

    monkeypatch.setattr(TTSStreamEngine, "synthesise", fake_synth)
    monkeypatch.setattr(TTSStreamEngine, "stream", fake_stream)
    monkeypatch.setattr(audio_cache, "get_cached_audio_bytes", lambda text: None)


async def test_fast_path_turn_is_traced(no_network):
    res = await vt.process_voice_turn("mấy giờ rồi", sink=_Sink(), session_id="t-trace-fast",
                                      source_device="hud", request_id="req-fast")
    t = res.trace
    assert t["request_id"] == "req-fast" and t["channel"] == "hud" and t["outcome"] == "fast_path"
    assert t["router_ms"] is not None and t["llm_first_token_ms"] is None
    assert t["ttfa_answer_ms"] is not None and t["ttl_ms"] >= t["ttfa_answer_ms"]
    assert vt.recent_traces(1)[0]["request_id"] == "req-fast"


async def test_llm_turn_separates_ack_from_answer_audio(no_network, monkeypatch):
    async def fake_stream(query, history=None, source_device=None, turn=None, **kw):
        import time
        turn["llm_first_token_at"] = time.perf_counter()
        yield "RAID 1 nhân bản dữ liệu sang hai ổ."
        yield "Một ổ hỏng thì ổ kia vẫn còn."

    monkeypatch.setattr(llm_engine, "stream_voice_response", fake_stream)
    res = await vt.process_voice_turn("Kiểm tra RAID 1 là gì", sink=_Sink(), session_id="t-trace-llm",
                                      source_device="portal")
    t = res.trace
    assert t["outcome"] == "llm" and t["sentences"] == 2
    assert t["llm_first_token_ms"] is not None
    assert t["ttft_ms"] >= t["llm_first_token_ms"]
    assert t["ttfa_answer_ms"] >= t["ttft_ms"]
    if t["ack_audio_ms"] is not None:  # câu xác nhận (lệnh vận hành) tính riêng
        assert t["ack_audio_ms"] <= t["ttfa_answer_ms"]
        assert t["ttfa_ms"] == t["ack_audio_ms"]


async def test_cancelled_turn_is_recorded(no_network, monkeypatch):
    import asyncio

    async def slow(query, history=None, source_device=None, turn=None, **kw):
        await asyncio.sleep(10)
        yield "không bao giờ tới"

    monkeypatch.setattr(llm_engine, "stream_voice_response", slow)
    task = asyncio.create_task(vt.process_voice_turn("giải thích dài", sink=_Sink(),
                                                     session_id="t-trace-cancel", source_device="hud",
                                                     request_id="req-cancel"))
    await asyncio.sleep(0.2)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert next(t for t in vt.recent_traces(20) if t["request_id"] == "req-cancel")["outcome"] == "cancelled"


def _client(role):
    import mateai.interfaces.http.routers.voice as voice
    app = FastAPI()
    app.include_router(voice.router)
    app.dependency_overrides[get_current_user] = lambda: {"username": f"{role}_u", "role": role}
    return TestClient(app)


@pytest.mark.parametrize("role", ["viewer", "manager"])
def test_metrics_endpoint_is_admin_only(role):
    assert _client(role).get("/api/v1/voice/metrics").status_code == 403


async def test_metrics_endpoint_reports_percentiles(no_network):
    await vt.process_voice_turn("mấy giờ rồi", sink=_Sink(), session_id="t-trace-ep", source_device="hud")
    data = _client("admin").get("/api/v1/voice/metrics?channel=hud&recent=5").json()
    fast = data["stats"]["by_outcome"]["fast_path"]["metrics"]["ttl_ms"]
    assert fast["n"] >= 1 and fast["p50"] <= fast["p95"] <= fast["p99"]
    assert all(r["channel"] == "hud" for r in data["recent"])
