# Ma trận trùng lặp (Duplication Matrix)

> **Ảnh chụp lịch sử (trước refactor).** Đa số dòng đã được xử lý ở các phase sau; trạng thái hiện tại và những gì còn lại (kèm lý do) ở `docs/migration/final-audit.md`.

> Phase 0 / Phase A — chỉ đọc. Không file code nào bị sửa hay xóa để lập tài liệu này.
> Ngày audit: 2026-10-01 · Commit gốc: `4f6464a`
> Cách lấy bằng chứng: đồ thị import AST (gồm import trong hàm, `importlib`, chuỗi tên module),
> grep tên hàm/lớp, introspect `app.routes` của FastAPI đang chạy, so sánh md5 nội dung file.

## Cách đọc

| Cột | Ý nghĩa |
|---|---|
| Status | `Canonical` (bản chính), `Active-dup` (đang chạy nhưng trùng trách nhiệm với bản khác), `Facade` (chỉ re-export), `Test-only` (chỉ test gọi), `Dead` (không ai gọi), `Unwired` (code mới chưa nối vào runtime) |
| Action | `KEEP`, `MERGE`, `REPLACE`, `DELETE`, `REVIEW` (theo chính sách mục 23–27 của yêu cầu audit) |
| Confidence | `HIGH` chỉ khi đã kiểm tra cả import tĩnh, import lười, chuỗi động, test, frontend, registry |

Không có dòng nào được đánh `DELETE` mà chưa đủ điều kiện xóa; mọi lần xóa thực tế thuộc Phase C và phải qua test + chạy thử runtime.

---

## 1. Voice pipeline (phía server)

Năm lối vào cùng làm một việc "nghe → hiểu → trả lời bằng giọng", mỗi lối tự ghép LLM, TTS và lịch sử theo cách riêng.

| Feature | Implementation | Status | Caller | Replacement | Action | Confidence |
|---|---|---|---|---|---|---|
| Voice turn | P1 `core/realtime_voice_ws.py` (`/ws/v1/voice-stream`, alias `/ws/voice`) — `llm_engine.stream` → `SentenceBuffer` → `StreamingTTSWorkerPipeline` → binary WS, có barge-in, backpressure, fast path | Canonical | `web/app.js` (portal) | — | KEEP | HIGH |
| Voice turn | P2 `core/server.py::_process_hud_voice_command[_body]` (`/ws/hud`) — `stream_voice_response` → `SentenceStreamer` → `TTSStreamEngine`, lịch sử ở `voice_sessions` | Active-dup | `web/hud.js` | P1 | MERGE (HUD dùng giao thức P1) | HIGH |
| Voice turn | P3 `core/xiaozhi_gateway.py` (`/api/v1/xiaozhi/ws[/{id}]`, alias `/ws/audio-stream[/{id}]`) — `stream_voice_response` → `AudioEngine.text_to_speech_stream`, không lưu lịch sử | Active-dup (phần ứng dụng); transport thiết bị là riêng | firmware ESP32 (`esp32_firmware/*/config.h`) | lõi ứng dụng của P1 + transport XiaoZhi | MERGE (giữ transport, bỏ logic trùng) | HIGH |
| Voice turn | P4 `core/voice_controller.py` (mic + wake word trên máy chủ) — `stream_voice_response` → `AudioEngine.text_to_speech_bytes`, lịch sử `_history` riêng | Active-dup (phần ứng dụng); thu âm/phát loa cục bộ là riêng | `main.py`, `server.py` (mic toggle) | lõi ứng dụng của P1 | MERGE | HIGH |
| Voice turn | P5 HTTP `POST /api/v1/voice-command` (`server.py::voice_command`) — `ask_async` (không stream) | Active-dup | `web/app.js:533`, `web/hud.js:2102` | lõi ứng dụng của P1 (giữ REST làm transport) | MERGE | HIGH |

## 2. TTS

