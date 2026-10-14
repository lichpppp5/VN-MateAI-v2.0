# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 Dương Thanh Lịch — VN-MateAI. See LICENSE and NOTICE.
"""
tests/test_config_router_contract.py
====================================
Supervisor Phase 10 (§198): router `/api/v1/config` không tự chuẩn hoá / ghép cấu hình —
nghiệp vụ ở `application/administration/config_service.py`.

Cố định hành vi trước khi chuyển + hai lỗi thật đã sửa:
  - hỏi router danh sách model bằng `urlopen` ĐỒNG BỘ ngay trong hàm async: mỗi lần
    Lưu chặn event loop tới 5 s (mọi kênh voice / WebSocket đứng theo);
  - `POST /api/v1/routing` gọi lưu cấu hình không kèm người dùng: lịch sử + audit ghi
    người sửa là "api" thay vì admin thật.
Dùng config.json tạm; không gọi mạng.
"""
from __future__ import annotations

import asyncio
import json
import threading
import time

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient


@pytest.fixture
def env(tmp_path, monkeypatch):
    import mateai.config.loader as loader
    from mateai.application.security import safety_guard
    from mateai.config import secret_box
    cfg = tmp_path / "config.json"
    cfg.write_text(json.dumps({
        "_comment": "giữ nguyên",
        "llm": {"base_url": "http://r.local/v1", "model_name": "p/m1", "api_key": "sk-REAL",
                "router_models": ["p/m1"], "routing_mode": "router"},
        "telegram": {"enabled": False, "bot_token": "1:TOKEN", "admin_chat_ids": ["1"]},
    }), encoding="utf-8")
    monkeypatch.setattr(loader, "CONFIG_PATH", cfg)
    monkeypatch.delenv("VNMATEAI_CONFIG_KEY", raising=False)
    monkeypatch.delenv("VNMATEAI_CONFIG_ENCRYPTION", raising=False)
    secret_box.reset_cache()
    audits = []
    monkeypatch.setattr(safety_guard.security_engine, "log_audit", lambda *a, **k: audits.append(a))
    import mateai.interfaces.telegram.telegram_gateway as tgm
    monkeypatch.setattr(tgm.telegram_gateway, "start", lambda: None)
    monkeypatch.setattr(tgm.telegram_gateway, "stop", lambda: None)
    yield cfg, audits
    secret_box.reset_cache()
    monkeypatch.undo()
    loader.reload_settings()


def _client(monkeypatch, pool=("p/m1", "p/m2"), role="admin"):
    import mateai.interfaces.http.routers.config as config_router
    from mateai.interfaces.http.auth_dependencies import get_current_user

    async def fake_pool():
        return list(pool)

    monkeypatch.setattr(config_router, "_router_model_pool", fake_pool)
    app = FastAPI()
    app.include_router(config_router.router)
    app.dependency_overrides[get_current_user] = lambda: {"username": f"{role}_u", "role": role}
    return TestClient(app)


def _raw():
    from mateai.config.loader import read_raw_config
    return read_raw_config(strict=True)


def test_save_keeps_unsent_fields_secrets_and_comments(env, monkeypatch):
    c = _client(monkeypatch)
    r = c.post("/api/v1/config", json={"llm": {"model_name": "p/m2", "api_key": "••••••••"},
                                       "telegram": {"admin_chat_ids": ["9"]}, "auto_execute": True})
    assert r.status_code == 200, r.text
    raw = _raw()
    assert raw["_comment"] == "giữ nguyên"
    assert raw["llm"]["api_key"] == "sk-REAL"                         # ký hiệu che không đè khoá thật
    assert raw["llm"]["router_models"][0] == "p/m2"                   # model chính đứng đầu danh sách
    assert raw["telegram"] == {"enabled": False, "bot_token": "1:TOKEN", "admin_chat_ids": ["9"]}
    assert raw["AUTO_EXECUTE_UNVERIFIED_CODE"] is True


def test_legacy_routing_payload_maps_to_llm_block(env, monkeypatch):
    c = _client(monkeypatch)
    r = c.post("/api/v1/config", json={"routing": {"primary": {"provider_model": "p/m2", "api_base": "http://r.local/v1"}}})
    assert r.status_code == 200, r.text
    llm = _raw()["llm"]
    assert llm["model_name"] == "p/m2" and llm["api_key"] == "sk-REAL"
    assert llm["router_models"] == ["p/m2", "p/m1"]                   # pool router, model chính trước


def test_routing_endpoint_records_real_admin(env, monkeypatch):
    cfg, audits = env
    from mateai.infrastructure.database.db_manager import db_manager
    c = _client(monkeypatch)
    r = c.post("/api/v1/routing", json={"routing": {"primary": {"provider_model": "p/m2"}}})
    assert r.status_code == 200, r.text
    change = [a for a in audits if a[1] == "config_change"][-1]
    assert change[0] == "admin_u"                                     # trước đây: "api"
    assert db_manager.list_config_history(1)[0]["saved_by"] == "admin_u"


def test_get_config_view_is_masked_and_has_legacy_aliases(env, monkeypatch):
    body = _client(monkeypatch).get("/api/v1/config").json()
    assert body["llm"]["api_key"] != "sk-REAL" and "sk-REAL" not in json.dumps(body)
    assert body["MODEL_NAME"] == "p/m1" and body["router"] == body["routing"]
    assert "_comment" not in body


async def test_router_model_pool_does_not_block_event_loop(monkeypatch):
    from mateai.application.administration import config_service

    def slow_fetch():
        time.sleep(0.3)
        return ["p/m1"]

    monkeypatch.setattr(config_service, "fetch_router_models", slow_fetch)
    ticks = 0

    async def ticker():
        nonlocal ticks
        while True:
            await asyncio.sleep(0.02)
            ticks += 1

    t = asyncio.create_task(ticker())
    assert await config_service.router_model_pool() == ["p/m1"]
    t.cancel()
    assert ticks >= 8                                                 # loop vẫn chạy trong 0.3 s chờ


def test_deep_merge_contract():
    from mateai.application.administration.config_service import deep_merge
    base = {"t": {"token": "x", "enabled": True}, "l": [1, 2]}
    assert deep_merge(base, {"t": {"enabled": False}, "l": [3]}) == {"t": {"token": "x", "enabled": False}, "l": [3]}
    assert base == {"t": {"token": "x", "enabled": True}, "l": [1, 2]}  # không sửa bản gốc
