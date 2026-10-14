# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 Dương Thanh Lịch — VN-MateAI. See LICENSE and NOTICE.
"""
tests/test_phase2_llm_streaming.py
==================================
Kiểm thử Giai đoạn 2: LLM Streaming & Provider Abstraction (Phase 2):
1. Khả năng phát sinh LLMStreamChunk qua giao thức stream chuẩn.
2. Phương thức stream_tokens trích xuất chính xác token văn bản thô.
3. Event Bus LLMStreamEventBus phát sóng đồng thời tới nhiều subscriber.
4. Provider Abstraction (DirectLLMProvider, NineRouterLLMProvider, TriBrainLLMProvider).
5. llm_engine.get_provider() trả provider chuẩn hoá (lõi stream qua provider.stream).
"""

import asyncio
import sys
import time
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

# Add project root to sys.path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from mateai.infrastructure.llm.llm_provider import (
    BaseLLMProvider,
    LLMStreamChunk,
    DirectLLMProvider,
    NineRouterLLMProvider,
    TriBrainLLMProvider,
    LLMStreamEventBus,
)
from mateai.application.agent.llm_engine import llm_engine


def test_stream_chunk_model():
    print("\n▸ 1. Kiểm thử Data Model LLMStreamChunk")
    chunk = LLMStreamChunk(
        content="Xin",
        model="ag/gemini-3.6-flash-high",
        role="assistant",
        ttft_ms=180,
    )
    assert chunk.content == "Xin"
    assert chunk.model == "ag/gemini-3.6-flash-high"
    assert chunk.ttft_ms == 180
    print("  ✅ LLMStreamChunk tạo lập và truy xuất trường hợp lệ.")


async def test_llm_stream_event_bus():
    print("\n▸ 2. Kiểm thử Event Bus phát sóng Token đa kênh (LLMStreamEventBus)")
    bus = LLMStreamEventBus()

    ui_tokens = []
    buffer_tokens = []

    # Nhánh 1: UI Stream (WebSocket)
    def on_ui_token(tok: str):
        ui_tokens.append(tok)

    # Nhánh 2: Sentence Buffer (TTS)
    async def on_buffer_token(tok: str):
        buffer_tokens.append(tok)

    bus.subscribe(on_ui_token)
    bus.subscribe(on_buffer_token)

    test_sentence = ["Xin ", "chào ", "anh ", "Lịch!"]
    for t in test_sentence:
        await bus.publish_token(t)

    assert "".join(ui_tokens) == "Xin chào anh Lịch!"
    assert "".join(buffer_tokens) == "Xin chào anh Lịch!"
    print("  ✅ Event Bus phát token đồng thời tới cả kênh UI và kênh Sentence Buffer.")


async def test_provider_mock_streaming():
    print("\n▸ 3. Kiểm thử Provider Abstraction & async generator stream()")

    class MockProvider(BaseLLMProvider):
        async def stream(self, messages, tools=None, **kwargs):
            tokens = ["Dạ, ", "hệ ", "thống ", "sẵn ", "sàng."]
            for t in tokens:
                yield LLMStreamChunk(content=t, model="mock-model")

        async def complete(self, messages, tools=None, **kwargs):
            return "Complete result"

    mock_prov = MockProvider()

    # Kiểm thử stream() trả về LLMStreamChunk
    collected_chunks = []
    async for chunk in mock_prov.stream([{"role": "user", "content": "hi"}]):
        assert isinstance(chunk, LLMStreamChunk)
        collected_chunks.append(chunk.content)
    assert "".join(collected_chunks) == "Dạ, hệ thống sẵn sàng."
    print("  ✅ mock_prov.stream() trả về các chunk chuẩn LLMStreamChunk.")

    # Kiểm thử stream_tokens() trả về chuỗi str
    collected_tokens = []
    async for token in mock_prov.stream_tokens([{"role": "user", "content": "hi"}]):
        assert isinstance(token, str)
        collected_tokens.append(token)
    assert "".join(collected_tokens) == "Dạ, hệ thống sẵn sàng."
    print("  ✅ mock_prov.stream_tokens() stream trực tiếp chuỗi token thô.")


async def test_tribrain_provider_routing():
    print("\n▸ 4. Kiểm thử TriBrainLLMProvider điều phối Direct và Router")

    mock_direct = MagicMock()
    mock_router = MagicMock()

    async def _mock_stream_router(*args, **kwargs):
        yield LLMStreamChunk(content="from_router", model="router-m")

    async def _mock_stream_direct(*args, **kwargs):
        yield LLMStreamChunk(content="from_direct", model="direct-m")

    mock_router.stream = _mock_stream_router
    mock_direct.stream = _mock_stream_direct

    tribrain = TriBrainLLMProvider(direct_provider=mock_direct, router_provider=mock_router)

    # 1. Routing mode router
    chunks_router = []
    async for c in tribrain.stream([{"role": "user", "content": "test"}], routing_mode="router"):
        chunks_router.append(c.content)
    assert chunks_router == ["from_router"]
    print("  ✅ routing_mode='router' gọi chính xác router provider.")

    # 2. Routing mode direct
    chunks_direct = []
    async for c in tribrain.stream([{"role": "user", "content": "test"}], routing_mode="direct"):
        chunks_direct.append(c.content)
    assert chunks_direct == ["from_direct"]
    print("  ✅ routing_mode='direct' gọi chính xác direct provider.")


def test_llm_engine_provider_methods():
    print("\n▸ 5. Kiểm thử tích hợp Provider trên LLMEngine")
    provider = llm_engine.get_provider("voice")
    assert isinstance(provider, BaseLLMProvider), "get_provider() phải trả về BaseLLMProvider"
    print("  ✅ llm_engine.get_provider('voice') trả về Provider chuẩn hóa.")
    # Realtime P6: llm_engine.stream / stream_tokens đã gỡ (chỉ test gọi) — lõi
    # dùng thẳng provider.stream (đã kiểm ở trên).


def main():
    print("=" * 60)
    print("PHASE 2: LLM STREAMING & PROVIDER ABSTRACTION VERIFICATION")
    print("=" * 60)

    test_stream_chunk_model()
    asyncio.run(test_llm_stream_event_bus())
    asyncio.run(test_provider_mock_streaming())
    asyncio.run(test_tribrain_provider_routing())
    test_llm_engine_provider_methods()

    print("\n" + "─" * 60)
    print("✅ TẤT CẢ TEST PHASE 2 (LLM STREAMING & PROVIDER) ĐÃ PASS HOÀN TOÀN!")


if __name__ == "__main__":
    main()
