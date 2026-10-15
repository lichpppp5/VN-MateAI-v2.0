# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 Dương Thanh Lịch — VN-MateAI. See LICENSE and NOTICE.
"""
mateai/interfaces/http/routers/health.py
=========================================
Sức khoẻ hệ thống: /health, bảng sức khoẻ tổng hợp, danh sách node âm thanh.
"""
from __future__ import annotations

from mateai import __version__

import asyncio
import json
import logging
import os
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

import psutil
from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from core.plugin_manager import run_blocking
from mateai.application.security.auth_manager import auth_manager
from mateai.config.loader import get_assistant_name, settings
from mateai.interfaces.http import log_stream
from mateai.interfaces.http.auth_dependencies import get_current_user, require_roles
from mateai.interfaces.websocket.client_orchestrator import orchestrator
from mateai.interfaces.websocket.realtime_hub import active_audio_nodes, active_hud_websockets, active_portal_websockets

logger = logging.getLogger(__name__)

router = APIRouter()


class HealthResponse(BaseModel):
    """Response for GET /health."""
    status: str
    version: str
    skill_count: int
    skill_names: list
    model: str
    asr_backend: str
    tts_voice: str
    routing_primary: Optional[str] = None
    routing_fallback_1: Optional[str] = None
    routing_fallback_2: Optional[str] = None


@router.get(
    "/api/v1/audio-nodes",
    summary="Danh sách mạch âm thanh ESP32 Xiaozhi đang kết nối",
    tags=["Audio Nodes"],
)
async def get_audio_nodes_endpoint(current_user: Dict[str, Any] = Depends(get_current_user)) -> Dict[str, Any]:
    """Trả về danh sách mạch thoại ESP32 Xiaozhi đang kết nối trực tuyến theo thời gian thực."""
    from mateai.interfaces.websocket.xiaozhi_gateway import pairing_registry
    nodes = []
    for dev_id, info in active_audio_nodes.items():
        nodes.append({
            "device_id": dev_id,
            "pairing_code": pairing_registry.get_code_for_device(dev_id),
            "client_host": info.get("client_host", "unknown"),
            "connected_at": info.get("connected_at"),
            "last_active": info.get("last_active"),
            "state": info.get("state", "idle"),
            "emotion": info.get("emotion", "sleeping"),
            "screen_text": info.get("screen_text"),
            "audio_format": info.get("audio_format", "mp3_24k"),
        })
    return {
        "status": "success",
        "count": len(nodes),
        "nodes": nodes,
    }


@router.get(
    "/health",
    response_model=HealthResponse,
    summary="Server Health & Inventory",
    tags=["System"],
)
async def health_check() -> HealthResponse:
    """Returns server status, loaded skills, model name, and audio config."""
    from core.plugin_manager import plugin_manager
    from mateai.config.loader import settings

    model_name = getattr(getattr(settings, "llm", None), "model_name", settings.MODEL_NAME)
    return HealthResponse(
        status="running",
        version=__version__,
        skill_count=plugin_manager.get_skill_count(),
        skill_names=plugin_manager.get_skill_names(),
        model=model_name,
        asr_backend=getattr(settings, "ASR_BACKEND", "google"),
        tts_voice="vi-VN-HoaiMyNeural",
        routing_primary=model_name,
        routing_fallback_1="",
        routing_fallback_2="",
    )


