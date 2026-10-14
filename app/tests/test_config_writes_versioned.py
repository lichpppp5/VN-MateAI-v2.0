# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 Dương Thanh Lịch — VN-MateAI. See LICENSE and NOTICE.
"""
tests/test_config_writes_versioned.py
=====================================
Một đường ghi cấu hình (`config_governance.save_config`) cho mọi màn hình — Supervisor
Phase 10 (§198) + §70 / §128: mọi thay đổi chính sách / cấu hình có lịch sử phiên bản
và audit.

Trước 2026-10-06: Telegram (kể cả DANH SÁCH CHAT ADMIN — quyết định ai ra lệnh admin
qua Telegram), AD sync, mẫu báo cáo, danh sách từ khoá bảo mật tự ghi `config.json`
trong router: không lịch sử, phần lớn không audit. Không gọi mạng; cấu hình tạm.
"""
from __future__ import annotations

import json

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from mateai.config.loader import settings
from mateai.infrastructure.database.db_manager import db_manager


@pytest.fixture
def env(monkeypatch, tmp_path):
    import mateai.config.loader as loader
    from mateai.application.security import safety_guard
    cfg = tmp_path / "config.json"
    cfg.write_text(json.dumps({"telegram": {"enabled": False, "bot_token": "123:SECRET", "admin_chat_ids": ["1"]},
                               "security": {}, "ad_sync": {"enabled": False}}), encoding="utf-8")
    monkeypatch.setattr(loader, "CONFIG_PATH", cfg)
    audits = []
    monkeypatch.setattr(safety_guard.security_engine, "log_audit", lambda *a, **k: audits.append(a))
    import mateai.interfaces.telegram.telegram_gateway as tgm
    monkeypatch.setattr(tgm.telegram_gateway, "start", lambda: None)
    monkeypatch.setattr(tgm.telegram_gateway, "stop", lambda: None)
    yield cfg, audits
    monkeypatch.undo()          # trả CONFIG_PATH thật TRƯỚC khi nạp lại
    loader.reload_settings()


def _client(router, role="admin"):
    from mateai.interfaces.http.auth_dependencies import get_current_user
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_current_user] = lambda: {"username": f"{role}_u", "role": role}
    return TestClient(app)


def _history_count():
    return db_manager.count_config_history()


def test_telegram_admin_chat_change_is_versioned_and_audited_without_token(env):
    cfg, audits = env
    import mateai.interfaces.http.routers.telegram as tg
    n0 = _history_count()
    r = _client(tg.router).put("/api/v1/telegram/config",
                                json={"enabled": True, "bot_token": "", "admin_chat_ids": ["1", "999"], "incident_group_id": ""})
    assert r.status_code == 200
    saved = json.loads(cfg.read_text(encoding="utf-8"))["telegram"]
    assert saved["admin_chat_ids"] == ["1", "999"]
    assert _history_count() > n0
    change = [a for a in audits if a[1] == "config_change"][-1]
    assert "telegram.admin_chat_ids" in change[4]["paths"]
    assert "SECRET" not in json.dumps(change, ensure_ascii=False)          # không lộ token trong audit


def test_ad_sync_and_report_templates_and_security_list_are_versioned(env):
    cfg, audits = env
    import mateai.interfaces.http.routers.domain as dom
    import mateai.interfaces.http.routers.report_templates as rt
    import mateai.interfaces.http.routers.security as sec
    n0 = _history_count()
    assert _client(dom.router).post("/api/v1/domain/toggle", json={"enabled": True}).status_code == 200
    assert _client(rt.router).put("/api/v1/report-templates", json={"templates": {"tuần": "Mẫu A"}}).status_code == 200
    r = _client(sec.router).post("/api/v1/security/blacklist", json={"keyword": "format d:", "action": "add", "category": "blacklist"})
    assert r.status_code == 200 and "format d:" in r.json()["forbidden_keywords"]
    data = json.loads(cfg.read_text(encoding="utf-8"))
    assert data["ad_sync"]["enabled"] is True and data["report_templates"] == {"tuần": "Mẫu A"}
    assert _history_count() >= n0 + 3
    assert any(a[1] == "update_blacklist" for a in audits)


def test_unchanged_save_writes_nothing(env):
    cfg, _ = env
    from mateai.application.administration import config_governance as gov
    before = cfg.read_text(encoding="utf-8")
    n0 = _history_count()
    gov.save_config("x", lambda c: None, "không đổi gì")
    assert cfg.read_text(encoding="utf-8") == before and _history_count() == n0
