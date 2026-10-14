# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 Dương Thanh Lịch — VN-MateAI. See LICENSE and NOTICE.
"""
mateai/interfaces/http/topology.py
==================================
Trạng thái THẬT của hệ thống cho trang `/admin/topology` (docs/realtime/topology-plan.md).

- `snapshot()`: các thành phần + cạnh nối, mỗi thành phần có trạng thái
  ok | degraded | down | off | unknown, số đo thật, lý do và "từ lúc nào".
  Không có số đo thì ghi rõ "chưa có số đo" — không điền số giả (trước đây:
  số máy trạm `max(thật, 17)`, "uptime 99.98%", "latency < 12ms", connector
  luôn "active").
- `topology_loop()`: khi có người xem, cứ 2 s đẩy snapshot qua /ws/topology và
  phát sự kiện khi một thành phần ĐỔI trạng thái.
- Sự kiện bước xử lý (application/operations/topology_events) được đẩy ngay.
"""
from __future__ import annotations

import asyncio
import json
import logging
import sys
import threading
import time
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

from mateai.application.operations import topology_events
from mateai.config.loader import settings

logger = logging.getLogger(__name__)

SNAPSHOT_INTERVAL_S = 2.0
#: Thành phần đổi trạng thái lúc nào (để hiện "từ lúc nào" và phát sự kiện).
_status_since: Dict[str, Tuple[str, float]] = {}


def _node(nid: str, kind: str, label: str, status: str, detail: str = "",
          metrics: Optional[Dict[str, Any]] = None, group: str = "core") -> Dict[str, Any]:
    prev = _status_since.get(nid)
    since = prev[1] if prev and prev[0] == status else time.time()
    return {"id": nid, "kind": kind, "label": label, "status": status, "detail": detail,
            "metrics": metrics or {}, "group": group, "since": since}


def _svc(services: Dict[str, Any], key: str) -> Dict[str, Any]:
    return services.get(key) or {}


def _health_nodes(nodes: List[Dict[str, Any]]) -> None:
    from mateai.application.operations.health_monitor import SYSTEM_HEALTH_CACHE as H
    hw = H.get("hardware") or {}
    services = H.get("services") or {}
    fresh = time.time() - float(H.get("last_updated") or 0) < 30

    cpu, ram, disk = hw.get("cpu_percent"), hw.get("ram_percent"), hw.get("disk_percent")
    if not fresh:
        status, detail = "unknown", "chưa có số đo phần cứng (health_monitor chưa chạy)"
    elif max(cpu or 0, ram or 0) >= 95 or (disk or 0) >= 95:
        status, detail = "down", "tài nguyên máy chủ cạn (≥ 95%)"
    elif max(cpu or 0, ram or 0) >= 85 or (disk or 0) >= 90:
        status, detail = "degraded", "tài nguyên máy chủ cao"
    else:
        status, detail = "ok", ""
    nodes.append(_node("core", "server", "Máy chủ VN-MateAI", status, detail, {
        "cpu_percent": cpu, "ram_percent": ram, "disk_percent": disk,
        "uptime": (H.get("nodes") or {}).get("uptime_human"),
    }))

    llm = _svc(services, "llm_9router")
    from mateai.infrastructure.llm.llm_provider import model_health
    cooling = model_health()
    if llm.get("status") == "OK":
        status = "degraded" if cooling else "ok"
        detail = (f"{len(cooling)} model đang tạm bỏ qua do lỗi" if cooling else "")
    elif llm.get("status") == "FAIL":
        status, detail = "down", f"9Router không phản hồi: {llm.get('detail', '')}"
    else:
        status, detail = "unknown", "chưa kiểm tra"
    nodes.append(_node("llm", "llm", "9Router LLM", status, detail, {
        "latency_ms": llm.get("latency_ms"), "model": llm.get("model"),
        "models_cooling": sorted(cooling)[:6],
    }))

    db = _svc(services, "database_sqlite")
    db_label = {"postgresql": "CSDL PostgreSQL", "sqlite": "CSDL SQLite"}.get(db.get("backend"), "CSDL")
    nodes.append(_node("db", "database", db_label,
                       {"OK": "ok", "FAIL": "down"}.get(db.get("status"), "unknown"),
                       db.get("detail") or db.get("message") or ""))

    tg = _svc(services, "telegram_gateway")
    tg_detail = tg.get("detail") or tg.get("message") or ""
    tg_status = "ok" if tg.get("status") == "OK" else (
        "off" if "Chưa cấu hình" in tg_detail else ("down" if tg.get("status") == "FAIL" else "unknown"))
    nodes.append(_node("telegram", "channel", "Telegram", tg_status, tg_detail, group="channel"))


