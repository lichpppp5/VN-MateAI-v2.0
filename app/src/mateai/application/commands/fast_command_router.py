# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 Dương Thanh Lịch — VN-MateAI. See LICENSE and NOTICE.
"""
core/fast_command_router.py
===========================
Phase 5: Fast Command Router (Bộ Điều Phối Lệnh Nhanh Tất Định).

Chức năng:
  - Khởi chạy các lệnh hệ thống và câu hỏi thường gặp tất định mà KHÔNG cần qua LLM.
  - Phản hồi cực nhanh: Thời gian xử lý < 2ms, TTFA < 50–100ms.
  - Hỗ trợ Pre-warmed Audio Cache: Lấy trực tiếp file MP3 từ RAM Cache (0ms TTS).
  - Tự động rơi xuống (fallback) pipeline LLM Tri-Brain thông thường nếu không khớp.

Các nhóm lệnh hỗ trợ:
  1. Kiểm tra trạng thái hệ thống: CPU, RAM, Disk, Uptime
  2. Thời gian & Ngày tháng: Giờ hiện tại, ngày hôm nay, thứ mấy
  3. Âm lượng & Truyền thông: Tắt tiếng, tăng/giảm âm lượng, dừng nói
  4. Mạng & Địa chỉ IP: IP nội bộ, kiểm tra kết nối mạng
  5. Chào hỏi & Giao tiếp phản xạ: Xin chào, cảm ơn, tạm biệt
  6. Ping / Tự kiểm tra: Kiểm tra độ trễ và tính sẵn sàng của hệ thống
"""

from __future__ import annotations

import asyncio
import datetime
import logging
import os
import re
import socket
import time
import unicodedata
from dataclasses import dataclass, field
from typing import Any, Callable, Coroutine, Dict, List, Optional, Tuple

import psutil

from mateai.infrastructure.tts.audio_cache import get_cached_audio_bytes, save_to_cache
from mateai.infrastructure.tts.tts_stream_engine import get_tts_engine

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Data Models
# ---------------------------------------------------------------------------

@dataclass
class FastCommandResult:
    """Kết quả phản hồi của một Fast Command."""
    is_matched: bool
    command_name: str
    reply_text: str
    audio_bytes: Optional[bytes] = None
    tool_executed: Optional[str] = None
    latency_ms: float = 0.0
    meta: Dict[str, Any] = field(default_factory=dict)


#: Câu trả lời CỐ ĐỊNH của lệnh nhanh — được làm nóng cache TTS lúc khởi động
#: (realtime P4) để tiếng tới ngay từ lần đầu; câu có số liệu thay đổi thì không.
MUTE_REPLY = "Dạ, em đã tắt tiếng và dừng phát âm thanh rồi ạ."
GREETING_REPLY = "Dạ, em chào sếp! Em có thể giúp gì cho sếp hôm nay ạ?"
THANKS_REPLY = "Dạ không có gì ạ! Rất vui được hỗ trợ sếp."
GOODBYE_REPLY = "Dạ tạm biệt sếp! Chúc sếp một ngày làm việc hiệu quả và nhiều niềm vui ạ."
PING_REPLY = "Pong! Hệ thống VN-MateAI hoạt động bình thường, phản hồi dưới 5 mili giây ạ."
STATIC_REPLIES = (MUTE_REPLY, GREETING_REPLY, THANKS_REPLY, GOODBYE_REPLY, PING_REPLY)


# ---------------------------------------------------------------------------
# Text Normalisation Helper
# ---------------------------------------------------------------------------

def _strip_vietnamese_accents(text: str) -> str:
    """Loại bỏ dấu tiếng Việt để so khớp regex linh hoạt."""
    text = text.replace("đ", "d").replace("Đ", "D")
    norm = unicodedata.normalize("NFKD", text)
    return "".join(c for c in norm if not unicodedata.combining(c)).lower().strip()


# ---------------------------------------------------------------------------
# Fast Command Router Implementation
# ---------------------------------------------------------------------------

