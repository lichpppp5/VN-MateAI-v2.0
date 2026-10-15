# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 Dương Thanh Lịch — VN-MateAI. See LICENSE and NOTICE.
"""
mateai/application/tasks/ledger.py
==================================
Sổ tác vụ vận hành của AI (prompt Supervisor §20–§28, §153). Tồn tại độc lập với
giao diện chat: chat là giao diện, tác vụ là trạng thái vận hành (§192).

- Mỗi lượt agent có gọi tool = một tác vụ `agent_turn`; mỗi lần gọi tool = một bước
  (quyết định chính sách, kết quả, mức + kết quả kiểm chứng, bằng chứng).
- Sentinel phát hiện sự cố -> tác vụ `incident` (KHÔNG tự xử lý).
- Máy trạng thái rõ ràng; chỉ vào COMPLETED từ VERIFYING khi kiểm chứng đạt (§22).
- Bằng chứng lưu tóm tắt + băm tham chiếu, không chép dữ liệu nhạy cảm (§27).

Ghi sổ lỗi KHÔNG được làm hỏng tác vụ chính (chỉ log cảnh báo).
"""
from __future__ import annotations

import contextvars
import hashlib
import json
import logging
import uuid
from datetime import datetime
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

NEW, ANALYZING, PLANNED = "NEW", "ANALYZING", "PLANNED"
WAITING_AUTHORIZATION, AUTHORIZED, EXECUTING = "WAITING_AUTHORIZATION", "AUTHORIZED", "EXECUTING"
VERIFYING, COMPLETED, FAILED = "VERIFYING", "COMPLETED", "FAILED"
BLOCKED, CANCELLED, ESCALATED = "BLOCKED", "CANCELLED", "ESCALATED"
STATES = (NEW, ANALYZING, PLANNED, WAITING_AUTHORIZATION, AUTHORIZED, EXECUTING, VERIFYING,
          COMPLETED, FAILED, BLOCKED, CANCELLED, ESCALATED)
TERMINAL = (COMPLETED, FAILED, CANCELLED)

#: Chuyển trạng thái hợp lệ. BLOCKED / ESCALATED / CANCELLED đến được từ mọi trạng thái chưa kết thúc.
TRANSITIONS: Dict[str, tuple] = {
    NEW: (ANALYZING, PLANNED, EXECUTING),
    ANALYZING: (PLANNED, EXECUTING),
    PLANNED: (WAITING_AUTHORIZATION, AUTHORIZED, EXECUTING),
    WAITING_AUTHORIZATION: (AUTHORIZED, EXECUTING),
    AUTHORIZED: (EXECUTING,),
    EXECUTING: (WAITING_AUTHORIZATION, VERIFYING, FAILED),
    VERIFYING: (COMPLETED, FAILED, EXECUTING),
    BLOCKED: (EXECUTING,),
    ESCALATED: (EXECUTING, COMPLETED, FAILED),   # COMPLETED từ đây chỉ khi người xác nhận
    COMPLETED: (), FAILED: (), CANCELLED: (),
}

#: Tác vụ của lượt agent đang chạy (đặt trong ask_async, đọc ở tool_gate).
CURRENT_TASK: contextvars.ContextVar[Optional[str]] = contextvars.ContextVar("vnmate_op_task", default=None)


class InvalidTransition(ValueError):
    pass


def _now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _db():
    from mateai.infrastructure.database.db_manager import db_manager
    return db_manager


_LEVEL_SCORE = {"low": 1, "medium": 2, "high": 3, "critical": 4}
_PRIORITY_ORDER = ("LOW", "MEDIUM", "HIGH", "CRITICAL")