> **Phase 2 (2026-10-01):** đã gộp — chỉ còn `TTSStreamEngine` (thứ tự 9Router → Edge), `sanitise_for_tts` + `shorten_for_speech`, `SentenceBuffer`. Các dòng Active-dup / Facade / Test-only / Dead dưới đây và ở mục 3 đã xoá. Xem `docs/migration/production-refactor-plan.md` §7.

| Feature | Implementation | Status | Caller | Replacement | Action | Confidence |
|---|---|---|---|---|---|---|
| Tổng hợp giọng | `core/audio/tts_stream_engine.py::TTSStreamEngine` (cache → Edge → ElevenLabs → 9Router → gTTS) | Canonical | P1, P2, `fast_command_router`, `streaming_tts_pipeline`, `tts_queue_pipeline` | — | KEEP | HIGH |
| Tổng hợp giọng | `core/audio_processor.py::AudioEngine.text_to_speech_stream/_bytes/_tts_gtts` (Edge → 9Router → gTTS) | Active-dup | P3, P4, P5, `server.py` (4 chỗ), `scripts/prewarm_vocabulary.py`, `prewarm_tts_cache` | `TTSStreamEngine` | REPLACE (giữ `AudioEngine` cho STT) | HIGH |
| Tổng hợp giọng | `AudioEngine._tts_9router` | Active, bị gọi ngược từ `TTSStreamEngine` (dòng 341, 414) | `TTSStreamEngine` | chuyển vào `tts_stream_engine.py` | MERGE | HIGH |
| Tổng hợp giọng | `core/server.py` ~dòng 215–260: race gTTS ↔ engine riêng cho HUD | Active-dup | P2 | `TTSStreamEngine` | DELETE sau khi P2 gộp | HIGH |
| Hàng đợi TTS | `core/audio/tts_queue_pipeline.py::StreamingTTSWorkerPipeline` (2 worker, re-sequencer, maxsize) | Canonical | P1, `SentenceBoundaryStreamer` | — | KEEP | HIGH |
| Facade | `core/audio/streaming_tts_pipeline.py` — re-export + `warmup_acoustic_ack_cache`, `get_acoustic_ack_audio` | Facade + 2 hàm thật | `server.py` (warmup), `orchestrator.py`, P1 | — | KEEP TEMP (giữ 2 hàm thật, bỏ phần dưới) | HIGH |
| Facade | `streaming_tts_pipeline.SentenceBoundaryStreamer` ("tương thích `api_voice_stream.py`" — file đó **không còn tồn tại**) | Test-only | `tests/test_phase4_tts_pipeline.py`, `tests/test_phase92_voice_stream_pipeline.py` | P1 | DELETE (sửa test trước) | HIGH |
| Facade | `streaming_tts_pipeline.edge_tts_stream_audio` | Dead | không có | `TTSStreamEngine.stream` | DELETE | HIGH |
| Facade | `streaming_tts_pipeline.get_acoustic_ack_for_query` | Test-only | `tests/test_phase6_acoustic_ack.py` | `acoustic_ack_catalog` | DELETE (sửa test trước) | HIGH |
| TTS (port mới) | `src/mateai/infrastructure/tts/edge_tts_adapter.py` | Unwired | chỉ `tests/unit` | — | xem mục 11 | HIGH |

## 3. Tách câu & làm sạch text cho TTS

| Feature | Implementation | Status | Caller | Replacement | Action | Confidence |
|---|---|---|---|---|---|---|
| Tách câu | `core/audio/sentence_buffer.py::SentenceBuffer` (chống tách sai số/IP/URL) | Canonical | P1, `SentenceStreamer` | — | KEEP | HIGH |
| Tách câu | `core/audio/sentence_streamer.py::SentenceStreamer` — bọc `SentenceBuffer` thành async generator | Facade (vòng import 2 chiều với `sentence_buffer`) | P2, `streaming_tts_pipeline` | `SentenceBuffer` | MERGE (bỏ vòng import) | HIGH |
| Tách câu | `src/mateai/application/voice/sentence_buffer.py` | Unwired | chỉ `tests/unit` | — | xem mục 11 | HIGH |
| Làm sạch text | `sentence_streamer.sanitise_for_tts` | Canonical | `sentence_buffer`, `streaming_tts_pipeline` | — | KEEP | HIGH |
| Làm sạch text | `llm_engine.LLMEngine._sanitise_for_tts` (dòng 2117) | Active-dup | `llm_engine` (6 chỗ), `server.py` (3 chỗ) | `sanitise_for_tts` | MERGE | HIGH |
| Làm sạch text | `audio_processor.clean_text_for_tts` | Active-dup | `AudioEngine`, `xiaozhi_gateway` (2 chỗ) | `sanitise_for_tts` | MERGE | HIGH |

