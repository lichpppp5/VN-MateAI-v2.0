# VN-MateAI — Bộ Quy Tắc Phụ Thuộc & Ranh Giới Kiến Trúc (Dependency & Architecture Rules)

## I. MƯỜI ĐIỀU LUẬT BẮT BUỘC (ARCHITECTURE RULES)

Mọi module và thay đổi code trong quá trình tái cấu trúc VN-MateAI phải tuân thủ nghiêm ngặt 10 quy tắc kiến trúc sau:

### RULE-001: Domain Layer không được import Infrastructure
- **Mô tả**: Tầng nghiệp vụ cốt lõi (`domain/`) hoàn toàn độc lập với công nghệ kỹ thuật.
- **Cấm**: Tuyệt đối không import `httpx`, `aiohttp`, `sqlite3`, `asyncpg`, `redis`, `edge_tts`, `boto3`, hay bất kỳ thư viện SDK bên thứ ba nào trong `domain/`.
- **Được phép**: Chỉ sử dụng kiểu dữ liệu Python thuần túy (`dataclasses`, `typing`, `enum`, `datetime`, `uuid`, `pydantic`).

### RULE-002: Domain Layer không được import Web Framework
- **Mô tả**: Nghiệp vụ hệ thống không gắn liền với cách thức phân phối qua web.
- **Cấm**: Tuyệt đối không import `fastapi`, `starlette`, `uvicorn`, `websockets` trong `domain/` và `application/`.

### RULE-003: Application Layer không gọi Database thô (No Raw SQL)
- **Mô tả**: Các Use Case trong `application/` chỉ tương tác với dữ liệu thông qua các giao diện trừu tượng (`Repository Interfaces`).
- **Cấm**: Không viết câu lệnh SQL thô (`SELECT`, `INSERT`, `UPDATE`), không quản lý database connections hoặc cursors trong `application/`.

### RULE-004: Tầng Giao Diện (Interfaces Layer) không chứa Business Logic
- **Mô tả**: HTTP Router và WebSocket Handler chỉ đóng vai trò bộ chuyển đổi giao thức (Transport Adapters).
- **Trách nhiệm**: Validate request payload, giải mã JWT token (nếu có), ủy quyền thực thi cho Application Use Case, và format dữ liệu trả về theo chuẩn API response.

### RULE-005: WebSocket Handler không gọi trực tiếp AI SDK hoặc Database
- **Mô tả**: WebSocket endpoint không được tự ý kết nối DeepSeek, Groq, hay database để thực thi logic.
- **Luồng chuẩn**: WebSocket Handler phải gọi Use Case thuộc `application/voice/` hoặc `application/agent/`.

### RULE-006: LLM Providers không được truy xuất trực tiếp Enterprise Database
- **Mô tả**: Các adapter LLM (`infrastructure/llm/`) chỉ thực hiện nhiệm vụ suy luận ngôn ngữ, nhận prompt và trả về completion/stream.
- **Quyền hạn**: Không có quyền truy xuất trực tiếp database doanh nghiệp để lấy dữ liệu ngoài context được cung cấp.

### RULE-007: Mọi công cụ đặc quyền (Privileged Tools) phải kiểm tra quyền (RBAC & Clearance)
- **Mô tả**: Tất cả các tool can thiệp vào hệ thống máy chủ, mạng, cơ sở dữ liệu hoặc thông tin nhân sự/tài chính phải đi qua bộ kiểm tra quyền hạn (`SecurityGuard` / `ZeroTrustValidator`).

### RULE-008: Mọi lời gọi dịch vụ ngoài (External Calls) phải có Timeout & Circuit Breaker
- **Mô tả**: Tuyệt đối không gọi HTTP client ngoài hoặc connector bên thứ ba mà không thiết lập timeout rõ ràng (mặc định 5s - 10s).
- **Bảo vệ**: Phải có cơ chế ngắt mạch (Circuit Breaker) để tránh tình trạng dịch vụ ngoài bị treo làm nghẽn toàn bộ event loop của backend.

