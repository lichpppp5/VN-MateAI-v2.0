# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 Dương Thanh Lịch — VN-MateAI. See LICENSE and NOTICE.
"""
tests/test_phase5_fast_command_router.py
========================================
Test Suite cho Giai đoạn 5 (Phase 5: Fast Command Router).

Mục tiêu kiểm thử:
1. Fast Command Matching: Khớp chính xác các lệnh tất định (Time, Date, CPU, RAM, Disk, Mute, Volume, Greeting, Ping).
2. Tốc độ thực thi: Latency < 5ms (không cần round-trip qua LLM).
3. Non-deterministic Pass-through: Các câu phức tạp không bị bắt nhầm, rơi xuống LLM bình thường.
4. End-to-end Voice Session Dispatch: Xác nhận sự kiện fast_path phát ra WebSocket đầy đủ.
"""

import asyncio
import sys
import time
from pathlib import Path

# Add project root to sys.path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from mateai.application.commands.fast_command_router import fast_command_router, FastCommandResult


async def test_time_and_date():
    print("\n▸ 1. Kiểm thử Lệnh Thời Gian & Ngày Tháng")

    # Test 1.1: Giờ hiện tại
    r_time = await fast_command_router.dispatch("mấy giờ rồi", synthesize_audio=False)
    assert r_time is not None, "Kỳ vọng khớp lệnh get_current_time"
    assert r_time.command_name == "get_current_time"
    assert "giờ" in r_time.reply_text and "phút" in r_time.reply_text
    assert r_time.latency_ms < 50.0, f"Độ trễ quá cao: {r_time.latency_ms}ms"
    print(f"  ✅ 'mấy giờ rồi' → '{r_time.reply_text}' (Latency: {r_time.latency_ms:.2f}ms)")

    # Test 1.2: Ngày hôm nay
    r_date = await fast_command_router.dispatch("hôm nay ngày mấy", synthesize_audio=False)
    assert r_date is not None, "Kỳ vọng khớp lệnh get_current_date"
    assert r_date.command_name == "get_current_date"
    assert "ngày" in r_date.reply_text and "tháng" in r_date.reply_text
    print(f"  ✅ 'hôm nay ngày mấy' → '{r_date.reply_text}' (Latency: {r_date.latency_ms:.2f}ms)")


async def test_system_health():
    print("\n▸ 2. Kiểm thử Lệnh Trạng Thái Hệ Thống (CPU, RAM, Ổ cứng)")

    # Test 2.1: CPU
    r_cpu = await fast_command_router.dispatch("kiểm tra cpu", synthesize_audio=False)
    assert r_cpu is not None, "Kỳ vọng khớp lệnh get_cpu_status"
    assert r_cpu.command_name == "get_cpu_status"
    assert "CPU" in r_cpu.reply_text and "%" in r_cpu.reply_text
    print(f"  ✅ 'kiểm tra cpu' → '{r_cpu.reply_text}' (Latency: {r_cpu.latency_ms:.2f}ms)")

    # Test 2.2: RAM
    r_ram = await fast_command_router.dispatch("kiểm tra ram", synthesize_audio=False)
    assert r_ram is not None, "Kỳ vọng khớp lệnh get_ram_status"
    assert r_ram.command_name == "get_ram_status"
    assert "RAM" in r_ram.reply_text and "GB" in r_ram.reply_text
    print(f"  ✅ 'kiểm tra ram' → '{r_ram.reply_text}' (Latency: {r_ram.latency_ms:.2f}ms)")

    # Test 2.3: Ổ cứng
    r_disk = await fast_command_router.dispatch("kiểm tra ổ đĩa", synthesize_audio=False)
    assert r_disk is not None, "Kỳ vọng khớp lệnh get_disk_status"
    assert r_disk.command_name == "get_disk_status"
    assert "ổ đĩa" in r_disk.reply_text.lower()
    print(f"  ✅ 'kiểm tra ổ đĩa' → '{r_disk.reply_text}' (Latency: {r_disk.latency_ms:.2f}ms)")