def _voice_nodes(nodes: List[Dict[str, Any]]) -> None:
    from mateai.application.voice.voice_turn import recent_traces
    from mateai.config.loader import settings
    traces = recent_traces(30)
    last = traces[-1] if traces else None
    errors = sum(1 for t in traces[-10:] if t.get("outcome") == "error")
    if not traces:
        status, detail = "unknown", "chưa có lượt thoại nào từ lúc máy chủ chạy"
    elif last.get("outcome") == "error":
        status, detail = "down", "lượt thoại gần nhất lỗi"
    elif errors:
        status, detail = "degraded", f"{errors}/10 lượt gần nhất lỗi"
    else:
        status, detail = "ok", ""
    nodes.append(_node("voice", "pipeline", "Lõi hội thoại", status, detail, {
        "turns": len(traces),
        "last_outcome": last.get("outcome") if last else None,
        "last_ttfa_ms": last.get("ttfa_answer_ms") if last else None,
        "last_ttl_ms": last.get("ttl_ms") if last else None,
    }))

    stt_vals = [t["stt_ms"] for t in traces if isinstance(t.get("stt_ms"), (int, float))]
    nodes.append(_node("stt", "stt", "Nhận dạng giọng nói", "ok" if stt_vals else "unknown",
                       "" if stt_vals else "chưa có số đo", {
                           "backend": str(getattr(settings, "ASR_BACKEND", "") or ""),
                           "last_ms": stt_vals[-1] if stt_vals else None}))

    tts_vals = [t["tts_first_latency_ms"] for t in traces if isinstance(t.get("tts_first_latency_ms"), (int, float))]
    if not tts_vals:
        status, detail = "unknown", "chưa có số đo"
    elif tts_vals[-1] > 6000:
        status, detail = "degraded", "tổng hợp giọng chậm (> 6 s)"
    else:
        status, detail = "ok", ""
    nodes.append(_node("tts", "tts", "Tổng hợp giọng (TTS)", status, detail,
                       {"last_ms": tts_vals[-1] if tts_vals else None}))


def _channel_nodes(nodes: List[Dict[str, Any]], edges: List[Dict[str, Any]]) -> None:
    from mateai.interfaces.websocket.realtime_hub import active_hud_websockets, active_portal_websockets
    hud = len(active_hud_websockets)
    nodes.append(_node("hud", "channel", "HUD", "ok" if hud else "off",
                       "" if hud else "không có HUD nào đang mở", {"connections": hud}, group="channel"))
    portal = len(active_portal_websockets)
    try:
        from mateai.interfaces.websocket.realtime_voice_ws import voice_ws_registry
        voice_sessions = len(getattr(voice_ws_registry, "_sessions", {}))
    except Exception:  # noqa: BLE001
        voice_sessions = 0
    nodes.append(_node("portal", "channel", "Web Portal", "ok" if portal else "off",
                       "" if portal else "không có trang portal nào đang mở",
                       {"connections": portal, "voice_sessions": voice_sessions}, group="channel"))
    edges += [_edge("hud", "voice"), _edge("portal", "voice"), _edge("telegram", "voice")]

    from mateai.interfaces.websocket.xiaozhi_gateway import xiaozhi_gateway
    robots = xiaozhi_gateway.get_all_nodes()
    for rid, n in robots.items():
        nid = f"robot:{rid}"
        busy = n.active_task is not None and not n.active_task.done()
        nodes.append(_node(nid, "robot", f"Robot {rid}", "ok", "", {
            "ip": n.client_host, "state": n.state, "emotion": n.emotion,
            "busy": busy, "follow_up": getattr(n, "follow_up", 0),
            "firmware": n.firmware_version, "last_active": n.last_active,
        }, group="channel"))
        edges += [_edge(nid, "stt"), _edge("tts", nid)]
    if not robots:
        nodes.append(_node("robot:none", "robot", "Robot", "off", "không có robot nào kết nối", group="channel"))
    edges += [_edge("stt", "voice"), _edge("voice", "llm"), _edge("voice", "tts"), _edge("voice", "tools")]


