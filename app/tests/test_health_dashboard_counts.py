# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 Dương Thanh Lịch — VN-MateAI. See LICENSE and NOTICE.
"""
tests/test_health_dashboard_counts.py
=====================================
`/api/v1/health-dashboard` đếm đúng số máy trạm đang kết nối.

Trước đây hàm dùng tên `orchestrator` không tồn tại ở cấp module của server;
NameError bị `except` nuốt nên `active_lan_clients` LUÔN là 0. Không gọi mạng.
"""
from __future__ import annotations

import asyncio

import mateai.interfaces.http.routers.health as health


def test_active_lan_clients_counts_connected_workers(monkeypatch):
    monkeypatch.setattr(health.orchestrator, "get_connected_clients", lambda: [{"client_id": "a"}, {"client_id": "b"}])
    out = asyncio.run(health.health_dashboard_endpoint())
    nodes = out.get("nodes", out) if isinstance(out, dict) else {}
    assert nodes.get("active_lan_clients") == 2, out
