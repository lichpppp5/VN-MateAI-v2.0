# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 Dương Thanh Lịch — VN-MateAI. See LICENSE and NOTICE.
"""
core/xiaozhi_gateway.py
========================
Phase 43: Xiaozhi Desktop Companion Protocol Gateway.

Key Responsibilities:
  1. LCD/OLED Display & Emotion Control (Step 1):
     - Send UI JSON payloads to ESP32 displays (ST7789, GC9A01, SSD1306):
       * listening  : {"type": "ui", "state": "listening", "emotion": "focused"}
       * processing : {"type": "ui", "state": "processing", "emotion": "thinking"}
       * alert      : {"type": "ui", "state": "alert", "text": "IIS Server 503 Error!"}
       * idle       : {"type": "ui", "state": "idle", "emotion": "sleeping"}
       * speaking   : {"type": "ui", "state": "speaking", "emotion": "happy"}
  2. Barge-in / Interruption Engine (Step 2):
     - Detects interruption frame {"type": "interrupt"} or flag `is_wakeup: true` while speaking.
     - Instantly cancels running LLM / Claude tasks.
     - Flushes / stops ESP32 audio playback buffer.
     - Plays 0ms reflex from local cache (Phase 36): "Dạ, anh nói đi em nghe đây."
     - Transitions UI back to listening / focused state.
  3. High-Fidelity Audio Streaming for I2S (Step 3):
     - 24kHz / 32kHz high-quality audio streaming.
     - Jitter buffer pre-fill burst (primes ESP32 DMA ring buffer) followed by smooth
       duration-based pacing to prevent buffer underrun and crackling on custom DACs/Amps.
  4. Autonomous Push Notification Wake (Step 4):
     - Direct linkage with Autonomous Sentinel.
     - Wakes up Desktop Robot with flashing red eyes / alert face on LCD.
     - Voices: "Báo cáo anh, hệ thống máy chủ vừa ghi nhận cảnh báo..."
"""

from __future__ import annotations

import asyncio
import io
import json
import logging
import random
import string
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, AsyncGenerator, Dict, List, Optional, Set

from fastapi import WebSocket, WebSocketDisconnect

from mateai.infrastructure.tts.audio_cache import check_cached_audio, get_cached_audio_bytes, save_to_cache
from mateai.infrastructure.audio.audio_processor import audio_engine
from mateai.application.voice.speech_text import sanitise_for_tts, shorten_for_speech
from mateai.infrastructure.tts.tts_stream_engine import get_tts_engine
from mateai.config.loader import settings

logger = logging.getLogger("mateai.interfaces.websocket.xiaozhi_gateway")

def _is_stop_reply(text: str) -> bool:
    from mateai.application.voice.voice_session import is_stop_reply
    return is_stop_reply(text)


def _topo(kind: str, **kw: Any) -> None:
    """Báo trang giám sát /admin/topology (không bao giờ làm hỏng luồng robot)."""
    try:
        from mateai.application.operations.topology_events import publish
        publish(kind, **kw)
    except Exception:  # noqa: BLE001
        pass


