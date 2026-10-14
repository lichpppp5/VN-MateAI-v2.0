# VN-MateAI — Kế hoạch refactor production

> Cập nhật Phase 0 · 2026-10-01. Thay thế bản trước (bản trước ghi Phase 2–11 đã xong, nhưng phần code của các phase đó nằm trong `src/mateai/` và chưa được nối vào runtime).
> Số liệu hiện trạng: `docs/architecture/current-vs-target.md`. Trùng lặp: `docs/architecture/duplication-matrix.md`.

## 0. Kế hoạch "Enterprise Autonomous AI Supervisor" (2026-10-05) — đọc trước

> Yêu cầu nguồn: `prompupdatequytrinchuan.txt` (do chủ dự án cung cấp ngày 2026-10-05). Phase 0 của yêu cầu này **đã xong, không sửa mã** — các mục §1–§48 bên dưới là lịch sử refactor trước đó (vẫn đúng cho phần đã làm).
> Tài liệu Phase 0: `docs/architecture/{current-vs-target,canonical-components,dependency-rules}.md`, `docs/autonomy/{autonomy,policy,risk,task}-model.md`, `docs/realtime/{voice-architecture,call-graph}.md`, `docs/security/{security-architecture,threat-model}.md`, `docs/migration/legacy-removal-plan.md`, `docs/production/readiness-score.md`.

### 0.1 Kết luận Phase 0

- **Thoại realtime đã có đường chuẩn duy nhất** (§41–§58 phần lớn đạt). Không dựng "Voice Engine V2"; việc còn lại là tối ưu đo được (B1–B5 trong `voice-architecture.md` §5).
- **Khoảng trống lớn nhất là lớp kiểm soát tự trị** (§10–§36): chưa có Policy Engine chuẩn (luật rải 4 nơi, luật cấu hình không được áp dụng), không DENY / L5, 3 đường phân quyền tool, đa tác nhân gọi hàm thẳng, không kill switch, không danh tính tác nhân, không sổ tác vụ / bằng chứng / kiểm chứng.
- Theo §190 ("bảo mật trước, tự trị sau"): **không tăng tự trị** (không thêm hành động tự động mới) cho tới khi P2–P3 xong.

### 0.2 Quyết định cần chủ dự án (chặn các phase tương ứng)

| Mã | Câu hỏi | Đề xuất | Chặn |
|---|---|---|---|
| A1 | Admin ra lệnh qua AI có được chạy tác vụ mức 5 / L5 không? (hôm nay: có, không hỏi) | Không | P2 bước DENY |
| A2 | Email gateway có được tự trả lời ra ngoài? | Chỉ khi bật rõ + danh sách miền | P3 |
| A3 | Uỷ quyền "duyệt rồi nhớ" có hạn dùng? | 30 ngày | P3 |
| A4 | Ai bật/tắt kill switch? | Admin, có audit | P3 |
| A5 | `src/mateai/domain/*` (0 caller): dùng làm kiểu cho control plane / task ledger hay xoá? | Dùng lại `domain/tasks`, `domain/audit`, `domain/identity`; xoá phần còn lại ở P13 | P1 |
| D3 | Gộp 2 mô hình vai trò | (giữ từ trước) | P2 ABAC |
| D5 | Khi nào chuyển PostgreSQL | sau P8 gộp truy vấn | P8 |

### 0.2b Trạng thái (2026-10-05, sau P15)

| Phase | Trạng thái | Commit |
|---|---|---|
| P0 audit | xong | `b949d57` |
| P1 test kiến trúc RULE-017/024/025/026 | xong | `7ba41de` |
| P2 Policy / Risk Engine, danh tính, bảo vệ chỉ thị | xong | `7ba41de` |
| P3 kill switch, ngân sách, email, ranh giới tin cậy | xong | `7d9930d` |
| P4 sổ tác vụ, kiểm chứng, bằng chứng, sự cố | xong | `76202fd` |
| P5 ngân sách thử model + đo lại | xong (số đo trung thực trong `performance-before-after.md`) | `41a5f5e` |
| P6 gộp TTS / cảnh báo | phần lớn; còn L5 (2 danh mục tool), L10 (HUD) | `10adf6f`, `1ce9488` |
| P7 quan sát | xong phần một tiến trình (trace bền, token, bảng Supervisor); chưa OpenTelemetry | `e20b4ae` |
| P8 dữ liệu | SQL ra khỏi application; chưa PostgreSQL / Redis (D5) | `e20b4ae` |
| P9 worker / sự kiện | tắt máy an toàn, idempotency giao việc | `e20b4ae` |
| P10 API / domain | **phần chính xong (2026-10-06)** — test hợp đồng HTTP viết TRƯỚC (`test_enterprise_router_contract.py`), rồi chuyển: connector / data source → `application/enterprise/integrations.py`; sổ quỹ, đa tác nhân, onboarding → `application/enterprise/operations.py`; tải tài liệu → `rag_engine.save_upload`; giới hạn tự trị → `application/administration/autonomy_settings.py`. `enterprise.py` 1 442 → 1 068 dòng. Sửa kèm: mạo danh `created_by` ở sổ quỹ, audit tải tài liệu chưa từng được ghi (NameError bị nuốt), 5 lời gọi đồng bộ chậm trong endpoint async. Tiếp theo (cùng ngày): 8 chỗ ghi cấu hình trong 6 router gộp về `config_governance.save_config` — Telegram (kể cả danh sách chat admin), AD sync, mẫu báo cáo, luật bảo mật nay có lịch sử phiên bản + audit; RULE-027 = 0. **Hoàn tất (2026-10-06, commit thứ ba)**, cũng theo cách test hợp đồng trước: `server.py` 1 073 → 218 dòng, chỉ còn dựng app + middleware xác thực + listener IoT; khởi động / tắt máy → `interfaces/http/lifecycle.py` (20 bước có tên, kết quả từng bước ở `GET /api/v1/health/startup`, admin/manager); route → `interfaces/http/routes.py` (bảng route cố định bằng bản chụp `tests/fixtures/route_table_p10.json`); probe → `routers/probes.py`. Màn Cấu Hình → `application/administration/config_service.py`; duyệt tác vụ → `application/security/approval_decisions.py` + `interfaces/http/approval_flow.py` (REST và HUD dùng chung); nhập Excel ERP → `application/enterprise/erp_import.py`. Lỗi thật sửa kèm: beacon UDP trả IP cứng `192.168.100.128` khi không dò được IP; cổng IoT / beacon viết cứng (nay `settings.IOT_PORT` / `DISCOVERY_PORT`); task nền không giữ tham chiếu; phát thanh KPI gọi từ thread bị nuốt lỗi; `urlopen` đồng bộ trong hàm async ở mỗi lần Lưu cấu hình (RULE-026 nay bắt `urlopen`); `POST /api/v1/routing` ghi người sửa là "api"; HUD duyệt yêu cầu đã hết hạn không nhận được phản hồi; `/ws/hud` "simulate" cho kết nối chưa đăng nhập phát chữ lên mọi HUD; `/ws/portal-ui` cho viewer phát toast / chuyển tab trên màn hình mọi người; nhập ERP nhận `.csv`/`.xls` nhưng không đọc được, lỗi "thiếu sheet" bị nuốt thành 200, pandas chạy trên event loop | `0333b7a`, `50e6a68` + commit hoàn tất |
| P11 siết bảo mật | xong các lỗ đã phát hiện; còn S7 (mật khẩu mặc định — việc của chủ hệ thống), S10 (ABAC) | `e20b4ae` |
| P12 đánh giá | kịch bản vàng + đối kháng; chưa đánh giá model trên LLM thật | `e20b4ae` |
| P13 gỡ mã cũ | lớp domain chết đã xoá; alias WS giữ có đo | `10adf6f` |
| P14 production | sao lưu / khôi phục kiểm chứng, CI, runbook | `5f03aca` |
| P15 audit cuối | xong | (commit tài liệu P15) |

### 0.3 Thứ tự thực hiện (mỗi bước: test khoá hành vi → chuyển → test → chạy thật → báo cáo §214)

| Phase (§187) | Việc cụ thể trên mã hiện có | Điều kiện xong |
|---|---|---|
| **P1** Call graph + bản đồ chuẩn | Xong trong Phase 0 (tài liệu trên). Bổ sung test kiến trúc RULE-017…026 ở chế độ baseline (ghi nhận vi phạm hiện có, cấm thêm) | test kiến trúc mới pass với baseline |
| **P2** Bảo mật / chính sách / thẩm quyền | Chuyển phần quyết định của `tool_gate` + `zero_trust` (rủi ro) + `security_guard` (RBAC) vào **một** module control plane (`authorize()`); thứ tự: kill switch → DENY → RBAC → rủi ro → uỷ quyền; nối `security.forbidden_keywords` / `require_confirmation_actions`; L5; `routers/skills.py` + `plugin_registry` + `agent_orchestrator` đi qua cổng (L1–L3) | RULE-017/018/019/020 pass; test §207 (xoá DB → DENY) pass |
| **P3** Control plane tự trị | `agent_id` cho từng tác nhân (thoại, Telegram, sentinel, email, proactive); kill switch toàn cục / tác nhân / tool / phiên (lưu cấu hình, đọc ở cổng); ngân sách thời gian + số tool + token (đọc `usage` từ provider); hạn cho `approval_grants`; chính sách giao tiếp ngoài cho email gateway | RULE-021/022 pass; bật kill switch → không tool nào chạy |
| **P4** Task / goal / incident | Sổ tác vụ `op_tasks` + bước + bằng chứng (`task-model.md`); sentinel tạo task incident thay vì chỉ cảnh báo; ưu tiên tất định | không task nào `COMPLETED` khi chưa kiểm chứng (test bất biến) |
| **P5** Tối ưu thoại | B4 (CPU chặn loop), B1 (dùng lại lựa chọn tool, giới hạn thời gian thử model), đo lại bằng `scripts/bench_voice.py` | số đo trước/sau trong `docs/realtime/performance-before-after.md` |
| **P6** Gộp LLM / TTS / WS | L5, L8, L9, L10 (`legacy-removal-plan.md`) | RULE-011/012 baseline = 0; HUD dùng sự kiện portal (kiểm trên trình duyệt) |
| **P7** Trí nhớ / bằng chứng / quan sát | một API audit có trường chuẩn (L4); trace thoại lưu bền; đếm token / chi phí; tách bộ nhớ hội thoại / dài hạn có nguồn + độ tin cậy; tệp bảo vệ khỏi `write_file` (S4) | `/api/v1/voice/metrics` còn số sau khởi động lại |
| **P8** DB / Redis / storage | L7 (SQL ra khỏi application); sau đó mới bàn PostgreSQL (D5) | RULE-024 pass |
| **P9** Worker / sự kiện | idempotency cho hành động gửi tin / giao việc; tắt máy đóng pool HTTP, email gateway, proactive, WS máy trạm (`server.py:1023`) | test tắt máy |
| **P10** API / domain | tách nghiệp vụ còn trong router (ví dụ `routers/enterprise.py` 1 436 dòng) | XONG — xem §0.2b |
| **P11** Siết bảo mật | rate limit đăng nhập (S6), nhãn dữ liệu không tin cậy (S5), bỏ `shell=True` (S12), cảnh báo mật khẩu mặc định (S7), kịch bản `threat-model.md` §3 | các test đối kháng pass |
| **P12** Kiểm thử / đánh giá | bộ đánh giá agent (§130–§132): 10 kịch bản vàng | báo cáo `docs/evaluation/*` |
| **P13** Gỡ mã cũ | L11, L12, quét rác §203 | — |
| **P14** Production | backup / khôi phục có kiểm, CI thêm lint + type-check + scan, runbook | `readiness-score.md` cập nhật |
| **P15** Audit cuối | trả lời §213 bằng bằng chứng | — |

## 1. Nguyên tắc

1. **Một tính năng = một implementation.** Không thêm bản mới bên cạnh bản cũ. Trình tự mỗi bước: chứng minh bản thay thế → chuyển caller → xóa bản cũ.
2. **Gộp trước, di chuyển sau.** Bản trùng được gộp vào bản canonical ngay trong `core/`. Chỉ khi một context còn đúng một implementation mới di chuyển nó sang tầng đích. Không lúc nào có hai bản chạy song song ở hai cây thư mục.
3. **Từng bounded context một**, theo thứ tự: Voice → LLM/Agent → Skills/Tools → Security → Data → Connectors → Deployment.
4. **Không xóa** khi chưa đủ: thay thế có sẵn, caller đã chuyển, test pass, đã chạy thử runtime. Phase 0–3 không xóa, không di chuyển hàng loạt, không đổi tên hàng loạt.
5. **Không lùi hiệu năng realtime.** Mỗi bước Voice phải đo lại TTFT/TTFA/fast path bằng cùng một công cụ đo (xây ở Phase 1).
6. **Không số liệu giả.** Số nào chưa đo thì ghi "chưa đo".

## 2. Trạng thái thật của các phase cũ (commit `4f6464a`)

| Phase cũ | Ghi trong bản trước | Thực tế |
|---|---|---|
| 0 Audit | đã xong | số liệu sai (đếm lẫn `node_modules`/`.venv`) — làm lại ở Phase 0 này |
| 1 Dependency graph + test baseline | đã xong | "24 vòng import" không khớp (thực đo: 1 khối 37 module + 1 vòng 2 module); không có runner test chung |
| 2 Skeleton `src/mateai` | đã xong | có thư mục; không được import |
| 3–11 Domain, voice, LLM, fast router, skills, repository, connectors, IoT, client agent | đã xong | mỗi phase viết một bản mới trong `src/mateai/`; runtime vẫn dùng `core/`; unit test kiểm tra bản mới |
| Architecture tests | 100 % pass | chỉ quét `src/mateai/`, 3/10 quy tắc |

## 3. Quyết định cần chủ dự án chốt

| Mã | Câu hỏi | Khuyến nghị | Ảnh hưởng nếu chưa chốt |
|---|---|---|---|
| **D1** | Làm gì với `src/mateai/` (69 file viết lại, chưa nối)? | Giữ `src/mateai/` làm **thư mục đích**, nhưng nội dung đích là **code canonical di chuyển từ `core/`**. Bản viết lại nào trùng với code đang chạy thì xóa khi context đó được di chuyển; entity domain thuần Python giữ lại nếu được dùng thật. Sửa import `src.mateai.*` → `mateai.*` (cài package editable). | Phase 4 trở đi bị chặn |
| D2 | HUD có chuyển sang giao thức `/ws/v1/voice-stream` không? | Có. `/ws/hud` giữ phần telemetry, bỏ phần voice | P2 phải giữ song song |
| D3 | Gộp hai mô hình vai trò (`admin/manager/viewer` và `admin/it_support/operator/viewer`) thế nào? | Bảng ánh xạ do chủ dự án duyệt | Phase Security bị chặn |
| D4 | Máy chủ có được chạy skill điều khiển PC (`skills/pc_control_skills.py`…) trên chính nó không? | Hỏi chủ dự án | E3 trong `legacy-candidates.md` giữ nguyên |
| D5 | Khi nào chuyển PostgreSQL làm system of record? | Sau khi gộp DAO (bảng `tasks`, user) — không chuyển DB khi dữ liệu còn hai chủ | Phase Data chỉ làm phần gộp |

## 4. Lộ trình

Mỗi phase kết thúc bằng báo cáo: PHASE / STATUS / FILES CHANGED / ARCHITECTURAL IMPACT / RISKS / TESTS / PERFORMANCE / NEXT STEP.

### Phase 0 — Audit (xong)
Tài liệu: `current-vs-target.md`, `target-architecture.md`, `dependency-rules.md`, `duplication-matrix.md`, `legacy-candidates.md`, `canonical-components.md`, file này. Không đổi code.

### Phase 1 — Lưới an toàn (xong 2026-10-01, xem báo cáo ở mục 6)
- Khai báo phụ thuộc test (`pytest`, `pytest-asyncio`, `pytest-timeout`). (`gTTS` **không** thêm: xung đột `click` với `huggingface-hub` — nhánh gTTS chuyển thành ứng viên xoá, xem `legacy-candidates.md` A7.)
- Một lệnh chạy toàn bộ test: chuyển các file dạng script sang hàm `test_*` hoặc runner tách tiến trình; cô lập test khỏi `config.json`/DB thật (thư mục tạm + biến môi trường).
- Sửa test phụ thuộc môi trường (ngưỡng thời gian, số dòng audit của máy dev, quyền file trên Windows, gọi mạng thật → đánh dấu `network`).
- Sửa architecture test: quét `core/` + baseline ngoại lệ; thêm RULE-011…016.
- **Công cụ đo latency voice** chạy được lặp lại: fast path, TTFT, TTFA, first audio qua WS, RAM/CPU — ghi baseline thật trước khi đụng Voice.
- Kiểm chứng: tất cả test pass hoặc được đánh dấu rõ lý do skip.

### Phase 2 — Voice: gộp TTS và làm sạch text (Phase B/C trong `core/`)
- Bỏ chặn event loop: `fast_command_router` gọi `psutil.cpu_percent(interval=0.05)` trong handler async → khoá loop 50 ms cho **mọi** phiên realtime mỗi lần hỏi CPU (đo: p50 50,5 ms).
- Edge-TTS trả 403 → mọi câu rơi xuống 9Router (TTFA engine p50 2,3 s). Kiểm tra nâng `edge-tts` (đang ghim 6.1.12).
- A7: xoá nhánh gTTS chết (3 nơi).
- C3: một hàm `sanitise_for_tts` (test so sánh đầu ra ba hàm cũ trước).
- C1, C2: mọi tổng hợp giọng qua `TTSStreamEngine`; `AudioEngine` chỉ còn STT/VAD; bỏ race gTTS trong `server.py`.
- A1, B1, B2: xóa facade chết sau khi sửa test.
- Kiểm chứng: test voice, chạy thử portal + HUD + ESP32 (nếu có thiết bị) + mic máy chủ, đo lại TTFA.

### Phase 3 — Voice: một use case xử lý lượt nói
- Tách phần xử lý lượt nói của P1 thành một hàm/lớp dùng chung (không có WebSocket bên trong).
- Chuyển P2 (HUD, sau D2), P3 (XiaoZhi), P4 (mic), P5 (REST) sang use case đó; mỗi kênh chỉ còn transport + audio sink.
- C4, C7, C8: bỏ `stream_voice_response`, pipeline HUD trong `server.py`, `VoiceSessionStore`, `_history` của `voice_controller`.
- A5: bỏ alias `/ws/voice`. E2 `/ws/audio-stream`: chỉ bỏ khi log truy cập xác nhận không còn thiết bị dùng.
- Kiểm chứng: như Phase 2 + test barge-in trên mọi kênh.

### Phase 4 — Voice: di chuyển sang tầng đích (cần D1)
- Di chuyển canonical Voice sang `src/mateai/{application,infrastructure,interfaces}` theo bảng ánh xạ ở `target-architecture.md` §V; xóa bản viết lại trùng trong `src/mateai/`; trỏ test về code đã di chuyển.

### Phase 5 — LLM / Agent
- Failover phải nhớ model hỏng: hiện `NineRouterLLMProvider` thử lại toàn bộ danh sách mỗi lượt, mỗi model hỏng tốn tới 5 s → đo được TTFT p50 22,6 s (6 model hỏng/chậm trước model chạy được). Cần breaker theo từng model + bỏ giá trị mẫu `YOUR_MODEL_NAME_HERE` khỏi danh sách dự phòng.
- C5, C6: mọi lời gọi LLM qua `LLMProvider` (`complete()` cho đường không stream); bỏ hai vòng fallback trong `llm_engine`; 5 module tự tạo client chuyển sang provider.
- Sau đó di chuyển `llm_provider` → `infrastructure/llm`, phần agent/prompt của `llm_engine` → `application/agent`.

### Phase 6 — Skills / Tools
- D7: một registry (giữ circuit breaker + HITL gate của `plugin_registry`); A2, A3, A4.
- D4 quyết định skill máy trạm phía server; C9 sinh gói tải về từ `client_agent/`.

### Phase 7 — Security (cẩn thận, không xóa vì "trông giống")
- D1 (legacy) HITL: gộp trạng thái chờ duyệt rồi mới chuyển caller. D2 audit: một sink, giữ INSERT-only. D3 rủi ro. D4 vai trò (cần quyết định D3 của mục 3).
- Health `/livez` `/readyz` `/startupz`.

### Phase 8 — Data
- D6: một repository cho `tasks`; D5: một kho user; C11: bỏ `sqlite3.connect` rải rác; E5 `hr_kpi.db`.
- Sau khi dữ liệu chỉ còn một chủ: kế hoạch SQLite → PostgreSQL (inventory schema, script, đếm dòng, checksum, cutover, rollback).

### Phase 9 — Connectors, cấu hình
- C10: một loader cấu hình; gộp khóa trùng; tách secret khỏi `config.json`.
- 17 `httpx` client: timeout/retry/breaker thống nhất.

