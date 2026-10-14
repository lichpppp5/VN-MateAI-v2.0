# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 Dương Thanh Lịch — VN-MateAI. See LICENSE and NOTICE.
"""
tests/test_phase3_sentence_buffer.py
====================================
Kiểm thử Giai đoạn 3: Sentence Buffer & Anti-False-Split (Phase 3):
1. Test case chuẩn:
   - "Xin chào anh." → 1 câu
   - "Xin chào anh. Hôm nay thế nào?" → 2 câu
   - "CPU là 32%. RAM là 61%." → 2 câu
2. Anti-False-Split (TUYỆT ĐỐI KHÔNG ngắt sai):
   - Số thập phân: 3.14
   - Địa chỉ IP: 192.168.1.1
   - Phiên bản: v1.2.3
   - Domain: example.com
   - Đường dẫn: C:\\Users\\Admin
3. Mô phỏng streaming từng token (mảnh token nhỏ) kiểm tra tính đúng đắn.
"""

import asyncio
import sys
from pathlib import Path

# Add project root to sys.path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from mateai.application.voice.sentence_buffer import SentenceBuffer


def split_into_sentences(text: str) -> list[str]:
    """Tách cả đoạn văn bằng SentenceBuffer (trước đây là hàm trong sentence_streamer)."""
    buf = SentenceBuffer(min_chars=8)
    return buf.add_token(text) + buf.flush()


def test_standard_sentences():
    print("\n▸ 1. Kiểm thử Tách câu tiêu chuẩn (Standard Sentences)")

    # Test 1.1: 1 câu đơn
    t1 = "Xin chào anh."
    res1 = split_into_sentences(t1)
    assert len(res1) == 1, f"Kỳ vọng 1 câu, nhận {len(res1)}: {res1}"
    assert res1[0] == "Xin chào anh."
    print("  ✅ 'Xin chào anh.' → 1 câu chính xác.")

    # Test 1.2: 2 câu liên tiếp
    t2 = "Xin chào anh. Hôm nay thế nào?"
    res2 = split_into_sentences(t2)
    assert len(res2) == 2, f"Kỳ vọng 2 câu, nhận {len(res2)}: {res2}"
    assert res2[0] == "Xin chào anh."
    assert res2[1] == "Hôm nay thế nào?"
    print("  ✅ 'Xin chào anh. Hôm nay thế nào?' → 2 câu chính xác.")

    # Test 1.3: 2 câu có chứa tỷ lệ phần trăm
    t3 = "CPU là 32%. RAM là 61%."
    res3 = split_into_sentences(t3)
    assert len(res3) == 2, f"Kỳ vọng 2 câu, nhận {len(res3)}: {res3}"
    assert res3[0] == "CPU là 32%."
    assert res3[1] == "RAM là 61%."
    print("  ✅ 'CPU là 32%. RAM là 61%.' → 2 câu chính xác.")


def test_anti_false_split():
    print("\n▸ 2. Kiểm thử Chống Ngắt Sai (Anti-False-Split Guards)")

    # Test 2.1: Số thập phân 3.14
    t_decimal = "Hằng số toán học pi có giá trị xấp xỉ là 3.14 trong sách giáo khoa."
    res_dec = split_into_sentences(t_decimal)
    assert len(res_dec) == 1, f"Không được tách số 3.14, nhận: {res_dec}"
    assert "3.14" in res_dec[0]
    print(f"  ✅ [Số thập phân 3.14] Giữ nguyên câu: '{res_dec[0]}'")

    # Test 2.2: Địa chỉ IP 192.168.1.1
    t_ip = "Máy chủ nội bộ đang chạy tại địa chỉ IP 192.168.1.1 đã kết nối."
    res_ip = split_into_sentences(t_ip)
    assert len(res_ip) == 1, f"Không được tách địa chỉ IP 192.168.1.1, nhận: {res_ip}"
    assert "192.168.1.1" in res_ip[0]
    print(f"  ✅ [IP 192.168.1.1] Giữ nguyên câu: '{res_ip[0]}'")

    # Test 2.3: Phiên bản v1.2.3
    t_ver = "Hệ thống VN-MateAI đã nâng cấp lên phiên bản v1.2.3 ổn định."
    res_ver = split_into_sentences(t_ver)
    assert len(res_ver) == 1, f"Không được tách phiên bản v1.2.3, nhận: {res_ver}"
    assert "v1.2.3" in res_ver[0]
    print(f"  ✅ [Version v1.2.3] Giữ nguyên câu: '{res_ver[0]}'")

    # Test 2.4: Tên miền example.com
    t_dom = "Anh vui lòng truy cập trang web example.com để lấy tài liệu."
    res_dom = split_into_sentences(t_dom)
    assert len(res_dom) == 1, f"Không được tách tên miền example.com, nhận: {res_dom}"
    assert "example.com" in res_dom[0]
    print(f"  ✅ [Domain example.com] Giữ nguyên câu: '{res_dom[0]}'")

    # Test 2.5: Đường dẫn file C:\Users\Admin
    t_path = "Tệp cấu hình được đặt tại C:\\Users\\Admin\\Desktop\\config.json thành công."
    res_path = split_into_sentences(t_path)
    assert len(res_path) == 1, f"Không được tách đường dẫn Windows, nhận: {res_path}"
    print(f"  ✅ [Path C:\\Users\\Admin] Giữ nguyên câu: '{res_path[0]}'")


async def test_token_streaming_simulation():
    print("\n▸ 3. Mô phỏng LLM Streaming từng Token (Mảnh nhỏ)")

    tokens = [
        "Chào ", "anh ", "Lịch. ",
        "Máy ", "chủ ", "tại ", "192.", "168.", "1.", "1 ",
        "đang ", "chạy ", "phiên ", "bản ", "3.", "14. ",
        "Hôm ", "nay ", "anh ", "cần ", "kiểm ", "tra ", "gì ", "ạ?"
    ]

    async def _token_generator():
        for t in tokens:
            yield t

    streamer = SentenceBuffer(min_chars=8)
    streamed_sentences = []
    async for s in streamer.stream_sentences(_token_generator()):
        streamed_sentences.append(s)

    # Kỳ vọng 3 câu rõ ràng:
    # 1: "Chào anh Lịch."
    # 2: "Máy chủ tại 192.168.1.1 đang chạy phiên bản 3.14."
    # 3: "Hôm nay anh cần kiểm tra gì ạ?"
    assert len(streamed_sentences) == 3, f"Kỳ vọng đúng 3 câu, nhận {len(streamed_sentences)}: {streamed_sentences}"
    assert "192.168.1.1" in streamed_sentences[1]
    assert "3.14" in streamed_sentences[1]
    print(f"  ✅ Câu 1: '{streamed_sentences[0]}'")
    print(f"  ✅ Câu 2: '{streamed_sentences[1]}'")
    print(f"  ✅ Câu 3: '{streamed_sentences[2]}'")


def main():
    print("=" * 60)
    print("PHASE 3: SENTENCE BUFFER & ANTI-FALSE-SPLIT VERIFICATION")
    print("=" * 60)

    test_standard_sentences()
    test_anti_false_split()
    asyncio.run(test_token_streaming_simulation())

    print("\n" + "─" * 60)
    print("✅ TẤT CẢ TEST PHASE 3 (SENTENCE BUFFER) ĐÃ PASS HOÀN TOÀN!")


if __name__ == "__main__":
    main()
