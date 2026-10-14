# Hướng dẫn triển khai VN-MateAI trên môi trường production

Cập nhật 2026-10-06, khớp với mã ở nhánh `main`. Dành cho người cài đặt và vận hành máy chủ.
Làm lần lượt từ mục 1 đến mục 12. Mỗi mục có bước **Kiểm tra**; chỉ sang mục sau khi kiểm tra đạt.

Tài liệu liên quan: xử lý sự cố → [`runbook.md`](runbook.md) · vận hành hằng ngày → [`operations.md`](operations.md) ·
bảo mật → [`security.md`](security.md) · việc còn mở → [`owner-todo.md`](owner-todo.md).

---

## 0. Toàn cảnh

```text
 Người dùng (trình duyệt) ──HTTPS 443──┐
 Telegram (bot, kéo tin ra ngoài) ─────┤
 Agent máy trạm ──WSS 443 /ws/client───┤         ┌─ PostgreSQL 16  127.0.0.1:5432  (dữ liệu)
                                       ├─► VN-MateAI ─┼─ Redis 7        127.0.0.1:6379  (trạng thái ngắn hạn)
 Robot ESP32 ──WS 8000 (chỉ đường thiết bị)┤  (1 tiến trình│  ─ S3 SeaweedFS  127.0.0.1:8333  (tài liệu, sao lưu)
 Robot tìm máy chủ ──UDP 8888 beacon ──┘   Python)     └─ OTel collector 127.0.0.1:4317  (trace)
                                              │
                                              └──► 9Router (LLM gateway, http://localhost:20128/v1) ──► nhà cung cấp LLM
```

- Ứng dụng chạy **trực tiếp trên Windows** (cần cổng COM, micro, điều khiển màn hình). Hạ tầng chạy bằng Docker trên cùng máy.
- **Một tiến trình** là đủ cho một văn phòng: đo thật 100 phiên đồng thời không nghẽn ([`../architecture/multi-process-plan.md`](../architecture/multi-process-plan.md)).
- Dữ liệu nằm ở PostgreSQL. Không đặt `DATABASE_URL` thì ứng dụng chạy SQLite (chỉ dùng thử).

### Danh sách cần chuẩn bị trước

| Thứ | Ai cung cấp | Dùng ở mục |
|---|---|---|
| Máy chủ Windows 10/11 64-bit, quyền Administrator | IT | 1 |
| Khoá API của 9Router (hoặc gateway LLM tương thích OpenAI) | quản trị AI | 5.1 |
| Mật khẩu PostgreSQL, mật khẩu Redis, cặp khoá S3 (tự đặt, ngẫu nhiên, ≥ 24 ký tự) | người cài | 4 |
| Chứng chỉ TLS cho tên miền nội bộ của máy chủ (CA nội bộ hoặc công khai) | IT | 7 |
| Mật khẩu 3 tài khoản ban đầu `admin`, `manager`, `viewer` | chủ hệ thống | 6 |
| Token bot Telegram + ID nhóm/chat admin (nếu dùng) | chủ hệ thống | 10.1 |
| Wi-Fi cho robot + ID robot (nếu dùng) | IT | 10.2 |
| Ổ đĩa thứ hai / NAS / ổ ngoài cho bản sao lưu | IT | 11 |

---

## 1. Yêu cầu máy chủ

| Hạng mục | Tối thiểu | Ghi chú |
|---|---|---|
| CPU | 4 nhân | nhận dạng giọng nói (faster-whisper) chạy trên CPU |
| RAM | 8 GB (khuyến nghị 16 GB) | ứng dụng + torch + Docker (PostgreSQL, Redis, S3) |
| Ổ đĩa | 30 GB trống | + chỗ cho bản sao lưu; **nên có ổ vật lý thứ hai** cho bản sao lưu |
| Hệ điều hành | Windows 10/11 64-bit | bật ảo hoá (cho WSL2 / Docker) |
| Mạng | IP tĩnh trong LAN | robot và máy trạm kết nối vào IP này |

Phần mềm cần cài (bằng tài khoản Administrator):

1. **Python 3.11** (64-bit) — tick "Add python.exe to PATH".
2. **Git**.
3. **Docker Desktop** dùng nền WSL2. Sau khi cài: mở PowerShell quản trị, chạy `wsl --update`, khởi động lại máy.
   Trong Docker Desktop → Settings → General: bật **"Start Docker Desktop when you sign in"**.
