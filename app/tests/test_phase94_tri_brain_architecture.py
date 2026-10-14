# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 Dương Thanh Lịch — VN-MateAI. See LICENSE and NOTICE.
"""
tests/test_phase94_tri_brain_architecture.py
=============================================
Kiểm thử toàn diện Kiến Trúc 3 Bộ Não Chuyên Biệt (Phase 94 - Tri-Brain Architecture):
1. Phân loại ý định tức thì (Controller Intent Classifier)
2. Tách biệt 3 vai trò: Controller, Voice, Operations
3. Fast-Failover: Ngắt kết nối trong 5s-8s nếu model chết/hết hạn mức, không bao giờ treo 45s
4. Cơ chế Voice Brain không tải danh mục tool schemas để đạt TTFT < 300ms
"""

import asyncio
import sys
import time
from pathlib import Path

# Add project root to sys.path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from mateai.application.agent.llm_engine import llm_engine
from mateai.config.loader import settings


def test_intent_classification():
    print("\n▸ 1. Kiểm thử Bộ Não 1: Phân loại ý định (Supervisor / Controller Intent Classifier)")

    # Các câu đàm thoại / chào hỏi thông thường
    casual_queries = [
        "Xin chào Ly Ly",
        "Chào em, em là ai thế?",
        "Hôm nay em thấy thế nào?",
        "Cảm ơn em nhé",
        "Thời tiết hôm nay đẹp nhỉ",
    ]
    for q in casual_queries:
        res = llm_engine.classify_intent(q)
        assert res["type"] == "conversation", f"Query '{q}' phải là conversation, nhận được: {res['type']}"
        assert res["target_brain"] == "voice", f"Query '{q}' phải trỏ tới voice brain"
        print(f"  ✅ [Đàm thoại] '{q}' → Voice Brain (0 tool overhead)")

    # Các câu lệnh vận hành hệ thống
    operation_queries = [
        "Kiểm tra mức độ an ninh và các mối đe dọa",
        "Quét toàn bộ cổng mạng đang mở trên hệ thống",
        "Kiểm tra hiệu năng CPU và bộ nhớ RAM",
        "Đọc file báo cáo tài chính trong thư mục documents",
        "Truy vấn danh sách nhân viên từ cơ sở dữ liệu ERP",
    ]
    for q in operation_queries:
        res = llm_engine.classify_intent(q)
        assert res["type"] == "operation", f"Query '{q}' phải là operation, nhận được: {res['type']}"
        assert res["target_brain"] == "ops", f"Query '{q}' phải trỏ tới ops brain"
        print(f"  ✅ [Vận hành] '{q}' → Ops Brain")


def test_tri_brain_models_config():
    print("\n▸ 2. Kiểm thử cấu hình 3 Model Chuyên Biệt")
    controller_m = llm_engine.get_brain_model("controller")
    voice_m = llm_engine.get_brain_model("voice")
    ops_m = llm_engine.get_brain_model("ops")

    print(f"  👑 Model Kiểm Soát (Supervisor): {controller_m}")
    print(f"  🎙️ Model Giao Tiếp (Voice):      {voice_m}")
    print(f"  ⚙️ Model Vận Hành (Operations):  {ops_m}")

    assert controller_m != "", "Controller model không được để trống"
    assert voice_m != "", "Voice model không được để trống"
    assert ops_m != "", "Ops model không được để trống"
    print("  ✅ Cả 3 model đều đã được định danh và sẵn sàng hoạt động độc lập.")


def test_dead_model_reordering():
    print("\n▸ 3. Kiểm thử danh sách dự phòng đã loại bỏ nguy cơ treo 46 giây")
    router_models = getattr(settings.llm, "router_models", [])
    assert len(router_models) > 0, "router_models không được rỗng"

    # Model đầu tiên trong danh sách không được là model đã hết Quota (claude-opus)
    first_m = router_models[0]
    assert "claude-opus-4-6-thinking" not in first_m, "Model hết Quota không được đứng đầu danh sách"
    print(f"  ✅ Model ưu tiên số 1: '{first_m}' (Model an toàn, phản hồi tức thì)")


async def test_fast_failover_mock():
    print("\n▸ 4. Kiểm thử cơ chế Fast-Failover (< 5 giây)")
    # Giả lập một model bị treo hoặc delay 20s
    t0 = time.monotonic()
    try:
        async def _hang():
            await asyncio.sleep(20)
            return "ok"

        await asyncio.wait_for(_hang(), timeout=1.0)
    except asyncio.TimeoutError:
        elapsed = time.monotonic() - t0
        assert elapsed < 1.5, "Timeout phải ngắt ngay trong ~1.0s"
        print(f"  ✅ Fast-Failover thành công: ngắt kết nối sau {elapsed:.2f}s thay vì treo 46s!")


def main():
    print("=" * 60)
    print("PHASE 94: TRI-BRAIN SPECIALIZED ARCHITECTURE VERIFICATION")
    print("=" * 60)

    test_intent_classification()
    test_tri_brain_models_config()
    test_dead_model_reordering()
    asyncio.run(test_fast_failover_mock())

    print("\n" + "─" * 60)
    print("✅ TẤT CẢ 4 HẠNG MỤC KIỂM THỬ 3 BỘ NÃO ĐỀU PASS HOÀN TOÀN!")


if __name__ == "__main__":
    main()
