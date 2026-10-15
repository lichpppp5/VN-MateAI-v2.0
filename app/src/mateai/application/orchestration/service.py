# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 Dương Thanh Lịch — VN-MateAI. See LICENSE and NOTICE.
"""
mateai/application/orchestration/service.py
===========================================
Lõi điều phối: tạo phiếu → (duyệt) → hàng đợi → giao cho agent → nhận phiếu báo cáo → thẩm định độc lập → xong / thử lại /
cần người. Mỗi phiếu là MỘT tác vụ `work_order` trong sổ tác vụ — dùng chung máy trạng thái, bằng chứng và luật "chỉ COMPLETED
khi kiểm chứng đạt". Xem docs/integrations/orchestration.md.

Trạng thái phiếu (cột `status` của orch_orders)                     → sổ tác vụ
  PLANNED            đã được phép, chờ chỗ trống (hàng đợi lưu CSDL)  PLANNED / AUTHORIZED
  WAITING_APPROVAL   rủi ro ≥ 3: chờ người có quyền duyệt              WAITING_AUTHORIZATION
  DISPATCHED         agent đang làm                                    EXECUTING
  REVIEWING          đang thẩm định độc lập                            VERIFYING
  COMPLETED          thẩm định đạt                                     COMPLETED (verification passed)
  NEEDS_CONFIRMATION xong nhưng không tự kiểm được → người xác nhận    ESCALATED
  FAILED / BLOCKED / CANCELLED / INTERRUPTED                           FAILED / BLOCKED / CANCELLED / ESCALATED

Ổn định:
  - Giới hạn bằng MÃ (không bằng prompt): tối đa MAX_RUNNING phiếu chạy cùng lúc, `max_concurrent` mỗi agent, MAX_OPEN_PER_REQUESTER
    phiếu mở mỗi người, MAX_OPEN_TOTAL toàn hệ thống, `max_attempts` mỗi phiếu, thời hạn mỗi lượt.
  - Hàng đợi nằm trong CSDL: khởi động lại không mất phiếu chưa giao; phiếu đang giao dở → INTERRUPTED (không tự chạy lại vì
    agent có thể đã làm một phần).
  - Phiếu duyệt bị từ chối / hết hạn được đối soát định kỳ → CANCELLED (không kẹt mãi ở chờ duyệt).
  - Thất bại tạm (agent lỗi mạng, hết giờ, không đạt thẩm định) → thử lại, ưu tiên agent KHÁC có cùng năng lực.
"""
from __future__ import annotations

import asyncio
import json
import logging
import uuid
from datetime import datetime
from typing import Any, Dict, List, Optional

from mateai.application.orchestration import contracts as C
from mateai.application.orchestration import registry as R
from mateai.application.orchestration import verify as V

logger = logging.getLogger(__name__)

KIND = "work_order"
AGENT_ID = V.AGENT_ID
TABLE = "orch_orders"

PLANNED, WAITING_APPROVAL, DISPATCHED, REVIEWING = "PLANNED", "WAITING_APPROVAL", "DISPATCHED", "REVIEWING"
COMPLETED, NEEDS_CONFIRMATION, FAILED = "COMPLETED", "NEEDS_CONFIRMATION", "FAILED"
BLOCKED, CANCELLED, INTERRUPTED = "BLOCKED", "CANCELLED", "INTERRUPTED"
FINAL = (COMPLETED, FAILED, CANCELLED)
OPEN = R.OPEN_STATES

MAX_RUNNING = 8
MAX_OPEN_TOTAL = 50
MAX_OPEN_PER_REQUESTER = 20
APPROVAL_RISK = 3
PUMP_INTERVAL_S = 30.0
DEVFLEET_POLL_S = 15.0
#: Thời gian dư trên thời hạn của phiếu trước khi cắt lượt giao (agent ngoài có timeout riêng).
DEADLINE_GRACE_S = 30.0
RETRY_PAUSE_S = 2.0

_tasks: Dict[str, "asyncio.Task[None]"] = {}
#: Agent đang giữ chỗ của từng lượt chạy (đổi khi thử lại trên agent khác).
_task_exec: Dict[str, Optional[str]] = {}


class OrderError(ValueError):
    """Lỗi người dùng nhìn thấy được (HTTP 422 / 409 / 429)."""

    def __init__(self, message: str, code: str = "invalid") -> None:
        super().__init__(message)
        self.code = code


