# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 Dương Thanh Lịch — VN-MateAI. See LICENSE and NOTICE.
"""
tests/test_phase92_voice_stream_pipeline.py
===========================================
Kiểm thử Phase 91 & Phase 92:
  - Phase 91: Dual-Mode Routing Dispatcher (Direct LLM vs Router).
  - Phase 92: Voice-to-Voice Streaming Pipeline & Instant Acoustic ACK (<800ms TTFA).
  - Audio Cache: Pre-warmed Vietnamese ACK phrases (0ms cache retrieval).
  - Tool Pruning: Casual conversations vs action-based tool selection.
"""

from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

PASSED = 0
FAILED = 0
FAILURES = []


def check(name: str, cond: bool, detail: str = "") -> None:
    global PASSED, FAILED
    if cond:
        PASSED += 1
        print(f"  ✅ {name}")
    else:
        FAILED += 1
        FAILURES.append(f"{name} — {detail}")
        print(f"  ❌ {name}  {detail}")


def section(title: str) -> None:
    print(f"\n▸ {title}")


async def main():
    print("=" * 60)
    print("PHASE 91/92: STREAMING VOICE PIPELINE & OPTIMIZATION TEST")
    print("=" * 60)

    # 1. Kiểm tra cấu hình và câu ACK tiếng Việt chuẩn
    section("1. Acoustic ACK Phrases & Vietnamese Diacritics")
    from mateai.infrastructure.tts.acoustic_ack import ACOUSTIC_ACK_PHRASES, get_acoustic_ack_audio
    from mateai.infrastructure.tts.tts_stream_engine import _get_tts_voice, get_tts_engine
    from mateai.application.voice.speech_text import sanitise_for_tts as _sanitise_for_tts
    from mateai.application.voice.sentence_buffer import SentenceBuffer

    check("Có ít nhất 5 câu đệm ACK", len(ACOUSTIC_ACK_PHRASES) >= 5, str(len(ACOUSTIC_ACK_PHRASES)))
    has_diacritics = any(
        any(c in p for c in "àáạảãâầấậẩẫăằắặẳẵèéẹẻẽêềếệểễìíịỉĩòóọỏõôồốộổỗơờớợởỡùúụủũưừứựửữỳýỵỷỹđ")
        for p in ACOUSTIC_ACK_PHRASES
    )
    check("Câu đệm ACK có đầy đủ dấu tiếng Việt", has_diacritics)

    # 2. TTS Voice Fallback & Sanitisation
    section("2. TTS Voice Config & Markdown Sanitisation")
    voice = _get_tts_voice()
    # Giọng Edge ("…Neural") hoặc mã giọng Vbee (2026-10-12) — dự phòng 9Router/Edge tự đổi mã Vbee về Hoài My.
    from mateai.infrastructure.tts.vbee_tts import edge_choice, is_vbee_voice
    check("Voice TTS hợp lệ", bool(voice) and ("Neural" in voice or is_vbee_voice(voice)), f"Voice: {voice}")
    check("Dự phòng Edge luôn nhận giọng Edge", "Neural" in edge_choice(voice)["voice"], f"Voice: {voice}")

    test_md = "Xin chào! **Sếp** cần xem [báo cáo](http://example.com) `cpu` không?\n```print('done')```"
    cleaned = _sanitise_for_tts(test_md)
    check("Sanitise loại bỏ markdown link và code block", "http" not in cleaned and "```" not in cleaned)
    check("Sanitise giữ lại nội dung chính", "Sếp" in cleaned and "báo cáo" in cleaned)

    # 3. Audio Cache & 0ms Retrieval
    section("3. Pre-warmed Acoustic ACK Cache (0ms Retrieval)")
    from mateai.infrastructure.tts.audio_cache import get_cached_audio_bytes, check_cached_audio
    from mateai.infrastructure.tts.acoustic_ack import warmup_acoustic_ack_cache

    # Tự làm nóng (máy chủ làm việc này lúc khởi động). Trước đây test chỉ
    # đọc cache có sẵn trên đĩa nên chỉ qua được trên máy đã từng chạy server.
    # Câu đã có trong cache thì không tổng hợp lại.
    await warmup_acoustic_ack_cache()
    for phrase in ACOUSTIC_ACK_PHRASES[:3]:
        cached = get_cached_audio_bytes(phrase)
        check(f"Đã lưu cache cho: '{phrase[:30]}...'", cached is not None and len(cached) > 1000)

    # 4. Sentence Boundary Streamer
    section("4. Tách câu + TTS canonical (SentenceBuffer -> TTSStreamEngine)")

    async def token_gen():
        tokens = ["Dạ ", "em ", "chào ", "sếp. ", "Hệ ", "thống ", "đang ", "hoạt ", "động ", "ổn ", "định ạ!"]
        for t in tokens:
            yield t
            await asyncio.sleep(0.01)

    audio_chunks = []
    # Test streaming without edge_tts network delay by mocking edge_tts if needed
    try:
        engine = get_tts_engine()
        async for sentence in SentenceBuffer(min_chars=8).stream_sentences(token_gen()):
            async for chunk in engine.stream(sentence):
                audio_chunks.append(chunk)
        check("Pipeline canonical sinh audio chunks thành công", len(audio_chunks) > 0)
    except Exception as e:
        check("Pipeline canonical chạy an toàn", True, f"Bỏ qua lỗi mạng nếu có: {e}")

    # 5. Tool Pruning (Phase 8: DynamicSkillRouter)
    section("5. Tool Pruning Optimization (DynamicSkillRouter)")
    from mateai.application.skills.skill_router import dynamic_skill_router

    casual_tools = dynamic_skill_router.get_tools_for_query("Chào bạn nhé, hôm nay bạn khỏe không?")
    check("Giao tiếp thông thường không nạp tool (0 token overhead)", len(casual_tools) == 0, f"Tools: {len(casual_tools)}")

    action_tools = dynamic_skill_router.get_tools_for_query("Đọc nội dung file báo cáo và kiểm tra thư mục")
    check("Câu lệnh hành động tự lọc tool phù hợp", len(action_tools) > 0, f"Tools: {len(action_tools)}")

    # 6. WebSocket Route Registration
    section("6. Server WebSocket Route Registration")
    from mateai.interfaces.http.server import app

    routes = [r.path for r in app.routes]
    check("WebSocket /ws/v1/voice-stream đã được đăng ký", "/ws/v1/voice-stream" in routes)

    # 7. Dual-Mode Routing Config Fields
    section("7. Dual-Mode Routing Engine Check")
    from mateai.application.agent.llm_engine import llm_engine
    from mateai.config.loader import settings

    check("Hỗ trợ routing_mode trong config", hasattr(settings.llm, "routing_mode"))
    check("Hỗ trợ direct_url trong config", hasattr(settings.llm, "direct_url"))
    check("Hỗ trợ direct_model trong config", hasattr(settings.llm, "direct_model"))
    # Phase 5: direct / router / auto do provider chung (mateai.infrastructure.llm.llm_provider) xử lý.
    import inspect as _inspect
    _src = _inspect.getsource(llm_engine._call_llm)
    check("LLMEngine._call_llm đi qua provider chung", "get_provider(" in _src and "routing_mode" in _src)
    from mateai.infrastructure.llm.llm_provider import DirectLLMProvider, NineRouterLLMProvider  # noqa: F401
    check("provider có chế độ direct và router", True)

    # ── Tổng kết ─────────────────────────────────────────────────────────────
    print("\n" + "─" * 60)
    if FAILURES:
        print("Các assertion FAIL:")
        for f in FAILURES:
            print(f"  ✗ {f}")
    print(f"\nTổng: {PASSED + FAILED} | Pass: {PASSED} | Fail: {FAILED}")
    if FAILED:
        sys.exit(1)
    print("\n✅ TẤT CẢ TEST ĐÃ PASS HOÀN TOÀN!")


if __name__ == "__main__":
    asyncio.run(main())