### RULE-009: Mọi phiên thoại Realtime phải có vòng đời Cancellation (Barge-In Lifecycle)
- **Mô tả**: Mỗi lượt tương tác thoại gắn liền với một `session_id`, `turn_id` và một `asyncio.Event` / Cancellation Token.
- **Ngắt lời**: Khi người dùng ngắt lời (Barge-In), toàn bộ tác vụ LLM stream, TTS synthesis worker, và audio queue của turn cũ phải bị hủy lập tức trong vòng dưới 10ms.

### RULE-010: Mọi tiến trình Production phải cung cấp Health Endpoints tiêu chuẩn
- **Mô tả**: Các tiến trình backend phải cung cấp đầy đủ 3 endpoint kiểm tra sức khỏe độc lập:
  - `/livez`: Liveness probe (kiểm tra tiến trình còn sống).
  - `/readyz`: Readiness probe (kiểm tra khả năng nhận traffic, kết nối DB/Redis sẵn sàng).
  - `/startupz`: Startup probe (kiểm tra tiến trình đã nạp xong model, config và cache khởi đầu).

---

## II. MA TRẬN PHỤ THUỘC GIỮA CÁC TẦNG (IMPORT BOUNDARY MATRIX)

| Tầng gọi tới ➔ Gọi vào Tầng | Domain | Application | Infrastructure | Interfaces | Frameworks (FastAPI) | External SDKs |
| :--- | :---: | :---: | :---: | :---: | :---: | :---: |
| **Domain** | ✅ | ❌ | ❌ | ❌ | ❌ | ❌ |
| **Application** | ✅ | ✅ | ❌ (Chỉ qua Ports) | ❌ | ❌ | ❌ |
| **Infrastructure** | ✅ | ✅ (Thực thi Ports) | ✅ | ❌ | ❌ | ✅ |
| **Interfaces** | ✅ | ✅ | ❌ (DI container nạp) | ✅ | ✅ | ❌ |

---

## III. CƠ CHẾ KIỂM TRA TỰ ĐỘNG — TRẠNG THÁI THẬT (Phase 0, 2026-10-01)

`tests/architecture/test_architecture_boundaries.py` hiện chỉ kiểm tra **3/10 quy tắc** (RULE-001/002, RULE-003, RULE-004) và chỉ quét `src/mateai/` — cây code **không chạy** trong runtime. Thư mục `src/mateai/interfaces/` rỗng nên RULE-004 luôn đạt. Kết quả "đạt 100 %" vì thế không nói gì về `core/`.

**Phase 1 đã làm:** `tests/architecture/test_core_rules.py` quét code đang chạy (`core/`, `skills/`, `workers/`, `main.py`) theo RULE-011…015 với baseline `tests/architecture/core_rules_baseline.json` — vi phạm mới làm test fail, số vi phạm giảm thì phải hạ baseline (bánh cóc). Test cũ cho `src/mateai/` nay được pytest chạy.

Yêu cầu ban đầu (giữ để đối chiếu):
1. Quét **code thật đang chạy**: `core/` hôm nay, và các tầng đích khi code được di chuyển sang.
2. Cho phép ghi nhận vi phạm hiện có vào một danh sách ngoại lệ có hạn chót (baseline), và **fail khi xuất hiện vi phạm mới** — để cấm quay lui mà không chặn công việc.
3. Kiểm tra thêm các quy tắc chống trùng lặp ở mục IV.

## IV. QUY TẮC CHỐNG TRÙNG LẶP (bổ sung từ audit trùng lặp)

| Mã | Quy tắc | Kiểm tra tự động |
|---|---|---|
| RULE-011 | Chỉ `infrastructure/llm` (`mateai/infrastructure/llm/llm_provider.py`) được tạo client `OpenAI`/`AsyncOpenAI` hoặc gọi `chat.completions.create` | grep AST `OpenAI(`, `AsyncOpenAI(`, `.chat.completions.create` |
| RULE-012 | Chỉ `infrastructure/tts` (`mateai/infrastructure/tts/tts_stream_engine.py`) được gọi `edge_tts.Communicate`, `gTTS`, `/audio/speech` | grep AST |
| RULE-013 | Chỉ loader cấu hình được mở `config.json` | grep chuỗi `"config.json"` kèm `open`/`read_text`/`json.load` |
| RULE-014 | Chỉ tầng persistence được gọi `sqlite3.connect` | grep AST |
| RULE-015 | Không module nào trong lõi import `core.server` (hoặc `interfaces/*`) | đồ thị import |
| RULE-016 | Mỗi tên tool chỉ được đăng ký ở một registry | so khớp tên từ `plugin_manager` và `plugin_registry` lúc khởi động |

