# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 Dương Thanh Lịch — VN-MateAI. See LICENSE and NOTICE.
"""
mateai/interfaces/http/log_stream.py
====================================
Nhật ký thời gian thực: handler giữ bộ đệm vòng các dòng log và đẩy qua
WebSocket portal; bộ lọc che bí mật gắn vào MỌI handler. `install(loop)` gọi
một lần lúc khởi động; router `/api/v1/logs` và health dashboard đọc qua
`get_handler()`.
"""
from __future__ import annotations

import asyncio
import collections
import json
import logging
import re
import threading
from datetime import datetime
from typing import Any, Dict, List, Optional

from mateai.interfaces.http.secret_masking import _SECRET_MASK
from mateai.interfaces.websocket.realtime_hub import active_hud_websockets, active_portal_websockets, broadcast_hud

logger = logging.getLogger(__name__)


class _WebSocketLogHandler(logging.Handler):
    """
    Python log handler that stores recent log records in an in-memory ring buffer
    and pushes every log record to all connected Portal UI & HUD WebSocket clients
    as a 'log_entry' event in real-time.
    Thread-safe: uses threading.Lock and run_coroutine_threadsafe.
    """
    _LEVEL_COLOR = {
        "DEBUG":    "text-slate-400",
        "INFO":     "text-emerald-400",
        "WARNING":  "text-amber-400",
        "ERROR":    "text-red-400",
        "CRITICAL": "text-red-600 font-bold",
    }

    def __init__(self, maxlen: int = 600) -> None:
        super().__init__()
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._buffer: collections.deque = collections.deque(maxlen=maxlen)
        self._lock = threading.Lock()

    def set_loop(self, loop: asyncio.AbstractEventLoop) -> None:
        """Store the running event loop so emit() can schedule safely."""
        self._loop = loop

    def get_recent_logs(self, limit: int = 200) -> List[Dict[str, Any]]:
        """Lấy danh sách log gần nhất từ buffer."""
        with self._lock:
            return list(self._buffer)[-limit:]

    def clear_buffer(self) -> None:
        """Làm trống buffer."""
        with self._lock:
            self._buffer.clear()

    def emit(self, record: logging.LogRecord) -> None:
        try:
            level = record.levelname
            raw_msg = record.getMessage()
            now_iso = datetime.utcnow().isoformat()

            entry = {
                "event":     "log_entry",
                "level":     level,
                "color":     self._LEVEL_COLOR.get(level, "text-slate-300"),
                "logger":    record.name,
                "message":   raw_msg,
                "timestamp": now_iso,
            }

            # Luôn lưu vào in-memory ring buffer để client mới vào xem được ngay lịch sử
            with self._lock:
                self._buffer.append(entry)

            # Skip WebSocket broadcast if loop not ready or no clients
            if not self._loop or not self._loop.is_running():
                return
            if not active_portal_websockets and not active_hud_websockets:
                return

            # 1. Send to Portal UI
            if active_portal_websockets:
                msg = json.dumps(entry, ensure_ascii=False)
                asyncio.run_coroutine_threadsafe(
                    self._send_to_all(msg), self._loop
                )

            # 2. Phase 33: Send to VN-MateAI Standby HUD
            if active_hud_websockets:
                hud_payload = {
                    "type":      "system_log",
                    "level":     level,
                    "logger":    record.name,
                    "message":   raw_msg,
                    "timestamp": now_iso,
                }
                asyncio.run_coroutine_threadsafe(
                    broadcast_hud(hud_payload), self._loop
                )
        except Exception:
            pass  # never let logging errors crash the server

    @staticmethod
    async def _send_to_all(msg: str) -> None:
        dead: set = set()
        for ws in list(active_portal_websockets):
            try:
                await ws.send_text(msg)
            except Exception:
                dead.add(ws)
        for ws in dead:
            active_portal_websockets.discard(ws)


# Module-level reference so startup can configure it
_ws_log_handler: Optional["_WebSocketLogHandler"] = None


