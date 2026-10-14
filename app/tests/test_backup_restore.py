# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 Dương Thanh Lịch — VN-MateAI. See LICENSE and NOTICE.
"""
tests/test_backup_restore.py
============================
Sao lưu / kiểm chứng / khôi phục (prompt Supervisor §116): "có file backup" chưa đủ.
Chạy trên một cây thư mục tạm — không đụng dữ liệu thật.
"""
from __future__ import annotations

import importlib.util
import json
import sqlite3
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("vn_backup", ROOT / "scripts" / "backup.py")
backup = importlib.util.module_from_spec(spec)
spec.loader.exec_module(backup)


@pytest.fixture
def site(tmp_path):
    db = tmp_path / "vnmateai.db"
    con = sqlite3.connect(db)
    con.execute("CREATE TABLE users (id INTEGER PRIMARY KEY, name TEXT)")
    con.executemany("INSERT INTO users (name) VALUES (?)", [("a",), ("b",), ("c",)])
    con.commit()
    con.close()
    (tmp_path / "config.json").write_text('{"x": 1}', encoding="utf-8")
    (tmp_path / "certs").mkdir()
    (tmp_path / "certs" / "config_secret.key").write_text("k", encoding="utf-8")
    (tmp_path / "storage" / "vector_db").mkdir(parents=True)
    (tmp_path / "storage" / "vector_db" / "chroma.bin").write_bytes(b"\x00\x01")
    return tmp_path


def test_create_is_verified_and_counts_rows(site):
    dest = backup.create(site)
    m = json.loads((dest / "manifest.json").read_text(encoding="utf-8"))
    assert m["items"]["vnmateai.db"]["rows"] == {"users": 3}
    assert m["items"]["hr_kpi.db"]["present"] is False
    assert backup.verify(dest) == []


def test_corruption_is_detected(site):
    dest = backup.create(site)
    (dest / "config.json").write_text('{"x": 2}', encoding="utf-8")
    con = sqlite3.connect(dest / "vnmateai.db")
    con.execute("DELETE FROM users WHERE id = 1")
    con.commit()
    con.close()
    problems = backup.verify(dest)
    assert any("config.json" in p for p in problems) and any("vnmateai.db" in p for p in problems)
    with pytest.raises(RuntimeError):
        backup.restore(dest, site)                                  # không khôi phục từ bản hỏng


def test_restore_round_trip_keeps_a_safety_copy(site):
    dest = backup.create(site)
    con = sqlite3.connect(site / "vnmateai.db")
    con.execute("DELETE FROM users")
    con.commit()
    con.close()
    (site / "config.json").write_text('{"x": 99}', encoding="utf-8")
    safety = backup.restore(dest, site)
    con = sqlite3.connect(site / "vnmateai.db")
    assert con.execute("SELECT COUNT(*) FROM users").fetchone()[0] == 3
    con.close()
    assert json.loads((site / "config.json").read_text(encoding="utf-8")) == {"x": 1}
    assert json.loads((safety / "manifest.json").read_text(encoding="utf-8"))["items"]["vnmateai.db"]["rows"] == {"users": 0}


def test_backups_dir_is_gitignored():
    assert "backups/" in (ROOT / ".gitignore").read_text(encoding="utf-8")
