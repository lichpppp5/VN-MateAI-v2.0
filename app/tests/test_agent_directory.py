# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 Dương Thanh Lịch — VN-MateAI. See LICENSE and NOTICE.
"""
Danh bạ Agent (docs/integrations/agent-gateway.md):
  - inbound: token chỉ hiện một lần, chỉ lưu hash, xoay token làm token cũ hết hiệu lực; `agent:<id>` lấy quyền trong danh bạ; tạm dừng
    → bị từ chối ngay (middleware + route) và quyền về thấp nhất; JWT người dùng không thay được token agent; gọi công cụ qua cổng
    chính sách; hạn mức token / ngày riêng; duyệt-rồi-nhớ như robot;
  - A2A: đọc agent card (dự phòng agent.json, chặn địa chỉ RPC metadata), giao việc → Message hoặc Task (hỏi lại tasks/get), trạng thái
    cần người, lỗi; công cụ `a2a_<id>` rủi ro mặc định L3 (cần duyệt); tạm dừng gỡ công cụ;
  - danh bạ gộp cả MCP server; tạm dừng MCP = tắt server.
"""
from __future__ import annotations

import json

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from core.plugin_manager import plugin_manager
from mateai.application.agent import tool_gate
from mateai.application.agent_gateway import directory as d
from mateai.application.agent_gateway import mcp_gateway as gw
from mateai.application.llmops import usage
from mateai.application.security import policy_engine
from mateai.application.security.security_guard import security_guard
from mateai.application.skills.plugin_registry import plugin_registry
from mateai.config.loader import settings
from mateai.infrastructure.a2a import client as a2a
from mateai.infrastructure.database.db_manager import db_manager


@pytest.fixture(autouse=True)
def clean(monkeypatch, tmp_path):
    monkeypatch.setattr(gw, "STORE_PATH", tmp_path / "mcp_servers.json")
    for r in db_manager.dev_list("agent_directory", limit=1000):
        d.delete(r["agent_id"], "t")
    for r in db_manager.dev_list("llm_usage", limit=5000):
        db_manager.dev_delete("llm_usage", r["usage_id"])
    yield
    for r in db_manager.dev_list("agent_directory", limit=1000):
        d.delete(r["agent_id"], "t")


# ── inbound ─────────────────────────────────────────────────────────────────

def test_inbound_token_identity_role_and_kill_switch():
    with pytest.raises(d.DirectoryError):
        d.save("A B", {"kind": "inbound"}, "admin")
    with pytest.raises(d.DirectoryError):
        d.save("crm-bot", {"kind": "inbound", "role": "superadmin"}, "admin")
    v = d.save("crm-bot", {"kind": "inbound", "name": "Bot CRM", "owner": "Phòng KD", "role": "it_support"}, "admin")
    assert v["status"] == d.ACTIVE and v["has_token"] is False
    assert security_guard._resolve_role("agent:crm-bot") == "it_support"
    token = d.issue_token("crm-bot", "admin")
    assert token.startswith("crm-bot.") and token not in json.dumps(db_manager.dev_get("agent_directory", "crm-bot"))
    assert d.authenticate(token)[0]["agent_id"] == "crm-bot" and d.get("crm-bot")["last_seen_at"]
    assert d.authenticate("crm-bot.sai")[0] is None and d.authenticate("khong-dau-cham")[0] is None
    new = d.issue_token("crm-bot", "admin")
    assert d.authenticate(token)[0] is None and d.authenticate(new)[0]                          # xoay token: token cũ hết hiệu lực
    d.set_status("crm-bot", d.SUSPENDED, "admin")
    row, why = d.authenticate(new)
    assert row is None and "tạm dừng" in why and security_guard._resolve_role("agent:crm-bot") == "viewer"
    assert security_guard._resolve_role("agent:khong-co") == "viewer"
    with pytest.raises(d.DirectoryError):
        d.save("crm-bot", {"kind": "a2a", "url": "https://x"}, "admin")                         # không đổi loại


def test_agent_principals_get_remembered_approvals_like_robots():
    assert policy_engine.grant_principal("agent:crm-bot") == "agent:crm-bot"
    assert policy_engine.grant_principal("crm-bot") is None


