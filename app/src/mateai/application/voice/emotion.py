# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 Dương Thanh Lịch — VN-MateAI. See LICENSE and NOTICE.
"""
mateai/application/voice/emotion.py
===================================
Biểu cảm gương mặt robot theo NỘI DUNG câu đang đọc (vui, buồn, khóc, wow, phấn
khích, yêu). Theo từ khoá — tức thì, không tốn thêm một lần gọi LLM.

Trước đây robot luôn hiện mặt "happy" khi nói, kể cả lúc báo lỗi hay xin lỗi.
"""
from __future__ import annotations

import re
import unicodedata

# Thứ tự = ưu tiên (câu "rất tiếc, ... chúc mừng" -> buồn trước).
_RULES = (
    ("cry", ("rat tiec", "dau long", "chia buon", "thuong tiec", "khoc")),
    ("sad", ("tiec", "xin loi", "khong the", "that bai", "bi loi", "gap loi", "buon",
             "khong tim thay", "chua tra duoc", "chua lay duoc", "khong ket noi")),
    ("excited", ("chuc mung", "tuyet voi", "xuat sac", "hoan hao", "qua dinh", "sieu")),
    ("wow", ("wow", "bat ngo", "khong ngo", "that khong", "o hay", "ghe vay", "kinh ngac")),
    ("love", ("yeu", "thuong anh", "cam on anh", "rat vui duoc", "quy anh")),
    ("happy", ("vui", "on dinh", "thanh cong", "hoan thanh", "da xong", "chao anh", "tot", "san sang")),
)


def _fold(text: str) -> str:
    t = unicodedata.normalize("NFD", str(text or ""))
    t = "".join(ch for ch in t if unicodedata.category(ch) != "Mn")
    return " " + re.sub(r"[^a-z0-9]+", " ", t.replace("đ", "d").replace("Đ", "D").lower()) + " "


def emotion_for_text(text: str) -> str:
    """Tên biểu cảm cho câu (khớp `Face::parseEmotion` của firmware); "neutral" nếu không rõ."""
    folded = _fold(text)
    for name, keys in _RULES:
        if any(f" {k} " in folded for k in keys):
            return name
    if str(text or "").count("!") >= 2:
        return "excited"
    return "neutral"
