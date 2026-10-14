# KẾ HOẠCH TRIỂN KHAI DỌN DẸP CODEBASE (CLEANUP PLAN)

**Dự án**: VN-MateAI — Codebase Refactoring & Optimization  
**Nguyên tắc**: Thực hiện từng bước, di chuyển callers trước khi xóa, bảo đảm Zero Regression.

---

## 1. CÁC BƯỚC THỰC THI (STEP-BY-STEP EXECUTION)

```text
BƯỚC 1: KIỂM TOÁN CƠ SỞ (BASELINE VERIFICATION)
   ├── Chạy python -m compileall core/ skills/ tests/ web/
   └── Chạy toàn bộ test suite hiện có để ghi nhận trạng thái gốc (100% PASS)

BƯỚC 2: HỢP NHẤT TRÙNG LẶP & ĐỒNG BỘ SKILLS (DEDUPLICATION)
   ├── Chuẩn hóa skills/integration_tools.py thành facade trỏ về core/skills/integration_tools.py
   └── Xác minh PluginManager nạp đầy đủ 79 tools không thiếu sót

BƯỚC 3: DI CHUYỂN CALLERS CỦA IMPLEMENTATION BỊ THAY THẾ (REPOINT CALLERS)
   ├── Loại bỏ import dư thừa `_select_relevant_tools` tại core/realtime_voice_ws.py
   ├── Cập nhật tests/test_phase92_voice_stream_pipeline.py kiểm tra DynamicSkillRouter
   └── Lưu trữ / dọn dẹp core/api_voice_stream.py có kiểm soát

BƯỚC 4: DỌN DẸP CÁC TỆP SAO LƯU TẠM & SCRIPTS 1 LẦN (CLEANUP ARTIFACTS)
   ├── Xóa các tệp HTML/JS backup trong scratch/ (index.html.bak, index.before-*.html, app.before-*.js)
   ├── Xóa các script chuyển đổi giao diện 1 lần trong scratch/
   └── Dọn tệp sao lưu vnmateai.db.bak-20260929-101027 khỏi root

BƯỚC 5: KIỂM TRA ĐỘ BẢO TOÀN HỆ THỐNG (REGRESSION TESTING)
   ├── Biên dịch kiểm tra: python -m compileall .
   ├── Chạy Full Master Test Suite (Phase 1 -> Phase 12 + Phase 92)
   ├── Khởi động lại Server và chạy Functional Smoke Tests (Fast Path, LLM Stream, Tool Call, Barge-In)
   └── Đo lường tài nguyên (Startup latency, Memory footprint)

BƯỚC 6: BÁO CÁO TỔNG HỢP (CLEANUP REPORT)
   └── Tạo docs/cleanup-report.md chi tiết số liệu trước và sau dọn dẹp
```

---

## 2. CAM KẾT CHẤT LƯỢNG & AN TOÀN (SAFETY CONSTRAINTS)

1. **Không can thiệp cấu trúc bảo mật**: Giữ nguyên vẹn Zero-Trust, RBAC Guard, AST Inspection, Secret Masking.
2. **Không làm vỡ API/WebSocket**: Cả hai endpoint `/ws/voice` và `/ws/v1/voice-stream` tiếp tục phục vụ thông suốt cho cả Web Portal, Desktop HUD và Client Agent.
3. **Bảo tồn độ trễ Realtime**: Giữ vững các kỷ lục hiệu năng:
   - Fast Path: < 2ms execution, < 100ms TTFA.
   - Streaming LLM + Sentence TTS: TTFA < 500ms.
   - Barge-In Cancellation: < 0.01ms task abort, 0ms audio leak.