def _tool_nodes(nodes: List[Dict[str, Any]], edges: List[Dict[str, Any]]) -> None:
    try:
        from core.plugin_manager import plugin_manager
        skills = plugin_manager.get_skill_count()
    except Exception:  # noqa: BLE001
        skills = None
    nodes.append(_node("tools", "tools", "Cổng tool / Kỹ năng", "ok" if skills else "unknown",
                       "" if skills else "chưa nạp được danh mục kỹ năng",
                       {"skills": skills}, group="tools"))

    from mateai.application.security.zero_trust import hitl_manager
    pending = hitl_manager.get_pending_list()
    nodes.append(_node("hitl", "approval", "Hàng đợi phê duyệt", "degraded" if pending else "ok",
                       f"{len(pending)} tác vụ chờ duyệt" if pending else "", {
                           "pending": len(pending),
                           "items": [{"tool": p.get("action_name"), "by": p.get("requested_by")}
                                     for p in pending[:5]],
                       }, group="tools"))
    edges += [_edge("tools", "hitl"), _edge("tools", "core"), _edge("core", "db"), _edge("voice", "db")]

    from mateai.interfaces.websocket.client_orchestrator import (
        bundled_agent_version, orchestrator, version_tuple)
    workers = orchestrator.get_connected_clients()
    latest = bundled_agent_version()
    for w in workers:
        nid = f"worker:{w.get('client_id')}"
        m = w.get("metrics") or {}
        age = w.get("heartbeat_age_s")
        ver = w.get("agent_version")
        if age is None:
            status, detail = "degraded", "Agent bản cũ — không gửi nhịp tim / số đo (tải lại Agent)"
        elif age > 90:
            status, detail = "down", f"mất nhịp tim {age} s — máy trạm treo hoặc mạng chập chờn"
        elif max(m.get("cpu_percent") or 0, m.get("ram_percent") or 0, m.get("disk_percent") or 0) >= 95:
            status, detail = "degraded", "tài nguyên máy trạm cạn (≥ 95%)"
        elif latest and ver and version_tuple(ver) < version_tuple(latest):
            status, detail = "degraded", f"Agent {ver} cũ hơn bản phát hành {latest} — tải lại Agent"
        else:
            status, detail = "ok", ""
        nodes.append(_node(nid, "worker", f"Máy trạm {w.get('hostname') or w.get('client_id')}", status, detail,
                           {"ip": w.get("ip"), "platform": w.get("platform"), "uptime": w.get("uptime"),
                            "cpu_percent": m.get("cpu_percent"), "ram_percent": m.get("ram_percent"),
                            "disk_percent": m.get("disk_percent"), "agent_version": ver,
                            "heartbeat_age_s": age},
                           group="tools"))
        edges.append(_edge("tools", nid))
    if not workers:
        nodes.append(_node("worker:none", "worker", "Máy trạm LAN", "off", "không có máy trạm nào kết nối",
                           group="tools"))

    from mateai.infrastructure.connectors import CONNECTOR_REGISTRY
    from mateai.infrastructure.connectors.base_connector import missing_required_fields
    for name in CONNECTOR_REGISTRY:
        try:
            missing = missing_required_fields(name)
        except Exception:  # noqa: BLE001
            missing = ["?"]
        nid = f"connector:{name}"
        nodes.append(_node(nid, "connector", f"Connector {name}", "off" if missing else "unknown",
                           f"chưa cấu hình ({', '.join(missing[:3])})" if missing else "đã cấu hình, chưa kiểm tra kết nối",
                           group="connector"))
        edges.append(_edge("tools", nid))


def _loaded(module: str, attr: str) -> Any:
    """Đối tượng của module ĐÃ được máy chủ nạp — không import mới (RAG / ChromaDB
    nạp nặng; sơ đồ chỉ đọc trạng thái, không được tự khởi động dịch vụ)."""
    mod = sys.modules.get(module)
    return getattr(mod, attr, None) if mod else None


def _ago(ts: Optional[float]) -> Optional[str]:
    if not ts:
        return None
    sec = max(0, int(time.time() - ts))
    return f"{sec}s trước" if sec < 60 else f"{sec // 60} phút trước" if sec < 3600 else f"{sec // 3600} giờ trước"


