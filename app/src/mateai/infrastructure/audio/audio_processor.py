# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 Dương Thanh Lịch — VN-MateAI. See LICENSE and NOTICE.
"""
core/audio_processor.py
========================
Audio Pipeline Engine for VN-MateAI — Phase 23 Latency-Optimised.

Responsibilities:
  - ASR  : `transcribe_audio(audio_bytes)` — chuyển đổi luồng byte âm thanh → văn bản tiếng Việt.
           Backend priority: local_whisper (faster-whisper, ~0.3s) → google (2s).
           Không nhận dạng được thì trả chuỗi rỗng — không tự bịa nội dung lời nói.
  - VAD  : `SileroVADDetector` — cắt câu theo khoảng lặng.
  - TTS  : KHÔNG ở đây — implementation duy nhất là `core/audio/tts_stream_engine.py`.

Zero-Disk-I/O Contract:
  - Tất cả âm thanh đều xử lý qua `io.BytesIO` trong RAM.
  - Không gọi `open(...)` với mode ghi ra bất kỳ file tạm nào.

ASR Backend (config.json ASR_BACKEND):
  - "local_whisper" → faster-whisper tiny model, CPU int8, offline, ~0.3s  [DEFAULT]
  - "whisper"       → OpenAI Whisper API (api.openai.com)
  - "groq"          → Groq Whisper API (ultra-fast cloud)
  - "google"        → Google Speech Recognition (free, fallback)

Phase 73: đã gỡ backend "mock". Backend đó trả câu văn bản mẫu dựa trên độ dài
byte âm thanh, khiến hệ thống hành xử như thể người dùng đã nói đúng câu đó.

Phase 23 Changes:
  - Local faster-whisper singleton (loaded once into RAM on first use).
"""

from __future__ import annotations

import asyncio
import io
import logging
import threading
import time
from typing import Any, Dict

import numpy as np

from mateai.config.loader import settings

logger = logging.getLogger(__name__)

# TTS không còn ở đây: implementation duy nhất là core/audio/tts_stream_engine.py
# (Phase 2). Module này chỉ còn STT + VAD.

# ---------------------------------------------------------------------------
# ASR Configuration
# ---------------------------------------------------------------------------

_WHISPER_MODEL: str = "whisper-1"
_GROQ_WHISPER_MODEL: str = "whisper-large-v3"

# ---------------------------------------------------------------------------
# Local Whisper — singleton loaded once into RAM (thread-safe)
# ---------------------------------------------------------------------------

_local_whisper_model = None
_local_whisper_lock = threading.Lock()
#: Một đoạn câu gọi được nhận dạng một lúc — giới hạn CPU khi nhiều người nói gần robot.
_wake_clip_lock = threading.Lock()
#: Model kiểm lại câu gọi (bước 2 của detect_wake): chỉ chạy với đoạn NGHE GẦN GIỐNG tên. Đo 2026-10-12 (giọng tổng hợp,
#: nói xa + ồn): tiny 23/32 câu gọi, 330 ms; small 27/32 nhưng 1,9 s — dùng làm bước kiểm lại, không thay tiny.
_wake_verify_model = None
_wake_verify_lock = threading.Lock()


def _get_wake_verify_model():
    """faster-whisper cho bước kiểm lại câu gọi (mặc định "small"; WAKE_VERIFY_MODEL="off" để tắt). Nạp lần đầu dùng."""
    global _wake_verify_model
    size = str(getattr(settings, "WAKE_VERIFY_MODEL", "small") or "small").strip()
    if size.lower() in ("off", "none", "false", "0"):
        return None
    if _wake_verify_model is not None:
        return _wake_verify_model
    with _wake_verify_lock:
        if _wake_verify_model is None:
            try:
                from faster_whisper import WhisperModel  # type: ignore[import]
                t0 = time.monotonic()
                _wake_verify_model = WhisperModel(size, device="cpu", compute_type="int8")
                logger.info("[WakeVerify] Đã nạp model '%s' trong %.1fs.", size, time.monotonic() - t0)
            except Exception as exc:  # noqa: BLE001
                logger.warning("[WakeVerify] Không nạp được model '%s': %s — chỉ dùng bước nhanh.", size, exc)
                _wake_verify_model = False
    return _wake_verify_model or None


