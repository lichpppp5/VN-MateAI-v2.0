# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 Dương Thanh Lịch — VN-MateAI. See LICENSE and NOTICE.
"""
core/audio/tts_stream_engine.py
================================
TTS Stream Engine — implementation TTS DUY NHẤT của hệ thống (mọi kênh voice).

Thứ tự nguồn (đo 2026-10-01 trên máy chủ, câu mới mỗi lần):
  1. Cache RAM / Disk (0ms) — khoá theo câu + GIỌNG (audio_cache.get_audio_hash)
  2. Nguồn đã chọn trong Voice Studio (chỉ khi đủ thông tin đăng nhập — vbee_tts.resolve_tts):
       Vbee AIVoice Realtime (byte đầu ~0,5 s theo tài liệu Vbee) hoặc ElevenLabs WebSocket stream
  3. 9Router /v1/audio/speech — Hoài My qua proxy, p50 1,2 s cả câu
  4. Microsoft Edge-TTS stream — Hoài My trực tiếp, chunk đầu p50 3,8 s;
     dự phòng khi 9Router lỗi / chưa cấu hình (đo được 1/5 lần 502)
  Nguồn 2 lỗi (token Vbee hết hạn, hết điểm, mất mạng…) → tự rơi xuống 3 / 4 với giọng Edge: không bao giờ câm.

Không còn nhánh gTTS: thư viện chưa từng được khai báo nên nhánh đó chưa bao
giờ chạy, và giọng khác Hoài My.
"""

from __future__ import annotations

import asyncio
import logging
import re
import time
from typing import AsyncGenerator, Optional

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _get_tts_voice() -> str:
    try:
        from mateai.config.loader import settings
        v = (
            getattr(settings, "TTS_VOICE", None)
            or getattr(settings, "tts_voice", None)
        )
        if not v and hasattr(settings, "audio"):
            audio = settings.audio
            v = (
                (audio.get("tts_voice") or audio.get("TTS_VOICE"))
                if isinstance(audio, dict)
                else (getattr(audio, "tts_voice", None) or getattr(audio, "TTS_VOICE", None))
            )
        return v or "vi-VN-HoaiMyNeural"
    except Exception:
        return "vi-VN-HoaiMyNeural"


def _get_tts_rate() -> str:
    try:
        from mateai.config.loader import settings
        rate = getattr(settings, "TTS_RATE", None) or getattr(settings, "tts_rate", None)
        if rate is not None:
            return f"+{int(rate)}%" if str(rate).lstrip("+-").isdigit() else str(rate)
        if hasattr(settings, "audio"):
            audio = settings.audio
            rate = (
                (audio.get("speech_rate") or audio.get("TTS_RATE"))
                if isinstance(audio, dict)
                else (getattr(audio, "speech_rate", None) or getattr(audio, "TTS_RATE", None))
            )
            if rate is not None:
                return f"+{int(rate)}%" if str(rate).lstrip("+-").isdigit() else str(rate)
    except Exception:
        pass
    return "+50%"


# ---------------------------------------------------------------------------
# Gợi ý phát âm — chỉ áp lên chữ GỬI ĐI tổng hợp, không lên chữ hiển thị
# ---------------------------------------------------------------------------

_PRONUNCIATION = [
    (re.compile(r'\bVN-?MateAI\b', re.I), 'VN Mate AI'),
    (re.compile(r'\bLyly\b', re.I), 'Ly Ly'),
    (re.compile(r'\bAPI\b'), 'A P I'),
    (re.compile(r'\bRAM\b'), 'Ram'),
    (re.compile(r'\bCPU\b'), 'C P U'),
    (re.compile(r'\bPC-([a-zA-Z0-9]+)\b'), r'PC \1'),
]


def apply_pronunciation(text: str) -> str:
    """Đổi từ viết tắt / tên riêng sang cách Hoài My đọc tự nhiên."""
    for pattern, repl in _PRONUNCIATION:
        text = pattern.sub(repl, text)
    return text


# ---------------------------------------------------------------------------
# Cache helpers
# ---------------------------------------------------------------------------

