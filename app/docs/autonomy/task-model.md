# Mô hình tác vụ (Task Model)

> Phase 0 — 2026-10-05. Mục tiêu §20–§23, §153: sổ tác vụ độc lập với giao diện chat, có máy trạng thái, không cho "hoàn thành" khi chưa kiểm chứng.

## 1. Hiện trạng — 4 khái niệm "task" khác nhau

| # | Khái niệm | Lưu ở | Trạng thái | Ai tạo | Bằng chứng |
|---|---|---|---|---|---|
| T1 | Việc giao cho máy trạm (popup) | bảng `tasks` (`dept_id IS NULL`, `client_id` = máy) | `pending` → `completed` / `issue` / `failed` / `cancelled`; có hạn, người giao, lúc phản hồi | Portal, skill `lean_hr` | `task_manager.py`, `db_manager.close_pending_task` |
| T2 | Công việc ERP | bảng `tasks` (`dept_id`, `assignee_id`, `title`, `due_date`, `created_by_ai`) | chuỗi tự do | import Excel, email gateway, AI (`create_erp_task`) | `erp_database.py:1073–1120` |
| T3 | Tác vụ nền của lượt thoại vận hành | RAM | chạy / xong | `voice_turn` | `background_workers.py:183–186` |
| T4 | Yêu cầu phê duyệt (HITL) | RAM + khôi phục từ `audit_logs` | `pending` / `approved` / `rejected` / `expired` | cổng tool | `zero_trust.py:127–779` |

Còn có `state_manager` giữ "hành động vừa hoàn thành" để agent tránh làm lại (RAM).

**Không cái nào** là sổ tác vụ tự trị: không có mục tiêu cha, ưu tiên, các bước, bằng chứng, kiểm chứng, leo thang. T1 và T2 dùng chung bảng `tasks` (một chủ schema: `ensure_tasks_table`).

## 2. Thiết kế đích — Task Ledger

Tái dùng bảng `tasks` cho **dữ liệu nghiệp vụ** (T1, T2 giữ nguyên). Sổ tác vụ tự trị là **bảng riêng** vì vòng đời khác hẳn (nhiều bước, bằng chứng), nhưng **một** module quản lý và liên kết được với `tasks.id` khi một bước là giao việc.

```
op_tasks(
  task_id, goal_id?, parent_task_id?,
  created_at, created_by (human/service), assigned_agent (agent_id),
  priority (CRITICAL|HIGH|MEDIUM|LOW), risk (1..5),
  status, current_step, deadline, approval_id?,
  result_summary, verification_status, trace_id
)
op_task_steps(step_id, task_id, tool_id, args_hash, policy_decision, risk,
              started_at, ended_at, outcome, evidence_id?)
op_evidence(evidence_id, task_id, source, collected_at, tool_id,
            ref (đường dẫn / id, không chép dữ liệu nhạy cảm), conclusion,
            kind (FACT|INFERENCE), verified)
```

### Máy trạng thái (§21–§22)

```
NEW → ANALYZING → PLANNED → WAITING_AUTHORIZATION → AUTHORIZED → EXECUTING → VERIFYING → COMPLETED
                                     │                                  │           │
                                     └→ CANCELLED                       └→ FAILED   └→ FAILED (kiểm chứng trượt)
bất kỳ → BLOCKED (chính sách / kill switch) → ESCALATED
```

Luật bất biến: chỉ vào `COMPLETED` từ `VERIFYING` với `verification_status = passed`; `FAILED / TIMEOUT / UNKNOWN / PARTIAL` phải báo đúng (§151).

## 3. Ưu tiên (§23) — luật xác định, LLM chỉ giải thích

`priority = f(môi trường, tác động bảo mật, số người ảnh hưởng, SLA, hạn)`; nguồn số liệu là `health_monitor` / sentinel, không phải lời LLM.

## 4. Sự cố (§153–§154)

Sentinel hiện phát cảnh báo (`autonomous_sentinel.py:230`). Đích: sentinel **tạo task loại incident** (mức độ, nguồn, tài sản, dòng thời gian từ `topology_events`), không tự xử lý; xử lý đi qua vòng ở `autonomy-model.md` §3.

## 5. Chưa đo

Tỷ lệ thành công / thất bại tác vụ tự trị (§29, §73): **chưa đo được** vì chưa có sổ tác vụ.
