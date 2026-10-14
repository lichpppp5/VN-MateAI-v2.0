# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 Dương Thanh Lịch — VN-MateAI. See LICENSE and NOTICE.
"""
tests/test_robot_utterance_end.py
=================================
Ba cách robot báo HẾT CÂU NÓI, cùng một xử lý (nhận dạng -> asr_result -> chạy lượt)
nhưng thông điệp gửi lại khác nhau theo firmware — phải giữ nguyên từng cái:

  1. VAD máy chủ (Silero) thấy im 500 ms — firmware VN-MateAI stream PCM liên tục;
     PCM được đóng gói WAV trước khi nhận dạng.
  2. {"type":"listen","state":"stop"} — firmware XiaoZhi gốc: thêm gói "stt" có
     session_id, không đổi màn hình sang "đang suy nghĩ".
  3. {"type":"end_of_speech"} — báo lỗi riêng khi chưa có âm thanh.

Chạy đúng vòng `handle_client` với WebSocket giả. Không gọi mạng.
"""
from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace

import pytest
from starlette.websockets import WebSocketDisconnect

import mateai.interfaces.websocket.xiaozhi_gateway as xg

PCM = b"\x01\x00" * 4000


class FakeWS:
    def __init__(self, msgs):
        self.msgs = list(msgs)
        self.sent = []
        self.client = SimpleNamespace(host="127.0.0.1")

    async def accept(self):
        return None

    async def receive(self):
        if self.msgs:
            return self.msgs.pop(0)
        raise WebSocketDisconnect(code=1000)

    async def send_text(self, text):
        self.sent.append(json.loads(text))

    async def send_bytes(self, data):
        return None


def _txt(obj):
    return {"text": json.dumps(obj)}


@pytest.fixture
def run(monkeypatch):
    import mateai.infrastructure.audio.audio_processor as ap
    rec = {"ui": [], "pipeline": [], "asr_in": [], "asr_out": "mấy giờ rồi"}
    gw = xg.XiaozhiGateway()

    async def ui(device_id, state=None, **kw):
        rec["ui"].append(state)

    def pipeline(node, text):
        # Ghi lúc GỌI (đối số của create_task) — task bị huỷ khi WS giả ngắt.
        rec["pipeline"].append(text)
        return asyncio.sleep(0)

    async def asr(node, audio):
        rec["asr_in"].append(audio)
        return rec["asr_out"]

    monkeypatch.setattr(gw, "send_ui_payload", ui)
    monkeypatch.setattr(gw, "_execute_pipeline", pipeline)
    monkeypatch.setattr(gw, "_transcribe", asr)
    monkeypatch.setattr(ap, "warm_local_whisper", lambda: True)

    async def go(msgs, vad=None):
        if vad is not None:
            monkeypatch.setattr(ap.SileroVADDetector, "process_pcm16", lambda self, b, incoming_rate=16000: vad)
        ws = FakeWS(msgs)
        await gw.handle_client(ws, "robot_t")
        return ws.sent, rec

    return go


def _after_connect(ui):
    return ui[1:]   # bỏ khung "idle" gửi lúc kết nối


async def test_server_vad_end(run):
    sent, rec = await run([{"bytes": PCM}], vad={"speech_ended": True})
    assert rec["asr_in"][0][:4] == b"RIFF"                       # PCM đóng gói WAV
    assert sent == [{"type": "asr_start"}, {"type": "asr_result", "text": "mấy giờ rồi"}]
    assert _after_connect(rec["ui"]) == ["processing"] and rec["pipeline"] == ["mấy giờ rồi"]


async def test_server_vad_end_nothing_recognised(run):
    sent, rec = await run([{"bytes": PCM}], vad={"speech_ended": True})
    rec["asr_out"] = ""
    sent, rec = await run([{"bytes": PCM}], vad={"speech_ended": True})
    assert sent[-1] == {"type": "asr_result", "text": "", "error": "ASR không nhận diện được giọng nói."}
    assert rec["ui"][-1] == "idle"


async def test_xiaozhi_listen_stop(run):
    sent, rec = await run([{"bytes": PCM}, _txt({"type": "listen", "state": "stop"})], vad={})
    assert rec["asr_in"][0] == PCM                                # firmware gốc: không đóng gói
    assert sent == [
        {"session_id": "robot_t", "type": "asr_start"},
        {"session_id": "robot_t", "type": "stt", "text": "mấy giờ rồi"},
        {"type": "asr_result", "text": "mấy giờ rồi"},
    ]
    assert _after_connect(rec["ui"]) == [] and rec["pipeline"] == ["mấy giờ rồi"]


async def test_xiaozhi_listen_stop_nothing_recognised(run):
    sent, rec = await run([], vad={})
    rec["asr_out"] = ""
    sent, rec = await run([{"bytes": PCM}, _txt({"type": "listen", "state": "stop"})], vad={})
    assert sent[-1] == {"session_id": "robot_t", "type": "stt", "text": "",
                        "error": "Không nhận diện được giọng nói."}
    assert rec["ui"][-1] == "idle" and rec["pipeline"] == []


async def test_end_of_speech(run):
    sent, rec = await run([{"bytes": PCM}, _txt({"type": "end_of_speech"})], vad={})
    assert rec["asr_in"][0] == PCM
    assert sent == [{"type": "asr_start"}, {"type": "asr_result", "text": "mấy giờ rồi"}]
    assert _after_connect(rec["ui"]) == ["processing"] and rec["pipeline"] == ["mấy giờ rồi"]


async def test_end_of_speech_without_audio(run):
    sent, rec = await run([_txt({"type": "end_of_speech"})], vad={})
    assert sent == [{"type": "error", "message": "Không có dữ liệu âm thanh trong buffer."}]
    assert rec["asr_in"] == []


async def test_end_of_speech_nothing_recognised(run):
    sent, rec = await run([], vad={})
    rec["asr_out"] = ""
    sent, rec = await run([{"bytes": PCM}, _txt({"type": "end_of_speech"})], vad={})
    assert sent[-1] == {"type": "asr_result", "text": "", "error": "ASR không nhận diện được giọng nói."}
    assert rec["ui"][-1] == "idle" and rec["pipeline"] == []
