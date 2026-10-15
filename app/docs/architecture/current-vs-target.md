# Hiện trạng và đích — VN-MateAI

> **Trạng thái sau P1–P15 (2026-10-05):** bảng §3 dưới đây là ảnh chụp Phase 0. Hiện trạng: `docs/migration/final-audit.md` §0 (30 câu §213) và `docs/production/readiness-score.md`.

> **Phase 0 (chỉ đọc) — 2026-10-05**, branch `refactor/phase-0-1-safety-net`, commit `128c87d`.
> Thay bản 2026-10-01: bản đó mô tả `core/` là runtime. Từ đó mã chạy thật đã chuyển sang `src/mateai/` (quyết định D1). `core/` chỉ còn `plugin_manager.py`.
> Mọi nhận định dưới đây có bằng chứng `file:dòng` hoặc lệnh kiểm. Chỗ nào chưa đo thì ghi **chưa đo**.
> Đích nhắm tới là "Enterprise Autonomous AI Supervisor" theo yêu cầu `prompupdatequytrinchuan.txt` (§10–§36, §160–§166).

## 1. Bản đồ repository (mã chạy thật)

| Vùng | Đường dẫn | Quy mô | Vai trò thật |
|---|---|---|---|
| Ứng dụng máy chủ | `src/mateai/` | 125 file `.py`, 45 782 dòng | Toàn bộ runtime; điểm vào `main.py` → `mateai.interfaces.http.server:app` |
| ↳ application | `src/mateai/application/{agent,voice,commands,conversation,skills,security,operations,devices,knowledge,analytics,enterprise,administration}` | ~14 600 dòng | Use case: vòng agent, lượt thoại, lệnh nhanh, cổng tool, HITL, sentinel, giao việc |
| ↳ infrastructure | `src/mateai/infrastructure/{llm,tts,audio,database,connectors,http,cache,memory,notifications,security,directory,files,websocket}` | ~12 200 dòng | Adapter: LLM provider, TTS, STT/VAD, SQLite, connector ngoài |
| ↳ interfaces | `src/mateai/interfaces/{http,websocket,telegram,email,desktop}` | ~10 300 dòng | 204 route HTTP, 9 route WebSocket, Telegram, email, mic máy chủ |
| ↳ domain | `src/mateai/domain/*` | 595 dòng | Entity Python thuần — **0 nơi dùng ngoài test** (`grep mateai.domain.<x>` = 0) |
| ↳ config | `src/mateai/config/{loader,secret_box}.py` | 821 dòng | Cấu hình Pydantic + mã hoá bí mật Fernet (`enc:v1:`) |
| Bộ nạp skill | `core/plugin_manager.py` | 498 dòng | Quét `skills/*.py`, decorator `@export_skill` (102 skill) |
| Skill | `skills/*.py` | 33 file | 12 file là lớp chuyển tiếp ≤ 36 dòng sang `application/skills/builtin`; còn lại là skill thật |
| Worker phụ | `workers/` | 4 file | Computer-use (self-healing UI, vault phiên trình duyệt, OS driver, daemon từ xa) |
| Agent máy trạm | `client_agent/` | — | Agent 2.3.1 (Windows .exe, macOS), giao thức `/ws/client` |
| Firmware robot | `esp32_firmware/src/main.cpp` | — | ESP32-S3, giao thức `/api/v1/xiaozhi/ws` |
| Frontend | `web/` (portal `app.js`, HUD `hud.html`/`hud.js`), `admin/` (Next.js) | — | Portal quản trị, HUD thoại |
| Test | `tests/` | 106 file pytest + 14 file `.mjs` | **664 pass** (lần chạy 2026-10-05) |
| CI | `.github/workflows/tests.yml` | — | Chỉ chạy `pytest -q`; không lint, type-check, scan, deploy |
| Dữ liệu | `vnmateai.db`, `hr_kpi.db` (SQLite, không commit) | — | Nguồn dữ liệu chính duy nhất hiện nay |

## 2. Đồ thị gọi thật (tóm tắt — chi tiết: `docs/realtime/call-graph.md`)

