# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 Dương Thanh Lịch — VN-MateAI. See LICENSE and NOTICE.
"""
tests/test_model_registry.py
============================
Prompt cuối §79 (model registry) + §80: mỗi model có trạng thái
APPROVED / EXPERIMENTAL / DEPRECATED / BLOCKED trong `llm.model_registry`.

  - provider KHÔNG BAO GIỜ gọi model BLOCKED (kể cả khi nó là model chính hoặc dự phòng);
    DEPRECATED chỉ dùng sau mọi model khác;
  - lưu cấu hình với model chính bị BLOCKED -> 400, không ghi;
  - API xem (manager) / sửa (chỉ admin, qua đường ghi cấu hình có lịch sử + audit).
Cấu hình tạm; không gọi mạng.
"""
from __future__ import annotations

import json

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from mateai.config.loader import settings


@pytest.fixture
def cfg(monkeypatch, tmp_path):
    import mateai.config.loader as loader
    from mateai.application.security import safety_guard
    p = tmp_path / "config.json"
    p.write_text(json.dumps({"llm": {"base_url": "http://r.local/v1", "model_name": "p/a", "api_key": "k",
                                     "router_models": ["p/a", "p/b", "p/c"], "routing_mode": "router"}}),
                 encoding="utf-8")
    monkeypatch.setattr(loader, "CONFIG_PATH", p)
    audits = []
    monkeypatch.setattr(safety_guard.security_engine, "log_audit", lambda *a, **k: audits.append(a))
    loader.reload_settings()
    yield p, audits
    monkeypatch.undo()
    loader.reload_settings()


def test_blocked_models_are_never_candidates_and_deprecated_go_last(cfg):
    from mateai.infrastructure.llm.llm_provider import NineRouterLLMProvider
    settings.llm.model_registry = {"p/a": {"status": "BLOCKED"}, "p/b": {"status": "DEPRECATED"},
                                   "p/c": {"status": "APPROVED"}}
    prov = NineRouterLLMProvider(client=None, primary_model="p/a", router_models=["p/a", "p/b", "p/c"])
    assert prov._resolve_candidate_models() == ["p/c", "p/b"]
    assert prov._resolve_candidate_models(preferred_model="p/a") == ["p/c", "p/b"]


async def test_blocked_model_call_is_refused_without_network(cfg):
    from mateai.infrastructure.llm.llm_provider import NineRouterLLMProvider
    settings.llm.model_registry = {"p/a": {"status": "BLOCKED"}}
    prov = NineRouterLLMProvider(client=None, primary_model="p/a", router_models=[])
    with pytest.raises(RuntimeError, match="BLOCKED|không có model"):
        await prov.complete([{"role": "user", "content": "hi"}])


def _client(role):
    import mateai.interfaces.http.routers.config as cr
    from mateai.interfaces.http.auth_dependencies import get_current_user

    async def pool():
        return ["p/a", "p/b", "p/c"]

    app = FastAPI()
    app.include_router(cr.router)
    app.dependency_overrides[get_current_user] = lambda: {"username": f"{role}_u", "role": role}
    return TestClient(app), pool


def test_registry_api_admin_only_versioned_and_saving_blocked_primary_fails(cfg, monkeypatch):
    path, audits = cfg
    import mateai.interfaces.http.routers.config as cr
    admin, pool = _client("admin")
    monkeypatch.setattr(cr, "_router_model_pool", pool)
    mgr, _ = _client("manager")
    view = mgr.get("/api/v1/llm/model-registry").json()
    assert {m["model"] for m in view["models"]} >= {"p/a", "p/b", "p/c"}
    assert all(m["status"] == "UNREGISTERED" for m in view["models"])
    assert mgr.put("/api/v1/llm/model-registry", json={"models": {"p/b": {"status": "BLOCKED"}}}).status_code == 403
    r = admin.put("/api/v1/llm/model-registry",
                  json={"models": {"p/b": {"status": "BLOCKED", "note": "rò dữ liệu"}}, "reason": "đánh giá bảo mật"})
    assert r.status_code == 200, r.text
    assert json.loads(path.read_text(encoding="utf-8"))["llm"]["model_registry"]["p/b"]["status"] == "BLOCKED"
    assert any(a[1] == "config_change" for a in audits)
    assert admin.put("/api/v1/llm/model-registry", json={"models": {"p/b": {"status": "CAMXUC"}}}).status_code == 422
    before = path.read_text(encoding="utf-8")
    bad = admin.post("/api/v1/config", json={"llm": {"model_name": "p/b"}})
    assert bad.status_code == 400 and "BLOCKED" in bad.text
    assert path.read_text(encoding="utf-8") == before
