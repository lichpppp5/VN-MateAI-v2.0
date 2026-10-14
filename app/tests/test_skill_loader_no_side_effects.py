# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 Dương Thanh Lịch — VN-MateAI. See LICENSE and NOTICE.
"""
tests/test_skill_loader_no_side_effects.py
==========================================
Prompt cuối §106 / §118 / §61: nạp skill KHÔNG được chạy code tuỳ ý.

`plugin_manager.load_plugins()` import mọi `skills/*.py`. Một script đặt nhầm vào
`skills/` (thấy thật 2026-10-06: tệp ghi đè một file HTML ngoài Desktop mỗi lần máy chủ
khởi động / mỗi lần chạy test) hay một tệp do AI sinh có lệnh ở cấp module sẽ CHẠY
ngay khi nạp — trước mọi cổng chính sách. Nay tệp có câu lệnh cấp module ngoài
import / định nghĩa / gán bị từ chối, không import, và được báo tên.
"""
from __future__ import annotations

import importlib
from pathlib import Path

from core.plugin_manager import PluginManager, top_level_side_effects

GOOD = '''"""Skill hợp lệ."""
import logging
from core.plugin_manager import export_skill

logger = logging.getLogger(__name__)
LIMIT = 3

try:
    import psutil  # noqa: F401
except ImportError:
    psutil = None


@export_skill(name="t_good", description="x", parameters={"type": "object", "properties": {}})
def t_good() -> dict:
    return {"status": "success"}


if __name__ == "__main__":
    print(t_good())
'''

BAD = '''import os
path = os.path.join("C:/", "x.html")
with open(path, "w") as f:
    f.write("ghi đè")
print("DONE")
'''


def test_detects_module_level_side_effects(tmp_path):
    good, bad = tmp_path / "good.py", tmp_path / "bad.py"
    good.write_text(GOOD, encoding="utf-8")
    bad.write_text(BAD, encoding="utf-8")
    assert top_level_side_effects(good) == []
    found = top_level_side_effects(bad)
    assert [f.split(":")[0] for f in found] == ["3", "5"]                   # dòng `with` và `print`


def test_loader_never_imports_rejected_files(tmp_path, monkeypatch):
    (tmp_path / "good.py").write_text(GOOD, encoding="utf-8")
    (tmp_path / "bad.py").write_text(BAD, encoding="utf-8")
    pm = PluginManager()
    pm._skills_dir = tmp_path
    pm._registry_json_path = tmp_path / "registry.json"
    imported = []
    real = importlib.import_module

    def spy(name, *a, **k):
        if name.startswith("skills."):
            imported.append(name)
            raise ImportError("không import thật trong test")
        return real(name, *a, **k)

    monkeypatch.setattr(importlib, "import_module", spy)
    pm.load_plugins()
    assert "skills.good" in imported and "skills.bad" not in imported
    assert list(pm.rejected_modules) == ["bad.py"] and "print" in pm.rejected_modules["bad.py"][-1]


def test_every_skill_in_repo_passes_the_rule():
    import subprocess
    root = Path(__file__).resolve().parents[1] / "skills"
    offenders = {p.name: top_level_side_effects(p) for p in root.glob("*.py")
                 if p.name != "__init__.py" and top_level_side_effects(p)}
    # Tệp người dùng chưa commit (script, không phải skill) được phép bị từ chối;
    # skill thật của repo thì không tệp nào được vi phạm.
    git_tracked = set(subprocess.run(["git", "ls-files", "skills"], capture_output=True, text=True,
                                     cwd=root.parent).stdout.split())
    assert git_tracked
    assert not {n for n in offenders if f"skills/{n}" in git_tracked}, offenders
