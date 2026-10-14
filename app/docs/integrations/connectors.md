# Đấu nối hạ tầng doanh nghiệp: khai báo, không cần viết mã

Dành cho quản trị viên. VN-MateAI lấy dữ liệu và can thiệp hạ tầng sẵn có (giám sát, ITSM, ảo hoá, sao lưu, CSDL) qua **một bộ nối chung điều khiển bằng khai báo**. Gắn vào hạ tầng thật chỉ cần điền khai báo; không sửa mã.

> **Trung thực về độ tin cậy.** Logic giao thức đã được kiểm thử với máy chủ giả mô phỏng theo tài liệu API của hãng (`tests/fake_enterprise.py`: đăng nhập vCenter/GLPI/Veeam, OAuth2, phân trang, TLS tự ký, JSON-RPC kiểu Zabbix…). Các **mẫu** (preset) chưa được thử trên hệ thống thật của hãng và mang cờ `verified=false`. Sau khi lưu, luôn bấm **Thử kết nối** (hoặc **Rà Soát Kết Nối**) trên hạ tầng thật; khác biệt phiên bản sửa ngay trong khai báo.

## 1. Thêm nguồn

Tab **Tích hợp → Kết nối → Thêm nguồn**:

1. Chọn **Mẫu hạ tầng có sẵn** (Prometheus, Grafana, Zabbix, GLPI, Jira, ServiceNow, Proxmox VE, vCenter, Veeam) hoặc tự khai báo.
2. Điền **Địa chỉ API** và **Khoá**. Khoá được mã hoá khi lưu (`enc:v1:`), không bao giờ hiện lại, không đưa cho AI.
3. Lưu → **Thử kết nối**.

Phần còn lại (đăng nhập, truy vấn, thao tác, phân trang, TLS, CSDL) nằm trong ô **Khai báo nâng cao (JSON)**. Tệp lưu ở `config/data_sources.json` (quyền 600, không đưa lên Git).

## 2. Loại nguồn

| `kind` | Dùng cho | Ghi chú |
|---|---|---|
| `rest` | API HTTP/JSON | xác thực: `none`, `bearer`, `basic`, `header`, `query`, `oauth2_client`, `login` (lấy token phiên) |
| `sql` | CSDL chỉ đọc | `sqlite`, `postgresql` có sẵn; `mysql` (`pymysql`), `mssql` (`pymssql`/`pyodbc`), `oracle` (`oracledb`) cài thêm: `pip install -r requirements-connectors.txt` |

### Truy vấn (đọc) và thao tác (ghi)

- `queries`: truy vấn **đặt tên** có tham số `{tham_so}` (kiểu, mặc định, min/max, `pattern`). AI chỉ gọi tên + tham số, không tự soạn URL/SQL.
- `actions`: thao tác can thiệp (POST/PUT/PATCH/DELETE). Rủi ro luôn **≥ 3** → **bắt buộc người có thẩm quyền duyệt (HITL)**; không bao giờ tự thử lại; AI chỉ admin được gọi (`run_data_source_action`).
- Tham số đường dẫn bị chặn ký tự thoát (`../`, `/`, `?`); trang kế của phân trang bị chặn nếu khác origin.
- SQL: chỉ một câu `SELECT/WITH`, kiểm lúc lưu **và** lúc chạy; phiên CSDL mở chế độ chỉ đọc (PostgreSQL chặn cả `nextval`…), có timeout và trần số dòng.

### TLS nội bộ

Chứng chỉ tự ký làm kết nối thất bại kèm gợi ý. Khai báo `ca_bundle` (khuyên dùng) hoặc `verify_ssl: false`; mTLS dùng `client_cert` + `client_key`.

## 3. Quyền của AI

| Vai trò | Đọc (`list_data_sources`, `fetch_data_source`, xuất file) | Can thiệp (`run_data_source_action`) |
|---|---|---|
| admin | có | có, **luôn qua duyệt** |
| it_support, operator, viewer | có (it_support được bổ sung) | không |

Truy cập còn chịu kiểm soát ABAC (độ nhạy dữ liệu) và nhật ký kiểm toán bất biến.

## 4. Kiểm thử bằng máy chủ giả

```
.venv/Scripts/python -m pytest -q tests/test_connector_hub.py tests/test_sql_connector.py \
    tests/test_connector_ai_tools.py tests/test_connector_presets.py tests/test_legacy_connectors.py
```

## 5. Chưa làm / cần hạ tầng thật

- Thử mẫu trên hệ thống thật của từng hãng (đổi `verified` sau khi xác nhận).
- Connector AWS/OCI/e-Invoice cũ chưa có test với máy chủ giả (Paperless đã có; test này đã bắt được lỗi map `correspondent`/`tags` khi API trả số ID).
- SNMP, LDAP/AD trực tiếp, SSH/WinRM, hàng đợi tin nhắn: chưa có kiểu nguồn riêng.
