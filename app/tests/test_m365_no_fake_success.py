# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 Dương Thanh Lịch — VN-MateAI. See LICENSE and NOTICE.
"""
tests/test_m365_no_fake_success.py
==================================
Connector Microsoft 365 (Teams / Outlook) khi CHƯA cấu hình trước đây trả
{"status": "success", "mode": "simulated"} — người gọi tưởng cảnh báo đã tới
nơi. Nay phải báo rõ là KHÔNG gửi, kèm lý do. Không gọi mạng.
"""
from __future__ import annotations

from mateai.infrastructure.connectors.m365_connector import Microsoft365Connector


async def test_teams_without_credentials_is_not_reported_as_sent(monkeypatch):
    for k in ("M365_TENANT_ID", "M365_CLIENT_ID", "M365_CLIENT_SECRET"):
        monkeypatch.delenv(k, raising=False)
    c = Microsoft365Connector(tenant_id="", client_id="", client_secret="")
    c.tenant_id = c.client_id = c.client_secret = ""
    res = await c.send_teams_channel_message("team", "chan", "CPU cao", "<b>x</b>")
    assert res["status"] == "error" and "M365_TENANT_ID" in res["detail"]


async def test_outlook_without_credentials_is_not_reported_as_sent():
    c = Microsoft365Connector()
    c.tenant_id = c.client_id = c.client_secret = ""
    res = await c.send_outlook_email(["it@congty.vn"], "CPU cao", "<b>x</b>")
    assert res["status"] == "error" and res["destination"] == "it@congty.vn"
