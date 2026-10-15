# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 Dương Thanh Lịch — VN-MateAI. See LICENSE and NOTICE.
"""
tests/test_llm_failover_budget.py
=================================
Chuỗi thử model dự phòng có ngân sách tổng (realtime P5).

Bench 2026-10-03: lệnh vận hành p95 40,7 s — mỗi model trong danh sách chờ đủ 5 s
(stream) / 8 s (complete) rồi mới thử model sau. Nay một lời gọi dừng khi hết ngân
sách và báo lỗi để tầng trên trả lời dự phòng. Không gọi mạng: client giả treo.
"""
from __future__ import annotations

import asyncio
import time
from types import SimpleNamespace

import pytest

import mateai.infrastructure.llm.llm_provider as lp


class HangingClient:
    def __init__(self):
        self.tried = []

        async def create(**kw):
            self.tried.append(kw["model"])
            await asyncio.sleep(10)

        self.chat = SimpleNamespace(completions=SimpleNamespace(create=create))


@pytest.fixture
def provider(monkeypatch):
    monkeypatch.setattr(lp, "STREAM_FAILOVER_BUDGET_S", 1.2)
    monkeypatch.setattr(lp, "COMPLETE_FAILOVER_BUDGET_S", 1.2)
    monkeypatch.setattr(lp, "_mark_model_failed", lambda *a, **k: None)
    client = HangingClient()
    return lp.NineRouterLLMProvider(client, "m1", [f"m{i}" for i in range(2, 9)]), client


async def test_stream_failover_stops_at_budget(provider):
    prov, client = provider
    t0 = time.monotonic()
    with pytest.raises(RuntimeError):
        async for _ in prov.stream([{"role": "user", "content": "x"}]):
            pass
    assert time.monotonic() - t0 < 2.5
    assert 1 <= len(client.tried) < 8           # không thử cả 8 model


async def test_complete_failover_stops_at_budget(provider):
    prov, client = provider
    t0 = time.monotonic()
    with pytest.raises(Exception):
        await prov.complete([{"role": "user", "content": "x"}], timeout=8.0)
    # ngân sách = max(COMPLETE_FAILOVER_BUDGET_S, timeout) = 8 s ở đây -> dừng sau ~8 s, không 8 model × 8 s
    assert time.monotonic() - t0 < 10
    assert len(client.tried) < 8


class SlowThenFastClient:
    """m1 treo (hết hạn mức), m2 trả lời sau 0,1 s."""

    def __init__(self, slow=("m1",)):
        self.tried = []
        self.slow = set(slow)

        async def create(**kw):
            self.tried.append(kw["model"])
            await asyncio.sleep(10 if kw["model"] in self.slow else 0.1)
            return SimpleNamespace(model=kw["model"], choices=[SimpleNamespace(message=SimpleNamespace(content="ok", tool_calls=None))])

        self.chat = SimpleNamespace(completions=SimpleNamespace(create=create))


async def test_complete_hedges_to_next_model_while_first_is_slow(monkeypatch):
    monkeypatch.setattr(lp, "HEDGE_AFTER_S", 0.3)
    failed = []
    monkeypatch.setattr(lp, "_mark_model_failed", lambda m, *a, **k: failed.append(m))
    monkeypatch.setattr(lp, "_mark_model_ok", lambda m: None)
    client = SlowThenFastClient()
    prov = lp.NineRouterLLMProvider(client, "m1", ["m2", "m3"])
    t0 = time.monotonic()
    resp = await prov.complete([{"role": "user", "content": "x"}])
    assert resp.model == "m2" and time.monotonic() - t0 < 1.0     # không chờ m1 hết 20 s
    assert client.tried == ["m1", "m2"] and failed == []          # m1 chỉ chậm, bị huỷ, không bị xếp cuối


async def test_long_task_with_explicit_timeout_is_not_sent_twice(monkeypatch):
    monkeypatch.setattr(lp, "HEDGE_AFTER_S", 0.1)
    monkeypatch.setattr(lp, "_mark_model_failed", lambda *a, **k: None)
    client = SlowThenFastClient(slow=())
    prov = lp.NineRouterLLMProvider(client, "m1", ["m2"])
    resp = await prov.complete([{"role": "user", "content": "x"}], timeout=5.0)
    assert resp.model == "m1" and client.tried == ["m1"]         # tác vụ dài, đắt: thử lần lượt, không gửi song song
