# Cấu hình: mã hoá khoá, kiểm tra, lịch sử và khôi phục

## Mã hoá khoá bí mật trong `config.json`

Từ 2026-10-05, mọi trường bí mật trong `config.json` được lưu **mã hoá** dưới dạng `enc:v1:…`. Danh sách trường nằm trong `mateai.config.secret_box.SECRET_FIELD_NAMES`, gồm:
- API key 9Router / Direct / Groq / ElevenLabs;
- token Telegram;
- mật khẩu SMTP;
- URL webhook Teams / Slack;
- client secret.

Thuật toán là Fernet (AES-128-CBC + HMAC-SHA256).

- **Lúc khởi động:** khoá nào còn dạng chữ thường sẽ tự được mã hoá. Máy chủ đọc thử lại để kiểm tra trước khi ghi đè file.
- **Trong ứng dụng:** mọi chỗ đọc cấu hình đều qua `mateai.config.loader` (RULE-013), nên luôn thấy khoá thật. Không cần sửa code nào khác.
- **Chuỗi mã hoá giải không được** (sai khoá): máy chủ coi trường đó là **trống** và ghi lỗi vào log. Máy chủ không sập, nhưng dịch vụ dùng khoá đó sẽ báo chưa cấu hình.

### Khoá giải mã — PHẢI sao lưu

| Nguồn | Khi nào dùng |
|---|---|
| Biến môi trường `VNMATEAI_CONFIG_KEY` | Khuyên dùng cho máy chủ thật: khoá không nằm trên đĩa cùng `config.json`. |
| `certs/config_secret.key` | Mặc định. Tự sinh ở lần chạy đầu. Không commit (thư mục `certs/`). |

**Mất khoá này thì mọi khoá trong `config.json` không đọc được nữa** và phải nhập lại. Luôn sao lưu `certs/config_secret.key` **cùng** `config.json`, nhưng cất ở chỗ khác.

Chuyển khoá sang biến môi trường: đọc nội dung `certs/config_secret.key`, đặt vào `VNMATEAI_CONFIG_KEY`, khởi động lại máy chủ, rồi xoá file.

Tắt mã hoá (không khuyên dùng): đặt `VNMATEAI_CONFIG_ENCRYPTION=off`. Máy chủ vẫn đọc được các giá trị đã mã hoá, chỉ thôi mã hoá khoá mới.

Bản sao `certs/config.json.pre-encrypt.bak` được tạo trước lần mã hoá đầu. File này **chứa khoá dạng chữ thường**: xoá nó sau khi đã kiểm tra hệ thống chạy bình thường.

## Kiểm tra trước khi lưu

`POST /api/v1/config` từ chối cấu hình sai và trả HTTP 400 kèm lý do; khi bị từ chối, **không có gì được ghi**. Các trường hợp bị từ chối:
- URL không hợp lệ;
- tốc độ đọc ngoài khoảng −50..100, âm lượng ngoài 0..100;
- engine TTS / ASR không tồn tại; ElevenLabs thiếu key hoặc voice;
- tên trợ lý rỗng hoặc dài quá 40 ký tự; chỉ thị cá tính dài quá 8.000 ký tự;
- **model không có trên 9Router** (model chính và 3 "não").

Riêng lỗi model, giao diện sẽ hỏi lại người dùng rồi gửi `_force_models: true` nếu họ vẫn muốn lưu. Máy chủ chỉ kiểm các mục được gửi lên, nên một mục cũ đang sai ở nơi khác không chặn việc lưu mục khác.

## Lịch sử và khôi phục

Mỗi lần lưu tạo một phiên bản trong bảng `config_history`: ai lưu, lúc nào, mục nào đổi (giá trị trước → sau). Xem ở Portal → Quản Lý Trợ Lý AI → **Giám sát & Lịch sử**.

- **Khoá bí mật:** lịch sử **không lưu** giá trị thật, chỉ ghi "(khoá bí mật — đã đổi)".
- **"So với hiện tại":** hiện những gì sẽ thay đổi nếu khôi phục phiên bản đó.
- **"Khôi phục"** (chỉ admin): đưa cấu hình về phiên bản đó nhưng **giữ nguyên các khoá bí mật hiện tại**. Bản thân lần khôi phục cũng được ghi thành một phiên bản mới, nên hoàn tác được.

API:
- `GET /api/v1/config/history`
- `GET /api/v1/config/history/{id}/diff`
- `POST /api/v1/config/history/{id}/restore`
