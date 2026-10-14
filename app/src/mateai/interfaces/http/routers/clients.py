# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 Dương Thanh Lịch — VN-MateAI. See LICENSE and NOTICE.
"""
mateai/interfaces/http/routers/clients.py
=========================================
Máy trạm (client agent): danh sách, chạy skill, triển khai skill, giám sát,
dừng tiến trình, hiển thị thị giác HUD (một máy hoặc phát rộng).
"""
from __future__ import annotations

import hashlib
import logging
from datetime import datetime
from typing import Any, Dict, List, Optional, Union

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from pydantic import BaseModel, Field

from core.plugin_manager import run_blocking
from mateai.application.agent.tool_gate import run_tool_with_policy
from mateai.interfaces.http.auth_dependencies import get_current_user, require_roles

logger = logging.getLogger(__name__)

router = APIRouter()


class ClientExecuteRequest(BaseModel):
    """Payload for POST /api/v1/clients/{client_id}/execute."""
    skill_name: str = Field(..., min_length=1)
    args: Optional[Dict[str, Any]] = Field(default_factory=dict)
    timeout: float = Field(default=35.0, ge=1.0, le=120.0)


class ClientDeploySkillRequest(BaseModel):
    """Payload for POST /api/v1/clients/{client_id}/deploy-skill."""
    filename: str = Field(..., min_length=1)
    code: str = Field(..., min_length=5)
    timeout: float = Field(default=20.0, ge=1.0, le=60.0)


class ClientKillProcessRequest(BaseModel):
    """Payload for POST /api/v1/clients/{client_id}/kill-process."""
    pid: int = Field(..., description="PID của tiến trình cần tắt")
    timeout: float = Field(default=10.0, ge=1.0, le=30.0)


class ClientVisualRequest(BaseModel):
    """Payload for POST /api/v1/clients/{client_id}/visual and /api/v1/visual/broadcast."""
    type: str = Field(default="metric_chart", description="Visual HUD type: text_board | network_map | metric_chart | security_alert | screenshot")
    title: str = Field(default="VN-MateAI VISUAL HUD", description="Tiêu đề cửa sổ overlay")
    data: Optional[Union[Dict[str, Any], str]] = Field(default_factory=dict, description="Dữ liệu hiển thị template")
    duration: int = Field(default=15, ge=1, le=120, description="Thời gian tự động mờ dần (giây)")


@router.get(
    "/api/v1/clients",
    summary="List all connected worker clients",
    tags=["Orchestrator"],
)
async def list_connected_clients() -> List[Dict[str, Any]]:
    """Return all currently connected LAN worker nodes."""
    from mateai.interfaces.websocket.client_orchestrator import orchestrator
    return orchestrator.get_connected_clients()


@router.post(
    "/api/v1/clients/{client_id}/execute",
    summary="Execute a skill remotely on a specific client agent",
    tags=["Orchestrator"],
)
async def execute_skill_on_client(
    client_id: str,
    payload: ClientExecuteRequest,
    user: dict = Depends(require_roles(["admin"])),
) -> Dict[str, Any]:
    """
    Chạy một skill trên máy trạm qua cổng tool chung (Zero-Trust, HITL, RBAC,
    audit) — giống hệt khi agent gọi cùng tool. Tác vụ NEED_CONFIRM vào hàng
    đợi duyệt; `args.confirmed` KHÔNG tự xác nhận được (trước đây được).
    """
    from mateai.interfaces.websocket.client_orchestrator import orchestrator

    if not orchestrator.is_client_online(client_id):
        raise HTTPException(
            status_code=404,
            detail=f"Máy trạm '{client_id}' hiện không trực tuyến hoặc chưa kết nối vào mạng LAN.",
        )

    gate = await run_tool_with_policy(
        payload.skill_name,
        {**(payload.args or {}), "target_client": client_id},
        caller=str(user.get("username") or "admin"),
        source_device="http:clients",
        query=f"Kỹ năng '{payload.skill_name}' trên máy trạm [{client_id}]",
        client_timeout=payload.timeout,
    )
    return gate["result"]


@router.post(
    "/api/v1/clients/{client_id}/deploy-skill",
    summary="Deploy and hot-load a Python skill on a remote client",
    tags=["Orchestrator"],
)
async def deploy_skill_to_client(
    client_id: str,
    payload: ClientDeploySkillRequest,
    user: dict = Depends(require_roles(["admin"])),
) -> Dict[str, Any]:
    """Push new Python skill code to a remote worker node for immediate loading."""
    from mateai.interfaces.websocket.client_orchestrator import orchestrator
    if not orchestrator.is_client_online(client_id):
        raise HTTPException(
            status_code=404,
            detail=f"Máy trạm '{client_id}' hiện không trực tuyến.",
        )

    res = await orchestrator.deploy_skill_to_client(
        client_id=client_id,
        filename=payload.filename,
        code=payload.code,
        timeout=payload.timeout,
    )
    # Đẩy mã chạy được xuống máy trạm — phải để lại dấu vết (ai, máy nào, mã gì).
    from mateai.application.security.safety_guard import security_engine
    security_engine.log_audit(
        client_id, "deploy_skill", "NEED_CONFIRM",
        "SUCCESS" if res.get("status") == "success" else "FAILED",
        {"filename": payload.filename, "code_sha256": hashlib.sha256(payload.code.encode("utf-8")).hexdigest(),
         "code_bytes": len(payload.code.encode("utf-8")), "by": user.get("username")},
    )
    return res