def test_per_agent_daily_token_budget(monkeypatch):
    d.save("crm-bot", {"kind": "inbound", "role": "admin", "daily_token_limit": 1000}, "admin")
    monkeypatch.setattr(settings.llmops, "budget_mode", "block")
    for f in ("daily_token_limit", "user_daily_token_limit", "daily_cost_limit", "monthly_cost_limit"):
        monkeypatch.setattr(settings.llmops, f, 0)
    usage.record("agent:crm-bot", "m", 900, 200)
    assert "agent:crm-bot" in (usage.check("agent:crm-bot") or "") and usage.check("admin") is None
    assert d.daily_limit("crm-bot") == 0 and d.daily_limit("agent:crm-bot") == 1000


def _server_client():
    from mateai.interfaces.http.server import app
    return TestClient(app)


def test_inbound_http_requires_an_agent_token_not_a_user_jwt(monkeypatch):
    d.save("crm-bot", {"kind": "inbound", "role": "admin"}, "admin")
    token = d.issue_token("crm-bot", "admin")
    from mateai.application.security.auth_manager import auth_manager
    jwt = auth_manager.create_access_token({"sub": "admin", "role": "admin"})
    c = _server_client()
    assert c.get("/api/v1/agents/inbound/tools").status_code == 401
    assert c.get("/api/v1/agents/inbound/tools", headers={"Authorization": f"Bearer {jwt}"}).status_code == 401
    r = c.get("/api/v1/agents/inbound/tools", headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 200 and r.json()["role"] == "admin" and any(t["name"] == "get_system_info" for t in r.json()["tools"])
    d.set_status("crm-bot", d.SUSPENDED, "admin")
    assert c.get("/api/v1/agents/inbound/tools", headers={"Authorization": f"Bearer {token}"}).status_code == 403


def test_inbound_tool_call_and_ask_go_through_policy(monkeypatch):
    d.save("crm-bot", {"kind": "inbound", "role": "operator"}, "admin")
    token = d.issue_token("crm-bot", "admin")
    seen = {}

    async def fake_gate(name, args, **kw):
        seen["gate"] = (name, args, kw["caller"])
        return {"result": {"status": "success", "data": 1}}
    monkeypatch.setattr(tool_gate, "run_tool_with_policy", fake_gate)

    async def fake_ask(msg, **kw):
        seen["ask"] = (msg, kw)
        return {"reply": "xong", "tool_calls_made": [{"skill": "get_system_info"}]}
    from mateai.application.agent.llm_engine import llm_engine
    monkeypatch.setattr(llm_engine, "ask_async", fake_ask)
    c = _server_client()
    h = {"Authorization": f"Bearer {token}"}
    assert c.post("/api/v1/agents/inbound/tools/get_system_info", json={"arguments": {"x": 1}}, headers=h).json()["status"] == "success"
    assert seen["gate"] == ("get_system_info", {"x": 1}, "agent:crm-bot")
    r = c.post("/api/v1/agents/inbound/ask", json={"message": "Kiểm tra máy chủ", "conversation_id": "c1"}, headers=h).json()
    assert r["reply"] == "xong" and r["tools_used"] == ["get_system_info"]
    assert seen["ask"][1]["caller"] == "agent:crm-bot" and seen["ask"][1]["session_id"] == "agent:crm-bot:c1"
    assert c.post("/api/v1/agents/inbound/ask", json={"message": ""}, headers=h).status_code == 422


# ── A2A ─────────────────────────────────────────────────────────────────────

class FakeAgent:
    def __init__(self, mode="task", card_url="/rpc"):
        self.mode, self.card_url, self.rpc, self.polls = mode, card_url, [], 0

    def __call__(self, request: httpx.Request) -> httpx.Response:
        if request.method == "GET":
            if request.url.path == "/.well-known/agent-card.json":
                return httpx.Response(404)
            if request.url.path == "/.well-known/agent.json":
                return httpx.Response(200, json={"name": "Agent Kế toán", "description": "Đối soát hoá đơn", "url": self.card_url,
                                                 "skills": [{"id": "doi-soat", "name": "Đối soát", "description": "So hoá đơn với sổ"}]})
        body = json.loads(request.content)
        self.rpc.append((body["method"], body["params"], request.headers.get("authorization")))
        mid = body["id"]
        if body["method"] == "message/send":
            if self.mode == "message":
                return httpx.Response(200, json={"jsonrpc": "2.0", "id": mid, "result": {"kind": "message", "role": "agent",
                                                                                           "parts": [{"kind": "text", "text": "Đã đối soát: khớp."}]}})
            if self.mode == "input":
                return httpx.Response(200, json={"jsonrpc": "2.0", "id": mid, "result": {"kind": "task", "id": "t1", "contextId": "ctx1",
                                      "status": {"state": "input-required", "message": {"parts": [{"kind": "text", "text": "Tháng nào?"}]}}}})
            if self.mode == "error":
                return httpx.Response(200, json={"jsonrpc": "2.0", "id": mid, "error": {"code": -32000, "message": "quá tải"}})
            return httpx.Response(200, json={"jsonrpc": "2.0", "id": mid, "result": {"kind": "task", "id": "t1", "contextId": "ctx1",
                                                                                       "status": {"state": "working"}}})
        self.polls += 1
        return httpx.Response(200, json={"jsonrpc": "2.0", "id": mid, "result": {"kind": "task", "id": "t1", "contextId": "ctx1",
                              "status": {"state": "completed"}, "artifacts": [{"parts": [{"kind": "text", "text": "Lệch 2 hoá đơn."}]}]}})


@pytest.fixture
def agent(monkeypatch):
    fake = FakeAgent()
    real = a2a.A2AClient

    def factory(base_url, headers=None, timeout=60.0, client=None, poll_interval=1.0):
        return real(base_url, headers=headers, timeout=timeout, client=httpx.AsyncClient(transport=httpx.MockTransport(fake)), poll_interval=0.01)
    monkeypatch.setattr(a2a, "A2AClient", factory)
    return fake


async def _save_a2a(**extra):
    d.save("ke-toan", {"kind": "a2a", "name": "Kế toán", "url": "https://kt.congty.vn", "auth_value": "Bearer kt-secret", **extra}, "admin")
    return await d.refresh_card("ke-toan", "admin")


async def test_a2a_card_registers_a_tool_and_tasks_are_polled(agent):
    out = await _save_a2a()
    assert out["ok"] and out["agent"]["card"]["skills"][0]["id"] == "doi-soat" and out["agent"]["card"]["url"] == "https://kt.congty.vn/rpc"
    assert out["agent"]["tool"] == "a2a_ke_toan" and plugin_registry.get_tool("a2a_ke_toan").risk_level == 3
    assert "a2a_ke_toan" in {t["function"]["name"] for t in plugin_manager.get_all_tools()}
    res = await d.call_a2a("ke-toan", "Đối soát hoá đơn tháng 9")
    assert res["success"] and res["content"] == "Lệch 2 hoá đơn." and res["untrusted"] and agent.polls == 1
    assert agent.rpc[0][0] == "message/send" and agent.rpc[0][2] == "Bearer kt-secret"
    assert agent.rpc[0][1]["message"]["parts"][0]["text"] == "Đối soát hoá đơn tháng 9"


async def test_a2a_message_input_required_and_errors(agent):
    await _save_a2a()
    agent.mode = "message"
    assert (await d.call_a2a("ke-toan", "x"))["content"] == "Đã đối soát: khớp."
    agent.mode = "input"
    res = await d.call_a2a("ke-toan", "x")
    assert res["success"] is False and res["state"] == "input-required" and "ctx1" in res["error"] and "Hỏi lại NGƯỜI DÙNG" in res["error"]
    agent.mode = "error"
    res = await d.call_a2a("ke-toan", "x")
    assert res["success"] is False and "quá tải" in res["error"] and d.get("ke-toan")["last_error"]


async def test_a2a_card_pointing_at_cloud_metadata_is_refused(agent):
    agent.card_url = "http://169.254.169.254/latest/meta-data"
    out = await _save_a2a()
    assert out["ok"] is False and plugin_registry.get_tool("a2a_ke_toan") is None


async def test_a2a_suspend_removes_the_tool_and_the_gate_requires_approval(agent, monkeypatch):
    await _save_a2a()
    approvals = []
    monkeypatch.setattr(tool_gate.hitl_manager, "request_approval", lambda **kw: approvals.append(kw) or {"id": "AP-1"})
    gate = await tool_gate.run_tool_with_policy("a2a_ke_toan", {"message": "đối soát"}, caller="admin", source_device="portal")
    assert gate["result"]["status"] == "need_confirm" and agent.rpc == []
    d.set_status("ke-toan", d.SUSPENDED, "admin")
    assert plugin_registry.get_tool("a2a_ke_toan") is None and (await d.call_a2a("ke-toan", "x"))["success"] is False
    d.set_status("ke-toan", d.ACTIVE, "admin")
    assert plugin_registry.get_tool("a2a_ke_toan") is not None


# ── danh bạ gộp ─────────────────────────────────────────────────────────────

def test_directory_lists_mcp_servers_and_can_suspend_them():
    d.save("crm-bot", {"kind": "inbound", "role": "viewer"}, "admin")
    gw.save_server("erp", {"name": "ERP", "transport": "http", "url": "https://erp.congty.vn/mcp"}, "admin")
    kinds = {a["agent_id"]: a["kind"] for a in d.list_all()}
    assert kinds == {"crm-bot": "inbound", "erp": "mcp"}
    with pytest.raises(d.DirectoryError):
        d.save("erp", {"kind": "inbound"}, "admin")                                           # trùng mã MCP server
    d.set_status("erp", d.SUSPENDED, "admin")
    assert gw.get_server("erp")["enabled"] is False
    gw.delete_server("erp", "admin")


def test_admin_api_permissions(monkeypatch):
    from mateai.interfaces.http.auth_dependencies import get_current_user
    from mateai.interfaces.http.routers.agent_directory import router

    def client(role):
        app = FastAPI()
        app.include_router(router)
        app.dependency_overrides[get_current_user] = lambda: {"role": role, "username": "u-" + role}
        return TestClient(app)
    base = "/api/v1/agent-gateway/agents"
    assert client("viewer").get(base).status_code == 403
    m = client("manager")
    assert m.get(base).status_code == 200 and m.put(base + "/x1", json={"kind": "inbound"}).status_code == 403
    a = client("admin")
    assert a.put(base + "/bot", json={"kind": "robot"}).status_code == 422
    assert a.put(base + "/bot", json={"kind": "inbound", "role": "operator"}).json()["role"] == "operator"
    tok = a.post(base + "/bot/token").json()
    assert tok["token"].startswith("bot.") and "/api/v1/agents/inbound/ask" in tok["usage"]
    assert a.post(base + "/bot/status", json={"status": "lung-tung"}).status_code == 422
    assert a.post(base + "/khong-co/status", json={"status": "suspended"}).status_code == 404
    assert a.get(base + "/bot").json()["has_token"] is True
    assert a.delete(base + "/bot").status_code == 200 and a.delete(base + "/bot").status_code == 404


def test_directory_shows_dev_fleet_and_workstations_read_only(monkeypatch):
    from mateai.application.devfleet.service import dev_fleet, DevFleetService
    from mateai.interfaces.websocket.client_orchestrator import orchestrator
    monkeypatch.setattr(DevFleetService, "mode", property(lambda self: "read_only"))
    monkeypatch.setattr(dev_fleet, "_task_counts", lambda: {"total": 4})
    monkeypatch.setattr(orchestrator, "get_connected_clients", lambda: [{"client_id": "c1", "hostname": "PC-KETOAN-01"}])
    rows = {a["agent_id"]: a for a in d.list_all()}
    assert rows["dev-fleet"]["kind"] == "devfleet" and rows["dev-fleet"]["mode"] == "read_only" and rows["dev-fleet"]["tasks"] == 4
    assert rows["may-tram"]["online"] == 1 and rows["may-tram"]["names"] == ["PC-KETOAN-01"]
    for reserved in ("dev-fleet", "may-tram"):
        with pytest.raises(d.DirectoryError):
            d.save(reserved, {"kind": "inbound"}, "admin")
    with pytest.raises(d.DirectoryError):
        d.set_status("dev-fleet", d.SUSPENDED, "admin")                                       # quản lý ở tab Dev Fleet


def test_heartbeat_grid_only_monitors_and_never_hands_out_work():
    from mateai.application.devices.elastic_grid_manager import ElasticGridManager
    g = ElasticGridManager()
    ack = g.record_heartbeat("mac-1", ip="10.0.0.5", cpu_percent=12.5)
    assert ack["assigned_task"] is None and ack["grid_online_nodes"] == 1
    assert not hasattr(g, "dispatch_or_standby") and "standby_queue_length" not in g.get_grid_overview()
    assert g.get_grid_overview()["nodes"][0]["node_id"] == "mac-1"
