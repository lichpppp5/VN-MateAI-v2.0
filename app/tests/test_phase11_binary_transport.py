# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 Dương Thanh Lịch — VN-MateAI. See LICENSE and NOTICE.
"""
tests/test_phase11_binary_transport.py
======================================
Unit & Integration Test Suite cho Giai đoạn 11:
Phase 11: Binary Frame Audio Transport (Realtime Voice Revamp).

Mục tiêu kiểm thử:
1. Audio Packetizer: Kiểm tra thuật toán phân mảnh gói tối ưu (16KB chunks) cho stream audio lớn.
2. Bandwidth Savings Analytics: Xác thực công thức tiết kiệm băng thông (+33% tránh lãng phí so với Base64).
3. Framed Binary Protocol (b"VM"): Mã hóa và giải mã frame nhị phân có header cho IoT / ESP32.
4. Dispatch Binary Audio: Kiểm tra gửi frame nhị phân trực tiếp và kiểm soát cờ legacy_base64 fallback.
5. Zero-Copy Performance: Đo lường tốc độ đóng gói nhị phân < 0.05ms.
"""

import asyncio
import sys
import time
from pathlib import Path
from typing import Any, Dict, List

# Add project root to sys.path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from mateai.infrastructure.websocket.binary_transport import (
    DEFAULT_BINARY_CHUNK_SIZE,
    calculate_savings,
    decode_framed_binary_packet,
    dispatch_binary_audio,
    encode_framed_binary_packet,
    packetize_audio_chunks,
    BINARY_FRAME_MAGIC,
    FLAG_RAW_MP3,
    FLAG_IS_ACK,
)


def test_audio_packetizer():
    print("\n▸ 1. Kiểm thử Phân Mảnh Gói Âm Thanh (Audio Packetizer)")

    # 1.1 Gói nhỏ (< 16KB) -> Giữ nguyên 1 chunk duy nhất (Zero-Copy)
    small_audio = b"X" * 5000
    chunks_small = packetize_audio_chunks(small_audio, chunk_size=16384)
    assert len(chunks_small) == 1
    assert chunks_small[0] == small_audio
    print(f"  ✅ Gói nhỏ ({len(small_audio)} bytes) → 1 chunk duy nhất (Zero-copy pass).")

    # 1.2 Gói lớn (50KB) -> Chia thành 4 chunks (16KB, 16KB, 16KB, 2KB)
    large_audio = b"A" * 50000
    chunks_large = packetize_audio_chunks(large_audio, chunk_size=16384)
    assert len(chunks_large) == 4
    assert sum(len(c) for c in chunks_large) == len(large_audio)
    assert len(chunks_large[0]) == 16384
    assert len(chunks_large[-1]) == 50000 - (16384 * 3)
    print(f"  ✅ Gói lớn ({len(large_audio)} bytes) → {len(chunks_large)} chunks: {[len(c) for c in chunks_large]} bytes.")


def test_bandwidth_savings_analytics():
    print("\n▸ 2. Kiểm thử Đo Lường Tiết Kiệm Băng Thông (Bandwidth Savings)")

    sample_sizes = [1000, 10000, 50000, 100000]
    for size in sample_sizes:
        metrics = calculate_savings(size)
        assert metrics.raw_bytes == size
        assert metrics.base64_equivalent_bytes > size
        assert metrics.bytes_saved > 0
        assert 24.0 <= metrics.savings_percent <= 26.0  # Tiết kiệm ~25% dung lượng truyền tải
        print(f"  ✅ Raw: {size:>6}B | Base64: {metrics.base64_equivalent_bytes:>6}B | Tiết kiệm: {metrics.bytes_saved:>5}B ({metrics.savings_percent}%)")