def score_priority(*, impact: str = "low", urgency: str = "low", deadline: Optional[str] = None,
                   security_impact: bool = False, affected_users: int = 0, dependencies: int = 0,
                   risk: int = 1, severity: Optional[str] = None) -> Dict[str, Any]:
    """Priority engine (prompt cuối §43): tác động, độ khẩn, SLA, bảo mật, phụ thuộc, số người bị
    ảnh hưởng — tất định, không hỏi LLM, trả kèm lý do để người xem hiểu vì sao."""
    score = _LEVEL_SCORE.get(str(impact).lower(), 1) + _LEVEL_SCORE.get(str(urgency).lower(), 1)
    reasons = [f"tác động {impact}, độ khẩn {urgency}"]
    if deadline:
        try:
            left_min = (datetime.fromisoformat(str(deadline)) - datetime.now()).total_seconds() / 60
            if left_min <= 60:
                score += 4
                reasons.append(f"SLA còn {max(0, int(left_min))} phút")
            elif left_min <= 24 * 60:
                score += 2
                reasons.append("SLA trong 24 giờ")
        except ValueError:
            reasons.append("hạn SLA không đọc được — bỏ qua")
    if security_impact:
        score += 4
        reasons.append("ảnh hưởng bảo mật")
    if affected_users >= 100:
        score += 3
        reasons.append(f"{affected_users} người bị ảnh hưởng")
    elif affected_users >= 10:
        score += 1
        reasons.append(f"{affected_users} người bị ảnh hưởng")
    if dependencies:
        score += min(2, int(dependencies))
        reasons.append(f"{dependencies} việc phụ thuộc")
    base = priority_for("", risk, severity)
    by_score = "CRITICAL" if score >= 9 else "HIGH" if score >= 6 else "MEDIUM" if score >= 4 else "LOW"
    final = max(base, by_score, key=_PRIORITY_ORDER.index)
    if final == base and base != by_score:
        reasons.append(f"mức nghiêm trọng / rủi ro đặt tối thiểu {base}")
    return {"priority": final, "score": score, "reasons": reasons}


def priority_for(kind: str, risk: int = 1, severity: Optional[str] = None) -> str:
    """Ưu tiên tất định (§23) — không hỏi LLM."""
    sev = str(severity or "").lower()
    if sev in ("critical", "p1"):
        return "CRITICAL"
    if sev in ("high", "error", "p2") or risk >= 4:
        return "HIGH"
    if sev in ("warning", "medium") or risk == 3:
        return "MEDIUM"
    return "LOW"


def open_task(title: str, *, kind: str = "agent_turn", created_by: Optional[str] = None,
              agent_id: Optional[str] = None, channel: Optional[str] = None, source: Optional[str] = None,
              trace_id: Optional[str] = None, risk: int = 1, severity: Optional[str] = None,
              goal: Optional[str] = None, status: str = NEW, goal_id: Optional[str] = None,
              impact: str = "low", urgency: str = "low", deadline: Optional[str] = None,
              security_impact: bool = False, affected_users: int = 0) -> Optional[str]:
    task_id = f"OP-{uuid.uuid4().hex[:10]}"
    now = _now()
    prio = score_priority(impact=impact, urgency=urgency, deadline=deadline, security_impact=security_impact,
                          affected_users=affected_users, risk=risk, severity=severity)["priority"]
    try:
        _db().op_insert("op_tasks", {
            "task_id": task_id, "kind": kind, "title": str(title or "")[:300], "goal": goal, "goal_id": goal_id,
            "created_at": now, "updated_at": now, "created_by": created_by, "agent_id": agent_id,
            "channel": channel, "priority": prio, "risk": int(risk),
            "status": status, "current_step": 0, "source": source, "trace_id": trace_id,
        })
    except Exception as exc:  # noqa: BLE001
        logger.warning("[Ledger] Không mở được tác vụ: %s", exc)
        return None
    return task_id


def transition(task_id: Optional[str], status: str, **fields: Any) -> bool:
    """Chuyển trạng thái theo bảng TRANSITIONS. COMPLETED chỉ từ VERIFYING / ESCALATED."""
    if not task_id:
        return False
    if status not in STATES:
        raise InvalidTransition(status)
    task = _db().op_get_task(task_id)
    if task is None:
        return False
    cur = task["status"]
    if cur == status:
        if fields:
            _db().op_update("op_tasks", "task_id", task_id, {**fields, "updated_at": _now()})
        return True
    allowed = TRANSITIONS.get(cur, ())
    if status in (BLOCKED, ESCALATED, CANCELLED) and cur not in TERMINAL:
        pass
    elif status not in allowed:
        raise InvalidTransition(f"{cur} -> {status}")
    if status == COMPLETED and fields.get("verification_status", task.get("verification_status")) != "passed":
        raise InvalidTransition("COMPLETED cần kiểm chứng đạt (verification_status = passed)")
    _db().op_update("op_tasks", "task_id", task_id, {**fields, "status": status, "updated_at": _now()})
    return True


def _args_hash(args: Dict[str, Any]) -> str:
    blob = json.dumps(args or {}, sort_keys=True, ensure_ascii=False, default=str)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:16]


