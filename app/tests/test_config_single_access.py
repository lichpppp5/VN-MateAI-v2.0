# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 Dương Thanh Lịch — VN-MateAI. See LICENSE and NOTICE.
"""
tests/test_config_single_access.py
==================================
config.json chỉ được đọc/ghi qua mateai.config.loader.

Trước đây 14 chỗ tự mở file: ghi không nguyên tử (mất điện giữa chừng = file cụt =
server không khởi động được: SystemExit), đọc-sửa-ghi không khoá (hai request
Lưu cùng lúc ghi đè nhau), và nhiều chỗ nuốt lỗi JSON rồi GHI ĐÈ bằng bản rỗng.
Dùng file cấu hình tạm, không đụng config.json thật.
"""
from __future__ import annotations

import json
import sys
import threading
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import mateai.config.loader as cl  # noqa: E402


@pytest.fixture
def cfg(tmp_path, monkeypatch):
    p = tmp_path / "config.json"
    monkeypatch.setattr(cl, "CONFIG_PATH", p)
    return p


def test_reads_are_tolerant_and_sections_are_dicts(cfg):
    assert cl.read_raw_config() == {} and cl.get_config_section("telegram") == {}
    cfg.write_text("{ not json", encoding="utf-8")
    assert cl.read_raw_config() == {}
    cfg.write_text(json.dumps({"telegram": {"enabled": True}, "persona": "x"}), encoding="utf-8")
    assert cl.get_config_section("telegram") == {"enabled": True}
    assert cl.get_config_section("persona") == {}  # không phải dict


def test_strict_read_refuses_corrupt_file_and_update_does_not_overwrite_it(cfg):
    cfg.write_text("{ not json", encoding="utf-8")
    with pytest.raises(ValueError):
        cl.read_raw_config(strict=True)
    with pytest.raises(ValueError):
        cl.update_config_section("telegram", {"enabled": True})
    assert cfg.read_text(encoding="utf-8") == "{ not json", "file hỏng không được bị ghi đè bằng bản rỗng"


def test_write_is_atomic_and_leaves_no_temp_files(cfg, monkeypatch):
    cfg.write_text(json.dumps({"keep": 1}), encoding="utf-8")

    def boom(*_a, **_k):
        raise OSError("disk full")

    monkeypatch.setattr(cl.json, "dump", boom)
    with pytest.raises(OSError):
        cl.write_raw_config({"keep": 2})
    assert json.loads(cfg.read_text(encoding="utf-8")) == {"keep": 1}, "ghi lỗi không được làm hỏng file cũ"
    assert not list(cfg.parent.glob(".config.*.tmp")), "không để lại file tạm"


def test_concurrent_section_updates_do_not_lose_each_other(cfg):
    cfg.write_text(json.dumps({"other": {"a": 1}}), encoding="utf-8")
    errs = []

    def worker(i):
        try:
            cl.update_config_section(f"sec{i}", {"v": i})
        except Exception as exc:  # pragma: no cover
            errs.append(exc)

    ts = [threading.Thread(target=worker, args=(i,)) for i in range(12)]
    [t.start() for t in ts]
    [t.join() for t in ts]
    data = json.loads(cfg.read_text(encoding="utf-8"))
    assert not errs and data["other"] == {"a": 1}
    assert all(data[f"sec{i}"] == {"v": i} for i in range(12))