## V. TRẠNG THÁI TUÂN THỦ CỦA CODE ĐANG CHẠY (`src/mateai/`, `core/`) — cập nhật 2026-10-02 (sau Phase 4), sửa RULE-007/016 ngày 2026-10-05 (audit Supervisor Phase 0)

Số vi phạm RULE-011…015 do `tests/architecture/test_core_rules.py` đo trên mã thật; baseline ở `tests/architecture/core_rules_baseline.json` chỉ được giảm (vi phạm mới = test fail).

| Quy tắc | Trạng thái | Bằng chứng |
|---|---|---|
| RULE-001/002 | Đạt (Phase 4 xong) | toàn bộ code chạy thật nằm trong `src/mateai` theo tầng domain/application/infrastructure/interfaces; `test_architecture_boundaries` giữ ranh giới (RULE-003: application không import web framework/sqlite; RULE-004: interfaces không chạy SQL). `core/` chỉ còn `plugin_manager` (API plugin công khai) |
| RULE-005 | Một phần | mọi kênh thoại dùng chung `mateai.application.voice.voice_turn.process_voice_turn`; handler WS ở `routers/websockets.py` (chỉ vận chuyển; duyệt từ HUD qua `approval_flow` dùng chung với REST — Supervisor P10) |
| RULE-007 | **Chưa đạt** (2026-10-05) | Tool do LLM gọi qua `tool_gate.run_tool_with_policy`; nhưng còn 2 đường khác: `routers/skills.py:233–245` (RBAC + `execute_with_hitl` riêng) và cổng duyệt trong `plugin_registry.py:441–514`; `agent_orchestrator.py:252,264` gọi hàm skill thẳng, không qua cổng. Danh sách `security.forbidden_keywords` / `require_confirmation_actions` không được áp dụng ở cổng (chỉ `routers/security.py:166` dùng). Còn hai mô hình role (portal ↔ RBAC) |
| RULE-008 | Một phần | `plugin_registry`: timeout + circuit breaker; client httpx riêng còn lại đã phân loại có lý do (plan §19) |
| RULE-009 | Chỉ kênh portal/HUD | huỷ lượt khi có lệnh mới (barge-in) |
| RULE-010 | Đạt | `/livez`, `/startupz`, `/readyz` (`test_health_probes`) |
| RULE-011 | 7 chỗ, đều có lý do | `llm_engine` (3: tạo client cho chính provider), `server` (3: nút "thử kết nối" dùng URL/khoá người dùng nhập), `ai_delegation` (1: client async cho provider chuyên gia). `audio_processor` (Whisper) được miễn |
| RULE-012 | 1 | `skills/ninerouter_skills.py` |
| RULE-013 | **0** | `config_loader` là cổng duy nhất (plan §18) |
| RULE-014 | **0** | `mateai.infrastructure.database.erp_database.open_sqlite` là đường mở duy nhất (plan §23) |
| RULE-015 | **0** | trạng thái kết nối + phát sóng ở `mateai/interfaces/websocket/realtime_hub.py`, helper xuất file ở `mateai/infrastructure/files/file_export.py` (plan §27) |
| RULE-016 | Đạt về tên trùng; **hai danh mục** | không tên nào đăng ký hai lần; nhưng `plugin_registry` vẫn giữ tool riêng đăng ký lúc khởi động (bước khởi động `connector_tools` / `computer_use_tool` trong `interfaces/http/lifecycle.py`: connector qua `tool_bridge`, computer-use) — `plugin_manager` không chứa chúng. Gộp ở Phase 6 |

