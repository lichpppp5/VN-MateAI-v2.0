# Sổ hoạt động xử lý dữ liệu cá nhân

Dành cho: người phụ trách bảo vệ dữ liệu của tổ chức triển khai, và kiểm toán viên.

Căn cứ: Luật Bảo vệ dữ liệu cá nhân 2025 (hiệu lực 01/01/2026) và Nghị định 356/2025/NĐ-CP. Đây không phải ý kiến pháp lý; tổ chức triển khai tự chịu trách nhiệm với vai trò bên kiểm soát dữ liệu.

**Bản đang chạy luôn khớp hệ thống:** quản trị viên xem tại `GET /api/v1/privacy/register`, nguồn là `src/mateai/application/privacy/compliance.py`. Bảng dưới là bản chụp ngày 14/10/2026.

| Hoạt động | Dữ liệu | Mục đích | Cơ sở | Nơi lưu | Thời hạn | Bên nhận |
|---|---|---|---|---|---|---|
| Tài khoản đăng nhập | tên đăng nhập, họ tên, vai trò, phòng ban, băm mật khẩu, MFA | xác thực, phân quyền | hợp đồng lao động / dịch vụ | `vnmate.users` | đến khi xoá tài khoản | không |
| Danh bạ nhân sự (AD / ERP) | họ tên, email, SĐT, chức danh, phòng ban, máy tính | tra cứu nội bộ, giao việc, hỗ trợ IT | lợi ích hợp pháp | `hr.employees`, `hr.computers`, `vnmate.employees` | theo đồng bộ AD; ẩn danh khi có yêu cầu | LLM ngoài chỉ thấy bản **đã che** khi bật che PII (bản thương mại: luôn bật) |
| Hội thoại với trợ lý | câu hỏi, trả lời, kết quả công cụ | trả lời yêu cầu | yêu cầu của người dùng | **RAM**, không lưu bền | hết phiên / khởi động lại | nhà cung cấp LLM (cần DPA) |
| Trace lượt thoại | câu hỏi rút gọn, kênh, thời gian các pha | đo chất lượng, SLA | lợi ích hợp pháp | `vnmate.voice_traces` | 400 ngày (cấu hình được) | không |
| Giọng nói | âm thanh micro | nhận dạng lệnh | đồng ý khi dùng | **không lưu âm thanh** | — | dịch vụ STT khi không dùng STT tại chỗ |
| Nhật ký kiểm toán | người thực hiện, hành động, tham số (đã che), IP | an toàn thông tin, truy vết | nghĩa vụ pháp lý / an toàn hệ thống | `vnmate.audit_logs` (chỉ ghi thêm, chuỗi băm) | theo chính sách lưu trữ; không xoá từng dòng | kiểm toán viên |
| Tác vụ vận hành AI | mục tiêu, người yêu cầu, kết quả | sổ tác vụ, kiểm chứng | lợi ích hợp pháp | `vnmate.op_tasks` | đến khi quản trị viên dọn | không |
| Log ứng dụng | sự kiện, tên người dùng | vận hành, sự cố | lợi ích hợp pháp | `logs/vnmateai.log` (che token / mật khẩu) | 20 MB × 10 tệp | không |

## Quyền của chủ thể dữ liệu

| Yêu cầu | Cách làm | Ghi chú |
|---|---|---|
| **Truy cập**: dữ liệu nào về tôi | `POST /api/v1/privacy/subject/lookup` với tên đăng nhập, email, SĐT hoặc họ tên đầy đủ | Có ghi audit (`PRIVACY_LOOKUP`). Audit chỉ lưu mã băm của định danh. |
| **Xoá** | `POST /api/v1/privacy/subject/erase`, gõ lại định danh vào `confirm` | Ẩn danh hồ sơ nhân sự (giữ khoá liên kết), xoá trace thoại và phiên hội thoại có định danh đó, xoá tài khoản. Không xoá admin cuối cùng. Ghi audit (`PRIVACY_ERASURE`). |
| **Giữ lại** | Nhật ký kiểm toán | Bất biến theo nghĩa vụ an toàn thông tin. Kết quả xoá ghi rõ phần giữ lại để trả lời chủ thể. |

## Chuyển dữ liệu ra nước ngoài

Prompt gửi tới LLM qua gateway (9Router → Gemini / Claude…) là chuyển dữ liệu ra nước ngoài. Biện pháp hiện có:

- **Che dữ liệu cá nhân trước khi gửi:** áp dụng ở bản `commercial`, hoặc khi bật `privacy.external_llm_mask_pii` ở bản `internal`.
- **Chặn model miễn phí** ở bản `commercial`.

Việc tổ chức triển khai cần làm:

- Ký hợp đồng xử lý dữ liệu (DPA) với nhà cung cấp LLM.
- Hoặc dùng model tự host cho dữ liệu `CONFIDENTIAL`.
- Lập hồ sơ đánh giá tác động chuyển dữ liệu ra nước ngoài theo mẫu `mau-danh-gia-tac-dong.md`.
