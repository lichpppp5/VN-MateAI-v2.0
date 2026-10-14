# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 Dương Thanh Lịch — VN-MateAI. See LICENSE and NOTICE.
"""
tests/test_audit_integrity.py
=============================
Prompt cuối §73 (immutable audit: append-only + integrity protection).

  - mỗi bản ghi audit mang `prev_hash` + `row_hash` (SHA-256 nối chuỗi) -> sửa / xoá một dòng
    giữa chuỗi bị phát hiện, chỉ ra đúng id;
  - DB chặn UPDATE / DELETE trên `audit_logs` bằng trigger (kể cả code ghi thẳng SQL);
  - endpoint kiểm tra chỉ admin.
DB test riêng (conftest).
"""
from __future__ import annotations

import sqlite3

import pytest


def _ids(n):
    from mateai.infrastructure.database.erp_database import erp_db
    return [erp_db.write_audit_log(f"integrity_test_{i}", "success", "tester", {"i": i}) for i in range(n)]


def test_chain_verifies_and_db_refuses_update_delete():
    from mateai.infrastructure.database.erp_database import erp_db
    ids = _ids(3)
    rep = erp_db.verify_audit_chain()
    assert rep["ok"] is True and rep["verified"] >= 3 and rep["first_broken_id"] is None
    with erp_db.get_connection() as conn:
        with pytest.raises(sqlite3.DatabaseError, match="append-only"):
            conn.execute("UPDATE audit_logs SET status = 'failed' WHERE id = ?;", (ids[1],))
        with pytest.raises(sqlite3.DatabaseError, match="append-only"):
            conn.execute("DELETE FROM audit_logs WHERE id = ?;", (ids[1],))


def test_tampering_is_detected_at_the_right_row():
    """Mô phỏng kẻ có quyền ghi file DB: gỡ trigger rồi sửa một dòng."""
    from mateai.infrastructure.database.erp_database import erp_db
    ids = _ids(3)
    with erp_db.get_connection() as conn:
        from mateai.infrastructure.database.erp_database import is_pg
        conn.execute("DROP TRIGGER IF EXISTS audit_logs_no_update" + (" ON audit_logs" if is_pg(conn) else ""))
        conn.execute("UPDATE audit_logs SET payload = '{\"i\": 999}' WHERE id = ?;", (ids[1],))
        conn.commit()
    try:
        rep = erp_db.verify_audit_chain()
        assert rep["ok"] is False and rep["first_broken_id"] == ids[1]
    finally:
        erp_db.ensure_audit_guards()          # dựng lại trigger cho test sau


def test_verify_endpoint_admin_only(monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    import mateai.interfaces.http.routers.security as sec
    from mateai.interfaces.http.auth_dependencies import get_current_user

    def client(role):
        app = FastAPI()
        app.include_router(sec.router)
        app.dependency_overrides[get_current_user] = lambda: {"username": "u", "role": role}
        return TestClient(app)

    assert client("manager").get("/api/v1/security/audit-logs/verify").status_code == 403
    body = client("admin").get("/api/v1/security/audit-logs/verify").json()
    assert set(body) >= {"ok", "verified", "legacy_unhashed", "first_broken_id"}