def _get_local_whisper():
    """
    Lazy-load faster-whisper tiny model into RAM.
    Thread-safe singleton: model is loaded only once and reused across all calls.
    Loading time: ~1-2s on first call, 0s on subsequent calls.
    """
    global _local_whisper_model
    if _local_whisper_model is not None:
        return _local_whisper_model

    with _local_whisper_lock:
        if _local_whisper_model is not None:
            return _local_whisper_model
        try:
            from faster_whisper import WhisperModel  # type: ignore[import]
            model_size = getattr(settings, "WHISPER_MODEL_SIZE", "tiny")
            logger.info("[LocalWhisper] Loading model '%s' (CPU int8)...", model_size)
            t0 = time.monotonic()
            _local_whisper_model = WhisperModel(
                model_size,
                device="cpu",
                compute_type="int8",
            )
            logger.info("[LocalWhisper] Model loaded in %.2fs.", time.monotonic() - t0)
        except ImportError:
            logger.warning("[LocalWhisper] faster-whisper not installed. Will fall back to Google ASR.")
            _local_whisper_model = None
        except Exception as exc:
            logger.error("[LocalWhisper] Model load failed: %s", exc)
            _local_whisper_model = None
    return _local_whisper_model


# ---------------------------------------------------------------------------
# Silero VAD (Voice Activity Detection) — Phase 50 Ultra-Low Latency
# ---------------------------------------------------------------------------

_silero_vad_model = None
_silero_vad_lock = threading.Lock()


def get_silero_vad_model():
    """Lazy-load Silero VAD neural network model singleton into RAM."""
    global _silero_vad_model
    if _silero_vad_model is not None:
        return _silero_vad_model

    with _silero_vad_lock:
        if _silero_vad_model is not None:
            return _silero_vad_model
        try:
            import warnings
            with warnings.catch_warnings():
                warnings.simplefilter("ignore")
                from silero_vad import load_silero_vad
                _silero_vad_model = load_silero_vad()
            logger.info("[SileroVAD] Model loaded successfully into RAM.")
        except Exception as exc:
            logger.warning("[SileroVAD] Failed to load Silero VAD model (%s). Will use energy RMS fallback.", exc)
            _silero_vad_model = None
    return _silero_vad_model


