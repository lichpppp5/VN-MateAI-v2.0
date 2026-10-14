# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 Dương Thanh Lịch — VN-MateAI. See LICENSE and NOTICE.
"""
tests/test_secret_mask_paste.py
===============================
Dán khoá mới vào SAU ký hiệu che `••••••••` (không xoá ký hiệu trước) phải lưu
đúng khoá, không lưu cả ký hiệu.

Đã xảy ra thật: token Telegram lưu thành `••••••••<token>` → không đúng dạng →
gateway không gửi được gì.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from mateai.interfaces.http import secret_masking  # noqa: E402

MASK = secret_masking._SECRET_MASK
TOKEN = "123456789:AAHf0abcdefghijklmnopqrstuvwxyz012"


def test_mask_prefix_is_stripped_from_new_secret():
    out = secret_masking._restore_masked_secrets({"bot_token": MASK + TOKEN}, {"bot_token": "old-token"})
    assert out["bot_token"] == TOKEN


def test_mask_alone_keeps_existing_and_normal_values_untouched():
    assert secret_masking._restore_masked_secrets({"bot_token": MASK}, {"bot_token": "old"})["bot_token"] == "old"
    assert secret_masking._restore_masked_secrets({"bot_token": "  " + MASK + "  "}, {"bot_token": "old"})["bot_token"] == "old"
    assert secret_masking._restore_masked_secrets({"bot_token": TOKEN}, {"bot_token": "old"})["bot_token"] == TOKEN
    # trường không phải bí mật: không động vào
    assert secret_masking._restore_masked_secrets({"ai_name": "Ly • Ly"}, {})["ai_name"] == "Ly • Ly"


def test_nested_and_list_secrets():
    out = secret_masking._restore_masked_secrets({"llm": {"api_key": MASK + "sk-new-key-1234567890"}},
                                         {"llm": {"api_key": "sk-old"}})
    assert out["llm"]["api_key"] == "sk-new-key-1234567890"