### Phase 10 — State, worker, triển khai
- Đưa state chia sẻ (session, HITL chờ duyệt, lịch sử ngắn hạn) ra Redis; tiến trình api / realtime / worker tách được; container; CI.

### Phase 11 — Kiểm toán cuối
- Trả lời đầy đủ các câu hỏi "final check" (số implementation cũ, đã xóa, wrapper còn lại và lý do, có bản cũ nào còn gọi được lúc chạy không).

## 5. Rủi ro chính

| Rủi ro | Giảm thiểu |
|---|---|
| Gộp voice làm lệch giọng đọc / ngắt câu ở một kênh | test so sánh đầu ra sanitize + chạy thử từng kênh |
| Thiết bị ESP32 ngoài thực địa dùng đường WS cũ | không bỏ `/ws/audio-stream` khi chưa có log truy cập |
| Gộp HITL làm mất yêu cầu đang chờ duyệt | gộp kho trạng thái trước, chuyển caller sau, có test đường Telegram |
| Test hiện tại ghi vào dữ liệu thật | cô lập test ở Phase 1 trước mọi thay đổi khác |
| Hiệu năng lùi mà không ai biết | công cụ đo Phase 1, đo lại sau mỗi phase Voice |

## 6. Báo cáo Phase 1 (2026-10-01)

**STATUS:** xong. Chạy toàn bộ test bằng một lệnh: `python -m pytest` (cài trước `pip install -r requirements-dev.txt`).

**Kết quả test:** 158 pass / 0 fail (trước Phase 1: 38/47 file Python pass, 5/12 file `.mjs` pass, không có lệnh chạy chung). 1 test `network` bỏ qua mặc định (`pytest -m network` để chạy; đang fail vì model trên 9Router chết — dữ liệu ngoài, không phải lỗi code).

**Lỗi thật tìm ra và đã sửa** (mỗi lỗi có test fail trên `HEAD`, pass sau khi sửa):

| Lỗi | Nơi | Hệ quả trước khi sửa | Test |
|---|---|---|---|
| `NameError: sanitized_query` | `core/realtime_voice_ws.py` | **mọi câu hỏi qua LLM ở portal đều lỗi**; chỉ lệnh nhanh chạy được | `tests/test_realtime_voice_llm_turn.py` |
| HUD mất hàng đợi TTS song song, lời đệm, timeout TTS (commit 4f6464a) | `core/server.py` `_process_hud_voice_command_body` | mỗi câu cộng nguyên thời gian TTS; TTS treo thì treo cả lượt | `tests/test_hud_voice_pipeline_behavior.py` (+ 2 test cũ) |
| Khoá giả `sk-dummy` quay lại UI + ghi vào `config.json` | `web/app.js`, `core/server.py` | ghi khoá giả vào config | `test_phase74_no_fake_ui.mjs` |
| Nút thử kết nối "direct" gửi **khoá 9Router sang URL direct** khi ô khoá trống | `core/server.py` `/api/v1/llm/test` | lộ khoá 9Router cho máy chủ khác | (kiểm tra tay) |
| Pane Phòng Ban / Elastic Grid không được bảo vệ | `web/app.js` `CC_HANDWRITTEN_SUBTABS` | lần đồng bộ nguồn dữ liệu kế tiếp xoá trắng nội dung pane | `test_phase85_may_tram_that_muc.py` |
| `with sqlite3.Connection` không đóng kết nối | `core/database.py`, `db_manager.py`, `domain_sync.py` | file DB bị giữ tới khi GC; Windows lỗi `WinError 32` | `test_phase73`, `test_phase75` |
| Đồng hồ đo latency phân giải 15,6 ms trên Windows | `core/realtime_voice_ws.py` `VoiceRequestTrace` | TTFD/TTFT/TTFA nhảy bậc | `test_phase1_realtime_ws.py` |

**Cô lập test:** `VNMATEAI_DB_PATH` / `VNMATEAI_HR_DB_PATH` (mặc định giữ đường dẫn cũ) — `tests/conftest.py` trỏ chúng vào thư mục tạm; sao lưu và trả lại `config.json`, `users.json`, `skills/registry.json`, `config/data_sources.json` quanh phiên test. Trước đó test đã ghi 54 sự kiện audit giả vào `audit_logs` thật và 3 task giả vào `tasks` thật (3 task đã xoá; 54 dòng audit chưa xoá vì bảng là chỉ-ghi — chờ chủ dự án quyết).

**Test sửa vì phụ thuộc môi trường** (không nới điều kiện kiểm tra): đọc file `.mjs` chuẩn hoá CRLF (6 file fail chỉ vì `core.autocrlf=true`); `test_repositories` dùng DB tạm thay vì đòi ≥ 2459 dòng audit của máy dev; `test_phase62` bỏ qua kiểm tra chmod 600 trên Windows kèm cảnh báo (rủi ro thật, xem `legacy-candidates.md` E7); `test_phase85` đo đúng phạm vi khối Máy Trạm; `test_phase67` chấp nhận `_tts_bytes` (bọc timeout, trả bytes cho binary frame).

**Architecture test:** `tests/architecture/test_core_rules.py` quét `core/`, `skills/`, `workers/`, `main.py` theo RULE-011…015, baseline `core_rules_baseline.json` (bánh cóc: vi phạm mới → fail; giảm → phải hạ baseline). Test cũ chỉ quét `src/mateai` nay được pytest chạy (trước đó không có hàm `test_*`).

**Baseline hiệu năng** (`scripts/bench_voice.py`, Windows 10, `perf_counter`, đo thật):

| Chỉ số | p50 | p95 / max | Ghi chú |
|---|---|---|---|
| Fast path — 4/5 lệnh | 0,006–1,3 ms | ≤ 2 ms | `mấy giờ rồi`, `xin chào`, `ping`, `xem ram` |
| Fast path — `kiểm tra cpu` | 50,5 ms | 51,7 ms | chặn event loop (Phase 2) |
| SentenceBuffer, 58 token → 5 câu | 0,48 ms | 1,37 ms | |
| TTS chunk đầu (engine, không cache) | 2.297 ms | 3.625 ms | Edge-TTS 403 → 9Router |
| WS fast path: sự kiện đầu / chữ đầu | 2,8 ms / 252 ms | 2,8 / 321 ms | 3 lượt |
| WS fast path: audio đầu / hết lượt | 1.612 ms / 1.830 ms | 3.221 ms | |
| WS LLM: chữ đầu (TTFT) | 22.623 ms | 26.552 ms | 6 model hỏng trước model chạy được (Phase 5) |
| WS LLM: audio đầu / hết lượt | 26.402 ms / 26.408 ms | 26.558 ms | |
| Server rảnh | 405 MB WS, 6,4 % CPU | | đo Phase 0 |

Số liệu chỉ có 3 lượt WS — dùng làm mốc so sánh, không phải p95 có ý nghĩa thống kê. Tăng `--ws-rounds` khi cần.

## 7. Báo cáo Phase 2 — Voice: gộp TTS và làm sạch text (2026-10-01)

**STATUS:** xong, trừ một mục bị chặn (xem cuối mục).

**Một implementation cho mỗi việc (đã chuyển caller rồi xoá bản cũ):**

| Việc | Trước | Sau |
|---|---|---|
| Tổng hợp giọng | `TTSStreamEngine` + `AudioEngine.text_to_speech_stream/_bytes/_tts_9router/_tts_gtts` + race gTTS trong `server._tts_bytes` | chỉ `core/audio/tts_stream_engine.py` (`AudioEngine` còn STT/VAD) |
| Làm sạch text cho TTS | 3 hàm: `sanitise_for_tts`, `llm_engine._sanitise_for_tts`, `audio_processor.clean_text_for_tts` | `sentence_streamer.sanitise_for_tts` + `shorten_for_speech` (rút gọn) + `tts_stream_engine.apply_pronunciation` (chỉ áp lên chữ gửi đi tổng hợp) |
| Tách câu từ luồng token | `SentenceBuffer` + `SentenceStreamer` (bọc lại) + `split_into_sentences` | chỉ `SentenceBuffer` (hết vòng import `sentence_buffer ↔ sentence_streamer`) |
| Làm nóng cache TTS | `prewarm_tts_cache` (thread + event loop riêng) + `warmup_acoustic_ack_cache` | chỉ `warmup_acoustic_ack_cache` (loop chính) |
| Hàm bọc TTS của server | `_tts_bytes(audio_engine, …)` + `_safe_tts` (base64) | `_tts_bytes(text)` |

**Đã xoá:** `SentenceBoundaryStreamer`, `edge_tts_stream_audio`, `get_acoustic_ack_for_query`, `race_synthesise`, `SentenceStreamer`, `split_into_sentences`, `clean_text_for_tts`, `prewarm_tts_cache`, `_safe_tts`, 4 phương thức TTS của `AudioEngine`, mọi nhánh gTTS (3 nơi), cache RAM riêng `_TTS_CACHE`.

**Hành vi thay đổi có chủ đích:**
- Thứ tự nguồn TTS: 9Router → Edge (trước: Edge → 9Router → gTTS). Lý do đo được: 9Router p50 1,2 s; Edge trực tiếp p50 3,8 s.
- `edge-tts` 6.1.12 → 7.2.8 (6.1.12 bị dịch vụ trả 403; API dùng trong code tương thích).
- Mọi kênh đọc cùng một cách: hết đọc to URL/bảng/khối code ở HUD; hết "Wi, Fi" / "COVID, 19" ở ESP32 + mic; gợi ý phát âm (A P I, C P U) áp cho mọi kênh nhưng không lên chữ hiển thị.
- Pool HTTP theo từng event loop (`core/connection_pool.py`): luồng tự chạy loop riêng không còn dùng nhầm client của loop chính (log khởi động trước đây: `Event loop is closed`).

**Test:** 176 pass / 0 fail (Phase 1: 158). Test mới: `test_tts_text_sanitiser.py`, `test_tts_engine_sources.py`, `test_connection_pool_event_loops.py` (fail trên code cũ). RULE-012 (TTS ngoài engine) giảm từ 5 vi phạm / 3 file xuống 1 (`skills/ninerouter_skills.py` — tool tạo file giọng nói cho người dùng, REVIEW).

**Hiệu năng (cùng công cụ `scripts/bench_voice.py`):**

| Chỉ số | Phase 1 | Phase 2 |
|---|---|---|
| TTS chunk đầu (engine, câu mới) | p50 2.297 ms | **p50 1.213 ms** (max 2.298) |
| WS fast path: audio đầu | p50 1.612 ms | **p50 1.172 ms** |
| WS fast path: hết lượt | p50 1.830 ms | **p50 1.173 ms** |
| WS LLM: từ chữ đầu tới audio đầu | ~3.780 ms | **~560 ms** |
| WS LLM: chữ đầu (TTFT) | p50 22.623 ms | p50 22.405 ms (không đổi — Phase 5) |
| Khởi động: câu đệm sẵn sàng | 21/23, có lỗi TTS | 28/28, 0 lỗi |

**Bị chặn, chưa làm:** sửa `psutil.cpu_percent(interval=0.05)` chặn event loop trong `core/fast_command_router.py` (dòng ~257 và ~281). Thao tác đọc đoạn code đó bị bộ phân loại an toàn của Claude Code từ chối; chờ chủ dự án cho phép hoặc tự sửa (đề xuất: `await asyncio.to_thread(psutil.cpu_percent, 0.05)`).

**Phát hiện mới (chưa sửa):** `tts_stream_engine.reset_tts_engine()` không được gọi ở đâu → đổi giọng/tốc độ trong cấu hình chỉ có hiệu lực sau khi khởi động lại server.

## 8. Báo cáo Phase 3 — Voice: một use case cho mọi kênh (2026-10-02)

**Quyết định của chủ dự án:** "stream + vòng agent" — câu trò chuyện stream từng câu; câu cần tool chạy vòng agent đầy đủ (`ask_async`: nhiều bước, "Đồng ý" tiếp tục tác vụ chờ duyệt, gửi lệnh tới máy trạm). Giữ nguyên mọi giao thức client (không đổi `/ws/hud`, firmware, REST — D2 chưa áp dụng).

**Lỗ hổng bảo mật đã vá (commit `b792374`):** đường voice của portal chạy MỌI tool không qua Zero-Trust / RBAC / audit (import `zero_trust.evaluate_risk` — không tồn tại — rồi nuốt lỗi). Nay chỉ còn một cổng: `core/agent_voice_loop.run_tool_with_policy`, dùng bởi vòng agent; portal truyền username đã đăng nhập làm danh tính RBAC (`caller`), các kênh khác giữ nguyên hành vi (gồm chính sách bỏ qua xác nhận cho kênh quản trị từ commit f389bbe).

**Một implementation:**

| Việc | Trước | Sau |
|---|---|---|
| Xử lý lượt nói | 4 đường: `realtime_voice_ws._execute_voice_turn` (vòng tool 1 bước riêng), `server._process_hud_voice_command_body`, `xiaozhi_gateway._execute_pipeline`, `voice_controller._stream_response_and_play` | `core/voice_turn.process_voice_turn`; mỗi kênh còn transport + `VoiceSink` (đầu ra) |
| Thực thi tool | `ask_async` (có kiểm soát) + `agent_voice_loop.execute_tool_call` (không kiểm soát) | `run_tool_with_policy` |
| Lịch sử hội thoại | `memory_manager` + `VoiceSessionStore` (HUD) + `voice_controller._session_history` | `memory_manager` (khoá = session_id); `VoiceSession` chỉ giữ trạng thái "đang chờ admin trả lời" |
| Nhận diện câu hỏi | `voice_session.looks_like_question` + bản riêng trong `xiaozhi_gateway` | `looks_like_question` |
| Hàng đợi TTS | HUD tự làm (`_tts_pending`), ESP32/mic tuần tự | `StreamingTTSWorkerPipeline` cho mọi kênh, thêm timeout 14 s mỗi câu |

Đã xoá: vòng tool 1 bước của portal + `execute_tool_call`, `can_synthesize_direct_response`, `prune_tool_schemas`, `prune_tool_payload_for_llm`, `ToolExecutionResult`, `AgentTurnMetrics`; lịch sử trong `VoiceSession`; `tests/test_phase7_agent_loop.py` (chỉ kiểm tra code đã xoá; hành vi chọn tool vẫn được `test_phase8` kiểm tra).

**Lỗi có sẵn đã sửa:**
- `stream_voice_response` lưu lịch sử / chữ hiển thị từ `text_buffer` — chỉ còn phần dư sau câu cuối → câu trả lời kết thúc bằng dấu câu KHÔNG được lưu lịch sử, chữ hiển thị là của lượt trước (gốc rễ của "HUD không nhớ" ở Phase 65).
- Kết quả lượt (chữ hiển thị, suy nghĩ) ghi trên singleton `llm_engine` → nhiều phiên song song ghi đè nhau; nay trả theo từng lượt (`turn`).
- `can_synthesize_direct_response` coi mọi kết quả tool thành công là lỗi.
- Hàng đợi TTS không có timeout mỗi câu → một câu treo giữ mọi câu sau.
- Mảnh chỉ có dấu câu ("!", "--") bị đưa đi tổng hợp giọng → TTS lỗi.

**Kênh được thêm năng lực (hành vi mới có chủ đích):** HUD, ESP32, mic có lệnh nhanh và TTS gối đầu; câu dài được rút gọn khi ĐỌC ở mọi kênh (chữ hiển thị đầy đủ); portal hiển thị chữ gốc (Markdown) và đọc bản đã làm sạch.

**Test:** 185 pass / 0 fail. Mới: `test_voice_turn.py`, `test_tool_policy_gate.py`; viết lại `test_realtime_voice_llm_turn.py` (giả lập ở client OpenAI, đi qua code thật của `stream_voice_response`).

**Chạy thử thật (server đang chạy):** HUD qua `/ws/hud`: lệnh nhanh "mấy giờ rồi" trả lời đúng giờ có tiếng; câu hỏi LLM có lời đệm sau ~1 s, đọc đủ các câu, `voice_state` cuối lượt đúng. Portal qua `/ws/v1/voice-stream`:

| Chỉ số (3 lượt) | Phase 2 | Phase 3 |
|---|---|---|
| Fast path: chữ đầu | p50 53 ms | p50 11 ms |
| Fast path: tiếng đầu | p50 1.172 ms | p50 1.243 ms |
| LLM: chữ đầu | p50 22.405 ms | p50 2.484 ms — **không phải TTFT thật**, xem dưới |

**Phát hiện cho Phase 5 (chưa sửa):**
1. Model `ag/gemini-3.6-flash-low` trả về thông báo của nhà cung cấp *"Gemini 3.5 Flash is no longer available…"* như một câu trả lời bình thường — hệ thống đọc to nó. Cần cập nhật model trong cấu hình và phát hiện kiểu phản hồi này.
2. Sau Phase 3, `core/llm_provider.py` + `LLMEngine.stream/stream_tokens/get_provider` KHÔNG còn caller production (chỉ test). Mọi lời gọi LLM thật dùng client OpenAI thô trong `llm_engine` với vòng fallback riêng. Phase 5: chuyển `stream_voice_response` và `_call_llm*` lên provider (không xoá provider — kiến trúc đích cần nó).
3. Bộ tách câu thứ ba `LLMEngine._extract_sentences` (Phase 0 bỏ sót) cắt sai số thập phân ("3.5" → "3. 5"); gộp vào `SentenceBuffer`.

## 9. Báo cáo Phase 5 — LLM: một provider, nhớ model hỏng (2026-10-02)

(Làm trước Phase 4 theo đề xuất: model hỏng và provider không ai dùng ảnh hưởng trực tiếp tới chất lượng trả lời.)

**Một implementation:**

| Việc | Trước | Sau |
|---|---|---|
| Gọi LLM không stream (vòng agent) | `_call_llm` → `_call_llm_direct` / `_call_llm_router` (vòng thử model riêng) | `_call_llm` → `provider.complete()` |
| Stream cho voice | `stream_voice_response` tự mở stream + vòng thử model riêng (client OpenAI thô) | `provider.stream()` |
| Ủy quyền chuyên gia | `ai_delegation`: vòng thử model async + vòng thứ hai bằng client đồng bộ | `NineRouterLLMProvider.complete(timeout=180)`; bỏ vòng đồng bộ |
| Tách câu cho giọng đọc | `SentenceBuffer` + `LLMEngine._extract_sentences` (regex thô, cắt "3.5" thành "3. 5") | `SentenceBuffer(min_words=8, max_words=30)` — ranh giới an toàn + chính sách nghe tự nhiên chuyển từ `_extract_sentences` |
| Model đã trả lời (route_info) | `self._last_successful_model` (trạng thái trên singleton) | `response.model` |

Đã xoá: `_call_llm_direct`, `_call_llm_router`, `_extract_sentences`, vòng thử model đồng bộ trong `delegate_to_specialist`.

**Mới trong provider (`core/llm_provider.py`):** model lỗi / quá hạn bị xếp cuối danh sách 120 s (dùng chung mọi phiên); model chạy được thì gỡ khỏi danh sách hỏng; giá trị mẫu `YOUR_*_HERE` bị bỏ qua; mọi model đều "đang hỏng" thì vẫn thử lại hết (không khoá cứng). `complete()` nhận `timeout` và `extra_body` (mặc định giữ 8 s, tắt thinking).

**Đo thật (portal, 9Router, 3 lượt liên tiếp ngay sau khởi động):** chữ đầu tiên 20,5 s → 5,5 s → 3,6 s (trước Phase 5: ~22 s mọi lượt). Lượt 1 phát hiện 4 model timeout; lượt 3 đi thẳng tới model chạy được. Dịch vụ 9Router chập chờn (một model chạy ở lượt 1 rồi timeout ở lượt 2), nên lượt đầu sau khởi động / sau 120 s vẫn có thể chậm.

**Test:** 191 pass / 0 fail. Mới: `test_llm_provider_health.py`, `test_ai_delegation_provider.py`. RULE-011 (client LLM ngoài provider) 19 → 12.

**Còn lại (chưa làm, có lý do):**
- `meta_architect` (3) và `analytics_engine` (2) tự tạo client OpenAI **đồng bộ** trong hàm đồng bộ được gọi ngay trên event loop — chuyển sang provider async cần quyết định chạy ở thread nào; làm cùng đợt Skills/Tools.
- `server.py` (3): nút "thử kết nối" kiểm tra endpoint/model/khoá DO NGƯỜI DÙNG NHẬP — cố ý không qua provider đã cấu hình; giữ.
- `llm_engine` (3): nơi tạo client cho chính provider; đúng vai trò.
- Chưa phát hiện được phản hồi kiểu "model X is no longer available" mà nhà cung cấp trả như câu trả lời bình thường — cần cập nhật model trong cấu hình.

## 10. Báo cáo Phase 6 — Skills/Tools: một danh mục tool, một client agent (2026-10-02)

