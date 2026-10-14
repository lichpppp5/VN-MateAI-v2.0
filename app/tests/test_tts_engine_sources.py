# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 Dương Thanh Lịch — VN-MateAI. See LICENSE and NOTICE.
"""
tests/test_tts_engine_sources.py
================================
Thứ tự nguồn của engine TTS canonical (core/audio/tts_stream_engine.py):
cache -> 9Router -> Edge-TTS. Gợi ý phát âm chỉ áp lên chữ gửi đi tổng hợp,
cache vẫn theo chữ gốc. Không gọi mạng.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import mateai.infrastructure.tts.tts_stream_engine as tse  # noqa: E402


@pytest.fixture
def calls(monkeypatch):
    log = {"router": [], "edge": [], "cache_set": []}
    state = {"router_result": b"R" * 200}

    async def fake_router(text, voice):
        log["router"].append(text)
        return state["router_result"]

    async def fake_edge(text, voice, rate, cache_key=None):
        log["edge"].append(text)
        yield b"E" * 200

    monkeypatch.setattr(tse, "_synthesise_9router", fake_router)
    monkeypatch.setattr(tse, "_stream_edge_tts", fake_edge)
    monkeypatch.setattr(tse, "_cache_get", lambda text, tag=None: None)
    monkeypatch.setattr(tse, "_cache_set", lambda text, data, voice="", tag=None: log["cache_set"].append(text))
    return log, state


async def test_router_first_edge_not_called(calls):
    log, _ = calls
    engine = tse.TTSStreamEngine(voice="vi-VN-HoaiMyNeural", rate="+30%")
    out = await engine.synthesise("Xin chào anh.")
    assert out == b"R" * 200
    assert log["router"] == ["Xin chào anh."]
    assert log["edge"] == []


async def test_edge_used_when_router_fails(calls):
    log, state = calls
    state["router_result"] = None
    engine = tse.TTSStreamEngine(voice="vi-VN-HoaiMyNeural", rate="+30%")
    out = await engine.synthesise("Xin chào anh.")
    assert out == b"E" * 200
    assert log["edge"] == ["Xin chào anh."]


async def test_pronunciation_sent_to_provider_cache_keeps_original(calls):
    log, _ = calls
    engine = tse.TTSStreamEngine(voice="vi-VN-HoaiMyNeural", rate="+30%")
    await engine.synthesise("CPU đang ở mức 32%.")
    assert log["router"] == ["C P U đang ở mức 32%."]
    assert log["cache_set"] == ["CPU đang ở mức 32%."]
