# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 Dương Thanh Lịch — VN-MateAI. See LICENSE and NOTICE.
"""
mateai/interfaces/http/routes.py
================================
Gắn file tĩnh + mọi router vào app. Chuyển từ `server.py` (Supervisor Phase 10,
§198). THỨ TỰ quan trọng (route khớp theo thứ tự đăng ký) và được cố định bằng
`tests/test_server_lifecycle_contract.py::test_route_table_unchanged_by_extraction`.
Route webhook đăng ký lúc khởi động (`lifecycle`, bước `webhook_routes`).
"""
from __future__ import annotations

import importlib
import logging

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

logger = logging.getLogger(__name__)


class NoStaleStatic(StaticFiles):
    """
    Phục vụ file tĩnh nhưng LUÔN buộc trình duyệt kiểm tra lại (Phase 69).

    `StaticFiles` mặc định không gửi `Cache-Control`, trình duyệt tự cache theo
    heuristics: HTML (no-cache) luôn mới nhưng JS/CSS nó trỏ tới vẫn là bản CŨ —
    giao diện mới, hành vi cũ, không cách nào đoán ra. `no-cache` không có nghĩa là
    không cache: trình duyệt gửi `If-None-Match`, server trả 304 nếu không đổi.
    """

    def file_response(self, *args, **kwargs):  # type: ignore[override]
        resp = super().file_response(*args, **kwargs)
        resp.headers["Cache-Control"] = "no-cache, must-revalidate"
        return resp


#: Router đăng ký SAU trang portal, theo đúng thứ tự cũ trong server.py.
_ROUTERS_AFTER_PAGES = (
    "config", "auth", "users", "health", "system", "voice", "memory", "tts", "logs",
    "skills", "pairing", "websockets", "workers", "clients", "agent_devices", "robots",
    "hud", "xiaozhi", "sentinel", "security", "files", "tasks", "wake_word", "domain",
    "telegram", "report_templates", "itsm", "analytics", "enterprise", "computer_use", "dev_fleet", "monitoring", "sso", "playbooks", "llmops", "agent_gateway", "agent_directory",
)


def _router(name: str):
    return importlib.import_module(f"mateai.interfaces.http.routers.{name}").router


def register_routes(app: FastAPI) -> None:
    from mateai.interfaces.http.api_admin import router as admin_router
    from mateai.interfaces.http.api_erp import router as erp_router
    from mateai.interfaces.http.routers.pages import _ADMIN_OUT_DIR, _WEB_DIR

    if _WEB_DIR.exists():
        app.mount("/static", NoStaleStatic(directory=str(_WEB_DIR)), name="static")
        logger.info("Static files mounted from: %s (no-cache: luôn kiểm tra bản mới)", _WEB_DIR)
    else:
        logger.warning("web/ directory not found at %s — portal will be unavailable.", _WEB_DIR)

    app.include_router(erp_router)          # Phase 47: tổ chức ERP & nhập hàng loạt
    app.include_router(admin_router)        # quản trị doanh nghiệp & standby grid
    app.include_router(_router("probes"))   # /livez /startupz /readyz
    app.include_router(_router("pages"))    # portal ở "/"

    # Phase 81 đã gỡ app Admin Next.js (trùng chức năng portal); chỉ còn phục vụ
    # tài nguyên build cũ nếu thư mục còn trên đĩa.
    if (_ADMIN_OUT_DIR / "_next").exists():
        app.mount("/admin/_next", NoStaleStatic(directory=str(_ADMIN_OUT_DIR / "_next")), name="admin_next")
        app.mount("/_next", NoStaleStatic(directory=str(_ADMIN_OUT_DIR / "_next")), name="admin_next_root")

    for name in _ROUTERS_AFTER_PAGES:
        app.include_router(_router(name))
