# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 Dương Thanh Lịch — VN-MateAI. See LICENSE and NOTICE.
"""
core/voice_controller.py
========================
Voice Interaction Controller — Phase 13.

Điều phối toàn bộ luồng:
  WakeWordEngine → VoiceWidget (subprocess) → AudioProcessor → LLM → TTS → Feedback

Kiến trúc subprocess:
  - VoiceWidget chạy trong subprocess riêng (tương tự popup_ui.py).
  - Giao tiếp qua stdin JSON pipe.
  - Không block main thread hay FastAPI event loop.

Luồng xử lý:
  1. WakeWordEngine phát hiện "Hey Lyly" → gọi _on_wake_detected()
  2. _on_wake_detected() → spawn VoiceWidget subprocess (nếu chưa có)
                        → gửi {"cmd": "show", "mode": "listening"}
  3. Ghi âm lệnh từ microphone (sử dụng SpeechRecognition)
  4. Gửi audio → AudioEngine.transcribe_audio() → text
  5. Gửi text → LLMEngine → response
  6. TTS response → phát qua loa cục bộ (edge-tts + pydub playback)
  7. Gửi {"cmd": "hide"} → widget ẩn đi
"""

from __future__ import annotations

import asyncio
import io
import json
import logging
import subprocess
import sys
import threading
import time
from datetime import datetime
import random
from pathlib import Path
from typing import Any, Dict, List, Optional

from mateai.application.voice.speech_text import sanitise_for_tts, shorten_for_speech
from mateai.infrastructure.tts.tts_stream_engine import get_tts_engine

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Lazy imports — avoid crash if deps not installed
# ---------------------------------------------------------------------------
try:
    import speech_recognition as sr  # type: ignore[import]
    _SR_AVAILABLE = True
except ImportError:
    _SR_AVAILABLE = False

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

COMMAND_TIMEOUT_SEC: float = 15.0  # Max time to wait for user to START speaking
COMMAND_PHRASE_LIMIT_SEC: float = 10.0  # Max duration of a single command phrase
FOLLOWUP_TIMEOUT_SEC: float = 20.0  # Max time to wait for user to reply to AI question
FOLLOWUP_PHRASE_LIMIT_SEC: float = 10.0  # Max duration of a follow-up reply
SESSION_MAX_TURNS: int = 20  # Maximum turns in a single conversation session
SESSION_IDLE_TIMEOUT_SEC: float = 300.0  # Reset history after 5 min of inactivity
#: Khoá lịch sử của mic máy chủ trong memory_manager (Phase 3: một kho lịch sử).
MIC_SESSION_ID: str = "server-mic"
WIDGET_SCRIPT = str(Path(__file__).resolve().parent / "voice_widget.py")

# Phase 40 & Phase 36: Dynamic Audio Cache Fillers
DELEGATION_FILLER_PHRASE: str = "Ca này hơi sâu, anh chờ em một lát để em đẩy dữ liệu qua hệ thống phân tích chuyên sâu nhé."

# Số câu đệm tối đa được nói TRƯỚC câu trả lời thật. Lạm dụng lời đệm khiến
# người dùng phải nghe hai lần "em đang xử lý" trước khi tới nội dung, và đó
# là cảm giác "chậm" dù máy đã trả lời rất nhanh.
MAX_FILLERS_PER_TURN = 1

CONTEXTUAL_FILLERS: Dict[str, Dict[str, Any]] = {
    "communication": {
        "keywords": ["nhắn", "gửi", "thông báo", "email", "chat"],
        "phrases": [
            "Dạ, em gửi ngay ạ.",
            "Vâng, em đang soạn tin nhắn đây ạ.",
            "Dạ, em truyền tin ngay ạ.",
            "Vâng ạ, em gửi đây.",
        ],
    },
    "system_check": {
        "keywords": ["kiểm tra", "check", "quét", "lỗi", "log", "tình trạng"],
        "phrases": [
            "Vâng, em đang kiểm tra hệ thống ngay đây ạ.",
            "Anh đợi em quét một chút nhé.",
            "Dạ, em rà hệ thống xem ngay ạ.",
            "Vâng ạ, em kiểm tra đây.",
        ],
    },
    "action_execute": {
        "keywords": ["bật", "tắt", "mở", "khởi động", "reset", "xóa", "tạo"],
        "phrases": [
            "Dạ, em thực hiện ngay đây ạ.",
            "Vâng, em đang xử lý lệnh của anh ạ.",
            "Dạ, em bắt tay vào làm ngay ạ.",
            "Vâng ạ, em thực hiện đây.",
        ],
    },
    "deep_analysis": {
        "keywords": [
            "quét log", "phân tích log", "nguyên nhân", "root cause",
            "iis", "chuyên gia", "chuyên sâu", "viết script", "viết code",
            "sửa mã", "powershell", "sập", "crash", "bị lỗi", "kiến trúc"
        ],
        "phrases": [
            "Ca này hơi sâu, anh chờ em một lát để em đẩy dữ liệu qua hệ thống phân tích chuyên sâu nhé.",
            "Phần này cần phân tích kỹ, em đang vào đây ạ.",
            "Em cần mở rộng dữ liệu để tìm ra nguyên nhân, anh đợi em nhé.",
        ],
    },
    "general": {
        "keywords": [],  # Fallback nếu không khớp bất kỳ từ khóa nào
        "phrases": [
            "Dạ, anh chờ em một chút ạ.",
            "Em đang xử lý ngay đây ạ.",
            "Vâng ạ, em xem ngay.",
            "Dạ, để em xử lý nhé.",
            "Em tra cứu giúp anh một chút ạ.",
            "Vâng, anh cho em một chút nhé.",
        ],
    },
}