def _cache_get(text: str, tag: Optional[str] = None) -> Optional[bytes]:
    try:
        from mateai.infrastructure.tts.audio_cache import get_cached_audio_bytes
        return get_cached_audio_bytes(text, tag)
    except Exception:
        return None


def _cache_set(text: str, data: bytes, voice: str = "", tag: Optional[str] = None) -> None:
    try:
        from mateai.infrastructure.tts.audio_cache import save_to_cache
        save_to_cache(text, data, voice=voice or _get_tts_voice(), tag=tag)
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Backend: Edge-TTS streaming (byte chunks — không qua base64)
# ---------------------------------------------------------------------------

async def _stream_edge_tts(
    text: str,
    voice: str,
    rate: str,
    cache_key: Optional[str] = None,
) -> AsyncGenerator[bytes, None]:
    """
    Stream raw MP3 bytes từ Edge-TTS Microsoft (dự phòng sau 9Router).
    Đo trên máy chủ này 2026-10-03 (bench_voice --tts-providers): byte đầu p50
    2,7 s (câu 7 từ) – 4,6 s (28 từ), chậm hơn 9Router (1,3 – 2,2 s cả câu).
    """
    try:
        import edge_tts  # type: ignore
    except ImportError:
        logger.warning("[TTS] edge_tts chưa cài. pip install edge-tts")
        return

    accumulated = bytearray()
    for attempt in range(2):
        accumulated.clear()
        try:
            communicate = edge_tts.Communicate(text=text, voice=voice, rate=rate)
            async for item in communicate.stream():
                if item.get("type") == "audio" and item.get("data"):
                    chunk: bytes = item["data"]
                    accumulated.extend(chunk)
                    yield chunk

            if accumulated:
                logger.debug(
                    "[TTS/edge-tts] OK: %d bytes, attempt=%d, voice=%s",
                    len(accumulated), attempt + 1, voice,
                )
                from mateai.infrastructure.tts.vbee_tts import edge_choice
                _cache_set(cache_key or text, bytes(accumulated), voice, tag=edge_choice(voice)["tag"])
                return

            logger.warning("[TTS/edge-tts] 0 chunks trả về (attempt %d), retry...", attempt + 1)
            await asyncio.sleep(0.2)

        except Exception as exc:
            if accumulated:
                return  # Đã có một phần audio — dùng luôn
            if attempt == 0:
                logger.debug("[TTS/edge-tts] Lỗi attempt 1: %s — retry...", exc)
                await asyncio.sleep(0.15)
                continue
            logger.warning("[TTS/edge-tts] Lỗi sau retry: %s", exc)
            return


# ---------------------------------------------------------------------------
# Backend: 9Router /v1/audio/speech (Hoài My qua proxy)
# ---------------------------------------------------------------------------

#: Quá ngưỡng này thì bỏ 9Router, chuyển sang Edge (đo: p50 1,2 s, max 1,6 s).
_ROUTER_TTS_TIMEOUT_S = 6.0


async def _synthesise_9router(text: str, voice: str) -> Optional[bytes]:
    """Tổng hợp cả câu qua 9Router (OpenAI-compatible /audio/speech)."""
    try:
        from mateai.config.loader import settings
        from mateai.infrastructure.http.connection_pool import get_tts_http_client

        base_url = getattr(settings.llm, "base_url", "http://localhost:20128/v1").rstrip("/")
        api_key = getattr(settings.llm, "api_key", "")
        model_id = voice if voice.startswith("edge-tts/") else f"edge-tts/{voice}"
        client = await get_tts_http_client()
        resp = await asyncio.wait_for(
            client.post(
                f"{base_url}/audio/speech",
                headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
                json={"model": model_id, "input": text},
            ),
            timeout=_ROUTER_TTS_TIMEOUT_S,
        )
        if resp.status_code == 200 and len(resp.content) > 100:
            return resp.content
        logger.warning("[TTS/9Router] HTTP %d: %s", resp.status_code, resp.text[:120])
    except asyncio.TimeoutError:
        logger.warning("[TTS/9Router] quá %.0fs — chuyển sang Edge-TTS", _ROUTER_TTS_TIMEOUT_S)
    except Exception as exc:
        logger.warning("[TTS/9Router] lỗi: %s", exc)
    return None