```
Portal mic ─┐  /ws/v1/voice-stream ── realtime_voice_ws.py:316 ─┐
HUD        ─┤  /ws/hud ─────────────── hud_voice.py:224 ─────────┤
Robot      ─┤  /api/v1/xiaozhi/ws ─── xiaozhi_gateway.py:890 ────┼─► voice_turn.process_voice_turn
Mic máy chủ ┤  (luồng desktop) ────── voice_controller.py:662 ───┤      ├─ fast_command_router.dispatch   (không LLM)
REST        ┘  POST /api/v1/voice-command ─ routers/voice.py:135 ┘      ├─ classify_intent → acoustic ACK (cache)
                                                                        ├─ llm_engine.stream_voice_response → provider.stream
Telegram ── telegram_gateway.py:199 ── llm_engine.ask_async ◄───────────┘   (câu cần tool → ask_async)
                                              │
                                              ▼  vòng agent ≤ MAX_TOOL_ROUNDS = 6 (llm_engine.py:57)
                               tool_gate.run_tool_with_policy (tool_gate.py:84)
                                  risk (zero_trust) → HITL → RBAC (security_guard) → thực thi → audit
                                              │
               ┌──────────────────────────────┼─────────────────────────────┐
     plugin_registry.execute_tool     plugin_manager.execute_skill     orchestrator (máy trạm /ws/client)
```

## 3. Hiện trạng theo năng lực đích

Ký hiệu: ✅ có và chạy · 🟡 có một phần · ❌ không có.

