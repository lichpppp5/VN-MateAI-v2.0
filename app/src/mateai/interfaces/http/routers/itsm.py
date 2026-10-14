# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 Dương Thanh Lịch — VN-MateAI. See LICENSE and NOTICE.
"""
mateai/interfaces/http/routers/itsm.py
=======================================
Phiếu yêu cầu IT (ITSM): tạo, liệt kê, cập nhật trạng thái.
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel, Field

from core.plugin_manager import run_blocking
from mateai.interfaces.http.auth_dependencies import get_current_user, require_roles

logger = logging.getLogger(__name__)

router = APIRouter()


class CreateTicketRequest(BaseModel):
    """Payload cho POST /api/v1/itsm/tickets."""
    title: str
    category: str = "other"
    severity: str = "medium"
    description: Optional[str] = None
    assignee_name: Optional[str] = None
    dept_name: Optional[str] = None
    created_by_ai: bool = False
    resolution_notes: Optional[str] = None


class UpdateTicketRequest(BaseModel):
    """Payload cho PUT /api/v1/itsm/tickets/{ticket_id}."""
    status: str
    resolution_notes: Optional[str] = None


@router.post(
    "/api/v1/itsm/tickets",
    summary="Phase 48: Tạo phiếu ITSM mới",
    tags=["ITSM"],
)
async def api_create_ticket(
    payload: CreateTicketRequest,
    current_user: Dict[str, Any] = Depends(get_current_user),
) -> Dict[str, Any]:
    """Tạo phiếu công việc ITSM và ghi audit trail bất biến."""
    from skills.itsm_skills import create_system_ticket
    return await run_blocking(create_system_ticket,
        title=payload.title,
        category=payload.category,
        severity=payload.severity,
        description=payload.description,
        assignee_name=payload.assignee_name,
        dept_name=payload.dept_name,
        created_by_ai=payload.created_by_ai,
        resolution_notes=payload.resolution_notes,
        caller_id=current_user.get("username"),
    )


@router.get(
    "/api/v1/itsm/tickets",
    summary="Phase 48: Danh sách phiếu ITSM",
    tags=["ITSM"],
)
async def api_get_tickets(
    status_filter: str = "all",
    ai_only: bool = False,
    limit: int = 50,
    current_user: Dict[str, Any] = Depends(get_current_user),
) -> Dict[str, Any]:
    """Lấy danh sách phiếu ITSM với bộ lọc."""
    from skills.itsm_skills import get_tickets
    return await run_blocking(get_tickets, status_filter=status_filter, ai_only=ai_only, limit=limit)


@router.put(
    "/api/v1/itsm/tickets/{ticket_id}",
    summary="Phase 48: Cập nhật trạng thái phiếu ITSM",
    tags=["ITSM"],
)
async def api_update_ticket(
    ticket_id: str,
    payload: UpdateTicketRequest,
    current_user: Dict[str, Any] = Depends(get_current_user),
) -> Dict[str, Any]:
    """Cập nhật trạng thái và ghi chú giải quyết cho phiếu ITSM."""
    from skills.itsm_skills import update_ticket_status
    return await run_blocking(update_ticket_status,
        ticket_id=ticket_id,
        status=payload.status,
        resolution_notes=payload.resolution_notes,
        caller_id=current_user.get("username"),
    )
