# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 Dương Thanh Lịch — VN-MateAI. See LICENSE and NOTICE.
"""
tests/test_llm_provider_health.py
=================================
Provider LLM chung (mateai.infrastructure.llm.llm_provider.NineRouterLLMProvider) nhớ model hỏng.

Trước Phase 5 mỗi lượt thử lại từ đầu danh sách model: đo được 6 model hỏng/chậm
× tới 5 s trước khi gặp model chạy được (chữ đầu tiên sau ~22 s). Không gọi mạng.
"""
from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import mateai.infrastructure.llm.llm_provider as lp  # noqa: E402


class FakeClient:
    def __init__(self, bad):
        self.bad = set(bad)
        self.calls = []
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self._create))

    async def _create(self, **kw):
        self.calls.append(kw["model"])
        if kw["model"] in self.bad:
            raise RuntimeError(f"{kw['model']} is down")
        if kw.get("stream"):
            async def gen():
                delta = SimpleNamespace(content="ok", tool_calls=None, reasoning=None, reasoning_content=None)
                yield SimpleNamespace(choices=[SimpleNamespace(delta=delta, finish_reason="stop")])
            return gen()
        return SimpleNamespace(model=kw["model"])


@pytest.fixture(autouse=True)
def clean_health():
    lp._model_down_until.clear()
    yield
    lp._model_down_until.clear()


async def test_failed_model_tried_last_next_time():
    client = FakeClient(bad={"dead-a", "dead-b"})
    p = lp.NineRouterLLMProvider(client, "dead-a", ["dead-b", "good"])
    await p.complete(messages=[])
    assert client.calls == ["dead-a", "dead-b", "good"]
    client.calls.clear()
    await p.complete(messages=[])
    assert client.calls == ["good"], "lượt sau phải đi thẳng tới model đang chạy"


async def test_stream_marks_health_too():
    client = FakeClient(bad={"dead"})
    p = lp.NineRouterLLMProvider(client, "dead", ["good"])
    out = [c.content async for c in p.stream(messages=[])]
    assert out == ["ok"]
    client.calls.clear()
    _ = [c async for c in p.stream(messages=[])]
    assert client.calls == ["good"]
    assert "dead" in lp.model_health()


async def test_placeholder_model_never_called():
    client = FakeClient(bad=set())
    p = lp.NineRouterLLMProvider(client, "YOUR_MODEL_NAME_HERE", ["good"])
    await p.complete(messages=[])
    assert client.calls == ["good"]


async def test_all_cooling_still_tried():
    client = FakeClient(bad={"a", "b"})
    p = lp.NineRouterLLMProvider(client, "a", ["b"])
    with pytest.raises(RuntimeError):
        await p.complete(messages=[])
    client.bad.clear()
    resp = await p.complete(messages=[])  # cả hai đang bị xếp cuối nhưng vẫn được thử
    assert resp.model == "a"
    assert lp.model_health() == {"b": pytest.approx(lp.MODEL_COOLDOWN_S, abs=1)}


RETIRED = ("Gemini 3.5 Flash is no longer available. Please switch to Gemini 3.7 Flash "
           "in the latest version of Antigravity.")


class ChattyClient:
    """Model trả lời bằng chữ (HTTP 200); `replies[model]` là nội dung trả về."""

    def __init__(self, replies, chunk=7):
        self.replies = replies
        self.chunk = chunk
        self.calls = []
        self.closed = []
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self._create))

    async def _create(self, **kw):
        model = kw["model"]
        self.calls.append(model)
        text = self.replies[model]
        if kw.get("stream"):
            parts = [text[i:i + self.chunk] for i in range(0, len(text), self.chunk)]
            closed = self.closed

            class S:
                # Như AsyncStream của openai: chỉ duyệt MỘT lần trên cùng response.
                def __init__(self):
                    self._it = self._gen()

                def __aiter__(self):
                    return self._it

                async def _gen(self):
                    for i, part in enumerate(parts):
                        delta = SimpleNamespace(content=part, tool_calls=None, reasoning=None, reasoning_content=None)
                        yield SimpleNamespace(choices=[SimpleNamespace(
                            delta=delta, finish_reason="stop" if i == len(parts) - 1 else None)])

                async def close(self):
                    closed.append(model)

            return S()
        msg = SimpleNamespace(content=text, tool_calls=None)
        return SimpleNamespace(model=model, choices=[SimpleNamespace(message=msg, finish_reason="stop")])


async def test_complete_skips_retired_model_reply():
    client = ChattyClient({"old": RETIRED, "new": "Dạ, RAID 1 là nhân bản ổ đĩa."})
    p = lp.NineRouterLLMProvider(client, "old", ["new"])
    res = await p.complete(messages=[])
    assert res.model == "new" and client.calls == ["old", "new"]
    assert lp.model_health()["old"] > lp.MODEL_COOLDOWN_S, "model đã ngừng phải xếp cuối lâu hơn lỗi tạm thời"


async def test_stream_skips_retired_model_and_keeps_full_reply():
    good = "Dạ, RAID 1 là nhân bản dữ liệu sang hai ổ đĩa giống hệt nhau để chống mất dữ liệu ạ."
    client = ChattyClient({"old": RETIRED, "new": good})
    p = lp.NineRouterLLMProvider(client, "old", ["new"])
    chunks = [c async for c in p.stream(messages=[])]
    assert "".join(c.content for c in chunks) == good, "chunk đã đọc trước phải được phát lại đủ"
    assert {c.model for c in chunks} == {"new"}
    assert client.closed == ["old"]


async def test_normal_and_short_replies_are_not_flagged():
    for text in ("Dạ.", "Dạ, máy chủ đang chạy ổn định ạ.",
                 "The printer is no longer in the office, sếp ạ"):
        lp._model_down_until.clear()
        client = ChattyClient({"m": text})
        p = lp.NineRouterLLMProvider(client, "m", [])
        out = "".join([c.content async for c in p.stream(messages=[])])
        assert out == text and client.calls == ["m"]
    assert lp.looks_like_retired_model_reply(RETIRED)
    assert lp.looks_like_retired_model_reply("This model has been deprecated.")


async def test_quota_exhausted_model_parked_for_long():
    class QuotaClient(FakeClient):
        async def _create(self, **kw):
            self.calls.append(kw["model"])
            if kw["model"] == "poor":
                raise RuntimeError("Error code: 503 - RESOURCE_EXHAUSTED Individual quota reached. Resets in 101h")
            return SimpleNamespace(model=kw["model"])

    client = QuotaClient(bad=set())
    p = lp.NineRouterLLMProvider(client, "poor", ["rich"])
    await p.complete(messages=[])
    assert lp.model_health()["poor"] > lp.MODEL_COOLDOWN_S
