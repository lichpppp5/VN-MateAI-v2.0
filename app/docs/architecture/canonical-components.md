# Ma trận thành phần chuẩn (Canonical Component Matrix)

> **Phase 0 — 2026-10-05**, commit `128c87d`. Thay bản 2026-10-01: bản đó trỏ `core/*`, nay đã chuyển hết sang `src/mateai/` (trừ `core/plugin_manager.py`).
> Bản riêng cho đường thoại, chi tiết hơn: `docs/realtime/canonical-components.md`.
> **Action**: KEEP (giữ) · MERGE (gộp vào bản chuẩn) · REPLACE · DEPRECATE · DELETE · REVIEW (cần chủ dự án quyết định).
> **Confidence**: CAO = đã lần caller bằng grep + có test chạy; TB = lần caller nhưng thiếu test; THẤP = cần chạy thực tế để chắc.

## A. Thoại realtime

| Feature | Canonical | Bản / đường khác | Caller thật | Runtime | Test | Action | Conf. |
|---|---|---|---|---|---|---|---|
| Lượt thoại | `application/voice/voice_turn.process_voice_turn` | — | `realtime_voice_ws:316`, `hud_voice:224`, `xiaozhi_gateway:890`, `voice_controller:662`, `routers/voice:135` | có | `test_voice_turn.py`, `test_voice_turn_trace.py` | KEEP | CAO |
| Lệnh nhanh | `application/commands/fast_command_router` | — | `voice_turn:397` | có | `test_phase5_fast_command_router.py` | KEEP; sửa chặn loop (`:267,:291`) | CAO |
| Tách câu | `application/voice/sentence_buffer.SentenceBuffer` | — | `llm_engine.stream_voice_response` | có | `test_phase3_sentence_buffer.py`, `test_sentence_buffer_first_sentence.py` | KEEP | CAO |
| Làm sạch lời đọc | `application/voice/speech_text.sanitise_for_tts` | — | mọi kênh | có | trong test sentence | KEEP | CAO |
| TTS | `infrastructure/tts/tts_stream_engine.TTSStreamEngine` | `skills/ninerouter_skills.py:255` tự gọi `/audio/speech` | voice_turn, routers/tts | có | `test_phase4_tts_pipeline.py` | KEEP; skill → MERGE (gọi engine) | CAO |
| Hàng đợi TTS | `infrastructure/tts/tts_queue_pipeline.StreamingTTSWorkerPipeline` (maxsize 5) | — | `voice_turn:465` | có | `test_tts_pipeline_backpressure.py` | KEEP | CAO |
| Câu đệm (ACK) | `infrastructure/tts/acoustic_ack` + `acoustic_ack_catalog` + `audio_cache` | — | `voice_turn:432–435` | có | trong test voice_turn | KEEP | CAO |
| STT | `infrastructure/audio/audio_processor.AudioEngine.transcribe_audio` | Whisper dựng `OpenAI()` riêng (`:490`) | robot, mic, `/api/v1/voice/transcribe` | có | `test_voice_tab_pro.py` | KEEP; client Whisper → MERGE vào pool/provider | CAO |
| Truyền audio nhị phân | `infrastructure/websocket/binary_transport` | Base64 ở REST `voice-command` | portal, HUD | có | `test_phase11_binary_transport.py` | KEEP; Base64 chỉ ở biên REST | CAO |
| WS thoại trình duyệt | `/ws/v1/voice-stream` | `/ws/hud` (schema sự kiện riêng) | portal / HUD | có | `test_websockets_require_login.py`, `test_hud_voice_pipeline_behavior.py` | MERGE schema HUD | TB |
| WS robot | `/api/v1/xiaozhi/ws[/{id}]` | `/ws/audio-stream[/{id}]` (alias firmware cũ) | robot | có | `test_voice_tab_pro.py` | KEEP alias tới khi log xác nhận hết firmware cũ | TB |
| Trace thoại | `voice_turn.VoiceTurnTrace` + `_RECENT_TRACES` | — | mọi kênh | có (RAM) | `test_voice_turn_trace.py` | KEEP; thêm lưu bền | CAO |

## B. LLM / Agent

