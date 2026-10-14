#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 Dương Thanh Lịch — VN-MateAI. See LICENSE and NOTICE.
"""
scripts/prewarm_vocabulary.py
=============================
Phase 36: Prewarm Reflex Vocabulary Store.

Khởi tạo sẵn các file âm thanh cho vốn từ phản xạ tức thì (Filler Words,
Keep-Alive Alerts, Wake & Sleep Phrases) và lưu vào storage/audio_cache/.

Đảm bảo khi hệ thống chạy thực tế, toàn bộ câu đệm và câu phản xạ đạt
độ trễ 0ms (Cache Hit) hoàn toàn, không phải chờ mạng hay gọi Edge-TTS.
"""

import asyncio
import os
import sys
import time
from pathlib import Path

# Add project root to sys.path
PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from mateai.infrastructure.tts.audio_cache import (
    check_cached_audio,
    get_audio_hash,
    get_cache_stats,
    save_to_cache,
)

# Phase 36.1 (Hotfix): Phân loại từ đệm theo Intent (Ngữ cảnh)
CONTEXTUAL_FILLERS = {
    "communication": {
        "keywords": ["nhắn", "gửi", "thông báo", "email", "chat"],
        "phrases": [
            "Dạ, em gửi ngay ạ.",
            "Vâng, em đang soạn tin nhắn đây.",
        ],
    },
    "system_check": {
        "keywords": ["kiểm tra", "check", "quét", "lỗi", "log", "tình trạng"],
        "phrases": [
            "Vâng, em đang kiểm tra hệ thống ngay đây.",
            "Anh đợi em quét dữ liệu một chút nhé.",
        ],
    },
    "action_execute": {
        "keywords": ["bật", "tắt", "mở", "khởi động", "reset", "xóa", "tạo"],
        "phrases": [
            "Dạ, em thực hiện ngay đây.",
            "Vâng, em đang xử lý lệnh của anh ạ.",
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
        ],
    },
    "general": {
        "keywords": [],  # Fallback nếu không khớp cái nào
        "phrases": [
            "Dạ, anh chờ em một chút ạ.",
            "Em đang xử lý ngay đây ạ.",
        ],
    },
}

# Các câu thoại phản xạ hệ thống khác (Keep-alive, Wake, Sleep, Active Listening)
SYSTEM_REFLEX_PHRASES = [
    # Cảnh báo chờ lâu (Keep-Alive Timeout > 8s)
    "Hệ thống mạng đang phản hồi hơi chậm, anh chờ em thêm một chút xíu nhé!",

    # Phản hồi đánh thức (Wake phrases)
    "Dạ em nghe ạ.",
    "Dạ em đây ạ.",

    # Thoát & Chuyển chế độ ngủ (Sleep & Inactivity phrases)
    "Em xin phép tạm nghỉ, khi nào cần anh cứ gọi em nhé.",
    "Dạ, tạm biệt anh! Khi nào cần chỉ cần gọi em nhé.",

    # Câu hỏi gợi mở sau tác vụ (Active Listening prompts)
    "Anh có muốn thao tác gì tiếp không ạ?",
    "Anh cần em hỗ trợ gì thêm nữa không ạ?",

    # Từ đệm mở rộng
    "Dạ, em đang thực thi ngay đây ạ.",
    "Dạ vâng, em đã nhận lệnh và đang xử lý ạ.",
]

# Tổng hợp toàn bộ danh sách cần sinh âm thanh (Cache)
_all_phrases = []
for group in CONTEXTUAL_FILLERS.values():
    _all_phrases.extend(group.get("phrases", []))
_all_phrases.extend(SYSTEM_REFLEX_PHRASES)

# Loại bỏ trùng lặp mà vẫn giữ thứ tự
REFLEX_PHRASES = list(dict.fromkeys(_all_phrases))


async def prewarm_all(force: bool = False, voice: str = "vi-VN-HoaiMyNeural") -> None:
    """
    Sinh file âm thanh cho toàn bộ danh sách REFLEX_PHRASES và lưu vào storage/audio_cache/.
    """
    from mateai.application.voice.speech_text import sanitise_for_tts, shorten_for_speech
    from mateai.infrastructure.tts.tts_stream_engine import get_tts_engine

    print("=" * 70)
    print("🚀 [VN-MateAI] KHỞI TẠO VỐN TỪ PHẢN XẠ - DYNAMIC AUDIO CACHE PREWARM")
    print(f"🎙️ Giọng đọc mục tiêu: {voice} (Microsoft Neural)")
    print(f"📁 Thư mục lưu trữ   : {PROJECT_ROOT / 'storage' / 'audio_cache'}")
    print(f"📝 Số lượng câu thoại: {len(REFLEX_PHRASES)}")
    print("=" * 70)

    success_count = 0
    hit_count = 0
    t_start = time.perf_counter()

    for idx, phrase in enumerate(REFLEX_PHRASES, start=1):
        h = get_audio_hash(phrase)
        existing = check_cached_audio(phrase)

        if existing and not force:
            size_kb = round(os.path.getsize(existing) / 1024, 1)
            print(f"[{idx:02d}/{len(REFLEX_PHRASES):02d}] ⚡ [HIT 0ms] '{phrase}'")
            print(f"       ↳ File: {Path(existing).name} ({size_kb} KB) | MD5: {h}")
            hit_count += 1
            success_count += 1
            continue

        print(f"[{idx:02d}/{len(REFLEX_PHRASES):02d}] ⏳ [SYNTH] Đang tổng hợp: '{phrase}'...")
        synth_t0 = time.perf_counter()
        try:
            audio_bytes = await get_tts_engine().synthesise(
                shorten_for_speech(sanitise_for_tts(phrase)), voice=voice
            )
            synth_dur = time.perf_counter() - synth_t0

            if audio_bytes and len(audio_bytes) > 100:
                saved_path = save_to_cache(phrase, audio_bytes, voice=voice)
                size_kb = round(len(audio_bytes) / 1024, 1)
                print(f"       ✅ Đã lưu: {Path(saved_path).name} ({size_kb} KB) trong {synth_dur:.2f}s | MD5: {h}")
                success_count += 1
            else:
                print(f"       ❌ Thất bại: Không nhận được dữ liệu âm thanh hợp lệ.")
        except Exception as exc:
            print(f"       ❌ Lỗi tổng hợp: {exc}")

        # Khoảng nghỉ nhẹ giữa các request API
        await asyncio.sleep(0.3)

    t_total = time.perf_counter() - t_start
    stats = get_cache_stats()

    print("\n" + "=" * 70)
    print("📊 KẾT QUẢ HOÀN TẤT PREWARM VỐN TỪ:")
    print(f"  • Thành công   : {success_count}/{len(REFLEX_PHRASES)} câu thoại ({hit_count} có sẵn, {success_count - hit_count} mới sinh)")
    print(f"  • Tổng thời gian: {t_total:.2f}s")
    print(f"  • Bộ nhớ đệm   : {stats['total_files']} files, {stats['total_size_mb']} MB")
    print("=" * 70)


def main():
    force_rebuild = "--force" in sys.argv or "-f" in sys.argv
    asyncio.run(prewarm_all(force=force_rebuild))


if __name__ == "__main__":
    main()
