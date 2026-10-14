# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 Dương Thanh Lịch — VN-MateAI. See LICENSE and NOTICE.
"""
mateai/application/playbooks/triggers.py
========================================
Kịch bản TỰ KÍCH HOẠT (docs/integrations/playbooks.md): theo lịch (cron), theo cảnh báo (mọi cảnh báo qua `alert_dispatcher`: giám sát
hạ tầng, sentinel, webhook), hoặc webhook riêng của kịch bản.

Không có đường vòng quanh chính sách: lượt tự kích hoạt chạy bằng `engine.start(..., human=False)` với danh tính admin đã đặt trình
kích hoạt (`triggers_owner`). Bước cần duyệt → MỘT phiếu duyệt cho cả kế hoạch, như lượt chạy tay; bước bị chặn → không chạy.

Chống bão: mỗi trình kích hoạt có thời gian nghỉ (`cooldown_min`); toàn hệ thống tối đa `playbook_triggers.max_auto_runs_per_hour` lượt;
khoá chống trùng (idempotency) theo phút (lịch) / theo khoảng nghỉ (cảnh báo) nên nhiều tiến trình không chạy hai lần. Cảnh báo do chính
kịch bản sinh ra (category `playbook*`) không kích hoạt kịch bản. Bật kill switch → không tự kích hoạt gì.
"""
from __future__ import annotations

import asyncio
import hashlib
import hmac
import logging
import re
import secrets
import time
from collections import deque
from datetime import datetime
from typing import Any, Deque, Dict, List, Optional, Set, Tuple

logger = logging.getLogger(__name__)

TYPES = ("schedule", "alert", "webhook")
SEVERITIES = ("info", "warning", "critical")
_TEMPLATE = re.compile(r"\{\{\s*([A-Za-z0-9_.\-]+)\s*\}\}")
_ALERT_FIELDS = ("title", "message", "severity", "category", "source")
_last_fire: Dict[Tuple[str, int], float] = {}
_recent_runs: Deque[float] = deque()
_loop_task: Optional["asyncio.Task[Any]"] = None


class TriggerError(ValueError):
    pass


def _cfg():
    from mateai.config.loader import settings
    return settings.playbook_triggers


# ── cron (5 trường: phút giờ ngày tháng thứ; thứ 0 = Chủ nhật) ───────────────

_CRON_RANGES = ((0, 59), (0, 23), (1, 31), (1, 12), (0, 6))


def parse_cron(expr: str) -> List[Set[int]]:
    fields = str(expr or "").split()
    if len(fields) != 5:
        raise TriggerError("cron cần đúng 5 trường: phút giờ ngày tháng thứ (vd «0 8 * * 1-5»)")
    out = []
    for raw, (lo, hi) in zip(fields, _CRON_RANGES):
        vals: Set[int] = set()
        for part in raw.split(","):
            m = re.match(r"^(\*|\d+(?:-\d+)?)(?:/(\d+))?$", part)
            if not m:
                raise TriggerError(f"cron: «{part}» không hợp lệ")
            base, step = m.group(1), int(m.group(2) or 1)
            if step < 1:
                raise TriggerError("cron: bước nhảy phải ≥ 1")
            top = 7 if hi == 6 else hi                   # thứ: nhận cả 7 = Chủ nhật
            a, b = (lo, hi) if base == "*" else (int(base.split("-")[0]), int(base.split("-")[-1]))
            if a < lo or b > top or a > b:
                raise TriggerError(f"cron: «{part}» ngoài khoảng {lo}–{hi}")
            vals.update(range(a, b + 1, step))
        if hi == 6 and 7 in vals:
            vals.discard(7)
            vals.add(0)
        out.append(vals)
    return out


def cron_matches(spec: List[Set[int]], dt: datetime) -> bool:
    weekday = (dt.weekday() + 1) % 7                     # Python: thứ Hai = 0 → cron: Chủ nhật = 0
    return dt.minute in spec[0] and dt.hour in spec[1] and dt.day in spec[2] and dt.month in spec[3] and weekday in spec[4]


