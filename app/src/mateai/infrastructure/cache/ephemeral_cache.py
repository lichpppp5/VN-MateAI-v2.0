# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 Dương Thanh Lịch — VN-MateAI. See LICENSE and NOTICE.
"""
core/ephemeral_cache.py
=======================
Bộ Đệm Tự Hủy Tuân Thủ Quyền Riêng Tư (Ephemeral Session Cache).
Phase: Ephemeral Data Lifecycle & Privacy-Compliant Cache (GDPR & Nghị định 13).

Đặc điểm:
  1. Vận hành 100% trên RAM (In-Memory), tuyệt đối không ghi xuống đĩa cứng.
  2. Cơ chế Sliding Expiration (mặc định 15 phút / 900s) và Hard Timeout (mặc định 30 phút / 1800s).
  3. Quản trị vòng đời ngữ cảnh: get, set, invalidate_domain, flush_all.
  4. Background Sweeper (Garbage Collector): coroutine chạy định kỳ mỗi 60 giây giải phóng các key hết hạn, chống rò rỉ RAM.
"""

from __future__ import annotations

import asyncio
import logging
import threading
import time
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger("mateai.infrastructure.cache.ephemeral_cache")

DEFAULT_SLIDING_TTL_SEC = 900.0   # 15 phút
DEFAULT_HARD_TIMEOUT_SEC = 1800.0 # 30 phút
SWEEPER_INTERVAL_SEC = 60.0       # 60 giây


@dataclass
class CacheItem:
    """Đơn vị lưu trữ trong RAM với hạn định kép (Sliding & Hard Timeout)."""
    session_id: str
    domain_key: str
    data: Any
    created_at: float
    last_accessed_at: float
    sliding_ttl_seconds: float = DEFAULT_SLIDING_TTL_SEC
    hard_timeout_seconds: float = DEFAULT_HARD_TIMEOUT_SEC

    def is_expired(self, now: float) -> bool:
        """Kiểm tra xem item đã quá hạn Sliding hoặc Hard Timeout chưa."""
        if (now - self.created_at) > self.hard_timeout_seconds:
            return True
        if (now - self.last_accessed_at) > self.sliding_ttl_seconds:
            return True
        return False