@router.get(
    "/api/v1/health-dashboard",
    summary="Phase 24.5: Zero-Overhead Observability Dashboard — System Health Snapshot",
    tags=["System"],
)
async def health_dashboard_endpoint(_user: Dict[str, Any] = Depends(get_current_user)) -> Dict[str, Any]:
    """
    Zero-Overhead Health Snapshot (O(1) in-memory lookup).
    Contains no computational logic or network requests.
    Directly returns SYSTEM_HEALTH_CACHE in < 1ms response time.

    Phase 61: bổ sung nhóm 'counters' (hàng đợi, phê duyệt, slot worker) và
    'connections' để dashboard lấy counter rẻ ngay trong payload 2 giây,
    thay vì gọi thêm nhiều endpoint nặng. Tất cả chỉ đọc trạng thái đã có
    sẵn trong bộ nhớ — không thêm worker, không thêm request mạng.
    """
    from mateai.application.operations.health_monitor import SYSTEM_HEALTH_CACHE
    from core.plugin_manager import plugin_manager

    # Inject live websocket & node counts in O(1)
    SYSTEM_HEALTH_CACHE["nodes"]["active_web_clients"] = len(active_portal_websockets)
    SYSTEM_HEALTH_CACHE["nodes"]["active_audio_hardware"] = len(active_audio_nodes)
    SYSTEM_HEALTH_CACHE["nodes"]["skills_count"] = plugin_manager.get_skill_count()
    SYSTEM_HEALTH_CACHE["nodes"]["skills_enabled"] = len(plugin_manager.get_all_tools())
    # Phase 76: KHÔNG còn nhét "security_role" = "ADMIN" vào cache dùng chung.
    # Endpoint này không có ngữ cảnh người gọi, nên mọi người — kể cả chưa đăng
    # nhập — đều nhận một vai trò bịa. Vai trò thật lấy từ JWT ở nơi có ngữ cảnh
    # (vd. gói hud_welcome của /ws/hud).

    # ── Phase 61: số kết nối WebSocket / LAN ────────────────────────────
    # active_hud_websockets trước đây không có chỗ nào lộ ra ngoài.
    try:
        SYSTEM_HEALTH_CACHE["nodes"]["active_hud_websockets"] = len(active_hud_websockets)
    except Exception:
        SYSTEM_HEALTH_CACHE["nodes"].setdefault("active_hud_websockets", 0)
    try:
        SYSTEM_HEALTH_CACHE["nodes"]["active_lan_clients"] = len(orchestrator.get_connected_clients())
    except Exception:
        SYSTEM_HEALTH_CACHE["nodes"].setdefault("active_lan_clients", 0)

    # ── Phase 61: counter hàng đợi & phê duyệt ──────────────────────────
    # Mỗi nhánh độc lập, lỗi ở nhánh này không được làm hỏng nhánh kia.
    counters: Dict[str, Any] = SYSTEM_HEALTH_CACHE.setdefault("counters", {})

    # Hàng đợi phê duyệt Zero-Trust (Phase 57)
    try:
        from mateai.application.security.zero_trust import hitl_manager as zt_hitl
        counters["zt_pending"] = len(zt_hitl.get_pending_list())
    except Exception:
        counters.setdefault("zt_pending", 0)

    # Worker nền: đang chạy / tổng / số slot tối đa
    try:
        from mateai.application.operations.background_workers import background_worker_manager as bg
        counters["bg_running"] = len(bg._running_tasks)
        counters["bg_total"] = len(bg._tasks)
        counters["bg_max_concurrent"] = bg.max_concurrent
    except Exception:
        counters.setdefault("bg_running", 0)
        counters.setdefault("bg_total", 0)
        counters.setdefault("bg_max_concurrent", 0)

    return SYSTEM_HEALTH_CACHE


@router.get("/api/v1/health/startup", summary="Kết quả từng bước khởi động dịch vụ nền")
async def startup_report(user: Dict[str, Any] = Depends(require_roles(["admin", "manager"]))) -> Dict[str, Any]:
    """ok / skipped / error + thời gian của từng bước (`interfaces/http/lifecycle.py`).

    Trước Phase 10 dịch vụ nền khởi động lỗi chỉ để lại một dòng log cảnh báo.
    """
    from mateai.interfaces.http import lifecycle
    st = lifecycle.STATE
    return {"complete": st.complete, "steps": st.report,
            "failed": [k for k, v in st.report.items() if v.get("status") == "error"]}
