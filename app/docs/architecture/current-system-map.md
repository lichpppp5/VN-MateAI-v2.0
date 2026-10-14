# Bản đồ hệ thống hiện tại — VN-MateAI

> Cập nhật **2026-10-06**, nhánh `refactor/phase-0-1-safety-net`, đối chiếu `promptfinalvnmateai.txt` (Enterprise Autonomous AI Supervisor, 194 mục).
> Mọi con số lấy từ mã nguồn, `pytest`, quét AST, server THẬT (PostgreSQL / Redis / S3 chạy cục bộ) hoặc máy chủ đang chạy — không ước lượng. §4: mã nguồn thắng tài liệu, hành vi chạy thật thắng mã nguồn.
> Chi tiết: `canonical-components.md`, `dependency-rules.md`, `docs/realtime/*`, `docs/autonomy/*`, `docs/production/readiness-score.md`, `docs/production/owner-todo.md`.

---

## 1. Kết luận ngắn

| Câu hỏi | Trả lời |
|---|---|
| Kiến trúc lõi đúng hướng prompt chưa? | **Rồi.** Mỗi chức năng lõi một bản chuẩn; 10 luật kiến trúc tự động = 0 vi phạm; **831 pytest (cả SQLite lẫn PostgreSQL: 823 + 8 bỏ qua có lý do) + 14 test Node** đạt; lint lỗi chạy thật = 0; quét lỗ hổng phụ thuộc chỉ còn chromadb (không áp dụng — §7). |
| Đã đủ điều kiện production theo §150? | **Gần đủ.** Hạ tầng Docker đang chạy thật (PostgreSQL 16, Redis 7, S3 có khoá, OTel collector; chỉ mở trên 127.0.0.1); máy chủ đã nối Redis + S3 + OTLP; sao lưu tự động 02:00 đẩy lên S3; robot chạy firmware 54.0. Đã có: identity, phân quyền (RBAC + ABAC), policy, risk, audit có chuỗi băm, evidence, verification, kill switch + chế độ khẩn cấp, backup / restore (+ đẩy lên S3), health, trace OpenTelemetry, log có cấu trúc, rate limit, timeout, kịch bản vàng 15/15. **Đã cutover sang PostgreSQL (2026-10-06)**: ứng dụng đọc / ghi PostgreSQL 16 Docker (schema `vnmate`, `hr`); tệp SQLite giữ làm đường lùi. Còn: CI GitHub chưa xác nhận chạy. |
| Còn trùng lặp không lý do? | Không. Hai chỗ "hai thứ" có lý do (§6). |
| Còn đường cũ chạy được? | Một: alias `/ws/audio-stream` cho firmware cũ (cùng handler mới, log `[DEPRECATED]`). |
| Rủi ro lớn nhất còn lại | Máy chủ ứng dụng + PostgreSQL cùng một máy (một điểm hỏng; sao lưu 02:00 đẩy lên S3 cùng máy) — cần bản sao lưu ngoài máy. Mật khẩu admin đã đổi (chủ dự án xác nhận 2026-10-06). |

---

## 2. Cây thư mục (mã nguồn thật; bỏ `__pycache__`, `node_modules`, bản build)