def _thread_alive(name: str) -> bool:
    return any(t.name == name and t.is_alive() for t in threading.enumerate())


def _automation_nodes(nodes: List[Dict[str, Any]], edges: List[Dict[str, Any]]) -> None:
    """Dịch vụ chạy nền: Sentinel, lịch đôn đốc, tác vụ nền, Email, Webhook, UDP beacon."""
    sentinel = _loaded("mateai.application.operations.autonomous_sentinel", "autonomous_sentinel")
    if sentinel is None:
        nodes.append(_node("sentinel", "sentinel", "Autonomous Sentinel", "unknown", "chưa nạp", group="automation"))
    else:
        task = getattr(sentinel, "_monitor_task", None)
        active = getattr(sentinel, "_active_incidents", {}) or {}
        last = max((getattr(sentinel, "_last_alert_times", {}) or {}).values(), default=None)
        if not getattr(sentinel, "_running", False):
            status, detail = "off", "không chạy"
        elif task is not None and task.done():
            status, detail = "down", "vòng giám sát đã dừng bất thường"
        elif active:
            status, detail = "degraded", "sự cố đang mở: " + ", ".join(
                str(i.get("title") or c) for c, i in list(active.items())[:3])
        else:
            status, detail = "ok", ""
        nodes.append(_node("sentinel", "sentinel", "Autonomous Sentinel", status, detail, {
            "interval_s": getattr(sentinel, "_check_interval", None),
            "active_incidents": len(active),
            "last_alert": _ago(last),
        }, group="automation"))
    edges += [_edge("sentinel", "core"), _edge("sentinel", "db"), _edge("sentinel", "ad"),
              _edge("sentinel", "alerts"), _edge("sentinel", "robot:*")]

    pm = _loaded("mateai.application.skills.builtin.proactive_manager", "proactive_manager")
    if pm is None:
        nodes.append(_node("scheduler", "scheduler", "Lịch đôn đốc tự động", "unknown", "chưa nạp", group="automation"))
    else:
        history = getattr(pm, "_audit_history", []) or []
        running = getattr(pm, "_running", False)
        alive = _thread_alive("proactive-manager-loop")
        status, detail = ("ok", "") if running and alive else (
            ("down", "luồng lịch đã dừng bất thường") if running else ("off", "không chạy"))
        last = history[-1] if history else None
        nodes.append(_node("scheduler", "scheduler", "Lịch đôn đốc tự động", status, detail, {
            "schedule": "08:00 · 16:00",
            "runs": len(history),
            "last_run": last.get("timestamp") if last else None,
            "last_result": (f"{last.get('total_overdue')} quá hạn · {last.get('total_upcoming')} sắp hạn"
                            if last else None),
        }, group="automation"))
    edges += [_edge("scheduler", "db"), _edge("scheduler", "telegram")]

    bw = _loaded("mateai.application.operations.background_workers", "background_worker_manager")
    if bw is None:
        nodes.append(_node("jobs", "jobs", "Tác vụ nền", "unknown", "chưa nạp", group="automation"))
    else:
        tasks = list((getattr(bw, "_tasks", {}) or {}).values())
        by = {}
        for t in tasks:
            key = getattr(getattr(t, "status", None), "value", "?")
            by[key] = by.get(key, 0) + 1
        recent_failed = [t for t in tasks[-10:] if getattr(getattr(t, "status", None), "value", "") == "failed"]
        if getattr(bw, "_semaphore", None) is None:
            status, detail = "off", "chưa khởi động"
        elif getattr(bw, "_shutdown", False):
            status, detail = "down", "đang tắt"
        elif recent_failed:
            status, detail = "degraded", f"{len(recent_failed)}/10 tác vụ gần nhất lỗi: {recent_failed[-1].name}"
        else:
            status, detail = "ok", ""
        nodes.append(_node("jobs", "jobs", "Tác vụ nền", status, detail, {
            "running": by.get("running", 0), "pending": by.get("pending", 0),
            "completed": by.get("completed", 0), "failed": by.get("failed", 0),
            "max_concurrent": getattr(bw, "max_concurrent", None),
        }, group="automation"))
    edges += [_edge("tools", "jobs")]

    eg = _loaded("mateai.interfaces.email.email_gateway", "email_gateway")
    if eg is None or not getattr(eg, "enabled", False):
        nodes.append(_node("email", "email", "Email Gateway", "off",
                           "chưa bật / chưa cấu hình (config: email_gateway)", group="channel"))
    else:
        alive = _thread_alive("email-gateway-loop")
        hist = getattr(eg, "_history", []) or []
        nodes.append(_node("email", "email", "Email Gateway", "ok" if alive else "down",
                           "" if alive else "luồng đọc hộp thư đã dừng", {
                               "inbox": getattr(eg, "username", ""),
                               "tickets": len(hist),
                               "last_ticket": hist[0].get("received_at") if hist else None,
                           }, group="channel"))
    edges += [_edge("email", "db"), _edge("email", "alerts")]

    stats = _loaded("mateai.interfaces.http.webhook_gateway", "WEBHOOK_STATS")
    if stats is None:
        nodes.append(_node("webhook", "webhook", "Webhook Gateway", "off", "chưa đăng ký route", group="channel"))
    else:
        nodes.append(_node("webhook", "webhook", "Webhook Gateway", "ok", "", {
            "received": stats.get("received"), "duplicates": stats.get("duplicates"),
            "last": _ago(stats.get("last_at")), "last_source": stats.get("last_source"),
        }, group="channel"))
    edges += [_edge("webhook", "alerts"), _edge("webhook", "robot:*")]

    alive = _thread_alive("vnmate-udp-beacon")
    nodes.append(_node("beacon", "beacon", "UDP Beacon (tìm máy chủ)", "ok" if alive else "down",
                       "" if alive else "luồng beacon không chạy — robot mới không tự tìm được máy chủ",
                       {"port": settings.DISCOVERY_PORT}, group="automation"))
    edges += [_edge("beacon", "robot:*")]