class _AttemptError(Exception):
    """Lượt giao thất bại do agent / mạng (không phải do phiếu) — được thử lại."""


def _db():
    from mateai.infrastructure.database.db_manager import db_manager
    return db_manager


def _ledger():
    from mateai.application.tasks import ledger
    return ledger


def _now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _jl(text: Any, default: Any) -> Any:
    try:
        return json.loads(text) if text else default
    except (TypeError, ValueError):
        return default


def _dump(obj: Any, limit: int = 200_000) -> str:
    return json.dumps(obj, ensure_ascii=False, default=str)[:limit]


def _audit(actor: str, action: str, details: Dict[str, Any], status: str = "SUCCESS") -> None:
    try:
        from mateai.application.security.zero_trust import log_security_audit
        log_security_audit(str(actor or "system"), action, "ORCHESTRATION", status, details)
    except Exception:  # noqa: BLE001
        pass


def _history(order_id: str, event: str, **data: Any) -> None:
    row = _db().dev_get(TABLE, order_id) or {}
    hist = _jl(row.get("history_json"), [])
    hist.append({"at": _now(), "event": event, **data})
    _db().dev_update(TABLE, order_id, {"history_json": _dump(hist[-100:]), "updated_at": _now()})


def _set(order_id: str, **fields: Any) -> None:
    _db().dev_update(TABLE, order_id, {**fields, "updated_at": _now()})


def _ledger_move(task_id: Optional[str], *states: str, **fields: Any) -> None:
    """Đi qua chuỗi trạng thái của sổ tác vụ; bỏ qua bước đã đứng sẵn. Lỗi sổ không làm hỏng phiếu (chỉ log)."""
    if not task_id:
        return
    led = _ledger()
    for i, st in enumerate(states):
        try:
            led.transition(task_id, st, **(fields if i == len(states) - 1 else {}))
        except Exception as exc:  # noqa: BLE001
            logger.warning("[Orchestration] sổ tác vụ %s → %s: %s", task_id, st, exc)


def _kill_switch() -> bool:
    try:
        from mateai.config.loader import settings
        return bool(settings.autonomy.kill_switch)
    except Exception:  # noqa: BLE001
        return False


# ── tạo phiếu ───────────────────────────────────────────────────────────────

def preview(payload: Dict[str, Any]) -> Dict[str, Any]:
    """Chạy thử: phiếu hợp lệ không, sẽ giao cho ai, có cần duyệt không — không tạo gì."""
    order = C.parse_order(payload)
    routing = R.route(order)
    ex = R.get(routing["executor_id"]) if routing["executor_id"] else None
    return {"order": order.to_dict(), "routing": routing,
            "needs_approval": bool(ex) and ex["kind"] != "devfleet" and order.risk >= APPROVAL_RISK,
            "message": C.render_message(order, "XEM-TRUOC")}


