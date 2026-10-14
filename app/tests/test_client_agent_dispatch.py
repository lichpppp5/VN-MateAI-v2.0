# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 Dương Thanh Lịch — VN-MateAI. See LICENSE and NOTICE.
"""
tests/test_client_agent_dispatch.py
===================================
client_agent: chạy được skill khi `execute_skill` là coroutine (worker cục bộ
trên máy chủ nạp `core.plugin_manager` của server) lẫn bản đồng bộ (máy trạm
thật), và không ghi enrollment token ra log.

Trước đây: worker cục bộ rớt kết nối ở MỌI lệnh ("'coroutine' object has no
attribute 'get'"), và URL kèm `?token=<secret>` được ghi nguyên văn ra log.
"""
from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def client_agent():
    """agent.py tự đặt bí danh `core` / `client_agent` trong sys.modules khi
    import — phải hoàn nguyên sau test, nếu không các test khác import nhầm."""
    saved_modules, saved_path = dict(sys.modules), list(sys.path)
    sys.path.insert(0, str(ROOT / "client_agent"))
    try:
        import agent
        yield agent
    finally:
        sys.modules.clear()
        sys.modules.update(saved_modules)
        sys.path[:] = saved_path


class _WS:
    def __init__(self):
        self.sent = []

    async def send(self, text):
        self.sent.append(json.loads(text))


class _AsyncPM:
    async def execute_skill(self, name, args):
        return {"success": True, "data": {"ran": name}, "error": None}


class _SyncPM:
    def execute_skill(self, name, args):
        return {"status": "success", "ran": name}


@pytest.mark.parametrize("pm", [_AsyncPM(), _SyncPM()])
def test_dispatch_runs_async_and_sync_plugin_managers(client_agent, monkeypatch, pm):
    monkeypatch.setattr(client_agent, "client_plugin_manager", pm)
    a = client_agent.ClientAgent.__new__(client_agent.ClientAgent)
    a.client_id = "T1"
    ws = _WS()
    asyncio.run(a._dispatch_message(ws, {"action": "execute", "skill_name": "get_cpu", "args": {}, "task_id": "t"}))
    assert ws.sent and ws.sent[0]["success"] is True and ws.sent[0]["task_id"] == "t"


def test_token_is_redacted_for_logs(client_agent):
    out = client_agent._redact_url("wss://h/ws/client?token=SeCrEt123&x=1")
    assert "SeCrEt123" not in out and out == "wss://h/ws/client?token=***&x=1"
