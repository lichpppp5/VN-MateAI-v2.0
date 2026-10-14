# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 Dương Thanh Lịch — VN-MateAI. See LICENSE and NOTICE.
"""
tests/test_realtime_voice_llm_turn.py
=====================================
Chạy THẬT một lượt qua LLM của portal (`mateai.interfaces.websocket.realtime_voice_ws._execute_voice_turn`
→ `mateai.application.voice.voice_turn.process_voice_turn` → `LLMEngine.stream_voice_response`).
Chỉ giả lập client OpenAI (stream token), TTS và WebSocket — không gọi mạng.

Bắt các lỗi đã từng xảy ra:
  * NameError `sanitized_query` (commit 4f6464a) — mọi lượt LLM ở portal lỗi.
  * Lịch sử không được lưu khi câu trả lời kết thúc bằng dấu câu
    (`stream_voice_response` dùng phần dư `text_buffer` thay cho toàn bộ câu).
  * Mật khẩu / IP nội bộ lọt sang LLM.
"""
from __future__ import annotations

import json
import sys
import uuid
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import mateai.interfaces.websocket.realtime_voice_ws as rvw  # noqa: E402
from mateai.application.agent.llm_engine import llm_engine  # noqa: E402
from mateai.application.conversation.memory_manager import memory_manager  # noqa: E402

REPLY = "Dạ, RAID 1 ghi cùng dữ liệu lên **hai ổ**. Một ổ hỏng thì ổ kia vẫn còn nguyên."
SECRET_QUERY = "máy 192.168.1.27 mật khẩu password=Abc12345 bị chậm, giải thích giúp em"


class FakeWebSocket:
    def __init__(self):
        self.events: list[dict] = []
        self.binary: list[bytes] = []

    async def send_text(self, text: str) -> None:
        self.events.append(json.loads(text))

    async def send_bytes(self, data: bytes) -> None:
        self.binary.append(data)


class _FakeStream:
    def __init__(self, text):
        self._parts = [text[i:i + 6] for i in range(0, len(text), 6)]

    def __aiter__(self):
        return self._gen()

    async def _gen(self):
        for p in self._parts:
            delta = SimpleNamespace(content=p, tool_calls=None, reasoning="", reasoning_content="")
            yield SimpleNamespace(choices=[SimpleNamespace(delta=delta)])


@pytest.fixture
def turn(monkeypatch):
    seen_messages: list = []

    async def fake_create(**kwargs):
        seen_messages.append(kwargs["messages"])
        return _FakeStream(REPLY)

    fake_client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=fake_create)))

    async def no_client():
        return None

    monkeypatch.setattr(llm_engine, "_ensure_shared_client", no_client)
    monkeypatch.setattr(llm_engine, "_client", fake_client, raising=False)
    monkeypatch.setattr(llm_engine, "_direct_client", None, raising=False)
    monkeypatch.setattr(llm_engine, "classify_intent",
                        staticmethod(lambda q: {"type": "conversation", "target_brain": "voice", "ack_needed": False}))
    monkeypatch.setattr(llm_engine, "get_brain_model", lambda role: "fake-model")

    async def fake_tts_stream(self, text, *a, **k):
        yield ("MP3:" + text).encode("utf-8") * 20

    async def fake_synthesise(self, text, *a, **k):
        return ("MP3:" + text).encode("utf-8") * 20

    from mateai.infrastructure.tts.tts_stream_engine import TTSStreamEngine
    monkeypatch.setattr(TTSStreamEngine, "stream", fake_tts_stream)
    monkeypatch.setattr(TTSStreamEngine, "synthesise", fake_synthesise)

    ws = FakeWebSocket()
    user = f"test-llm-turn-{uuid.uuid4().hex[:6]}"
    session = rvw.RealtimeVoiceSession(ws, {"username": user})
    return {"ws": ws, "session": session, "seen_messages": seen_messages}


async def _run(turn, query: str):
    await rvw._execute_voice_turn(turn["session"], query, {}, "req-llm-1")
    return [e["type"] for e in turn["ws"].events]


async def test_llm_turn_completes_without_error(turn):
    types = await _run(turn, "Giải thích ngắn gọn RAID 1 là gì.")
    errors = [e for e in turn["ws"].events if e["type"] == "error"]
    assert not errors, f"lượt LLM báo lỗi: {errors}"
    assert "session_ended" in types, f"lượt không kết thúc: {types}"


async def test_portal_text_keeps_markdown(turn):
    await _run(turn, "Giải thích ngắn gọn RAID 1 là gì.")
    shown = "".join(e.get("content", "") for e in turn["ws"].events if e["type"] == "text_delta")
    assert shown == REPLY, f"portal phải hiển thị nguyên chữ gốc (Markdown): {shown!r}"


async def test_llm_turn_sends_audio_without_markdown(turn):
    await _run(turn, "Giải thích ngắn gọn RAID 1 là gì.")
    assert turn["ws"].binary, "không có frame audio nào được gửi"
    spoken = " ".join(e["text"] for e in turn["ws"].events if e["type"] == "sentence_ready")
    assert "**" not in spoken


async def test_history_saved_when_reply_ends_with_punctuation(turn):
    await _run(turn, "Giải thích ngắn gọn RAID 1 là gì.")
    hist = memory_manager.get_history(turn["session"].session_id)
    assistant = [m["content"] for m in hist if m.get("role") == "assistant"]
    assert assistant and "Một ổ hỏng thì ổ kia vẫn còn nguyên" in assistant[-1], hist


async def test_sensitive_data_masked_before_llm(turn):
    await _run(turn, SECRET_QUERY)
    sent = turn["seen_messages"][0][-1]["content"]
    assert "Abc12345" not in sent, f"mật khẩu lọt sang LLM: {sent}"
    assert "192.168.1.27" not in sent, f"IP nội bộ lọt sang LLM: {sent}"



async def test_turn_metrics_measure_answer_audio_separately(turn):
    """session_ended mang số đo của VoiceTurnTrace: tiếng câu TRẢ LỜI tách khỏi tiếng câu xác nhận."""
    await _run(turn, "Giải thích ngắn gọn RAID 1 là gì.")
    metrics = next(e for e in turn["ws"].events if e["type"] == "session_ended")["metrics"]
    assert metrics["request_id"] == "req-llm-1" and metrics["channel"] == "portal"
    assert metrics["llm_first_token_ms"] is not None and metrics["ttfa_answer_ms"] is not None
    assert metrics["ttft_ms"] >= metrics["llm_first_token_ms"]
    assert metrics["ttfa_answer_ms"] >= metrics["ttft_ms"] and metrics["ttl_ms"] >= metrics["ttfa_answer_ms"]