def _pcm16_to_wav(pcm: bytes, rate: int = 16000) -> bytes:
    """PCM 16-bit mono thô -> WAV (bộ nhận dạng cần định dạng có header)."""
    import wave
    buf = io.BytesIO()
    with wave.open(buf, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(rate)
        wf.writeframes(pcm)
    return buf.getvalue()


def pcm_levels(pcm: bytes) -> Dict[str, Optional[float]]:
    """RMS / đỉnh của PCM16 (số mẫu thô + dBFS) — chỉ số đo, không có nội dung lời nói."""
    import math
    import numpy as np
    a = np.frombuffer(pcm[: len(pcm) // 2 * 2], dtype=np.int16).astype(np.float32)
    if a.size == 0:
        return {"rms": None, "peak": None, "rms_dbfs": None, "seconds": 0.0}
    rms = float(np.sqrt(np.mean(a * a)))
    return {"rms": round(rms, 1), "peak": float(np.abs(a).max()),
            "rms_dbfs": round(20 * math.log10(max(rms, 1.0) / 32768.0), 1),
            "seconds": round(a.size / 16000.0, 2)}


def audio_check_verdict(text: str, wake_ok: bool, speech_rms: Optional[float], noise_rms: Optional[float]) -> Dict[str, Any]:
    """Kết luận kiểm tra âm thanh + gợi ý (thuần — test được)."""
    import math
    snr = None
    if speech_rms and noise_rms and noise_rms > 0:
        snr = round(20 * math.log10(speech_rms / noise_rms), 1)
    if not text:
        grade, advice = "kém", "Không nhận dạng được câu nói. Nói to / gần robot hơn, hoặc giảm tiếng ồn quanh robot."
    elif snr is not None and snr < 10:
        grade, advice = "yếu", (f"Giọng chỉ to hơn tiếng ồn {snr} dB (nên ≥ 15 dB). Ngồi gần hơn hoặc giảm ồn "
                                "(quạt, điều hoà, TV).")
    elif not wake_ok:
        grade, advice = "trung bình", ("Nhận dạng được câu nhưng không nghe ra tên gọi. Gọi rõ tên trợ lý ở đầu câu, "
                                       "hoặc đổi 'Câu đánh thức' cho dễ nhận dạng hơn.")
    else:
        grade, advice = "tốt", "Micro nghe rõ ở khoảng cách này; gọi tên được nhận ra."
    return {"grade": grade, "snr_db": snr, "advice": advice}


def _clip_level(clip: bytes) -> str:
    """Mức tín hiệu của đoạn PCM16 (RMS, đỉnh) — chỉ số đo, không có nội dung lời nói."""
    import numpy as np
    a = np.frombuffer(clip[: len(clip) // 2 * 2], dtype=np.int16).astype(np.float32)
    if a.size == 0:
        return "rỗng"
    return f"RMS {int(np.sqrt(np.mean(a * a)))}, đỉnh {int(np.abs(a).max())}"


#: Hội thoại liên tục sau mỗi câu trả lời: nghe 10 s; im lặng -> hỏi lại "Anh còn cần
#: em hỗ trợ gì nữa không ạ?" và nghe thêm 20 s; vẫn im lặng (tổng 30 s) -> tạm biệt.
ROBOT_FOLLOW_UP_LISTEN_MS = 10000
ROBOT_FOLLOW_UP_REASK_LISTEN_MS = 20000
#: Âm thanh gửi trước nhịp phát tối đa ngần này giây (đủ đệm mạng, không dồn hàng
#: chục giây — trước đây gửi nhanh gấp 1,4 lần: máy chủ tưởng đã đọc xong khi robot
#: còn vài giây chưa phát, chuyển sang nghe, mic thu chính giọng robot và cắt ngang).
PLAYBACK_LEAD_S = 0.5
#: PCM 16 kHz mono 16 bit = 32.000 byte mỗi giây.
PCM_BYTES_PER_S = 32000


#: Đoạn câu gọi tên robot gửi lúc nghỉ (PCM16 16 kHz): tối thiểu 0,3 s, tối đa 5 s.
WAKE_CLIP_MIN_BYTES = 9600
WAKE_CLIP_MAX_BYTES = 160000


def convert_to_pcm16_16k(audio_bytes: bytes) -> bytes:
    """Convert MP3/WAV audio bytes to 16000Hz 16-bit mono raw PCM for ESP32 I2S MAX98357A speaker."""
    if not audio_bytes:
        return b""
    try:
        import av, io
        container = av.open(io.BytesIO(audio_bytes))
        resampler = av.AudioResampler(format='s16', layout='mono', rate=16000)
        chunks = []
        for frame in container.decode(audio=0):
            for resampled in resampler.resample(frame):
                chunks.append(resampled.to_ndarray().tobytes())
        return b"".join(chunks)
    except Exception as exc:
        try:
            from pydub import AudioSegment
            import io
            seg = AudioSegment.from_file(io.BytesIO(audio_bytes)).set_frame_rate(16000).set_channels(1).set_sample_width(2)
            return seg.raw_data
        except Exception:
            return audio_bytes


# ─── Pairing Code Registry ──────────────────────────────────────────────────
# Cho phép robot kết nối qua mã 6 số thay vì nhập IP server.
# Robot sinh mã, gửi trong frame "hello", server map mã → device_id.
# User nhập mã vào Web UI để xác nhận ghép cặp (hoặc chỉ cần nhập mã để tìm robot).

class PairingCodeRegistry:
    """Thread-safe registry ánh xạ mã 6 số → device_id của robot đang trực tuyến.

    Flow:
      1. Robot khởi động → load pairing_code từ NVS (hoặc sinh mới rồi lưu vào NVS).
      2. Robot gửi frame `hello` kèm `pairing_code` lên server.
      3. Server gọi `register(code, device_id)` để ghi nhận ánh xạ.
      4. Web UI gọi `GET /api/v1/robot/pair?code=XXXXXX` → nhận `device_id`.
      5. Khi robot ngắt kết nối, `unregister(device_id)` xoá ánh xạ.
    """

    _CODE_TTL_SECONDS = 3600  # Mã hết hiệu lực sau 1 giờ nếu robot mất kết nối

    def __init__(self) -> None:
        # {code: {"device_id": str, "expires_at": datetime}}
        self._codes: Dict[str, Dict[str, Any]] = {}
        # {device_id: code} — reverse mapping để unregister nhanh
        self._device_to_code: Dict[str, str] = {}
        self._lock = asyncio.Lock()

    async def register(self, code: str, device_id: str) -> None:
        """Đăng ký mã pairing cho robot. Mã cũ của cùng device_id sẽ bị xoá."""
        code = code.strip().upper()
        async with self._lock:
            # Xoá mã cũ nếu device_id đã có mã khác
            old_code = self._device_to_code.get(device_id)
            if old_code and old_code != code:
                self._codes.pop(old_code, None)
            expires_at = datetime.utcnow() + timedelta(seconds=self._CODE_TTL_SECONDS)
            self._codes[code] = {"device_id": device_id, "expires_at": expires_at}
            self._device_to_code[device_id] = code
        logger.info("[PairingCode] Đã đăng ký mã '%s' → device [%s]", code, device_id)

    async def unregister(self, device_id: str) -> None:
        """Xoá ánh xạ khi robot ngắt kết nối."""
        async with self._lock:
            code = self._device_to_code.pop(device_id, None)
            if code:
                self._codes.pop(code, None)
                logger.info("[PairingCode] Đã xoá mã '%s' của device [%s]", code, device_id)

    def lookup(self, code: str) -> Optional[str]:
        """Tra cứu device_id từ mã 6 số. Trả về None nếu không tồn tại hoặc hết hạn."""
        code = code.strip().upper()
        entry = self._codes.get(code)
        if not entry:
            return None
        if datetime.utcnow() > entry["expires_at"]:
            # Hết hạn — dọn dẹp ngầm
            self._codes.pop(code, None)
            self._device_to_code.pop(entry["device_id"], None)
            return None
        return entry["device_id"]

    def get_code_for_device(self, device_id: str) -> Optional[str]:
        """Lấy mã pairing hiện tại của một device."""
        return self._device_to_code.get(device_id)

    def list_all(self) -> List[Dict[str, Any]]:
        """Liệt kê tất cả ánh xạ đang hoạt động (dùng cho admin API)."""
        now = datetime.utcnow()
        return [
            {
                "code": code,
                "device_id": info["device_id"],
                "expires_at": info["expires_at"].isoformat(),
            }
            for code, info in self._codes.items()
            if now <= info["expires_at"]
        ]

    @staticmethod
    def generate_code() -> str:
        """Sinh mã 6 ký tự chữ số (dễ nhập bằng giọng nói / bàn phím)."""
        return "".join(random.choices(string.digits, k=6))


# Singleton pairing registry — dùng chung toàn server
pairing_registry = PairingCodeRegistry()

# Thư mục gốc dự án (đúng cả bản đóng gói) — không suy từ vị trí file mã nguồn.
from mateai.config.loader import settings as _settings  # noqa: E402

_PROJECT_ROOT = Path(_settings.PROJECT_ROOT)

# Reflex phrases pre-cached for 0ms latency
REFLEX_BARGE_IN_TEXT = "Dạ, anh nói đi em nghe đây."
REFLEX_SENTINEL_PREFIX = "Báo cáo anh, hệ thống máy chủ vừa ghi nhận cảnh báo..."


class XiaozhiNode:
    """Represents a connected Xiaozhi Desktop Companion / ESP32 hardware node."""

    def __init__(self, device_id: str, websocket: WebSocket, client_host: str) -> None:
        self.device_id = device_id
        self.websocket = websocket
        self.client_host = client_host
        self.connected_at = datetime.utcnow().isoformat()
        self.last_active = datetime.utcnow().isoformat()

        # UI & Hardware state
        self.state: str = "idle"             # idle | listening | processing | speaking | alert
        self.emotion: str = "sleeping"       # sleeping | focused | thinking | happy | alert | shocked
        self.screen_text: Optional[str] = None
        self.audio_format: str = "mp3_24k"   # mp3_24k | pcm_24k | opus
        # Thời gian STT của câu gần nhất (ms) — gắn vào trace của lượt thoại.
        self.last_stt_ms: Optional[float] = None
        # Đoạn nghi là câu gọi tên (robot gửi lúc nghỉ) đang nhận / đang xét.
        self.wake_buf: Optional[bytearray] = None
        self.wake_busy: bool = False
        # Danh tính RBAC khi robot chạy tool. "device:<id>" chỉ khi robot xác thực
        # bằng token RIÊNG — khi đó dùng quyền đặt cho thiết bị ở Web Portal.
        self.caller: str = device_id
        # Hội thoại liên tục: 0 = không; 1 = vừa trả lời, đang nghe 10 s; 2 = đã hỏi
        # lại, nghe nốt 20 s rồi tạm biệt.
        self.follow_up: int = 0
        # Thời điểm (perf_counter) robot phát XONG phần âm thanh đã gửi — gửi theo
        # nhịp phát thật, biết chính xác lúc nào đọc xong.
        self.play_end: float = 0.0

        # Firmware metadata — được lấp đầy khi thiết bị gửi frame 'hello'.
        # Trước khi handshake thì là "unknown" / rỗng.
        self.firmware_version: str = "unknown"
        self.capabilities: str = ""
        self.features: List[str] = []
        # Số đo micro robot gửi định kỳ (wake_stats: ồn nền / đỉnh RMS) — trước chỉ ghi log.
        self.audio_stats: Dict[str, Any] = {}
        # Báo cáo trạng thái firmware ≥ 54 (âm lượng, Wi-Fi, bộ nhớ, thời gian chạy).
        self.status_report: Dict[str, Any] = {}
        # Kiểm tra âm thanh đang chạy / kết quả gần nhất (Portal -> Robot).
        self.audio_check: Optional[Dict[str, Any]] = None

        # Active tasks & cancellation
        self.active_task: Optional[asyncio.Task] = None
        self.cancel_event: asyncio.Event = asyncio.Event()
        self.audio_buffer: io.BytesIO = io.BytesIO()

        # Audio stream lock to prevent overlapping TTS
        self.stream_lock = asyncio.Lock()

        # Phase 50: Silero VAD detector for 500ms zero-wait silence cutoff
        from mateai.infrastructure.audio.audio_processor import SileroVADDetector
        self.vad_detector = SileroVADDetector(
            threshold=0.5,
            min_silence_duration_ms=500,
            sample_rate=16000,
        )

    def to_dict(self) -> Dict[str, Any]:
        """Telemetry representation for health monitor and Web Portal."""
        return {
            "device_id": self.device_id,
            "client_host": self.client_host,
            "connected_at": self.connected_at,
            "last_active": self.last_active,
            "state": self.state,
            "emotion": self.emotion,
            "screen_text": self.screen_text,
            "audio_format": self.audio_format,
            "firmware_version": self.firmware_version,
            "capabilities": self.capabilities,
            "is_busy": self.active_task is not None and not self.active_task.done(),
        }


class XiaozhiGateway:
    """Singleton Manager for Xiaozhi Desktop Companion Protocol."""

    def __init__(self) -> None:
        self._nodes: Dict[str, XiaozhiNode] = {}
        self._lock = asyncio.Lock()
        # Tham chiếu tới global pairing registry
        self.pairing_registry = pairing_registry
        # Cử động tự nhiên (application/devices/robot_behavior): khung đã gửi theo thiết bị +
        # vòng kiểm tra mỗi phút (đổi cấu hình, vào / ra giờ yên lặng).
        self._behavior_sent: Dict[str, Dict[str, Any]] = {}
        self._behavior_task: Optional["asyncio.Task[None]"] = None

    # -----------------------------------------------------------------------
    # Natural behaviour (idle motion, speech gestures)
    # -----------------------------------------------------------------------

    async def push_behavior(self, device_id: str, force: bool = False) -> bool:
        """Gửi khung `behavior` khi khác lần gửi trước (hoặc `force`). Lỗi cấu hình không chặn robot."""
        from mateai.application.devices.robot_behavior import behavior_frame
        node = self._nodes.get(device_id)
        if not node or not node.websocket:
            return False
        try:
            frame = behavior_frame()
        except Exception as exc:  # noqa: BLE001
            logger.warning("[Xiaozhi] Cấu hình robot_behavior lỗi: %s", exc)
            return False
        if not force and self._behavior_sent.get(device_id) == frame:
            return False
        try:
            await node.websocket.send_text(json.dumps(frame, ensure_ascii=False))
        except Exception:
            return False
        self._behavior_sent[device_id] = frame
        return True

    def behavior_allows_gestures(self, device_id: str) -> bool:
        """Chỉ firmware khai báo `natural_behavior` (v55+): cử chỉ chạy trong task riêng. Firmware
        cũ chạy cử chỉ NGAY trong vòng xử lý WebSocket -> âm thanh câu trả lời bị giật."""
        node = self._nodes.get(device_id)
        feats = set(getattr(node, "features", None) or []) if node else set()
        if not node or ("natural_behavior" not in feats and "natural_behavior" not in (node.capabilities or "")):
            return False
        return bool((self._behavior_sent.get(device_id) or {}).get("speech_gestures"))

    def _ensure_behavior_loop(self) -> None:
        if self._behavior_task is None or self._behavior_task.done():
            self._behavior_task = asyncio.create_task(self._behavior_loop())

    async def _behavior_loop(self, interval_s: float = 60.0) -> None:
        while True:
            await asyncio.sleep(interval_s)
            for device_id in list(self._nodes):
                await self.push_behavior(device_id)

    # -----------------------------------------------------------------------
    # Node Registry
    # -----------------------------------------------------------------------

    def get_node(self, device_id: str) -> Optional[XiaozhiNode]:
        return self._nodes.get(device_id)

    def get_all_nodes(self) -> Dict[str, XiaozhiNode]:
        return dict(self._nodes)

    def get_connected_count(self) -> int:
        return len(self._nodes)

    def get_nodes_telemetry(self) -> List[Dict[str, Any]]:
        return [node.to_dict() for node in self._nodes.values()]

    # -----------------------------------------------------------------------
    # STEP 1: LCD / OLED Display & Emotion Control
    # -----------------------------------------------------------------------

    async def send_ui_payload(
        self,
        device_id: str,
        state: str,
        emotion: Optional[str] = None,
        text: Optional[str] = None,
        **extra: Any,
    ) -> bool:
        """
        Send formatted UI frame to Xiaozhi LCD / OLED display.
        Payload spec:
          - listening  : {"type": "ui", "state": "listening", "emotion": "focused"}
          - processing : {"type": "ui", "state": "processing", "emotion": "thinking"}
          - alert      : {"type": "ui", "state": "alert", "text": "..."}
          - idle       : {"type": "ui", "state": "idle", "emotion": "sleeping"}
          - speaking   : {"type": "ui", "state": "speaking", "emotion": "happy"}
        """
        node = self._nodes.get(device_id)
        if not node or not node.websocket:
            return False

        # Set sensible defaults based on state
        if not emotion:
            if state == "listening":
                emotion = "focused"
            elif state == "processing" or state == "thinking":
                emotion = "thinking"
            elif state == "alert":
                emotion = "alert"
            elif state == "idle":
                emotion = "sleeping"
            elif state == "speaking":
                emotion = "happy"
            else:
                emotion = "normal"

        payload: Dict[str, Any] = {
            "type": "ui",
            "state": state,
            "emotion": emotion,
        }
        if text:
            payload["text"] = text
        payload.update(extra)

        node.state = state
        node.emotion = emotion
        node.screen_text = text
        node.last_active = datetime.utcnow().isoformat()

        try:
            await node.websocket.send_text(json.dumps(payload, ensure_ascii=False))
            logger.debug("[Xiaozhi UI] Sent frame to [%s]: %s", device_id, payload)
            return True
        except Exception as exc:
            logger.warning("[Xiaozhi UI] Failed to send UI frame to [%s]: %s", device_id, exc)
            return False

    async def broadcast_ui_payload(
        self,
        state: str,
        emotion: Optional[str] = None,
        text: Optional[str] = None,
    ) -> int:
        """Send UI frame to all online Xiaozhi companion nodes."""
        success_count = 0
        for dev_id in list(self._nodes.keys()):
            if await self.send_ui_payload(dev_id, state=state, emotion=emotion, text=text):
                success_count += 1
        return success_count

    # -----------------------------------------------------------------------
    # STEP 3: High-Fidelity Audio Streaming for I2S
    # -----------------------------------------------------------------------

    async def _stream_audio_smooth(
        self,
        node: XiaozhiNode,
        audio_stream: AsyncGenerator[bytes, None],
        text_summary: str = "",
        sample_rate: int = 16000,
        bitrate: int = 32000,
        chunk_size: int = 2048,
    ) -> int:
        """
        Stream PCM audio to ESP32 I2S MAX98357A speaker:
          1. Collects TTS MP3 stream and converts to raw 16-bit 16000Hz PCM.
          2. Streams in 2048-byte PCM chunks with natural DMA backpressure.
          3. Listens to node.cancel_event for instant barge-in cut-off.
        """
        ws = node.websocket
        cancel_ev = node.cancel_event
        cancel_ev.clear()

        # Send TTS start header to notify robot OLED to show speaking animation
        await ws.send_text(json.dumps({
            "type": "tts_start",
            "format": "audio/pcm",
            "sample_rate": 16000,
            "channels": 1,
            "text": text_summary,
        }, ensure_ascii=False))

        # Collect full audio stream bytes for conversion
        mp3_buffer = bytearray()
        try:
            async for chunk in audio_stream:
                if cancel_ev.is_set():
                    break
                if chunk:
                    mp3_buffer.extend(chunk)
        except Exception as read_err:
            logger.error("[Xiaozhi I2S] Error collecting TTS stream: %s", read_err)

        if cancel_ev.is_set() or not mp3_buffer:
            return 0

        # Convert to 16-bit 16kHz mono raw PCM for MAX98357A
        pcm_bytes = convert_to_pcm16_16k(bytes(mp3_buffer))
        chunk_count = 0

        try:
            for offset in range(0, len(pcm_bytes), chunk_size):
                if cancel_ev.is_set():
                    break
                chunk = pcm_bytes[offset : offset + chunk_size]
                await ws.send_bytes(chunk)
                chunk_count += 1
                # 2048 bytes = ~64ms of audio at 16kHz mono (32KB/sec). Pace at 45ms to keep DMA ring buffer primed
                await asyncio.sleep(0.045)
        except (WebSocketDisconnect, ConnectionResetError):
            logger.info("[Xiaozhi I2S] WebSocket disconnected during playback on [%s]", node.device_id)
            return chunk_count
        except Exception as exc:
            logger.error("[Xiaozhi I2S] Error streaming PCM to [%s]: %s", node.device_id, exc)

        if not cancel_ev.is_set():
            try:
                await ws.send_text(json.dumps({
                    "type": "tts_end",
                    "chunk_count": chunk_count,
                }))
            except Exception:
                pass

        return chunk_count

    async def _stream_audio_chunks(self, node: XiaozhiNode, audio_bytes: bytes) -> None:
        """Stream cached audio bytes to ESP32 converted to PCM 16kHz."""
        try:
            pcm_bytes = convert_to_pcm16_16k(audio_bytes)
            await node.websocket.send_text(json.dumps({
                "type": "tts_start",
                "format": "audio/pcm",
                "sample_rate": 16000,
            }))

            for offset in range(0, len(pcm_bytes), 2048):
                if node.cancel_event.is_set():
                    break
                await node.websocket.send_bytes(pcm_bytes[offset : offset + 2048])
                await asyncio.sleep(0.045)

            await node.websocket.send_text(json.dumps({
                "type": "tts_end",
            }))
        except Exception as exc:
            logger.error("[Xiaozhi] Error streaming audio chunks: %s", exc)

    # -----------------------------------------------------------------------
    # STEP 2: Barge-in / Interruption Engine
    # -----------------------------------------------------------------------

    async def handle_barge_in(self, device_id: str) -> None:
        """
        Handle Wake Word or Barge-in Interruption during active generation or playback:
          1. Cancel active 9router/Claude generation task.
          2. Clear ESP32 hardware audio buffer (send tts_stop).
          3. Play 0ms reflex from Cache: "Dạ, anh nói đi em nghe đây."
          4. Set UI to listening / focused.
        """
        node = self._nodes.get(device_id)
        if not node:
            return

        logger.info("[Xiaozhi Barge-in] Kích hoạt ngắt lời ngay lập tức trên mạch [%s]", device_id)

        # 1. Trigger cancel event to stop audio streamer loop
        node.cancel_event.set()

        # 2. Cancel active background LLM task if running
        if node.active_task and not node.active_task.done():
            logger.info("[Xiaozhi Barge-in] Huỷ bỏ tác vụ LLM/Claude đang chạy trên [%s]", device_id)
            node.active_task.cancel()
            try:
                await asyncio.wait_for(asyncio.shield(node.active_task), timeout=0.1)
            except (asyncio.CancelledError, asyncio.TimeoutError, Exception):
                pass
            node.active_task = None

        # 3. Instruct ESP32 to drop playback buffer immediately
        try:
            await node.websocket.send_text(json.dumps({"type": "tts_stop"}))
            await node.websocket.send_text(json.dumps({"type": "clear_buffer"}))
        except Exception as e:
            logger.debug("[Xiaozhi Barge-in] Error sending tts_stop: %s", e)

        # 4. Update UI: listening / focused
        await self.send_ui_payload(device_id, state="listening", emotion="focused")

        # 5. Play 0ms Reflex confirmation from Cache
        reflex_bytes = get_cached_audio_bytes(REFLEX_BARGE_IN_TEXT)
        if reflex_bytes:
            logger.info("[Xiaozhi Barge-in] Phát câu đệm 0ms từ Cache cho [%s]: '%s'", device_id, REFLEX_BARGE_IN_TEXT)
            async def _cached_gen():
                yield reflex_bytes

            # Reset cancel event for the quick reflex speech
            node.cancel_event.clear()
            await self._stream_audio_smooth(node, _cached_gen(), text_summary=REFLEX_BARGE_IN_TEXT)

        # Reset audio input buffer to record user's new utterance
        node.audio_buffer = io.BytesIO()
        logger.info("[Xiaozhi Barge-in] Mạch [%s] đã sẵn sàng nhận lệnh mới.", device_id)

    # -----------------------------------------------------------------------
    # STEP 4: Push Notification / Autonomous Sentinel Wake Integration
    # -----------------------------------------------------------------------

    async def wake_and_alert(
        self,
        error_title: str,
        detail_message: str,
        device_id: Optional[str] = None,
    ) -> bool:
        """
        Autonomously wake up the Desktop Robot and voice an alert:
          1. Wakes up LCD with blinking red eyes / alert state:
             {"type": "ui", "state": "alert", "text": error_title}
          2. Voices prompt: "Báo cáo anh, hệ thống máy chủ vừa ghi nhận cảnh báo..."
             followed by the concise error detail.
        """
        targets = [self._nodes[device_id]] if device_id and device_id in self._nodes else list(self._nodes.values())
        if not targets:
            logger.warning("[Xiaozhi Push] Không có mạch Xiaozhi nào trực tuyến để phát cảnh báo: '%s'", error_title)
            return False

        logger.info("[Xiaozhi Push] Đang phát cảnh báo tới %d mạch Xiaozhi: '%s'", len(targets), error_title)

        # Build clean speech text
        spoken_text = f"{REFLEX_SENTINEL_PREFIX} {detail_message}"
        clean_spoken = shorten_for_speech(sanitise_for_tts(spoken_text))

        for node in targets:
            # 1. Update LCD Screen immediately
            await self.send_ui_payload(node.device_id, state="alert", emotion="alert", text=error_title)

            # 2. Stream voice alert
            try:
                async with node.stream_lock:
                    await self._stream_audio_smooth(
                        node,
                        get_tts_engine().stream(clean_spoken),
                        text_summary=clean_spoken,
                        sample_rate=24000,
                    )
            except Exception as exc:
                logger.error("[Xiaozhi Push] Lỗi phát thanh cảnh báo tới [%s]: %s", node.device_id, exc)

        return True

    # -----------------------------------------------------------------------
    # Core Pipeline: Speech / Query Execution
    # -----------------------------------------------------------------------

    async def _pace_playback(self, node: XiaozhiNode, n_bytes: int) -> None:
        """Gửi theo nhịp phát của robot: chờ tới khi phần đã gửi chỉ còn đi trước PLAYBACK_LEAD_S."""
        now = time.perf_counter()
        ahead = node.play_end - now
        if ahead > PLAYBACK_LEAD_S:
            await asyncio.sleep(ahead - PLAYBACK_LEAD_S)
            now = time.perf_counter()
        node.play_end = max(node.play_end, now) + n_bytes / PCM_BYTES_PER_S

    async def _wait_playback_done(self, node: XiaozhiNode) -> None:
        """Chờ robot phát hết phần âm thanh đã gửi (thoát sớm nếu bị ngắt lời)."""
        while not node.cancel_event.is_set():
            left = node.play_end - time.perf_counter()
            if left <= 0:
                break
            await asyncio.sleep(min(left, 0.1))
        await asyncio.sleep(0.2)   # loa tắt hẳn trước khi mở mic — không thu tiếng vọng

    async def _start_follow_up(self, node: XiaozhiNode, stage: int = 1) -> None:
        node.follow_up = stage
        _topo("robot", stage="listen", node=f"robot:{node.device_id}", status="waiting",
              detail="nghe tiếp chỉ lệnh (10 s)" if stage == 1 else "đã hỏi lại, nghe thêm 20 s")
        node.audio_buffer = io.BytesIO()
        node.vad_detector.reset()
        timeout = ROBOT_FOLLOW_UP_LISTEN_MS if stage == 1 else ROBOT_FOLLOW_UP_REASK_LISTEN_MS
        await self.send_ui_payload(node.device_id, state="listening", emotion="focused",
                                   listen_timeout_ms=timeout)

    async def _say(self, node: XiaozhiNode, text: str) -> None:
        """Đọc một câu cố định ra loa robot (theo nhịp phát) và chờ đọc xong."""
        try:
            audio = await get_tts_engine().synthesise(text)
            sink = _XiaozhiSink(self, node, "")
            await sink.on_audio(1, audio or b"", text, "speech")
            await self._wait_playback_done(node)
            await node.websocket.send_text(json.dumps({"type": "tts_end", "text": text}))
        except Exception as exc:  # noqa: BLE001
            logger.warning("[Xiaozhi] Không phát được câu '%s' trên [%s]: %s", text, node.device_id, exc)

    async def _on_follow_up_silence(self, node: XiaozhiNode) -> None:
        """Robot nghe hết thời gian mà không ai nói: lần đầu hỏi lại, lần sau tạm biệt."""
        from mateai.application.voice.voice_session import FAREWELL_PHRASE, FOLLOW_UP_PHRASE
        if node.follow_up == 1:
            await self._say(node, FOLLOW_UP_PHRASE)
            await self._start_follow_up(node, stage=2)
            return
        node.follow_up = 0
        _topo("robot", stage="farewell", node=f"robot:{node.device_id}", detail="im lặng 30 s — tạm biệt")
        await self._say(node, FAREWELL_PHRASE)
        await self.send_ui_payload(node.device_id, state="idle", emotion="sleeping")

    async def _finish_utterance(
        self,
        node: XiaozhiNode,
        audio_data: bytes,
        *,
        pcm16_wav: bool = False,
        xiaozhi_stt: bool = False,
    ) -> None:
        """Hết một câu nói: nhận dạng -> asr_result -> chạy lượt. MỘT nơi cho cả 3
        cách robot báo hết câu (VAD máy chủ, "listen stop", "end_of_speech") —
        trước đây chép 3 lần (realtime L5).

        pcm16_wav   — PCM 16 kHz thô (VAD máy chủ): đóng gói WAV trước khi nhận dạng.
        xiaozhi_stt — firmware XiaoZhi gốc ("listen stop"): asr_start / stt kèm
                      session_id, không đổi màn hình sang "đang suy nghĩ".
        """
        ws = node.websocket
        device_id = node.device_id
        if xiaozhi_stt:
            await ws.send_text(json.dumps({"session_id": device_id, "type": "asr_start"}))
        else:
            await self.send_ui_payload(device_id, state="processing", emotion="thinking", text="Đang suy nghĩ...")
            await ws.send_text(json.dumps({"type": "asr_start"}))

        audio = _pcm16_to_wav(audio_data) if pcm16_wav else audio_data
        if node.audio_check and node.audio_check.get("status") == "listening":
            await self._finish_audio_check(node, audio_data if pcm16_wav else b"", audio)
            return
        transcribed = await self._transcribe(node, audio)

        if transcribed and node.follow_up and _is_stop_reply(transcribed):
            # "Không / không cần, cảm ơn…" khi robot đang chờ chỉ lệnh tiếp: chào tạm biệt rồi về chờ (như HUD).
            from mateai.application.voice.voice_session import GOODBYE_PHRASE
            node.follow_up = 0
            await ws.send_text(json.dumps({"type": "asr_result", "text": transcribed}))
            _topo("robot", stage="farewell", node=f"robot:{node.device_id}", detail="admin không còn yêu cầu — chào")
            await self._say(node, GOODBYE_PHRASE)
            await self.send_ui_payload(device_id, state="idle", emotion="sleeping")
            return

        if transcribed:
            if xiaozhi_stt:
                await ws.send_text(json.dumps({"session_id": device_id, "type": "stt", "text": transcribed}))
            await ws.send_text(json.dumps({"type": "asr_result", "text": transcribed}))
            node.active_task = asyncio.create_task(self._execute_pipeline(node, transcribed))
            return

        if xiaozhi_stt:
            await ws.send_text(json.dumps({"session_id": device_id, "type": "stt", "text": "",
                                           "error": "Không nhận diện được giọng nói."}))
        else:
            await ws.send_text(json.dumps({"type": "asr_result", "text": "",
                                           "error": "ASR không nhận diện được giọng nói."}))
        await self.send_ui_payload(device_id, state="idle", emotion="sleeping")

    # ── Điều khiển từ Portal (routers/robots.py) ───────────────────────────────

    async def speak(self, device_id: str, text: str) -> bool:
        node = self._nodes.get(device_id)
        if not node:
            return False
        await self._say(node, text)
        await self.send_ui_payload(device_id, state="idle", emotion="happy")
        return True

    async def send_command(self, device_id: str, payload: Dict[str, Any]) -> bool:
        node = self._nodes.get(device_id)
        if not node:
            return False
        await node.websocket.send_text(json.dumps(payload, ensure_ascii=False))
        return True

    async def start_audio_check(self, device_id: str, listen_s: int = 8) -> bool:
        """Robot mời người dùng gọi tên + một câu từ vị trí thường ngồi, đo mức tiếng
        nói so với ồn nền, nhận dạng và kiểm tra có nghe ra tên gọi không."""
        from mateai.infrastructure.audio.wake_word_engine import wake_names
        node = self._nodes.get(device_id)
        if not node:
            return False
        name = (wake_names() or ["Ly Ly"])[0]
        node.audio_check = {"status": "prompting", "started": time.time(), "prompt": f"{name} ơi, mấy giờ rồi"}
        node.follow_up = 0
        await self._say(node, f"Kiểm tra micro. Mời anh ngồi ở chỗ quen thuộc và nói: {name} ơi, mấy giờ rồi.")
        node.audio_buffer = io.BytesIO()
        node.vad_detector.reset()
        node.audio_check["status"] = "listening"
        await self.send_ui_payload(device_id, state="listening", emotion="focused",
                                   listen_timeout_ms=listen_s * 1000)

        async def _timeout() -> None:
            await asyncio.sleep(listen_s + 4)
            if node.audio_check and node.audio_check.get("status") == "listening":
                node.audio_check.update(status="done", finished=time.time(), heard="", wake_ok=False,
                                        levels=None, **audio_check_verdict("", False, None, None))
                node.audio_check["advice"] = "Không nghe thấy tiếng nói nào trong lúc kiểm tra. " + node.audio_check["advice"]
                await self.send_ui_payload(device_id, state="idle", emotion="sad")
        asyncio.create_task(_timeout())
        return True

    async def _finish_audio_check(self, node: XiaozhiNode, pcm: bytes, audio: bytes) -> None:
        from mateai.infrastructure.audio.wake_word_engine import find_wake_command
        node.audio_check["status"] = "analysing"
        levels = pcm_levels(pcm) if pcm else None
        text = await self._transcribe(node, audio)
        wake_ok = find_wake_command(text or "") is not None
        noise = (node.audio_stats or {}).get("noise")
        verdict = audio_check_verdict(text, wake_ok, (levels or {}).get("rms"), float(noise) if noise else None)
        node.audio_check.update(status="done", finished=time.time(), heard=text or "", wake_ok=wake_ok,
                                levels=levels, noise_rms=noise, **verdict)
        _topo("robot", stage="audio_check", node=f"robot:{node.device_id}",
              status="ok" if verdict["grade"] in ("tốt", "trung bình") else "error",
              detail=f"kiểm tra âm thanh: {verdict['grade']}")
        await self._say(node, f"Kết quả: {verdict['grade']}. {verdict['advice']}")
        await self.send_ui_payload(node.device_id, state="idle",
                                   emotion="happy" if verdict["grade"] == "tốt" else "thinking")

    async def _handle_wake_clip(self, node: XiaozhiNode, clip: bytes) -> None:
        """Đoạn robot gửi lúc nghỉ: có gọi tên trợ lý không -> lắng nghe / chạy lệnh.

        Nhận dạng OFFLINE (audio_engine.transcribe_wake_clip). Câu không gọi tên
        thì bỏ — không ghi log nội dung (âm thanh trong phòng lúc chưa gọi trợ lý).
        Gọi tên kèm lệnh ("Ly Ly ơi, mấy giờ rồi") -> nhận dạng lại bằng bộ nhận
        dạng chính (chính xác hơn model nhỏ) rồi chạy lệnh luôn.
        """
        from mateai.infrastructure.audio.wake_word_engine import detect_wake, find_wake_command
        try:
            t0 = time.perf_counter()
            # Hai bước (tiny → small khi nghe gần giống tên) — chung với HUD. Đo 2026-10-12: 24 → 29/32 câu gọi.
            command, heard, _stage = await detect_wake(clip)
            elapsed_ms = round((time.perf_counter() - t0) * 1000)
            level = _clip_level(clip)
            if command is None:
                logger.info("[Wake] [%s] đoạn %.1f s không gọi tên (%d ms, %s)",
                            node.device_id, len(clip) / 32000, elapsed_ms, level)
                return
            logger.info("[Wake] [%s] gọi tên sau %d ms (%s): '%s'", node.device_id, elapsed_ms, level, heard)
            _topo("robot", stage="wake", node=f"robot:{node.device_id}", ms=elapsed_ms,
                  detail="được gọi tên" + (" kèm lệnh" if command else ""))
            if command:
                full = await self._transcribe(node, clip)
                again = find_wake_command(full)
                command = again if again is not None else command
            if node.active_task is not None and not node.active_task.done():
                return
            node.audio_buffer = io.BytesIO()
            node.vad_detector.reset()
            if command:
                await node.websocket.send_text(json.dumps({"type": "asr_result", "text": command}))
                node.active_task = asyncio.create_task(self._execute_pipeline(node, command))
            else:
                await self.send_ui_payload(node.device_id, state="listening", emotion="focused")
        except Exception as exc:  # noqa: BLE001
            logger.warning("[Wake] [%s] lỗi xử lý câu gọi: %s", node.device_id, exc)
        finally:
            node.wake_busy = False

    async def _transcribe(self, node: XiaozhiNode, audio: bytes) -> str:
        """STT câu vừa nói (một chỗ cho cả 3 định dạng gói) + đo thời gian cho trace."""
        t0 = time.perf_counter()
        text = ""
        try:
            text = await audio_engine.transcribe_audio(audio)
            return text
        finally:
            node.last_stt_ms = (time.perf_counter() - t0) * 1000
            _topo("robot", stage="stt", source=f"robot:{node.device_id}", target="stt",
                  status="ok" if text else "error", ms=node.last_stt_ms,
                  detail="đã nhận dạng câu nói" if text else "không nhận dạng được")

    async def _execute_pipeline(self, node: XiaozhiNode, text_query: str) -> None:
        """
        Một lượt nói của robot ESP32. Nghiệp vụ ở mateai.application.voice.voice_turn.process_voice_turn
        (dùng chung mọi kênh); ở đây chỉ còn phần thiết bị: biểu cảm LCD, gói tts
        của firmware xiaozhi-esp32, PCM 16 kHz cho loa MAX98357A, phản chiếu chữ
        lên HUD/portal (không phát tiếng ở đó), giữ mic mở khi robot vừa hỏi.
        """
        from mateai.application.voice.voice_turn import process_voice_turn

        device_id = node.device_id
        node.last_active = datetime.utcnow().isoformat()
        node.follow_up = 0   # đang xử lý chỉ lệnh — hết lượt mới nghe tiếp

        await self.send_ui_payload(device_id, state="processing", emotion="thinking")
        await node.websocket.send_text(json.dumps({"type": "llm_start"}))

        # Phase 71: Đồng bộ thị giác sang HUD & Web Portal (âm thanh chỉ phát ở loa robot)
        try:
            from mateai.interfaces.websocket.realtime_hub import broadcast_hud, broadcast_portal_ui
            await broadcast_hud({
                "type": "voice_active",
                "status": "listening",
                "text": text_query,
                "source_device": device_id,
                "timestamp": datetime.utcnow().isoformat(),
            })
            await broadcast_portal_ui("voice_response", {
                "query": text_query,
                "reply": "Đang xử lý...",
                "source_device": device_id,
                "timestamp": datetime.utcnow().isoformat(),
            })
        except Exception:
            pass

        sink = _XiaozhiSink(self, node, text_query)
        try:
            async with node.stream_lock:
                stt_ms, node.last_stt_ms = node.last_stt_ms, None
                result = await process_voice_turn(
                    text_query,
                    sink=sink,
                    session_id=device_id,
                    source_device=device_id,
                    caller=node.caller,
                    stt_ms=stt_ms,
                )

            if not node.cancel_event.is_set():
                await self._wait_playback_done(node)
            if not node.cancel_event.is_set():
                try:
                    await node.websocket.send_text(json.dumps({
                        "session_id": device_id, "type": "tts", "state": "stop",
                    }))
                except Exception:
                    pass
                await node.websocket.send_text(json.dumps({
                    "type": "tts_end",
                    "text": result.reply_text,
                }))
                try:
                    from mateai.interfaces.websocket.realtime_hub import broadcast_hud
                    await broadcast_hud({
                        "type": "voice_active",
                        "status": "idle",
                        "text": "",
                        "source_device": device_id,
                        "timestamp": datetime.utcnow().isoformat(),
                    })
                except Exception:
                    pass

        except asyncio.CancelledError:
            logger.info("[Xiaozhi] Pipeline bị huỷ bởi ngắt lời trên [%s]", device_id)
            return
        except Exception as exc:
            logger.error("[Xiaozhi] Lỗi LLM trên [%s]: %s", device_id, exc)
            await self.send_ui_payload(device_id, state="alert", text=f"Lỗi: {str(exc)[:40]}")
            await node.websocket.send_text(json.dumps({
                "type": "error",
                "message": f"Lỗi LLM: {exc}",
            }))
            return

        # Robot vừa đặt câu hỏi -> giữ mic mở 8s chờ trả lời; không thì về idle sau 1s.
        # Hội thoại liên tục (sau MỌI câu trả lời, không chỉ khi robot hỏi): nghe 10 s;
        # firmware im lặng hết hạn thì báo "abort" -> hỏi lại, nghe 20 s, rồi tạm biệt. Trước đây chỉ mở mic 8 s khi câu trả lời là câu hỏi, còn lại
        # về nghỉ ngay — muốn hỏi tiếp phải gọi tên lại.
        if not node.cancel_event.is_set():
            await self._start_follow_up(node)

    async def handle_client(self, websocket: WebSocket, device_id: str, auth_method: Optional[str] = None) -> None:
        """
        Handle WebSocket connection lifecycle for a Xiaozhi ESP32 device:
          - Manages binary audio stream from mic.
          - Processes control frames (barge-in, query_text, end_of_speech).
          - Handles state transitions and LCD sync.
        """
        await websocket.accept()
        client_host = websocket.client.host if websocket.client else "unknown"
        logger.info("[Xiaozhi] Thiết bị [%s] đã kết nối từ IP %s.", device_id, client_host)

        node = XiaozhiNode(device_id=device_id, websocket=websocket, client_host=client_host)
        from mateai.interfaces.http.ws_auth import DEVICE_TOKEN
        from mateai.application.security.security_guard import DEVICE_PRINCIPAL_PREFIX
        if auth_method == DEVICE_TOKEN:
            node.caller = f"{DEVICE_PRINCIPAL_PREFIX}{device_id}"
        async with self._lock:
            self._nodes[device_id] = node

        # Sync with global active_audio_nodes for server compatibility
        from mateai.interfaces.websocket.realtime_hub import active_audio_nodes
        active_audio_nodes[device_id] = {
            "websocket": websocket,
            "client_host": client_host,
            "connected_at": node.connected_at,
            "last_active": node.last_active,
            "state": node.state,
            "emotion": node.emotion,
        }

        # Send initial idle UI frame to LCD screen
        await self.send_ui_payload(device_id, state="idle", emotion="sleeping")
        # Nạp sẵn model nhận dạng offline cho câu gọi tên (không chặn kết nối).
        from mateai.infrastructure.audio.audio_processor import warm_local_whisper
        asyncio.create_task(asyncio.to_thread(warm_local_whisper))

        try:
            while True:
                message = await websocket.receive()
                node.last_active = datetime.utcnow().isoformat()
                active_audio_nodes[device_id]["last_active"] = node.last_active

                # Frame nhị phân: Micro chunks từ ESP32
                if message.get("bytes") is not None:
                    raw_chunk: bytes = message["bytes"]
                    if node.wake_buf is not None:
                        # Thuộc đoạn câu gọi — không vào luồng nhận lệnh.
                        if len(node.wake_buf) < WAKE_CLIP_MAX_BYTES:
                            node.wake_buf.extend(raw_chunk)
                        continue
                    node.audio_buffer.write(raw_chunk)

                    # Phase 50 Step 1.2: Silero VAD real-time streaming analysis at 16kHz
                    vad_res = node.vad_detector.process_pcm16(raw_chunk, incoming_rate=16000)

                    if vad_res.get("speech_started") and node.state != "listening":
                        await self.send_ui_payload(device_id, state="listening", emotion="focused")

                    # Phase 50 Step 1.3: Dứt lời 500ms -> lập tức cắt luồng bytes và chuyển sang ASR In-Memory
                    if vad_res.get("speech_ended"):
                        audio_data = node.audio_buffer.getvalue()
                        node.audio_buffer = io.BytesIO()
                        node.vad_detector.reset()

                        if audio_data and len(audio_data) >= 1600:
                            logger.info(
                                "[Phase50 Silero VAD] Dứt lời sau 500ms im lặng trên [%s] (%d bytes) -> Chạy ASR tức thì!",
                                device_id, len(audio_data),
                            )
                            await self._finish_utterance(node, audio_data, pcm16_wav=True)

                # Frame văn bản: JSON điều khiển
                elif message.get("text") is not None:
                    raw_text: str = message["text"]
                    try:
                        ctrl: Dict[str, Any] = json.loads(raw_text)
                    except json.JSONDecodeError:
                        await websocket.send_text(json.dumps({
                            "type": "error",
                            "message": "Invalid JSON control frame.",
                        }))
                        continue

                    msg_type: str = str(ctrl.get("type", "")).lower()
                    action: str = str(ctrl.get("action", "")).lower()
                    is_wakeup: bool = bool(ctrl.get("is_wakeup", False))

                    # Gọi tên trợ lý lúc robot nghỉ (máy chủ nhận dạng offline).
                    if msg_type == "wake_check":
                        node.wake_buf = bytearray()
                        continue
                    if msg_type == "wake_check_end":
                        clip = bytes(node.wake_buf or b"")
                        node.wake_buf = None
                        busy = node.wake_busy or (node.active_task is not None and not node.active_task.done())
                        if not busy and len(clip) >= WAKE_CLIP_MIN_BYTES:
                            node.wake_busy = True
                            asyncio.create_task(self._handle_wake_clip(node, clip))
                        continue
                    if msg_type == "wake_stats":
                        logger.info("[Wake] [%s] ồn nền RMS %s, đỉnh %s", device_id,
                                    ctrl.get("noise"), ctrl.get("peak"))
                        node.audio_stats = {"noise": ctrl.get("noise"), "peak": ctrl.get("peak"), "at": time.time()}
                        continue
                    if msg_type == "status_report":
                        node.status_report = {k: ctrl.get(k) for k in (
                            "reason", "version", "volume", "rssi", "ip", "heap", "psram", "uptime_s", "noise", "peak")}
                        node.status_report["at"] = time.time()
                        if ctrl.get("noise") is not None:
                            node.audio_stats = {"noise": ctrl.get("noise"), "peak": ctrl.get("peak"), "at": time.time()}
                        continue

                    # -------------------------------------------------------
                    # Phase 72: Official XiaoZhi ESP32 Protocol Handshake ("hello")
                    # -------------------------------------------------------
                    if msg_type == "hello":
                        logger.info(
                            "[Xiaozhi] Nhận frame 'hello' từ [%s] (version=%s, transport=%s)",
                            device_id, ctrl.get("version", 1), ctrl.get("transport", "websocket")
                        )
                        node.firmware_version = str(ctrl.get("version", 1))
                        node.capabilities = json.dumps(ctrl.get("features", {}))
                        feats = ctrl.get("features", "")
                        node.features = ([f.strip() for f in feats.split(",") if f.strip()] if isinstance(feats, str)
                                         else [str(f) for f in feats] if isinstance(feats, list) else [])

                        req_params = ctrl.get("audio_params", {})
                        fmt = req_params.get("format", "opus")
                        rate = req_params.get("sample_rate", 16000)
                        channels = req_params.get("channels", 1)
                        frame_dur = req_params.get("frame_duration", 60)
                        node.audio_format = fmt

                        # ── Pairing Code Registration ──────────────────────
                        # Robot gửi pairing_code trong frame hello.
                        # Nếu robot không có code, server tự sinh và trả về.
                        robot_code = str(ctrl.get("pairing_code", "")).strip()
                        if not robot_code or len(robot_code) != 6 or not robot_code.isdigit():
                            robot_code = PairingCodeRegistry.generate_code()
                            logger.info(
                                "[PairingCode] Robot [%s] không gửi mã hợp lệ → server sinh mã mới: %s",
                                device_id, robot_code,
                            )
                        await pairing_registry.register(robot_code, device_id)

                        hello_ack = {
                            "type": "hello",
                            "transport": "websocket",
                            "session_id": device_id,
                            "pairing_code": robot_code,  # Trả về để robot hiển thị trên OLED
                            "audio_params": {
                                "format": fmt,
                                "sample_rate": rate,
                                "channels": channels,
                                "frame_duration": frame_dur,
                            },
                        }
                        await websocket.send_text(json.dumps(hello_ack))
                        logger.info(
                            "[Xiaozhi] Đã phản hồi 'hello' ACK tới [%s] (format=%s, rate=%d, code=%s)",
                            device_id, fmt, rate, robot_code,
                        )
                        # Cử động tự nhiên: cấu hình hành vi ngay sau bắt tay (firmware cũ bỏ qua khung lạ).
                        await self.push_behavior(device_id, force=True)
                        self._ensure_behavior_loop()
                        continue

                    # -------------------------------------------------------
                    # Phase 72: Official XiaoZhi Listen Event (start / stop / detect)
                    # -------------------------------------------------------
                    elif msg_type == "listen":
                        listen_state = str(ctrl.get("state", "")).lower()
                        if listen_state in ("start", "detect"):
                            logger.info("[Xiaozhi] Bắt đầu thu âm (listen %s) trên [%s]", listen_state, device_id)
                            node.audio_buffer = io.BytesIO()
                            node.vad_detector.reset()
                            await self.send_ui_payload(device_id, state="listening", emotion="focused")
                            continue
                        elif listen_state == "abort":
                            # Robot nghe mãi không thấy ai nói -> tự về nghỉ.
                            node.audio_buffer = io.BytesIO()
                            node.vad_detector.reset()
                            node.state = "idle"
                            if node.follow_up:
                                asyncio.create_task(self._on_follow_up_silence(node))
                            continue
                        elif listen_state == "stop":
                            logger.info("[Xiaozhi] Kết thúc thu âm (listen stop) trên [%s]", device_id)
                            audio_data = node.audio_buffer.getvalue()
                            node.audio_buffer = io.BytesIO()
                            if audio_data:
                                await self._finish_utterance(node, audio_data, xiaozhi_stt=True)
                            continue

                    # -------------------------------------------------------
                    # Phase 52 Step 3: ToF Edge / Cliff Detection Interlock Alert
                    # -------------------------------------------------------
                    if msg_type == "alert" or ctrl.get("msg") == "edge_detected":
                        logger.warning("[Robotics Safety] CẢNH BÁO TOF: Phát hiện mép bàn/vực trên robot [%s]! Motor đã tự động ngắt điện.", device_id)
                        # Set alert face and text on OLED
                        await self.send_ui_payload(device_id, state="alert", emotion="shocked", text="Mép bàn! Đã phanh khẩn cấp.")
                        
                        # Broadcast alert to Web Portal & Standby HUD
                        try:
                            from mateai.interfaces.websocket.realtime_hub import broadcast_portal_ui, broadcast_hud
                            await broadcast_portal_ui("system_alert", {
                                "level": "WARNING",
                                "title": "ToF Safety Alert",
                                "message": f"Robot [{device_id}] phát hiện mép bàn! Motor đã dừng khẩn cấp.",
                                "timestamp": datetime.utcnow().isoformat(),
                            })
                            await broadcast_hud({
                                "type": "system_log",
                                "level": "WARNING",
                                "message": f"[ROBOT SAFETY] ⚠️ Robot [{device_id}] gặp mép bàn! Đã ngắt điện motor.",
                                "timestamp": datetime.utcnow().isoformat(),
                            })
                        except Exception as b_err:
                            logger.debug("Broadcast error: %s", b_err)

                        # Synthesize voice warning to node
                        edge_text = "Dạ, phía trước là mép bàn, em không đi được nữa đâu ạ."
                        try:
                            edge_audio = await get_tts_engine().synthesise(shorten_for_speech(sanitise_for_tts(edge_text)))
                            if edge_audio:
                                asyncio.create_task(self._stream_audio_chunks(node, edge_audio))
                        except Exception as spk_err:
                            logger.debug("Edge alert voice error: %s", spk_err)
                        continue

                    # -------------------------------------------------------
                    # Phase 52 Step 4: Touch Sensor Wake (GPIO 17)
                    # -------------------------------------------------------
                    if msg_type == "touch" or action == "wake" or ctrl.get("event") == "touch_wake":
                        logger.info("[Robotics Touch] Cảm biến chạm GPIO 17 kích hoạt trên robot [%s]. Đánh thức robot!", device_id)
                        await self.send_ui_payload(device_id, state="listening", emotion="focused", text="Dạ, em nghe đây!")
                        node.audio_buffer = io.BytesIO()
                        node.vad_detector.reset()

                        # Fast reflex wake chime or voice filler
                        from mateai.infrastructure.tts.audio_cache import get_cached_audio_bytes
                        wake_filler = "Dạ, em nghe đây ạ."
                        cached_bytes = get_cached_audio_bytes(wake_filler)
                        if cached_bytes:
                            asyncio.create_task(self._stream_audio_chunks(node, cached_bytes))
                        continue

                    # -------------------------------------------------------
                    # Step 2: Barge-in / Interruption Signal Check
                    # -------------------------------------------------------
                    if msg_type in ("interrupt", "abort") or action == "interrupt" or is_wakeup:
                        logger.info("[Xiaozhi] Tín hiệu ngắt lời (Barge-in) từ [%s]", device_id)
                        await self.handle_barge_in(device_id)
                        continue

                    # -------------------------------------------------------
                    # Micro speech completed -> Transcribe & Run Pipeline
                    # -------------------------------------------------------
                    if msg_type == "end_of_speech":
                        audio_data = node.audio_buffer.getvalue()
                        node.audio_buffer = io.BytesIO()

                        if not audio_data:
                            await websocket.send_text(json.dumps({
                                "type": "error",
                                "message": "Không có dữ liệu âm thanh trong buffer.",
                            }))
                            continue

                        logger.info("[Xiaozhi] end_of_speech từ [%s] (%d bytes)", device_id, len(audio_data))
                        await self._finish_utterance(node, audio_data)

                    # -------------------------------------------------------
                    # Direct text query
                    # -------------------------------------------------------
                    elif msg_type == "query_text":
                        query_str = ctrl.get("text", "").strip()
                        if not query_str:
                            await websocket.send_text(json.dumps({
                                "type": "error",
                                "message": "'text' field is empty.",
                            }))
                            continue

                        node.active_task = asyncio.create_task(
                            self._execute_pipeline(node, query_str)
                        )

                    # -------------------------------------------------------
                    # Ping / Pong & Diagnostics
                    # -------------------------------------------------------
                    elif msg_type == "ping":
                        await websocket.send_text(json.dumps({
                            "type": "pong",
                            "timestamp": time.time(),
                        }))

                    elif msg_type == "clear_buffer":
                        node.audio_buffer = io.BytesIO()
                        await websocket.send_text(json.dumps({
                            "type": "ack",
                            "status": "buffer_cleared",
                        }))

                    elif msg_type == "ui":
                        # Client sending UI ack or custom state request
                        req_state = ctrl.get("state", "idle")
                        req_emotion = ctrl.get("emotion")
                        req_text = ctrl.get("text")
                        await self.send_ui_payload(device_id, state=req_state, emotion=req_emotion, text=req_text)

                    elif msg_type == "hello":
                        # Handshake thiết bị gửi ngay sau khi WebSocket mở
                        # (xem esp32_firmware/src/main.cpp, WStype_CONNECTED).
                        node.firmware_version = str(ctrl.get("version", "unknown"))[:32]
                        node.capabilities = str(ctrl.get("features", ""))[:256]
                        logger.info(
                            "[Xiaozhi] Handshake từ [%s]: firmware=%s, features=%s",
                            device_id, node.firmware_version, node.capabilities or "(không khai báo)",
                        )
                        node.state = "idle"
                        node.emotion = "happy"
                        active_audio_nodes[device_id]["firmware_version"] = node.firmware_version
                        active_audio_nodes[device_id]["capabilities"] = node.capabilities

                        # Pairing code (legacy hello path)
                        robot_code = str(ctrl.get("pairing_code", "")).strip()
                        if not robot_code or len(robot_code) != 6 or not robot_code.isdigit():
                            robot_code = PairingCodeRegistry.generate_code()
                        await pairing_registry.register(robot_code, device_id)

                        await websocket.send_text(json.dumps({
                            "type": "hello_ack",
                            "device_id": device_id,
                            "pairing_code": robot_code,
                            "message": "Handshake thành công.",
                            "server_time": datetime.utcnow().isoformat(),
                        }))
                        # Trả trạng thái idle để thiết bị đồng bộ OLED ngay
                        await self.send_ui_payload(device_id, state="idle", emotion="happy")
                        await self.push_behavior(device_id, force=True)
                        self._ensure_behavior_loop()

                    else:
                        await websocket.send_text(json.dumps({
                            "type": "error",
                            "message": f"Unknown control type: '{msg_type}'",
                        }))

        except WebSocketDisconnect:
            logger.info("[Xiaozhi] Thiết bị [%s] đã ngắt kết nối.", device_id)
        except Exception as exc:
            logger.error("[Xiaozhi] Lỗi ngoài dự kiến trên thiết bị [%s]: %s", device_id, exc)
        finally:
            async with self._lock:
                self._nodes.pop(device_id, None)
            self._behavior_sent.pop(device_id, None)      # kết nối lại = gửi cấu hình lại
            # Xoá pairing code khi robot offline
            await pairing_registry.unregister(device_id)
            from mateai.interfaces.websocket.realtime_hub import active_audio_nodes
            active_audio_nodes.pop(device_id, None)
            logger.info("[Xiaozhi] Đã dọn dẹp kết nối [%s]. Còn lại: %d thiết bị.", device_id, len(self._nodes))
            try:
                await websocket.close()
            except Exception:
                pass


# ---------------------------------------------------------------------------
# Global Singleton Instance
# ---------------------------------------------------------------------------
xiaozhi_gateway = XiaozhiGateway()


class _XiaozhiSink:
    """Đầu ra của robot ESP32 cho mateai.application.voice.voice_turn: chữ trên LCD + PCM ra loa."""

    def __init__(self, gateway: "XiaozhiGateway", node: "XiaozhiNode", query: str) -> None:
        self.gateway = gateway
        self.node = node
        self.query = query
        self.started = False
        self.spoken: list = []
        self.emotion = ""      # biểu cảm đã gửi cho câu đang đọc

    async def on_status(self, status: str, **info: Any) -> None:
        return None

    async def on_sentence(self, seq: int, text: str, display_text: str, **info: Any) -> None:
        self.display_text = display_text

    async def on_audio(self, seq: int, audio: bytes, text: str, kind: str, **info: Any) -> None:
        node = self.node
        device_id = node.device_id
        if node.cancel_event.is_set():
            return
        ws = node.websocket
        # Biểu cảm theo nội dung câu (trước đây luôn "happy", kể cả lúc xin lỗi / báo lỗi).
        from mateai.application.voice.emotion import emotion_for_text
        emotion = emotion_for_text(text) if kind == "speech" else (self.emotion or "neutral")
        if not self.started:
            self.started = True
            self.emotion = emotion
            await self.gateway.send_ui_payload(device_id, state="speaking", emotion=emotion, text=text[:60])
            if kind == "speech" and self.gateway.behavior_allows_gestures(device_id):
                from mateai.application.devices.robot_behavior import gesture_for_text
                gesture = gesture_for_text(text, emotion)
                if gesture:
                    await self.gateway.send_command(device_id, {"type": "cmd", "action": "animate", "anim": gesture})
            try:
                await ws.send_text(json.dumps({"session_id": device_id, "type": "tts", "state": "start"}))
                await ws.send_text(json.dumps({
                    "session_id": device_id, "type": "llm", "emotion": emotion, "text": text[:30],
                }))
            except Exception:
                pass
        elif emotion != "neutral" and emotion != self.emotion:
            self.emotion = emotion
            await self.gateway.send_ui_payload(device_id, state="speaking", emotion=emotion)
        # Phụ đề trên LCD đúng lúc câu được đọc
        try:
            await ws.send_text(json.dumps({
                "session_id": device_id, "type": "tts", "state": "sentence_start", "text": text,
            }))
        except Exception:
            pass
        if kind == "speech":
            self.spoken.append(text)
            display = getattr(self, "display_text", "") or " ".join(self.spoken)
            try:
                from mateai.interfaces.websocket.realtime_hub import broadcast_hud, broadcast_portal_ui
                await broadcast_hud({
                    "type": "voice_active",
                    "status": "speaking",
                    "text": text,
                    "display_text": display,
                    "source_device": device_id,
                    "audio_base64": None,
                    "timestamp": datetime.utcnow().isoformat(),
                })
                await broadcast_portal_ui("voice_response", {
                    "query": self.query,
                    "reply": text,
                    "display_text": display,
                    "source_device": device_id,
                    "timestamp": datetime.utcnow().isoformat(),
                })
            except Exception:
                pass
        if not audio:
            return
        # Phase 70: PCM 16 kHz mono thuần cho loa MAX98357A, gửi theo nhịp
        try:
            pcm = convert_to_pcm16_16k(audio)
            for offset in range(0, len(pcm), 2048):
                if node.cancel_event.is_set():
                    break
                chunk = pcm[offset: offset + 2048]
                await self.gateway._pace_playback(node, len(chunk))
                await ws.send_bytes(chunk)
        except Exception as stream_err:
            logger.error("[Xiaozhi] Lỗi stream PCM tới [%s]: %s", device_id, stream_err)
