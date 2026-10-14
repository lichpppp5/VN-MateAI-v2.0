# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 Dương Thanh Lịch — VN-MateAI. See LICENSE and NOTICE.
"""
mateai/interfaces/http/routers/monitoring.py
============================================
API Giám sát hạ tầng (Prometheus / Grafana) — docs/integrations/monitoring.md. Chỉ ĐỌC dữ liệu từ nguồn; không ghi vào Prometheus / Grafana.
Xem: manager + admin. Làm mới ngay + chạy PromQL: manager + admin (chỉ đọc). Router chỉ xác thực / dịch lỗi; logic ở application/monitoring.
"""
from __future__ import annotations

import logging
from typing import Any, Dict

import hmac

from fastapi import APIRouter, Body, Depends, Header, HTTPException, Query
from fastapi.responses import PlainTextResponse

from mateai.application.monitoring import infra_monitor as im
from mateai.interfaces.http.auth_dependencies import require_roles

logger = logging.getLogger(__name__)
router = APIRouter()
_USER = Depends(require_roles(["manager", "admin"]))
_ADMIN = Depends(require_roles(["admin"]))
_TAG = ["Monitoring"]


@router.get("/api/v1/monitoring/overview", summary="Tổng quan giám sát: cảnh báo, target, CPU/RAM/đĩa, dashboard", tags=_TAG)
async def overview(force: bool = Query(False), user: Dict[str, Any] = _USER) -> Dict[str, Any]:
    return await im.snapshot(force=force)


@router.post("/api/v1/monitoring/query", summary="Chạy một truy vấn PromQL (chỉ đọc, có giới hạn)", tags=_TAG)
async def promql(body: Dict[str, Any] = Body(...), user: Dict[str, Any] = _USER) -> Dict[str, Any]:
    try:
        return await im.query_promql(str(body.get("promql") or ""), source_id=(body.get("source_id") or None),
                                     limit=int(body.get("limit") or 30))
    except im.MonitorError as exc:
        raise HTTPException(status_code=422, detail=str(exc))


@router.get("/metrics", summary="Số đo của chính VN-MateAI cho Prometheus (Bearer = monitoring.metrics_token)", tags=_TAG,
            response_class=PlainTextResponse)
async def metrics(authorization: str = Header("")) -> PlainTextResponse:
    """Ngoài /api/v1/ (Prometheus không có JWT người dùng) nên tự xác thực bằng token riêng. Chưa đặt token -> 404 (tắt)."""
    from mateai.application.operations import metrics_export
    from mateai.config.loader import settings
    from core.plugin_manager import run_blocking
    want = (settings.monitoring.metrics_token or "").strip()
    if not want:
        raise HTTPException(status_code=404, detail="Not Found")
    got = authorization[7:].strip() if authorization.startswith("Bearer ") else ""
    if not got or not hmac.compare_digest(got.encode("utf-8"), want.encode("utf-8")):
        raise HTTPException(status_code=401, detail="Sai hoặc thiếu token", headers={"WWW-Authenticate": "Bearer"})
    text = await run_blocking(metrics_export.render)
    return PlainTextResponse(text, media_type="text/plain; version=0.0.4; charset=utf-8")


@router.post("/api/v1/monitoring/refresh", summary="Thu thập ngay + đồng bộ sự cố (không chờ chu kỳ)", tags=_TAG)
async def refresh(user: Dict[str, Any] = _ADMIN) -> Dict[str, Any]:
    return await im.poll_once()
