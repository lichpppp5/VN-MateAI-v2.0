# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 Dương Thanh Lịch — VN-MateAI. See LICENSE and NOTICE.
"""
core/audio/sentence_buffer.py
=============================
Phase 3: Vietnamese Intelligent Sentence Buffer (Anti-False-Split).

Mục tiêu:
  - Nhận luồng token LLM streaming từng mảnh nhỏ.
  - Tách câu hoàn chỉnh ngay khi gặp dấu kết thúc (. ! ? ; … \n\n) để chuyển ngay cho TTS worker.
  - TUYỆT ĐỐI KHÔNG ngắt sai tại:
      * Số thập phân: 3.14, 0.5, 99.9%
      * Địa chỉ IP: 192.168.1.1, 10.0.0.1, 192.168.1.27
      * Phiên bản phần mềm: v1.2.3, 2.0.0
      * Tên miền & URL: example.com, google.com.vn, localhost:8000
      * Đường dẫn file & thư mục: C:\\Users\\Admin, /var/log/syslog, file.py, data.json
      * Từ viết tắt: v.v., v.d., tp., ts., mr., dr.
  - Hỗ trợ câu ngắn đàm thoại ("Xin chào anh.", "Được rồi.", "CPU là 32%. RAM là 61%.").
  - Tự động chuẩn hóa văn bản tự nhiên (TTS Sanitisation) loại bỏ Markdown/Code rác.
"""

from __future__ import annotations

import re
from typing import AsyncGenerator, List, Optional

from mateai.application.voice.speech_text import CodeFenceStripper, sanitise_for_tts

# ---------------------------------------------------------------------------
# Regex Heuristics cho các trường hợp KHÔNG ĐƯỢC NGẮT (False Boundary Guards)
# ---------------------------------------------------------------------------

# 1. Số thập phân và tỷ lệ: 3.14, 0.05, 100.0%
_DECIMAL_RE = re.compile(r'\d+\.\d+')

# 2. Địa chỉ IPv4: 192.168.1.1
_IP_RE = re.compile(r'\b(?:\d{1,3}\.){3}\d{1,3}\b')

# 3. Phiên bản: v1.2.3, 2.0.0, 10.15.7
_VERSION_RE = re.compile(r'\bv?\d+\.\d+(\.\d+)+\b', re.IGNORECASE)

# 4. Tên miền phổ biến: domain.com, vn, net, org, io, ai, dev
_DOMAIN_RE = re.compile(r'\b[a-zA-Z0-9-]+\.(?:com|vn|net|org|io|ai|dev|edu|gov|co|info|biz)\b', re.IGNORECASE)

# 5. Đường dẫn file Windows & Linux: C:\..., /home/..., file.ext
_PATH_RE = re.compile(r'(?:[a-zA-Z]:\\[^\s]+|\/[^\s]+\.[a-zA-Z0-9]+|\b\w+\.(?:py|json|txt|docx|xlsx|pdf|csv|md|exe|sh)\b)', re.IGNORECASE)

# 6. Từ viết tắt tiếng Việt & tiếng Anh phổ biến
_ABBREV_RE = re.compile(r'\b(?:v\.v|v\.d|tp|ts|th\.s|mr|mrs|ms|dr|st|inc|ltd|co)\.$', re.IGNORECASE)

# Ký tự kết thúc câu thật sự
_PUNCTUATION_CHARS = {'.', '!', '?', ';', '…'}