Ba hàm làm sạch có regex khác nhau, nên cùng một câu trả lời sẽ được đọc khác nhau tùy kênh (portal / HUD / ESP32). Trước khi gộp phải viết test so sánh đầu ra.

## 4. LLM

| Feature | Implementation | Status | Caller | Replacement | Action | Confidence |
|---|---|---|---|---|---|---|
| Provider abstraction | `core/llm_provider.py` (`BaseLLMProvider`, `DirectLLMProvider`, `NineRouterLLMProvider`, `TriBrainLLMProvider`) | Canonical | `llm_engine.stream/stream_tokens` → P1 | — | KEEP | HIGH |
| Gọi LLM không stream + fallback | `llm_engine._call_llm/_call_llm_direct/_call_llm_router` (vòng fallback riêng, dòng 818–972) | Active-dup | `ask_async` (P5, Telegram, HUD fallback), `server.py:5771` | `provider.complete()` | REPLACE | HIGH |
| Stream + fallback | `llm_engine.stream_voice_response` (vòng fallback riêng, dòng 1700–1990) | Active-dup | P2, P3, P4 | `llm_engine.stream` | REPLACE | HIGH |
| Client OpenAI riêng | `core/analytics_engine.py:82`, `core/meta_architect.py:77,138`, `core/server.py:3083` (test model), `core/skills/ai_delegation.py:144,290` | Active-dup (tự tạo client, bỏ qua fallback/timeout chung) | các module trên | provider abstraction | REPLACE | HIGH |
| HTTP pool | `core/connection_pool.py` | Canonical cho LLM/STT/TTS | `llm_engine`, `audio_processor` | — | KEEP | HIGH |
| HTTP client riêng | 17 chỗ `httpx.AsyncClient(...)` ngoài pool (telegram 4, m365 3, einvoice 2, …) | Active-dup | connectors, gateways | pool chung hoặc client theo connector có vòng đời | REVIEW | MEDIUM |
| LLM (port mới) | `src/mateai/infrastructure/llm/*` (5 adapter + factory) | Unwired | chỉ `tests/unit` | — | xem mục 11 | HIGH |

## 5. Router / quyết định ý định

| Feature | Implementation | Status | Caller | Replacement | Action | Confidence |
|---|---|---|---|---|---|---|
| Lệnh nhanh tất định | `core/fast_command_router.py` | Canonical (chỉ P1 dùng) | P1 | — | KEEP; mở rộng cho P2–P5 khi gộp | HIGH |
| Phân loại ý định | `llm_engine.classify_intent` (staticmethod) | Canonical cho "brain role" | P1 | — | KEEP | MEDIUM |
| Chọn tool theo câu hỏi | `core/dynamic_skill_router.py::get_tools_for_query` | Canonical | `llm_engine`, `agent_voice_loop`, `plugin_manager` | — | KEEP | HIGH |
| Chọn tool theo câu hỏi | `core/plugin_manager.py::get_tools_for_query` / `get_tools_by_domain` | Facade (chuyển thẳng sang `dynamic_skill_router`) | `llm_engine` | `dynamic_skill_router` | KEEP TEMP; gọi thẳng router khi gộp registry | HIGH |
| Chọn tool theo câu hỏi | `core/plugin_registry.py::select_relevant_skills` (chấm điểm keyword riêng, "Phase 95") | Dead (không caller tĩnh/động/test) | không có | `dynamic_skill_router` | DELETE | HIGH |
| Lệnh nhanh (port mới) | `src/mateai/application/commands/fast_command_router.py` (viết lại, có RBAC, 252 dòng khác bản core) | Unwired | chỉ `tests/unit` | — | xem mục 11 | HIGH |

