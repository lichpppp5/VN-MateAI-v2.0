# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 Dương Thanh Lịch — VN-MateAI. See LICENSE and NOTICE.
"""
tests/test_skill_router_eval.py
===============================
Đường thoại chỉ đưa model ~5 tool do bộ định tuyến skill chọn. Đo trên LLM thật
(scripts/eval_llm.py --narrow 5, 2026-10-06): câu "máy chủ dùng bao nhiêu CPU và RAM" và
"có ticket nào đang mở" KHÔNG có tool đúng trong danh sách -> model chọn sai 6/42 lần.

Kiểm tra tất định (không gọi LLM): với MỌI câu nhóm "tool" của bộ đánh giá, ít nhất một tool
đúng phải nằm trong top 5 của bộ định tuyến — cùng danh mục skill thật.
"""
from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
_spec = importlib.util.spec_from_file_location("vn_eval_llm", ROOT / "scripts" / "eval_llm.py")
eval_llm = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(eval_llm)

TOOL_CASES = [(code, prompt, wanted) for code, group, prompt, kind, wanted in eval_llm.SCENARIOS if group == "tool"]


@pytest.fixture(scope="module")
def router():
    from core.plugin_manager import plugin_manager
    from mateai.application.skills.skill_router import dynamic_skill_router
    plugin_manager.load_plugins()
    dynamic_skill_router.rebuild_index()
    return dynamic_skill_router


@pytest.mark.parametrize("code,prompt,wanted", TOOL_CASES, ids=[c[0] for c in TOOL_CASES])
def test_right_tool_is_offered(router, code, prompt, wanted):
    offered = [t["function"]["name"] for t in router.get_tools_for_query(prompt, max_tools=5)]
    present = set(wanted) & {t["function"]["name"] for _, t in router.rank_tools(prompt)}
    if not present and not set(wanted) & set(router._tool_cache):
        pytest.skip(f"skill {wanted} không có trong danh mục của máy này")
    assert set(wanted) & set(offered), f"{code}: cần một trong {wanted}, bộ định tuyến đưa {offered}"