def _knowledge_nodes(nodes: List[Dict[str, Any]], edges: List[Dict[str, Any]]) -> None:
    """Dữ liệu & tri thức: Active Directory, RAG, bộ nhớ sự cố, đa tác tử, bộ đệm phiên."""
    from mateai.application.operations.health_monitor import SYSTEM_HEALTH_CACHE as H
    ad = (H.get("services") or {}).get("active_directory") or {}
    ad_status = {"OK": "ok", "FAIL": "down"}.get(ad.get("status"), "unknown")
    ad_detail = ad.get("detail") or "chưa có số đo"
    if ad_status == "ok" and not ad.get("employees_count") and "Chưa" in str(ad.get("last_sync")):
        # CSDL nhân sự đọc được nhưng chưa từng đồng bộ: không phải "đang hoạt động".
        ad_status, ad_detail = "off", "chưa đồng bộ lần nào (0 nhân viên)"
    nodes.append(_node("ad", "directory", "Active Directory / HR", ad_status, ad_detail, {
                           "employees": ad.get("employees_count"), "computers": ad.get("computers_count"),
                           "last_sync": ad.get("last_sync"),
                       }, group="data"))
    edges += [_edge("tools", "ad")]

    rag = _loaded("mateai.application.knowledge.rag_engine", "rag_engine")
    graph = _loaded("mateai.application.knowledge.graph_rag", "graph_rag")
    if rag is None:
        nodes.append(_node("rag", "knowledge", "Tri thức RAG (ChromaDB)", "unknown", "chưa nạp", group="data"))
    else:
        try:
            chunks = rag.collection.count()
            status, detail = ("ok", "") if chunks else ("degraded", "chưa có tài liệu nào được nạp")
        except Exception as exc:  # noqa: BLE001
            chunks, status, detail = None, "down", f"ChromaDB lỗi: {type(exc).__name__}"
        nodes.append(_node("rag", "knowledge", "Tri thức RAG (ChromaDB)", status, detail, {
            "chunks": chunks,
            "graph_entities": len(getattr(graph, "nodes", []) or []) if graph else None,
            "graph_relations": len(getattr(graph, "edges", []) or []) if graph else None,
        }, group="data"))
    edges += [_edge("tools", "rag")]

    mem_mod = sys.modules.get("mateai.infrastructure.memory.cognitive_memory")
    col = getattr(mem_mod, "_incident_collection", None) if mem_mod else None
    if col is None:
        nodes.append(_node("memory", "memory", "Bộ nhớ sự cố", "unknown",
                           "chưa mở (mở khi có tra cứu / ghi sự cố đầu tiên)", group="data"))
    else:
        try:
            nodes.append(_node("memory", "memory", "Bộ nhớ sự cố", "ok", "", {"records": col.count()}, group="data"))
        except Exception as exc:  # noqa: BLE001
            nodes.append(_node("memory", "memory", "Bộ nhớ sự cố", "down", f"lỗi: {type(exc).__name__}", group="data"))
    edges += [_edge("tools", "memory"), _edge("sentinel", "memory")]

    mas = _loaded("mateai.application.agent.agent_orchestrator", "multi_agent_system")
    if mas is None:
        nodes.append(_node("agents", "agents", "Đa tác tử (CEO/CFO/HR/CTO)", "unknown", "chưa nạp", group="tools"))
    else:
        bus = getattr(mas, "message_bus", None)
        agents = sorted((getattr(bus, "agents", {}) or {}).keys())
        log = getattr(bus, "interaction_log", []) or []
        nodes.append(_node("agents", "agents", "Đa tác tử (CEO/CFO/HR/CTO)", "ok" if agents else "degraded",
                           "" if agents else "chưa có tác tử nào đăng ký", {
                               "agents": ", ".join(a.upper() for a in agents), "interactions": len(log),
                           }, group="tools"))
    edges += [_edge("tools", "agents"), _edge("agents", "llm"), _edge("agents", "rag")]

    cache = _loaded("mateai.infrastructure.cache.ephemeral_cache", "ephemeral_cache")
    if cache is None:
        nodes.append(_node("cache", "cache", "Bộ đệm phiên (RAM)", "unknown", "chưa nạp", group="core"))
    else:
        st = cache.get_stats()
        nodes.append(_node("cache", "cache", "Bộ đệm phiên (RAM)", "ok", "", {
            "active_items": st.get("active_items"), "sessions": st.get("active_sessions"),
        }, group="core"))
    edges += [_edge("voice", "cache")]


