# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 Dương Thanh Lịch — VN-MateAI. See LICENSE and NOTICE.
"""
tests/test_sentinel_reads_health_cache.py
=========================================
Realtime P6 (D6): autonomous_sentinel không tự đo lại phần cứng / 9Router — đọc
số đo của health_monitor (SYSTEM_HEALTH_CACHE). Số đo cũ thì không báo sự cố.
"""
from __future__ import annotations

import time

import pytest

import mateai.application.operations.autonomous_sentinel as sentinel
from mateai.application.operations import health_monitor as hm


@pytest.fixture
def cache(monkeypatch):
    c = {"last_updated": time.time(), "hardware": {"ram_percent": 40.0, "disk_percent": 50.0},
         "services": {"llm_9router": {"status": "OK", "checked_at": time.time(), "detail": ""}}}
    monkeypatch.setattr(hm, "SYSTEM_HEALTH_CACHE", c)
    return c


def test_hardware_alert_from_cache(cache):
    s = sentinel.autonomous_sentinel
    assert s.check_hardware_limits() is None
    cache["hardware"]["ram_percent"] = 97.0
    assert "RAM" in s.check_hardware_limits()["title"]


def test_stale_hardware_data_is_not_an_incident(cache):
    cache["hardware"]["ram_percent"] = 97.0
    cache["last_updated"] = time.time() - 600
    assert sentinel.autonomous_sentinel.check_hardware_limits() is None


async def test_router_failure_from_cache(cache):
    s = sentinel.autonomous_sentinel
    assert await s.check_network_health() is None
    cache["services"]["llm_9router"].update(status="FAIL", detail="HTTP 502")
    assert "502" in (await s.check_network_health())["title"]
    cache["services"]["llm_9router"].update(detail="ConnectError")
    assert "Mất Kết Nối" in (await s.check_network_health())["title"]
    cache["services"]["llm_9router"]["checked_at"] = time.time() - 600
    assert await s.check_network_health() is None

