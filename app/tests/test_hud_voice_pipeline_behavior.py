# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 Dương Thanh Lịch — VN-MateAI. See LICENSE and NOTICE.
"""
tests/test_hud_voice_pipeline_behavior.py
=========================================
Kiểm tra HÀNH VI đường thoại HUD (không grep mã nguồn):

  1. Audio xuống HUD đúng thứ tự câu dù TTS câu sau xong trước câu trước.
  2. Không mất câu nào, kể cả câu cuối còn nằm trong hàng đợi.
  3. TTS lỗi/timeout -> vẫn gửi chữ của câu đó.
  4. Barge-in (huỷ task) -> không còn task TTS nào chạy dở.
  5. Vòng lặp LLM không chờ TTS từng câu (TTS chạy gối đầu).

LLM, TTS và kênh broadcast đều được thay bằng bản giả — không gọi mạng.
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import mateai.interfaces.http.server as server  # noqa: E402
from mateai.interfaces.http import hud_voice  # noqa: E402
from mateai.application.agent.llm_engine import llm_engine  # noqa: E402

SENTENCES = [f"Đây là câu số {i} của câu trả lời." for i in range(1, 6)]
# Câu 1 sinh TTS chậm nhất, câu sau nhanh dần -> nếu phát theo thứ tự hoàn
# thành thay vì thứ tự câu thì audio sẽ bị đảo.
TTS_DELAY = {s: 0.25 - i * 0.05 for i, s in enumerate(SENTENCES)}


@pytest.fixture
def hud(monkeypatch):
    # Bộ test này kiểm THỨ TỰ phát + không mất câu: tắt giới hạn số câu đọc to của kênh có màn hình
    # (giới hạn có test riêng: test_edition_commercial.test_long_answer_reads_four_sentences_then_points_to_screen).
    from mateai.application.voice import voice_turn
    monkeypatch.setattr(voice_turn, "spoken_sentence_limit", lambda *_a, **_k: 0)
    events: list = []
    llm_yield_times: list = []
    started: list = []
    cancelled: list = []

    async def fake_stream(query, history=None, source_device=None, **kw):
        for s in SENTENCES:
            llm_yield_times.append(asyncio.get_running_loop().time())
            yield s
            await asyncio.sleep(0)

    async def fake_tts_bytes(text, timeout_s=14.0):
        started.append(text)
        try:
            await asyncio.sleep(TTS_DELAY.get(text, 0.01))
        except asyncio.CancelledError:
            cancelled.append(text)
            raise
        if "lỗi" in text:
            return None
        return ("AUDIO:" + text).encode("utf-8") * 10

    async def fake_broadcast_hud(payload):
        events.append(("json", payload))

    async def fake_broadcast_binary(data):
        events.append(("bin", data))

    async def fake_portal(*_a, **_k):
        return None

    async def fake_thinking(*_a, **_k):
        return None

    monkeypatch.setattr(llm_engine, "stream_voice_response", fake_stream)
    monkeypatch.setattr(llm_engine, "last_voice_display_text", None, raising=False)
    from mateai.interfaces.http import speech
    monkeypatch.setattr(speech, "tts_bytes", fake_tts_bytes)
    # Thay engine canonical (hàng đợi TTS chung gọi .stream, lời đệm gọi .synthesise).
    from mateai.infrastructure.tts.tts_stream_engine import TTSStreamEngine

    async def fake_synthesise(self, text, *a, **k):
        return await fake_tts_bytes(text)

    async def fake_tts_stream(self, text, *a, **k):
        data = await fake_tts_bytes(text)
        if data:
            yield data

    monkeypatch.setattr(TTSStreamEngine, "synthesise", fake_synthesise)
    monkeypatch.setattr(TTSStreamEngine, "stream", fake_tts_stream)
    # Cache đĩa của máy chạy test có thể đã có MP3 thật của câu cố định (vd câu
    # hỏi lại) — test phải dùng audio giả, không phụ thuộc lần chạy trước.
    import mateai.infrastructure.tts.audio_cache as audio_cache
    monkeypatch.setattr(audio_cache, "get_cached_audio_bytes", lambda text: None)
    import mateai.application.commands.fast_command_router as fcr

    async def no_fast(query, synthesize_audio=True):
        return None

    monkeypatch.setattr(fcr.fast_command_router, "dispatch", no_fast)
    monkeypatch.setattr(llm_engine, "classify_intent",
                        staticmethod(lambda q: {"type": "conversation", "target_brain": "voice", "ack_needed": False}))
    monkeypatch.setattr(hud_voice, "broadcast_hud", fake_broadcast_hud)
    monkeypatch.setattr(hud_voice, "broadcast_hud_binary", fake_broadcast_binary)
    monkeypatch.setattr(hud_voice, "broadcast_portal_ui", fake_portal)
    monkeypatch.setattr(hud_voice, "broadcast_thinking", fake_thinking)

    from mateai.application.voice.voice_session import voice_sessions
    session_id = "test-hud-behavior"
    voice_sessions.get(session_id).clear_expecting_reply()

    return {
        "events": events,
        "started": started,
        "cancelled": cancelled,
        "llm_yield_times": llm_yield_times,
        "session_id": session_id,
    }


def _spoken_audio(events):
    return [data.decode("utf-8").split("AUDIO:")[1] for kind, data in events if kind == "bin"]


def _spoken_text(events):
    return [
        p["text"] for kind, p in events
        if kind == "json" and p.get("status") == "speaking"
        and not p.get("is_filler") and not p.get("is_follow_up")
    ]


async def test_audio_in_sentence_order_and_nothing_lost(hud):
    await hud_voice.process_command_body("câu hỏi thử", hud["session_id"], caller="hud-admin")

    audio = [a[: len(SENTENCES[0])] for a in _spoken_audio(hud["events"])]
    # Sau câu trả lời (không kết thúc bằng câu hỏi) trợ lý hỏi lại một câu.
    assert audio[:-1] == SENTENCES, f"audio sai thứ tự hoặc thiếu câu: {audio}"
    assert hud_voice.FOLLOW_UP_QUESTION.startswith(audio[-1])
    assert _spoken_text(hud["events"]) == SENTENCES


async def test_tts_runs_ahead_of_playback(hud):
    """Mọi câu đã được giao cho TTS trước khi câu đầu kịp phát xong."""
    await hud_voice.process_command_body("câu hỏi thử", hud["session_id"], caller="hud-admin")
    # Câu 1 mất 0.25s; nếu TTS tuần tự thì câu 4 chỉ bắt đầu sau ~0.6s.
    # Gối đầu 3 câu -> 4 câu đầu được khởi động gần như cùng lúc.
    assert hud["started"][:4] == SENTENCES[:4]
    spread = hud["llm_yield_times"][3] - hud["llm_yield_times"][0]
    assert spread < 0.1, f"vòng lặp LLM bị TTS chặn: {spread:.3f}s"


async def test_tts_failure_still_sends_text(hud, monkeypatch):
    bad = "Câu này gây lỗi TTS."

    async def stream_with_bad(query, history=None, source_device=None, **kw):
        for s in [SENTENCES[0], bad, SENTENCES[1]]:
            yield s

    monkeypatch.setattr(llm_engine, "stream_voice_response", stream_with_bad)
    await hud_voice.process_command_body("câu hỏi thử", hud["session_id"], caller="hud-admin")

    assert _spoken_text(hud["events"]) == [SENTENCES[0], bad, SENTENCES[1]]
    audio = [a[: len(SENTENCES[0])] for a in _spoken_audio(hud["events"])]
    assert audio[:-1] == [SENTENCES[0], SENTENCES[1]]


async def test_barge_in_leaves_no_tts_task_running(hud):
    task = asyncio.create_task(
        hud_voice.process_command_body("câu hỏi thử", hud["session_id"], caller="hud-admin")
    )
    # Đợi đến khi hàng đợi TTS đã có việc, rồi huỷ như khi người dùng ngắt lời.
    for _ in range(100):
        if len(hud["started"]) >= 3:
            break
        await asyncio.sleep(0.005)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    events_at_cancel = len(hud["events"])
    await asyncio.sleep(0.3)  # quá thời gian TTS dài nhất

    assert hud["cancelled"], "phải có câu TTS bị huỷ giữa chừng thì test mới có ý nghĩa"
    # Câu TTS nào đã bắt đầu thì hoặc đã xong, hoặc đã bị huỷ — không còn cái nào chạy.
    still_running = [
        t for t in asyncio.all_tasks()
        if t is not asyncio.current_task() and not t.done()
        and ("fake_tts" in repr(t.get_coro()) or "_worker_loop" in repr(t.get_coro()))
    ]
    assert not still_running, f"task TTS mồ côi sau khi ngắt lời: {still_running}"
    # Ngắt lời xong thì không phát thêm chữ/tiếng nào của lượt cũ.
    assert len(hud["events"]) == events_at_cancel, hud["events"][events_at_cancel:]


def _spoken_audio_prefixes(events):
    return [a[: len(SENTENCES[0])] for a in _spoken_audio(events)]



def _states(events):
    return [p for kind, p in events if kind == "json" and p.get("type") == "voice_state"]


async def test_answer_then_follow_up_question_keeps_listening(hud):
    """Trả lời → hỏi lại "Anh còn cần…" → HUD được báo chờ nghe tiếp."""
    await hud_voice.process_command_body("câu hỏi thử", hud["session_id"], caller="hud-admin")
    follow = [p for kind, p in hud["events"] if kind == "json" and p.get("is_follow_up")]
    assert [p["text"] for p in follow] == [hud_voice.FOLLOW_UP_QUESTION] and follow[0]["has_audio"]
    state = _states(hud["events"])[-1]
    assert state["expecting_reply"] is True and state["question"] == hud_voice.FOLLOW_UP_QUESTION


async def test_answer_ending_with_question_is_not_asked_twice(hud, monkeypatch):
    async def asking(query, history=None, source_device=None, **kw):
        yield "Em đã kiểm tra xong máy chủ."
        yield "Anh muốn xem chi tiết ổ đĩa nào ạ?"

    monkeypatch.setattr(llm_engine, "stream_voice_response", asking)
    await hud_voice.process_command_body("câu hỏi thử", hud["session_id"], caller="hud-admin")
    assert not [p for kind, p in hud["events"] if kind == "json" and p.get("is_follow_up")]
    assert _states(hud["events"])[-1]["expecting_reply"] is True


@pytest.mark.parametrize("reply", ["dạ không có gì nữa đâu em", "không cần , cảm ơn bạn", "thôi, cảm ơn em nhé"])
async def test_no_more_requests_says_goodbye_and_closes_without_llm(hud, monkeypatch, reply):
    # 2026-10-12 người dùng: trả lời "không cần, cảm ơn bạn" mà trợ lý lại nói "Em đang xử lý…" — câu ghép không được nhận
    # ra. Nay: nhận ra câu ghép, chào tạm biệt ("Dạ vâng, em chào anh…"), đóng, về chờ — không gọi LLM.
    from mateai.application.voice.voice_session import voice_sessions
    voice_sessions.get(hud["session_id"]).mark_expecting_reply(hud_voice.FOLLOW_UP_QUESTION)

    async def must_not_run(*a, **k):
        raise AssertionError("không được gọi LLM khi admin nói không còn yêu cầu")
        yield  # pragma: no cover

    monkeypatch.setattr(llm_engine, "stream_voice_response", must_not_run)
    await hud_voice.process_command_body(reply, hud["session_id"], caller="hud-admin")
    state = _states(hud["events"])[-1]
    assert state["closed"] is True and state["reason"] == "user_done" and state["expecting_reply"] is False
    said = [p["text"] for kind, p in hud["events"] if kind == "json" and p.get("status") == "speaking"]
    assert said == [hud_voice.GOODBYE]


async def test_timeout_says_goodbye_then_closes(hud):
    await hud_voice.end_conversation(hud["session_id"], "timeout")
    said = [p["text"] for kind, p in hud["events"] if kind == "json" and p.get("status") == "speaking"]
    assert said == [hud_voice.FAREWELL]
    assert _states(hud["events"])[-1]["closed"] is True
