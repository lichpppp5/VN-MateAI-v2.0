# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 Dương Thanh Lịch — VN-MateAI. See LICENSE and NOTICE.
"""
tests/test_project_root_resolution.py
=====================================
settings.PROJECT_ROOT = thư mục gốc repo (chứa main.py, web/, config.json) bất
kể config_loader nằm ở đâu; VNMATEAI_PROJECT_ROOT ghi đè được.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def test_project_root_is_repo_root():
    from mateai.config.loader import settings, CONFIG_PATH
    assert Path(settings.PROJECT_ROOT).resolve() == ROOT
    assert Path(CONFIG_PATH).resolve() == ROOT / "config.json"


def test_env_override(tmp_path):
    code = ("from mateai.config.loader import _resolve_project_root as r; print(r())")
    out = subprocess.run([sys.executable, "-c", code], cwd=str(ROOT), capture_output=True, text=True,
                         env={**__import__("os").environ, "VNMATEAI_PROJECT_ROOT": str(tmp_path)})
    assert Path(out.stdout.strip().splitlines()[-1]).resolve() == tmp_path.resolve(), out.stderr[-400:]
