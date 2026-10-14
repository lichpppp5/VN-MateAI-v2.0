# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 Dương Thanh Lịch — VN-MateAI. See LICENSE and NOTICE.
"""
tests/test_phase88_workflow_topology.py
=======================================
Trang giám sát `/admin/topology` — trạng thái THẬT, sự kiện bước xử lý thời gian
thực (docs/realtime/topology-plan.md, 2026-10-04).

Bản cũ của file này khoá đúng dữ liệu viết cứng: nút "agent_ceo", "plugin_m365"…
luôn "online", 17 máy trạm (`max(thật, 17)`), "uptime 99.98%". Nay kiểm:
  - trạng thái lấy từ số đo thật; thiếu số đo thì "unknown", không điền số giả;
  - mỗi lượt thoại / tool / phê duyệt phát sự kiện từng bước;
  - đổi trạng thái thành phần -> sự kiện;
  - API theo vai trò; bố cục chỉ lưu vị trí; mô phỏng gắn nhãn, chỉ admin;
  - WebSocket gửi trạng thái thật ngay khi mở.
Không gọi mạng.
"""
from __future__ import annotations

import json
import time
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from mateai.application.operations import topology_events
from mateai.interfaces.http import topology

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(autouse=True)
def fresh_events():
    topology_events.clear()
    topology._status_since.clear()
    yield
    topology_events.clear()


@pytest.fixture
def health(monkeypatch):
    import mateai.application.operations.health_monitor as hm
    cache = {
        "last_updated": time.time(),
        "hardware": {"cpu_percent": 12.0, "ram_percent": 40.0, "disk_percent": 50.0},
        "nodes": {"uptime_human": "2h 3m"},
        "services": {
            "llm_9router": {"status": "OK", "latency_ms": 180.0, "model": "m1"},
            "database_sqlite": {"status": "OK", "detail": "ok"},
            "telegram_gateway": {"status": "FAIL", "detail": "Chưa cấu hình Bot Token"},
        },
    }
    monkeypatch.setattr(hm, "SYSTEM_HEALTH_CACHE", cache)
    return cache


def _by_id(snap):
    return {n["id"]: n for n in snap["nodes"]}


def test_statuses_come_from_real_measurements(health):
    nodes = _by_id(topology.snapshot())
    assert nodes["core"]["status"] == "ok" and nodes["core"]["metrics"]["cpu_percent"] == 12.0
    assert nodes["llm"]["status"] == "ok" and nodes["llm"]["metrics"]["latency_ms"] == 180.0
    assert nodes["db"]["status"] == "ok"
    assert nodes["telegram"]["status"] == "off"                       # chưa cấu hình ≠ hỏng
    health["services"]["llm_9router"] = {"status": "FAIL", "detail": "ConnectError"}
    health["hardware"]["ram_percent"] = 97.0
    nodes = _by_id(topology.snapshot())
    assert nodes["llm"]["status"] == "down" and "ConnectError" in nodes["llm"]["detail"]
    assert nodes["core"]["status"] == "down"


def test_no_fake_numbers_when_nothing_measured(health):
    health["last_updated"] = 0                                        # health_monitor chưa chạy
    nodes = _by_id(topology.snapshot())
    assert nodes["core"]["status"] == "unknown"
    # Không có máy trạm thật -> một ô "off", KHÔNG phải 17 máy "online".
    workers = [n for n in nodes.values() if n["kind"] == "worker"]
    assert [w["status"] for w in workers] == ["off"]
    # Connector chưa cấu hình hiện đúng là chưa cấu hình (trước đây luôn "active").
    connectors = [n for n in nodes.values() if n["kind"] == "connector"]
    assert connectors and all(c["status"] in ("off", "unknown") for c in connectors)


def test_status_change_emits_event(health):
    topology.track_status_changes(topology.snapshot())                # mốc ban đầu
    health["services"]["llm_9router"] = {"status": "FAIL", "detail": "timeout"}
    events = topology.track_status_changes(topology.snapshot())
    assert any(e["node"] == "llm" and e["status"] == "down" for e in events)


