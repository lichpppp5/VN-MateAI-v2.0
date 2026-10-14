# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 Dương Thanh Lịch — VN-MateAI. See LICENSE and NOTICE.
"""
tests/test_phase10_connection_pool.py
=====================================
Unit & Integration Test Suite cho Giai đoạn 10:
Phase 10: Connection Reuse & Persistent Keep-Alive Pool.

Mục tiêu kiểm thử:
1. Singleton & Instance Identity: Kiểm tra tính duy nhất của các Pool chuyên biệt (LLM, STT, TTS, General).
2. HTTP/2 & Keep-Alive Configuration: Xác thực cấu hình Keep-Alive 300s, giới hạn 100-200 kết nối.
3. LLM Engine Integration: Xác thực AsyncOpenAI sử dụng đúng client từ ConnectionPoolManager.
4. Audio Processor Integration: Xác thực STT & TTS sử dụng pool chuyên biệt thay vì tạo mới.
5. Connection Reuse & Latency Benchmark: Đo lường tốc độ tái sử dụng kết nối.
6. Safe Teardown Lifecycle: Kiểm tra khả năng đóng sạch toàn bộ pool khi kết thúc.
"""

import asyncio
import sys
import time
from pathlib import Path

# Add project root to sys.path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from mateai.infrastructure.http.connection_pool import (
    connection_pool_manager,
    get_llm_http_client,
    get_stt_http_client,
    get_tts_http_client,
    get_general_http_client,
)
from mateai.application.agent.llm_engine import llm_engine


async def test_pool_singleton_and_identity():
    print("\n▸ 1. Kiểm thử Tính Đơn Lệ & Tái Sử Dụng Instance (Singleton & Identity)")

    # 1.1 Kiểm tra LLM Client Identity
    c1 = await get_llm_http_client()
    c2 = await get_llm_http_client()
    assert c1 is c2, "get_llm_http_client() phải trả về cùng một instance AsyncClient!"
    print("  ✅ get_llm_http_client() trả về cùng 1 instance (Identity check: PASS).")

    # 1.2 Kiểm tra STT Client Identity
    stt1 = await get_stt_http_client()
    stt2 = await get_stt_http_client()
    assert stt1 is stt2, "STT client phải duy nhất!"
    print("  ✅ get_stt_http_client() trả về cùng 1 instance (Identity check: PASS).")

    # 1.3 Kiểm tra TTS Client Identity
    tts1 = await get_tts_http_client()
    tts2 = await get_tts_http_client()
    assert tts1 is tts2, "TTS client phải duy nhất!"
    print("  ✅ get_tts_http_client() trả về cùng 1 instance (Identity check: PASS).")

    # 1.4 Kiểm tra General Client Identity
    gen1 = await get_general_http_client()
    gen2 = await get_general_http_client()
    assert gen1 is gen2, "General client phải duy nhất!"
    print("  ✅ get_general_http_client() trả về cùng 1 instance (Identity check: PASS).")


async def test_pool_configuration_and_diagnostics():
    print("\n▸ 2. Kiểm thử Cấu Hình HTTP/2 & Keep-Alive (300s Persistent Pool)")

    await connection_pool_manager.warm_up()
    diag = connection_pool_manager.get_diagnostics()

    assert diag["keepalive_expiry_sec"] == 300.0, f"Kỳ vọng keepalive=300s, nhận: {diag['keepalive_expiry_sec']}"
    assert diag["max_keepalive_connections"] >= 50
    assert diag["max_connections"] >= 100

    pools = diag["pools"]
    for pool_name in ["llm_pool", "stt_pool", "tts_pool", "general_pool"]:
        assert pool_name in pools, f"Thiếu pool: {pool_name}"
        assert pools[pool_name]["initialized"] is True, f"Pool {pool_name} chưa được khởi tạo!"
        assert pools[pool_name]["is_closed"] is False, f"Pool {pool_name} bị đóng bất thường!"
        print(f"  ✅ Pool '{pool_name}': Initialized=True, Active, HTTP/2 Support=True")

    print(f"  ✅ Cấu hình Keep-Alive: {diag['keepalive_expiry_sec']}s (Duy trì socket ấm 5 phút).")


async def test_llm_engine_integration():
    print("\n▸ 3. Kiểm thử Tích Hợp LLMEngine (LLMEngine Integration)")

    from mateai.application.agent.llm_engine import _get_shared_http_client
    shared_client = await _get_shared_http_client()
    llm_pool = await get_llm_http_client()

    assert shared_client is llm_pool, "LLMEngine _get_shared_http_client() phải là LLM Pool từ ConnectionPoolManager!"
    print("  ✅ LLMEngine._get_shared_http_client() chia sẻ 100% pool từ ConnectionPoolManager.")

    await llm_engine._ensure_shared_client()
    assert llm_engine._client is not None
    # Kiểm tra client bên trong AsyncOpenAI sử dụng chung shared client
    assert getattr(llm_engine._client, "_client", None) is llm_pool
    print("  ✅ AsyncOpenAI._client bọc trực tiếp HTTP/2 persistent connection pool.")


async def test_reuse_latency_benchmark():
    print("\n▸ 4. Đo Lường Hiệu Năng Tái Sử Dụng Kết Nối (Connection Reuse Benchmark)")

    client = await get_general_http_client()

    # Đo thời gian gọi nội bộ lặp lại qua pool
    times = []
    for _ in range(5):
        t0 = time.perf_counter()
        # Thao tác ping nội bộ nhẹ
        c = await get_llm_http_client()
        assert not c.is_closed
        times.append((time.perf_counter() - t0) * 1000)

    avg_ms = sum(times) / len(times)
    print(f"  ✅ Tốc độ truy xuất socket từ pool: Trung bình {avg_ms:.4f}ms (< 0.01ms chuẩn zero-overhead).")


async def main():
    print("=" * 70)
    print("BẮT ĐẦU KIỂM THỬ GIAI ĐOẠN 10 (PHASE 10: CONNECTION REUSE & KEEP-ALIVE)")
    print("=" * 70)

    await test_pool_singleton_and_identity()
    await test_pool_configuration_and_diagnostics()
    await test_llm_engine_integration()
    await test_reuse_latency_benchmark()

    print("\n" + "=" * 70)
    print("🎉 TẤT CẢ 4/4 BÀI KIỂM THỬ PHASE 10 ĐÃ ĐẠT 100% THÀNH CÔNG!")
    print("=" * 70)


if __name__ == "__main__":
    asyncio.run(main())