# Bí mật nằm trong URL mà thư viện HTTP tự ghi ra log.
#
# PHÁT HIỆN KHI QUÉT RÒ RỈ (Phase 80): `httpx` ở mức INFO ghi lại nguyên dòng
# request, mà URL của Telegram Bot API có dạng `/bot<token>/sendMessage`. Nên
# mỗi lần bot gửi tin là token bot thật nằm trong dòng log — và
# `GET /api/v1/logs/recent` phục vụ chính dòng log đó cho MỌI tài khoản đã
# đăng nhập, kể cả `viewer`. Che bí mật ở endpoint cấu hình không có tác dụng
# với đường rò này.
#
# Tắt logger của httpx không đủ: bất kỳ thư viện hay dòng log nào khác cũng
# có thể lọt bí mật ra. Nên che TẠI MỘT CHỖ: mọi bản ghi log đi qua bộ lọc này
# trước khi tới handler nào.
_SECRET_LOG_PATTERNS = (
    # Token bot Telegram nằm trong URL API. Che MỌI giá trị sau
    # "api.telegram.org/bot" chứ không chỉ đúng dạng token chuẩn: giá trị lệch
    # dạng (gõ nhầm, token kiểu cũ) vẫn là bí mật và từng lọt ra nguyên vẹn.
    re.compile(r"(api\.telegram\.org/(?:file/)?bot)([^/\s\"']+)"),
    re.compile(r"(bot)(\d{5,}:[A-Za-z0-9_\-]{20,})"),
    # Khoá dạng phổ biến.
    re.compile(r"\b(sk|gsk|rk|pk|xoxb|xoxp)[-_][A-Za-z0-9_\-]{16,}"),
)


class _SecretRedactingFilter(logging.Filter):
    """Thay bí mật trong mọi dòng log bằng ký hiệu, trước khi ghi ra."""

    def filter(self, record: logging.LogRecord) -> bool:
        try:
            text = record.getMessage()
        except Exception:  # noqa: BLE001 — lỗi format không được làm hỏng log
            return True
        cleaned = text
        for pat in _SECRET_LOG_PATTERNS:
            if pat.groups == 2:
                cleaned = pat.sub(lambda m: m.group(1) + _SECRET_MASK, cleaned)
            else:
                cleaned = pat.sub(_SECRET_MASK, cleaned)
        if cleaned != text:
            record.msg = cleaned
            record.args = ()
        return True


def _install_secret_redaction() -> None:
    """Gắn bộ lọc che bí mật vào MỌI handler của logger gốc (một lần).

    Phải gắn vào HANDLER chứ không gắn vào logger. Python chỉ chạy
    `Logger.filter()` cho đúng logger được gọi tới; bản ghi của logger con
    (`httpx`, `uvicorn.access`…) đi lên bằng `callHandlers()` và chỉ bị lọc
    bởi filter của handler. Gắn lên logger gốc sẽ tưởng đã che mà thực ra
    dòng log của httpx vẫn lộ nguyên vẹn.
    """
    root = logging.getLogger()
    # Handler của chính logger con cũng phải gắn — record đi qua handler ở
    # đâu thì bị lọc ở đó.
    targets = list(root.handlers)
    for name in ("httpx", "httpx.httpcore", "httpcore", "uvicorn.access", "asyncio"):
        targets.extend(logging.getLogger(name).handlers)
    for h in targets:
        if not any(isinstance(f, _SecretRedactingFilter) for f in h.filters):
            h.addFilter(_SecretRedactingFilter())


def install(loop: asyncio.AbstractEventLoop) -> None:
    """Attach WebSocket log handler to the root logger (once)."""
    global _ws_log_handler
    _install_secret_redaction()
    root = logging.getLogger()
    for h in root.handlers:
        if isinstance(h, _WebSocketLogHandler):
            h.set_loop(loop)
            _ws_log_handler = h
            return  # already installed, just update loop
    handler = _WebSocketLogHandler()
    handler.set_loop(loop)
    handler.setLevel(logging.DEBUG)
    fmt = logging.Formatter("%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
                            datefmt="%H:%M:%S")
    handler.setFormatter(fmt)
    # Handler MỚI thì chưa qua `_install_secret_redaction()` (hàm đó chỉ
    # gắn bộ lọc lên handler có sẵn lúc nó chạy). Bỏ dòng này thì nhật ký
    # đẩy qua WebSocket lại chứa bí mật — và `/api/v1/logs/recent` đọc thẳng
    # từ bộ đệm của handler này.
    handler.addFilter(_SecretRedactingFilter())
    root.addHandler(handler)
    _ws_log_handler = handler
    logger.info("WebSocket real-time log handler installed (thread-safe).")


def get_handler() -> Optional[_WebSocketLogHandler]:
    """Bộ đệm log hiện hành (None trước khi máy chủ khởi động xong)."""
    return _ws_log_handler