## 6. Skill / Tool / Plugin registry

| Feature | Implementation | Status | Caller | Replacement | Action | Confidence |
|---|---|---|---|---|---|---|
| Nạp skill | `core/plugin_manager.py` (`@export_skill`, quét `skills/*.py`, ghi `skills/registry.json`) | Canonical | 55 module | — | KEEP | HIGH |
| Đăng ký tool + circuit breaker | `core/plugin_registry.py` (`PluginRegistry.register_tool`) | Active, registry thứ hai | `llm_engine`, `agent_orchestrator`, `tool_bridge`, `computer_use_plugin`, `server` | hợp nhất với `plugin_manager` | MERGE | HIGH |
| Cầu nối | `core/connectors/tool_bridge.py` — đẩy connector vào `plugin_registry` | Active | `server` | sau khi hợp nhất registry | REVIEW | MEDIUM |
| Đăng ký skill | `skills/*.py` dạng shim re-export từ `core/skills/*` (`file_system`, `integration_tools`, `ai_delegation`, …) | Facade có lý do: là bề mặt được `plugin_manager` quét (`glob("*.py")`) | `plugin_manager` (động) | — | KEEP (ghi rõ lý do) | HIGH |
| Skill phía server sao y skill máy trạm | `skills/pc_control_skills.py`, `visual_skills.py`, `sysadmin_skills.py`, `custom_skills.py`, `excel_records_skill.py` (md5 trùng tuyệt đối với `client_template/skills/*`) | Active-dup (server nạp và thực thi trên máy chủ) | `plugin_manager` | một nguồn chung cho máy chủ + máy trạm | REVIEW (quyết định sản phẩm: máy chủ có được điều khiển PC chính nó không) | HIGH |
| Skill không được đăng ký | `core/skills/erp_organization.py` | Dead (không shim, không registry, không test, không frontend) | không có | `erp_db.query_organization` qua skill khác | DELETE hoặc đăng ký nếu cần tính năng | HIGH |
| Registry (port mới) | `src/mateai/domain/skills/registry.py`, `application/skills/*` | Unwired | chỉ `tests/unit` | — | xem mục 11 | HIGH |

## 7. Security

Không xóa lớp bảo mật nào chỉ vì trông giống nhau. Bảng dưới phân tách theo trách nhiệm.

| Trách nhiệm | Implementation | Status | Caller | Replacement | Action | Confidence |
|---|---|---|---|---|---|---|
| Xác thực (JWT, user) | `core/auth_manager.py` (users.json) | Canonical | `server`, `api_erp`, `lean_hr_skills` | — | KEEP | HIGH |
| Lưu user | `core/db_manager.py` bảng `users` (đồng bộ một chiều từ users.json) | Active-dup (2 nguồn sự thật cho user) | `db_manager` | một kho user | MERGE | HIGH |
| RBAC portal | `auth_manager.require_roles` (admin/manager/viewer) | Canonical cho HTTP | 7 endpoint | — | KEEP | HIGH |
| RBAC tool | `core/security_guard.py` (admin/it_support/operator/viewer từ bảng `employees`) | Active, mô hình vai trò thứ hai | `server.py` `/skills/execute`, `llm_engine`, `itsm_skills` | ánh xạ vai trò thống nhất | MERGE (cẩn thận) | MEDIUM |
| Đánh giá rủi ro | `zero_trust.evaluate_action_risk` | Canonical | `llm_engine:1395`, `server:6066,6109` | — | KEEP | HIGH |
| Đánh giá rủi ro | `safety_guard.SecurityEngine.evaluate_action_risk` | Active-dup | qua `security_engine` | `zero_trust` | MERGE | MEDIUM |
| HITL | `zero_trust.HumanInTheLoopManager` (`hitl_manager`) | Active | `plugin_registry`, `computer_use_plugin`, `integration_tools`, `server` (4 chỗ) | một HITL | MERGE | HIGH |
| HITL | `core/security/hitl_manager.py::HITLManager` (`hitl_manager`, cùng tên biến) | Active | `server` (3 chỗ), `telegram_gateway` | một HITL | MERGE | HIGH |
| Audit | `safety_guard.SecurityEngine.log_audit` (file `logs/security_audit.log`) | Active | `llm_engine` (5 chỗ) | một audit sink | MERGE | HIGH |
| Audit | `security_guard._write_audit` (bảng `audit_logs`) | Active | `security_guard` | một audit sink | MERGE | HIGH |
| Audit | `zero_trust.log_security_audit` | Active | `hitl_manager` (4), `webhook_gateway` | một audit sink | MERGE | HIGH |
| Che dữ liệu nhạy cảm | `safety_guard.mask_sensitive_data` | Canonical | `llm_engine` (6), P1 | — | KEEP | HIGH |
| Kiểm tra code sinh ra | `safety_guard.inspect_generated_code` + `SafetyGuard` wrapper | Canonical | `meta_architect` | — | KEEP | HIGH |

