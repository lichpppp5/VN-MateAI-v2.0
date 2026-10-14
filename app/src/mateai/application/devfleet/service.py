# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 Dương Thanh Lịch — VN-MateAI. See LICENSE and NOTICE.
"""
mateai/application/devfleet/service.py
======================================
Dev Fleet Orchestration (docs/integrations/dev-fleet.md): VN-MateAI là Project Authority — quyết định giao việc gì,
cho máy nào, có cần duyệt không, và việc đã XONG thật chưa. Ubuntu Master là cổng hạ tầng; OpenClaw thực thi.

Tái dùng, không dựng song song:
  - Task Ledger (`application/tasks/ledger.py`): MỘT máy trạng thái tác vụ (NEW…COMPLETED), bằng chứng, cổng COMPLETED
    chỉ khi kiểm chứng đạt. Tác vụ Dev là `kind="dev_task"`; trạng thái chạy chi tiết nằm ở `dev_runs` (một lượt chạy).
  - Cổng chính sách (`zero_trust.execute_with_hitl` -> `policy_engine.authorize`): kill switch, L0–L5, duyệt, audit.
  - Ranh giới provider (`provider.py`): service không biết HTTP / SSH / Ansible / OpenClaw.

Nguồn sự thật: hạ tầng + trạng thái thực thi = Master; dự án / tác vụ / chính sách / kết luận hoàn thành = VN-MateAI.
Mất liên lạc với Master KHÔNG kết luận COMPLETED/FAILED: lượt chạy chuyển UNKNOWN cho tới khi có bằng chứng.
"""
from __future__ import annotations

import asyncio
import json
import logging
import time
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Dict, List, Optional

from mateai.application.devfleet import models as m
from mateai.application.devfleet.provider import DevFleetProvider, FleetError
from mateai.application.devfleet.scheduler import rank_workers
from mateai.application.devfleet.verification import FAILED as V_FAILED
from mateai.application.devfleet.verification import PASSED as V_PASSED
from mateai.application.devfleet.verification import verify_result

logger = logging.getLogger(__name__)

MODES = ("disabled", "read_only", "controlled", "autonomous")
TASK_KIND = "dev_task"
_PRIORITY_WEIGHT = {"critical": 4, "high": 3, "medium": 2, "low": 1}


class FleetModeError(Exception):
    """Module tắt / chỉ đọc / không cấu hình. `code`: disabled | read_only | not_configured."""

    def __init__(self, message: str, code: str) -> None:
        super().__init__(message)
        self.code = code


def _db():
    from mateai.infrastructure.database.db_manager import db_manager
    return db_manager


def _ledger():
    from mateai.application.tasks import ledger
    return ledger


def _stamp(dt: Optional[datetime] = None) -> str:
    return (dt or datetime.now()).strftime("%Y-%m-%d %H:%M:%S")


def _age_s(stamp: Optional[str]) -> Optional[float]:
    if not stamp:
        return None
    try:
        return (datetime.now() - datetime.strptime(stamp, "%Y-%m-%d %H:%M:%S")).total_seconds()
    except ValueError:
        return None


def _jload(text: Any) -> Any:
    try:
        return json.loads(text) if text else None
    except (TypeError, ValueError):
        return None


