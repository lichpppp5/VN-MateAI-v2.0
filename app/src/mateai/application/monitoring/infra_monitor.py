# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 Dương Thanh Lịch — VN-MateAI. See LICENSE and NOTICE.
"""
mateai/application/monitoring/infra_monitor.py
==============================================
Giám sát hạ tầng bằng dữ liệu Prometheus / Grafana của doanh nghiệp (docs/integrations/monitoring.md).

Nguồn dữ liệu = các nguồn trong hub kết nối có `monitor_type` ("prometheus" | "grafana"); mẫu của chúng khai báo sẵn các truy vấn
ĐẶT TÊN mà module này dùng. Module chỉ ĐỌC (GET) — không bao giờ ghi vào Prometheus / Grafana.

  Prometheus : cảnh báo (/api/v1/alerts) · target up/down (/api/v1/targets) · CPU / RAM / đĩa theo máy (PromQL node_exporter hoặc
               windows_exporter) · truy vấn PromQL tuỳ ý cho AI (chỉ đọc, có giới hạn)
  Grafana    : cảnh báo đang bật (Alertmanager nội bộ) · trạng thái quy tắc · danh sách dashboard

Nguyên tắc: không bịa số liệu (không có số đo -> `available=false` + lý do); một nguồn hỏng không làm mất nguồn khác; cảnh báo ngoài
đạt mức `incident_min_severity` được mở thành SỰ CỐ trong sổ tác vụ (AI không tự xử lý — người phụ trách) và báo qua kênh cảnh báo;
cảnh báo hết bắn thì sự cố được đóng bằng bằng chứng "nguồn không còn báo".
"""
from __future__ import annotations

import asyncio
import hashlib
import logging
import re
import time
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

PROM_Q = {"alerts": "canh bao", "targets": "muc tieu", "query": "truy van"}
GRAFANA_Q = {"alerts": "canh bao dang bat", "rules": "quy tac", "dashboards": "dashboard"}

#: PromQL mặc định (thử lần lượt: node_exporter -> windows_exporter; truy vấn đầu tiên có dữ liệu thắng).
METRIC_QUERIES: Dict[str, List[str]] = {
    "cpu": [
        '100 - (avg by (instance) (rate(node_cpu_seconds_total{mode="idle"}[5m])) * 100)',
        '100 - (avg by (instance) (rate(windows_cpu_time_total{mode="idle"}[5m])) * 100)',
    ],
    "memory": [
        '(1 - node_memory_MemAvailable_bytes / node_memory_MemTotal_bytes) * 100',
        '100 - (windows_os_physical_memory_free_bytes / windows_cs_physical_memory_bytes * 100)',
    ],
    "disk": [
        '(1 - node_filesystem_avail_bytes{mountpoint="/",fstype!~"tmpfs|overlay"} / node_filesystem_size_bytes{mountpoint="/",fstype!~"tmpfs|overlay"}) * 100',
        '100 - (windows_logical_disk_free_bytes{volume="C:"} / windows_logical_disk_size_bytes{volume="C:"} * 100)',
    ],
}
SEVERITY_RANK = {"info": 0, "warning": 1, "critical": 2}
_CRIT = {"critical", "crit", "page", "high", "error", "fatal", "emergency"}
_WARN = {"warning", "warn", "medium", "moderate"}
MAX_PROMQL = 600
_FORBIDDEN_PROMQL = re.compile(r"[\x00-\x08\x0b-\x1f]")

_cache: Dict[str, Any] = {"at": 0.0, "data": None}
_lock: Optional[asyncio.Lock] = None
_poller: Optional["asyncio.Task[Any]"] = None
_opened: Dict[str, str] = {}                       # category -> tiêu đề, các sự cố do module này đã mở trong phiên chạy này


def _cfg():
    from mateai.config.loader import settings
    return settings.monitoring


def monitor_sources(include_secrets: bool = True) -> List[Dict[str, Any]]:
    from mateai.infrastructure.connectors import custom_registry
    out = []
    for item in custom_registry.list_sources(include_secrets=False):
        if item.get("monitor_type") and item.get("enabled", True):
            src = custom_registry.get_source(item["id"], include_secrets=include_secrets)
            if src:
                out.append(src)
    return out


def severity_of(labels: Dict[str, Any]) -> str:
    raw = str((labels or {}).get("severity") or (labels or {}).get("priority") or "").strip().lower()
    if raw in _CRIT:
        return "critical"
    if raw in _WARN:
        return "warning"
    return "info"