def _alert_nodes(nodes: List[Dict[str, Any]], edges: List[Dict[str, Any]]) -> None:
    """Khâu cảnh báo chung + từng kênh gửi ra (Teams, Email, Outlook, Slack, Webhook)."""
    from mateai.application.operations import alert_dispatcher
    from mateai.infrastructure.notifications import load_rules
    chans = alert_dispatcher.channel_status()
    ready = [c for c in chans if c["configured"] and c["enabled"]]
    rules = load_rules()
    last = alert_dispatcher.HISTORY[0] if alert_dispatcher.HISTORY else None
    if not ready:
        status, detail = "degraded", "chưa kênh nào kết nối — cảnh báo không tới được ai"
    elif last and not last["resolved"] and last["delivered"] == 0 and last["results"]:
        status, detail = "down", "lần gửi gần nhất không tới được kênh nào"
    else:
        status, detail = "ok", ""
    nodes.append(_node("alerts", "alerts", "Khâu cảnh báo", status, detail, {
        "channels_ready": f"{len(ready)}/{len(chans)}",
        "min_severity": rules["min_severity"],
        "watch_topology": rules["watch_topology"],
        "last_alert": (f"{last['title']} ({last['delivered']}/{len(last['results'])} kênh)" if last else None),
    }, group="alerts"))
    edges.append(_edge("alerts", "telegram"))
    for c in chans:
        if c["id"] == alert_dispatcher.TELEGRAM:
            continue
        if not c["configured"]:
            st, why = "off", "chờ kết nối — thiếu: " + ", ".join(c["missing_fields"])
        elif not c["enabled"]:
            st, why = "off", "đã tắt trong cấu hình"
        elif c.get("last_status") == "error":
            st, why = "down", f"lần gửi gần nhất lỗi: {c.get('last_detail', '')}"
        elif c.get("last_status") == "ok":
            st, why = "ok", ""
        else:
            st, why = "unknown", "đã kết nối, chưa gửi lần nào (bấm Gửi thử)"
        nodes.append(_node(c["node"], "notify", c["display_name"], st, why, {
            "sent": c.get("sent", 0), "failed": c.get("failed", 0),
            "last": _ago(c.get("last_at")),
        }, group="alerts"))
        edges.append(_edge("alerts", c["node"]))