# ---------------------------------------------------------------------------
# Backend: ElevenLabs WebSocket streaming (optional, requires API key)
# ---------------------------------------------------------------------------

async def _stream_elevenlabs_ws(
    text: str,
    voice_id: str,
    api_key: str,
    model: str = "eleven_turbo_v2_5",
) -> AsyncGenerator[bytes, None]:
    """
    Stream audio từ ElevenLabs WebSocket API (không dùng REST để tránh delay 2-3s).
    Kết nối: wss://api.elevenlabs.io/v1/text-to-speech/{voice_id}/stream-input
    """
    try:
        import websockets  # type: ignore
        import json
        import base64
    except ImportError:
        logger.warning("[TTS/ElevenLabs] websockets chưa cài. pip install websockets")
        return

    uri = f"wss://api.elevenlabs.io/v1/text-to-speech/{voice_id}/stream-input?model_id={model}"
    has_yielded = False

    try:
        async with websockets.connect(
            uri,
            additional_headers={"xi-api-key": api_key},
            ping_timeout=10,
            close_timeout=5,
        ) as ws:
            # Gửi config ban đầu
            await ws.send(json.dumps({
                "text": " ",
                "voice_settings": {"stability": 0.5, "similarity_boost": 0.75},
                "generation_config": {"chunk_length_schedule": [120, 160, 250, 290]},
            }))

            # Gửi text
            await ws.send(json.dumps({"text": text, "try_trigger_generation": True}))

            # Gửi kết thúc
            await ws.send(json.dumps({"text": ""}))

            # Nhận chunks
            async for raw_msg in ws:
                try:
                    msg = json.loads(raw_msg)
                    audio_b64 = msg.get("audio")
                    if audio_b64:
                        chunk = base64.b64decode(audio_b64)
                        if chunk:
                            has_yielded = True
                            yield chunk
                    if msg.get("isFinal"):
                        break
                except Exception:
                    continue

        if has_yielded:
            logger.debug("[TTS/ElevenLabs] WS streaming OK")

    except Exception as exc:
        logger.warning("[TTS/ElevenLabs] WS lỗi: %s", exc)


# ---------------------------------------------------------------------------
# Main TTS Stream Engine
# ---------------------------------------------------------------------------

