# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 Dương Thanh Lịch — VN-MateAI. See LICENSE and NOTICE.
"""
tests/test_client_agent_hardening.py
====================================
Agent máy trạm (client_agent/) — rà soát 2026-10-05:

  - cài skill từ xa: tên file kiểu `../agent.py` từng ghi đè được file NGOÀI skills/;
  - nhịp tim + phiên bản: máy chủ thấy số đo thật, biết máy trạm nào treo / Agent cũ;
  - một Agent mỗi máy (hai bản cùng client_id giành kết nối của nhau);
  - skill trả số GIẢ (`test_ping_host` luôn "15ms", `worker_health_check` luôn "khỏe");
  - máy chủ: máy trạm ngắt kết nối từng huỷ task của MỌI máy; máy trạm trả kết quả
    thay máy khác được; `/kill-process` đi vòng cổng phê duyệt;
  - gói tải về là file zip có bộ cài, không kèm log / venv của máy chủ.
Không gọi mạng.
"""
from __future__ import annotations

import asyncio
import io
import json
import socket
import sys
import time
import zipfile
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def agent_mod():
    """agent.py đặt bí danh `core` / `client_agent` trong sys.modules — hoàn nguyên sau test."""
    saved_modules, saved_path = dict(sys.modules), list(sys.path)
    sys.path.insert(0, str(ROOT / "client_agent"))
    try:
        import agent
        yield agent
    finally:
        sys.modules.clear()
        sys.modules.update(saved_modules)
        sys.path[:] = saved_path


