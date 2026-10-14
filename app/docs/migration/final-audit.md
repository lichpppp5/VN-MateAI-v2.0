# Kiểm toán cuối

## 0. Prompt Supervisor — audit cuối (P15), 2026-10-05

Từ commit `128c87d` (trước Phase 0) tới `5f03aca` + tài liệu P15: 10 commit, 104 tệp (30 thêm, 18 xoá, 56 sửa), +8 110 / −1 469 dòng (gồm số đo bench JSON và tài liệu). 714 pytest + 14 test Node ĐẠT. Trả lời 30 câu §213 bằng bằng chứng mã:

| # | Câu hỏi | Trả lời |
|---|---|---|
| 1 | Đường thoại chuẩn | `application/voice/voice_turn.process_voice_turn` — 5 kênh (portal, HUD, robot, mic máy chủ, REST) đều gọi |
| 2 | Abstraction LLM | `infrastructure/llm/llm_provider.BaseLLMProvider` (Direct / NineRouter / TriBrain); ngoại lệ có ghi: client Whisper (STT) — RULE-011 = 0 ngoài danh sách cho phép |
| 3 | Đường TTS | `infrastructure/tts/tts_stream_engine.TTSStreamEngine` — RULE-012 = 0 (skill 9Router đã chuyển qua engine) |
| 4 | Giao thức WS thoại | `/ws/v1/voice-stream` (`docs/realtime/protocol.md`); HUD còn schema riêng (L10); robot `/api/v1/xiaozhi/ws` |
| 5 | Registry tool | thực thi: một cổng; danh mục: **hai** — `core/plugin_manager` (skill) + `plugin_registry` (tool đăng ký động: connector, computer-use). Lý do còn hai: chưa gộp (L5) |
| 6 | Registry skill | `core/plugin_manager` (`@export_skill`, 85 skill) |
| 7 | Policy Engine | `application/security/policy_engine.authorize` |
| 8 | Đường phân quyền | một: mọi thực thi tool gọi `authorize()` — qua `tool_gate.run_tool_with_policy` (AI) hoặc `zero_trust.execute_with_hitl` (người bấm trên Portal, cổng lồng trong skill). RULE-017 = 0 |
| 9 | Vòng đời tác vụ | `application/tasks/ledger` — máy trạng thái NEW…ESCALATED; COMPLETED chỉ khi kiểm chứng đạt (`test_state_machine_rejects_invalid_and_unverified_completion`) |
| 10 | Đường audit | một hàm ghi `erp_db.write_audit_log` → bảng `audit_logs` (chỉ INSERT); 4 hàm bọc là lớp chuyển đổi tham số (L4, giữ có lý do) |
| 11 | Đã xoá | `src/mateai/domain/*` (8 gói entity, 0 caller) + test riêng; lối tắt danh tính theo tiền tố / chuỗi con; nhánh HITL thứ hai trong `plugin_registry`; RBAC riêng ở router skills; đường TTS riêng của skill 9Router; SQL trong application (17 chỗ); `shell=True` (2); gọi chặn trong async (5) |
| 12 | Đã gộp | luật rủi ro → `risk_engine` (chuyển từ `zero_trust`); luật cấu hình Portal → cổng; uỷ quyền "duyệt rồi nhớ" → `policy_engine`; đọc trạng thái kết quả tool → `verification.tool_outcome` (portal dùng chung); cảnh báo đôn đốc → `alert_dispatcher` |
| 13 | Lớp tương thích còn lại | alias `/ws/audio-stream` (L12); 4 hàm bọc audit (L4); Base64 ở REST thoại; client Whisper đồng bộ |
| 14 | Vì sao còn | L12: chưa có bằng chứng log là không thiết bị nào dùng — nay ghi `[DEPRECATED]` để thu bằng chứng; L4: chỉ đổi tham số, không có logic trùng; REST: client không hỗ trợ WS; Whisper: SDK đồng bộ, đã được miễn có ghi |
| 15 | Đường cũ còn chạy được? | Có một: `/ws/audio-stream` (cùng handler, có cảnh báo). Không còn đường thực thi tool nào ngoài cổng (quét AST) |
| 16 | LLM vượt được phân quyền? | Không: quyết định ở `authorize()` từ danh tính máy chủ xác thực + cấu hình; `confirmed` trong tham số bị bỏ; DENY thắng cả `approved` (`test_207_…`, `test_confirmed_flag_and_role_names_cannot_bypass`) |
| 17 | Tác nhân chạy tool không qua chính sách? | Không (RULE-017 = 0; `agent_orchestrator` cũng qua `authorize()`) |
| 18 | Tự trị chạy vô hạn? | Không theo lượt: ≤ 6 vòng, ≤ `max_tool_calls_per_turn`, ≤ `max_agent_seconds`. Tác vụ nền (sentinel, đôn đốc) chạy định kỳ theo thiết kế nhưng chỉ cảnh báo / tạo tác vụ, và dừng được bằng kill switch (hành động ghi) |
| 19 | Người dừng được AI? | Có: kill switch toàn cục / theo tác nhân / theo tool (Portal, chỉ admin, audit), huỷ lượt / ngắt lời; kiểm thật: ghi tệp bị chặn khi bật |
| 20 | Mọi hành động quan trọng truy được? | Có cho tool: audit có `caller`, `agent_id`, quyết định, luật, `policy_version`, `op_task_id`; sổ tác vụ có từng bước + bằng chứng |
| 21 | Mọi hành động kiểm chứng được? | Một phần: NONE / BASIC tự động; STANDARD có bộ kiểm chứng cho `kill_process`, `write_file`; tool rủi ro cao khác → ESCALATED (người xác nhận), không báo xong |
| 22 | An toàn khi LLM / TTS / Redis / DB / WS lỗi? | LLM: trả lời an toàn, chờ lỗi ≤ ngân sách (đo thật). TTS: chữ vẫn hiện, Edge dự phòng. Redis: không dùng. DB: sao lưu / khôi phục có kiểm chứng. WS: huỷ lượt khi ngắt kết nối. Ghi sổ / trace lỗi không làm hỏng lượt |
| 23 | Mở rộng ngang được? | **Không** — trạng thái phiên, hàng đợi, giới hạn đăng nhập, idempotency nằm trong RAM một tiến trình |
| 24 | Nguồn sự thật | SQLite `vnmateai.db` (tài khoản, tác vụ, sổ tác vụ, audit, trace thoại) + `config.json` (cấu hình / chính sách) |
| 25 | Dữ liệu bền | DB trên, `storage/vector_db` (trí nhớ dài hạn), `certs/` (khoá), `logs/` |
| 26 | Dữ liệu tạm | phiên WS, lịch sử hội thoại, hàng đợi TTS, cache âm thanh RAM, bộ đếm đăng nhập / idempotency |
| 27 | Dữ liệu ra ngoài | câu hỏi + kết quả tool (đã che) → 9Router; văn bản → TTS; cảnh báo → các kênh đã cấu hình (Telegram chỉ chat nội bộ) |
| 28 | p50 / p95 / p99 thoại | 2026-10-05, n = 20: lệnh nhanh tiếng trả lời 53 / 1 888 ms; câu cần LLM 4 858 / 13 700 ms; p99 chưa đủ mẫu (`docs/realtime/performance-before-after.md`) |
| 29 | Tỷ lệ thành công tác vụ tự trị | đo từ 2026-10-05 qua `GET /api/v1/ops/overview` (`tasks.success_rate`); chưa đủ thời gian để có con số đại diện |
| 30 | Chưa sẵn sàng production | `docs/production/readiness-score.md`: mở rộng ngang (FAIL), ABAC phòng ban, đánh giá model trên LLM thật, OpenTelemetry, linter / quét phụ thuộc trong CI, lịch sao lưu tự động, mật khẩu mặc định |