4. **Node.js 20 LTS** — chỉ cần nếu build lại trang `/admin` (mục 3.3).
5. **RSAT Active Directory** — chỉ cần nếu đồng bộ nhân sự từ AD (mục 10.6); máy chủ phải tham gia domain.
6. **9Router** (LLM gateway) chạy trên máy chủ hoặc máy khác trong LAN, phục vụ API dạng OpenAI tại `…/v1`.

**Kiểm tra:** `python --version` → 3.11.x · `docker version` có cả Client và Server · `git --version`.

---

## 2. Lấy mã nguồn

```powershell
cd D:\
git clone https://github.com/lichpppp/VNMateAI-v1.0.git VNMateAI
cd D:\VNMateAI
```

> Repo là phần mềm độc quyền (`LICENSE`). Chỉ cài khi có phép của chủ sở hữu.

---

## 3. Cài thư viện

### 3.1 Môi trường Python

```powershell
cd D:\VNMateAI
python -m venv .venv
.venv\Scripts\activate
python -m pip install --upgrade pip
pip install -r requirements.txt
pip install -e .
```

- `pip install -e .` cài gói `mateai` (thư mục `src/mateai`). Thiếu bước này, `python main.py` báo không tìm thấy `mateai`.
- `fastapi` được ghim ở **0.135.4** — không nâng (lý do ghi trong `requirements.txt`).

### 3.2 Thư mục và chứng chỉ tạm

```powershell
setup.bat
```
Tạo `storage/`, `data/`, `certs/`, `models/`, `logs/`, chép `config.example.json` → `config.json`, sinh chứng chỉ TLS tự ký
(chỉ để chạy thử — thay ở mục 7).

### 3.3 Trang `/admin` (tuỳ chọn)

Hai trang `/admin/topology` và `/admin/computer-use` phục vụ từ bản build Next.js:
```powershell
cd admin; npm ci; npm run build; cd ..
```

**Kiểm tra:** `.venv\Scripts\python -c "import mateai, fastapi; print(fastapi.__version__)"` → `0.135.4`.

---

## 4. Hạ tầng Docker (PostgreSQL, Redis, S3, OpenTelemetry)

### 4.1 Tệp bí mật của hạ tầng (không commit — đã có trong `.gitignore`)

`deploy\.env`:
```ini
VNMATE_PG_PASSWORD=<mật khẩu PostgreSQL ngẫu nhiên>
VNMATE_REDIS_PASSWORD=<mật khẩu Redis ngẫu nhiên>
```

`deploy\s3.json` (S3 **bắt buộc có khoá** vì bản sao lưu chứa bí mật):
```json
{"identities": [{"name": "vnmate",
  "credentials": [{"accessKey": "<access key>", "secretKey": "<secret key>"}],
  "actions": ["Admin", "Read", "Write", "List", "Tagging"]}]}
```

Sinh chuỗi ngẫu nhiên: `.venv\Scripts\python -c "import secrets; print(secrets.token_urlsafe(32))"`.

### 4.2 Khởi động

```powershell
docker compose -f deploy\docker-compose.infra.yml --env-file deploy\.env up -d
docker compose -f deploy\docker-compose.infra.yml --env-file deploy\.env ps
```

| Dịch vụ | Cổng (chỉ 127.0.0.1) | Dữ liệu |
|---|---|---|
| PostgreSQL 16 | 5432 (user / db `vnmate`) | volume Docker |
| Redis 7 | 6379 (có mật khẩu, không lưu xuống đĩa) | — |
| S3 (SeaweedFS) | 8333 | volume `s3data`; bucket `vnmateai` tự tạo ở lần ghi đầu |
| OTel collector | 4317 | — |

Các cổng **chỉ mở trên 127.0.0.1** — máy khác trong LAN không vào được. Giữ nguyên như vậy.

**Kiểm tra:** `ps` cho thấy `postgres` và `redis` ở trạng thái `healthy`, `s3` và `otel-collector` ở `Up`.

---

## 5. Cấu hình `config.json`

Sửa `config.json` **khi máy chủ đang tắt** (khi đang chạy: dùng trang Cấu hình trên portal — có lịch sử phiên bản và audit).
Ở lần khởi động, mọi trường bí mật (`api_key`, `bot_token`, `password`, `DATABASE_URL`, `REDIS_URL`, khoá S3…) được
**tự mã hoá ngay trong tệp**. Khoá giải mã là `certs\config_secret.key` — mất khoá này thì không đọc lại được cấu hình.

### 5.1 Bắt buộc