async def test_voice_turn_publishes_each_step():
    import mateai.application.voice.voice_turn as vt
    from mateai.infrastructure.tts.tts_stream_engine import TTSStreamEngine
    import mateai.infrastructure.tts.audio_cache as audio_cache

    async def synth(self, text, *a, **k):
        return b"A" * 400

    async def stream(self, text, *a, **k):
        yield b"A" * 400

    mp = pytest.MonkeyPatch()
    mp.setattr(TTSStreamEngine, "synthesise", synth)
    mp.setattr(TTSStreamEngine, "stream", stream)
    mp.setattr(audio_cache, "get_cached_audio_bytes", lambda text: None)
    try:
        res = await vt.process_voice_turn("mấy giờ rồi", sink=vt.VoiceSink(), session_id="t-topo", source_device="hud")
    finally:
        mp.undo()
    evs = [e for e in topology_events.recent() if e.get("trace_id") == res.trace["trace_id"]]
    stages = [e["stage"] for e in evs]
    assert stages[0] == "start" and stages[-1] == "end" and "router" in stages
    assert evs[0]["source"] == "hud" and evs[0]["target"] == "voice"
    assert evs[-1]["status"] == "ok" and evs[-1]["ms"] >= 0
    assert (evs[-1]["source"], evs[-1]["target"]) == ("voice", "hud")     # cạnh trả lời sáng trên sơ đồ


async def test_tool_steps_and_approval_waiting(monkeypatch):
    import mateai.application.agent.tool_gate as tg

    async def inner(fn_name, fn_args, **kw):
        return {"result": {"status": "need_confirm" if fn_name == "kill_process" else "success"}}

    monkeypatch.setattr(tg, "_run_tool_with_policy", inner)
    await tg.run_tool_with_policy("get_system_info", {})
    await tg.run_tool_with_policy("kill_process", {"pid": 1}, caller="bob")
    evs = topology_events.recent()
    assert [(e["kind"], e["status"]) for e in evs] == [
        ("tool", "running"), ("tool", "ok"), ("tool", "running"), ("approval", "waiting")]
    assert evs[-1]["target"] == "hitl" and "bob" in evs[-1]["detail"]
    assert evs[0]["target"] == "core"                                  # tool chạy trên máy chủ


def _client(role):
    import mateai.interfaces.http.routers.system as system
    from mateai.interfaces.http.auth_dependencies import get_current_user
    app = FastAPI()
    app.include_router(system.router)
    app.dependency_overrides[get_current_user] = lambda: {"username": f"{role}_u", "role": role}
    return TestClient(app)


def test_api_roles_layout_and_simulation(health, tmp_path, monkeypatch):
    import mateai.interfaces.http.routers.system as system
    from mateai.application.operations import topology_layout
    monkeypatch.setattr(topology_layout, "LAYOUT_PATH", tmp_path / "custom_topology.json")
    assert _client("viewer").get("/api/v1/system/topology").status_code == 403
    for path, body in (("/api/v1/system/topology/save", {"nodes": []}),
                       ("/api/v1/system/topology/trigger", {}),
                       ("/api/v1/system/topology/reset", None)):
        assert _client("manager").post(path, json=body).status_code == 403

    admin = _client("admin")
    saved = admin.post("/api/v1/system/topology/save", json={
        "nodes": [{"id": "core", "position": {"x": 10, "y": 20}, "data": {"status": "online"}}], "edges": []})
    assert saved.status_code == 200
    data = admin.get("/api/v1/system/topology").json()
    assert data["layout"] == {"core": {"x": 10, "y": 20}}             # chỉ lưu vị trí
    assert _by_id(data)["core"]["status"] == "ok"                      # trạng thái vẫn thật

    ev = admin.post("/api/v1/system/topology/trigger", json={"source": "core", "target": "llm", "action": "thử"}).json()["event"]
    assert ev["simulated"] is True and "MÔ PHỎNG" in ev["detail"]
    events = _client("manager").get("/api/v1/system/topology/events").json()["events"]
    assert events[-1]["seq"] == ev["seq"]


def test_ws_sends_real_snapshot_first(monkeypatch):
    import mateai.interfaces.http.server as server
    from mateai.interfaces.http import ws_auth
    monkeypatch.setattr(ws_auth, "authenticate_websocket", lambda _ws: {"username": "dan", "role": "admin"})
    topology_events.publish("tool", stage="start", detail="trước khi mở trang")
    with TestClient(server.app).websocket_connect("/ws/topology") as ws:
        first = json.loads(ws.receive_text())
        second = json.loads(ws.receive_text())
    assert first["event"] == "snapshot" and any(n["id"] == "core" for n in first["nodes"])
    assert second["event"] == "history" and second["events"][-1]["detail"] == "trước khi mở trang"


