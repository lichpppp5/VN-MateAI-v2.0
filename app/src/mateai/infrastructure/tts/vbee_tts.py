# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 Dương Thanh Lịch — VN-MateAI. See LICENSE and NOTICE.
"""
mateai/infrastructure/tts/vbee_tts.py
=====================================
Nguồn đọc Vbee AIVoice (studio.vbee.vn) + chọn nguồn đọc đang dùng.

Vbee Realtime API (https://api-docs.vbee.vn/vbee-api/text-to-speech/realtime-api):
  POST https://api.vbee.vn/v1/tts   Authorization: Bearer <token>, App-Id: <app id>
  body {"text" ≤ 300 ký tự, "mode": "sync", "voiceCode", "outputFormat": "mp3", "speed" 0,25–1,9}
  → trả từng khối audio nhị phân ngay khi có (byte đầu ~0,5 s theo tài liệu). Lỗi: JSON {"error": {code, message}}.
Danh sách giọng: GET https://vbee.vn/api/public/v1/voices (cùng hai header).
Token tạo ở studio.vbee.vn/apps (Tích hợp API → Tạo ứng dụng) và CÓ THỂ HẾT HẠN (7/30/60/90 ngày, hoặc vĩnh viễn).

`resolve_tts()` là MỘT nơi quyết định nguồn + giọng đang dùng — engine đọc và khoá cache âm thanh cùng gọi nó, nên câu
đã lưu bằng giọng này không bao giờ bị phát lại khi đã đổi sang giọng khác (trước 2026-10-12 cache chỉ khoá theo chữ).
"""
from __future__ import annotations

import logging
import re
import time
from typing import Any, AsyncGenerator, Dict, List, Optional

logger = logging.getLogger(__name__)

VBEE_TTS_URL = "https://api.vbee.vn/v1/tts"
VBEE_VOICES_URL = "https://vbee.vn/api/public/v1/voices"
DEFAULT_VBEE_VOICE = "hn_female_ngochuyen_full_48k-fhg"
DEFAULT_EDGE_VOICE = "vi-VN-HoaiMyNeural"
#: Giọng Vbee Realtime API đang hỗ trợ (tài liệu Vbee) — giọng khác có thể bị từ chối ở chế độ realtime.
REALTIME_VOICE_NAMES = ("Ngọc Huyền", "Tường Vy", "Mai Phương", "Lan Trinh", "Thảo Trinh")
#: Vbee Realtime giới hạn 300 ký tự mỗi lần gọi.
VBEE_MAX_CHARS = 300


_cfg_cache: Dict[str, Any] = {"mtime": None, "audio": {}}


def audio_cfg() -> Dict[str, Any]:
    """
    Khối `audio` của config.json (Voice Studio lưu vào đây), token đã giải mã. Đọc lại chỉ khi file đổi (mtime).

    KHÔNG dùng `settings.audio`: AppSettings bỏ khoá lạ nên nó luôn None — trước 2026-10-12 nhánh ElevenLabs đọc từ
    đó và vì vậy chưa từng chạy dù đã nhập khoá.
    """
    import os
    if os.environ.get("VNMATEAI_TTS_PAID_PROVIDERS", "").strip().lower() == "off":
        return {}                                        # bộ test: không gọi nguồn trả phí (tests/conftest.py)
    try:
        from mateai.config.loader import CONFIG_PATH, get_config_section
        mtime = CONFIG_PATH.stat().st_mtime_ns
        if _cfg_cache["mtime"] != mtime:
            _cfg_cache["audio"], _cfg_cache["mtime"] = dict(get_config_section("audio")), mtime
        return _cfg_cache["audio"]
    except Exception:  # noqa: BLE001
        return {}


def is_vbee_voice(voice: Optional[str]) -> bool:
    """Mã giọng Vbee dạng `hn_female_ngochuyen_full_48k-fhg` (giọng Edge dạng `vi-VN-HoaiMyNeural`)."""
    v = str(voice or "")
    return bool(v) and "Neural" not in v and bool(re.match(r"^[a-z]{2,}_(female|male)_", v))


def speed_from_rate(rate: Any) -> float:
    """Tốc độ đọc của Voice Studio (+15 = nhanh hơn 15%) → `speed` của Vbee (1,15), giới hạn 0,25–1,9."""
    try:
        pct = float(str(rate).replace("%", "").strip() or 0)
    except (TypeError, ValueError):
        pct = 0.0
    return round(max(0.25, min(1.9, 1.0 + pct / 100.0)), 2)


