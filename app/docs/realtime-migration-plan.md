# KẾ HOẠCH NÂNG CẤP REALTIME VOICE PIPELINE (PHASE 0 - MIGRATION PLAN)
**Dự án**: VN-MateAI — Realtime Voice Performance Revamp
**Mục tiêu**: Lộ trình chuyển đổi từng bước không làm gián đoạn hệ thống.

---

## 1. LỘ TRÌNH 13 GIAI ĐOẠN (PHASE DECOMPOSITION)

| Giai đoạn | Tên Phase | Trọng tâm công việc | Trạng thái |
| :--- | :--- | :--- | :--- |
| **Phase 0** | Architecture Audit & Baseline | Kiểm toán toàn diện, đo lường độ trễ cơ sở, lập hồ sơ kiến trúc | ✅ **Hoàn thành** |
| **Phase 1** | Realtime WebSocket Foundation | Nền tảng WebSocket, Event Protocol chuẩn, Tracing, Binary/Base64 Frame, Cancellation, Lifecycle | ✅ **Hoàn thành** |
| **Phase 2** | LLM Streaming Token Layer | Chuẩn hóa provider stream, Event Bus đẩy token delta | ✅ **Hoàn thành** |
| **Phase 3** | Sentence Boundary Streamer | Bộ đệm tách câu thông minh tiếng Việt, chống ngắt sai số/IP | ✅ **Hoàn thành** |
| **Phase 4** | Streaming TTS Pipeline | Worker hàng đợi TTS gối đầu, phát âm thanh theo thứ tự câu (In-Order Guaranteed) | ✅ **Hoàn thành** |
| **Phase 5** | Fast Command Router | Khởi chạy lệnh tất định không qua LLM, phản hồi âm thanh < 100ms (TTFT < 1ms) | ✅ **Hoàn thành** |
| **Phase 6** | Pre-warmed Acoustic ACK Cache | Mở rộng kho câu đệm tức thì (0ms retrieval) cho mọi ngữ cảnh | ✅ **Hoàn thành** |
| **Phase 7** | Agent Loop & Tool Pruning | Tối ưu hóa số vòng lặp LLM, loại bỏ LLM lần 2 khi không cần (Tiết kiệm ~2.5s) | ✅ **Hoàn thành** |
| **Phase 8** | Dynamic Skill Loading | Chỉ nạp schema công cụ phù hợp với ý định (Pre-indexed Taxonomy) | ✅ **Hoàn thành** |
| **Phase 9** | History & Context Pruning | Tóm tắt hội thoại + Sliding window thoại + Nén lọc rác bảng/code | ✅ **Hoàn thành** |
| **Phase 10**| Connection Reuse & Keep-Alive | Tận dụng HTTP/2 persistent connection pool (300s socket) | ✅ **Hoàn thành** |
| **Phase 11**| Binary Frame Audio Transport | Giảm tải Base64, truyền frame âm thanh nhị phân (Zero Base64 overhead) | ✅ **Hoàn thành** |
| **Phase 12**| Cancellation & Barge-In | Hủy tác vụ đang chạy khi có lệnh mới hoặc người dùng ngắt lời | ✅ **Hoàn thành** |

---

## 2. NGUYÊN TẮC THI CÔNG BẮT BUỘC (CRITICAL RULES)

1. **Tuân thủ đúng phạm vi từng Phase**:
   - Ở **Phase 1**, CHỈ xây dựng tầng nền tảng WebSocket (`Realtime Voice WebSocket Foundation`).
   - **TUYỆT ĐỐI CHƯA CAN THIỆP VÀO CỐT LÕI CỦA LLM HOẶC TTS** (giữ nguyên logic LLM/TTS hiện tại, bọc bằng protocol sự kiện).
2. **Bảo tồn tính năng & An ninh**:
   - Zero-Trust, RBAC Guard, Mask Sensitive Data phải giữ nguyên vẹn.
   - Giữ tương thích ngược hoàn toàn với REST API cũ (`/api/v1/voice-command`).
3. **Đo lường & Kiểm chứng**:
   - Mỗi phase phải có unit/integration test độc lập trước khi chuyển giao.
