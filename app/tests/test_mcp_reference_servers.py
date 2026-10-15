# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 Dương Thanh Lịch — VN-MateAI. See LICENSE and NOTICE.
"""MCP server tham chiếu chính thức (vendor/mcp-servers: time, fetch, git) — docs/integrations/mcp-reference-servers.md.

Chạy THẬT các server trong môi trường riêng `.mcp-venv` (tạo bằng `python scripts/setup_mcp_servers.py --install`); thiếu thì bỏ qua.
Kiểm: tiến trình con không nhận biến bí mật của máy chủ; fetch chặn địa chỉ nội bộ; git giới hạn trong một kho; kịch bản đăng ký đặt
đúng rủi ro và TẮT công cụ ghi của git; kết quả MCP mang nhãn không tin cậy."""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest

from mateai.application.agent_gateway import mcp_gateway as gw
from mateai.config.loader import settings
from mateai.infrastructure.mcp import client as mc

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import setup_mcp_servers as setup  # noqa: E402

PY = setup.venv_python(ROOT / ".mcp-venv")
needs_venv = pytest.mark.skipif(not PY.exists(), reason="chưa có .mcp-venv (python scripts/setup_mcp_servers.py --install)")


def test_child_process_gets_a_minimal_environment(monkeypatch):
    monkeypatch.setenv("VNMATEAI_DEFAULT_ADMIN_PASSWORD", "bi-mat")
    monkeypatch.setenv("VNMATEAI_DATABASE_URL", "postgresql://u:p@db/x")
    monkeypatch.setenv("HTTPS_PROXY", "http://proxy:3128")
    env = mc.child_env({"TOKEN": "khai-bao-rieng"})
    assert "VNMATEAI_DEFAULT_ADMIN_PASSWORD" not in env and "VNMATEAI_DATABASE_URL" not in env
    assert env["TOKEN"] == "khai-bao-rieng" and env["HTTPS_PROXY"] == "http://proxy:3128" and env["PATH"]
    assert env["PYTHONIOENCODING"] == "utf-8"


def test_specs_pin_risks_and_switch_off_git_writes():
    sp = setup.specs(PY, git_repo=ROOT)
    assert set(sp) == {"time", "fetch", "git"}
    assert sp["time"]["payload"]["default_risk"] == 1 and "Asia/Ho_Chi_Minh" in sp["time"]["payload"]["args"]
    assert sp["fetch"]["overrides"] == {"fetch": {"risk": 2}} and "--allow-private-ips" not in sp["fetch"]["payload"]["args"]
    assert sp["git"]["payload"]["args"][-2:] == ["--repository", str(ROOT)]
    assert set(sp["git"]["overrides"]) == set(setup.GIT_WRITE_TOOLS) and all(o == {"enabled": False, "risk": 4} for o in sp["git"]["overrides"].values())
    assert "git" not in setup.specs(PY, git_repo=None)


@pytest.fixture
def store(monkeypatch, tmp_path):
    monkeypatch.setattr(gw, "STORE_PATH", tmp_path / "mcp_servers.json")
    monkeypatch.setattr(settings.agent_gateway, "allow_stdio", True)
    gw._clients.clear()
    yield
    for sid in list(gw._registered):
        gw.unregister(sid)
    for sid in list(gw._clients):
        gw._drop_client(sid)


@needs_venv
async def test_setup_registers_the_real_servers_with_the_intended_rules(store):
    results = {r["server"]: r for r in await setup.register(setup.specs(PY, git_repo=ROOT))}
    assert all(r["ok"] for r in results.values()), results
    tools = {t["name"]: t for r in results.values() for t in r["tools_detail"]}
    assert tools["get_current_time"]["risk"] == 1 and tools["convert_time"]["enabled"]
    assert tools["fetch"]["risk"] == 2 and tools["fetch"]["registered_as"] == "mcp_fetch_fetch"
    assert {n for n, t in tools.items() if n.startswith("git_") and t["enabled"]} == {
        "git_status", "git_diff_unstaged", "git_diff_staged", "git_diff", "git_log", "git_show", "git_branch"}
    assert all(tools[n]["risk"] == 1 for n in ("git_status", "git_log", "git_show"))
    assert all(not tools[n]["enabled"] and tools[n]["risk"] == 4 for n in setup.GIT_WRITE_TOOLS)
    registered = set(gw._registered["git"])
    assert "mcp_git_git_status" in registered and "mcp_git_git_commit" not in registered       # công cụ ghi không tới được AI

    now = await gw.call_tool("time", "get_current_time", {"timezone": "Asia/Ho_Chi_Minh"})
    assert now["success"] and now["untrusted"] and json.loads(now["content"])["timezone"] == "Asia/Ho_Chi_Minh"
    conv = await gw.call_tool("time", "convert_time", {"source_timezone": "Asia/Ho_Chi_Minh", "time": "09:00", "target_timezone": "Asia/Tokyo"})
    assert json.loads(conv["content"])["time_difference"] == "+2.0h"

    for url in ("http://127.0.0.1:443/", "http://169.254.169.254/latest/meta-data/", "http://192.168.1.1/"):
        res = await gw.call_tool("fetch", "fetch", {"url": url})
        assert res["success"] is False and "not a public address" in res["error"], (url, res)

    st = await gw.call_tool("git", "git_status", {"repo_path": str(ROOT)})
    assert st["success"] and "Repository status" in st["content"]
    out = await gw.call_tool("git", "git_status", {"repo_path": str(ROOT.parent)})
    assert out["success"] is False and "outside the allowed repository" in out["content"]


@needs_venv
async def test_registration_is_idempotent_and_keeps_admin_overrides(store):
    sp = setup.specs(PY, git_repo=None)
    await setup.register(sp)
    gw.set_tool_override("fetch", "fetch", False, None, "admin")                # admin tắt fetch trên Portal
    again = {r["server"]: r for r in await setup.register(sp)}
    fetch_tool = again["fetch"]["tools_detail"][0]
    assert again["time"]["ok"] and fetch_tool["risk"] == 2
    assert fetch_tool["enabled"] is False                                        # chạy lại kịch bản không bật lại thứ admin đã tắt