```json
{
  "HOST": "0.0.0.0",
  "PORT": 443,
  "IOT_PORT": 8000,
  "LOG_LEVEL": "INFO",
  "LOG_FORMAT": "json",

  "DATABASE_URL": "postgresql://vnmate:<VNMATE_PG_PASSWORD>@127.0.0.1:5432/vnmate",
  "REDIS_URL": "redis://:<VNMATE_REDIS_PASSWORD>@127.0.0.1:6379/0",
  "OTEL_EXPORTER": "otlp",
  "object_storage": {
    "backend": "s3", "endpoint": "127.0.0.1:8333", "bucket": "vnmateai",
    "access_key_id": "<access key>", "secret_access_key": "<secret key>", "secure": false
  },

  "llm": {
    "base_url": "http://localhost:20128/v1",
    "api_key": "<khoá 9Router>",
    "model_name": "<model chính đang chạy được>",
    "router_models": ["<model dự phòng 1>", "<model dự phòng 2>"],
    "specialist_models": ["<model cho tác vụ phân tích>"]
  },

  "auto_execute": false,
  "security": { "require_per_device_token": true }
}
```

| Khoá | Ý nghĩa |
|---|---|
| `DATABASE_URL` | PostgreSQL là nguồn sự thật. Mật khẩu có ký tự đặc biệt phải mã hoá URL (`@` → `%40`). Bảng tự tạo ở lần chạy đầu (schema `vnmate`, `hr`) |
| `REDIS_URL` | Rate limit, khoá đăng nhập, bộ đếm khẩn cấp dùng chung. Redis lỗi → tự lùi về RAM, không dừng dịch vụ |
| `OTEL_EXPORTER` | `otlp` gửi trace tới collector (`OTEL_EXPORTER_OTLP_ENDPOINT`, mặc định `http://localhost:4317`); `none` = tắt |
| `object_storage` | Tài liệu RAG tải lên + bản sao lưu đẩy lên. Bỏ khối này → lưu thư mục cục bộ |
| `llm.*` | **Chỉ liệt kê model đang chạy được.** Model hỏng tự bị xếp cuối 120 s (lỗi tạm) hoặc 3 600 s (đã ngừng / hết hạn mức) |
| `auto_execute` | `false` = mã do AI tự sinh phải có người duyệt mới chạy. **Production nên để `false`** |
| `security.require_per_device_token` | `true` = robot bắt buộc token riêng, token chung bị từ chối |
| `LOG_FORMAT` | `json` cho hệ thống gom log; `text` để người đọc |

### 5.2 Tuỳ chọn theo nhu cầu

| Khối | Dùng khi | Khoá chính |
|---|---|---|
| `telegram` | báo sự cố / nhận lệnh qua Telegram | `enabled`, `bot_token`, `admin_chat_ids` (chỉ các chat này được ra lệnh / duyệt), `incident_group_id` |
| `alert_rules` | ngưỡng cảnh báo | `min_severity`, `cooldown_s`, `watch_topology`, `down_after_s`, `ignore_nodes` |
| `alert_email`, `alert_outlook`, `alert_teams`, `alert_slack`, `alert_webhook` | kênh cảnh báo khác | SMTP / Microsoft Graph / webhook URL; `alert_webhook.hmac_secret` để bên nhận xác minh |
| `email_gateway` | đọc hộp thư khách hàng → tạo ticket | `enabled`, `username`, `password`, `imap_host`, `smtp_host` |
| `ad_sync` | đồng bộ nhân sự / máy tính từ AD | `enabled` (cần RSAT + máy chủ trong domain) |
| `autonomy` | giới hạn AI tự trị | `max_tool_calls_per_turn` (12), `max_agent_seconds` (180), `max_tool_failures_per_turn` (3), `emergency_max_actions_per_minute` (30), `never_autonomous_tools`, `approval_grant_ttl_days` (30), `kill_switch` |
| `backup` | sao lưu (mục 11) | `offsite_dirs`, `keep` (30), `max_age_hours` (26) |
| `robot_behavior` | cử động tự nhiên của robot (firmware ≥ 55) | `enabled`, `idle_motion` (ngó quanh, nghiêng đầu, đèn thở lúc rảnh), `idle_wheels` (lắc bánh nhẹ: trái/phải, nhích tiến rồi lùi — luôn về chỗ cũ, không nhích tiến khi cảm biến báo mép bàn), `speech_gestures` (vẫy tay khi chào, gật khi xác nhận, buồn khi xin lỗi… + gật nhẹ theo nhịp nói), `led` (đèn RGB theo trạng thái / cảm xúc), `idle_min_s` / `idle_max_s` (30 / 120), `wheel_ms` (120, giới hạn 60–300), `quiet_hours` (vd `22:00-07:00`: không cử động, tắt đèn) |
| `audio` | giọng đọc | `tts_engine` (`edge-tts`), `tts_voice` (`vi-VN-HoaiMyNeural`), `speech_rate`, `volume` |
| `persona`, `AI_NAME`, `WAKE_WORD` | tên, tính cách trợ lý | — |
| `memory_db` | trí nhớ vector | để `mode: "local"`. Chế độ `microservice` mặc định cổng 8000 — **trùng** cổng IoT, phải đổi |
| `llm.model_registry` | phê duyệt model + bảng giá | mỗi model: `status` (`APPROVED` / `EXPERIMENTAL` / `DEPRECATED` / `BLOCKED`), `price_in_per_1m`, `price_out_per_1m` (USD / 1 triệu token) — sửa qua `PUT /api/v1/llm/model-registry` |