async def create(payload: Dict[str, Any], *, requested_by: str, channel: str = "portal",
                 idempotency_key: Optional[str] = None) -> Dict[str, Any]:
    if _kill_switch():
        raise OrderError("Công tắc dừng khẩn cấp đang bật: không giao việc cho agent.", "kill_switch")
    order = C.parse_order(payload)
    if order.risk >= 5:
        raise OrderError("Việc rủi ro 5 (L5) không giao cho agent nào — chỉ người thực hiện.", "l5")
    key = str(idempotency_key or "").strip()[:120] or None
    if key:
        dup = _db().dev_list(TABLE, {"idempotency_key": key}, limit=1)
        if dup:
            return {**view(dup[0]), "duplicate": True}
    open_rows = _db().dev_list(TABLE, {"status": list(OPEN)}, limit=1000)
    if len(open_rows) >= MAX_OPEN_TOTAL:
        raise OrderError(f"Đang có {len(open_rows)} phiếu chưa xong (tối đa {MAX_OPEN_TOTAL}) — chờ bớt rồi giao tiếp.", "busy")
    if sum(1 for r in open_rows if r["requested_by"] == requested_by) >= MAX_OPEN_PER_REQUESTER:
        raise OrderError(f"Bạn đang có {MAX_OPEN_PER_REQUESTER} phiếu chưa xong — chờ bớt rồi giao tiếp.", "busy")
    routing = R.route(order)
    if not routing["executor_id"]:
        raise OrderError(routing["error"], "no_executor")
    ex = R.get(routing["executor_id"])
    order_id = f"WO-{uuid.uuid4().hex[:10]}"
    led = _ledger()
    task_id = led.open_task(f"Phiếu việc: {order.objective}"[:300], kind=KIND, created_by=requested_by, agent_id=AGENT_ID,
                            channel=channel, source=f"orch:{ex['executor_id']}", risk=order.risk, goal=order.capability)
    now = _now()
    _db().dev_insert(TABLE, {
        "order_id": order_id, "task_id": task_id, "executor_id": ex["executor_id"], "capability": order.capability,
        "status": PLANNED, "attempt": 0, "risk": order.risk, "requested_by": requested_by, "channel": channel,
        "idempotency_key": key, "spec_json": _dump(order.to_dict()), "routing_json": _dump(routing),
        "history_json": _dump([{"at": now, "event": "created", "by": requested_by, "executor": ex["executor_id"],
                                "reason": routing.get("reason")}]),
        "created_at": now, "updated_at": now,
    })
    _ledger_move(task_id, led.PLANNED)
    _audit(requested_by, "orch_order_create", {"order_id": order_id, "executor_id": ex["executor_id"], "capability": order.capability,
                                               "risk": order.risk})
    # Dev Fleet có cổng duyệt riêng (rủi ro medium trở lên) — không hỏi duyệt hai lần.
    if order.risk >= APPROVAL_RISK and ex["kind"] != "devfleet":
        _request_approval(order_id, order, ex, requested_by)
    else:
        pump()
    return view(_db().dev_get(TABLE, order_id))  # type: ignore[arg-type]


def _request_approval(order_id: str, order: C.WorkOrder, ex: Dict[str, Any], requested_by: str) -> None:
    from mateai.application.security.zero_trust import hitl_manager
    row = _db().dev_get(TABLE, order_id) or {}
    desc = (f"Giao việc cho agent «{ex['name']}» ({ex['kind']}, năng lực {order.capability}): {order.objective[:300]} "
            f"— sản phẩm: {'; '.join(order.deliverables)[:300]}")
    req = hitl_manager.request_approval(action_name=f"work_order:{ex['executor_id']}", params={"order_id": order_id},
                                        requested_by=requested_by, description=desc, kind=KIND, risk_level=order.risk,
                                        context={"order_id": order_id, "op_task_id": row.get("task_id"),
                                                 "configured_by": [ex.get("created_by")] if ex.get("created_by") else []})
    _set(order_id, status=WAITING_APPROVAL, approval_id=req.get("id"))
    _ledger_move(row.get("task_id"), _ledger().WAITING_AUTHORIZATION, approval_id=req.get("id"))
    _history(order_id, "approval_requested", approval_id=req.get("id"))


async def execute_approved_order(item: Dict[str, Any]) -> Dict[str, Any]:
    """Executor HITL (kind work_order): người có quyền đã duyệt → vào hàng đợi."""
    order_id = str((item.get("params") or {}).get("order_id") or "")
    row = _db().dev_get(TABLE, order_id)
    if not row or row["status"] != WAITING_APPROVAL:
        return {"success": False, "error": "Phiếu không còn chờ duyệt (đã chạy, huỷ hoặc không tồn tại)"}
    by = str(item.get("reviewed_by") or "?")
    _set(order_id, status=PLANNED)
    _ledger_move(row.get("task_id"), _ledger().AUTHORIZED)
    _history(order_id, "approved", by=by)
    _audit(by, "orch_order_approved", {"order_id": order_id})
    pump()
    return {"success": True, "status": "queued", "order_id": order_id}


# ── hàng đợi ────────────────────────────────────────────────────────────────

