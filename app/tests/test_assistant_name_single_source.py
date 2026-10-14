# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 Dương Thanh Lịch — VN-MateAI. See LICENSE and NOTICE.
"""
tests/test_assistant_name_single_source.py
==========================================
Tên trợ lý có MỘT nguồn: `mateai.config.loader.get_assistant_name`.

Trước đây server và lời chào đọc `settings.AI_NAME` — luôn None vì AppSettings
bỏ khoá lạ — nên luôn nói "Ly Ly" dù người dùng đổi tên trong giao diện
(giao diện lưu `persona.ai_name`). Prompt LLM thì đọc persona, nên hai nơi
nói hai tên.
"""
from __future__ import annotations

import pytest

import mateai.config.loader as loader


@pytest.mark.parametrize("raw,expected", [
    ({"persona": {"ai_name": "Mai"}, "AI_NAME": "Ly Ly"}, "Mai"),
    ({"persona": {"ai_name": "  "}, "AI_NAME": "Lan"}, "Lan"),
    ({"ASSISTANT_NAME": "Hà"}, "Hà"),
    ({}, "Ly Ly"),
    ({"persona": "hỏng"}, "Ly Ly"),
])
def test_precedence_matches_ui(monkeypatch, raw, expected):
    monkeypatch.setattr(loader, "read_raw_config", lambda strict=False: raw)
    assert loader.get_assistant_name() == expected


def test_system_prompt_and_greeting_use_the_same_name(monkeypatch):
    monkeypatch.setattr(loader, "read_raw_config", lambda strict=False: {"persona": {"ai_name": "Mai"}})
    from mateai.application.agent.llm_engine import build_system_prompt
    assert "[TÊN TRỢ LÝ AI: Mai]" in build_system_prompt()
