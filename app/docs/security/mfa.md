# Xác thực hai lớp (MFA) bằng TOTP

Mật khẩu đúng **chưa đủ** để vào hệ thống khi tài khoản đã bật MFA. Dùng được với Google Authenticator, Microsoft Authenticator, Authy, 1Password… (chuẩn RFC 6238: 6 số, đổi mỗi 30 giây).

## Người dùng

**Bật:** tab **Bảo mật Hệ thống → Xác thực hai lớp → Bật MFA** → trong ứng dụng xác thực thêm tài khoản bằng **khoá thủ công** (hoặc dán liên kết `otpauth://`) → nhập mã 6 số để xác nhận → **lưu 10 mã khôi phục** (chỉ hiện một lần; mỗi mã dùng một lần).

**Đăng nhập:** nhập mật khẩu → hệ thống hỏi mã 6 số (hoặc một mã khôi phục dạng `ABCDE-FGHIJ`). Sai quá nhiều lần bị khoá tạm như đăng nhập sai mật khẩu.

**Tắt:** cần **mật khẩu + một mã MFA hợp lệ**. **Mất điện thoại:** dùng mã khôi phục; hết mã thì nhờ quản trị viên gỡ MFA (`POST /api/v1/users/{id}/mfa/reset`, có ghi nhật ký kiểm toán).

## Quản trị — bắt buộc theo vai trò

`config.json → security.require_mfa_roles: ["admin"]` (mặc định rỗng = tuỳ chọn). Người thuộc vai trò này mà chưa bật MFA: đăng nhập xong chỉ vào được **màn cài MFA** (token `mfa_enroll`, 15 phút, không dùng được cho bất kỳ API / WebSocket nào khác); cài xong mới có phiên đầy đủ. Họ **không tự tắt** MFA được. Nên bật quy tắc này **sau khi** chính các admin đã cài MFA xong, để khỏi tự khoá mình; nếu lỡ khoá, đặt `require_mfa_roles` về `[]` trong `config.json` rồi khởi động lại.

## Thiết kế an toàn

- Hai lớp token trung gian: `scope=mfa` (5 phút, sau khi đúng mật khẩu) và `scope=mfa_enroll`. **Mọi đường kiểm token hiện có tự từ chối chúng** (middleware, dependency, WebSocket) vì `decode_access_token` mặc định trả `None` cho token có `scope`.
- Mỗi bước 30 s chỉ dùng **một lần** (chống phát lại); chấp nhận lệch ±1 bước.
- Mã bí mật mã hoá khi lưu (cùng cơ chế với khoá trong `config.json`), không bao giờ trả lại sau lần cài đầu, không có trong danh sách tài khoản; mã khôi phục lưu dạng băm.
- Tài khoản đăng nhập SSO dùng MFA của IdP, không cài MFA cục bộ.

## Chưa làm

Mã QR (hiện nhập khoá thủ công / liên kết otpauth); khoá bảo mật phần cứng (WebAuthn / FIDO2); nhớ thiết bị tin cậy 30 ngày; nút gỡ MFA trong bảng tài khoản (hiện gọi API).
