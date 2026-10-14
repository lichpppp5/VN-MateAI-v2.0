# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 Dương Thanh Lịch — VN-MateAI. See LICENSE and NOTICE.
"""
core/audio_cache.py
===================
Phase 36: Dynamic Audio Cache & Reflex Vocabulary Store.

Quản lý bộ nhớ đệm âm thanh cục bộ sử dụng thuật toán băm MD5:
  - Băm nội dung câu thoại (chuẩn hóa khoảng trắng, chữ thường).
  - Lưu và truy xuất file .mp3 từ storage/audio_cache/[hash].mp3.
  - Phục vụ tức thì với độ trễ 0ms cho các câu phản xạ, từ đệm, thông báo hệ thống.
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
import threading
from pathlib import Path
from typing import Dict, Optional, Tuple

logger = logging.getLogger(__name__)

# Base directory for cached audio files
# Thư mục gốc dự án — một nguồn (settings.PROJECT_ROOT, đúng cả bản đóng gói),
# không suy từ vị trí file mã nguồn.
from mateai.config.loader import settings as _settings  # noqa: E402
PROJECT_ROOT = Path(_settings.PROJECT_ROOT)
CACHE_DIR = PROJECT_ROOT / "storage" / "audio_cache"
INDEX_FILE = CACHE_DIR / "cache_index.json"

_cache_lock = threading.Lock()
_RAM_AUDIO_CACHE: Dict[str, bytes] = {}
_MAX_RAM_CACHE: int = 256


def _ensure_cache_dir() -> Path:
    """Ensure storage/audio_cache directory exists."""
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    return CACHE_DIR


def normalize_text_for_hash(text: str) -> str:
    """
    Chuẩn hóa văn bản trước khi tạo mã băm:
    - Loại bỏ dấu cách thừa, tab, xuống dòng.
    - Chuyển thành chữ thường.
    - Loại bỏ dấu câu cơ bản ở cuối để tối ưu hóa tái sử dụng.
    """
    if not text:
        return ""
    # Chuyển thường và gộp khoảng trắng
    cleaned = re.sub(r"\s+", " ", text.strip().lower())
    # Bỏ dấu câu cuối câu (. ! ? : ;) để "chờ em một chút ạ." khớp với "chờ em một chút ạ"
    cleaned = re.sub(r"[\.!\?:;…]+$", "", cleaned).strip()
    return cleaned


def _active_tag() -> str:
    try:
        from mateai.infrastructure.tts.vbee_tts import resolve_tts
        return resolve_tts()["tag"]
    except Exception:  # noqa: BLE001
        return ""


def get_audio_hash(text: str, tag: Optional[str] = None) -> str:
    """
    Mã MD5 của câu (chữ thường, gộp khoảng trắng) + GIỌNG đọc.

    `tag` = giọng (vd "vbee:hn_female_ngochuyen_full_48k-fhg"); bỏ trống = giọng đang cấu hình. Trước 2026-10-12 khoá
    chỉ theo chữ: đổi sang giọng khác (Vbee, ElevenLabs, Nam Minh…) mà các câu đã lưu (lời đệm, "Anh còn cần…", lời chào)
    vẫn phát giọng cũ. Giọng mặc định Hoài My có tag rỗng → khoá giữ nguyên như cũ, cache sẵn có vẫn dùng được.
    """
    norm = normalize_text_for_hash(text)
    if not norm:
        return ""
    tag = _active_tag() if tag is None else tag
    key = f"{tag}|{norm}" if tag else norm
    return hashlib.md5(key.encode("utf-8")).hexdigest()


def check_cached_audio(text: str, tag: Optional[str] = None) -> Optional[str]:
    """
    Kiểm tra nếu file [hash].mp3 tồn tại trong storage/audio_cache/,
    trả về đường dẫn tệp (str). Nếu không tồn tại hoặc rỗng, trả về None.
    """
    h = get_audio_hash(text, tag)
    if not h:
        return None

    file_path = CACHE_DIR / f"{h}.mp3"
    if file_path.is_file() and file_path.stat().st_size > 100:
        return str(file_path)
    return None


def get_cached_audio_bytes(text: str, tag: Optional[str] = None) -> Optional[bytes]:
    """
    Lấy trực tiếp nội dung bytes âm thanh từ RAM cache (0ms) hoặc Disk cache nếu tồn tại (theo giọng — xem get_audio_hash).
    """
    h = get_audio_hash(text, tag)
    if not h:
        return None

    # 1. Tra cứu RAM cache siêu tốc (0ms, Zero Disk I/O)
    with _cache_lock:
        if h in _RAM_AUDIO_CACHE:
            return _RAM_AUDIO_CACHE[h]

    # 2. Tra cứu Disk cache
    path_str = check_cached_audio(text, tag)
    if not path_str:
        return None
    try:
        with open(path_str, "rb") as f:
            data = f.read()
            if len(data) > 100:
                with _cache_lock:
                    if len(_RAM_AUDIO_CACHE) < _MAX_RAM_CACHE:
                        _RAM_AUDIO_CACHE[h] = data
                return data
    except Exception as exc:
        logger.warning("[AudioCache] Lỗi đọc file cache %s: %s", path_str, exc)
    return None


def save_to_cache(text: str, audio_bytes: bytes, voice: str = "vi-VN-HoaiMyNeural",
                  tag: Optional[str] = None) -> Optional[str]:
    """
    Lưu luồng bytes âm thanh sinh ra từ TTS vào RAM cache và file [hash].mp3.
    Cập nhật cache_index.json để dễ theo dõi.
    """
    if not audio_bytes or len(audio_bytes) < 100:
        return None

    h = get_audio_hash(text, tag)
    if not h:
        return None

    with _cache_lock:
        if len(_RAM_AUDIO_CACHE) < _MAX_RAM_CACHE:
            _RAM_AUDIO_CACHE[h] = audio_bytes

    _ensure_cache_dir()
    file_path = CACHE_DIR / f"{h}.mp3"

    with _cache_lock:
        try:
            # Ghi file nhị phân mp3
            with open(file_path, "wb") as f:
                f.write(audio_bytes)

            # Cập nhật metadata index
            _update_index(h, text, voice, len(audio_bytes))
            logger.info("[AudioCache] Đã lưu cache âm thanh: hash=%s text='%s' (%d bytes)", h, text[:50], len(audio_bytes))
            return str(file_path)
        except Exception as exc:
            logger.error("[AudioCache] Lỗi lưu cache âm thanh: %s", exc)
            return None


def _update_index(h: str, text: str, voice: str, size: int) -> None:
    """Cập nhật tệp chỉ mục cache_index.json."""
    index_data: Dict[str, dict] = {}
    if INDEX_FILE.is_file():
        try:
            with open(INDEX_FILE, "r", encoding="utf-8") as f:
                index_data = json.load(f)
        except Exception:
            index_data = {}

    index_data[h] = {
        "text": text,
        "norm_text": normalize_text_for_hash(text),
        "voice": voice,
        "size_bytes": size,
        "file_name": f"{h}.mp3",
    }

    try:
        with open(INDEX_FILE, "w", encoding="utf-8") as f:
            json.dump(index_data, f, ensure_ascii=False, indent=2)
    except Exception as exc:
        logger.warning("[AudioCache] Không thể cập nhật cache_index.json: %s", exc)


def get_cache_stats() -> dict:
    """Trả về thống kê số lượng file và dung lượng bộ nhớ đệm."""
    _ensure_cache_dir()
    mp3_files = list(CACHE_DIR.glob("*.mp3"))
    total_size = sum(f.stat().st_size for f in mp3_files)
    return {
        "total_files": len(mp3_files),
        "total_size_bytes": total_size,
        "total_size_mb": round(total_size / (1024 * 1024), 2),
        "cache_dir": str(CACHE_DIR),
    }
