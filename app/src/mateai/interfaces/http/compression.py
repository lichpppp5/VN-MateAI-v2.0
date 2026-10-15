# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 Dương Thanh Lịch — VN-MateAI. See LICENSE and NOTICE.
"""
Nén gzip CHỈ cho trang giao diện và tệp tĩnh dạng chữ (JS / CSS / HTML / JSON / SVG).

app.js ~770 KB + index.html ~500 KB từng gửi nguyên mỗi lần mở portal (~1,8 MB); nén còn khoảng 1/5. Không bọc toàn app:
API trả luồng (SSE), WebSocket, tải tệp / âm thanh có Range không được đụng — nên lọc theo đường dẫn + đuôi tệp và bỏ qua
mọi yêu cầu có Range.
"""
from __future__ import annotations

from starlette.middleware.gzip import GZipMiddleware
from starlette.types import ASGIApp, Receive, Scope, Send

PAGE_PATHS = {"/", "/hud", "/roi", "/roi-dashboard"}
TEXT_SUFFIXES = (".js", ".css", ".html", ".json", ".svg", ".txt", ".map", ".mjs")


def eligible(scope: Scope) -> bool:
    if scope.get("type") != "http" or scope.get("method") not in ("GET", "HEAD"):
        return False
    if any(k == b"range" for k, _ in scope.get("headers") or ()):
        return False
    path = scope.get("path", "")
    if path in PAGE_PATHS:
        return True
    return path.startswith("/static/") and path.lower().endswith(TEXT_SUFFIXES)


class StaticGZipMiddleware:
    def __init__(self, app: ASGIApp, minimum_size: int = 1024) -> None:
        self.app = app
        self.gzip = GZipMiddleware(app, minimum_size=minimum_size)

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if eligible(scope):
            await self.gzip(scope, receive, send)
        else:
            await self.app(scope, receive, send)
