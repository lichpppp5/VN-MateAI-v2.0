# VN-MateAI 1.0 — Cài đặt trên Docker

Bộ cài **bản sạch**: không kèm dữ liệu, tài khoản, token hay khoá của máy khác. Máy mới tự sinh khoá riêng ở lần chạy đầu.

## 1. Cần có

- Máy chủ **Linux x86_64** (Ubuntu 22.04 / 24.04, Debian 12…) có **Docker Engine + Docker Compose v2**,
  hoặc **Windows 10/11 / Server** có **Docker Desktop** (WSL2),
  hoặc **macOS** (Intel hay Apple Silicon M1–M4) có **Docker Desktop for Mac**. Trên Apple Silicon trình cài tự build
  image bản ARM (cần Internet, 15–30 phút lần đầu); image dựng sẵn trong `images/` là bản x86_64.
- Tối thiểu **4 CPU, 8 GB RAM, 20 GB đĩa trống** (khuyên dùng 8 CPU, 16 GB RAM).
- Một **gateway mô hình AI** dạng OpenAI-compatible (9Router, VNmateai-proxy, OpenAI, OpenRouter, DeepSeek…): địa chỉ URL,
  API key, tên model.
- Mở cổng **443** (Portal / HUD) và **8000** (robot) trên tường lửa nếu người dùng / robot ở máy khác.

## 2. Cài

**Linux**

```bash
cd /opt && sudo cp -r <thư mục gói> vnmateai && cd vnmateai
sudo ./install.sh
```

**macOS (Docker Desktop đang chạy; Settings → Resources: cấp ≥ 8 GB RAM)**

```bash
cp -R "/Volumes/<USB>/<thư mục gói>" ~/vnmateai && cd ~/vnmateai
bash install.sh
```

**Windows (Docker Desktop đang chạy)**

```powershell
cd D:\vnmateai
powershell -ExecutionPolicy Bypass -File .\install.ps1
```

Trình cài sẽ:

1. nạp image dựng sẵn trong `images/` (không cần Internet để cài);
2. tạo `.env`, **sinh mật khẩu ngẫu nhiên** cho admin, manager, viewer, PostgreSQL, Redis;
3. hỏi gateway AI (URL, key, model). Bấm Enter để bỏ qua và nhập sau trên Portal;
4. khởi động, chờ sẵn sàng, in địa chỉ Portal và **mật khẩu admin**.

Sau khi cài:

- Portal: `https://<địa chỉ máy chủ>/`
- HUD (màn hình treo tường): `https://<địa chỉ máy chủ>/hud`
- Đăng nhập `admin` với mật khẩu được in ra (cũng nằm trong `.env`), rồi **đổi mật khẩu ngay**.

Chứng chỉ HTTPS là chứng chỉ **tự ký**, nên trình duyệt sẽ cảnh báo; chọn "Vẫn tiếp tục". Muốn dùng chứng chỉ thật: đặt
`server.crt` và `server.key` vào volume dữ liệu (thư mục `certs/`) rồi khởi động lại:

```bash
docker compose cp server.crt app:/data/certs/server.crt
docker compose cp server.key app:/data/certs/server.key
docker compose restart app
```

## 3. Cấu hình sau khi cài (Portal → Quản Lý Trợ Lý AI)

- **Bộ não (LLM):** URL / key / model của gateway AI (nếu chưa nhập lúc cài). Bấm "Kiểm tra kết nối".
- **Âm thanh:** giọng đọc (Edge-TTS mặc định, cần Internet; hoặc Vbee / ElevenLabs với key riêng).
- **Telegram** (cảnh báo + duyệt từ điện thoại), **Robot** (Portal → Bảo mật → Thiết bị: cấp token riêng cho từng robot),
  **Máy trạm** (nút "Tải Agent" trên Portal), **Danh bạ nhân sự / AD**: nhập trên Portal.
- **Dữ liệu cá nhân** (che SĐT, CCCD, lương… theo quyền): mục `privacy`, xem `app/docs/security/ai-ethics.md`.

Mọi khoá bí mật nhập trên Portal được **mã hoá** trong `config.json`, khoá giải mã nằm ở `certs/config_secret.key` của máy này.

## 4. Vận hành hằng ngày

