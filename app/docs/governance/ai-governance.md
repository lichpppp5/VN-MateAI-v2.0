# Quản trị AI (AI Governance)

> 2026-10-05. Prompt Supervisor §125–§129, §150. Khung **sẵn sàng** để tiến tới NIST AI RMF (Govern / Map / Measure / Manage) và ISO/IEC 42001 — **không** tuyên bố đã đạt hay được chứng nhận. Mỗi dòng ghi cái đang có thật và cái còn thiếu.

## 1. Danh mục hệ thống AI (AI system inventory)

| Thành phần | Thực tế 2026-10-05 | Nguồn |
|---|---|---|
| Nhà cung cấp LLM | 9Router (OpenAI-compatible), chế độ `router`, Tri-Brain bật | `config.json → llm` (đọc qua `settings.llm`) |
| Model đang dùng | `ag/gemini-3.8-flash-low` cho controller / voice / ops; 10 model dự phòng | `settings.llm` |
| Tác nhân AI (agent_id) | `VN-MATEAI-VOICE`, `-TELEGRAM`, `-PORTAL-OPS`, `-ORCHESTRATOR`, `-CONNECTOR`, `-SENTINEL`, `-EMAIL`; người bấm trực tiếp = `HUMAN-DIRECT` | `policy_engine.KNOWN_AGENTS` |
| Kỹ năng / tool | 85 skill (`@export_skill`) + tool đăng ký động (connector, computer-use) | `skills/registry.json`, `plugin_registry` |
| Dữ liệu gửi ra LLM | câu hỏi + kết quả tool, đã che IP LAN / mật khẩu / token / chuỗi kết nối | `safety_guard.mask_sensitive_data` |
| Thoại | STT máy chủ (faster-whisper / Groq / Whisper), TTS 9Router + Edge dự phòng | `audio_processor`, `tts_stream_engine` |

## 2. Govern — vai trò và kiểm soát

| Kiểm soát | Có? | Ở đâu |
|---|---|---|
| Quyền hạn nằm ngoài LLM (Policy Engine) | có | `security/policy_engine.authorize` — mọi tool |
| Mức tự trị L0–L5, L5 không bao giờ chạy qua AI | có | `autonomy.never_autonomous_tools` |
| Con người duyệt rủi ro ≥ 3 | có | hàng đợi HITL duy nhất |
| Dừng khẩn cấp / tắt tác nhân / tắt tool | có | Trung tâm Bảo mật → "Kiểm Soát Tự Trị AI" |
| Thay đổi chính sách có audit + phiên bản | có | `autonomy_policy_change` (phiên bản trước / sau), lịch sử cấu hình khôi phục được |
| Hiến chương hành vi (§150) | một phần — quy tắc trong chỉ thị agent: không bịa số liệu, nói đúng trạng thái thất bại / chờ duyệt, nội dung ngoài là dữ liệu | `llm_engine._LIVE_DATA_RULE`, `_UNTRUSTED_DATA_RULE` |
| Phiên bản cấu hình tác nhân (§129) | một phần — `policy_version` ghi kèm mọi quyết định; model + cấu hình LLM có lịch sử cấu hình; chưa có "agent version" tách riêng | `config_governance` |
| Ranh giới quyết định của người (§149) | có cho nhóm L5 (xoá dữ liệu, chuyển tiền, cài mã ngoài); nhân sự / pháp lý: chưa phân loại tool | `DEFAULT_NEVER_AUTONOMOUS` |

## 3. Map — rủi ro

`docs/security/threat-model.md` (14 kịch bản), `docs/autonomy/risk-model.md` (thang 1–5, kiểm chứng theo mức).

## 4. Measure — đo được gì

| Chỉ số | Nguồn | Ghi chú |
|---|---|---|
| Tỷ lệ thành công tác vụ, theo trạng thái | `GET /api/v1/ops/overview`, `/api/v1/ops/tasks` | chỉ tính từ 2026-10-05 (sổ tác vụ mới có) |
| Tỷ lệ bị chính sách từ chối, theo luật | `actions.by_decision`, `by_rule` | — |
| Kết quả kiểm chứng | `actions.by_verification` | — |
| Token / số lần gọi LLM | `cost.total_tokens`, `llm_calls` | số nhà cung cấp báo; **không** quy ra tiền (chưa có bảng giá) |
| Độ trễ thoại p50 / p95 / p99 | `GET /api/v1/voice/metrics` (bền 30 ngày) | — |
| Đánh giá agent (ý định, chọn tool, tuân thủ chính sách) | `docs/evaluation/agent-evaluation.md` | bộ kịch bản tất định; chưa có bộ đánh giá với LLM thật |

## 5. Manage — sự cố và thay đổi

- Sự cố: sentinel → tác vụ `incident` (ESCALATED) + cảnh báo đa kênh; quy trình xử lý: `docs/production/runbook.md`.
- Thay đổi: mọi thay đổi chính sách / giới hạn tự trị có audit + lịch sử cấu hình; mã thay đổi qua git + CI (`pytest`).
- Đánh giá lại: chạy `scripts/bench_voice.py` và bộ test đối kháng sau mỗi thay đổi model / chính sách.

## 6. Còn thiếu để tiến tới ISO/IEC 42001

Danh mục trường hợp sử dụng có chủ sở hữu, đánh giá tác động định kỳ, quy trình phê duyệt thay đổi model chính thức, đào tạo người vận hành, đánh giá nhà cung cấp LLM (lưu trữ / xử lý dữ liệu), chính sách lưu giữ audit và sổ tác vụ.
