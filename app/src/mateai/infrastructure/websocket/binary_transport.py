# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 Dương Thanh Lịch — VN-MateAI. See LICENSE and NOTICE.
"""
core/audio/binary_transport.py
==============================
Phase 11: Binary Frame Audio Transport (Realtime Voice Revamp).

Mục tiêu cốt lõi:
  - Tối ưu hóa toàn diện tầng truyền dẫn âm thanh qua WebSocket.
  - Loại bỏ hoàn toàn 33% overhead băng thông và chi phí CPU của Base64 string serialization:
      + Base64: 100 KB MP3 -> 133 KB JSON string + CPU Encode/Decode + Garbage Collection pauses.
      + Binary Transport: 100 KB MP3 -> 100 KB raw Binary Frame (Zero serialization overhead).
  - Cơ chế Packetizer thông minh:
      + Chia nhỏ các câu âm thanh dài (>32KB) thành các gói nhỏ 8KB-16KB để Client (MediaSource Extensions)
        bắt đầu phát âm thanh ngay lập tức (sub-10ms buffer playback) thay vì chờ tải trọn vẹn cả câu.
  - Tương thích kép (Dual-Mode):
      (1) Raw Binary Frame: Chuẩn ArrayBuffer đưa thẳng vào MediaSource / Web Audio API của trình duyệt.
      (2) Framed Binary Protocol (b"VM" header): Dành cho thiết bị nhúng IoT ESP32 / Robot cần phân giải sequence nhị phân.
      (3) Legacy Base64 Fallback: Chỉ kích hoạt khi client cũ gửi cờ `format: "base64"`.
"""

from __future__ import annotations

import base64
import logging
import struct
import time
from dataclasses import dataclass
from typing import Any, AsyncGenerator, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

# Kích thước gói âm thanh tối ưu cho Web Audio Streaming (16KB ~ 1 giây audio 128kbps)
DEFAULT_BINARY_CHUNK_SIZE: int = 16384  # 16 KB

# Header ma thuật nhận diện gói Framed Binary Protocol: b"VM" (VN-Mate)
BINARY_FRAME_MAGIC = b"VM"
# Cờ định dạng
FLAG_RAW_MP3 = 0x01
FLAG_IS_ACK = 0x02
FLAG_IS_FINAL = 0x04
FLAG_OPUS = 0x08


@dataclass
class BinaryAudioMetrics:
    """Số liệu hiệu năng truyền dẫn âm thanh nhị phân."""
    raw_bytes: int = 0
    base64_equivalent_bytes: int = 0
    bytes_saved: int = 0
    savings_percent: float = 0.0
    chunks_count: int = 1
    transfer_latency_ms: float = 0.0


def calculate_savings(raw_byte_count: int) -> BinaryAudioMetrics:
    """Tính toán dung lượng và phần trăm băng thông tiết kiệm được so với Base64."""
    if raw_byte_count <= 0:
        return BinaryAudioMetrics()
    # Base64 mở rộng 4/3 kích thước dữ liệu gốc + padding
    b64_size = ((raw_byte_count + 2) // 3) * 4
    saved = b64_size - raw_byte_count
    pct = (saved / b64_size) * 100.0 if b64_size > 0 else 0.0
    return BinaryAudioMetrics(
        raw_bytes=raw_byte_count,
        base64_equivalent_bytes=b64_size,
        bytes_saved=saved,
        savings_percent=round(pct, 2),
    )


def packetize_audio_chunks(audio_bytes: bytes, chunk_size: int = DEFAULT_BINARY_CHUNK_SIZE) -> List[bytes]:
    """
    Chia nhỏ luồng âm thanh thành các gói tối ưu (Packetizer):
    Nếu audio_bytes <= chunk_size, trả về danh sách 1 phần tử (Zero-Copy).
    Nếu dài hơn, chia thành các lát cắt nhỏ để client phát streaming mượt mà.
    """
    if not audio_bytes:
        return []
    if len(audio_bytes) <= chunk_size:
        return [audio_bytes]

    chunks: List[bytes] = []
    total_len = len(audio_bytes)
    offset = 0
    while offset < total_len:
        end = min(offset + chunk_size, total_len)
        chunks.append(audio_bytes[offset:end])
        offset = end

    return chunks


def encode_framed_binary_packet(
    sequence: int,
    flags: int,
    audio_data: bytes,
) -> bytes:
    """
    Đóng gói frame nhị phân có cấu trúc header chuẩn (dành cho ESP32 / IoT Hardware Client):
    Header (6 bytes):
      - Magic: 2 bytes ('VM')
      - Sequence: 2 bytes uint16 (0..65535)
      - Flags: 1 byte uint8
      - Reserved: 1 byte uint8 (0x00)
    Payload:
      - raw audio bytes
    """
    header = struct.pack("!2sHBB", BINARY_FRAME_MAGIC, sequence & 0xFFFF, flags & 0xFF, 0x00)
    return header + audio_data


def decode_framed_binary_packet(packet: bytes) -> Optional[Tuple[int, int, bytes]]:
    """
    Giải mã frame nhị phân có header:
    Trả về: (sequence, flags, raw_audio_data) hoặc None nếu không hợp lệ.
    """
    if len(packet) < 6:
        return None
    magic, seq, flags, _ = struct.unpack("!2sHBB", packet[:6])
    if magic != BINARY_FRAME_MAGIC:
        return None
    return seq, flags, packet[6:]


async def dispatch_binary_audio(
    session: Any,
    audio_bytes: bytes,
    sequence: int = 1,
    is_ack: bool = False,
    request_id: str = "",
    packetize: bool = True,
    chunk_size: int = DEFAULT_BINARY_CHUNK_SIZE,
) -> BinaryAudioMetrics:
    """
    Hàm phát âm thanh tối ưu hóa cho Realtime Voice Session:
      1. Bỏ qua Base64, gửi trực tiếp Binary Frame qua websocket.send_bytes.
      2. Nếu file âm thanh lớn, tự động packetize để browser bắt đầu phát âm thanh ngay lập tức.
      3. Nếu client yêu cầu tương thích ngược (session.legacy_base64), gửi thêm audio_chunk JSON.
      4. Thu thập và trả về số liệu tiết kiệm băng thông.
    """
    if not audio_bytes or not session or not session.is_connected:
        return BinaryAudioMetrics()

    t0 = time.perf_counter()
    chunks = packetize_audio_chunks(audio_bytes, chunk_size) if packetize else [audio_bytes]

    for chunk in chunks:
        # Gửi Binary Frame thô (Raw ArrayBuffer) cho MediaSource / Web Audio API
        await session.send_binary(chunk)

    # Nếu client yêu cầu fallback Base64 cũ
    if getattr(session, "legacy_base64", False):
        b64_str = base64.b64encode(audio_bytes).decode("ascii")
        await session.send_event("audio_chunk", {
            "sequence": sequence,
            "is_ack": is_ack,
            "request_id": request_id,
            "data": b64_str,
        })

    elapsed_ms = (time.perf_counter() - t0) * 1000
    metrics = calculate_savings(len(audio_bytes))
    metrics.chunks_count = len(chunks)
    metrics.transfer_latency_ms = round(elapsed_ms, 3)

    logger.debug(
        "[BinaryTransport] Dispatched #%d (%d bytes across %d chunk(s), saved %d bytes ~%.1f%% vs Base64 in %.2fms)",
        sequence, len(audio_bytes), len(chunks), metrics.bytes_saved, metrics.savings_percent, elapsed_ms,
    )
    return metrics
