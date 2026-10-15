# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 Dương Thanh Lịch — VN-MateAI. See LICENSE and NOTICE.
"""
mateai/interfaces/http/routers/reports.py
=========================================
Báo cáo hiệu năng theo tháng (thẩm định thương mại P1-15) — JSON cho màn hình, CSV cho báo cáo SLA — và tổng quan cho
giám đốc (#22). Admin / manager.
"""
from __future__ import annotations

from functools import partial
from typing import Any, Dict

from fastapi import APIRouter, Depends, Query
from fastapi.responses import Response

from core.plugin_manager import run_blocking
from mateai.interfaces.http.auth_dependencies import require_roles

router = APIRouter()


@router.get("/api/v1/reports/performance/monthly", summary="Hiệu năng AI theo tháng (lượt thoại, độ trễ, thành công, token)",
            tags=["Reports"])
async def performance_monthly(months: int = Query(default=13, ge=1, le=36), format: str = Query(default="json", pattern="^(json|csv)$"),
                              user: Dict[str, Any] = Depends(require_roles(["admin", "manager"]))):
    from mateai.application.monitoring import performance_report
    rows = await run_blocking(partial(performance_report.monthly, months))
    if format == "csv":
        return Response(performance_report.to_csv(rows), media_type="text/csv; charset=utf-8",
                        headers={"Content-Disposition": 'attachment; filename="hieu_nang_theo_thang.csv"'})
    return {"months": rows}


@router.get("/api/v1/reports/executive-summary", summary="Tổng quan cho giám đốc: tháng này so với tháng trước, việc chờ quyết định",
            tags=["Reports"])
async def executive_summary(user: Dict[str, Any] = Depends(require_roles(["admin", "manager"]))) -> Dict[str, Any]:
    from mateai.application.monitoring import performance_report
    return await run_blocking(performance_report.executive)
