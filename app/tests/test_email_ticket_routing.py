# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 Dương Thanh Lịch — VN-MateAI. See LICENSE and NOTICE.
"""
tests/test_email_ticket_routing.py
==================================
Định tuyến ticket email: tìm phòng ban theo từ khoá tên, lấy nhân viên đầu tiên.
Truy vấn nằm ở tầng dữ liệu (ERPDatabase) — trước đây email_gateway (tầng giao
diện) tự chạy SQL thô (RULE-004). CSDL tạm.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from mateai.infrastructure.database.erp_database import ERPDatabase  # noqa: E402


def test_department_lookup_and_first_employee(tmp_path):
    db = ERPDatabase(db_path=tmp_path / "erp.db")
    fin = db.add_department("Phòng Tài Chính")["id"]
    it = db.add_department("Phòng Kỹ Thuật")["id"]
    with db.get_connection() as conn:
        conn.execute("INSERT INTO employees (dept_id, name, position, email, phone) VALUES (?, ?, ?, ?, ?);",
                     (it, "Nguyễn Văn A", "Kỹ sư", "a@x.vn", "0900"))
        conn.commit()
    assert db.find_department_id_by_name(["tài chính", "kế toán"]) == fin
    assert db.find_department_id_by_name(["kỹ thuật", "it"]) == it
    assert db.find_department_id_by_name(["không tồn tại"]) is None
    assert db.first_employee_id_in_department(it) is not None
    assert db.first_employee_id_in_department(fin) is None


def test_readonly_select_sandbox_blocks_writes(tmp_path):
    """Hộp cát SQL chỉ-đọc ở tầng dữ liệu: SELECT chạy, mọi ghi bị engine chặn."""
    import sqlite3
    import pytest
    db = ERPDatabase(db_path=tmp_path / "erp.db")
    db.add_department("Phòng A")
    assert db.execute_readonly_select("SELECT name FROM departments")[0]["name"] == "Phòng A"
    for bad in ("DELETE FROM departments", "UPDATE departments SET name='x'",
                "CREATE TABLE t(a)", "ATTACH DATABASE 'x.db' AS x"):
        with pytest.raises(sqlite3.DatabaseError):
            db.execute_readonly_select(bad)
    assert db.execute_readonly_select("SELECT COUNT(*) AS n FROM departments")[0]["n"] == 1


def test_list_employees_filters(tmp_path):
    db = ERPDatabase(db_path=tmp_path / "erp.db")
    a = db.add_department("Phòng A")["id"]
    b = db.add_department("Phòng B")["id"]
    with db.get_connection() as conn:
        conn.execute("INSERT INTO employees (dept_id, name, position, email, phone) VALUES (?, 'An', 'NV', 'a@x', '1')", (a,))
        conn.execute("INSERT INTO employees (dept_id, name, position, email, phone) VALUES (?, 'Bình', 'NV', 'b@x', '2')", (b,))
        conn.commit()
    assert [e["name"] for e in db.list_employees()] == ["An", "Bình"]
    only_b = db.list_employees(dept_id=b)
    assert [e["name"] for e in only_b] == ["Bình"] and only_b[0]["dept_name"] == "Phòng B"
    assert len(db.list_employees(limit=1)) == 1
    db.ping()