class SentenceBuffer:
    """
    Bộ đệm nhận token stream và phát ra câu hoàn chỉnh cho TTS.
    Bảo vệ các cấu trúc số, IP, version, domain không bị xé vụn.
    """

    def __init__(
        self,
        min_chars: int = 5,
        max_buffer_chars: int = 250,
        min_words: int = 0,
        max_words: int = 0,
        first_max_words: int = 0,
    ) -> None:
        """
        min_words / max_words (0 = tắt): chính sách NGHE TỰ NHIÊN cho đường voice,
        chuyển từ `LLMEngine._extract_sentences` (Phase 5) — người dùng phản ánh
        "đọc 2-3 chữ một": câu ngắn hơn min_words được GIỮ LẠI gộp với câu sau
        (không bỏ); câu dài hơn max_words được tách, ưu tiên sau dấu phẩy.
        Ranh giới câu vẫn là ranh giới AN TOÀN của lớp này (không cắt "3.5", IP,
        URL…) — bản cũ cắt bằng regex thô nên "3.5" thành "3. 5".

        first_max_words (0 = tắt): giới hạn RIÊNG cho câu phát ĐẦU TIÊN (realtime
        P4). Thời gian TTS tăng theo độ dài câu (9Router đo được: 7 từ 1,26 s,
        28 từ 2,18 s) nên câu đầu ngắn -> tiếng đầu sớm hơn; câu sau tổng hợp
        gối đầu trong lúc câu đầu đang đọc. Câu đầu chưa có dấu kết câu mà đã
        đủ dài thì cắt luôn sau dấu phẩy, không chờ hết câu.
        """
        self.min_chars = min_chars
        self.max_buffer_chars = max_buffer_chars
        self.min_words = min_words
        self.max_words = max_words
        self.first_max_words = first_max_words
        self._emitted_any = False
        self._buffer: str = ""
        self._pending: str = ""
        # Khối ```code``` kéo dài qua nhiều câu — lọc trên luồng token.
        self._fence = CodeFenceStripper()

    def add_token(self, token: str) -> List[str]:
        """
        Nạp một token từ LLM stream vào buffer.
        Trả về danh sách câu hoàn chỉnh nếu phát hiện ranh giới câu an toàn.
        """
        if not token:
            return []

        token = self._fence.feed(token)
        if not token:
            return []
        self._buffer += token
        ready_sentences: List[str] = []

        while True:
            split_idx = self._find_safe_boundary(self._buffer)
            if split_idx is None and not ready_sentences and self._wants_early_first_cut():
                split_idx = self._early_first_cut(self._buffer)
                # Phần cắt đủ min_words nên chắc chắn được phát: câu sau không
                # còn là câu đầu.
                self._emitted_any = split_idx is not None
            if split_idx is None:
                # Nếu buffer quá dài vượt ngưỡng an toàn mà không có dấu kết thúc:
                if len(self._buffer) >= self.max_buffer_chars:
                    # Cắt tại khoảng trắng gần nhất sau 100 ký tự
                    space_idx = self._buffer.rfind(" ", 60, self.max_buffer_chars)
                    if space_idx != -1:
                        chunk = self._buffer[:space_idx].strip()
                        self._buffer = self._buffer[space_idx:].lstrip()
                        clean = sanitise_for_tts(chunk)
                        if clean and len(clean) >= self.min_chars:
                            ready_sentences.append(clean)
                        continue
                break

            # Tách câu an toàn
            raw_sentence = self._buffer[:split_idx].strip()
            self._buffer = self._buffer[split_idx:].lstrip()

            clean = sanitise_for_tts(raw_sentence)
            if clean and len(clean) >= self.min_chars:
                ready_sentences.append(clean)

        return self._group(ready_sentences, final=False)

    def flush(self) -> List[str]:
        """
        Xả toàn bộ nội dung còn lại trong buffer khi LLM kết thúc stream.
        """
        parts: List[str] = []
        self._buffer += self._fence.flush()
        if self._buffer.strip():
            clean = sanitise_for_tts(self._buffer.strip())
            if clean and len(clean) >= self.min_chars:
                parts.append(clean)
        self._buffer = ""
        return self._group(parts, final=True)

    # ------------------------------------------------------------------
    # Gộp câu ngắn / tách câu dài (chỉ khi bật min_words / max_words)
    # ------------------------------------------------------------------

    def _group(self, parts: List[str], final: bool) -> List[str]:
        if not self.min_words and not self.max_words:
            return parts
        out: List[str] = []
        for part in parts:
            if self._pending:
                part = f"{self._pending} {part}"
                self._pending = ""
            if self.min_words and len(part.split()) < self.min_words:
                self._pending = part  # chờ câu sau để gộp
                continue
            out.extend(self._split_long(part))
        if final and self._pending:
            out.extend(self._split_long(self._pending))
            self._pending = ""
        return out

    def _wants_early_first_cut(self) -> bool:
        return bool(self.first_max_words) and not self._emitted_any and not self._pending

    def _early_first_cut(self, text: str) -> Optional[int]:
        """Câu đầu đang stream, chưa có dấu kết câu: cắt ở dấu phẩy (", ") đầu tiên
        có đủ min_words từ phía trước (và không quá max_words) — đọc sớm nhất có thể."""
        for m in re.finditer(r",\s", text):
            n = len(text[: m.start()].split())
            if self.max_words and n > self.max_words:
                return None
            if n >= max(1, self.min_words):
                return m.end()
        return None

    def _split_long(self, part: str) -> List[str]:
        if not self.max_words:
            return [part]
        words = part.split()
        chunks: List[str] = []
        if self.first_max_words and not self._emitted_any and len(words) > self.first_max_words:
            # Câu đầu: chỉ cắt ở dấu phẩy (cắt giữa cụm từ thì giọng đọc ngắt sai
            # chỗ), đủ min_words, càng gần first_max_words càng tốt.
            commas = [i + 1 for i, w in enumerate(words[:-1])
                      if w.endswith(",") and i + 1 >= max(1, self.min_words)]
            within = [n for n in commas if n <= self.first_max_words]
            n = max(within) if within else min((c for c in commas if c <= self.max_words), default=0)
            if n:
                chunks.append(" ".join(words[:n]))
                words = words[n:]
        while len(words) > self.max_words:
            head = words[: self.max_words]
            # Ưu tiên cắt sau dấu phẩy gần nhất — chỗ thở tự nhiên của giọng đọc.
            cut = max((i for i, w in enumerate(head) if w.endswith(",")), default=None)
            if cut is not None and cut >= max(1, self.min_words // 2):
                head = head[: cut + 1]
            chunks.append(" ".join(head).strip())
            words = words[len(head):]
        tail = " ".join(words).strip()
        if tail:
            chunks.append(tail)
        if chunks:
            self._emitted_any = True
        return chunks

    async def stream_sentences(
        self,
        token_generator: AsyncGenerator[str, None],
    ) -> AsyncGenerator[str, None]:
        """
        Giao diện Async Generator tiêu chuẩn:
        Nhận từng token -> yield từng câu hoàn chỉnh.
        """
        async for token in token_generator:
            sentences = self.add_token(token)
            for s in sentences:
                yield s

        for s in self.flush():
            yield s

    # ------------------------------------------------------------------
    # Heuristic Detection of Safe Sentence Boundaries
    # ------------------------------------------------------------------

    def _find_safe_boundary(self, text: str) -> Optional[int]:
        """
        Tìm vị trí kết thúc câu an toàn trong buffer.
        Trả về index kết thúc (bao gồm cả dấu câu) nếu tìm thấy ranh giới thật sự.
        Trả về None nếu dấu câu chỉ là một phần của số, IP, domain, hoặc chưa chắc chắn.
        """
        length = len(text)
        if length < 3:
            return None

        # Quét từng ký tự trong chuỗi (chừa ký tự cuối cùng nếu là dấu chấm chưa rõ ngữ cảnh)
        for i, char in enumerate(text):
            # Xuống dòng kép (\n\n) là ranh giới câu tuyệt đối
            if char == '\n':
                if i + 1 < length and text[i + 1] == '\n':
                    return i + 2
                # Hoặc xuống dòng đơn sau một câu dài
                if i >= self.min_chars and i + 1 < length and text[i + 1].strip():
                    return i + 1

            if char not in _PUNCTUATION_CHARS:
                continue

            # Xử lý dấu chấm kết thúc câu
            end_idx = i + 1

            # Gom các dấu liên tiếp (ví dụ: "...", "!?")
            while end_idx < length and text[end_idx] in _PUNCTUATION_CHARS:
                end_idx += 1

            # NẾU dấu chấm nằm ở cuối buffer cùng và trước đó là chữ số hoặc chữ cái:
            # Ta CHƯA THỂ BIẾT token tiếp theo có phải là số (e.g. "3." -> "14") hay domain không.
            # Do đó phải chờ thêm token!
            if end_idx >= length:
                # Nếu là ? hoặc ! thì an toàn kết thúc ngay
                if char in ('?', '!'):
                    return end_idx
                # Nếu trước dấu chấm là số hoặc chữ cái không có khoảng trắng -> Chờ token tiếp theo!
                if i > 0 and (text[i - 1].isalnum() or text[i - 1] in ('%', ')', '"')):
                    return None

            # Kiểm tra ký tự ngay sau dấu câu:
            # Trong văn bản tiếng Việt/Anh chuẩn, sau dấu chấm ngắt câu PHẢI là khoảng trắng hoặc xuống dòng hoặc ngoặc/nháy.
            if end_idx < length:
                next_char = text[end_idx]
                if next_char not in (' ', '\t', '\n', '"', "'", '”', ')', ']'):
                    # Ký tự liền sau là chữ hoặc số (e.g. "3.14", "example.com", "v1.2") -> KHÔNG ngắt!
                    continue

            # Kiểm tra ngữ cảnh xung quanh vị trí i
            if not self._is_safe_sentence_end(text, i, end_idx):
                continue

            # Đảm bảo độ dài câu trước dấu ngắt tối thiểu
            candidate = text[:end_idx].strip()
            if len(candidate) >= self.min_chars:
                return end_idx

        return None

    def _is_safe_sentence_end(self, text: str, punct_idx: int, end_idx: int) -> bool:
        """
        Kiểm tra chuyên sâu xem vị trí punct_idx có vi phạm bất kỳ False-Boundary nào không.
        """
        # Lấy cửa sổ ngữ cảnh xung quanh (tối đa 30 ký tự trước và sau)
        start_win = max(0, punct_idx - 30)
        end_win = min(len(text), end_idx + 30)
        context = text[start_win:end_win]

        rel_idx = punct_idx - start_win

        # 1. Kiểm tra số thập phân (e.g. "3.14")
        for m in _DECIMAL_RE.finditer(context):
            if m.start() < rel_idx < m.end():
                return False

        # 2. Kiểm tra địa chỉ IP (e.g. "192.168.1.1")
        for m in _IP_RE.finditer(context):
            if m.start() < rel_idx < m.end():
                return False

        # 3. Kiểm tra version (e.g. "v1.2.3")
        for m in _VERSION_RE.finditer(context):
            if m.start() < rel_idx < m.end():
                return False

        # 4. Kiểm tra domain (e.g. "example.com")
        for m in _DOMAIN_RE.finditer(context):
            if m.start() < rel_idx < m.end():
                return False

        # 5. Kiểm tra file path & extensions (e.g. "C:\Users\Admin", "file.py")
        for m in _PATH_RE.finditer(context):
            if m.start() < rel_idx < m.end():
                return False

        # 6. Kiểm tra từ viết tắt (e.g. "v.v.", "tp.")
        prefix = text[:punct_idx + 1]
        if _ABBREV_RE.search(prefix):
            return False

        return True
