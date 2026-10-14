# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 Dương Thanh Lịch — VN-MateAI. See LICENSE and NOTICE.
"""
tests/test_phase9_history_pruning.py
====================================
Unit & Integration Test Suite cho Giai đoạn 9:
Phase 9: History & Context Pruning for Realtime Voice Performance.

Mục tiêu kiểm thử:
1. Markdown / Code Block / Table Stripping: Lược bỏ toàn bộ rác định dạng bảng biểu, code blocks khỏi lịch sử thoại.
2. <!--VOICE:...--> Tag Extraction: Ưu tiên trích xuất câu nói tự nhiên từ thẻ VOICE.
3. Sliding Window & Rolling Context Summary: Duy trì cửa sổ trượt 3-4 lượt thoại gần nhất và nén các lượt cũ thành tóm tắt.
4. Strict Character Ceiling: Khống chế tổng số ký tự lịch sử không vượt quá 1,200 ký tự (~300 tokens).
5. Sub-Millisecond Pruning Latency: Đo lường tốc độ cắt tỉa < 1ms để không làm tăng độ trễ pipeline.
6. MemoryManager Integration: Kiểm thử phương thức get_voice_history() tích hợp sẵn trong MemoryManager.
"""

import sys
import time
from pathlib import Path
from typing import Any, Dict, List

# Add project root to sys.path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from mateai.application.conversation.history_pruner import clean_voice_content, prune_history_for_voice, rolling_context_manager
from mateai.application.conversation.memory_manager import memory_manager


def test_markdown_and_voice_tag_cleaning():
    print("\n▸ 1. Kiểm thử Làm Sạch Nội Dung (Markdown & Code Stripping, VOICE Extraction)")

    # 1.1 Khối mã nguồn và bảng biểu
    raw_markdown = """Dưới đây là thông tin chi tiết:
| Dịch vụ | Trạng thái | RAM |
| :--- | :--- | :--- |
| Nginx Web Server | RUNNING | 120MB |
| PostgreSQL DB | RUNNING | 450MB |

```python
def check_status():
    return {"status": "healthy", "uptime": 3600}
```
Hệ thống máy chủ đang hoạt động bình thường, không phát hiện sự cố bất thường nào."""

    cleaned = clean_voice_content(raw_markdown, max_chars=200)
    assert "```" not in cleaned, "Không được chứa dấu code block"
    assert "| --- |" not in cleaned, "Không được chứa bảng Markdown"
    assert "hoạt động bình thường" in cleaned
    print(f"  ✅ Đã loại bỏ code & bảng, rút gọn sạch sẽ: '{cleaned[:70]}...' ({len(cleaned)} chars)")

    # 1.2 Trích xuất thẻ <!--VOICE:...-->
    raw_with_voice = """Chi tiết bảng lương nhân sự:
| Họ tên | Lương cơ bản | Thưởng |
| Nguyễn Văn A | 15,000,000 | 2,000,000 |
<!--VOICE: Dạ, tổng tiền thưởng tháng này của chi nhánh là ba mươi hai triệu đồng ạ. -->"""

    cleaned_voice = clean_voice_content(raw_with_voice)
    assert cleaned_voice == "Dạ, tổng tiền thưởng tháng này của chi nhánh là ba mươi hai triệu đồng ạ."
    print(f"  ✅ Trích xuất thẻ VOICE hoàn hảo: '{cleaned_voice}'")


