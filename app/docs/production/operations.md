# Vận hành VN-MateAI

Dành cho: người trực vận hành hệ thống.

## 1. Dữ liệu cần sao lưu

| Đường dẫn | Nội dung | Mất thì |
|---|---|---|
| `vnmateai.db` (+ `-wal`, `-shm`) | tài khoản, ERP (phòng ban, nhân viên, công việc `tasks`), `audit_logs` | mất tài khoản và lịch sử audit |
| `hr_kpi.db` | đồng bộ Active Directory (employees, computers) | đồng bộ lại từ AD được |
| `storage/vector_db/` | trí nhớ sự cố (ChromaDB, chế độ `local`) | mất "kinh nghiệm" xử lý sự cố |
| `config.json` | cấu hình | phải cấu hình lại |
| `certs/` | khoá JWT, secret worker/thiết bị, chứng chỉ TLS | mọi phiên, agent, thiết bị phải cấp lại |
| `skills/` (skill tự sinh `auto_*.py`, `custom_skills.py`) | kỹ năng do AI/người dùng thêm | |

CSDL dùng chế độ WAL: **không** chép file `.db` khi máy chủ đang chạy. Dùng sao lưu trực tuyến của SQLite:

```bash
sqlite3 vnmateai.db ".backup 'backup/vnmateai-$(date +%F).db'"
sqlite3 hr_kpi.db   ".backup 'backup/hr_kpi-$(date +%F).db'"
```

Khôi phục: dừng máy chủ → chép bản sao lưu đè lên `vnmateai.db` (xoá `-wal`/`-shm` cũ) → khởi động → kiểm `/readyz`.

## 2. Log

- Ứng dụng ghi log ra stdout/stderr (không tự ghi file) — để dịch vụ hệ thống chuyển hướng/xoay vòng log.
- Bộ đệm 600 dòng gần nhất (đã che bí mật): portal → Nhật ký, hoặc `GET /api/v1/logs/recent?limit=…` (mặc định 200, tối đa 600).
- Audit an ninh nằm trong CSDL (`audit_logs`), không trong log.

## 3. Giám sát

| Kiểm | Cách |
|---|---|
| Sống / sẵn sàng | `/livez`, `/readyz` (xem deployment.md §6) |
| Model LLM hỏng | log `Tạm xếp cuối model '<tên>' trong <N>s: <lý do>`; 120 s = lỗi tạm thời, 3600 s = model đã ngừng hoặc hết quota |
| CSDL | Sentinel cảnh báo khi CSDL bị khoá lâu hoặc `PRAGMA quick_check` báo lỗi toàn vẹn |
| Hàng chờ duyệt | portal → Bảo mật; `GET /api/v1/enterprise/hitl/pending` |
| Thiết bị / máy trạm / worker | portal → Topology; `GET /api/v1/clients`, `/api/v1/worknodes/status` |

## 4. Sự cố thường gặp

| Triệu chứng | Nguyên nhân thường gặp | Xử lý |
|---|---|---|
| Lượt hỏi đầu sau khởi động chậm (20–90 s), các lượt sau nhanh | danh sách model có model đã ngừng / hết quota / timeout; trí nhớ model hỏng nằm trong bộ nhớ, mất khi khởi động lại | bỏ model đó khỏi `llm.*_models`; đặt model đang chạy lên đầu |
| Trợ lý trả lời "… is no longer available" | (đã sửa) provider giờ tự bỏ qua câu này và thử model khác | nếu vẫn gặp: mọi model đều đã ngừng → cập nhật danh sách model |
| Biểu đồ phân tích dùng "SQL dự phòng" | model sinh SQL hỏng/hết quota/trả câu cụt | xem `llm_error` trong phản hồi; sửa danh sách model chuyên gia |
| Telegram không gửi gì | `telegram.enabled` = false, token không đúng dạng `<số>:<chuỗi>`, hoặc `admin_chat_ids` trống | điền đúng ba mục; dùng nút "Kiểm tra kết nối" |
| Robot ESP32 không kết nối (HTTP 403) | thiếu / sai device token | deployment.md §8 |
| HUD báo "chưa đăng nhập" khi nói | HUD mở không có phiên đăng nhập | đăng nhập portal rồi mở lại HUD |
| Worker không lên grid (401) | thiếu `VNMATE_ENROLLMENT_TOKEN` | deployment.md §9 |
| Đăng nhập qua `http://…:8000` trả 404 | đúng thiết kế — cổng 8000 chỉ cho thiết bị | dùng `https://` cổng 443 |
| Lưu cấu hình báo lỗi đọc config | `config.json` hỏng JSON — hệ thống từ chối ghi đè để không mất cấu hình | sửa JSON bằng tay hoặc khôi phục bản sao lưu |

## 5. Khởi động lại

An toàn bất cứ lúc nào. Mất: yêu cầu duyệt HITL đang chờ (pending action của hội thoại được khôi phục từ audit trong 2 giờ), phiên thoại đang mở, trí nhớ model hỏng (lượt đầu chậm lại).