def _fp(*parts: Any) -> str:
    return hashlib.sha1("|".join(str(p) for p in parts).encode("utf-8")).hexdigest()[:12]


def _f(value: Any) -> Optional[float]:
    try:
        num = float(value)
        return num if num == num and abs(num) != float("inf") else None
    except (TypeError, ValueError):
        return None


def _trim(labels: Any, keep: int = 8) -> Dict[str, str]:
    return {str(k)[:40]: str(v)[:120] for k, v in list((labels or {}).items())[:keep]} if isinstance(labels, dict) else {}


# ── Chuẩn hoá phản hồi thô ───────────────────────────────────────────────────

def parse_prom_alerts(payload: Any, src: Dict[str, Any]) -> List[Dict[str, Any]]:
    alerts = (((payload or {}).get("data") or {}).get("alerts")) if isinstance(payload, dict) else None
    out = []
    for a in alerts or []:
        labels = a.get("labels") or {}
        ann = a.get("annotations") or {}
        name = str(labels.get("alertname") or "alert")
        inst = str(labels.get("instance") or labels.get("job") or "")
        out.append({"id": _fp(src["id"], name, inst, labels.get("severity")), "source": src["id"], "source_type": "prometheus",
                    "name": name, "severity": severity_of(labels), "state": str(a.get("state") or "firing").lower(),
                    "summary": str(ann.get("summary") or ann.get("description") or "")[:300], "instance": inst,
                    "since": a.get("activeAt"), "value": a.get("value"), "labels": _trim(labels)})
    return out


def parse_prom_targets(payload: Any, src: Dict[str, Any]) -> Dict[str, Any]:
    items = (((payload or {}).get("data") or {}).get("activeTargets")) if isinstance(payload, dict) else None
    items = items or []
    down = []
    up = 0
    for t in items:
        health = str(t.get("health") or "unknown").lower()
        if health == "up":
            up += 1
        elif health == "down":
            lb = t.get("labels") or {}
            down.append({"source": src["id"], "job": str(lb.get("job") or ""), "instance": str(lb.get("instance") or t.get("scrapeUrl") or ""),
                         "error": str(t.get("lastError") or "")[:200], "last_scrape": t.get("lastScrape")})
    return {"total": len(items), "up": up, "down": len(down), "unknown": len(items) - up - len(down), "down_list": down[:50]}


def parse_vector(payload: Any) -> List[Tuple[str, float]]:
    """Kết quả instant-vector -> [(nhãn máy, giá trị)]; bỏ NaN / không phải số."""
    result = (((payload or {}).get("data") or {}).get("result")) if isinstance(payload, dict) else None
    out = []
    for r in result or []:
        metric = r.get("metric") or {}
        val = r.get("value") or [None, None]
        num = _f(val[1] if len(val) > 1 else None)
        if num is not None:
            out.append((str(metric.get("instance") or metric.get("job") or "(toàn cục)"), num))
    return out


def parse_grafana_alerts(payload: Any, src: Dict[str, Any]) -> List[Dict[str, Any]]:
    items = payload if isinstance(payload, list) else (payload or {}).get("data") if isinstance(payload, dict) else []
    out = []
    for a in items or []:
        if not isinstance(a, dict):
            continue
        state = str(((a.get("status") or {}).get("state")) or "active").lower()
        if state not in ("active", "firing"):
            continue                                        # bỏ cảnh báo đã tắt tiếng / chặn
        labels = a.get("labels") or {}
        ann = a.get("annotations") or {}
        name = str(labels.get("alertname") or "grafana-alert")
        out.append({"id": _fp(src["id"], a.get("fingerprint") or name), "source": src["id"], "source_type": "grafana", "name": name,
                    "severity": severity_of(labels), "state": "firing", "summary": str(ann.get("summary") or ann.get("description") or "")[:300],
                    "instance": str(labels.get("instance") or labels.get("grafana_folder") or ""), "since": a.get("startsAt"),
                    "value": None, "labels": _trim(labels)})
    return out


def parse_grafana_rules(payload: Any) -> Dict[str, int]:
    groups = (((payload or {}).get("data") or {}).get("groups")) if isinstance(payload, dict) else None
    counts = {"total": 0, "firing": 0, "pending": 0, "normal": 0, "error": 0}
    for g in groups or []:
        for r in g.get("rules") or []:
            counts["total"] += 1
            st = str(r.get("state") or "").lower()
            if st in ("firing", "alerting"):
                counts["firing"] += 1
            elif st == "pending":
                counts["pending"] += 1
            else:
                counts["normal"] += 1
            if str(r.get("health") or "").lower() == "error":
                counts["error"] += 1
    return counts


