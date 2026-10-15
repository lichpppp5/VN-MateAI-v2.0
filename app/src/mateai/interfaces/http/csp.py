# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 Dương Thanh Lịch — VN-MateAI. See LICENSE and NOTICE.
"""
Content-Security-Policy của portal (thẩm định thương mại §8).

Chặt (mặc định): `script-src 'self'` — trình duyệt không chạy <script> nội tuyến, `onclick="…"`, `javascript:` nên HTML
bị chèn vào trang (XSS) không chạy được mã. Các thuộc tính on* sẵn có của giao diện do web/inline-handlers.js chạy hộ
theo ngữ pháp nhỏ + danh sách cho phép; trang báo cho tệp đó bằng <meta name="vnmate-csp" content="strict">.

Trang Next.js xuất tĩnh (/topology, /computer-use) có script nội tuyến của Next → giữ CSP cũ cho riêng các trang đó.
Khẩn cấp: VNMATEAI_CSP_LEGACY=1 → mọi trang về CSP cũ ('unsafe-inline'), không chèn meta (trình duyệt tự chạy on*).
`style-src` vẫn cho 'unsafe-inline' (thuộc tính style= của Tailwind / JS); CSS không chạy được mã.
"""
from __future__ import annotations

import os

_TEMPLATE = (
    "default-src 'self'; {script}; style-src 'self' 'unsafe-inline'; "
    "img-src 'self' data: blob:; media-src 'self' data: blob:; font-src 'self' data:; "
    "connect-src 'self' wss: ws:; worker-src 'self' blob:; object-src 'none'; base-uri 'self'; "
    "form-action 'self'; frame-ancestors 'none'"
)
LEGACY_CSP = _TEMPLATE.format(script="script-src 'self' 'unsafe-inline'")
STRICT_CSP = _TEMPLATE.format(script="script-src 'self'")
STRICT_META = '<meta name="vnmate-csp" content="strict" />'


def strict_enabled() -> bool:
    return os.environ.get("VNMATEAI_CSP_LEGACY", "").strip().lower() not in ("1", "true", "yes", "on")


def default_csp() -> str:
    return STRICT_CSP if strict_enabled() else LEGACY_CSP


def mark_strict(html: str) -> str:
    """Chèn meta báo CSP chặt ngay sau <head> (trước mọi script) để inline-handlers.js biết phải chạy hộ on*."""
    if not strict_enabled() or STRICT_META in html:
        return html
    i = html.find("<head>")
    if i < 0:
        return html
    i += len("<head>")
    return html[:i] + "\n  " + STRICT_META + html[i:]
