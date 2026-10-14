# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 Dương Thanh Lịch — VN-MateAI. See LICENSE and NOTICE.
"""
mateai/interfaces/http/routers/logs.py
======================================
Nhật ký thời gian thực: đọc các dòng gần nhất (đã che bí mật) và xoá bộ đệm.
"""
from __future__ import annotations

import logging
from typing import Any, Dict

from fastapi import APIRouter, Depends, Query

from mateai.interfaces.http import log_stream
from mateai.interfaces.http.auth_dependencies import get_current_user, require_roles

logger = logging.getLogger(__name__)

router = APIRouter()


@router.get(
    "/api/v1/logs/recent",
    summary="Lấy danh sách nhật ký hệ thống gần đây từ ring buffer",
    tags=["System Logs"],
)
async def get_recent_logs(
    limit: int = Query(default=200, ge=1, le=600),
    user: dict = Depends(require_roles(["viewer", "manager", "admin"])),
) -> Dict[str, Any]:
    """Trả về danh sách log mới nhất đang được lưu trong RAM."""
    logs = (h.get_recent_logs(limit=limit) if (h := log_stream.get_handler()) else [])
    return {
        "status": "success",
        "count": len(logs),
        "logs": logs,
    }


@router.delete(
    "/api/v1/logs",
    summary="Xóa bộ đệm nhật ký hệ thống trong RAM",
    tags=["System Logs"],
)
async def clear_logs_buffer(
    user: dict = Depends(require_roles(["manager", "admin"])),
) -> Dict[str, Any]:
    """Xóa toàn bộ các dòng log trong bộ nhớ đệm RAM."""
    h = log_stream.get_handler()
    if h:
        h.clear_buffer()
    return {"status": "success", "message": "Đã xóa sạch bộ đệm nhật ký máy chủ."}
