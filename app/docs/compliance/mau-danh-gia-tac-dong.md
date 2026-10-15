# Mẫu đánh giá tác động xử lý dữ liệu cá nhân (DPIA)

Dành cho: tổ chức triển khai, điền trước khi đưa hệ thống vào vận hành. Đây là mẫu tham khảo, không phải ý kiến pháp lý; đối chiếu với biểu mẫu chính thức hiện hành theo Luật Bảo vệ dữ liệu cá nhân 2025 và Nghị định 356/2025/NĐ-CP.

Các mục đã điền sẵn là thông tin về hệ thống (lấy từ `so-hoat-dong-xu-ly.md`). Các mục **[điền]** là việc của tổ chức.

## 1. Thông tin chung

| Mục | Nội dung |
|---|---|
| Bên kiểm soát dữ liệu | **[điền]**: tên tổ chức, người đại diện, đầu mối bảo vệ dữ liệu |
| Bên xử lý | **[điền]**: đơn vị vận hành hệ thống; nhà cung cấp LLM, STT, TTS |
| Hệ thống | Trợ lý AI vận hành CNTT (phiên bản, edition `commercial` / `internal`) |
| Phạm vi triển khai | **[điền]**: số người dùng, chi nhánh, hệ thống được kết nối |

## 2. Mô tả xử lý

- Loại dữ liệu và nơi lưu: xem bảng trong `so-hoat-dong-xu-ly.md`.
- Dữ liệu nhạy cảm: hệ thống không chủ động thu thập. Lương, CCCD, STK có thể xuất hiện trong kết quả tra cứu ERP/HR và bị che theo vai trò trước khi tới AI. **[điền]**: nguồn nào có dữ liệu nhạy cảm.
- Luồng ra ngoài: prompt tới LLM (qua gateway), âm thanh tới STT, văn bản tới TTS. **[điền]**: nhà cung cấp cụ thể, quốc gia lưu trữ, có DPA chưa.

## 3. Đánh giá cần thiết và tương xứng

| Câu hỏi | Trả lời |
|---|---|
| Mục đích có rõ ràng, chính đáng? | **[điền]** |
| Có cách đạt mục đích với ít dữ liệu hơn? | Hội thoại không lưu bền. Trace giữ 400 ngày (giảm được bằng `observability.voice_trace_retention_days`). **[điền]**: trường HR nào thật sự cần. |
| Người dùng được thông báo đang làm việc với AI? | **[điền]**: thông báo trong quy chế nội bộ / màn hình đăng nhập |

## 4. Rủi ro và biện pháp

| Rủi ro | Khả năng / tác động | Biện pháp có sẵn | Biện pháp bổ sung **[điền]** |
|---|---|---|---|
| Dữ liệu cá nhân lộ qua LLM bên ngoài | | Che PII trước khi gửi (bản thương mại), chặn model miễn phí | DPA, model tự host |
| AI thực hiện thao tác sai trên dữ liệu | | Cổng chính sách L0–L5, duyệt người, duyệt hai người (tuỳ chọn), kill switch, audit bất biến | |
| Truy cập trái phép | | MFA bắt buộc cho admin, bắt đổi mật khẩu ban đầu, RBAC + ABAC phòng ban | |
| Không đáp ứng quyền xoá / truy cập | | API tra cứu và xoá theo chủ thể, có audit | Quy trình tiếp nhận yêu cầu |
| Lưu quá thời hạn | | Hạn lưu trace, log xoay vòng | Chính sách lưu trữ audit |

## 5. Kết luận và phê duyệt

| Mục | Nội dung |
|---|---|
| Rủi ro còn lại | **[điền]** |
| Quyết định | Triển khai / Triển khai có điều kiện / Không triển khai |
| Người phê duyệt, ngày | **[điền]** |
| Lần rà soát tiếp theo | **[điền]**: ít nhất mỗi năm, hoặc khi thêm nguồn dữ liệu hay nhà cung cấp mới |
