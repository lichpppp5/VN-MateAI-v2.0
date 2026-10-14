# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 Dương Thanh Lịch — VN-MateAI. See LICENSE and NOTICE.
"""
tests/test_tracing.py
=====================
Prompt cuối §93: OpenTelemetry trace cho đường thoại và đường tool.

  voice.turn ── voice.routing / voice.agent / voice.llm_first_token / voice.first_audio
  tool.execute (quyết định chính sách, luật, rủi ro, kiểm chứng)
  http.request (request_id, route, status)

Exporter trong RAM (`setup_tracing("memory")`) — không cần collector. Mặc định "none":
không xuất gì, không tốn chi phí đáng kể.
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def spans():
    from mateai.infrastructure.observability import tracing
    exporter = tracing.setup_tracing("memory")
    exporter.clear()
    yield exporter
    exporter.clear()


async def test_voice_turn_emits_parent_and_stage_spans(spans, monkeypatch):
    from mateai.application.voice import voice_turn as vt

    async def fake_run(query, *, trace, **kw):
        trace.mark("router")
        trace.mark("first_text")
        return vt.VoiceTurnResult(reply_text="ok", fast_command="mute")

    class Sink:
        async def on_status(self, *a, **k): pass
        async def on_sentence(self, *a, **k): pass
        async def on_audio(self, *a, **k): pass

    monkeypatch.setattr(vt, "_run_voice_turn", fake_run)
    await vt.process_voice_turn("tắt tiếng", sink=Sink(), session_id="otel-1", caller="u", request_id="rq-9")
    done = {s.name: s for s in spans.get_finished_spans()}
    root = done["voice.turn"]
    assert root.attributes["voice.request_id"] == "rq-9" and root.attributes["voice.outcome"] == "fast_path"
    routing = done["voice.routing"]
    assert routing.parent.span_id == root.context.span_id and routing.end_time <= root.end_time


async def test_tool_execution_span_carries_policy_decision(spans, monkeypatch):
    import mateai.application.agent.tool_gate as tg
    from core.plugin_manager import plugin_manager

    async def fake_exec(name, args):
        return {"status": "success"}

    monkeypatch.setattr(plugin_manager, "execute_skill", fake_exec)
    await tg.run_tool_with_policy("get_system_info", {}, caller="admin", source_device="portal")
    s = [x for x in spans.get_finished_spans() if x.name == "tool.execute"][-1]
    assert s.attributes["tool.name"] == "get_system_info"
    assert s.attributes["policy.decision"] == "allow" and "policy.rule" in s.attributes


def test_http_request_span_has_request_id(spans):
    import mateai.interfaces.http.server as server
    TestClient(server.app).get("/livez", headers={"X-Request-ID": "rq-http-1"})
    s = [x for x in spans.get_finished_spans() if x.name == "http.request"][-1]
    assert s.attributes["http.request_id"] == "rq-http-1" and s.attributes["http.status_code"] == 200


def test_none_exporter_is_default():
    """Mặc định của schema là none (máy chủ có thể bật otlp trong config.json)."""
    from mateai.config.loader import AppSettings
    assert AppSettings.model_fields["OTEL_EXPORTER"].default == "none"
