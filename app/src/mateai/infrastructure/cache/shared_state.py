# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 Dương Thanh Lịch — VN-MateAI. See LICENSE and NOTICE.
"""
mateai/infrastructure/cache/shared_state.py
===========================================
Trạng thái ngắn hạn dùng chung (prompt cuối §67, §145): giới hạn tần suất, bộ đếm chế độ khẩn
cấp, khoá đăng nhập, số phiên. Thay ba bộ đếm riêng từng nằm trong RAM của MỘT tiến trình.

  - `MemoryStore`  (mặc định) — một tiến trình;
  - `RedisStore`   khi có `REDIS_URL` / `VNMATEAI_REDIS_URL` — nhiều tiến trình / máy dùng chung;
  - `FallbackStore` bọc Redis: Redis lỗi -> lùi về RAM, có cảnh báo, không làm hỏng request (§98).

Redis KHÔNG là nguồn sự thật (§67): chỉ giữ số đếm có hạn dùng; mất Redis = mất số đếm, không mất dữ liệu.
"""
from __future__ import annotations

import collections
import logging
import os
import threading
import time
import uuid
from typing import Deque, Dict, List, Optional

logger = logging.getLogger(__name__)


class MemoryStore:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._hits: Dict[str, Deque[float]] = {}
        self._events: Dict[str, List[float]] = {}
        self._slots: Dict[str, int] = collections.Counter()

    def hit(self, key: str, limit: int, window_s: float) -> float:
        """Ghi một lần gọi; 0.0 = được phép, > 0 = số giây phải chờ (lần này KHÔNG được tính)."""
        if limit <= 0:
            return 0.0
        now = time.time()
        with self._lock:
            q = self._hits.setdefault(key, collections.deque())
            while q and now - q[0] > window_s:
                q.popleft()
            if len(q) >= limit:
                return max(0.05, window_s - (now - q[0]))
            q.append(now)
            return 0.0

    def events_add(self, key: str, ts: float) -> None:
        with self._lock:
            self._events.setdefault(key, []).append(ts)

    def events_since(self, key: str, since: float) -> List[float]:
        with self._lock:
            kept = [t for t in self._events.get(key, []) if t >= since]
            if kept:
                self._events[key] = kept
            else:
                self._events.pop(key, None)
            return list(kept)

    def events_clear(self, key: str) -> None:
        with self._lock:
            self._events.pop(key, None)

    def slot_acquire(self, key: str, max_slots: int) -> bool:
        if max_slots <= 0:
            return True
        with self._lock:
            if self._slots[key] >= max_slots:
                return False
            self._slots[key] += 1
            return True

    def slot_release(self, key: str) -> None:
        with self._lock:
            self._slots[key] = max(0, self._slots[key] - 1)

    def reset(self) -> None:
        with self._lock:
            self._hits.clear()
            self._events.clear()
            self._slots.clear()


# Cửa sổ trượt nguyên tử trên Redis: dọn mốc cũ -> đếm -> thêm (nếu còn chỗ) trong MỘT lệnh.
_HIT_LUA = """
local key = KEYS[1]
local now = tonumber(ARGV[1]); local win = tonumber(ARGV[2]); local lim = tonumber(ARGV[3])
redis.call('ZREMRANGEBYSCORE', key, '-inf', now - win)
if redis.call('ZCARD', key) >= lim then
  local oldest = redis.call('ZRANGE', key, 0, 0, 'WITHSCORES')
  return tostring(win - (now - tonumber(oldest[2])))
end
redis.call('ZADD', key, now, ARGV[4])
redis.call('PEXPIRE', key, math.ceil(win * 1000) + 1000)
return '0'
"""
_SLOT_LUA = """
local n = tonumber(redis.call('GET', KEYS[1]) or '0')
if n >= tonumber(ARGV[1]) then return 0 end
redis.call('INCR', KEYS[1]); redis.call('EXPIRE', KEYS[1], 86400)
return 1
"""