```text
VNMateaiv1/
├── main.py                     điểm vào: 2 listener uvicorn trên cùng app (HTTPS settings.PORT=443, IoT settings.IOT_PORT=8000)
├── src/mateai/                 MÃ CHÍNH — 188 tệp Python, 48 642 dòng
│   ├── config/        4 tệp    972 dòng   cấu hình chuẩn (loader, secret_box mã hoá khoá, log_setup)
│   ├── application/  70 tệp 18 609 dòng   nghiệp vụ / use case (không SQL, không SDK — RULE-011/012/024)
│   ├── infrastructure/47 tệp 12 481 dòng  adapter: DB (+ di trú PG), LLM, TTS, audio, connector, cache (+ Redis),
│   │                                      object storage (local / S3), observability (OpenTelemetry)
│   └── interfaces/   66 tệp 16 566 dòng   HTTP, WebSocket, Telegram, Email, desktop, IoT
├── core/plugin_manager.py               API skill `@export_skill` (+ tool contract) — vị trí cũ vì skill ngoài repo import nó
├── skills/           33 tệp  5 256 dòng   bề mặt đăng ký skill; tệp có lệnh cấp module bị TỪ CHỐI nạp
├── client_agent/     14 tệp  4 415 dòng   agent máy trạm (độc lập, không import `mateai`)
├── workers/           5 tệp  1 246 dòng   worker từ xa
├── web/               8 tệp 28 260 dòng   portal + HUD; `voice-audio-queue.js` (bộ phát chung + jitter buffer)
├── admin/            41 tệp  5 435 dòng   app Next.js — còn phục vụ /admin/topology và /admin/computer-use (build ra admin/out, CI build)
├── esp32_firmware/  130 tệp 10 414 dòng   firmware robot Xiaozhi
├── deploy/                              docker-compose.infra.yml (PostgreSQL, Redis, S3, OTel collector) + otel-collector.yaml
├── scripts/           9 tệp  1 265 dòng   backup (+push/pull S3), migrate_sqlite_to_pg, dev_infra, bench_codec, build CSS
├── tests/           154 tệp 24 660 dòng   831 pytest (chạy được trên SQLite và PostgreSQL) + 14 Node; tests/architecture = luật phụ thuộc
└── docs/             53 tệp
```

Không có tệp tên `*_old / *_new / *_v2 / *_v3 / *_legacy / *_backup / *_copy / *_tmp / *_experimental` (§14).

### 2.1 `application/`

| Gói | Bản chuẩn |
|---|---|
| `voice` | `voice_turn.process_voice_turn` (một lượt thoại cho 5 kênh, có rate limit + trace), `sentence_buffer`, `speech_text` |
| `commands` | `fast_command_router` — lệnh nhanh không LLM, vẫn qua cổng tool |
| `agent` | `llm_engine` (vòng agent: ngân sách vòng / tool / thời gian / tool lỗi lặp; rate limit), `tool_gate` (cổng tool DUY NHẤT), `agent_orchestrator` (đa tác nhân: tin nhắn có `message_id` + người được phục vụ) |
| `security` | `policy_engine.authorize` (kill switch → L5 / cấm → RBAC → **ABAC** → rủi ro → duyệt / uỷ quyền → **chế độ khẩn cấp**), `risk_engine`, `security_guard` (RBAC + thuộc tính ABAC), `zero_trust` (hàng duyệt HITL), `approval_decisions`, `rate_limit`, `auth_manager` |
| `tasks` | `ledger` — sổ tác vụ AI (NEW…ESCALATED), **mục tiêu 3 cấp**, **priority engine**, **vòng đời sự cố**; `verification` |
| `administration` | `config_governance.save_config` (một đường ghi cấu hình), `config_service` (+ model registry), `autonomy_settings` |
| `operations` | `health_monitor`, `autonomous_sentinel` (đóng sự cố khi đo lại thấy khôi phục), `alert_dispatcher`, `background_workers` |
| `enterprise` | `integrations`, `operations`, `erp_import`, `department_engine` |
| `knowledge` | `rag_engine` (tài liệu tải lên lưu bền ở object storage), `graph_rag` |

### 2.2 `infrastructure/`

| Gói | Nội dung |
|---|---|
| `database` | `erp_database` (điểm mở CSDL DUY NHẤT `open_sqlite`: PostgreSQL khi có `DATABASE_URL`, ngược lại SQLite; audit có chuỗi băm + trigger chặn sửa / xoá trên cả hai), **`pg_compat`** (lớp tương thích: dịch SQL kiểu SQLite → PostgreSQL, pool kết nối, mỗi tệp DB cũ → một schema), `db_manager`, **`pg_migration`** (SQLite ⇄ PostgreSQL có kiểm chứng checksum; `export_to_sqlite` cho sao lưu) |
| `cache` | `ephemeral_cache`, **`shared_state`** (RAM hoặc Redis: rate limit, bộ đếm khẩn cấp, khoá đăng nhập; Redis lỗi → lùi về RAM) |
| `files` | `file_export`, **`object_storage`** (local / S3) |
| `observability` | **`tracing`** (OpenTelemetry: none / console / otlp) |
| `llm` | `llm_provider` (Direct / NineRouter / TriBrain; **model registry**: BLOCKED không bao giờ gọi) |
| `tts`, `audio`, `connectors`, `memory` (trí nhớ có độ tin cậy + hạn dùng), `notifications`, `websocket`, `directory`, `http`, `security` | như trước |

