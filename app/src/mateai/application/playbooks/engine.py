# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 Dương Thanh Lịch — VN-MateAI. See LICENSE and NOTICE.
"""
mateai/application/playbooks/engine.py
======================================
Kịch bản vận hành (playbook) — chuỗi bước công cụ có điều kiện, hoàn tác và kiểm chứng (docs/integrations/playbooks.md).

Nguyên tắc (không có đường vòng nào quanh chính sách):
  - MỌI bước đi qua đúng `tool_gate.run_tool_with_policy` như một lời gọi công cụ của AI: RBAC theo NGƯỜI YÊU CẦU, kill switch, L5,
    từ khoá cấm, ABAC, audit, ghi bước vào Sổ tác vụ. DENY luôn thắng.
  - CHẠY THỬ (`plan`) không thực thi và không ghi gì: cho thấy từng bước sẽ được cho phép / chờ duyệt / bị chặn, mức rủi ro, có hoàn tác không.
  - Kế hoạch có bước cần duyệt -> MỘT yêu cầu duyệt cho cả kế hoạch (người duyệt đọc được từng bước); định nghĩa được ĐÓNG BĂNG (snapshot)
    lúc tạo lượt chạy nên sửa kịch bản sau đó không đổi thứ đã được duyệt.
  - "Xong" cần BẰNG CHỨNG: chỉ khi khối `verify` đạt. Không khai `verify` -> chờ người xác nhận (SUCCEEDED_UNVERIFIED), không tự coi là xong.
  - Máy chủ khởi động lại giữa chừng -> lượt chạy RUNNING thành INTERRUPTED (chưa biết bước đang dở ra sao), không tự chạy lại.
"""
from __future__ import annotations

import asyncio
import json
import logging
import uuid
from datetime import datetime
from typing import Any, Dict, List, Optional, Set

from mateai.application.playbooks import definition as D

logger = logging.getLogger(__name__)

AGENT_ID = "VN-MATEAI-PLAYBOOK"
KIND = "playbook"
MAX_CONCURRENT = 3
MAX_RUN_SECONDS = 900

PLANNED, WAITING_APPROVAL, RUNNING = "PLANNED", "WAITING_APPROVAL", "RUNNING"
SUCCEEDED, UNVERIFIED, FAILED, CANCELLED, BLOCKED, INTERRUPTED = (
    "SUCCEEDED", "SUCCEEDED_UNVERIFIED", "FAILED", "CANCELLED", "BLOCKED", "INTERRUPTED")
ACTIVE = (PLANNED, WAITING_APPROVAL, RUNNING)

_tasks: Dict[str, "asyncio.Task[Any]"] = {}
_cancel: Set[str] = set()


def _now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _db():
    from mateai.infrastructure.database.db_manager import db_manager
    return db_manager


def _ledger():
    from mateai.application.tasks import ledger
    return ledger


def _jl(text: Any, default: Any = None) -> Any:
    try:
        return json.loads(text) if text else default
    except (TypeError, ValueError):
        return default


def known_tools() -> Set[str]:
    """Công cụ có thật trong hệ thống (kỹ năng đã nạp + Plugin Registry)."""
    names: Set[str] = set()
    try:
        from core.plugin_manager import plugin_manager
        names |= set(plugin_manager.get_skill_names())
    except Exception:  # noqa: BLE001
        pass
    try:
        from mateai.application.skills.plugin_registry import plugin_registry
        names |= set(plugin_registry.get_tool_names())
    except Exception:  # noqa: BLE001
        pass
    return names


# ── kho kịch bản ────────────────────────────────────────────────────────────

def save_playbook(raw: Dict[str, Any], *, actor: str, validate_tools: bool = True) -> Dict[str, Any]:
    defn = D.validate_definition(raw, known_tools() if validate_tools else None)
    if defn["triggers"]:
        # Lượt tự kích hoạt chạy bằng danh tính người đặt trình kích hoạt (admin lưu kịch bản) — RBAC / duyệt áp theo người này.
        defn["triggers_owner"] = actor
    existing = _db().dev_get("pb_playbooks", defn["id"])
    now = _now()
    row = {"name": defn["name"], "definition_json": json.dumps(defn, ensure_ascii=False), "updated_at": now}
    if existing:
        _db().dev_update("pb_playbooks", defn["id"], {**row, "version": int(existing["version"]) + 1})
    else:
        _db().dev_insert("pb_playbooks", {"playbook_id": defn["id"], **row, "enabled": 1, "version": 1, "created_by": actor, "created_at": now})
    _audit(actor, "playbook_save", {"playbook_id": defn["id"], "version": (int(existing["version"]) + 1) if existing else 1})
    return get_playbook(defn["id"])


