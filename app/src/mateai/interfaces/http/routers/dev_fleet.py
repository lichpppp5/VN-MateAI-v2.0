# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 Dương Thanh Lịch — VN-MateAI. See LICENSE and NOTICE.
"""
mateai/interfaces/http/routers/dev_fleet.py
===========================================
API quản lý Dev Fleet (docs/integrations/dev-fleet.md). Router chỉ xác thực / phân quyền / dịch lỗi — toàn bộ logic ở
`application/devfleet/service.py`. Đọc: manager + admin. Ghi (giao / huỷ / chạy lại / đổi chế độ): admin, và vẫn qua cổng
chính sách + duyệt trong service (admin bấm trực tiếp không phải AI nên không vướng kill switch của AI).
"""
from __future__ import annotations

import logging
from typing import Any, Dict, Optional

from fastapi import APIRouter, Body, Depends, HTTPException, Query
from fastapi.responses import JSONResponse

from mateai.application.devfleet import models as m
from mateai.application.devfleet.provider import FleetError
from mateai.application.devfleet.service import FleetModeError, dev_fleet
from mateai.interfaces.http.auth_dependencies import require_roles

logger = logging.getLogger(__name__)
router = APIRouter()

_READ = Depends(require_roles(["manager", "admin"]))
_WRITE = Depends(require_roles(["admin"]))
_TAG = ["Dev Fleet"]


def _actor(user: Dict[str, Any]) -> str:
    return str(user.get("username") or user.get("sub") or "admin")


async def _guard(coro):
    """Dịch lỗi miền -> HTTP: chế độ (409), đặc tả sai (422), Master không dùng được (502)."""
    try:
        return await coro
    except FleetModeError as exc:
        raise HTTPException(status_code=409, detail={"code": exc.code, "message": str(exc)})
    except m.SpecError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    except FleetError as exc:
        raise HTTPException(status_code=502, detail={"code": exc.kind, "message": str(exc)})


async def _sync(fn, *args, **kwargs):
    """Chạy hàm đồng bộ BÊN TRONG `_guard` (để lỗi miền của nó cũng được dịch sang HTTP)."""
    return fn(*args, **kwargs)


@router.get("/api/v1/dev-fleet/status", summary="Tổng quan cụm Dev (Master, worker, tác vụ)", tags=_TAG)
async def fleet_status(user: Dict[str, Any] = _READ) -> Dict[str, Any]:
    return await dev_fleet.status()


@router.get("/api/v1/dev-fleet/workers", summary="Danh sách worker (Mac mini) do Master báo", tags=_TAG)
async def fleet_workers(user: Dict[str, Any] = _READ) -> Dict[str, Any]:
    snap = await _guard(dev_fleet.snapshot())
    return {"reachable": snap["reachable"], "error": snap["error"], "observed_at": snap["observed_at"], "workers": snap["workers"]}


@router.get("/api/v1/dev-fleet/workers/{worker_id}", summary="Chi tiết một worker", tags=_TAG)
async def fleet_worker(worker_id: str, user: Dict[str, Any] = _READ) -> Dict[str, Any]:
    worker = await _guard(dev_fleet.worker(worker_id))
    if worker is None:
        raise HTTPException(status_code=404, detail="Master không báo worker này")
    return worker


@router.get("/api/v1/dev-fleet/workers/{worker_id}/metrics", summary="Số đo CPU/RAM/đĩa của worker (từ Master)", tags=_TAG)
async def fleet_worker_metrics(worker_id: str, user: Dict[str, Any] = _READ) -> Dict[str, Any]:
    return await _guard(dev_fleet.worker_metrics(worker_id))


@router.get("/api/v1/dev-fleet/workers/{worker_id}/git", summary="Trạng thái Git của một repository trên worker", tags=_TAG)
async def fleet_worker_git(worker_id: str, repository: str = Query(..., min_length=1, max_length=200),
                           user: Dict[str, Any] = _READ) -> Dict[str, Any]:
    return await _guard(dev_fleet.git_status(worker_id, repository))


