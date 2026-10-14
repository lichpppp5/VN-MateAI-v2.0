# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 Dương Thanh Lịch — VN-MateAI. See LICENSE and NOTICE.
"""
mateai/interfaces/iot/discovery_beacon.py
=========================================
Beacon UDP để robot mới tự tìm máy chủ (Phase 53). Robot phát `VNMATE_DISCOVER`
ra LAN; máy chủ trả `VNMATE_BEACON:<ip>:<cổng IoT>`.

Chuyển từ `server.py` (Supervisor Phase 10, §198). Hai lỗi cũ đã sửa:
  - Không dò được IP thì trả về IP CỨNG `192.168.100.128` — robot ở mạng khác sẽ
    kết nối nhầm máy. Nay không dò được thì không trả lời (robot sẽ hỏi lại).
  - Cổng IoT viết cứng `8000`; nay lấy `settings.IOT_PORT` (cùng cổng `main.py` mở).
  - `bind()` nằm trong thread nên cổng bận thì thread chết im lặng; nay bind trước,
    lỗi báo về bước khởi động.
"""
from __future__ import annotations

import logging
import socket
import threading
from typing import Optional, Tuple

from mateai.config.loader import settings

logger = logging.getLogger(__name__)

THREAD_NAME = "vnmate-udp-beacon"   # topology.py đo trạng thái theo tên thread này
_DISCOVER = "VNMATE_DISCOVER"


def _local_ip_towards(peer_ip: str) -> Optional[str]:
    """IP của máy chủ trên đường tới robot (UDP connect không gửi gói nào)."""
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as probe:
            probe.connect((peer_ip, 80))
            return probe.getsockname()[0]
    except OSError:
        return None


def reply_for(data: bytes, addr: Tuple[str, int]) -> Optional[bytes]:
    """Câu trả lời cho một gói nhận được; None = không trả lời."""
    if _DISCOVER not in data.decode("utf-8", errors="ignore"):
        return None
    ip = _local_ip_towards(addr[0])
    if not ip:
        logger.warning("[Beacon] Không xác định được IP máy chủ hướng tới %s — bỏ qua gói dò.", addr[0])
        return None
    return f"VNMATE_BEACON:{ip}:{settings.IOT_PORT}".encode("utf-8")


def _serve(sock: socket.socket) -> None:
    while True:
        try:
            data, addr = sock.recvfrom(1024)
            reply = reply_for(data, addr)
            if reply:
                sock.sendto(reply, addr)
        except Exception as exc:  # noqa: BLE001 — một gói lỗi không được dừng beacon
            logger.debug("[Beacon] Bỏ qua gói lỗi: %s", exc)


def start() -> str:
    """Mở cổng beacon và chạy luồng nền. Lỗi bind ném ra cho bước khởi động ghi lại."""
    port = settings.DISCOVERY_PORT
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    try:
        sock.bind(("0.0.0.0", port))
    except OSError:
        sock.close()
        raise
    threading.Thread(target=_serve, args=(sock,), daemon=True, name=THREAD_NAME).start()
    return f"UDP {port}"
