# Kênh cảnh báo: kết nối Teams, Email, Outlook, Slack, Webhook

VN-MateAI gửi mọi cảnh báo sự cố qua **một khâu chung** (`application/operations/alert_dispatcher.py`) tới **tất cả kênh đã kết nối**. Nguồn cảnh báo:

- **Autonomous Sentinel:** phát hiện sự cố và khi tự khôi phục.
- **Sơ đồ hệ thống:** thành phần ở trạng thái *Lỗi* quá `down_after_s` giây; báo lại khi hoạt động trở lại.
- **Webhook từ hệ thống ngoài:** AWS, OCI, Paperless, e-Invoice.
- **Email khách hàng mức P1/P2.**
- **Cảnh báo dòng tiền nguy hiểm.**

Kênh chưa đủ thông tin hiện **"chờ kết nối"**. Kênh ở trạng thái này không gửi gì, không bị tính là lỗi, và không bao giờ báo "đã gửi".

## Cách kết nối

**Cách 1 — Portal (khuyên dùng):**
1. Vào **Portal → Cấu hình kết nối ngoại vi**, chọn thẻ **Cảnh báo · …**.
2. Điền các ô có dấu `*`, rồi bấm **Lưu cấu hình**.
3. Bấm **Gửi thử cảnh báo**.

Giá trị bí mật (URL webhook, mật khẩu, client secret) không bao giờ hiện lại trên trang. Để trống ô bí mật nghĩa là giữ nguyên giá trị đã lưu.

**Cách 2 — `config.json`:** thêm các khối như trong `config.example.json` (`alert_rules`, `alert_teams`, `alert_email`, `alert_outlook`, `alert_slack`, `alert_webhook`).

**Cách 3 — biến môi trường:** đặt tên theo dạng `<KHỐI>_<TRƯỜNG>` viết hoa, ví dụ `ALERT_TEAMS_WEBHOOK_URL`, `ALERT_EMAIL_PASSWORD`, `ALERT_OUTLOOK_CLIENT_SECRET`. Biến môi trường **ưu tiên hơn** `config.json`. Nên dùng cách này cho khoá bí mật trên máy chủ thật.

Kiểm tra sau khi kết nối:
- Trên trang **/admin/topology**, bấm ô **Khâu cảnh báo** hoặc ô của từng kênh → **Gửi thử**.
- Hoặc gọi `POST /api/v1/system/notifications/test` với body `{"channel": "alert_teams"}` (chỉ admin).

## Từng kênh

| Khối | Kênh | Bắt buộc | Lấy ở đâu |
|---|---|---|---|
| `alert_teams` | Microsoft Teams | `webhook_url` | Teams → kênh → **⋯ → Workflows** → mẫu *"Post to a channel when a webhook request is received"* → sao chép URL. Tin gửi dạng Adaptive Card; màu theo mức độ. |
| `alert_email` | Email SMTP (Exchange, Gmail, Zimbra…) | `smtp_host`, `from_address`, `to_addresses` | Cổng `587` + STARTTLS (mặc định) hoặc `465` + `use_ssl: true`. Với Gmail / Microsoft 365, dùng *App password*. `username` để trống nếu máy chủ cho relay nội bộ. |
| `alert_outlook` | Outlook qua Microsoft Graph | `tenant_id`, `client_id`, `client_secret`, `sender_mailbox`, `to_addresses` | Microsoft Entra ID → **App registrations** → New. Cấp quyền **Application → `Mail.Send`** → *Grant admin consent*. Nên giới hạn quyền vào đúng hộp thư gửi bằng *Application Access Policy*. |
| `alert_slack` | Slack | `webhook_url` | Slack App → **Incoming Webhooks** → *Add New Webhook to Workspace*. |
| `alert_webhook` | Hệ thống bất kỳ: ITSM, Jira/ServiceNow automation, Zalo OA qua middleware… | `webhook_url` | Nhận `POST` JSON gồm `title`, `message`, `severity`, `category`, `source`, `resolved`, `time`. Nếu có `hmac_secret`, request kèm header `X-VNMate-Signature: sha256=<hex HMAC-SHA256 của body>`. |
| `telegram` | Telegram | (đã có: tab Telegram) | `bot_token` + `incident_group_id`. |

Mọi kênh đều có:
- `enabled`: tạm tắt kênh mà không phải xoá cấu hình.
- `min_severity`: chỉ nhận cảnh báo từ mức này trở lên. Ví dụ Slack chỉ nhận `critical`.

## Quy tắc chung (`alert_rules`)

| Trường | Mặc định | Ý nghĩa |
|---|---|---|
| `min_severity` | `warning` | Mức thấp nhất được gửi đi: `info` < `warning` < `critical`. |
| `cooldown_s` | `300` | Cùng một sự cố không gửi lại trong khoảng này. |
| `watch_topology` | `true` | Tự cảnh báo khi một thành phần trên sơ đồ chuyển sang *Lỗi*. |
| `down_after_s` | `30` | Lỗi phải kéo dài ít nhất ngần này giây mới báo, để tránh báo nhầm khi chập chờn. |
| `alert_on_degraded` | `false` | Bật để báo cả khi thành phần ở mức *Suy giảm*. |
| `ignore_nodes` | `voice,hud,portal` | Id các ô trên sơ đồ không cần báo. |

Khi Sentinel đang chạy, sơ đồ **không báo trùng** các phần Sentinel đã tự canh: `llm`, `ad`, `db`, `core`.

## Giám sát

- **Trang /admin/topology:**
  - Ô **Khâu cảnh báo** hiện số kênh đã kết nối và cảnh báo gần nhất.
  - Mỗi kênh một ô: *Tắt* khi chờ kết nối, *Chưa rõ* khi đã kết nối nhưng chưa gửi lần nào, *Hoạt động* hoặc *Lỗi* theo lần gửi gần nhất.
  - Mỗi lần gửi hiện trong tab "Luồng trực tiếp".
- **API `GET /api/v1/system/notifications`** (quản lý / admin): trạng thái từng kênh và 20 cảnh báo gần nhất. API chỉ trả **tên** khoá còn thiếu, không bao giờ trả giá trị.

## Giới hạn hiện tại

- Telegram gửi kiểu *fire-and-forget*: kết quả "đã xếp gửi"; xác nhận HTTP 200 thật hiện ở sự kiện `alert_out` trên sơ đồ. Các kênh còn lại đều chờ phía nhận xác nhận.
- Lịch sử cảnh báo lưu trong RAM (50 mục gần nhất), mất khi khởi động lại. Nhật ký bất biến vẫn ở `audit_logs` cho các nguồn có ghi audit.
- Gửi tin vào **kênh** Teams bằng Microsoft Graph với quyền ứng dụng **không** được Microsoft hỗ trợ cho tin thường. Vì vậy Teams dùng Workflows webhook. Hàm `send_teams_channel_message` trong `m365_connector.py` giữ nguyên cho trường hợp dùng quyền ủy quyền (delegated).
