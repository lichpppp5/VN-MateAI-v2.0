# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 Dương Thanh Lịch — VN-MateAI. See LICENSE and NOTICE.
"""
tests/test_connector_ai_tools.py
================================
Mặt công cụ AI của lớp đấu nối: AI thấy truy vấn/thao tác đã khai báo (không thấy khoá), truyền tham số cho truy vấn,
và thao tác CAN THIỆP luôn bị chặn chờ duyệt — máy chủ thật không nhận lệnh trước khi người có thẩm quyền duyệt.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))
from fake_enterprise import FakeServer  # noqa: E402

from mateai.application.security import security_guard as sg  # noqa: E402
from mateai.infrastructure.connectors import custom_registry as reg  # noqa: E402
from skills import data_source_tools as tools  # noqa: E402


@pytest.fixture
def source(tmp_path, monkeypatch):
    monkeypatch.setattr(reg, "STORE_PATH", tmp_path / "data_sources.json")
    with FakeServer() as srv:
        reg.upsert_source("ha-tang", {
            "id": "ha-tang", "title": "Hạ tầng", "base_url": srv.url, "auth_type": "bearer", "auth_value": "GOOD",
            "queries": {"vm": {"description": "Danh sách", "method": "GET", "path": "/bearer/{vm_id}",
                               "params": {"vm_id": {"type": "string", "required": True}}}},
            "actions": {"restart vm": {"description": "Khởi động lại", "method": "POST", "path": "/api/vms/{vm_id}/restart",
                                       "params": {"vm_id": {"type": "string", "required": True}}}},
        })
        yield srv


async def test_listing_shows_declared_queries_and_actions_but_no_secret(source):
    out = await tools.list_data_sources()
    item = out["data_sources"][0]
    assert item["queries"][0]["name"] == "vm" and item["queries"][0]["params"]["vm_id"]["required"] is True
    assert item["actions"][0]["name"] == "restart vm" and item["actions"][0]["risk_level"] >= 3
    assert "GOOD" not in str(out) and source.url not in str(out)


async def test_fetch_passes_declared_arguments(source):
    out = await tools.fetch_data_source("ha-tang", report="vm", args={"vm_id": "items"})
    assert out.get("success"), out
    assert out["returned"] >= 1 and source.calls_to("/bearer/items")


async def test_action_waits_for_approval_and_the_server_is_not_touched(source):
    out = await tools.run_data_source_action("ha-tang", "restart vm", {"vm_id": "vm-7"})
    assert out.get("awaiting_approval") is True and out.get("approval_id")
    assert source.calls_to("/api/vms/vm-7/restart") == []


async def test_unknown_action_is_refused_with_the_declared_names(source):
    out = await tools.run_data_source_action("ha-tang", "xoa tat ca", {})
    assert out["success"] is False and out["available_actions"] == ["restart vm"]
    assert source.calls_to("/api/vms/") == []


def test_only_admin_may_run_actions_but_support_can_read():
    def allowed(role, tool):
        return sg.security_guard._evaluate_rbac(tool, role, "t")[0]
    assert allowed("admin", "run_data_source_action") is True
    for role in ("operator", "viewer", "it_support"):
        assert allowed(role, "run_data_source_action") is False, role
    assert allowed("it_support", "fetch_data_source") is True
