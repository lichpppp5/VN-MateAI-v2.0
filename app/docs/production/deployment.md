# Triển khai VN-MateAI

## 1. Yêu cầu

- Windows 10/11 hoặc Linux; Python **3.11** (đã chạy với 3.11.9).
- Một máy chủ, **một tiến trình** (xem README — state nằm trong bộ nhớ tiến trình).
- Truy cập mạng tới LLM gateway (9Router, mặc định `http://localhost:20128/v1`).
- Tuỳ chọn: Node.js để build lại trang quản trị (`admin/` → `admin/out`).

## 2. Cài đặt

```bash
python -m venv .venv
# Windows: .venv\Scripts\activate    Linux: source .venv/bin/activate
pip install -r requirements.txt
cp config.example.json config.json        # rồi sửa theo mục 3
```

Trang quản trị Next.js được phục vụ từ `admin/out` tại `/admin`. Sau khi sửa mã trong `admin/`:
`cd admin && npm install && npm run build`, rồi khởi động lại máy chủ.

## 3. Cấu hình bắt buộc (`config.json`)

`config.json` chỉ được đọc/ghi qua `mateai.config.loader` (ghi nguyên tử). Sửa khi máy chủ đang chạy thì dùng trang Cấu hình của portal; sửa tay thì khởi động lại.

| Khối | Bắt buộc | Ghi chú |
|---|---|---|
| `llm.base_url`, `llm.api_key` | Có | API key nên đặt bằng `VNMATEAI_LLM_API_KEY` thay vì ghi vào file |
| `llm.model_name`, `llm.router_models`, `llm.specialist_models` | Có | Chỉ liệt kê **model đang chạy**. Model đã ngừng / hết quota bị tự động xếp cuối 1 giờ nhưng vẫn làm lượt đầu sau khởi động chậm. Giá trị mẫu `YOUR_*_HERE` bị bỏ qua |
| `telegram.enabled`, `bot_token`, `admin_chat_ids` | Nếu dùng Telegram | Telegram chỉ gửi khi `enabled: true` **và** token đúng dạng `<số>:<chuỗi>`. `admin_chat_ids` trống = **không ai** được ra lệnh/duyệt qua Telegram |
| `ad_sync.enabled` | Nếu dùng AD | Bật/tắt qua portal |
| `memory_db.mode` | Không | `local` (mặc định, `storage/vector_db`). Chế độ `microservice` mặc định cổng 8000 — **trùng** cổng IoT, phải đổi cổng |

## 4. Biến môi trường

Biến môi trường thắng giá trị trong `config.json`.

| Biến | Dùng cho |
|---|---|
| `VNMATEAI_JWT_SECRET` | Khoá ký JWT. Không đặt → tự sinh `certs/jwt_secret.key`. Nhiều máy chủ phải dùng chung giá trị |
| `VNMATEAI_LLM_API_KEY` | API key LLM gateway |
| `VNMATEAI_TELEGRAM_BOT_TOKEN` | Token bot Telegram |
| `GROQ_API_KEY` | Groq (STT dự phòng) |
| `VNMATEAI_DEFAULT_ADMIN_PASSWORD`, `…_MANAGER_…`, `…_VIEWER_…` | Mật khẩu 3 tài khoản mặc định — **chỉ có tác dụng khi bảng `users` còn rỗng** (lần khởi động đầu với CSDL mới). Không đặt → `admin123/manager123/viewer123` kèm cảnh báo trong log |
| `VNMATEAI_CORS_ORIGINS` | Origin được gọi chéo (phẩy phân tách). Mặc định rỗng = cùng origin |
| `VNMATEAI_DB_PATH`, `VNMATEAI_HR_DB_PATH` | Đường dẫn CSDL (mặc định `vnmateai.db`, `hr_kpi.db` ở thư mục dự án) |
| `VNMATE_WEBHOOK_<SOURCE>_SECRET` | Khoá HMAC xác minh webhook theo nguồn (`PAPERLESS`, `EINVOICE`, `CUSTOM`, `OCI`) |
| `VNMATE_CONNECTOR_<NAME>_TIMEOUT/_RETRY/_ENABLED` | Ghi đè cấu hình từng connector |
| `M365_TENANT_ID`, `M365_CLIENT_ID`, `M365_CLIENT_SECRET`, `M365_SYSTEM_EMAIL` | Connector Microsoft 365 |
| `REDIS_URL` | Hàng đợi worker computer-use (không có → hàng đợi trong bộ nhớ) |

## 5. Cổng mạng và TLS

| Cổng | Giao thức | Phục vụ |
|---|---|---|
| `PORT` (mặc định 443) | HTTPS / WSS | Toàn bộ: portal, `/admin`, API, mọi WebSocket |
| 8000 | HTTP / WS **không TLS** | **Chỉ** `/api/v1/xiaozhi/ws…`, `/ws/audio-stream…` (thiết bị ESP32 — TLS làm tràn heap chip) và `/livez`, `/readyz`, `/startupz`. Mọi đường khác trả 404 / đóng 1008 |

- Chứng chỉ: `certs/server.crt` + `certs/server.key`, tự sinh (tự ký) nếu thiếu. Production: thay bằng chứng chỉ do CA nội bộ/công khai cấp, giữ nguyên tên file.
- Chặn cổng 8000 khỏi mạng không cần thiết (chỉ VLAN thiết bị).

## 6. Khởi động

```bash
python main.py
```

Chạy hai listener (443 và 8000) trong một tiến trình. Dùng dịch vụ hệ thống (Windows Service / systemd) để tự khởi động lại; chuyển hướng stdout/stderr ra file (ứng dụng ghi log ra luồng chuẩn, xem operations.md).

