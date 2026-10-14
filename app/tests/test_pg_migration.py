# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 Dương Thanh Lịch — VN-MateAI. See LICENSE and NOTICE.
"""
tests/test_pg_migration.py
==========================
Prompt cuối §65–§66: di trú SQLite -> PostgreSQL theo giai đoạn, có kiểm chứng và đường lùi.

  schema map -> migrate -> validate (số dòng + checksum từng bảng) -> rollback được.
Chạy trên PostgreSQL THẬT (`pgserver`, gói dev, khởi động trong thư mục tạm); không có thì bỏ qua.
Nguồn là DB SQLite của bộ test (đủ mọi bảng thật của hệ thống, do db_manager / erp_database dựng).
"""
from __future__ import annotations

import pytest

import os

# Nguồn di trú là FILE SQLite của ứng dụng; khi cả bộ test chạy trên PostgreSQL thì ứng dụng không
# ghi SQLite nên không có nguồn để chép.
pytestmark = pytest.mark.skipif(os.environ.get("VNMATEAI_DATABASE_URL", "sqlite") != "sqlite",
                                reason="nguồn di trú là SQLite — bộ test đang chạy trên PostgreSQL")

pgserver = pytest.importorskip("pgserver", reason="cần gói dev pgserver (PostgreSQL thật) — requirements-dev.txt")


@pytest.fixture(scope="module")
def pg_dsn(tmp_path_factory):
    srv = pgserver.get_server(str(tmp_path_factory.mktemp("pg")), cleanup_mode="stop")
    yield srv.get_uri()
    try:
        srv.cleanup()
    except Exception:
        pass


@pytest.fixture
def source_db():
    from mateai.infrastructure.database.db_manager import db_manager
    from mateai.infrastructure.database.erp_database import erp_db
    # Dữ liệu có đủ kiểu: chữ tiếng Việt, số thực, NULL, chuỗi băm audit.
    dept = erp_db.add_department(name="PG Kế toán", description="phòng thử")
    erp_db.add_employee(dept["id"], "Nguyễn Văn Á", "Kế toán", None, "", "viewer")
    erp_db.write_audit_log("pg_test", "success", "tester", {"so_tien": 1234.5, "ghi_chu": "tiền mặt"})
    yield db_manager.db_path
    erp_db.delete_department(dept["id"])


def test_schema_map_covers_every_table(source_db):
    from mateai.infrastructure.database import pg_migration as pm
    plan = pm.schema_map(source_db)
    names = {t["table"] for t in plan}
    assert {"users", "audit_logs", "op_tasks", "departments", "employees"} <= names
    users = next(t for t in plan if t["table"] == "users")
    assert users["primary_key"] == ["id"] and any(c["pg_type"] == "TEXT" for c in users["columns"])


def test_migrate_verify_and_rollback(source_db, pg_dsn):
    import psycopg
    from mateai.infrastructure.database import pg_migration as pm
    rep = pm.migrate(source_db, pg_dsn, schema="vnmate_t1")
    assert rep["ok"] is True, rep["mismatches"]
    t = {r["table"]: r for r in rep["tables"]}
    assert t["audit_logs"]["rows_sqlite"] == t["audit_logs"]["rows_pg"] > 0
    assert t["employees"]["checksum_sqlite"] == t["employees"]["checksum_pg"]
    with psycopg.connect(pg_dsn, autocommit=True) as conn:
        name = conn.execute("SELECT name FROM vnmate_t1.employees WHERE name LIKE 'Nguyễn%'").fetchone()[0]
        assert name == "Nguyễn Văn Á"
        with pytest.raises(psycopg.errors.RaiseException, match="append-only"):
            conn.execute("UPDATE vnmate_t1.audit_logs SET status = 'failed'")
        # tự tăng tiếp tục SAU id lớn nhất đã chép (không đụng khoá chính)
        nxt = conn.execute("SELECT nextval(pg_get_serial_sequence('vnmate_t1.departments', 'id'))").fetchone()[0]
        mx = conn.execute("SELECT max(id) FROM vnmate_t1.departments").fetchone()[0]
        assert nxt > mx
    with pytest.raises(pm.MigrationError, match="đã tồn tại"):
        pm.migrate(source_db, pg_dsn, schema="vnmate_t1")          # không ghi đè âm thầm
    pm.rollback(pg_dsn, schema="vnmate_t1")
    with psycopg.connect(pg_dsn, autocommit=True) as conn:
        assert conn.execute("SELECT count(*) FROM information_schema.schemata WHERE schema_name = 'vnmate_t1'"
                            ).fetchone()[0] == 0


def test_verification_detects_divergence(source_db, pg_dsn):
    import psycopg
    from mateai.infrastructure.database import pg_migration as pm
    pm.migrate(source_db, pg_dsn, schema="vnmate_t2")
    try:
        with psycopg.connect(pg_dsn, autocommit=True) as conn:
            conn.execute("UPDATE vnmate_t2.employees SET position = 'đã sửa' WHERE name LIKE 'Nguyễn%'")
        rep = pm.verify(source_db, pg_dsn, schema="vnmate_t2")
        assert rep["ok"] is False and "employees" in {m["table"] for m in rep["mismatches"]}
    finally:
        pm.rollback(pg_dsn, schema="vnmate_t2")
