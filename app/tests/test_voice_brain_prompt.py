# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 Dương Thanh Lịch — VN-MateAI. See LICENSE and NOTICE.
"""
tests/test_voice_brain_prompt.py
================================
Voice Brain (realtime P2): câu TRÒ CHUYỆN dùng prompt gọn, lệnh VẬN HÀNH giữ
prompt đầy đủ. Bench Phase 1: mọi lượt thoại gửi ~10.600 ký tự prompt, kể cả
câu chào. Không gọi mạng — provider được thay bằng bản giả ghi lại messages.
"""
from __future__ import annotations

import pytest

import mateai.application.agent.llm_engine as le
from mateai.application.agent.llm_engine import build_system_prompt, llm_engine


def test_conversation_prompt_drops_ops_blocks_keeps_identity():
    full = build_system_prompt(source_device="hud")
    voice = build_system_prompt(source_device="hud", conversation=True)
    assert len(voice) < len(full) / 2
    for ops_only in ("PHÂN QUYỀN THIẾT BỊ", "NATIVE FILE SYSTEM", "ERP DOANH NGHIỆP", "DUAL-LLM"):
        assert ops_only in full and ops_only not in voice
    for kept in ("[TÊN TRỢ LÝ AI:", "QUY TẮC HỎI LẠI", "XƯNG HÔ BẮT BUỘC"):
        assert kept in voice
    # Xưng hô vẫn là khối CUỐI (LLM ưu tiên chỉ dẫn gần cuối — Phase 66).
    assert voice.rindex("XƯNG HÔ BẮT BUỘC") > voice.rindex("NGỮ CẢNH")


class _Provider:
    def __init__(self):
        self.calls = []

    async def stream(self, messages, tools=None, **kw):
        self.calls.append((messages, tools))
        from types import SimpleNamespace
        yield SimpleNamespace(content="Dạ, em trả lời ngắn gọn.", tool_calls=None, reasoning=None)


@pytest.fixture
def provider(monkeypatch):
    p = _Provider()

    async def no_pool(self):
        return None

    monkeypatch.setattr(type(llm_engine), "_ensure_shared_client", no_pool)
    monkeypatch.setattr(llm_engine, "get_provider", lambda brain_role=None: p)
    monkeypatch.setattr(le, "_may_create_skills", lambda caller: False)
    return p


async def _run(query, intent, monkeypatch):
    monkeypatch.setattr(llm_engine, "classify_intent", staticmethod(lambda q: intent))
    turn = {}
    out = [s async for s in llm_engine.stream_voice_response(query, history=[], source_device="portal",
                                                              session_id="t-voice-brain", turn=turn)]
    return out, turn


async def test_conversation_turn_sends_small_prompt(provider, monkeypatch):
    out, turn = await _run("Giải thích ngắn gọn RAID 1 là gì trong hai câu.",
                           {"type": "conversation", "target_brain": "voice"}, monkeypatch)
    messages, tools = provider.calls[0]
    assert out and turn["brain"] == "voice" and not tools
    assert "PHÂN QUYỀN THIẾT BỊ" not in messages[0]["content"]
    assert turn["system_chars"] == len(messages[0]["content"]) and turn["tools_chars"] == 0
    assert turn["prompt_chars"] == turn["system_chars"] + turn["history_chars"] + len(messages[-1]["content"])


async def test_operation_turn_keeps_full_prompt(provider, monkeypatch):
    _, turn = await _run("Báo cáo thông tin hệ thống máy chủ",
                         {"type": "operation", "target_brain": "ops"}, monkeypatch)
    messages, tools = provider.calls[0]
    assert turn["brain"] == "ops" and "PHÂN QUYỀN THIẾT BỊ" in messages[0]["content"]
    assert turn["tools_chars"] == (len(le.json.dumps(tools, ensure_ascii=False)) if tools else 0)


@pytest.mark.parametrize("conversation", [True, False])
def test_both_prompts_tell_the_model_todays_date(conversation):
    """Prompt của vòng agent từng KHÔNG có ngày hiện tại (khối dựng ra nhưng không
    nối vào) — model đoán ngày ("hôm nay là 24/02/2025")."""
    import datetime
    prompt = build_system_prompt(source_device="portal", conversation=conversation)
    assert f"ngày {datetime.datetime.now():%d/%m/%Y}" in prompt
    assert "TUYỆT ĐỐI không đoán" in prompt or not conversation


def test_voice_prompts_ask_for_natural_short_speech_without_template_phrases():
    """Người dùng phản ánh (2026-10-04): nghe như đọc mẫu, câu nào cũng kèm "chi tiết
    đã được hiển thị trên màn hình" và hỏi lại xã giao."""
    conv = build_system_prompt(source_device="hud", conversation=True)
    ops_voice = build_system_prompt(source_device="hud", spoken=True)
    agent = build_system_prompt(source_device="hud")
    for p in (conv, ops_voice):
        assert "Nói như người thật" in p
    assert "TRẢ LỜI BẰNG GIỌNG NÓI" in ops_voice and "TRẢ LỜI BẰNG GIỌNG NÓI" not in agent
    for p in (conv, ops_voice, agent):
        assert "BẮT BUỘC kết thúc bằng câu hỏi" not in p
        assert "chi tiết đã hiển thị trên màn hình. -->" not in p   # ví dụ cũ dạy model nói câu mẫu
