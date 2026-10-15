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
  - che token / mật khẩu / API key ở MỌI handler gắn `ContextFilter`;
  - thêm một bản ra TỆP xoay vòng (`logs/vnmateai.log`, 20 MB × 10): chạy bằng Start.bat log chỉ nằm trên cửa sổ
    console — đóng cửa sổ là mất dấu vết sự cố. `VNMATEAI_LOG_FILE` đổi đường dẫn, `none` để tắt.
"""
from __future__ import annotations

import contextvars
import json
import logging
import re
import sys
from datetime import datetime, timezone
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import IO, Optional, Union

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


#: Giới hạn tệp log: 20 MB mỗi tệp, giữ 10 tệp cũ (≤ ~220 MB).
LOG_FILE_MAX_BYTES = 20 * 1024 * 1024
LOG_FILE_BACKUPS = 10


class _SafeRotatingFileHandler(RotatingFileHandler):
    """Windows: tiến trình khác (sao lưu, script) đang mở cùng tệp thì đổi tên lúc xoay vòng lỗi PermissionError — khi đó
    ghi tiếp vào tệp cũ, lượt sau thử xoay lại; không in lỗi ở mỗi dòng log."""

    def doRollover(self) -> None:
        try:
            super().doRollover()
        except OSError:
            if self.stream is None:
                self.stream = self._open()


def _decorate(handler: logging.Handler, fmt: str) -> logging.Handler:
    handler.addFilter(ContextFilter())
    handler.setFormatter(JsonFormatter() if fmt == "json"
                         else logging.Formatter(TEXT_FORMAT, datefmt="%Y-%m-%d %H:%M:%S"))
    return handler


def make_handler(io_stream: Optional[IO[str]] = None, fmt: str = "text") -> logging.Handler:
    return _decorate(logging.StreamHandler(io_stream or sys.stderr), fmt)


def make_file_handler(path: Union[str, Path], fmt: str = "text") -> Optional[logging.Handler]:
    """Tệp log xoay vòng; không mở được (ổ đầy, không có quyền) thì bỏ qua — log console vẫn chạy."""
    try:
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        handler = _SafeRotatingFileHandler(path, maxBytes=LOG_FILE_MAX_BYTES, backupCount=LOG_FILE_BACKUPS,
                                      encoding="utf-8", delay=True)
    except OSError as exc:
        print(f"[log_setup] Không mở được tệp log {path}: {exc}", file=sys.stderr)
        return None
    return _decorate(handler, fmt)


class QuietPollFilter(logging.Filter):
    """Bỏ dòng httpx của vòng hỏi tin Telegram thành công (`getUpdates` … 200) — mỗi ~10 s một dòng, nhấn chìm log
    thật trong tab Nhật ký. Lỗi (409, 5xx, timeout) vẫn giữ."""

    def filter(self, record: logging.LogRecord) -> bool:
        try:
            msg = record.getMessage()
        except Exception:
            return True
        return not ("/getUpdates" in msg and " 200 " in msg)


def configure_logging(level: str = "INFO", fmt: str = "text", log_file: Optional[Union[str, Path]] = None) -> None:
    """Gọi một lần khi nạp cấu hình. Thay handler của root (không cộng dồn khi nạp lại)."""
    httpx_logger = logging.getLogger("httpx")
    if not any(isinstance(f, QuietPollFilter) for f in httpx_logger.filters):
        httpx_logger.addFilter(QuietPollFilter())
    root = logging.getLogger()
    handlers = [make_handler(fmt=fmt)]
    if log_file:
        fh = make_file_handler(log_file, fmt)
        if fh:
            handlers.append(fh)
    for h in handlers:
        h._vnmate = True  # type: ignore[attr-defined]
    # Chỉ thay handler do chính hàm này tạo — handler của nơi khác (log stream portal, pytest) giữ nguyên.
    old = [h for h in root.handlers if getattr(h, "_vnmate", False)]
    root.handlers[:] = [h for h in root.handlers if not getattr(h, "_vnmate", False)] + handlers
    for h in old:
        h.close()
    root.setLevel(getattr(logging, str(level).upper(), logging.INFO))


def default_log_file(project_root: Union[str, Path]) -> Optional[Path]:
    """`VNMATEAI_LOG_FILE` (đường dẫn, hoặc `none` / `off` / rỗng để tắt) — mặc định <dự án>/logs/vnmateai.log."""
    import os
    env = os.environ.get("VNMATEAI_LOG_FILE")
    if env is None:
        return Path(project_root) / "logs" / "vnmateai.log"
    env = env.strip()
    return None if env.lower() in ("", "none", "off", "0") else Path(env)