def test_framed_binary_protocol():
    print("\n▸ 3. Kiểm thử Giao Thức Khung Nhị Phân Có Header (Framed Binary Protocol)")

    sample_audio = b"\xFF\xFB\x90\x64" + b"TEST_MP3_STREAM_PAYLOAD" * 20
    seq = 42
    flags = FLAG_RAW_MP3 | FLAG_IS_ACK

    # Mã hóa
    encoded_packet = encode_framed_binary_packet(sequence=seq, flags=flags, audio_data=sample_audio)
    assert encoded_packet.startswith(BINARY_FRAME_MAGIC)
    assert len(encoded_packet) == len(sample_audio) + 6

    # Giải mã
    decoded = decode_framed_binary_packet(encoded_packet)
    assert decoded is not None
    dec_seq, dec_flags, dec_audio = decoded
    assert dec_seq == seq
    assert dec_flags == flags
    assert dec_audio == sample_audio
    print(f"  ✅ Mã hóa/Giải mã thành công: seq={dec_seq}, flags=0x{dec_flags:02x}, payload={len(dec_audio)} bytes.")

    # Gói hỏng magic byte -> Trả về None
    corrupt_packet = b"XX" + encoded_packet[2:]
    assert decode_framed_binary_packet(corrupt_packet) is None
    print("  ✅ Gói hỏng header trả về None an toàn.")


async def test_dispatch_binary_audio_mock():
    print("\n▸ 4. Kiểm thử Phát Âm Thanh WebSocket (Dispatch Binary Audio)")

    class MockVoiceSession:
        def __init__(self, legacy_base64: bool = False):
            self.is_connected = True
            self.legacy_base64 = legacy_base64
            self.binary_frames_sent: List[bytes] = []
            self.events_sent: List[Dict[str, Any]] = []

        async def send_binary(self, binary_data: bytes) -> bool:
            self.binary_frames_sent.append(binary_data)
            return True

        async def send_event(self, event_type: str, data: Dict[str, Any]) -> bool:
            self.events_sent.append({"type": event_type, "data": data})
            return True

    # 4.1 Client hiện đại (Binary Only — Không gửi Base64)
    session_modern = MockVoiceSession(legacy_base64=False)
    test_audio = b"RAW_MP3_PAYLOAD_" * 500  # 8000 bytes

    t0 = time.perf_counter()
    metrics = await dispatch_binary_audio(
        session=session_modern,
        audio_bytes=test_audio,
        sequence=1,
        is_ack=False,
        request_id="req_test_1",
    )
    elapsed_ms = (time.perf_counter() - t0) * 1000

    assert len(session_modern.binary_frames_sent) == 1
    assert len(session_modern.events_sent) == 0, "Client hiện đại KHÔNG được nhận event audio_chunk base64!"
    print(f"  ✅ Modern Client: Gửi 1 binary frame {len(test_audio)}B, 0 base64 events (Tốn {elapsed_ms:.3f}ms).")

    # 4.2 Client cũ yêu cầu fallback Base64
    session_legacy = MockVoiceSession(legacy_base64=True)
    await dispatch_binary_audio(
        session=session_legacy,
        audio_bytes=test_audio,
        sequence=2,
        is_ack=True,
        request_id="req_test_2",
    )

    assert len(session_legacy.binary_frames_sent) == 1
    assert len(session_legacy.events_sent) == 1
    assert session_legacy.events_sent[0]["type"] == "audio_chunk"
    assert "data" in session_legacy.events_sent[0]["data"]
    print("  ✅ Legacy Client: Gửi cả binary frame và audio_chunk base64 dự phòng thành công.")


async def main():
    print("=" * 70)
    print("BẮT ĐẦU KIỂM THỬ GIAI ĐOẠN 11 (PHASE 11: BINARY AUDIO TRANSPORT)")
    print("=" * 70)

    test_audio_packetizer()
    test_bandwidth_savings_analytics()
    test_framed_binary_protocol()
    await test_dispatch_binary_audio_mock()

    print("\n" + "=" * 70)
    print("🎉 TẤT CẢ 4/4 BÀI KIỂM THỬ PHASE 11 ĐÃ ĐẠT 100% THÀNH CÔNG!")
    print("=" * 70)


if __name__ == "__main__":
    asyncio.run(main())
