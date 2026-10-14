# Kế hoạch chuyển SQLite → PostgreSQL

Trạng thái: **ĐÃ CUTOVER (2026-10-06).** Ứng dụng chạy trên PostgreSQL 16 Docker. Thực tế khác kế hoạch ban đầu: thay vì viết lại từng câu SQL, lớp `pg_compat` dịch SQL kiểu SQLite sang PostgreSQL tại điểm mở duy nhất `open_sqlite`; kiểu thời gian giữ TEXT (định dạng `YYYY-MM-DD HH:MM:SS`, so sánh chuỗi như cũ); mỗi tệp DB → một schema (`vnmate`, `hr`). Toàn bộ pytest chạy trên cả hai backend. Phần dưới là kế hoạch gốc, giữ để đối chiếu.

Điều kiện tiên quyết đã đạt (Phase Data, plan §21–23):
- Mỗi bảng có **một chủ schema**: `vnmateai.db` thuộc `mateai.infrastructure.database.erp_database`, `hr_kpi.db` thuộc `mateai.infrastructure.directory.domain_sync` (giám sát chỉ đọc qua `probe_hr_database` và `check_sqlite_integrity`). `sqlite3.connect` ngoài các module này bị test RULE-014 chặn.
- Một kho tài khoản (bảng `users`); `users.json` đã xoá.
- Đường dẫn CSDL lấy từ một nơi (`VNMATEAI_DB_PATH`, `VNMATEAI_HR_DB_PATH`, mặc định theo `settings.PROJECT_ROOT`).

## 1. Kiểm kê (đo 2026-10-03, chỉ đọc, chế độ WAL)

### `vnmateai.db` — chủ: `erp_database.py`

| Bảng | Số dòng | Cột | Index | Ghi chú |
|---|---:|---:|---:|---|
| audit_logs | 266 | 9 | 4 | **Chỉ INSERT** — giữ tính chất này ở PG (REVOKE UPDATE/DELETE) |
| users | 3 | 7 | 3 | hash mật khẩu bcrypt — chuyển nguyên |
| tasks | 0 | 14 | 6 | |
| device_tokens | 0 | 5 | 1 | chỉ lưu SHA-256 |
| devices, employees, departments, enterprise_departments, department_data_sources, department_historical_metrics, unified_department_metrics, finances, attendance, records | 0 | 3–8 | 1–3 | chưa có dữ liệu |

### `hr_kpi.db` — chủ: `domain_sync.py` (đồng bộ Active Directory)

| Bảng | Số dòng | Cột | Index |
|---|---:|---:|---:|
| employees | 0 | 8 | 3 |
| computers | 0 | 6 | 3 |

Lưu ý: có **hai bảng `employees`** (ERP trong `vnmateai.db`, AD trong `hr_kpi.db`) với ý nghĩa khác nhau. Ở PostgreSQL đặt vào hai schema (`erp`, `directory`) thay vì gộp — gộp là quyết định nghiệp vụ, không phải kỹ thuật.

Dữ liệu hiện rất nhỏ (269 dòng), nên rủi ro chuyển dữ liệu thấp; rủi ro chính là **khác biệt SQL** (kiểu, `AUTOINCREMENT`, `datetime('now')`, `INSERT OR REPLACE`, chuỗi ISO cho thời gian).

## 2. Các bước

1. **Lớp kết nối**: thêm cấu hình `database.url` (env `VNMATEAI_DATABASE_URL`); giữ SQLite làm mặc định. Chỉ hai module chủ (`erp_database`, `domain_sync`) cần đổi — test RULE-014 bảo đảm không có nơi thứ ba.
2. **Schema PG**: sinh từ `sqlite_master` của hai DB, đổi kiểu (`INTEGER PRIMARY KEY AUTOINCREMENT` → `BIGSERIAL`, `TEXT` thời gian → `TIMESTAMPTZ`), tái tạo đủ index (bảng trên). `audit_logs`: role ứng dụng chỉ có `INSERT, SELECT`.
3. **Chạy test trên PG**: toàn bộ `pytest` với `VNMATEAI_DATABASE_URL` trỏ PG tạm (container `postgres:16`). Mục tiêu: cùng số test pass như SQLite.
4. **Chuyển dữ liệu**: script đọc từng bảng → ghi PG trong một transaction; kiểm **số dòng từng bảng** và **checksum** (SHA-256 của các dòng đã sắp theo khoá chính) hai phía phải bằng nhau.
5. **Cutover**: dừng máy chủ → sao lưu `vnmateai.db`, `hr_kpi.db` (+ `-wal`, `-shm`) → chạy script → đặt `VNMATEAI_DATABASE_URL` → khởi động → kiểm `/readyz`, đăng nhập, đọc audit, tạo/duyệt một yêu cầu HITL thử.
6. **Rollback**: bỏ `VNMATEAI_DATABASE_URL` và khởi động lại — ứng dụng quay về tệp SQLite đã sao lưu. Dữ liệu ghi vào PG sau cutover phải chép ngược bằng cùng script (đảo nguồn/đích) nếu muốn giữ.

## 3. Việc còn thiếu trước khi làm

- Chưa có máy chủ PostgreSQL (chủ dự án cung cấp).
- `erp_database.py` dùng cú pháp riêng SQLite ở nhiều câu lệnh — cần rà từng câu khi viết lớp kết nối (bước 1); chưa đếm.
- Khoá ghi (`threading.Lock`) hiện bảo vệ SQLite trong một tiến trình; với PG và nhiều tiến trình cần dựa vào transaction của DB.