def resolve_tts(voice: Optional[str] = None) -> Dict[str, Any]:
    """
    Nguồn + giọng đang dùng: {"engine": "vbee" | "elevenlabs" | "edge", "voice", "tag", …thông tin đăng nhập}.
    `tag` vào khoá cache âm thanh; giọng mặc định Hoài My giữ tag rỗng → cache cũ vẫn dùng được.
    Chọn Vbee / ElevenLabs mà thiếu thông tin đăng nhập → coi như Edge (không bao giờ gọi một nguồn chắc chắn lỗi).
    """
    cfg = audio_cfg()
    engine = str(cfg.get("tts_engine") or "edge-tts")
    if engine == "vbee" and cfg.get("vbee_app_id") and cfg.get("vbee_token"):
        v = voice if is_vbee_voice(voice) else (cfg.get("tts_voice") if is_vbee_voice(cfg.get("tts_voice")) else DEFAULT_VBEE_VOICE)
        return {"engine": "vbee", "voice": v, "tag": f"vbee:{v}", "app_id": str(cfg["vbee_app_id"]).strip(),
                "token": str(cfg["vbee_token"]).strip(), "speed": speed_from_rate(cfg.get("speech_rate", 0))}
    if engine == "elevenlabs" and cfg.get("elevenlabs_api_key") and cfg.get("elevenlabs_voice_id"):
        vid = str(cfg["elevenlabs_voice_id"])
        return {"engine": "elevenlabs", "voice": vid, "tag": f"el:{vid}", "api_key": cfg["elevenlabs_api_key"],
                "model": cfg.get("elevenlabs_model", "eleven_turbo_v2_5")}
    return edge_choice(voice or cfg.get("tts_voice"))


def edge_choice(voice: Optional[str]) -> Dict[str, Any]:
    """Giọng Edge / 9Router (cũng là đường dự phòng khi Vbee / ElevenLabs lỗi)."""
    v = voice if voice and not is_vbee_voice(voice) else DEFAULT_EDGE_VOICE
    return {"engine": "edge", "voice": v, "tag": "" if v == DEFAULT_EDGE_VOICE else f"edge:{v}"}


def split_for_vbee(text: str, limit: int = VBEE_MAX_CHARS) -> List[str]:
    """Cắt câu dài thành các đoạn ≤ `limit` ký tự, ưu tiên ranh giới câu, rồi dấu phẩy, rồi khoảng trắng."""
    text = re.sub(r"\s+", " ", str(text or "")).strip()
    parts: List[str] = []
    while len(text) > limit:
        cut = -1
        for pattern in (r"[.!?…;:](?=\s)", r",(?=\s)", r"\s"):
            hits = [m.end() for m in re.finditer(pattern, text[: limit + 1])]
            if hits:
                cut = hits[-1]
                break
        if cut <= 0:
            cut = limit
        parts.append(text[:cut].strip())
        text = text[cut:].strip()
    if text:
        parts.append(text)
    return parts


def _headers(app_id: str, token: str) -> Dict[str, str]:
    return {"Authorization": f"Bearer {token}", "App-Id": app_id, "Content-Type": "application/json"}


def _explain(status: int, body: str) -> str:
    """Lỗi Vbee → câu dễ hiểu cho nhật ký / giao diện."""
    code, msg = "", body[:160]
    try:
        import json
        err = (json.loads(body) or {}).get("error") or {}
        code, msg = str(err.get("code") or ""), str(err.get("message") or msg)
    except Exception:  # noqa: BLE001
        pass
    if status == 401:
        return f"Vbee từ chối token / App ID (401 {code}) — token có thể đã hết hạn: tạo lại ở studio.vbee.vn/apps"
    if status == 429:
        return f"Vbee: quá nhiều yêu cầu đồng thời (429 {code})"
    if status == 400 and "credit" in msg.lower():
        return f"Vbee: hết điểm API ({code}) — nạp thêm ở studio.vbee.vn"
    return f"Vbee HTTP {status} {code}: {msg}"