def pump() -> int:
    """Giao các phiếu PLANNED khi còn chỗ (toàn hệ thống MAX_RUNNING + `max_concurrent` từng agent). Gọi nhiều lần vẫn an toàn.
    Số đang chạy đếm theo lượt chạy THẬT trong tiến trình (`_tasks`) — lượt vừa khởi tạo chưa kịp ghi DISPATCHED vẫn được tính."""
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return 0                                         # ngoài event loop (vd. script) — vòng nền sẽ giao sau
    queued = _db().dev_list(TABLE, {"status": PLANNED}, order_by="created_at ASC", limit=200)
    started = 0
    for row in queued:
        oid = row["order_id"]
        if oid in _tasks:
            continue
        if len(_tasks) >= MAX_RUNNING:
            break
        eid = row.get("executor_id")
        ex = R.get(eid or "")
        limit = int((ex or {}).get("max_concurrent") or 1)
        if sum(1 for o in _tasks if _task_exec.get(o) == eid) >= limit:
            continue
        task = loop.create_task(_run(oid), name=f"work-order-{oid}")
        _tasks[oid] = task
        _task_exec[oid] = eid
        task.add_done_callback(lambda _t, o=oid: _on_done(o))
        started += 1
    return started


def _on_done(order_id: str) -> None:
    """Lượt chạy kết thúc hẳn → nhả chỗ rồi giao ngay phiếu đang chờ (không đợi vòng nền 30 s)."""
    _tasks.pop(order_id, None)
    _task_exec.pop(order_id, None)
    try:
        asyncio.get_running_loop().call_soon(pump)
    except RuntimeError:
        pass


async def wait(order_id: str, timeout: float = 60.0) -> Dict[str, Any]:
    """Chờ lượt chạy hiện tại xong (dùng cho test / công cụ đồng bộ)."""
    t = _tasks.get(order_id)
    if t:
        await asyncio.wait_for(asyncio.shield(t), timeout)
    return get(order_id) or {}


# ── chạy một phiếu ──────────────────────────────────────────────────────────

async def _run(order_id: str) -> None:
    row = _db().dev_get(TABLE, order_id)
    if not row or row["status"] != PLANNED:
        return
    order = C.order_from_dict(_jl(row["spec_json"], {}))
    tried: List[str] = [h.get("executor") for h in _jl(row.get("history_json"), []) if h.get("event") == "attempt_failed"]
    while True:
        row = _db().dev_get(TABLE, order_id) or {}
        if row.get("status") == CANCELLED:
            return
        ex = R.get(row.get("executor_id") or "")
        if ex is None or R.availability(ex):
            alt = R.route(order, exclude=[*tried, *( [ex["executor_id"]] if ex else [])])
            if not alt["executor_id"]:
                _finish(order_id, FAILED, error=f"Không còn agent nhận được việc: {(R.availability(ex) if ex else 'agent đã bị xoá')}")
                return
            _set(order_id, executor_id=alt["executor_id"], routing_json=_dump(alt))
            _task_exec[order_id] = alt["executor_id"]
            _history(order_id, "rerouted", executor=alt["executor_id"], reason=alt.get("reason"))
            ex = R.get(alt["executor_id"])
        attempt = int(row.get("attempt") or 0) + 1
        _set(order_id, status=DISPATCHED, attempt=attempt, dispatched_at=_now(), error=None)
        _ledger_move(row.get("task_id"), _ledger().EXECUTING)
        _history(order_id, "dispatched", executor=ex["executor_id"], attempt=attempt)
        try:
            report = await asyncio.wait_for(_dispatch(ex, order, row), order.deadline_s + DEADLINE_GRACE_S)
            fail_reason = None
        except asyncio.CancelledError:
            raise
        except asyncio.TimeoutError:
            report, fail_reason = None, f"quá thời hạn {order.deadline_s} giây"
        except _AttemptError as exc:
            report, fail_reason = None, str(exc)
        except Exception as exc:  # noqa: BLE001 — lỗi lạ của agent không được làm chết phiếu im lặng
            logger.exception("[Orchestration] phiếu %s lỗi khi giao", order_id)
            report, fail_reason = None, f"{type(exc).__name__}: {exc}"[:300]
        if (_db().dev_get(TABLE, order_id) or {}).get("status") == CANCELLED:
            return
        if report is not None:
            _set(order_id, status=REVIEWING, report_json=_dump(report))
            _ledger_move(row.get("task_id"), _ledger().VERIFYING)
            _history(order_id, "reported", status=report["status"], structured=report.get("structured"))
            try:
                result = await V.verify(order, report, caller=row["requested_by"], executor_kind=ex["kind"])
            except Exception as exc:  # noqa: BLE001
                logger.exception("[Orchestration] thẩm định phiếu %s lỗi", order_id)
                result = {"status": V.NOT_VERIFIABLE, "blocked": False, "review": None,
                          "checks": [{"criterion": "thẩm định", "ok": None, "kind": "FACT", "detail": f"lỗi thẩm định: {exc}"[:300]}]}
            _set(order_id, verification_json=_dump(result))
            _record_checks(row.get("task_id"), ex, result)
            if result["status"] == V.PASSED:
                _finish(order_id, COMPLETED, summary=_summary(ex, report, result))
                return
            if result["status"] == V.NOT_VERIFIABLE:
                _finish(order_id, NEEDS_CONFIRMATION, summary="Agent báo xong; còn tiêu chí không tự kiểm được — cần người xác nhận.")
                return
            if result.get("blocked"):
                _finish(order_id, BLOCKED, error=(report.get("result") or "agent báo bị chặn")[:500],
                        summary="Agent bị chặn (thiếu quyền / thông tin / chờ duyệt bước riêng) — xử lý rồi giao lại.")
                return
            fail_reason = "; ".join(f"{c['criterion']}: {c['detail']}" for c in result["checks"] if c["ok"] is False)[:500]
        _history(order_id, "attempt_failed", executor=ex["executor_id"], attempt=attempt, reason=fail_reason)
        tried.append(ex["executor_id"])
        if attempt >= order.max_attempts:
            _finish(order_id, FAILED, error=fail_reason or "không rõ", summary=f"Thất bại sau {attempt} lượt: {fail_reason}")
            return
        # Thử lại: ưu tiên agent KHÁC cùng năng lực; không có thì cùng agent (lỗi có thể chỉ là tạm thời).
        nxt = R.route(order, exclude=tried) if not order.executor_id else {"executor_id": None}
        _set(order_id, status=PLANNED, executor_id=nxt["executor_id"] or ex["executor_id"])
        _task_exec[order_id] = nxt["executor_id"] or ex["executor_id"]
        if nxt["executor_id"]:
            _history(order_id, "rerouted", executor=nxt["executor_id"], reason=nxt.get("reason"))
        await asyncio.sleep(RETRY_PAUSE_S)