## 8. Database / persistence

| Feature | Implementation | Status | Caller | Replacement | Action | Confidence |
|---|---|---|---|---|---|---|
| ERP + audit | `core/database.py::ERPDatabase` (12 bảng) | Canonical | 15 module | — | KEEP | HIGH |
| User + task KPI | `core/db_manager.py::DatabaseManager` | Active-dup: cùng file `vnmateai.db`, **cùng bảng `tasks`** | `auth_manager`, `security_guard`, `server`, `task_manager` | repository task/user chung | MERGE | HIGH |
| HR/AD sync | `core/domain_sync.py` → `hr_kpi.db` (bảng `employees` thứ hai, `computers`) | Active-dup (bảng `employees` trùng tên khác schema với `vnmateai.db`) | `server` | `ERPDatabase` | REVIEW | MEDIUM |
| Đọc DB trực tiếp | `autonomous_sentinel.py:121,161`, `health_monitor.py:152` (`sqlite3.connect` riêng) | Active-dup | — | repository | MERGE | HIGH |
| Repository (port mới) | `src/mateai/infrastructure/database/sqlite_repository.py` + `domain/repository_ports.py` | Unwired | chỉ `tests/unit` | — | xem mục 11 | HIGH |

## 9. Hội thoại / lịch sử

| Feature | Implementation | Status | Caller | Replacement | Action | Confidence |
|---|---|---|---|---|---|---|
| Lịch sử hội thoại | `core/memory_manager.py::MemoryManager` | Canonical | P1, `llm_engine`, `server` | — | KEEP | HIGH |
| Lịch sử hội thoại | `core/voice_session.py::VoiceSessionStore` | Active-dup | P2, `server` | `MemoryManager` | MERGE | HIGH |
| Lịch sử hội thoại | `voice_controller` `_history` | Active-dup | P4 | `MemoryManager` | MERGE | HIGH |
| Cắt gọn ngữ cảnh | `core/history_pruner.py` | Canonical | `llm_engine`, `memory_manager`, P1 | — | KEEP | HIGH |
| Trí nhớ dài hạn | `core/cognitive_memory.py` (ChromaDB) | Canonical, trách nhiệm riêng | `server` | — | KEEP | HIGH |
| Hành động chờ duyệt | `core/state_manager.py` | Trách nhiệm riêng (gần HITL) | `llm_engine`, `server` | xem mục 7 HITL | REVIEW | MEDIUM |

## 10. Client agent / firmware

| Feature | Implementation | Status | Caller | Replacement | Action | Confidence |
|---|---|---|---|---|---|---|
| Máy trạm | `client_agent/` | Active (server tự khởi chạy làm `MASTER_LOCAL_WORKER`, `server.py:4828`) | server | — | KEEP làm nguồn chính | HIGH |
| Máy trạm | `client_template/` (11/16 file md5 trùng tuyệt đối; `agent.py`, `file_system.py`, `monitoring_skills.py` đã lệch) | Active-dup (đóng gói zip tải về, `server.py:6860`) | endpoint download agent | sinh zip từ `client_agent/` + bootstrap config | MERGE | HIGH |
| Firmware | `esp32_firmware/src/` (PlatformIO) | Active | `platformio.ini` | — | KEEP | MEDIUM |
| Firmware | `esp32_firmware/vnmate_robot/` (Arduino `.ino`, `motion_core` sao chép có sửa) | Active-dup | Arduino IDE | một nguồn firmware | REVIEW (cần người có phần cứng xác nhận) | MEDIUM |

