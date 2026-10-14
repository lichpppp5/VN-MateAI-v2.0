# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 Dương Thanh Lịch — VN-MateAI. See LICENSE and NOTICE.
"""
core/wake_word_engine.py
========================
Wake Word Detection Engine — Phase 13.

Chức năng:
  - Lắng nghe microphone liên tục ở background (daemon thread).
  - Phát hiện từ khóa "Hey Lyly" (không phân biệt hoa thường, accent).
  - CPU sử dụng < 5%: dùng phrase_time_limit ngắn + pause_threshold cao.
  - Khi phát hiện wake word → gọi callback (thường là kích hoạt VoiceWidget).
  - Thread-safe stop/start lifecycle.

Kiến trúc:
  - Dùng `speech_recognition` (Google STT) để nhận dạng trong clip ngắn.
  - Fallback: nếu không có mạng, thử English-locale recognition.
  - Amplitude được đưa vào queue cho VoiceWidget animation.

Anti-blocking Design:
  - WakeWordEngine chạy trong thread daemon riêng.
  - Callback được gọi trong thread của engine — UI phải spawn subprocess
    hoặc dùng thread-safe queue để tránh deadlock Tkinter.
"""

from __future__ import annotations

import json
import logging
import os
import queue
import re
import threading
import time
from typing import Callable, Optional

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Try importing heavy deps — graceful degrade if not installed
# ---------------------------------------------------------------------------
try:
    import speech_recognition as sr  # type: ignore[import]
    _SR_AVAILABLE = True
except ImportError:
    _SR_AVAILABLE = False
    logger.warning(
        "speech_recognition not installed. Wake word detection disabled. "
        "Run: pip install SpeechRecognition pyaudio"
    )

try:
    import numpy as np  # type: ignore[import]
    _NP_AVAILABLE = True
except ImportError:
    _NP_AVAILABLE = False

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

WAKE_PHRASES: list[str] = [
    "hey lyly",
    "hey ly ly",
    "hey lily",
    "hey lilly",
    "hey lili",
    "hey li li",
    "hey lee lee",
    "hé lyly",
    "hé ly ly",
    "hé lily",
    "hei lyly",
    "ê lyly",
    "ê ly ly",
    "ê lily",
    "hay lyly",
    "hay lily",
    "lyly",
    "lily",
    "lilly",
    "lili",
    "xin chào lyly",
    "xin chào ly ly",
    "xin chào lily",
    "chào lyly",
    "chào ly ly",
    "chào lily",
    "hello lyly",
    "hello lily",
    "ok lyly",
    "ok lily",
    # Vietnamese STT mishears of "Hey Lyly"
    "hãy lyly",
    "hãy ly ly",
    "hãy lily",
    "này lyly",
    "này ly ly",
    "này lily",
    "nè lyly",
    "nè ly ly",
    "nè lily",
    "lý lý",
    "ly ly",
    "li li",
    "lee lee",
    "lili",
    "lile",
    "lyle",
    "lulu",
]

# Additional fuzzy tokens — any of these alone triggers wake
WAKE_TOKENS: list[str] = [
    "lyly", "lily", "lilly", "lili", "lyle", "lile",
    "lý lý", "ly ly", "li li", "lulu", "leelee",
]

PHRASE_TIME_LIMIT_SEC: float = 3.0
CALIBRATION_SEC: float = 1.0
PAUSE_THRESHOLD_SEC: float = 0.8
ENERGY_THRESHOLD: int = 300
WAKE_COOLDOWN_SEC: float = 2.0

# Thư mục gốc dự án — không suy từ vị trí file mã nguồn.
from mateai.config.loader import settings as _settings  # noqa: E402
_PATTERNS_FILE = os.path.join(str(_settings.PROJECT_ROOT), "wake_word_patterns.json")


# ---------------------------------------------------------------------------
# So khớp câu gọi tên trợ lý — DÙNG CHUNG (mic máy chủ + robot Xiaozhi)
# ---------------------------------------------------------------------------
#
# Nhận dạng giọng nói nghe tên "Ly Ly" rất khác nhau (đo faster-whisper tiny/base/
# small trên giọng Hoài My / Nam Minh, 2026-10-04): "Hây li lì", "Lý lý ơi",
# "Lili", "Lilia", "Hey Lily", "Em lý Ly". So theo DẠNG ÂM: bỏ dấu, i ≡ y, các âm
# tiết của tên liền nhau (có thể bị dính thành một từ). Tên lấy từ cấu hình
# (get_assistant_name) — đổi tên trợ lý thì câu gọi đổi theo.