| Feature | Canonical | Bản / đường khác | Caller thật | Runtime | Test | Action | Conf. |
|---|---|---|---|---|---|---|---|
| Abstraction LLM | `infrastructure/llm/llm_provider.BaseLLMProvider` (Direct, NineRouter, TriBrain) | — | `llm_engine.get_provider` | có | `test_llm_provider_health.py` | KEEP | CAO |
| Vòng agent | `application/agent/llm_engine.ask_async` | `stream_voice_response` (đường thoại, chuyển sang `ask_async` khi cần tool) | voice_turn, telegram, webhook | có | `test_agent_loop_limits.py`, `test_confirm_pending_action.py` | KEEP | CAO |
| Chọn tool | `application/skills/skill_router.dynamic_skill_router` | — | `llm_engine:679,1350–1370` | có | (gián tiếp) | KEEP | TB |
| Đa tác nhân | `application/agent/agent_orchestrator` | — | skill `delegate_to_multi_agent` | có | — | REVIEW: gọi hàm skill **không qua cổng** (`:252,:264`) | CAO |
| Trạng thái chờ / vừa xong | `application/agent/state_manager` | — | llm_engine | có | — | KEEP | TB |

## C. Kiểm soát (Policy / Risk / Authorization / HITL / Audit)

| Feature | Canonical | Bản / đường khác | Caller thật | Runtime | Test | Action | Conf. |
|---|---|---|---|---|---|---|---|
| Cổng thực thi tool | `application/agent/tool_gate.run_tool_with_policy` | `routers/skills.py:233–245` (RBAC + `execute_with_hitl`); `plugin_registry.py:441–514` (HITL riêng) | llm_engine, routers/files, routers/clients, executor HITL | có | `test_tool_policy_gate.py` | MERGE: router skills và registry đi qua cổng | CAO |
| Đánh giá rủi ro | `application/security/zero_trust.HumanInTheLoopManager.get_risk_level` (thang 1–5) | `safety_guard.SecurityEngine.evaluate_action_risk` (đọc `forbidden_keywords` cấu hình — chỉ nút thử ở `routers/security.py:166` dùng) | tool_gate (`:127`), plugin_registry | có | `test_tool_policy_gate.py` | MERGE: luật cấu hình vào Risk/Policy chuẩn | CAO |
| RBAC | `application/security/security_guard.SecurityGuard.check_permission` | — | tool_gate (`:180`), routers/skills | có | `test_device_access.py` | KEEP (chuyển vào control plane) | CAO |
| Vai trò | 2 mô hình: portal `admin/manager/viewer` ↔ RBAC `admin/it_support/operator/viewer` qua `PORTAL_ROLE_MAP` | — | — | có | — | REVIEW (D3 cũ) | CAO |
| HITL | `zero_trust.hitl_manager` | — | mọi kênh | có, bền qua audit | `test_phase83_hitl_dedup_telegram.py`, `test_confirm_action_endpoint.py` | KEEP | CAO |
| Audit | bảng `audit_logs` qua `erp_database.write_audit_log` (`:882`) | 4 hàm bọc: `safety_guard.log_audit`, `security_guard._write_audit`, `erp_db.log_audit_action`, `zero_trust.log_security_audit` | toàn hệ thống | có, INSERT-only | `test_audit_single_store.py` | KEEP kho; MERGE 4 hàm bọc về 1 | CAO |
| Che dữ liệu | `safety_guard.mask_sensitive_data` | — | llm_engine (5 chỗ) | có | — | KEEP | TB |
| Kiểm mã sinh tự động | `safety_guard.inspect_generated_code` (AST) | — | meta_architect | có | — | KEEP | TB |
| Xác thực | `application/security/auth_manager` (JWT) + `interfaces/http/auth_dependencies.require_roles` | `ws_auth` (WS) | toàn bộ router | có | `test_websockets_require_login.py` | KEEP | CAO |

## D. Skill / Tool / Connector

| Feature | Canonical | Bản / đường khác | Caller thật | Runtime | Test | Action | Conf. |
|---|---|---|---|---|---|---|---|
| Registry skill | `core/plugin_manager` (102 `@export_skill`) | `application/skills/plugin_registry` (tool đăng ký động: `tool_bridge`, `computer_use_plugin`) | tool_gate `:200,:213` | có | — | REVIEW: gộp 2 registry (giữ breaker + timeout của `plugin_registry`) | CAO |
| Lớp chuyển tiếp skill | `skills/{agent_orchestrator,ai_delegation,…}.py` (≤ 36 dòng) | — | plugin_manager quét thư mục | có | — | KEEP (cơ chế nạp) | CAO |
| Connector ngoài | `infrastructure/connectors/base_connector` (timeout, retry, rate limit) | m365 / einvoice / paperless dựng `httpx` riêng | tool_bridge | có | `test_http_timeouts.py` | KEEP | TB |
| Thông báo đa kênh | `infrastructure/notifications/channels` + `application/operations/alert_dispatcher` | `telegram_gateway.send_incident_alert` gọi thẳng ở `proactive_manager:198,356` | sentinel, proactive | có | `test_alert_dispatcher.py` | MERGE proactive → dispatcher | CAO |