### 2.3 `interfaces/`

`http/server.py` (264 dòng: dựng app, CORS, request-id + span HTTP, mô hình lỗi chuẩn, xác thực, listener IoT), `http/routes.py`, `http/lifecycle.py` (21 bước khởi động có báo cáo), `http/routers/*` (mỏng, RULE-027 = 0), `websocket/*`, `telegram`, `email`, `desktop`, `iot`.

---

## 3. Runtime (§10)

- **Tiến trình:** một tiến trình Python, một event loop, 2 listener uvicorn (HTTPS + cổng IoT lọc đường dẫn).
- **21 bước khởi động** (`lifecycle.default_steps()`, đo trên máy thật: 20 ok, email bỏ qua đúng): core → **tracing** → ephemeral_sweeper → telegram → observability_workers → hud_telemetry → encrypt_secrets → alert_dispatcher → topology_loop → autonomous_sentinel → udp_beacon → proactive_manager → rag_warmup → multi_agent_warmup → webhook_routes → connectors → acoustic_ack_warmup → connector_tools → computer_use_tool → background_workers → email_gateway. Kết quả: `GET /api/v1/health/startup`.
- **Endpoint:** 217 route HTTP + 9 WebSocket (bảng route cố định bằng test chụp; route mới phải khai báo).
- **Probe:** `/livez`, `/startupz`, `/readyz`.

| WebSocket | Mục đích | Xác thực / giới hạn |
|---|---|---|
| `/ws/v1/voice-stream` | thoại portal (audio nhị phân) | JWT; ≤ `ws_voice_sessions_per_user` phiên / người |
| `/ws/hud` | HUD: telemetry, lệnh thoại, duyệt (admin), audio nhị phân | JWT (không token = chỉ xem) |
| `/api/v1/xiaozhi/ws[/{id}]` · `/ws/audio-stream[/{id}]` (alias) | robot ESP32 | token thiết bị |
| `/ws/portal-ui`, `/ws/topology` | đồng bộ portal, sơ đồ | JWT (phát lên màn hình người khác: chỉ admin) |
| `/ws/client` | agent máy trạm | khoá thiết bị / enrollment |

---

## 4. Hai đường xử lý chuẩn

**Thoại (§18–§30):**

```text
mic / robot / portal / HUD / REST
  → STT (audio_processor, hoặc Web Speech trên trình duyệt)
  → voice_turn.process_voice_turn   (rate limit theo người → trace → span OTel "voice.turn" + span con)
      ├─ fast_command_router → tool_gate → câu trả lời cố định
      ├─ ACK dựng sẵn (không LLM, không nói "đã xong")
      └─ llm_engine.stream_voice_response → SentenceBuffer → speech_text → tts_queue_pipeline (bounded)
           → tts_stream_engine → khung nhị phân → VoiceAudioQueue (jitter buffer 50–250 ms)
  Barge-in: lệnh mới huỷ lượt cũ; WS đóng → huỷ lượt.
```

**Hành động (§31–§57):**

```text
LLM / fast router / Portal / đa tác nhân / plugin_registry gọi thẳng
  → tool_gate.run_tool_with_policy  (span "tool.execute"; CURRENT_PRINCIPAL = người được phục vụ)
      → policy_engine.authorize:
           kill switch → L5 / tool cấm / từ khoá cấm → RBAC → ABAC (cấp bảo mật ≥ mức dữ liệu của tool)
           → rủi ro → L0/L2 tự chạy | L3 chờ duyệt | L4 uỷ quyền có hạn → chế độ khẩn cấp (AI dồn dập → chỉ đọc)
      → DENY: không chạy + audit | duyệt: hàng đợi duy nhất
      → thực thi → verification → ledger (bước + bằng chứng) → audit_logs (append-only, chuỗi băm)
  Vòng agent: ≤ 6 vòng, ≤ max_tool_calls_per_turn, ≤ max_agent_seconds, tool lỗi ≥ max_tool_failures_per_turn → dừng + leo thang.
```

---

## 5. Dữ liệu (§65–§68, §135)