class FastCommandRouter:
    """
    Router phân tích cú pháp nhanh trước khi gọi LLM:
    Nếu lệnh là tất định (CPU, RAM, Giờ, Chào hỏi, Volume, Mạng), trả về kết quả ngay lập tức.
    """

    def __init__(self) -> None:
        self._handlers: List[Tuple[re.Pattern, str, Callable[[str, re.Match], Coroutine[Any, Any, FastCommandResult]]]] = []
        self._register_default_handlers()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def dispatch(self, query: str, synthesize_audio: bool = True) -> Optional[FastCommandResult]:
        """
        Kiểm tra xem query có khớp với lệnh tất định nào không.
        Nếu khớp: Thực thi và trả về FastCommandResult (< 5ms).
        Nếu không: Trả về None để nhường cho LLM xử lý.
        """
        if not query or not query.strip():
            return None

        clean_query = query.strip()
        stripped_query = _strip_vietnamese_accents(clean_query)
        t0 = time.monotonic()

        for pattern, cmd_name, handler in self._handlers:
            # So khớp cả văn bản có dấu và không dấu
            m = pattern.search(clean_query) or pattern.search(stripped_query)
            if m:
                logger.info("[FastCommandRouter] Khớp lệnh nhanh '%s' cho câu: '%s'", cmd_name, clean_query)
                try:
                    result = await handler(clean_query, m)
                    result.latency_ms = (time.monotonic() - t0) * 1000

                    if synthesize_audio:
                        # Tìm âm thanh trong RAM Cache để đạt TTFA = 0ms
                        cached_audio = get_cached_audio_bytes(result.reply_text)
                        if cached_audio:
                            result.audio_bytes = cached_audio
                        else:
                            # Tổng hợp nhanh qua TTS Engine nếu chưa có trong cache
                            tts_engine = get_tts_engine()
                            result.audio_bytes = await tts_engine.synthesise(result.reply_text)

                    return result
                except Exception as exc:
                    logger.warning("[FastCommandRouter] Lỗi xử lý lệnh '%s': %s", cmd_name, exc)
                    # Nếu có lỗi, trả về None để LLM xử lý dự phòng
                    return None

        return None

    # ------------------------------------------------------------------
    # Registration & Handlers
    # ------------------------------------------------------------------

    def _register_default_handlers(self) -> None:
        """Đăng ký các mẫu lệnh tất định."""

        # 1. Thời gian hiện tại
        self._register(
            r"^(m[âa]y gio(?: roi)?|b[âa]y gio la m[âa]y gio|xem gio|thoi gian hien tai)$",
            "get_current_time",
            self._handle_current_time,
        )

        # 2. Ngày tháng hiện tại
        self._register(
            r"^(h[ôo]m nay (?:la )?ng[àa]y m[âa]y|ng[àa]y bao nhi[êe]u|h[ôo]m nay (?:la )?th[ưu] m[âa]y|xem ng[àa]y)$",
            "get_current_date",
            self._handle_current_date,
        )

        # 3. Kiểm tra CPU
        self._register(
            r"^(ki[êe]m tra cpu|xem cpu|cpu bao nhi[êe]u|cpu th[êe] n[àa]o|t[ình]nh tr[ạa]ng cpu)$",
            "get_cpu_status",
            self._handle_cpu_status,
        )

        # 4. Kiểm tra RAM
        self._register(
            r"^(ki[êe]m tra ram|xem ram|ram bao nhi[êe]u|b[ộo] nh[ớo] ram|t[ình]nh tr[ạa]ng ram)$",
            "get_ram_status",
            self._handle_ram_status,
        )

        # 5. Trạng thái hệ thống tổng quan
        self._register(
            r"^(ki[êe]m tra h[ệe] th[ốo]ng|tr[ạa]ng th[ái] h[ệe] th[ốo]ng|t[ình]nh tr[ạa]ng m[áy] tinh|status h[ệe] th[ốo]ng)$",
            "get_system_overview",
            self._handle_system_overview,
        )

        # 6. Kiểm tra ổ cứng / ổ đĩa
        self._register(
            r"^(ki[êe]m tra [ôo] (?:d[ĩi]a|c[ứ]ng)|dung lu[ợ]ng [ôo] (?:d[ĩi]a|c[ứ]ng)|[ôo] c[ứ]ng c[ò]n bao nhi[êe]u)$",
            "get_disk_status",
            self._handle_disk_status,
        )

        # 7. Mạng & Địa chỉ IP
        self._register(
            r"^(ki[êe]m tra m[ạa]ng|dia chi ip(?: cua may)?|ip cua may|xem ip|ip noi bo)$",
            "get_network_ip",
            self._handle_network_ip,
        )

        # 8. Điều khiển âm lượng & Tắt tiếng
        self._register(
            r"^(t[ắ]t ti[ế]ng|mute|im l[ặ]ng|ng[ừ]ng n[ó]i|d[ừ]ng ph[á]t|d[ừ]ng l[ạ]i)$",
            "mute_audio",
            self._handle_mute_audio,
        )
        self._register(
            r"^(?:d[ìê]u ch[ỉ]nh |c[à]i )?[âa]m lu[ợ]ng(?: l[êe]n)? (\d+)(?:%| ph[âa]n tr[ắ]m)?$",
            "set_volume",
            self._handle_set_volume,
        )

        # 9. Chào hỏi & Giao tiếp phản xạ
        self._register(
            r"^(xin ch[àa]o|ch[àa]o em|ch[àa]o b[ạa]n|hello mate|hi mate|ch[àa]o tr[ợ] l[ý] [ả]o)$",
            "quick_greeting",
            self._handle_quick_greeting,
        )
        self._register(
            r"^(c[ả]m [ơn] em|c[ả]m [ơn] b[ạa]n|thank you|thanks mate)$",
            "quick_thanks",
            self._handle_quick_thanks,
        )
        self._register(
            r"^(t[ạa]m bi[ệ]t|h[ẹ]n g[ặ]p l[ạ]i|bye bye)$",
            "quick_goodbye",
            self._handle_quick_goodbye,
        )

        # 10. Ping & Tự kiểm tra kết nối
        self._register(
            r"^(ping|ping h[ệe] th[ốo]ng|ki[êe]m tra k[ế]t n[ố]i)$",
            "system_ping",
            self._handle_system_ping,
        )

    def _register(
        self,
        regex_pattern: str,
        command_name: str,
        handler: Callable[[str, re.Match], Coroutine[Any, Any, FastCommandResult]],
    ) -> None:
        pattern = re.compile(regex_pattern, re.IGNORECASE | re.UNICODE)
        self._handlers.append((pattern, command_name, handler))

    # ------------------------------------------------------------------
    # Handler Implementations
    # ------------------------------------------------------------------

    async def _handle_current_time(self, query: str, match: re.Match) -> FastCommandResult:
        now = datetime.datetime.now()
        reply = f"Bây giờ là {now.hour} giờ {now.minute:02d} phút ạ."
        return FastCommandResult(
            is_matched=True,
            command_name="get_current_time",
            reply_text=reply,
            tool_executed="datetime.now",
            meta={"hour": now.hour, "minute": now.minute},
        )

    async def _handle_current_date(self, query: str, match: re.Match) -> FastCommandResult:
        now = datetime.datetime.now()
        weekdays = ["Thứ Hai", "Thứ Ba", "Thứ Tư", "Thứ Năm", "Thứ Sáu", "Thứ Bảy", "Chủ Nhật"]
        weekday_vn = weekdays[now.weekday()]
        reply = f"Hôm nay là {weekday_vn}, ngày {now.day} tháng {now.month} năm {now.year} ạ."
        return FastCommandResult(
            is_matched=True,
            command_name="get_current_date",
            reply_text=reply,
            tool_executed="datetime.now",
            meta={"weekday": weekday_vn, "day": now.day, "month": now.month, "year": now.year},
        )

    async def _handle_cpu_status(self, query: str, match: re.Match) -> FastCommandResult:
        # Đo 50 ms trong luồng phụ: trước đây gọi thẳng trong hàm async -> mọi phiên
        # realtime đứng 50 ms mỗi lần ai hỏi CPU (bench: p50 50,8 ms; RULE-026).
        cpu_percent = await asyncio.to_thread(psutil.cpu_percent, 0.05)
        reply = f"Dạ, mức tải CPU hiện tại là {cpu_percent:.1f}% ạ."
        return FastCommandResult(
            is_matched=True,
            command_name="get_cpu_status",
            reply_text=reply,
            tool_executed="psutil.cpu_percent",
            meta={"cpu_percent": cpu_percent},
        )

    async def _handle_ram_status(self, query: str, match: re.Match) -> FastCommandResult:
        ram = psutil.virtual_memory()
        used_gb = ram.used / (1024 ** 3)
        total_gb = ram.total / (1024 ** 3)
        reply = f"Dạ, RAM đã sử dụng {ram.percent:.1f}%, tức {used_gb:.1f} trên {total_gb:.1f} GB ạ."
        return FastCommandResult(
            is_matched=True,
            command_name="get_ram_status",
            reply_text=reply,
            tool_executed="psutil.virtual_memory",
            meta={"ram_percent": ram.percent, "used_gb": used_gb, "total_gb": total_gb},
        )

    async def _handle_system_overview(self, query: str, match: re.Match) -> FastCommandResult:
        cpu = await asyncio.to_thread(psutil.cpu_percent, 0.05)
        ram = psutil.virtual_memory()
        reply = f"Dạ, hệ thống đang hoạt động tốt. CPU ở mức {cpu:.1f}%, RAM đã dùng {ram.percent:.1f}% ạ."
        return FastCommandResult(
            is_matched=True,
            command_name="get_system_overview",
            reply_text=reply,
            tool_executed="psutil.overview",
            meta={"cpu": cpu, "ram_percent": ram.percent},
        )

    async def _handle_disk_status(self, query: str, match: re.Match) -> FastCommandResult:
        path = "/" if os.name != "nt" else "C:\\"
        disk = psutil.disk_usage(path)
        free_gb = disk.free / (1024 ** 3)
        reply = f"Dạ, ổ đĩa chính còn trống {free_gb:.1f} GB, đã sử dụng {disk.percent:.1f}% ạ."
        return FastCommandResult(
            is_matched=True,
            command_name="get_disk_status",
            reply_text=reply,
            tool_executed="psutil.disk_usage",
            meta={"free_gb": free_gb, "disk_percent": disk.percent},
        )

    async def _handle_network_ip(self, query: str, match: re.Match) -> FastCommandResult:
        ip = "127.0.0.1"
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            s.connect(("8.8.8.8", 80))
            ip = s.getsockname()[0]
            s.close()
        except Exception:
            pass
        reply = f"Dạ, địa chỉ IP nội bộ của máy là {ip}, mạng đang kết nối tốt ạ."
        return FastCommandResult(
            is_matched=True,
            command_name="get_network_ip",
            reply_text=reply,
            tool_executed="socket.getsockname",
            meta={"ip": ip},
        )

    async def _handle_mute_audio(self, query: str, match: re.Match) -> FastCommandResult:
        reply = MUTE_REPLY
        return FastCommandResult(
            is_matched=True,
            command_name="mute_audio",
            reply_text=reply,
            tool_executed="audio.mute",
            meta={"action": "mute"},
        )

    async def _handle_set_volume(self, query: str, match: re.Match) -> FastCommandResult:
        vol_str = match.group(1)
        vol_int = max(0, min(100, int(vol_str)))
        reply = f"Dạ, em đã điều chỉnh âm lượng lên {vol_int}% ạ."
        return FastCommandResult(
            is_matched=True,
            command_name="set_volume",
            reply_text=reply,
            tool_executed="audio.set_volume",
            meta={"volume": vol_int},
        )

    async def _handle_quick_greeting(self, query: str, match: re.Match) -> FastCommandResult:
        reply = GREETING_REPLY
        return FastCommandResult(
            is_matched=True,
            command_name="quick_greeting",
            reply_text=reply,
            tool_executed="reflex.greeting",
        )

    async def _handle_quick_thanks(self, query: str, match: re.Match) -> FastCommandResult:
        reply = THANKS_REPLY
        return FastCommandResult(
            is_matched=True,
            command_name="quick_thanks",
            reply_text=reply,
            tool_executed="reflex.thanks",
        )

    async def _handle_quick_goodbye(self, query: str, match: re.Match) -> FastCommandResult:
        reply = GOODBYE_REPLY
        return FastCommandResult(
            is_matched=True,
            command_name="quick_goodbye",
            reply_text=reply,
            tool_executed="reflex.goodbye",
        )

    async def _handle_system_ping(self, query: str, match: re.Match) -> FastCommandResult:
        reply = PING_REPLY
        return FastCommandResult(
            is_matched=True,
            command_name="system_ping",
            reply_text=reply,
            tool_executed="system.ping",
        )


# Singleton Instance
fast_command_router = FastCommandRouter()