#: Gói Vbee chưa có Realtime API (lỗi "not supported in user package") → dùng Batch API, thử lại Realtime sau 6 giờ.
_realtime_blocked_until = 0.0
#: Lý do lỗi gần nhất (nút "Nghe thử" hiện cho người dùng).
last_error = ""
#: Batch: trần thời gian một câu — quá thì engine dùng 9Router / Edge (hàng đợi TTS cho mỗi câu tối đa 14 s).
BATCH_BUDGET_S = 12.0
#: Batch bắt buộc có webhookUrl. Trỏ về CHÍNH máy chủ này (Vbee không tới được) — không gửi link audio cho bên thứ ba;
#: kết quả lấy bằng cách hỏi GET /v1/tts/requests/{requestId}.
BATCH_WEBHOOK = "https://localhost/api/v1/tts/vbee/callback"


def realtime_available() -> bool:
    return time.time() >= _realtime_blocked_until


async def _stream_realtime(client: Any, part: str, voice: str, speed: float, app_id: str, token: str,
                           timeout: Any) -> AsyncGenerator[bytes, None]:
    """Realtime API: trả khối audio ngay khi có. Gói không hỗ trợ → đánh dấu, không yield gì (nơi gọi chuyển Batch)."""
    global _realtime_blocked_until, last_error
    body = {"text": part, "mode": "sync", "voiceCode": voice, "outputFormat": "mp3", "bitrate": 128, "speed": speed}
    async with client.stream("POST", VBEE_TTS_URL, headers=_headers(app_id, token), json=body, timeout=timeout) as resp:
        ctype = resp.headers.get("content-type", "")
        if resp.status_code != 200 or "json" in ctype:
            raw = (await resp.aread()).decode("utf-8", "replace")
            if "not supported in user package" in raw.lower():
                _realtime_blocked_until = time.time() + 6 * 3600
                logger.info("[TTS/Vbee] Gói hiện tại chưa có Realtime API — dùng Batch API (thử lại Realtime sau 6 giờ)")
                return
            last_error = _explain(resp.status_code, raw)
            logger.warning("[TTS/Vbee] %s", last_error)
            raise _VbeeFailed(last_error)
        async for chunk in resp.aiter_bytes():
            if chunk:
                yield chunk


class _VbeeFailed(Exception):
    """Vbee trả lỗi thật (token, điểm, giọng…) — không thử đường khác, để engine dùng 9Router / Edge."""


async def _stream_batch(client: Any, text: str, voice: str, speed: float, app_id: str, token: str) -> AsyncGenerator[bytes, None]:
    """Batch API: gửi → nhận requestId → hỏi trạng thái tới COMPLETED → tải audioLink. Đo 2026-10-12: 2,3–3,8 s một câu."""
    global last_error
    import asyncio
    t0 = time.perf_counter()
    body = {"text": text, "mode": "async", "webhookUrl": BATCH_WEBHOOK, "voiceCode": voice,
            "outputFormat": "mp3", "bitrate": 128, "speed": speed}
    resp = await client.post(VBEE_TTS_URL, headers=_headers(app_id, token), json=body, timeout=10.0)
    if resp.status_code >= 300:
        last_error = _explain(resp.status_code, resp.text)
        logger.warning("[TTS/Vbee] Batch: %s", last_error)
        raise _VbeeFailed(last_error)
    rid = (resp.json() or {}).get("requestId")
    if not rid:
        raise _VbeeFailed("Vbee Batch không trả requestId")
    await asyncio.sleep(0.8)                                  # câu ngắn nhất cũng cần ~2 s — đừng hỏi dồn
    link = None
    while time.perf_counter() - t0 < BATCH_BUDGET_S:
        g = await client.get(f"{VBEE_TTS_URL}/requests/{rid}", headers=_headers(app_id, token), timeout=5.0)
        data = g.json() if g.status_code == 200 else {}
        status = str(data.get("status") or "")
        if status == "COMPLETED":
            link = data.get("audioLink")
            break
        if status and status != "PROCESSING":
            err = data.get("error") or {}
            last_error = f"Vbee Batch {status}: {err.get('message') or data.get('error_message') or ''}".strip()
            logger.warning("[TTS/Vbee] %s", last_error)
            raise _VbeeFailed(last_error)
        await asyncio.sleep(0.25)
    if not link:
        last_error = f"Vbee Batch quá {BATCH_BUDGET_S:.0f} s chưa xong"
        logger.warning("[TTS/Vbee] %s — dùng nguồn dự phòng", last_error)
        raise _VbeeFailed(last_error)
    async with client.stream("GET", link, timeout=10.0, follow_redirects=True) as a:
        if a.status_code != 200:
            raise _VbeeFailed(f"Không tải được audio Vbee (HTTP {a.status_code})")
        async for chunk in a.aiter_bytes():
            if chunk:
                yield chunk
    logger.debug("[TTS/Vbee] Batch xong sau %.0f ms", (time.perf_counter() - t0) * 1000)