def _expand_wildcards(nodes: List[Dict[str, Any]], edges: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """`robot:*` -> mọi ô robot hiện có (kể cả ô "robot:none" khi chưa có robot)."""
    robots = [n["id"] for n in nodes if n["kind"] == "robot"]
    out = []
    for e in edges:
        if e["target"] == "robot:*":
            out += [_edge(e["source"], r) for r in robots]
        else:
            out.append(e)
    return out


def _edge(source: str, target: str) -> Dict[str, Any]:
    return {"id": f"{source}->{target}", "source": source, "target": target}


def snapshot() -> Dict[str, Any]:
    nodes: List[Dict[str, Any]] = []
    edges: List[Dict[str, Any]] = []
    for build in (_health_nodes, _voice_nodes):
        try:
            build(nodes)
        except Exception as exc:  # noqa: BLE001 — một nguồn hỏng không làm mất cả sơ đồ
            logger.warning("[Topology] %s lỗi: %s", build.__name__, exc)
    for build in (_channel_nodes, _tool_nodes, _automation_nodes, _knowledge_nodes, _alert_nodes):
        try:
            build(nodes, edges)
        except Exception as exc:  # noqa: BLE001
            logger.warning("[Topology] %s lỗi: %s", build.__name__, exc)
    ids = {n["id"] for n in nodes}
    seen = set()
    edges = [e for e in _expand_wildcards(nodes, edges)
             if e["source"] in ids and e["target"] in ids and not (e["id"] in seen or seen.add(e["id"]))]
    counts: Dict[str, int] = {}
    for n in nodes:
        counts[n["status"]] = counts.get(n["status"], 0) + 1
    return {"status": "success", "nodes": nodes, "edges": edges, "counts": counts,
            "timestamp": datetime.now().isoformat(timespec="seconds")}


def track_status_changes(snap: Dict[str, Any]) -> List[Dict[str, Any]]:
    """So với lần trước: thành phần nào đổi trạng thái -> phát sự kiện "status"."""
    events = []
    now = time.time()
    for n in snap["nodes"]:
        prev = _status_since.get(n["id"])
        if prev is None:
            _status_since[n["id"]] = (n["status"], n["since"])
            continue
        if prev[0] != n["status"]:
            _status_since[n["id"]] = (n["status"], now)
            events.append(topology_events.publish(
                "status", node=n["id"], status=n["status"],
                detail=f"{n['label']}: {prev[0]} → {n['status']}" + (f" — {n['detail']}" if n["detail"] else "")))
    return events


# ── Đẩy qua WebSocket ────────────────────────────────────────────────────────

async def _broadcast(payload: Dict[str, Any]) -> None:
    from mateai.interfaces.websocket.realtime_hub import active_topology_websockets
    if not active_topology_websockets:
        return
    msg = json.dumps(payload, ensure_ascii=False, default=str)
    for ws in list(active_topology_websockets):
        try:
            await ws.send_text(msg)
        except Exception:  # noqa: BLE001
            active_topology_websockets.discard(ws)


def _on_event(ev: Dict[str, Any]):
    return _broadcast({"event": "step", **ev})


#: Không ai mở trang vẫn kiểm tra (để tự gửi cảnh báo), nhưng thưa hơn.
IDLE_INTERVAL_S = 10.0


async def topology_loop() -> None:
    """Chạy suốt đời máy chủ: snapshot 2 s/lần khi có người xem (10 s khi không ai
    xem — vẫn theo dõi để tự gửi cảnh báo); sự kiện bước xử lý đẩy ngay."""
    from mateai.interfaces.websocket.realtime_hub import active_topology_websockets
    from mateai.application.operations import alert_dispatcher
    topology_events.subscribe(_on_event)
    while True:
        try:
            snap = await asyncio.to_thread(snapshot)
            track_status_changes(snap)
            if active_topology_websockets:
                await _broadcast({"event": "snapshot", **snap})
            asyncio.create_task(alert_dispatcher.evaluate_topology(snap))
        except Exception as exc:  # noqa: BLE001
            logger.warning("[Topology] vòng cập nhật lỗi: %s", exc)
        await asyncio.sleep(SNAPSHOT_INTERVAL_S if active_topology_websockets else IDLE_INTERVAL_S)