#: Câu vừa nói lần trước, để không chọn lại — nghe hai lần liên tiếp cùng một
#: câu "Dạ, anh chờ em một chút ạ" sẽ khiến người dùng tưởng bị kẹt.
_last_filler: str = ""


# Backward compatibility flattened list
FILLER_WORDS: List[str] = [
    phrase
    for group in CONTEXTUAL_FILLERS.values()
    for phrase in group.get("phrases", [])
]


def get_contextual_filler(transcript: str) -> str:
    """
    Phase 36.1 (Hotfix): Phân tích nhanh từ khóa trong câu lệnh ASR
    để chọn câu đệm đúng ngữ cảnh (communication, system_check, action_execute)
    thay vì random ngẫu nhiên. Fallback về nhóm 'general'.
    """
    if not transcript or not isinstance(transcript, str):
        return random.choice(CONTEXTUAL_FILLERS["general"]["phrases"])

    clean_text = transcript.lower().strip()

    # Duyệt qua các nhóm intent có keywords
    for intent, group in CONTEXTUAL_FILLERS.items():
        keywords = group.get("keywords", [])
        if not keywords:
            continue
        for kw in keywords:
            if kw.lower() in clean_text:
                selected = _pick_filler(group["phrases"])
                logger.info(
                    "VoiceController: [Contextual Filler] Khớp nhóm '%s' (từ khóa: '%s') -> '%s'",
                    intent, kw, selected,
                )
                return selected

    # Fallback nhóm general
    return _pick_filler(CONTEXTUAL_FILLERS["general"]["phrases"])


def _pick_filler(phrases: List[str]) -> str:
    """
    Chọn câu đệm, ưu tiên câu KHÁC câu vừa nói.

    Chọn ngẫu nhiên thuần thì xác suất lặp lại câu trước vẫn khá cao khi kho
    nhỏ, mà câu lặp là thứ người dùng nhận ra đầu tiên.
    """
    global _last_filler
    if not phrases:
        return ""
    if len(phrases) == 1:
        return phrases[0]

    choices = [p for p in phrases if p != _last_filler] or phrases
    selected = random.choice(choices)
    _last_filler = selected
    return selected


KEEP_ALIVE_PHRASE = "Dạ em đang xử lý và tổng hợp dữ liệu, anh chờ em thêm một chút nhé!"
#: Câu xin lỗi khi lượt thoại không có câu trả lời nào (lõi lỗi / quá hạn).
MIC_TURN_FAILED_PHRASE = "Xin lỗi anh, em chưa xử lý được yêu cầu này, anh thử lại giúp em nhé."
SLEEP_PHRASE = "Em xin phép tạm nghỉ, khi nào cần anh cứ gọi em nhé."
ACTIVE_LISTENING_TIMEOUT_SEC: float = 5.0

# Global lock to serialize audio playback and prevent overlapping sound tracks
_AUDIO_PLAYBACK_LOCK = threading.Lock()

# Keywords that indicate AI is asking a follow-up question
_FOLLOWUP_INDICATORS = [
    "?", "bạn muốn", "bạn có", "bạn cần", "cho tôi biết", "hãy nói",
    "what", "which", "when", "how", "do you", "would you", "could you",
    "please specify", "please tell", "can you",
]

# Phrases that signal the user wants to end the conversation
_CLOSING_PHRASES = [
    "thôi", "ok thôi", "không cần nữa", "cảm ơn", "cảm ơn bạn", "tạm biệt", "hẹn gặp lại",
    "xong rồi", "ok được", "bye", "goodbye", "that's all", "no more", "stop",
    "exit", "quit", "done", "finish",
]


# ---------------------------------------------------------------------------
# VoiceController
# ---------------------------------------------------------------------------

