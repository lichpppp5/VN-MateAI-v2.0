# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 Dương Thanh Lịch — VN-MateAI. See LICENSE and NOTICE.
"""
tests/test_sentence_buffer_first_sentence.py
============================================
Realtime P4: câu ĐẦU của câu trả lời thoại ngắn để tiếng đầu tới sớm.

Thời gian TTS tăng theo độ dài câu (9Router, bench 2026-10-03: 7 từ 1,26 s; 12
từ 1,55 s; 28 từ 2,18 s). Câu đầu chỉ được cắt ở dấu phẩy (cắt giữa cụm từ thì
giọng đọc ngắt sai chỗ) và vẫn phải đủ min_words (không "đọc 2-3 chữ một").
"""
from __future__ import annotations

import pytest

from mateai.application.voice.sentence_buffer import SentenceBuffer

RAID = ("RAID 1 là kỹ thuật ghi cùng một dữ liệu lên hai ổ đĩa cùng lúc, nhờ vậy khi một ổ hỏng "
        "thì ổ còn lại vẫn giữ đủ dữ liệu. Đổi lại anh chỉ dùng được một nửa tổng dung lượng.")


def _buf():
    return SentenceBuffer(min_chars=1, min_words=8, max_words=30, first_max_words=12)


def _stream(buf, text):
    """Nạp từng từ như token LLM; trả (câu, số từ đã nạp lúc câu phát ra)."""
    out = []
    for i, w in enumerate(text.split(" "), 1):
        out += [(s, i) for s in buf.add_token(w + " ")]
    return out + [(s, None) for s in buf.flush()]


def test_first_sentence_is_released_at_first_comma_while_streaming():
    out = _stream(_buf(), RAID)
    first, at_word = out[0]
    assert first.endswith("cùng lúc,") and at_word == 16  # không chờ hết câu (từ 31)
    assert " ".join(s for s, _ in out) == RAID


def test_first_sentence_never_cut_mid_phrase_or_too_short():
    text = ("Em đã kiểm tra xong, máy chủ tên SRV-01 chạy Windows 10 bản 22H2 và đã hoạt động "
            "liên tục mười hai ngày. Anh có muốn kiểm tra thêm gì không ạ?")
    sentences = [s for s, _ in _stream(_buf(), text)]
    # Dấu phẩy duy nhất đứng sau 5 từ (< min_words) -> giữ cả câu, không cắt ở từ thứ 12.
    assert sentences[0].endswith("mười hai ngày.")
    assert all(len(s.split()) >= 8 for s in sentences)


#: 19 từ (< max_words), dấu phẩy sau 10 từ — chỉ câu ĐẦU mới bị tách ở đây.
STATUS = "Máy chủ đang chạy Windows 10 bản 22H2 ổn định, bộ nhớ còn trống khoảng sáu mươi phần trăm."


@pytest.mark.parametrize("whole", [True, False])
def test_only_first_sentence_is_shortened(whole):
    text = STATUS + " " + STATUS
    buf = _buf()
    sentences = (buf.add_token(text) + buf.flush()) if whole else [s for s, _ in _stream(buf, text)]
    assert sentences[0] == "Máy chủ đang chạy Windows 10 bản 22H2 ổn định,"
    assert sentences[-1] == STATUS  # lần thứ hai: nguyên câu
    assert " ".join(sentences) == text


def test_disabled_by_default():
    buf = SentenceBuffer(min_chars=1, min_words=8, max_words=30)
    assert buf.add_token(STATUS) + buf.flush() == [STATUS]