| Năng lực đích (prompt §) | Hiện trạng | Bằng chứng |
|---|---|---|
| Một đường thoại chuẩn (§41) | ✅ 5 kênh thoại đều qua `process_voice_turn` | các dòng gọi ở §2 |
| LLM stream (§44) | ✅ `provider.stream` | `llm_provider.py:116,178,294,582` |
| Tách câu + TTS theo câu (§47–§49) | ✅ `SentenceBuffer` → `StreamingTTSWorkerPipeline` | `voice_turn.py:465–501` |
| Hàng đợi audio có giới hạn (§51) | ✅ hàng đợi câu `maxsize=5` | `tts_queue_pipeline.py:98` |
| Audio nhị phân (§52) | ✅ portal, HUD, robot; Base64 còn ở REST | `binary_transport.py` |
| Câu đệm (ACK) cache (§57) | ✅ không gọi LLM | `voice_turn.py:432–435` |
| Lệnh nhanh không LLM (§56) | ✅ nhưng "kiểm tra CPU" **chặn event loop 50 ms** | `fast_command_router.py:267,291` (`psutil.cpu_percent(interval=0.05)` trong hàm async) |
| Một abstraction LLM (§45) | 🟡 `BaseLLMProvider` + 3 adapter; Whisper STT tự dựng `OpenAI()` | `audio_processor.py:490` |
| Một TTS (§49) | 🟡 `TTSStreamEngine`; skill `ninerouter_skills` tự gọi `/audio/speech` | `skills/ninerouter_skills.py:255` |
| Policy Engine (§14) | ❌ không có module chính sách độc lập; luật nằm rải ở 4 nơi (bảng rủi ro, RBAC, danh sách cấm, cấu hình) | `risk-model.md`, `policy-model.md` |
| Danh sách cấm / bắt duyệt trong cấu hình | ❌ **không được áp dụng** ở cổng tool — chỉ dùng ở nút thử của trang bảo mật | `safety_guard.py:257` chỉ được gọi từ `routers/security.py:166`; `tool_gate.py:116` dùng hàm khác |
| Risk Engine (§15) | 🟡 thang 1–5 theo **tên** tool + khai báo registry; không xét môi trường, đích, khả năng hoàn tác | `zero_trust.py:58–125,164–231` |
| Mức tự trị L0–L5 (§16) | ❌ chỉ có ngưỡng "≥ 3 phải duyệt"; tài khoản admin bỏ qua mọi mức, kể cả mức 5 | `zero_trust.py:43`, `tool_gate.py:128` |
| Lệnh từ chối tuyệt đối (DENY) | 🟡 chỉ 2 tên tool (`format_drive`, `wipe_all_data`); nhánh `BLOCKED` của cổng **không bao giờ chạy** | `security_guard.py:109`; `zero_trust.py:781` chỉ trả SAFE/NEED_CONFIRM |
| Thứ tự kiểm tra | 🟡 rủi ro → duyệt → **rồi mới** RBAC: có thể sinh yêu cầu duyệt cho việc người hỏi không có quyền | `tool_gate.py:127–186` |
| Danh tính tác nhân AI (§11–§12) | ❌ không có `agent_id`; hành động ghi theo người/thiết bị gọi | `grep agent_id` = 0 |
| HITL (§32) | ✅ hàng đợi duyệt bền (khôi phục từ audit), hết hạn không tự cho phép | `zero_trust.py:127–779` |
| Số đường phân quyền tool (§17) | 🟡 **3 đường**: `tool_gate`, `execute_with_hitl` (router skills), cổng duyệt riêng trong `plugin_registry` | `tool_gate.py:84`, `routers/skills.py:233–245`, `plugin_registry.py` |
| Tác nhân con gọi thẳng hàm | ❌ `agent_orchestrator` gọi `assign_task_intelligently`… **không qua cổng** | `agent_orchestrator.py:252,264` |
| Giới hạn vòng agent (§35) | 🟡 6 vòng/lượt; không giới hạn thời gian, chi phí, số tool tổng, phạm vi dữ liệu | `llm_engine.py:57,1046` |
| Kill switch (§95) | ❌ không có công tắc toàn cục / theo tool / theo tác nhân | `grep kill_switch` = 0 |
| Task ledger tự trị (§21–§22) | ❌ có 4 khái niệm "task" khác nhau, không cái nào là sổ tác vụ tự trị có máy trạng thái | `task-model.md` |
| Goal / Priority / Incident (§20, §23, §153) | ❌ | `grep goal_id, incident_id` = 0 |
| Evidence ledger, kiểm chứng sau hành động (§27–§30) | ❌ kết quả tool là chuỗi trả về; không bước verify | — |
| Escalation engine (§80) | 🟡 có bộ phát cảnh báo đa kênh (Telegram/Teams/email/Slack/webhook) nhưng không có luật leo thang | `alert_dispatcher.py` |
| Audit bất biến (§68–§69) | ✅ một bảng `audit_logs`, chỉ INSERT; 5 hàm ghi đều đổ về `erp_db.write_audit_log` | `erp_database.py:882` |
| Quan sát (§71–§74) | 🟡 trace thoại đủ mốc nhưng **chỉ trong RAM** (`deque(maxlen=500)`), mất khi khởi động lại; không OpenTelemetry; không đếm token/chi phí | `voice_turn.py:94` |
| Health (§100) | ✅ `/livez`, `/readyz`, `/startupz` | `server.py:322,328,368` |
| Tắt máy an toàn (§101) | 🟡 dừng worker nền, sentinel, Telegram; **không** đóng pool HTTP, email gateway, luồng proactive, WebSocket máy trạm | `server.py:1023–1052`; `connection_pool.close_all` không có caller |
| Giới hạn tần suất (§78) | ❌ không có cho đăng nhập / WS / LLM / API quản trị (chỉ connector ngoài) | `grep rate_limit` |
| Dữ liệu (§62–§65) | 🟡 SQLite là nguồn duy nhất; 3 module application truy vấn SQL thẳng; không Redis/PostgreSQL/object storage | `agent_orchestrator.py`, `onboarding_workflow.py`, `proactive_manager.py` |
| Nhiều tenant / phòng ban (§67) | ❌ không có `tenant_id`, không lọc dữ liệu theo phòng ban | — |
| Chống prompt injection (§92–§94) | ❌ kết quả tool đi vào hội thoại dưới vai `tool` mà không đánh dấu "dữ liệu không tin cậy"; `identity_core.md` đọc thẳng vào system prompt | `llm_engine.py:1173,230` |
| Che dữ liệu trước khi gửi LLM (§91) | ✅ câu hỏi và kết quả tool qua `mask_sensitive_data` | `llm_engine.py:847,896,1170,1340` |
| Không `shell=True` (§19) | 🟡 còn 2 chỗ, lệnh là hằng số (không chèn được) | `domain_sync.py:101,193` |
| Mở rộng ngang (§114) | ❌ phiên thoại, hàng đợi TTS, WS, task chờ, trace đều nằm trong RAM một tiến trình | `call-graph.md` §5 |

