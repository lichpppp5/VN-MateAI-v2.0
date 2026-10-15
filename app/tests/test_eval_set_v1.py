# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 Dương Thanh Lịch — VN-MateAI. See LICENSE and NOTICE.
"""
tests/test_eval_set_v1.py
=========================
Bộ đánh giá 300 câu theo vai trò + cổng phát hành (thẩm định thương mại 14/10/2026 #11). Không gọi LLM: kiểm dữ liệu bộ câu
(tool kỳ vọng có thật, mã / câu không trùng, đủ nhóm và vai trò) và luật của cổng phát hành.
"""
from __future__ import annotations

import importlib.util
import json
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _mod(name):
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    sys.path.insert(0, str(ROOT / "scripts"))
    spec.loader.exec_module(mod)
    return mod


def test_set_has_300_unique_cases_by_role_and_group():
    cases = _mod("eval_cases_v1").cases()
    assert len(cases) == 300
    assert len({c[0] for c in cases}) == 300 and len({c[3] for c in cases}) == 300
    roles, groups = Counter(c[1] for c in cases), Counter(c[2] for c in cases)
    assert set(roles) == {"cntt", "lanh_dao", "nhan_su", "chung"} and min(roles.values()) >= 40
    assert groups["tool"] >= 200 and groups["chat"] >= 40 and groups["safety"] >= 30


def test_every_expected_tool_exists_in_the_catalog():
    reg = json.loads((ROOT / "skills" / "registry.json").read_text(encoding="utf-8"))
    for code, _role, group, _q, kind, tools in _mod("eval_cases_v1").cases():
        if kind == "expect_any":
            assert set(tools) & set(reg), f"{code}: không tool nào trong {tools} có trong registry"
        if kind == "forbid":
            assert tools, code


def test_sample_keeps_every_group():
    ev = _mod("eval_llm")
    sample = ev.load_cases("v1", sample=30)
    assert 25 <= len(sample) <= 35 and {c[2] for c in sample} == {"tool", "chat", "safety"}
    assert len(ev.load_cases("v0")) == 20


def test_release_gate_rules():
    ev = _mod("eval_llm")
    base = {"passed": 95, "total": 100, "errors": 0, "unsafe_proposals": 0}
    assert ev.gate_exit(base, 0.9) == 0
    assert ev.gate_exit({**base, "passed": 85}, 0.9) == 1
    assert ev.gate_exit({**base, "unsafe_proposals": 1}, 0.9) == 1          # một đề xuất tool cấm là đủ trượt
    assert ev.gate_exit({**base, "passed": 0, "errors": 100}, 0.9) == 1     # gateway chết ≠ "đạt"
    assert ev.gate_exit({**base, "passed": 10}, 0.0) == 0                   # không đặt cổng: chỉ báo cáo
