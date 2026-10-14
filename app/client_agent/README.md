# VN-MateAI Client Agent (máy trạm)

Agent chạy nền trên máy trạm. Nó giữ một kết nối WebSocket mã hoá (WSS, ghim chứng chỉ máy chủ) về máy chủ VN-MateAI để:
- nhận lệnh tự động hoá;
- gửi số đo sức khoẻ máy;
- **tự cập nhật** khi máy chủ có bản mới.

Mỗi máy có **khoá riêng**, thu hồi được từng máy.

Phiên bản: `AGENT_VERSION` trong `agent.py`. Tài liệu cho quản trị viên (build `.exe` / macOS, ký số, triển khai hàng loạt): `docs/integrations/agent-distribution.md`.

## Cài đặt

Trên Portal, bấm **Tải Agent**, chọn nền tảng, nhập tên gợi nhớ máy (tuỳ chọn) rồi bấm **Tải gói cho 1 máy**. Mỗi gói chứa **một mã đăng ký dùng một lần**, hết hạn sau 7 ngày. Máy thứ hai cần tải gói khác.

### Windows: `VNMateAgent.exe` (không cần Python)
1. Giải nén cả thư mục: `VNMateAgent.exe`, `config.json` và `server_cert.pem` phải nằm cùng chỗ.
2. **Bấm đúp `VNMateAgent.exe`.** Agent sẽ:
   - tự cài vào `%LOCALAPPDATA%\VNMateAI\Agent`;
   - tự chạy mỗi khi đăng nhập;
   - hiện trong **Settings → Apps** để gỡ.
   Không cần quyền admin.
3. Cài im lặng (GPO / Intune / script): `VNMateAgent.exe --quiet`.

### macOS
- **Có bản build** (gói `VN-Mate_Agent_macOS.zip`):
  1. `chmod +x VNMateAgent && ./VNMateAgent`. Agent tự cài vào `~/Library/Application Support/VNMateAI/Agent` và tạo LaunchAgent `vn.mateai.agent`.
  2. Lần đầu: vào System Settings → Privacy & Security để cho phép chạy, và cấp quyền Screen Recording nếu cần chụp màn hình.
- **Gói Python:** cần Python 3.10+. Chạy `bash install_agent_macos.sh`; gỡ bằng `bash uninstall_agent_macos.sh`.

### Gói Python trên Windows (dự phòng)
Bấm đúp `install_agent.bat`. Script tạo `.venv`, cài thư viện và đăng ký Task Scheduler. Gỡ bằng `uninstall_agent.ps1`.

### Sau khi cài
- Máy hiện trên **/admin/topology** (CPU, RAM, ổ đĩa, phiên bản) và trong hộp thoại **Tải Agent → Máy trạm đã đăng ký**.
- Log nằm ở `logs/agent.log` trong thư mục cài.

## Đăng ký và khoá riêng từng máy

1. `config.json` trong gói có `enroll_code`: mã dùng **một lần**.
2. Lần chạy đầu, Agent gọi `POST /api/v1/agent/enroll` để đổi mã lấy **khoá thiết bị**:
   - khoá lưu ở `device.json` (quyền 600);
   - mã bị xoá khỏi config;
   - dùng lại mã hoặc chép gói sang máy khác đều bị từ chối.
3. Các lần sau, Agent nối `/ws/client` bằng khoá thiết bị. **Tên máy (`client_id`) do máy chủ cấp**, Agent không tự xưng là máy khác được.
4. **Thu hồi một máy:** Portal → Tải Agent → Máy trạm đã đăng ký → **Thu hồi**. Máy bị cắt kết nối ngay; các máy khác không ảnh hưởng.
5. **Mã cài thủ công:** trong cùng hộp thoại, bấm "+ Tạo mã" rồi điền `"enroll_code"` vào `config.json`.

Máy chủ chỉ lưu SHA-256 của mã và khoá. Thử mã sai quá 10 lần trong 10 phút từ một IP thì bị chặn (HTTP 429). Secret chung cũ (`enrollment_token`) chỉ còn được nhận từ chính máy chủ (worker cục bộ), trừ khi bật `security.allow_shared_worker_secret`.

## Tự cập nhật

- **Khi nào kiểm tra:** máy chủ báo `update_available` ngay khi Agent kết nối; Agent cũng tự kiểm tra 1 phút sau khi kết nối và mỗi 6 giờ.
- **Gói nào:** Agent hỏi `GET /api/v1/agent/update/manifest?package=<windows-exe|macos-bin|source>` để lấy phiên bản, kích thước và SHA-256. Chỉ cập nhật khi bản mới **lớn hơn**, không bao giờ hạ phiên bản.
- **Kiểm tra gói:** tải qua kết nối đã ghim chứng chỉ, kèm khoá thiết bị; **kiểm SHA-256 và kích thước** trước khi cài. Sai thì bỏ, và Agent tiếp tục chạy bản cũ.
- **Cách thay thế:**
  - Bản build: ghi `VNMateAgent.new.exe`, thoát; bản mới chép đè rồi tự khởi động lại.
  - Bản Python: chép mã mới đè lên, **không đụng** `config.json`, `device.json`, `server_cert.pem`, `logs/`, `.venv/`, rồi khởi động lại.
