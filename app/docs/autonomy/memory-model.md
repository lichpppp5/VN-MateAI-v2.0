# Mô hình bộ nhớ (Memory Model)

> 2026-10-05. Prompt Supervisor §26, §37–§40, §94. Hiện trạng có bằng chứng + quy tắc tin cậy.

## 1. Các loại bộ nhớ đang có — tách riêng, không gộp một `memory.json`

| Loại (§37) | Nơi lưu | Vòng đời | Ai ghi | Tin cậy | Mã |
|---|---|---|---|---|---|
| Working memory (lượt hiện tại) | RAM — `messages` của vòng agent | một lượt | hệ thống | — | `llm_engine.ask_async` |
| Conversation memory | RAM — cửa sổ 14 tin; thoại: 4 lượt / 1 200 ký tự | phiên, mất khi khởi động lại | hệ thống | dữ liệu người dùng | `conversation/memory_manager`, `history_pruner` |
| Long-term (cách khắc phục sự cố) | ChromaDB `storage/vector_db` | bền | **manager / admin** qua `POST /api/v1/memory/memorize` | `verified` = true chỉ khi admin ghi; có `created_by`, `source`, `timestamp` | `infrastructure/memory/cognitive_memory`, `routers/memory.py` |
| Policy memory | `config.json → security.*`, `autonomy.*` | bền, có lịch sử phiên bản | chỉ admin, có audit | tin cậy (đã duyệt) | `policy_engine`, `config_governance` |
| Operational state | SQLite: `op_tasks`, `tasks`, `approval_grants`, hàng đợi HITL (khôi phục từ audit) | bền | hệ thống qua cổng | nguồn sự thật (§26) | `tasks/ledger`, `db_manager` |
| Evidence store | SQLite `op_evidence` | bền | hệ thống (tool đã kiểm chứng) / người xác nhận | FACT có `verified` | `tasks/ledger.add_evidence` |
| Chỉ thị hệ thống | mã nguồn + `identity_core.md` | theo phiên bản mã | chỉ người (tool không ghi được) | tin cậy | `llm_engine.build_system_prompt`, `file_system._write_denied_reason` |
| Trace thoại | SQLite `voice_traces` (giữ 30 ngày) + bộ đệm 500 lượt | bền | hệ thống | số đo | `voice/voice_turn` |

## 2. Quy tắc tin cậy (§38–§39, §94)

1. Nội dung do model sinh **không** tự thành sự thật đã xác minh: không có đường nào để model tự ghi trí nhớ dài hạn (endpoint ghi yêu cầu tài khoản manager/admin; không có tool ghi trí nhớ).
2. Người gửi không tự khai `verified`, `created_by`, `source` — máy chủ ghi đè (`routers/memory.py`).
3. Chỉ thị hệ thống không ghi được bằng tool: `write_file` / `delete_item` từ chối mã nguồn, cấu hình, `identity_core.md`, chứng chỉ, CSDL (`test_file_write_protection`).
4. Kết quả tool, tài liệu, email, web, trí nhớ là **dữ liệu**: quy tắc `[RANH GIỚI TIN CẬY]` trong chỉ thị agent; quyền hạn vẫn do cổng chính sách ngoài LLM quyết định.
5. Nguồn sự thật vận hành là sổ tác vụ + giám sát (`health_monitor`), không phải trí nhớ hội thoại của model (§26).

## 3. Lưu giữ (§144, §146)

| Dữ liệu | Hạn |
|---|---|
| Trace thoại | 30 ngày (`db_manager.add_voice_trace`, dọn mỗi 200 bản ghi) |
| Hội thoại | phiên (RAM) |
| Audit | không xoá qua API (bất biến); chính sách lưu giữ dài hạn: **chưa quyết định** |
| Sổ tác vụ, bằng chứng | chưa có hạn — cần quyết định của chủ hệ thống |

## 4. Chưa làm

- Trí nhớ dài hạn chưa có `expires_at` / phạm vi phòng ban.
- Tóm tắt hội thoại dài (summary) cho thoại: hiện cắt theo cửa sổ, chưa tóm tắt.
