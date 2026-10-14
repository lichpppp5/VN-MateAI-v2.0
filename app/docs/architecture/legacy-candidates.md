# Ứng viên legacy / xóa (Legacy Candidates)

> **2026-10-05:** danh sách hiện hành (đã kiểm lại) ở `docs/migration/legacy-removal-plan.md`. Tệp này giữ lịch sử những gì đã xoá; vài mục "chưa làm" bên dưới thực tế đã xong — xem bảng đính chính ở tệp mới §1.

> Phase 0 — chỉ phân loại, **chưa xóa gì**. Thay thế `docs/cleanup-candidates.md` (tài liệu đó tham chiếu các file đã không còn, ví dụ `core/api_voice_stream.py`, `scratch/`, `vnmateai.db.bak-*`).
> Bằng chứng xem `duplication-matrix.md`. Điều kiện xóa (Phase C): thay thế đã có, caller đã chuyển, test pass, đã chạy thử runtime.

## Trạng thái (cập nhật Phase Security, 2026-10-02)

| Mục | Trạng thái |
|---|---|
| A1 `edge_tts_stream_audio`, A7 nhánh gTTS (3 nơi) | **ĐÃ XOÁ** |
| B1 `SentenceBoundaryStreamer`, B2 `get_acoustic_ack_for_query` | **ĐÃ XOÁ** (test chuyển sang thành phần canonical) |
| C1 TTS của `AudioEngine`, C2 race gTTS trong `server.py` | **ĐÃ THAY** bằng `TTSStreamEngine`, bản cũ đã xoá |
| C3 `llm_engine._sanitise_for_tts`, `clean_text_for_tts` | **ĐÃ THAY** bằng `sanitise_for_tts` + `shorten_for_speech`, bản cũ đã xoá |
| Thêm: `SentenceStreamer`, `split_into_sentences`, `race_synthesise`, `_safe_tts`, `prewarm_tts_cache` | **ĐÃ XOÁ** (chỉ còn test / không caller) |
| C7 pipeline HUD trong `server.py`, C8 `VoiceSessionStore` (lịch sử) + `voice_controller._session_history` | **ĐÃ THAY** bằng `core/voice_turn.py` + `memory_manager` (Phase 3) |
| C4 `stream_voice_response` | **ĐẢO HƯỚNG** theo quyết định Phase 3: là bước LLM chung của voice; đường `llm_engine.stream` (provider) mất caller — Phase 5 chuyển `stream_voice_response` lên provider |
| Thêm: vòng tool 1 bước của portal (`execute_tool_call` và phụ trợ) | **ĐÃ XOÁ** (Phase 3) |
| C5 `_call_llm_direct/_call_llm_router`, C6 phần `ai_delegation`, bộ tách câu `_extract_sentences` | **ĐÃ THAY** bằng provider chung / `SentenceBuffer` (Phase 5) |
| C6 còn lại: `meta_architect`, `analytics_engine` (client đồng bộ) | chưa làm — xem plan §9 |
| A2 `select_relevant_skills`, A3 `smart_comm_router` | **ĐÃ XOÁ** (Phase 6) |
| A4 `erp_organization` | **ĐÃ ĐĂNG KÝ** làm skill (`skills/erp_organization.py`) thay vì xoá |
| C9 `client_template/` | **ĐÃ THAY** — zip tải về sinh từ `client_agent/`, fork đã xoá (Phase 6) |
| D7 `plugin_manager` + `plugin_registry` | **ĐÃ GỘP**: danh mục = `plugin_manager`; registry = chính sách thực thi (Phase 6) |
| D1 HITL `security/hitl_manager.HITLManager` | **ĐÃ XOÁ** — không có producer; HITL duy nhất là `zero_trust.hitl_manager` |
| Kho audit file `logs/security_audit.log` | **ĐÃ THAY** bằng `audit_logs` (bất biến); API xoá log đã gỡ |
| A5–A6, C10–C11, D2–D6, D8, E1–E8 | chưa làm |

## A. Xóa được ngay khi vào Phase C (không có caller)

