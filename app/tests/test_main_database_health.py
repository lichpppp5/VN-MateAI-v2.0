# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 Dương Thanh Lịch — VN-MateAI. See LICENSE and NOTICE.
"""
tests/test_main_database_health.py
==================================
Giám sát CSDL CHÍNH (không phải CSDL nhân sự AD):

  - `health_monitor._check_main_database` đo đúng CSDL của ứng dụng và nói rõ backend;
    ô "CSDL" trước đây đo tệp `hr_kpi.db` và ghi "SQLite · N KB" kể cả khi đã chạy PostgreSQL;
  - `autonomous_sentinel.check_main_database` mở sự cố `database_down` khi CSDL chính không truy
    cập được (đêm 2026-10-06 PostgreSQL mất kết nối mà không có sự cố nào được mở).
"""
from __future__ import annotations

import asyncio
import os

import pytest

from mateai.application.operations import health_monitor as hm


def _expected_backend() -> str:
    return "sqlite" if os.environ.get("VNMATEAI_DATABASE_URL", "sqlite") == "sqlite" else "postgresql"


def test_main_database_probe_reports_backend_and_size():
    res = hm._check_main_database()
    assert res["ok"] is True and res["backend"] == _expected_backend()
    assert res["size_kb"] >= 0
    entry = hm._database_service_entry(res)
    label = "PostgreSQL" if res["backend"] == "postgresql" else "SQLite"
    assert entry["status"] == "OK" and entry["backend"] == res["backend"] and entry["detail"].startswith(label + " · ")


def test_main_database_probe_failure_is_fail_not_ok(monkeypatch):
    from mateai.infrastructure.database import erp_database

    def boom():
        raise RuntimeError("the connection is lost")
    monkeypatch.setattr(erp_database.erp_db, "get_connection", boom)
    res = hm._check_main_database()
    assert res["ok"] is False and "RuntimeError" in res["error"]
    entry = hm._database_service_entry(res)
    assert entry["status"] == "FAIL" and entry["detail"].startswith("CSDL chính lỗi")


def test_size_formatting():
    assert hm._format_db_size(512.0) == "512.0 KB" and hm._format_db_size(12800.0) == "12.5 MB"


def test_sentinel_opens_incident_when_main_db_is_down(monkeypatch):
    from mateai.application.operations.autonomous_sentinel import autonomous_sentinel
    from mateai.infrastructure.database import erp_database
    assert autonomous_sentinel.check_main_database() is None            # CSDL ổn: không sự cố

    def down():
        raise ConnectionError("the connection is lost")
    monkeypatch.setattr(erp_database.erp_db, "ping", down)
    inc = autonomous_sentinel.check_main_database()
    assert inc["category"] == "database_down" and "ConnectionError" in inc["message"]


def test_scan_includes_main_database_incident(monkeypatch):
    from mateai.application.operations import autonomous_sentinel as sen
    s = sen.autonomous_sentinel

    async def no_net():
        return None
    monkeypatch.setattr(s, "check_network_health", no_net)
    monkeypatch.setattr(s, "check_ad_sync_health", lambda: None)
    monkeypatch.setattr(s, "check_sql_health", lambda: None)
    monkeypatch.setattr(s, "check_hardware_limits", lambda: None)
    monkeypatch.setattr(s, "check_backup_freshness", lambda now=None: None)
    monkeypatch.setattr(s, "check_main_database",
                        lambda: {"category": "database_down", "title": "t", "message": "m"})
    incidents = asyncio.run(s.scan_all())
    assert [i["category"] for i in incidents] == ["database_down"]