class DevFleetService:
    def __init__(self, provider: Optional[DevFleetProvider] = None, config: Optional[Callable[[], Any]] = None) -> None:
        self._provider = provider
        self._config_getter = config
        self._snapshot: Optional[Dict[str, Any]] = None
        self._snapshot_at = 0.0
        self._refresh_lock = asyncio.Lock()
        self._poller: Optional["asyncio.Task[Any]"] = None

    # ── cấu hình / chế độ ───────────────────────────────────────────────────
    @property
    def cfg(self):
        if self._config_getter:
            return self._config_getter()
        from mateai.config import loader
        return loader.settings.dev_fleet

    @property
    def mode(self) -> str:
        c = self.cfg
        if not c.enabled or c.mode == "disabled":
            return "disabled"
        return c.mode if c.mode in MODES else "read_only"

    def _need_read(self) -> None:
        if self.mode == "disabled":
            raise FleetModeError("Module Dev Fleet đang TẮT (dev_fleet.enabled / mode).", "disabled")
        if not (self.cfg.endpoint or self._provider is not None):
            raise FleetModeError("Chưa khai báo `dev_fleet.endpoint` (địa chỉ Master Control API).", "not_configured")

    def _need_control(self) -> None:
        self._need_read()
        if self.mode not in ("controlled", "autonomous"):
            raise FleetModeError("Dev Fleet đang ở chế độ CHỈ ĐỌC — không giao / dừng / chạy lại tác vụ.", "read_only")

    def provider(self) -> DevFleetProvider:
        if self._provider is None:
            from mateai.infrastructure.connectors.dev_fleet_master import DevFleetMasterClient
            self._provider = DevFleetMasterClient.from_config(self.cfg)
        return self._provider

    def reset(self) -> None:
        """Quên provider + bộ đệm (sau khi đổi cấu hình)."""
        self._provider = None
        self._snapshot = None
        self._snapshot_at = 0.0

    # ── đọc trạng thái hạ tầng ──────────────────────────────────────────────
    async def snapshot(self, force: bool = False) -> Dict[str, Any]:
        """Ảnh chụp Master + worker + agent. Có bộ đệm ngắn; Master không tới được thì trả ảnh cũ nhưng worker bị hạ
        về UNKNOWN (không báo ONLINE khi không còn bằng chứng mới — prompt §13, §40–§41)."""
        self._need_read()
        c = self.cfg
        if not force and self._snapshot and (time.monotonic() - self._snapshot_at) < c.cache_ttl_s:
            return self._snapshot
        async with self._refresh_lock:
            if not force and self._snapshot and (time.monotonic() - self._snapshot_at) < c.cache_ttl_s:
                return self._snapshot
            observed = datetime.now(timezone.utc)
            try:
                prov = self.provider()
                health = await prov.health()
                major = str(health.get("api_version") or "").split(".")[0]
                if major != m.SUPPORTED_API_MAJOR:
                    raise FleetError(f"Master Control API phiên bản '{health.get('api_version')}' không tương thích "
                                     f"(bộ nối hiểu phiên bản {m.SUPPORTED_API_MAJOR}.x).", "incompatible")
                skew = timedelta(0)
                server_time = m.parse_time(health.get("server_time"))
                if server_time:
                    skew = server_time - observed
                raw_workers = await prov.list_workers()
                raw_agents = await prov.list_agents()
            except FleetError as exc:
                return self._degraded_snapshot(str(exc), exc.kind)
            except Exception as exc:  # noqa: BLE001
                logger.exception("[DevFleet] lỗi khi đọc Master")
                return self._degraded_snapshot(f"{type(exc).__name__}: {exc}", "unavailable")
            workers = [w for w in (m.normalise_worker(r, now=observed + skew, stale_after_s=c.stale_after_s,
                                                     offline_after_s=c.offline_after_s, disabled=c.disabled_workers)
                                   for r in raw_workers) if w]
            agents = [a for a in (m.normalise_agent(r) for r in raw_agents) if a]
            master = m.normalise_master(health)
            self._snapshot = {"reachable": True, "error": None, "error_kind": None, "master": master,
                              "workers": workers, "agents": agents, "observed_at": observed.isoformat(),
                              "api_version": health.get("api_version"), "clock_skew_s": round(skew.total_seconds(), 1)}
            self._snapshot_at = time.monotonic()
            return self._snapshot

    def _degraded_snapshot(self, error: str, kind: str) -> Dict[str, Any]:
        last = self._snapshot or {}
        workers = []
        for w in last.get("workers", []):
            workers.append({**w, "state": m.UNKNOWN if w["state"] != m.DISABLED else m.DISABLED, "freshness": m.STALE,
                            "note": "Master không liên lạc được — trạng thái lần trước không còn được bảo đảm"})
        master = {**(last.get("master") or {}), "health": m.MASTER_OFFLINE}
        self._snapshot = {"reachable": False, "error": error, "error_kind": kind, "master": master, "workers": workers,
                          "agents": last.get("agents", []), "observed_at": last.get("observed_at"),
                          "last_ok_at": last.get("last_ok_at") or last.get("observed_at"),
                          "api_version": last.get("api_version"), "clock_skew_s": last.get("clock_skew_s")}
        self._snapshot_at = time.monotonic()
        return self._snapshot

    async def workers(self) -> List[Dict[str, Any]]:
        return (await self.snapshot())["workers"]

    async def worker(self, worker_id: str) -> Optional[Dict[str, Any]]:
        return next((w for w in await self.workers() if w["worker_id"] == worker_id), None)

    async def agents(self) -> List[Dict[str, Any]]:
        return (await self.snapshot())["agents"]

    async def worker_metrics(self, worker_id: str) -> Dict[str, Any]:
        self._need_read()
        try:
            return await self.provider().get_metrics(worker_id)
        except FleetError as exc:
            return {"available": False, "error": str(exc)}

    async def git_status(self, worker_id: str, repository: str) -> Dict[str, Any]:
        self._need_read()
        try:
            return await self.provider().get_git_status(worker_id, repository)
        except FleetError as exc:
            return {"available": False, "error": str(exc)}

    async def status(self) -> Dict[str, Any]:
        """Tổng quan. Tắt -> không gọi mạng. Mọi số đếm lấy từ dữ liệu thật; không có -> None."""
        base = {"enabled": self.mode != "disabled", "mode": self.mode}
        if self.mode == "disabled":
            return {**base, "master": None, "workers": None, "tasks": None}
        try:
            snap = await self.snapshot()
        except FleetModeError as exc:
            return {**base, "configured": False, "error": str(exc), "master": None, "workers": None, "tasks": self._task_counts()}
        by_state: Dict[str, int] = {}
        for w in snap["workers"]:
            by_state[w["state"]] = by_state.get(w["state"], 0) + 1
        return {**base, "configured": True, "reachable": snap["reachable"], "error": snap["error"],
                "master": snap["master"], "api_version": snap.get("api_version"), "observed_at": snap["observed_at"],
                "last_ok_at": snap.get("last_ok_at"), "workers": {"total": len(snap["workers"]), "by_state": by_state},
                "agents": {"total": len(snap["agents"])}, "tasks": self._task_counts()}

    def _task_counts(self) -> Dict[str, Any]:
        runs = _db().dev_list("dev_runs", order_by="dispatched_at DESC", limit=1000)
        latest: Dict[str, Dict[str, Any]] = {}
        for r in runs:
            latest.setdefault(r["task_id"], r)
        counts: Dict[str, int] = {}
        for r in latest.values():
            counts[r["status"]] = counts.get(r["status"], 0) + 1
        return {"by_run_status": counts, "total": len(latest)}

    # ── dự án ───────────────────────────────────────────────────────────────
    def create_project(self, name: str, *, repository: str = "", description: str = "",
                       preferred_workers: Optional[List[str]] = None, created_by: str = "",
                       goal_id: Optional[str] = None) -> Dict[str, Any]:
        name = str(name or "").strip()
        if not name or len(name) > 120:
            raise m.SpecError("Dự án cần tên (tối đa 120 ký tự)")
        if goal_id and not _db().op_get_goal(goal_id):
            raise m.SpecError(f"Không có mục tiêu '{goal_id}' trong sổ mục tiêu")
        pid = f"PRJ-{uuid.uuid4().hex[:8]}"
        row = {"project_id": pid, "name": name, "repository": str(repository or "").strip() or None, "goal_id": goal_id,
               "description": str(description or "")[:1000],
               "preferred_workers": json.dumps(list(preferred_workers or [])), "status": "ACTIVE",
               "created_at": _stamp(), "created_by": created_by or None}
        _db().dev_insert("dev_projects", row)
        self._audit(created_by, "dev_fleet_project_create", "SUCCESS", {"project_id": pid, "name": name})
        return self._project_view(row)

    @staticmethod
    def _project_view(row: Dict[str, Any]) -> Dict[str, Any]:
        return {**row, "preferred_workers": _jload(row.get("preferred_workers")) or []}

    def projects(self) -> List[Dict[str, Any]]:
        return [self._project_view(r) for r in _db().dev_list("dev_projects", order_by="created_at DESC")]

    def project(self, project_id: str) -> Optional[Dict[str, Any]]:
        row = _db().dev_get("dev_projects", project_id)
        return self._project_view(row) if row else None

    def project_progress(self, project_id: str) -> Dict[str, Any]:
        """Tiến độ có trọng số theo ưu tiên (critical 4 · high 3 · medium 2 · low 1). Chỉ tác vụ COMPLETED *đã kiểm chứng*
        mới tính là xong; tác vụ đã huỷ không tính vào mẫu số. Không có tác vụ -> percent None (không bịa 0%)."""
        task_ids: List[str] = []
        for r in _db().dev_list("dev_runs", {"project_id": project_id}, order_by="dispatched_at ASC", limit=1000):
            if r["task_id"] not in task_ids:
                task_ids.append(r["task_id"])
        total_w = done_w = 0
        buckets: Dict[str, int] = {}
        unverified = 0
        for tid in task_ids:
            task = _ledger().get_task(tid)
            if not task:
                continue
            status = task["status"]
            buckets[status] = buckets.get(status, 0) + 1
            if status == "CANCELLED":
                continue
            spec = self._spec_of(tid)
            weight = _PRIORITY_WEIGHT.get((spec or {}).get("priority", "medium"), 2)
            total_w += weight
            if status == "COMPLETED" and task.get("verification_status") == "passed":
                done_w += weight
            elif status == "VERIFYING" and task.get("verification_status") == "unverified":
                unverified += 1
        return {"project_id": project_id, "tasks": len(task_ids), "by_status": buckets, "unverified_waiting_human": unverified,
                "percent": round(done_w / total_w * 100, 1) if total_w else None,
                "basis": "trọng số theo ưu tiên; chỉ tính tác vụ COMPLETED đã kiểm chứng; chưa tính phụ thuộc / đường găng"}

    # ── tác vụ ──────────────────────────────────────────────────────────────
    def _runs(self, task_id: str) -> List[Dict[str, Any]]:
        return _db().dev_list("dev_runs", {"task_id": task_id}, order_by="dispatched_at ASC", limit=100)

    def _spec_of(self, task_id: str) -> Optional[Dict[str, Any]]:
        runs = self._runs(task_id)
        return _jload(runs[-1]["spec_json"]) if runs else None

    def _event(self, kind: str, message: str, *, task_id: Optional[str] = None, run_id: Optional[str] = None,
               worker_id: Optional[str] = None) -> None:
        try:
            _db().dev_insert("dev_events", {"event_id": f"EVT-{uuid.uuid4().hex[:10]}", "ts": _stamp(), "kind": kind,
                                            "task_id": task_id, "run_id": run_id, "worker_id": worker_id,
                                            "message": str(message)[:300]})
        except Exception as exc:  # noqa: BLE001
            logger.warning("[DevFleet] không ghi được sự kiện: %s", exc)

    def events(self, limit: int = 100) -> List[Dict[str, Any]]:
        return _db().dev_list("dev_events", order_by="ts DESC", limit=limit)

    def _audit(self, actor: Optional[str], action: str, status: str, details: Dict[str, Any]) -> None:
        from mateai.application.security.zero_trust import log_security_audit
        log_security_audit(str(actor or "system"), action, "DEV_FLEET", status, details)

    def task(self, task_id: str) -> Optional[Dict[str, Any]]:
        task = _ledger().get_task(task_id)
        if not task or task.get("kind") != TASK_KIND:
            return None
        runs = self._runs(task_id)
        for r in runs:
            r["spec"] = _jload(r.pop("spec_json", None))
            r["result"] = _jload(r.pop("result_json", None))
        view = dict(task)
        view["runs"] = runs
        view["display_status"] = self._display_status(task, runs)
        return view

    @staticmethod
    def _display_status(task: Dict[str, Any], runs: List[Dict[str, Any]]) -> str:
        """COMPLETED_UNVERIFIED / WAITING_APPROVAL … cho người đọc; trạng thái gốc vẫn là của Task Ledger."""
        if task["status"] == "VERIFYING" and task.get("verification_status") == "unverified":
            return "COMPLETED_UNVERIFIED"
        if runs and runs[-1]["status"] == m.RUN_PENDING_APPROVAL:
            return "WAITING_APPROVAL"
        if runs and runs[-1]["status"] in (m.RUN_UNKNOWN, m.RUN_INTERRUPTED) and task["status"] == "EXECUTING":
            return "INTERRUPTED_UNKNOWN"
        return task["status"]

    def tasks(self, status: Optional[str] = None, limit: int = 100) -> List[Dict[str, Any]]:
        rows = _ledger().list_tasks(kind=TASK_KIND, status=status, limit=limit)
        return [{**r, "display_status": self._display_status(r, self._runs(r["task_id"]))} for r in rows]

    def _lease_keys(self, spec: m.TaskSpec, worker_id: str) -> List[str]:
        keys = []
        if spec.repository:
            keys.append(f"workspace:{worker_id}:{spec.repository}")
            if spec.branch:
                keys.append(f"branch:{spec.repository}:{spec.branch}")
        return keys

    async def plan(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        """Dry-run (prompt §63): máy được chọn, điểm, mức rủi ro, có cần duyệt không — KHÔNG ghi gì, KHÔNG gọi Master để chạy."""
        self._need_control()
        spec = m.parse_task_spec(payload)
        return await self._plan_for(spec)

    async def _plan_for(self, spec: m.TaskSpec, exclude_workers: Optional[List[str]] = None) -> Dict[str, Any]:
        snap = await self.snapshot()
        if not snap["reachable"]:
            return {"ok": False, "error": f"Master không liên lạc được: {snap['error']}", "candidates": [], "rejected": []}
        project = self.project(spec.project_id) if spec.project_id else None
        if spec.project_id and not project:
            raise m.SpecError(f"Không có dự án '{spec.project_id}'")
        active = {}
        locked: List[str] = []
        for r in _db().dev_list("dev_runs", {"status": list(m.RUN_ACTIVE)}, limit=500):
            if r.get("worker_id"):
                active[r["worker_id"]] = active.get(r["worker_id"], 0) + 1
        if spec.repository:
            now = _stamp()
            for lease in _db().dev_leases():
                if lease["lease_key"].startswith("workspace:") and lease["lease_key"].endswith(f":{spec.repository}") \
                        and lease["expires_at"] > now:
                    locked.append(lease["worker_id"])
        workers = [w for w in snap["workers"] if w["worker_id"] not in set(exclude_workers or [])]
        ranked = rank_workers(spec, workers, preferred=(project or {}).get("preferred_workers"),
                              active_runs=active, locked_workers=locked)
        top = ranked["candidates"][0] if ranked["candidates"] else None
        return {"ok": top is not None, "worker_id": top["worker_id"] if top else None, "score": top["score"] if top else None,
                "candidates": ranked["candidates"], "rejected": ranked["rejected"], "risk": spec.risk,
                "risk_level": spec.risk_level, "requires_approval": spec.risk_level >= 3,
                "error": None if top else "Không có worker phù hợp (xem `rejected`)"}

    async def create_task(self, payload: Dict[str, Any], *, requested_by: str, agent_id: Optional[str] = None,
                          idempotency_key: Optional[str] = None, check_rbac: bool = True) -> Dict[str, Any]:
        """Tạo tác vụ Dev -> chọn máy -> qua cổng chính sách (có thể chờ duyệt) -> giao cho Master."""
        self._need_control()
        spec = m.parse_task_spec(payload)
        if idempotency_key:                                  # giao trùng (client gửi lại) -> trả lượt đã có
            dup = _db().dev_list("dev_runs", {"idempotency_key": idempotency_key}, limit=1)
            if dup:
                return {"status": "duplicate", "task_id": dup[0]["task_id"], "run_id": dup[0]["run_id"],
                        "run_status": dup[0]["status"]}
        plan = await self._plan_for(spec)
        if not plan["ok"]:
            return {"status": "no_worker", "error": plan["error"], "rejected": plan["rejected"]}
        return await self._start(spec, plan["worker_id"], requested_by=requested_by, agent_id=agent_id,
                                 idempotency_key=idempotency_key, check_rbac=check_rbac, plan=plan)

    async def _start(self, spec: m.TaskSpec, worker_id: str, *, requested_by: str, agent_id: Optional[str],
                     idempotency_key: Optional[str], check_rbac: bool, plan: Optional[Dict[str, Any]] = None,
                     task_id: Optional[str] = None, attempt: int = 1) -> Dict[str, Any]:
        led = _ledger()
        run_id = f"RUN-{uuid.uuid4().hex[:10]}"
        key = idempotency_key or f"{task_id or run_id}:attempt-{attempt}"
        if task_id is None:
            task_id = led.open_task(spec.title, kind=TASK_KIND, created_by=requested_by, agent_id=agent_id,
                                    source="dev_fleet", risk=spec.risk_level, goal=spec.project_id,
                                    impact="medium" if spec.priority in ("high", "critical") else "low",
                                    urgency=spec.priority if spec.priority in ("low", "medium", "high", "critical") else "low",
                                    deadline=spec.deadline)
            if not task_id:
                return {"status": "error", "error": "Không mở được tác vụ trong sổ tác vụ"}
            led.transition(task_id, led.PLANNED)
        # thuê workspace / nhánh trước khi giao (prompt §22)
        leased: List[str] = []
        for lease_key in self._lease_keys(spec, worker_id):
            got = _db().dev_lease_acquire(lease_key, run_id, task_id, worker_id, self.cfg.lease_ttl_s)
            if not got:
                _db().dev_lease_release(task_id)
                led.transition(task_id, led.BLOCKED, result_summary=f"workspace/nhánh đang bị tác vụ khác thuê: {lease_key}")
                return {"status": "blocked", "task_id": task_id, "error": f"Workspace/nhánh đang bị thuê: {lease_key}"}
            leased.append(lease_key)
        _db().dev_insert("dev_runs", {"run_id": run_id, "task_id": task_id, "project_id": spec.project_id, "attempt": attempt,
                                      "worker_id": worker_id, "agent_id": None, "master_task_id": None,
                                      "idempotency_key": key, "status": m.RUN_PENDING_APPROVAL, "dispatched_at": _stamp(),
                                      "spec_json": json.dumps(spec.to_dict(), ensure_ascii=False)})
        self._event("task.created", f"{spec.title} → {worker_id}", task_id=task_id, run_id=run_id, worker_id=worker_id)

        async def _execute() -> Dict[str, Any]:          # phải là coroutine function: HITL chỉ await được loại này
            return await self._dispatch(task_id, run_id)

        from mateai.application.security.zero_trust import execute_with_hitl
        outcome = await execute_with_hitl(
            action_name="dev_fleet_dispatch",
            params={"task_id": task_id, "run_id": run_id, "worker_id": worker_id, "risk": spec.risk,
                    "repository": spec.repository, "branch": spec.branch},
            executor=_execute,
            requested_by=requested_by, agent_id=agent_id, check_rbac=check_rbac, risk_level=spec.risk_level,
            description=f"Giao tác vụ Dev '{spec.title}' cho {worker_id} (rủi ro {spec.risk}, repo {spec.repository or '-'})")
        base = {"task_id": task_id, "run_id": run_id, "worker_id": worker_id, "plan": plan}
        status = outcome.get("status")
        if status == "denied":
            _db().dev_update("dev_runs", run_id, {"status": m.RUN_CANCELLED, "finished_at": _stamp()})
            _db().dev_lease_release(task_id)
            led.transition(task_id, led.BLOCKED, result_summary=str(outcome.get("message"))[:300])
            self._audit(requested_by, "dev_fleet_dispatch", "REJECTED", {"task_id": task_id, "run_id": run_id,
                                                                         "worker_id": worker_id, "rule": outcome.get("rule")})
            return {**base, "status": "denied", "error": outcome.get("message")}
        if status == "awaiting_approval":
            led.transition(task_id, led.WAITING_AUTHORIZATION, approval_id=outcome.get("approval_id"))
            self._audit(requested_by, "dev_fleet_dispatch", "PENDING", {"task_id": task_id, "run_id": run_id,
                                                                        "worker_id": worker_id, "risk": spec.risk})
            return {**base, "status": "awaiting_approval", "approval_id": outcome.get("approval_id"),
                    "message": outcome.get("message")}
        result = outcome.get("result") or {}
        return {**base, "status": "dispatched" if result.get("ok") else "failed", **{k: v for k, v in result.items() if k != "ok"}}

    async def _dispatch(self, task_id: str, run_id: str) -> Dict[str, Any]:
        """Thân thực thi sau khi cổng chính sách cho phép (ngay, hoặc sau khi người duyệt). Kiểm lại mọi điều kiện:
        chế độ, máy còn đủ điều kiện — vì từ lúc xin duyệt có thể đã khác."""
        led = _ledger()
        run = _db().dev_get("dev_runs", run_id)
        if not run or run["status"] != m.RUN_PENDING_APPROVAL:
            return {"ok": False, "error": "Lượt chạy không còn ở trạng thái chờ giao (đã xử lý hoặc đã huỷ)."}
        spec_d = _jload(run["spec_json"]) or {}
        try:
            self._need_control()
            spec = m.parse_task_spec(spec_d)
            snap = await self.snapshot(force=True)
            worker = next((w for w in snap["workers"] if w["worker_id"] == run["worker_id"]), None)
            if not snap["reachable"] or worker is None:
                raise FleetError("Master không liên lạc được hoặc không còn thấy máy", "unavailable")
            check = rank_workers(spec, [worker], active_runs={})
            if not check["candidates"]:
                raise FleetError("Máy không còn đủ điều kiện: " + "; ".join(r["reason"] for r in check["rejected"]), "rejected")
        except (FleetModeError, FleetError, m.SpecError) as exc:
            _db().dev_update("dev_runs", run_id, {"status": m.RUN_FAILED, "finished_at": _stamp(),
                                                 "result_json": json.dumps({"error": str(exc)}, ensure_ascii=False)})
            _db().dev_lease_release(task_id)
            led.transition(task_id, led.BLOCKED, result_summary=f"không giao được: {exc}"[:300])
            self._event("task.dispatch_refused", str(exc), task_id=task_id, run_id=run_id, worker_id=run["worker_id"])
            return {"ok": False, "error": str(exc)}
        if (led.get_task(task_id) or {}).get("status") in ("PLANNED", "WAITING_AUTHORIZATION"):
            led.transition(task_id, led.AUTHORIZED)
        led.transition(task_id, led.EXECUTING)
        _db().dev_update("dev_runs", run_id, {"status": m.RUN_DISPATCHING})
        wire = {**spec_d, "task_id": task_id, "run_id": run_id, "attempt": run["attempt"],
                "idempotency_key": run["idempotency_key"]}
        try:
            reply = await self.provider().dispatch_task(wire, worker_id=run["worker_id"], idempotency_key=run["idempotency_key"])
        except FleetError as exc:
            if exc.kind in ("rejected", "not_found", "incompatible"):
                _db().dev_update("dev_runs", run_id, {"status": m.RUN_FAILED, "finished_at": _stamp(),
                                                     "result_json": json.dumps({"error": str(exc)}, ensure_ascii=False)})
                _db().dev_lease_release(task_id)
                led.transition(task_id, led.VERIFYING, verification_status="failed", result_summary=f"Master từ chối: {exc}"[:300])
                self._event("task.dispatch_rejected", str(exc), task_id=task_id, run_id=run_id, worker_id=run["worker_id"])
                return {"ok": False, "error": str(exc)}
            # mất liên lạc giữa chừng: KHÔNG biết Master đã nhận hay chưa -> UNKNOWN, chờ đối chiếu (không giao lại mù quáng)
            _db().dev_update("dev_runs", run_id, {"status": m.RUN_UNKNOWN})
            self._event("task.dispatch_unknown", str(exc), task_id=task_id, run_id=run_id, worker_id=run["worker_id"])
            self._audit(None, "dev_fleet_dispatch", "UNKNOWN", {"task_id": task_id, "run_id": run_id, "error": str(exc)})
            return {"ok": True, "run_status": m.RUN_UNKNOWN,
                    "warning": "Mất liên lạc với Master khi giao việc — chưa biết Master đã nhận chưa; sẽ đối chiếu ở lần đồng bộ."}
        run_status = self._run_status_from(reply.get("status")) or m.RUN_QUEUED
        _db().dev_update("dev_runs", run_id, {"status": run_status, "master_task_id": str(reply.get("task_id") or run_id),
                                             "last_progress_at": _stamp(), "agent_id": reply.get("agent_id")})
        led.add_evidence(task_id, source="dev_fleet.master", kind="FACT", summary=f"Master nhận lượt {run_id} cho {run['worker_id']} "
                         f"(trạng thái {run_status})", ref=run_id, verified=True)
        self._event("task.dispatched", f"{spec.title} → {run['worker_id']}", task_id=task_id, run_id=run_id,
                    worker_id=run["worker_id"])
        self._audit(None, "dev_fleet_dispatch", "SUCCESS", {"task_id": task_id, "run_id": run_id, "worker_id": run["worker_id"],
                                                            "risk": spec.risk, "idempotency_key": run["idempotency_key"]})
        return {"ok": True, "run_status": run_status}

    @staticmethod
    def _run_status_from(value: Any) -> Optional[str]:
        word = str(value or "").strip().upper()
        return {"QUEUED": m.RUN_QUEUED, "PENDING": m.RUN_QUEUED, "ACCEPTED": m.RUN_QUEUED, "RUNNING": m.RUN_RUNNING,
                "EXECUTING": m.RUN_RUNNING, "FINISHED": m.RUN_FINISHED, "COMPLETED": m.RUN_FINISHED,
                "SUCCEEDED": m.RUN_FINISHED, "SUCCESS": m.RUN_FINISHED, "FAILED": m.RUN_FAILED, "ERROR": m.RUN_FAILED,
                "CANCELLED": m.RUN_CANCELLED, "CANCELED": m.RUN_CANCELLED}.get(word)

    # ── theo dõi + kiểm chứng ───────────────────────────────────────────────
    async def sync_run(self, run_id: str) -> Dict[str, Any]:
        """Đối chiếu một lượt chạy với Master; kết thúc thì chạy kiểm chứng. Idempotent."""
        self._need_read()
        led = _ledger()
        run = _db().dev_get("dev_runs", run_id)
        if not run:
            return {"ok": False, "error": "Không có lượt chạy này"}
        if run["status"] in m.RUN_TERMINAL or run["status"] == m.RUN_PENDING_APPROVAL:
            return {"ok": True, "run_status": run["status"], "changed": False}
        master_id = run.get("master_task_id") or run_id
        try:
            remote = await self.provider().get_task_status(master_id)
        except FleetError as exc:
            if exc.kind == "not_found":
                # Master không biết lượt này: nếu ta chưa từng nhận ack -> an toàn để giao lại; ngược lại là mất dấu
                new = m.RUN_UNKNOWN
                if run["status"] != new:
                    _db().dev_update("dev_runs", run_id, {"status": new})
                    self._event("run.not_found", "Master không có lượt chạy này", task_id=run["task_id"], run_id=run_id,
                                worker_id=run["worker_id"])
                return {"ok": True, "run_status": new, "changed": run["status"] != new,
                        "note": "Master không biết lượt chạy này — có thể chưa được nhận; dùng retry để giao lại an toàn."}
            if run["status"] in (m.RUN_QUEUED, m.RUN_RUNNING, m.RUN_DISPATCHING):
                _db().dev_update("dev_runs", run_id, {"status": m.RUN_INTERRUPTED})
                self._event("run.interrupted", str(exc), task_id=run["task_id"], run_id=run_id, worker_id=run["worker_id"])
            return {"ok": False, "run_status": m.RUN_INTERRUPTED if run["status"] != m.RUN_UNKNOWN else m.RUN_UNKNOWN,
                    "error": str(exc)}
        new = self._run_status_from(remote.get("status")) or run["status"]
        fields: Dict[str, Any] = {}
        if new != run["status"]:
            fields["status"] = new
        if new in (m.RUN_QUEUED, m.RUN_RUNNING) and (self._progressed(run, remote, new) or not run.get("last_progress_at")):
            fields["last_progress_at"] = _stamp()
        if remote.get("agent_id") and remote.get("agent_id") != run.get("agent_id"):
            fields["agent_id"] = remote["agent_id"]
        fields["result_json"] = json.dumps(remote, ensure_ascii=False, default=str)     # ảnh chụp mới nhất của Master
        if new in (m.RUN_FINISHED, m.RUN_FAILED, m.RUN_CANCELLED):
            fields.update({"finished_at": _stamp(), "exit_code": remote.get("exit_code")})
        _db().dev_update("dev_runs", run_id, fields)
        if new == m.RUN_CANCELLED:
            led.transition(run["task_id"], led.CANCELLED, result_summary="Master xác nhận đã huỷ")
            _db().dev_lease_release(run["task_id"])
            self._event("task.cancelled", "Master xác nhận đã huỷ", task_id=run["task_id"], run_id=run_id, worker_id=run["worker_id"])
        elif new in (m.RUN_FINISHED, m.RUN_FAILED):
            await self._finish(run, remote, new)
        return {"ok": True, "run_status": new, "changed": new != run["status"]}

    @staticmethod
    def _progressed(run: Dict[str, Any], remote: Dict[str, Any], new_status: str) -> bool:
        """Có dấu hiệu tiến triển thật: đổi trạng thái, hoặc `progress` / `last_progress_at` / `log_lines` khác lần trước."""
        prev = _jload(run.get("result_json")) or {}
        return new_status != run["status"] or any(remote.get(k) != prev.get(k) for k in ("progress", "last_progress_at", "log_lines"))

    async def _finish(self, run: Dict[str, Any], remote: Dict[str, Any], run_status: str) -> None:
        led = _ledger()
        task_id = run["task_id"]
        task = led.get_task(task_id) or {}
        if task.get("status") in ("COMPLETED", "FAILED", "CANCELLED"):
            return
        spec_d = _jload(run["spec_json"]) or {}
        spec = m.parse_task_spec(spec_d)
        if task.get("status") == "EXECUTING":
            led.transition(task_id, led.VERIFYING)
        git = None
        if run_status == m.RUN_FINISHED and spec.require_commit and spec.repository:
            try:
                git = await self.provider().get_git_status(run["worker_id"], spec.repository)
            except FleetError as exc:
                logger.info("[DevFleet] không lấy được Git độc lập: %s", exc)
        verdict = verify_result(spec, {**remote, "status": remote.get("status") if run_status != m.RUN_FAILED else "FAILED"}, git)
        for c in verdict["checks"]:
            led.add_evidence(task_id, source="dev_fleet.verify", kind="FACT" if c["ok"] is not None else "INFERENCE",
                             summary=f"{c['name']}: {'PASS' if c['ok'] else 'FAIL' if c['ok'] is False else 'CHƯA RÕ'} — {c['detail']}",
                             ref=run["run_id"], verified=c["ok"] is True)
        summary = str(remote.get("summary") or "")[:300]
        if verdict["status"] == V_PASSED:
            led.transition(task_id, led.COMPLETED, verification_status="passed", result_summary=summary or "đã kiểm chứng")
            _db().dev_lease_release(task_id)
            self._event("task.completed", summary or "đã kiểm chứng", task_id=task_id, run_id=run["run_id"], worker_id=run["worker_id"])
        elif verdict["status"] == V_FAILED:
            led.transition(task_id, led.VERIFYING, verification_status="failed", result_summary=summary or "kiểm chứng trượt")
            if len(self._runs(task_id)) - 1 >= spec.max_retries:        # đã dùng hết số lần thử lại
                led.transition(task_id, led.FAILED, verification_status="failed")
                _db().dev_lease_release(task_id)
            self._event("task.failed", summary or "kiểm chứng trượt", task_id=task_id, run_id=run["run_id"], worker_id=run["worker_id"])
        else:
            led.transition(task_id, led.VERIFYING, verification_status="unverified",
                           result_summary=(summary + " — CHƯA kiểm chứng được, cần người xác nhận").strip())
            self._event("task.unverified", "thực thi xong nhưng chưa đủ bằng chứng", task_id=task_id, run_id=run["run_id"],
                        worker_id=run["worker_id"])
        self._audit(None, "dev_fleet_verify", verdict["status"].upper(), {"task_id": task_id, "run_id": run["run_id"],
                                                                          "checks": verdict["checks"]})

    async def sync_active(self) -> Dict[str, Any]:
        """Đối chiếu mọi lượt chạy đang hoạt động (poller / nút Đồng bộ). Master mất liên lạc -> UNKNOWN, không kết luận."""
        self._need_read()
        results = []
        for r in _db().dev_list("dev_runs", {"status": [s for s in m.RUN_ACTIVE if s != m.RUN_PENDING_APPROVAL]}, limit=500):
            try:
                results.append({"run_id": r["run_id"], **await self.sync_run(r["run_id"])})
            except Exception as exc:  # noqa: BLE001
                logger.exception("[DevFleet] sync_run lỗi")
                results.append({"run_id": r["run_id"], "ok": False, "error": str(exc)})
        return {"synced": len(results), "results": results, "stuck": self.stuck()}

    def stuck(self) -> List[Dict[str, Any]]:
        """Lượt chạy treo: không tiến triển quá `stuck_after_s`, hoặc máy đã mất (OFFLINE/UNKNOWN trong ảnh chụp mới nhất)."""
        limit = float(self.cfg.stuck_after_s)
        by_worker = {w["worker_id"]: w for w in (self._snapshot or {}).get("workers", [])}
        out = []
        for r in _db().dev_list("dev_runs", {"status": [m.RUN_QUEUED, m.RUN_RUNNING, m.RUN_UNKNOWN, m.RUN_INTERRUPTED]}, limit=500):
            age = _age_s(r.get("last_progress_at") or r["dispatched_at"])
            reasons = []
            if age is not None and age > limit:
                reasons.append(f"không có tiến triển {int(age)} s (ngưỡng {int(limit)} s)")
            w = by_worker.get(r.get("worker_id"))
            if w and w["state"] in (m.OFFLINE, m.UNKNOWN):
                reasons.append(f"máy {r['worker_id']} đang {w['state']}")
            if r["status"] in (m.RUN_UNKNOWN, m.RUN_INTERRUPTED):
                reasons.append(f"lượt chạy {r['status']} — chưa có bằng chứng về kết quả")
            if reasons:
                out.append({"task_id": r["task_id"], "run_id": r["run_id"], "worker_id": r.get("worker_id"),
                            "status": r["status"], "reasons": reasons, "suggested": "inspect → retry/reassign → escalate"})
        return out

    # ── thao tác điều khiển ─────────────────────────────────────────────────
    async def cancel_task(self, task_id: str, *, requested_by: str, agent_id: Optional[str] = None,
                          check_rbac: bool = True) -> Dict[str, Any]:
        self._need_control()
        task = _ledger().get_task(task_id)
        if not task or task.get("kind") != TASK_KIND:
            return {"status": "not_found", "error": "Không có tác vụ Dev này"}
        if task["status"] in ("COMPLETED", "FAILED", "CANCELLED"):
            return {"status": "noop", "error": f"Tác vụ đã ở trạng thái cuối ({task['status']})"}
        runs = [r for r in self._runs(task_id) if r["status"] in m.RUN_ACTIVE]
        run = runs[-1] if runs else None

        async def _do() -> Dict[str, Any]:
            led = _ledger()
            confirmed = True
            if run and run["status"] != m.RUN_PENDING_APPROVAL:
                try:
                    await self.provider().cancel_task(run.get("master_task_id") or run["run_id"],
                                                      idempotency_key=f"cancel:{run['run_id']}")
                except FleetError as exc:
                    if exc.kind != "not_found":
                        return {"ok": False, "error": f"Master không xác nhận việc huỷ: {exc}"}
            if run:
                _db().dev_update("dev_runs", run["run_id"], {"status": m.RUN_CANCELLED, "finished_at": _stamp()})
            _db().dev_lease_release(task_id)
            led.transition(task_id, led.CANCELLED, result_summary=f"huỷ bởi {requested_by}")
            self._event("task.cancelled", f"huỷ bởi {requested_by}", task_id=task_id, run_id=(run or {}).get("run_id"),
                        worker_id=(run or {}).get("worker_id"))
            self._audit(requested_by, "dev_fleet_cancel", "SUCCESS", {"task_id": task_id, "run_id": (run or {}).get("run_id")})
            return {"ok": confirmed}

        from mateai.application.security.zero_trust import execute_with_hitl
        out = await execute_with_hitl("dev_fleet_cancel", {"task_id": task_id}, _do, requested_by=requested_by,
                                      agent_id=agent_id, check_rbac=check_rbac, risk_level=2,
                                      description=f"Huỷ tác vụ Dev {task_id}")
        if out.get("status") == "denied":
            return {"status": "denied", "error": out.get("message")}
        if out.get("status") == "awaiting_approval":
            return {"status": "awaiting_approval", "approval_id": out.get("approval_id")}
        res = out.get("result") or {}
        return {"status": "cancelled" if res.get("ok") else "failed", **{k: v for k, v in res.items() if k != "ok"}}

    async def retry_task(self, task_id: str, *, requested_by: str, agent_id: Optional[str] = None,
                         worker_id: Optional[str] = None, check_rbac: bool = True) -> Dict[str, Any]:
        """Chạy lại / chuyển máy. Chỉ khi lượt trước ĐÃ kết thúc chắc chắn (hoặc Master xác nhận không có) — không giao
        trùng một việc có tác dụng phụ (prompt §42–§44)."""
        self._need_control()
        led = _ledger()
        task = led.get_task(task_id)
        if not task or task.get("kind") != TASK_KIND:
            return {"status": "not_found", "error": "Không có tác vụ Dev này"}
        if task["status"] in ("COMPLETED", "CANCELLED", "FAILED"):
            return {"status": "noop", "error": f"Tác vụ đã ở trạng thái cuối ({task['status']}) — hãy tạo tác vụ mới"}
        runs = self._runs(task_id)
        last = runs[-1] if runs else None
        if not last:
            return {"status": "error", "error": "Tác vụ chưa có lượt chạy nào"}
        spec = m.parse_task_spec(_jload(last["spec_json"]) or {})
        attempts = len(runs)
        if attempts > spec.max_retries:
            led.transition(task_id, led.ESCALATED, result_summary="vượt số lần thử lại cho phép")
            return {"status": "escalated", "error": f"Đã chạy {attempts} lần (tối đa {spec.max_retries} lần thử lại) — chuyển người xử lý"}
        if last["status"] in (m.RUN_QUEUED, m.RUN_RUNNING, m.RUN_DISPATCHING, m.RUN_UNKNOWN, m.RUN_INTERRUPTED):
            # phải chắc lượt cũ đã dừng: huỷ ở Master; Master không biết lượt đó (404) cũng chấp nhận
            try:
                await self.provider().cancel_task(last.get("master_task_id") or last["run_id"],
                                                  idempotency_key=f"cancel:{last['run_id']}")
            except FleetError as exc:
                if exc.kind != "not_found":
                    return {"status": "refused", "error": f"Không chắc lượt trước đã dừng ({exc}) — không giao lại để tránh chạy trùng"}
            _db().dev_update("dev_runs", last["run_id"], {"status": m.RUN_CANCELLED, "finished_at": _stamp()})
        _db().dev_lease_release(task_id)
        if worker_id:
            spec = m.TaskSpec(**{**spec.to_dict(), "worker_id": worker_id})
        plan = await self._plan_for(spec)
        if not plan["ok"]:
            return {"status": "no_worker", "error": plan["error"], "rejected": plan["rejected"]}
        if task["status"] in ("VERIFYING", "ESCALATED", "BLOCKED"):
            led.transition(task_id, led.EXECUTING, verification_status=None)
        out = await self._start(spec, plan["worker_id"], requested_by=requested_by, agent_id=agent_id, idempotency_key=None,
                                check_rbac=check_rbac, plan=plan, task_id=task_id, attempt=attempts + 1)
        return out

    async def set_worker_disabled(self, worker_id: str, disabled: bool, *, actor: str, reason: str = "",
                                  mask: Optional[Callable[[Any], Any]] = None) -> Dict[str, Any]:
        """Kill switch theo máy (prompt §65). Lưu vào cấu hình có lịch sử; hiệu lực ngay ở lần đọc tiếp theo."""
        current = set(self.cfg.disabled_workers)
        (current.add if disabled else current.discard)(worker_id)
        self._save_config(actor, {"disabled_workers": sorted(current)}, reason or f"{'tắt' if disabled else 'bật'} worker {worker_id}", mask)
        self._audit(actor, "dev_fleet_worker_disable" if disabled else "dev_fleet_worker_enable", "SUCCESS", {"worker_id": worker_id})
        return {"worker_id": worker_id, "disabled": disabled, "disabled_workers": sorted(current)}

    async def set_mode(self, mode: str, *, actor: str, reason: str = "", mask: Optional[Callable[[Any], Any]] = None) -> Dict[str, Any]:
        """Đổi chế độ (disabled / read_only / controlled / autonomous) — cũng là công tắc FLEET OFF."""
        if mode not in MODES:
            raise m.SpecError(f"Chế độ phải thuộc {', '.join(MODES)}")
        self._save_config(actor, {"mode": mode, "enabled": mode != "disabled" or self.cfg.enabled}, reason or f"chế độ → {mode}", mask)
        self._audit(actor, "dev_fleet_mode_change", "SUCCESS", {"mode": mode, "reason": reason})
        return {"mode": self.mode}

    def _save_config(self, actor: str, updates: Dict[str, Any], reason: str, mask: Optional[Callable[[Any], Any]]) -> None:
        from mateai.application.administration import config_governance as gov
        from mateai.config.loader import DevFleetConfig

        def _apply(cfg: Dict[str, Any]) -> None:
            section = {**dict(cfg.get("dev_fleet") or {}), **updates}      # chỉ phần đã đổi — không ghi lại khoá lấy từ môi trường
            DevFleetConfig(**section)                         # kiểm kiểu trước khi ghi
            cfg["dev_fleet"] = section
        gov.save_config(actor, _apply, f"Dev Fleet: {reason}", mask, audit=False)
        self.reset()

    # ── cấu hình + thử kết nối (màn hình Dev Fleet) ─────────────────────────
    def config_view(self) -> Dict[str, Any]:
        """Cấu hình để hiển thị. Token KHÔNG BAO GIỜ trả về — chỉ cờ `has_token`."""
        import os
        c = self.cfg
        return {"enabled": c.enabled, "mode": self.mode, "endpoint": c.endpoint, "has_token": bool(c.api_token),
                "token_from_env": bool(os.environ.get("VNMATEAI_DEV_FLEET_TOKEN", "").strip()),
                "tls_verify": c.tls_verify, "ca_bundle": c.ca_bundle, "timeout_s": c.timeout_s,
                "stale_after_s": c.stale_after_s, "offline_after_s": c.offline_after_s,
                "disabled_workers": list(c.disabled_workers)}

    @staticmethod
    def _check_endpoint(endpoint: str) -> str:
        from urllib.parse import urlsplit
        text = str(endpoint or "").strip().rstrip("/")
        parts = urlsplit(text)
        if parts.scheme not in ("https", "http") or not parts.hostname:
            raise m.SpecError("Địa chỉ Master phải dạng https://host[:cổng]")
        if parts.hostname in ("169.254.169.254", "metadata.google.internal") or parts.username or parts.path.strip("/"):
            raise m.SpecError("Địa chỉ Master không hợp lệ (không kèm tài khoản / đường dẫn; không phải endpoint metadata)")
        return text

    def save_settings(self, actor: str, values: Dict[str, Any], *, mask: Optional[Callable[[Any], Any]] = None) -> Dict[str, Any]:
        """Lưu cấu hình có lịch sử. `api_token` rỗng/thiếu = GIỮ token đang lưu. Chế độ khác `disabled` -> bật module."""
        updates: Dict[str, Any] = {}
        if "endpoint" in values:
            updates["endpoint"] = self._check_endpoint(values["endpoint"]) if str(values["endpoint"] or "").strip() else ""
        token = str(values.get("api_token") or "").strip()
        if token:
            updates["api_token"] = token
        if "tls_verify" in values:
            updates["tls_verify"] = bool(values["tls_verify"])
        if "ca_bundle" in values:
            ca = str(values["ca_bundle"] or "").strip()
            if len(ca) > 260 or any(ch in ca for ch in "\r\n\x00"):
                raise m.SpecError("ca_bundle không hợp lệ (đường dẫn tệp, tối đa 260 ký tự)")
            updates["ca_bundle"] = ca
        if "timeout_s" in values:
            try:
                updates["timeout_s"] = float(values["timeout_s"])
            except (TypeError, ValueError):
                raise m.SpecError("timeout_s phải là số")
        if "mode" in values:
            mode = str(values["mode"] or "")
            if mode not in MODES:
                raise m.SpecError(f"Chế độ phải thuộc {', '.join(MODES)}")
            updates.update({"mode": mode, "enabled": mode != "disabled"})
        if not updates:
            raise m.SpecError("Không có thay đổi nào")
        if updates.get("enabled") and not (updates.get("endpoint") or self.cfg.endpoint):
            raise m.SpecError("Cần điền địa chỉ Master trước khi bật module")
        self._save_config(actor, updates, "cập nhật cấu hình", mask)
        self._audit(actor, "dev_fleet_config_change", "SUCCESS", {"keys": sorted(k for k in updates if k != "api_token"),
                                                                  "token_changed": "api_token" in updates})
        return self.config_view()

    async def test_connection(self, values: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """Thử kết nối THẬT tới Master bằng giá trị đang nhập (hoặc đã lưu). Không ghi gì, không cần bật module."""
        from mateai.infrastructure.connectors.dev_fleet_master import DevFleetMasterClient
        values = values or {}
        c = self.cfg
        try:
            endpoint = self._check_endpoint(values.get("endpoint") or c.endpoint)
        except m.SpecError as exc:
            return {"ok": False, "error": str(exc), "kind": "rejected"}
        token = str(values.get("api_token") or "").strip() or c.api_token
        client = DevFleetMasterClient(endpoint, token, tls_verify=bool(values.get("tls_verify", c.tls_verify)),
                                      ca_bundle=str(values.get("ca_bundle", c.ca_bundle) or ""), timeout_s=c.timeout_s, retries=0)
        t0 = time.monotonic()
        try:
            health = await client.health()
            workers = await client.list_workers()
        except FleetError as exc:
            return {"ok": False, "error": str(exc), "kind": exc.kind}
        version = str(health.get("api_version") or "")
        compatible = version.split(".")[0] == m.SUPPORTED_API_MAJOR
        return {"ok": compatible, "api_version": version or None, "compatible": compatible,
                "error": None if compatible else f"Master Control API {version or 'không rõ phiên bản'} không tương thích "
                                                 f"(cần {m.SUPPORTED_API_MAJOR}.x)",
                "kind": None if compatible else "incompatible", "master": m.normalise_master(health),
                "workers": len(workers), "latency_ms": round((time.monotonic() - t0) * 1000, 1)}

    # ── báo cáo điều hành ───────────────────────────────────────────────────
    async def briefing(self) -> Dict[str, Any]:
        """Toàn bộ số liệu lấy từ dữ liệu thật (Master + sổ tác vụ); không có thì None."""
        st = await self.status()
        stuck = self.stuck() if self.mode != "disabled" else []
        waiting = [t for t in self.tasks(limit=200) if t["display_status"] in ("WAITING_APPROVAL", "COMPLETED_UNVERIFIED")]
        workers = (await self.workers()) if st.get("configured") else []
        offline = [w["worker_id"] for w in workers if w["state"] in (m.OFFLINE, m.UNKNOWN)]
        recommended = []
        for s in stuck:
            recommended.append(f"Kiểm tra lượt chạy {s['run_id']} ({s['task_id']}): {'; '.join(s['reasons'])}")
        for w in offline:
            recommended.append(f"Kiểm tra máy {w} (không còn bằng chứng là đang chạy)")
        for t in waiting:
            recommended.append(f"{t['task_id']} đang {t['display_status']}")
        return {"generated_at": _stamp(), "status": st, "stuck": stuck, "needs_attention": waiting,
                "offline_workers": offline, "recommended": recommended}

    # ── vòng đồng bộ nền (chỉ khi module bật) ───────────────────────────────
    def start_poller(self, interval_s: float = 20.0) -> None:
        if self._poller and not self._poller.done():
            return

        async def _loop() -> None:
            while True:
                try:
                    if self.mode != "disabled" and (self.cfg.endpoint or self._provider):
                        await self.snapshot(force=True)
                        await self.sync_active()
                except asyncio.CancelledError:
                    raise
                except Exception:  # noqa: BLE001
                    logger.exception("[DevFleet] poller lỗi")
                await asyncio.sleep(interval_s)
        self._poller = asyncio.get_running_loop().create_task(_loop(), name="dev-fleet-poller")

    def stop_poller(self) -> None:
        if self._poller:
            self._poller.cancel()
            self._poller = None

    stop = stop_poller            # tên chung của các dịch vụ nền khi tắt máy chủ


dev_fleet = DevFleetService()
