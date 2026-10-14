# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 Dương Thanh Lịch — VN-MateAI. See LICENSE and NOTICE.
"""
core/api_admin.py
=================
API Quản Trị Trung Tâm Điều Hành Doanh Nghiệp (Enterprise Admin Control Center).
Cung cấp các endpoint:
  (Sơ đồ hệ thống: /api/v1/system/topology — trạng thái thật, interfaces/http/topology.py.)
  2. GET  /api/v1/admin/departments/overview: Danh sách phòng ban, nguồn dữ liệu và cảnh báo.
  3. POST /api/v1/admin/departments/save: Lưu cấu hình phòng ban & nguồn dữ liệu động (No-Code Form).
  4. POST /api/v1/worknodes/heartbeat: Nhận ping từ các máy trạm Mac Mini OpenClaw và phát sóng live event.
  5. GET  /api/v1/worknodes/status: Thống kê trạng thái cụm Elastic Grid.
  6. POST /api/v1/admin/cross-report: Tạo báo cáo điều hành liên phòng ban tức thì.
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any, Dict, List, Optional
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from mateai.infrastructure.database.erp_database import erp_db
from mateai.application.enterprise.department_engine import department_engine
from mateai.application.devices.elastic_grid_manager import elastic_grid_manager
from mateai.application.agent.agent_orchestrator import multi_agent_system
from mateai.interfaces.http.auth_dependencies import require_roles

# Vai trò: middleware chỉ kiểm ĐĂNG NHẬP — trước đây viewer cũng gọi được mọi API
# quản trị ở đây, kể cả xoá toàn bộ bộ đệm RAM.
_READ = Depends(require_roles(["manager", "admin"]))
_ADMIN = Depends(require_roles(["admin"]))

logger = logging.getLogger("mateai.interfaces.http.api_admin")

router = APIRouter(prefix="/api/v1", tags=["Enterprise Admin & Elastic Grid"])


# ─── Pydantic Models ────────────────────────────────────────────────────────

class DepartmentSaveRequest(BaseModel):
    dept_code: str = Field(..., min_length=1, description="Mã phòng ban (vd: FIN, HR, IT, OPS)")
    dept_name: str = Field(..., min_length=1, description="Tên phòng ban")
    data_clearance_level: int = Field(default=1, ge=1, le=4, description="Cấp độ bảo mật (1-4)")
    config_metadata: Optional[Dict[str, Any]] = Field(default_factory=dict, description="Metadata JSON động")
    data_source: Optional[Dict[str, Any]] = Field(default=None, description="Cấu hình nguồn dữ liệu nếu có")
    is_active: bool = Field(default=True)


class WorknodeHeartbeatRequest(BaseModel):
    node_id: str = Field(..., description="Mã định danh máy trạm (vd: mac-mini-01)")
    ip: str = Field(default="127.0.0.1", description="Địa chỉ IP")
    capabilities: Optional[List[str]] = Field(default_factory=lambda: ["GUI_OPENCLAW", "LOCAL_OCR"])
    status: str = Field(default="READY")
    cpu_percent: float = Field(default=0.0)
    ram_percent: float = Field(default=0.0)
    active_tasks: int = Field(default=0)
    platform: Optional[str] = Field(default="")


class CrossReportRequest(BaseModel):
    scope: Optional[List[str]] = Field(default=None, description="Danh sách phòng ban cần đối soát")
    #: Bỏ qua (giữ cho client cũ): cấp bảo mật lấy từ tài khoản người gọi, không tự khai.
    clearance_level: Optional[int] = Field(default=None, description="Không dùng — lấy từ tài khoản")


# ─── Endpoints ─────────────────────────────────────────────────────────────

@router.get("/admin/departments/overview")
async def get_departments_overview(user: dict = _READ) -> Dict[str, Any]:
    """Lấy danh sách các phòng ban kèm các nguồn dữ liệu đang kết nối."""
    depts = erp_db.get_enterprise_departments()
    result = []
    for d in depts:
        code = d["dept_code"]
        sources = erp_db.get_department_data_sources(code)
        cached_summary = department_engine.get_cached_summary(code)
        result.append({
            **d,
            "sources": sources,
            "sources_count": len(sources),
            "cached_summary": cached_summary or "Chưa thực hiện quét dữ liệu",
        })
    return {
        "status": "success",
        "total_departments": len(result),
        "departments": result,
    }


@router.post("/admin/departments/save")
async def save_department_config(req: DepartmentSaveRequest, user: dict = _ADMIN) -> Dict[str, Any]:
    """Khai báo hoặc cập nhật cấu hình phòng ban & nguồn dữ liệu qua No-Code Web UI."""
    saved_dept = department_engine.register_department({
        "dept_code": req.dept_code,
        "dept_name": req.dept_name,
        "data_clearance_level": req.data_clearance_level,
        "config_metadata": req.config_metadata,
        "is_active": req.is_active,
    })

    bound_source = None
    if req.data_source:
        bound_source = department_engine.bind_data_source(
            dept_code=req.dept_code,
            source_dict=req.data_source,
        )

    # Thu thập thử dữ liệu ngay sau khi cấu hình
    try:
        await department_engine.ingest_department_data(req.dept_code, force_refresh=True)
    except Exception as e:
        logger.warning("Không thể tự động ingest ngay cho %s: %s", req.dept_code, e)

    return {
        "status": "success",
        "department": saved_dept,
        "data_source": bound_source,
        "message": f"Đã lưu cấu hình phòng ban {req.dept_code} thành công.",
    }


@router.post("/worknodes/heartbeat")
async def receive_worknode_heartbeat(req: WorknodeHeartbeatRequest) -> Dict[str, Any]:
    """
    Endpoint nhận ping từ máy Mac Mini OpenClaw hoặc trạm ngoại vi.
    Tự động phát hiện node mới và phát sóng sự kiện qua WebSocket Topology.
    """
    ack = elastic_grid_manager.record_heartbeat(
        node_id=req.node_id,
        ip=req.ip,
        capabilities=req.capabilities,
        status=req.status,
        cpu_percent=req.cpu_percent,
        ram_percent=req.ram_percent,
        active_tasks=req.active_tasks,
    )

    # Phát sóng sự kiện WebSocket Topology để Web UI cập nhật ngay mà không cần reload trang
    try:
        from mateai.interfaces.websocket.realtime_hub import broadcast_topology_event
        await broadcast_topology_event(
            source=f"worknode_{req.node_id}",
            target="worker_grid_cluster",
            action=f"heartbeat_{req.status}",
        )
    except Exception:
        pass

    return ack


@router.get("/worknodes/status")
async def get_worknodes_status(user: dict = _READ) -> Dict[str, Any]:
    """Lấy danh sách các trạm ngoại vi và hàng đợi Standby."""
    return {
        "status": "success",
        "grid": elastic_grid_manager.get_grid_overview(),
    }


@router.post("/admin/cross-report")
async def generate_cross_report_api(req: CrossReportRequest, user: dict = _ADMIN) -> Dict[str, Any]:
    """Kích hoạt báo cáo liên phòng ban tức thời từ Admin Web UI."""
    from mateai.application.agent.agent_orchestrator import principal_clearance
    from core.plugin_manager import run_blocking
    level = await run_blocking(principal_clearance, username=user.get("username"))
    return multi_agent_system.generate_cross_domain_report(
        query_context="Yêu cầu từ Admin Web UI",
        requested_departments=req.scope,
        clearance_level=level,
    )


@router.get("/admin/ephemeral-cache")
async def get_ephemeral_cache_stats(user: dict = _ADMIN) -> Dict[str, Any]:
    """Giám sát bộ đệm RAM tự hủy theo tiêu chuẩn GDPR / Nghị định 13."""
    from mateai.infrastructure.cache.ephemeral_cache import ephemeral_cache
    return {
        "status": "success",
        "cache_stats": ephemeral_cache.get_stats(),
        "policy": {
            "sliding_ttl_minutes": 15,
            "hard_timeout_minutes": 30,
            "sweeper_interval_seconds": 60,
            "compliance": "GDPR / Decree 13 Non-PII In-Memory Only",
        },
    }


@router.post("/admin/ephemeral-cache/flush")
async def flush_ephemeral_cache_all(user: dict = _ADMIN) -> Dict[str, Any]:
    """Tiêu hủy khẩn cấp toàn bộ dữ liệu tạm trên RAM (Emergency RAM Flush)."""
    from mateai.infrastructure.cache.ephemeral_cache import ephemeral_cache
    flushed = ephemeral_cache.sweep_expired()
    # Hoặc xóa toàn bộ cửa sổ cache hiện tại
    with ephemeral_cache._lock:
        total_purged = len(ephemeral_cache._store)
        ephemeral_cache._store.clear()
        ephemeral_cache._active_domain_by_session.clear()

    return {
        "status": "success",
        "purged_items_count": total_purged,
        "message": f"Đã tiêu hủy an toàn {total_purged} mục dữ liệu tạm trên RAM.",
    }

