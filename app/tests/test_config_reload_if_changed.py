# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 Dương Thanh Lịch — VN-MateAI. See LICENSE and NOTICE.
"""
Vòng hỏi tin Telegram (~10 s) từng gọi `reload_settings()` vô điều kiện: đọc + giải mã config.json liên tục và mỗi vòng
một dòng "AppSettings reloaded" làm ngập tab Nhật ký. Nay chỉ nạp lại khi config.json đổi; dòng httpx `getUpdates … 200`
bị lọc, lỗi vẫn giữ.
"""
from __future__ import annotations

import json
import logging
import os

import pytest


@pytest.fixture
def tmp_config(tmp_path, monkeypatch):
    import mateai.config.loader as loader
    cfg = tmp_path / "config.json"
    cfg.write_text(json.dumps({"llm": {"base_url": "http://localhost:20128/v1", "model_name": "m-one"}}), encoding="utf-8")
    monkeypatch.setattr(loader, "CONFIG_PATH", cfg)
    loader.reload_settings()
    yield cfg
    monkeypatch.undo()
    loader.reload_settings()


def test_no_reload_when_file_unchanged(tmp_config, caplog):
    import mateai.config.loader as loader
    with caplog.at_level(logging.INFO, logger="mateai.config.loader"):
        for _ in range(5):
            loader.reload_settings_if_changed()
    assert not [r for r in caplog.records if "reloaded" in r.getMessage()]


def test_reload_when_file_changes(tmp_config):
    import mateai.config.loader as loader
    tmp_config.write_text(json.dumps({"llm": {"base_url": "http://localhost:20128/v1", "model_name": "m-two-longer"}}),
                          encoding="utf-8")
    st = tmp_config.stat()
    os.utime(tmp_config, ns=(st.st_atime_ns, st.st_mtime_ns + 1_000_000))
    assert loader.reload_settings_if_changed().llm.model_name == "m-two-longer"


def test_telegram_gateway_uses_conditional_reload():
    from pathlib import Path
    src = (Path(__file__).resolve().parents[1] / "src/mateai/interfaces/telegram/telegram_gateway.py").read_text(encoding="utf-8")
    assert "reload_settings_if_changed()" in src
    assert "                reload_settings()\n" not in src.replace("\r\n", "\n")


def _rec(msg: str) -> logging.LogRecord:
    return logging.LogRecord("httpx", logging.INFO, __file__, 1, msg, None, None)


def test_quiet_poll_filter_drops_only_successful_polls():
    from mateai.config.log_setup import QuietPollFilter
    f = QuietPollFilter()
    assert not f.filter(_rec('HTTP Request: POST https://api.telegram.org/bot•••/getUpdates "HTTP/1.1 200 OK"'))
    assert f.filter(_rec('HTTP Request: POST https://api.telegram.org/bot•••/getUpdates "HTTP/1.1 409 Conflict"'))
    assert f.filter(_rec('HTTP Request: POST https://api.telegram.org/bot•••/sendMessage "HTTP/1.1 200 OK"'))
    assert any(isinstance(x, QuietPollFilter) for x in logging.getLogger("httpx").filters)
