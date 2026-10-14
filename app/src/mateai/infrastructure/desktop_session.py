# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 Dương Thanh Lịch — VN-MateAI. See LICENSE and NOTICE.
"""
mateai/infrastructure/desktop_session.py
========================================
Máy chủ có đang chạy trong PHIÊN ĐĂNG NHẬP có màn hình không.

Vì sao: tiến trình chạy dạng dịch vụ / tác vụ nền (Windows Session 0) mở trình duyệt, ứng dụng, chụp màn hình… "thành công" nhưng không
ai thấy gì. Ngày 2026-10-08 robot ra lệnh mở YouTube, cửa sổ không hiện, AI tưởng thiếu kỹ năng và tạo 39 kỹ năng gần giống nhau. Cổng
tool (`tool_gate`) hỏi module này trước khi chạy tool cần màn hình (`needs_desktop`) và trả lỗi rõ ràng thay vì "thành công" giả.

- Windows: phiên của tiến trình (`ProcessIdToSessionId`) phải khác 0 và đang là phiên điều khiển (console) hoặc phiên từ xa đang hoạt động.
- Linux: cần `DISPLAY` hoặc `WAYLAND_DISPLAY`. macOS: coi là có màn hình (không có Session 0).
- Không xác định được thì coi là CÓ màn hình (không chặn nhầm) và ghi lý do.
"""
from __future__ import annotations

import logging
import os
import sys
import time
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

_CACHE_S = 15.0
_cached: Optional[Dict[str, Any]] = None
_cached_at = 0.0


def _windows() -> Dict[str, Any]:
    import ctypes
    from ctypes import wintypes

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    sid = wintypes.DWORD()
    if not kernel32.ProcessIdToSessionId(kernel32.GetCurrentProcessId(), ctypes.byref(sid)):
        return {"interactive": True, "reason": "không đọc được phiên của tiến trình", "session_id": None}
    session = int(sid.value)
    console = int(kernel32.WTSGetActiveConsoleSessionId())
    if session == 0:
        return {"interactive": False, "session_id": 0, "console_session_id": console,
                "reason": "máy chủ đang chạy ở Session 0 (dịch vụ / tác vụ nền) — cửa sổ mở ra sẽ không hiện trên màn hình nào"}
    if session == console:
        return {"interactive": True, "session_id": session, "console_session_id": console, "reason": "phiên điều khiển (console)"}
    # Phiên khác console: có thể là Remote Desktop đang mở (vẫn có màn hình) hoặc phiên đã ngắt kết nối.
    try:
        wtsapi = ctypes.WinDLL("wtsapi32")
        state = ctypes.c_void_p()
        size = wintypes.DWORD()
        wts_conn_state = 8           # WTSConnectState
        if wtsapi.WTSQuerySessionInformationW(None, session, wts_conn_state, ctypes.byref(state), ctypes.byref(size)):
            value = ctypes.cast(state, ctypes.POINTER(ctypes.c_int)).contents.value
            wtsapi.WTSFreeMemory(state)
            if value == 0:           # WTSActive
                return {"interactive": True, "session_id": session, "console_session_id": console, "reason": "phiên từ xa đang hoạt động"}
            return {"interactive": False, "session_id": session, "console_session_id": console,
                    "reason": f"phiên {session} của máy chủ đã ngắt kết nối (không ai nhìn thấy màn hình)"}
    except Exception as exc:  # noqa: BLE001
        logger.debug("Không đọc được trạng thái phiên %s: %s", session, exc)
    return {"interactive": True, "session_id": session, "console_session_id": console, "reason": "không xác định được, coi như có màn hình"}


def _probe() -> Dict[str, Any]:
    try:
        if sys.platform == "win32":
            return _windows()
        if sys.platform == "darwin":
            return {"interactive": True, "reason": "macOS"}
        if os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY"):
            return {"interactive": True, "reason": "có DISPLAY / WAYLAND_DISPLAY"}
        return {"interactive": False, "reason": "máy chủ không có màn hình (thiếu DISPLAY / WAYLAND_DISPLAY)"}
    except Exception as exc:  # noqa: BLE001
        return {"interactive": True, "reason": f"không kiểm tra được ({type(exc).__name__}), coi như có màn hình"}


def status(refresh: bool = False) -> Dict[str, Any]:
    """{"interactive": bool, "reason": str, ...} — đọc lại tối đa mỗi 15 s (người dùng có thể đăng nhập / ngắt Remote Desktop)."""
    global _cached, _cached_at
    now = time.monotonic()
    if refresh or _cached is None or now - _cached_at > _CACHE_S:
        _cached, _cached_at = _probe(), now
    return dict(_cached)
