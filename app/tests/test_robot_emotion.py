# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 Dương Thanh Lịch — VN-MateAI. See LICENSE and NOTICE.
"""
tests/test_robot_emotion.py
===========================
Biểu cảm gương mặt robot theo nội dung câu đang đọc (`voice/emotion.py`) — trước
đây robot luôn hiện mặt "happy", kể cả lúc xin lỗi hay báo lỗi.
Tên biểu cảm phải khớp `Face::parseEmotion` trong esp32_firmware/src/face.cpp.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from mateai.application.voice.emotion import emotion_for_text


@pytest.mark.parametrize("text,emotion", [
    ("Máy chủ đang hoạt động ổn định, CPU khoảng 8 phần trăm.", "happy"),
    ("Xin lỗi anh, em chưa tra được giá vàng hôm nay.", "sad"),
    ("Rất tiếc, ổ đĩa đã hỏng hoàn toàn.", "cry"),
    ("Chúc mừng anh, hệ thống đã sao lưu xong!", "excited"),
    ("Wow, máy chủ chạy liên tục hơn 100 ngày rồi.", "wow"),
    ("Em cũng rất vui được làm việc cùng anh, cảm ơn anh nhé.", "love"),
    ("RAID 1 ghi cùng dữ liệu lên hai ổ đĩa.", "neutral"),
])
def test_emotion_from_sentence(text, emotion):
    assert emotion_for_text(text) == emotion


def test_every_emotion_is_known_to_firmware():
    src = (Path(__file__).resolve().parents[1] / "esp32_firmware" / "src" / "face.cpp").read_text(encoding="utf-8")
    for name in ("happy", "sad", "cry", "wow", "excited", "love"):
        assert f'n == "{name}"' in src, name