**PHASE:** 6 — Skills/Tools
**STATUS:** XONG (đã kiểm tra runtime)

**Một implementation:**

| Việc | Trước | Sau |
|---|---|---|
| Danh mục tool LLM thấy | `plugin_manager` + 4 danh sách viết tay trong `llm_engine` (6/7 tool trùng) + schema nạp từ `plugin_registry` | chỉ `plugin_manager` (@export_skill / `skills/registry.json`) |
| `plugin_registry` | danh mục thứ hai + `select_relevant_skills` (không caller) | chỉ chính sách thực thi (timeout, circuit breaker, risk_level → HITL) qua `run_tool_with_policy` |
| Tool không tìm thấy | cổng gọi thẳng hàm Python bằng tên (vượt danh mục) | bị từ chối như tool lạ |
| Phê duyệt "Đồng ý" | `asyncio.to_thread(execute_skill)` trên hàm async → tác vụ đã duyệt **không bao giờ chạy** | `run_tool_with_policy(..., confirmed=True)` — có RBAC + audit |
| Client agent | `client_agent/` + bản fork `client_template/` (zip download lấy từ fork) | chỉ `client_agent/`; zip bỏ `__pycache__`, log, `config.json`, cert cũ |

**Đã xoá:** `client_template/` (13 file), `core/connectors/smart_comm_router.py` (không tham chiếu), `PluginRegistry.select_relevant_skills`, đăng ký hỏng trong `core/agents/agent_orchestrator.py`, đăng ký lúc import trong `computer_use_plugin`, nhánh native/registry trong `dynamic_skill_router`.
**Đã đăng ký (thay vì xoá):** `query_organization_data` (A4), `get_enterprise_executive_summary`.

**Test:** 192 pass / 0 fail. Mới: `test_confirm_pending_action.py` (fail trên code cũ). Cập nhật test phase 3/4/6/65/67/85/87/92, `test_tool_policy_gate`, `test_hud_*` theo danh mục mới.
**Runtime:** tải `/api/v1/download-agent` (200, 18 file, có README/requirements, không pycache) → giải nén → chạy `agent.py` → kết nối WSS và xuất hiện trong `/api/v1/clients` (DESKTOP-M1875L9), nạp 25 kỹ năng.
**Hiệu năng:** không đo — thay đổi không nằm trên đường nóng voice ngoài việc bớt dựng danh sách tool mỗi lượt.

**Rủi ro:** tool trước đây chỉ có trong danh sách viết tay mà không có skill tương ứng sẽ biến mất khỏi LLM — đã đối chiếu 7/7 tool (6 trùng, 1 đăng ký mới). Connector Phase 59 phải tự `@export_skill` để LLM thấy.

**Còn lại:** D4 (bản server của kỹ năng máy con), C6 `meta_architect`/`analytics_engine`, E2 alias `/ws/audio-stream`.

## 11. Báo cáo Phase Security — một HITL, một kho audit, đóng các lối vào không xác thực (2026-10-02)

**PHASE:** Security (sau Skills/Tools theo thứ tự Voice → LLM → Skills → Security)
**STATUS:** XONG phần dưới; còn lại ghi ở cuối mục

**Một implementation:**

| Việc | Trước | Sau |
|---|---|---|
| HITL | `zero_trust.HumanInTheLoopManager` + `security/hitl_manager.HITLManager` (Phase 60) | chỉ `zero_trust.hitl_manager`. `HITLManager` **không có nơi nào tạo request** ngoài test → hàng đợi luôn rỗng; đã xoá cùng vòng dọn dẹp, nhánh Telegram, counter `p60_pending` trên dashboard |
| Kho audit | bảng `audit_logs` (bất biến) + file `logs/security_audit.log` (API `DELETE` xoá sạch được) | chỉ `audit_logs`. `security_engine.log_audit` ghi DB (sự kiện gốc giữ trong payload), portal + `StateManager` đọc từ DB. `DELETE /api/v1/security/audit-logs` và nút "Làm Sạch Log" đã gỡ (→ 405). File cũ để nguyên, không xoá, không còn ghi |

**Lỗi bảo mật đã sửa (đều có test fail trên code cũ):**
1. `POST /api/v1/voice-command`: RBAC theo `source_device` do client tự khai — manager gửi `"hud"` là thành admin. Nay theo user đã đăng nhập.
2. `/ws/hud` chưa đăng nhập vẫn gửi `voice_command`, chạy dưới danh tính `"hud"` = admin. Nay bị từ chối (`auth_required`); HUD đã đăng nhập chạy theo username (quyết định của chủ dự án).
3. 13 endpoint nằm trong danh sách public của middleware, gồm `computer-use/dispatch` (điều khiển GUI máy chủ), `computer-use/screenshot`, topology save/reset/trigger, `departments/save`, `cross-report`, `ephemeral-cache/flush`. Nay chỉ còn public: login, assistant-name, health-dashboard, worknodes/heartbeat. Portal (`apiFetch`) và admin (`authFetch` mới trong `admin/lib/api.ts`) gửi JWT.
4. `ask_async` tra pending action theo `source_device` trong khi cổng tool lưu theo `caller` → trên portal, "Đồng ý" không bao giờ tìm thấy tác vụ. Nay dùng cùng khoá.

**Test:** 212 pass / 0 fail. Mới: `test_hud_requires_login.py`, `test_audit_single_store.py`, `test_public_endpoints_locked.py`, 2 test thêm trong `test_confirm_pending_action.py`. Sửa: phase60 (bỏ test của manager đã xoá), phase61 mjs (bỏ counter P60), phase88 (gửi token).
**Runtime (server thật):** endpoint khoá: không token 401 / có token 200; `DELETE audit-logs` 405; HUD không token bị từ chối, có token chạy đủ lượt (listening → speaking); `/admin/topology` 200 và chunk topology gửi Bearer.
**Hiệu năng:** không đo. `log_audit` đổi từ append file sang INSERT SQLite (đồng bộ, như trước).

**Rủi ro / còn lại:**
- `POST /api/v1/worknodes/heartbeat` vẫn public, và phản hồi giao task cho node bất kỳ — ai giả làm worker là nhận được task. Cần danh tính cho worker daemon.
- `security_guard._resolve_role` vẫn cấp admin theo **tiền tố** id (`esp32*`, `xiaozhi*`, `telegram*`, `hud*`, `robot*`; f389bbe). Sau bản sửa này, các đường đã biết đều đi theo user hoặc thiết bị đã xác thực; nhưng kênh mới nào truyền id do client tự đặt thì sẽ lại thành admin.
- Hai mô hình role (portal admin/manager/viewer ↔ RBAC admin/it_support/operator/viewer qua `PORTAL_ROLE_MAP`) chưa gộp: cần đổi role trong DB, là quyết định sản phẩm.
- `/ws/topology` và các WebSocket khác chưa được rà soát trong đợt này.
- Health endpoints `/livez` `/readyz` `/startupz` chưa làm.

## 12. Báo cáo Security (tiếp) — danh tính worker, health probes (2026-10-02)

**STATUS:** XONG

- **Heartbeat worker:** `POST /api/v1/worknodes/heartbeat` không còn public. Chỉ nhận enrollment secret của worker hoặc JWT admin/manager — cùng hàm `_is_valid_worker_token` với `/ws/client` (tách từ `_authenticate_worker`, không có cơ chế thứ hai). JWT người dùng thường KHÔNG giả được worker. `workers/remote_worker_daemon.py` gửi `VNMATE_ENROLLMENT_TOKEN` (giá trị `enrollment_token` trong gói tải agent).
- **Probes:** `/livez` (tiến trình sống), `/startupz` (lifecycle startup chạy xong — `lifecycle.STATE.complete`, đặt sau bước cuối; Supervisor P10 chuyển probe sang `routers/probes.py`), `/readyz` (startup + `SELECT 1` DB có timeout 3 s + đã nạp skill; 503 kèm check hỏng). Nằm ngoài `/api/v1/`, không lộ cấu hình.

**Test:** 216 pass / 0 fail. Mới: `test_health_probes.py` (3), heartbeat trong `test_public_endpoints_locked.py` (không token / sai secret / JWT viewer → 401; secret → 200).
**Runtime:** 3 probe trả 200 trên server thật; chạy `remote_worker_daemon.py` thật: không token → 401 và node không lên grid; có token → node có trong `/api/v1/worknodes/status`.

**Còn lại (Security):** cấp admin theo tiền tố id trong `_resolve_role`; gộp hai mô hình role; rà các WebSocket còn lại (`/ws/topology`, `/ws/portal-ui`, `/ws/voice`, `/ws/audio-stream`).

## 13. Báo cáo Security (tiếp) — rà soát WebSocket (2026-10-02)

**STATUS:** XONG

| Socket | Trước | Sau |
|---|---|---|
| `/ws/portal-ui`, `/ws/client`, `/ws/audio-stream` (+ alias xiaozhi) | đã bắt buộc JWT / enrollment secret / device secret | giữ nguyên |
| `/ws/hud` | xem §11 | chỉ xem telemetry khi chưa đăng nhập |
| `/ws/voice`, `/ws/v1/voice-stream` | ẩn danh chạy dưới tên `web_user` (viewer: vẫn đọc dữ liệu tổ chức qua tool, tốn chi phí LLM) | bắt buộc JWT, đóng 1008 |
| `/ws/topology` | không xác thực: ai cũng xem luồng tool và bơm sự kiện `trigger` giả | bắt buộc JWT; admin `SystemCanvas` gửi `?token=` (`sessionToken()` trong `admin/lib/api.ts`) |

**Test:** 221 pass / 0 fail. Mới: `test_websockets_require_login.py` (5; 3 test từ chối fail trên code cũ).
**Runtime:** 4 socket: không token → HTTP 403, có token → nhận gói chào. Lượt thoại thật qua `/ws/v1/voice-stream` (bench_voice, 1 lượt mỗi loại): fast path chữ đầu 280 ms, audio đầu 3,5 s; lượt LLM chữ đầu 22 s — lượt đầu sau khởi động, khớp hành vi 9Router đã ghi ở §9, không do thay đổi này.

**Còn lại (Security):** cấp admin theo tiền tố id trong `_resolve_role`; gộp hai mô hình role.

## 14. Báo cáo Security (tiếp) — thứ tự xác định role, Telegram fail-closed (2026-10-02)

**STATUS:** XONG

- `security_guard._resolve_role`: danh tính có trong DB (nhân viên ERP / user portal) dùng role trong DB, xét **trước** service principal và tiền tố thiết bị — đúng như chú thích thiết kế gốc ("bước 1 sẽ thắng bước 0"). Trước đây tài khoản viewer tên `hudson` (tiền tố `hud`) hay `console` (service principal) được admin. Lỗi tra cứu DB → `viewer` (fail-closed), kể cả id mang tiền tố thiết bị. Quy tắc admin cho id do server gán (Telegram, ESP32/xiaozhi; f389bbe) giữ nguyên.
- Telegram: `admin_chat_ids` trống trước đây nghĩa là **ai nhắn bot cũng được** (và nhận admin qua tiền tố `telegram`); callback duyệt HITL còn bỏ qua kiểm tra khi thiếu cấu hình. Nay cả tin nhắn lẫn nút duyệt: không có trong danh sách → bỏ qua / từ chối. Chat lạ vẫn được ghi lại để admin chọn chat_id khi cấu hình. Cấu hình hiện tại có 1 chat_id nên hành vi thực tế không đổi.

**Test:** 225 pass / 0 fail. Mới: `test_role_resolution_order.py` (4; 3 fail trên code cũ).
**Runtime:** không kiểm được đường tool qua LLM — 9Router trả lời "Gemini 3.5 Flash is no longer available" (model hết hạn, đã ghi ở §9; cần cập nhật model trong cấu hình). Telegram đang `enabled: false` nên không chạy thật được.

**Sự cố trong lúc làm:** chạy tay `python tests/test_phase60_sot_bao_mat.py` (không qua pytest → không có cô lập DB của conftest) đã ghi 14 dòng `HITL_*` vào `audit_logs` thật (id 55–68). Bảng là bất biến; chưa xoá — chờ chủ dự án quyết định.

**Còn lại (Security):** gộp hai mô hình role (quyết định sản phẩm).

## 15. Báo cáo LLM (tiếp) — nhận ra model đã ngừng (2026-10-02)

**PHASE:** LLM/Agent (bổ sung, ưu tiên vì ảnh hưởng trực tiếp người dùng)
**STATUS:** XONG

**Lỗi:** 9Router báo model đã ngừng bằng một câu trả lời HTTP 200 bình thường ("Gemini 3.5 Flash is no longer available. Please switch to …"). Provider coi là thành công → câu đó được hiển thị/đọc cho người dùng và tool không bao giờ chạy (gặp thật trên server lúc kiểm tra §14).

**Sửa (`core/llm_provider.py`, một chỗ cho mọi kênh):**
- `complete()`: câu trả lời không có tool call mà khớp mẫu thông báo ngừng của nhà cung cấp → coi là model hỏng, thử model kế tiếp.
- `stream()`: đọc trước tối đa 60 ký tự đầu (hoặc tới tool call / hết stream) trên CÙNG iterator, kiểm tra, rồi phát lại nguyên vẹn các chunk đã đọc. SentenceBuffer vốn cần ~8 từ mới phát câu đầu nên không thêm trễ đáng kể (không đo riêng).
- Model đã ngừng xếp cuối 1 giờ (`MODEL_RETIRED_COOLDOWN_S`), lỗi tạm thời giữ 120 s.
- Mẫu chỉ khớp cụm của nhà cung cấp (tiếng Anh: "is no longer available/supported", "has been deprecated", "please switch to") ở 240 ký tự đầu; câu trả lời tiếng Việt bình thường không bị bắt nhầm (có test).

**Test:** 228 pass / 0 fail. Thêm 3 test trong `test_llm_provider_health.py` (fail trên code cũ).
**Runtime:** trước khi sửa: REST trả nguyên câu "Gemini 3.5 Flash is no longer available", không chạy tool. Sau khi sửa: log ghi 3 model `ag/gemini-*` bị bỏ qua vì "đã ngừng", tool `get_system_info` chạy thật và có trong `audit_logs` (người gọi `admin`); lượt đó 88 s do các model khác timeout. Sau khi tăng cooldown: 2 lượt liên tiếp 9,1 s và 6,8 s — nhưng lần chạy này 9Router không trả câu "ngừng" nào, nên không quy được mức cải thiện cho thay đổi.

**Cần làm ở cấu hình (không sửa thay chủ dự án):** danh sách model trong cấu hình còn các model `ag/gemini-3.5-*` / `ag/gemini-3-flash-agent` đã ngừng — cần thay bằng model đang chạy trong trang cấu hình LLM.

## 16. Báo cáo Skills/Tools (tiếp) — skill đồng bộ chạy ngoài event loop (2026-10-02)

**STATUS:** XONG

**Lỗi:** `plugin_manager.execute_skill` gọi thẳng `func(**args)` trên event loop với mọi skill đồng bộ (đa số skill: file, PowerShell, WMI, LLM đồng bộ của `analytics_engine`/`meta_architect` tới 60 s/model, hộp thoại phê duyệt). Trong lúc đó mọi kênh voice/WebSocket/REST của mọi người dùng đứng. Hệ quả kèm theo:
- `orchestrator.send_visual_to_client_sync` / `lean_hr` dùng `run_coroutine_threadsafe(...).result()` vào CHÍNH loop đang bị chặn → tự khoá tới hết timeout; overlay không tới được client.
- WMI (COM) chưa khởi tạo trên thread loop → `check_peripherals` lỗi im lặng và trả danh sách dự phòng.

**Một implementation:** `core.plugin_manager.run_blocking(func, **kw)` — `asyncio.to_thread` + `CoInitialize/CoUninitialize` theo cặp mỗi lần gọi (nếu có pywin32). Dùng bởi: `plugin_manager.execute_skill` (skill đồng bộ), `plugin_registry` (thay `run_in_executor` riêng), endpoint analytics REST, luồng MetaArchitect trong `ask_async`. Skill Excel bỏ `CoUninitialize` lẻ (runner sở hữu vòng đời COM).

**Skill phải sửa để chạy được trong thread:** `computer_use` và `ai_delegation` (bỏ `get_event_loop()` → `asyncio.run` khi không có loop); `robotics_tools` (gửi WebSocket trên loop của server qua `orchestrator._loop`, không tạo loop mới); `visual_skills` (gọi `broadcast_portal_event` — hàm **không tồn tại**, lỗi bị nuốt → portal chưa bao giờ hiện visual; nay `broadcast_portal_ui` trên loop server).

**Test:** 230 pass / 0 fail. Mới: `test_skills_run_off_loop.py` (loop vẫn chạy ≥10 tick trong lúc skill ngủ 0,4 s; skill chạy thread khác; COM init/uninit theo cặp kể cả khi lỗi) — fail trên code cũ. `tests/unit/test_connectors.py`: nới `recovery_timeout` 0,05 → 0,5 s (test chập chờn khi cả bộ chạy; assert giữ nguyên).

**Runtime / hiệu năng (server thật, `/api/v1/skills/execute`, đo `/livez` mỗi 50 ms trong lúc skill chạy):**

| | code cũ | code mới |
|---|---|---|
| `get_system_info` (~528 ms) | `/livez` chờ 528 ms (1 mẫu — loop đứng cả lượt) | max 15–17 ms, median 6–7 ms (9 mẫu) |
| `check_peripherals` | 25 ms, **3** thiết bị (WMI lỗi, dự phòng) | ~2,1 s, **31** thiết bị (WMI thật); `/livez` median 2 ms, max ~260 ms (1 mẫu, lúc khởi tạo WMI) |

**Còn lại:** `meta_architect`/`analytics_engine` vẫn tạo client OpenAI đồng bộ riêng (RULE-011) — không còn chặn loop, nhưng chưa đi qua provider chung (chưa có nhận diện model hỏng/đã ngừng).

## 17. Báo cáo LLM (tiếp) — nốt hai client đồng bộ cuối, RULE-011 (2026-10-02)

**STATUS:** XONG

**Một implementation:** `core.llm_provider.complete_text_blocking()` — cầu nối đồng bộ cho code chạy trong thread worker, bọc `NineRouterLLMProvider` (cùng vòng thử model + trí nhớ model hỏng/đã ngừng/hết quota). `analytics_engine` (sinh SQL) và `meta_architect` (sinh mã skill) bỏ client `OpenAI` đồng bộ riêng. RULE-011: 12 → 7 vi phạm (hạ baseline: analytics 2→0, meta_architect 3→0). Còn lại 7 là cố ý đã ghi ở §9 (provider, nút "thử kết nối" của người dùng) + `audio_processor` (Whisper, không phải chat).

**Lỗi tìm thấy khi chạy thật và đã sửa:**
1. `_extract_sql` nhận câu SQL bị cắt cụt (`COUNT(employees.id`) rồi tự gắn `;` → "near ';' syntax error". Nay câu thiếu ngoặc bị coi là không sinh được → thử model khác / dự phòng, báo rõ lỗi. `max_tokens` 400 → 1000 (model có bước suy luận). Lỗi có từ trước, chỉ lộ ra khi model chính hết quota.
2. Model hết quota ("Resets in 101h", HTTP 503/429 `RESOURCE_EXHAUSTED`) chỉ bị xếp cuối 120 s → cứ vài phút lại mất thêm một lượt gọi vô ích. Nay xếp cuối 1 giờ (như model đã ngừng).
3. Timeout sinh SQL 60 → 20 s/model (câu ≤ 1000 token; có SQL dự phòng) thay vì chờ tới N × 60 s.

**Test:** 233 pass / 0 fail. Mới: cầu nối bỏ qua model đã ngừng; analytics + meta_architect dùng cầu nối; quota → cooldown dài; SQL cụt không được chạy.
**Runtime/hiệu năng (9Router thật, `_ask_llm_for_sql`, 2 lượt liên tiếp sau khởi động):** 38,4 s → 5,0 s. Lượt đầu trả giá học 2 model hết quota (mỗi model ~18 s mới trả 503); từ lượt 2 hai model đó bị bỏ qua. REST `/enterprise/analytics/chart` trả SQL hợp lệ, `sql_source: llm`, có biểu đồ. Trí nhớ model hỏng nằm trong bộ nhớ tiến trình nên mỗi lần khởi động lại lượt đầu vẫn chậm; chưa lưu xuống đĩa.

**Cần ở cấu hình:** `ag/claude-opus-4-6-thinking` và `ag/claude-sonnet-4-6` hết quota tới 2026-10-06 09:22 UTC; danh sách còn giá trị mẫu `YOUR_MODEL_NAME_HERE` (bị bỏ qua đúng). Nên đặt model chạy được (vd. `ag/gemini-3-flash`) lên đầu danh sách chuyên gia.

## 18. Báo cáo Config — một cổng vào config.json (2026-10-02)