def _summary(ex: Dict[str, Any], report: Dict[str, Any], result: Dict[str, Any]) -> str:
    facts = sum(1 for c in result["checks"] if c.get("kind") == "FACT")
    return f"«{ex['name']}» hoàn thành; {len(result['checks'])} tiêu chí đạt ({facts} kiểm bằng sự thật). {str(report.get('result') or '')[:150]}"


def _record_checks(task_id: Optional[str], ex: Dict[str, Any], result: Dict[str, Any]) -> None:
    if not task_id:
        return
    led = _ledger()
    for c in result["checks"]:
        mark = {True: "ĐẠT", False: "KHÔNG ĐẠT", None: "CHƯA KIỂM ĐƯỢC"}[c["ok"]]
        led.add_evidence(task_id, source=f"orch-verify:{ex['executor_id']}", kind="FACT" if c.get("kind") == "FACT" else "INFERENCE",
                         summary=f"[{mark}] {c['criterion']} — {c['detail']}", verified=c["ok"] is True and c.get("kind") == "FACT")


def _finish(order_id: str, status: str, *, error: str = "", summary: str = "") -> None:
    led = _ledger()
    row = _db().dev_get(TABLE, order_id) or {}
    fields: Dict[str, Any] = {"status": status, "error": (error or None) and error[:500]}
    if status in FINAL or status in (NEEDS_CONFIRMATION, BLOCKED, INTERRUPTED):
        fields["finished_at"] = _now()
    _set(order_id, **fields)
    _history(order_id, status.lower(), error=error[:300] or None)
    tid = row.get("task_id")
    if status == COMPLETED:
        _ledger_move(tid, led.VERIFYING, led.COMPLETED, verification_status="passed", result_summary=summary[:300])
    elif status == FAILED:
        _ledger_move(tid, led.FAILED, verification_status="failed", result_summary=(summary or error)[:300])
    elif status == NEEDS_CONFIRMATION:
        _ledger_move(tid, led.ESCALATED, verification_status="not_verifiable", result_summary=summary[:300])
    elif status == BLOCKED:
        _ledger_move(tid, led.BLOCKED, result_summary=summary[:300])
    elif status == CANCELLED:
        _ledger_move(tid, led.CANCELLED, result_summary=summary[:300])
    elif status == INTERRUPTED:
        _ledger_move(tid, led.ESCALATED, result_summary=summary[:300])
    try:
        pump()
    except Exception as exc:  # noqa: BLE001
        logger.warning("[Orchestration] không giao tiếp hàng đợi: %s", exc)