def get_playbook(playbook_id: str) -> Optional[Dict[str, Any]]:
    row = _db().dev_get("pb_playbooks", str(playbook_id or "").strip().lower())
    return _view(row) if row else None


def list_playbooks() -> List[Dict[str, Any]]:
    return [_view(r) for r in _db().dev_list("pb_playbooks", order_by="name ASC", limit=500)]


def _view(row: Dict[str, Any]) -> Dict[str, Any]:
    return {"id": row["playbook_id"], "name": row["name"], "enabled": bool(row["enabled"]), "version": row["version"],
            "created_by": row.get("created_by"), "updated_at": row["updated_at"], "definition": _jl(row["definition_json"], {})}


def set_enabled(playbook_id: str, enabled: bool, actor: str) -> bool:
    if not _db().dev_get("pb_playbooks", playbook_id):
        return False
    _db().dev_update("pb_playbooks", playbook_id, {"enabled": 1 if enabled else 0, "updated_at": _now()})
    _audit(actor, "playbook_enable" if enabled else "playbook_disable", {"playbook_id": playbook_id})
    return True


def delete_playbook(playbook_id: str, actor: str) -> bool:
    n = _db().dev_delete("pb_playbooks", playbook_id)
    if n:
        _audit(actor, "playbook_delete", {"playbook_id": playbook_id})
    return bool(n)


def _audit(actor: str, action: str, details: Dict[str, Any], status: str = "SUCCESS") -> None:
    try:
        from mateai.application.security.zero_trust import log_security_audit
        log_security_audit(str(actor or "system"), action, "PLAYBOOK", status, details)
    except Exception:  # noqa: BLE001
        pass


# ── chạy thử (dry-run) ──────────────────────────────────────────────────────

def _agent(human: bool) -> str:
    from mateai.application.security import policy_engine
    return policy_engine.HUMAN_DIRECT if human else AGENT_ID


def _decide(tool: str, args: Dict[str, Any], caller: str, human: bool) -> Dict[str, Any]:
    from mateai.application.security import policy_engine
    declared = None
    try:
        from mateai.application.skills.plugin_registry import plugin_registry
        d = plugin_registry.get_tool(tool)
        declared = d.risk_level if d is not None else None
    except Exception:  # noqa: BLE001
        pass
    a = {k: v for k, v in args.items() if k not in ("target_client_id", "target_client")}
    dec = policy_engine.authorize(tool, a, caller=caller, agent_id=_agent(human), approved=False, declared_risk=declared)
    return {"decision": dec.effect, "risk": dec.risk, "level": dec.level, "rule": dec.rule, "reasons": list(dec.reasons)}