## E. Task / Vận hành

| Feature | Canonical | Bản / đường khác | Caller thật | Runtime | Test | Action | Conf. |
|---|---|---|---|---|---|---|---|
| Giao việc máy trạm | `application/devices/task_manager` | — | routers/tasks, client_orchestrator, skill lean_hr | có | `test_tasks_monitoring.py` | KEEP | CAO |
| Công việc ERP | `erp_database.create_erp_task` | `proactive_manager.assign_task_intelligently` | AI skill, import Excel | có | `test_tasks_table_single_owner.py` | KEEP (dữ liệu nghiệp vụ) | TB |
| Tác vụ nền thoại | `application/operations/background_workers` | — | voice ops | có | — | KEEP | TB |
| Sổ tác vụ tự trị | **không có** | — | — | — | — | CREATE (Phase 4), tái dùng bảng/trạng thái `tasks` nếu được | — |
| Giám sát | `health_monitor` (cache) + `autonomous_sentinel` (phát hiện → cảnh báo) | — | server startup | có | `test_sentinel_reads_health_cache.py` | KEEP | CAO |

## F. Dữ liệu / Cấu hình

| Feature | Canonical | Bản / đường khác | Action | Conf. |
|---|---|---|---|---|
| Persistence | `infrastructure/database/{erp_database,db_manager}` (SQLite) | SQL thẳng ở `agent_orchestrator`, `onboarding_workflow`, `proactive_manager`; `domain_sync` mở DB riêng | MERGE truy vấn vào repository | CAO |
| Schema `tasks` | `erp_database.ensure_tasks_table` (một chủ) | — | KEEP | CAO |
| Cấu hình | `config/loader.settings` + `secret_box` | — | KEEP | CAO |
| Entity domain | `src/mateai/domain/*` | — | **0 caller runtime** → REVIEW: dùng thật khi dựng control plane, hoặc DELETE | CAO |

## Đếm trùng lặp (§184) — sau P15 (2026-10-05)

| Câu hỏi | Phase 0 | Nay | Ghi chú |
|---|---|---|---|
| Đường thoại | 1 | 1 | — |
| Abstraction LLM | 1 (+ Whisper) | 1 (+ Whisper, miễn có ghi) | STT, không phải LLM |
| TTS | 1 (+ skill) | **1** | skill 9Router qua engine |
| Giao thức WS thoại | 2 + robot | 2 + robot | HUD chưa gộp (L10) |
| Fast router | 1 | 1 | — |
| Tool registry (danh mục) | 2 | 1 danh mục + 2 bộ thực thi | 2026-10-06: `plugin_registry` gọi thẳng cũng qua `tool_gate` (bỏ đường duyệt riêng); còn lại chỉ là bộ thực thi có breaker cho connector — xem `current-system-map.md` §6 |
| Policy engine | 0 chuẩn / 4 nguồn | **1** (`policy_engine`) | 4 nguồn luật gộp vào một hàm quyết định |
| Đường phân quyền tool | 3 | **1** (`authorize()`) | RULE-017 = 0 |
| Task engine | 3 khái niệm + 0 sổ tự trị | 3 khái niệm nghiệp vụ + **1 sổ tác vụ AI** | việc máy trạm / ERP / tác vụ nền là dữ liệu nghiệp vụ khác nhau |
| Audit pipeline | 1 kho / 5 hàm | 1 kho / 1 hàm ghi + 4 lớp chuyển đổi | L4 giữ có lý do |
| Worker nền trùng trách nhiệm | 0 | 0 | — |

## Đếm trùng lặp — ảnh chụp Phase 0

| Câu hỏi | Số | Lý do / việc cần làm |
|---|---|---|
| Đường thoại | 1 | — |
| Abstraction LLM | 1 (+1 client Whisper riêng) | gộp client |
| TTS | 1 (+1 skill tự gọi) | gộp |
| Giao thức WS thoại | 2 (`/ws/v1/voice-stream`, `/ws/hud`) + robot | HUD gộp schema |
| Fast router | 1 | — |
| Tool registry | **2** | gộp |
| Policy engine | **0 chuẩn / 4 nguồn luật** | dựng control plane từ bản có sẵn |
| Đường phân quyền tool | **3** | gộp về `tool_gate` |
| Task engine | 3 khái niệm + 0 sổ tự trị | Phase 4 |
| Audit pipeline | 1 kho, 5 hàm ghi | gộp hàm |
| Worker nền trùng trách nhiệm | sentinel và health_monitor từng đo trùng — nay sentinel đọc cache | — |
