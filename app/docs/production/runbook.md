# Runbook vận hành VN-MateAI

> 2026-10-05. Dành cho người trực vận hành. Mỗi mục: **dấu hiệu → kiểm tra → xử lý → xác minh**. Lệnh dùng PowerShell trên máy chủ Windows, thư mục `D:\VNMateaiv1`.

## 0. Lệnh nền

| Việc | Lệnh / nơi |
|---|---|
| Sức khoẻ | `GET https://localhost/livez`, `/readyz`, `/startupz` |
| Dừng máy chủ | `Get-CimInstance Win32_Process -Filter "Name='python.exe'" \| ? { $_.CommandLine -like '*main.py*' } \| % { Stop-Process -Id $_.ProcessId -Force }` |
| Chạy máy chủ | `Start-Process .venv\Scripts\python.exe main.py -WindowStyle Hidden -RedirectStandardOutput logs\server.out.log -RedirectStandardError logs\server.err.log`, chờ ~35 s rồi kiểm `/readyz` |
| Log | `logs\server.err.log` (log ứng dụng) |
| AI đang làm gì | Portal → Bảng điều khiển → "AI Supervisor"; `GET /api/v1/ops/overview` |
| Dừng khẩn cấp AI | Portal → Trung tâm Bảo mật → "DỪNG KHẨN CẤP AI" (hoặc `PUT /api/v1/security/autonomy {"kill_switch": true, "reason": "..."}`) |
| Sao lưu | `python scripts/backup.py create` (tự kiểm chứng) |

## 1. Máy chủ không phản hồi
- Kiểm tra: `/livez`; tiến trình `python main.py` còn không; cuối `logs\server.err.log`.
- Xử lý: dừng → chạy lại (mục 0). Lỗi lúc khởi động: đọc dòng `ERROR` đầu tiên trong log.
- Xác minh: `/readyz` = 200; Portal đăng nhập được.

## 2. LLM không trả lời ("hệ thống quá tải")
- Dấu hiệu: câu trả lời "quá tải"; log `[NineRouter TIMEOUT]`, `Dừng thử model: hết 12s`.
- Kiểm tra: Portal → Tích hợp → 9Router; `GET http://localhost:20128/v1/models` trên máy chủ.
- Xử lý: khởi động lại 9Router; đổi model chính ở "Quản lý Trợ lý AI" sang model đang chạy. Model lỗi tự xếp cuối 120 s.
- Xác minh: hỏi một câu thường; `GET /api/v1/voice/metrics` có lượt `llm` mới.

## 3. TTS không có tiếng
- Dấu hiệu: có chữ, không có tiếng. Chữ vẫn phải hiện (TTS lỗi không chặn trả lời).
- Kiểm tra: log `[TTS]`; `GET /api/v1/tts/voices`.
- Xử lý: 9Router `/audio/speech` lỗi thì engine tự dùng Edge; Edge cũng lỗi → kiểm mạng ra ngoài.

## 4. WebSocket / robot không kết nối
- Kiểm tra log: `Từ chối thiết bị ... thiếu hoặc sai token`; `[DEPRECATED] ... /ws/audio-stream`.
- Xử lý: token thiết bị (Trung tâm Bảo mật → Thiết bị & Robot); firmware dùng `/api/v1/xiaozhi/ws`.

## 5. Cơ sở dữ liệu lỗi
- Dấu hiệu: `database is locked`, `malformed`.
- Kiểm tra: `python scripts/backup.py create` (chạy `integrity_check` trên bản chụp).
- Xử lý: dừng máy chủ → `python scripts/backup.py restore backups\<bản gần nhất đạt> --yes` (tự lưu trạng thái hiện tại vào `backups\pre-restore-*`).
- Xác minh: `python scripts/backup.py verify <thư mục>` = ĐẠT; `/readyz` = 200.

## 6. Redis / nhiều tiến trình
Không áp dụng: hệ thống chạy một tiến trình, không dùng Redis (`docs/production/readiness-score.md`).

## 7. Sự cố bảo mật (§169: phát hiện → khoanh vùng → điều tra → loại bỏ → khôi phục → rút kinh nghiệm)
1. **Khoanh vùng**: bật DỪNG KHẨN CẤP AI (chỉ còn tác vụ chỉ đọc); tắt tác nhân bị nghi (ví dụ `VN-MATEAI-TELEGRAM`) trong "Kiểm Soát Tự Trị AI".
2. **Giữ bằng chứng**: KHÔNG xoá log / audit; chạy `python scripts/backup.py create`.
3. **Điều tra**: `GET /api/v1/security/audit-logs` (lọc `REJECTED`, `autonomy_policy_change`, `login`); `GET /api/v1/ops/tasks?days=7`.
4. **Loại bỏ**: thu hồi uỷ quyền thiết bị (Thiết bị & Robot); đổi mật khẩu; xoay khoá (`certs/jwt_secret.key` → mọi phiên phải đăng nhập lại).
5. **Khôi phục**: tắt dừng khẩn cấp, ghi lý do.

## 8. Agent tự trị chạy mất kiểm soát
- Dấu hiệu: nhiều tác vụ / tin nhắn bất thường trong "AI Supervisor"; tỷ lệ bị chặn tăng đột biến.
- Xử lý: DỪNG KHẨN CẤP; xem tác vụ gần nhất (bước, quyết định chính sách, ai yêu cầu); hạ `max_tool_calls_per_turn` / `max_agent_seconds`; đưa tool liên quan vào "Tool đang tắt".

## 9. Chính sách bị đổi sai
- Kiểm tra: audit `autonomy_policy_change` (giá trị trước / sau, người đổi, lý do); Cấu hình → Lịch sử.
- Xử lý: khôi phục phiên bản cấu hình trước trong Lịch sử cấu hình.

## 10. Nghi rò dữ liệu
- AI chỉ gửi Telegram tới chat nội bộ đã cấu hình; email gateway chỉ tự trả lời khi bật + miền được phép.
- Kiểm tra audit `email_auto_reply`, log `Từ chối gửi tới chat ngoài danh sách nội bộ`.
- Xử lý như mục 7.
