# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 Dương Thanh Lịch — VN-MateAI. See LICENSE and NOTICE.
"""
tests/test_data_paths_stable.py
===============================
CSDL và kho vector nằm ở thư mục gốc dự án (settings.PROJECT_ROOT), không suy
từ vị trí file mã nguồn. Chuyển module mà đường dẫn lệch là máy chủ mở một CSDL
RỖNG mới: dữ liệu ERP/audit biến mất khỏi tầm nhìn, tài khoản tạo lại với
mật khẩu mặc định.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from mateai.config.loader import settings  # noqa: E402


def test_data_modules_resolve_from_project_root():
    import importlib
    root = Path(settings.PROJECT_ROOT).resolve()
    for mod, attr in (("DATABASE", "_PROJECT_ROOT"), ("DB_MANAGER", "_PROJECT_ROOT"),
                      ("DOMAIN_SYNC", "_PROJECT_ROOT"), ("COGNITIVE", "PROJECT_ROOT")):
        m = importlib.import_module(MODULES[mod])
        assert Path(getattr(m, attr)).resolve() == root, (mod, getattr(m, attr))


MODULES = {
    "DATABASE": "mateai.infrastructure.database.erp_database",
    "DB_MANAGER": "mateai.infrastructure.database.db_manager",
    "DOMAIN_SYNC": "mateai.infrastructure.directory.domain_sync",
    "COGNITIVE": "mateai.infrastructure.memory.cognitive_memory",
}
