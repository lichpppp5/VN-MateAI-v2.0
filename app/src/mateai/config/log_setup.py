# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 Dương Thanh Lịch — VN-MateAI. See LICENSE and NOTICE.
"""
mateai/config/log_setup.py
==========================
Cấu hình log DUY NHẤT của tiến trình (prompt cuối §95). Gộp hai thứ trước đây rải ở hai
nơi: `logging.basicConfig` trong `config/loader.py` và bộ lọc che token chỉ gắn cho
uvicorn ở `main.py` (log của chính ứng dụng thì không được che).

  - `REQUEST_ID`: id request đang xử lý — tầng HTTP đặt (`server.request_id_middleware`),
    mọi dòng log ghi trong request đó mang theo;
  - `LOG_FORMAT=text` (mặc định, đọc bằng mắt) hoặc `json` (một JSON mỗi dòng cho hệ thống
    thu log): timestamp, service, level, logger, event, request_id, (exc);
  - che token / mật khẩu / API key ở MỌI handler gắn `ContextFilter`.
"""
from __future__ import annotations

import contextvars
import json
import logging
import re
import sys
from datetime import datetime, timezone
from typing import IO, Optional

SERVICE = "vn-mateai"

#: Id của request đang xử lý — "-" khi ngoài request (luồng nền, khởi động).
REQUEST_ID: contextvars.ContextVar[str] = contextvars.ContextVar("request_id", default="-")

_SECRET_RE = re.compile(
    r"((?:[?&]|%26|\b)(?:access_token|refresh_token|auth_token|api[_-]?key|apikey|password|passwd|pwd|"
    r"secret|token)\s*[=:]\s*)[^&\s\"',]+",
    re.IGNORECASE,
)
_BEARER_RE = re.compile(r"(Bearer\s+)[A-Za-z0-9._\-]+", re.IGNORECASE)


def redact(text: str) -> str:
    return _BEARER_RE.sub(r"\1[REDACTED]", _SECRET_RE.sub(r"\1[REDACTED]", text))


class ContextFilter(logging.Filter):
    """Gắn request_id + che bí mật vào bản ghi (một lần, trước mọi formatter)."""

    def filter(self, record: logging.LogRecord) -> bool:
        rid = REQUEST_ID.get()
        record.request_id = rid
        record.rid_tag = f"[{rid}] " if rid != "-" else ""
        # Che trong TỪNG tham số, giữ nguyên cấu trúc: formatter của uvicorn.access đọc lại
        # record.args (client, method, path…) — gộp thành một chuỗi sẽ làm vỡ nó.
        if isinstance(record.args, tuple) and record.args:
            record.args = tuple(redact(a) if isinstance(a, str) else a for a in record.args)
        if isinstance(record.msg, str):
            record.msg = redact(record.msg)
        return True


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        out = {
            "ts": datetime.fromtimestamp(record.created, tz=timezone.utc).isoformat(timespec="milliseconds"),
            "service": SERVICE,
            "level": record.levelname,
            "logger": record.name,
            "event": record.getMessage(),
            "request_id": getattr(record, "request_id", REQUEST_ID.get()),
        }
        if record.exc_info:
            out["exc"] = redact(self.formatException(record.exc_info))
        return json.dumps(out, ensure_ascii=False)


TEXT_FORMAT = "%(asctime)s | %(levelname)-8s | %(name)s | %(rid_tag)s%(message)s"


def make_handler(io_stream: Optional[IO[str]] = None, fmt: str = "text") -> logging.Handler:
    handler = logging.StreamHandler(io_stream or sys.stderr)
    handler.addFilter(ContextFilter())
    handler.setFormatter(JsonFormatter() if fmt == "json"
                         else logging.Formatter(TEXT_FORMAT, datefmt="%Y-%m-%d %H:%M:%S"))
    return handler


def configure_logging(level: str = "INFO", fmt: str = "text") -> None:
    """Gọi một lần khi nạp cấu hình. Thay handler của root (không cộng dồn khi nạp lại)."""
    root = logging.getLogger()
    handler = make_handler(fmt=fmt)
    handler._vnmate = True  # type: ignore[attr-defined]
    # Chỉ thay handler do chính hàm này tạo — handler của nơi khác (log stream portal, pytest) giữ nguyên.
    root.handlers[:] = [h for h in root.handlers if not getattr(h, "_vnmate", False)] + [handler]
    root.setLevel(getattr(logging, str(level).upper(), logging.INFO))