| # | Đối tượng | Bằng chứng không có caller | Rủi ro |
|---|---|---|---|
| A1 | `core/audio/streaming_tts_pipeline.py::edge_tts_stream_audio` | không import/gọi ở `core`, `skills`, `workers`, `main.py`, `scripts`, `tests`, `web`, `admin` | thấp |
| A2 | `core/plugin_registry.py::PluginRegistry.select_relevant_skills` | không caller tĩnh, lười, chuỗi, test | thấp |
| A3 | `core/connectors/smart_comm_router.py` (166 dòng) | không file nào tham chiếu tên module hay `SmartCommRouter`, kể cả `skills/registry.json` | thấp |
| A4 | `core/skills/erp_organization.py` | không có shim trong `skills/`, không có trong `registry.json`, không test → LLM không bao giờ thấy tool này | thấp — nhưng cần chủ dự án xác nhận không muốn **đăng ký** nó thay vì xóa |
| A5 | WS alias `/ws/voice` (`server.py:4647`) | không client nào trong repo kết nối | thấp |
| A6 | `apps/api`, `apps/realtime`, `apps/worker` | chỉ có `__init__.py` 5 dòng, không ai import | thấp — giữ nếu D1 chọn tách tiến trình |
| A7 | Nhánh dự phòng gTTS: `tts_stream_engine._synthesise_gtts`, `audio_processor.AudioEngine._tts_gtts`, `server._tts_bytes._gtts_direct` | `gTTS` chưa bao giờ có trong `requirements.txt` → `import gtts` luôn lỗi, nhánh không bao giờ chạy. Thêm `gTTS` thì xung đột `click` với `huggingface-hub` (Phase 1). Ngoài ra là giọng khác Hoài My | thấp — hành vi hiện tại không đổi |

## B. Chỉ còn test gọi (sửa test rồi xóa)

| # | Đối tượng | Test phụ thuộc | Việc cần làm trước |
|---|---|---|---|
| B1 | `streaming_tts_pipeline.SentenceBoundaryStreamer` (ghi là tương thích `api_voice_stream.py` — file này không còn) | `tests/test_phase4_tts_pipeline.py`, `tests/test_phase92_voice_stream_pipeline.py` | chuyển test sang `StreamingTTSWorkerPipeline` + `SentenceBuffer` |
| B2 | `streaming_tts_pipeline.get_acoustic_ack_for_query` | `tests/test_phase6_acoustic_ack.py` | test trực tiếp `acoustic_ack_catalog` |

## C. Thay thế sau khi chuyển caller (Phase B rồi C)

| # | Đối tượng cũ | Bản thay thế | Caller phải chuyển |
|---|---|---|---|
| C1 | `AudioEngine.text_to_speech_stream / text_to_speech_bytes / _tts_gtts / _tts_9router` | `TTSStreamEngine` | `xiaozhi_gateway` (3), `voice_controller` (3), `server.py` (5), `scripts/prewarm_vocabulary.py`, `prewarm_tts_cache` |
| C2 | `server.py` race gTTS riêng cho HUD (~dòng 215–260) | `TTSStreamEngine` | HUD (P2) |
| C3 | `llm_engine._sanitise_for_tts`, `audio_processor.clean_text_for_tts` | `sentence_streamer.sanitise_for_tts` | `llm_engine` (6), `server` (3), `xiaozhi_gateway` (2) — cần test so sánh đầu ra trước |
| C4 | `llm_engine.stream_voice_response` (vòng fallback riêng) | `llm_engine.stream` qua provider | P2, P3, P4 |
| C5 | `llm_engine._call_llm*` (vòng fallback riêng) | `provider.complete()` | `ask_async`, `server.py:5771` |
| C6 | `OpenAI(...)`/`AsyncOpenAI(...)` tự tạo trong `analytics_engine`, `meta_architect`, `server.py:3083`, `ai_delegation` | provider abstraction | chính các module đó |
| C7 | `server._process_hud_voice_command[_body]` (P2) | P1 `realtime_voice_ws` | `web/hud.js` |
| C8 | `voice_session.VoiceSessionStore`, `voice_controller._history` | `memory_manager` | P2, P4 |
| C9 | `client_template/` (bản fork) | sinh zip từ `client_agent/` | endpoint download agent (`server.py:~6840`) |
| C10 | 14 chỗ tự đọc `config.json` | `config_loader.settings` | `server.py` (8), `llm_engine` (2), `telegram_gateway` (2), `autonomous_sentinel`, `cognitive_memory` |
| C11 | `sqlite3.connect` riêng trong `autonomous_sentinel`, `health_monitor` | `ERPDatabase` / repository | chính các module đó |