## Phụ lục — kiểm toán refactor production (2026-10-03)

### Kiểm toán cuối — refactor production (Phase 11)

Ngày: 2026-10-03 · Nhánh `refactor/phase-0-1-safety-net` · Mốc so sánh: commit `4f6464a` (trước refactor).
Mọi số dưới đây đo bằng `git` / `pytest` / quét AST trên repo — không ước lượng.

## 1. Tổng quan thay đổi

| Chỉ số | Giá trị |
|---|---|
| Commit kể từ mốc | 58 (tới `f77cca4`) |
| Tệp xoá / thêm / đổi chỗ | 71 / 125 / 66 |
| Dòng thêm / xoá | +21.194 / −24.321 (giảm ròng ~3.100 dòng) |
| `core/` | còn **1 module**: `core/plugin_manager.py` (API plugin công khai) |
| `src/mateai` | 171 tệp Python, mọi code chạy thật |
| `server.py` | 8.898 (mốc) → **1.021 dòng** (chỉ dựng app); 30 router trong `interfaces/http/routers/` |
| Route HTTP/WS | 183 route + 3 mount tĩnh (bảng route so với HEAD sau mỗi bước tách; `/api/v1/audit-logs` gỡ có chủ đích ở §52) |
| Test | 96 tệp; **371 pass**, 1 bỏ qua (`network`, gọi model thật) |
| Bản checkout sạch + `config.example.json` | 371 pass (môi trường Python có sẵn) |

