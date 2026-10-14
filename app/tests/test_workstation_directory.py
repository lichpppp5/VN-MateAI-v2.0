# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 Dương Thanh Lịch — VN-MateAI. See LICENSE and NOTICE.
"""
tests/test_workstation_directory.py
===================================
Khớp tên máy + trạng thái Agent:

  - `norm_host` / `resolve` / `agent_status` / `coverage` / `online_list` (hàm thuần);
  - bộ điều phối khớp tên người dùng / AI nói với mã agent đang kết nối: không phân biệt hoa / thường,
    hậu tố miền; mơ hồ thì KHÔNG đoán (báo rõ các máy trùng); offline thì báo "không trực tuyến";
  - công cụ `list_online_workstations` của AI + RBAC (operator / it_support được, viewer không).
"""
from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace

import pytest

from mateai.application.devices import workstation_directory as wd
from mateai.infrastructure.directory.host_names import norm_host


def test_norm_host():
    assert norm_host("PC-KT-01.corp.local") == norm_host("pc-kt-01") == norm_host("PC-KT-01$") == "pc-kt-01"
    assert norm_host("  DESKTOP-ABC ") == "desktop-abc"
    assert norm_host("10.0.0.5") == "10.0.0.5"               # IP giữ nguyên, không cắt theo dấu chấm
    assert norm_host(None) == "" and norm_host("") == ""


CONNECTED = [
    {"client_id": "PC-KT-01", "hostname": "PC-KT-01", "ip": "10.0.0.5", "platform": "Windows", "agent_version": "1.4", "skills_count": 12},
    {"client_id": "may-ban-hang", "hostname": "MAY-BAN-HANG.corp.local", "ip": "10.0.0.6", "platform": "Windows"},
]
ENROLLED = [
    {"client_id": "PC-KT-01", "hostname": "PC-KT-01", "revoked_at": None},
    {"client_id": "PC-NS-02", "hostname": "PC-NS-02", "revoked_at": None},                # đã cài, đang ngoại tuyến
    {"client_id": "PC-OLD-03", "hostname": "PC-OLD-03", "revoked_at": "2026-10-01T00:00:00"},
]


def test_resolve_and_status():
    ix = wd.build_index(CONNECTED, ENROLLED)
    assert wd.resolve("pc-kt-01", ix) == "PC-KT-01" and wd.resolve("PC-KT-01.corp.local", ix) == "PC-KT-01"
    assert wd.resolve("MAY-BAN-HANG", ix) == "may-ban-hang"            # khớp theo tên máy, ra mã agent thật
    assert wd.resolve("PC-NS-02", ix) is None                          # đã cài nhưng ngoại tuyến: không chạy lệnh được
    assert wd.resolve("khong-co", ix) is None and wd.resolve("", ix) is None
    assert wd.agent_status("pc-kt-01.corp.local", ix) == {"status": "online", "label": "Trực tuyến", "client_id": "PC-KT-01"}
    assert wd.agent_status("PC-NS-02", ix)["status"] == "offline"
    assert wd.agent_status("PC-OLD-03", ix)["status"] == "revoked"
    assert wd.agent_status("PC-CHUA-CAI", ix) == {"status": "none", "label": "Chưa cài", "client_id": None}


def test_ambiguous_is_not_guessed():
    ix = wd.build_index([{"client_id": "PC-01", "hostname": "pc-01"}, {"client_id": "pc-01.b", "hostname": "PC-01.b.local"}], [])
    assert wd.resolve("PC-01", ix) == "PC-01"                          # khớp ĐÚNG mã thì không mơ hồ
    assert wd.resolve("Pc-01", ix) is None and wd.resolve("pc-01", ix) is None   # chỉ khớp theo chuẩn hoá, có hai máy -> không đoán
    assert sorted(wd.ambiguous("Pc-01", ix)) == ["PC-01", "pc-01.b"]


def test_coverage_and_online_list():
    ix = wd.build_index(CONNECTED, ENROLLED)
    cov = wd.coverage(["PC-KT-01", "pc-ns-02", "PC-OLD-03", "PC-X", "", None], ix)
    assert cov == {"online": 1, "offline": 1, "revoked": 1, "none": 1, "total": 4}
    rows = wd.online_list(ix)
    assert [r["client_id"] for r in rows] == ["PC-KT-01", "may-ban-hang"]
    assert [r["client_id"] for r in wd.online_list(ix, "10.0.0.6")] == ["may-ban-hang"]
    assert wd.online_list(ix, "khong-co") == []