**PHASE:** Connectors/Config (RULE-013)
**STATUS:** XONG cho `core/`; connector `base_connector` xem "Còn lại"

**Một implementation:** `core.config_loader` là nơi duy nhất mở `config.json`: `read_raw_config(strict)`, `get_config_section(name)`, `write_raw_config(raw)` (ghi nguyên tử: file tạm + `os.replace`, có khoá), `update_config_section(name, updates)` (đọc-sửa-ghi dưới khoá). 14 chỗ tự mở file (server 8, telegram 2, llm_engine 2, sentinel 1, cognitive_memory 1) chuyển sang các hàm này; `_CONFIG_PATH` của server bỏ. RULE-013: 14 → 0.

**Lỗi đã loại bỏ:**
- Ghi không nguyên tử: mất điện/lỗi giữa chừng để lại config.json cụt → lần khởi động sau `SystemExit`.
- Đọc-sửa-ghi không khoá: hai request "Lưu" cùng lúc ghi đè nhau (test: 12 thread cập nhật 12 mục, không mất mục nào).
- Mọi đường GHI đọc ở chế độ strict: config.json hỏng thì báo lỗi thay vì nuốt lỗi rồi ghi đè bằng bản chỉ có payload (trước đây `/api/v1/config` làm đúng việc đó, mất toàn bộ cấu hình). Đường chỉ ĐỌC vẫn dung sai (trả `{}`), một mục hỏng không làm sập hội thoại.

**Test:** 237 pass / 0 fail. Mới: `test_config_single_access.py` (4: dung sai khi đọc, strict không ghi đè file hỏng, ghi lỗi không làm hỏng file cũ + không để file tạm, 12 thread không mất cập nhật). Baseline RULE-013 hạ về 0.
**Runtime:** qua endpoint thật: `domain/config`, `telegram/config` đọc đúng; `domain/toggle` ghi, đọc lại, hoàn nguyên; phần còn lại của config.json giống hệt (so sánh dict), không file tạm sót.

**Còn lại:** `core/connectors/base_connector.py` (14 lần nhắc config.json) chưa quét — rule hiện chưa đo vì dùng đường dẫn khác; cần rà khi làm nhóm Connectors. `client_agent/` có config.json riêng của máy con (khác file này) — không thuộc phạm vi.

## 19. Báo cáo Security/Connectors — HITL computer-use, HTTP đồng bộ trên loop, phân loại client httpx (2026-10-02)

**STATUS:** XONG

**Lỗ hổng (nghiêm trọng): tác vụ GUI rủi ro cấp 4 chạy không cần duyệt.** `computer_use_plugin` gọi `await hitl_manager.request_approval(...)` — hàm ĐỒNG BỘ trả dict → `TypeError` bị `except` nuốt → rơi xuống `_enqueue_task_to_worker(task)`: thao tác tài chính ("chuyển tiền lương", "phê duyệt thanh toán") vào hàng đợi worker ngay. Kể cả không có lỗi đó, thiết kế cũ cũng đẩy task vào hàng đợi trước khi duyệt và worker không kiểm tra trạng thái duyệt → HITL chỉ là hình thức. Test Phase 90 patch bằng `AsyncMock` nên che lỗi.
**Sửa:** task cấp ≥4 chỉ vào hàng đợi trong `action_callback` chạy khi được duyệt (`approve_async` chạy callback coroutine); không tạo được yêu cầu duyệt → fail-closed, không chạy; câu trả lời giọng nói nói đúng là đang chờ duyệt.
**Test:** `test_phase90_computer_use.py` 14 pass — mới: chưa vào hàng đợi trước duyệt / vào sau callback; HITL lỗi → không chạy; luồng thật qua `hitl_manager.approve_async`. 2 test fail trên code cũ.
**Runtime:** dispatch "Chuyển tiền lương…" → `awaiting_approval`, risk 4, có trong `/enterprise/hitl/pending`; hàng đợi worker 0 trước và sau khi **từ chối** (không bấm duyệt trên server thật để tránh thao tác GUI tài chính thật).

**HTTP đồng bộ trên event loop:** `telegram/test-alert` (`test_connection`, tới 20 s), `telegram/detect-chat` (`get_recent_chats`, 15 s), `receive_webhook` (`verify_aws_sns` tải chứng chỉ, 10 s) → nay qua `run_blocking`. Runtime: detect-chat với token sai trả sau 907 ms, `/livez` vẫn phản hồi (11 mẫu). `send_hitl_request`/`send_incident_alert` đã gửi ở thread nền — không đổi.

**Phân loại 17 client httpx — quyết định KHÔNG gộp phần lớn (có lý do):**
| Nhóm | Số | Quyết định |
|---|---|---|
| `connection_pool` (chính nó) | 2 | canonical |
| Telegram (4), webhook SNS (1) | 5 | client ĐỒNG BỘ chạy trong thread — pool là async, không dùng chung được |
| `server` nút "thử kết nối LLM" | 1 | cố ý tách: URL/khoá do người dùng nhập |
| health probe (`autonomous_sentinel`, `health_monitor`) | 2 | cố ý kết nối mới: đo độ trễ/khả năng kết nối thật, không bị keep-alive che |
| connector (m365 ×3, base, einvoice ×2, paperless) | 6 | mỗi connector TLS/auth/base_url riêng; gộp không kiểm chứng được (không có tài khoản M365/eInvoice để chạy thật) — để nhóm Connectors |
| `workers/remote_worker_daemon` | 1 | tiến trình riêng |

**Test toàn bộ:** 239 pass / 0 fail.

## 20. Báo cáo — rà lỗi async/sync toàn repo, tự khoá loop, Telegram gửi khi đang tắt (2026-10-02)

**STATUS:** XONG

**Rà bằng AST (core/skills/src/workers):**
- `await` hàm đồng bộ: 22 kết quả theo tên — đều dương tính giả (`asyncio.Queue.put/get`, `httpx.AsyncClient.get/request`); 9 tên vừa sync vừa async — đều trỏ đúng bản async. Lỗi computer-use (§19) là trường hợp duy nhất.
- Gọi hàm async không `await`: không có coroutine bị bỏ rơi (mọi lời gọi nằm trong `await`/`create_task`/`gather`/`run_coroutine_threadsafe`/`async for`).
- Hàm `async def` gọi thẳng hàm skill đồng bộ: 13 chỗ trong `server.py`.

**Tự khoá event loop — `/api/v1/visual/broadcast`:** endpoint async gọi thẳng skill `display_visual_data`, skill này chờ coroutine gửi visual trên CHÍNH loop đang bị chặn → mỗi máy trạm online đứng tới 12 s, visual không tới. Test mới: code cũ đứng 19,7 s rồi fail; code mới < 3 s. Runtime: 12 ms.
**12 chỗ còn lại** (fs list/read/write/delete — `delete_item` thư mục có thể mất vài giây; ticket; báo cáo ROI; audit đôn đốc) → `run_blocking`. `run_blocking` import một lần ở đầu `server.py` (bỏ 7 import cục bộ).

**Telegram:**
- Gửi chủ động (`send_hitl_request`, `send_incident_alert`) chỉ kiểm "có token": gateway `enabled: false` vẫn gọi `api.telegram.org`, bằng token giá trị mẫu `YOUR_TELEGRAM_BOT_TOKEN_HERE`. Nay qua `_outbound_config()`: phải bật VÀ token đúng dạng `<số>:<chuỗi>`.
- Bộ lọc che log chỉ khớp token đúng dạng chuẩn → giá trị lệch dạng trong URL `/bot…/` lọt ra `/api/v1/logs/recent` (test Phase 80 quét server thật bắt được: giá trị là placeholder, không phải token thật). Nay che mọi giá trị sau `api.telegram.org/bot`.
- Test mới `test_telegram_outbound_guard.py` (5; 3 fail trên code cũ). Runtime: tạo yêu cầu HITL → 0 request Telegram trong log, không giá trị token trong log; yêu cầu đã bị từ chối sau kiểm tra.

**Test toàn bộ:** 245 pass / 0 fail.

## 21. Báo cáo Data — một kho tài khoản (2026-10-02)

**PHASE:** Data (kho user)
**STATUS:** XONG

**Một implementation:** bảng `users` (SQLite, `core.db_manager`) là kho tài khoản duy nhất. `auth_manager` bỏ hẳn `users.json`: không còn đọc dự phòng, không còn ghi đồng bộ khi tạo/sửa/xoá/đổi mật khẩu (bỏ `_ensure_users_file`, `_load_users`, `_save_users`, `_find_user_entry`, `USERS_FILE`). `users.json` chỉ còn là nguồn **di trú một lần** khi bảng users rỗng; file KHÔNG bị sửa hay xoá (còn hash cũ, cần chủ dự án quyết định — xem dưới).

**Lỗi đã loại bỏ:**
1. Tài khoản đã xoá vẫn đăng nhập được: xoá ở SQLite, bước xoá trong users.json lỗi bị nuốt → `get_user` tìm thấy ở users.json. Và mỗi lần khởi động `_sync_from_users_json` nạp lại → tài khoản sống lại.
2. DB mới + users.json có mật khẩu riêng: tạo `admin/admin123` TRƯỚC rồi mới nhập users.json (bỏ qua vì trùng) → mật khẩu người dùng đặt bị thay bằng mặc định.
3. `VNMATEAI_DEFAULT_<ROLE>_PASSWORD` không có tác dụng: SQLite gán cứng `admin123/manager123/viewer123`. Nay đọc biến môi trường (thiếu thì giá trị dev + cảnh báo).
4. User trong users.json thiếu hash được gán mật khẩu `123456`. Nay bị bỏ qua (có log).

**Kiểm tra dữ liệu trước khi gộp:** 3 tài khoản có mặt ở cả hai kho, cùng role; hash khác nhau nhưng cùng xác thực đúng mật khẩu (chỉ khác salt bcrypt) → không mất dữ liệu.

**Test:** 249 pass / 0 fail. Mới: `test_single_user_store.py` (4 tình huống trên — cả 4 fail trên code cũ).
**Runtime:** đăng nhập admin/manager/viewer 200; tạo user qua `/api/v1/users` → đăng nhập 200 → xoá → đăng nhập 401.

**Cần chủ dự án quyết định:** `users.json` nay không còn được dùng nhưng vẫn chứa hash mật khẩu (cũ dần theo thời gian). Có thể xoá hoặc chuyển ra ngoài thư mục dự án; chưa làm vì là dữ liệu bí mật.

## 22. Báo cáo Data — bảng tasks có một chủ schema (2026-10-02)

**STATUS:** XONG

**Lỗi:** bảng `tasks` dùng chung cho hai luồng (giao việc máy trạm — `db_manager`; công việc ERP — `ERPDatabase`), mỗi module tự `CREATE` bản của mình. Schema thật phụ thuộc module nào khởi tạo trước:
- `ERPDatabase` trước → `title NOT NULL` → `db_manager.add_or_update_task` (không có title) lỗi `sqlite3.IntegrityError: NOT NULL constraint failed: tasks.title` (đã tái hiện bằng test trên code cũ) — giao việc cho máy trạm hỏng trên cài đặt mới.
- `db_manager` trước → `client_id/task_message/sender NOT NULL`; phía ERP điền đủ nên chạy được.

**Một implementation:** `core.database.ensure_tasks_table(cursor)` — nơi duy nhất có DDL + di trú cột + index của `tasks`; `ERPDatabase` và `db_manager` cùng gọi. Schema chuẩn là hợp nới nhất của hai bản cũ (`title` cho phép NULL). `add_or_update_task` điền `title = task_message[:200]` để chạy được cả trên CSDL cũ đã có `title NOT NULL` (SQLite không bỏ được NOT NULL nếu không dựng lại bảng).

**Test:** 251 pass / 0 fail. Mới: `test_tasks_table_single_owner.py` (2 thứ tự khởi tạo × 2 luồng ghi; fail trên code cũ với đúng lỗi NOT NULL). `tests/unit/test_repositories.py` (repository `src/mateai`, không có title) bắt được bản đầu tiên của sửa đổi để `title NOT NULL` — đã chỉnh.
**Runtime (chỉ đọc, không ghi dữ liệu thử vào CSDL thật):** cột `tasks` của CSDL đang dùng không đổi (14 cột); `/roi-dashboard`, `/itsm/tickets`, `/admin/departments/overview` trả 200.

**Kế hoạch PostgreSQL (chưa làm, cần quyết định):** hiện có 4 nơi mở SQLite trực tiếp (RULE-014: `autonomous_sentinel` 2, `db_manager`, `domain_sync`, `health_monitor`) + `ERPDatabase` + `db_manager` + `memory_manager`/HR DB. Bước trước khi chuyển: gom về một lớp truy cập (`get_connection`) để đổi driver ở một chỗ.

## 23. Báo cáo Data — một đường mở SQLite (RULE-014) (2026-10-02)

**STATUS:** XONG

**Một implementation:** `core.database.open_sqlite(path, timeout, foreign_keys, wal, synchronous)` — nơi duy nhất trong `core/` gọi `sqlite3.connect`. Trả `ClosingConnection` (thoát `with` là đóng) + `sqlite3.Row`. Chuyển sang: `ERPDatabase.get_connection` (FK + WAL, 30 s), `db_manager` (WAL, 20 s), `domain_sync` (WAL + synchronous NORMAL, 10 s), probe của `health_monitor` và `autonomous_sentinel` (×2, `wal=False`, vẫn kiểm tồn tại file trước để không tạo CSDL rỗng). Mỗi lớp giữ đúng tuỳ chọn cũ. RULE-014: 5 → 0. Đây là bước chuẩn bị cho PostgreSQL: đổi driver/tuỳ chọn ở một chỗ.

**Lỗi tìm thấy khi gom:** `autonomous_sentinel.check_sql_health` gọi `PRAGMA quick_check` nhưng bỏ qua kết quả. `quick_check` không ném lỗi khi file hỏng — nó trả các dòng mô tả (`"ok"` nếu lành) → CSDL hỏng không bao giờ bị cảnh báo. Nay kết quả khác `["ok"]` → cảnh báo kèm nội dung lỗi. Probe cũ dùng `with sqlite3.connect()` (chỉ commit, không đóng) — nay đóng thật.

**Test:** 255 pass / 0 fail. Mới: `test_sqlite_single_open.py` (4: tuỳ chọn + đóng khi thoát; probe không đổi journal mode; CSDL lành → không cảnh báo; quick_check báo lỗi → có cảnh báo — fail trên code cũ).
**Runtime:** `/readyz` ok (database ok); `/domain/employees`, `/roi-dashboard`, `/users` 200; log không có lỗi SQLite.

## 24. Dọn dữ liệu theo yêu cầu + cổng IoT không TLS (2026-10-02)

**Dọn dữ liệu (chủ dự án yêu cầu):**
- Xoá 14 dòng audit test (id 55–68, khớp action `HITL_*`, người gọi `ceo/AI_Agent/T/test`, thời điểm 03:02:39–42 UTC) khỏi `audit_logs` của `vnmateai.db`. Sao lưu CSDL trước khi xoá (scratchpad). Còn lại 22 dòng — sự kiện thật.
- Xoá `users.json` (không còn được dùng, chứa hash mật khẩu cũ; không bị git theo dõi). Đã đối chiếu trước: 3 tài khoản có đủ trong bảng users với mật khẩu khớp. Sau khi xoá và khởi động lại: đăng nhập 3 tài khoản 200, file không bị tạo lại, 255 test pass. Mã di trú một lần trong `db_manager` được giữ cho máy cài bản cũ còn file này.

**Lỗ hổng: cổng 8000 (không TLS) phục vụ nguyên app.** Listener IoT (cho ESP32 — TLS làm tràn heap chip) chạy cùng `app` với cổng 443: đo trên server thật, `POST http://…:8000/api/v1/login` trả **200** → mật khẩu và JWT đi qua LAN dạng rõ; mọi API/portal truy cập được không qua TLS.
**Sửa:** `core.server.iot_listener_app` (ASGI) bọc `app` cho listener 8000: chỉ cho qua `/api/v1/xiaozhi/ws…`, `/ws/audio-stream…` (đường firmware dùng — `esp32_firmware/src/config.h` `DEFAULT_WS_PATH`) và `/livez|/readyz|/startupz`; HTTP khác 404, WebSocket khác đóng 1008. Cổng 443 không đổi.
**Test:** `test_iot_port_filter.py` (10). **Runtime:** cổng 8000: login 404, `/livez` 200, WS portal bị từ chối 403; WS thiết bị hành xử giống hệt qua cổng 443 (nhận gói `ui` idle) — bộ lọc không ảnh hưởng thiết bị. Cổng 443: login 200. Toàn bộ: 265 pass.

**Ghi chú cấu hình:** `memory_db.microservice_port` mặc định 8000 (server ChromaDB riêng) trùng cổng listener IoT — chỉ ảnh hưởng nếu bật chế độ `microservice`.

## 25. Thiết bị IoT bắt buộc device token (2026-10-02)

**Quyết định của chủ dự án:** bắt buộc token (bỏ "Zero-Config LAN").
**Lỗ hổng:** `_authenticate_device` nhận mọi IP nội bộ (192.168/10/172.16–31/loopback) KHÔNG cần token; `device_id` lấy từ URL do client đặt; id `esp32*`/`xiaozhi*` được admin (RBAC theo tiền tố, f389bbe) → mọi máy trong LAN/Wi-Fi ra lệnh tool với quyền admin. Chú thích firmware (`config.h`) vốn ghi "server chặn kết nối nếu thiếu token".
**Sửa:** chỉ nhận device enrollment secret (query `?token=` hoặc header `Authorization: Bearer`) hoặc JWT admin/manager.
**Test:** `test_device_auth_requires_token.py` (5 — fail trên code cũ). **Runtime (cả cổng 8000 và 443):** không token / token sai → HTTP 403; device token (query hoặc Bearer) → nhận, thiết bị nhận gói `ui`. Toàn bộ: 270 pass.
**Ảnh hưởng vận hành:** robot/ESP32 nạp firmware với `DEFAULT_DEVICE_TOKEN ""` sẽ bị từ chối cho tới khi nạp token (lấy ở `GET /api/v1/security/device-enrollment-token`, quyền admin).

## 26. Token riêng cho từng thiết bị IoT (2026-10-02)

**STATUS:** XONG phía máy chủ; firmware đã sửa mã, chưa build/nạp trên chip thật.

**Rủi ro:** mọi robot dùng CHUNG một device secret; `device_id` lấy từ URL → lộ secret của một robot = giả được mọi robot. Firmware còn kết nối không kèm id (mọi robot là `esp32-default`), và `DEFAULT_DEVICE_ID` bị `#define` cứng (không ghi đè được trong `secrets.h`).
**Sửa:**
- Bảng `device_tokens` (`db_manager`, chỉ lưu SHA-256). `issue/verify/list/revoke_device_token`. So sánh hằng thời gian.
- `_authenticate_device(websocket, device_id)`: token riêng chỉ mở đúng `device_id` của nó; token chung vẫn được nhận (cảnh báo log) trừ khi `security.require_per_device_token` = true.
- API admin: `POST /api/v1/security/devices` (cấp/xoay — token hiện một lần), `GET` (danh sách, không lộ token), `DELETE /{id}` (thu hồi); ghi audit.
- Firmware: `wsPath = DEFAULT_WS_PATH + "/" + DEFAULT_DEVICE_ID`; `DEFAULT_DEVICE_ID` bọc `#ifndef`; `secrets.example.h` hướng dẫn id + token riêng.
**Test:** `test_per_device_tokens.py` (5). Toàn bộ 275 pass.
**Runtime (cổng 8000):** cấp token cho `smoke_robot` → kết nối đúng id: nhận; cùng token cho `esp32_kitchen` hoặc đường không id: 403; thu hồi → 403. Token thử đã thu hồi.
**Việc của chủ dự án:** `docs/production/owner-todo.md`.

## 27. Gỡ phụ thuộc vòng vào core.server (RULE-015) (2026-10-02)

**STATUS:** XONG

**Vấn đề:** 16 lệnh import `core.server` trong 10 module lõi (gateway, worker, orchestrator, registry, skill) — server import các module này, chúng import ngược lại server (vòng). Hệ quả: không tách được module nào khỏi `server.py`, và lỗi thứ tự khởi tạo tiềm ẩn.
**Sửa (di chuyển code, không thêm lớp bọc):**
- `core/realtime_hub.py`: `active_audio_nodes`, `active_hud/portal/topology_websockets`, `broadcast_hud`, `broadcast_hud_binary`, `broadcast_portal_ui`, `broadcast_topology_event` — chuyển nguyên từ `server.py`; server import lại các tên này.
- `core/file_export.py`: `BOM_UTF8`, `_safe_filename`, `_content_disposition`, `_cell_value`, `_rows_to_csv`, `_rows_to_xlsx` (dùng bởi `data_source_tools` và endpoint tải file).
- 10 module chuyển import sang hai module trên. RULE-015: 16 → 0 (baseline hạ). Mọi RULE-013/014/015 nay bằng 0.
**Lỗi bắt được trong lúc chuyển (trước khi commit):** `_content_disposition` dùng `quote` không được import ở module mới — lỗi bị `try/except` nuốt, sẽ âm thầm mất tên file UTF-8; thiếu `json` và `BOM_UTF8`. Tìm bằng quét AST tên chưa định nghĩa.
**Test:** 275 pass. `test_phase63_export` import helper từ module chuẩn mới.
**Runtime:** thiết bị kết nối qua `xiaozhi_gateway` hiện trong `/api/v1/audio-nodes` (server đọc cùng đối tượng); heartbeat worker (`api_admin`) phát `tool_executed` tới viewer `/ws/topology`. Token thử đã thu hồi.

