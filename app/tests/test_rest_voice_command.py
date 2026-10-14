# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 Dương Thanh Lịch — VN-MateAI. See LICENSE and NOTICE.
"""
tests/test_rest_voice_command.py
================================
Realtime P5: REST /api/v1/voice-command dùng CHUNG lõi thoại với mọi kênh
(`voice_turn.process_voice_turn`). Trước đây REST gọi thẳng `ask_async`: không
lệnh nhanh, không stream, TTS cả đoạn — cùng câu hỏi, khác hành vi tuỳ kênh.
Không gọi mạng.
"""
from __future__ import annotations

import base64

import pytest

import mateai.interfaces.http.routers.voice as voice
from mateai.application.agent.llm_engine import llm_engine
from mateai.interfaces.http import hud_voice


@pytest.fixture
def quiet(monkeypatch):
    from mateai.infrastructure.tts.tts_stream_engine import TTSStreamEngine
    import mateai.infrastructure.tts.audio_cache as audio_cache

    async def _noop(*_a, **_k):
        return None

    async def fake_stream(self, text, *a, **k):
        yield f"MP3[{text}]".encode()

    async def fake_synth(self, text, *a, **k):
        return f"MP3[{text}]".encode()

    monkeypatch.setattr(TTSStreamEngine, "stream", fake_stream)
    monkeypatch.setattr(TTSStreamEngine, "synthesise", fake_synth)
    monkeypatch.setattr(audio_cache, "get_cached_audio_bytes", lambda text: None)
    monkeypatch.setattr(voice, "broadcast_hud", _noop)
    monkeypatch.setattr(voice, "broadcast_portal_ui", _noop)
    monkeypatch.setattr(hud_voice, "broadcast_thinking", _noop)


async def _call(query, **kw):
    payload = voice.VoiceCommandRequest(query=query, source_device="web", include_audio=True,
                                        session_id=f"t-rest-{abs(hash(query))}", **kw)
    return await voice.voice_command(payload, user={"username": "admin", "role": "admin"})


async def test_fast_command_answered_without_llm(quiet, monkeypatch):
    async def must_not_run(*a, **k):
        raise AssertionError("lệnh nhanh không được gọi LLM")
        yield  # pragma: no cover

    monkeypatch.setattr(llm_engine, "stream_voice_response", must_not_run)
    res = await _call("mấy giờ rồi")
    assert res.success and "giờ" in res.reply
    assert base64.b64decode(res.audio_base64).startswith(b"MP3[")


async def test_llm_answer_audio_is_all_sentences_in_order(quiet, monkeypatch):
    async def fake_stream(query, history=None, source_device=None, turn=None, **kw):
        yield "RAID 1 ghi cùng dữ liệu lên hai ổ đĩa cùng lúc."
        yield "Một ổ hỏng thì ổ kia vẫn còn đủ dữ liệu."

    monkeypatch.setattr(llm_engine, "stream_voice_response", fake_stream)
    monkeypatch.setattr(llm_engine, "classify_intent",
                        staticmethod(lambda q: {"type": "conversation", "target_brain": "voice"}))
    res = await _call("RAID 1 là gì")
    assert res.speech_reply == ("RAID 1 ghi cùng dữ liệu lên hai ổ đĩa cùng lúc. "
                                "Một ổ hỏng thì ổ kia vẫn còn đủ dữ liệu.")
    audio = base64.b64decode(res.audio_base64)
    # Không có câu xác nhận / lời đệm trong một phản hồi REST — chỉ hai câu trả lời.
    assert audio.count(b"MP3[") == 2 and audio.index("RAID 1".encode()) < audio.index("Một ổ".encode())


async def test_tool_results_reach_rest_response(quiet, monkeypatch):
    async def fake_stream(query, history=None, source_device=None, turn=None, **kw):
        turn["used_agent"] = True
        turn["tool_calls_made"] = [{"skill": "kill_process", "args": {"pid": 7}}]
        turn["requires_confirmation"] = True
        yield "Tác vụ này cần anh phê duyệt trên màn hình."

    monkeypatch.setattr(llm_engine, "stream_voice_response", fake_stream)
    monkeypatch.setattr(llm_engine, "classify_intent",
                        staticmethod(lambda q: {"type": "operation", "target_brain": "ops"}))
    res = await _call("dừng tiến trình 7")
    assert res.requires_confirmation is True
    assert res.tool_calls_made == [{"skill": "kill_process", "args": {"pid": 7}}]


async def test_no_ack_sentence_in_rest_reply(quiet, monkeypatch):
    """REST là một phản hồi: câu "để em xử lý" khi model gọi tool không được lẫn vào."""
    seen = {}

    async def fake_stream(query, history=None, source_device=None, turn=None, tool_ack=True, **kw):
        seen["tool_ack"] = tool_ack
        yield "Máy chủ chạy Windows 10."

    monkeypatch.setattr(llm_engine, "stream_voice_response", fake_stream)
    monkeypatch.setattr(llm_engine, "classify_intent",
                        staticmethod(lambda q: {"type": "operation", "target_brain": "ops"}))
    res = await _call("thông tin máy chủ")
    assert seen["tool_ack"] is False and res.speech_reply == "Máy chủ chạy Windows 10."