def parse_dashboards(payload: Any, src: Dict[str, Any]) -> List[Dict[str, Any]]:
    base = str(src.get("base_url") or "").rstrip("/")
    out = []
    for d in payload if isinstance(payload, list) else []:
        if isinstance(d, dict) and d.get("title"):
            url = str(d.get("url") or "")
            out.append({"title": str(d["title"])[:120], "folder": str(d.get("folderTitle") or ""), "tags": list(d.get("tags") or [])[:6],
                        "url": (base + url) if url.startswith("/") and base else url})
    return out[:100]


def level_for(metric: str, value: float) -> str:
    c = _cfg()
    warn, crit = getattr(c, f"{metric}_warn"), getattr(c, f"{metric}_crit")
    return "crit" if value >= crit else "warn" if value >= warn else "ok"


# ── Thu thập ────────────────────────────────────────────────────────────────

async def _raw(src: Dict[str, Any], name: str, args: Optional[Dict[str, Any]] = None) -> Tuple[Any, Optional[str], float]:
    from mateai.infrastructure.connectors.generic_connector import fetch_raw_query
    res = await fetch_raw_query(src, name, args)
    return (res.data if res.success else None), (None if res.success else (res.error or "lỗi không rõ")), res.latency_ms


async def _collect_prometheus(src: Dict[str, Any]) -> Dict[str, Any]:
    out: Dict[str, Any] = {"id": src["id"], "title": src.get("title"), "type": "prometheus", "reachable": False, "error": None,
                           "latency_ms": None, "alerts": [], "targets": None, "metrics": [], "metrics_note": None}
    (alerts_p, alerts_e, lat), (targets_p, targets_e, _) = await asyncio.gather(_raw(src, PROM_Q["alerts"]), _raw(src, PROM_Q["targets"]))
    out["latency_ms"] = round(lat, 1) if lat else None
    if alerts_e and targets_e:
        out["error"] = alerts_e
        return out
    out["reachable"] = True
    if alerts_p is not None:
        out["alerts"] = parse_prom_alerts(alerts_p, src)
    if targets_p is not None:
        out["targets"] = parse_prom_targets(targets_p, src)
    errors = [e for e in (alerts_e, targets_e) if e]
    if errors:
        out["error"] = "một phần dữ liệu lỗi: " + "; ".join(errors)[:200]
    # CPU / RAM / đĩa theo máy
    rows: Dict[Tuple[str, str], float] = {}
    notes = []
    for metric, candidates in METRIC_QUERIES.items():
        got = False
        for expr in candidates:
            payload, err, _ = await _raw(src, PROM_Q["query"], {"promql": expr})
            if err:
                notes.append(f"{metric}: {err[:80]}")
                break
            vec = parse_vector(payload)
            if vec:
                for inst, val in vec:
                    rows[(inst, metric)] = round(val, 1)
                got = True
                break
        if not got and not any(n.startswith(metric) for n in notes):
            notes.append(f"{metric}: chưa có số đo (exporter chưa chạy hoặc khác tên metric)")
    out["metrics"] = [{"instance": i, "metric": m, "value": v, "level": level_for(m, v)} for (i, m), v in sorted(rows.items())]
    out["metrics_note"] = "; ".join(notes) if notes else None
    return out


async def _collect_grafana(src: Dict[str, Any]) -> Dict[str, Any]:
    out: Dict[str, Any] = {"id": src["id"], "title": src.get("title"), "type": "grafana", "reachable": False, "error": None,
                           "latency_ms": None, "alerts": [], "rules": None, "dashboards": [], "base_url": src.get("base_url")}
    (alerts_p, alerts_e, lat), (rules_p, rules_e, _), (dash_p, dash_e, _) = await asyncio.gather(
        _raw(src, GRAFANA_Q["alerts"]), _raw(src, GRAFANA_Q["rules"]), _raw(src, GRAFANA_Q["dashboards"], {"tu_khoa": ""}))
    out["latency_ms"] = round(lat, 1) if lat else None
    if alerts_e and rules_e and dash_e:
        out["error"] = alerts_e
        return out
    out["reachable"] = True
    if alerts_p is not None:
        out["alerts"] = parse_grafana_alerts(alerts_p, src)
    if rules_p is not None:
        out["rules"] = parse_grafana_rules(rules_p)
    if dash_p is not None:
        out["dashboards"] = parse_dashboards(dash_p, src)
    errors = [e for e in (alerts_e, rules_e, dash_e) if e]
    if errors:
        out["error"] = "một phần dữ liệu lỗi: " + "; ".join(errors)[:200]
    return out


