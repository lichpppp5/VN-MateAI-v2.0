# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 Dương Thanh Lịch — VN-MateAI. See LICENSE and NOTICE.
"""
tests/test_approval_reply_classifier.py
=======================================
`llm_engine.classify_approval_reply` — câu nào là "đồng ý" / "huỷ" tác vụ đang
chờ duyệt. Bản cũ so khớp bằng startswith/endswith: "hủy" kết thúc bằng "y"
nên bị hiểu là ĐỒNG Ý và tác vụ chạy (đã tái hiện trên máy chủ thật).
"""
from __future__ import annotations

import pytest

from mateai.application.agent.llm_engine import classify_approval_reply as cls


@pytest.mark.parametrize("text", [
    "đồng ý", "Đồng ý.", "ok", "OK em", "y", "yes", "duyệt", "duyệt đi", "xác nhận",
    "anh đồng ý", "cho chạy", "chạy đi em", "tiến hành", "dong y", "phê duyệt rồi", "tiếp tục",
])
def test_confirm(text):
    assert cls(text) == "confirm"


@pytest.mark.parametrize("text", [
    "hủy", "huỷ", "huy", "Hủy bỏ!", "không", "không đồng ý", "từ chối", "n", "no",
    "cancel", "thôi", "dừng lại", "bỏ qua", "đừng chạy, hủy đi", "không duyệt",
])
def test_reject(text):
    assert cls(text) == "reject"


@pytest.mark.parametrize("text", [
    "yêu cầu báo cáo doanh thu",            # mở đầu bằng "y" — không phải "y"
    "chưa duyệt",                           # phủ định nhưng không phải lệnh huỷ
    "đừng đồng ý vội",                      # phủ định → không bao giờ là đồng ý
    "running total là bao nhiêu",           # "run" chỉ là một phần của từ
    "bạn có thể giải thích cho tôi tại sao server lại chậm như vậy không",  # câu dài
    "thời tiết hôm nay",
    "",
])
def test_neither(text):
    assert cls(text) is None
