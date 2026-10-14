# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 Dương Thanh Lịch — VN-MateAI. See LICENSE and NOTICE.
"""
tests/test_skills_execute_endpoint.py
=====================================
`POST /api/v1/skills/execute` — đường THÀNH CÔNG và chờ duyệt.

Lint (ruff F821, prompt cuối §125) bắt được: sau khi skill đã CHẠY XONG, endpoint ném
`NameError: source_ip` -> người gọi nhận 500 dù tác vụ đã thực hiện, và audit bị bỏ qua.
Chưa test nào đi qua nhánh này nên lỗi lọt. Không chạy skill thật.
"""
from __future__ import annotations

from fastapi import FastAPI
from fastapi.testclient import TestClient


def test_successful_skill_returns_200_and_is_audited(monkeypatch):
    import mateai.interfaces.http.routers.skills as sk
    from core.plugin_manager import plugin_manager
    from mateai.application.security.security_guard import security_guard
    from mateai.interfaces.http.auth_dependencies import get_current_user
    audited = []

    async def fake_exec(name, args):
        return {"status": "success", "cpu": 12}

    monkeypatch.setattr(plugin_manager, "execute_skill", fake_exec)
    monkeypatch.setattr(security_guard, "audit_tool_execution", lambda **kw: audited.append(kw))
    app = FastAPI()
    app.include_router(sk.router)
    app.dependency_overrides[get_current_user] = lambda: {"username": "admin", "role": "admin"}
    r = TestClient(app).post("/api/v1/skills/execute", json={"name": "get_system_info", "arguments": {}})
    assert r.status_code == 200, r.text
    assert r.json()["skill_name"] == "get_system_info"
    assert audited and audited[-1]["execution_status"] == "success" and audited[-1]["source_ip"]
