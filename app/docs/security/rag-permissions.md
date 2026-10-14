# Phân quyền đọc kho tri thức (RAG / GraphRAG)

Không phải ai cũng được đọc mọi tài liệu: bảng lương ban giám đốc, hợp đồng, quy trình tài chính… Mỗi **tài liệu** trong kho tri thức có một khai báo quyền; hệ thống **lọc trước khi nội dung đến người hỏi hoặc AI**.

> **Trạng thái trung thực.** Đã kiểm thử với bộ sưu tập giả (`tests/test_rag_permissions.py`, 13 ca). Chưa thử trên kho ChromaDB lớn thật. Quyền áp theo **tài liệu**, chưa theo từng đoạn.

## Quy tắc

Mỗi tài liệu có:

| Trường | Giá trị |
|---|---|
| Mức phân loại | `PUBLIC` (cấp 0) · `INTERNAL` (cấp 1, **mặc định**) · `CONFIDENTIAL` (cấp 3) · `RESTRICTED` (cấp 4) |
| Phòng ban được xem | danh sách (để trống = mọi phòng ban) |

Người hỏi được xem khi **cấp bảo mật của họ ≥ cấp của tài liệu** VÀ (tài liệu không giới hạn phòng ban HOẶC họ thuộc đúng phòng ban). **Admin xem tất cả.** Cấp bảo mật và phòng ban lấy từ tài khoản (Quản lý người dùng) — cùng thuộc tính ABAC đang dùng cho các công cụ khác.

- Tài liệu **chưa khai báo = INTERNAL, mọi phòng ban** → hành vi cũ không đổi sau khi nâng cấp.
- **Không rõ người hỏi** (lời gọi nội bộ không có danh tính) → coi như cấp 1, không phòng ban: thấy INTERNAL trở xuống, **không bao giờ thành admin**.
- **Không đọc được bảng quyền** (lỗi CSDL) → người không phải admin **không thấy gì** (fail-closed).

## Phạm vi áp dụng

Cùng một bộ lọc cho: tìm kiếm vector (`query_company_policy`, `/api/v1/enterprise/rag/query`), nhánh BM25 và **quan hệ đồ thị tri thức** (mỗi quan hệ gắn tài liệu nguồn; nguồn bị ẩn thì quan hệ bị ẩn), `GraphRAG` (`/api/v1/enterprise/graph-rag/...`) và danh sách tài liệu. Kết quả chỉ nêu **số đoạn bị ẩn** (`hidden_by_permission`), **không** nêu tên hay nội dung.

## Quản trị (admin)

| API | Việc |
|---|---|
| `GET /api/v1/enterprise/rag/acl` | tài liệu + quyền hiện tại |
| `PUT /api/v1/enterprise/rag/acl/{doc_name}` | `{"classification": "CONFIDENTIAL", "departments": ["Kế toán"]}` |
| `DELETE /api/v1/enterprise/rag/acl/{doc_name}` | về mặc định |

Mọi thay đổi ghi nhật ký kiểm toán (`rag_acl_set`, `rag_acl_reset`). Chưa có màn hình quản trị — dùng API (hoặc `/docs`).

## Giới hạn

- Quan hệ đồ thị trùng giữa nhiều tài liệu chỉ nhớ **tài liệu nguồn đầu tiên**; nếu đó là tài liệu hạn chế, quan hệ bị ẩn với người khác (thà ẩn thừa còn hơn lộ).
- Quan hệ có sẵn từ dữ liệu mẫu (không có tài liệu nguồn) không bị lọc.
- Đổi tên tệp khi nạp lại tạo tài liệu mới có quyền mặc định — khai báo lại quyền cho tên mới.
- Câu trả lời tự do do LLM sinh ra dựa trên đoạn đã lọc; hệ thống không kiểm tra nội dung LLM đã học sẵn.
