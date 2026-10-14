# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 Dương Thanh Lịch — VN-MateAI. See LICENSE and NOTICE.
"""
tests/test_robot_follow_up.py
=============================
Hội thoại liên tục trên robot (yêu cầu 2026-10-04):

  - Trả lời xong -> robot NGHE TIẾP 10 s (trước đây về nghỉ ngay, phải gọi tên lại);
    im lặng -> hỏi lại, nghe thêm 20 s; vẫn im lặng (tổng 30 s) -> tạm biệt;
    "không / thôi" -> đóng ngay.
  - Âm thanh gửi THEO NHỊP PHÁT (đi trước tối đa PLAYBACK_LEAD_S): trước đây gửi
    nhanh gấp 1,4 lần, máy chủ tưởng đọc xong khi robot còn vài giây chưa phát,
    chuyển sang nghe -> mic thu giọng robot -> lượt mới cắt ngang câu đang đọc.
Không gọi mạng.
"""
from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace

import pytest

import mateai.interfaces.websocket.xiaozhi_gateway as xg


class WS:
    def __init__(self):
        self.text = []
        self.bytes = 0

    async def send_text(self, t):
        self.text.append(json.loads(t))

    async def send_bytes(self, b):
        self.bytes += len(b)


@pytest.fixture
def gw(monkeypatch):
    g = xg.XiaozhiGateway()
    node = xg.XiaozhiNode(device_id="r1", websocket=WS(), client_host="127.0.0.1")
    g._nodes["r1"] = node
    ui = []

    async def send_ui(device_id, state=None, emotion=None, text=None, **extra):
        ui.append({"state": state, "emotion": emotion, **extra})
        node.state = state
        return True

    monkeypatch.setattr(g, "send_ui_payload", send_ui)
    return g, node, ui


async def test_audio_is_paced_to_playback(gw, monkeypatch):
    g, node, _ = gw
    slept = []
    clock = {"t": 1000.0}                      # đồng hồ giả: trôi đúng bằng thời gian chờ
    real_sleep = asyncio.sleep

    async def fake_sleep(sec):
        slept.append(sec)
        clock["t"] += sec
        await real_sleep(0)

    monkeypatch.setattr(xg.asyncio, "sleep", fake_sleep)
    monkeypatch.setattr(xg, "time", SimpleNamespace(perf_counter=lambda: clock["t"]))
    for _ in range(50):                        # 50 x 2048 byte = 3,2 s âm thanh
        await g._pace_playback(node, 2048)
    # Đi trước nhịp phát không quá PLAYBACK_LEAD_S: phải chờ ~2,7 s chứ không gửi dồn.
    assert sum(slept) == pytest.approx(3.2 - xg.PLAYBACK_LEAD_S, abs=0.15)


async def test_after_answer_robot_keeps_listening_10s(gw, monkeypatch):
    g, node, ui = gw
    import mateai.application.voice.voice_turn as vt

    async def turn(query, **kw):
        return vt.VoiceTurnResult(reply_text="CPU khoảng 8 phần trăm.")

    monkeypatch.setattr(vt, "process_voice_turn", turn)
    await g._execute_pipeline(node, "kiểm tra cpu")
    assert node.follow_up == 1
    assert ui[-1] == {"state": "listening", "emotion": "focused", "listen_timeout_ms": 10000}


@pytest.mark.parametrize("reply", ["không có gì nữa đâu em", "không cần , cảm ơn bạn"])
async def test_no_more_requests_says_goodbye_then_idle(gw, monkeypatch, speech, reply):
    # 2026-10-12: nói "không / không cần, cảm ơn…" → robot chào tạm biệt rồi về chờ (trước: tắt im lặng), không gọi AI.
    from mateai.application.voice.voice_session import GOODBYE_PHRASE
    g, node, ui = gw
    node.follow_up = 1

    async def asr(n, audio):
        return reply

    started = []
    monkeypatch.setattr(g, "_transcribe", asr)
    monkeypatch.setattr(g, "_execute_pipeline", lambda n, t: started.append(t) or asyncio.sleep(0))
    await g._finish_utterance(node, b"\0" * 4000)
    assert node.follow_up == 0 and started == [] and ui[-1]["state"] == "idle"
    assert speech == [GOODBYE_PHRASE]


@pytest.fixture
def speech(monkeypatch):
    said = []

    async def synth(self, text, *a, **k):
        said.append(text)
        return b"MP3"

    from mateai.infrastructure.tts.tts_stream_engine import TTSStreamEngine
    monkeypatch.setattr(TTSStreamEngine, "synthesise", synth)
    monkeypatch.setattr(xg, "convert_to_pcm16_16k", lambda audio, *a, **k: b"\0" * 2048)
    return said


async def test_silence_10s_asks_again_then_listens_20s(gw, speech):
    g, node, ui = gw
    from mateai.application.voice.voice_session import FOLLOW_UP_PHRASE
    node.follow_up = 1
    await g._on_follow_up_silence(node)
    assert speech == [FOLLOW_UP_PHRASE] and node.follow_up == 2
    assert ui[-1] == {"state": "listening", "emotion": "focused", "listen_timeout_ms": 20000}


async def test_still_silent_after_asking_says_goodbye(gw, speech):
    g, node, ui = gw
    from mateai.application.voice.voice_session import FAREWELL_PHRASE
    node.follow_up = 2
    await g._on_follow_up_silence(node)
    assert speech == [FAREWELL_PHRASE] and node.follow_up == 0
    assert {"type": "tts_end", "text": FAREWELL_PHRASE} in node.websocket.text
    assert ui[-1]["state"] == "idle"


def test_hud_and_robot_share_the_phrases():
    from mateai.application.voice.voice_session import FAREWELL_PHRASE, FOLLOW_UP_PHRASE
    from mateai.interfaces.http import hud_voice
    assert hud_voice.FAREWELL == FAREWELL_PHRASE and hud_voice.FOLLOW_UP_QUESTION == FOLLOW_UP_PHRASE
