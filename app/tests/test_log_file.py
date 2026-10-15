# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 Dương Thanh Lịch — VN-MateAI. See LICENSE and NOTICE.
"""
tests/test_log_file.py
======================
Log ra tệp xoay vòng (2026-10-14): chạy bằng Start.bat log chỉ nằm trên cửa sổ console — đóng cửa sổ là mất dấu vết
sự cố (tệp log mới nhất khi đó là của 12/10). Tệp phải che bí mật như console, xoay vòng có giới hạn, và tắt được.
"""
from __future__ import annotations

import logging
from pathlib import Path

import pytest

from mateai.config import log_setup


@pytest.fixture
def restore_root():
    root = logging.getLogger()
    saved, level = list(root.handlers), root.level
    yield
    for h in root.handlers:
        if h not in saved:
            h.close()
    root.handlers[:] = saved
    root.setLevel(level)


def _flush():
    for h in logging.getLogger().handlers:
        h.flush()


def test_file_log_is_redacted_and_kept_once(tmp_path, restore_root):
    f = tmp_path / "logs" / "vnmateai.log"
    log_setup.configure_logging("INFO", "text", log_file=f)
    log_setup.configure_logging("INFO", "text", log_file=f)          # nạp lại cấu hình: không nhân đôi handler
    logging.getLogger("t").info("gọi https://x/?api_key=sk-THAT-SU-BI-MAT&q=1 với Bearer abc.def.ghi")
    _flush()
    text = f.read_text(encoding="utf-8")
    assert text.count("gọi https://x/") == 1
    assert "sk-THAT-SU-BI-MAT" not in text and "abc.def.ghi" not in text and "[REDACTED]" in text


def test_file_log_rotates_with_a_bound(tmp_path, monkeypatch, restore_root):
    monkeypatch.setattr(log_setup, "LOG_FILE_MAX_BYTES", 400)
    monkeypatch.setattr(log_setup, "LOG_FILE_BACKUPS", 2)
    f = tmp_path / "vnmateai.log"
    log_setup.configure_logging("INFO", "text", log_file=f)
    for i in range(60):
        logging.getLogger("t").info("dòng %d %s", i, "x" * 40)
    _flush()
    files = sorted(p.name for p in tmp_path.iterdir())
    assert files == ["vnmateai.log", "vnmateai.log.1", "vnmateai.log.2"]


def test_rollover_failure_keeps_logging(tmp_path, monkeypatch, restore_root):
    monkeypatch.setattr(log_setup, "LOG_FILE_MAX_BYTES", 200)
    f = tmp_path / "vnmateai.log"
    log_setup.configure_logging("INFO", "text", log_file=f)

    def locked(*_a, **_k):
        raise PermissionError("tệp đang mở ở tiến trình khác")
    monkeypatch.setattr(log_setup.RotatingFileHandler, "rotate", locked)
    for i in range(20):
        logging.getLogger("t").info("dòng %d %s", i, "y" * 40)
    _flush()
    assert "dòng 19" in f.read_text(encoding="utf-8")


def test_log_file_location_and_switch_off(monkeypatch, tmp_path):
    monkeypatch.delenv("VNMATEAI_LOG_FILE", raising=False)
    assert log_setup.default_log_file(tmp_path) == tmp_path / "logs" / "vnmateai.log"
    for off in ("none", "off", "", "0"):
        monkeypatch.setenv("VNMATEAI_LOG_FILE", off)
        assert log_setup.default_log_file(tmp_path) is None
    monkeypatch.setenv("VNMATEAI_LOG_FILE", str(tmp_path / "khac.log"))
    assert log_setup.default_log_file(tmp_path) == Path(tmp_path / "khac.log")


def test_unwritable_log_file_does_not_break_console(tmp_path, restore_root):
    blocker = tmp_path / "la_tep"
    blocker.write_text("x")
    log_setup.configure_logging("INFO", "text", log_file=blocker / "vnmateai.log")   # thư mục cha là một tệp
    assert sum(1 for h in logging.getLogger().handlers if getattr(h, "_vnmate", False)) == 1
