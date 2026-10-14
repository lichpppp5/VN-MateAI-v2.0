# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 Dương Thanh Lịch — VN-MateAI. See LICENSE and NOTICE.
"""
tests/test_ai_delegation_provider.py
====================================
Ủy quyền chuyên gia (mateai.application.skills.builtin.ai_delegation) dùng provider LLM chung từ Phase 5:
thử lần lượt specialist_models, nhớ model hỏng, timeout dài cho phân tích sâu.
Không gọi mạng.
"""
from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import mateai.infrastructure.llm.llm_provider as lp  # noqa: E402
import mateai.application.skills.builtin.ai_delegation as deleg  # noqa: E402


@pytest.fixture
def fake(monkeypatch):
    calls = []
    state = {"bad": set()}

    class FakeAsyncOpenAI:
        def __init__(self, **kw):
            self.chat = SimpleNamespace(completions=SimpleNamespace(create=self._create))

        async def _create(self, **kw):
            calls.append(kw)
            if kw["model"] in state["bad"]:
                raise RuntimeError("down")
            msg = SimpleNamespace(content=f"phân tích từ {kw['model']}")
            return SimpleNamespace(model=kw["model"], choices=[SimpleNamespace(message=msg)])

    # Client dựng ở MỘT nơi (llm_provider.make_llm_client) — thay ở đó.
    monkeypatch.setattr(lp.openai, "AsyncOpenAI", FakeAsyncOpenAI)
    monkeypatch.setattr(deleg, "_get_llm_config", lambda: {
        "base_url": "http://x/v1", "api_key": "k",
        "specialist_model": "spec-a", "specialist_models": ["spec-a", "spec-b"],
    })
    monkeypatch.setattr(deleg, "trigger_delegation_reflex", lambda: None)
    lp._model_down_until.clear()
    yield calls, state
    lp._model_down_until.clear()


async def test_falls_back_and_uses_long_timeout(fake, monkeypatch):
    calls, state = fake
    state["bad"] = {"spec-a"}
    seen_timeouts = []
    real_wait_for = lp.asyncio.wait_for

    async def spy_wait_for(aw, timeout):
        seen_timeouts.append(timeout)
        return await real_wait_for(aw, timeout)

    monkeypatch.setattr(lp.asyncio, "wait_for", spy_wait_for)
    res = await deleg.delegate_to_specialist_async("chẩn đoán lỗi")
    assert res["status"] == "success"
    assert res["specialist_model"] == "spec-b"
    assert [c["model"] for c in calls] == ["spec-a", "spec-b"]
    # Ngân sách thử model (realtime P5): lần đầu đủ timeout dài; các lần sau nhận phần
    # ngân sách còn lại (<= 180 s) — tổng không vượt ngân sách.
    assert seen_timeouts[0] == 180.0
    assert all(170.0 < t <= 180.0 for t in seen_timeouts)
    assert "extra_body" not in calls[-1]


async def test_all_models_fail_returns_error_not_exception(fake):
    calls, state = fake
    state["bad"] = {"spec-a", "spec-b"}
    res = await deleg.delegate_to_specialist_async("chẩn đoán lỗi")
    assert res["status"] == "error" and res["error"] == "ALL_SPECIALIST_MODELS_FAILED"


# ── cầu nối đồng bộ dùng chung (analytics_engine, meta_architect) ────────────
def test_sync_bridge_skips_retired_model_and_returns_text(monkeypatch):
    """Code trong thread worker dùng cùng provider: model đã ngừng bị bỏ qua."""
    import mateai.infrastructure.llm.llm_provider as lp
    from types import SimpleNamespace

    calls = []
    retired = "Gemini 3.5 Flash is no longer available. Please switch to Gemini 3.7 Flash."

    class Fake:
        def __init__(self, **kw):
            self.chat = SimpleNamespace(completions=SimpleNamespace(create=self._create))

        async def _create(self, **kw):
            calls.append(kw["model"])
            text = retired if kw["model"] == "old" else "SELECT 1"
            msg = SimpleNamespace(content=text, tool_calls=None)
            return SimpleNamespace(model=kw["model"], choices=[SimpleNamespace(message=msg)])

        async def close(self):
            pass

    monkeypatch.setattr(lp.openai, "AsyncOpenAI", Fake)
    lp._model_down_until.clear()
    text, model = lp.complete_text_blocking("http://x/v1", "k", ["old", "new"],
                                            [{"role": "user", "content": "q"}], max_tokens=10)
    assert (text, model) == ("SELECT 1", "new") and calls == ["old", "new"]
    lp._model_down_until.clear()


def test_analytics_and_meta_architect_use_the_shared_bridge(monkeypatch):
    import mateai.infrastructure.llm.llm_provider as lp
    import mateai.application.analytics.analytics_engine as ae
    from mateai.application.skills.meta_architect import meta_architect

    seen = []

    def fake_bridge(base_url, api_key, models, messages, **kw):
        seen.append((models, kw.get("max_tokens")))
        if "SQL" in messages[0]["content"]:
            return "SELECT name FROM employees LIMIT 3", models[0]
        return "```python\nfrom core.plugin_manager import export_skill\n```", models[0]

    monkeypatch.setattr(lp, "complete_text_blocking", fake_bridge)
    monkeypatch.setattr("mateai.application.skills.builtin.ai_delegation._get_llm_config", lambda: {
        "base_url": "http://x/v1", "api_key": "k", "specialist_model": "m1",
        "specialist_models": ["m1", "m2"]})
    sql, model, err = ae._ask_llm_for_sql("danh sách nhân viên")
    assert sql.startswith("SELECT") and model == "m1" and not err and seen[0][0] == ["m1", "m2"]

    code = meta_architect.synthesize_skill("làm gì đó")
    assert "export_skill" in code and seen[1][1] == 4096