## 28. Phase 4 — gói `mateai` + Voice vào `src/mateai` (2026-10-02)

**STATUS:** XONG cho Voice; các context khác chưa chuyển.

**Đóng gói (D1):** `pyproject.toml` đóng gói `src/mateai` thành `mateai` (cài editable; `-e .` trong `requirements.txt` để `pip install -r requirements.txt` cũng cài). Mọi `src.mateai.*` → `mateai.*`. `*.egg-info/` vào `.gitignore`.

**Voice — code THẬT chuyển từ core/ (git mv, giữ lịch sử):**
| Từ | Đến |
|---|---|
| `core/voice_turn.py` | `mateai/application/voice/voice_turn.py` |
| `core/voice_session.py` | `mateai/application/voice/voice_session.py` |
| `core/audio/sentence_buffer.py` | `mateai/application/voice/sentence_buffer.py` |
| `core/audio/sentence_streamer.py` | `mateai/application/voice/speech_text.py` |
| `core/audio/tts_stream_engine.py` | `mateai/infrastructure/tts/tts_stream_engine.py` |
| `core/audio/tts_queue_pipeline.py` | `mateai/infrastructure/tts/tts_queue_pipeline.py` |
| `core/audio/streaming_tts_pipeline.py` | `mateai/infrastructure/tts/acoustic_ack.py` |
| `core/audio/acoustic_ack_catalog.py` | `mateai/infrastructure/tts/acoustic_ack_catalog.py` |
| `core/audio/binary_transport.py` | `mateai/infrastructure/websocket/binary_transport.py` |

28 file đổi import (không để lại shim ở đường cũ); 2 test đọc mã nguồn theo đường dẫn được sửa đường dẫn. Không module nào tính đường dẫn từ `__file__` nên không đổi hành vi đọc/ghi file.

**Đã xoá (bản viết lại song song, không có caller production, bản thật có test):** `application/voice/use_cases.py`, `application/voice/sentence_buffer.py` (bản cũ), `infrastructure/tts/edge_tts_adapter.py`, `tests/unit/test_voice_pipeline.py`. Giữ tạm `application/voice/barge_in_controller.py` + `domain/voice/entities.py` vì `application/devices/xiaozhi_service.py` (bản song song của Devices) còn dùng — xoá khi chuyển Devices.

**Test kiến trúc** quét thêm `src/mateai/**`: lộ 2 vi phạm có sẵn trong bản song song chưa chuyển (`config/settings.py` RULE-013, `database/sqlite_repository.py` RULE-014) — ghi vào baseline (chỉ được giảm).
**Test:** 271 pass (bớt 4 test của bản song song đã xoá).
**Runtime:** khởi động không lỗi import, `/readyz` ok; bench thật qua `/ws/v1/voice-stream`: fast path chữ đầu 258 ms, audio đầu 3,0 s; TTS engine chunk đầu 1,96 s. Lượt LLM 88,9 s chữ đầu — do danh sách model 9Router (hỏng/hết quota, xem owner-todo.md), không liên quan việc chuyển code.

**Còn lại của Phase 4 (theo thứ tự D1):** LLM/Agent (`llm_provider`, `llm_engine`, `agent_voice_loop`), Skills (`plugin_manager`, `plugin_registry`), Security (`security_guard`, `zero_trust`, `safety_guard`, `auth_manager`), Data (`database`, `db_manager`), Connectors, rồi interfaces (`server.py`).

## 29. Phase 4 — LLM/Agent vào `src/mateai` (2026-10-02)

**STATUS:** XONG

| Từ | Đến |
|---|---|
| `core/llm_provider.py` | `mateai/infrastructure/llm/llm_provider.py` |
| `core/llm_engine.py` | `mateai/application/agent/llm_engine.py` |
| `core/agent_voice_loop.py` (cổng tool `run_tool_with_policy`) | `mateai/application/agent/tool_gate.py` |

27 file đổi import, không shim. **Đã xoá** bản viết lại song song không có caller: `infrastructure/llm/{provider_interface,openai_compatible_adapter,router_adapter,groq_adapter,deepseek_adapter,factory}.py`, `application/agent/llm_orchestrator.py`, `tests/unit/test_llm_providers.py`.

**Bẫy đường dẫn đã xử lý trước khi chuyển:**
- `build_system_prompt` tìm `identity_core.md` bằng `Path(__file__).parent.parent` → đổi sang `settings.PROJECT_ROOT`; thêm `test_system_prompt_identity.py` (đạt cả trước và sau khi chuyển).
- `test_phase66_persona_pronoun` suy thư mục gốc từ vị trí `llm_engine` → sau khi chuyển nó GHI một bản sao `config.json` (kèm khoá) vào `src/mateai/application/`. File đã xoá (bị `.gitignore` chặn, chưa từng vào git); test nay lấy thư mục gốc từ vị trí của chính nó.
- `test_phase68`, `test_phase80` (quét model chết / khoá trong mã) cập nhật đường dẫn và quét thêm `llm_provider.py`.

**Test:** 269 pass. **Runtime:** khởi động không lỗi import, `/readyz` ok; log do `mateai.infrastructure.llm.llm_provider` / `mateai.application.agent.llm_engine` ghi (code ở vị trí mới đang chạy). Lúc kiểm tra 9Router timeout/400 với mọi model → trợ lý trả câu báo quá tải (đúng hành vi, đã ghi vào owner-todo). Voice fast path: chữ đầu 261 ms, audio đầu 1,7 s.

## 30. Phase 4 — Skills (phần nội bộ) vào `src/mateai` (2026-10-02)

**STATUS:** XONG

| Từ | Đến |
|---|---|
| `core/plugin_registry.py` (chính sách thực thi: timeout, breaker, HITL) | `mateai/application/skills/plugin_registry.py` |
| `core/dynamic_skill_router.py` (lọc tool theo domain) | `mateai/application/skills/skill_router.py` |

**Ngoại lệ có chủ đích — `core.plugin_manager` GIỮ NGUYÊN chỗ:** đây là API plugin công khai. 21 file trong `skills/`, mẫu `skills/custom_skills.py`, skill do AI sinh (`skills/auto_*.py` — trên máy người dùng, không nằm trong git) và prompt sinh mã của MetaArchitect đều viết `from core.plugin_manager import export_skill`. Chuyển đi sẽ làm hỏng skill của người dùng ở các bản cài khác mà ta không sửa được. Đổi đường dẫn này cần một đợt riêng có thông báo cho người viết skill.

**Đã xoá (bản song song, chỉ test dùng):** `application/skills/skill_resolver.py`, `application/skills/tool_executor.py`, `domain/skills/registry.py`, `tests/unit/test_skills_taxonomy.py`. Giữ `domain/skills/entities.py` (mô hình domain, `domain/__init__` export).
**Test:** 266 pass. **Runtime:** khởi động không lỗi import, log "Computer-Use Plugin registered (1 tool)" (đăng ký qua registry ở vị trí mới), `/readyz` ok, skill `get_system_info` chạy, `/computer-use/status` success.

## 31. Phase 4 — Security vào `src/mateai` (2026-10-02)

**STATUS:** XONG

| Từ | Đến |
|---|---|
| `core/security_guard.py` (RBAC) | `mateai/application/security/security_guard.py` |
| `core/zero_trust.py` (HITL) | `mateai/application/security/zero_trust.py` |
| `core/safety_guard.py` (audit, che dữ liệu, AST) | `mateai/application/security/safety_guard.py` |
| `core/auth_manager.py` — lớp `AuthManager` (mật khẩu, JWT) | `mateai/application/security/auth_manager.py` |
| `core/auth_manager.py` — `get_current_user`, `require_roles`, `http_bearer` (FastAPI) | `mateai/interfaces/http/auth_dependencies.py` |
| `core/security_tls.py` | `mateai/infrastructure/security/tls.py` |
| `core/state_manager.py` (pending action) | `mateai/application/agent/state_manager.py` |

Xoá package rỗng `core/security/` (còn lại sau khi gộp HITL). 32 file đổi import; 1 dạng `from core import zero_trust` bắt riêng.

**Bẫy đường dẫn xử lý trước khi chuyển:** `JWT_SECRET_FILE` và thư mục `certs/` của TLS tính từ `Path(__file__)` → đổi sang `settings.PROJECT_ROOT` (cũng sửa lỗi tiềm ẩn ở bản đóng gói, nơi `__file__` trỏ thư mục giải nén tạm → sinh khoá/chứng chỉ mới mỗi lần chạy). Test bảo vệ `test_secret_paths_stable.py`.
**Ranh giới tầng:** `test_architecture_boundaries` (RULE-003) bắt `auth_manager` ở tầng application import FastAPI → tách phần dependency HTTP sang `interfaces/http`.
**Test:** 267 pass.
**Runtime:** dấu vân tay `certs/jwt_secret.key` (`e4c652ed…`) và `certs/server.crt` (`81f798f1…`) KHÔNG đổi; JWT cấp trước khi chuyển vẫn hợp lệ (200); audit-logs và HITL pending 200; viewer vào route admin 403; thiết bị không token 403.

## 32. Phase 4 — Data vào `src/mateai` (2026-10-02)

**STATUS:** XONG

| Từ | Đến |
|---|---|
| `core/database.py` (`ERPDatabase`, `open_sqlite`, `ensure_tasks_table`, audit) | `mateai/infrastructure/database/erp_database.py` |
| `core/db_manager.py` (users, tasks LAN, device_tokens) | `mateai/infrastructure/database/db_manager.py` |
| `core/domain_sync.py` (đồng bộ AD, `hr_kpi.db`) | `mateai/infrastructure/directory/domain_sync.py` |
| `core/memory_manager.py` (lịch sử hội thoại) | `mateai/application/conversation/memory_manager.py` |
| `core/cognitive_memory.py` (ChromaDB) | `mateai/infrastructure/memory/cognitive_memory.py` |

**Bẫy nguy hiểm nhất của Phase 4, xử lý TRƯỚC khi chuyển:** cả 4 module tính thư mục gốc bằng `Path(__file__).parent.parent` → sau khi chuyển, máy chủ sẽ mở `src/mateai/vnmateai.db` RỖNG (mất dữ liệu ERP/audit khỏi tầm nhìn, tạo lại tài khoản `admin123`). Đổi sang `settings.PROJECT_ROOT`; test bảo vệ `test_data_paths_stable.py` (đạt trước và sau khi chuyển).
**Đã xoá (bản song song không có caller):** `infrastructure/database/{sqlite_repository,factory}.py`, `domain/repository_ports.py`, `tests/unit/test_repositories.py`. RULE-014 baseline: vi phạm của `sqlite_repository` mất theo.
**Test:** 265 pass.
**Runtime (CSDL thật):** chỉ có `vnmateai.db`, `hr_kpi.db` ở thư mục gốc, không file CSDL mới; số dòng trước/sau: users 3/3, tasks 0/0, device_tokens 0/0, audit_logs 31/32 (+1 = sự kiện thật `PROACTIVE_TASK_AUDIT` lúc khởi động, ghi vào đúng CSDL cũ); JWT cũ hợp lệ, danh sách user đúng; `/readyz`, ROI, domain 200.

## 33. Kích hoạt LLM + Telegram thật (2026-10-02)

**Lỗi tìm thấy:** token Telegram người dùng nhập được lưu thành `••••••••` + token (dán khoá mới vào SAU ký hiệu che của ô bí mật). `_restore_masked_secrets` chỉ nhận đúng chuỗi ký hiệu → coi chuỗi ghép là giá trị thật → token sai dạng → gateway (đúng thiết kế) không gửi gì. **Sửa:** `_strip_mask_chars` bỏ ký tự `•` khỏi mọi trường bí mật gửi lên (kể cả trong danh sách, khối lồng); còn rỗng = giữ giá trị cũ. Test `test_secret_mask_paste.py` (3; fail trên code cũ). Dữ liệu: bỏ ký hiệu khỏi `telegram.bot_token` đang lưu (sao lưu config trước) → token đúng dạng (46 ký tự). Quét: không trường bí mật nào khác dính ký hiệu.
**LLM:** đo thật 23 model trong cấu hình (1 lượt, rồi 2 lượt nữa cho model chạy được) → 10 model chạy 3/3; danh sách mới theo độ trễ (owner-todo.md).
**Runtime:** Telegram polling chạy, tin thử tới nhóm sự cố thành công, token được che trong log; REST + tool 14,6 s với số liệu thật; voice qua LLM chữ đầu 8,5 s / 2,8 s (trước 88,9 s); fast path 157 ms.

## 34. Phase 4 — Devices vào `src/mateai` (2026-10-02)

**STATUS:** XONG

| Từ | Đến |
|---|---|
| `core/xiaozhi_gateway.py` | `mateai/interfaces/websocket/xiaozhi_gateway.py` |
| `core/realtime_voice_ws.py` | `mateai/interfaces/websocket/realtime_voice_ws.py` |
| `core/orchestrator.py` (kết nối client agent) | `mateai/interfaces/websocket/client_orchestrator.py` |
| `core/task_manager.py` | `mateai/application/devices/task_manager.py` |
| `core/voice_controller.py` + `core/voice_widget.py` | `mateai/interfaces/desktop/` (cùng thư mục — widget chạy như tiến trình con) |
| `core/wake_word_engine.py` | `mateai/infrastructure/audio/wake_word_engine.py` |

Đặt theo tầng: module dùng `fastapi.WebSocket` ở `interfaces`, không ở `application` (RULE-003).
**Đường dẫn sửa trước khi chuyển:** `xiaozhi_gateway._PROJECT_ROOT`, `task_manager._PROJECT_ROOT` (logs), `wake_word_engine._PATTERNS_FILE` → `settings.PROJECT_ROOT`; test `test_device_paths_stable.py` (gồm script widget tồn tại).
**Đã xoá (bản song song):** `application/devices/{xiaozhi_service,client_agent_service}.py`, `infrastructure/websocket/{xiaozhi_protocol,client_agent_protocol}.py`, `application/voice/barge_in_controller.py`, `tests/unit/{test_xiaozhi_device,test_client_agent_protocol}.py`.
**Test kiến trúc:** bộ dò SQL của RULE-004 đổi sang so khớp chữ hoa — `.upper()` bắt nhầm docstring "amplitude update from…" của widget.
**Test:** 265 pass. **Runtime:** wake word nạp 46 cụm (đúng file mẫu); client agent tải gói → chạy → có trong `/api/v1/clients`; thiết bị Xiaozhi (token riêng) nhận gói `ui`; voice portal fast path 255 ms, LLM chữ đầu 2,9 s.

**Bổ sung §34:** script đổi import đã sửa nhầm `client_agent/skills/visual_skills.py` (gói chạy trên MÁY TRẠM, nơi không có `mateai`) → đã hoàn nguyên; các script sau loại trừ `client_agent/`. **Lỗi có sẵn phát hiện kèm:** file này import `core.orchestrator` — module của máy chủ, không tồn tại trong `client_agent/core/` → skill `visual_skills` không nạp được trên máy trạm (trước và sau refactor như nhau). Thuộc mục D4 (bản sao skill máy chủ trong gói client) — chưa sửa.

## 35. Phase 4 — Connectors vào `src/mateai` (2026-10-02)

**STATUS:** XONG

Cả package `core/connectors/` (base, aws, oci, paperless, einvoice, m365, generic, custom_registry, tool_bridge) → `mateai/infrastructure/connectors/` (git mv; 18 file đổi import; `client_agent/` không bị đụng).
**Sửa trước khi chuyển:**
- `base_connector._read_config_json_block` tự mở `config.json` (qua `CONFIG_PATH` — RULE-013 không đếm được vì không có chuỗi `"config.json"`) + cache riêng phải xoá sau khi lưu. Nay đọc qua `config_loader.get_config_section` (đọc mới mỗi lần); bỏ `invalidate_config_cache` và chỗ gọi trong `server.py`.
- `custom_registry.STORE_PATH` (`config/data_sources.json` — chứa thông tin đăng nhập nguồn dữ liệu của khách) tính bằng `Path(__file__).parents[2]` → đổi sang `settings.PROJECT_ROOT`.
- Test `test_connector_config_paths.py` (đọc mới qua config_loader; kho nguồn dữ liệu ở thư mục gốc).
**Đã xoá (bản song song):** `infrastructure/connectors/{base_connector,circuit_breaker,erp_connector,telegram_connector,__init__}.py` (bản cũ), `tests/unit/test_connectors.py`.
**Test:** 264 pass. **Runtime:** "Enterprise Connectors loaded", 11/11 connector tool đăng ký vào registry; `/enterprise/connectors/health`, `/catalog`, `/data-sources` 200 (4 connector dựng sẵn).

## 36. Phase 4 — kênh giao tiếp vào `src/mateai` + chặn webhook không chữ ký (2026-10-02)

**STATUS:** XONG

| Từ | Đến |
|---|---|
| `core/telegram_gateway.py` | `mateai/interfaces/telegram/telegram_gateway.py` |
| `core/email_gateway.py` | `mateai/interfaces/email/email_gateway.py` |
| `core/webhook_gateway.py` | `mateai/interfaces/http/webhook_gateway.py` |

(Bằng script chuyển dùng chung — không đụng `client_agent/`.)

**RULE-004 (test ranh giới bắt được, vi phạm thật):** `email_gateway` (tầng giao diện) tự chạy SQL tìm phòng ban/nhân viên cho ticket → chuyển vào `ERPDatabase.find_department_id_by_name` / `first_employee_id_in_department` (cùng câu truy vấn, tham số hoá). Test `test_email_ticket_routing.py`.

**Lỗ hổng: webhook không chữ ký được chuyển vào nhóm Telegram admin.** `/api/webhooks/*` không cần đăng nhập; khi chưa cấu hình secret, webhook vẫn được nhận và chuyển thành tin nhắn Telegram + HUD (gắn nhãn "CHƯA XÁC THỰC"). Phát hiện khi kiểm tra runtime: một POST thử không chữ ký đã tạo tin nhắn thật trong nhóm (xem owner-todo). **Sửa:** mặc định trả 401 khi chưa có secret; chỉ nhận nếu bật `security.allow_unsigned_webhooks`. Test `test_webhook_unsigned_rejected.py` (mặc định 401 + không dispatch; bật cờ → nhận). Runtime: POST không chữ ký → 401, số lần `sendMessage` không đổi.
**Test:** 267 pass.

## 37. Một nguồn cho thư mục gốc dự án (2026-10-02)

**STATUS:** XONG (chuẩn bị cho các lô Phase 4 còn lại)

10 chỗ còn tự tính thư mục gốc từ `__file__` (`audio_cache`, `autonomous_sentinel`, `health_monitor`, `knowledge/graph_rag`, `rag_engine`, `server` ×3 — gốc, `admin/out`, `custom_topology.json` —, `skills/file_system`, `skills/onboarding_workflow`) → `settings.PROJECT_ROOT`. `health_monitor` và `server` bỏ nhánh `frozen` riêng (đã có trong `config_loader`); ở bản đóng gói, `admin/out` và `custom_topology.json` của server từng tính theo thư mục giải nén tạm, lệch với phần còn lại.
**Đã xoá:** `src/mateai/config/settings.py` — bộ nạp cấu hình viết lại, không caller, vi phạm RULE-013 cuối cùng (baseline RULE-013 → 0).
**Test:** `test_project_root_single_source.py` (mọi module dùng `settings.PROJECT_ROOT`; không file nào trong `core/`, `src/mateai/` còn đi lên thư mục gốc bằng `Path(__file__).parent.parent`/`parents[n]`, trừ chính `config_loader`). Toàn bộ 270 pass.
**Runtime:** `/admin/topology` + chunk JS 200, `/static/app.js` 200, API topology 200, audit đọc được, `/readyz` ok.

## 38. Phase 4 — lô dịch vụ ứng dụng (2026-10-02)

**STATUS:** XONG

| Từ | Đến |
|---|---|
| `core/analytics_engine.py` | `mateai/application/analytics/analytics_engine.py` |
| `core/meta_architect.py` | `mateai/application/skills/meta_architect.py` |
| `core/rag_engine.py`, `core/knowledge/{graph_rag,lexical}.py` | `mateai/application/knowledge/` |
| `core/department_engine.py` | `mateai/application/enterprise/department_engine.py` |
| `core/agents/agent_orchestrator.py` | `mateai/application/agent/agent_orchestrator.py` |
| `core/history_pruner.py` | `mateai/application/conversation/history_pruner.py` |
| `core/fast_command_router.py` | `mateai/application/commands/fast_command_router.py` |

