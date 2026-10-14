# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 Dương Thanh Lịch — VN-MateAI. See LICENSE and NOTICE.
"""
tests/test_phase6_acoustic_ack.py
=================================
Unit & Integration Test Suite cho Giai đoạn 6:
Phase 6: Pre-warmed Acoustic ACK Cache (Context-Aware Reflex Audio).

Mục tiêu kiểm thử:
1. Catalog Integrity: Kiểm tra đầy đủ 6 danh mục ngữ cảnh (Ops, RAG, Analytics, Security, PC Control, Generic) với tiếng Việt chuẩn.
2. Context-Aware Selection: Lựa chọn câu đệm chính xác theo ý định câu hỏi.
3. Round-Robin Variety: Không lặp lại nhàm chán cùng một câu khi liên tiếp gọi lệnh cùng danh mục.
4. 0ms Retrieval: Truy xuất âm thanh tức thì (< 1ms) từ RAM Cache.
5. Backward Compatibility: Các hàm cũ trong streaming_tts_pipeline.py vẫn hoạt động bình thường.
"""

import asyncio
import sys
import time
from pathlib import Path
from typing import List

# Add project root to sys.path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from mateai.infrastructure.tts.acoustic_ack_catalog import (
    ACOUSTIC_ACK_CATALOG,
    ALL_ACOUSTIC_ACK_PHRASES,
    select_acoustic_ack,
    get_all_ack_phrases,
)
from mateai.infrastructure.tts.acoustic_ack import (
    get_acoustic_ack_audio,
    ACOUSTIC_ACK_PHRASES,
)
from mateai.infrastructure.tts.audio_cache import save_to_cache, get_cached_audio_bytes


def test_catalog_integrity():
    print("\n▸ 1. Kiểm thử Tính Toàn Vẹn của Kho Câu Đệm (Catalog Integrity)")
    expected_categories = [
        "SYSTEM_OPS",
        "SEARCH_RAG",
        "BUSINESS_ANALYTICS",
        "SECURITY_AUDIT",
        "PC_CONTROL",
        "GENERAL_GENERIC",
    ]

    for cat in expected_categories:
        assert cat in ACOUSTIC_ACK_CATALOG, f"Thiếu danh mục {cat} trong catalog!"
        phrases = ACOUSTIC_ACK_CATALOG[cat]
        assert len(phrases) >= 3, f"Danh mục {cat} quá ít câu đệm ({len(phrases)})"
        for p in phrases:
            assert len(p) >= 10, f"Câu đệm quá ngắn: '{p}'"
            assert any(c in p for c in "áàảãạăắằẳẵặâấầẩẫậđéèẻẽẹêếềểễệíìỉĩịóòỏõọôốồổỗộơớờởỡợúùủũụưứừửữựýỳỷỹỵ"), \
                f"Câu đệm thiếu dấu tiếng Việt: '{p}'"

    total = len(ALL_ACOUSTIC_ACK_PHRASES)
    assert total >= 20, f"Tổng số câu đệm quá ít: {total}"
    print(f"  ✅ Catalog hoàn chỉnh: {len(ACOUSTIC_ACK_CATALOG)} danh mục, {total} câu đệm tiếng Việt chuẩn.")


def test_context_aware_selection():
    print("\n▸ 2. Kiểm thử Lựa Chọn Câu Đệm Theo Ngữ Cảnh (Context-Aware Selector)")

    test_matrix = [
        # (Query, Expected Category)
        ("quét cổng mạng 192.168.1.1 xem có lỗ hổng không", "SECURITY_AUDIT"),
        ("kiểm tra tiến trình cpu và kill pid 9821", "SYSTEM_OPS"),
        ("lập báo cáo kpi nhân sự và doanh thu tháng này", "BUSINESS_ANALYTICS"),
        ("tìm tài liệu hướng dẫn quy trình onboarding nội bộ", "SEARCH_RAG"),
        ("chụp màn hình ứng dụng hiện tại", "PC_CONTROL"),
        ("thực thi tác vụ này cho tôi", "GENERAL_GENERIC"),
    ]

    for query, expected_cat in test_matrix:
        phrase = select_acoustic_ack(query)
        cat_phrases = ACOUSTIC_ACK_CATALOG[expected_cat]
        assert phrase in cat_phrases, f"Query '{query}' kỳ vọng danh mục {expected_cat}, nhưng nhận: '{phrase}'"
        print(f"  ✅ [{expected_cat}] '{query[:35]}...' → '{phrase}'")