async def stream_vbee(text: str, voice: str, speed: float, app_id: str, token: str,
                      timeout_s: float = 8.0) -> AsyncGenerator[bytes, None]:
    """
    Đọc `text` bằng Vbee: Realtime API (byte đầu ~0,5 s) nếu gói hỗ trợ, không thì Batch API (cả câu ~2,3–3,8 s).
    Lỗi → ghi nhật ký + `last_error`, không ném (engine tự dùng 9Router / Edge-TTS).
    """
    import httpx
    from mateai.infrastructure.http.connection_pool import get_tts_http_client

    client = await get_tts_http_client()
    timeout = httpx.Timeout(timeout_s, connect=5.0)
    try:
        if realtime_available():
            got = 0
            parts = split_for_vbee(text)
            for n, part in enumerate(parts):
                async for chunk in _stream_realtime(client, part, voice, speed, app_id, token, timeout):
                    got += len(chunk)
                    yield chunk
                if not got and n == 0 and not realtime_available():
                    break                                      # gói không có Realtime → Batch bên dưới
                if not got:
                    return
            if got:
                return
        async for chunk in _stream_batch(client, text[:100000], voice, speed, app_id, token):
            yield chunk
    except _VbeeFailed:
        return
    except Exception as exc:  # noqa: BLE001
        global last_error
        last_error = f"Không kết nối được Vbee: {exc}"
        logger.warning("[TTS/Vbee] %s", last_error)


async def list_vbee_voices(app_id: str, token: str, language: str = "vi-VN") -> Dict[str, Any]:
    """Giọng Vbee của tài khoản (giọng Vbee + giọng cá nhân). Giọng hỗ trợ Realtime xếp trước, có cờ `realtime`."""
    from mateai.infrastructure.http.connection_pool import get_tts_http_client

    client = await get_tts_http_client()
    voices: List[Dict[str, Any]] = []
    for ownership in ("VBEE", "PERSONAL"):
        cursor: Optional[str] = None
        for _ in range(10):                                   # tối đa 10 trang × 100 giọng
            params: Dict[str, Any] = {"voiceOwnership": ownership, "languageCode": language, "limit": 100}
            if cursor:
                params["cursor"] = cursor
            resp = await client.get(VBEE_VOICES_URL, headers=_headers(app_id, token), params=params, timeout=10.0)
            if resp.status_code != 200:
                if ownership == "PERSONAL":
                    break                                     # tài khoản không có giọng cá nhân: bỏ qua
                return {"ok": False, "error": _explain(resp.status_code, resp.text), "voices": []}
            result = (resp.json() or {}).get("result") or {}
            for v in result.get("voices") or []:
                name = str(v.get("name") or v.get("code") or "")
                voices.append({"code": v.get("code"), "name": name, "gender": v.get("gender"),
                               "language_code": v.get("language_code"), "demo": v.get("demo"),
                               "credit_factor": v.get("credit_factor"), "personal": ownership == "PERSONAL",
                               "realtime": any(n in name for n in REALTIME_VOICE_NAMES)})
            pag = result.get("pagination") or {}
            cursor = pag.get("next_cursor") if pag.get("has_next_page") else None
            if not cursor:
                break
    seen, unique = set(), []
    for v in voices:
        if v["code"] and v["code"] not in seen:
            seen.add(v["code"])
            unique.append(v)
    unique.sort(key=lambda v: (not v["realtime"], not v["personal"], v["name"]))
    return {"ok": True, "voices": unique}


async def vbee_try(text: str, voice: str, speed: float, app_id: str, token: str) -> Dict[str, Any]:
    """Một lần đọc thử (nút "Nghe thử" trong Voice Studio) — cùng đường với HUD / robot (Realtime, không có thì Batch).
    Trả {"ok", "audio", "ms", "mode"} hoặc {"ok": False, "error"} — lỗi được nói rõ, không nuốt."""
    global last_error
    last_error = ""
    t0 = time.perf_counter()
    buf = bytearray()
    async for chunk in stream_vbee(text or "Xin chào", voice, speed, app_id, token):
        buf.extend(chunk)
    if len(buf) < 100:
        return {"ok": False, "error": last_error or "Vbee không trả audio"}
    return {"ok": True, "audio": bytes(buf), "ms": round((time.perf_counter() - t0) * 1000),
            "mode": "realtime" if realtime_available() else "batch"}