# ── giao cho từng loại agent ────────────────────────────────────────────────

async def _dispatch(ex: Dict[str, Any], order: C.WorkOrder, row: Dict[str, Any]) -> Dict[str, Any]:
    message = C.render_message(order, row["order_id"])
    if ex["kind"] == "local":
        return await _run_local(ex, message, row)
    if ex["kind"] == "a2a":
        return await _run_a2a(ex, message)
    if ex["kind"] == "devfleet":
        return await _run_devfleet(ex, order, row)
    raise _AttemptError(f"loại agent lạ: {ex['kind']}")


async def _run_local(ex: Dict[str, Any], message: str, row: Dict[str, Any]) -> Dict[str, Any]:
    """Vòng agent của VN-MateAI, chỉ thấy bộ công cụ của vai, chạy dưới danh tính NGƯỜI GIAO (mọi bước qua tool_gate)."""
    from mateai.application.agent.llm_engine import llm_engine
    led = _ledger()
    role = f"[VAI: {ex['name']}] {ex['description']}".strip()
    token = led.CURRENT_TASK.set(row.get("task_id"))      # bước công cụ ghi thẳng vào tác vụ của phiếu (bằng chứng thật)
    try:
        out = await llm_engine.ask_async(f"{role}\n\n{message}", source_device="orchestrator",
                                         session_id=f"orch-{row['order_id']}-{int(row.get('attempt') or 0) + 1}",
                                         caller=row["requested_by"], tool_names=list(ex["tools"]))
    finally:
        led.CURRENT_TASK.reset(token)
    if not out.get("success") and not out.get("tool_calls_made"):
        raise _AttemptError(f"agent nội bộ không trả lời được: {out.get('error') or out.get('reply')}"[:300])
    report = C.parse_report(out.get("reply") or "", fallback_status="success" if out.get("success") else "failed")
    calls = out.get("tool_calls_made") or []
    report["evidence"] = [{"summary": f"{c.get('skill')}: thành công", "ref": c.get("skill")} for c in calls
                          if isinstance(c.get("result"), dict) and (c["result"].get("success") is True or c["result"].get("status") == "success")]
    if out.get("requires_confirmation"):
        report["status"] = "blocked"
        report["risks"].append({"level": "blocking", "text": "Một bước cần người duyệt riêng — duyệt trong hộp thư rồi giao lại phiếu."})
    return report


async def _run_a2a(ex: Dict[str, Any], message: str) -> Dict[str, Any]:
    from mateai.application.agent_gateway import directory
    res = await directory.call_a2a(ex["ref"], message)
    state = res.get("state")
    if state == "completed":
        report = C.parse_report(res.get("content") or "", fallback_status="success")
    elif state in ("input-required", "auth-required"):
        report = C.parse_report(res.get("content") or "", fallback_status="blocked")
        report["status"] = "blocked"
        report["risks"].append({"level": "blocking", "text": f"Agent cần thêm thông tin ({state}): {str(res.get('content') or '')[:200]}"})
    else:
        raise _AttemptError(str(res.get("error") or f"agent A2A trả trạng thái {state}")[:300])
    report["untrusted"] = True
    return report


