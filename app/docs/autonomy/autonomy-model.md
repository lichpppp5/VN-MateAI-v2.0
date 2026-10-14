# Mô hình tự trị (Autonomy Model)

> Phase 0 — 2026-10-05. Hiện trạng có bằng chứng + thiết kế đích. Không có mã mới trong Phase 0.
> Liên quan: `policy-model.md`, `risk-model.md`, `task-model.md`, `docs/security/threat-model.md`.

## 1. Hiện trạng — những gì đang tự chạy

"Tự chạy" = hành động xảy ra khi không có người vừa ra lệnh trong lượt đó.

| Thành phần | Kích hoạt | Hành động có tác dụng phụ | Qua cổng chính sách? | Dừng được? | Bằng chứng |
|---|---|---|---|---|---|
| Autonomous Sentinel | vòng 30 s | gửi cảnh báo đa kênh, đánh thức robot | không cần (chỉ thông báo) | chỉ khi tắt máy chủ | `autonomous_sentinel.py:52,230–260` |
| Proactive Manager | 08:00 và 16:00 mỗi ngày | quét việc ERP quá hạn, **nhắn Telegram trực tiếp** | không | không có nút | `proactive_manager.py:57–78,198,356` |
| Email Gateway (nếu bật) | thăm dò hộp thư | **tạo việc ERP** + **tự gửi email trả lời** cho người gửi bất kỳ | không | không có nút | `email_gateway.py:143–246` |
| Webhook Gateway | webhook có chữ ký (AWS SNS / HMAC / OCI) | dịch, khử trùng lặp, phát cảnh báo | — (chỉ thông báo) | — | `webhook_gateway.py:80–240` |
| Vòng agent (theo lượt) | người dùng hỏi | gọi tool, tối đa 6 vòng | có (`tool_gate`), trừ đa tác nhân | huỷ lượt / barge-in | `llm_engine.py:57,1046`, `agent_orchestrator.py:252,264` |
| Đa tác nhân | skill `delegate_to_multi_agent` (mức 2) | gọi hàm giao việc, chấm công, RAG **trực tiếp** | **không** | không | `agent_orchestrator.py:240–280` |
| Tác vụ nền thoại | lệnh vận hành dài | chạy tool qua vòng agent | có | theo lượt | `background_workers.py:183–186` |
| Duyệt rồi nhớ (robot / Telegram) | tác vụ đã được duyệt một lần | chạy lại cùng tool không hỏi | có (bộ nhớ duyệt) | thu hồi được | `tool_gate.py:265–300` |

**Kết luận hiện trạng.** Hệ thống có tự trị **phản ứng** (theo lượt), tự trị **định kỳ** (proactive, sentinel), và tự trị **theo sự kiện ngoài** (email, webhook). Ba nhóm sau **không có**: danh tính tác nhân, kill switch, ngân sách, sổ tác vụ, bước kiểm chứng. Email gateway giao tiếp ra **bên ngoài** mà không có chính sách.

## 2. Mức tự trị đích (L0–L5, §16)

Mức gắn với **hành động + ngữ cảnh**, không gắn với người gọi. Chính sách doanh nghiệp được hạ mức (khắt khe hơn) nhưng không vượt trần L5.

| Mức | Tên | Ý nghĩa | Ánh xạ từ hiện trạng |
|---|---|---|---|
| L0 | READ_ONLY | đọc, không tác dụng phụ — chạy ngay | rủi ro 1 (`zero_trust.RISK_LEVEL_MAP`) |
| L1 | RECOMMEND | chỉ đề xuất / soạn sẵn, người bấm thực hiện | chưa có — hiện mọi thứ hoặc chạy hoặc xin duyệt |
| L2 | LOW_RISK_AUTO | tự chạy, có audit + kiểm chứng cơ bản | rủi ro 2 |
| L3 | SUPERVISED | xin duyệt mỗi lần (HITL) | rủi ro 3–4 hiện nay |
| L4 | DELEGATED | tự chạy **sau khi đã được uỷ quyền** cho đúng tác nhân + tool + phạm vi, có hạn và thu hồi được | "duyệt rồi nhớ" của robot / Telegram (`approval_grants`) — cần thêm hạn và phạm vi |
| L5 | NEVER_AUTONOMOUS | chỉ người làm; AI không bao giờ thực thi, kể cả admin ra lệnh qua AI | hiện **không có**: rủi ro 5 chỉ là "phải duyệt", và tài khoản admin bỏ qua (`tool_gate.py:128`) |