class EphemeralSessionCache:
    """Bộ đệm ngữ nghĩa tự hủy trên bộ nhớ RAM bảo vệ dữ liệu nhạy cảm."""

    def __init__(self) -> None:
        # Lưu trữ: { (session_id, domain_key): CacheItem }
        self._store: Dict[Tuple[str, str], CacheItem] = {}
        # Theo dõi domain đang active gần nhất của từng session: { session_id: last_domain_key }
        self._active_domain_by_session: Dict[str, str] = {}
        self._lock = threading.RLock()
        self._sweeper_task: Optional[asyncio.Task] = None

    def get(self, session_id: str, domain_key: str) -> Optional[Any]:
        """
        Lấy dữ liệu đệm trong RAM.
        Nếu còn hạn: cập nhật lại last_accessed_at (Sliding Expiration).
        Nếu quá hạn: xóa ngay lập tức khỏi RAM và trả về None.
        """
        s_id = session_id.strip()
        d_key = domain_key.strip().upper()
        key = (s_id, d_key)
        now = time.time()

        with self._lock:
            item = self._store.get(key)
            if not item:
                return None

            if item.is_expired(now):
                # Tự hủy tức thì khi phát hiện quá hạn
                del self._store[key]
                logger.info("[EphemeralCache] Cache item (%s, %s) đã hết hạn và được giải phóng khỏi RAM.", s_id, d_key)
                return None

            # Cập nhật sliding expiration
            item.last_accessed_at = now
            self._active_domain_by_session[s_id] = d_key
            logger.debug("[EphemeralCache] Hit cache (%s, %s) — Sliding TTL gia hạn thêm %ss.", s_id, d_key, item.sliding_ttl_seconds)
            return item.data

    def set(
        self,
        session_id: str,
        domain_key: str,
        data: Any,
        sliding_ttl: float = DEFAULT_SLIDING_TTL_SEC,
        hard_timeout: float = DEFAULT_HARD_TIMEOUT_SEC,
    ) -> None:
        """Lưu dữ liệu mới vào RAM kèm thời hạn tự hủy."""
        s_id = session_id.strip()
        d_key = domain_key.strip().upper()
        now = time.time()

        item = CacheItem(
            session_id=s_id,
            domain_key=d_key,
            data=data,
            created_at=now,
            last_accessed_at=now,
            sliding_ttl_seconds=sliding_ttl,
            hard_timeout_seconds=hard_timeout,
        )

        with self._lock:
            self._store[(s_id, d_key)] = item
            self._active_domain_by_session[s_id] = d_key

        logger.info(
            "[EphemeralCache] Lưu vào RAM (%s, %s) — Sliding TTL: %ss, Hard Timeout: %ss.",
            s_id, d_key, sliding_ttl, hard_timeout,
        )

    def invalidate_domain(self, session_id: str, domain_key: str) -> bool:
        """Hủy cache của domain cụ thể khi người dùng chuyển ngữ cảnh (Context Switch)."""
        s_id = session_id.strip()
        d_key = domain_key.strip().upper()
        key = (s_id, d_key)

        with self._lock:
            if key in self._store:
                del self._store[key]
                logger.info("[EphemeralCache] Context Switch: Đã tiêu hủy an toàn dữ liệu domain %s của phiên %s.", d_key, s_id)
                return True
        return False

    def flush_all(self, session_id: str) -> int:
        """Xóa sạch toàn bộ dữ liệu phiên khi người dùng phát lệnh kết thúc ('Cảm ơn', 'Xong việc')."""
        s_id = session_id.strip()
        deleted_count = 0

        with self._lock:
            keys_to_delete = [k for k in self._store if k[0] == s_id]
            for k in keys_to_delete:
                del self._store[k]
                deleted_count += 1
            self._active_domain_by_session.pop(s_id, None)

        logger.info("[EphemeralCache] Lệnh kết thúc phiên: Đã xóa sạch %d cache items của phiên %s khỏi RAM.", deleted_count, s_id)
        return deleted_count

    def get_active_domain(self, session_id: str) -> Optional[str]:
        """Lấy domain đang hoạt động gần nhất của phiên."""
        with self._lock:
            return self._active_domain_by_session.get(session_id.strip())

    def sweep_expired(self) -> int:
        """Quét và giải phóng toàn bộ các key đã hết hạn (Garbage Collector)."""
        now = time.time()
        expired_keys = []

        with self._lock:
            for key, item in self._store.items():
                if item.is_expired(now):
                    expired_keys.append(key)
            for key in expired_keys:
                del self._store[key]

        if expired_keys:
            logger.info("[EphemeralCache Background Sweeper] Đã dọn dẹp %d key hết hạn khỏi RAM.", len(expired_keys))
        return len(expired_keys)

    def get_stats(self) -> Dict[str, Any]:
        """Thống kê bộ đệm RAM phục vụ giám sát Zero-Trust."""
        now = time.time()
        with self._lock:
            total_items = len(self._store)
            active_items = sum(1 for item in self._store.values() if not item.is_expired(now))
            sessions = len(set(k[0] for k in self._store.keys()))
            return {
                "total_items_in_ram": total_items,
                "active_items": active_items,
                "active_sessions": sessions,
                "storage_type": "100% In-Memory RAM (Ephemeral)",
            }

    async def start_sweeper_loop(self) -> None:
        """Chạy coroutine ngầm quét dọn rác định kỳ mỗi 60 giây."""
        logger.info("[EphemeralCache] Khởi động Background Sweeper (quét mỗi %ss)...", SWEEPER_INTERVAL_SEC)
        while True:
            try:
                await asyncio.sleep(SWEEPER_INTERVAL_SEC)
                self.sweep_expired()
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error("[EphemeralCache Sweeper] Lỗi vòng lặp quét rác: %s", e)


# Singleton instance
ephemeral_cache = EphemeralSessionCache()
