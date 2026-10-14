# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 Dương Thanh Lịch — VN-MateAI. See LICENSE and NOTICE.
"""
tests/test_connector_config_paths.py
====================================
1. Connector đọc cấu hình qua config_loader (cổng duy nhất vào config.json) và
   đọc MỚI mỗi lần — trước đây tự mở file + cache riêng phải nhớ xoá.
2. Kho nguồn dữ liệu (config/data_sources.json — có thông tin đăng nhập của
   khách) nằm ở thư mục gốc dự án, không suy từ vị trí file mã nguồn.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from mateai.config.loader import settings  # noqa: E402


def test_connector_block_read_fresh_through_config_loader(monkeypatch):
    import mateai.infrastructure.connectors.base_connector as bc
    blocks = {"paperless": {"base_url": "http://a"}}
    monkeypatch.setattr("mateai.config.loader.get_config_section", lambda name: blocks.get(name, {}))
    assert bc._read_config_json_block("paperless") == {"base_url": "http://a"}
    blocks["paperless"] = {"base_url": "http://b"}
    assert bc._read_config_json_block("paperless") == {"base_url": "http://b"}, "phải đọc mới, không cache"
    assert bc._read_config_json_block("aws") == {}


def test_data_source_store_under_project_root():
    import mateai.infrastructure.connectors.custom_registry as cr
    assert Path(cr.STORE_PATH).resolve() == (Path(settings.PROJECT_ROOT) / "config" / "data_sources.json").resolve()