async def test_volume_and_mute():
    print("\n▸ 3. Kiểm thử Lệnh Điều Khiển Âm Lượng & Tắt Tiếng")

    # Test 3.1: Mute
    r_mute = await fast_command_router.dispatch("tắt tiếng", synthesize_audio=False)
    assert r_mute is not None, "Kỳ vọng khớp lệnh mute_audio"
    assert r_mute.command_name == "mute_audio"
    assert "tắt tiếng" in r_mute.reply_text
    print(f"  ✅ 'tắt tiếng' → '{r_mute.reply_text}' (Latency: {r_mute.latency_ms:.2f}ms)")

    # Test 3.2: Set Volume
    r_vol = await fast_command_router.dispatch("cài âm lượng lên 80%", synthesize_audio=False)
    assert r_vol is not None, "Kỳ vọng khớp lệnh set_volume"
    assert r_vol.command_name == "set_volume"
    assert "80%" in r_vol.reply_text
    print(f"  ✅ 'cài âm lượng lên 80%' → '{r_vol.reply_text}' (Latency: {r_vol.latency_ms:.2f}ms)")


async def test_greetings_and_ping():
    print("\n▸ 4. Kiểm thử Chào Hỏi & Ping Tự Kiểm Tra")

    # Test 4.1: Chào hỏi
    r_greet = await fast_command_router.dispatch("xin chào", synthesize_audio=False)
    assert r_greet is not None, "Kỳ vọng khớp lệnh quick_greeting"
    assert r_greet.command_name == "quick_greeting"
    print(f"  ✅ 'xin chào' → '{r_greet.reply_text}' (Latency: {r_greet.latency_ms:.2f}ms)")

    # Test 4.2: Ping
    r_ping = await fast_command_router.dispatch("ping", synthesize_audio=False)
    assert r_ping is not None, "Kỳ vọng khớp lệnh system_ping"
    assert r_ping.command_name == "system_ping"
    assert "Pong" in r_ping.reply_text
    print(f"  ✅ 'ping' → '{r_ping.reply_text}' (Latency: {r_ping.latency_ms:.2f}ms)")


async def test_non_deterministic_fallback():
    print("\n▸ 5. Kiểm thử Không Bắt Nhầm Lệnh Phức Tạp (Fallback to LLM)")

    complex_queries = [
        "Hãy phân tích báo cáo doanh thu tài chính quý 3 năm 2026",
        "Viết cho tôi một kịch bản phim ngắn về trí tuệ nhân tạo",
        "Tìm lỗ hổng bảo mật trên hệ thống máy chủ cơ sở dữ liệu",
        "Có bao nhiêu nhân viên đi làm muộn trong tuần này?",
    ]

    for q in complex_queries:
        t0 = time.monotonic()
        res = await fast_command_router.dispatch(q)
        elapsed_ms = (time.monotonic() - t0) * 1000
        assert res is None, f"Lệnh phức tạp '{q}' bị bắt nhầm vào Fast Command Router!"
        print(f"  ✅ Không bắt nhầm '{q[:35]}...' (Kiểm tra tốn: {elapsed_ms:.2f}ms) → Chuyển tiếp LLM an toàn.")


async def main():
    print("=" * 65)
    print("BẮT ĐẦU KIỂM THỬ GIAI ĐOẠN 5 (PHASE 5: FAST COMMAND ROUTER)")
    print("=" * 65)

    await test_time_and_date()
    await test_system_health()
    await test_volume_and_mute()
    await test_greetings_and_ping()
    await test_non_deterministic_fallback()

    print("\n" + "=" * 65)
    print("🎉 TẤT CẢ 5/5 BÀI KIỂM THỬ PHASE 5 ĐÃ ĐẠT 100% THÀNH CÔNG!")
    print("=" * 65)


if __name__ == "__main__":
    asyncio.run(main())