## 11. Cây code song song `src/mateai/` và `apps/`

| Feature | Implementation | Status | Caller | Replacement | Action | Confidence |
|---|---|---|---|---|---|---|
| Toàn bộ kiến trúc đích | `src/mateai/**` (69 file, 3.679 dòng): domain, application, infrastructure, interfaces (rỗng), config | Unwired: **0 importer trong production**; chỉ `tests/unit/*` (9 file) và `tests/architecture/*` import qua `src.mateai.*` | tests | — | REVIEW — cần quyết định của chủ dự án (xem `docs/migration/production-refactor-plan.md` §Quyết định D1) | HIGH |
| Entry point | `apps/api`, `apps/realtime`, `apps/worker` (mỗi cái 5 dòng `__init__`) | Dead | không có | — | DELETE hoặc dùng khi tách tiến trình | HIGH |
| Cấu hình | `src/mateai/config/settings.py` | Unwired, trùng `core/config_loader.py` | tests | `config_loader` | xem D1 | HIGH |

Hệ quả: các unit test thêm ở commit `4f6464a` kiểm tra bản viết lại không bao giờ chạy, còn `core/` (code thật) không được các test đó bảo vệ.

## 12. WebSocket

| Endpoint | Mục đích | Handler | Client thật | Status | Action |
|---|---|---|---|---|---|
| `/ws/v1/voice-stream` | VOICE | `realtime_voice_ws` | `web/app.js` | Canonical | KEEP |
| `/ws/voice` | VOICE (alias cùng handler) | như trên | **không có** | Legacy alias | DELETE (HIGH — không client nào trong repo dùng) |
| `/ws/hud` | HUD (telemetry + voice P2) | `server.websocket_hud_endpoint` | `web/hud.js` | Active-dup (phần voice) | MERGE voice vào giao thức P1; giữ telemetry |
| `/api/v1/xiaozhi/ws[/{device_id}]` | DEVICE | `server._handle_audio_stream` → `xiaozhi_gateway` | firmware, `web/app.js` (hiển thị URL) | Canonical cho thiết bị | KEEP |
| `/ws/audio-stream[/{device_id}]` | DEVICE (alias cùng handler) | như trên | **không có trong repo** | Legacy alias | REVIEW (thiết bị đã nạp firmware cũ ngoài repo có thể còn dùng) |
| `/ws/client` | CLIENT AGENT | `server.websocket_client_endpoint` | `client_agent`, `client_template` | Canonical | KEEP |
| `/ws/portal-ui` | TELEMETRY/UI sync | `server.websocket_portal_ui` | `web/app.js` | Canonical | KEEP |
| `/ws/topology` | TELEMETRY (admin) | `server.websocket_topology_endpoint` | `admin/components/topology/SystemCanvas.tsx` | Canonical | KEEP |

Thực đo: 10 decorator WebSocket / 8 đường dẫn khác biệt về chức năng (không phải 13 như tài liệu cũ).

## 13. HTTP API

Thực đo bằng `app.routes`: **165 cặp method+path, 0 cặp trùng**. 139 path dưới `/api/v1`, 5 dưới `/api/erp` (chưa có version). Năm handler phục vụ 2 path (alias ở tầng transport, đúng quy tắc): `/topology`, `/computer-use`, `/roi-dashboard`, `/api/v1/wake-word/status`, `/api/v1/wake-word/toggle`. Không tìm thấy cặp endpoint khác handler mà cùng nghiệp vụ.

## 14. Cấu hình