| Kho | Vai trò | Trạng thái |
|---|---|---|
| `vnmateai.db`, `hr_kpi.db` (SQLite) | **đường lùi** (không còn được ghi từ 2026-10-06 19:35) | giữ nguyên + bản sao lưu đã kiểm chứng `backups/pre-pg-cutover/` |
| PostgreSQL | **nguồn sự thật** (PostgreSQL 16 Docker, schema `vnmate` 23 bảng + `hr` 2 bảng; `DATABASE_URL` mã hoá trong config.json) | **Cutover 2026-10-06**: dừng máy chủ → sao lưu SQLite (kiểm chứng) → `pg_migration.migrate` 23 bảng / 1 260 dòng khớp checksum 100 %, khoá ngoại hợp lệ hết → bật `DATABASE_URL` → khởi động: `/readyz` ok, đăng nhập sai bị từ chối + ghi audit vào PG, chuỗi audit `ok`, robot nối lại. Sao lưu chụp từ PG (REPEATABLE READ → tệp SQLite, đối chiếu checksum); khôi phục bằng `migrate`. Đường lùi: xoá `DATABASE_URL` → về tệp SQLite (dữ liệu ghi sau cutover phải chép ngược bằng `export_to_sqlite`) |
| Redis | trạng thái ngắn hạn dùng chung | **Đang dùng** — Redis 7 Docker (mật khẩu, 127.0.0.1); `REDIS_URL` mã hoá trong config.json. Không là nguồn sự thật |
| Object storage (local / S3) | tài liệu tri thức, bản sao lưu | **Đang dùng S3** Docker (bắt buộc khoá); sao lưu 02:00 hằng ngày đẩy lên, kéo về kiểm chứng ĐẠT |
| `config.json` (khoá mã hoá) | cấu hình + chính sách | một đường ghi có lịch sử + audit |
| `storage/vector_db`, `chroma_db` | trí nhớ + RAG | Chroma nhúng; bản ghi trí nhớ có `confidence`, `scope`, `expires_at` |
| RAM | phiên WS, hội thoại ngắn, hàng đợi TTS | theo tiến trình (đúng bản chất phiên realtime) |

Bí mật không vào git (`config.json`, `users.json`, `*.db`, `certs/`, `.infra/` bị `.gitignore`).

---

## 6. "Một chức năng = một bản" (§1, §160)

| Chức năng | Bản chuẩn | Ghi chú |
|---|---|---|
| Voice pipeline | `voice_turn.process_voice_turn` | |
| LLM | `llm_provider.BaseLLMProvider` + model registry | Whisper (STT) dựng client riêng — miễn trừ có ghi |
| TTS | `tts_stream_engine` + `tts_queue_pipeline` | |
| Bộ phát audio client | `web/voice-audio-queue.js` | portal + HUD dùng chung |
| WS thoại | `/ws/v1/voice-stream` | HUD là kênh **phát hiển thị** (một → nhiều màn hình), khác bản chất phiên thoại; audio cả hai đều nhị phân |
| Fast router · Policy · Phân quyền · Task ledger · Audit · Ghi cấu hình · Khởi động | một bản mỗi thứ | RULE-017 / 027 = 0 |
| Tool registry | danh mục `plugin_manager`; `plugin_registry` chỉ là **bộ thực thi** connector (timeout + circuit breaker), mọi lời gọi qua `tool_gate` | lý do giữ: connector gọi mạng cần breaker |
| Bộ đếm trạng thái ngắn hạn | `shared_state` | thay 3 bộ đếm riêng trước đây |

---

## 7. Đối chiếu prompt theo nhóm mục

PASS = có mã + test + chạy thật · PARTIAL = có, thiếu phần · NOT IMPLEMENTED = chưa có.