async def _run_devfleet(ex: Dict[str, Any], order: C.WorkOrder, row: Dict[str, Any]) -> Dict[str, Any]:
    from mateai.application.devfleet.service import dev_fleet
    risk = "low" if order.risk <= 2 else "medium" if order.risk == 3 else "high"
    criteria = [*order.deliverables, *[c["text"] for c in order.done_criteria]]
    payload = {"title": order.objective[:120], "objective": f"{order.objective}\n\n{order.context}".strip()[:4000],
               "acceptance_criteria": criteria[:30], "verification_steps": [c["text"] for c in order.done_criteria][:30],
               "risk": risk, "constraints": [*order.boundaries, *C.FIXED_BOUNDARIES][:30],
               "rollback": "; ".join(order.boundaries)[:400] or "Hoàn tác bằng git revert các commit của tác vụ này"}
    attempt = int(row.get("attempt") or 0) + 1
    out = await dev_fleet.create_task(payload, requested_by=row["requested_by"], agent_id=AGENT_ID,
                                      idempotency_key=f"orch:{row['order_id']}:{attempt}")
    if out.get("status") in ("no_worker", "failed", "denied", "error", "blocked") or not out.get("task_id"):
        raise _AttemptError(f"Dev Fleet: {out.get('error') or out.get('status')}"[:300])
    dev_task = out["task_id"]
    _history(row["order_id"], "devfleet_task", task_id=dev_task, status=out.get("status"))
    led = _ledger()
    while True:
        t = led.get_task(dev_task) or {}
        st = t.get("status")
        if st == led.COMPLETED:
            ev = [{"summary": e.get("summary"), "ref": e.get("source")} for e in t.get("evidence") or [] if e.get("verified")]
            return {"status": "success", "result": str(t.get("result_summary") or "Dev Fleet hoàn thành, kiểm chứng đạt"),
                    "evidence": ev or [{"summary": "Dev Fleet kiểm chứng đạt", "ref": dev_task}], "risks": [], "next_steps": [],
                    "structured": True}
        if st in (led.FAILED, led.CANCELLED):
            raise _AttemptError(f"Dev Fleet {st}: {t.get('result_summary') or ''}"[:300])
        if st == led.ESCALATED:
            return {"status": "partial", "result": str(t.get("result_summary") or "Dev Fleet xong nhưng chưa kiểm chứng được"),
                    "evidence": [], "risks": [{"level": "warning", "text": "Dev Fleet chưa kiểm chứng được — cần người xem"}],
                    "next_steps": [], "structured": True}
        await asyncio.sleep(DEVFLEET_POLL_S)


# ── người điều khiển ────────────────────────────────────────────────────────

def cancel(order_id: str, actor: str) -> Dict[str, Any]:
    row = _db().dev_get(TABLE, order_id)
    if not row:
        return {"status": "not_found"}
    if row["status"] in FINAL:
        return {"status": "noop", "order_status": row["status"]}
    t = _tasks.get(order_id)
    _finish(order_id, CANCELLED, summary=f"Huỷ bởi {actor}")
    if t:
        t.cancel()                                     # agent ngoài có thể đã làm một phần — ghi rõ trong ghi chú
    _audit(actor, "orch_order_cancel", {"order_id": order_id, "was": row["status"]})
    return {"status": "cancelled", "note": "Agent ngoài có thể đã làm một phần trước khi huỷ — kiểm tra phía agent nếu cần."
            if row["status"] in (DISPATCHED, REVIEWING) else ""}


def confirm(order_id: str, actor: str, accept: bool, note: str = "") -> Dict[str, Any]:
    """Người xác nhận phiếu NEEDS_CONFIRMATION: chấp nhận → COMPLETED (tên người + ghi chú là bằng chứng); không → FAILED."""
    row = _db().dev_get(TABLE, order_id)
    if not row or row["status"] != NEEDS_CONFIRMATION:
        raise OrderError("Chỉ xác nhận được phiếu đang chờ người xác nhận", "state")
    led = _ledger()
    if row.get("task_id"):
        led.add_evidence(row["task_id"], source=f"human:{actor}", kind="FACT", verified=accept,
                         summary=f"{'Chấp nhận' if accept else 'Không chấp nhận'} kết quả: {note or '(không ghi chú)'}")
    if accept:
        _set(order_id, status=COMPLETED, finished_at=_now())
        _history(order_id, "confirmed", by=actor, note=note[:300])
        _ledger_move(row.get("task_id"), led.COMPLETED, verification_status="passed", result_summary=f"{actor} xác nhận: {note}"[:300])
    else:
        _finish(order_id, FAILED, error=f"{actor} không chấp nhận: {note}", summary=f"{actor} không chấp nhận kết quả: {note}")
    _audit(actor, "orch_order_confirm", {"order_id": order_id, "accept": accept, "note": note[:300]})
    return view(_db().dev_get(TABLE, order_id))  # type: ignore[arg-type]


