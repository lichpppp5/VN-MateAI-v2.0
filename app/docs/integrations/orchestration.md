# Lõi điều phối — giao việc cho đội agent

VN-MateAI giao việc cho các agent khác bằng **phiếu chuẩn**, theo dõi trên sổ tác vụ và **tự thẩm định kết quả**. Agent báo
"xong" chưa phải là xong. Mã: `src/mateai/application/orchestration/`. API: `/api/v1/orchestration/*`. Giao diện: tab
**Cổng Agent**, gồm ba khối Đội agent, Giao việc và Phiếu việc. Nguồn ý tưởng: `docs/architecture/integration-candidates-2026-10-15.md`.

## 1. Đội agent (agent thực thi)

| Loại | Chạy thế nào | Bằng chứng |
|---|---|---|
| `local` — vai nội bộ | Vòng agent của VN-MateAI, **chỉ thấy các công cụ được cấp**, chạy dưới danh tính **người giao**. Mọi lời gọi công cụ vẫn qua `tool_gate` (RBAC, ABAC, HITL, L5) | Các bước công cụ chạy thật, ghi thẳng vào tác vụ của phiếu |
| `a2a` — agent ngoài | Agent trong Danh bạ (Cổng Agent → Danh bạ, loại A2A). Phiếu gửi bằng chữ; kết quả gắn nhãn **dữ liệu không tin cậy** | Lời agent tự khai (suy luận) |
| `devfleet` | Dev Fleet (Ubuntu Master → worker). Dev Fleet tự xin duyệt và kiểm build / test / Git | Bằng chứng Dev Fleet đã kiểm |

Mỗi agent khai báo:
- **năng lực** (`dba`, `sysadmin`, `ba`, `dev`…);
- **trần rủi ro** nhận việc (1–4; L5 không bao giờ giao);
- **số việc chạy cùng lúc**;
- với vai nội bộ: **danh sách công cụ được cấp**. Vai nội bộ không được cầm công cụ giao việc hay sinh mã, nên không có giao việc lồng nhau.

Mỗi lần sửa agent tăng `version` và được ghi vào audit.

**Ví dụ vai "Quản trị CSDL"** (Portal → Cổng Agent → Đội agent → Thêm):
- loại `local`;
- năng lực `dba`;
- trần rủi ro 2;
- công cụ `run_local_sql_check, list_data_sources, fetch_data_source`;
- mô tả: "Chẩn đoán hiệu năng CSDL, chỉ đọc, đề xuất chỉ mục kèm câu lệnh".

## 2. Phiếu giao việc

| Trường | Ý nghĩa |
|---|---|
| `objective` | Mục tiêu cụ thể (≥ 10 ký tự). Agent không thấy hội thoại gốc |
| `capability` | Năng lực cần |
| `deliverables` | Sản phẩm phải nộp |
| `done_criteria` | **Thế nào là xong** (bảng dưới) |
| `context`, `boundaries` | Ngữ cảnh tối thiểu; ranh giới thêm. Ba ranh giới cố định luôn kèm theo: không làm việc không đảo ngược được, không chuyển việc cho agent khác, thiếu thông tin thì báo `blocked` |
| `risk` | 1–4. Từ 3 trở lên cần người có thẩm quyền duyệt (duyệt hai người / tách nhiệm vụ áp dụng nếu bật) |
| `deadline_s`, `max_attempts` | Thời hạn mỗi lượt (30 s – 24 h); số lượt tối đa (1–3) |

**Tiêu chí "xong"** — VN-MateAI tự kiểm, không dựa vào lời báo:

| Loại | Kiểm thế nào | Được tính đạt khi |
|---|---|---|
| `probe` | Chạy **một công cụ chỉ đọc** (rủi ro ≤ 2, qua chính sách, bằng danh tính người giao) rồi so `expect {path, op, value}` | Mọi rủi ro |
| `contains` | Sản phẩm nộp có chứa đoạn chữ | Mọi rủi ro |
| `evidence` | Có ít nhất N bằng chứng | Nội bộ / Dev Fleet: mọi rủi ro. Agent ngoài (tự khai): chỉ rủi ro ≤ 2 |
| `text` | AI thẩm định chấm (một lời gọi LLM riêng) | Chỉ rủi ro ≤ 2; rủi ro cao hơn cần người xác nhận |
| `human` | Người xác nhận | Luôn cần người |

AI thẩm định chạy mặc định (`review: true`) và **luôn được đánh trượt**. Lỗi khi kiểm (LLM lỗi, công cụ lỗi) không bao giờ
biến thành "đạt".

## 3. Vòng đời