# ── kiểm tra định nghĩa ─────────────────────────────────────────────────────

def validate(raw: Any, params: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Chuẩn hoá `triggers` của một kịch bản (gọi từ definition.validate_definition)."""
    if raw is None:
        return []
    if not isinstance(raw, list) or len(raw) > 5:
        raise TriggerError("`triggers` phải là danh sách (tối đa 5)")
    out = []
    for i, t in enumerate(raw, 1):
        where = f"Trình kích hoạt {i}"
        if not isinstance(t, dict) or t.get("type") not in TYPES:
            raise TriggerError(f"{where}: type phải thuộc {', '.join(TYPES)}")
        typ = t["type"]
        tp = t.get("params") or {}
        if not isinstance(tp, dict):
            raise TriggerError(f"{where}: params phải là đối tượng")
        for k, v in tp.items():
            if k not in params:
                raise TriggerError(f"{where}: kịch bản không có tham số «{k}»")
            for ref in _TEMPLATE.findall(str(v)):
                root = ref.split(".")[0]
                ok = (typ == "alert" and root == "alert" and ref.split(".")[1:2] and ref.split(".")[1] in _ALERT_FIELDS) or \
                     (typ == "webhook" and root == "payload" and len(ref.split(".")) >= 2)
                if not ok:
                    raise TriggerError(f"{where}: mẫu «{{{{{ref}}}}}» không dùng được ở trình kích hoạt {typ}"
                                       + (" (dùng alert.title / message / severity / category / source)" if typ == "alert" else
                                          " (dùng payload.<đường dẫn>)" if typ == "webhook" else " (lịch không có dữ liệu đầu vào)"))
        norm: Dict[str, Any] = {"type": typ, "params": tp}
        if typ == "schedule":
            parse_cron(t.get("cron"))
            norm["cron"] = " ".join(str(t["cron"]).split())
        else:
            try:
                norm["cooldown_min"] = max(1, min(int(t.get("cooldown_min", 10 if typ == "alert" else 1)), 1440))
            except (TypeError, ValueError):
                raise TriggerError(f"{where}: cooldown_min phải là số phút")
        if typ == "alert":
            m = t.get("match") or {}
            if not isinstance(m, dict):
                raise TriggerError(f"{where}: match phải là đối tượng")
            sev = m.get("severity")
            sev = [sev] if isinstance(sev, str) else (sev or [])
            if any(s not in SEVERITIES for s in sev):
                raise TriggerError(f"{where}: severity thuộc {', '.join(SEVERITIES)}")
            match = {"severity": list(sev), "category_prefix": str(m.get("category_prefix") or "")[:100],
                     "title_contains": str(m.get("title_contains") or "")[:200], "source": str(m.get("source") or "")[:100]}
            if not (match["category_prefix"] or match["title_contains"] or match["source"]):
                raise TriggerError(f"{where}: cần ít nhất một điều kiện category_prefix / title_contains / source "
                                   "(tránh chạy kịch bản cho MỌI cảnh báo)")
            if match["category_prefix"].startswith("playbook"):
                raise TriggerError(f"{where}: cảnh báo của chính kịch bản không dùng để kích hoạt (tránh vòng lặp)")
            norm["match"] = match
        out.append(norm)
    return out


# ── chạy ────────────────────────────────────────────────────────────────────

def _engine():
    from mateai.application.playbooks import engine
    return engine


def _blocked_globally() -> Optional[str]:
    from mateai.config.loader import settings
    if not _cfg().enabled:
        return "playbook_triggers.enabled = false"
    if settings.autonomy.kill_switch:
        return "kill switch đang bật"
    return None


def _storm_slot() -> bool:
    now = time.monotonic()
    while _recent_runs and now - _recent_runs[0] > 3600:
        _recent_runs.popleft()
    if len(_recent_runs) >= _cfg().max_auto_runs_per_hour:
        return False
    _recent_runs.append(now)
    return True


async def fire(pb: Dict[str, Any], index: int, ctx: Dict[str, Any], key: str, why: str) -> Dict[str, Any]:
    """Khởi động một lượt tự kích hoạt. Không ném: trả {"status": ...}."""
    from mateai.application.playbooks import definition as D
    defn = pb["definition"]
    trig = defn["triggers"][index]
    blocked = _blocked_globally()
    if blocked:
        return {"status": "skipped", "reason": blocked}
    cool = float(trig.get("cooldown_min", 0)) * 60
    last = _last_fire.get((pb["id"], index), 0.0)
    if cool and time.monotonic() - last < cool:
        return {"status": "skipped", "reason": "đang trong thời gian nghỉ"}
    if not _storm_slot():
        logger.warning("[PlaybookTrigger] Vượt %s lượt tự kích hoạt / giờ — bỏ qua %s", _cfg().max_auto_runs_per_hour, pb["id"])
        _audit("system", "playbook_trigger_storm", {"playbook_id": pb["id"], "trigger": trig["type"]}, "REJECTED")
        return {"status": "skipped", "reason": "vượt giới hạn lượt tự kích hoạt mỗi giờ"}
    _last_fire[(pb["id"], index)] = time.monotonic()
    owner = str(defn.get("triggers_owner") or pb.get("created_by") or "system")
    try:
        # Không "lenient": thiếu trường trong dữ liệu kích hoạt thì KHÔNG chạy (chạy với tham số giả còn tệ hơn không chạy).
        params = D.render(trig.get("params") or {}, ctx)
        out = await _engine().start(pb["id"], params, caller=owner, human=False, idempotency_key=key)
    except Exception as exc:  # noqa: BLE001 — thiếu tham số / quá số lượt đồng thời…
        logger.warning("[PlaybookTrigger] %s (%s) không khởi động được: %s", pb["id"], why, exc)
        _audit(owner, "playbook_trigger", {"playbook_id": pb["id"], "trigger": trig["type"], "why": why, "error": str(exc)[:300]}, "FAILED")
        return {"status": "error", "error": str(exc)[:300]}
    _audit(owner, "playbook_trigger", {"playbook_id": pb["id"], "trigger": trig["type"], "why": why, "run_id": out.get("run_id"),
                                       "result": out.get("status")})
    return out


def _audit(actor: str, action: str, details: Dict[str, Any], status: str = "SUCCESS") -> None:
    try:
        from mateai.application.security.zero_trust import log_security_audit
        log_security_audit(str(actor or "system"), action, "PLAYBOOK_TRIGGER", status, details)
    except Exception:  # noqa: BLE001
        pass


def _with_triggers(kind: str) -> List[Tuple[Dict[str, Any], int, Dict[str, Any]]]:
    out = []
    for pb in _engine().list_playbooks():
        if not pb["enabled"]:
            continue
        for i, t in enumerate(pb["definition"].get("triggers") or []):
            if t["type"] == kind:
                out.append((pb, i, t))
    return out


# — theo cảnh báo —

def alert_matches(match: Dict[str, Any], alert: Dict[str, Any]) -> bool:
    if match.get("severity") and alert.get("severity") not in match["severity"]:
        return False
    if match.get("category_prefix") and not str(alert.get("category") or "").startswith(match["category_prefix"]):
        return False
    if match.get("title_contains") and match["title_contains"].casefold() not in str(alert.get("title") or "").casefold():
        return False
    if match.get("source") and str(alert.get("source") or "") != match["source"]:
        return False
    return True


async def on_alert(alert: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Gọi từ alert_dispatcher.dispatch cho mỗi cảnh báo (không phải thông báo khôi phục)."""
    if alert.get("resolved") or str(alert.get("category") or "").startswith("playbook"):
        return []
    results = []
    for pb, i, t in _with_triggers("alert"):
        if not alert_matches(t["match"], alert):
            continue
        bucket = int(time.time() // (t["cooldown_min"] * 60))
        key = f"alert:{pb['id']}:{i}:{hashlib.sha1(str(alert.get('category')).encode()).hexdigest()[:8]}:{bucket}"
        ctx = {"alert": {k: alert.get(k) for k in _ALERT_FIELDS}}
        results.append(await fire(pb, i, ctx, key, f"cảnh báo «{str(alert.get('title'))[:80]}»"))
    return results


def spawn_on_alert(alert: Dict[str, Any]) -> None:
    try:
        asyncio.get_running_loop().create_task(on_alert(dict(alert)))
    except RuntimeError:
        pass                                             # không có vòng lặp (gọi đồng bộ): bỏ qua


# — theo lịch —

async def tick(now: Optional[datetime] = None) -> List[Dict[str, Any]]:
    now = (now or datetime.now()).replace(second=0, microsecond=0)
    results = []
    for pb, i, t in _with_triggers("schedule"):
        if cron_matches(parse_cron(t["cron"]), now):
            key = f"cron:{pb['id']}:{i}:{now.strftime('%Y%m%d%H%M')}"
            results.append(await fire(pb, i, {}, key, f"lịch «{t['cron']}» lúc {now.strftime('%H:%M %d/%m')}"))
    return results


async def _loop() -> None:
    last_minute = None
    while True:
        try:
            now = datetime.now().replace(second=0, microsecond=0)
            if now != last_minute:
                last_minute = now
                await tick(now)
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001
            logger.warning("[PlaybookTrigger] Vòng lịch lỗi: %s", exc)
        await asyncio.sleep(15)


def start_scheduler() -> None:
    global _loop_task
    if _loop_task is None or _loop_task.done():
        _loop_task = asyncio.get_running_loop().create_task(_loop())


# — webhook —

def _sha(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def issue_webhook_token(playbook_id: str, actor: str) -> str:
    pb = _engine().get_playbook(playbook_id)
    if not pb or not any(t["type"] == "webhook" for t in pb["definition"].get("triggers") or []):
        raise TriggerError("Kịch bản không có trình kích hoạt webhook")
    from mateai.infrastructure.database.db_manager import db_manager
    token = secrets.token_urlsafe(32)
    row = {"token_sha256": _sha(token), "created_by": actor, "created_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S")}
    if db_manager.dev_get("pb_webhook_tokens", playbook_id):
        db_manager.dev_update("pb_webhook_tokens", playbook_id, row)
    else:
        db_manager.dev_insert("pb_webhook_tokens", {"playbook_id": playbook_id, **row})
    _audit(actor, "playbook_webhook_token", {"playbook_id": playbook_id})
    return token


def verify_webhook_token(playbook_id: str, token: str) -> bool:
    from mateai.infrastructure.database.db_manager import db_manager
    row = db_manager.dev_get("pb_webhook_tokens", playbook_id)
    return bool(row and token and hmac.compare_digest(row["token_sha256"], _sha(token)))


async def on_webhook(playbook_id: str, payload: Dict[str, Any], idempotency_key: Optional[str]) -> Dict[str, Any]:
    pb = _engine().get_playbook(playbook_id)
    if not pb or not pb["enabled"]:
        raise TriggerError("Không có kịch bản này hoặc đang tắt")
    idx = next((i for i, t in enumerate(pb["definition"].get("triggers") or []) if t["type"] == "webhook"), None)
    if idx is None:
        raise TriggerError("Kịch bản không có trình kích hoạt webhook")
    key = f"webhook:{playbook_id}:{idempotency_key}" if idempotency_key else f"webhook:{playbook_id}:{secrets.token_hex(8)}"
    return await fire(pb, idx, {"payload": payload}, key, "webhook")