#: Từ đệm sau tên ("Ly Ly ơi", "Ly Ly à") — không phải nội dung lệnh.
_WAKE_FILLERS = {"oi", "ai", "a", "e", "ei", "er", "nhe", "nha", "ha", "hey", "ah"}


def _fold(text: str) -> str:
    import unicodedata
    t = unicodedata.normalize("NFD", str(text or ""))
    t = "".join(ch for ch in t if unicodedata.category(ch) != "Mn")
    return t.replace("đ", "d").replace("Đ", "D").lower()


#: Lời chào đứng trước / sau tên trong câu đánh thức ("Hey Ly Ly", "Ly Ly ơi").
_WAKE_GREETINGS = {"hey", "hi", "hello", "ok", "okay", "alo", "e", "oi", "nay", "a"}


def wake_names() -> "list[str]":
    """Các tên GỌI được: tên trợ lý + phần tên trong "Câu đánh thức" (Portal → Quản
    Lý Trợ Lý AI → Persona, bỏ lời chào) nếu khác. Trước đây ô "Câu đánh thức" chỉ
    để hiển thị — nhận câu gọi luôn và chỉ theo tên trợ lý."""
    phrase = ""
    try:
        from mateai.config.loader import get_config_section, read_raw_config
        phrase = str(get_config_section("persona").get("wake_word") or read_raw_config().get("WAKE_WORD") or "")
    except Exception:  # noqa: BLE001
        phrase = ""
    words = phrase.split()
    while words and _fold(words[0]).strip(",.!?") in _WAKE_GREETINGS:
        words.pop(0)
    while words and _fold(words[-1]).strip(",.!?") in _WAKE_GREETINGS:
        words.pop()
    try:
        from mateai.config.loader import get_assistant_name
        assistant = get_assistant_name()
    except Exception:  # noqa: BLE001
        assistant = ""
    compact = lambda t: re.sub(r"[^a-z0-9]", "", _fold(t))  # noqa: E731
    names = [assistant] if compact(assistant) else []
    # Câu đánh thức chỉ là tên trợ lý (viết liền "Lyly" hay rời "Ly Ly"): đã có.
    if words and compact(" ".join(words)) not in {compact(n) for n in names}:
        names.append(" ".join(words))
    return names or ["Ly Ly"]


def _name_syllables(name: Optional[str] = None) -> "list[str]":
    """Âm tiết của tên gọi dạng mẫu (bỏ dấu, i ≡ y): "Ly Ly" -> ["l[iy]", "l[iy]"]."""
    if name is None:
        name = wake_names()[0]
    syllables = [re.sub(r"[^a-z0-9]", "", s) for s in _fold(name).split()]
    syllables = [s for s in syllables if s] or ["ly", "ly"]
    return [re.sub(r"[iy]", "[iy]", re.escape(s)) for s in syllables]


def _is_filler(word: str, syllables: "list[str]") -> bool:
    """Từ đệm, hoặc âm tiết tên bị nhận dạng lặp ("Ly Ly Ly")."""
    f = re.sub(r"[^a-z0-9]", "", _fold(word))
    return f in _WAKE_FILLERS or not f or any(re.fullmatch(s, f) for s in syllables)


def find_wake_command(transcript: str) -> Optional[str]:
    """Câu có GỌI TÊN trợ lý không.

    None  -> không gọi tên.
    ""    -> chỉ gọi tên ("hey Ly Ly", "Ly Ly ơi").
    "..." -> phần lệnh nói liền sau tên ("Ly Ly ơi, mấy giờ rồi" -> "mấy giờ rồi").
    """
    words = str(transcript or "").split()
    folded = [re.sub(r"[^a-z0-9]", "", _fold(w)) for w in words]
    candidates = [_name_syllables(n) for n in wake_names()]
    for i in range(len(folded)):
        if not folded[i]:
            continue
        for syllables in candidates:
            pattern = re.compile("".join(syllables))
            for k in range(1, len(syllables) + 2):
                if i + k > len(folded):
                    break
                if pattern.match("".join(folded[i:i + k])):
                    rest = words[i + k:]
                    while rest and _is_filler(rest[0], syllables):
                        rest = rest[1:]
                    return " ".join(rest).strip(" ,.!?;:")
    return None


#: Âm tiết nghe GẦN GIỐNG tên khi nói xa / giọng nữ (đo 2026-10-12, model tiny): "Ly Ly ơi" → "Mình đi ở", "Ninh Ly ơi";
#: "Hey Ly Ly" → "Hãy nhìn đi"; "Ê Ly Ly" → "Em đi đi". Chỉ dùng để quyết định CÓ KIỂM LẠI bằng model lớn hơn hay không.
_NEAR_NAME = re.compile(r"^(?:l|n|m|nh|d)(?:i|y|inh|ynh|e|ee|ia)$")