def test_page_is_served():
    assert (ROOT / "admin" / "out" / "topology.html").exists() or (ROOT / "admin" / "out" / "admin" / "topology.html").exists()


# ── Module bổ sung (2026-10-05): dịch vụ nền, dữ liệu & tri thức ────────────

def test_all_modules_are_on_the_map(health):
    nodes = _by_id(topology.snapshot())
    for nid in ("sentinel", "scheduler", "jobs", "email", "webhook", "beacon",
                "ad", "rag", "memory", "agents", "cache"):
        assert nid in nodes, nid
        assert nodes[nid]["status"] in ("ok", "degraded", "down", "off", "unknown")


def test_unloaded_services_are_not_started_by_the_map(health, monkeypatch):
    """Sơ đồ chỉ đọc: không được import RAG (nạp ChromaDB) chỉ để vẽ một ô."""
    import sys
    monkeypatch.delitem(sys.modules, "mateai.application.knowledge.rag_engine", raising=False)
    nodes = _by_id(topology.snapshot())
    assert "mateai.application.knowledge.rag_engine" not in sys.modules
    assert nodes["rag"]["status"] == "unknown" and "chưa nạp" in nodes["rag"]["detail"]


def test_missing_beacon_thread_is_reported(health):
    # Trong test không có luồng beacon -> phải báo lỗi kèm hậu quả, không "ok".
    beacon = _by_id(topology.snapshot())["beacon"]
    assert beacon["status"] == "down" and "robot" in beacon["detail"]


def test_wildcard_edges_reach_the_robot_tile(health):
    edges = {(e["source"], e["target"]) for e in topology.snapshot()["edges"]}
    assert ("beacon", "robot:none") in edges and ("sentinel", "robot:none") in edges
    assert not any(t == "robot:*" for _, t in edges)


async def test_background_job_publishes_start_and_end():
    from mateai.application.operations.background_workers import BackgroundWorkerManager
    bw = BackgroundWorkerManager()
    await bw.start()

    async def work():
        return 1

    await bw.submit("bao_cao_thang", work, notify_on_complete=False)
    import asyncio
    for _ in range(50):
        if any(e["stage"] == "end" for e in topology_events.recent()):
            break
        await asyncio.sleep(0.01)
    evs = [e for e in topology_events.recent() if e["kind"] == "job"]
    assert [(e["stage"], e["status"]) for e in evs] == [("start", "running"), ("end", "ok")]
    assert evs[0]["trace_id"] == evs[1]["trace_id"] and evs[1]["detail"] == "bao_cao_thang"
    await bw.stop()


def test_emit_never_raises(monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("x")
    monkeypatch.setattr(topology_events, "publish", boom)
    assert topology_events.emit("job", node="jobs") is None


def test_telegram_alert_event_tells_why_it_was_not_sent(monkeypatch):
    from mateai.interfaces.telegram.telegram_gateway import telegram_gateway
    monkeypatch.setattr(telegram_gateway, "_outbound_config", lambda: None)
    assert telegram_gateway.send_incident_alert("🚨 CPU cao\nchi tiết") is False
    ev = [e for e in topology_events.recent() if e["kind"] == "alert"][-1]
    assert ev["status"] == "cancelled" and "tắt" in ev["detail"] and ev["detail"].endswith("🚨 CPU cao")


def test_never_synced_directory_is_not_shown_as_working(health):
    health["services"]["active_directory"] = {"status": "OK", "last_sync": "Chưa đồng bộ",
                                              "employees_count": 0, "detail": "Đồng bộ: Chưa đồng bộ · 0 NV"}
    assert _by_id(topology.snapshot())["ad"]["status"] == "off"
    health["services"]["active_directory"] = {"status": "OK", "last_sync": "5 phút trước",
                                              "employees_count": 42, "detail": "Đồng bộ: 5 phút trước · 42 NV"}
    assert _by_id(topology.snapshot())["ad"]["status"] == "ok"
