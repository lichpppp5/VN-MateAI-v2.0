# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 Dương Thanh Lịch — VN-MateAI. See LICENSE and NOTICE.
"""Connector cũ (Paperless-ngx) chạy với máy chủ giả đúng hình dạng API thật — trước đây chưa có test nào."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))
from fake_enterprise import FakeServer  # noqa: E402

from mateai.infrastructure.connectors.base_connector import ConnectorConfig  # noqa: E402
from mateai.infrastructure.connectors.paperless_connector import PaperlessConnector  # noqa: E402


@pytest.fixture(scope="module")
def srv():
    with FakeServer() as s:
        yield s


def paperless(url, token="PL-TOKEN"):
    return PaperlessConnector(ConnectorConfig(name="paperless", timeout_seconds=5.0, retry_count=0, retry_backoff_seconds=0.01,
                                              extra={"base_url": url, "api_token": token}))


async def test_paperless_health_and_search(srv):
    c = paperless(srv.url)
    health = await c.health_check()
    assert health.success and health.data["version"] == "2.11.0"
    res = await c.search_document("hợp đồng", limit=5)
    assert res.success, res.error
    assert res.data["total_results"] == 3 and res.data["documents"][0]["title"] == "Hợp đồng 1"


async def test_paperless_wrong_token_is_reported_not_crashed(srv):
    c = paperless(srv.url, token="SAI")
    assert not (await c.health_check()).success
    assert not (await c.search_document("x")).success


async def test_paperless_without_configuration_says_so():
    c = PaperlessConnector(ConnectorConfig(name="paperless", timeout_seconds=1.0, retry_count=0, extra={}))
    res = await c.health_check()
    assert not res.success