### 5.3 Biến môi trường (thắng giá trị trong `config.json`)

| Biến | Khi nào đặt |
|---|---|
| `VNMATEAI_DEFAULT_ADMIN_PASSWORD`, `VNMATEAI_DEFAULT_MANAGER_PASSWORD`, `VNMATEAI_DEFAULT_VIEWER_PASSWORD` | **Trước lần khởi động đầu** — chỉ có tác dụng khi bảng `users` còn rỗng. Không đặt → mật khẩu dev yếu `admin123`… |
| `VNMATEAI_JWT_SECRET` | khi chuyển máy chủ / chạy nhiều máy (không đặt → tự sinh `certs\jwt_secret.key`) |
| `VNMATEAI_LLM_API_KEY`, `VNMATEAI_TELEGRAM_BOT_TOKEN`, `GROQ_API_KEY` | muốn giữ khoá ngoài tệp cấu hình |
| `VNMATEAI_DATABASE_URL`, `VNMATEAI_REDIS_URL`, `VNMATEAI_OTEL_EXPORTER`, `OTEL_EXPORTER_OTLP_ENDPOINT` | ghi đè hạ tầng theo máy. `VNMATEAI_DATABASE_URL=sqlite` ép chạy SQLite (đường lùi) |
| `VNMATEAI_CORS_ORIGINS` | chỉ khi trang web ở tên miền khác gọi API (mặc định không mở) |
| `VNMATE_WEBHOOK_<NGUỒN>_SECRET` | nhận webhook (`PAPERLESS`, `EINVOICE`, `CUSTOM`, `OCI`) — **thiếu khoá thì webhook bị từ chối** |
| `M365_TENANT_ID`, `M365_CLIENT_ID`, `M365_CLIENT_SECRET`, `M365_SYSTEM_EMAIL` | connector Microsoft 365 |

Đặt biến cố định cho máy (PowerShell quản trị):
```powershell
[Environment]::SetEnvironmentVariable("VNMATEAI_DEFAULT_ADMIN_PASSWORD", "<mật khẩu mạnh>", "Machine")
```

---

## 6. Khởi động lần đầu

```powershell
cd D:\VNMateAI
.venv\Scripts\python.exe main.py
```

Chờ khoảng 40 giây. Ứng dụng chạy 21 bước khởi động (tạo bảng CSDL, mã hoá bí mật, nạp skill, Telegram…).

**Kiểm tra:**
```powershell
Invoke-WebRequest -UseBasicParsing http://127.0.0.1:8000/readyz | Select-Object -Expand Content
# {"status":"ok","checks":{"startup":"ok","database":"ok","skills":"ok"}}
```
- `database` khác `ok` → sai `DATABASE_URL` hoặc PostgreSQL chưa chạy (mục 4).
- Chi tiết từng bước khởi động: đăng nhập admin rồi mở `https://<máy chủ>/api/v1/health/startup`.

Sau đó trên portal (`https://<máy chủ>`):
1. Đăng nhập `admin` → **đổi mật khẩu** nếu chưa đặt biến môi trường ở mục 5.3.
2. Người dùng → đổi mật khẩu `manager`, `viewer`; gán **phòng ban** và **cấp bảo mật** cho từng tài khoản
   (tài khoản chưa có phòng ban không thấy dữ liệu ERP theo phòng ban; dữ liệu tài chính cần cấp 3).
3. Mở `config.json`: các trường bí mật giờ là chuỗi mã hoá — đúng như mong đợi.

### Đã có dữ liệu SQLite từ bản cài cũ?