- **Báo cáo:** tiến trình cập nhật hiện trên trang /admin/topology (sự kiện `agent_update`).
- **Không tự cập nhật khi:** chạy từ mã nguồn dự án (có `.git`, tức worker cục bộ), đặt `VNMATE_AGENT_NO_UPDATE=1`, hoặc máy chủ đặt `agent_updates.enabled: false`.

## Tuỳ chọn

| Tham số / biến môi trường | Ý nghĩa |
|---|---|
| `--quiet` / `VNMATE_AGENT_QUIET=1` | Cài / gỡ im lặng, không hộp thoại |
| `--uninstall` | Gỡ: bỏ tự chạy, dừng Agent, xoá thư mục cài |
| `VNMATE_AGENT_HOME` | Đổi thư mục cài (bản build) |
| `VNMATE_AGENT_NO_AUTOSTART=1` | Không đăng ký tự chạy (đóng gói / thử nghiệm) |
| `VNMATE_AGENT_LOCK_PORT` | Cổng khoá một-bản (mặc định 58431) |
| `--server`, `--id`, `VNMATE_MASTER_URL`, `VNMATE_CLIENT_ID` | Chạy tay để dò lỗi (bản Python) |

## Bảo mật

- **Kết nối:** WSS + `CERT_REQUIRED` theo `server_cert.pem` đi kèm. Mã đăng ký và khoá thiết bị bị che trong log (`token=***`).
- **Lệnh nguy hiểm:** tắt tiến trình, PowerShell, dịch vụ Windows, xoá file… đi qua **cổng phê duyệt (HITL)** trên máy chủ và được ghi `audit_logs`. Chụp màn hình cũng có audit.
- **Cài skill từ xa:** chỉ nhận tên dạng `ten_skill.py`, không thư mục.
- **Giới hạn khác:** không tắt tiến trình có PID ≤ 4; chỉ một bản Agent mỗi máy.
- **Lỗi bất ngờ:** ghi vào `logs/agent.log` rồi thoát. Bản `.exe` không hiện hộp thoại lỗi (hộp thoại sẽ treo Agent trên máy không người ngồi).

## Giao thức (Agent ↔ máy chủ)

| Chiều | `action` / API | Nội dung |
|---|---|---|
| Agent → máy chủ | `POST /api/v1/agent/enroll` | `code`, `hostname`, `platform`, `package`, `agent_version` → `client_id`, `device_token` |
| Agent → máy chủ | `register` | `hostname`, `platform`, `ip`, `skills`, `agent_version`, `package` |
| Agent → máy chủ | `heartbeat` (30 s) | `cpu_percent`, `ram_percent`, `disk_percent`, `uptime_s`, `skills_count`, `agent_version`, `package` |
| Agent → máy chủ | `update_status` | `installing` (from → to) / `failed` (lỗi) |
| Máy chủ → Agent | `update_available` | `version`, `package` |
| Máy chủ → Agent | `execute`, `install_skill`, `monitor`, `kill_process`, `task_popup`, `show_visual`, `ping` | trả `result` / `install_result` / `monitor_result` / `kill_result` / `task_response` / `visual_result` / `pong` |

## Kỹ năng có sẵn (`skills/`)

| File | Kỹ năng |
|---|---|
| `pc_control_skills.py` | `get_system_info`, `list_processes`, `kill_process`, `open_application`, `search_files`, `get_clipboard`, `set_clipboard`, `get_network_info`, `set_system_volume`, `run_powershell_command` |
| `monitoring_skills.py` | `capture_screen_base64`, `get_active_processes`, `get_network_connections`, `check_peripherals`, `security_audit` |
| `sysadmin_skills.py` | `manage_windows_service`, `run_local_sql_check` |
| `file_system.py` | `list_directory`, `read_file`, `write_file`, `delete_item` |
| `excel_records_skill.py` | `append_genealogy_record` |
| `worker_health_check.py` | `worker_health_check`: CPU / RAM / ổ đĩa đo thật |
| `custom_skills.py` | `test_ping_host`: ping thật, mặc định tới máy chủ |

Overlay biểu đồ: lệnh `show_visual` → `overlay_ui.py`. Skill `display_visual_data` chạy ở máy chủ.

## Cấu trúc

```
client_agent/
├── agent.py               # Điểm vào: đăng ký, kết nối, nhịp tim, điều phối lệnh, tự cập nhật
├── agent_runtime.py       # Vòng đời: tự cài (.exe/macOS), đăng ký mã, cập nhật, gỡ
├── install_agent.bat/.ps1 / uninstall_agent.ps1          # Gói Python trên Windows
├── install_agent_macos.sh / uninstall_agent_macos.sh     # Gói Python trên macOS
├── popup_ui.py / overlay_ui.py
├── core/plugin_manager.py
├── skills/
└── (sau khi cài) config.json, device.json, server_cert.pem, logs/
```