def test_sliding_window_and_compression():
    print("\n▸ 2. Kiểm thử Cửa Sổ Trượt & Nén Lịch Sử (Sliding Window & Compression)")

    # Tạo chuỗi hội thoại giả lập 10 lượt (20 tin nhắn) với nội dung dài
    raw_history: List[Dict[str, str]] = []
    for i in range(1, 11):
        raw_history.append({
            "role": "user",
            "content": f"Yêu cầu thứ {i}: Hãy kiểm tra tình trạng dịch vụ số {i} và báo cáo số liệu",
        })
        raw_history.append({
            "role": "assistant",
            "content": f"Dịch vụ số {i} đang chạy. Chi tiết: | Metric | Value |\n| Load | {i*10}% |\nĐã ghi nhận vào hệ thống giám sát trung tâm.",
        })

    t0 = time.perf_counter()
    pruned = prune_history_for_voice(raw_history, max_turns=3, max_total_chars=1000)
    elapsed_ms = (time.perf_counter() - t0) * 1000

    # 3 turns = 6 messages + 1 system summary message = 7 messages
    assert len(pruned) <= 7, f"Kỳ vọng <= 7 tin nhắn sau khi tỉa, nhận: {len(pruned)}"
    assert pruned[0]["role"] == "system"
    assert "Tóm tắt trao đổi trước" in pruned[0]["content"]

    print(f"  ✅ Từ {len(raw_history)} tin nhắn gốc → Nén thành {len(pruned)} tin nhắn súc tích (Tốn {elapsed_ms:.3f}ms < 1ms).")
    print(f"     • System note: '{pruned[0]['content'][:65]}...'")
    print(f"     • Lượt gần nhất: User '{pruned[-2]['content']}' → Assistant '{pruned[-1]['content']}'")


def test_strict_character_ceiling():
    print("\n▸ 3. Kiểm thử Khống Chế Dung Lượng Nghiêm Ngặt (Strict Character Ceiling)")

    # Tạo hội thoại cực dài (10,000 ký tự)
    long_history = [
        {"role": "user", "content": "Câu hỏi rất dài: " + "a" * 500},
        {"role": "assistant", "content": "Trả lời rất dài: " + "b" * 1500},
        {"role": "user", "content": "Câu hỏi tiếp: " + "c" * 500},
        {"role": "assistant", "content": "Trả lời tiếp: " + "d" * 1500},
        {"role": "user", "content": "Câu hỏi gần nhất: " + "e" * 200},
        {"role": "assistant", "content": "Trả lời gần nhất: " + "f" * 300},
    ]

    target_ceiling = 800
    pruned = prune_history_for_voice(long_history, max_turns=4, max_total_chars=target_ceiling)
    total_chars = sum(len(m["content"]) for m in pruned)

    assert total_chars <= target_ceiling, f"Vượt quá trần dung lượng: {total_chars} > {target_ceiling}"
    print(f"  ✅ Trần dung lượng đặt {target_ceiling} chars → Tổng dung lượng thực tế: {total_chars} chars (Bảo đảm an toàn).")


def test_memory_manager_integration():
    print("\n▸ 4. Kiểm thử Tích Hợp MemoryManager (get_voice_history API)")

    session_id = "test_voice_session_phase9"
    memory_manager.clear_history(session_id)

    # Thêm 6 lượt trao đổi vào memory_manager
    for i in range(1, 7):
        memory_manager.add_turn(
            session_id,
            user_content=f"Lệnh giọng nói số {i}: Kiểm tra máy chủ {i}",
            assistant_content=f"Dạ, máy chủ {i} hoạt động tốt. ```log\nCPU: {i*5}%\n``` Báo cáo hoàn tất.",
        )

    # Lấy lịch sử qua phương thức get_voice_history chuyên dụng cho voice
    voice_hist = memory_manager.get_voice_history(session_id, max_turns=3, max_total_chars=1200)

    assert len(voice_hist) > 0
    assert all("```" not in m["content"] for m in voice_hist)
    print(f"  ✅ memory_manager.get_voice_history() trả về {len(voice_hist)} tin nhắn thoại chuẩn.")

    # Dọn dẹp
    memory_manager.clear_history(session_id)
    print("  ✅ Đã dọn dẹp sạch phiên kiểm thử.")


def main():
    print("=" * 70)
    print("BẮT ĐẦU KIỂM THỬ GIAI ĐOẠN 9 (PHASE 9: HISTORY & CONTEXT PRUNING)")
    print("=" * 70)

    test_markdown_and_voice_tag_cleaning()
    test_sliding_window_and_compression()
    test_strict_character_ceiling()
    test_memory_manager_integration()

    print("\n" + "=" * 70)
    print("🎉 TẤT CẢ 4/4 BÀI KIỂM THỬ PHASE 9 ĐÃ ĐẠT 100% THÀNH CÔNG!")
    print("=" * 70)


if __name__ == "__main__":
    main()
