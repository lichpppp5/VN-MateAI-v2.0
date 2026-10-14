# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 Dương Thanh Lịch — VN-MateAI. See LICENSE and NOTICE.
"""
tests/test_telegram_outbound_guard.py
=====================================
1. Telegram TẮT (hoặc token là giá trị mẫu) → không gọi api.telegram.org.
   Trước đây chỉ kiểm "có token": gateway enabled=false vẫn gửi HITL/alert,
   bằng token mẫu YOUR_TELEGRAM_BOT_TOKEN_HERE, và URL đó vào log.
2. Bộ lọc log che MỌI giá trị sau api.telegram.org/bot — không chỉ đúng dạng
   token chuẩn (giá trị lệch dạng từng lọt ra /api/v1/logs/recent).
Không gọi mạng.
"""
from __future__ import annotations

import logging
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import mateai.interfaces.telegram.telegram_gateway as tg_mod  # noqa: E402
from mateai.interfaces.telegram.telegram_gateway import telegram_gateway  # noqa: E402

REAL_SHAPE = "123456789:AAHf0abcdefghijklmnopqrstuvwxyz012"


@pytest.fixture
def no_network(monkeypatch):
    started = []
    monkeypatch.setattr(tg_mod.threading, "Thread",
                        lambda *a, **k: SimpleNamespace(start=lambda: started.append(k.get("name"))))
    monkeypatch.setattr(tg_mod, "_TELEGRAM_AVAILABLE", True, raising=False)
    return started


def _cfg(monkeypatch, enabled, token):
    monkeypatch.setattr("mateai.config.loader.get_config_section",
                        lambda name: {"enabled": enabled, "bot_token": token} if name == "telegram" else {})
    monkeypatch.setattr(telegram_gateway, "_get_config", lambda: SimpleNamespace(
        bot_token=token, admin_chat_ids=["111"], incident_group_id=""))


@pytest.mark.parametrize("enabled,token", [
    (False, REAL_SHAPE),                       # tắt
    (True, "YOUR_TELEGRAM_BOT_TOKEN_HERE"),    # giá trị mẫu
    (True, ""),
])
def test_no_outbound_send_when_disabled_or_placeholder(monkeypatch, no_network, enabled, token):
    _cfg(monkeypatch, enabled, token)
    assert telegram_gateway.send_incident_alert("x") is False
    assert telegram_gateway.send_hitl_request("HITL-1", "kill_process", "d", 4, {}) is False
    assert no_network == []


def test_sends_when_enabled_with_real_token(monkeypatch, no_network):
    # conftest tắt gửi ra ngoài cho cả phiên; mạng ở test này đã bị no_network chặn.
    monkeypatch.delenv("VNMATEAI_TELEGRAM_OUTBOUND", raising=False)
    _cfg(monkeypatch, True, REAL_SHAPE)
    assert telegram_gateway.send_incident_alert("x") is True
    assert no_network, "phải khởi chạy thread gửi"


def test_outbound_kill_switch_blocks_even_when_enabled(monkeypatch, no_network):
    monkeypatch.setenv("VNMATEAI_TELEGRAM_OUTBOUND", "off")
    _cfg(monkeypatch, True, REAL_SHAPE)
    assert telegram_gateway.send_incident_alert("x") is False
    assert not no_network


def test_log_redaction_masks_any_value_after_bot():
    from mateai.interfaces.http.log_stream import _SecretRedactingFilter
    for tok in ("YOUR_TELEGRAM_BOT_TOKEN_HERE", REAL_SHAPE, "weird-token.with.dots"):
        rec = logging.LogRecord("httpx", logging.INFO, __file__, 1,
                                f'HTTP Request: POST https://api.telegram.org/bot{tok}/sendMessage "HTTP/1.1 200 OK"',
                                (), None)
        _SecretRedactingFilter().filter(rec)
        assert tok not in rec.getMessage(), rec.getMessage()
        assert "sendMessage" in rec.getMessage()