def add_evidence(task_id: str, *, source: str, kind: str, summary: str, ref: Optional[str] = None,
                 verified: bool = False) -> Optional[str]:
    """`kind`: FACT (đo / đọc lại được) hoặc INFERENCE (suy luận) — không trộn (§28)."""
    ev_id = f"EV-{uuid.uuid4().hex[:10]}"
    try:
        _db().op_insert("op_evidence", {"evidence_id": ev_id, "task_id": task_id, "collected_at": _now(),
                                        "source": source, "kind": kind, "summary": str(summary)[:500],
                                        "ref": ref, "verified": 1 if verified else 0})
    except Exception as exc:  # noqa: BLE001
        logger.warning("[Ledger] Không ghi được bằng chứng: %s", exc)
        return None
    return ev_id


def record_step(task_id: Optional[str], *, tool: str, target: str, args: Dict[str, Any],
                decision: Any, result: Any, verification: Dict[str, Any]) -> None:
    """Ghi một bước đã quyết định (+ kết quả + kiểm chứng) rồi cập nhật trạng thái tác vụ."""
    if not task_id:
        return
    from mateai.application.tasks.verification import tool_outcome
    try:
        steps = _db().op_steps(task_id)
        out = tool_outcome(result)
        ev = None
        if verification.get("checks"):
            ev = add_evidence(task_id, source=f"tool:{tool}", kind="FACT", summary="; ".join(verification["checks"]),
                              ref=f"args_sha256:{_args_hash(args)}", verified=verification.get("status") == "passed")
        now = _now()
        _db().op_insert("op_task_steps", {
            "step_id": f"ST-{uuid.uuid4().hex[:10]}", "task_id": task_id, "seq": len(steps) + 1,
            "tool": tool, "target": target, "args_hash": _args_hash(args),
            "decision": getattr(decision, "effect", None), "policy_rule": getattr(decision, "rule", None),
            "policy_version": getattr(decision, "policy_version", None), "risk": getattr(decision, "risk", None),
            "level": getattr(decision, "level", None), "started_at": now, "ended_at": now,
            "outcome": out["status"], "message": out["message"], "verification_level": verification.get("level"),
            "verification_status": verification.get("status"), "evidence_id": ev,
        })
        task = _db().op_get_task(task_id) or {}
        risk = max([int(task.get("risk") or 1)] + [int(getattr(decision, "risk", 1) or 1)])
        _db().op_update("op_tasks", "task_id", task_id, {"current_step": len(steps) + 1, "risk": risk,
                                                         "priority": priority_for("agent_turn", risk),
                                                         "updated_at": _now()})
        if out["approval_id"]:
            _db().op_update("op_tasks", "task_id", task_id, {"approval_id": str(out["approval_id"])})
        if task.get("status") in (NEW, ANALYZING, PLANNED, AUTHORIZED, WAITING_AUTHORIZATION, BLOCKED):
            transition(task_id, EXECUTING)
    except Exception as exc:  # noqa: BLE001
        logger.warning("[Ledger] Không ghi được bước %s: %s", tool, exc)


def settle(task_id: Optional[str]) -> Optional[str]:
    """Tính trạng thái tác vụ từ các bước (gọi khi lượt agent kết thúc / sau khi duyệt chạy)."""
    if not task_id:
        return None
    try:
        task = _db().op_get_task(task_id)
        if task is None or task["status"] in TERMINAL:
            return task and task["status"]
        steps = task["steps"]
        if not steps:
            return task["status"]
        latest: Dict[str, Dict[str, Any]] = {}
        for st in steps:  # bước sau của cùng tool + tham số (vd sau khi duyệt) thay bước trước
            latest[f"{st['tool']}|{st['args_hash']}"] = st
        vs = [st["verification_status"] for st in latest.values()]
        decisions = [st["decision"] for st in latest.values()]
        if any(v == "pending" for v in vs):
            transition(task_id, WAITING_AUTHORIZATION, verification_status="pending",
                       result_summary="Đang chờ người có quyền phê duyệt.")
        elif all(d == "deny" for d in decisions):
            transition(task_id, BLOCKED, verification_status="failed",
                       result_summary="Mọi bước bị chính sách từ chối.")
        elif all(v == "failed" for v in vs) or (any(v == "failed" for v in vs) and not any(v == "passed" for v in vs)):
            bad = [st for st in latest.values() if st["verification_status"] == "failed"]
            transition(task_id, VERIFYING)
            transition(task_id, FAILED, verification_status="failed",
                       result_summary="; ".join(f"{b['tool']}: {b['message'] or b['outcome']}" for b in bad)[:300])
        elif any(v == "not_verifiable" for v in vs) and not any(v == "passed" for v in vs):
            transition(task_id, VERIFYING)
            transition(task_id, ESCALATED, verification_status="not_verifiable",
                       result_summary="Đã thực thi nhưng không kiểm chứng được tự động — cần người xác nhận kết quả.")
        else:
            passed_cnt = sum(1 for v in vs if v == "passed")
            transition(task_id, VERIFYING)
            transition(task_id, COMPLETED, verification_status="passed",
                       result_summary=f"{passed_cnt}/{len(latest)} bước đã thực thi thành công.")
        return (_db().op_get_task(task_id) or {}).get("status")
    except Exception as exc:  # noqa: BLE001
        logger.warning("[Ledger] Không tổng kết được tác vụ %s: %s", task_id, exc)
        return None