def plan(defn: Dict[str, Any], raw_params: Optional[Dict[str, Any]], *, caller: str, human: bool = True) -> Dict[str, Any]:
    """Chạy thử: KHÔNG thực thi, KHÔNG ghi. Mỗi bước: công cụ, tham số đã thay mẫu (chỗ phụ thuộc kết quả bước trước hiện «dấu vết»),
    quyết định chính sách + rủi ro, có hoàn tác không. `blocked` = có bước bị chặn -> không chạy được."""
    from mateai.application.security import policy_engine
    params = D.coerce_params(defn, raw_params)
    ctx: Dict[str, Any] = {"params": params, "steps": {}}
    rows: List[Dict[str, Any]] = []
    for st in defn["steps"]:
        args = D.render(st["args"], ctx, lenient=True)
        row = {"id": st["id"], "title": st["title"], "tool": st["tool"], "args": args, "on_failure": st["on_failure"],
               "conditional": bool(st["when"]), "has_rollback": bool(st["rollback"]), **_decide(st["tool"], args, caller, human)}
        if st["rollback"]:
            row["rollback"] = {"tool": st["rollback"]["tool"], "args": D.render(st["rollback"]["args"], ctx, lenient=True),
                               **_decide(st["rollback"]["tool"], D.render(st["rollback"]["args"], ctx, lenient=True), caller, human)}
        rows.append(row)
        ctx["steps"][st["id"]] = {"result": {}}
    verify_rows = []
    notes: List[str] = []
    for v in defn["verify"]:
        args = D.render(v["args"], ctx, lenient=True)
        dec = _decide(v["tool"], args, caller, human)
        if dec["risk"] > 2:
            notes.append(f"Kiểm chứng «{v['id']}» dùng công cụ rủi ro {dec['risk']} — kiểm chứng nên chỉ đọc")
        verify_rows.append({"id": v["id"], "tool": v["tool"], "args": args, "expect": v["expect"], **dec})
    if not defn["verify"]:
        notes.append("Chưa khai báo `verify`: chạy xong sẽ ở trạng thái CHƯA KIỂM CHỨNG, cần người xác nhận mới tính là hoàn thành")
    everything = rows + [r["rollback"] for r in rows if r.get("rollback")] + verify_rows
    denied = [{"id": r.get("id") or r["tool"], "tool": r["tool"], "reason": (r["reasons"] or [""])[0], "rule": r["rule"]}
              for r in everything if r["decision"] == policy_engine.DENY]
    needs = [r["id"] for r in rows if r["decision"] == policy_engine.REQUIRE_APPROVAL]
    return {"playbook_id": defn["id"], "params": params, "steps": rows, "verify": verify_rows, "blocked": bool(denied), "denied": denied,
            "needs_approval": bool(needs), "approval_steps": needs, "max_risk": max([r["risk"] for r in everything] or [1]),
            "plan_hash": D.plan_hash(defn, params), "notes": notes}


def _summary(defn: Dict[str, Any], pl: Dict[str, Any]) -> str:
    lines = [f"Kịch bản «{defn['name']}» — {len(pl['steps'])} bước, rủi ro tối đa {pl['max_risk']}/5 (kế hoạch {pl['plan_hash']}):"]
    for i, r in enumerate(pl["steps"], 1):
        mark = "cần duyệt" if r["decision"] == "require_approval" else r["decision"]
        lines.append(f"{i}. {r['title']} → {r['tool']} [{mark}, rủi ro {r['risk']}]{' · có hoàn tác' if r['has_rollback'] else ''}")
    return "\n".join(lines)[:1800]


# ── bắt đầu một lượt chạy ───────────────────────────────────────────────────