## 2. Một chức năng — một implementation

| Chức năng | Trước | Nay (bản duy nhất) | Test canh giữ |
|---|---|---|---|
| Lượt thoại | 5 đường (portal WS, HUD, Xiaozhi, mic máy chủ, REST) mỗi đường một vòng LLM/TTS | `application/voice/voice_turn.process_voice_turn` (transport giữ riêng) | test voice pipeline, phase67 |
| Tổng hợp giọng | `AudioEngine` + `TTSStreamEngine` + race gTTS trong server | `infrastructure/tts/tts_stream_engine` | RULE-012 |
| Làm sạch text TTS | 3 hàm | `application/voice/speech_text` | |
| Gọi LLM | client OpenAI tự tạo ở 5 module, 2 vòng fallback | `infrastructure/llm/llm_provider` | RULE-011 (3 chỗ có lý do: nút thử kết nối) |
| Thực thi tool | vòng agent + vòng voice riêng (vòng voice không qua kiểm tra nào) | `application/agent/tool_gate.run_tool_with_policy` | `test_tool_policy_gate` |
| Hàng đợi duyệt (HITL) | `StateManager` + `hitl_manager` | `zero_trust.hitl_manager` (executor theo `kind`) | `test_pending_action_lookup` |
| Kho audit | `audit_logs` + `logs/security_audit.log` (xoá được) | bảng `audit_logs` (chỉ INSERT), một endpoint đọc | `test_audit_single_store` |
| Kho tài khoản | `users.json` + bảng `users` | bảng `users` | |
| Đọc/ghi `config.json` | tự mở ở nhiều module | `config/loader` (`read_raw_config`, `write_raw_config` nguyên tử) | RULE-013 (cả `CONFIG_PATH.read_text/write_text/open`) |
| Mở SQLite | `sqlite3.connect` rải rác | `erp_database.open_sqlite` | RULE-014 |
| Thư mục gốc dự án | suy từ `__file__` ở nhiều nơi | `loader._resolve_project_root` | `test_project_root_single_source` |
| Tên trợ lý | 4 cách đọc (server/lời chào luôn "Ly Ly") | `loader.get_assistant_name` | `test_assistant_name_single_source` |
| Che bí mật cấu hình | trong server | `interfaces/http/secret_masking` | phase80 |
| Secret ghi danh worker/thiết bị | 2 hàm gần giống | `interfaces/http/enrollment` | |
| Xác thực WebSocket | trong server | `interfaces/http/ws_auth` | `test_websockets_require_login` |
| Đường dẫn `hr_kpi.db` | tính ở 3 module | `domain_sync.DEFAULT_DB_PATH` | `test_hr_db_path_has_one_owner` |

## 3. Lớp bọc / bản sao CÒN LẠI và lý do