def sounds_like_name(transcript: str) -> bool:
    """Đoạn ngắn có âm tiết gần giống tên trợ lý (l/n/m/nh/đ + i/y…) — đáng kiểm lại bằng model chính xác hơn."""
    words = [re.sub(r"[^a-z0-9]", "", _fold(w)) for w in str(transcript or "").split()]
    words = [w for w in words if w]
    if not words or len(words) > 8:
        return False
    return any(_NEAR_NAME.match(w) for w in words)


async def detect_wake(pcm16: bytes) -> "tuple[Optional[str], str, str]":
    """
    Đoạn âm thanh có GỌI TÊN trợ lý không — dùng chung robot (Xiaozhi) và HUD. Nhận dạng OFFLINE (âm thanh phòng không
    rời máy chủ). Hai bước: model nhanh (tiny, ~0,3 s); nếu chưa thấy tên mà đoạn nghe GẦN GIỐNG tên → model chính xác hơn
    (small, ~2 s) kiểm lại. Trả (lệnh | None, câu nghe được, bước: "fast" | "verify").
    """
    from mateai.infrastructure.audio.audio_processor import audio_engine
    heard = await audio_engine.transcribe_wake_clip(pcm16)
    command = find_wake_command(heard)
    if command is not None or not sounds_like_name(heard):
        return command, heard, "fast"
    heard2 = await audio_engine.transcribe_wake_clip(pcm16, verify=True)
    command2 = find_wake_command(heard2)
    return command2, heard2 or heard, "verify"


def _load_dynamic_patterns() -> tuple[list[str], list[str]]:
    """Load trained patterns from wake_word_patterns.json if exists."""
    try:
        path = os.path.normpath(_PATTERNS_FILE)
        if os.path.exists(path):
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            extra_phrases = data.get("patterns", [])
            extra_tokens = data.get("tokens", [])
            logger.info(
                "WakeWordEngine: Loaded %d trained patterns, %d tokens from %s",
                len(extra_phrases), len(extra_tokens), path
            )
            return extra_phrases, extra_tokens
    except Exception as exc:
        logger.warning("WakeWordEngine: Cannot load trained patterns: %s", exc)
    return [], []


def _get_initial_mic_state() -> bool:
    """Read default mic state from config (MIC_AUTO_START, default True)."""
    try:
        from mateai.config.loader import settings
        return bool(getattr(settings, "MIC_AUTO_START", True))
    except Exception:
        return True


# Phase 16: Hardware Toggle control — initialized from settings.MIC_AUTO_START
MIC_ENABLED: bool = _get_initial_mic_state()
_mic_enabled_lock = threading.Lock()


def is_mic_enabled() -> bool:
    """Check if microphone background listening is currently enabled."""
    global MIC_ENABLED
    with _mic_enabled_lock:
        return MIC_ENABLED


def set_mic_enabled(enabled: bool) -> bool:
    """Enable or disable microphone background listening and release hardware if disabled."""
    global MIC_ENABLED
    with _mic_enabled_lock:
        MIC_ENABLED = bool(enabled)
        state_str = "ENABLED (Listening)" if MIC_ENABLED else "DISABLED (Hardware Released)"
        logger.info("WakeWordEngine: Hardware state changed to %s", state_str)
        return MIC_ENABLED


# ---------------------------------------------------------------------------
# WakeWordEngine
# ---------------------------------------------------------------------------