@router.post("/api/v1/dev-fleet/workers/{worker_id}/disable", summary="Tắt / bật một worker (kill switch theo máy)", tags=_TAG)
async def fleet_worker_disable(worker_id: str, body: Dict[str, Any] = Body(default={}), user: Dict[str, Any] = _WRITE) -> Dict[str, Any]:
    from mateai.interfaces.http.secret_masking import _mask_secrets
    return await _guard(dev_fleet.set_worker_disabled(worker_id, bool(body.get("disabled", True)), actor=_actor(user),
                                                      reason=str(body.get("reason") or ""), mask=_mask_secrets))


@router.get("/api/v1/dev-fleet/agents", summary="Agent đang chạy trên cụm", tags=_TAG)
async def fleet_agents(user: Dict[str, Any] = _READ) -> Dict[str, Any]:
    return {"agents": await _guard(dev_fleet.agents())}


@router.get("/api/v1/dev-fleet/events", summary="Hoạt động trực tiếp gần đây", tags=_TAG)
async def fleet_events(limit: int = Query(50, ge=1, le=500), user: Dict[str, Any] = _READ) -> Dict[str, Any]:
    return {"events": dev_fleet.events(limit)}


@router.get("/api/v1/dev-fleet/briefing", summary="Báo cáo điều hành Dev Fleet (số liệu thật)", tags=_TAG)
async def fleet_briefing(user: Dict[str, Any] = _READ) -> Dict[str, Any]:
    return await _guard(dev_fleet.briefing())


@router.get("/api/v1/dev-fleet/config", summary="Cấu hình Dev Fleet (token không bao giờ trả về)", tags=_TAG)
async def fleet_config(user: Dict[str, Any] = _WRITE) -> Dict[str, Any]:
    return dev_fleet.config_view()


@router.post("/api/v1/dev-fleet/config", summary="Lưu cấu hình Dev Fleet (token rỗng = giữ nguyên)", tags=_TAG)
async def fleet_config_save(body: Dict[str, Any] = Body(...), user: Dict[str, Any] = _WRITE) -> Dict[str, Any]:
    from mateai.interfaces.http.secret_masking import _mask_secrets
    return await _guard(_sync(dev_fleet.save_settings, _actor(user), body, mask=_mask_secrets))


@router.post("/api/v1/dev-fleet/test-connection", summary="Thử kết nối thật tới Master bằng giá trị đang nhập", tags=_TAG)
async def fleet_test_connection(body: Dict[str, Any] = Body(default={}), user: Dict[str, Any] = _WRITE) -> Dict[str, Any]:
    return await _guard(dev_fleet.test_connection(body))


@router.post("/api/v1/dev-fleet/mode", summary="Đổi chế độ: disabled | read_only | controlled | autonomous", tags=_TAG)
async def fleet_mode(body: Dict[str, Any] = Body(...), user: Dict[str, Any] = _WRITE) -> Dict[str, Any]:
    from mateai.interfaces.http.secret_masking import _mask_secrets
    return await _guard(dev_fleet.set_mode(str(body.get("mode") or ""), actor=_actor(user),
                                           reason=str(body.get("reason") or ""), mask=_mask_secrets))


# ── dự án ───────────────────────────────────────────────────────────────────

@router.get("/api/v1/dev-fleet/projects", summary="Danh sách dự án Dev", tags=_TAG)
async def fleet_projects(user: Dict[str, Any] = _READ) -> Dict[str, Any]:
    return {"projects": [{**p, "progress": dev_fleet.project_progress(p["project_id"])} for p in dev_fleet.projects()]}