def summarise(sources: List[Dict[str, Any]]) -> Dict[str, Any]:
    alerts = [a for s in sources for a in s.get("alerts", [])]
    firing = [a for a in alerts if a["state"] == "firing"]
    crit = [a for a in firing if a["severity"] == "critical"]
    warn = [a for a in firing if a["severity"] == "warning"]
    targets = [s["targets"] for s in sources if s.get("targets")]
    reachable = [s for s in sources if s["reachable"]]
    if not sources:
        health = "UNKNOWN"
    elif not reachable:
        health = "UNKNOWN"
    elif crit or any(t["down"] for t in targets) or any(m["level"] == "crit" for s in sources for m in s.get("metrics", [])):
        health = "CRITICAL"
    elif warn or len(reachable) < len(sources) or any(m["level"] == "warn" for s in sources for m in s.get("metrics", [])):
        health = "DEGRADED"
    else:
        health = "HEALTHY"
    return {"health": health, "sources_total": len(sources), "sources_reachable": len(reachable),
            "alerts_firing": len(firing), "alerts_critical": len(crit), "alerts_warning": len(warn),
            "targets_total": sum(t["total"] for t in targets), "targets_down": sum(t["down"] for t in targets),
            "dashboards": sum(len(s.get("dashboards", [])) for s in sources)}


async def snapshot(force: bool = False) -> Dict[str, Any]:
    """Ảnh chụp giám sát (có bộ đệm ngắn). Không có nguồn giám sát nào -> `configured=false`, không giả số liệu."""
    global _lock
    cfg = _cfg()
    if not cfg.enabled:
        return {"configured": False, "enabled": False, "sources": [], "alerts": [], "summary": summarise([]), "checked_at": None}
    if not force and _cache["data"] is not None and (time.monotonic() - _cache["at"]) < cfg.cache_ttl_s:
        return _cache["data"]
    if _lock is None:
        _lock = asyncio.Lock()
    async with _lock:
        if not force and _cache["data"] is not None and (time.monotonic() - _cache["at"]) < cfg.cache_ttl_s:
            return _cache["data"]
        srcs = monitor_sources()

        async def one(s: Dict[str, Any]) -> Dict[str, Any]:
            try:
                return await (_collect_prometheus(s) if s["monitor_type"] == "prometheus" else _collect_grafana(s))
            except Exception as exc:  # noqa: BLE001
                logger.exception("[Monitoring] nguồn %s lỗi", s.get("id"))
                return {"id": s["id"], "title": s.get("title"), "type": s["monitor_type"], "reachable": False,
                        "error": f"{type(exc).__name__}: {exc}", "latency_ms": None, "alerts": []}
        results = list(await asyncio.gather(*(one(s) for s in srcs)))
        alerts = [a for r in results for a in r.get("alerts", [])]
        alerts.sort(key=lambda a: (-SEVERITY_RANK.get(a["severity"], 0), a["state"] != "firing", a["name"]))
        data = {"configured": bool(srcs), "enabled": True, "sources": results, "alerts": alerts, "summary": summarise(results),
                "checked_at": time.strftime("%Y-%m-%d %H:%M:%S")}
        _cache.update(at=time.monotonic(), data=data)
        return data


def reset() -> None:
    _cache.update(at=0.0, data=None)
    _opened.clear()


# ── Truy vấn PromQL cho AI / người dùng (chỉ đọc) ────────────────────────────

class MonitorError(Exception):
    pass


async def query_promql(expr: str, source_id: Optional[str] = None, limit: int = 30) -> Dict[str, Any]:
    expr = str(expr or "").strip()
    if not expr:
        raise MonitorError("Thiếu biểu thức PromQL")
    if len(expr) > MAX_PROMQL or _FORBIDDEN_PROMQL.search(expr):
        raise MonitorError(f"PromQL không hợp lệ (tối đa {MAX_PROMQL} ký tự, không ký tự điều khiển)")
    proms = [s for s in monitor_sources() if s["monitor_type"] == "prometheus"]
    if source_id:
        proms = [s for s in proms if s["id"] == source_id]
    if not proms:
        raise MonitorError("Chưa có nguồn Prometheus nào được khai báo (Tích hợp → Thêm kết nối → Prometheus)")
    src = proms[0]
    payload, err, latency = await _raw(src, PROM_Q["query"], {"promql": expr})
    if err:
        raise MonitorError(f"Prometheus báo lỗi: {err}")
    data = (payload or {}).get("data") or {}
    rows = []
    for r in (data.get("result") or [])[: max(1, min(int(limit), 200))]:
        val = r.get("value") or r.get("values") or []
        rows.append({"metric": _trim(r.get("metric"), 10), "value": (val[1] if len(val) == 2 and not isinstance(val[0], list) else val)})
    return {"source": src["id"], "result_type": data.get("resultType"), "count": len(data.get("result") or []), "rows": rows,
            "latency_ms": round(latency, 1)}


