# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 Dương Thanh Lịch — VN-MateAI. See LICENSE and NOTICE.
"""
esp32_firmware/tools/gen_oled_labels.py
=======================================
Dựng chữ trạng thái TIẾNG VIỆT CÓ DẤU cho màn hình OLED 128x64 của robot thành
bitmap 1-bit (font của Adafruit GFX chỉ có ASCII, không vẽ được dấu).

    python esp32_firmware/tools/gen_oled_labels.py

Sinh `esp32_firmware/src/oled_labels.h` (định dạng của Adafruit_GFX::drawBitmap:
từng hàng, bit cao trước, mỗi hàng làm tròn lên byte). Đổi chữ: sửa LABELS rồi
chạy lại, build và nạp firmware.
"""
from __future__ import annotations

import os
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

LABELS = {
    "LISTENING": "Đang lắng nghe",
    "THINKING": "Đang suy nghĩ",
    "SPEAKING": "Đang nói",
    "EDGE": "Cẩn thận mép bàn!",
}
FONT_CANDIDATES = [r"C:\Windows\Fonts\segoeui.ttf", r"C:\Windows\Fonts\arial.ttf",
                   "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"]
FONT_PX = 11          # cao ~12 px kể cả dấu chồng ("ắ", "ợ") — vừa dải chữ 12 hàng
OUT = Path(__file__).resolve().parents[1] / "src" / "oled_labels.h"


def _font() -> ImageFont.FreeTypeFont:
    for path in FONT_CANDIDATES:
        if os.path.exists(path):
            return ImageFont.truetype(path, FONT_PX)
    raise SystemExit("Không tìm thấy font có dấu tiếng Việt (Segoe UI / Arial / DejaVu).")


def render(text: str, font: ImageFont.FreeTypeFont) -> tuple[int, int, bytes]:
    canvas = Image.new("1", (256, 32), 0)
    draw = ImageDraw.Draw(canvas)
    draw.fontmode = "1"                      # không khử răng cưa: OLED chỉ có trắng/đen
    draw.text((2, 6), text, font=font, fill=1)
    box = canvas.getbbox()
    img = canvas.crop(box)
    w, h = img.size
    row_bytes = (w + 7) // 8
    data = bytearray()
    px = img.load()
    for y in range(h):
        for bx in range(row_bytes):
            byte = 0
            for bit in range(8):
                x = bx * 8 + bit
                if x < w and px[x, y]:
                    byte |= 0x80 >> bit
            data.append(byte)
    return w, h, bytes(data)


def main() -> None:
    font = _font()
    lines = [
        "// Sinh tự động bởi esp32_firmware/tools/gen_oled_labels.py — KHÔNG sửa tay.",
        "// Chữ trạng thái tiếng Việt có dấu cho OLED (định dạng Adafruit_GFX::drawBitmap).",
        "#pragma once",
        "#include <Arduino.h>",
        "",
        "struct OledLabel { const uint8_t* bits; uint8_t w; uint8_t h; };",
        "",
    ]
    for name, text in LABELS.items():
        w, h, data = render(text, font)
        body = ", ".join(f"0x{b:02X}" for b in data)
        lines.append(f"// \"{text}\" — {w}x{h}")
        lines.append(f"static const uint8_t LABEL_{name}_BITS[] PROGMEM = {{{body}}};")
        lines.append(f"static const OledLabel LABEL_{name} = {{LABEL_{name}_BITS, {w}, {h}}};")
        lines.append("")
        print(f"{name:10} {w:3}x{h:<2} {text}")
    OUT.write_text("\n".join(lines), encoding="utf-8")
    print("->", OUT)


if __name__ == "__main__":
    main()