Dừng ứng dụng cũ, rồi chép sang PostgreSQL (đối chiếu số dòng + checksum từng bảng; lỗi thì không để lại gì):
```powershell
.venv\Scripts\python scripts\migrate_sqlite_to_pg.py migrate --sqlite vnmateai.db --pg "<DATABASE_URL>" --schema vnmate
.venv\Scripts\python scripts\migrate_sqlite_to_pg.py migrate --sqlite hr_kpi.db  --pg "<DATABASE_URL>" --schema hr
```
Giữ các tệp `.db` cũ làm đường lùi (mục 12).

---

## 7. TLS, cổng mạng, tường lửa

### 7.1 Chứng chỉ

Thay chứng chỉ tự ký bằng chứng chỉ do CA cấp, **giữ nguyên tên tệp**:
- `certs\server.crt` — chứng chỉ (kèm chuỗi CA trung gian, dạng PEM)
- `certs\server.key` — khoá riêng (PEM, không đặt mật khẩu)

Khởi động lại ứng dụng. Robot dùng cổng 8000 không TLS (chip ESP32 không đủ bộ nhớ cho TLS) — vì vậy cổng này
chỉ được mở cho VLAN thiết bị.

### 7.2 Cổng

| Cổng | Giao thức | Ai được vào | Phục vụ |
|---|---|---|---|
| 443 | HTTPS / WSS | người dùng, agent máy trạm | portal, HUD, ROI, API, WebSocket người dùng |
| 8000 | HTTP / WS | **chỉ VLAN robot** | `/api/v1/xiaozhi/ws…` và `/livez`, `/readyz`, `/startupz`; mọi đường khác 404 |
| 8888 | UDP | VLAN robot | beacon để robot mới tự tìm máy chủ |
| 5432, 6379, 8333, 4317 | — | **không ai từ ngoài** | chỉ mở trên 127.0.0.1 |

### 7.3 Tường lửa Windows (PowerShell quản trị; thay dải mạng cho đúng)

```powershell
New-NetFirewallRule -DisplayName "VNMateAI HTTPS" -Direction Inbound -Protocol TCP -LocalPort 443 -RemoteAddress 192.168.1.0/24 -Action Allow
New-NetFirewallRule -DisplayName "VNMateAI IoT"   -Direction Inbound -Protocol TCP -LocalPort 8000 -RemoteAddress 192.168.50.0/24 -Action Allow
New-NetFirewallRule -DisplayName "VNMateAI Beacon" -Direction Inbound -Protocol UDP -LocalPort 8888 -RemoteAddress 192.168.50.0/24 -Action Allow
```
(`192.168.1.0/24` = mạng người dùng, `192.168.50.0/24` = mạng robot — ví dụ.)

**Kiểm tra:** từ máy người dùng mở `https://<máy chủ>` thấy trang đăng nhập, trình duyệt không cảnh báo chứng chỉ;
từ máy người dùng, `http://<máy chủ>:8000/` **không** vào được (bị tường lửa chặn hoặc trả 404).

---

## 8. Chạy như dịch vụ (tự khởi động cùng máy)

Tạo tác vụ khởi động trong Task Scheduler (PowerShell quản trị; thay `D:\VNMateAI` nếu khác):

```powershell
$dir = "D:\VNMateAI"
$act = New-ScheduledTaskAction -Execute "cmd.exe" -WorkingDirectory $dir `
  -Argument "/c `"$dir\.venv\Scripts\python.exe main.py >> $dir\logs\server.out.log 2>> $dir\logs\server.err.log`""
$trg = New-ScheduledTaskTrigger -AtStartup
$trg.Delay = "PT90S"      # chờ Docker Desktop lên trước
$set = New-ScheduledTaskSettingsSet -RestartCount 5 -RestartInterval (New-TimeSpan -Minutes 1) -ExecutionTimeLimit (New-TimeSpan -Seconds 0) -AllowStartIfOnBatteries
$pri = New-ScheduledTaskPrincipal -UserId "$env:USERDOMAIN\$env:USERNAME" -LogonType S4U -RunLevel Highest
Register-ScheduledTask -TaskName "VNMateAI-Server" -Action $act -Trigger $trg -Settings $set -Principal $pri
```

Lưu ý:
- Docker Desktop chỉ chạy khi có người đăng nhập Windows. Để máy tự lên sau khi mất điện: bật tự đăng nhập Windows
  cho tài khoản dịch vụ, hoặc chuyển hạ tầng sang Docker Engine chạy dạng dịch vụ.