@router.get(
    "/api/v1/clients/{client_id}/monitor/{monitor_type}",
    summary="Get real-time endpoint telemetry & monitoring data from client",
    tags=["Orchestrator"],
)
async def get_client_monitoring_data(
    client_id: str,
    monitor_type: str,
    user: dict = Depends(require_roles(["manager", "admin"])),
    quality: int = Query(default=65, ge=10, le=100),
    max_width: int = Query(default=1280, ge=320, le=3840),
    limit: int = Query(default=15, ge=1, le=100),
    sort_by: str = Query(default="cpu"),
) -> Dict[str, Any]:
    """
    Proxy live telemetry request to client agent via WebSocket.
    monitor_type: 'screen' | 'processes' | 'network' | 'peripherals' | 'security'
    """
    from mateai.interfaces.websocket.client_orchestrator import orchestrator
    if not orchestrator.is_client_online(client_id):
        raise HTTPException(
            status_code=404,
            detail=f"Máy trạm '{client_id}' hiện không trực tuyến hoặc chưa kết nối vào mạng LAN.",
        )

    args: Dict[str, Any] = {}
    if monitor_type == "screen":
        args = {"quality": quality, "max_width": max_width}
        # Chụp màn hình người dùng máy trạm là dữ liệu riêng tư — phải để lại dấu
        # vết ai xem màn hình máy nào, lúc nào (audit_logs bất biến).
        from mateai.application.security.safety_guard import security_engine
        security_engine.log_audit(client_id, "capture_screen", "PRIVACY", "REQUESTED",
                                  {"by": user.get("username"), "max_width": max_width})
    elif monitor_type == "processes":
        args = {"limit": limit, "sort_by": sort_by}
    elif monitor_type == "network":
        args = {"limit": limit}

    res = await orchestrator.monitor_client(
        client_id=client_id,
        monitor_type=monitor_type,
        args=args,
        timeout=18.0,
    )
    return res


@router.post(
    "/api/v1/clients/{client_id}/kill-process",
    summary="Kill a process running on client machine by PID",
    tags=["Orchestrator"],
)
async def kill_client_process_endpoint(
    client_id: str,
    payload: ClientKillProcessRequest,
    user: dict = Depends(require_roles(["admin"])),
) -> Dict[str, Any]:
    """
    Tắt tiến trình trên máy trạm qua CỔNG TOOL CHUNG (HITL + audit), giống hệt
    `/execute` với skill `kill_process`. Trước đây endpoint này gửi thẳng xuống
    máy trạm: `kill_process` nằm trong danh sách phải phê duyệt
    (security.require_confirmation_actions) nhưng đi đường này thì không cần.
    """
    from mateai.interfaces.websocket.client_orchestrator import orchestrator
    if not orchestrator.is_client_online(client_id):
        raise HTTPException(
            status_code=404,
            detail=f"Máy trạm '{client_id}' hiện không trực tuyến.",
        )
    gate = await run_tool_with_policy(
        "kill_process",
        {"pid": payload.pid, "target_client": client_id},
        caller=str(user.get("username") or "admin"),
        source_device="http:clients",
        query=f"Tắt tiến trình PID {payload.pid} trên máy trạm [{client_id}]",
        client_timeout=payload.timeout,
    )
    return gate["result"]


@router.post(
    "/api/v1/clients/{client_id}/visual",
    summary="Phase 32: Hiển thị giao diện thị giác HUD trên máy trạm cụ thể",
    tags=["Orchestrator"],
)
async def send_visual_to_client_endpoint(
    client_id: str,
    payload: ClientVisualRequest,
    user: dict = Depends(require_roles(["manager", "admin"])),
) -> Dict[str, Any]:
    """Phát lệnh hiển thị giao diện thị giác HUD xuống Client Agent."""
    from mateai.interfaces.websocket.client_orchestrator import orchestrator
    if not orchestrator.is_client_online(client_id):
        raise HTTPException(
            status_code=404,
            detail=f"Máy trạm '{client_id}' hiện không trực tuyến.",
        )
    return await orchestrator.send_visual_to_client(
        client_id=client_id,
        visual_type=payload.type,
        data=payload.data or {},
        title=payload.title,
        duration=payload.duration,
    )


@router.post(
    "/api/v1/visual/broadcast",
    summary="Phase 32: Bắn giao diện thị giác HUD tới máy trạm và máy chủ",
    tags=["Orchestrator"],
)
async def broadcast_visual_endpoint(
    payload: ClientVisualRequest,
    user: dict = Depends(require_roles(["viewer", "manager", "admin"])),
) -> Dict[str, Any]:
    """Kích hoạt hiển thị giao diện HUD thị giác (network_map, metric_chart, image, alert)."""
    from skills.visual_skills import display_visual_data
    # Skill đồng bộ, bên trong chờ coroutine trên loop server (gửi tới máy trạm,
    # portal). Gọi thẳng trên loop thì nó chờ chính loop đang bị nó chặn: tự
    # khoá tới hết timeout (12 s/máy trạm) và visual không bao giờ tới nơi.
    return await run_blocking(
        display_visual_data,
        type=payload.type,
        context_data=payload.data or {},
        title=payload.title,
        duration=payload.duration,
    )
