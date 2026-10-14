# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 Dương Thanh Lịch — VN-MateAI. See LICENSE and NOTICE.
"""
tests/test_role_resolution_order.py
===================================
Danh tính có trong DB dùng role trong DB, xét TRƯỚC service principal / tiền tố.

Trước đây tài khoản viewer tên "hudson" (tiền tố "hud") hay "console" (service
principal) được cấp admin. Telegram: danh sách admin_chat_ids trống từng nghĩa
là ai nhắn bot cũng được chạy lệnh (và nhận admin qua tiền tố "telegram").
Không gọi mạng; DB tra cứu được giả lập.
"""
from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from mateai.application.security.security_guard import security_guard  # noqa: E402


def _fake_db(monkeypatch, users):
    import mateai.infrastructure.database.erp_database as database
    import mateai.infrastructure.database.db_manager as db_manager_mod
    monkeypatch.setattr(database.erp_db, "get_employee_by_identifier", lambda _i: None)
    monkeypatch.setattr(db_manager_mod.db_manager, "get_user_by_username_or_id",
                        lambda i: users.get(str(i)))


def test_db_user_named_like_a_device_keeps_db_role(monkeypatch):
    _fake_db(monkeypatch, {"hudson": {"role": "viewer"}, "console": {"role": "manager"}})
    assert security_guard._resolve_role("hudson") == "operator"   # viewer → operator
    assert security_guard._resolve_role("console") == "it_support"  # manager → it_support


def test_no_prefix_identity_shortcuts(monkeypatch):
    """Prompt Supervisor (2026-10-05) thay f389bbe: tên bắt đầu bằng esp32/robot/hud
    không còn là admin; Telegram chỉ admin khi chat_id nằm trong admin_chat_ids."""
    from mateai.config.loader import settings
    _fake_db(monkeypatch, {})
    monkeypatch.setattr(settings.telegram, "admin_chat_ids", ["6112"])
    assert security_guard._resolve_role("telegram:6112:alice") == "admin"
    assert security_guard._resolve_role("telegram:999:mallory") == "viewer"
    for name in ("esp32_livingroom", "robot", "hud", "console", "xiaozhi"):
        assert security_guard._resolve_role(name) == "viewer", name
    assert security_guard._resolve_role("someone-unknown") == "viewer"


def test_lookup_error_fails_closed_even_for_device_prefix(monkeypatch):
    import mateai.infrastructure.database.erp_database as database

    def boom(_i):
        raise RuntimeError("db down")

    monkeypatch.setattr(database.erp_db, "get_employee_by_identifier", boom)
    assert security_guard._resolve_role("esp32_kitchen") == "viewer"


async def test_telegram_ignores_everyone_when_no_admin_chat_configured(monkeypatch):
    from mateai.interfaces.telegram.telegram_gateway import telegram_gateway
    import mateai.application.agent.llm_engine as llm_mod

    asked = []

    async def fake_ask(**kw):
        asked.append(kw)
        return {"reply": "ok", "route_info": {}}

    monkeypatch.setattr(llm_mod.llm_engine, "ask_async", fake_ask)
    monkeypatch.setattr(telegram_gateway, "_get_config",
                        lambda: SimpleNamespace(admin_chat_ids=[], bot_token="x"))

    replies = []

    async def reply_text(text, **_kw):
        replies.append(text)

    async def send_chat_action(**_kw):
        return None

    update = SimpleNamespace(
        effective_chat=SimpleNamespace(id=4242),
        effective_user=SimpleNamespace(username="stranger", first_name="S"),
        message=SimpleNamespace(text="xoá thư mục logs", reply_text=reply_text),
    )
    context = SimpleNamespace(bot=SimpleNamespace(send_chat_action=send_chat_action))
    await telegram_gateway._handle_message(update, context)
    assert asked == [] and replies == []
