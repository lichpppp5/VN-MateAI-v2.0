# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 Dương Thanh Lịch — VN-MateAI. See LICENSE and NOTICE.
"""
core/audio/sentence_streamer.py
================================
Chuẩn hoá văn bản cho lời nói — DUY NHẤT cho mọi kênh voice (Phase 2).

  - sanitise_for_tts(text):   bỏ phần không đọc được (Markdown, code, JSON, URL,
                              bảng, emoji…). Kết quả vẫn dùng để hiển thị.
  - shorten_for_speech(text): rút lời nói dài về 1–2 câu đầu khi đọc nguyên đoạn.

Trước Phase 2 có ba hàm làm sạch khác nhau (`llm_engine._sanitise_for_tts`,
`audio_processor.clean_text_for_tts` và hàm này), nên cùng câu trả lời được đọc
khác nhau tuỳ kênh. Gợi ý phát âm (A P I, C P U…) nằm ở
`tts_stream_engine.apply_pronunciation`, không ở đây.

Tách câu từ luồng token: `core/audio/sentence_buffer.py` (lớp `SentenceStreamer`
cũ chỉ bọc lại SentenceBuffer và đã được gỡ).
"""

from __future__ import annotations

import re

#: Emoji / ký hiệu hình — TTS đọc vấp hoặc đọc tên ký hiệu.
_EMOJI_RE = re.compile('[\U0001F300-\U0001FAFF☀-➿️‍]')

#: Khối code khi ĐỌC: bỏ hẳn, không nói gì thay. Trước đây đọc câu "Phần mã em đã
#: hiển thị trên màn hình." — người dùng thấy lặp lại như câu mẫu (2026-10-04),
#: và robot không có màn hình.
CODE_PLACEHOLDER = ""

#: Dòng trông như code / lệnh (khi model viết code KHÔNG bọc trong ```).
_CODE_LINE_RE = re.compile(
    r"""^\s*(?:
        (?:import|from\s+\S+\s+import|def|class|return|async\s+def|await|elif|else:|try:|except|finally:
           |for\s+\w+\s+in|while|if\s+.+:|with\s+.+:|print\(|console\.|const|let|var|function|public|private
           |\#include|SELECT|INSERT|UPDATE\s+\w+\s+SET|DELETE\s+FROM|CREATE\s+TABLE)\b
      | (?:\$|>>>|PS\s?[A-Z]:\\|C:\\>)\s
      | (?:pip|npm|npx|yarn|git|cd|sudo|apt|docker|curl|python|node)\s+[-\w./]
      | [\w.\[\]'"]+\s*(?:\+=|-=|==|=)\s*\S.*[()\[\]{}].*
      | .*[;{]\s*$
      | [})\]]+[;,]?\s*$
    )""",
    re.VERBOSE,
)


def _innermost_braces_removed(text: str) -> str:
    """Bỏ JSON / dict kể cả lồng nhau (bỏ cặp ngoặc trong cùng tới khi hết)."""
    prev = None
    while prev != text:
        prev = text
        text = re.sub(r'\{[^{}]*\}', '', text)
    return text