class TTSStreamEngine:
    """
    Engine tổng hợp giọng đọc đa nguồn, streaming-first.

    Sử dụng:
        engine = TTSStreamEngine()

        # Stream (yield chunks ngay khi có)
        async for chunk in engine.stream(sentence):
            await ws.send_bytes(chunk)

        # Hoặc lấy toàn bộ (cho cache, HUD base64 fallback)
        audio_bytes = await engine.synthesise(sentence)
    """

    def __init__(
        self,
        voice: Optional[str] = None,
        rate: Optional[str] = None,
    ) -> None:
        # Chỉ giữ giá trị truyền VÀO; còn lại đọc cấu hình mỗi lần đọc. Trước đây đọc một lần lúc tạo singleton (và
        # reset_tts_engine() không ai gọi) → đổi nguồn / giọng trong Voice Studio phải khởi động lại máy chủ mới ăn.
        self._voice_override = voice
        self._rate_override = rate

    # ------------------------------------------------------------------
    # stream() — cache → nguồn đã chọn (Vbee / ElevenLabs) → 9Router → Edge-TTS
    # ------------------------------------------------------------------

    async def stream(
        self,
        text: str,
        voice: Optional[str] = None,
        rate: Optional[str] = None,
    ) -> AsyncGenerator[bytes, None]:
        """
        Yield raw audio bytes ngay khi có — không chờ toàn bộ file hoàn thành.
        Sử dụng cho WebSocket binary frame.
        """
        if not text or not text.strip():
            return
        from mateai.infrastructure.tts.vbee_tts import edge_choice, resolve_tts, stream_vbee

        choice = resolve_tts(voice or self._voice_override)
        effective_rate = rate or self._rate_override or _get_tts_rate()

        # 1. Cache hit (0ms) — đúng giọng đang chọn
        cached = _cache_get(text, choice["tag"])
        if cached and len(cached) > 100:
            logger.info("[TTSStream] Cache HIT (0ms): '%s'", text[:50])
            yield cached
            return

        t0 = time.perf_counter()
        spoken = apply_pronunciation(text)

        # 2. Nguồn đã chọn trong Voice Studio
        if choice["engine"] in ("vbee", "elevenlabs"):
            buf = bytearray()
            if choice["engine"] == "vbee":
                gen = stream_vbee(spoken, choice["voice"], choice["speed"], choice["app_id"], choice["token"])
            else:
                gen = _stream_elevenlabs_ws(text, choice["voice"], choice["api_key"], choice["model"])
            async for chunk in gen:
                buf.extend(chunk)
                yield chunk
            if buf:
                elapsed = (time.perf_counter() - t0) * 1000
                logger.info("[TTSStream] %s: %d bytes / %.0fms", "Vbee" if choice["engine"] == "vbee" else "ElevenLabs",
                            len(buf), elapsed)
                _cache_set(text, bytes(buf), choice["voice"], tag=choice["tag"])
                return
            logger.warning("[TTSStream] %s không trả audio — dùng 9Router / Edge-TTS dự phòng", choice["engine"])

        # 3. 9Router Hoài My (nhanh nhất theo số đo) — giọng Edge (Vbee lỗi thì về giọng Edge của cấu hình / Hoài My)
        edge = edge_choice(voice or self._voice_override or _get_tts_voice())
        router_bytes = await _synthesise_9router(spoken, edge["voice"])
        if router_bytes:
            elapsed = (time.perf_counter() - t0) * 1000
            logger.info("[TTSStream] 9Router: %d bytes / %.0fms", len(router_bytes), elapsed)
            _cache_set(text, router_bytes, edge["voice"], tag=edge["tag"])
            yield router_bytes
            return

        # 4. Edge-TTS stream trực tiếp (dự phòng) — lưu cache theo tag giọng Edge
        edge_buf = bytearray()
        async for chunk in _stream_edge_tts(spoken, edge["voice"], effective_rate, cache_key=text):
            edge_buf.extend(chunk)
            yield chunk

        if edge_buf:
            elapsed = (time.perf_counter() - t0) * 1000
            logger.info("[TTSStream] Edge-TTS: %d bytes / %.0fms", len(edge_buf), elapsed)
            _cache_set(text, bytes(edge_buf), edge["voice"], tag=edge["tag"])
            return

        logger.error("[TTSStream] TẤT CẢ nguồn TTS thất bại cho: '%s'", text[:60])

    # ------------------------------------------------------------------
    # synthesise() — collect tất cả chunks thành bytes (cho cache/base64)
    # ------------------------------------------------------------------

    async def synthesise(
        self,
        text: str,
        voice: Optional[str] = None,
        rate: Optional[str] = None,
    ) -> Optional[bytes]:
        """
        Tổng hợp toàn bộ câu thành bytes (blocking-style, có cache).
        Dùng cho: Acoustic ACK warmup, HUD base64 fallback, lưu cache.
        """
        from mateai.infrastructure.tts.vbee_tts import resolve_tts
        cached = _cache_get(text, resolve_tts(voice or self._voice_override)["tag"])
        if cached and len(cached) > 100:
            return cached

        buf = bytearray()
        async for chunk in self.stream(text, voice=voice, rate=rate):
            buf.extend(chunk)

        return bytes(buf) if buf else None


# ---------------------------------------------------------------------------
# Module-level singleton (khởi tạo lazy)
# ---------------------------------------------------------------------------

_tts_engine: Optional[TTSStreamEngine] = None


def get_tts_engine() -> TTSStreamEngine:
    """Lấy singleton TTSStreamEngine (tạo mới nếu cần)."""
    global _tts_engine
    if _tts_engine is None:
        _tts_engine = TTSStreamEngine()
    return _tts_engine


def reset_tts_engine() -> None:
    """Reset singleton (dùng khi config thay đổi)."""
    global _tts_engine
    _tts_engine = None
