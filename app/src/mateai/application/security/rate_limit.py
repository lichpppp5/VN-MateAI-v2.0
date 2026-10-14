# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 Dương Thanh Lịch — VN-MateAI. See LICENSE and NOTICE.
"""
mateai/application/security/rate_limit.py
=========================================
Giới hạn tần suất dùng chung (prompt cuối §97) cho voice, lượt agent / LLM và phiên
WebSocket thoại. Ngưỡng: `settings.security.rate_limits` (0 = tắt).

Số đếm nằm ở kho trạng thái dùng chung (`infrastructure/cache/shared_state`): RAM khi chạy
một tiến trình, Redis khi có REDIS_URL — nhiều tiến trình / máy cùng một giới hạn (§145).
Đăng nhập dùng cùng kho nhưng khác nghĩa (đếm lần SAI rồi khoá) — xem `routers/auth.py`.
"""
from __future__ import annotations

from mateai.infrastructure.cache import shared_state


def limit(name: str, default: int) -> int:
    try:
        from mateai.config.loader import settings
        return int((settings.security.rate_limits or {}).get(name, default))
    except Exception:  # noqa: BLE001
        return default


def hit(key: str, max_hits: int, window_s: float) -> float:
    """Ghi một lần gọi. 0.0 = được phép; > 0 = số giây phải chờ (lần gọi này KHÔNG được tính)."""
    return shared_state.store().hit("rl:" + key, max_hits, window_s)


def open_session(kind: str, who: str, max_sessions: int) -> bool:
    return shared_state.store().slot_acquire(f"{kind}:{who}", max_sessions)


def close_session(kind: str, who: str) -> None:
    shared_state.store().slot_release(f"{kind}:{who}")


def reset() -> None:
    shared_state.reset()


def busy_message(wait_s: float) -> str:
    return f"Dạ, anh/chị đang gửi quá nhiều yêu cầu. Vui lòng thử lại sau khoảng {int(wait_s) + 1} giây."
