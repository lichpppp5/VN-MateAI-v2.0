# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 Dương Thanh Lịch — VN-MateAI. See LICENSE and NOTICE.
"""
tests/test_llm_cost.py
======================
Chi phí LLM từ token THẬT theo từng model × giá khai trong `llm.model_registry`.

  - model chưa khai giá: không đoán — token của nó ghi riêng (`llm_unpriced_tokens`);
  - sổ tác vụ cộng chi phí, API giám sát trả `currency_cost` (None khi chưa có giá nào).
"""
from __future__ import annotations

import pytest


@pytest.fixture
def priced(monkeypatch):
    from mateai.config.loader import settings
    monkeypatch.setattr(settings.llm, "model_registry", {
        "m-priced": {"status": "APPROVED", "price_in_per_1m": 2.0, "price_out_per_1m": 10.0},
        "m-free": {"status": "APPROVED"},
    }, raising=False)


def test_estimate_cost_only_for_priced_models(priced):
    from mateai.infrastructure.llm.llm_provider import estimate_cost, model_price
    assert model_price("m-priced") == (2.0, 10.0) and model_price("m-free") is None
    cost, unpriced = estimate_cost({"m-priced": {"prompt_tokens": 1000, "completion_tokens": 500},
                                    "m-free": {"prompt_tokens": 70, "completion_tokens": 30}})
    assert cost == pytest.approx((1000 * 2.0 + 500 * 10.0) / 1_000_000)
    assert unpriced == 100
    assert estimate_cost({"m-free": {"prompt_tokens": 5, "completion_tokens": 5}}) == (None, 10)


def test_ledger_records_cost(priced):
    from mateai.application.tasks import ledger
    from mateai.infrastructure.database.db_manager import db_manager
    tid = ledger.open_task("chi phí", source="test", agent_id="test")
    ledger.add_usage(tid, {"llm_calls": 2, "total_tokens": 1600},
                     by_model={"m-priced": {"prompt_tokens": 1000, "completion_tokens": 500},
                               "m-free": {"prompt_tokens": 70, "completion_tokens": 30}})
    task = db_manager.op_get_task(tid)
    assert task["llm_cost"] == pytest.approx(0.007) and task["llm_unpriced_tokens"] == 100
    stats = db_manager.op_stats("2000-01-01")
    assert stats["llm_cost"] >= 0.007 and stats["unpriced_tokens"] >= 100