| Thành phần | Loại | Lý do giữ |
|---|---|---|
| `core/plugin_manager.py` | module ở vị trí cũ | API công khai `from core.plugin_manager import export_skill` của skill người dùng và skill do AI sinh trên các bản cài khác — đổi sẽ làm hỏng skill ngoài repo |
| `skills/*.py` dạng re-export (13 tệp: `agent_orchestrator`, `ai_delegation`, `file_system`, …) | lớp re-export | `plugin_manager` nạp skill bằng cách quét `skills/*.py`; đây là bề mặt đăng ký, code thật ở `application/skills/builtin` |
| `client_agent/` (core, skills) | bản riêng | chạy trên máy trạm KHÔNG có `mateai`; gói "Tải Agent" đóng từ thư mục này |
| `skills/{custom_skills, pc_control_skills, sysadmin_skills}.py` | **giống hệt** `client_agent/skills/` | chờ quyết định sản phẩm (owner-todo): máy chủ có tự điều khiển chính nó không |
| `skills/{file_system, monitoring_skills, excel_records_skill, visual_skills}.py` | **đã lệch** so với `client_agent/skills/` | như trên — hai bên đã sửa khác nhau, gộp cần quyết định trên |
| Hai bảng `employees` (ERP ở `vnmateai.db`, AD ở `hr_kpi.db`) | hai mô hình dữ liệu | ý nghĩa khác nhau; kế hoạch PG để hai schema |
| Ánh xạ role portal → RBAC (`admin→admin`, `manager→it_support`, `viewer→operator`) | hai mô hình role | gộp cần đổi dữ liệu role trong CSDL |

Không còn module chỉ re-export trong `src/mateai`, `core/`, `workers/` (quét AST).

## 4. Bản cũ còn gọi được lúc chạy không?

- `core/` chỉ còn `plugin_manager.py`; test RULE-015 chặn import ngược `mateai.interfaces.http.server` từ module khác.
- Các lớp tương thích chết đã xoá (`SentenceBoundaryStreamer`, `edge_tts_stream_audio`, `get_acoustic_ack_for_query`, `select_relevant_skills`, `SentenceStreamer`, `_sanitise_for_tts`, `clean_text_for_tts`, `text_to_speech_stream`, `_call_llm_router`) — chỉ còn nhắc trong docstring.
- `apps/{api,realtime,worker}` (gói rỗng từ cây song song) đã xoá.
- Endpoint cũ: `/api/v1/audit-logs` gỡ; `DELETE /api/v1/security/audit-logs` → 405.

## 5. Bảo mật đã sửa trong lúc refactor (đều tái hiện trên máy chủ thật trước khi sửa)

Chi tiết ở plan §44–52. Tóm tắt: viewer đọc được `config.json` qua `fs/read`; cờ `confirmed` trong body/tham số tool bỏ qua được duyệt; ai đăng nhập cũng duyệt/chạy skill tuỳ ý qua `confirm-action`; "đồng ý" duyệt nhầm tác vụ của người khác; **"hủy" bị hiểu là đồng ý**; manager duyệt qua HUD; danh sách chờ duyệt, audit và log lộ tham số tác vụ cho viewer; ký ức tác vụ đã chạy trả kết quả của người khác; config ghi không nguyên tử; enrollment token ghi ra log của agent; Telegram có thể nhận tin từ bộ test.

## 6. Chưa làm (cần thông tin/hạ tầng/quyết định — xem `docs/production/owner-todo.md`)

- PostgreSQL (kế hoạch có số liệu: `sqlite-to-postgresql-plan.md`).
- Tách tiến trình + Redis cho state chia sẻ.
- Container (ứng dụng phụ thuộc Windows: COM, micro, điều khiển màn hình).
- CI: workflow đã thêm, chưa xác nhận chạy trên GitHub.
- Firmware ESP32 với token riêng: đã sửa mã, chưa nạp thử trên chip.
- Connector M365/eInvoice/Paperless/OCI: chưa chạy với tài khoản thật.
- Gộp skill máy chủ ↔ agent máy trạm: chờ quyết định sản phẩm.
