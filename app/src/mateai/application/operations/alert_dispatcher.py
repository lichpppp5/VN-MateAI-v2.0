# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 Dương Thanh Lịch — VN-MateAI. See LICENSE and NOTICE.
"""
mateai/application/operations/alert_dispatcher.py
=================================================
Khâu cảnh báo DUY NHẤT của hệ thống: mọi sự cố (Sentinel, sơ đồ hệ thống,
webhook ngoài, email khẩn, cảnh báo dòng tiền) đi qua đây rồi tới MỌI kênh đã
kết nối — Telegram, Microsoft Teams, Email SMTP, Outlook (M365), Slack, Webhook.

Trước đây mỗi nơi tự gọi `telegram_gateway.send_incident_alert()` (Telegram là
kênh duy nhất), mỗi nơi một định dạng — Sentinel còn gửi cú pháp Markdown vào
kênh HTML nên tin hiện nguyên dấu `*`.

- Lọc theo mức (info / warning / critical): quy tắc chung + từng kênh.
- Chống lặp theo `category` (cooldown_s); thông báo "đã khôi phục" chỉ gửi khi
  sự cố đó đã từng được báo.
- Gửi song song, mỗi kênh một kết quả THẬT (đã gửi / chờ kết nối / lỗi + lý do),
  ghi lịch sử và phát sự kiện lên trang giám sát /admin/topology.
- `evaluate_topology()`: thành phần trên sơ đồ ở trạng thái Lỗi quá `down_after_s`
  -> cảnh báo; trở lại Hoạt động -> báo đã khôi phục.

Không bao giờ ném lỗi ra nơi gọi: cảnh báo hỏng không được làm hỏng nghiệp vụ.
"""
from __future__ import annotations

import asyncio
import logging
import sys
import time
from collections import deque
from typing import Any, Deque, Dict, List, Optional

from mateai.application.operations.topology_events import emit
from mateai.infrastructure.notifications import channels as ch

logger = logging.getLogger(__name__)

#: Trạng thái gần nhất của từng kênh (cho trang giám sát / API).
CHANNEL_STATE: Dict[str, Dict[str, Any]] = {}
HISTORY: Deque[Dict[str, Any]] = deque(maxlen=50)

_last_sent: Dict[str, float] = {}       # category -> lần gửi gần nhất
_open_incidents: Dict[str, Dict[str, Any]] = {}   # category -> cảnh báo đã gửi, chưa khôi phục
_watch: Dict[str, Dict[str, Any]] = {}  # node_id -> {"since", "alerted"}
_LOOP: Optional[asyncio.AbstractEventLoop] = None

TELEGRAM = "telegram"
TELEGRAM_NODE = "telegram"
#: Sentinel đã tự canh các phần này (mạng/LLM, AD, SQL, phần cứng) — sơ đồ không
#: báo trùng khi Sentinel đang chạy.
SENTINEL_COVERED = frozenset({"llm", "ad", "db", "core"})


def bind_loop(loop: asyncio.AbstractEventLoop) -> None:
    """Gọi lúc khởi động: cho phép `notify()` từ thread khác đẩy về event loop chính."""
    global _LOOP
    _LOOP = loop


def _rank(sev: str) -> int:
    return ch.SEVERITIES.index(sev) if sev in ch.SEVERITIES else 1


def channel_node(cid: str) -> str:
    return TELEGRAM_NODE if cid == TELEGRAM else ch.CHANNELS[cid]["node"]


def channel_status() -> List[Dict[str, Any]]:
    """Mọi kênh: đã kết nối chưa, thiếu khoá nào (chỉ TÊN), bật/tắt, kết quả gần nhất."""
    out = []
    try:
        from mateai.interfaces.telegram.telegram_gateway import telegram_gateway
        tg_ready = telegram_gateway._outbound_config() is not None
    except Exception:  # noqa: BLE001
        tg_ready = False
    out.append({"id": TELEGRAM, "display_name": "Telegram", "node": TELEGRAM_NODE,
                "configured": tg_ready, "enabled": tg_ready, "missing_fields": [] if tg_ready else ["telegram"],
                **CHANNEL_STATE.get(TELEGRAM, {})})
    for cid, spec in ch.CHANNELS.items():
        s = ch.load_settings(cid)
        missing = ch.missing_fields(cid, s)
        out.append({"id": cid, "display_name": spec["display_name"], "node": spec["node"],
                    "configured": not missing, "enabled": bool(s.get("enabled")),
                    "missing_fields": missing, "min_severity": s.get("min_severity") or None,
                    **CHANNEL_STATE.get(cid, {})})
    return out