@router.post("/api/v1/dev-fleet/projects", summary="Tạo dự án Dev", tags=_TAG)
async def fleet_project_create(body: Dict[str, Any] = Body(...), user: Dict[str, Any] = _WRITE) -> Dict[str, Any]:
    return await _guard(_sync(
        dev_fleet.create_project, str(body.get("name") or ""), repository=str(body.get("repository") or ""),
        description=str(body.get("description") or ""), preferred_workers=[str(w) for w in (body.get("preferred_workers") or [])],
        created_by=_actor(user), goal_id=body.get("goal_id") or None))


@router.get("/api/v1/dev-fleet/projects/{project_id}", summary="Chi tiết dự án + tiến độ", tags=_TAG)
async def fleet_project(project_id: str, user: Dict[str, Any] = _READ) -> Dict[str, Any]:
    project = dev_fleet.project(project_id)
    if not project:
        raise HTTPException(status_code=404, detail="Không có dự án này")
    return {**project, "progress": dev_fleet.project_progress(project_id)}


# ── tác vụ ──────────────────────────────────────────────────────────────────

@router.get("/api/v1/dev-fleet/tasks", summary="Danh sách tác vụ Dev", tags=_TAG)
async def fleet_tasks(status: Optional[str] = None, limit: int = Query(100, ge=1, le=500), user: Dict[str, Any] = _READ) -> Dict[str, Any]:
    return {"tasks": dev_fleet.tasks(status=status, limit=limit)}


@router.get("/api/v1/dev-fleet/tasks/{task_id}", summary="Chi tiết tác vụ: lượt chạy, bằng chứng, kiểm chứng", tags=_TAG)
async def fleet_task(task_id: str, user: Dict[str, Any] = _READ) -> Dict[str, Any]:
    task = dev_fleet.task(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="Không có tác vụ Dev này")
    return task


@router.post("/api/v1/dev-fleet/tasks/plan", summary="Dry-run: máy nào sẽ được chọn, rủi ro, có cần duyệt không", tags=_TAG)
async def fleet_task_plan(body: Dict[str, Any] = Body(...), user: Dict[str, Any] = _WRITE) -> Dict[str, Any]:
    return await _guard(dev_fleet.plan(body))


@router.post("/api/v1/dev-fleet/tasks", summary="Tạo + giao tác vụ Dev (qua cổng chính sách / duyệt)", tags=_TAG)
async def fleet_task_create(body: Dict[str, Any] = Body(...), user: Dict[str, Any] = _WRITE) -> JSONResponse:
    out = await _guard(dev_fleet.create_task(body, requested_by=_actor(user), idempotency_key=(body.get("idempotency_key") or None),
                                             check_rbac=False))
    code = {"denied": 403, "no_worker": 409, "blocked": 409}.get(out.get("status"), 200)
    return JSONResponse(out, status_code=code)


@router.post("/api/v1/dev-fleet/tasks/{task_id}/cancel", summary="Huỷ tác vụ (Master xác nhận)", tags=_TAG)
async def fleet_task_cancel(task_id: str, user: Dict[str, Any] = _WRITE) -> Dict[str, Any]:
    return await _guard(dev_fleet.cancel_task(task_id, requested_by=_actor(user), check_rbac=False))


@router.post("/api/v1/dev-fleet/tasks/{task_id}/retry", summary="Chạy lại / chuyển sang máy khác", tags=_TAG)
async def fleet_task_retry(task_id: str, body: Dict[str, Any] = Body(default={}), user: Dict[str, Any] = _WRITE) -> Dict[str, Any]:
    return await _guard(dev_fleet.retry_task(task_id, requested_by=_actor(user), worker_id=(body.get("worker_id") or None),
                                             check_rbac=False))


@router.post("/api/v1/dev-fleet/sync", summary="Đối chiếu ngay mọi lượt chạy đang hoạt động với Master", tags=_TAG)
async def fleet_sync(user: Dict[str, Any] = _WRITE) -> Dict[str, Any]:
    return await _guard(dev_fleet.sync_active())