```
tạo ─▶ PLANNED ─(rủi ro ≥ 3)─▶ WAITING_APPROVAL ─duyệt─▶ PLANNED
          │                         └─ từ chối / hết hạn ─▶ CANCELLED
          ▼ (còn chỗ)
      DISPATCHED ─báo cáo─▶ REVIEWING ─▶ COMPLETED            (thẩm định đạt)
          │                       ├────▶ NEEDS_CONFIRMATION   (còn tiêu chí không tự kiểm được → người xác nhận)
          │                       ├────▶ BLOCKED              (agent thiếu quyền / thông tin / chờ duyệt bước riêng)
          └─ lỗi / quá hạn / không đạt ─▶ thử lại (ưu tiên agent KHÁC cùng năng lực) ─▶ … ─▶ FAILED
```

Mỗi phiếu là một tác vụ `work_order` trong **sổ tác vụ**. Phiếu tự hiện trong hộp thư khi bị leo thang hoặc thất bại, kèm
bằng chứng của từng tiêu chí, và chỉ thành `COMPLETED` khi `verification_status = passed`.

- **Xác nhận** (quản trị viên, phiếu `NEEDS_CONFIRMATION`): bắt buộc ghi chú đã kiểm gì. Tên người và ghi chú thành bằng chứng.
- **Giao lại** = tạo **phiếu mới** cùng nội dung (định tuyến lại và duyệt lại). Hai phiếu được liên kết với nhau trong lịch sử.
- **Huỷ**: agent ngoài có thể đã làm một phần trước khi huỷ.

## 4. Định tuyến

Định tuyến tất định, không hỏi LLM. Các bước:
1. Lọc theo năng lực, trạng thái (tạm dừng, tắt trong Kiểm soát tự trị), khả dụng (thiếu công cụ, agent A2A tạm dừng, Dev Fleet tắt) và trần rủi ro.
2. Xếp theo **điểm** = (số phiếu xong đã kiểm chứng + 1) / (số phiếu đã kết thúc + 2), tính trong 90 ngày.
3. Sau đó xếp theo tải hiện tại, rồi theo mã agent.

Kết quả luôn kèm lý do chọn và lý do từng agent bị loại (xem trước bằng `POST /orders/preview`).

## 5. Ổn định

- **Giới hạn trong mã:**
  - 8 phiếu chạy cùng lúc;
  - `max_concurrent` riêng cho từng agent;
  - 20 phiếu mở mỗi người, 50 phiếu mở toàn hệ thống;
  - thời hạn mỗi lượt;
  - `max_attempts` mỗi phiếu.
- **Hàng đợi lưu trong CSDL:** khởi động lại không mất phiếu chưa giao. Phiếu đang giao dở chuyển thành `INTERRUPTED` và **không tự chạy lại**, vì agent có thể đã làm một phần.
- **Vòng nền 30 s:** giao tiếp các phiếu trong hàng đợi, đối soát phiếu duyệt bị từ chối hoặc hết hạn.
- **Công tắc dừng khẩn cấp** chặn mọi việc giao mới. Agent bị tắt trong Kiểm soát tự trị sẽ không nhận việc.

## 6. AI Ly Ly dùng thế nào

| Công cụ | Rủi ro | Ai dùng |
|---|---|---|
| `list_agent_capabilities` | 1 | Mọi người |
| `dispatch_work_order` | 2 (phiếu tự xin duyệt theo rủi ro của nó) | Vai trò Portal **quản lý / quản trị viên**; agent gọi vào cần quyền admin trong danh bạ |
| `get_work_order_status` | 1 | Người giao phiếu, quản lý, quản trị viên |

## 7. API

| Phương thức | Đường dẫn | Quyền |
|---|---|---|
| GET | `/api/v1/orchestration/executors`, `/capabilities` | quản lý, admin |
| GET | `/api/v1/orchestration/tools` | admin |
| PUT / DELETE | `/api/v1/orchestration/executors/{id}` | admin |
| POST | `/api/v1/orchestration/executors/{id}/status` | admin |
| POST | `/api/v1/orchestration/orders/preview`, `/orders` | quản lý, admin |
| GET | `/api/v1/orchestration/orders`, `/orders/{id}` | quản lý, admin |
| POST | `/api/v1/orchestration/orders/{id}/cancel`, `/retry` | người giao phiếu, admin |
| POST | `/api/v1/orchestration/orders/{id}/confirm` `{accept, note}` | admin |

Test: `tests/test_orchestration.py`, `tests/test_orchestration_api.py`, `tests/test_orchestration_ui.mjs`.