async def retry(order_id: str, actor: str) -> Dict[str, Any]:
    """Giao lại = phiếu MỚI cùng nội dung (qua lại định tuyến + duyệt); phiếu cũ chưa đóng thì huỷ, ghi liên kết hai chiều."""
    row = _db().dev_get(TABLE, order_id)
    if not row:
        raise OrderError("Không có phiếu này", "not_found")
    if row["status"] in (PLANNED, WAITING_APPROVAL, DISPATCHED, REVIEWING, COMPLETED):
        raise OrderError(f"Phiếu đang {row['status']} — không giao lại", "state")
    spec = _jl(row["spec_json"], {})
    new = await create(spec, requested_by=actor, channel=row.get("channel") or "portal")
    _history(order_id, "retried_as", to=new["order_id"], by=actor)
    _history(new["order_id"], "retry_of", of=order_id)
    if row["status"] not in FINAL:
        _finish(order_id, CANCELLED, summary=f"Thay bằng phiếu {new['order_id']} ({actor})")
    return new


# ── tra cứu ─────────────────────────────────────────────────────────────────

def view(row: Dict[str, Any], brief: bool = False) -> Dict[str, Any]:
    spec = _jl(row.get("spec_json"), {})
    out = {k: row.get(k) for k in ("order_id", "task_id", "executor_id", "capability", "status", "attempt", "risk", "requested_by",
                                   "channel", "approval_id", "error", "created_at", "updated_at", "dispatched_at", "finished_at")}
    out["objective"] = spec.get("objective")
    ex = R.get(row.get("executor_id") or "") if row.get("executor_id") else None
    out["executor_name"] = (ex or {}).get("name")
    if not brief:
        out.update(spec=spec, routing=_jl(row.get("routing_json"), {}), report=_jl(row.get("report_json"), None),
                   verification=_jl(row.get("verification_json"), None), history=_jl(row.get("history_json"), []))
    return out


def get(order_id: str) -> Optional[Dict[str, Any]]:
    row = _db().dev_get(TABLE, str(order_id or ""))
    return view(row) if row else None


def list_orders(status: Optional[str] = None, executor_id: Optional[str] = None, limit: int = 100) -> List[Dict[str, Any]]:
    where: Dict[str, Any] = {}
    if status:
        where["status"] = status
    if executor_id:
        where["executor_id"] = executor_id
    return [view(r, brief=True) for r in _db().dev_list(TABLE, where or None, order_by="created_at DESC", limit=limit)]


# ── khởi động / vòng nền ────────────────────────────────────────────────────

def recover_interrupted() -> int:
    """Lúc khởi động: phiếu đang giao dở → INTERRUPTED (agent có thể đã làm một phần — không tự chạy lại). Phiếu PLANNED
    (chưa giao) giữ nguyên, vòng nền sẽ giao."""
    n = 0
    for r in _db().dev_list(TABLE, {"status": [DISPATCHED, REVIEWING]}, limit=500):
        if r["order_id"] in _tasks:
            continue
        _finish(r["order_id"], INTERRUPTED, error="Máy chủ khởi động lại khi agent đang làm",
                summary="Bị gián đoạn do máy chủ khởi động lại — kiểm tra phía agent rồi giao lại nếu cần")
        n += 1
    return n


def reconcile_approvals() -> int:
    """Phiếu chờ duyệt mà phiếu duyệt đã bị từ chối / hết hạn / mất (khởi động lại) → CANCELLED."""
    from mateai.application.security.zero_trust import hitl_manager
    n = 0
    for r in _db().dev_list(TABLE, {"status": WAITING_APPROVAL}, limit=500):
        st = hitl_manager.get_status(r.get("approval_id") or "")
        if st in ("rejected", "expired") or (st is None and r.get("approval_id")):
            why = {"rejected": "bị từ chối", "expired": "hết hạn"}.get(st or "", "không còn (máy chủ đã khởi động lại)")
            _finish(r["order_id"], CANCELLED, summary=f"Phiếu duyệt {r.get('approval_id')} {why}")
            n += 1
    return n


async def background_loop() -> None:
    """Đối soát duyệt + giao hàng đợi định kỳ. Lỗi một vòng không dừng vòng sau."""
    while True:
        try:
            reconcile_approvals()
            pump()
        except Exception as exc:  # noqa: BLE001
            logger.warning("[Orchestration] vòng nền lỗi: %s", exc)
        await asyncio.sleep(PUMP_INTERVAL_S)


def _register() -> None:
    from mateai.application.security.zero_trust import hitl_manager
    hitl_manager.register_executor(KIND, execute_approved_order)


_register()