def _record(cid: str, ok: Optional[bool], detail: str, alert: Dict[str, Any]) -> Dict[str, Any]:
    st = CHANNEL_STATE.setdefault(cid, {"sent": 0, "failed": 0})
    status = "ok" if ok else ("cancelled" if ok is None else "error")
    if ok:
        st["sent"] += 1
    elif ok is False:
        st["failed"] += 1
    if ok is not None:
        st.update(last_status=status, last_at=time.time(), last_detail=detail)
    emit("alert", stage="notify", source="alerts", target=channel_node(cid), status=status,
         detail=f"{alert['title']} — {detail}")
    return {"channel": cid, "status": status, "detail": detail}


async def _send_telegram(alert: Dict[str, Any]) -> Optional[bool]:
    from mateai.interfaces.telegram.telegram_gateway import telegram_gateway
    if telegram_gateway._outbound_config() is None:
        return None
    # Fire-and-forget phía gateway: True = đã xếp gửi; xác nhận 200 hiện ở sự kiện "alert_out".
    return bool(telegram_gateway.send_incident_alert(ch.format_telegram_html(alert)))


async def dispatch(
    title: str,
    message: str = "",
    *,
    severity: str = "warning",
    category: str = "general",
    source: str = "VN-MateAI",
    resolved: bool = False,
    force: bool = False,
    only: Optional[str] = None,
) -> Dict[str, Any]:
    """Gửi một cảnh báo tới mọi kênh đủ điều kiện. Trả kết quả từng kênh."""
    try:
        rules = ch.load_rules()
        now = time.time()
        alert = {"title": title, "message": message, "severity": severity if severity in ch.SEVERITIES else "warning",
                 "category": category, "source": source, "resolved": resolved, "time": now}
        if not resolved:
            # Kịch bản tự kích hoạt theo cảnh báo: xét MỌI cảnh báo, độc lập với ngưỡng / chống lặp của kênh gửi.
            try:
                from mateai.application.playbooks import triggers as _pb_triggers
                _pb_triggers.spawn_on_alert(alert)
            except Exception as _exc:  # noqa: BLE001
                logger.debug("[Alert] Không xét được kịch bản tự kích hoạt: %s", _exc)

        if resolved:
            if category not in _open_incidents and not force:
                return {"status": "skipped", "reason": "sự cố chưa từng được báo", "results": []}
            alert["severity"] = _open_incidents.pop(category, {}).get("severity", alert["severity"])
        elif not force:
            if _rank(alert["severity"]) < _rank(rules["min_severity"]):
                return {"status": "skipped", "reason": f"dưới mức {rules['min_severity']}", "results": []}
            if now - _last_sent.get(category, 0) < rules["cooldown_s"]:
                return {"status": "skipped", "reason": "đang trong thời gian chống lặp", "results": []}

        emit("alert", stage="dispatch", node="alerts", status="running",
             detail=("[khôi phục] " if resolved else f"[{alert['severity']}] ") + title)

        jobs: List[Any] = []
        names: List[str] = []
        if only in (None, TELEGRAM):
            names.append(TELEGRAM)
            jobs.append(_send_telegram(alert))
        for cid in ch.CHANNELS:
            if only not in (None, cid):
                continue
            s = ch.load_settings(cid)
            if ch.missing_fields(cid, s) or not s.get("enabled"):
                continue          # chờ kết nối / đã tắt: không gửi, không tính lỗi
            floor = s.get("min_severity")
            if not force and not resolved and floor in ch.SEVERITIES and _rank(alert["severity"]) < _rank(floor):
                continue
            names.append(cid)
            jobs.append(ch.send(cid, s, alert))

        raw = await asyncio.gather(*jobs, return_exceptions=True)
        results = []
        for cid, r in zip(names, raw):
            if isinstance(r, BaseException):
                results.append(_record(cid, False, f"lỗi {type(r).__name__}", alert))
            elif cid == TELEGRAM:
                if r is None:
                    if only == TELEGRAM:
                        results.append(_record(cid, None, "chờ kết nối (Telegram chưa bật gửi ra)", alert))
                    continue
                results.append(_record(cid, r, "đã xếp gửi" if r else "không xếp được", alert))
            else:
                ok, detail = r
                results.append(_record(cid, ok, detail, alert))

        delivered = sum(1 for r in results if r["status"] == "ok")
        if not resolved and delivered:
            _last_sent[category] = now
            _open_incidents[category] = alert
        entry = {"time": now, "title": title, "severity": alert["severity"], "category": category,
                 "source": source, "resolved": resolved, "delivered": delivered, "results": results}
        HISTORY.appendleft(entry)
        emit("alert", stage="dispatched", node="alerts",
             status="ok" if delivered else ("error" if results else "cancelled"),
             detail=f"{title}: {delivered}/{len(results)} kênh" + ("" if results else " — chưa kênh nào kết nối"))
        if not results:
            logger.warning("[Alerts] Không có kênh nào kết nối để gửi: %s", title)
        return {"status": "sent" if delivered else ("failed" if results else "no_channel"),
                "delivered": delivered, "results": results}
    except Exception as exc:  # noqa: BLE001
        logger.error("[Alerts] dispatch lỗi: %s", exc)
        return {"status": "error", "error": type(exc).__name__, "results": []}