| Nhóm | Trạng thái | Bằng chứng / còn thiếu |
|---|---|---|
| Một bản chuẩn, legacy, luật phụ thuộc (§1, §11–§17, §124, §160–§163, §176) | **PASS** | §6; 10 luật = 0; gói rỗng đã xoá |
| Realtime: streaming, sentence, TTS theo câu, nhị phân, barge-in, huỷ, bounded queue (§22–§30) | **PASS** | test realtime; HUD phê duyệt nay cũng nhị phân |
| Jitter buffer (§26) | **PASS** | `voice-audio-queue.js` + test Node (cạn bộ đệm, trần 250 ms) |
| Codec (§27) | **PASS (đã đánh giá, có số đo cả hai chiều)** | giữ MP3 chiều xuống; chiều lên đo thật khoảng 82 kbps → giữ PCM (`codec-evaluation.md`) |
| STT partial (§82) | PARTIAL | trình duyệt có kết quả tạm (Web Speech); STT máy chủ (robot) theo đoạn sau VAD |
| Policy / Risk / Autonomy / Approval / Kill switch (§31–§38, §51, §53) | **PASS** | |
| Emergency mode · loop guard (§54, §55, §158) | **PASS** | `test_emergency_and_loop_guards.py`, kịch bản vàng g4b |
| Tool contract (§39, §50) | **PASS** | `@export_skill(data_classification, reversible, output_schema, data_scope, verification_required)` |
| Task ledger · verification · evidence (§41, §47, §49) | **PASS** | |
| Goal hierarchy · Priority engine (§42, §43) | **PASS** | `ledger.create_goal / goal_tree / score_priority`, `/api/v1/ops/goals` |
| Incident (§88) | **PASS** | pha DETECTED…RESOLVED, chủ, tài sản, dòng thời gian; Sentinel tự đóng bằng số đo |
| Identity · RBAC · **ABAC** · phân loại dữ liệu · cô lập phòng ban (§33–§35, §62, §64, §155) | **PASS** | `test_abac_department.py`, kịch bản g13 |
| Đa tác nhân an toàn (§56–§57) | **PASS** | tin nhắn có id + người được phục vụ; CFO xét cấp bảo mật; độ sâu bus ≤ 3; g15 |
| Prompt injection · memory trust / poisoning (§59–§61) | **PASS** | ranh giới tin cậy; g9b; trí nhớ có độ tin cậy + hạn dùng + xác minh admin |
| Audit bất biến (§73) | **PASS** | chuỗi băm + trigger; `/api/v1/security/audit-logs/verify` |
| API mỏng · lỗi chuẩn · request id (§74, §143, §95) | **PASS** | |
| Log có cấu trúc (§95) | **PASS** | `LOG_FORMAT=json`, request_id, che bí mật mọi handler |
| Rate limit (§97) | **PASS** | voice, lượt agent, phiên WS, đăng nhập — dùng chung Redis khi có |
| Timeout · retry · circuit breaker (§99–§101) | **PASS** | |
| Health · graceful shutdown (§91–§92) | **PASS** | |
| OpenTelemetry (§93) | **PASS** | span http / voice / tool / llm; exporter none / console / otlp; collector trong compose |
| Model registry (§79) | **PASS** | |
| Redis (§67) · object storage (§68) | **PASS** | kiểm trên server thật |
| PostgreSQL (§65–§66) | **PASS** | ứng dụng chạy trên PostgreSQL; toàn bộ pytest chạy được trên cả PG (`VNMATEAI_TEST_BACKEND=pg`) lẫn SQLite |
| Mở rộng ngang (§145, §164) | PARTIAL | CSDL (PostgreSQL) + bộ đếm (Redis) đã dùng chung; còn phiên WS theo tiến trình |
| Deployment (§138) | **PASS (phạm vi một máy)** | compose hạ tầng đang chạy thật; ứng dụng chạy trên host Windows (COM, micro) |
| CI/CD (§139) | PARTIAL | workflow: lint lỗi chạy thật + pip-audit + pytest + Node; **chưa xác nhận chạy trên GitHub** (repo riêng tư, máy không có `gh`) |
| Backup / DR (§136–§137) | **PASS** | backup có kiểm chứng + đẩy / kéo S3 |
| Governance (§108–§109) | PARTIAL | `docs/governance/ai-governance.md`; chưa có risk register riêng |
| Golden scenarios (§152) | **PASS** | 15/15 (`docs/evaluation/agent-evaluation.md`, `tests/test_golden_scenarios.py`) |

---

## 8. Đã làm trong đợt hoàn thiện (2026-10-06, sau báo cáo đầu)

7 commit, 82 tệp, +3 613 / −161 dòng (tính từ `f2928ee`). Mỗi mục: test viết trước, chạy trên máy chủ thật.

