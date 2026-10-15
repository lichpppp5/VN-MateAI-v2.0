# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 Dương Thanh Lịch — VN-MateAI. See LICENSE and NOTICE.
"""
Danh mục kỹ năng (thẩm định thương mại #3): mọi kỹ năng có nhóm + chủ sở hữu, tệp skills/catalog.json khớp registry;
hợp đồng kỹ năng: tham số khai cho LLM khớp chữ ký hàm thật (lệch → AI gọi là lỗi).
"""
from __future__ import annotations

import importlib.util
import inspect
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _catalog_mod():
    spec = importlib.util.spec_from_file_location("skill_catalog", ROOT / "scripts" / "skill_catalog.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_catalog_file_matches_registry():
    saved = json.loads((ROOT / "skills" / "catalog.json").read_text(encoding="utf-8"))
    fresh = _catalog_mod().build()
    assert set(saved) == set(fresh), "chạy: python scripts/skill_catalog.py --write"
    for name, c in fresh.items():
        assert saved[name]["tier"] == c["tier"] and saved[name]["commercial"] == c["commercial"], name
        assert c["owner"] != "Chưa gán", f"{name}: thêm module vào OWNERS"


def test_demo_tier_never_ships_in_commercial():
    for name, c in _catalog_mod().build().items():
        if c["tier"] in ("demo", "personal"):
            assert not c["commercial"], name


def test_every_skill_schema_matches_its_function():
    from core.plugin_manager import plugin_manager
    plugin_manager._ensure_loaded()
    problems = []
    for name, entry in plugin_manager._registry.items():
        func = entry.get("func")
        if func is None:
            continue
        sig = inspect.signature(func)
        if any(p.kind == p.VAR_KEYWORD for p in sig.parameters.values()):
            continue
        schema = (entry.get("meta") or {}).get("parameters") or {}
        declared = set((schema.get("properties") or {}))
        required = set(schema.get("required") or [])
        params = {k for k, p in sig.parameters.items() if p.kind not in (p.VAR_POSITIONAL,)}
        no_default = {k for k, p in sig.parameters.items() if p.default is p.empty and p.kind not in (p.VAR_POSITIONAL,)}
        if declared - params:
            problems.append(f"{name}: khai {sorted(declared - params)} nhưng hàm không nhận")
        if no_default - declared:
            problems.append(f"{name}: hàm bắt buộc {sorted(no_default - declared)} nhưng LLM không được khai")
        if required - params:
            problems.append(f"{name}: required {sorted(required - params)} không có trong hàm")
    assert not problems, "\n".join(problems)
