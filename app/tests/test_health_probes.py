# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 Dương Thanh Lịch — VN-MateAI. See LICENSE and NOTICE.
"""
tests/test_health_probes.py
===========================
/livez, /startupz, /readyz: công khai, đúng mã trạng thái, phản ánh lỗi thật.
Không chạy startup thật (TestClient không `with`), không gọi mạng.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fastapi.testclient import TestClient  # noqa: E402

import mateai.interfaces.http.server as server  # noqa: E402
from mateai.interfaces.http import lifecycle  # noqa: E402
from mateai.interfaces.http.routers import probes  # noqa: E402


def test_livez_is_public_and_ok():
    assert TestClient(server.app).get("/livez").json() == {"status": "ok"}


def test_startup_and_ready_follow_lifecycle(monkeypatch):
    client = TestClient(server.app)
    monkeypatch.setattr(lifecycle, "STATE", lifecycle.LifecycleState(complete=False))
    assert client.get("/startupz").status_code == 503
    r = client.get("/readyz")
    assert r.status_code == 503 and r.json()["checks"]["startup"] == "starting"

    monkeypatch.setattr(lifecycle, "STATE", lifecycle.LifecycleState(complete=True))
    from core.plugin_manager import plugin_manager
    monkeypatch.setattr(plugin_manager, "get_skill_count", lambda: 3)
    assert client.get("/startupz").status_code == 200
    from mateai.infrastructure.database import migrations
    monkeypatch.setattr(migrations, "_STATE", {"status": "not_run", "detail": ""})
    r = client.get("/readyz")
    assert r.status_code == 503 and r.json()["checks"]["schema"].startswith("pending")   # chưa chạy bước nâng cấp CSDL
    migrations.migrate(migrations.main_db_connect, backup=None)                            # bước "schema_migrations"
    r = client.get("/readyz")
    assert r.status_code == 200, r.json()
    assert r.json()["checks"] == {"startup": "ok", "database": "ok", "schema": "ok", "skills": "ok"}


def test_ready_reports_database_failure(monkeypatch):
    monkeypatch.setattr(lifecycle, "STATE", lifecycle.LifecycleState(complete=True))
    from core.plugin_manager import plugin_manager
    monkeypatch.setattr(plugin_manager, "get_skill_count", lambda: 3)

    def broken():
        raise OSError("disk gone")

    monkeypatch.setattr(probes, "_check_database", broken)
    r = TestClient(server.app).get("/readyz")
    assert r.status_code == 503
    assert r.json()["checks"]["database"] == "error: OSError"