class RedisStore:
    def __init__(self, url: str, prefix: str = "vnmate:", socket_timeout: float = 1.0) -> None:
        import redis
        # protocol=2: tương thích cả Redis 5 (bản Windows) — RESP3 cần Redis >= 6.
        self._r = redis.Redis.from_url(url, protocol=2, socket_timeout=socket_timeout,
                                       socket_connect_timeout=socket_timeout, decode_responses=True)
        self._p = prefix
        self._hit = self._r.register_script(_HIT_LUA)
        self._slot = self._r.register_script(_SLOT_LUA)

    def hit(self, key: str, limit: int, window_s: float) -> float:
        if limit <= 0:
            return 0.0
        wait = float(self._hit(keys=[self._p + "hit:" + key],
                               args=[time.time(), window_s, limit, uuid.uuid4().hex]))
        return max(0.05, wait) if wait > 0 else 0.0

    def events_add(self, key: str, ts: float) -> None:
        k = self._p + "ev:" + key
        pipe = self._r.pipeline()
        pipe.zadd(k, {f"{ts}:{uuid.uuid4().hex[:6]}": ts})
        pipe.expire(k, 86400)
        pipe.execute()

    def events_since(self, key: str, since: float) -> List[float]:
        k = self._p + "ev:" + key
        self._r.zremrangebyscore(k, "-inf", f"({since}")
        return [float(score) for _m, score in self._r.zrange(k, 0, -1, withscores=True)]

    def events_clear(self, key: str) -> None:
        self._r.delete(self._p + "ev:" + key)

    def slot_acquire(self, key: str, max_slots: int) -> bool:
        if max_slots <= 0:
            return True
        return bool(self._slot(keys=[self._p + "slot:" + key], args=[max_slots]))

    def slot_release(self, key: str) -> None:
        k = self._p + "slot:" + key
        if int(self._r.decr(k)) < 0:
            self._r.set(k, 0)

    def reset(self) -> None:
        keys = list(self._r.scan_iter(match=self._p + "*", count=500))
        if keys:
            self._r.delete(*keys)


class FallbackStore:
    """Redis trước; lỗi kết nối -> RAM cho tới khi Redis trả lời lại (thử lại sau 30 s)."""

    def __init__(self, primary: RedisStore) -> None:
        self.primary, self.local = primary, MemoryStore()
        self.degraded = False
        self._retry_at = 0.0

    def _call(self, name: str, *args):
        if not self.degraded or time.monotonic() >= self._retry_at:
            try:
                out = getattr(self.primary, name)(*args)
                if self.degraded:
                    logger.warning("[SharedState] Redis đã trả lời lại — dùng Redis.")
                self.degraded = False
                return out
            except Exception as exc:  # noqa: BLE001
                if not self.degraded:
                    logger.warning("[SharedState] Redis lỗi (%s) — tạm dùng bộ đếm RAM của tiến trình này.", exc)
                self.degraded, self._retry_at = True, time.monotonic() + 30.0
        return getattr(self.local, name)(*args)

    def hit(self, key, limit, window_s):
        return self._call("hit", key, limit, window_s)

    def events_add(self, key, ts):
        return self._call("events_add", key, ts)

    def events_since(self, key, since):
        return self._call("events_since", key, since)

    def events_clear(self, key):
        return self._call("events_clear", key)

    def slot_acquire(self, key, max_slots):
        return self._call("slot_acquire", key, max_slots)

    def slot_release(self, key):
        return self._call("slot_release", key)

    def reset(self):
        self.local.reset()
        try:
            self.primary.reset()
        except Exception:  # noqa: BLE001
            pass


_STORE: Optional[object] = None
_STORE_LOCK = threading.Lock()


def store():
    """Kho dùng chung của tiến trình: Redis nếu cấu hình REDIS_URL, không thì RAM."""
    global _STORE
    with _STORE_LOCK:
        if _STORE is None:
            url = os.environ.get("VNMATEAI_REDIS_URL") or _configured_url()
            _STORE = FallbackStore(RedisStore(url)) if url else MemoryStore()
            logger.info("[SharedState] Backend: %s", "Redis" if url else "RAM (một tiến trình)")
        return _STORE


def _configured_url() -> str:
    try:
        from mateai.config.loader import settings
        return str(getattr(settings, "REDIS_URL", "") or "")
    except Exception:  # noqa: BLE001
        return ""


def reset() -> None:
    """Xoá mọi số đếm (test, hoặc admin sau sự cố)."""
    store().reset()