async def start(playbook_id: str, raw_params: Optional[Dict[str, Any]] = None, *, caller: str, human: bool = True,
                idempotency_key: Optional[str] = None) -> Dict[str, Any]:
    from mateai.application.security import policy_engine
    from mateai.application.security.zero_trust import hitl_manager
    pb = get_playbook(playbook_id)
    if not pb:
        raise D.PlaybookError(f"Không có kịch bản «{playbook_id}»")
    if not pb["enabled"]:
        raise D.PlaybookError(f"Kịch bản «{playbook_id}» đang tắt")
    if idempotency_key:
        dup = _db().dev_list("pb_runs", {"idempotency_key": idempotency_key}, limit=1)
        if dup:
            return {"status": "duplicate", "run_id": dup[0]["run_id"], "run_status": dup[0]["status"]}
    in_flight = {rid for rid, t in _tasks.items() if not t.done()} | {r["run_id"] for r in _db().dev_list("pb_runs", {"status": [RUNNING]}, limit=50)}
    if len(in_flight) >= MAX_CONCURRENT:                      # đếm cả lượt vừa xếp lịch chạy nhưng chưa kịp ghi RUNNING
        raise D.PlaybookError(f"Đang có {MAX_CONCURRENT} kịch bản chạy cùng lúc — chờ một cái xong")
    defn = pb["definition"]
    pl = plan(defn, raw_params, caller=caller, human=human)
    led = _ledger()
    task_id = led.open_task(f"Kịch bản: {defn['name']}", kind=KIND, created_by=caller, agent_id=_agent(human), source="playbook",
                            risk=pl["max_risk"], goal=defn["id"])
    run_id = f"PBR-{uuid.uuid4().hex[:10]}"
    row = {"run_id": run_id, "playbook_id": defn["id"], "task_id": task_id, "status": PLANNED, "caller": caller, "agent_id": _agent(human),
           "params_json": json.dumps(pl["params"], ensure_ascii=False), "definition_json": json.dumps(defn, ensure_ascii=False),
           "plan_json": json.dumps(pl, ensure_ascii=False), "plan_hash": pl["plan_hash"], "idempotency_key": idempotency_key,
           "created_at": _now()}
    _db().dev_insert("pb_runs", row)
    if task_id:
        led.transition(task_id, led.PLANNED)
    if pl["blocked"]:
        why = "; ".join(f"{d['id']}: {d['reason']}" for d in pl["denied"])[:400]
        _finish(run_id, BLOCKED, error=why, summary=f"Bị chính sách chặn trước khi chạy: {why}")
        _audit(caller, "playbook_start", {"run_id": run_id, "playbook_id": defn["id"], "result": "blocked", "denied": pl["denied"]}, "REJECTED")
        return {"status": "blocked", "run_id": run_id, "plan": pl}
    if pl["needs_approval"]:
        req = hitl_manager.request_approval(action_name=f"playbook:{defn['id']}", params={"run_id": run_id, "plan_hash": pl["plan_hash"]},
                                            requested_by=caller, description=_summary(defn, pl), kind=KIND,
                                            context={"run_id": run_id, "op_task_id": task_id}, risk_level=pl["max_risk"])
        _db().dev_update("pb_runs", run_id, {"status": WAITING_APPROVAL, "approval_id": req.get("id")})
        if task_id:
            led.transition(task_id, led.WAITING_AUTHORIZATION, approval_id=req.get("id"))
        _audit(caller, "playbook_start", {"run_id": run_id, "playbook_id": defn["id"], "result": "awaiting_approval", "approval_id": req.get("id")}, "PENDING")
        return {"status": "awaiting_approval", "run_id": run_id, "approval_id": req.get("id"), "plan": pl,
                "message": f"CHƯA chạy: kế hoạch có bước cần duyệt (phiếu {req.get('id')})."}
    _spawn(run_id, approved=False)
    _audit(caller, "playbook_start", {"run_id": run_id, "playbook_id": defn["id"], "result": "running"})
    return {"status": "running", "run_id": run_id, "plan": pl}


async def execute_approved_playbook(item: Dict[str, Any]) -> Dict[str, Any]:
    """Executor HITL (kind="playbook"): người có quyền đã duyệt cả kế hoạch -> chạy nền các bước với `approved=True`."""
    run_id = str((item.get("params") or {}).get("run_id") or "")
    run = _db().dev_get("pb_runs", run_id)
    if not run or run["status"] != WAITING_APPROVAL:
        return {"success": False, "error": "Lượt chạy không còn chờ duyệt (đã chạy, huỷ hoặc không tồn tại)"}
    _audit(str(item.get("reviewed_by") or "?"), "playbook_approved", {"run_id": run_id, "plan_hash": run["plan_hash"]})
    _spawn(run_id, approved=True)
    return {"success": True, "status": "started", "run_id": run_id}


def _spawn(run_id: str, approved: bool) -> None:
    task = asyncio.get_running_loop().create_task(_execute(run_id, approved), name=f"playbook-{run_id}")
    _tasks[run_id] = task
    task.add_done_callback(lambda t: _tasks.pop(run_id, None))


async def wait(run_id: str, timeout: float = 60.0) -> Dict[str, Any]:
    t = _tasks.get(run_id)
    if t:
        await asyncio.wait_for(asyncio.shield(t), timeout)
    return get_run(run_id) or {}


# ── thực thi ────────────────────────────────────────────────────────────────

def _ok(result: Any) -> bool:
    return isinstance(result, dict) and (result.get("success") is True or result.get("status") == "success")


def _reason(result: Any) -> str:
    if not isinstance(result, dict):
        return "kết quả không hợp lệ"
    if result.get("status") in ("need_confirm", "awaiting_approval"):
        return f"cần phê duyệt riêng (phiếu {result.get('approval_id')})"
    return str(result.get("error") or result.get("message") or result.get("code") or result.get("status") or "thất bại")[:300]