## D. Gộp (MERGE) — cả hai bản đều đang chạy, không bản nào là "legacy" thuần

| # | Nhóm | Ghi chú an toàn |
|---|---|---|
| D1 | HITL: `zero_trust.HumanInTheLoopManager` + `security/hitl_manager.HITLManager` | không được để rơi request đang chờ duyệt; gộp trạng thái trước rồi mới chuyển caller |
| D2 | Audit: 3 sink (`safety_guard` file log, `security_guard` bảng `audit_logs`, `zero_trust.log_security_audit`) | `audit_logs` là INSERT-only — giữ nguyên tính bất biến |
| D3 | Đánh giá rủi ro: `zero_trust` + `safety_guard.SecurityEngine` | so sánh bảng quyết định trên toàn bộ tool trước khi gộp |
| D4 | Role model: `auth_manager` (admin/manager/viewer) + `security_guard` (admin/it_support/operator/viewer) | cần bảng ánh xạ vai trò được chủ dự án duyệt |
| D5 | User store: `users.json` + bảng `users` | chọn một nguồn sự thật |
| D6 | Bảng `tasks` dùng chung bởi `db_manager` và `ERPDatabase` | một repository task |
| D7 | Registry: `plugin_manager` + `plugin_registry` | `plugin_registry` có circuit breaker — giữ hành vi đó |
| D8 | Telemetry CPU/RAM ở 5 module | một nguồn đo |

## E. REVIEW — chưa đủ bằng chứng hoặc cần quyết định sản phẩm

| # | Đối tượng | Vì sao chưa quyết |
|---|---|---|
| E1 | **Toàn bộ `src/mateai/` (69 file)** | bản viết lại chưa nối vào runtime; hoặc biến nó thành đích di chuyển code, hoặc xóa và trỏ test về `core/` — xem quyết định D1 trong `docs/migration/production-refactor-plan.md` |
| E2 | WS alias `/ws/audio-stream[/{id}]` | không client trong repo, nhưng thiết bị ESP32 đã nạp firmware cũ (ngoài repo) có thể vẫn dùng — cần log truy cập thực tế |
| E3 | Skill máy trạm sao y trong `skills/` (`pc_control_skills.py`, `visual_skills.py`, `sysadmin_skills.py`, `custom_skills.py`, `excel_records_skill.py`) | server đang nạp và có thể thực thi trên máy chủ; xóa sẽ đổi hành vi |
| E4 | `esp32_firmware/vnmate_robot/` vs `esp32_firmware/src/` | cần người có phần cứng xác nhận bản nào đang được nạp |
| E5 | `hr_kpi.db` (`domain_sync`) — bảng `employees` thứ hai | dữ liệu AD sync, cần xem có triển khai thật không |
| E6 | 17 `httpx.AsyncClient` ngoài pool | một số là client theo connector có vòng đời riêng hợp lý |
| E7 | **Rủi ro bảo mật (không phải legacy):** `config/data_sources.json` chứa credential, chỉ được bảo vệ bằng `os.chmod(0o600)` — trên Windows lệnh này không giới hạn quyền đọc | cần ACL (`icacls`) hoặc kho secret — xử lý ở Phase Security |
| E8 | 54 dòng trong `audit_logs` thật là sự kiện giả do test tạo (trước khi có cô lập ở Phase 1) | bảng thiết kế chỉ-ghi; xoá hay giữ là quyết định của chủ dự án |

## Không phải legacy (đã kiểm tra, giữ)

- `core/voice_widget.py` — không ai import nhưng được `voice_controller` chạy dạng subprocess (`WIDGET_SCRIPT`).
- `workers/remote_worker_daemon.py`, `scripts/prewarm_vocabulary.py`, `train_wake_word.py` — script CLI chạy tay.
- `skills/*.py` dạng shim re-export (`file_system`, `integration_tools`, …) — là bề mặt mà `plugin_manager` quét bằng `glob`.
- Alias HTTP (`/topology`, `/computer-use`, `/roi-dashboard`, `/api/v1/wake-word/*`) — cùng handler, chỉ khác transport.