| Setting | Nơi khai báo | Action |
|---|---|---|
| Model / API key / base URL | `llm.*`, `MODEL_NAME`, `API_KEY`, `BASE_URL`, `routing.primary`, `router.primary` (6 nơi) | MERGE về `llm.*` + biến môi trường |
| Giọng TTS | `TTS_VOICE`, `audio.tts_voice` | MERGE |
| Tốc độ TTS | `TTS_RATE`, `audio.speech_rate` | MERGE |
| ASR | `ASR_BACKEND`, `audio.asr_engine` | MERGE |
| Tên trợ lý | `AI_NAME`, `ASSISTANT_NAME`, `persona.ai_name` | MERGE |
| Wake word, system prompt | `WAKE_WORD`, `persona.wake_word`; `SYSTEM_PROMPT`, `persona.system_prompt` | MERGE |
| Đọc `config.json` bỏ qua loader | 14 chỗ: `server.py` (8), `llm_engine.py` (2), `telegram_gateway.py` (2), `autonomous_sentinel.py`, `cognitive_memory.py` | REPLACE bằng `config_loader` |
| Loader thứ hai | `src/mateai/config/settings.py` | xem D1 |

## 15. Frontend

| Feature | Implementation | Status | Action |
|---|---|---|---|
| Voice client + audio player | `web/app.js` (portal, `/ws/v1/voice-stream`, 1 AudioContext, `playVoiceAudio`) | Canonical | KEEP |
| Voice client + audio player | `web/hud.js` (`/ws/hud`, 2 AudioContext riêng) | Active-dup | MERGE khi HUD chuyển sang giao thức P1 |
| Admin API client | `admin/lib/api.ts` | Canonical | KEEP |
| Admin fetch/WS trực tiếp | `admin/components/computer-use/ComputerUseDashboard.tsx` (5), `topology/SystemCanvas.tsx` (6) bỏ qua `lib/api.ts` | Active-dup | MERGE vào `lib/api.ts` |

## 16. Background workers

| Trách nhiệm | Implementation | Ghi chú | Action |
|---|---|---|---|
| Đo CPU/RAM | `health_monitor`, `autonomous_sentinel`, `fast_command_router`, `db_manager`, `server` (5 module gọi `psutil`) | lấy mẫu độc lập ở nhiều vòng lặp | MERGE về một nguồn telemetry |
| Theo dõi + cảnh báo | `autonomous_sentinel` (dùng `health_monitor`) | trách nhiệm khác nhau (đo vs cảnh báo) | KEEP |
| HUD telemetry loop | `server._hud_telemetry_loop` | đo riêng cho HUD | MERGE với nguồn telemetry chung |
| Dọn HITL | `zero_trust` (purge) + `security/hitl_manager` (cleanup loop) | hai vòng dọn cho hai HITL | MERGE cùng HITL |

`asyncio.create_task` / `Thread` xuất hiện ở 25 file (server.py 24 chỗ); chưa có vòng đời chung (đăng ký/hủy khi shutdown) cho toàn bộ.

---

## Tổng hợp số lượng

| Nhóm | Số implementation | Canonical | Active-dup | Facade | Test-only | Dead | Unwired |
|---|---|---|---|---|---|---|---|
| Voice pipeline | 5 | 1 | 4 | 0 | 0 | 0 | 0 |
| TTS + sentence + sanitize | 16 | 5 | 6 | 2 | 2 | 1 | 2 (mateai) |
| LLM | 7 nhóm | 2 | 4 | 0 | 0 | 0 | 1 |
| Security | 13 | 6 | 7 | 0 | 0 | 0 | 0 |
| Database | 5 | 1 | 3 | 0 | 0 | 0 | 1 |
| Lịch sử | 6 | 3 | 2 | 0 | 0 | 0 | 0 |
| Skill/registry | 8 | 2 | 2 | 2 | 0 | 2 | 1 |
| WebSocket path | 8 | 5 | 1 | 0 | 0 | 0 | 0 (+2 alias legacy) |
| Cây `src/mateai` + `apps` | 72 file | 0 | 0 | 0 | 69 (test-only) | 3 | toàn bộ |