1. **ABAC + phân loại dữ liệu** — người dùng có phòng ban + cấp bảo mật (admin đặt, audit); skill khai mức dữ liệu; `authorize()` từ chối `abac_clearance`; ERP / chấm công / xếp hạng lọc theo phòng ban; sổ quỹ cần cấp 3. Sửa kèm: `PUT /api/v1/users/{id}` **trả nguyên password_hash**; báo cáo liên phòng ban dùng chi phí cloud **bịa** 15 triệu và câu "ổn định 100%", cấp bảo mật lấy từ request / hằng số 4.
2. **Model registry** (BLOCKED không bao giờ gọi), **vòng đời sự cố**, **mục tiêu + priority engine**, **audit chuỗi băm + trigger**. Sửa kèm: sự cố đã tự khôi phục **không bao giờ được đóng** trong sổ.
3. **Log có cấu trúc** (một cấu hình log; che bí mật cả log ứng dụng), **rate limit**, **độ tin cậy trí nhớ**, **OpenTelemetry**.
4. **Hạ tầng**: Redis (`shared_state`), S3 (`object_storage` + backup push/pull), di trú PostgreSQL có kiểm chứng, compose + `dev_infra.py` (SHA-256 ghim).
5. **15/15 kịch bản vàng**; **đa tác nhân** (CFO trả tài chính cho bất kỳ ai; CEO chèn số dư quỹ cho mọi người; HR mất danh tính người hỏi); **jitter buffer**; audio phê duyệt nhị phân; đánh giá codec.
6. **Lint + quét phụ thuộc**: `NameError` ở `/api/v1/skills/execute` (skill chạy xong rồi trả 500, mất audit); câu trả lời mic máy chủ không tới HUD / portal (`datetime` chưa import); nâng starlette / fastapi / python-multipart / pillow / python-dotenv lên bản đã vá.
7. Test tự cô lập object storage (một test đã ghi tệp vào kho thật — đã dọn).

---

## 9. Trả lời 40 câu §186

