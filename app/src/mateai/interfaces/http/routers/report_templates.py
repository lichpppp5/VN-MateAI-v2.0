# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 Dương Thanh Lịch — VN-MateAI. See LICENSE and NOTICE.
"""
mateai/interfaces/http/routers/report_templates.py
===================================================
Kho biểu mẫu báo cáo tiêu chuẩn (đọc / ghi qua config loader).
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
    "/api/v1/report-templates",
    summary="Phase 28: Lấy danh sách các biểu mẫu báo cáo tiêu chuẩn",
    tags=["Reporting & Templates"],
)
async def get_report_templates(
    current_user: Dict[str, Any] = Depends(get_current_user),
) -> Dict[str, Any]:
    """Retrieve all report templates from settings or config.json."""
    from mateai.config.loader import settings
    templates = getattr(settings, "report_templates", {}) or {}
    if not templates:
        from mateai.config.loader import _load_raw_config
        templates = _load_raw_config().get("report_templates", {})
    return {"status": "success", "templates": templates}


@router.put(
    "/api/v1/report-templates",
    summary="Phase 28: Cập nhật kho biểu mẫu báo cáo tiêu chuẩn",
    tags=["Reporting & Templates"],
)
async def update_report_templates(
    payload: Dict[str, Any],
    current_user: Dict[str, Any] = Depends(get_current_user),
) -> Dict[str, Any]:
    """Save report templates to config.json and reload in-memory settings."""
    if current_user.get("role") not in ("admin", "manager"):
        raise HTTPException(status_code=403, detail="Chỉ Admin hoặc Manager mới có quyền cập nhật biểu mẫu báo cáo.")

    templates = payload.get("templates") if "templates" in payload else payload
    if not isinstance(templates, dict):
        raise HTTPException(status_code=400, detail="Dữ liệu biểu mẫu không hợp lệ, phải là một JSON object.")

    from mateai.application.administration import config_governance as gov

    try:
        gov.save_config(str(current_user.get("username", "?")),
                        lambda c: c.__setitem__("report_templates", templates), "Biểu mẫu báo cáo")
        return {
            "status": "success",
            "message": "Đã lưu kho biểu mẫu báo cáo tiêu chuẩn thành công. System Prompt đã được cập nhật.",
            "templates": templates,
        }
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Lỗi lưu biểu mẫu: {exc}")
