# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 Dương Thanh Lịch — VN-MateAI. See LICENSE and NOTICE.
"""
tests/test_project_root_single_source.py
========================================
Mọi module tính đường dẫn dữ liệu (CSDL, cache âm thanh, RAG, admin build,
topology, thư mục file của skill...) lấy thư mục gốc từ MỘT nguồn:
settings.PROJECT_ROOT — không suy từ vị trí file mã nguồn. Suy từ __file__ thì
chuyển module (Phase 4) hay chạy bản đóng gói là đường dẫn lệch âm thầm.
"""
from __future__ import annotations

import importlib
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from mateai.config.loader import settings  # noqa: E402

CASES = [
    ("mateai.infrastructure.tts.audio_cache", "PROJECT_ROOT"),
    ("mateai.application.knowledge.graph_rag", "_PROJECT_ROOT"),
    ("mateai.application.knowledge.rag_engine", "_PROJECT_ROOT"),
    ("mateai.application.skills.builtin.file_system", "_PROJECT_ROOT"),
    ("mateai.application.skills.builtin.onboarding_workflow", "_PROJECT_ROOT"),
]


def test_modules_use_settings_project_root():
    root = Path(settings.PROJECT_ROOT).resolve()
    for mod, attr in CASES:
        assert Path(getattr(importlib.import_module(mod), attr)).resolve() == root, mod


def test_server_paths_derive_from_root():
    import mateai.interfaces.http.routers.pages as s
    root = Path(settings.PROJECT_ROOT).resolve()
    assert Path(s._ADMIN_OUT_DIR).resolve() == root / "admin" / "out"
    import mateai.interfaces.http.routers.system as system
    from mateai.application.operations import topology_layout
    assert Path(topology_layout.LAYOUT_PATH).resolve() == root / "storage" / "custom_topology.json"
    import mateai.interfaces.http.routers.skills as skills
    assert Path(skills._REGISTRY_PATH).resolve() == root / "skills" / "registry.json"


def test_no_module_derives_root_from_its_own_location():
    offenders = []
    for base in ("core", "src/mateai"):
        for p in Path(base).rglob("*.py"):
            if p.as_posix().endswith("mateai/config/loader.py"):
                continue  # nơi định nghĩa PROJECT_ROOT
            text = p.read_text(encoding="utf-8")
            # Chỉ bắt dạng ĐI LÊN thư mục gốc; file cùng thư mục (Path(__file__).parent / "x")
            # là hợp lệ (vd. script widget đi kèm voice_controller).
            if re.search(r"Path\(__file__\).*(\.parent\.parent|\.parents\[)", text):
                offenders.append(str(p))
    assert not offenders, offenders


def test_hr_db_path_has_one_owner():
    """hr_kpi.db: domain_sync tính đường dẫn; giám sát dùng đúng hằng đó (trước tự tính ở 3 nơi)."""
    from mateai.infrastructure.directory import domain_sync
    import mateai.application.operations.autonomous_sentinel as sentinel
    import mateai.application.operations.health_monitor as health
    assert sentinel._DB_PATH is domain_sync.DEFAULT_DB_PATH
    assert health._DB_PATH is domain_sync.DEFAULT_DB_PATH