class SileroVADDetector:
    """
    Phase 50 Full-Duplex Voice Activity Detector (VAD).
    Monitors incoming audio chunks (16kHz / 24kHz PCM from Web Mic or ESP32 Xiaozhi).
    Detects user silence <= 500ms immediately to trigger zero-wait cutoff.
    """

    def __init__(
        self,
        threshold: float = 0.5,
        min_silence_duration_ms: int = 500,
        sample_rate: int = 16000,
    ) -> None:
        self.sample_rate = sample_rate
        self.threshold = threshold
        self.min_silence_duration_ms = min_silence_duration_ms
        self.buffer = np.array([], dtype=np.float32)
        self.is_speaking: bool = False
        self.silence_samples_count: int = 0
        self.min_silence_samples = int(sample_rate * min_silence_duration_ms / 1000)

        # Silero VAD Iterator instance if available
        self.iterator = None
        model = get_silero_vad_model()
        if model is not None:
            try:
                from silero_vad import VADIterator
                self.iterator = VADIterator(
                    model,
                    threshold=threshold,
                    sampling_rate=16000,
                    min_silence_duration_ms=min_silence_duration_ms,
                )
            except Exception as e:
                logger.debug("[SileroVADDetector] VADIterator init error: %s", e)

    def reset(self) -> None:
        """Reset internal VAD states for a new speech turn."""
        self.buffer = np.array([], dtype=np.float32)
        self.is_speaking = False
        self.silence_samples_count = 0
        if self.iterator is not None:
            try:
                self.iterator.reset_states()
            except Exception:
                pass

    def process_pcm16(
        self,
        pcm_bytes: bytes,
        incoming_rate: int = 16000,
    ) -> Dict[str, Any]:
        """
        Feed a raw 16-bit PCM chunk from WebSocket/mic.
        Returns:
            {
                "is_speaking": bool,
                "speech_started": bool,
                "speech_ended": bool, # True when 500ms silence detected after speech
            }
        """
        if not pcm_bytes:
            return {"is_speaking": self.is_speaking, "speech_started": False, "speech_ended": False}

        arr = np.frombuffer(pcm_bytes, dtype=np.int16).astype(np.float32) / 32768.0
        if incoming_rate != 16000 and len(arr) > 0:
            num_target = int(len(arr) * 16000 / incoming_rate)
            if num_target > 0:
                arr = np.interp(
                    np.linspace(0, len(arr), num_target, endpoint=False),
                    np.arange(len(arr)),
                    arr,
                ).astype(np.float32)

        self.buffer = np.concatenate([self.buffer, arr])
        speech_started = False
        speech_ended = False

        if self.iterator is not None:
            # torch là phụ thuộc bắt buộc của silero-vad (đã khai báo trong
            # requirements.txt). Nếu thiếu, KHÔNG để ImportError làm sập toàn bộ
            # luồng âm thanh — hạ xuống VAD dựa trên năng lượng (RMS) bên dưới.
            try:
                import torch
            except ImportError:
                logger.warning(
                    "Không import được torch — tạm hạ xuống VAD năng lượng (RMS) cho "
                    "đến khi cài lại torch. Cài bằng: pip install torch"
                )
                self.iterator = None
            else:
                while len(self.buffer) >= 512:
                    chunk = self.buffer[:512]
                    self.buffer = self.buffer[512:]
                    try:
                        res = self.iterator(torch.from_numpy(chunk))
                        if res:
                            if "start" in res:
                                self.is_speaking = True
                                speech_started = True
                            elif "end" in res:
                                self.is_speaking = False
                                speech_ended = True
                    except Exception:
                        pass

        if self.iterator is None:
            # Fallback Energy-based RMS VAD with 500ms silence threshold
            while len(self.buffer) >= 512:
                chunk = self.buffer[:512]
                self.buffer = self.buffer[512:]
                rms = float(np.sqrt(np.mean(chunk ** 2)))
                if rms > 0.02:  # Active speech
                    if not self.is_speaking:
                        self.is_speaking = True
                        speech_started = True
                    self.silence_samples_count = 0
                else:  # Silence
                    if self.is_speaking:
                        self.silence_samples_count += 512
                        if self.silence_samples_count >= self.min_silence_samples:
                            self.is_speaking = False
                            speech_ended = True
                            self.silence_samples_count = 0

        return {
            "is_speaking": self.is_speaking,
            "speech_started": speech_started,
            "speech_ended": speech_ended,
        }


def warm_local_whisper() -> bool:
    """Nạp sẵn model faster-whisper cục bộ (gọi trong thread) — câu gọi đầu tiên không chờ 1-2 s."""
    return _get_local_whisper() is not None


def audio_bytes_to_numpy_float32(audio_bytes: bytes) -> np.ndarray:
    """
    Phase 50 Step 2: Pure in-memory conversion of audio bytes to float32 NumPy array.
    Zero Disk I/O.
    """
    if not audio_bytes:
        return np.array([], dtype=np.float32)

    # If already raw PCM (e.g. from mic/ESP32, without container headers)
    if not (audio_bytes.startswith(b"RIFF") or audio_bytes.startswith(b"\x1aE\xdf\xa3") or
            audio_bytes.startswith(b"OggS") or audio_bytes.startswith(b"ID3") or
            audio_bytes[:2] == b"\xff\xfb"):
        try:
            return np.frombuffer(audio_bytes, dtype=np.int16).astype(np.float32) / 32768.0
        except Exception:
            pass

    # Decode containerized audio (WAV, MP3, WebM, Ogg) completely in RAM via pydub
    try:
        from pydub import AudioSegment
        seg = AudioSegment.from_file(io.BytesIO(audio_bytes))
        seg = seg.set_channels(1).set_frame_rate(16000)
        samples = np.array(seg.get_array_of_samples(), dtype=np.float32) / 32768.0
        return samples
    except Exception as exc:
        logger.debug("[AudioDecoder] RAM decode using pydub failed (%s), fallback raw: ", exc)
        try:
            return np.frombuffer(audio_bytes, dtype=np.int16).astype(np.float32) / 32768.0
        except Exception:
            return np.array([], dtype=np.float32)