## VI. QUY TẮC KIỂM SOÁT TỰ TRỊ (bổ sung 2026-10-05 — prompt Supervisor §118, §122)

Chưa có test tự động cho các quy tắc này; trạng thái là kết quả audit Phase 0 (`docs/architecture/current-vs-target.md` §3).

| Mã | Quy tắc | Trạng thái hôm nay | Cách kiểm tự động (Phase 2–3) |
|---|---|---|---|
| RULE-017 | Không đường thực thi tool nào bỏ qua cổng kiểm soát chuẩn (`tool_gate` → control plane) | **Đạt** (2026-10-05): một hàm `policy_engine.authorize()`; quét AST = 0 (`test_rule_017_…`) | AST: `execute_skill(`, `execute_tool(` và import hàm skill builtin chỉ được xuất hiện trong module cổng |
| RULE-018 | Chính sách DENY luôn thắng — kể cả với admin, kể cả khi đã duyệt | **Đạt** (`test_policy_engine`) | test thuộc tính: mọi tool trong danh sách DENY → không thực thi với mọi role / mọi cờ `approved` |
| RULE-019 | Luật bảo mật trong cấu hình (`security.*`) phải được chính cổng thực thi đọc | **Đạt** (`test_portal_forbidden_keywords_are_enforced_at_the_gate`, `…require_confirmation…`) | test: thêm từ khoá cấm vào cấu hình → tool chứa từ khoá đó bị chặn ở `run_tool_with_policy` |
| RULE-020 | Kiểm tra quyền (RBAC) chạy **trước** khi tạo yêu cầu duyệt | **Đạt** (`test_rbac_runs_before_any_approval_request`) | test: role không có quyền → không sinh yêu cầu HITL |
| RULE-021 | Mọi hành động tự trị có danh tính tác nhân (`agent_id`) + người uỷ quyền + `trace_id` trong audit | Chưa đạt (không có `agent_id`) | test: payload audit của tool chạy từ vòng agent có đủ 3 trường |
| RULE-022 | Mọi vòng agent / tác vụ tự trị có giới hạn bước, thời gian, chi phí và tôn trọng kill switch | Một phần (6 vòng/lượt; không giới hạn thời gian/chi phí; không kill switch) | test: bật kill switch → không tool nào chạy; vượt ngân sách → dừng |
| RULE-023 | Nội dung ngoài (kết quả tool, RAG, email, web, tệp) không được đưa vào phần chỉ thị hệ thống | Một phần: tool không ghi / xoá được mã nguồn, cấu hình, `identity_core.md` (`test_file_write_protection`); nhãn "dữ liệu không tin cậy" cho kết quả tool: chưa | AST/grep: chỉ hằng số + cấu hình được vào system prompt; tệp trong danh sách bảo vệ không ghi được qua tool |
| RULE-024 | Application không truy vấn SQL trực tiếp (RULE-003 mở rộng cho `application/`) | Baseline 17 (`agent_orchestrator` 6, `onboarding_workflow` 4, `proactive_manager` 7) — chỉ được giảm; xử lý ở Phase 8 | mở rộng `test_architecture_boundaries` sang `get_connection()` / `.execute(` |
| RULE-025 | Không `subprocess(..., shell=True)` | **Đạt**: 0 (`test_rule_025_…`) | AST, baseline = 2, chỉ được giảm |
| RULE-026 | Hàm `async` không gọi API chặn (`psutil.cpu_percent(interval>0)`, `time.sleep`, `requests`, `urllib.request.urlopen`) | **Đạt**: 0 (`test_rule_026_…`; `urlopen` thêm 2026-10-06 sau khi gặp ở `routers/config.py`) |
| RULE-027 | Router HTTP không tự ghi tệp / cấu hình — qua use case tầng application (§198) | **Đạt**: 0 (2026-10-06). Mọi lần ghi cấu hình qua `config_governance.save_config` (nguyên tử + lịch sử phiên bản + audit); bố cục topology qua `application/operations/topology_layout` (`test_rule_027_…`, `test_config_writes_versioned.py`) | AST trong thân `async def` |