def add_usage(task_id: Optional[str], usage: Optional[Dict[str, int]],
              by_model: Optional[Dict[str, Dict[str, int]]] = None) -> None:
    """Số lần gọi LLM + token THẬT do nhà cung cấp báo. Chi phí chỉ tính cho model đã khai giá
    trong model registry; token của model chưa có giá ghi riêng (`llm_unpriced_tokens`)."""
    if not task_id or not usage:
        return
    from mateai.infrastructure.llm.llm_provider import estimate_cost
    cost, unpriced = estimate_cost(by_model or {})
    try:
        _db().op_update("op_tasks", "task_id", task_id, {"llm_calls": int(usage.get("llm_calls") or 0),
                                                         "total_tokens": int(usage.get("total_tokens") or 0),
                                                         "llm_cost": cost, "llm_unpriced_tokens": unpriced})
    except Exception as exc:  # noqa: BLE001
        logger.warning("[Ledger] Không ghi được số token: %s", exc)


# ── Phân cấp mục tiêu (prompt cuối §42) ──────────────────────────────────────
GOAL_LEVELS = ("company", "department", "operational")


def create_goal(title: str, *, level: str, parent_id: Optional[str] = None, department: Optional[str] = None,
                owner: Optional[str] = None, due_date: Optional[str] = None) -> str:
    """company (không cha) -> department -> operational: cấp con đúng một bậc dưới cha."""
    if level not in GOAL_LEVELS:
        raise ValueError(f"Cấp mục tiêu phải thuộc {GOAL_LEVELS}")
    if level == "company":
        if parent_id:
            raise ValueError("Mục tiêu công ty không có mục tiêu cha.")
    else:
        parent = _db().op_get_goal(parent_id or "")
        if parent is None:
            raise ValueError("Không có mục tiêu cha này.")
        if GOAL_LEVELS.index(parent["level"]) != GOAL_LEVELS.index(level) - 1:
            raise ValueError(f"'{level}' phải nằm ngay dưới '{GOAL_LEVELS[GOAL_LEVELS.index(level) - 1]}'.")
    if not str(title or "").strip():
        raise ValueError("Mục tiêu cần tên.")
    goal_id = f"GOAL-{uuid.uuid4().hex[:8]}"
    _db().op_insert("op_goals", {"goal_id": goal_id, "parent_id": parent_id, "level": level,
                                 "title": str(title).strip()[:300], "department": department, "owner": owner,
                                 "status": "ACTIVE", "due_date": due_date, "created_at": _now()})
    return goal_id


def goal_tree(goal_id: Optional[str] = None) -> Any:
    """Cây mục tiêu kèm tiến độ THẬT (tác vụ COMPLETED / tổng, gộp cả cấp con)."""
    def build(g: Dict[str, Any]) -> Dict[str, Any]:
        children = [build(c) for c in _db().op_list_goals(parent_id=g["goal_id"])]
        own = _db().op_goal_task_counts([g["goal_id"]])
        tasks = own["tasks"] + sum(c["progress"]["tasks"] for c in children)
        done = own["completed"] + sum(c["progress"]["completed"] for c in children)
        return {**g, "children": children,
                "progress": {"tasks": tasks, "completed": done,
                             "percent": round(done / tasks * 100, 1) if tasks else 0.0}}
    if goal_id:
        g = _db().op_get_goal(goal_id)
        return build(g) if g else None
    return [build(g) for g in _db().op_list_goals(top_level=True)]