async def _run_tool(tool: str, args: Dict[str, Any], *, caller: str, human: bool, approved: bool) -> Dict[str, Any]:
    from mateai.application.agent.tool_gate import run_tool_with_policy
    gate = await run_tool_with_policy(tool, dict(args), caller=caller, source_device="playbook", query=f"Kịch bản: {tool}",
                                      approved=approved, agent_id=_agent(human))
    return gate["result"] if isinstance(gate, dict) and isinstance(gate.get("result"), dict) else {"success": False, "error": "cổng công cụ không trả kết quả"}


def _persist(run_id: str, results: Dict[str, Any], **extra: Any) -> None:
    _db().dev_update("pb_runs", run_id, {"results_json": json.dumps(results, ensure_ascii=False, default=str)[:200000], **extra})


def _finish(run_id: str, status: str, *, error: str = "", summary: str = "", verification: Optional[str] = None) -> None:
    led = _ledger()
    _db().dev_update("pb_runs", run_id, {"status": status, "error": error[:500] or None, "finished_at": _now()})
    run = _db().dev_get("pb_runs", run_id) or {}
    tid = run.get("task_id")
    if not tid:
        return
    try:
        if status == SUCCEEDED:
            led.transition(tid, led.VERIFYING)
            led.transition(tid, led.COMPLETED, verification_status="passed", result_summary=summary[:300])
        elif status == UNVERIFIED:
            led.transition(tid, led.VERIFYING, verification_status="unverified", result_summary=summary[:300])
        elif status == FAILED:
            led.transition(tid, led.VERIFYING, verification_status="failed")
            led.transition(tid, led.FAILED, verification_status="failed", result_summary=(summary or error)[:300])
        elif status == CANCELLED:
            led.transition(tid, led.CANCELLED, result_summary=summary[:300])
        elif status == BLOCKED:
            led.transition(tid, led.BLOCKED, result_summary=summary[:300])
        elif status == INTERRUPTED:
            led.transition(tid, led.ESCALATED, result_summary=summary[:300])
    except Exception as exc:  # noqa: BLE001
        logger.warning("[Playbook] không cập nhật được sổ tác vụ %s: %s", tid, exc)