class VoiceController:
    """
    Orchestrates Wake Word → Widget → ASR → LLM → TTS pipeline.
    Maintains multi-turn conversation history within a session for natural
    conversational continuity. Thread-safe singleton.
    """

    def __init__(self) -> None:
        self._widget_proc: Optional[subprocess.Popen] = None
        self._lock = threading.Lock()
        self._active = False  # True while processing a command
        self._wake_engine = None

        # Async event loop reference (set by start())
        self._loop: Optional[asyncio.AbstractEventLoop] = None

        # ---- Conversation session state ----
        # Each entry: {"role": "user"|"assistant", "content": str}
        self._last_display_text: str = ""
        self._session_last_turn_ts: float = 0.0  # epoch seconds of last turn

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def start(self, loop: Optional[asyncio.AbstractEventLoop] = None) -> None:
        """
        Start WakeWordEngine in background.
        Call from main thread after uvicorn starts.

        Args:
            loop: The running asyncio event loop (for async LLM calls).
        """
        self._loop = loop

        from mateai.infrastructure.audio.wake_word_engine import start_wake_word_engine
        self._wake_engine = start_wake_word_engine(on_wake=self._on_wake_detected)
        if self._wake_engine:
            logger.info("VoiceController: WakeWordEngine started.")
        else:
            logger.warning("VoiceController: WakeWordEngine failed to start (missing deps?).")

        # Start amplitude relay thread
        relay = threading.Thread(target=self._amplitude_relay_loop, daemon=True)
        relay.start()

    def stop(self) -> None:
        """Stop all subsystems."""
        from mateai.infrastructure.audio.wake_word_engine import stop_wake_word_engine
        stop_wake_word_engine()
        self._kill_widget()
        logger.info("VoiceController: Stopped.")

    # ------------------------------------------------------------------
    # Wake Callback
    # ------------------------------------------------------------------

    def _on_wake_detected(self) -> None:
        """
        Called by WakeWordEngine (in its listener thread) when wake word heard.
        MUST NOT block — spawn work in thread.
        """
        if self._active:
            logger.debug("VoiceController: Already active, ignoring duplicate wake.")
            return

        logger.info("VoiceController: Wake detected! Starting command flow...")
        try:
            from mateai.interfaces.websocket.realtime_hub import broadcast_hud, broadcast_portal_ui
            if self._loop and self._loop.is_running():
                asyncio.run_coroutine_threadsafe(
                    broadcast_hud({
                        "type": "voice_active",
                        "status": "listening",
                        "text": "🎤 Trợ lý đã nghe thấy 'Hey Lyly'! Xin mời nói câu lệnh...",
                    }),
                    self._loop,
                )
                asyncio.run_coroutine_threadsafe(
                    broadcast_portal_ui("show_toast", {
                        "message": "🎙️ Trợ lý đã nghe thấy 'Hey Lyly'! Xin mời nói câu lệnh...",
                        "type": "info",
                    }),
                    self._loop,
                )
        except Exception:
            pass
        threading.Thread(target=self._command_flow, daemon=True).start()

    # ------------------------------------------------------------------
    # Command Flow (runs in thread)
    # ------------------------------------------------------------------

    def _command_flow(self) -> None:
        """Full wake→command→response multi-turn conversation loop in a dedicated thread."""
        with self._lock:
            if self._active:
                return
            self._active = True

        try:
            # --- Session idle reset ---
            now = time.monotonic()
            if (now - self._session_last_turn_ts) > SESSION_IDLE_TIMEOUT_SEC:
                logger.info("VoiceController: Session idle timeout — clearing history.")
                self._clear_history()

            # ---- Show listening widget ----
            self._send_widget({"cmd": "show", "mode": "listening"})
            self._send_widget({"cmd": "text", "content": "🎤  Đang lắng nghe..."})

            # ======================================================
            # Multi-turn conversation loop (Phase 36 Proactive Engine)
            # ======================================================
            turn_idx = 0
            pending_transcript: Optional[str] = None

            while turn_idx < SESSION_MAX_TURNS:
                is_first_turn = (turn_idx == 0)

                if pending_transcript:
                    transcript = pending_transcript
                    pending_transcript = None
                    logger.info("VoiceController: Turn %d using active listening transcript: '%s'", turn_idx, transcript)
                else:
                    # -- Record --
                    logger.info("VoiceController: Turn %d — recording...", turn_idx)
                    if is_first_turn:
                        audio_bytes = self._record_command()
                    else:
                        audio_bytes = self._record_active_listening(timeout=ACTIVE_LISTENING_TIMEOUT_SEC)

                    if not audio_bytes:
                        if is_first_turn:
                            self._send_widget({"cmd": "text", "content": "❌  Không nhận được âm thanh."})
                            time.sleep(1.5)
                        else:
                            # Silence > 5s in active listening mode -> go to sleep!
                            logger.info("VoiceController: Active listening timeout (>5s silence) -> going to sleep.")
                            self._send_widget({"cmd": "text", "content": "💤  Em xin phép tạm nghỉ..."})
                            self._play_cached_phrase_instant(SLEEP_PHRASE)
                            time.sleep(0.5)
                        break

                    # -- Transcribe --
                    self._send_widget({"cmd": "show", "mode": "processing"})
                    self._send_widget({"cmd": "text", "content": "⚙  Đang nhận dạng..."})
                    transcript = self._transcribe_sync(audio_bytes)
                    logger.info("VoiceController: Turn %d transcript: '%s'", turn_idx, transcript)

                if not transcript:
                    if is_first_turn:
                        self._send_widget({"cmd": "text", "content": "❓  Không rõ lệnh. Vui lòng thử lại."})
                        time.sleep(1.5)
                    else:
                        self._send_widget({"cmd": "text", "content": "💤  Em xin phép tạm nghỉ..."})
                        self._play_cached_phrase_instant(SLEEP_PHRASE)
                        time.sleep(0.5)
                    break

                self._send_widget({"cmd": "text", "content": f"📝  {transcript}"})

                # -- Detect closing phrase --
                if self._is_closing_phrase(transcript):
                    logger.info("VoiceController: User said closing phrase, ending session.")
                    self._send_widget({"cmd": "text", "content": "👋  Tạm biệt anh!"})
                    self._play_cached_phrase_instant(SLEEP_PHRASE)
                    self._clear_history()  # Full reset on explicit goodbye
                    time.sleep(0.5)
                    break

                # -- Phase 36.1: Context-Aware Filler Word (Từ đệm đúng ngữ cảnh 0ms từ Audio Cache) --
                filler = get_contextual_filler(transcript)
                self._send_widget({"cmd": "text", "content": f"⚡  {filler}"})
                # Bug #13 fix: Play filler synchronously (blocks until done) before streaming
                # to prevent audio overlap when LLM responds faster than filler playback.
                self._play_cached_phrase_instant(filler)

                # -- LLM Streaming + TTS (Phase 23 + Phase 36 Keep-Alive Watchdog) --
                self._send_widget({"cmd": "text", "content": "🤖  Đang xử lý & phân tích..."})
                response = self._stream_response_and_play(transcript)
                display_text = self._last_display_text or response
                logger.info("VoiceController: Turn %d complete, response: '%s'", turn_idx, response[:120])

                # Phase 47: Broadcast full detailed results to HUD & Web Portal so screen displays immediately!
                try:
                    from mateai.interfaces.websocket.realtime_hub import broadcast_hud, broadcast_portal_ui
                    if self._loop and self._loop.is_running():
                        asyncio.run_coroutine_threadsafe(
                            broadcast_hud({
                                "type": "voice_active",
                                "status": "speaking",
                                "text": response,
                                "display_text": display_text,
                                "source_device": "voice",
                                "timestamp": datetime.utcnow().isoformat(),
                            }),
                            self._loop,
                        )
                        asyncio.run_coroutine_threadsafe(
                            broadcast_portal_ui("voice_response", {
                                "query": transcript,
                                "reply": display_text,
                                "speech_reply": response,
                                "source_device": "voice",
                                "timestamp": datetime.utcnow().isoformat(),
                            }),
                            self._loop,
                        )
                except Exception as bc_err:
                    logger.debug("VoiceController screen broadcast error: %s", bc_err)

                # Lịch sử được tầng LLM ghi vào memory_manager (phiên MIC_SESSION_ID).
                self._session_last_turn_ts = time.monotonic()

                turn_idx += 1

                # -- Phase 36 Step 4: Active Listening Loop (chờ 5.0 giây) --
                logger.info("VoiceController: Entering Active Listening loop (timeout=%.1fs)...", ACTIVE_LISTENING_TIMEOUT_SEC)
                self._send_widget({"cmd": "show", "mode": "listening"})
                self._send_widget({"cmd": "text", "content": "👂  Đang lắng nghe phản hồi của anh (5s)..."})

                next_audio = self._record_active_listening(timeout=ACTIVE_LISTENING_TIMEOUT_SEC)
                if not next_audio:
                    # Trường hợp 2: Im lặng > 5s -> Phát câu chốt từ Cache -> Đóng mic -> Về ngủ
                    logger.info("VoiceController: Active listening silence > 5s -> playing sleep phrase.")
                    self._send_widget({"cmd": "text", "content": "💤  Em xin phép tạm nghỉ..."})
                    self._play_cached_phrase_instant(SLEEP_PHRASE)
                    time.sleep(0.5)
                    break

                # Trường hợp 1: Có âm thanh mới -> Transcribe và nạp vào vòng lặp kế tiếp
                self._send_widget({"cmd": "show", "mode": "processing"})
                self._send_widget({"cmd": "text", "content": "⚙  Đang nhận dạng câu lệnh tiếp..."})
                next_transcript = self._transcribe_sync(next_audio)
                if not next_transcript:
                    self._send_widget({"cmd": "text", "content": "💤  Em xin phép tạm nghỉ..."})
                    self._play_cached_phrase_instant(SLEEP_PHRASE)
                    time.sleep(0.5)
                    break

                if self._is_closing_phrase(next_transcript):
                    logger.info("VoiceController: User said closing phrase in active listening: '%s'", next_transcript)
                    self._send_widget({"cmd": "text", "content": "👋  Tạm biệt anh!"})
                    self._play_cached_phrase_instant(SLEEP_PHRASE)
                    self._clear_history()
                    time.sleep(0.5)
                    break

                # Gán vào pending_transcript để vòng lặp tiếp tục xử lý mượt mà!
                pending_transcript = next_transcript

            # ---- Done — hide widget ----
            time.sleep(0.8)
            self._send_widget({"cmd": "hide"})

        except Exception as exc:
            logger.error("VoiceController: Command flow error: %s", exc, exc_info=True)
            self._send_widget({"cmd": "text", "content": f"⚠  Lỗi: {str(exc)[:60]}"})
            time.sleep(2.0)
            self._send_widget({"cmd": "hide"})
        finally:
            self._active = False

    @staticmethod
    def _is_followup_question(response: str) -> bool:
        """Detect if the LLM response is asking the user a follow-up question."""
        r = response.lower().strip()
        return any(indicator in r for indicator in _FOLLOWUP_INDICATORS)

    @staticmethod
    def _is_closing_phrase(text: str) -> bool:
        """Detect if user said a conversation-ending phrase."""
        t = text.lower().strip()
        return any(phrase in t for phrase in _CLOSING_PHRASES)

    # ------------------------------------------------------------------
    # Audio Recording
    # ------------------------------------------------------------------

    def _record_command(self) -> Optional[bytes]:
        """
        Record a single command phrase from the microphone.
        Returns raw audio bytes (WAV format) or None on failure.

        IMPORTANT: Temporarily pauses WakeWordEngine mic before opening
        our own sr.Microphone() to avoid hardware conflict (both cannot
        hold the mic simultaneously on most systems).
        """
        if not _SR_AVAILABLE:
            return None

        # --- Pause WakeWordEngine to release hardware mic ---
        from mateai.infrastructure.audio.wake_word_engine import set_mic_enabled, is_mic_enabled
        wake_was_enabled = is_mic_enabled()
        if wake_was_enabled:
            logger.info("VoiceController: Pausing WakeWordEngine mic for command recording...")
            set_mic_enabled(False)
            # Chờ WakeWordEngine thoát khỏi with sr.Microphone() block
            # 0.3s đủ để stream đóng — wake engine chạy vong lặp cứ 0.8s
            time.sleep(0.3)

        try:
            recognizer = sr.Recognizer()
            recognizer.pause_threshold = 0.5          # Phase 50: Ngừng 500ms lập tức cắt câu
            recognizer.non_speaking_duration = 0.4    # Silence buffer
            recognizer.dynamic_energy_threshold = True
            recognizer.energy_threshold = 400          # Wake engine đã calibrate, dùng lại

            with sr.Microphone() as source:
                # NOTE: Bỏ adjust_for_ambient_noise — đã calibrate trong WakeWordEngine
                # Calibrate thêm chỉ thêm 0.5s delay mà không cải thiện đáng kể
                logger.info("VoiceController: Recording command (timeout=%ss, phrase_limit=%ss)...",
                            COMMAND_TIMEOUT_SEC, COMMAND_PHRASE_LIMIT_SEC)
                audio = recognizer.listen(
                    source,
                    timeout=COMMAND_TIMEOUT_SEC,
                    phrase_time_limit=COMMAND_PHRASE_LIMIT_SEC,
                )
            return audio.get_wav_data()
        except sr.WaitTimeoutError:
            logger.warning("VoiceController: Command recording timed out (user did not speak).")
            return None
        except Exception as exc:
            logger.error("VoiceController: Recording error: %s", exc, exc_info=True)
            return None
        finally:
            if wake_was_enabled:
                logger.info("VoiceController: Resuming WakeWordEngine mic after command recording.")
                set_mic_enabled(True)

    def _record_followup(self) -> Optional[bytes]:
        """
        Record a follow-up reply after AI asks a question.
        Uses longer timeout so user has time to think and respond.
        """
        if not _SR_AVAILABLE:
            return None

        # --- Pause WakeWordEngine ---
        from mateai.infrastructure.audio.wake_word_engine import set_mic_enabled, is_mic_enabled
        wake_was_enabled = is_mic_enabled()
        if wake_was_enabled:
            set_mic_enabled(False)
            time.sleep(0.2)  # Giảm từ 0.5s xuống 0.2s

        try:
            recognizer = sr.Recognizer()
            recognizer.pause_threshold = 0.5          # Phase 50: 500ms silence cutoff
            recognizer.non_speaking_duration = 0.4
            recognizer.dynamic_energy_threshold = True
            recognizer.energy_threshold = 400

            with sr.Microphone() as source:
                # Không calibrate — tiết kiệm 0.4s
                logger.info("VoiceController: Waiting for follow-up reply (timeout=%ss)...",
                            FOLLOWUP_TIMEOUT_SEC)
                audio = recognizer.listen(
                    source,
                    timeout=FOLLOWUP_TIMEOUT_SEC,
                    phrase_time_limit=FOLLOWUP_PHRASE_LIMIT_SEC,
                )
            return audio.get_wav_data()
        except sr.WaitTimeoutError:
            logger.info("VoiceController: Follow-up timed out — user did not reply.")
            return None
        except Exception as exc:
            logger.error("VoiceController: Follow-up recording error: %s", exc, exc_info=True)
            return None
        finally:
            if wake_was_enabled:
                set_mic_enabled(True)

    # ------------------------------------------------------------------
    # ASR (sync wrapper)
    # ------------------------------------------------------------------

    def _transcribe_sync(self, audio_bytes: bytes) -> str:
        """Sync wrapper around AudioEngine.transcribe_audio() (+ đo thời gian STT cho trace)."""
        _t0 = time.perf_counter()
        try:
            return self._transcribe_sync_inner(audio_bytes)
        finally:
            self._last_stt_ms = (time.perf_counter() - _t0) * 1000

    def _transcribe_sync_inner(self, audio_bytes: bytes) -> str:
        try:
            if self._loop and self._loop.is_running():
                future = asyncio.run_coroutine_threadsafe(
                    self._transcribe_async(audio_bytes), self._loop
                )
                return future.result(timeout=15.0)
            else:
                # No running loop — use new event loop
                return asyncio.run(self._transcribe_async(audio_bytes))
        except Exception as exc:
            logger.error("VoiceController: Transcription error: %s", exc)
            return ""

    async def _transcribe_async(self, audio_bytes: bytes) -> str:
        from mateai.infrastructure.audio.audio_processor import audio_engine
        return await audio_engine.transcribe_audio(audio_bytes)

    # ------------------------------------------------------------------
    # LLM + TTS Streaming Pipeline (Phase 23)
    # ------------------------------------------------------------------

    def _clear_history(self) -> None:
        try:
            from mateai.application.conversation.memory_manager import memory_manager
            memory_manager.clear_history(MIC_SESSION_ID)
        except Exception as exc:  # pragma: no cover
            logger.debug("VoiceController: clear history error: %s", exc)

    def _stream_response_and_play(self, text: str) -> str:
        """
        Một lượt nói của mic máy chủ: mateai.application.voice.voice_turn.process_voice_turn (dùng
        chung mọi kênh) chạy trên event loop riêng của luồng mic; ở đây chỉ còn
        phần riêng: cập nhật widget, phát loa cục bộ, câu chờ sau 18s.

        Returns: toàn bộ câu đã đọc (để log / hiển thị).
        """
        from mateai.application.voice.voice_turn import VoiceSink, process_voice_turn

        controller = self
        parts: list = []
        holder: Dict[str, Any] = {}

        class _MicSink(VoiceSink):
            async def on_sentence(self, seq: int, sentence: str, display_text: str, **info: Any) -> None:
                parts.append(sentence)
                controller._send_widget({"cmd": "text", "content": f"💬  {sentence[:80]}"})

            async def on_audio(self, seq: int, audio: bytes, spoken: str, kind: str, **info: Any) -> None:
                if not audio:
                    return
                if kind == "filler":
                    controller._send_widget({"cmd": "text", "content": f"⏳  {spoken}"})
                else:
                    controller._send_widget({"cmd": "text", "content": "🔊  Đang phát âm..."})
                loop = asyncio.get_running_loop()
                await loop.run_in_executor(None, controller._play_audio_bytes, audio)

        def _run() -> None:
            async def _turn() -> None:
                holder["result"] = await process_voice_turn(
                    text,
                    sink=_MicSink(),
                    session_id=MIC_SESSION_ID,
                    source_device=None,
                    pre_ack=False,           # mic đã phát lời đệm ngữ cảnh trước lượt
                    filler_after_s=18.0,     # Phase 47: câu chờ nếu >18s chưa có câu trả lời
                    filler_text=lambda _q: KEEP_ALIVE_PHRASE,
                    stt_ms=getattr(controller, "_last_stt_ms", None),
                )
            try:
                asyncio.run(_turn())
            except Exception as exc:
                logger.error("VoiceController: Streaming pipeline error: %s", exc)

        t_start = time.monotonic()
        t = threading.Thread(target=_run, daemon=True, name="mic-voice-turn")
        t.start()
        t.join(timeout=60.0)

        full_response = " ".join(parts).strip()
        result = holder.get("result")
        self._last_display_text = (result.display_text if result else "") or full_response
        logger.info(
            "VoiceController: Pipeline complete in %.2fs, %d sentences.",
            time.monotonic() - t_start, len(parts),
        )

        # Lõi chung không ra câu nào (pipeline hỏng / quá 60 s): xin lỗi bằng câu
        # cố định. Trước đây gọi một đường LLM THỨ HAI (process_voice_command_sync
        # -> ask) — trùng lõi thoại (realtime P5, D2) và có thể treo thêm.
        if not full_response:
            logger.warning("VoiceController: Lượt thoại không có câu trả lời — phát câu xin lỗi.")
            full_response = MIC_TURN_FAILED_PHRASE
            self._last_display_text = full_response
            self._play_tts_sync(full_response)

        return full_response

    # ------------------------------------------------------------------
    # TTS Playback (sync)
    # ------------------------------------------------------------------

    def _play_tts_sync(self, text: str) -> None:
        """Generate TTS and play locally using pydub/afplay with Audio Cache support."""
        from mateai.infrastructure.tts.audio_cache import get_cached_audio_bytes
        cached = get_cached_audio_bytes(text)
        if cached:
            self._play_audio_bytes(cached)
            return

        try:
            try:
                running_loop = asyncio.get_running_loop()
            except RuntimeError:
                running_loop = None

            if running_loop and running_loop.is_running():
                import concurrent.futures
                with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
                    pool.submit(asyncio.run, self._play_tts_async(text)).result(timeout=30.0)
            elif self._loop and self._loop.is_running():
                future = asyncio.run_coroutine_threadsafe(
                    self._play_tts_async(text), self._loop
                )
                future.result(timeout=30.0)
            else:
                asyncio.run(self._play_tts_async(text))
        except Exception as exc:
            logger.error("VoiceController: TTS playback error: %s", exc)

    async def _play_tts_async(self, text: str) -> None:
        audio_bytes = await get_tts_engine().synthesise(shorten_for_speech(sanitise_for_tts(text)))
        if not audio_bytes:
            return
        loop = asyncio.get_event_loop()
        await loop.run_in_executor(None, self._play_audio_bytes, audio_bytes)

    def _play_cached_phrase_instant(self, text: str) -> None:
        """Phase 36: Phát ngay lập tức câu từ Audio Cache cục bộ (0ms latency)."""
        from mateai.infrastructure.tts.audio_cache import get_cached_audio_bytes
        data = get_cached_audio_bytes(text)
        if data:
            self._play_audio_bytes(data)
        else:
            self._play_tts_sync(text)

    def _play_cached_phrase_instant_async(self, text: str) -> None:
        """Phát câu từ Audio Cache trong một worker thread song song không block luồng gọi."""
        threading.Thread(target=self._play_cached_phrase_instant, args=(text,), daemon=True).start()

    def notify_delegation_started(self) -> None:
        """Phase 40: Kích hoạt câu đệm từ Audio Cache khi Gemini ủy quyền bài toán cho Claude."""
        logger.info("VoiceController: [Phase 40 Reflex] Kích hoạt câu đệm ủy quyền Claude...")
        if self._active:
            self._send_widget({
                "cmd": "text",
                "content": "🧠  Đang chuyển dữ liệu cho Chuyên gia Claude...",
            })
        self._play_cached_phrase_instant_async(DELEGATION_FILLER_PHRASE)

    def _record_active_listening(self, timeout: float = ACTIVE_LISTENING_TIMEOUT_SEC) -> Optional[bytes]:
        """
        Phase 36: Lắng nghe chủ động (Active Listening) sau khi AI báo cáo xong tác vụ.
        Nếu người dùng nói trong vòng 5 giây: thu âm và tiếp tục lượt hội thoại.
        Nếu im lặng quá 5 giây: trả về None để kích hoạt câu kết thúc và đưa hệ thống về chế độ ngủ.
        """
        if not _SR_AVAILABLE:
            return None

        from mateai.infrastructure.audio.wake_word_engine import set_mic_enabled, is_mic_enabled
        wake_was_enabled = is_mic_enabled()
        if wake_was_enabled:
            set_mic_enabled(False)
            time.sleep(0.15)

        try:
            recognizer = sr.Recognizer()
            recognizer.pause_threshold = 0.5          # Phase 50: 500ms silence cutoff
            recognizer.non_speaking_duration = 0.4
            recognizer.dynamic_energy_threshold = True
            recognizer.energy_threshold = 400

            with sr.Microphone() as source:
                logger.info("VoiceController: Active listening loop active (timeout=%.1fs)...", timeout)
                audio = recognizer.listen(
                    source,
                    timeout=timeout,
                    phrase_time_limit=COMMAND_PHRASE_LIMIT_SEC,
                )
            return audio.get_wav_data()
        except sr.WaitTimeoutError:
            logger.info("VoiceController: Active listening timed out (silent > %.1fs).", timeout)
            return None
        except Exception as exc:
            logger.error("VoiceController: Active listening recording error: %s", exc)
            return None
        finally:
            if wake_was_enabled:
                set_mic_enabled(True)

    @staticmethod
    def _play_audio_bytes(audio_bytes: bytes) -> None:
        """
        Play audio bytes with ultra-low startup latency.
        Serialized through _AUDIO_PLAYBACK_LOCK to prevent simultaneous audio overlap.
        """
        if not audio_bytes:
            return

        with _AUDIO_PLAYBACK_LOCK:
            import sys
            if sys.platform == "darwin":
                import os
                import subprocess
                import tempfile
                tmp_fd, tmp_path = tempfile.mkstemp(suffix=".mp3")
                try:
                    with os.fdopen(tmp_fd, "wb") as f:
                        f.write(audio_bytes)
                    subprocess.run(["/usr/bin/afplay", tmp_path], check=True)
                except Exception as exc:
                    logger.warning("VoiceController: afplay error (%s), fallback to pydub", exc)
                    VoiceController._pydub_play(audio_bytes)
                finally:
                    try:
                        os.remove(tmp_path)
                    except OSError:
                        pass
            else:
                VoiceController._pydub_play(audio_bytes)

    @staticmethod
    def _pydub_play(mp3_bytes: bytes) -> None:
        """Play MP3 bytes via pydub (cross-platform)."""
        try:
            from pydub import AudioSegment  # type: ignore[import]
            from pydub.playback import play  # type: ignore[import]
            audio = AudioSegment.from_mp3(io.BytesIO(mp3_bytes))
            play(audio)
        except ImportError:
            logger.warning("pydub not installed; TTS playback skipped.")
        except Exception as exc:
            logger.warning("pydub playback error: %s", exc)

    # ------------------------------------------------------------------
    # VoiceWidget subprocess management
    # ------------------------------------------------------------------

    def _ensure_widget_running(self) -> None:
        """Launch VoiceWidget subprocess if not already running."""
        if self._widget_proc and self._widget_proc.poll() is None:
            return  # Already running

        logger.info("VoiceController: Launching VoiceWidget subprocess...")
        try:
            self._widget_proc = subprocess.Popen(
                [sys.executable, WIDGET_SCRIPT],
                stdin=subprocess.PIPE,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                text=True,
                bufsize=1,  # Line-buffered
            )
            time.sleep(0.5)  # Give widget time to initialize
            logger.info("VoiceController: VoiceWidget subprocess started (pid=%d).", self._widget_proc.pid)
        except Exception as exc:
            logger.error("VoiceController: Failed to launch VoiceWidget: %s", exc)
            self._widget_proc = None

    def _send_widget(self, payload: dict) -> None:
        """Send JSON command to VoiceWidget subprocess stdin."""
        self._ensure_widget_running()
        if not self._widget_proc or self._widget_proc.poll() is not None:
            logger.warning("VoiceController: Widget not running, cannot send: %s", payload)
            return
        try:
            line = json.dumps(payload, ensure_ascii=False) + "\n"
            self._widget_proc.stdin.write(line)
            self._widget_proc.stdin.flush()
        except (BrokenPipeError, OSError) as exc:
            logger.warning("VoiceController: Widget pipe broken: %s", exc)
            self._widget_proc = None

    def _kill_widget(self) -> None:
        """Kill VoiceWidget subprocess."""
        if self._widget_proc:
            try:
                self._send_widget({"cmd": "quit"})
                self._widget_proc.wait(timeout=2.0)
            except Exception:
                self._widget_proc.kill()
            self._widget_proc = None

    # ------------------------------------------------------------------
    # Amplitude Relay (WakeWordEngine → VoiceWidget)
    # ------------------------------------------------------------------

    def _amplitude_relay_loop(self) -> None:
        """
        Continuously relay amplitude from WakeWordEngine to VoiceWidget.
        Runs in its own daemon thread.
        """
        while True:
            try:
                if self._wake_engine and self._widget_proc and self._widget_proc.poll() is None:
                    amp = self._wake_engine.get_amplitude()
                    if amp is not None and self._active:
                        self._send_widget({"cmd": "amplitude", "value": amp})
            except Exception:
                pass
            time.sleep(0.033)  # ~30 FPS relay rate


# ---------------------------------------------------------------------------
# Module-level singleton
# ---------------------------------------------------------------------------

voice_controller = VoiceController()
