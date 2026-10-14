# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 Dương Thanh Lịch — VN-MateAI. See LICENSE and NOTICE.
"""
Sao lưu tin cậy (sự cố 2026-10-09 → 12: tác vụ 02:00 chạy khi PostgreSQL chưa lên → 85 giờ không có bản nào, mỗi lần hỏng để lại
thư mục rỗng):
  - bản dở không bao giờ nằm lại: làm ở `.partial-*`, hỏng thì xoá, đạt mới đổi tên; sentinel / chạy bù bỏ qua `.partial-*`;
  - khoá loại trừ: hai lần sao lưu không chạy cùng lúc, khoá mồ côi (tiến trình chết) quá 2 giờ được gỡ;
  - `--ensure-infra`: CSDL SQLite thì không làm gì; PostgreSQL chưa lên thì bật hạ tầng, vẫn hỏng thì nói rõ bước hỏng;
  - hỏng → cảnh báo NGAY (không đợi 26 giờ), mã thoát 1; đang có lần khác chạy → bỏ qua, mã 0;
  - máy chủ chạy bù khi bản mới nhất cũ hơn ngưỡng (tắt được), không chạy khi chưa từng bật sao lưu.
"""
from __future__ import annotations

import importlib.util
import os
import sqlite3
import sys
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("vn_backup_rel", ROOT / "scripts" / "backup.py")
backup = importlib.util.module_from_spec(spec)
spec.loader.exec_module(backup)


@pytest.fixture
def site(tmp_path):
    con = sqlite3.connect(tmp_path / "vnmateai.db")
    con.execute("CREATE TABLE t (id INTEGER PRIMARY KEY)")
    con.commit()
    con.close()
    (tmp_path / "config.json").write_text("{}", encoding="utf-8")
    return tmp_path


def test_successful_backup_has_no_partial_left_and_releases_the_lock(site):
    dest = backup.create(site)
    names = [p.name for p in (site / "backups").iterdir()]
    assert dest.name[:8].isdigit() and (dest / "manifest.json").is_file()
    assert not any(n.startswith(".partial-") for n in names) and ".backup.lock" not in names


def test_failed_backup_removes_its_partial_folder(site, monkeypatch):
    def boom(root, dest):
        (dest / "half.txt").write_text("dở", encoding="utf-8")
        raise RuntimeError("PostgreSQL không kết nối được")
    monkeypatch.setattr(backup, "_create_into", boom)
    with pytest.raises(RuntimeError):
        backup.create(site)
    assert [p.name for p in (site / "backups").iterdir()] == []                                # không thư mục dở, không khoá


def test_lock_blocks_a_second_run_and_stale_locks_are_cleared(site):
    lock = backup._lock(site)
    with pytest.raises(backup.BackupLocked):
        backup.create(site)
    old = time.time() - backup.LOCK_STALE_S - 10
    os.utime(lock, (old, old))
    assert backup.create(site).is_dir()


def test_ensure_infra_is_a_no_op_on_sqlite(monkeypatch):
    monkeypatch.setattr(backup, "_pg_target", lambda root, rel: None)
    assert "SQLite" in backup.ensure_infra()


def test_ensure_infra_starts_docker_and_reports_the_failing_step(monkeypatch):
    monkeypatch.setattr(backup, "_pg_target", lambda root, rel: ("postgresql://x", "vnmate"))
    calls = []
    state = {"db": False}
    monkeypatch.setattr(backup, "_pg_reachable", lambda url, timeout=3: state["db"])

    def run(cmd, timeout):
        calls.append(cmd[:3])
        if cmd[:3] == ["docker", "compose", "-f"]:
            state["db"] = True
        return True
    monkeypatch.setattr(backup, "_run", run)
    assert "đã bật hạ tầng" in backup.ensure_infra() and ["docker", "info"] in calls
    state["db"] = False
    monkeypatch.setattr(backup, "_run", lambda cmd, timeout: cmd[:2] != ["docker", "compose"])
    with pytest.raises(RuntimeError, match="docker compose"):
        backup.ensure_infra()
    monkeypatch.setattr(backup, "_run", lambda cmd, timeout: True)
    with pytest.raises(RuntimeError, match="vẫn không kết nối được"):
        backup.ensure_infra(wait_db_s=0)


def test_cli_failure_alerts_immediately_and_lock_is_skipped(monkeypatch, capsys):
    alerts = []
    monkeypatch.setattr(backup, "_notify_failure", lambda reason: alerts.append(reason))
    monkeypatch.setattr(backup, "create", lambda: (_ for _ in ()).throw(RuntimeError("PoolTimeout")))
    monkeypatch.setattr(sys, "argv", ["backup.py", "create"])
    assert backup.main() == 1 and "PoolTimeout" in alerts[0] and "THẤT BẠI" in capsys.readouterr().out
    monkeypatch.setattr(backup, "create", lambda: (_ for _ in ()).throw(backup.BackupLocked("đang chạy")))
    assert backup.main() == 0 and len(alerts) == 1


# ── chạy bù từ máy chủ ──────────────────────────────────────────────────────

def test_catch_up_runs_only_when_the_latest_backup_is_too_old(tmp_path, monkeypatch):
    from mateai.application.operations import backup_catchup as bc
    monkeypatch.setattr(bc, "_root", lambda: tmp_path)
    cfg = {"max_age_hours": 26}
    monkeypatch.setattr(bc, "_cfg", lambda: cfg)
    assert bc.due() is False                                                                 # chưa từng bật sao lưu
    (tmp_path / "backups" / ".partial-20261012-010101").mkdir(parents=True)
    assert bc.due() is True                                                                  # chỉ có bản dở = chưa có bản nào
    good = tmp_path / "backups" / "20261012-020000"
    good.mkdir()
    (good / "manifest.json").write_text("{}", encoding="utf-8")
    assert bc.due() is False and bc.latest_local_age_hours() < 1
    old = time.time() - 30 * 3600
    os.utime(good, (old, old))
    assert bc.due() is True
    cfg["auto_catch_up"] = False
    assert bc.due() is False and "tắt" in bc.start()