async def _execute(run_id: str, approved: bool) -> None:
    led = _ledger()
    run = _db().dev_get("pb_runs", run_id)
    if not run or run["status"] not in (PLANNED, WAITING_APPROVAL):
        return
    defn = _jl(run["definition_json"], {})
    params = _jl(run["params_json"], {})
    caller, human = run["caller"] or "system", run["agent_id"] != AGENT_ID
    tid = run.get("task_id")
    _db().dev_update("pb_runs", run_id, {"status": RUNNING, "started_at": _now()})
    token = led.CURRENT_TASK.set(tid)
    if tid:
        t = led.get_task(tid) or {}
        if t.get("status") in ("PLANNED", "WAITING_AUTHORIZATION"):
            led.transition(tid, led.AUTHORIZED)
        led.transition(tid, led.EXECUTING)
    ctx: Dict[str, Any] = {"params": params, "steps": {}}
    log: List[Dict[str, Any]] = []
    done: List[Dict[str, Any]] = []
    hard_fail = ""
    verification: List[Dict[str, Any]] = []
    deadline = asyncio.get_running_loop().time() + MAX_RUN_SECONDS
    try:
        for st in defn["steps"]:
            if run_id in _cancel:
                break
            if asyncio.get_running_loop().time() > deadline:
                hard_fail = f"Quá {MAX_RUN_SECONDS}s cho cả kịch bản"
                break
            entry: Dict[str, Any] = {"id": st["id"], "tool": st["tool"], "title": st["title"]}
            if not D.evaluate(st["when"], ctx):
                entry.update(status="skipped", note="điều kiện `when` không thoả")
                ctx["steps"][st["id"]] = {"ok": None, "result": {}, "status": "skipped"}
                log.append(entry)
                continue
            try:
                args = D.render(st["args"], ctx)
                res = await asyncio.wait_for(_run_tool(st["tool"], args, caller=caller, human=human, approved=approved), st["timeout_s"])
                ok = _ok(res)
                err = "" if ok else _reason(res)
            except asyncio.TimeoutError:
                res, ok, err = {}, False, f"quá {st['timeout_s']}s — CHƯA biết công cụ đã dừng chưa"
            except D.PlaybookError as exc:
                res, ok, err = {}, False, str(exc)
            except Exception as exc:  # noqa: BLE001
                logger.exception("[Playbook] bước %s lỗi", st["id"])
                res, ok, err = {}, False, f"{type(exc).__name__}: {exc}"
            entry.update(status="ok" if ok else "failed", error=err or None, result=_trim(res))
            ctx["steps"][st["id"]] = {"ok": ok, "result": res, "status": entry["status"]}
            log.append(entry)
            _persist(run_id, {"steps": log})
            if ok:
                done.append(st)
                continue
            if st["on_failure"] == "continue":
                entry["status"] = "failed_optional"
                continue
            if st["on_failure"] == "rollback":
                await _rollback(done, ctx, log, caller=caller, human=human, approved=approved)
            hard_fail = f"Bước «{st['id']}» ({st['tool']}) thất bại: {err}"
            break
        if not hard_fail and run_id not in _cancel:
            verification = await _verify(defn, ctx, caller=caller, human=human, approved=approved)
    finally:
        led.CURRENT_TASK.reset(token)
    _persist(run_id, {"steps": log, "verify": verification})
    if run_id in _cancel:
        _cancel.discard(run_id)
        _finish(run_id, CANCELLED, summary="Đã huỷ giữa chừng")
        return
    if hard_fail:
        _finish(run_id, FAILED, error=hard_fail, summary=hard_fail)
        return
    if not defn["verify"]:
        _finish(run_id, UNVERIFIED, summary="Các bước đã chạy xong nhưng kịch bản không khai báo kiểm chứng — cần người xác nhận kết quả")
    elif all(v["ok"] for v in verification):
        _finish(run_id, SUCCEEDED, summary=f"{len(done)} bước đã chạy, {len(verification)} kiểm chứng đạt")
    else:
        bad = "; ".join(f"{v['id']}: {v['detail']}" for v in verification if not v["ok"])
        _finish(run_id, FAILED, error=f"Kiểm chứng không đạt — {bad}", summary=f"Kiểm chứng không đạt — {bad}")


def _trim(res: Any, limit: int = 1500) -> Any:
    text = json.dumps(res, ensure_ascii=False, default=str)
    return res if len(text) <= limit else {"_truncated": text[:limit]}


async def _rollback(done: List[Dict[str, Any]], ctx: Dict[str, Any], log: List[Dict[str, Any]], *, caller: str, human: bool, approved: bool) -> None:
    for st in reversed(done):
        rb = st.get("rollback")
        if not rb:
            continue
        entry = {"id": f"rollback:{st['id']}", "tool": rb["tool"], "title": f"Hoàn tác «{st['title']}»"}
        try:
            res = await asyncio.wait_for(_run_tool(rb["tool"], D.render(rb["args"], ctx), caller=caller, human=human, approved=approved), st["timeout_s"])
            entry.update(status="ok" if _ok(res) else "failed", error=None if _ok(res) else _reason(res), result=_trim(res))
        except Exception as exc:  # noqa: BLE001
            entry.update(status="failed", error=f"{type(exc).__name__}: {exc}")
        log.append(entry)


async def _verify(defn: Dict[str, Any], ctx: Dict[str, Any], *, caller: str, human: bool, approved: bool) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    for v in defn["verify"]:
        ok, detail = False, ""
        for attempt in range(v["retries"] + 1):
            try:
                res = await asyncio.wait_for(_run_tool(v["tool"], D.render(v["args"], ctx), caller=caller, human=human, approved=approved), 120)
                if _ok(res) and D.evaluate(v["expect"], {**ctx, "result": res, "r": res}):
                    ok, detail = True, "đạt"
                    break
                detail = "công cụ báo lỗi: " + _reason(res) if not _ok(res) else f"kết quả chưa như mong đợi ({v['expect']['path']} {v['expect']['op']} {v['expect'].get('value')})"
            except Exception as exc:  # noqa: BLE001
                detail = f"{type(exc).__name__}: {exc}"
            if attempt < v["retries"]:
                await asyncio.sleep(v["delay_s"])
        out.append({"id": v["id"], "ok": ok, "detail": detail})
    return out