class _WS:
    def __init__(self):
        self.sent = []

    async def send_text(self, text):
        self.sent.append(json.loads(text))


def _orch():
    from mateai.interfaces.websocket.client_orchestrator import Orchestrator
    o = Orchestrator()
    ws = _WS()

    async def reg():
        await o.register_client("PC-KT-01", ws, {"hostname": "PC-KT-01", "ip": "10.0.0.5", "skills": []})
        await o.register_client("may-ban-hang", _WS(), {"hostname": "MAY-BAN-HANG.corp.local", "skills": []})
    asyncio.run(reg())
    return o, ws


def test_orchestrator_resolves_names():
    o, _ = _orch()
    assert o.resolve_client_id("pc-kt-01") == "PC-KT-01" and o.resolve_client_id("PC-KT-01") == "PC-KT-01"
    assert o.resolve_client_id("may-ban-hang.corp.local") == "may-ban-hang"
    assert o.is_client_online("Pc-Kt-01") is True and o.is_client_online("PC-KHAC") is False


def test_sync_call_reports_offline_and_ambiguous_clearly():
    from mateai.interfaces.websocket.client_orchestrator import Orchestrator
    o = Orchestrator()

    async def reg():
        await o.register_client("PC-01", _WS(), {"hostname": "pc-01"})
        await o.register_client("pc-01.b", _WS(), {"hostname": "PC-01.b.local"})
    asyncio.run(reg())
    res = o.execute_on_client_sync("Pc-01", "get_system_info", {})
    assert res["status"] == "error" and "khớp nhiều máy" in res["error"] and "PC-01" in res["error"]
    res = o.execute_on_client_sync("PC-VANG-MAT", "get_system_info", {})
    assert res["status"] == "error" and "không trực tuyến" in res["error"]


def test_task_is_sent_to_the_canonical_client_when_name_differs_in_case():
    o, ws = _orch()

    async def go():
        o.set_event_loop(asyncio.get_running_loop())
        task = asyncio.create_task(o.execute_on_client("pc-kt-01", "get_system_info", {"include_temps": False}, timeout=0.3))
        await asyncio.sleep(0.1)
        sent = [m for m in ws.sent if m.get("action") == "execute"]
        assert len(sent) == 1 and sent[0]["skill_name"] == "get_system_info" and sent[0]["args"] == {"include_temps": False}
        o.handle_incoming_message("PC-KT-01", {"action": "result", "task_id": sent[0]["task_id"], "success": True,
                                               "result": {"status": "success", "cpu": 12}})
        return await task
    res = asyncio.run(go())
    assert res["status"] == "success" and res["client_id"] == "PC-KT-01"


def test_ai_tool_lists_online_workstations(monkeypatch):
    import sys
    from pathlib import Path
    root = Path(__file__).resolve().parents[1]
    sys.path.insert(0, str(root))
    from mateai.application.devices import worker_enrollment
    from mateai.interfaces.websocket import client_orchestrator
    import skills.workstation_tools as tools
    monkeypatch.setattr(client_orchestrator.orchestrator, "get_connected_clients", lambda: CONNECTED)
    monkeypatch.setattr(worker_enrollment, "list_devices", lambda: ENROLLED)
    res = tools.list_online_workstations()
    assert res["status"] == "success" and res["count"] == 2 and res["workstations"][0]["client_id"] == "PC-KT-01"
    assert tools.list_online_workstations(query="ban-hang")["count"] == 1
    assert "target_client" in res["note"]


def test_rbac_for_the_listing_tool():
    from mateai.application.security.security_guard import RBAC_RULES
    allowed = lambda role: "list_online_workstations" in RBAC_RULES[role].get("allowed_prefixes", [])
    assert allowed("it_support") and allowed("operator") and not allowed("viewer")


def test_ai_is_told_to_list_workstations_before_choosing_a_target():
    from mateai.application.agent.llm_engine import LLMEngine
    tools = [{"type": "function", "function": {"name": "x", "description": "d", "parameters": {"type": "object", "properties": {}}}}]
    out = LLMEngine._enrich_tools_with_target_client(tools)
    assert "list_online_workstations" in out[0]["function"]["parameters"]["properties"]["target_client"]["description"]