- Log ứng dụng: `logs\server.err.log`. Xoay vòng log định kỳ (ví dụ tác vụ hằng tuần đổi tên tệp khi máy chủ dừng).

**Kiểm tra:** khởi động lại máy → sau khoảng 3 phút `/readyz` trả `ok`.

---

## 9. Kiểm tra toàn hệ thống trước khi bàn giao

| Kiểm | Cách | Đạt khi |
|---|---|---|
| Sẵn sàng | `GET /readyz` | `status: ok` |
| Đăng nhập | portal | vào được bằng mật khẩu mới; mật khẩu sai bị từ chối |
| Audit | `GET /api/v1/security/audit-logs/verify` (admin) | `ok: true` |
| LLM | hỏi portal "máy chủ đang dùng bao nhiêu CPU" | trả số thật, có gọi tool |
| Hiển thị giờ | Bảo mật → nhật ký | giờ đúng giờ Việt Nam |
| Dừng khẩn cấp | Trung tâm Bảo mật → "DỪNG KHẨN CẤP AI" → hỏi một việc có ghi | bị chặn; tắt lại sau khi thử |
| Sao lưu | mục 11 | bản sao lưu ĐẠT kiểm chứng |
| Đánh giá LLM (tuỳ chọn, tốn token) | `python scripts\eval_llm.py` | chọn tool đúng ≥ 13/14, 0 đề xuất tool bị cấm |
| Tải (tuỳ chọn) | `python scripts\load_test.py --sessions 50` | 0 phiên rớt |

---

## 10. Kết nối các kênh

### 10.1 Telegram
1. Tạo bot với @BotFather → lấy token dạng `<số>:<chuỗi>`.
2. Thêm bot vào nhóm vận hành, nhắn một tin trong nhóm; lấy ID chat bằng nút **"Dò Chat ID Gần Nhất"** trên trang Telegram của portal.
3. Cấu hình `telegram.enabled = true`, `bot_token`, `admin_chat_ids` (chỉ các chat này được ra lệnh / duyệt),
   `incident_group_id` (nhận cảnh báo sự cố).
4. **Kiểm tra:** gửi cảnh báo thử (`POST /api/v1/telegram/test-alert`, hoặc nút gửi thử trên trang Telegram) — tin phải tới nhóm; nhắn `/status` cho bot.

Bot chỉ gửi tới chat nội bộ đã cấu hình. Chỉ **một** máy chủ được chạy bot với cùng token (hai máy cùng chạy → lỗi 409).

### 10.2 Robot ESP32
1. Chọn ID robot (chữ, số, `_ - .`, ≤ 64 ký tự), ví dụ `robot_phong_hop`.
2. Cấp token (admin): portal → Trung tâm Bảo mật → Thiết bị & Robot, hoặc
   `POST /api/v1/security/devices` với `{"device_id": "robot_phong_hop"}`. **Token chỉ hiện một lần** — chép ngay.
3. Trên máy build firmware:
   ```powershell
   copy esp32_firmware\secrets.example.h esp32_firmware\src\secrets.h
   ```
   Điền `DEFAULT_WIFI_SSID`, `DEFAULT_WIFI_PASS`, `DEFAULT_DEVICE_ID`, `DEFAULT_DEVICE_TOKEN` (tệp này không commit).
4. Cắm robot qua USB, nạp: `cd esp32_firmware; pio run -e esp32s3 -t upload --upload-port COM7` (đổi COM theo máy).
5. **Kiểm tra:** `GET /api/v1/security/devices` có `last_seen_at` mới; log có `[vnmate_robot…]`.

Xoay token: gọi lại bước 2 (token cũ hết hiệu lực). Thu hồi: `DELETE /api/v1/security/devices/<id>`.

### 10.3 Agent máy trạm
1. Portal → nút **"Tải Agent"** (admin / manager). Mỗi gói có mã đăng ký riêng (`enrollment_token`) và IP máy chủ.
2. Trên máy trạm: giải nén, `python agent.py`.
3. **Kiểm tra:** máy trạm hiện trên Topology, trạng thái trực tuyến.

### 10.4 Worker từ xa
Đặt `VNMATE_ENROLLMENT_TOKEN` (giá trị `enrollment_token` trong gói agent) và `MASTER_API_URL=https://<máy chủ>`,
rồi chạy `python workers\remote_worker_daemon.py`. Thiếu token → bị từ chối (401).