def _skill_module(name):
    """Nạp skill của Agent theo ĐƯỜNG DẪN file — `skills.*` trùng tên với skills/ của máy chủ."""
    import importlib.util
    spec = importlib.util.spec_from_file_location(f"_agent_skill_{name}", ROOT / "client_agent" / "skills" / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class _WS:
    def __init__(self):
        self.sent = []

    async def send(self, text):
        self.sent.append(json.loads(text))


@pytest.mark.parametrize("name,expect", [
    ("new_skill", "new_skill.py"), ("disk_report.py", "disk_report.py"),
    ("../agent.py", None), ("..\\core\\plugin_manager.py", None), ("C:/Windows/x.py", None),
    ("a b.py", None), ("", None),
])
def test_skill_filename_is_confined_to_skills_dir(agent_mod, name, expect):
    assert agent_mod.safe_skill_filename(name) == expect


def test_install_skill_rejects_path_traversal(agent_mod, tmp_path, monkeypatch):
    class PM:
        loads = 0
        def load_plugins(self):
            PM.loads += 1
            return 0
        def get_skill_names(self):
            return []

    monkeypatch.setattr(agent_mod, "client_plugin_manager", PM())
    a = agent_mod.ClientAgent.__new__(agent_mod.ClientAgent)
    a.client_id, a._skills_dir = "T1", tmp_path / "skills"
    a._skills_dir.mkdir()
    ws = _WS()
    asyncio.run(a._dispatch_message(ws, {"action": "install_skill", "task_id": "t",
                                         "filename": "../evil.py", "code": "print(1)"}))
    assert ws.sent[0]["success"] is False and "không hợp lệ" in ws.sent[0]["error"]
    assert not (tmp_path / "evil.py").exists() and PM.loads == 0
    asyncio.run(a._dispatch_message(ws, {"action": "install_skill", "task_id": "t2",
                                         "filename": "ok_skill", "code": "x = 1"}))
    assert ws.sent[1]["success"] is True and (a._skills_dir / "ok_skill.py").read_text() == "x = 1"


def test_heartbeat_carries_real_measurements_and_version(agent_mod):
    hb = agent_mod.build_heartbeat("pc-01", 24)
    assert hb["action"] == "heartbeat" and hb["agent_version"] == agent_mod.AGENT_VERSION
    for k in ("cpu_percent", "ram_percent", "disk_percent"):
        assert hb[k] is None or 0 <= hb[k] <= 100
    assert hb["uptime_s"] > 0 and hb["skills_count"] == 24


def test_only_one_agent_per_machine(agent_mod):
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    first = agent_mod._acquire_single_instance(port)
    try:
        assert first is not None and agent_mod._acquire_single_instance(port) is None
    finally:
        first.close()


@pytest.mark.parametrize("out,ms", [
    ("Reply from 192.168.1.1: bytes=32 time=12ms TTL=64", 12.0),
    ("Reply from 10.0.0.1: bytes=32 time<1ms TTL=128", 1.0),
    ("64 bytes from 8.8.8.8: icmp_seq=1 ttl=117 time=23.4 ms", 23.4),
    ("Request timed out.", None),
])
def test_ping_parses_real_output(agent_mod, out, ms):
    assert _skill_module("custom_skills").parse_ping_ms(out) == ms


def test_health_check_is_measured_not_hardcoded(agent_mod):
    r = _skill_module("worker_health_check").worker_health_check()
    assert set(r) >= {"cpu_percent", "ram_percent", "disk_percent", "healthy"}
    assert r["message"] != "Máy trạm hoàn toàn khỏe mạnh và sẵn sàng!"


def test_single_kill_process_skill(agent_mod):
    names = []
    for f in (ROOT / "client_agent" / "skills").glob("*.py"):
        names += [line for line in f.read_text(encoding="utf-8").splitlines() if 'name="kill_process"' in line]
    assert len(names) == 1


# ── Máy chủ ──────────────────────────────────────────────────────────────────

class _ServerWS:
    def __init__(self):
        self.sent = []

    async def send_text(self, text):
        self.sent.append(json.loads(text))


async def test_disconnect_cancels_only_that_workers_tasks():
    from mateai.interfaces.websocket.client_orchestrator import Orchestrator
    o = Orchestrator()
    wa, wb = _ServerWS(), _ServerWS()
    await o.register_client("A", wa, {"agent_version": "2.1.0"})
    await o.register_client("B", wb, {})
    ta = asyncio.create_task(o.execute_on_client("A", "get_system_info", timeout=5))
    tb = asyncio.create_task(o.execute_on_client("B", "get_system_info", timeout=5))
    await asyncio.sleep(0.05)
    task_b = wb.sent[0]["task_id"]
    o.handle_incoming_message("A", {"action": "result", "task_id": task_b, "success": True, "result": {"fake": 1}})
    await o.unregister_client("A")
    o.handle_incoming_message("B", {"action": "result", "task_id": task_b, "success": True, "result": {"real": 1}})
    ra, rb = await ta, await tb
    assert ra["status"] == "error"                         # A ngắt -> task của A huỷ
    assert rb["status"] == "success" and rb["result"] == {"real": 1}   # B vẫn chạy, A không trả thay được


async def test_heartbeat_is_stored_and_exposed():
    from mateai.interfaces.websocket.client_orchestrator import Orchestrator
    o = Orchestrator()
    await o.register_client("A", _ServerWS(), {"agent_version": "2.0.0"})
    assert o.get_connected_clients()[0]["heartbeat_age_s"] is None
    o.handle_incoming_message("A", {"action": "heartbeat", "agent_version": "2.1.0", "cpu_percent": 12.5,
                                    "ram_percent": 40.0, "disk_percent": 70.0, "uptime_s": 99, "skills_count": 3})
    w = o.get_connected_clients()[0]
    assert w["agent_version"] == "2.1.0" and w["metrics"]["cpu_percent"] == 12.5 and w["heartbeat_age_s"] == 0


@pytest.mark.parametrize("worker,status", [
    ({"agent_version": "2.1.0", "metrics": {"cpu_percent": 5}, "heartbeat_age_s": 3}, "ok"),
    ({"agent_version": "2.1.0", "metrics": {}, "heartbeat_age_s": 200}, "down"),
    ({"agent_version": None, "metrics": {}, "heartbeat_age_s": None}, "degraded"),
    ({"agent_version": "1.0.0", "metrics": {"cpu_percent": 5}, "heartbeat_age_s": 3}, "degraded"),
])
def test_topology_worker_status_from_heartbeat(monkeypatch, worker, status):
    from mateai.interfaces.http import topology
    from mateai.interfaces.websocket import client_orchestrator as co
    monkeypatch.setattr(co, "bundled_agent_version", lambda: "2.1.0")
    monkeypatch.setattr(co.orchestrator, "get_connected_clients",
                        lambda: [{"client_id": "pc1", "hostname": "PC1", "ip": "10.0.0.5", **worker}])
    node = {n["id"]: n for n in topology.snapshot()["nodes"]}["worker:pc1"]
    assert node["status"] == status


def test_kill_process_endpoint_goes_through_approval_gate(monkeypatch):
    import mateai.interfaces.http.routers.clients as clients
    from mateai.interfaces.http.auth_dependencies import get_current_user
    from mateai.interfaces.websocket.client_orchestrator import orchestrator
    calls = []

    async def gate(name, args, **kw):
        calls.append((name, args))
        return {"result": {"status": "need_confirm"}}

    async def direct(**kw):
        raise AssertionError("không được gửi thẳng xuống máy trạm")

    monkeypatch.setattr(clients, "run_tool_with_policy", gate)
    monkeypatch.setattr(orchestrator, "is_client_online", lambda cid: True)
    monkeypatch.setattr(orchestrator, "kill_client_process", direct)
    app = FastAPI()
    app.include_router(clients.router)
    app.dependency_overrides[get_current_user] = lambda: {"username": "boss", "role": "admin"}
    res = TestClient(app).post("/api/v1/clients/pc-01/kill-process", json={"pid": 4242}).json()
    assert res == {"status": "need_confirm"} and calls == [("kill_process", {"pid": 4242, "target_client": "pc-01"})]


def test_download_is_a_zip_with_installer_and_no_server_logs(monkeypatch, tmp_path):
    import mateai.interfaces.http.routers.workers as workers
    from mateai.interfaces.http.auth_dependencies import get_current_user
    from mateai.interfaces.http import enrollment
    monkeypatch.setattr(enrollment, "get_worker_enrollment_secret", lambda: "enroll-secret")
    app = FastAPI()
    app.include_router(workers.router)
    app.dependency_overrides[get_current_user] = lambda: {"username": "boss", "role": "admin"}
    r = TestClient(app).get("/api/v1/download-agent")
    assert r.status_code == 200 and r.headers["content-type"] == "application/x-zip-compressed"
    names = zipfile.ZipFile(io.BytesIO(r.content)).namelist()
    assert {"agent.py", "config.json", "install_agent.ps1", "install_agent.bat", "uninstall_agent.ps1"} <= set(names)
    assert not [n for n in names if n.startswith(("logs", ".venv")) or n.endswith(".pyc")]
    assert r.headers["X-Agent-Version"] not in ("", "unknown")