| Việc | Lệnh (trong thư mục gói) |
|---|---|
| Xem trạng thái | `docker compose ps` |
| Xem nhật ký | `docker compose logs -f app` |
| Dừng / chạy lại | `docker compose down` / `docker compose up -d` |
| Khởi động lại ứng dụng | `docker compose restart app` |
| Sao lưu (Linux) | `./backup.sh` → `backups/db-*.sql.gz` + `backups/appdata-*.tgz` |

Hệ thống tự chạy lại khi máy chủ khởi động lại (`restart: unless-stopped`; Docker phải được bật cùng hệ điều hành).
Nên đặt sao lưu hằng đêm, ví dụ cron `0 2 * * * cd /opt/vnmateai && ./backup.sh`, và chép thư mục `backups/` ra máy khác.

**Khôi phục từ bản sao lưu**

```bash
docker compose down
docker volume rm vnmateai_pgdata vnmateai_appdata
docker compose up -d postgres && sleep 10
gunzip -c backups/db-<thời điểm>.sql.gz | docker compose exec -T postgres psql -U vnmate -d vnmate
docker run --rm --user 0 --entrypoint tar -v vnmateai_appdata:/data -v "$PWD/backups:/b" vnmateai/app:1.0.0 \
  xzf /b/appdata-<thời điểm>.tgz -C /data
docker compose up -d
```

**Cập nhật lên bản mới:** chép gói mới đè lên (giữ nguyên `.env`), rồi chạy lại `./install.sh`. Dữ liệu trong volume được giữ
nguyên. Kỹ năng có sẵn được cập nhật; kỹ năng AI tự tạo vẫn còn.

## 5. Khác biệt so với bản chạy trên máy Windows

Trong container **không có** các chức năng cần màn hình hay phần cứng của chính máy chủ: điều khiển màn hình / cửa sổ, COM
(Office), micro và loa gắn vào máy chủ, phím media, khay hệ thống. Các việc đó vẫn làm được **trên máy trạm Windows** qua
**Agent máy trạm** (Portal → "Tải Agent"). Portal, HUD (mic / loa nằm ở trình duyệt), robot, Telegram, agent doanh nghiệp,
kịch bản vận hành, kho tri thức, giám sát, phê duyệt đều chạy đầy đủ.

**Agent máy trạm Windows** (`VNMateAgent.exe` có sẵn trong gói, máy trạm không cần cài Python). Nói / gõ kèm tên máy, vd
"mở YouTube trên máy PC-KETOAN-01", AI gửi lệnh xuống máy đó (qua phê duyệt, ghi `audit_logs`):

| Nhóm | Kỹ năng trên máy trạm |
|---|---|
| Điều khiển máy | mở ứng dụng, âm lượng, phím media (phát / dừng / chuyển bài), clipboard, PowerShell, dịch vụ Windows, tắt tiến trình |
| Giải trí / văn bản | mở nhạc theo tên bài, mở YouTube, mở tệp trên Desktop bằng Notepad |
| Tệp | liệt kê, đọc, ghi, xoá, tìm kiếm |
| Giám sát | chụp màn hình, tiến trình, kết nối mạng, thiết bị ngoại vi, kiểm tra bảo mật, sức khoẻ máy |
| Office | ghi Excel qua COM |
| Hiển thị | cửa sổ nhắc việc, bảng HUD nổi trên màn hình máy trạm |

Tải Agent: mở Portal bằng **địa chỉ LAN của máy chủ** (vd `https://192.168.1.10/`, không dùng `localhost`) rồi bấm
"Tải Agent" — gói ghi địa chỉ máy chủ theo địa chỉ bạn đang mở. Mỗi gói dùng cho **một** máy. Mở cổng 443 trên máy chủ.

## 6. Thành phần

| Thành phần | Image | Dữ liệu (volume) |
|---|---|---|
| Ứng dụng VN-MateAI | `vnmateai/app:1.0.0` (Python 3.11, model giọng nói + embedding offline) | `vnmateai_appdata` → `/data` |
| CSDL | `postgres:16-alpine` (chỉ mạng nội bộ) | `vnmateai_pgdata` |
| Redis | `redis:7-alpine` (chỉ mạng nội bộ, không lưu đĩa) | — |

Mã nguồn đầy đủ nằm trong `app/` (build lại image: `docker compose build app`). Giấy phép: `app/LICENSE`, `app/NOTICE`.