# ── Vòng đời sự cố (prompt cuối §88) ─────────────────────────────────────────
#: Pha xử lý sự cố — nằm cạnh trạng thái tác vụ (incident vẫn ESCALATED tới khi RESOLVED: AI
#: không tự xử lý sự cố). ESCALATED (pha) đến được từ mọi pha chưa đóng.
INCIDENT_PHASES = ("DETECTED", "TRIAGED", "INVESTIGATING", "MITIGATING", "VERIFYING", "RESOLVED", "ESCALATED")
_PHASE_NEXT: Dict[str, tuple] = {
    "DETECTED": ("TRIAGED", "INVESTIGATING"),
    "TRIAGED": ("INVESTIGATING", "MITIGATING"),
    "INVESTIGATING": ("MITIGATING", "VERIFYING"),
    "MITIGATING": ("VERIFYING", "INVESTIGATING"),
    "VERIFYING": ("RESOLVED", "INVESTIGATING", "MITIGATING"),
    "ESCALATED": ("TRIAGED", "INVESTIGATING", "MITIGATING", "VERIFYING"),
    "RESOLVED": (),
}


def incident_phase(task_id: str, phase: str, *, actor: str, note: str = "", owner: Optional[str] = None,
                   assets: Optional[str] = None) -> Dict[str, Any]:
    """Chuyển pha sự cố; mỗi bước thành một mục dòng thời gian (bằng chứng có tên người làm).
    RESOLVED chỉ từ VERIFYING — và đóng tác vụ COMPLETED (người đã kiểm chứng)."""
    task = get_task(task_id)
    if task is None or task.get("kind") != "incident":
        raise InvalidTransition("Không phải sự cố.")
    if phase not in INCIDENT_PHASES:
        raise InvalidTransition(f"Pha không hợp lệ: {phase}")
    cur = task.get("incident_phase") or "DETECTED"
    if task["status"] in TERMINAL or cur == "RESOLVED":
        raise InvalidTransition("Sự cố đã đóng.")
    if phase != cur and phase != "ESCALATED" and phase not in _PHASE_NEXT.get(cur, ()):
        raise InvalidTransition(f"{cur} -> {phase}")
    fields: Dict[str, Any] = {"incident_phase": phase, "updated_at": _now()}
    if owner is not None:
        fields["owner"] = owner.strip() or None
    if assets is not None:
        fields["affected_assets"] = assets.strip() or None
    _db().op_update("op_tasks", "task_id", task_id, fields)
    add_evidence(task_id, source=f"human:{actor}", kind="FACT", verified=True,
                 summary=f"{cur} -> {phase} ({actor}){': ' + note if note else ''}")
    if phase == "RESOLVED":
        transition(task_id, COMPLETED, verification_status="passed",
                   result_summary=f"Đã xử lý xong — {actor} kiểm chứng. {note}".strip()[:300])
    return get_task(task_id) or {}


def resolve_incident_by_probe(category: str, evidence: str) -> Optional[str]:
    """Sentinel đo lại thấy nguồn đã khôi phục: đóng sự cố bằng số đo THẬT (không đoán)."""
    try:
        existing = _db().op_find_open_incident(f"sentinel:{category}")
    except Exception:  # noqa: BLE001
        existing = None
    if not existing:
        return None
    tid = existing["task_id"]
    add_evidence(tid, source=f"sentinel:{category}", kind="FACT", verified=True,
                 summary=f"Đo lại: {evidence}")
    _db().op_update("op_tasks", "task_id", tid, {"incident_phase": "RESOLVED", "updated_at": _now()})
    transition(tid, COMPLETED, verification_status="passed",
               result_summary=f"Tự khôi phục — Sentinel đo lại xác nhận: {evidence}"[:300])
    return tid


def open_incident(category: str, title: str, message: str, severity: str = "critical") -> Optional[str]:
    """Sự cố do giám sát phát hiện: một tác vụ incident đang mở cho mỗi nguồn (không nhân bản)."""
    source = f"sentinel:{category}"
    try:
        existing = _db().op_find_open_incident(source)
    except Exception:  # noqa: BLE001
        existing = None
    if existing:
        add_evidence(existing["task_id"], source=source, kind="FACT", summary=f"Lặp lại: {title} — {message}")
        return existing["task_id"]
    tid = open_task(title, kind="incident", created_by="VN-MATEAI-SENTINEL", agent_id="VN-MATEAI-SENTINEL",
                    channel="sentinel", source=source, severity=severity, status=NEW,
                    impact="high", urgency="high")
    if tid:
        _db().op_update("op_tasks", "task_id", tid, {"incident_phase": "DETECTED", "affected_assets": category})
        add_evidence(tid, source=source, kind="FACT", summary=message)
        transition(tid, ESCALATED, result_summary="Đã cảnh báo người phụ trách; AI không tự xử lý sự cố.")
    return tid


def list_tasks(**kw: Any) -> List[Dict[str, Any]]:
    return _db().op_list_tasks(**kw)


def get_task(task_id: str) -> Optional[Dict[str, Any]]:
    return _db().op_get_task(task_id)
