# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 Dương Thanh Lịch — VN-MateAI. See LICENSE and NOTICE.
"""
mateai/interfaces/http/routers/tasks.py
========================================
Giao việc vi mô (micro-task) và nhật ký KPI.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from datetime import datetime
from functools import partial
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Body, Depends, Header, HTTPException, Query, Request, status
from fastapi.responses import FileResponse, JSONResponse, Response
from pydantic import BaseModel, Field

from core.plugin_manager import run_blocking
from mateai.config.loader import get_assistant_name, settings
from mateai.interfaces.http.auth_dependencies import get_current_user, require_roles
from mateai.interfaces.websocket.realtime_hub import broadcast_hud, broadcast_portal_ui

logger = logging.getLogger(__name__)

router = APIRouter()


class TaskDispatchRequest(BaseModel):
    """Payload for POST /api/v1/tasks/send."""
    client_id: Optional[str] = Field(default=None, description="ID máy trạm đích")
    client_ids: Optional[List[str]] = Field(default=None, max_length=200, description="Giao cùng lúc nhiều máy")
    message: str = Field(..., min_length=1, max_length=500, description="Nội dung công việc cần nhắc")
    sender: Optional[str] = Field(default=None, max_length=80, description="Nhãn người/phòng ban gửi hiện trên popup")
    due_minutes: Optional[int] = Field(default=None, ge=1, le=10080, description="Hạn phản hồi (phút)")


_WRITE = Depends(require_roles(["manager", "admin"]))


def _online_clients() -> set:
    from mateai.interfaces.websocket.client_orchestrator import orchestrator
    return {cid for cid in list(orchestrator._clients) if orchestrator.is_client_online(cid)}


def _audit(user: Dict[str, Any], action: str, details: Dict[str, Any], ok: bool = True) -> None:
    try:
        from mateai.application.security.safety_guard import security_engine
        security_engine.log_audit(str(user.get("username")), action, "TASKS", "SUCCESS" if ok else "FAILED", details)
    except Exception:  # noqa: BLE001
        pass


@router.get(
    "/api/v1/tasks/kpi-logs",
    summary="Get recent KPI task logs and completion statistics",
    tags=["Micro-Tasking"],
)
async def get_kpi_logs_endpoint(
    limit: int = Query(default=100, ge=1, le=1000),
    client_id: Optional[str] = Query(default=None),
    status: Optional[str] = Query(default=None),
    current_user: Dict[str, Any] = Depends(get_current_user),
) -> Dict[str, Any]:
    """Return historical task log from logs/kpi_logs.csv and aggregate KPI metrics."""
    from mateai.application.devices.task_manager import task_manager
    return task_manager.get_kpi_logs(limit=limit, client_id=client_id, status=status)


@router.get("/api/v1/tasks/board", summary="Bảng giám sát giao việc (số liệu thật)", tags=["Micro-Tasking"])
async def task_board(
    days: int = Query(default=30, ge=1, le=365),
    current_user: Dict[str, Any] = Depends(get_current_user),
) -> Dict[str, Any]:
    from mateai.application.devices.task_manager import task_manager
    online = _online_clients()
    data = await run_blocking(partial(task_manager.board, days, None, online))
    data["online_clients"] = sorted(online)
    data["days"] = days
    return {"status": "success", **data}


@router.post(
    "/api/v1/tasks/send",
    summary="Dispatch a micro-task popup to a worker client node",
    tags=["Micro-Tasking"],
)
async def send_task_endpoint(
    payload: TaskDispatchRequest,
    current_user: Dict[str, Any] = _WRITE,
    idempotency_key: Optional[str] = Header(default=None, alias="Idempotency-Key", max_length=100),
) -> Dict[str, Any]:
    """Gửi popup nhắc việc tới một hoặc nhiều máy trạm (manager / admin).

    Người giao THẬT là tài khoản đăng nhập (lưu `dispatched_by` + audit); `sender`
    chỉ là nhãn hiển thị trên popup.

    `Idempotency-Key` (§103, §140): gửi lại cùng khoá trong 10 phút (bấm hai lần, mạng
    chập chờn rồi thử lại) trả kết quả lần đầu — popup KHÔNG bật lần hai."""
    cache_key = f"{current_user.get('username')}|{idempotency_key}" if idempotency_key else None
    if cache_key:
        hit = _IDEMPOTENT.get(cache_key)
        if hit and time.time() - hit[0] < _IDEMPOTENT_TTL_S:
            return {**hit[1], "idempotent_replay": True}
    result = await _dispatch(payload, current_user)
    if cache_key:
        now = time.time()
        for k in [k for k, (t, _) in _IDEMPOTENT.items() if now - t >= _IDEMPOTENT_TTL_S]:
            _IDEMPOTENT.pop(k, None)
        _IDEMPOTENT[cache_key] = (now, result)
    return result


#: Kết quả theo Idempotency-Key (một tiến trình; nhiều tiến trình cần kho dùng chung — xem
#: docs/production/readiness-score.md "Khả năng mở rộng").
_IDEMPOTENT: Dict[str, Any] = {}
_IDEMPOTENT_TTL_S = 600.0


async def _dispatch(payload: TaskDispatchRequest, current_user: Dict[str, Any]) -> Dict[str, Any]:
    from mateai.application.devices.task_manager import task_manager
    targets = [c.strip() for c in (payload.client_ids or []) if c and c.strip()]
    if payload.client_id and payload.client_id.strip():
        targets.insert(0, payload.client_id.strip())
    targets = list(dict.fromkeys(targets))
    if not targets:
        raise HTTPException(status_code=400, detail="Chưa chọn máy trạm nhận việc.")
    username = str(current_user.get("username") or "unknown")
    sender = (payload.sender or "").strip() or current_user.get("full_name") or username
    results = []
    for cid in targets:
        r = await task_manager.dispatch_task(client_id=cid, message=payload.message.strip(), sender=sender,
                                             dispatched_by=username, due_minutes=payload.due_minutes)
        results.append(r)
    ok = [r for r in results if r.get("status") == "success"]
    _audit(current_user, "task_dispatch", {"targets": targets, "sent": len(ok),
                                            "due_minutes": payload.due_minutes}, ok=bool(ok))
    if len(targets) == 1:
        if not ok:
            raise HTTPException(status_code=400, detail=results[0].get("message", "Gửi task thất bại"))
        return results[0]
    return {"status": "success" if ok else "error", "sent": len(ok), "failed": len(results) - len(ok),
            "results": results}


@router.post("/api/v1/tasks/{task_id}/remind", summary="Nhắc lại việc còn chờ", tags=["Micro-Tasking"])
async def remind_task_endpoint(task_id: str, current_user: Dict[str, Any] = _WRITE) -> Dict[str, Any]:
    from mateai.application.devices.task_manager import task_manager
    r = await task_manager.remind_task(task_id)
    _audit(current_user, "task_remind", {"task_id": task_id}, ok=r.get("status") == "success")
    if r.get("status") != "success":
        raise HTTPException(status_code=409, detail=r.get("message"))
    return r


@router.post("/api/v1/tasks/{task_id}/cancel", summary="Huỷ việc còn chờ", tags=["Micro-Tasking"])
async def cancel_task_endpoint(task_id: str, current_user: Dict[str, Any] = _WRITE) -> Dict[str, Any]:
    from mateai.application.devices.task_manager import task_manager
    r = task_manager.cancel_task(task_id, str(current_user.get("username") or "unknown"))
    _audit(current_user, "task_cancel", {"task_id": task_id}, ok=r.get("status") == "success")
    if r.get("status") != "success":
        raise HTTPException(status_code=409, detail=r.get("message"))
    return r


@router.get("/api/v1/tasks/export.csv", summary="Xuất danh sách giao việc (CSV)", tags=["Micro-Tasking"])
async def export_tasks_csv(
    days: int = Query(default=30, ge=1, le=365),
    current_user: Dict[str, Any] = _WRITE,
) -> Response:
    import csv
    import io
    from mateai.application.devices.task_manager import task_manager
    data = await run_blocking(partial(task_manager.board, days, None, _online_clients()))
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["Giao lúc", "Máy trạm", "Nội dung", "Người giao", "Nhãn gửi", "Hạn", "Trạng thái",
                "Quá hạn", "Phản hồi lúc", "Thời gian phản hồi (giây)", "Ghi chú"])
    for t in data["tasks"]:
        w.writerow([t.get("timestamp"), t.get("client_id"), _csv_safe(t.get("task_message")),
                    t.get("dispatched_by"), _csv_safe(t.get("sender")), t.get("due_at"), t.get("status"),
                    "có" if t.get("overdue") else "", t.get("responded_at"), t.get("response_s"),
                    _csv_safe(t.get("resolution_notes"))])
    _audit(current_user, "task_export", {"days": days, "rows": len(data["tasks"])})
    return Response(content="\ufeff" + buf.getvalue(), media_type="text/csv; charset=utf-8",
                    headers={"Content-Disposition": f'attachment; filename="giao_viec_{days}ngay.csv"'})


def _csv_safe(v: Any) -> str:
    """Chặn công thức Excel (=, +, -, @) trong ô do người dùng nhập."""
    s = str(v or "")
    return "'" + s if s[:1] in ("=", "+", "-", "@", "\t", "\r") else s


# ── Sổ tác vụ vận hành của AI (prompt Supervisor §21–§30, §82–§83) ──────────

class OpTaskDecision(BaseModel):
    note: str = Field(default="", max_length=300)


@router.get("/api/v1/ops/tasks", summary="Sổ tác vụ của AI: trạng thái, bước, kiểm chứng", tags=["AI Operations"])
async def list_op_tasks(
    status: Optional[str] = Query(default=None),
    kind: Optional[str] = Query(default=None),
    days: int = Query(default=7, ge=1, le=365),
    limit: int = Query(default=200, ge=1, le=1000),
    current_user: Dict[str, Any] = Depends(require_roles(["manager", "admin"])),
) -> Dict[str, Any]:
    from datetime import timedelta
    from mateai.application.tasks import ledger
    since = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d %H:%M:%S")
    rows = await run_blocking(partial(ledger.list_tasks, status=status, kind=kind, since=since, limit=limit))
    by_status: Dict[str, int] = {}
    for r in rows:
        by_status[r["status"]] = by_status.get(r["status"], 0) + 1
    done = by_status.get("COMPLETED", 0)
    finished = done + by_status.get("FAILED", 0) + by_status.get("BLOCKED", 0)
    return {"status": "success", "tasks": rows, "by_status": by_status, "days": days,
            "success_rate": round(done * 100.0 / finished, 1) if finished else None}


@router.get("/api/v1/ops/tasks/{task_id}", summary="Chi tiết tác vụ: bước, quyết định chính sách, bằng chứng",
            tags=["AI Operations"])
async def get_op_task(task_id: str, current_user: Dict[str, Any] = Depends(require_roles(["manager", "admin"]))) -> Dict[str, Any]:
    from mateai.application.tasks import ledger
    task = await run_blocking(partial(ledger.get_task, task_id))
    if task is None:
        raise HTTPException(status_code=404, detail="Không có tác vụ này.")
    return {"status": "success", "task": task}


@router.post("/api/v1/ops/tasks/{task_id}/confirm", summary="Người xác nhận kết quả tác vụ đã leo thang",
             tags=["AI Operations"])
async def confirm_op_task(task_id: str, payload: OpTaskDecision,
                          current_user: Dict[str, Any] = Depends(require_roles(["admin"]))) -> Dict[str, Any]:
    """Tác vụ ESCALATED (AI không tự kiểm chứng được) chỉ thành COMPLETED khi NGƯỜI xác nhận —
    xác nhận được ghi thành bằng chứng + audit."""
    from mateai.application.tasks import ledger
    task = ledger.get_task(task_id)
    if task is None:
        raise HTTPException(status_code=404, detail="Không có tác vụ này.")
    if task["status"] != ledger.ESCALATED:
        raise HTTPException(status_code=409, detail=f"Chỉ xác nhận được tác vụ đang ESCALATED (hiện: {task['status']}).")
    who = str(current_user.get("username") or "admin")
    ledger.add_evidence(task_id, source=f"human:{who}", kind="FACT", verified=True,
                        summary=f"Người xác nhận kết quả: {who}. {payload.note}".strip())
    ledger.transition(task_id, ledger.COMPLETED, verification_status="passed",
                      result_summary=f"Người xác nhận ({who}). {payload.note}".strip()[:300])
    _audit(current_user, "op_task_confirm", {"task_id": task_id, "note": payload.note})
    return {"status": "success", "task": ledger.get_task(task_id)}


# ── Mục tiêu (prompt cuối §42) ──────────────────────────────────────────────

class GoalRequest(BaseModel):
    title: str = Field(..., min_length=1, max_length=300)
    level: str = Field(..., pattern="^(company|department|operational)$")
    parent_id: Optional[str] = None
    department: Optional[str] = Field(default=None, max_length=120)
    owner: Optional[str] = Field(default=None, max_length=80)
    due_date: Optional[str] = Field(default=None, max_length=30)


@router.get("/api/v1/ops/goals", summary="Cây mục tiêu + tiến độ thật", tags=["AI Operations"])
async def list_goals(current_user: Dict[str, Any] = Depends(require_roles(["manager", "admin"]))) -> Dict[str, Any]:
    from mateai.application.tasks import ledger
    return {"status": "success", "goals": await run_blocking(ledger.goal_tree)}


@router.post("/api/v1/ops/goals", summary="Tạo mục tiêu (công ty / phòng ban / vận hành)", tags=["AI Operations"])
async def create_goal(payload: GoalRequest,
                      current_user: Dict[str, Any] = Depends(require_roles(["manager", "admin"]))) -> Dict[str, Any]:
    from mateai.application.tasks import ledger
    if payload.level == "company" and current_user.get("role") != "admin":
        raise HTTPException(status_code=403, detail="Chỉ admin đặt mục tiêu cấp công ty.")
    try:
        goal_id = await run_blocking(ledger.create_goal, **payload.model_dump())
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    _audit(current_user, "goal_create", {"goal_id": goal_id, **payload.model_dump()})
    return {"status": "success", "goal": await run_blocking(ledger.goal_tree, goal_id=goal_id)}


# ── Sự cố (prompt cuối §88) ──────────────────────────────────────────────────

class IncidentPhaseRequest(BaseModel):
    phase: str = Field(..., max_length=20)
    note: str = Field(default="", max_length=500)
    owner: Optional[str] = Field(default=None, max_length=80)
    affected_assets: Optional[str] = Field(default=None, max_length=300)


@router.get("/api/v1/ops/incidents", summary="Sự cố: pha xử lý, người phụ trách, tài sản", tags=["AI Operations"])
async def list_incidents(limit: int = 50,
                         current_user: Dict[str, Any] = Depends(require_roles(["manager", "admin"]))) -> Dict[str, Any]:
    from mateai.application.tasks import ledger
    rows = await run_blocking(ledger.list_tasks, kind="incident", limit=max(1, min(limit, 200)))
    return {"status": "success", "phases": list(ledger.INCIDENT_PHASES), "incidents": rows}


@router.post("/api/v1/ops/incidents/{task_id}/phase", summary="Chuyển pha xử lý sự cố", tags=["AI Operations"])
async def set_incident_phase(task_id: str, payload: IncidentPhaseRequest,
                             current_user: Dict[str, Any] = Depends(require_roles(["manager", "admin"]))) -> Dict[str, Any]:
    from mateai.application.tasks import ledger
    who = str(current_user.get("username") or "?")
    try:
        task = await run_blocking(ledger.incident_phase, task_id=task_id, phase=payload.phase.strip().upper(),
                                  actor=who, note=payload.note, owner=payload.owner, assets=payload.affected_assets)
    except ledger.InvalidTransition as exc:
        code = 404 if "Không phải sự cố" in str(exc) else 409
        raise HTTPException(status_code=code, detail=str(exc))
    _audit(current_user, "incident_phase", {"task_id": task_id, "phase": payload.phase, "note": payload.note})
    return {"status": "success", "incident": task}


@router.post("/api/v1/ops/tasks/{task_id}/cancel", summary="Huỷ tác vụ của AI", tags=["AI Operations"])
async def cancel_op_task(task_id: str, payload: OpTaskDecision,
                         current_user: Dict[str, Any] = Depends(require_roles(["manager", "admin"]))) -> Dict[str, Any]:
    from mateai.application.tasks import ledger
    task = ledger.get_task(task_id)
    if task is None:
        raise HTTPException(status_code=404, detail="Không có tác vụ này.")
    if task["status"] in ledger.TERMINAL:
        raise HTTPException(status_code=409, detail=f"Tác vụ đã kết thúc ({task['status']}).")
    who = str(current_user.get("username") or "?")
    ledger.transition(task_id, ledger.CANCELLED, result_summary=f"Huỷ bởi {who}. {payload.note}".strip()[:300])
    _audit(current_user, "op_task_cancel", {"task_id": task_id, "note": payload.note})
    return {"status": "success", "task": ledger.get_task(task_id)}


@router.get("/api/v1/ops/overview", summary="Bảng giám sát AI Supervisor (số liệu thật)", tags=["AI Operations"])
async def ops_overview(
    hours: int = Query(default=24, ge=1, le=720),
    current_user: Dict[str, Any] = Depends(require_roles(["manager", "admin"])),
) -> Dict[str, Any]:
    """AI đang làm gì, ai cho phép, cái gì bị chặn / chờ duyệt / cần người xác nhận,
    tốn bao nhiêu token, thoại nhanh tới đâu (prompt §82). Không có số nào ước đoán."""
    from datetime import timedelta
    from mateai.application.security import policy_engine as pe
    from mateai.application.security.zero_trust import hitl_manager
    from mateai.application.tasks import ledger
    from mateai.application.voice.voice_turn import trace_stats
    from mateai.config.loader import settings
    from mateai.infrastructure.database.db_manager import db_manager

    since = (datetime.now() - timedelta(hours=hours)).strftime("%Y-%m-%d %H:%M:%S")
    stats = await run_blocking(partial(db_manager.op_stats, since))
    active = await run_blocking(partial(ledger.list_tasks, since=since, limit=500))
    open_states = ("NEW", "ANALYZING", "PLANNED", "WAITING_AUTHORIZATION", "AUTHORIZED", "EXECUTING", "VERIFYING", "ESCALATED", "BLOCKED")
    attention_all = [t for t in active if t["status"] in ("ESCALATED", "WAITING_AUTHORIZATION", "FAILED")]
    attention = attention_all[:20]
    by = stats["tasks_by_status"]
    finished = by.get("COMPLETED", 0) + by.get("FAILED", 0) + by.get("BLOCKED", 0)
    steps_total = sum(stats["steps_by_decision"].values())
    try:
        pending = len(hitl_manager.get_pending_list())
    except Exception:  # noqa: BLE001
        pending = None
    voice = await run_blocking(trace_stats)
    try:
        from mateai.application.operations.health_monitor import SYSTEM_HEALTH_CACHE
        hw = dict(SYSTEM_HEALTH_CACHE.get("hardware") or {})
        health = {"cpu_percent": hw.get("cpu_percent"), "ram_percent": hw.get("ram_percent"),
                  "disk_percent": hw.get("disk_percent")}
    except Exception:  # noqa: BLE001
        health = {}
    return {
        "status": "success", "hours": hours,
        "ai_status": {"kill_switch": settings.autonomy.kill_switch,
                      "disabled_agents": list(settings.autonomy.disabled_agents or []),
                      "disabled_tools": list(settings.autonomy.disabled_tools or []),
                      "policy_version": pe.policy_version()},
        "tasks": {"by_status": by, "by_kind": stats["tasks_by_kind"],
                  "active": sum(1 for t in active if t["status"] in open_states),
                  "success_rate": round(by.get("COMPLETED", 0) * 100.0 / finished, 1) if finished else None,
                  "attention": attention, "attention_total": len(attention_all)},
        "actions": {"total": steps_total, "by_decision": stats["steps_by_decision"], "by_rule": stats["steps_by_rule"],
                    "by_verification": stats["steps_by_verification"],
                    "denial_rate": round(stats["steps_by_decision"].get("deny", 0) * 100.0 / steps_total, 1) if steps_total else None},
        "approvals_pending": pending,
        "incidents_open": sum(1 for t in active if t["kind"] == "incident" and t["status"] not in ledger.TERMINAL),
        "cost": {"llm_calls": stats["llm_calls"], "total_tokens": stats["tokens"],
                 "currency_cost": None if stats["llm_cost"] is None else round(float(stats["llm_cost"]), 4),
                 "currency": "USD", "unpriced_tokens": stats["unpriced_tokens"]},
        "voice": voice,
        "system_health": health,
    }