def _detect_audio_extension_and_mime(audio_bytes: bytes) -> tuple[str, str]:
    """Tự động phát hiện container âm thanh từ magic bytes ở header."""
    if not audio_bytes:
        return "audio.wav", "audio/wav"
    if audio_bytes.startswith(b"RIFF"):
        return "audio.wav", "audio/wav"
    if audio_bytes.startswith(b"\x1aE\xdf\xa3"):
        return "audio.webm", "audio/webm"
    if audio_bytes.startswith(b"OggS"):
        return "audio.ogg", "audio/ogg"
    if audio_bytes.startswith(b"ID3") or audio_bytes[:2] == b"\xff\xfb":
        return "audio.mp3", "audio/mpeg"
    if audio_bytes.startswith(b"fLaC"):
        return "audio.flac", "audio/flac"
    return "audio.wav", "audio/wav"


# ---------------------------------------------------------------------------
# AudioEngine
# ---------------------------------------------------------------------------


class AudioEngine:
    """
    Stateless audio processing engine.
    All methods are coroutines; instances can be shared across WebSocket connections.
    """


    # ------------------------------------------------------------------
    # ASR — Speech to Text
    # ------------------------------------------------------------------

    async def transcribe_audio(self, audio_bytes: bytes) -> str:
        """
        Convert raw audio bytes (PCM/WAV/MP3/OPUS) to Vietnamese text.

        Priority chain (Phase 23):
          1. local_whisper — offline faster-whisper, ~0.3s, no network.
          2. groq          — cloud Groq Whisper, ~0.5s.
          3. whisper       — OpenAI Whisper API.
          4. google        — Google free ASR (~2s), last resort.

        Không engine nào nhận dạng được -> trả "" (không đoán nội dung lời nói).

        Args:
            audio_bytes: Raw audio data.

        Returns:
            Transcribed Vietnamese text, or "" on failure.
        """
        if not audio_bytes:
            logger.warning("transcribe_audio: received empty audio buffer, skipping.")
            return ""

        backend: str = getattr(settings, "ASR_BACKEND", "local_whisper").lower()

        logger.info(
            "ASR request: backend=%s, audio_size=%d bytes",
            backend, len(audio_bytes),
        )

        try:
            if backend == "local_whisper":
                result = await self._transcribe_local_whisper(audio_bytes)
                if result:
                    return result
                # If local model not available, fall through to google
                logger.warning("[LocalWhisper] No result, falling back to Google ASR.")
                return await self._transcribe_google(audio_bytes)

            elif backend == "groq" and getattr(settings, "GROQ_API_KEY", ""):
                return await self._transcribe_groq(audio_bytes)

            elif backend == "whisper" and getattr(settings, "API_KEY", ""):
                return await self._transcribe_whisper(audio_bytes)

            else:
                # Default fallback: Google (free, no key)
                result = await self._transcribe_google(audio_bytes)
                if result:
                    return result
                # Phase 73: KHÔNG bịa nội dung người dùng đã nói. Trước đây khi
                # không engine nào nhận dạng được, hệ thống trả về câu mẫu ("xin
                # chào"…) rồi đưa vào vòng lặp LLM — người dùng tưởng mình đã
                # nói câu đó. Không nhận dạng được thì trả chuỗi rỗng.
                logger.warning("Không có engine ASR nào nhận dạng được âm thanh; trả về chuỗi rỗng.")
                return ""

        except Exception as exc:  # pylint: disable=broad-except
            logger.error("ASR transcription failed [backend=%s]: %s", backend, exc)
            return ""

    async def transcribe_wake_clip(self, pcm16: bytes, verify: bool = False) -> str:
        """
        Nhận dạng đoạn âm thanh ngắn để tìm câu gọi tên trợ lý — CHỈ offline
        (faster-whisper cục bộ, không bao giờ chuyển sang dịch vụ đám mây: âm
        thanh trong phòng lúc chưa gọi trợ lý không rời máy chủ).

        `hotwords` = tên trợ lý: đo 2026-10-04 (model tiny, TTS Hoài My / Nam
        Minh) nhận đủ 8/8 câu gọi thay vì 7/8, 0/6 câu thường bị nhận nhầm.
        Trả "" nếu không có model hoặc lỗi.
        """
        if not pcm16:
            return ""
        try:
            from mateai.config.loader import get_assistant_name
            hot = get_assistant_name() or None
        except Exception:  # noqa: BLE001
            hot = None

        def _run() -> str:
            with _wake_clip_lock:
                return _transcribe(_get_wake_verify_model() if verify else _get_local_whisper())

        def _transcribe(model) -> str:
            if model is None:
                return ""
            audio_np = audio_bytes_to_numpy_float32(pcm16)
            if len(audio_np) == 0:
                return ""
            # Gọi từ xa: tín hiệu nhỏ — nâng biên độ đỉnh lên ~0,5 (tối đa ×8)
            # trước khi nhận dạng.
            peak = float(np.abs(audio_np).max())
            if 0.0 < peak < 0.5:
                audio_np = audio_np * min(0.5 / peak, 8.0)
            segments, _info = model.transcribe(
                audio_np, language="vi", beam_size=1, best_of=1, temperature=0.0,
                vad_filter=False, condition_on_previous_text=False,
                hotwords=hot, max_new_tokens=48,
            )
            return " ".join(seg.text.strip() for seg in segments).strip()

        try:
            return await asyncio.to_thread(_run)
        except Exception as exc:  # noqa: BLE001
            logger.warning("[WakeClip] Lỗi nhận dạng offline: %s", exc)
            return ""

    async def _transcribe_local_whisper(self, audio_bytes: bytes) -> str:
        """
        Transcribe via local faster-whisper model (offline, no network, ~0.1-0.2s).
        Phase 50 Step 2: In-Memory float32 NumPy array passed directly to transcribe() (Zero Disk I/O).
        """
        def _sync_transcribe() -> str:
            model = _get_local_whisper()
            if model is None:
                return ""

            try:
                t0 = time.monotonic()
                # Phase 50 Step 2: Convert to numpy float32 array in RAM directly (Zero Disk I/O)
                audio_np = audio_bytes_to_numpy_float32(audio_bytes)
                if len(audio_np) == 0:
                    return ""

                # Phase 50 Step 2.4: Faster-Whisper with vad_filter=True, beam_size=1
                segments, info = model.transcribe(
                    audio_np,
                    language="vi",
                    beam_size=1,            # 1% accuracy tradeoff for 2x speedup
                    best_of=1,
                    temperature=0.0,
                    vad_filter=True,        # Filter out silence
                    vad_parameters=dict(
                        min_silence_duration_ms=500,
                    ),
                )
                text = " ".join(seg.text.strip() for seg in segments).strip()
                elapsed = time.monotonic() - t0
                logger.info(
                    "[LocalWhisper-InRam] ASR done in %.2fs, lang=%s, text='%s'",
                    elapsed, getattr(info, "language", "vi"), text[:100],
                )
                return text
            except Exception as exc:
                logger.error("[LocalWhisper] Transcription error: %s", exc)
                return ""

        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, _sync_transcribe)

    async def _transcribe_whisper(self, audio_bytes: bytes) -> str:
        """
        Transcribe via OpenAI Whisper API.
        Runs the synchronous openai SDK call in a thread executor to avoid
        blocking the async event loop.
        """
        from openai import OpenAI

        fname, mime = _detect_audio_extension_and_mime(audio_bytes)

        def _sync_call() -> str:
            client = OpenAI(
                api_key=settings.API_KEY,
                base_url=settings.BASE_URL,
            )
            audio_file = io.BytesIO(audio_bytes)
            audio_file.name = fname

            transcript = client.audio.transcriptions.create(
                model=_WHISPER_MODEL,
                file=audio_file,
                language="vi",
                response_format="text",
            )
            return str(transcript).strip()

        loop = asyncio.get_event_loop()
        text = await loop.run_in_executor(None, _sync_call)
        logger.info("Whisper ASR result: '%s'", text[:100])
        return text

    async def _transcribe_groq(self, audio_bytes: bytes) -> str:
        """
        Transcribe via Groq Whisper API (ultra-fast, ~240x realtime).
        Uses httpx async client for non-blocking HTTP.
        """
        groq_key: str = getattr(settings, "GROQ_API_KEY", "")
        groq_url: str = getattr(settings, "GROQ_BASE_URL", "https://api.groq.com/openai/v1")

        if not groq_key:
            logger.warning("GROQ_API_KEY not set; falling back to OpenAI Whisper.")
            return await self._transcribe_whisper(audio_bytes)

        fname, mime = _detect_audio_extension_and_mime(audio_bytes)
        audio_file = io.BytesIO(audio_bytes)
        audio_file.name = fname

        from mateai.infrastructure.http.connection_pool import get_stt_http_client
        client = await get_stt_http_client()
        endpoint_url = groq_url.rstrip("/") + "/audio/transcriptions"
        response = await client.post(
            endpoint_url,
            headers={"Authorization": f"Bearer {groq_key}"},
            files={"file": (fname, audio_file, mime)},
            data={
                "model": _GROQ_WHISPER_MODEL,
                "language": "vi",
                "response_format": "text",
            },
        )
        response.raise_for_status()
        text = response.text.strip()
        logger.info("Groq ASR result: '%s'", text[:100])
        return text

    async def _transcribe_google(self, audio_bytes: bytes) -> str:
        """
        Transcribe audio via Google Speech Recognition (free, no API key needed).
        Converts WebM/Ogg/MP3 via pydub to standard WAV if necessary.
        """
        def _sync_google() -> str:
            try:
                import speech_recognition as sr
            except ImportError:
                logger.warning("speech_recognition not installed for Google ASR.")
                return ""

            r = sr.Recognizer()
            wav_buf = io.BytesIO(audio_bytes)

            if not audio_bytes.startswith(b"RIFF"):
                converted = None
                try:
                    from pydub import AudioSegment
                    audio_seg = AudioSegment.from_file(wav_buf)
                    converted = io.BytesIO()
                    audio_seg.export(converted, format="wav")
                    converted.seek(0)
                    wav_buf = converted
                except Exception:
                    # Fallback cho raw PCM 16-bit 16kHz mono từ ESP32/micro
                    try:
                        import wave
                        pcm_wav = io.BytesIO()
                        with wave.open(pcm_wav, "wb") as wf:
                            wf.setnchannels(1)
                            wf.setsampwidth(2)
                            wf.setframerate(16000)
                            wf.writeframes(audio_bytes)
                        pcm_wav.seek(0)
                        wav_buf = pcm_wav
                    except Exception as wave_err:
                        logger.debug("wave packaging fallback failed: %s", wave_err)
                        wav_buf.seek(0)

            try:
                with sr.AudioFile(wav_buf) as source:
                    audio_data = r.record(source)

                try:
                    text = r.recognize_google(audio_data, language="vi-VN")
                    return text.strip()
                except sr.UnknownValueError:
                    try:
                        text = r.recognize_google(audio_data, language="en-US")
                        return text.strip()
                    except (sr.UnknownValueError, sr.RequestError):
                        return ""
                except sr.RequestError as req_err:
                    logger.warning("Google ASR request error: %s", req_err)
                    return ""
            except Exception as exc:
                logger.warning("Google ASR processing error: %s", exc)
                return ""

        loop = asyncio.get_event_loop()
        text = await loop.run_in_executor(None, _sync_google)
        if text:
            logger.info("Google ASR result: '%s'", text[:100])
        return text

    # ------------------------------------------------------------------
    # TTS — Text to Speech với Fallback Chain (Phase 23: with timeout)
    # ------------------------------------------------------------------






# ---------------------------------------------------------------------------
# Module-level singleton
# ---------------------------------------------------------------------------

audio_engine = AudioEngine()


def preload_whisper_model() -> None:
    """
    Preload the local Whisper model into RAM at server startup.
    Call this from main.py after uvicorn starts to avoid first-request delay.
    """
    backend: str = getattr(settings, "ASR_BACKEND", "local_whisper").lower()
    if backend == "local_whisper":
        logger.info("[LocalWhisper] Preloading model in background thread...")
        t = threading.Thread(target=_get_local_whisper, daemon=True, name="whisper-preload")
        t.start()