Xoá `core/knowledge/__init__.py`, `core/agents/__init__.py` (chỉ re-export; không ai import ở dạng package) và bản song song `application/commands/fast_command_router.py` + `tests/unit/test_fast_command_router.py`.

**RULE-003 (test ranh giới bắt được, vi phạm thật):** `analytics_engine` (tầng ứng dụng) dùng thẳng `sqlite3` để chạy SQL do LLM sinh trong hộp cát (authorizer + `query_only`). Chuyển phần phụ thuộc SQLite vào `ERPDatabase.execute_readonly_select` (giữ nguyên ba lớp phòng vệ; lớp lọc văn bản vẫn ở tầng ứng dụng). Test: SELECT chạy; DELETE/UPDATE/CREATE/ATTACH bị engine chặn.
**Test:** 267 pass. **Runtime:** biểu đồ phân tích 3,6 s (SQL do LLM, có biểu đồ); voice fast path 252 ms — log từ `mateai.application.commands.fast_command_router` khớp `get_current_time`; voice LLM 3,1 s.

## 39. Phase 4 — lô hạ tầng (2026-10-02)

**STATUS:** XONG

| Từ | Đến |
|---|---|
| `core/audio_processor.py` (STT/Whisper) | `mateai/infrastructure/audio/audio_processor.py` |
| `core/audio_cache.py` | `mateai/infrastructure/tts/audio_cache.py` |
| `core/ephemeral_cache.py` | `mateai/infrastructure/cache/ephemeral_cache.py` |
| `core/download_queue.py` | `mateai/infrastructure/files/download_queue.py` |
| `core/connection_pool.py` | `mateai/infrastructure/http/connection_pool.py` |
| `core/worknodes/elastic_grid_manager.py` | `mateai/application/devices/elastic_grid_manager.py` |
| `core/plugins/computer_use_plugin.py`, `core/schemas/computer_use_schema.py` | `mateai/application/skills/` |

Xoá vỏ `core/plugins/__init__.py`, `core/schemas/__init__.py` (chỉ re-export, không ai import dạng package). 32 file đổi import; danh sách miễn trừ RULE-011 cập nhật đường dẫn `audio_processor`.
**Test:** 267 pass. **Runtime:** computer-use đăng ký 1 tool + status success; sweeper của ephemeral cache khởi động, API 200; heartbeat worker 200 và node lên grid; TTS engine chunk đầu 2,0 s; voice fast path 258 ms (audio 1,4 s), LLM 3,0 s.

## 40. Phase 4 — lô vận hành + giao diện (2026-10-02)

**STATUS:** XONG

| Từ | Đến |
|---|---|
| `core/health_monitor.py`, `core/autonomous_sentinel.py`, `core/background_workers.py` | `mateai/application/operations/` |
| `core/api_admin.py`, `core/api_erp.py` | `mateai/interfaces/http/` |
| `core/realtime_hub.py` | `mateai/interfaces/websocket/realtime_hub.py` |
| `core/file_export.py` | `mateai/infrastructure/files/file_export.py` |

**RULE-003 (test ranh giới bắt được):** sentinel và health monitor (tầng ứng dụng) chạy SQL trực tiếp trên `hr_kpi.db` → chuyển vào tầng dữ liệu: `domain_sync.probe_hr_database()` (đọc được không, lần đồng bộ gần nhất, số bản ghi; phân biệt "chưa có bảng" với "lỗi") và `erp_database.check_sqlite_integrity()` (quick_check + thử khoá ghi → ok/corrupt/locked/error). Cùng câu SQL, cùng thông điệp cảnh báo. Test `test_probe_hr_database_states` (chưa có bảng / có dữ liệu / file không phải SQLite).

**Còn trong `core/`:** `config_loader` (nơi định nghĩa PROJECT_ROOT, hơn 100 nơi import), `plugin_manager` (API plugin công khai — giữ nguyên chỗ có chủ đích), `core/skills/*` (cài đặt skill, được shim trong `skills/` import), `server.py` (8.700 dòng route HTTP/WS).
**Test:** 268 pass. **Runtime:** sentinel chạy từ module mới; health cache: `active_directory` OK (chưa đồng bộ), `database_sqlite` OK; `/admin/departments/overview`, `/api/erp/structure`, `/system/topology` 200.

## 41. Phase 4 — skill dựng sẵn (`core/skills`) vào `src/mateai` (2026-10-02)

**STATUS:** XONG

`core/skills/*.py` (8 file: ai_delegation, business_tools, erp_organization, file_system, integration_tools, onboarding_workflow, proactive_manager, robotics_tools) → `mateai/application/skills/builtin/`. Được nạp qua shim trong `skills/` (registry.json chỉ ghi `skills.*`, không ghi `core.skills`) nên danh mục skill không đổi. 17 file đổi import; 1 dạng `from core.skills import integration_tools` sửa tay. RULE-011: vi phạm có lý do của `ai_delegation` chỉ đổi đường dẫn (vẫn 1).
**Test:** 268 pass. **Runtime:** 80/80 skill nạp (= số mục registry); 11/11 connector tool; `fs/list` 200; `/enterprise/proactive/run-audit` 200.

**`core/` còn lại:** `config_loader.py`, `plugin_manager.py` (API công khai — giữ), `server.py`, `__init__.py`.

## 42. Phase 4 — Config vào `src/mateai` (2026-10-02)

**STATUS:** XONG

`core/config_loader.py` → `mateai/config/loader.py` (53 file đổi import).
**Trước khi chuyển:** `config_loader` là nơi tính `PROJECT_ROOT` (`Path(__file__).parent.parent`) — chuyển vào `src/mateai/config/` mà giữ cách tính đó thì gốc thành `src/mateai/` (config.json, CSDL, certs đều lệch). Nay `_resolve_project_root()`: `VNMATEAI_PROJECT_ROOT` → thư mục exe (bản đóng gói) → đi lên tới thư mục có `main.py` + `web/` → thư mục hiện tại. Kiểm chứng trước khi chuyển. Test `test_project_root_resolution.py` (gốc = thư mục repo; biến môi trường ghi đè).
**Test:** 270 pass.
**Runtime:** chỉ một `config.json` (ở gốc), không file mặc định mới; dấu vân tay khoá JWT không đổi, JWT cũ hợp lệ; model LLM đọc đúng; ghi cấu hình qua API vào đúng file và hoàn nguyên; Telegram polling chạy; CSDL đúng file (3 user).

**`core/` còn lại:** `plugin_manager.py` (API plugin công khai — giữ có chủ đích), `server.py`.

## 43. Phase 4 — `server.py` vào `src/mateai` — PHASE 4 HOÀN TẤT (2026-10-02)

**STATUS:** XONG

`core/server.py` (8.700 dòng, 187 route) → `mateai/interfaces/http/server.py`; `main.py` và 34 file import theo; 2 test JS đọc mã nguồn theo `join(..., 'core', 'server.py')` sửa tay. Dọn thư mục `core/security/` còn sót.
**RULE-004 (test ranh giới bắt được):** server (tầng giao diện) tự chạy SQL ở probe `/readyz` và `/api/v1/erp/employees` → `ERPDatabase.ping()`, `ERPDatabase.list_employees()` (cùng câu truy vấn); 1 dương tính giả là chữ "SELECT" trong docstring → đổi chữ. RULE-011: 3 vi phạm có lý do (nút "thử kết nối LLM") chỉ đổi đường dẫn.
**Test:** 271 pass. **Runtime:** hai listener khởi động; cổng 8000 vẫn lọc (login 404, livez 200); `/readyz` ok; danh sách nhân viên, portal, `/admin` 200; voice fast path 267 ms, LLM 8,4 s (lượt đầu sau khởi động).

**Tổng kết Phase 4:** mọi code chạy thật nằm trong `src/mateai` (domain / application / infrastructure / interfaces / config). `core/` chỉ còn `plugin_manager.py` — API plugin công khai (`from core.plugin_manager import export_skill`), giữ chỗ có chủ đích cho skill của người dùng và skill do AI sinh trên các bản cài khác. Các bản viết lại song song không dùng trong `src/mateai` đã xoá theo từng context. Việc tiếp theo hợp lý: tách `server.py` thành các router theo nhóm (HTTP routes vẫn nằm chung một file 8.700 dòng).

## 44. Tách `server.py` thành router + vá 5 lỗ hổng phê duyệt lộ ra khi kiểm tra (2026-10-02)

**STATUS:** ĐANG LÀM (tách router) / XONG (bảo mật bên dưới)

**FILES CHANGED:** `interfaces/http/routers/{users,files,itsm,memory,domain}.py` (mới, chuyển nguyên văn `@app`→`@router`, `include_router` đặt đúng vị trí route đầu tiên nên thứ tự route trùng mẫu không đổi — so bảng route với HEAD: 187/187, OK); `interfaces/http/server.py`; `application/agent/{tool_gate,llm_engine,state_manager}.py`; `application/security/security_guard.py` (`resolve_role` công khai); `application/skills/builtin/file_system.py`; test mới `test_fs_routes_policy`, `test_confirm_action_endpoint`, `test_pending_action_lookup`, `test_approval_reply_classifier`; `test_tool_policy_gate`, `test_confirm_pending_action` theo hợp đồng mới; `test_phase80_secret_masking` tra route cả trong `routers/*.py`.

**Lỗ hổng (đều có từ trước, tái hiện trên máy chủ thật rồi mới sửa):**

| # | Lỗ hổng | Sửa |
|---|---|---|
| 1 | `/api/v1/fs/read` chỉ cần đăng nhập → **viewer đọc được `config.json`** (khoá LLM, token Telegram); `_resolve_path` không giới hạn thư mục | `fs/*` chỉ admin, đi qua cổng tool chung; `read_file` (dùng chung cho HTTP + agent) từ chối tệp bí mật: `config.json`, `.env*`, `*.key/.pem/.crt/.p12/.pfx/.db/.sqlite*/.kdbx`, `data/*.key|*.json` |
| 2 | `fs/write`, `fs/delete` nhận `confirmed: true` trong body → bỏ qua HITL | Bỏ trường; NEED_CONFIRM luôn vào hàng đợi |
| 3 | `run_tool_with_policy` coi `fn_args["confirmed"]` là "đã duyệt" — tham số do LLM sinh, nên prompt injection bỏ qua được HITL | Cổng luôn bỏ `confirmed` khỏi tham số; chỉ tham số `approved=True` (đường resume sau duyệt) có hiệu lực |
| 4 | `POST /api/v1/security/confirm-action` chỉ cần đăng nhập và chạy `skill_name`/`args` **do client gửi** qua `plugin_manager.execute_skill` — viewer chạy được skill bất kỳ, không Zero-Trust/RBAC, không cần tác vụ nào đang chờ | Chỉ admin (như `/enterprise/hitl/approve`); chỉ chạy đúng tác vụ trong hàng đợi (body chỉ để đối chiếu, sai tên → 409); chạy qua cổng tool với `approved=True`, RBAC theo người yêu cầu; `action_id` đã xử lý/không có → 404 (trước đó rơi sang tác vụ khác — tái hiện khi gửi lại cùng id) |
| 5 | `StateManager.get_pending_action` khớp chuỗi con rồi trả "tác vụ mới nhất của bất kỳ ai"; vòng agent cũng tự rơi về tác vụ đầu hàng đợi → ai nói "đồng ý" cũng duyệt tác vụ của người khác | Tra cứu chỉ khớp chính xác; chạm tác vụ của người khác chỉ khi người nói có role RBAC `admin` (`_take_pending_action`) |
| 6 | Phân loại "đồng ý"/"huỷ" dùng `startswith/endswith`: **"hủy" kết thúc bằng "y" → bị hiểu là đồng ý và CHẠY tác vụ** (tái hiện: admin nói "hủy", tệp vẫn được ghi); "không đồng ý" → chạy; "yêu cầu…" → chạy; "không biết…" → huỷ | `classify_approval_reply`: so khớp theo ranh giới từ, ý phủ định thắng, chỉ câu ≤ 35 ký tự, ký tự đơn (`y`/`n`) chỉ khi đứng một mình |

**TESTS:** 337 pass (trước: 271). **RUNTIME (máy chủ thật):** viewer đọc `config.json` → 403; admin đọc → bị từ chối là tệp bí mật; `fs/write` có `confirmed:true` → `need_confirm`; viewer `confirm-action` → 403; duyệt đúng id → chạy + audit `USER_APPROVED`; gửi lại id → 404; sai tên tool → 409; từ chối `delete_item` → xoá khỏi hàng đợi; admin nói "hủy" → huỷ, tệp không được tạo; "đồng ý" → chạy. Tệp thử đã xoá.

**ARCHITECTURAL IMPACT:** mọi đường thực thi tool sau duyệt (hội thoại, portal, `fs/*`) đi qua `run_tool_with_policy`. Hợp đồng "đã duyệt" chuyển từ dữ liệu (`confirmed` trong args) sang tham số tin cậy (`approved`).

**RISKS / CÒN LẠI (ghi trong `docs/production/security.md` §6):** hai hàng đợi chờ duyệt (`StateManager` cho hội thoại/portal và `hitl_manager` cho `/skills/execute`, Plugin Registry, Telegram) — gộp ở phase Security sau; `/clients/{id}/execute` (admin) vẫn tự xác nhận bằng `args.confirmed` và gọi thẳng máy trạm; `GET /security/pending-action` cho mọi người đã đăng nhập xem tham số tác vụ đang chờ; khởi động lại có lúc khôi phục 2 bản cùng một tác vụ từ audit (quan sát 1 lần, chưa tái hiện).

**NEXT STEP:** tách tiếp nhóm router còn lại (enterprise, security, xiaozhi, telegram, skills, clients, voice, system, config, computer-use, …).

## 45. Router `enterprise` + `security`; module dùng chung `enrollment`, `speech` (2026-10-02)

**STATUS:** XONG (tách router vẫn tiếp tục)

**FILES CHANGED:** `interfaces/http/routers/enterprise.py` (34 route + helper riêng: `_CONNECTOR_DISPLAY`, `_CONNECTOR_SECRET_FIELDS`, `_build_connector_config_schema`, `_safe_int`, import `file_export`), `routers/security.py` (11 route + model), `interfaces/http/enrollment.py` (secret ghi danh worker/thiết bị — hai hàm gần giống nhau gộp thành `_load_or_create_secret`), `interfaces/http/speech.py` (`tts_bytes`, trước là `server._tts_bytes`). Nơi gọi dùng `module.ham()` nên test thay một chỗ là có hiệu lực mọi nơi; test trỏ tới module mới (không để lại bí danh trong server). `server.py`: 8.078 → ~6.200 dòng; 187 route không đổi, thứ tự route trùng mẫu giữ nguyên.

**Sửa trong lúc tách:**
- `POST /api/v1/security/blacklist` và `PUT /api/v1/report-templates` đọc/ghi `config.json` thẳng bằng `CONFIG_PATH.read_text/write_text` (qua bí danh `_json`, nên RULE-013 không thấy) → `read_raw_config(strict=True)` + `write_raw_config` (nguyên tử, có khoá). RULE-013 nay bắt cả `CONFIG_PATH.read_text/write_text/open` và `open(CONFIG_PATH)` (đã thử trên tệp mẫu: bắt đủ 3/3). Audit blacklist ghi đúng người thao tác thay vì chuỗi cứng `"admin"`.
- `/ws/hud` lệnh `confirm_action` vẫn cho **manager** duyệt (gọi thẳng hàm endpoint nên `require_roles(["admin"])` không chạy) → chỉ admin.
- `GET /api/v1/security/pending-action` và `GET /api/v1/audit-logs` mở cho mọi người đã đăng nhập (tham số tác vụ / payload audit có thể chứa nội dung tệp sắp ghi) → chỉ admin, cùng quy tắc với `/api/v1/security/audit-logs`. UI đã bỏ qua phản hồi lỗi nên người không phải admin chỉ không thấy banner/cột cảnh báo.

**TESTS:** 345 pass. Test mới: HUD manager không duyệt được / admin duyệt tới được endpoint; danh sách chờ duyệt và hai endpoint audit chỉ admin.

**RUNTIME:** ảnh chụp GET theo 4 vai (anon/viewer/manager/admin) trước–sau: `enterprise` 14/14 route giống hệt; `security` chỉ khác đúng các route cố ý siết. Token ghi danh thiết bị trả về khớp `certs/device_secret.key`; Xiaozhi WS không token → bị từ chối, có token → nhận. Blacklist thêm/xoá: `config.json` vẫn hợp lệ, cùng tập khoá, audit ghi `admin`. `speech.tts_bytes` gọi engine thật: 13.248 byte MP3, 1,5 s.

**RISKS:** hai endpoint audit trùng (ghi ở security.md §6). `WS_APPROVER_ROLES` (admin, manager) thực ra là quyền JWT dự phòng cho kết nối thiết bị/worker, không phải quyền duyệt — tên gây hiểu nhầm, giữ nguyên hành vi.

**NEXT STEP:** tách tiếp xiaozhi, telegram, skills, clients (chuyển `/clients/{id}/execute` qua cổng tool), voice, system, config, computer-use.

## 46. Router `clients` + `skills`; worker cục bộ chạy được lệnh (2026-10-02)

**STATUS:** XONG

**FILES CHANGED:** `interfaces/http/routers/clients.py` (6 route `/api/v1/clients/*` + `/api/v1/visual/broadcast`, 4 model), `routers/skills.py` (6 route, `_REGISTRY_PATH` lấy từ `settings.PROJECT_ROOT`); `application/agent/tool_gate.py` (`client_timeout`); `client_agent/agent.py` (chỉ sửa logic, không đổi import); test: `test_client_execute_gate`, `test_client_agent_dispatch`, `test_tool_policy_gate` (+1), `test_visual_broadcast_no_deadlock` trỏ router mới.

**Thay đổi hành vi / sửa lỗi:**
- `POST /api/v1/clients/{id}/execute` đi qua `run_tool_with_policy` (Zero-Trust, HITL, RBAC theo người gọi, audit); `args.confirmed` không còn tự xác nhận; `timeout` của request truyền tới máy trạm qua `client_timeout`. Tác vụ chờ lưu theo tên người gọi nên modal duyệt của portal (gọi `confirm-action` không kèm `action_id`) vẫn tìm thấy.
- `deploy-skill` và `kill-process` (admin) trước đây không để lại audit → ghi audit (deploy: tên tệp, SHA-256 và độ dài mã, người thao tác; không ghi nguyên mã).
- **Worker cục bộ (`MASTER_LOCAL_WORKER`) không chạy được lệnh nào** (có từ trước): chạy trên máy chủ, agent nạp `core.plugin_manager` của server — `execute_skill` là coroutine — nhưng luôn đẩy vào executor, nhận về coroutine chưa chạy, `.get` lỗi và rớt kết nối ở mọi lệnh. Nay gọi đúng kiểu (async hoặc đồng bộ). Máy trạm thật dùng bản đồng bộ trong `client_agent/core` nên không bị.
- **Agent ghi enrollment token nguyên văn ra log** (URL `?token=…`) và in ra `--help` → che bằng `_redact_url`.

**TESTS:** 353 pass. Test `client_agent` cô lập `sys.modules` (agent.py tự đặt bí danh `core` khi import — lần chạy đầu làm hỏng test khác, đã sửa).

**RUNTIME:** ảnh chụp GET `clients`/`skills` 4 vai trước–sau giống hệt; 80 skill; `skills/execute get_system_info` thành công. Bật worker cục bộ: trước khi sửa `execute` → "máy trạm đã ngắt kết nối trong khi thực thi"; sau khi sửa → `success` 530 ms, worker vẫn online; `delete_item` kèm `confirmed:true` → `need_confirm` (đích đúng máy trạm), từ chối được. Log worker: `token=***`. Đã tắt worker và xoá log thử.

**NEXT STEP:** tách xiaozhi, telegram, voice, system, config, computer-use, pairing, …

## 47. Router `xiaozhi`, `system`, `computer_use`, `config` (+llm, routing), `telegram`; `secret_masking`; tên trợ lý một nguồn (2026-10-03)

**STATUS:** XONG

**FILES CHANGED:** 5 router mới (`config.py` gồm `/api/v1/config*`, `/api/v1/llm/*`, `/api/v1/routing`); `interfaces/http/secret_masking.py` (`_SECRET_MASK`, `_mask_secrets`, `_restore_masked_secrets`, … — dùng chung cho config, telegram, bộ lọc log, nút thử kết nối); `config/loader.py` thêm `get_assistant_name()`; `llm_engine.py`, `acoustic_ack.py` dùng hàm đó. Test trỏ module mới; `test_phase80` quét nguồn toàn tầng HTTP (server + routers + secret_masking). RULE-011 baseline: 3 vi phạm có lý do (nút "thử kết nối LLM") chỉ đổi file server.py → routers/config.py. `server.py` còn ~4.000 dòng; 187 route, thứ tự route trùng mẫu giữ nguyên.

