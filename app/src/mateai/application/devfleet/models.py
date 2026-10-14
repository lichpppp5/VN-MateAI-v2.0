# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 Dương Thanh Lịch — VN-MateAI. See LICENSE and NOTICE.
"""
mateai/application/devfleet/models.py
=====================================
Mô hình thuần (không I/O) của module Dev Fleet: trạng thái canonical của worker / master, chuẩn hoá dữ liệu do
Ubuntu Master trả về, đặc tả tác vụ Dev và so khớp capability.

Nguyên tắc (prompt Dev Fleet §102, §40): không bịa số liệu — trường Master không cung cấp là `None` (UNKNOWN), và
trạng thái ONLINE chỉ được giữ khi còn bằng chứng đủ mới (`freshness`).
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List, Optional

# ── Trạng thái worker (canonical, prompt §12) ───────────────────────────────
UNKNOWN, DISCOVERED, ONLINE, IDLE, BUSY = "UNKNOWN", "DISCOVERED", "ONLINE", "IDLE", "BUSY"
DEGRADED, OFFLINE, DRAINING, MAINTENANCE, DISABLED = "DEGRADED", "OFFLINE", "DRAINING", "MAINTENANCE", "DISABLED"
WORKER_STATES = (UNKNOWN, DISCOVERED, ONLINE, IDLE, BUSY, DEGRADED, OFFLINE, DRAINING, MAINTENANCE, DISABLED)
#: Trạng thái còn "sống" (có bằng chứng Master thấy máy) — cần kiểm tra độ tươi.
LIVE_STATES = (ONLINE, IDLE, BUSY, DEGRADED)

FRESH, STALE, UNOBSERVED = "FRESH", "STALE", "UNOBSERVED"

MASTER_HEALTHY, MASTER_DEGRADED, MASTER_OFFLINE = "HEALTHY", "DEGRADED", "OFFLINE"

#: Phiên bản hợp đồng Master API mà bộ nối này hiểu (xem docs/integrations/dev-fleet.md).
SUPPORTED_API_MAJOR = "1"

# Trạng thái thực thi một lần chạy (run) — KHÁC trạng thái tác vụ: tác vụ do Task Ledger quản (một máy trạng thái).
RUN_PENDING_APPROVAL, RUN_DISPATCHING, RUN_QUEUED, RUN_RUNNING = "PENDING_APPROVAL", "DISPATCHING", "QUEUED", "RUNNING"
RUN_FINISHED, RUN_FAILED, RUN_CANCELLED = "FINISHED", "FAILED", "CANCELLED"
RUN_INTERRUPTED, RUN_UNKNOWN = "INTERRUPTED", "UNKNOWN"
RUN_ACTIVE = (RUN_PENDING_APPROVAL, RUN_DISPATCHING, RUN_QUEUED, RUN_RUNNING, RUN_UNKNOWN, RUN_INTERRUPTED)
RUN_TERMINAL = (RUN_FINISHED, RUN_FAILED, RUN_CANCELLED)

_MASTER_STATE_WORDS = {
    "online": ONLINE, "up": ONLINE, "ready": ONLINE, "active": ONLINE, "connected": ONLINE,
    "idle": IDLE, "free": IDLE,
    "busy": BUSY, "running": BUSY, "working": BUSY,
    "degraded": DEGRADED, "warning": DEGRADED, "unhealthy": DEGRADED,
    "offline": OFFLINE, "down": OFFLINE, "unreachable": OFFLINE, "disconnected": OFFLINE,
    "draining": DRAINING, "maintenance": MAINTENANCE, "disabled": DISABLED,
    "discovered": DISCOVERED, "pending": DISCOVERED, "unknown": UNKNOWN,
}

_CAP_RE = re.compile(r"[^a-z0-9._+-]+")


def normalise_capability(value: Any) -> str:
    """`Node.js` / `node js` -> `node.js` / `node-js`. Chỉ để so khớp, không đổi nghĩa."""
    return _CAP_RE.sub("-", str(value or "").strip().lower()).strip("-")


def capabilities_of(raw: Any) -> List[str]:
    if isinstance(raw, dict):                 # {"git": true, "docker": false}
        raw = [k for k, v in raw.items() if v]
    if not isinstance(raw, (list, tuple, set)):
        return []
    return sorted({c for c in (normalise_capability(x) for x in raw) if c})


def missing_capabilities(required: Iterable[str], available: Iterable[str]) -> List[str]:
    have = {normalise_capability(c) for c in available}
    return [c for c in (normalise_capability(r) for r in required) if c and c not in have]


def parse_time(value: Any) -> Optional[datetime]:
    """ISO-8601 hoặc epoch (giây / mili giây) -> datetime có múi giờ (UTC). Không đọc được -> None."""
    if value in (None, ""):
        return None
    try:
        if isinstance(value, (int, float)) or (isinstance(value, str) and re.fullmatch(r"\d+(\.\d+)?", value.strip())):
            num = float(value)
            if num > 1e11:                    # mili giây
                num /= 1000.0
            return datetime.fromtimestamp(num, tz=timezone.utc)
        text = str(value).strip().replace("Z", "+00:00")
        parsed = datetime.fromisoformat(text)
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
    except (ValueError, OverflowError, OSError):
        return None


def _num(value: Any) -> Optional[float]:
    if isinstance(value, bool) or value in (None, ""):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _pct(value: Any) -> Optional[float]:
    num = _num(value)
    return None if num is None else max(0.0, min(100.0, num))


def _text(value: Any, limit: int = 200) -> Optional[str]:
    if value in (None, ""):
        return None
    return str(value)[:limit]


def derive_freshness(last_seen: Optional[datetime], now: datetime, stale_after_s: float, offline_after_s: float) -> str:
    if last_seen is None:
        return UNOBSERVED
    age = (now - last_seen).total_seconds()
    if age > offline_after_s:
        return OFFLINE
    return STALE if age > stale_after_s else FRESH


def normalise_worker(raw: Dict[str, Any], *, now: datetime, stale_after_s: float, offline_after_s: float,
                     disabled: Iterable[str] = ()) -> Optional[Dict[str, Any]]:
    """Một worker do Master báo -> bản ghi chuẩn. Thiếu `worker_id` -> bỏ (None).

    Trạng thái cuối = trạng thái Master báo, nhưng: quá `offline_after_s` kể từ `last_seen` -> OFFLINE; quá
    `stale_after_s` -> giữ nguyên nhưng `freshness=STALE` (người đọc thấy rõ là cũ); worker bị tắt thủ công -> DISABLED.
    """
    if not isinstance(raw, dict):
        return None
    wid = _text(raw.get("worker_id") or raw.get("id"), 80)
    if not wid:
        return None
    declared = _MASTER_STATE_WORDS.get(str(raw.get("state") or raw.get("status") or "").strip().lower(), UNKNOWN)
    last_seen = parse_time(raw.get("last_seen"))
    freshness = derive_freshness(last_seen, now, stale_after_s, offline_after_s)
    state = declared
    if wid in set(disabled):
        state = DISABLED
    elif declared in LIVE_STATES and freshness == OFFLINE:
        state = OFFLINE
    elif declared in LIVE_STATES and freshness == UNOBSERVED:
        state = declared                       # Master khẳng định; `freshness` nói rõ là không có mốc thời gian
    return {
        "worker_id": wid,
        "hostname": _text(raw.get("hostname")),
        "platform": (_text(raw.get("platform"), 40) or "").lower() or None,
        "architecture": _text(raw.get("architecture") or raw.get("arch"), 40),
        "os_version": _text(raw.get("os_version")),
        "master_id": _text(raw.get("master_id"), 80),
        "openclaw_status": _text(raw.get("openclaw_status"), 40),
        "openclaw_version": _text(raw.get("openclaw_version"), 40),
        "agent_status": _text(raw.get("agent_status"), 40),
        "health_status": _text(raw.get("health_status"), 40),
        "state": state,
        "declared_state": declared,
        "freshness": freshness,
        "cpu_percent": _pct(raw.get("cpu") if raw.get("cpu") is not None else raw.get("cpu_percent")),
        "memory_percent": _pct(raw.get("memory") if raw.get("memory") is not None else raw.get("memory_percent")),
        "disk_percent": _pct(raw.get("disk") if raw.get("disk") is not None else raw.get("disk_percent")),
        "network": raw.get("network") if isinstance(raw.get("network"), dict) else None,
        "capabilities": capabilities_of(raw.get("capabilities")),
        "current_project": _text(raw.get("current_project"), 80),
        "current_task": _text(raw.get("current_task"), 80),
        "current_agent": _text(raw.get("current_agent"), 80),
        "last_seen": last_seen.isoformat() if last_seen else None,
        "labels": raw.get("labels") if isinstance(raw.get("labels"), (dict, list)) else None,
    }


def normalise_agent(raw: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    if not isinstance(raw, dict):
        return None
    aid = _text(raw.get("agent_id") or raw.get("id"), 80)
    if not aid:
        return None
    activity = parse_time(raw.get("last_activity"))
    return {
        "agent_id": aid, "worker_id": _text(raw.get("worker_id"), 80), "project_id": _text(raw.get("project_id"), 80),
        "role": _text(raw.get("role"), 60), "status": _text(raw.get("status"), 40), "runtime": _text(raw.get("runtime"), 60),
        "model": _text(raw.get("model"), 80), "provider": _text(raw.get("provider"), 60),
        "session_id": _text(raw.get("session_id"), 80), "current_task": _text(raw.get("current_task"), 80),
        "capabilities": capabilities_of(raw.get("capabilities")),
        "last_activity": activity.isoformat() if activity else None,
    }


def normalise_master(raw: Dict[str, Any]) -> Dict[str, Any]:
    m = raw.get("master") if isinstance(raw.get("master"), dict) else raw
    health = str(m.get("health") or "").strip().upper()
    return {
        "master_id": _text(m.get("master_id") or m.get("id"), 80), "name": _text(m.get("name"), 120),
        "hostname": _text(m.get("hostname")), "ip": _text(m.get("ip"), 60), "os": _text(m.get("os")),
        "version": _text(m.get("version"), 40),
        "ansible_status": _text(m.get("ansible_status"), 40), "router_status": _text(m.get("router_status"), 40),
        "openclaw_status": _text(m.get("openclaw_status"), 40), "fleet_status": _text(m.get("fleet_status"), 40),
        "health": health if health in (MASTER_HEALTHY, MASTER_DEGRADED, MASTER_OFFLINE) else None,
        "capacity": m.get("capacity") if isinstance(m.get("capacity"), dict) else None,
    }


# ── Đặc tả tác vụ Dev (prompt §24–§25) ──────────────────────────────────────
RISKS = {"low": 2, "medium": 3, "high": 4, "critical": 5}   # -> mức rủi ro của chính sách (1..5); >=3 cần duyệt
PRIORITIES = ("low", "medium", "high", "critical")


class SpecError(ValueError):
    """Đặc tả tác vụ thiếu / sai — thông báo nói rõ phải sửa gì."""


@dataclass
class TaskSpec:
    title: str
    objective: str
    acceptance_criteria: List[str]
    verification_steps: List[str]
    project_id: Optional[str] = None
    required_capabilities: List[str] = field(default_factory=list)
    required_platform: Optional[str] = None
    priority: str = "medium"
    risk: str = "medium"
    constraints: List[str] = field(default_factory=list)
    rollback: str = ""
    deadline: Optional[str] = None
    max_retries: int = 2
    repository: Optional[str] = None
    branch: Optional[str] = None
    #: Điều kiện bằng chứng để coi là hoàn thành (xem verification.py).
    require_build: bool = False
    require_tests: bool = False
    require_commit: bool = False
    allow_busy: bool = False
    worker_id: Optional[str] = None            # ép máy cụ thể (người giao) — vẫn qua kiểm capability + chính sách

    @property
    def risk_level(self) -> int:
        return RISKS[self.risk]

    def to_dict(self) -> Dict[str, Any]:
        return {k: getattr(self, k) for k in self.__dataclass_fields__}


def _str_list(value: Any, what: str, limit: int = 30) -> List[str]:
    if value in (None, ""):
        return []
    if isinstance(value, str):
        value = [value]
    if not isinstance(value, (list, tuple)):
        raise SpecError(f"`{what}` phải là danh sách")
    return [str(v).strip()[:500] for v in value if str(v).strip()][:limit]


def parse_task_spec(payload: Dict[str, Any]) -> TaskSpec:
    """Kiểm tra đặc tả. Một tác vụ chỉ có 'hãy sửa lỗi này' bị từ chối (prompt §25)."""
    if not isinstance(payload, dict):
        raise SpecError("Đặc tả tác vụ phải là một đối tượng")
    title = str(payload.get("title") or "").strip()
    objective = str(payload.get("objective") or "").strip()
    if not title:
        raise SpecError("Thiếu `title`")
    if len(objective) < 10:
        raise SpecError("Thiếu `objective` (mục tiêu cụ thể, ít nhất 10 ký tự)")
    criteria = _str_list(payload.get("acceptance_criteria"), "acceptance_criteria")
    if not criteria:
        raise SpecError("Thiếu `acceptance_criteria` — thế nào là xong? (ít nhất một tiêu chí kiểm chứng được)")
    steps = _str_list(payload.get("verification_steps"), "verification_steps")
    if not steps:
        raise SpecError("Thiếu `verification_steps` — kiểm chứng bằng cách nào? (vd chạy test, git diff)")
    risk = str(payload.get("risk") or "medium").strip().lower()
    if risk not in RISKS:
        raise SpecError(f"`risk` phải thuộc {', '.join(RISKS)}")
    priority = str(payload.get("priority") or "medium").strip().lower()
    if priority not in PRIORITIES:
        raise SpecError(f"`priority` phải thuộc {', '.join(PRIORITIES)}")
    rollback = str(payload.get("rollback") or "").strip()
    if RISKS[risk] >= 3 and not rollback:
        raise SpecError("Tác vụ rủi ro từ medium cần `rollback` (cách khôi phục nếu hỏng)")
    try:
        retries = int(payload.get("max_retries", 2))
    except (TypeError, ValueError):
        raise SpecError("`max_retries` phải là số nguyên")
    deadline = payload.get("deadline")
    if deadline and parse_time(deadline) is None:
        raise SpecError("`deadline` không đọc được (dùng ISO-8601)")
    flags = {k: bool(payload.get(k)) for k in ("require_build", "require_tests", "require_commit", "allow_busy")}
    repo = str(payload.get("repository") or "").strip() or None
    branch = str(payload.get("branch") or "").strip() or None
    if flags["require_commit"] and not repo:
        raise SpecError("`require_commit` cần `repository`")
    for label, val in (("repository", repo), ("branch", branch)):
        if val and (len(val) > 200 or re.search(r"[\s;|&`$<>]", val)):
            raise SpecError(f"`{label}` chứa ký tự không an toàn")
    return TaskSpec(
        title=title[:300], objective=objective[:2000], acceptance_criteria=criteria, verification_steps=steps,
        project_id=str(payload.get("project_id") or "").strip() or None,
        required_capabilities=capabilities_of(payload.get("required_capabilities")),
        required_platform=(str(payload.get("required_platform") or "").strip().lower() or None),
        priority=priority, risk=risk, constraints=_str_list(payload.get("constraints"), "constraints"),
        rollback=rollback[:1000], deadline=str(deadline) if deadline else None, max_retries=max(0, min(retries, 5)),
        repository=repo, branch=branch, worker_id=str(payload.get("worker_id") or "").strip() or None, **flags)