class CodeFenceStripper:
    """
    Lọc khỏi LUỒNG token, trước khi tách câu để đọc:
      - khối ```code``` → thay bằng một câu ngắn `CODE_PLACEHOLDER` (một lần mỗi lượt);
      - chú thích ẩn `<!-- … -->` (vd `<!--VOICE: …-->`) → bỏ hẳn: đường stream đã
        đọc chính câu trả lời, phần tóm tắt giọng nói chỉ lặp lại.

    Khi stream, các khối này bị tách qua nhiều câu; mỗi câu chỉ chứa một nửa nên
    `sanitise_for_tts` (làm theo từng câu) không nhận ra — TTS đọc cả
    `import …`, `print(...)` và "!--VOICE: …". Lớp này giữ trạng thái "đang ở
    trong khối" qua các token, kể cả khi dấu mở/đóng bị cắt giữa hai token.
    """

    #: (mở, đóng, câu đọc thay hoặc ""). Code nội dòng `x.replace(",", "")` cũng
    #: bị cắt ngay dấu chấm khi tách câu nên phải lọc trên luồng như khối lớn.
    _BLOCKS = (("```", "```", CODE_PLACEHOLDER), ("<!--", "-->", ""), ("`", "`", ""))

    def __init__(self) -> None:
        self._buf = ""
        self._close: str = ""  # dấu đóng đang chờ ("" = không ở trong khối)
        self._announced = False

    def feed(self, token: str) -> str:
        self._buf += token or ""
        out = []
        while True:
            if self._close:
                idx = self._buf.find(self._close)
                if self._close == "`" and "\n" in self._buf[: idx if idx != -1 else None]:
                    # Backtick lẻ không đóng trong dòng: không phải code nội dòng.
                    nl = self._buf.index("\n")
                    out.append(self._buf[:nl])
                    self._buf = self._buf[nl:]
                    self._close = ""
                    continue
                if idx == -1:
                    self._buf = self._buf[-(len(self._close) - 1):]  # có thể là đầu dấu đóng
                    return "".join(out)
                self._buf = self._buf[idx + len(self._close):]
                self._close = ""
                continue
            hits = [(self._buf.find(o), o, c, say) for o, c, say in self._BLOCKS if o in self._buf]
            if not hits:
                keep = len(self._buf)
                # giữ lại phần đuôi có thể là đầu của một dấu mở ("``", "<!-")
                for opener, _c, _s in self._BLOCKS:
                    for n in range(len(opener) - 1, 0, -1):
                        if self._buf.endswith(opener[:n]):
                            keep = min(keep, len(self._buf) - n)
                            break
                out.append(self._buf[:keep])
                self._buf = self._buf[keep:]
                return "".join(out)
            # Vị trí sớm nhất; cùng vị trí thì dấu dài hơn (``` trước `).
            idx, opener, closer, say = min(hits, key=lambda h: (h[0], -len(h[1])))
            if opener == "`" and len(self._buf) - idx < 3 and set(self._buf[idx:]) == {"`"}:
                # "`" / "``" ở cuối bộ đệm có thể là đầu của ``` — chờ token sau.
                out.append(self._buf[:idx])
                self._buf = self._buf[idx:]
                return "".join(out)
            out.append(self._buf[:idx])
            self._buf = self._buf[idx + len(opener):]
            self._close = closer
            if say and not self._announced:
                out.append(f" {say} ")
                self._announced = True

    def flush(self) -> str:
        rest = "" if self._close else self._buf.replace("`", "")
        self._buf, self._close = "", ""
        return rest


