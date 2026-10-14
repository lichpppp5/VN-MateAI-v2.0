# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 Dương Thanh Lịch — VN-MateAI. See LICENSE and NOTICE.
"""
skills/dev_fleet_tools.py
=========================
Công cụ cho AI Ly Ly quản lý cụm Dev (Dev Fleet) qua Ubuntu Master — docs/integrations/dev-fleet.md.

Tên công cụ đọc BẮT ĐẦU bằng động từ chỉ-đọc (`get_` / `list_`) để Risk Engine xếp L0 — vẫn dùng được khi bật kill switch.
Công cụ ghi (`create_dev_fleet_task`, `cancel_dev_fleet_task`) đi qua cổng chính sách ĐÚNG MỨC RỦI RO CỦA TÁC VỤ (đặc tả `risk`):
low tự chạy khi module ở chế độ `controlled`; medium trở lên phải có người duyệt; kill switch chặn mọi thao tác ghi của AI.

Module tắt / chỉ đọc -> công cụ nói thẳng điều đó, không giả vờ đã làm.
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from core.plugin_manager import export_skill

logger = logging.getLogger(__name__)

_AGENT = "VN-MATEAI-CONNECTOR"


def _svc():
    from mateai.application.devfleet.service import dev_fleet
    return dev_fleet


async def _safe(coro) -> Dict[str, Any]:
    from mateai.application.devfleet import models as m
    from mateai.application.devfleet.provider import FleetError
    from mateai.application.devfleet.service import FleetModeError
    try:
        return await coro
    except FleetModeError as exc:
        return {"success": False, "module_state": exc.code, "error": str(exc)}
    except m.SpecError as exc:
        return {"success": False, "error": f"Đặc tả tác vụ chưa đủ: {exc}"}
    except FleetError as exc:
        return {"success": False, "error": str(exc), "kind": exc.kind}
    except Exception as exc:  # noqa: BLE001
        logger.exception("[DevFleet tool] lỗi")
        return {"success": False, "error": f"{type(exc).__name__}: {exc}"}


@export_skill(
    name="get_dev_fleet_status",
    description=("Xem tình trạng cụm Dev (Ubuntu Master, các Mac mini, agent, số tác vụ). Dùng khi hỏi 'cụm dev thế nào', "
                 "'Master có khỏe không', 'Mac nào đang rảnh'. Chỉ đọc."),
    parameters_schema={"type": "object", "properties": {}, "required": []},
)
async def get_dev_fleet_status() -> Dict[str, Any]:
    async def _run():
        svc = _svc()
        st = await svc.status()
        if not st.get("enabled"):
            return {"success": False, "module_state": "disabled", "error": "Module Dev Fleet đang TẮT — chưa kết nối cụm Dev.", **st}
        out: Dict[str, Any] = {"success": True, **st}
        if st.get("configured"):
            out["workers_detail"] = [{k: w[k] for k in ("worker_id", "state", "freshness", "cpu_percent", "memory_percent",
                                                        "current_task", "capabilities")} for w in await svc.workers()]
        return out
    return await _safe(_run())


@export_skill(
    name="list_dev_fleet_workers",
    description="Liệt kê các Mac mini / worker do Master báo, kèm trạng thái, độ tươi dữ liệu, capability. Chỉ đọc.",
    parameters_schema={"type": "object", "properties": {
        "state": {"type": "string", "description": "Lọc theo trạng thái: IDLE, BUSY, OFFLINE… Bỏ trống = tất cả."}}, "required": []},
)
async def list_dev_fleet_workers(state: Optional[str] = None) -> Dict[str, Any]:
    async def _run():
        workers = await _svc().workers()
        if state:
            workers = [w for w in workers if w["state"] == state.strip().upper()]
        return {"success": True, "count": len(workers), "workers": workers}
    return await _safe(_run())


@export_skill(
    name="get_dev_fleet_task",
    description=("Xem một tác vụ Dev: trạng thái, máy đang chạy, các lượt chạy, bằng chứng, đã KIỂM CHỨNG chưa. "
                 "Nếu `display_status` là COMPLETED_UNVERIFIED thì KHÔNG được nói là đã xong — nói rõ chưa kiểm chứng."),
    parameters_schema={"type": "object", "properties": {"task_id": {"type": "string", "description": "Mã tác vụ (OP-…)"}},
                       "required": ["task_id"]},
)
async def get_dev_fleet_task(task_id: str) -> Dict[str, Any]:
    async def _run():
        svc = _svc()
        svc._need_read()
        task = svc.task(task_id)
        if not task:
            return {"success": False, "error": f"Không có tác vụ Dev '{task_id}'"}
        runs = [{k: r.get(k) for k in ("run_id", "attempt", "worker_id", "status", "dispatched_at", "last_progress_at", "finished_at")}
                for r in task["runs"]]
        return {"success": True, "task_id": task_id, "title": task["title"], "status": task["status"],
                "display_status": task["display_status"], "verification_status": task.get("verification_status"),
                "result_summary": task.get("result_summary"), "runs": runs,
                "evidence": [e["summary"] for e in task.get("evidence", [])][-8:]}
    return await _safe(_run())


@export_skill(
    name="get_dev_fleet_briefing",
    description="Báo cáo điều hành cụm Dev: tác vụ treo, máy mất liên lạc, việc chờ duyệt / chưa kiểm chứng, đề xuất. Chỉ đọc.",
    parameters_schema={"type": "object", "properties": {}, "required": []},
)
async def get_dev_fleet_briefing() -> Dict[str, Any]:
    return await _safe(_wrap(_svc().briefing()))


async def _wrap(coro) -> Dict[str, Any]:
    return {"success": True, **(await coro)}


@export_skill(
    name="create_dev_fleet_task",
    description=(
        "Giao một tác vụ lập trình cho cụm Dev (qua Ubuntu Master → Mac mini). BẮT BUỘC có mục tiêu cụ thể, tiêu chí chấp nhận "
        "và bước kiểm chứng — không nhận 'hãy sửa lỗi này'. Tác vụ rủi ro medium trở lên cần người duyệt: nếu kết quả có "
        "`awaiting_approval`, nói người dùng duyệt phiếu `approval_id`, KHÔNG nói là đã giao. Đặt `require_tests`/`require_build`/"
        "`require_commit` để tác vụ chỉ được coi là xong khi có bằng chứng."
    ),
    parameters_schema={
        "type": "object",
        "properties": {
            "title": {"type": "string"}, "objective": {"type": "string", "description": "Mục tiêu cụ thể (≥10 ký tự)"},
            "acceptance_criteria": {"type": "array", "items": {"type": "string"}},
            "verification_steps": {"type": "array", "items": {"type": "string"}},
            "risk": {"type": "string", "enum": ["low", "medium", "high", "critical"]},
            "priority": {"type": "string", "enum": ["low", "medium", "high", "critical"]},
            "rollback": {"type": "string", "description": "Cách khôi phục — bắt buộc từ risk medium"},
            "project_id": {"type": "string"}, "repository": {"type": "string"}, "branch": {"type": "string"},
            "required_capabilities": {"type": "array", "items": {"type": "string"}},
            "required_platform": {"type": "string"}, "worker_id": {"type": "string", "description": "Ép máy cụ thể (tuỳ chọn)"},
            "require_tests": {"type": "boolean"}, "require_build": {"type": "boolean"}, "require_commit": {"type": "boolean"},
            "dry_run": {"type": "boolean", "description": "Chỉ xem máy nào sẽ được chọn, không giao"},
        },
        "required": ["title", "objective", "acceptance_criteria", "verification_steps"],
    },
)
async def create_dev_fleet_task(title: str, objective: str, acceptance_criteria: List[str], verification_steps: List[str],
                                risk: str = "medium", priority: str = "medium", rollback: str = "",
                                project_id: Optional[str] = None, repository: Optional[str] = None, branch: Optional[str] = None,
                                required_capabilities: Optional[List[str]] = None, required_platform: Optional[str] = None,
                                worker_id: Optional[str] = None, require_tests: bool = False, require_build: bool = False,
                                require_commit: bool = False, dry_run: bool = False) -> Dict[str, Any]:
    payload = {"title": title, "objective": objective, "acceptance_criteria": acceptance_criteria,
               "verification_steps": verification_steps, "risk": risk, "priority": priority, "rollback": rollback,
               "project_id": project_id, "repository": repository, "branch": branch,
               "required_capabilities": required_capabilities or [], "required_platform": required_platform,
               "worker_id": worker_id, "require_tests": require_tests, "require_build": require_build,
               "require_commit": require_commit}

    async def _run():
        svc = _svc()
        if dry_run:
            return {"success": True, "dry_run": True, **(await svc.plan(payload))}
        out = await svc.create_task(payload, requested_by="AI_Agent", agent_id=_AGENT, check_rbac=False)
        status = out.get("status")
        out["success"] = status in ("dispatched", "awaiting_approval", "duplicate")
        if status == "awaiting_approval":
            out["message"] = ("CHƯA giao: cần người có thẩm quyền duyệt phiếu " + str(out.get("approval_id")) +
                              ". Nói rõ điều này với người dùng.")
        return out
    return await _safe(_run())


@export_skill(
    name="cancel_dev_fleet_task",
    description="Huỷ một tác vụ Dev đang chạy (Master phải xác nhận dừng). Chỉ dùng khi người dùng yêu cầu rõ.",
    parameters_schema={"type": "object", "properties": {"task_id": {"type": "string"}}, "required": ["task_id"]},
)
async def cancel_dev_fleet_task(task_id: str) -> Dict[str, Any]:
    async def _run():
        out = await _svc().cancel_task(task_id, requested_by="AI_Agent", agent_id=_AGENT, check_rbac=False)
        out["success"] = out.get("status") == "cancelled"
        return out
    return await _safe(_run())