Mặc định đề xuất cho L5 (chủ dự án duyệt): `delete_item`, `delete_records`, `drop_database`, `wipe_system`, `execute_financial_transfer`, `install_skill_from_url`, mọi thay đổi RBAC / quyền thiết bị / chính sách.

## 3. Vòng tự trị đích (§10)

```
Sự kiện (người / lịch / sentinel / webhook / email)
  → Task Ledger: tạo task (NEW) + priority            (task-model.md)
  → Lập kế hoạch: LLM đề xuất bước                    (LLM chỉ đề xuất)
  → Mỗi bước: ControlPlane.authorize(actor, agent, tool, args, context)
        kill switch → DENY? → RBAC/ABAC → rủi ro → mức tự trị → AUTO | APPROVAL | HUMAN_ONLY
  → Thực thi qua một cổng (tool_gate)
  → Kiểm chứng theo mức (NONE/BASIC/STANDARD/STRICT)  → Evidence
  → Audit (actor, agent_id, policy, risk, result, evidence_ref)
  → Báo cáo / leo thang (alert_dispatcher)
```

## 4. Giới hạn bắt buộc cho mọi vòng (§35)

| Giới hạn | Hiện có | Đích |
|---|---|---|
| Số vòng tool | 6/lượt (`llm_engine.py:57`) | giữ; thêm cho task tự trị |
| Thời gian | không (có timeout từng tool, thử model) | `MAX_TIME` theo task |
| Số lần gọi tool tổng | không | `MAX_TOOL_CALLS` theo task |
| Thử lại | breaker theo tool (`plugin_registry`) | `MAX_RETRIES` theo bước, không thử lại hành động không idempotent |
| Chi phí / token | **không đo** | đếm `usage` từ provider, ngân sách theo task / ngày |
| Phát hiện lặp | chặn gọi lại đúng tool + tham số (test `test_agent_loop_limits.py`) | giữ |
| Kill switch | **không** | toàn cục / theo tác nhân / theo tool / theo phiên — kiểm ở cổng, ngoài LLM |

## 5. Danh tính tác nhân (§11–§12)

Đích: mỗi hành động mang `human_id` (người uỷ quyền), `agent_id` (vd `VN-MATEAI-VOICE`, `VN-MATEAI-OPS`, `VN-MATEAI-SENTINEL`, `VN-MATEAI-EMAIL`), `session_id`, `request_id`, `trace_id`. Tận dụng sẵn: `caller` (RBAC) đã là danh tính người/thiết bị; `trace_id` đã có trong `VoiceTurnTrace` và `topology_events`. Thiếu: `agent_id`, và trường danh tính trong payload audit.

## 6. Việc cần chủ dự án quyết định trước Phase 2–3

| Mã | Câu hỏi | Đề xuất |
|---|---|---|
| A1 | Admin ra lệnh qua AI có được chạy tác vụ L5 không? (hôm nay: có, không hỏi) | Không — L5 chỉ làm trên giao diện quản trị có xác nhận, không qua AI |
| A2 | Email gateway có được tự trả lời ra ngoài không? | Chỉ khi bật rõ ràng + danh sách miền được phép; mặc định chỉ tạo việc, không gửi |
| A3 | "Duyệt rồi nhớ" (L4) có cần hạn dùng không? | Có — 30 ngày, thu hồi được (đã có thu hồi) |
| A4 | Ai được bật / tắt kill switch? | Admin; mọi lần bật/tắt vào audit |