**Sửa:** tên trợ lý có 4 cách đọc khác nhau — server và lời chào đọc `settings.AI_NAME` (luôn None vì `AppSettings` bỏ khoá lạ) nên luôn "Ly Ly" dù giao diện đã đổi `persona.ai_name`; prompt LLM đọc persona. Nay một hàm, cùng thứ tự với giao diện (`persona.ai_name` → `AI_NAME` → `ASSISTANT_NAME` → "Ly Ly"); sự kiện HUD `assistant_name_updated` gửi tên đã đọc lại từ file thay vì đoán từ payload.

**TESTS:** 359 pass (+ `test_assistant_name_single_source`).

**RUNTIME:** ảnh chụp GET 4 vai trước–sau: 13/13 route giống hệt. `GET /api/v1/config` và `/telegram/config` che khoá; gửi lại nguyên cấu hình đã che qua `POST /api/v1/config` → `config.json` không đổi (khoá thật được giữ). Routing, models, system, xiaozhi, computer-use, telegram status 200.

**NEXT STEP:** nhóm còn lại: voice, pairing, wake-word, tts, tasks, sentinel, orchestrator, logs, report-templates, trang tĩnh/admin, health, các WebSocket.

## 48. Router nhóm nhỏ + `workers`; `log_stream`; log không ghi giá trị tham số tool (2026-10-03)

**STATUS:** XONG

**FILES CHANGED:** router `pairing`, `sentinel`, `tasks`, `report_templates`, `tts`, `wake_word`, `analytics` (ROI, nhân viên ERP), `logs`, `workers` (bật/tắt worker cục bộ, `download-agent`, `_resolve_master_endpoint` / `_master_ws_url` / `_local_worker_ws_url`); `interfaces/http/log_stream.py` (handler bộ đệm log + bộ lọc che bí mật; `install(loop)`, `get_handler()` — thay biến toàn cục `server._ws_log_handler` mà router không đọc được nếu không import ngược server). Test trỏ module mới. `server.py` còn ~2.400 dòng; 187 route, thứ tự route trùng mẫu giữ nguyên.

**Sửa:** cổng tool ghi `tham số=<toàn bộ dict>` ra log ở mức INFO — gồm nội dung tệp sắp ghi, câu lệnh… — và `/api/v1/logs/recent` phục vụ log cho cả viewer (cùng loại dữ liệu đã giới hạn admin ở audit, §45). Nay log chỉ ghi TÊN tham số; giá trị đầy đủ vẫn ở `audit_logs` (admin). Không đổi quyền endpoint log (bảng nhật ký của viewer vẫn hoạt động).

**TESTS:** 360 pass (+ test log không chứa giá trị tham số).

**RUNTIME:** ảnh chụp GET 4 vai trước–sau: 10/10 route giống hệt. `POST /api/v1/tts` → MP3 7.632 byte; 322 giọng; `download-agent` → zip 18 tệp có `agent.py`; bật worker cục bộ → chạy `get_system_info` thành công → tắt; log gần đây không còn dòng tham số dạng dict.

**NEXT STEP:** phần lõi còn trong `server.py`: voice-command + phiên thoại, HUD (simulate, telemetry), các WebSocket (`/ws/hud`, `/ws/portal-ui`, `/ws/topology`, `/ws/voice`, `/ws/client`, audio-stream/xiaozhi), trang tĩnh/admin, health/login, middleware và startup.

## 49. Router `pages`, `auth`, `health` (2026-10-03)

**STATUS:** XONG

**FILES CHANGED:** `routers/pages.py` (`/`, `/admin*`, `/topology`, `/computer-use`, `/hud`, `/roi*`; `_WEB_DIR`, `_ADMIN_OUT_DIR`, `_inject_asset_versions` — các `app.mount` tĩnh ở lại server, đọc thư mục từ module này); `routers/auth.py` (login, `/auth/me`); `routers/health.py` (`/health`, `/api/v1/health-dashboard`, `/api/v1/audio-nodes`). Hai test JS đọc nguồn tầng HTTP (server + routers). `server.py` ~2.000 dòng.

**Sửa:** `health-dashboard` gọi `orchestrator.get_connected_clients()` nhưng server không có tên `orchestrator` ở cấp module → NameError bị `except` nuốt, `active_lan_clients` luôn 0. Router import đúng singleton (`test_health_dashboard_counts`).

**TESTS:** 361 pass.

**RUNTIME:** ảnh chụp TOÀN BỘ 81 route GET × 4 vai trước–sau: không khác. Trang `/`, `/hud` còn tiêm phiên bản script theo mtime; trang admin + tài nguyên `/_next` 200; `/static` gửi `no-cache, must-revalidate`; đăng nhập sai 401; `/auth/me` đúng dạng cũ.

**NEXT STEP:** voice-command + phiên thoại, HUD, các WebSocket, middleware/startup.

## 50. Phần lõi: `ws_auth`, `hud_voice`, router `voice`, `hud`, `websockets` — TÁCH `server.py` HOÀN TẤT (2026-10-03)

**STATUS:** XONG

**FILES CHANGED:** `interfaces/http/ws_auth.py` (`authenticate_device/worker/websocket`, `is_valid_worker_token`, `JWT_FALLBACK_ROLES` — tên cũ `WS_APPROVER_ROLES` gây hiểu nhầm là quyền duyệt); `interfaces/http/hud_voice.py` (`process_command[_body]`, `HudVoiceSink`, `cancel_task`, `broadcast_thinking`, `get_metrics_payload`, `telemetry_loop`); router `voice` (voice-command, phiên thoại), `hud` (simulate), `websockets` (8 đường WS: robot/xiaozhi, portal-ui, topology, hud, voice, client). Nơi gọi dùng `module.ham()`; test thay đúng module (trước đó vài test thay `server.broadcast_hud` không có tác dụng vì endpoint đã ở router khác — đã sửa). Test đọc nguồn quét cả tầng HTTP.

**`server.py`: 8.700 → 1.015 dòng**, chỉ còn phần dựng ứng dụng: FastAPI app, CORS, middleware xác thực, mount tĩnh, probe `/livez` `/startupz` `/readyz`, listener cổng 8000 cho thiết bị, startup/shutdown, `include_router` 31 router. 187 route giữ nguyên, thứ tự route trùng mẫu giữ nguyên (so với HEAD mỗi bước).

**TESTS:** 361 pass.

**RUNTIME:** ảnh chụp toàn bộ 81 route GET × 4 vai trước–sau: không khác. WS `/ws/voice`, `/ws/v1/voice-stream`, `/ws/topology`, `/ws/portal-ui`: không token 403, có token nhận. Lệnh thoại REST 5,1 s; lượt nói HUD qua WS: thinking → voice_active, 3 khung audio, câu đầu sau 1,0 s. Robot qua cổng 8000: không token bị từ chối, token thiết bị → nhận. Worker cục bộ chạy `get_system_info` qua `/ws/client`.

**NEXT STEP:** Phase Security tiếp: gộp hai hàng đợi duyệt (StateManager ↔ hitl_manager), gộp hai endpoint audit.

## 51. Security: MỘT hàng đợi duyệt (hitl_manager) — gỡ hàng đợi thứ hai trong StateManager (2026-10-03)

**STATUS:** XONG

**Trước:** hai hàng đợi chờ duyệt. `StateManager` (cổng tool: hội thoại, portal, `fs/*`, máy trạm) và `zero_trust.hitl_manager` (`/skills/execute`, Plugin Registry, computer-use, nút Telegram, panel HITL). Duyệt ở panel HITL/Telegram không thấy tác vụ từ hội thoại và ngược lại; hai cơ chế loại trùng, hết hạn, audit, khôi phục khác nhau.

**Sau:**
- `hitl_manager` là hàng đợi duy nhất. Yêu cầu có `kind` + `context`; `register_executor(kind, fn)` — cổng tool đăng ký `kind="tool"` → `tool_gate.execute_approved_tool` chạy lại đúng tool/tham số/máy đích qua cổng với `approved=True`, RBAC theo NGƯỜI YÊU CẦU. Duyệt ở đâu cũng qua `approve_async` (portal `confirm-action`, HUD, panel `/enterprise/hitl/approve`, nút Telegram, câu "đồng ý").
- `restore_pending_from_audit()` (gọi lúc startup): yêu cầu `kind` còn hạn, chưa duyệt/huỷ được dựng lại từ bản ghi `HITL_APPROVAL_REQUEST_*` (payload nay có action, kind, context, requested_by, created_ts). Yêu cầu dùng closure không khôi phục được — ghi rõ ở security.md.
- Loại trùng có tính máy đích (cùng tool + tham số, khác máy = hai việc).
- `StateManager` chỉ còn ký ức tác vụ đã duyệt + chạy xong; nạp lại từ `HITL_APPROVED_*`. **Sửa rò chéo người dùng:** `get_recent_completed_action` trước đây không khớp người hỏi thì trả tác vụ (kèm kết quả) gần nhất của BẤT KỲ ai — nay chỉ của đúng người.
- Bỏ các dòng audit trùng (`PENDING_CONFIRMATION`/`USER_APPROVED`/`USER_REJECTED` ghi thêm bên cạnh `HITL_*`).
- Bộ test: `VNMATEAI_TELEGRAM_OUTBOUND=off` cho cả phiên (conftest) — config thật có token bot thật; gateway tôn trọng biến này. Trước đây chỉ vài test tự patch.

**Thay đổi hành vi:** tác vụ cần duyệt từ hội thoại/portal/`fs/*` nay cũng gửi thẻ duyệt có nút bấm tới Telegram người duyệt (như các tác vụ HITL khác). Id yêu cầu đổi dạng `act_…` → `HITL-…` (UI chỉ chuyển tiếp id).

**TESTS:** 370 pass (`test_pending_action_lookup` viết lại cho hàng đợi mới; test executor, loại trùng theo máy đích, khôi phục, huỷ trong hội thoại, ký ức theo người dùng, công tắc Telegram).

**RUNTIME** (server chạy với Telegram tắt để không bắn tin thử vào nhóm thật, sau đó chạy lại bình thường): `fs/write` → `need_confirm` id `HITL-…`; cùng id ở `/security/pending-action` và `/enterprise/hitl/pending`; duyệt qua panel doanh nghiệp → tệp được ghi; duyệt qua portal → tệp được ghi; gửi lại id → 404; từ chối → không ghi; tạo yêu cầu, khởi động lại máy chủ → yêu cầu còn trong danh sách, duyệt → tệp được ghi. Tệp thử đã xoá, hàng đợi rỗng.

**NEXT STEP:** gộp hai endpoint audit; rà các phase còn lại (Data, Connectors, Deployment).

## 52. Một endpoint đọc audit (2026-10-03)

**STATUS:** XONG

`GET /api/v1/audit-logs` (dòng thô) và `GET /api/v1/security/audit-logs` (dạng sự kiện) đọc cùng bảng `audit_logs` với hai định dạng; giao diện gọi cả hai. Giữ `/api/v1/security/audit-logs`, thêm `outcome` (trạng thái chuẩn hoá của bảng) và `total`; chuyển panel Giám sát (Panel 8) và cột cảnh báo của Trung tâm chỉ huy sang endpoint này (đọc `outcome`, `action`, `client_id`); gỡ `/api/v1/audit-logs` (không còn nơi gọi: web, admin, client_agent, test).

**TESTS:** 370 pass; test JS phase61 112/112 với dữ liệu dạng mới. **RUNTIME:** `/api/v1/audit-logs` → 404; `/api/v1/security/audit-logs?limit=100` → 100 dòng, outcome {success 91, pending 4, blocked 2, failed 3}; `app.js` phục vụ đường mới.

## 53. Phase 11 — kiểm toán cuối; CI; kế hoạch PostgreSQL (2026-10-03)

**STATUS:** XONG (phần làm được trên máy này); phần cần hạ tầng/quyết định ghi ở `docs/production/owner-todo.md`.

- `docs/migration/final-audit.md`: số liệu thật (58 commit, 71 tệp xoá, −3.100 dòng ròng, `server.py` 8.898 → 1.021), bảng "một chức năng — một implementation" kèm test canh giữ, danh sách lớp bọc/bản sao còn lại và lý do, những gì chưa làm.
- `docs/migration/sqlite-to-postgresql-plan.md`: kiểm kê đo thật (2 CSDL, 16 bảng, 269 dòng), bước chuyển, kiểm số dòng + checksum, cutover, rollback.
- Xoá `apps/{api,realtime,worker}` (gói rỗng, không ai import). Đường dẫn `hr_kpi.db` một chủ (`domain_sync`). Test kiến trúc mới: mọi lời gọi HTTP ra ngoài phải có timeout (25/25 hiện có).
- CI `.github/workflows/tests.yml` (windows-latest: pip, build admin, pytest + node). Hai test làm cho tự cô lập (phase87 đặt model, phase92 tự làm nóng cache). Bản checkout sạch với `config.example.json`: **371 pass**. Chưa xác nhận chạy trên GitHub.

## 54. Kỹ năng tự tạo: trợ lý tự tìm và dùng, chủ động tạo khi chưa có (2026-10-03)

**STATUS:** XONG

**Triệu chứng (chủ dự án báo):** kỹ năng đã được AI tạo nhưng trợ lý không tự dùng, phải nhắc tên mới biết.

**Nguyên nhân (tái hiện trên danh mục thật 82 skill, đường thoại HUD/Telegram thoại/robot/portal thoại):**
1. `classify_intent` là danh sách từ khoá viết tay → "mở bài hát…", "thống kê lô xổ số…" bị coi là trò chuyện → model **không được đưa tool nào**.
2. Bộ định tuyến chỉ chấm tool trong các miền đoán theo từ khoá, khớp **chuỗi con** ("hat" khớp "chat") → skill tự tạo (nhóm chung) bị loại; "mở bài hát Lạc Trôi" chọn nhầm skill xổ số.
3. Mô tả skill do AI sinh **bằng tiếng Anh** → câu tiếng Việt không khớp.
4. Skill vừa tạo không gọi được trong cùng lượt (danh sách tool lấy một lần đầu lượt).
5. Đường thoại không bao giờ chủ động tạo skill; đường tự tạo trong vòng agent **không qua RBAC** (viewer cũng khiến máy chủ sinh + cài mã).

**Sửa:**
- `skill_router.rank_tools`: chấm MỌI skill theo từ (bỏ dấu, bỏ từ chức năng), cụm hai từ, trọng số độ hiếm trong danh mục (IDF); miền chỉ là điểm cộng. `STRONG_MATCH_SCORE` 4,5 / `WEAK_MATCH_SCORE` 2,0 (đo trên danh mục thật). Câu xã giao chỉ được 0 tool khi không khớp rõ skill nào.
- `classify_intent`: câu khớp rõ một skill = lệnh vận hành. Câu trò chuyện khớp yếu vẫn được đưa 3 skill gần nhất.
- `MetaArchitect`: prompt yêu cầu mô tả tiếng Việt có dấu + các cách nói; tự gắn **câu yêu cầu gốc của người dùng** vào mô tả (`_with_user_phrasing`).
- Vòng agent làm mới danh sách tool ngay sau `create_new_skill` / `reload_all_skills` và nhắc model gọi skill mới.
- Chủ động tạo: người hỏi là admin + không skill nào khớp rõ → đường thoại đưa thêm `create_new_skill`. `create_new_skill` và đường tự tạo **không tạo trùng** (`find_existing_skill` trả skill đã có). Đường tự tạo qua RBAC (`_may_create_skills`, chỉ admin).
- Skill đã có `skills/auto_play_music.py` (tệp do AI tạo, chưa vào git): gắn thêm cách nói tiếng Việt vào mô tả (chỉ mô tả, mã giữ nguyên; bản sao lưu ở scratchpad).

**TESTS:** 385 pass (+14 `test_auto_skill_discovery`: tìm skill không cần tên, khớp theo từ, câu xã giao 0 tool, phân loại, gắn câu yêu cầu, chỉ admin tạo, viewer không kích hoạt tự tạo, skill mới gọi được cùng lượt, không tạo trùng).

**RUNTIME:** danh mục thật: "mở bài hát Lạc Trôi", "bật nhạc Sơn Tùng", "nghe nhạc trên youtube" → `auto_play_music` đứng đầu; "hát cho tôi nghe bài…" (trò chuyện) vẫn được đưa `auto_play_music`; "lô nào về nhiều nhất tuần này" → `thong_ke_lo_xsmb`; "Kiểm tra CPU và RAM" → `get_active_processes`; "Xin chào", "Cảm ơn" → 0 tool. Lượt nói thật qua HUD "Lô nào về nhiều nhất trong hai tuần qua?" → trợ lý tự gọi `thong_ke_lo_xsmb`, trả lời có số liệu.

**Còn lại:** câu trò chuyện khớp yếu ("kể chuyện cười", "thời tiết") được đưa kèm 3 skill không liên quan (model không gọi, tốn thêm ít token). Chủ động tạo skill chưa thử thật trên máy chủ (sẽ sinh và cài mã mới) — đã có test với LLM giả.

## 55. Giọng nói không đọc code/ký tự; vòng agent luôn có câu trả lời (2026-10-03)

**STATUS:** XONG

