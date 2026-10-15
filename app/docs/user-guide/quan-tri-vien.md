# Hướng dẫn cho quản trị viên (admin)

Bạn thấy mọi mục và là người duy nhất được **duyệt việc rủi ro**, quản lý tài khoản, cấu hình và bảo mật. Tài khoản quản trị
bắt buộc xác thực hai lớp. Tài liệu kỹ thuật: `docs/production/runbook.md` (vận hành), `docs/security/security-architecture.md`
(kiến trúc an ninh).

## Duyệt việc

Khi trợ lý cần làm việc có rủi ro, bạn nhận yêu cầu ở ba nơi: biểu tượng **✉ Hộp thư & Chỉ thị điều hành** trên thanh đầu
trang, băng vàng đầu **Bảng Điều Khiển**, và tin Telegram (nếu đã cấu hình `telegram.admin_chat_ids`).

Mỗi yêu cầu ghi: việc gì, ai yêu cầu, mức rủi ro (1–5), giải thích vì sao cần duyệt. Bấm **Đồng ý** hoặc **Từ chối**.
- Yêu cầu tự hết hạn sau 15 phút nếu không ai quyết; hệ thống không tự cho phép.
- Việc **mức 5** (xoá cơ sở dữ liệu, sao lưu…) bị chặn với mọi người, kể cả khi bạn đồng ý.
- Kịch bản vận hành: một lần duyệt cho cả kế hoạch; định nghĩa được "đóng băng" — sửa kịch bản sau khi xin duyệt không làm
  thay đổi thứ sẽ chạy.

**Hai tuỳ chọn cho doanh nghiệp có nhiều quản trị viên** (Cấu Hình Hệ Thống, mục `security`, mặc định tắt vì một quản trị viên
sẽ không duyệt được gì):
- `dual_approval_min_risk` (ví dụ `4`): việc từ mức này cần **hai người khác nhau** duyệt.
- `separation_of_duties: true`: người yêu cầu, người soạn / sửa kịch bản không được tự duyệt.

## Tài khoản

**Trung Tâm Bảo Mật → khối Tài khoản**: tạo tài khoản, đặt vai trò, phòng ban, cấp bảo mật. Mật khẩu bạn đặt cho người khác là mật
khẩu tạm — họ phải đổi ở lần đăng nhập đầu.

- **Quên mật khẩu**: yêu cầu hiện trong ✉ Hộp thư. Chọn **Cấp mật khẩu tạm** (sau khi đã xác minh người yêu cầu qua điện
  thoại / gặp trực tiếp) hoặc **Bỏ qua**.
- **Mất điện thoại xác thực**: ở dòng của tài khoản đó, bấm nút điện thoại gạch chéo (**Gỡ xác thực hai lớp**); họ cài lại ở lần đăng nhập sau.
- **Chính bạn bị khoá** (quên mật khẩu hoặc mất cả điện thoại lẫn mã khôi phục): trên máy chủ, chạy
  `python scripts/reset_password.py admin --reset-mfa`.

## Cấu hình

**Cấu Hình Hệ Thống**: cổng mô hình AI, giọng đọc, kênh cảnh báo (Telegram, Teams, Email…), thương hiệu hiển thị (tên, logo,
màu). Mọi thay đổi vào nhật ký kiểm toán và lịch sử cấu hình. Khoá API được mã hoá khi lưu.

## Bảo mật và kiểm toán

**Trung Tâm Bảo Mật**:
- **Kiểm soát tự trị**: công tắc dừng khẩn cấp (AI chỉ còn giám sát, không hành động), tắt từng tác nhân hoặc công cụ, giới
  hạn số lần gọi mỗi lượt.
- **Nhật ký kiểm toán**: chỉ thêm, không sửa / xoá được. **Kiểm tra toàn vẹn** tính lại chuỗi băm và so với các điểm neo đã
  lưu ra ổ khác và S3. **Xuất báo cáo** CSV hoặc HTML in được cho đoàn kiểm toán.
- **Thiết bị**: robot và máy trạm dùng token riêng; thu hồi được từng thiết bị và các quyền đã nhớ của nó.

**Chính Sách & Tuân Thủ**: sổ hoạt động xử lý dữ liệu cá nhân, tra cứu và xoá dữ liệu theo yêu cầu của chủ thể (Nghị định 13),
bản quyền và mức sử dụng so với hạn mức.

## Sao lưu và nâng cấp

- Sao lưu tự động hằng đêm (cơ sở dữ liệu, cấu hình, khoá) và tự chạy bù khi bản mới nhất cũ hơn 26 giờ. Lỗi sao lưu được
  gửi cảnh báo qua kênh đã cấu hình (Telegram, Teams, Email…).
- Nâng cấp bản Docker: giải nén gói mới đè lên thư mục cũ (giữ `.env`), chạy `./install.sh`. Cấu trúc cơ sở dữ liệu tự nâng cấp
  và **tự sao lưu trước**; lỗi sao lưu thì không nâng cấp.
