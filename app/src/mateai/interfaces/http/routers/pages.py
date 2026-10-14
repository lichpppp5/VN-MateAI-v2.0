# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 Dương Thanh Lịch — VN-MateAI. See LICENSE and NOTICE.
"""
mateai/interfaces/http/routers/pages.py
=======================================
Trang giao diện: portal (`/`), trang admin build từ Next.js (`/admin*`,
`/topology`, `/computer-use`), HUD (`/hud`) và bảng ROI (`/roi`).
Thư mục tĩnh `_WEB_DIR` / `_ADMIN_OUT_DIR` dùng chung với các `app.mount`
trong server.
"""
from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Any, Dict, Optional

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse, Response

from mateai.config.loader import settings

logger = logging.getLogger(__name__)

router = APIRouter()

_WEB_DIR = Path(settings.PROJECT_ROOT) / "web"


def _asset_version(name: str) -> str:
    """
    Phiên bản của một file tĩnh, lấy từ thời điểm sửa gần nhất.

    Thẻ `<script src="...?v=X">` trong HTML từng ghi số cứng kiểu `?v=51.1`.
    Số đó chỉ đổi khi ai đó nhớ tăng lên, nên sau mỗi lần sửa JS mà quên
    tăng, người dùng vẫn chạy bản cũ. Nay lấy từ mtime: sửa file là URL đổi,
    không cần nhớ.

    Dùng `mtime_ns` vì hai lần sửa trong cùng một giây vẫn phải cho URL khác nhau.
    """
    f = _WEB_DIR / name
    try:
        return str(f.stat().st_mtime_ns)
    except OSError:
        return "0"


def _inject_asset_versions(html: str) -> str:
    """Thay số phiên bản cứng trong thẻ script bằng mtime của file tương ứng."""
    import re as _re

    def _sub(m: "re.Match[str]") -> str:
        # group(1) là phần sau `src="`. Phải trả lại CẢ `src="` và dấu `"` —
        # chỉ trả về URL thì thẻ script hỏng mà trình duyệt chỉ báo lỗi im lặng.
        url = m.group(1)
        fname = url.rsplit("/", 1)[-1].split("?", 1)[0].split("&", 1)[0]
        return f'src="{url.rsplit("?", 1)[0]}?v={_asset_version(fname)}"'

    return _re.sub(r'src="(/static/[^"]+\?v=)[^"]*"', _sub, html)


@router.get(
    "/",
    include_in_schema=False,
    summary="Web Portal",
)
async def serve_portal() -> HTMLResponse:
    """
    Serve the VN-MateAI Web Control Portal.
    Navigate to http://localhost:5843/ in a browser.
    """
    index = _WEB_DIR / "index.html"
    if not index.exists():
        raise HTTPException(
            status_code=404,
            detail="web/index.html not found. Make sure the web/ directory exists.",
        )
    return HTMLResponse(
        # Đọc và thay phiên bản script thay vì FileResponse: FileResponse phục
        # vụ tệp nguyên trạng, không cho chèn. Sửa app.js là URL trong HTML đổi
        # theo, nên không còn tình trạng HTML mới chạy JS cũ.
        _inject_asset_versions(index.read_text(encoding="utf-8")),
        headers={
            "Cache-Control": "no-cache, no-store, must-revalidate",
            "Pragma": "no-cache",
            "Expires": "0",
        },
    )


_ADMIN_OUT_DIR = Path(settings.PROJECT_ROOT) / "admin" / "out"


@router.get("/admin/topology", response_class=HTMLResponse, include_in_schema=True,
         summary="VN-MateAI Visual Workflow Topology Viewer (Phase 88)")
@router.get("/topology", response_class=HTMLResponse, include_in_schema=True)
async def serve_admin_topology():
    """Phục vụ giao diện Visual Workflow Topology (React Flow Node-based) xuất bản từ Next.js."""
    candidates = [
        _ADMIN_OUT_DIR / "topology.html",
        _ADMIN_OUT_DIR / "admin" / "topology.html",
        _ADMIN_OUT_DIR / "index.html",
    ]
    for c in candidates:
        if c.exists():
            return HTMLResponse(
                c.read_text(encoding="utf-8"),
                headers={
                    "Cache-Control": "no-cache, no-store, must-revalidate",
                    "Pragma": "no-cache",
                    "Expires": "0",
                },
            )
    raise HTTPException(status_code=404, detail="Topology build artifact not found. Please run 'npm run build' in admin/.")


@router.get("/admin/computer-use", response_class=HTMLResponse, include_in_schema=True,
         summary="VN-MateAI Computer-Use & Worker Console (Phase 90)")
@router.get("/computer-use", response_class=HTMLResponse, include_in_schema=True)
async def serve_admin_computer_use():
    """Phục vụ giao diện Computer-Use & Self-Healing Worker Console xuất bản từ Next.js."""
    candidates = [
        _ADMIN_OUT_DIR / "admin" / "computer-use.html",
        _ADMIN_OUT_DIR / "computer-use.html",
    ]
    for c in candidates:
        if c.exists():
            return HTMLResponse(
                c.read_text(encoding="utf-8"),
                headers={
                    "Cache-Control": "no-cache, no-store, must-revalidate",
                    "Pragma": "no-cache",
                    "Expires": "0",
                },
            )
    raise HTTPException(status_code=404, detail="Computer-Use build artifact not found. Please run 'npm run build' in admin/.")


@router.get("/admin", include_in_schema=True)
async def serve_admin_root():
    """Chuyển hướng trang /admin sang tab Topology."""
    return RedirectResponse(url="/admin/topology")


@router.get("/hud", include_in_schema=True, response_class=HTMLResponse,
         summary="HUD \"buồng lái AI\" — lõi nơ-ron, ra lệnh bằng giọng nói / gõ, phê duyệt, số liệu doanh nghiệp")
async def get_vnmate_hud():
    """HUD toàn màn hình: web/hud-visual.js (hình) + web/hud.js (WebSocket, micro, phát tiếng, phê duyệt, thanh lệnh)."""
    hud_file = _WEB_DIR / "hud.html"
    if not hud_file.exists():        raise HTTPException(status_code=404, detail="web/hud.html not found.")
    return HTMLResponse(
        # Xem giải thích ở serve_portal.
        _inject_asset_versions(hud_file.read_text(encoding="utf-8")),
        headers={
            "Cache-Control": "no-cache, no-store, must-revalidate",
            "Pragma": "no-cache",
            "Expires": "0",
        },
    )


@router.get("/hud-next", include_in_schema=False)
async def get_vnmate_hud_next():
    """Bản thử HUD "buồng lái AI" đã thay HUD cũ (2026-10-12) — giữ đường cũ cho dấu trang, chuyển về /hud."""
    return RedirectResponse(url="/hud")


@router.get(
    "/roi",
    include_in_schema=True,
    response_class=HTMLResponse,
    summary="ROI & Value Realization Dashboard (Phase 48)",
)
@router.get(
    "/roi-dashboard",
    include_in_schema=True,
    response_class=HTMLResponse,
    summary="ROI & Value Realization Dashboard (Phase 48)",
)
async def get_vnmate_roi():
    """Phục vụ giao diện ROI & Value Realization Dashboard (Phase 48)."""
    roi_file = _WEB_DIR / "roi_dashboard.html"
    if not roi_file.exists():
        raise HTTPException(status_code=404, detail="web/roi_dashboard.html not found.")
    return FileResponse(
        str(roi_file),
        media_type="text/html",
        headers={
            "Cache-Control": "no-cache, no-store, must-revalidate",
            "Pragma": "no-cache",
            "Expires": "0",
        },
    )