class WakeWordEngine:
    """
    Background wake word listener with hardware lifecycle management.

    Usage:
        engine = WakeWordEngine(on_wake=lambda: print("Woken!"))
        engine.start()
        ...
        engine.stop()
    """

    def __init__(
        self,
        on_wake: Callable[[], None],
        device_index: Optional[int] = None,
    ) -> None:
        self._on_wake = on_wake
        self._device_index = device_index
        self._stop_event = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._last_wake_time: float = 0.0
        self._is_running = False
        self._amplitude_queue: queue.Queue[float] = queue.Queue(maxsize=50)

        # Merge default + trained patterns
        extra_phrases, extra_tokens = _load_dynamic_patterns()
        self._wake_phrases = list(dict.fromkeys(WAKE_PHRASES + extra_phrases))  # dedupe
        self._wake_tokens = list(dict.fromkeys(WAKE_TOKENS + extra_tokens))     # dedupe
        logger.info(
            "WakeWordEngine: Active phrases=%d, tokens=%d",
            len(self._wake_phrases), len(self._wake_tokens)
        )

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def start(self) -> bool:
        if not _SR_AVAILABLE:
            logger.error("Cannot start WakeWordEngine: speech_recognition not installed.")
            return False
        if self._is_running:
            logger.warning("WakeWordEngine already running.")
            return True
        self._stop_event.clear()
        self._thread = threading.Thread(
            target=self._listen_loop,
            name="wake-word-listener",
            daemon=True,
        )
        self._thread.start()
        self._is_running = True
        logger.info("WakeWordEngine thread started (Initial mic state: %s)", is_mic_enabled())
        return True

    def stop(self) -> None:
        self._stop_event.set()
        self._is_running = False
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=3.0)
        logger.info("WakeWordEngine stopped.")

    @property
    def is_running(self) -> bool:
        return self._is_running

    def get_amplitude(self) -> Optional[float]:
        """Pop latest amplitude (0.0–1.0) for UI animation. None if empty."""
        try:
            return self._amplitude_queue.get_nowait()
        except queue.Empty:
            return None

    # ------------------------------------------------------------------
    # Internal: Listening Loop with Hardware Release (Phase 16 Bugfix)
    # ------------------------------------------------------------------

    def listen_in_background(self) -> None:
        """Alias for _listen_loop."""
        self._listen_loop()

    def _listen_loop(self) -> None:
        recognizer = sr.Recognizer()
        recognizer.energy_threshold = ENERGY_THRESHOLD
        recognizer.dynamic_energy_threshold = True
        recognizer.pause_threshold = PAUSE_THRESHOLD_SEC
        recognizer.non_speaking_duration = 0.3

        mic_kwargs = {}
        if self._device_index is not None:
            mic_kwargs["device_index"] = self._device_index

        logger.info("WakeWordEngine: Background loop active. Hardware listening enabled=%s", is_mic_enabled())

        calibrated = False

        while not self._stop_event.is_set():
            # 1. Nếu biến MIC_ENABLED == False: Nghỉ, BẮT BUỘC KHÔNG khởi tạo sr.Microphone()
            if not is_mic_enabled():
                calibrated = False
                time.sleep(1.0)
                continue

            # 2. Chỉ mở khóa phần cứng khi được Bật: Khởi tạo sr.Microphone() bên trong block with
            try:
                with sr.Microphone(**mic_kwargs) as source:
                    logger.info("WakeWordEngine: Opened microphone hardware (stream active).")
                    if not calibrated:
                        try:
                            logger.info("WakeWordEngine: Calibrating ambient noise (%.1fs)...", CALIBRATION_SEC)
                            recognizer.adjust_for_ambient_noise(source, duration=CALIBRATION_SEC)
                            calibrated = True
                            logger.info(
                                "WakeWordEngine: Calibration done. Energy threshold = %.0f",
                                recognizer.energy_threshold,
                            )
                        except Exception as cal_err:
                            logger.warning("WakeWordEngine: Noise calibration skipped: %s", cal_err)
                            calibrated = True

                    # 3. Vòng lặp bóc băng và nhận diện Wake Word:
                    # Chạy trong block with sr.Microphone().
                    # Ngay khi MIC_ENABLED chuyển sang False hoặc _stop_event set,
                    # vòng lặp kết thúc ngay -> thoát khỏi block with sr.Microphone()
                    # -> sr.Microphone.__exit__() được gọi -> đóng stream và terminate PyAudio
                    # -> Đèn Micro trên thiết bị TẮT HOÀN TOÀN trong 1-2 giây!
                    while not self._stop_event.is_set() and is_mic_enabled():
                        try:
                            audio = recognizer.listen(
                                source,
                                timeout=0.8,
                                phrase_time_limit=PHRASE_TIME_LIMIT_SEC,
                            )
                        except sr.WaitTimeoutError:
                            continue

                        self._push_amplitude(audio)
                        transcript = self._transcribe(recognizer, audio)

                        if transcript and self._is_wake_phrase(transcript):
                            now = time.monotonic()
                            if now - self._last_wake_time > WAKE_COOLDOWN_SEC:
                                self._last_wake_time = now
                                logger.info("Wake word detected! Transcript: '%s'", transcript)
                                try:
                                    self._on_wake()
                                except Exception as cb_exc:
                                    logger.error("Wake callback error: %s", cb_exc)

                logger.info("WakeWordEngine: Exited with sr.Microphone block -> Hardware stream released and closed.")
            except (OSError, IOError) as exc:
                logger.warning("WakeWordEngine: Microphone hardware error: %s. Retrying in 1s...", exc)
                calibrated = False
                time.sleep(1.0)
            except Exception as exc:  # pylint: disable=broad-except
                logger.debug("WakeWordEngine: Listen loop exception: %s", exc)
                time.sleep(0.5)

        logger.info("WakeWordEngine: Listen loop exited.")
        self._is_running = False

    def _transcribe(self, recognizer: "sr.Recognizer", audio: "sr.AudioData") -> str:
        """
        Try multiple STT locales (en-US, vi-VN) to maximize wake word detection.
        If en-US does not match a wake phrase, vi-VN is actively attempted.
        """
        en_result = ""
        # Try English first (for clear "Hey Lyly" / "Hey Lily")
        try:
            text = recognizer.recognize_google(audio, language="en-US")
            en_result = text.lower().strip()
            if self._is_wake_phrase(en_result):
                return en_result
        except (sr.UnknownValueError, sr.RequestError):
            pass

        # Try Vietnamese (for "Hé Lyly", "Xin chào Lyly", "Lý Lý", Vietnamese accent)
        try:
            text = recognizer.recognize_google(audio, language="vi-VN")
            vi_result = text.lower().strip()
            if self._is_wake_phrase(vi_result):
                return vi_result
            if vi_result:
                return vi_result
        except (sr.UnknownValueError, sr.RequestError):
            pass

        return en_result

    def _is_wake_phrase(self, transcript: str) -> bool:
        if not transcript:
            return False
        # So khớp theo dạng âm của tên trợ lý — dùng chung với robot Xiaozhi.
        if find_wake_command(transcript) is not None:
            return True
        t = transcript.lower().strip()

        # 1. Exact phrase match in raw transcript
        for phrase in self._wake_phrases:
            if phrase in t:
                return True

        # 2. Normalized space-stripped check (handles "hey ly ly" -> "heylyly")
        t_clean = t.replace(" ", "").replace("-", "").replace(".", "").replace(",", "")
        clean_keys = [
            "lyly", "lily", "lili", "lilly", "leelee", "lyle", "lile", "lulu",
            "lýlý", "lỳlỳ", "lìlì"
        ]
        for key in clean_keys:
            if key in t_clean:
                return True

        # 3. Token-level fuzzy match
        for token in self._wake_tokens:
            if token in t:
                return True

        # 4. Prefix greeting + name ("hey ly", "chào ly", "alo ly")
        words = t.split()
        for i, w in enumerate(words):
            if w in ("hey", "hi", "hé", "ê", "hãy", "chào", "alo", "halo") and i + 1 < len(words):
                next_w = words[i + 1]
                if next_w.startswith("ly") or next_w.startswith("li"):
                    return True

        # 5. Levenshtein-like: check if any word in transcript is close to 'lyly'
        for word in words:
            if len(word) >= 2 and (
                word.startswith("lyl") or
                word.startswith("lil") or
                word.endswith("lyly") or
                word in ("luly", "lyly", "lily", "lili", "lyle", "lile", "lilly")
            ):
                return True
        return False

    def _push_amplitude(self, audio: "sr.AudioData") -> None:
        if not _NP_AVAILABLE:
            return
        try:
            raw = np.frombuffer(audio.get_raw_data(), dtype=np.int16).astype(np.float32)
            if raw.size == 0:
                return
            rms = float(np.sqrt(np.mean(raw ** 2)))
            normalized = min(rms / 5000.0, 1.0)
            try:
                self._amplitude_queue.put_nowait(normalized)
            except queue.Full:
                pass
        except Exception:  # pylint: disable=broad-except
            pass


# ---------------------------------------------------------------------------
# Module-level singleton
# ---------------------------------------------------------------------------

_engine_instance: Optional[WakeWordEngine] = None


def get_engine() -> Optional[WakeWordEngine]:
    return _engine_instance


def start_wake_word_engine(on_wake: Callable[[], None]) -> Optional[WakeWordEngine]:
    """Create and start global WakeWordEngine."""
    global _engine_instance
    if _engine_instance and _engine_instance.is_running:
        return _engine_instance
    _engine_instance = WakeWordEngine(on_wake=on_wake)
    success = _engine_instance.start()
    return _engine_instance if success else None


def stop_wake_word_engine() -> None:
    global _engine_instance
    if _engine_instance:
        _engine_instance.stop()
        _engine_instance = None


def listen_in_background(on_wake: Optional[Callable[[], None]] = None) -> Optional[WakeWordEngine]:
    """Module-level helper to trigger background wake word listening."""
    if on_wake:
        return start_wake_word_engine(on_wake)
    return get_engine()