**1. Trợ lý đọc cả code và ký tự** (chủ dự án báo). Tái hiện: đường stream tách câu trước rồi làm sạch TỪNG câu — khối ```code``` bị chia qua nhiều câu, mỗi câu chỉ có nửa cặp ``` nên không nhận ra, TTS đọc "import shutil", "print(...)". Tương tự chú thích ẩn `<!--VOICE: …-->` (đọc ra "!--VOICE: …") và code nội dòng bị cắt ở dấu chấm (`x.replace(...)`). Ngoài ra đường dẫn bị dính chữ ("D:VNMateaiv1config.json"), `=>`, ``` rời.
- `speech_text.CodeFenceStripper`: lọc trên LUỒNG token (giữ trạng thái qua các token, kể cả dấu bị cắt giữa hai token) — khối code → một câu "Phần mã em đã hiển thị trên màn hình." (một lần mỗi lượt), `<!-- … -->` và code nội dòng → bỏ. `SentenceBuffer` (bộ tách câu chung của mọi đường stream) dùng nó.
- `sanitise_for_tts`: bỏ dòng trông như code/lệnh khi model không bọc ```, JSON lồng nhau, đường dẫn chỉ đọc tên tệp cuối, bỏ toán tử/mũi tên và ký hiệu (`* $ | ;` …), gộp dấu chấm lặp. Số thập phân, IP, ngày, "Wi-Fi" giữ nguyên.
- Kiểm thật qua HUD câu "Viết giúp anh đoạn mã Python đọc tệp CSV…": trước — đọc code và "!--VOICE"; sau — 6 câu nói, không còn code/ký hiệu.

**2. "Đã hoàn thành các bước tác vụ nhưng đã đạt giới hạn vòng lặp xử lý."** — hết 4 vòng gọi tool thì trả câu cố định, bỏ hết kết quả tool đã có. Nay: 6 vòng; hết vòng thì gọi model thêm MỘT lần không kèm tool để trả lời từ kết quả đã thu; model lỗi thì nói rõ đã chạy công cụ nào. Lời gọi lặp đúng tool + tham số trong cùng lượt không chạy lại (trả kết quả cũ, nhắc trả lời) — nguyên nhân hay gặp làm hết vòng, và tránh làm hai lần tác vụ có tác dụng phụ. Gỡ hằng `MAX_TOOL_ROUNDS_VOICE` không ai dùng.

**TESTS:** 403 pass (+ stream code/chú thích/code nội dòng ở nhiều cỡ token, dòng code không bọc, đường dẫn, JSON lồng; hết vòng vẫn có câu trả lời, model lỗi vẫn có câu báo, lời gọi lặp không chạy lại). Một kỳ vọng cũ đổi có chủ đích: câu đọc thay khối code "em đã thực thi xong." → "Phần mã em đã hiển thị trên màn hình.".

## 56. HUD: popup giữ tới khi đọc xong; vòng hội thoại nghe → trả lời → hỏi lại → chờ (2026-10-03)

**STATUS:** XONG (máy chủ + logic HUD có test; hành vi mic trong Chrome thật chưa kiểm bằng tay)

**Popup câu trả lời:** cố định 10 giây kể từ lần cập nhật cuối nên tắt khi còn đang đọc. Nay không đếm lùi trong lúc trợ lý còn phát tiếng ("🔊 Đang đọc…"); đọc xong mới đếm, thời gian theo độ dài nội dung (10–60 giây).

**Lặp câu (chào hai lần, "em đã xử lý" hai lần) — nguyên nhân tìm được:**
1. HUD mở mic **ngay khi máy chủ báo xong**, lúc loa còn đang đọc → mic thu giọng trợ lý, gửi như lệnh mới → trợ lý trả lời chính nó. Nay chỉ mở mic khi hết tiếng (+0,4 s), và lời mic nghe được trùng lời trợ lý vừa đọc bị bỏ qua (tiếng vọng).
2. Lời đệm (sau 1 s) + câu xác nhận khi model gọi tool cùng phát → MỘT câu xác nhận mỗi lượt (`turn["acked"]`). Câu "dẫn chuyện" đầu tiên của model ngay trước khi gọi tool bị bỏ khi đã có câu xác nhận.
3. Gói chữ của mỗi câu không có `audio_base64` (tiếng đi bằng khung nhị phân) → HUD tưởng "không có tiếng", hiện cảnh báo và nhảy idle/đang nói. Nay gói có `has_audio`.
4. Máy chủ đoán thời lượng rồi tự đẩy "idle" — đè lên trạng thái "đang nghe". Đã bỏ; HUD tự về idle khi hết tiếng.

**Vòng hội thoại:** câu hỏi lại cũ gọi `speakHudText` — **hàm không tồn tại**, nên câu hỏi lại chưa bao giờ có tiếng. Nay:
- Máy chủ đọc "Anh còn cần em hỗ trợ gì nữa không ạ?" sau mỗi câu trả lời không tự kết thúc bằng câu hỏi (kết thúc bằng câu hỏi thì không hỏi thêm — `looks_like_question` xét câu cuối).
- HUD mở mic khi đọc xong, chờ tối đa **30 giây** (Chrome tự tắt mic sau vài giây im lặng → mở lại).
- "không / thôi / hết rồi / cảm ơn / tạm biệt…" (câu ngắn, bỏ từ lễ phép) → đóng lắng nghe ngay, không gọi AI. `is_stop_reply` viết lại theo cả câu (trước so chuỗi con).
- Hết 30 giây → HUD gửi `end_conversation` → máy chủ đọc "Nếu anh không có yêu cầu nào khác thì tạm biệt, hẹn gặp lại anh nhé." rồi đóng.

**TESTS:** 408 pass (+4 test hội thoại phía máy chủ) và `tests/test_hud_conversation_loop.mjs` 23/23 (chạy đúng mã hud.js: câu kết thúc, tiếng vọng, thứ tự mở mic, 30 s, popup).

**RUNTIME (WebSocket HUD thật):** "Xin chào" → một lời chào, chờ nghe; "Mấy giờ rồi" → trả lời + hỏi lại; "dạ không có gì nữa đâu em" → đóng ngay, không tiếng; "Kiểm tra CPU và RAM máy chủ" → MỘT câu xác nhận + kết quả + hỏi lại; `end_conversation` timeout → lời chào tạm biệt + đóng.

## 57. Realtime Phase 0 (audit) + Phase 1 (đo đạc) (2026-10-03)

**STATUS:** XONG. Theo prompt "REALTIME VOICE / LOW LATENCY" của chủ dự án; chi tiết ở `docs/realtime/`.

- Phase 0 (chỉ đọc): `architecture-audit.md`, `call-graph.md`, `canonical-components.md`, `duplicate-components.md`, `migration-plan.md`.
- Phase 1: `VoiceTurnTrace` trong `application/voice/voice_turn.py` — một trace cho mọi kênh (portal, HUD, robot, mic máy chủ) thay `realtime_voice_ws.VoiceRequestTrace` chỉ có ở portal (gỡ). Sửa nghĩa: TTFT cũ là "câu đầu được đọc", TTFA cũ tính cả câu xác nhận; nay tách `llm_first_token_ms`, `ttft_ms`, `ack_audio_ms`, `ttfa_answer_ms`, `agent_ms`, `stt_ms` (robot gom 3 nhánh STT về `_transcribe`), kích thước prompt, số tool. Bộ đệm vòng 500 lượt + `GET /api/v1/voice/metrics` (admin, p50/p95/p99 theo kiểu lượt). `scripts/bench_voice.py` thêm lệnh vận hành, số đo phía máy chủ, đồng thời (trong tiến trình), bộ nhớ 100 lượt.
- Baseline: `docs/realtime/performance-baseline.md`. Nút thắt: lệnh vận hành TTFA-answer p50 15 s (P3), câu hỏi kiến thức bị định tuyến nhầm sang vận hành — lỗi lùi từ §54 (P2), prompt ~3.700 token (P2), TTS câu động 1–2 s (P4). Không rò bộ nhớ/task qua 100 lượt; không suy giảm tới 10 phiên.
- TESTS: 415 pass (+ `test_voice_turn_trace.py`; test cũ dùng `VoiceRequestTrace` chuyển sang trace mới; test HUD cô lập khỏi cache đĩa).

## 58. Realtime P2 — Voice Brain gọn + sửa định tuyến nhầm (2026-10-03)

**STATUS:** XONG. Số đo: `docs/realtime/performance-before-after.md`.

- `skill_router._task_text`: bỏ cụm chỉ CÁCH trả lời ("giải thích", "ngắn gọn", "chi tiết", "trong hai câu"…) trước khi chấm skill. Sửa lỗi lùi từ §54: câu hỏi kiến thức khớp mô tả skill xuất dữ liệu (6,6 điểm) → bị coi là lệnh vận hành.
- `llm_engine.build_system_prompt(conversation=True)`: prompt trò chuyện (`_VOICE_SYSTEM_PROMPT` + tên, cá tính, nguồn gốc, quy tắc hỏi lại HUD, thời gian, xưng hô). Khối vận hành gom vào `_ops_prompt_blocks`. `stream_voice_response` dựng prompt SAU khi phân loại ý định. Vòng agent vẫn dùng prompt đầy đủ.
- Trace thêm `system_chars`, `history_chars`, `tools_chars`, `brain`.
- Kết quả: câu kiến thức có prompt 11,3k → 3,6k ký tự, 5 → 1 tool, không còn câu xác nhận thừa; TTFA-answer p50/p95 4,5/10,5 s → 3,9/6,1 s (một phần là nhiễu nhà cung cấp, xem đối chứng).
- Ghi lại: `create_new_skill` (~1,1k ký tự) vẫn đưa cho câu hỏi kiến thức của admin — cần cân nhắc cùng yêu cầu "chủ động tạo skill".
- TESTS: 421 pass (+ `test_voice_brain_prompt.py`, + 3 test định tuyến).

## 59. Realtime P3 + P4 + P5 (một phần) + P6 (một phần) (2026-10-03)

**STATUS:** XONG phần ghi dưới đây; số đo ở `docs/realtime/performance-before-after.md`.

- **P3** — lệnh vận hành: lời gọi tool đã stream được ghép đủ (`_accumulate_tool_calls`, `_finish_streamed_tool_calls`, chờ tối đa 8 s) và chạy luôn ở vòng agent đầu (`ask_async(first_tool_calls=…)`) — bỏ một lần gọi LLM với 82 tool; vòng agent của lượt thoại chỉ thấy tool đã đưa cho lần stream + công cụ quản lý kỹ năng (`tool_names=…`); kênh khác gọi `ask_async` không gợi ý vẫn thấy cả danh mục. Câu trả lời của agent tách câu trước TTS. Vòng agent p50 5,7 → 3,4 s.
- **P4** — đo nhà cung cấp TTS (`bench_voice.py --tts-providers`): 9Router nhanh hơn Edge ở mọi độ dài, giữ thứ tự. `SentenceBuffer(first_max_words)` (câu đầu ngắn, chỉ cắt ở dấu phẩy) — A/B không chứng minh được cải thiện đáng kể, ghi rõ. Hàng đợi audio ra có giới hạn (4). Làm nóng cache thêm câu cố định của lệnh nhanh + HUD (`STATIC_REPLIES`, `extra_phrases`).
- **P5 (D1)** — REST `/api/v1/voice-command` chạy qua `process_voice_turn` (lệnh nhanh, stream, agent, TTS theo câu; base64 chỉ ở biên); `VoiceTurnResult` mang `tool_calls_made`, `requires_confirmation`. `pre_ack=False` tắt cả câu xác nhận khi gọi tool (REST, mic máy chủ).
- **P5 (D2)** — mic máy chủ: bỏ đường LLM thứ hai (`process_voice_command_sync` → `ask`) khi lượt rỗng; phát câu xin lỗi cố định.
- **P5 (D5)** — `web/voice-audio-queue.js` (`VoiceAudioQueue`) thay `StreamingAudioQueue` (portal) và `HudAudioQueue` (HUD). Sửa: HUD giải mã song song nên có thể đảo câu; cả hai bản phát nốt đoạn đang giải mã sau khi ngắt lời; portal tạo AudioContext mới mỗi lượt. Test `tests/test_voice_audio_queue.mjs`. **Chưa nghe thử trên trình duyệt thật** (máy không có công cụ tự động trình duyệt) — ghi ở owner-todo.
- **P6** — gỡ mã chết `llm_engine`: `chat`, `generate_response`, `process_voice_command(_sync)`, `report_action_execution`, `ask` (sync), `stream`, `stream_tokens`, `_get_client`, `_make_client` (kiểm caller tĩnh + chuỗi động: 0 caller ngoài nhau / test kiểm `hasattr`); `voice_controller._get_llm_response_*`. RULE-011 llm_engine 3 → 2 (baseline khoá lại).
- TESTS: 441 pass + mọi `.mjs`. Chạy thật: REST (lệnh nhanh 1,8 s, kiến thức 3,4 s, vận hành 9 s có tool), HUD (lượt có tool, đọc đúng, hỏi lại).
- Ghi nhận (không sửa, chờ chủ dự án): mỗi lần chạy tool ghi HAI dòng audit (cổng tool + RBAC note).

## 60. Realtime P6 — dọn trùng lặp còn lại (2026-10-03)

**STATUS:** XONG trừ hai mục ghi dưới.

- **L1** — `classify_intent` một lần mỗi lượt: `voice_turn` lưu `turn["intent"]`, `stream_voice_response` dùng lại; `ask_async` chỉ phân loại khi không có tool.
- **L3** — gỡ bí danh `/ws/voice` (0 client trong repo: web, client_agent, scripts, firmware; chỉ test). Test chuyển sang `/ws/v1/voice-stream`; chạy thật: `/ws/voice` bị từ chối, `/ws/v1/voice-stream` mở.
- **D6** — `autonomous_sentinel` đọc số đo phần cứng + 9Router từ `health_monitor.SYSTEM_HEALTH_CACHE` (thêm `checked_at`); số đo cũ hơn 90 s không báo sự cố; bỏ import `psutil` / `httpx`.
- **D7** — client OpenAI-compatible dựng ở MỘT nơi: `llm_provider.make_llm_client`; lời gọi thử model của màn hình chẩn đoán: `llm_provider.probe_model`. RULE-011 ngoài provider: llm_engine 2 → 0, ai_delegation 1 → 0, routers/config 3 → 0 (baseline `{}`). Chạy thật `/api/v1/llm/test`: model thật OK 3,9 s; model sai → tự chuyển model dự phòng.
- **Chưa làm (ghi lại):** L5 phần còn lại — ba nhánh kết thúc câu nói của robot gửi chuỗi thông điệp khác nhau theo firmware (`asr_result` / `stt`+`session_id` / gói WAV); gộp tiếp cần thử trên robot thật (không có thiết bị lúc làm). P5 D4 — HUD dùng chung schema sự kiện `/ws/v1/voice-stream`: viết lại phần nhận sự kiện của `hud.js` (trang dùng hằng ngày) mà không có công cụ kiểm tra trình duyệt tự động — để làm khi có thể kiểm tra trên trình duyệt.
- TESTS: 443 pass + `.mjs`.

## 61. Realtime P7 — báo cáo cuối (2026-10-03)

**STATUS:** XONG.

- Benchmark tổng kết cùng lệnh với baseline: `docs/realtime/bench-2026-10-03-final.json`; bảng tổng kết + đối chiếu mục tiêu của prompt: `docs/realtime/performance-before-after.md`.
- `docs/production/production-readiness.md` (đường thoại): đã kiểm chứng / rủi ro còn lại (R1 chuỗi thử model dự phòng 40 s, R2 độ trễ nhà cung cấp, R3 bộ phát trình duyệt chưa nghe tai, …).
- Kiểm tra trùng lặp chạy lại: `docs/realtime/duplicate-components.md` §5 (D1, D2, D3, D5, D6, D7, L1, L3, mã chết: đã gộp/gỡ; D4, L5: còn, có lý do).
- Việc của chủ dự án: `docs/production/owner-todo.md` § "Thoại realtime".
- TESTS: 443 pytest + mọi `.mjs`.

## 62. Rà soát tạo kỹ năng mới (2026-10-04)

**STATUS:** XONG. Một quy trình duy nhất: `meta_architect.create_skill` → `install_skill` (công cụ `create_new_skill` và đường tự tạo khi model báo thiếu công cụ cùng gọi).

Lỗi đã sửa (mỗi lỗi có test trong `tests/test_meta_architect_install.py`, `test_auto_skill_discovery.py`, `test_voice_brain_prompt.py`):
- **Trả SAI tên công cụ vừa tạo**: `get_skill_names()[-1]` (danh sách theo chữ cái) ở cả hai đường → model gọi nhầm tool. Nay lấy tên từ `@export_skill` của chính mã đã nạp và xác nhận đã đăng ký đúng module.
- **Ghi đè tệp kỹ năng có sẵn** (kể cả kỹ năng lõi) khi trùng tên tệp → nay luôn tạo tệp mới (`_2`, `_3`…); tên tệp ASCII `auto_<slug>` (trước đây có thể chứa chữ có dấu).
- **Tên công cụ trùng công cụ có sẵn** bị đè im lặng → từ chối.
- Kiểm tra trên mã nhưng ghi ra mã khác (thêm docstring đầu tệp SAU khi kiểm; docstring + `from __future__` → tệp sai cú pháp) → kiểm trên đúng nội dung ghi ra; đầu tệp là chú thích.
- **Module không nạp được vẫn báo thành công**, tệp hỏng nằm lại → gỡ tệp, nạp lại, báo lý do.
- Mã chạy lệnh ngay khi nạp (cấp module) → từ chối.
- Kiểm toán an ninh AST: thêm chặn `shell=True`, `os.kill/_exit/exec*`, import `ctypes/importlib/marshal/pickle` (hai kỹ năng AI hiện có vẫn qua).
- Duyệt bằng hộp thoại Windows trên máy chủ (`SafetyGuard`, khi tắt `auto_execute`) — máy chủ chạy nền, lượt nói treo → nay lưu `skills/pending/` chờ quản trị viên duyệt; gỡ `SafetyGuard`.
- **Đánh giá rủi ro theo chuỗi con**: "s-KILL-s" khớp "kill" → mọi tool có chữ "skill" ở Level 5 (`list_available_skills` chỉ đọc cũng phải duyệt). Nay so theo từ; khai báo rõ `list_available_skills` 1, `reload_all_skills` 2, `create_new_skill` 4, `install_skill_from_url` 5 (giữ mức cũ). So mọi tool trước/sau: chỉ 3 tool đổi mức.
- **Chống trùng quá lỏng**: dùng ngưỡng 4,5 ("đưa tool cho model") → "tra cứu ngày âm lịch" bị coi là trùng `fetch_data_source`, không tạo kỹ năng, model đọc dữ liệu không liên quan. Nay `DUPLICATE_SKILL_SCORE = 10` (danh mục thật: việc chưa có ≤ 8,94; việc đã có ≥ 10,15).
- Prompt agent dặn gọi `learn_new_skill` (không tồn tại) → `create_new_skill`.
- **Prompt vòng agent không có ngày hiện tại** (khối dựng ra nhưng không nối vào — có từ trước) → model bịa ngày. Nay cả hai prompt có "Hôm nay là <thứ>, ngày dd/mm/yyyy" + quy tắc dữ liệu thực tế (dùng / tạo công cụ, không đoán).

RUNTIME (portal, admin): "Giá vàng SJC hôm nay" → `create_new_skill` → tệp `auto_get_gold_price.py`, tên công cụ đúng `get_sjc_gold_price` → gọi ngay trong cùng lượt (25,6 s); kỹ năng sinh ra dùng nguồn đã đóng (SJC XML 403, tygia.com 436) nên trả lỗi — trợ lý báo thật, không bịa giá; tệp thử này đã gỡ. "Hôm nay âm lịch ngày bao nhiêu" → ngày dương đúng 04/10/2026, âm 24/8 Bính Ngọ. `reload_all_skills` bằng lời nói chạy ngay (trước đây đòi duyệt).
TESTS: 476 pass.

## 63. Robot: nghe xa hơn + L5 gộp ba nhánh hết câu (2026-10-04)

- **Méo âm mic robot:** `(int16_t)(raw32 >> 14)` không kẹp — tiếng to bị quấn số (đỉnh đo được luôn ~30.000). Ảnh hưởng CẢ âm thanh gửi nhận dạng lệnh. Nay `micToPcm16` kẹp, giữ độ khuếch đại.
- **Ồn nền** lên nhanh theo tiếng nói (đo tới 10.882) nên ngưỡng bắt tiếng quá cao, chỉ gọi gần ~30 cm mới nghe → ồn nền xuống nhanh / lên rất chậm; ngưỡng bắt tiếng 3× → 2× ồn nền; máy chủ nâng biên độ đoạn gọi nhỏ (tối đa ×8) trước khi nhận dạng; log mức tín hiệu từng đoạn (không nội dung). Sau sửa: ồn nền đo ~1.000.
- **L5:** ba nhánh hết câu của robot (VAD máy chủ, `listen stop` firmware XiaoZhi gốc, `end_of_speech`) gộp vào `XiaozhiGateway._finish_utterance` + `_pcm16_to_wav`; giữ nguyên từng thông điệp theo firmware — test đặc tả viết TRƯỚC khi gộp (`tests/test_robot_utterance_end.py`, 7 ca) pass trước và sau.
- TESTS: 515 pass.

## 64. Lời nói tự nhiên, ngắn hơn (2026-10-04)

Người dùng phản ánh: nghe như đọc mẫu; câu nào cũng "Chi tiết cụ thể đã được hiển thị trên màn hình"; luôn hỏi lại.
- Bỏ câu gắn tự động ở `shorten_for_speech` (gắn vào MỌI câu dài — mỗi câu được rút riêng) và `_make_concise_speech_text`; khối code bị lược khi đọc, không đọc câu thay ("Phần mã em đã hiển thị…").
- Prompt: bỏ ví dụ VOICE chứa câu đó; bỏ quy tắc "BẮT BUỘC kết thúc bằng câu hỏi" (Phase 36) → chỉ hỏi khi cần thêm thông tin; quy tắc chung `_NATURAL_SPEECH_RULE` (đi thẳng ý chính, không câu khuôn mẫu, làm tròn số khi đọc).
- Lượt thoại dùng prompt vận hành / vòng agent (`build_system_prompt(spoken=True)`, `ask_async(spoken=True)`): trả lời 1–3 câu văn nói, không bảng / Markdown / thẻ VOICE. Trước đây model viết bản hiển thị dài rồi mới tới thẻ VOICE, đường stream đọc hết.
- Chạy thật (REST, portal): "máy chủ chạy bao lâu" 18,7 s → 5,8 s ("…hơn 6 ngày rồi anh, tính từ tối ngày 28 tháng 9"); "CPU và RAM" 8,8 s → 5,5 s.
- TESTS: 524 pass.

## 65. Robot: hội thoại liên tục + đọc không bị cắt (2026-10-04)

- **Đọc bị cắt giữa chừng:** máy chủ gửi PCM nhanh gấp 1,4 lần nhịp phát (2048 byte = 64 ms mỗi 45 ms) nên tưởng đọc xong khi robot còn vài giây chưa phát; lúc đó (câu trả lời kết thúc bằng câu hỏi) chuyển robot sang nghe → mic thu giọng robot → lượt mới cắt ngang. Nay gửi theo nhịp phát (`_pace_playback`, đi trước ≤ 0,5 s) và chờ robot phát xong (`_wait_playback_done`) rồi mới `tts stop` / nghe.
- **Hội thoại liên tục:** sau MỌI câu trả lời robot nghe tiếp (`listen_timeout_ms` = 30 s do máy chủ đặt, firmware `listenTimeoutMs`); im lặng 30 s → firmware `listen abort` → máy chủ đọc câu tạm biệt (`voice_session.FAREWELL_PHRASE`, dùng chung HUD) rồi nghỉ; "không / thôi / cảm ơn" (`is_stop_reply`) → đóng ngay. Chạm để gọi vẫn 8 s.
- **Nội dung:** prompt thoại nói đủ ý chính (2–5 câu) và gợi ý một bước tiếp theo cụ thể khi có ích (lần trước ép 1–3 câu, không gợi ý — người dùng thấy thiếu).
- TESTS: 537 pass (+ `tests/test_robot_follow_up.py`).
