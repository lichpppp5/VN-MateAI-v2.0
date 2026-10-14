# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 Dương Thanh Lịch — VN-MateAI. See LICENSE and NOTICE.
"""
tests/test_tts_text_sanitiser.py
================================
Một hàm làm sạch văn bản cho TTS dùng chung cho mọi kênh voice
(`mateai.application.voice.speech_text.sanitise_for_tts`) + một hàm rút gọn lời nói
(`shorten_for_speech`).

Trước Phase 2 có ba hàm khác nhau (portal / HUD / ESP32 + mic), nên cùng một câu
trả lời được đọc khác nhau tuỳ kênh, và mỗi hàm có lỗi riêng: đọc to URL / bảng
/ khối code, "Wi-Fi" thành "Wi, Fi", "get_current_time" thành "getcurrenttime".
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from mateai.application.voice.speech_text import sanitise_for_tts, shorten_for_speech  # noqa: E402
from mateai.infrastructure.tts.tts_stream_engine import apply_pronunciation  # noqa: E402


@pytest.mark.parametrize("raw, expected", [
    # Markdown đậm bỏ; số thập phân giữ nguyên; chữ viết tắt GIỮ (vẫn để hiển thị)
    ("**CPU** đang ở mức 32,5%, RAM còn 4.2 GB.", "CPU đang ở mức 32,5%, RAM còn 4.2 GB."),
    # Gạch nối trong từ / số giữ nguyên; gạch có khoảng trắng hai bên -> dấu phẩy
    ("Máy 192.168.1.27 chạy Wi-Fi, lỗi COVID-19 - cần kiểm tra.",
     "Máy 192.168.1.27 chạy Wi-Fi, lỗi COVID-19, cần kiểm tra."),
    # Bullet + khối code
    ("Kết quả:\n- Dòng 1\n- Dòng 2\n```bash\nls -la\n```", "Kết quả: Dòng 1 Dòng 2"),
    # Khối JSON bỏ cả khối, emoji bỏ
    ('Tool trả về {"status": "ok", "count": 3} rồi ạ ✅', "Tool trả về rồi ạ"),
    # Link Markdown giữ nhãn, URL trần bỏ
    ("Xem tại [tài liệu](https://example.com/doc) hoặc https://x.vn", "Xem tại tài liệu hoặc"),
    # Chú thích giọng nói ẩn và bảng không bị đọc
    ("<!--VOICE:Đây là bản đọc--> Bảng:\n| a | b |\n|---|---|", "Bảng:."),
    # snake_case đọc thành từng từ
    ("Dạ, API của VN-MateAI dùng get_current_time() để lấy giờ.",
     "Dạ, API của VN-MateAI dùng get current time để lấy giờ."),
    # Bullet tròn
    ("• Ổ C còn 120 GB", "Ổ C còn 120 GB"),
    # In nghiêng bằng gạch dưới vẫn được bỏ dấu
    ("Đây là _quan trọng_ nhé.", "Đây là quan trọng nhé."),
])
def test_sanitise_for_tts(raw, expected):
    assert sanitise_for_tts(raw) == expected


def test_sanitise_empty():
    assert sanitise_for_tts("") == ""
    assert sanitise_for_tts("   ") == ""


def test_shorten_keeps_short_text():
    t = "Dạ, em đã kiểm tra xong."
    assert shorten_for_speech(t) == t


def test_shorten_long_text_keeps_whole_sentences():
    long = " ".join(f"Đây là câu thông tin số {i} trong báo cáo dài." for i in range(1, 15))
    out = shorten_for_speech(long)
    assert len(out) < len(long)
    assert out.startswith("Đây là câu thông tin số 1 trong báo cáo dài.")
    # Không gắn câu mẫu "chi tiết đã hiển thị trên màn hình" (người dùng phản ánh lặp lại).
    assert "màn hình" not in out and out.endswith(".")


def test_pronunciation_only_at_tts_boundary():
    """Gợi ý phát âm áp ở engine TTS, chữ hiển thị không bị đổi thành 'C P U'."""
    assert apply_pronunciation("API của VN-MateAI, CPU và RAM, máy PC-01") == (
        "A P I của VN Mate AI, C P U và Ram, máy PC 01"
    )
    assert "C P U" not in sanitise_for_tts("CPU là 32%.")


_STREAMED = r"""Đây là đoạn mã Python:
```python
import shutil
print(f"Free: {free // (2**30)} GB")
```
Tệp nằm ở D:\VNMateaiv1\config.json và log ở /var/log/syslog. Kết quả `{"a": {"b": 1}}` => ổn định 100%. CPU 32.5%, IP 192.168.1.10."""


@pytest.mark.parametrize("token_size", [1, 3, 7, 50])
def test_streamed_code_block_is_never_spoken(token_size):
    """Khối code bị tách qua nhiều câu khi stream — trước đây TTS đọc `import shutil`, `print(...)`."""
    from mateai.application.voice.sentence_buffer import SentenceBuffer
    buf = SentenceBuffer(min_chars=1, min_words=8, max_words=30)
    spoken = []
    for i in range(0, len(_STREAMED), token_size):
        spoken += buf.add_token(_STREAMED[i:i + token_size])
    spoken += buf.flush()
    said = " ".join(spoken)
    for leak in ("import", "print", "shutil", "```", "`", "=>", "VNMateaiv1", "/var/log", "{", "}"):
        assert leak not in said, (leak, said)
    assert "màn hình" not in said            # khối code bị bỏ hẳn, không đọc câu thay
    assert "config.json" in said and "syslog" in said
    assert "32.5%" in said and "192.168.1.10" in said


@pytest.mark.parametrize("raw, expected", [
    ("Chạy lệnh:\npip install -r requirements.txt\nrồi khởi động lại.", "Chạy lệnh: rồi khởi động lại."),
    ("Thêm dòng:\nx = load(path)\nvào cuối tệp.", "Thêm dòng: vào cuối tệp."),
    ("Kết quả {\"a\": {\"b\": 1}} đã lưu.", "Kết quả đã lưu."),
    (r"Mở tệp C:\Users\Admin\Desktop\bao_cao.xlsx giúp em.", "Mở tệp bao cao.xlsx giúp em."),
])
def test_unfenced_code_paths_and_nested_json(raw, expected):
    assert sanitise_for_tts(raw) == expected


@pytest.mark.parametrize("token_size", [1, 2, 5, 40])
def test_streamed_hidden_voice_comment_is_not_spoken(token_size):
    """`<!--VOICE: …-->` bị tách qua hai câu khi stream — trước đây đọc ra "!--VOICE: …"."""
    from mateai.application.voice.sentence_buffer import SentenceBuffer
    text = ("Em đã viết xong đoạn mã cho anh. Anh kiểm tra lại tên cột nhé. "
            "<!--VOICE: Em đã viết xong đoạn mã. Anh kiểm tra tên cột rồi báo em nhé.-->")
    buf = SentenceBuffer(min_chars=1, min_words=8, max_words=30)
    spoken = []
    for i in range(0, len(text), token_size):
        spoken += buf.add_token(text[i:i + token_size])
    spoken += buf.flush()
    said = " ".join(spoken)
    assert "VOICE" not in said and "--" not in said and "báo em" not in said, said
    assert "Anh kiểm tra lại tên cột nhé." in said


@pytest.mark.parametrize("token_size", [1, 4, 30])
def test_streamed_inline_code_is_not_spoken(token_size):
    from mateai.application.voice.sentence_buffer import SentenceBuffer
    text = 'Dùng `float(x.replace(",", ""))` để đổi số. Sau đó chạy `python main.py` là xong.'
    buf = SentenceBuffer(min_chars=1, min_words=8, max_words=30)
    spoken = []
    for i in range(0, len(text), token_size):
        spoken += buf.add_token(text[i:i + token_size])
    spoken += buf.flush()
    said = " ".join(spoken)
    assert "replace" not in said and "main" not in said and "float" not in said, said
    assert "để đổi số" in said and "là xong" in said