| # | Câu hỏi | Trả lời |
|---|---|---|
| 1 | Voice pipeline | `application/voice/voice_turn.process_voice_turn` |
| 2 | LLM abstraction | `infrastructure/llm/llm_provider.BaseLLMProvider` + model registry |
| 3 | TTS pipeline | `tts_stream_engine` + `tts_queue_pipeline` |
| 4 | WS protocol | `/ws/v1/voice-stream` (`docs/realtime/protocol.md`); HUD là kênh phát hiển thị, audio nhị phân |
| 5 | Fast router | `application/commands/fast_command_router` |
| 6 | Tool registry | danh mục `core/plugin_manager`; bộ thực thi connector `plugin_registry` — đều sau `tool_gate` |
| 7 | Skill registry | `core/plugin_manager` (86 skill; tệp có lệnh cấp module bị từ chối) |
| 8 | Policy engine | `policy_engine.authorize` |
| 9 | Authorization path | `tool_gate` → `authorize()` (RBAC + ABAC) |
| 10 | Task lifecycle | `application/tasks/ledger` (+ mục tiêu, ưu tiên, sự cố) |
| 11 | Audit path | `erp_db.write_audit_log` → `audit_logs` (append-only, chuỗi băm, trigger) |
| 12 | System of record | PostgreSQL (từ 2026-10-06); SQLite là đường lùi |
| 13 | Session state | phiên realtime trong RAM tiến trình; bộ đếm dùng chung ở Redis khi cấu hình |
| 14 | Binary storage | object storage (local hoặc S3) |
| 15 | Stateless | router HTTP, policy / risk / ABAC, provider LLM / TTS, object storage client |
| 16 | Stateful | phiên WS, hàng đợi TTS, hàng duyệt HITL (bền qua audit), cache |
| 17 | Scale ngang | bộ đếm + khoá đăng nhập + rate limit dùng chung qua Redis; còn cần PostgreSQL cutover và gắn phiên WS theo máy (sticky) |
| 18 | Đã xoá | `domain/*`, cây `apps/`, `sentence_streamer`, `streaming_tts_pipeline`, đường duyệt riêng của registry, 4 gói rỗng, 3 bộ đếm RAM riêng, nhánh base64 chết của HUD |
| 19 | Còn lại | alias `/ws/audio-stream`; 4 hàm bọc audit; base64 ở REST thoại; client Whisper đồng bộ |
| 20 | Vì sao còn | alias: chưa có bằng chứng hết firmware cũ; hàm bọc: chỉ đổi tham số; REST: client không có WS; Whisper: SDK đồng bộ |
| 21 | Legacy còn chạy được? | chỉ alias WS robot (cùng handler mới) |
| 22 | LLM vượt phân quyền? | không — quyết định từ danh tính máy chủ + cấu hình; g8, g9b |
| 23 | Tool vượt policy? | không — RULE-017 = 0 |
| 24 | Agent chạy vô hạn? | không — vòng / tool / thời gian / lỗi lặp / chế độ khẩn cấp / rate limit |
| 25 | Người dừng được? | có — kill switch toàn cục / tác nhân / tool; huỷ lượt; ngắt lời |
| 26 | Hành động quan trọng có audit? | có — caller, agent_id, quyết định, luật, policy_version, op_task_id; chuỗi băm phát hiện sửa / xoá |
| 27 | Kiểm chứng được? | một phần — NONE / BASIC tự động; STANDARD cho `kill_process`, `write_file`; rủi ro cao khác ESCALATED |
| 28 | LLM lỗi | trả lời an toàn, không chạy tool (g10); model BLOCKED bị loại khỏi dự phòng |
| 29 | TTS lỗi | chữ vẫn hiện, Edge dự phòng |
| 30 | Redis lỗi | tự lùi về bộ đếm RAM, có cảnh báo (`test_redis_outage_degrades_to_memory`) |
| 31 | PostgreSQL lỗi | chưa chạy trên PG; di trú là một transaction — lỗi thì không để lại gì |
| 32 | WS ngắt | huỷ lượt, trả slot phiên, gỡ khỏi danh sách phát |
| 33 | TTFT / TTFA / TTFD / TTL | trace mỗi lượt + span OTel; lượt có agent (24 h, n = 2): ACK tiếng ≈ 1 ms, token LLM đầu p50 2,6 s, câu trả lời có tiếng p50 10,0 s; lệnh nhanh p50 53 ms (n = 20, `performance-before-after.md`) |
| 34 | p50 / p95 / p99 | như trên — p99 chưa đủ mẫu |
| 35 | Task success rate | đo ở `/api/v1/ops/overview`; 24 h trước: 4/4 (mẫu quá nhỏ để kết luận) |
| 36 | Policy deny rate | đo ở cùng nơi; 24 h trước: 0 % |
| 37 | Human override rate | chưa có mẫu đại diện |
| 38 | Cost / task | đếm token thật mỗi tác vụ; router không trả giá tiền |
| 39 | Chưa production-ready | Docker trên máy này (WSL); CI GitHub chưa xác nhận; Opus cho robot (firmware); STT máy chủ có kết quả tạm |
| 40 | Rủi ro cao nhất | ứng dụng + CSDL + bản sao lưu trên cùng một máy |

---

## 10. Việc tiếp theo

1. **Chủ dự án**: gán phòng ban / cấp bảo mật cho tài khoản manager / viewer; gỡ khoá thanh toán GitHub để CI chạy; cho tác vụ sao lưu chạy khi chưa đăng nhập (lệnh quản trị trong owner-todo). (Mật khẩu admin, Docker, Redis, S3, PostgreSQL, sao lưu tự động + bản sao ổ thứ hai, firmware 54: **đã xong 2026-10-06**.)
2. **Thu hẹp tool cho portal / agent** như đường thoại: đo trên LLM thật giảm 70 % token, chất lượng bằng nhau (`docs/evaluation/llm-evaluation.md`) — cần thêm câu đánh giá cho tool ít dùng trước khi đổi.
3. **Quy ước thời gian**: hai kiểu lưu (ISO không múi giờ = UTC; `YYYY-MM-DD HH:MM:SS` = giờ địa phương), mỗi cột một kiểu. Portal hiển thị qua `web/server-time.js`. Chuyển sang `TIMESTAMPTZ` để sau — `audit_logs.timestamp` nằm trong chuỗi băm, không đổi được dữ liệu cũ.
4. STT máy chủ có kết quả tạm; nâng ngưỡng bộ lọc tiếng nói trên robot (88 đoạn ồn nền / 11 phút).