def sanitise_for_tts(text: str) -> str:
    """
    Chuẩn hoá văn bản trước khi đưa vào TTS — hàm DUY NHẤT cho mọi kênh voice.

    Loại bỏ: Markdown, khối code, khối JSON, bullet, URL, bảng, emoji, ký tự đặc
    biệt. Giữ nguyên số thập phân, IP, và gạch nối trong từ (Wi-Fi, COVID-19).
    Kết quả vẫn dùng để HIỂN THỊ (HUD, ESP32), nên gợi ý phát âm không đặt ở
    đây mà ở `tts_stream_engine.apply_pronunciation`.
    """
    if not text:
        return ""

    # Bỏ comment giọng nói ẩn <!--VOICE:...--> (và mảnh còn sót khi bị cắt giữa câu)
    text = re.sub(r'<!--.*?-->', '', text, flags=re.DOTALL)
    text = re.sub(r'<!--[\s\S]*$', '', text)

    # Thay code block bằng câu nói tắt; khối mở mà không đóng (câu cuối của
    # stream) thì bỏ tới hết.
    text = re.sub(r'```[\s\S]*?```', f' {CODE_PLACEHOLDER} ', text)
    text = re.sub(r'```[\s\S]*$', '', text)
    text = re.sub(r'`[^`\n]+`', '', text)
    text = text.replace('`', '')

    # Bỏ dòng trông như code / lệnh (model viết code không bọc ```)
    text = "\n".join(line for line in text.split("\n") if not _CODE_LINE_RE.match(line))

    # Bỏ nguyên khối JSON / dict (kết quả tool lọt vào câu trả lời), kể cả lồng nhau
    text = _innermost_braces_removed(text)

    # Đường dẫn tệp: chỉ đọc tên tệp / thư mục cuối ("config.json"), không đọc
    # cả chuỗi "D:VNMateaiv1config.json" sau khi dấu \ bị bỏ.
    text = re.sub(r'\b[A-Za-z]:\\(?:[^\s\\]+\\)*([^\s\\]*)', lambda m: m.group(1) or 'ổ đĩa', text)
    text = re.sub(r'(?<![\w:/])(?:~|\.{1,2})?/(?:[\w.-]+/)+([\w.-]+)', r'\1', text)

    # Bỏ tiêu đề Markdown
    text = re.sub(r'^#{1,6}\s+', '', text, flags=re.MULTILINE)

    # Bỏ định dạng bold/italic. Gạch dưới chỉ là in nghiêng khi đứng ngoài từ —
    # `get_current_time` là tên, không phải in nghiêng.
    text = re.sub(r'\*{1,3}(.*?)\*{1,3}', r'\1', text, flags=re.DOTALL)
    text = re.sub(r'(?<!\w)_{1,3}([^_\n]+?)_{1,3}(?!\w)', r'\1', text)

    # Bỏ bullet và list đánh số
    text = re.sub(r'^\s*[-*+•]\s+', '', text, flags=re.MULTILINE)
    text = re.sub(r'^\s*\d+[.)]\s+', '', text, flags=re.MULTILINE)

    # Bỏ link Markdown (giữ nhãn) và URL
    text = re.sub(r'\[([^\]]*)\]\([^)]*\)', r'\1', text)
    text = re.sub(r'https?://\S+', '', text)

    # Bỏ bảng và phân cách
    text = re.sub(r'\|[^\n]*', '', text)
    text = re.sub(r'-{3,}', '', text)
    text = re.sub(r'={3,}', '', text)

    text = _EMOJI_RE.sub(' ', text)

    # snake_case đọc thành từng từ
    text = re.sub(r'(?<=\w)_(?=\w)', ' ', text)
    # Gạch ngang giữa hai vế câu -> ngắt nghỉ; gạch nối trong từ giữ nguyên
    text = re.sub(r'\s+[-–—]\s+', ', ', text)

    # Toán tử / mũi tên: không đọc ra tên ký hiệu
    text = re.sub(r'\s*(?:=>|->|<=|>=|==|!=|:=|\+=|-=)\s*', ' ', text)
    text = re.sub(r'(?<=\s)[=*/+]+(?=\s)', ' ', text)
    # "a/b" giữa hai chữ (không phải ngày, số) -> "a b"
    text = re.sub(r'(?<=[^\W\d])/(?=[^\W\d])', ' ', text)

    # Bỏ ký tự đặc biệt không phát âm được
    text = re.sub(r'[#@&^~\\<>{}[\]()*$|;]', '', text)
    text = re.sub(r'(?<![\w])"|"(?![\w])', '', text)

    # Chuẩn hoá xuống dòng
    text = re.sub(r'\n{2,}', '. ', text)
    text = re.sub(r'\n', ' ', text)

    # Bỏ khoảng trắng thừa; dấu chấm lặp do bỏ bảng / code ("100%..")
    text = re.sub(r'\s{2,}', ' ', text)
    text = re.sub(r'\.(?:\s*\.)+', '.', text)
    text = re.sub(r'\s+([.,!?;:])', r'\1', text)

    return text.strip()


def shorten_for_speech(text: str, max_chars: int = 200) -> str:
    """
    Rút lời nói dài về 1–2 câu đầu (giữ trọn câu).

    Không gắn thêm câu "Chi tiết cụ thể đã hiển thị trên màn hình." — câu này bị
    gắn vào MỌI câu dài (mỗi câu được rút riêng) nên nghe lặp lại như câu mẫu.

    Dùng khi đọc NGUYÊN câu trả lời một lần (ESP32, mic máy chủ, REST); đường
    stream từng câu không cần. Trước Phase 2 nằm trong `clean_text_for_tts`.
    """
    if len(text) <= max_chars:
        return text
    sentences = [s.strip() for s in re.split(r'(?<=[.!?])\s+', text) if len(s.strip()) > 3]
    short_parts: list[str] = []
    cur_len = 0
    for s in sentences:
        if cur_len + len(s) < max_chars - 30:
            short_parts.append(s)
            cur_len += len(s)
        else:
            break
    if short_parts:
        out = " ".join(short_parts)
        if not out.endswith(('.', '!', '?')):
            out += "."
        return out
    cut = text[:max_chars]
    space = cut.rfind(" ")
    return (cut[:space] if space > max_chars // 2 else cut).rstrip(" ,;:") + "."
