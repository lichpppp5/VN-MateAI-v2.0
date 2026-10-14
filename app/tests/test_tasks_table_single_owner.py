# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 Dương Thanh Lịch — VN-MateAI. See LICENSE and NOTICE.
"""
tests/test_tasks_table_single_owner.py
======================================
Bảng `tasks` có MỘT định nghĩa schema (mateai.infrastructure.database.erp_database.ensure_tasks_table).

Trước đây db_manager và ERPDatabase mỗi bên tự CREATE bản riêng; schema thật
phụ thuộc bên nào khởi tạo trước. ERP trước → title NOT NULL → lệnh giao việc
cho máy trạm (db_manager, không có title) lỗi "NOT NULL constraint failed".
Kiểm cả hai thứ tự trên DB tạm.
"""
from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import mateai.infrastructure.database.erp_database as database  # noqa: E402
import mateai.infrastructure.database.db_manager as dbm  # noqa: E402


@pytest.mark.parametrize("erp_first", [True, False])
def test_both_writers_work_whatever_initialises_first(tmp_path, monkeypatch, erp_first):
    monkeypatch.setattr(dbm, "USERS_JSON_PATH", tmp_path / "none.json")
    db = tmp_path / "t.db"
    if erp_first:
        erp = database.ERPDatabase(db_path=db)
        lan = dbm.DatabaseManager(db_path=db)
    else:
        lan = dbm.DatabaseManager(db_path=db)
        erp = database.ERPDatabase(db_path=db)

    lan.add_or_update_task({"id": "t-lan-1", "client_id": "PC-01",
                            "task_message": "Kiểm tra backup", "sender": "CEO", "status": "pending"})
    erp_task = erp.create_erp_task(title="Lập báo cáo quý", status="pending")

    from mateai.infrastructure.database.erp_database import open_sqlite
    with open_sqlite(db) as conn:      # cùng điểm mở với ứng dụng (SQLite / PostgreSQL)
        rows = {r[0]: tuple(r) for r in conn.execute(
            "SELECT id, client_id, task_message, title FROM tasks").fetchall()}
    assert rows["t-lan-1"][1:] == ("PC-01", "Kiểm tra backup", "Kiểm tra backup")
    assert rows[erp_task["id"]][3] == "Lập báo cáo quý"