## 4. Kiến trúc đích (giữ "modular monolith", §161)

```
interfaces (http · websocket · telegram · email · desktop)        ← chỉ chuyển đổi giao thức
      │
application
  voice/        process_voice_turn (giữ nguyên — đã là bản chuẩn)
  agent/        llm_engine (vòng agent) ──► control/  ◄── MỌI tác nhân, router, tác vụ nền
  control/      [MỚI, GỘP TỪ CÓ SẴN] PolicyEngine · RiskEngine · AutonomyLevel · KillSwitch · LoopBudget
                = tool_gate + zero_trust(risk, HITL) + security_guard(RBAC) + settings.security
  tasks/        [GỘP] một Task Ledger có máy trạng thái (thay 4 khái niệm task hiện có cho phần tự trị)
  evidence/     [MỚI] bằng chứng + kiểm chứng sau hành động
  operations/   sentinel → sinh task (không tự xử lý) · alert_dispatcher → escalation
infrastructure (llm · tts · audio · database · connectors · notifications)
domain         entity thuần — chỉ giữ cái được dùng thật
```

Nguyên tắc: **không tạo bản mới bên cạnh bản cũ**. `control/` được hình thành bằng cách **chuyển** `tool_gate.py` + phần rủi ro/HITL của `zero_trust.py` + phần RBAC của `security_guard.py` vào một chỗ, rồi buộc `routers/skills.py`, `plugin_registry` và `agent_orchestrator` đi qua nó. Chi tiết thứ tự: `docs/migration/production-refactor-plan.md` §0.

## 5. Câu hỏi §213 — trả lời theo hiện trạng (Phase 0)

| # | Câu hỏi | Trả lời hôm nay |
|---|---|---|
| 1 | Đường thoại chuẩn | `application/voice/voice_turn.process_voice_turn` |
| 2 | Abstraction LLM | `infrastructure/llm/llm_provider.BaseLLMProvider` (ngoại lệ: Whisper STT) |
| 3 | Đường TTS | `infrastructure/tts/tts_stream_engine.TTSStreamEngine` (ngoại lệ: một skill) |
| 4 | Giao thức WS thoại | `/ws/v1/voice-stream` cho portal; HUD còn schema riêng `/ws/hud`; robot `/api/v1/xiaozhi/ws` (+ alias `/ws/audio-stream`) |
| 5–6 | Registry tool / skill | **Hai**: `core/plugin_manager` (skill `@export_skill`) và `plugin_registry` (tool đăng ký động: connector, computer-use) |
| 7 | Policy Engine | Chưa có bản chuẩn — 4 nguồn luật rời |
| 8 | Đường phân quyền | 3 (xem §3) |
| 9 | Vòng đời task | Không có vòng đời chung |
| 10 | Đường audit | Một bảng `audit_logs`; 5 hàm ghi |
| 16–17 | LLM có vượt được phân quyền? Tác nhân chạy tool không qua chính sách? | LLM không tự nâng quyền được (RBAC theo `caller`), nhưng (a) tài khoản admin chạy được tool mức 5 không cần duyệt; (b) `agent_orchestrator` gọi hàm trực tiếp; (c) danh sách cấm trong cấu hình không được áp dụng |
| 18 | Tự trị chạy vô hạn? | Một lượt ≤ 6 vòng tool; tác vụ nền (sentinel, proactive, email) chạy mãi theo thiết kế, **không có kill switch** |
| 19 | Người dừng được tác nhân? | Dừng được lượt thoại (barge-in / huỷ); **không** dừng toàn cục được |
| 28 | p50/p95/p99 thoại | Xem `docs/realtime/voice-architecture.md` §4 (đo 2026-10-03, p99 chưa có đủ mẫu) |
| 29 | Tỷ lệ thành công tác vụ tự trị | **Chưa đo** — chưa có sổ tác vụ |
| 30 | Chưa sẵn sàng production | `docs/production/readiness-score.md` |