### Health probe (không cần đăng nhập)

| Đường | 200 khi | 503 khi |
|---|---|---|
| `/livez` | tiến trình còn phục vụ request | — |
| `/startupz` | toàn bộ lifecycle khởi động đã chạy xong | đang khởi động |
| `/readyz` | khởi động xong + CSDL `SELECT 1` được (timeout 3 s) + đã nạp skill | kèm `checks` cho biết mục hỏng |

## 7. Lần khởi động đầu

1. CSDL mới: nếu có `users.json` của bản cài cũ, tài khoản được **di trú một lần** vào bảng `users` (giữ mật khẩu); nếu không, tạo 3 tài khoản mặc định theo biến môi trường ở mục 4.
2. Đăng nhập `admin`, **đổi mật khẩu** nếu đang dùng mặc định.
3. Thay LLM model không còn chạy (xem log `Tạm xếp cuối model …`).

## 8. Thiết bị ESP32 / Xiaozhi

Thiết bị **bắt buộc** có token (không còn chế độ cắm-là-chạy trong LAN). Khuyến nghị: **token riêng cho từng robot**, ràng buộc với id của robot.

1. Chọn id riêng (chữ, số, `_ - .`, ≤ 64 ký tự).
2. Cấp token (admin): `POST /api/v1/security/devices` `{"device_id": "<id>"}` — trả `device_token` **một lần** (máy chủ chỉ lưu hash) và `ws_path`. Gọi lại = xoay token (token cũ hết hiệu lực). Xem danh sách: `GET /api/v1/security/devices`; thu hồi: `DELETE /api/v1/security/devices/<id>`.
3. `esp32_firmware/src/secrets.h` (không commit): `DEFAULT_DEVICE_ID` = id, `DEFAULT_DEVICE_TOKEN` = token; build, nạp.
4. Robot kết nối `ws://<máy chủ>:8000/api/v1/xiaozhi/ws/<id>` với `?token=` hoặc `Authorization: Bearer`. Token của robot A dùng cho id khác → HTTP 403.

Tương thích: token **chung** (`certs/device_secret.key`, `GET /api/v1/security/device-enrollment-token`) vẫn được nhận, kèm cảnh báo trong log, cho tới khi đặt `"security": {"require_per_device_token": true}` trong `config.json`.

## 9. Máy trạm (client agent) và worker

- **Client agent:** tải gói từ portal (`/api/v1/download-agent`, quyền admin/manager); gói chứa `enrollment_token` trong `config.json` của nó. Chạy `python agent.py` trên máy trạm.
- **Remote worker daemon** (`workers/remote_worker_daemon.py`): đặt `VNMATE_ENROLLMENT_TOKEN` = `enrollment_token` ở trên và `MASTER_API_URL`. Thiếu token → heartbeat bị từ chối (401), node không lên grid.

## 10. Nâng cấp từ bản cũ

- `users.json` không còn được dùng sau khi di trú; có thể xoá (chứa hash mật khẩu).
- `logs/security_audit.log` không còn được ghi; audit nằm trong bảng `audit_logs`.
- HUD (`/hud`) phải đăng nhập mới ra lệnh thoại được; mở HUD bằng phiên đã đăng nhập.
- Thiết bị ESP32 nạp firmware không có token sẽ bị từ chối — nạp lại theo mục 8.

## 11. Sao lưu, khôi phục, rollback (2026-10-05)

| Việc | Lệnh | Ghi chú |
|---|---|---|
| Sao lưu | `python scripts/backup.py create` | CSDL chụp trực tuyến (an toàn khi máy chủ đang chạy): khi có `DATABASE_URL` thì chụp từng schema PostgreSQL (REPEATABLE READ) ra tệp SQLite và đối chiếu checksum, ngược lại chụp tệp SQLite; khôi phục vào PostgreSQL bằng `pg_migration.migrate` + `config.json` + khoá trong `certs/` + `storage/vector_db`; tự kiểm chứng (sha256, `integrity_check`, số dòng). Đo 2026-10-05: 0,09 s, 1,3 MB |
| Kiểm chứng bản cũ | `python scripts/backup.py verify backups\<thư mục>` | ĐẠT / KHÔNG ĐẠT + lý do |
| Khôi phục | dừng máy chủ → `python scripts/backup.py restore backups\<thư mục> --yes` | từ chối bản không đạt kiểm chứng; tự lưu trạng thái hiện tại vào `backups\pre-restore-*` |
| Rollback mã | `git checkout <commit trước>` → chạy lại máy chủ | schema chỉ THÊM bảng / cột (không xoá) nên bản cũ vẫn đọc được DB mới; nếu cần trạng thái dữ liệu cũ: khôi phục bản sao lưu tương ứng |

- Thư mục `backups/` chứa **bí mật** và bị `.gitignore` loại — chép ra nơi lưu trữ được bảo vệ (ổ ngoài mã hoá / NAS có phân quyền).
- RPO / RTO: chưa có lịch sao lưu tự động — RPO bằng khoảng cách giữa hai lần chạy `create`. Đề xuất: Task Scheduler chạy `create` mỗi ngày + trước mỗi lần nâng cấp.

## 12. CI (`.github/workflows/tests.yml`)

pytest (gồm test kiến trúc RULE-011…026, test đối kháng, kịch bản vàng) → test Node `.mjs` → kiểm tra cú pháp Python + `web/app.js` → chặn tệp bí mật / khoá riêng tư trong git. Chưa có: linter, kiểm kiểu, quét lỗ hổng phụ thuộc, triển khai staging.
