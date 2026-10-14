# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 Dương Thanh Lịch — VN-MateAI. See LICENSE and NOTICE.
"""
mateai/interfaces/http/routers/analytics.py
============================================
Bảng ROI và danh sách nhân viên ERP.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Body, Depends, HTTPException, Query, Request, status
from fastapi.responses import FileResponse, JSONResponse, Response
from pydantic import BaseModel, Field

from core.plugin_manager import run_blocking
from mateai.config.loader import get_assistant_name, settings
from mateai.interfaces.http.auth_dependencies import get_current_user, require_roles
from mateai.interfaces.websocket.realtime_hub import broadcast_hud, broadcast_portal_ui

logger = logging.getLogger(__name__)

router = APIRouter()


@router.get(
    "/api/v1/roi-dashboard",
    summary="Phase 48: ROI & KPI Dashboard Data",
    tags=["Dashboard"],
)
async def api_roi_dashboard(
    report_date: Optional[str] = None,
    current_user: Dict[str, Any] = Depends(get_current_user),
) -> Dict[str, Any]:
    """
    Tổng hợp toàn bộ dữ liệu cho ROI Dashboard:
    - Phiếu ITSM theo trạng thái
    - Nhật ký kiểm toán thống kê
    - KPI AI (số task, giờ tiết kiệm)
    - Thống kê tổ chức (nhân viên, phòng ban)
    """
    from skills.itsm_skills import generate_daily_report
    from mateai.infrastructure.database.erp_database import erp_db

    # Báo cáo ngày
    report = await run_blocking(generate_daily_report,
        report_date=report_date,
        include_audit_details=True,
    )

    # Tickets đang mở (pending + in_progress)
    from skills.itsm_skills import get_tickets
    open_tickets = await run_blocking(get_tickets, status_filter="pending", limit=20)
    inprogress_tickets = await run_blocking(get_tickets, status_filter="in_progress", limit=20)
    ai_tickets = await run_blocking(get_tickets, ai_only=True, limit=10)

    # Audit stats tổng hợp
    audit_stats = erp_db.get_audit_stats()

    return {
        "status": "success",
        "report_date": report.get("report_date"),
        "report_text": report.get("report_text"),
        "kpi": report.get("data", {}).get("kpi", {}),
        "tickets": {
            "today": report.get("data", {}).get("tickets", {}),
            "open": open_tickets.get("tickets", []),
            "in_progress": inprogress_tickets.get("tickets", []),
            "ai_created": ai_tickets.get("tickets", []),
        },
        "audit": {
            "stats": audit_stats,
            "today": report.get("data", {}).get("audit", {}),
            "recent": report.get("data", {}).get("recent_audits", []),
        },
        "org": report.get("data", {}).get("org", {}),
    }


@router.get(
    "/api/v1/erp/employees",
    summary="Phase 48: Danh sách nhân viên ERP",
    tags=["ERP"],
)
async def api_erp_employees(
    dept_id: Optional[int] = None,
    role: Optional[str] = None,
    limit: int = 100,
    current_user: Dict[str, Any] = Depends(get_current_user),
) -> Dict[str, Any]:
    """Trả về danh sách nhân viên từ ERP, lọc theo phòng ban hoặc role."""
    from mateai.infrastructure.database.erp_database import erp_db
    try:
        employees = await run_blocking(erp_db.list_employees, dept_id=dept_id, role=role, limit=limit)
        return {"status": "success", "total": len(employees), "employees": employees}
    except Exception as e:
        return {"status": "error", "error": str(e)}
