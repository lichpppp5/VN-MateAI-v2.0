# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 Dương Thanh Lịch — VN-MateAI. See LICENSE and NOTICE.
"""
tests/test_shared_state.py
==========================
Prompt cuối §67 / §145 / §164: trạng thái ngắn hạn dùng chung giữa nhiều tiến trình.

Một kho (`infrastructure/cache/shared_state`) thay ba bộ đếm riêng trong RAM: giới hạn tần suất,
bộ đếm chế độ khẩn cấp, khoá đăng nhập. Backend RAM (mặc định) và Redis (khi có REDIS_URL) chạy
CÙNG bộ test. Redis thật lấy từ `.infra/redis` (scripts/dev_infra.py) hoặc biến
VNMATEAI_TEST_REDIS_URL — không có thì test Redis bị bỏ qua, KHÔNG giả lập.
Redis hỏng giữa chừng -> tự lùi về RAM, có cảnh báo (§98: degrade gracefully).
"""
from __future__ import annotations

import os
import socket
import subprocess
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
REDIS_EXE = ROOT / ".infra" / "redis" / "redis-server.exe"


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture(scope="module")
def redis_url():
    url = os.environ.get("VNMATEAI_TEST_REDIS_URL")
    if url:
        yield url
        return
    if not REDIS_EXE.exists():
        pytest.skip("Không có Redis thật (.infra/redis hoặc VNMATEAI_TEST_REDIS_URL) — chạy scripts/dev_infra.py fetch")
    port = _free_port()
    proc = subprocess.Popen([str(REDIS_EXE), "--port", str(port), "--save", "", "--appendonly", "no"],
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        for _ in range(50):
            try:
                socket.create_connection(("127.0.0.1", port), timeout=0.2).close()
                break
            except OSError:
                time.sleep(0.1)
        yield f"redis://127.0.0.1:{port}/0"
    finally:
        proc.terminate()
        proc.wait(timeout=10)


@pytest.fixture(params=["memory", "redis"])
def store(request):
    from mateai.infrastructure.cache import shared_state
    if request.param == "memory":
        st = shared_state.MemoryStore()
    else:
        st = shared_state.RedisStore(request.getfixturevalue("redis_url"), prefix=f"t{time.time_ns()}:")
    yield st
    st.reset()


def test_sliding_hit(store):
    assert [store.hit("a", 2, 60) for _ in range(2)] == [0.0, 0.0]
    wait = store.hit("a", 2, 60)
    assert 0 < wait <= 60
    assert store.hit("b", 2, 60) == 0.0                     # khoá khác độc lập
    assert store.hit("short", 1, 0.3) == 0.0
    time.sleep(0.35)
    assert store.hit("short", 1, 0.3) == 0.0                # cửa sổ trượt hết hạn


def test_events_and_slots(store):
    now = time.time()
    store.events_add("login:x", now - 1000)
    store.events_add("login:x", now)
    assert store.events_since("login:x", now - 10) == [pytest.approx(now)]
    store.events_clear("login:x")
    assert store.events_since("login:x", 0) == []
    assert store.slot_acquire("ws:u", 2) and store.slot_acquire("ws:u", 2)
    assert store.slot_acquire("ws:u", 2) is False
    store.slot_release("ws:u")
    assert store.slot_acquire("ws:u", 2) is True


def test_two_processes_share_limits_via_redis(redis_url):
    """Hai 'tiến trình' (hai client độc lập) dùng chung một giới hạn — điều RAM không làm được."""
    from mateai.infrastructure.cache.shared_state import RedisStore
    prefix = f"p{time.time_ns()}:"
    a, b = RedisStore(redis_url, prefix=prefix), RedisStore(redis_url, prefix=prefix)
    try:
        assert a.hit("voice:u1", 2, 60) == 0.0 and b.hit("voice:u1", 2, 60) == 0.0
        assert a.hit("voice:u1", 2, 60) > 0 and b.hit("voice:u1", 2, 60) > 0
    finally:
        a.reset()


def test_redis_outage_degrades_to_memory(caplog):
    import logging
    from mateai.infrastructure.cache import shared_state
    # Không phụ thuộc mức log toàn cục (test khác có thể đặt root = CRITICAL lúc import).
    caplog.set_level(logging.WARNING, logger="mateai.infrastructure.cache.shared_state")
    st = shared_state.FallbackStore(shared_state.RedisStore(f"redis://127.0.0.1:{_free_port()}/0",
                                                            socket_timeout=0.2))
    assert st.hit("k", 1, 60) == 0.0 and st.hit("k", 1, 60) > 0      # vẫn giới hạn, bằng RAM
    assert st.degraded is True
    assert any("Redis" in r.message for r in caplog.records)


def test_default_backend_is_memory_without_redis_url(monkeypatch):
    from mateai.config.loader import settings
    from mateai.infrastructure.cache import shared_state
    monkeypatch.delenv("VNMATEAI_REDIS_URL", raising=False)
    monkeypatch.setattr(settings, "REDIS_URL", "")
    shared_state._STORE = None
    assert isinstance(shared_state.store(), shared_state.MemoryStore)


def test_redis_url_is_treated_as_a_secret():
    """REDIS_URL chứa mật khẩu: phải được che ở API cấu hình và mã hoá khi lưu."""
    from mateai.config.secret_box import is_secret as is_secret_field
    from mateai.interfaces.http.secret_masking import _mask_secrets
    assert is_secret_field("REDIS_URL")
    masked = _mask_secrets({"REDIS_URL": "redis://:matkhau@127.0.0.1:6379/0"})
    assert "matkhau" not in str(masked)