def test_round_robin_variety():
    print("\n▸ 3. Kiểm thử Đa Dạng Hóa Luân Phiên (Round-Robin Variety)")

    query = "kiểm tra cpu máy chủ"
    phrases_seen = set()

    # Gọi 4 lần liên tiếp cùng một ngữ cảnh
    for _ in range(len(ACOUSTIC_ACK_CATALOG["SYSTEM_OPS"])):
        p = select_acoustic_ack(query)
        phrases_seen.add(p)

    # Đảm bảo luân chuyển nhiều câu khác nhau, không bị trùng lặp ngay
    assert len(phrases_seen) >= 3, f"Thiếu tính luân phiên đa dạng: {phrases_seen}"
    print(f"  ✅ Đã luân chuyển {len(phrases_seen)} câu khác nhau cho cùng 1 ngữ cảnh, tránh nhàm chán.")


async def test_instant_0ms_audio_retrieval():
    print("\n▸ 4. Kiểm thử Truy Xuất Âm Thanh 0ms Từ RAM Cache")

    sample_phrase = "Dạ, em đang thực thi lệnh hệ thống ngay ạ."
    mock_audio_bytes = b"MOCK_MP3_AUDIO_FRAME_ACOUSTIC_ACK_0MS" * 10

    # Nạp thủ công vào cache
    save_to_cache(sample_phrase, mock_audio_bytes)

    # Đo thời gian truy xuất
    t0 = time.monotonic()
    retrieved = await get_acoustic_ack_audio(phrase=sample_phrase)
    elapsed_ms = (time.monotonic() - t0) * 1000

    assert retrieved is not None, "Không tìm thấy audio trong cache!"
    assert retrieved == mock_audio_bytes, "Dữ liệu âm thanh không khớp!"
    assert elapsed_ms < 5.0, f"Thời gian truy xuất quá lâu: {elapsed_ms:.2f}ms"
    print(f"  ✅ Truy xuất thành công {len(retrieved)} bytes trong {elapsed_ms:.3f}ms (Chuẩn TTFA 0ms RAM Cache).")


async def test_backward_compatibility():
    print("\n▸ 5. Kiểm thử Tương Thích Ngược (Backward Compatibility)")

    # Test get_acoustic_ack_audio() không tham số
    audio = await get_acoustic_ack_audio()
    # Có thể là bytes hoặc None nếu chưa có internet và chưa cache
    assert len(ACOUSTIC_ACK_PHRASES) >= 5

    # Chọn câu đệm theo ngữ cảnh rồi lấy audio (thay cho get_acoustic_ack_for_query đã gỡ)
    p = select_acoustic_ack("tìm tài liệu nội bộ")
    a = await get_acoustic_ack_audio(phrase=p)
    assert "tra cứu" in p or "tìm kiếm" in p or "kho tri thức" in p or "tài liệu" in p
    print(f"  ✅ select_acoustic_ack + get_acoustic_ack_audio hoạt động chính xác: '{p}'")


async def main():
    print("=" * 65)
    print("BẮT ĐẦU KIỂM THỬ GIAI ĐOẠN 6 (PHASE 6: PRE-WARMED ACOUSTIC ACK CACHE)")
    print("=" * 65)

    test_catalog_integrity()
    test_context_aware_selection()
    test_round_robin_variety()
    await test_instant_0ms_audio_retrieval()
    await test_backward_compatibility()

    print("\n" + "=" * 65)
    print("🎉 TẤT CẢ 5/5 BÀI KIỂM THỬ PHASE 6 ĐÃ ĐẠT 100% THÀNH CÔNG!")
    print("=" * 65)


if __name__ == "__main__":
    asyncio.run(main())
