# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 Dương Thanh Lịch — VN-MateAI. See LICENSE and NOTICE.
"""
tests/test_schema_migrations.py
===============================
Phiên bản cấu trúc CSDL (2026-10-14): máy chủ đã có dữ liệu nâng cấp lên bản mới — mỗi bước chạy đúng một lần, sao lưu
trước khi đổi dữ liệu thật, lỗi thì cuộn lại, và bản cũ chạy trên CSDL đã nâng cấp phải bị phát hiện.
"""
from __future__ import annotations

import pytest

from mateai.infrastructure.database import migrations as mig
from mateai.infrastructure.database.erp_database import open_sqlite


@pytest.fixture
def db(tmp_path, monkeypatch):
    path = tmp_path / "m.db"
    monkeypatch.setattr(mig, "_STATE", {"status": "not_run", "detail": ""})
    with open_sqlite(path) as conn:
        conn.execute("CREATE TABLE users (id TEXT PRIMARY KEY, username TEXT, note TEXT);")
    return lambda: open_sqlite(path)


def _with_users(connect):
    with connect() as conn:
        conn.execute("INSERT INTO users (id, username, note) VALUES ('1', 'admin', 'cu');")


def _step2(cur):
    cur.execute("UPDATE users SET note = 'moi' WHERE note = 'cu';")


def test_fresh_database_records_baseline_without_backup(db):
    called = []
    res = mig.migrate(db, backup=lambda: called.append(1) or "x")
    assert res["ran"] == ["0001"] and res["current"] == "0001" and not called
    assert mig.readiness() == "ok"
    assert mig.migrate(db, backup=lambda: called.append(1) or "x")["ran"] == []       # lần sau: không chạy lại


def test_real_step_backs_up_first_and_runs_once(db, monkeypatch):
    mig.migrate(db, backup=None)
    _with_users(db)
    monkeypatch.setattr(mig, "MIGRATIONS", mig.MIGRATIONS + [mig.Migration("0002", "đổi ghi chú", _step2)])
    order = []

    def backup():
        with db() as conn:
            order.append(("backup", conn.execute("SELECT note FROM users;").fetchone()[0]))
        return "backups/x"
    res = mig.migrate(db, backup=backup)
    assert res["ran"] == ["0002"] and res["backup"] == "backups/x"
    assert order == [("backup", "cu")]                                                  # sao lưu TRƯỚC khi đổi dữ liệu
    with db() as conn:
        assert conn.execute("SELECT note FROM users;").fetchone()[0] == "moi"
        row = conn.execute("SELECT app_version FROM schema_migrations WHERE version = '0002';").fetchone()
        assert row[0] == mig.app_version()
    assert mig.migrate(db, backup=backup)["ran"] == [] and len(order) == 1


def test_backup_failure_blocks_the_upgrade(db, monkeypatch):
    mig.migrate(db, backup=None)
    _with_users(db)
    monkeypatch.setattr(mig, "MIGRATIONS", mig.MIGRATIONS + [mig.Migration("0002", "đổi ghi chú", _step2)])

    def broken():
        raise OSError("ổ đầy")
    res = mig.migrate(db, backup=broken)
    assert res["ran"] == [] and mig.readiness().startswith("error:")
    with db() as conn:
        assert conn.execute("SELECT note FROM users;").fetchone()[0] == "cu"


def test_failing_step_rolls_back_and_is_not_recorded(db, monkeypatch):
    mig.migrate(db, backup=None)
    _with_users(db)

    def bad(cur):
        cur.execute("UPDATE users SET note = 'do_dang';")
        raise RuntimeError("lỗi giữa chừng")
    monkeypatch.setattr(mig, "MIGRATIONS", mig.MIGRATIONS + [mig.Migration("0002", "hỏng", bad)])
    with pytest.raises(RuntimeError):
        mig.migrate(db, backup=lambda: "b")
    with db() as conn:
        assert conn.execute("SELECT note FROM users;").fetchone()[0] == "cu"
        assert [r[0] for r in conn.execute("SELECT version FROM schema_migrations;").fetchall()] == ["0001"]
    assert mig.readiness().startswith("error:")


def test_old_app_on_upgraded_database_is_detected(db):
    mig.migrate(db, backup=None)
    with db() as conn:
        conn.execute("INSERT INTO schema_migrations (version, description, applied_at) VALUES ('0099', 'tương lai', 'x');")
    res = mig.migrate(db, backup=lambda: "b")
    assert res["unknown"] == ["0099"] and res["ran"] == []
    assert mig.readiness() == "newer_than_app: 0099"


def test_readiness_before_startup_only_reads(db):
    assert mig.readiness(db) == "pending: 0001"
    with db() as conn:
        assert conn.execute("SELECT COUNT(*) FROM schema_migrations;").fetchone()[0] == 0
    mig.migrate(db, backup=None)
    mig._STATE["status"] = "not_run"
    assert mig.readiness(db) == "ok"


def test_released_steps_are_ordered_and_unique():
    versions = [m.version for m in mig.MIGRATIONS]
    assert versions == sorted(versions) and len(set(versions)) == len(versions) and versions[0] == "0001"


def test_version_is_single_source():
    """pyproject.toml và mateai.__version__ từng lệch (1.0.0 / 2.0.0-enterprise) — bản ghi nâng cấp CSDL cần một số."""
    import re
    from pathlib import Path
    import mateai
    py = (Path(__file__).resolve().parents[1] / "pyproject.toml").read_text(encoding="utf-8")
    assert re.search(r'^version\s*=\s*"([^"]+)"', py, re.M).group(1) == mateai.__version__ == mig.app_version()