# ── Sự cố từ cảnh báo ngoài ──────────────────────────────────────────────────

def _category(alert: Dict[str, Any]) -> str:
    return f"infra:{alert['source']}:{alert['name']}:{alert['instance'] or '-'}"[:120]


async def sync_incidents(snap: Dict[str, Any]) -> Dict[str, Any]:
    """Cảnh báo bắn mức >= `incident_min_severity` -> mở sự cố (một cho mỗi cảnh báo) + báo kênh; hết bắn -> đóng sự cố bằng bằng chứng.
    Chỉ đóng khi nguồn ĐANG liên lạc được (nguồn mất liên lạc ≠ cảnh báo đã hết)."""
    from mateai.application.operations import alert_dispatcher
    from mateai.application.tasks import ledger
    cfg = _cfg()
    need = SEVERITY_RANK.get(cfg.incident_min_severity, 2)
    reachable = {s["id"] for s in snap.get("sources", []) if s["reachable"]}
    firing = {_category(a): a for a in snap.get("alerts", []) if a["state"] == "firing" and SEVERITY_RANK.get(a["severity"], 0) >= need}
    opened, closed = [], []
    for cat, a in firing.items():
        if cat in _opened:
            continue
        if len(opened) >= cfg.max_incidents_per_cycle:       # bão cảnh báo: mỗi chu kỳ chỉ mở tối đa N, phần còn lại ở chu kỳ sau
            break
        title = f"[{a['source_type'].capitalize()}] {a['name']}" + (f" — {a['instance']}" if a["instance"] else "")
        message = a["summary"] or f"Cảnh báo {a['name']} đang bắn trên {a['source']}"
        tid = await asyncio.to_thread(ledger.open_incident, cat, title, message, "critical" if a["severity"] == "critical" else "warning")
        await alert_dispatcher.dispatch(title, message, severity="critical" if a["severity"] == "critical" else "warning",
                                        category=f"sentinel:{cat}", source=f"Giám sát {a['source_type']}")
        _opened[cat] = title
        opened.append({"category": cat, "task_id": tid})
    # đóng: sự cố do module này mở (kể cả trước khi khởi động lại) mà cảnh báo đã hết bắn
    open_tasks = await asyncio.to_thread(ledger.list_tasks, kind="incident", limit=200)
    for t in open_tasks:
        src = str(t.get("source") or "")
        if not src.startswith("sentinel:infra:") or t.get("status") in ("COMPLETED", "FAILED", "CANCELLED"):
            continue
        cat = src[len("sentinel:"):]
        source_id = cat.split(":")[1] if cat.count(":") >= 2 else ""
        if cat in firing or source_id not in reachable:
            continue
        evidence = f"{source_id} không còn báo cảnh báo này lúc {snap.get('checked_at')}"
        await asyncio.to_thread(ledger.resolve_incident_by_probe, cat, evidence)
        await alert_dispatcher.dispatch(f"Đã khôi phục: {t.get('title')}", evidence, category=f"sentinel:{cat}",
                                        source="Giám sát hạ tầng", resolved=True)
        _opened.pop(cat, None)
        closed.append(cat)
    return {"opened": opened, "closed": closed}


async def poll_once() -> Dict[str, Any]:
    snap = await snapshot(force=True)
    if not snap.get("configured"):
        return {"configured": False}
    return {"configured": True, **(await sync_incidents(snap)), "health": snap["summary"]["health"]}


def start_poller() -> None:
    global _poller
    if _poller and not _poller.done():
        return

    async def _loop() -> None:
        while True:
            try:
                if _cfg().enabled and monitor_sources(include_secrets=False):
                    await poll_once()
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001
                logger.exception("[Monitoring] vòng giám sát lỗi")
            await asyncio.sleep(_cfg().interval_s)
    _poller = asyncio.get_running_loop().create_task(_loop(), name="infra-monitor")


def stop() -> None:
    global _poller
    if _poller:
        _poller.cancel()
        _poller = None


class _Stopper:
    """Cho `lifecycle.run_shutdown` (gọi `.stop()` trên một đối tượng)."""
    stop = staticmethod(stop)


infra_monitor_stopper = _Stopper()