### 10.5 Hộp thư khách hàng (email gateway)
Khối `email_gateway`: `enabled`, `username`, `password` (mật khẩu ứng dụng), `imap_host`, `smtp_host`.
Tự trả lời chỉ bật khi `autonomy.email_auto_reply = true` **và** miền người gửi nằm trong `email_auto_reply_domains`.

### 10.6 Active Directory, ERP và Agent máy trạm
Ba thứ này **độc lập**, nối với nhau bằng các bước có chủ đích:

1. **Đồng bộ AD** (máy chủ trong domain + RSAT, `ad_sync.enabled = true`): bấm "Đồng Bộ AD Ngay". Nhân viên và máy tính
   được ghi vào **bản sao AD** (kho `hr`), AI tra cứu bằng `lookup_domain_info`. Đồng bộ **không** tự đổi cây tổ chức ERP.
2. **Nhập vào ERP** (tab Cấu Trúc Tổ Chức & Quản Trị ERP → **"⬇ Nhập từ AD"**): chọn phòng ban mặc định (cho người / máy AD không
   có phòng ban), **Xem trước** (không ghi gì, số liệu y hệt khi nhập thật), rồi **Nhập thật**. Chạy lại không nhân đôi; không ghi
   đè dữ liệu nhập tay; không đổi phòng ban người đã có; nhân viên mới chỉ có quyền `viewer`. Chủ máy chỉ được gán khi mô tả của
   máy trong AD trùng đúng tên / tài khoản một nhân viên.
3. **Agent máy trạm**: **không** tự cài từ xa. Cài thủ công (tải gói ở portal, bấm đúp) hoặc đẩy cài im lặng (`--quiet`) bằng
   GPO / Intune. Cột **Agent** ở bảng thiết bị ERP và bảng máy AD cho biết từng máy: Trực tuyến / Đã cài · ngoại tuyến / Đã thu
   hồi khoá / Chưa cài (khớp theo tên máy, không phân biệt hoa / thường và hậu tố miền).
4. **AI chạy công cụ trên máy trạm** qua `target_client` (mã agent = tên máy mặc định). Máy phải đang trực tuyến; AI gọi
   `list_online_workstations` để biết máy nào đang có mặt. Tên khớp nhiều máy thì báo rõ, không đoán.

### 10.7 Webhook từ hệ thống khác
Đặt `VNMATE_WEBHOOK_<NGUỒN>_SECRET` cho từng nguồn; bên gửi ký HMAC bằng khoá đó. Webhook không có chữ ký bị từ chối.

---

## 11. Sao lưu và khôi phục

### 11.1 Cấu hình
```json
"backup": { "offsite_dirs": ["E:\\VNMateAI-backups"], "keep": 30, "max_age_hours": 26 }
```
- `offsite_dirs`: thư mục trên **ổ vật lý khác** / NAS / ổ ngoài. Bản sao lưu **chứa bí mật** (cấu hình, khoá) — chỉ chọn
  nơi bạn kiểm soát quyền truy cập.
- Mỗi bản sao lưu: chụp nhất quán PostgreSQL + `config.json` + khoá trong `certs\` + `storage\vector_db`; tự kiểm chứng
  (sha256, số dòng); đẩy lên S3; chép sang `offsite_dirs` và đối chiếu sha256; giữ `keep` bản mới nhất ở mỗi nơi chép.

### 11.2 Lịch tự động (PowerShell quản trị)
```powershell
$dir = "D:\VNMateAI"
$act = New-ScheduledTaskAction -Execute "cmd.exe" `
  -Argument "/c `"$dir\.venv\Scripts\python.exe $dir\scripts\backup.py create --push >> $dir\logs\backup.log 2>&1`""