# ── điều khiển / tra cứu ────────────────────────────────────────────────────

def cancel(run_id: str, actor: str) -> Dict[str, Any]:
    run = _db().dev_get("pb_runs", run_id)
    if not run:
        return {"status": "not_found"}
    if run["status"] == RUNNING:
        _cancel.add(run_id)                                    # dừng SAU bước đang chạy (không cắt ngang một công cụ)
        _audit(actor, "playbook_cancel", {"run_id": run_id})
        return {"status": "cancelling", "note": "Sẽ dừng sau bước đang chạy; các bước đã chạy KHÔNG tự hoàn tác."}
    if run["status"] in (PLANNED, WAITING_APPROVAL):
        _finish(run_id, CANCELLED, summary=f"Huỷ bởi {actor}")
        _audit(actor, "playbook_cancel", {"run_id": run_id})
        return {"status": "cancelled"}
    return {"status": "noop", "run_status": run["status"]}


def confirm(run_id: str, actor: str, note: str = "") -> Dict[str, Any]:
    """Người xác nhận kết quả của lượt chạy `SUCCEEDED_UNVERIFIED` (đã tự kiểm tra) -> hoàn thành, có tên người + ghi chú làm bằng chứng."""
    run = _db().dev_get("pb_runs", run_id)
    if not run or run["status"] != UNVERIFIED:
        return {"status": "noop", "error": "Chỉ xác nhận được lượt chạy đang ở trạng thái chưa kiểm chứng"}
    led = _ledger()
    if run.get("task_id"):
        led.add_evidence(run["task_id"], source=f"human:{actor}", kind="FACT", summary=f"Xác nhận kết quả: {note or '(không ghi chú)'}", verified=True)
        led.transition(run["task_id"], led.COMPLETED, verification_status="passed", result_summary=f"{actor} xác nhận: {note}"[:300])
    _db().dev_update("pb_runs", run_id, {"status": SUCCEEDED})
    _audit(actor, "playbook_confirm", {"run_id": run_id, "note": note})
    return {"status": "confirmed"}


def get_run(run_id: str) -> Optional[Dict[str, Any]]:
    run = _db().dev_get("pb_runs", run_id)
    return _run_view(run) if run else None


def list_runs(playbook_id: Optional[str] = None, limit: int = 50) -> List[Dict[str, Any]]:
    where = {"playbook_id": playbook_id} if playbook_id else None
    return [_run_view(r, brief=True) for r in _db().dev_list("pb_runs", where, order_by="created_at DESC", limit=limit)]


def _run_view(r: Dict[str, Any], brief: bool = False) -> Dict[str, Any]:
    out = {"run_id": r["run_id"], "playbook_id": r["playbook_id"], "task_id": r.get("task_id"), "status": r["status"], "caller": r.get("caller"),
           "approval_id": r.get("approval_id"), "error": r.get("error"), "created_at": r["created_at"], "started_at": r.get("started_at"),
           "finished_at": r.get("finished_at"), "plan_hash": r.get("plan_hash")}
    key = str(r.get("idempotency_key") or "")
    out["trigger"] = key.split(":", 1)[0] if key.startswith(("cron:", "alert:", "webhook:")) else None   # lượt tự kích hoạt
    if not brief:
        res = _jl(r.get("results_json"), {})
        out.update(params=_jl(r.get("params_json"), {}), plan=_jl(r.get("plan_json"), {}), steps=res.get("steps", []), verify=res.get("verify", []))
    return out


def recover_interrupted() -> int:
    """Gọi lúc khởi động: lượt chạy đang RUNNING mà tiến trình đã chết -> INTERRUPTED (chưa biết bước đang dở ra sao; không tự chạy lại)."""
    n = 0
    for r in _db().dev_list("pb_runs", {"status": [RUNNING]}, limit=200):
        _finish(r["run_id"], INTERRUPTED, error="Máy chủ khởi động lại khi đang chạy",
                summary="Bị gián đoạn do máy chủ khởi động lại — kiểm tra thủ công bước đang dở")
        n += 1
    return n


def _register() -> None:
    from mateai.application.security.zero_trust import hitl_manager
    hitl_manager.register_executor(KIND, execute_approved_playbook)


_register()
