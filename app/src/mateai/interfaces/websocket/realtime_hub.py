# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 Dương Thanh Lịch — VN-MateAI. See LICENSE and NOTICE.
"""
core/realtime_hub.py
====================
Trạng thái kết nối thời gian thực (HUD, portal, topology, thiết bị âm thanh) và
các hàm phát sóng tới chúng.

Tách khỏi core/server.py (RULE-015): module lõi (gateway, worker, skill) cần phát
sự kiện tới giao diện nhưng KHÔNG được import mateai.interfaces.http.server — server import chính
các module đó, nên import ngược lại tạo vòng phụ thuộc.
"""
from __future__ import annotations

import json as _json
from datetime import datetime
from typing import Any, Dict, Optional

from fastapi import WebSocket

# ---------------------------------------------------------------------------
# Multi-Node Audio State (Phase 10) & Portal UI WebSockets (Phase 17)
# ---------------------------------------------------------------------------
active_audio_nodes: Dict[str, Dict[str, Any]] = {}
active_portal_websockets: set[WebSocket] = set()
active_hud_websockets: set[WebSocket] = set()
active_topology_websockets: set[WebSocket] = set()


async def broadcast_topology_event(source: str, target: str, action: str = "") -> None:
    """Phase 88: Phát sóng sự kiện luồng dữ liệu thời gian thực tới giao diện Topology / Workflow."""
    payload = {
        "event": "tool_executed",
        "type": "tool_executed",
        "source": source,
        "target": target,
        "action": action,
        "timestamp": datetime.utcnow().isoformat(),
    }
    msg = _json.dumps(payload, ensure_ascii=False)

    # Gửi tới các viewer topology đang mở
    dead_topo = set()
    for ws in list(active_topology_websockets):
        try:
            await ws.send_text(msg)
        except Exception:
            dead_topo.add(ws)
    for ws in dead_topo:
        active_topology_websockets.discard(ws)

    # Gửi đồng bộ sang HUD
    dead_hud = set()
    for ws in list(active_hud_websockets):
        try:
            await ws.send_text(msg)
        except Exception:
            dead_hud.add(ws)
    for ws in dead_hud:
        active_hud_websockets.discard(ws)


async def broadcast_portal_ui(event: str, data: Optional[Dict[str, Any]] = None) -> None:
    """Phát sóng sự kiện điều khiển UI thời gian thực tới tất cả trình duyệt Web Portal."""
    if not active_portal_websockets:
        return
    payload = {"event": event, "timestamp": datetime.utcnow().isoformat(), **(data or {})}
    msg = _json.dumps(payload, ensure_ascii=False)
    dead_sockets = set()
    for ws in list(active_portal_websockets):
        try:
            await ws.send_text(msg)
        except Exception:
            dead_sockets.add(ws)
    for ws in dead_sockets:
        active_portal_websockets.discard(ws)


async def broadcast_hud(payload: Dict[str, Any]) -> None:
    """Phase 33: Phát sóng dữ liệu thị giác / âm thanh / metrics tới tất cả màn hình VN-MateAI HUD."""
    if not active_hud_websockets:
        return
    msg = _json.dumps(payload, ensure_ascii=False)
    dead_sockets = set()
    for ws in list(active_hud_websockets):
        try:
            await ws.send_text(msg)
        except Exception:
            dead_sockets.add(ws)
    for ws in dead_sockets:
        active_hud_websockets.discard(ws)


async def broadcast_hud_binary(data: bytes) -> None:
    """
    Phase 93 — Gửi raw audio bytes (MP3 chunks) tới HUD qua WebSocket Binary Frame.
    Không dùng base64 — giảm 33% overhead, client nhận và phát ngay qua Web Audio API.
    """
    if not active_hud_websockets or not data:
        return
    dead_sockets = set()
    for ws in list(active_hud_websockets):
        try:
            await ws.send_bytes(data)
        except Exception:
            dead_sockets.add(ws)
    for ws in dead_sockets:
        active_hud_websockets.discard(ws)
