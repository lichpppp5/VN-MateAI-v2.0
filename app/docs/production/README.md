# VN-MateAI — Tài liệu Production

Dành cho: quản trị viên IT triển khai và vận hành VN-MateAI trong doanh nghiệp.

| Tài liệu | Nội dung |
|---|---|
| **[production-setup-guide.md](production-setup-guide.md)** | **Hướng dẫn triển khai production từng bước**: máy chủ, Docker (PostgreSQL / Redis / S3 / OTel), `config.json`, biến môi trường, TLS + tường lửa, chạy như dịch vụ, kết nối Telegram / robot / máy trạm / email / AD, sao lưu, nâng cấp, kiểm tra go-live |
| [deployment.md](deployment.md) | Yêu cầu, cài đặt, cấu hình bắt buộc, biến môi trường, cổng mạng, chứng chỉ, khởi động, health probe, thiết bị & worker |
| [security.md](security.md) | Mô hình xác thực/phân quyền theo kênh, HITL, audit, bí mật, rủi ro còn lại đã biết |
| [owner-todo.md](owner-todo.md) | **Việc chủ dự án cần làm** (mật khẩu, model LLM, Telegram, robot, chứng chỉ) |
| [runbook.md](runbook.md) | Xử lý sự cố theo dấu hiệu → kiểm tra → xử lý → xác minh |
| [readiness-score.md](readiness-score.md) | Điểm sẵn sàng production theo hạng mục, có bằng chứng |
| [operations.md](operations.md) | Sao lưu/khôi phục, log, giám sát, xử lý sự cố thường gặp (LLM, Telegram, thiết bị) |
| [../architecture/dependency-rules.md](../architecture/dependency-rules.md) | Quy tắc kiến trúc và trạng thái tuân thủ (được test giữ) |
| [../migration/production-refactor-plan.md](../migration/production-refactor-plan.md) | Nhật ký từng phase refactor: lỗi tìm thấy, cách sửa, số đo thật |

## Trạng thái sẵn sàng

Xem [readiness-score.md](readiness-score.md) (cập nhật theo bằng chứng) và [../architecture/current-system-map.md](../architecture/current-system-map.md).
