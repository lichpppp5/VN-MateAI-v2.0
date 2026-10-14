# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 Dương Thanh Lịch — VN-MateAI. See LICENSE and NOTICE.
"""
tests/test_voice_turn.py
========================
Use case chung `mateai.application.voice.voice_turn.process_voice_turn` — mọi kênh voice (portal,
HUD, ESP32, mic máy chủ) đi qua đây từ Phase 3. LLM / TTS / lệnh nhanh giả lập.
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import mateai.application.voice.voice_turn as vt  # noqa: E402
from mateai.application.agent.llm_engine import llm_engine  # noqa: E402


class RecSink(vt.VoiceSink):
    def __init__(self):
        self.events = []

    async def on_status(self, status, **info):
        self.events.append(("status", status, info))

    async def on_sentence(self, seq, text, display_text, **info):
        self.events.append(("sentence", seq, text))

    async def on_audio(self, seq, audio, text, kind, **info):
        self.events.append(("audio", seq, text, kind, bool(audio)))


@pytest.fixture
def env(monkeypatch):
    st = {"sentences": ["Câu một ngắn.", "Câu hai ngắn."], "llm_delay": 0.0, "seen": {},
          "fast": None, "tts_hang": set()}

    async def fake_svr(query, history=None, source_device=None, **kw):
        st["seen"].update(kw, source_device=source_device)
        await asyncio.sleep(st["llm_delay"])
        for s in st["sentences"]:
            yield s

    async def fake_stream(self, text, *a, **k):
        if text in st["tts_hang"]:
            await asyncio.sleep(10)
        yield b"A" * 200

    async def fake_synth(self, text, *a, **k):
        return b"F" * 200

    import mateai.application.commands.fast_command_router as fcr

    async def fake_dispatch(query, synthesize_audio=True):
        return st["fast"]

    from mateai.infrastructure.tts.tts_stream_engine import TTSStreamEngine
    monkeypatch.setattr(llm_engine, "stream_voice_response", fake_svr)
    monkeypatch.setattr(llm_engine, "classify_intent",
                        staticmethod(lambda q: {"type": "conversation", "target_brain": "voice", "ack_needed": False}))
    monkeypatch.setattr(TTSStreamEngine, "stream", fake_stream)
    monkeypatch.setattr(TTSStreamEngine, "synthesise", fake_synth)
    monkeypatch.setattr(fcr.fast_command_router, "dispatch", fake_dispatch)
    monkeypatch.setattr("mateai.infrastructure.tts.audio_cache.get_cached_audio_bytes", lambda t: None)
    return st


async def test_fast_path_skips_llm(env):
    from types import SimpleNamespace
    env["fast"] = SimpleNamespace(is_matched=True, reply_text="Bây giờ là 10 giờ.", command_name="get_current_time")
    sink = RecSink()
    res = await vt.process_voice_turn("mấy giờ rồi", sink=sink, session_id="t-fast")
    assert res.fast_command == "get_current_time"
    assert env["seen"] == {}, "lệnh nhanh không được gọi LLM"
    assert ("audio", 1, "Bây giờ là 10 giờ.", "speech", True) in sink.events


async def test_sentences_and_audio_in_order_with_caller(env):
    sink = RecSink()
    res = await vt.process_voice_turn("chào", sink=sink, session_id="t-1", source_device="portal",
                                      caller="alice")
    assert [e[2] for e in sink.events if e[0] == "audio"] == env["sentences"]
    assert env["seen"]["caller"] == "alice" and env["seen"]["session_id"] == "t-1"
    assert res.reply_text == "Câu một ngắn. Câu hai ngắn."


async def test_punctuation_only_fragments_not_spoken(env):
    env["sentences"] = ["Câu một ngắn.", "!", "--", "Câu hai ngắn."]
    sink = RecSink()
    res = await vt.process_voice_turn("chào", sink=sink, session_id="t-punct")
    assert [e[2] for e in sink.events if e[0] == "audio"] == ["Câu một ngắn.", "Câu hai ngắn."]
    assert res.sentences == ["Câu một ngắn.", "Câu hai ngắn."]


async def test_filler_only_when_llm_is_slow(env):
    env["llm_delay"] = 0.3
    sink = RecSink()
    res = await vt.process_voice_turn("chào", sink=sink, session_id="t-2",
                                      filler_after_s=0.05, filler_text=lambda q: "Chờ em chút.")
    assert res.filler_played
    assert sink.events.index(("audio", 0, "Chờ em chút.", "filler", True)) < \
        next(i for i, e in enumerate(sink.events) if e[0] == "sentence")

    env["llm_delay"] = 0.0
    sink2 = RecSink()
    res2 = await vt.process_voice_turn("chào", sink=sink2, session_id="t-3",
                                       filler_after_s=0.2, filler_text=lambda q: "Chờ em chút.")
    assert not res2.filler_played


async def test_long_sentence_shortened_for_speech_only(env):
    long = " ".join(f"Đây là câu dài số {i} trong kết quả công cụ." for i in range(12))
    env["sentences"] = [long]
    sink = RecSink()
    await vt.process_voice_turn("báo cáo", sink=sink, session_id="t-4")
    shown = [e[2] for e in sink.events if e[0] == "sentence"][0]
    spoken = [e[2] for e in sink.events if e[0] == "audio"][0]
    assert shown == long
    # Rút gọn khi ĐỌC, không gắn câu mẫu "chi tiết đã hiển thị trên màn hình".
    assert len(spoken) < len(long) and long.startswith(spoken.rstrip(".")) and "màn hình" not in spoken


async def test_hung_tts_sentence_does_not_block_the_rest(env, monkeypatch):
    from mateai.infrastructure.tts.tts_queue_pipeline import StreamingTTSWorkerPipeline
    orig = StreamingTTSWorkerPipeline.__init__

    def short_timeout(self, *a, **k):
        k["sentence_timeout_s"] = 0.2
        orig(self, *a, **k)

    monkeypatch.setattr(StreamingTTSWorkerPipeline, "__init__", short_timeout)
    env["tts_hang"] = {"Câu một ngắn."}
    sink = RecSink()
    await asyncio.wait_for(vt.process_voice_turn("chào", sink=sink, session_id="t-5"), timeout=3)
    audio = [(e[2], e[4]) for e in sink.events if e[0] == "audio"]
    assert audio == [("Câu một ngắn.", False), ("Câu hai ngắn.", True)]
