# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 Dương Thanh Lịch — VN-MateAI. See LICENSE and NOTICE.
"""
tests/test_webhook_unsigned_rejected.py
=======================================
Webhook không chữ ký (chưa cấu hình VNMATE_WEBHOOK_<NGUỒN>_SECRET) bị TỪ CHỐI
mặc định. /api/webhooks/* không cần đăng nhập — trước đây webhook không chữ ký
vẫn được chuyển thành tin nhắn vào nhóm Telegram admin + HUD (đã xảy ra thật
khi kiểm tra). Chỉ nhận khi bật security.allow_unsigned_webhooks.
Không gọi mạng: dispatch được giả lập.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

import mateai.interfaces.http.webhook_gateway as wg  # noqa: E402


def _client(monkeypatch, allow: bool):
    dispatched = []

    async def fake_dispatch(self, payload, alert_id):
        dispatched.append(alert_id)
        return {"telegram": True}

    monkeypatch.setattr(wg.AlertProcessor, "dispatch", fake_dispatch)
    monkeypatch.delenv("VNMATE_WEBHOOK_CUSTOM_SECRET", raising=False)
    monkeypatch.setattr("mateai.config.loader.get_config_section",
                        lambda name: {"allow_unsigned_webhooks": allow} if name == "security" else {})
    app = FastAPI()
    app.include_router(wg.router)
    return TestClient(app), dispatched


def test_unsigned_webhook_rejected_by_default(monkeypatch):
    c, dispatched = _client(monkeypatch, allow=False)
    r = c.post("/api/webhooks/custom", json={"event_type": "test", "message": "spam"})
    assert r.status_code == 401
    assert dispatched == [], "không được chuyển tiếp tới Telegram/HUD"


def test_unsigned_webhook_allowed_only_when_opted_in(monkeypatch):
    c, dispatched = _client(monkeypatch, allow=True)
    r = c.post("/api/webhooks/custom", json={"event_type": "test", "message": "setup"})
    assert r.status_code == 200, r.text
    assert len(dispatched) == 1
