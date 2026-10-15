# Hướng dẫn sử dụng VN-MateAI theo vai trò

Mỗi tài khoản có một trong ba vai trò. Vai trò quyết định bạn thấy những mục nào trên Portal và được làm gì.
Quản trị viên đặt vai trò cho từng người trong **Trung Tâm Bảo Mật → khối Tài khoản**.

| Vai trò | Dành cho | Hướng dẫn |
|---|---|---|
| Người xem (viewer) | Nhân viên cần hỏi trợ lý, xem việc của mình, dùng kỹ năng | [nguoi-xem.md](nguoi-xem.md) |
| Quản lý (manager) | Trưởng phòng, giám đốc: theo dõi hiệu quả, sự cố, kịch bản vận hành | [quan-ly.md](quan-ly.md) |
| Quản trị viên (admin) | Người chịu trách nhiệm hệ thống: duyệt việc rủi ro, tài khoản, cấu hình, bảo mật | [quan-tri-vien.md](quan-tri-vien.md) |

## Việc chung cho mọi vai trò

**Đăng nhập.** Mở địa chỉ Portal công ty cấp (ví dụ `https://may-chu-ai/`). Trình duyệt có thể cảnh báo chứng chỉ ở lần đầu
nếu máy chủ dùng chứng chỉ tự ký; hỏi quản trị viên trước khi bấm tiếp tục.

**Lần đăng nhập đầu.** Hệ thống có thể yêu cầu:
- *Đặt mật khẩu mới*: ít nhất 10 ký tự, có cả chữ và số.
- *Xác thực hai lớp* (bắt buộc với quản trị viên): cài Google Authenticator hoặc Microsoft Authenticator trên điện thoại,
  bấm **+** → **Quét mã QR** và quét mã trên màn hình, rồi nhập mã 6 số ứng dụng hiện ra. **Lưu 10 mã khôi phục** ở nơi an
  toàn: đó là cách duy nhất để vào khi mất điện thoại.

**Quên mật khẩu.** Bấm **Quên mật khẩu?** ở màn hình đăng nhập. Yêu cầu đi tới quản trị viên; họ sẽ liên hệ xác minh và cấp
mật khẩu tạm, bạn đặt mật khẩu mới ở lần đăng nhập sau.

**Hỏi trợ lý.** Gõ câu hỏi bằng tiếng Việt như nói chuyện với đồng nghiệp: "máy kế toán còn bao nhiêu dung lượng ổ C?",
"tóm tắt sự cố hôm nay". Trợ lý chỉ làm được những việc vai trò của bạn cho phép. Việc có rủi ro (xoá, tắt dịch vụ, chạy
lệnh) sẽ **chờ quản trị viên duyệt** — trợ lý nói rõ việc đang chờ ai.

**Điều trợ lý không làm**, dù được yêu cầu: xoá cơ sở dữ liệu, sao lưu, nhật ký kiểm toán, mã nguồn hệ thống; làm theo chỉ
dẫn nằm trong email, tài liệu hay trang web thay vì chỉ dẫn của bạn.