$trg = New-ScheduledTaskTrigger -Daily -At 2am
$set = New-ScheduledTaskSettingsSet -StartWhenAvailable -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -ExecutionTimeLimit (New-TimeSpan -Hours 2)
$pri = New-ScheduledTaskPrincipal -UserId "$env:USERDOMAIN\$env:USERNAME" -LogonType S4U
Register-ScheduledTask -TaskName "VNMateAI-Backup" -Action $act -Trigger $trg -Settings $set -Principal $pri
```
Quá `max_age_hours` không có bản mới → hệ thống tự báo sự cố "Sao lưu tự động không chạy" qua các kênh cảnh báo.

### 11.3 Lệnh tay
```powershell
.venv\Scripts\python scripts\backup.py create --push              # tạo + kiểm chứng + đẩy S3 + chép offsite
.venv\Scripts\python scripts\backup.py verify backups\<thư mục>
.venv\Scripts\python scripts\backup.py restore backups\<thư mục> --yes   # DỪNG máy chủ trước
```
Khôi phục tự lưu trạng thái hiện tại vào `backups\pre-restore-*` trước khi ghi đè, và từ chối bản không đạt kiểm chứng.

**Kiểm tra (làm mỗi quý):** giải nén một bản trong `offsite_dirs` ra thư mục tạm → `backup.py verify <thư mục>` → ĐẠT.

### 11.4 Thứ không được mất
| Tệp | Vì sao |
|---|---|
| `certs\config_secret.key` | giải mã bí mật trong `config.json` |
| `certs\jwt_secret.key` | mất → mọi phiên đăng nhập hết hiệu lực |
| `certs\device_secret.key`, `worker_secret.key` | thiết bị / máy trạm phải đăng ký lại |
| `deploy\.env`, `deploy\s3.json` | mật khẩu hạ tầng |

Các tệp này nằm trong bản sao lưu (trừ `deploy\*` — chép riêng vào nơi cất mật khẩu của công ty).

---

## 12. Nâng cấp và quay lui

**Nâng cấp:**
1. `python scripts\backup.py create` (bản sao lưu trước nâng cấp).
2. Dừng ứng dụng (tác vụ `VNMateAI-Server`).
3. `git pull` → `pip install -r requirements.txt` → `pip install -e .` (→ `cd admin; npm ci; npm run build` nếu `admin/` đổi).
4. Chạy lại → `/readyz`.

Schema CSDL chỉ **thêm** bảng / cột, không xoá — bản mã cũ vẫn đọc được CSDL mới.

**Quay lui mã:** `git checkout <commit trước>` → chạy lại. Cần trạng thái dữ liệu cũ: `backup.py restore`.

**Quay lui về SQLite** (khi PostgreSQL hỏng hẳn): đặt `VNMATEAI_DATABASE_URL=sqlite` (hoặc xoá `DATABASE_URL`) → chạy lại.
Ứng dụng dùng các tệp `.db` — dữ liệu ghi vào PostgreSQL sau thời điểm chuyển sẽ không có ở đó; lấy lại từ bản sao lưu
(bản sao lưu PostgreSQL là tệp SQLite: chép `backups\<bản>\vnmateai.db` vào thư mục ứng dụng khi máy chủ đang dừng).

---

## 13. Danh sách kiểm tra bảo mật trước khi go-live

- [ ] Ba tài khoản ban đầu đã đổi mật khẩu; tài khoản có phòng ban + cấp bảo mật đúng.
- [ ] `auto_execute = false`; `security.require_per_device_token = true`.
- [ ] Chứng chỉ TLS do CA cấp; cổng 8000 / 8888 chỉ mở cho VLAN robot; 5432 / 6379 / 8333 / 4317 không mở ra ngoài.
- [ ] `config.json`: các trường bí mật đã thành chuỗi mã hoá; xoá `certs\config.json.pre-encrypt.bak` nếu có.
- [ ] `telegram.admin_chat_ids` chỉ chứa chat nội bộ.
- [ ] Webhook nào bật thì đã có `VNMATE_WEBHOOK_<NGUỒN>_SECRET`.
- [ ] Bucket S3 không công khai; `deploy\.env`, `deploy\s3.json` cất ở nơi giữ mật khẩu của công ty.
- [ ] Sao lưu tự động chạy, có bản ở `offsite_dirs`, thử khôi phục đạt.
- [ ] `GET /api/v1/security/audit-logs/verify` → `ok: true`.
- [ ] Người trực biết nút "DỪNG KHẨN CẤP AI" và đọc [`runbook.md`](runbook.md).

---

## Phụ lục — Tệp và thư mục quan trọng

| Đường dẫn | Nội dung | Commit? |
|---|---|---|
| `config.json` | cấu hình (bí mật đã mã hoá) | không |
| `certs\` | khoá TLS, JWT, thiết bị, worker, khoá giải mã cấu hình | không |
| `deploy\.env`, `deploy\s3.json` | mật khẩu hạ tầng | không |
| `backups\` | bản sao lưu (chứa bí mật) | không |
| `logs\server.err.log` | log ứng dụng | không |
| `skills\` | skill; `registry.json` do máy chủ ghi lại mỗi lần khởi động | mã: có · `registry.json`: không cần |
| `esp32_firmware\src\secrets.h` | Wi-Fi + token robot | không |
| `reports\llm-eval\`, `reports\load-test\` | kết quả đánh giá / đo tải | tuỳ |
