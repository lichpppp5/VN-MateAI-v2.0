# Mô hình leo thang (Escalation Model)

> 2026-10-05, sau Phase 4. Prompt Supervisor §32, §80–§81, §151–§152. Mô tả cái **đang chạy**; mục 4 là phần chưa làm.

## 1. Khi nào leo thang tới người

| Tình huống | Trạng thái sổ tác vụ | Ai được báo / làm gì | Mã |
|---|---|---|---|
| Hành động rủi ro ≥ 3 chưa có uỷ quyền | `WAITING_AUTHORIZATION` | Yêu cầu duyệt vào hàng đợi HITL duy nhất, báo Telegram (nút một chạm) + Portal | `policy_engine.authorize` → `tool_gate` → `hitl_manager.request_approval` |
| Đã thực thi nhưng không tự kiểm chứng được (tool rủi ro ≥ 3 chưa có bộ kiểm chứng, hoặc chạy trên máy trạm) | `ESCALATED` | Hiện ở "Cần chú ý" (Bảng điều khiển); admin xác nhận sau khi tự kiểm tra | `ledger.settle`, `POST /api/v1/ops/tasks/{id}/confirm` |
| Sự cố do giám sát phát hiện | `ESCALATED` (loại `incident`) | Cảnh báo đa kênh (Telegram / Teams / email / Slack / webhook đã kết nối) + robot; AI không tự khắc phục | `autonomous_sentinel.dispatch_incident` → `ledger.open_incident` |
| Tool thất bại / kiểm chứng trượt | `FAILED` | Hiện ở "Cần chú ý"; câu trả lời cho người dùng nói đúng là thất bại | `ledger.settle`, `verification.verify` |
| Bị chính sách từ chối | `BLOCKED` | Audit `REJECTED`; người dùng nhận lý do | `policy_engine` |
| Hết ngân sách lượt (thời gian / số lần gọi tool) | lượt dừng gọi tool, trả lời phần đã làm | Kết quả `budget_exceeded` không được báo là đã làm | `llm_engine.ask_async` |
| Dừng khẩn cấp | mọi hành động ghi bị `BLOCKED` (`kill_switch`) | Portal phát sự kiện `autonomy_changed` | `PUT /api/v1/security/autonomy` |

Hết hạn duyệt **không** tự cho phép (hàng đợi HITL: hết hạn = `expired`).

## 2. Nội dung một yêu cầu leo thang (§81)

| Trường | Lấy từ |
|---|---|
| Vấn đề | tiêu đề tác vụ / câu hỏi gốc |
| Bằng chứng | `op_evidence` (FACT / INFERENCE, nguồn, thời điểm) |
| Việc đã làm | `op_task_steps` (tool, quyết định chính sách, kết quả, kiểm chứng) |
| Trạng thái hiện tại | `op_tasks.status`, `result_summary` |
| Rủi ro | `op_tasks.risk`, `priority` |
| Cần người quyết định gì | duyệt (HITL) / xác nhận kết quả (ESCALATED) / xử lý sự cố |

Xem đủ các trường trên ở Bảng điều khiển → "AI Supervisor" → bấm một tác vụ.

## 3. Ưu tiên (§23) — tất định

`ledger.priority_for`: mức nghiêm trọng `critical` / P1 → CRITICAL; `high` / P2 hoặc rủi ro ≥ 4 → HIGH; `warning` hoặc rủi ro 3 → MEDIUM; còn lại LOW. LLM không quyết định ưu tiên.

## 4. Chưa làm

- Leo thang theo hạn chót (tác vụ `WAITING_AUTHORIZATION` quá N phút → nhắc người duyệt khác): chưa có.
- Leo thang khi độ tin cậy thấp (§80 "low confidence"): chưa có tín hiệu độ tin cậy đo được — không dùng tự đánh giá của LLM.
- Chuyển giao (handoff) cho người cụ thể theo phòng ban: chờ mô hình phòng ban (ABAC).