def notify(title: str, message: str = "", **kw: Any) -> bool:
    """Gọi từ code đồng bộ / thread khác: đẩy `dispatch` vào event loop, không chờ."""
    try:
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            loop = None
        if loop is not None:
            loop.create_task(dispatch(title, message, **kw))
            return True
        if _LOOP is not None and _LOOP.is_running():
            asyncio.run_coroutine_threadsafe(dispatch(title, message, **kw), _LOOP)
            return True
        logger.warning("[Alerts] Chưa có event loop — không gửi được: %s", title)
    except Exception as exc:  # noqa: BLE001
        logger.error("[Alerts] notify lỗi: %s", exc)
    return False


def _sentinel_running() -> bool:
    mod = sys.modules.get("mateai.application.operations.autonomous_sentinel")
    s = getattr(mod, "autonomous_sentinel", None) if mod else None
    return bool(s and getattr(s, "_running", False))


async def evaluate_topology(snap: Dict[str, Any], now: Optional[float] = None) -> List[Dict[str, Any]]:
    """Theo dõi sơ đồ: Lỗi kéo dài -> cảnh báo; hồi phục -> báo đã khôi phục."""
    rules = ch.load_rules()
    if not rules.get("watch_topology"):
        return []
    now = time.time() if now is None else now
    ignore = set(rules["ignore_nodes"]) | (SENTINEL_COVERED if _sentinel_running() else set())
    sent = []
    for n in snap.get("nodes", []):
        nid = n["id"]
        if nid in ignore or nid == "alerts" or nid.startswith("notify:"):
            continue
        bad = n["status"] == "down" or (rules.get("alert_on_degraded") and n["status"] == "degraded")
        st = _watch.get(nid)
        if bad:
            if st is None:
                _watch[nid] = {"since": now, "alerted": False}
            elif not st["alerted"] and now - st["since"] >= rules["down_after_s"]:
                st["alerted"] = True
                sent.append(await dispatch(
                    f"{n['label']}: {'LỖI' if n['status'] == 'down' else 'SUY GIẢM'}",
                    f"{n.get('detail') or 'Không có mô tả'} (kéo dài {int(now - st['since'])} s)",
                    severity="critical" if n["status"] == "down" else "warning",
                    category=f"topology:{nid}", source="Sơ đồ hệ thống"))
        elif st is not None:
            _watch.pop(nid, None)
            if st["alerted"] and n["status"] == "ok":
                sent.append(await dispatch(f"{n['label']}: đã hoạt động trở lại", n.get("detail") or "",
                                           category=f"topology:{nid}", source="Sơ đồ hệ thống", resolved=True))
    return sent


def reset_for_tests() -> None:
    CHANNEL_STATE.clear()
    HISTORY.clear()
    _last_sent.clear()
    _open_incidents.clear()
    _watch.clear()
