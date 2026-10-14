# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 Dương Thanh Lịch — VN-MateAI. See LICENSE and NOTICE.
"""
tests/test_backup_offsite.py
============================
Sao lưu không chỉ nằm trên ổ của máy chủ (một điểm hỏng):

  - `replicate` chép bản nén sang thư mục khác (ổ vật lý khác / ổ mạng), đối chiếu sha256 bản
    chép và chỉ giữ N bản mới nhất mang tên chuẩn — tệp khác trong thư mục không bị đụng;
  - sentinel báo sự cố `backup_stale` khi bản mới nhất ở máy chủ hoặc ở bản sao quá hạn,
    và im lặng khi chưa từng bật sao lưu.
"""
from __future__ import annotations

import importlib.util
import os
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("vn_backup_offsite", ROOT / "scripts" / "backup.py")
backup = importlib.util.module_from_spec(spec)
spec.loader.exec_module(backup)


def _zip(folder: Path, name: str, body: bytes = b"PK-test") -> Path:
    p = folder / name
    p.write_bytes(body)
    return p


def test_replicate_copies_verifies_and_keeps_newest(tmp_path):
    src = tmp_path / "local"
    src.mkdir()
    archive = _zip(src, "20261006-020000.zip", b"backup-bytes")
    off = tmp_path / "offsite"
    off.mkdir()
    for i in range(4):                                       # bản cũ hơn đã có ở nơi chép
        _zip(off, f"2026100{i}-020000.zip")
    (off / "ghi-chu.txt").write_text("không phải bản sao lưu", encoding="utf-8")

    res = backup.replicate(archive, [str(off), str(tmp_path / "moi" / "tao")], keep=3)
    assert [r["ok"] for r in res] == [True, True]
    assert (off / archive.name).read_bytes() == b"backup-bytes"
    kept = sorted(f.name for f in off.iterdir() if f.suffix == ".zip")
    assert kept == ["20261002-020000.zip", "20261003-020000.zip", "20261006-020000.zip"]
    assert res[0]["removed"] == ["20261000-020000.zip", "20261001-020000.zip"]
    assert (off / "ghi-chu.txt").exists()                    # tệp lạ giữ nguyên
    assert (tmp_path / "moi" / "tao" / archive.name).exists()  # tự tạo thư mục đích


def test_replicate_reports_unwritable_target(tmp_path):
    archive = _zip(tmp_path, "20261006-020000.zip")
    blocker = tmp_path / "la-tep"
    blocker.write_text("x", encoding="utf-8")                # đích là TỆP -> không tạo được thư mục
    res = backup.replicate(archive, [str(blocker)], keep=3)
    assert res[0]["ok"] is False and res[0]["error"]


def test_offsite_settings_only_for_this_install(tmp_path):
    assert backup.offsite_settings(tmp_path) == {"offsite_dirs": [], "keep": 30}


def _sentinel(monkeypatch, root: Path, backup_cfg):
    import mateai.application.operations.autonomous_sentinel as sen
    import mateai.config.loader as loader
    monkeypatch.setattr(sen.settings, "PROJECT_ROOT", root, raising=False)
    monkeypatch.setattr(loader, "read_raw_config", lambda strict=False: {"backup": backup_cfg})
    return sen.autonomous_sentinel


def test_sentinel_backup_freshness(tmp_path, monkeypatch):
    now = time.time()
    s = _sentinel(monkeypatch, tmp_path, {})
    assert s.check_backup_freshness(now) is None             # chưa từng sao lưu: không báo

    snap = tmp_path / "backups" / "20261006-020000"
    snap.mkdir(parents=True)
    (snap / "manifest.json").write_text("{}", encoding="utf-8")
    os.utime(snap, (now - 3600, now - 3600))
    assert s.check_backup_freshness(now) is None             # bản 1 giờ trước: ổn

    os.utime(snap, (now - 30 * 3600, now - 30 * 3600))
    inc = s.check_backup_freshness(now)
    assert inc["category"] == "backup_stale" and "30 giờ" in inc["message"]

    os.utime(snap, (now - 3600, now - 3600))
    off = tmp_path / "offsite"
    off.mkdir()
    s = _sentinel(monkeypatch, tmp_path, {"offsite_dirs": [str(off)]})
    inc = s.check_backup_freshness(now)
    assert inc and "không có bản nào" in inc["message"]      # bản sao ngoài máy chưa có
    z = _zip(off, "20261006-020000.zip")
    os.utime(z, (now - 600, now - 600))
    assert s.check_backup_freshness(now) is None
