# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 Dương Thanh Lịch — VN-MateAI. See LICENSE and NOTICE.
"""
mateai/application/orchestration/registry.py
============================================
Danh mục agent thực thi + thẻ năng lực + bảng điểm + định tuyến (docs/integrations/orchestration.md).

Ba loại agent thực thi — mỗi loại dựng trên khối đã có, không thêm đường gọi mới:
  - `local`    : vòng agent của VN-MateAI chạy với BỘ CÔNG CỤ THU HẸP (danh sách cho phép) dưới danh tính người giao —
                 mọi lời gọi công cụ vẫn qua `tool_gate`. Dùng để dựng vai Dev / BA / DBA / Quản trị hệ thống… bằng cấu hình.
  - `a2a`      : agent ngoài trong Danh bạ agent (`agent_gateway.directory`, kind a2a) — kết quả là dữ liệu không tin cậy.
  - `devfleet` : Dev Fleet (Ubuntu Master → worker OpenClaw) — đã có kiểm chứng build / test / commit bằng bằng chứng.

Định tuyến TẤT ĐỊNH (không hỏi LLM — ổn định, giải thích được): lọc theo năng lực, trạng thái, trần rủi ro, khả dụng; xếp theo
điểm thật (tỉ lệ làm xong ĐÃ KIỂM CHỨNG, làm trơn Laplace để agent mới không bị 0 hay 100%), rồi tải hiện tại, rồi tên.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta
from typing import Any, Dict, Iterable, List, Optional, Set

from mateai.application.orchestration.contracts import (
    EXECUTOR_ID_RE, ContractError, WorkOrder, normalize_capability,
)

logger = logging.getLogger(__name__)

TABLE = "orch_executors"
ORDERS = "orch_orders"
KINDS = ("local", "a2a", "devfleet")
ACTIVE, SUSPENDED = "active", "suspended"
MAX_TOOLS = 40
SCORE_WINDOW_DAYS = 90

#: Agent nội bộ không được cầm các công cụ này — chặn giao việc lồng nhau / tự mở rộng quyền.
FORBIDDEN_LOCAL_TOOLS = frozenset({
    "dispatch_work_order", "list_agent_capabilities", "get_work_order_status",
    "delegate_to_multi_agent", "create_new_skill", "install_skill_from_url", "reload_all_skills",
})

#: Trạng thái phiếu còn đang chiếm chỗ của agent (tính tải).
OPEN_STATES = ("PLANNED", "WAITING_APPROVAL", "DISPATCHED", "REVIEWING")


class RegistryError(ValueError):
    pass


def _db():
    from mateai.infrastructure.database.db_manager import db_manager
    return db_manager


def _now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _audit(actor: str, action: str, details: Dict[str, Any], status: str = "SUCCESS") -> None:
    try:
        from mateai.application.security.zero_trust import log_security_audit
        log_security_audit(str(actor or "system"), action, "ORCHESTRATION", status, details)
    except Exception:  # noqa: BLE001
        pass


def _jl(text: Any, default: Any) -> Any:
    try:
        return json.loads(text) if text else default
    except (TypeError, ValueError):
        return default


def known_tools() -> Set[str]:
    from mateai.application.playbooks.engine import known_tools as _known
    return _known()


# ── đọc ─────────────────────────────────────────────────────────────────────

def _view(row: Dict[str, Any]) -> Dict[str, Any]:
    return {"executor_id": row["executor_id"], "kind": row["kind"], "name": row["name"], "description": row.get("description") or "",
            "capabilities": _jl(row.get("capabilities_json"), []), "tools": _jl(row.get("tools_json"), []), "ref": row.get("ref"),
            "max_risk": int(row.get("max_risk") or 2), "max_concurrent": int(row.get("max_concurrent") or 2),
            "status": row["status"], "version": int(row.get("version") or 1), "created_by": row.get("created_by"),
            "created_at": row["created_at"], "updated_at": row["updated_at"]}


def get(executor_id: str) -> Optional[Dict[str, Any]]:
    row = _db().dev_get(TABLE, str(executor_id or "").strip().lower())
    return _view(row) if row else None


def list_all() -> List[Dict[str, Any]]:
    return [_view(r) for r in _db().dev_list(TABLE, order_by="name ASC", limit=500)]


# ── ghi ─────────────────────────────────────────────────────────────────────

def _caps(value: Any) -> List[str]:
    if isinstance(value, str):
        value = [v for v in value.replace(";", ",").split(",")]
    if not isinstance(value, (list, tuple)):
        raise RegistryError("`capabilities` phải là danh sách")
    try:
        caps = sorted({normalize_capability(v) for v in value if str(v or "").strip()})
    except ContractError as exc:
        raise RegistryError(str(exc)) from exc
    if not caps or len(caps) > 20:
        raise RegistryError("Cần 1–20 năng lực (vd `dba`, `sysadmin`, `ba`, `dev`)")
    return caps


def _int(v: Any, lo: int, hi: int, what: str) -> int:
    try:
        n = int(v)
    except (TypeError, ValueError):
        raise RegistryError(f"`{what}` phải là số nguyên") from None
    if not lo <= n <= hi:
        raise RegistryError(f"`{what}` phải từ {lo} đến {hi}")
    return n


def save(executor_id: str, payload: Dict[str, Any], actor: str) -> Dict[str, Any]:
    """Tạo / sửa agent thực thi. Mỗi lần sửa tăng `version` và vào audit (thay đổi cấu hình agent phải truy được)."""
    eid = str(executor_id or "").strip().lower()
    if not EXECUTOR_ID_RE.match(eid):
        raise RegistryError("Mã agent chỉ gồm a-z 0-9 _ - (2–32 ký tự)")
    prev = _db().dev_get(TABLE, eid) or {}
    kind = str(payload.get("kind") or prev.get("kind") or "").strip()
    if kind not in KINDS:
        raise RegistryError(f"`kind` phải thuộc {', '.join(KINDS)}")
    if prev and prev["kind"] != kind:
        raise RegistryError("Không đổi loại của agent đã có — xoá rồi tạo lại")
    row: Dict[str, Any] = {
        "kind": kind,
        "name": str(payload.get("name") or prev.get("name") or eid).strip()[:120],
        "description": str(payload.get("description", prev.get("description", "")) or "").strip()[:1000],
        "capabilities_json": json.dumps(_caps(payload.get("capabilities", _jl(prev.get("capabilities_json"), []))), ensure_ascii=False),
        "max_risk": _int(payload.get("max_risk", prev.get("max_risk", 2)), 1, 4, "max_risk"),   # 5 = L5, không agent nào nhận
        "max_concurrent": _int(payload.get("max_concurrent", prev.get("max_concurrent", 2)), 1, 10, "max_concurrent"),
        "updated_at": _now(),
    }
    tools: List[str] = []
    ref: Optional[str] = None
    if kind == "local":
        raw = payload.get("tools", _jl(prev.get("tools_json"), []))
        if isinstance(raw, str):
            raw = [t for t in raw.replace(";", ",").split(",")]
        tools = sorted({str(t).strip() for t in (raw or []) if str(t or "").strip()})
        if not tools or len(tools) > MAX_TOOLS:
            raise RegistryError(f"Agent nội bộ cần 1–{MAX_TOOLS} công cụ được phép (thu hẹp đúng việc của vai)")
        bad = sorted(set(tools) & FORBIDDEN_LOCAL_TOOLS)
        if bad:
            raise RegistryError(f"Agent nội bộ không được cầm công cụ giao việc / sinh mã: {', '.join(bad)}")
        missing = sorted(set(tools) - known_tools())
        if missing:
            raise RegistryError(f"Không có công cụ: {', '.join(missing)}")
    elif kind == "a2a":
        ref = str(payload.get("ref") or prev.get("ref") or eid).strip().lower()
        from mateai.application.agent_gateway import directory
        target = directory.get(ref)
        if not target or target.get("kind") != "a2a":
            raise RegistryError(f"Danh bạ agent không có agent A2A «{ref}» — thêm ở Cổng Agent trước")
    row["tools_json"] = json.dumps(tools, ensure_ascii=False)
    row["ref"] = ref
    if prev:
        row["version"] = int(prev.get("version") or 1) + 1
        _db().dev_update(TABLE, eid, row)
    else:
        _db().dev_insert(TABLE, {"executor_id": eid, "status": ACTIVE, "version": 1, "created_by": actor, "created_at": _now(), **row})
    _audit(actor, "orch_executor_save", {"executor_id": eid, "kind": kind, "version": row.get("version", 1),
                                         "capabilities": _jl(row["capabilities_json"], []), "tools": tools, "max_risk": row["max_risk"]})
    return get(eid)  # type: ignore[return-value]


def set_status(executor_id: str, status: str, actor: str) -> Dict[str, Any]:
    if status not in (ACTIVE, SUSPENDED):
        raise RegistryError("status phải là active hoặc suspended")
    eid = str(executor_id or "").strip().lower()
    if not _db().dev_get(TABLE, eid):
        raise RegistryError("Không có agent này")
    _db().dev_update(TABLE, eid, {"status": status, "updated_at": _now()})
    _audit(actor, "orch_executor_status", {"executor_id": eid, "status": status})
    return get(eid)  # type: ignore[return-value]


def delete(executor_id: str, actor: str) -> bool:
    eid = str(executor_id or "").strip().lower()
    busy = _db().dev_list(ORDERS, {"executor_id": eid, "status": list(OPEN_STATES)}, limit=1)
    if busy:
        raise RegistryError("Agent đang có phiếu việc chưa xong — tạm dừng agent, chờ xong rồi xoá")
    n = _db().dev_delete(TABLE, eid)
    if n:
        _audit(actor, "orch_executor_delete", {"executor_id": eid})
    return bool(n)


# ── khả dụng ────────────────────────────────────────────────────────────────

def availability(ex: Dict[str, Any]) -> Optional[str]:
    """None nếu nhận việc được ngay bây giờ; ngược lại lý do (tiếng Việt)."""
    if ex["status"] != ACTIVE:
        return "đang tạm dừng"
    try:
        from mateai.config.loader import settings
        if ex["executor_id"] in set(settings.autonomy.disabled_agents or []):
            return "bị tắt trong Kiểm soát tự trị"
    except Exception:  # noqa: BLE001
        pass
    if ex["kind"] == "local":
        missing = sorted(set(ex["tools"]) - known_tools())
        if missing:
            return f"thiếu công cụ {', '.join(missing[:5])}"
    elif ex["kind"] == "a2a":
        from mateai.application.agent_gateway import directory
        target = directory.get(ex.get("ref") or "")
        if not target:
            return "agent A2A đã bị xoá khỏi danh bạ"
        if target.get("status") != ACTIVE:
            return "agent A2A đang tạm dừng trong danh bạ"
        if not (target.get("card") or {}).get("url"):
            return "chưa đọc được thẻ agent A2A"
    elif ex["kind"] == "devfleet":
        try:
            from mateai.application.devfleet.service import dev_fleet
            if dev_fleet.mode == "disabled":
                return "Dev Fleet đang tắt"
        except Exception as exc:  # noqa: BLE001
            return f"Dev Fleet không dùng được: {exc}"
    return None


# ── bảng điểm ───────────────────────────────────────────────────────────────

def _since(days: int) -> str:
    return (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d %H:%M:%S")


def _seconds(a: Optional[str], b: Optional[str]) -> Optional[float]:
    try:
        return (datetime.strptime(str(b), "%Y-%m-%d %H:%M:%S") - datetime.strptime(str(a), "%Y-%m-%d %H:%M:%S")).total_seconds()
    except (TypeError, ValueError):
        return None


def scorecard(executor_id: str, rows: Optional[Iterable[Dict[str, Any]]] = None, days: int = SCORE_WINDOW_DAYS) -> Dict[str, Any]:
    """Số liệu THẬT từ phiếu việc trong `days` ngày. `score` = (xong đã kiểm chứng + 1) / (đã kết thúc + 2)."""
    since = _since(days)
    if rows is None:
        rows = _db().dev_list(ORDERS, {"executor_id": executor_id}, order_by="created_at DESC", limit=1000)
    rows = [r for r in rows if str(r.get("created_at") or "") >= since]
    by: Dict[str, int] = {}
    for r in rows:
        by[r["status"]] = by.get(r["status"], 0) + 1
    completed = by.get("COMPLETED", 0)
    failed = by.get("FAILED", 0)
    finished = completed + failed
    durations = [d for d in (_seconds(r.get("dispatched_at"), r.get("finished_at")) for r in rows if r["status"] == "COMPLETED")
                 if d is not None and d >= 0]
    durations.sort()
    return {"executor_id": executor_id, "window_days": days, "total": len(rows), "by_status": by,
            "completed": completed, "failed": failed, "needs_confirmation": by.get("NEEDS_CONFIRMATION", 0),
            "open": sum(by.get(s, 0) for s in OPEN_STATES),
            "success_rate": round(completed / finished, 3) if finished else None,
            "score": round((completed + 1) / (finished + 2), 3),
            "median_seconds": durations[len(durations) // 2] if durations else None}


def scoreboard(days: int = SCORE_WINDOW_DAYS) -> List[Dict[str, Any]]:
    since = _since(days)
    rows = [r for r in _db().dev_list(ORDERS, order_by="created_at DESC", limit=1000) if str(r.get("created_at") or "") >= since]
    out = []
    for ex in list_all():
        mine = [r for r in rows if r.get("executor_id") == ex["executor_id"]]
        out.append({**ex, "available": availability(ex) is None, "unavailable_reason": availability(ex),
                    "scorecard": scorecard(ex["executor_id"], mine, days)})
    return out


# ── định tuyến ──────────────────────────────────────────────────────────────

def route(order: WorkOrder, *, exclude: Iterable[str] = ()) -> Dict[str, Any]:
    """Chọn agent cho phiếu. Trả `executor_id` (hoặc None) + bảng ứng viên kèm lý do — người xem hiểu vì sao."""
    excluded = set(exclude)
    candidates, rejected = [], []
    pool = [get(order.executor_id)] if order.executor_id else list_all()
    if order.executor_id and pool[0] is None:
        return {"executor_id": None, "candidates": [], "rejected": [{"executor_id": order.executor_id, "reason": "không có agent này"}],
                "error": f"Không có agent «{order.executor_id}»"}
    for ex in pool:
        eid = ex["executor_id"]
        if eid in excluded:
            rejected.append({"executor_id": eid, "reason": "đã thử ở lượt trước"})
            continue
        if order.capability not in ex["capabilities"]:
            if order.executor_id:
                rejected.append({"executor_id": eid, "reason": f"không có năng lực `{order.capability}`"})
            continue
        why = availability(ex)
        if why:
            rejected.append({"executor_id": eid, "reason": why})
            continue
        if order.risk > ex["max_risk"]:
            rejected.append({"executor_id": eid, "reason": f"phiếu rủi ro {order.risk} vượt trần {ex['max_risk']} của agent"})
            continue
        sc = scorecard(eid)
        candidates.append({"executor_id": eid, "name": ex["name"], "kind": ex["kind"], "score": sc["score"], "open": sc["open"],
                           "max_concurrent": ex["max_concurrent"], "completed": sc["completed"], "failed": sc["failed"]})
    candidates.sort(key=lambda c: (-c["score"], c["open"] / max(1, c["max_concurrent"]), c["executor_id"]))
    if not candidates:
        return {"executor_id": None, "candidates": [], "rejected": rejected,
                "error": f"Không có agent nào nhận được năng lực `{order.capability}`" + ("" if not rejected else " (xem lý do từng agent)")}
    top = candidates[0]
    reason = (f"năng lực `{order.capability}`, điểm {top['score']} ({top['completed']} xong / {top['failed']} thất bại), "
              f"đang làm {top['open']}/{top['max_concurrent']}")
    return {"executor_id": top["executor_id"], "reason": reason, "candidates": candidates, "rejected": rejected, "error": None}


def capabilities_catalog() -> List[Dict[str, Any]]:
    """Năng lực → các agent nhận được (cho AI chọn đúng `capability` khi giao việc)."""
    cat: Dict[str, List[Dict[str, Any]]] = {}
    for ex in list_all():
        for cap in ex["capabilities"]:
            cat.setdefault(cap, []).append({"executor_id": ex["executor_id"], "name": ex["name"], "kind": ex["kind"],
                                            "available": availability(ex) is None, "description": ex["description"][:200]})
    return [{"capability": k, "executors": v} for k, v in sorted(cat.items())]
