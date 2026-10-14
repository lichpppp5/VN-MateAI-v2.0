# VN-MateAI — Thiết Kế Kiến Trúc Mục Tiêu (Target Production Architecture)

> **Trạng thái (Phase 0, 2026-10-01):** đây là kiến trúc **đích**, chưa phải kiến trúc đang chạy.
> Khung thư mục `src/mateai/` đã được tạo ở commit `4f6464a` nhưng **không module production nào import nó** — runtime vẫn chạy 100 % bằng `core/`.
> Mục V bên dưới ánh xạ từng thành phần canonical đang chạy (xem `canonical-components.md`) vào tầng đích. Cách xử lý phần viết lại trong `src/mateai/` là quyết định D1 trong `docs/migration/production-refactor-plan.md`.
> Các chỉ số latency ghi trong tài liệu này (`< 0.05ms`, Hoài My `+50%`, …) là **mục tiêu**, chưa được đo lại trong Phase 0.

## I. TỔNG QUAN KIẾN TRÚC MỤC TIÊU (MODULAR MONOLITH)

VN-MateAI được chuẩn hóa theo mô hình **Modular Monolith & Clean Hexagonal Architecture**, phân tách nghiêm ngặt giữa Domain Business Logic, Application Use Cases, Infrastructure Adapters và Network Delivery Interfaces:

```text
                                  ┌───────────────────────────┐
                                  │   Clients / Edge Devices  │
                                  │ (HUD, Admin, ESP32, Agent)│
                                  └─────────────┬─────────────┘
                                                │
                                    WebSocket / HTTPS (TLS)
                                                │
                                                ▼
                                  ┌───────────────────────────┐
                                  │      Load Balancer        │
                                  │   (Nginx / Traefik / ALB) │
                                  └─────────────┬─────────────┘
                                                │
                 ┌──────────────────────────────┴──────────────────────────────┐
                 ▼                                                             ▼
     ┌───────────────────────┐                                     ┌───────────────────────┐
     │  API Control Plane    │                                     │   Realtime Gateway    │
     │  (tiến trình API)     │                                     │ (tiến trình realtime) │
     └───────────┬───────────┘                                     └───────────┬───────────┘
                 │                                                             │
                 └──────────────────────────────┬──────────────────────────────┘
                                                │
                                                ▼
                                  ┌───────────────────────────┐
                                  │     Interfaces Layer      │
                                  │ (interfaces/http, ws)     │
                                  └─────────────┬─────────────┘
                                                │
                                                ▼
                                  ┌───────────────────────────┐
                                  │     Application Layer     │
                                  │ (Use Cases & Workflow)   │
                                  │ Voice | Agent | Skills    │
                                  └─────────────┬─────────────┘
                                                │
                                                ▼
                                  ┌───────────────────────────┐
                                  │       Domain Layer        │
                                  │ (Pure Business Logic)     │
                                  │ Session | Task | Security │
                                  └─────────────┬─────────────┘
                                                │
                                                ▼
                                  ┌───────────────────────────┐
                                  │   Infrastructure Layer    │
                                  │ (Technical Adapters)      │
                                  └─┬───────┬───────┬───────┬─┘
                                    │       │       │       │
                                    ▼       ▼       ▼       ▼
                               PostgreSQL Redis   LLM      TTS / STT
                               (Persistent)(Cache/ (DeepSeek/ (EdgeTTS/
                                System)    Session) Groq/OAI)  Whisper)
```

---

## II. ĐỊNH NGHĨA CÁC LỚP TRONG KIẾN TRÚC (LAYER RESPONSIBILITIES)

### 1. Domain Layer (`src/mateai/domain/`)
- **Nguyên tắc**: Tinh khiết (Pure Python), không phụ thuộc vào bất kỳ framework web (FastAPI, Starlette), thư viện ORM, hay SDK bên ngoài nào.
- **Thành phần**:
  - `domain/voice/`: `VoiceSession`, `VoiceState`, `VoiceCommand`, `VoiceInterruption`, `AudioFrame`.
  - `domain/conversation/`: `Message`, `ConversationContext`, `TurnHistory`.
  - `domain/agent/`: `AgentTask`, `Plan`, `ExecutionStep`, `AgentState`.
  - `domain/skills/`: `SkillDefinition`, `ToolDefinition`, `ToolPermissionLevel`.
  - `domain/identity/`: `UserIdentity`, `Role`, `ClearanceLevel`, `DepartmentContext`.
  - `domain/devices/`: `Device`, `DeviceState`, `DeviceCapability`.
  - `domain/audit/`: `AuditEvent`, `AuditRiskLevel`.

### 2. Application Layer (`src/mateai/application/`)
- **Nguyên tắc**: Điều phối (Orchestration) các use case của hệ thống. Không gọi trực tiếp SQL thô, không gọi trực tiếp WebSocket transport hay TTS SDK.
- **Thành phần**:
  - `application/voice/`:
    - `ProcessVoiceTurnUseCase`: Tiếp nhận text từ STT, điều phối qua FastRouter hoặc LLM.
    - `InterruptVoiceSessionUseCase`: Xử lý Barge-In, phát tín hiệu cancel downstream tasks.
  - `application/commands/`:
    - `FastCommandRouter`: Xử lý các câu lệnh tất định (Deterministic System Ops) với latency siêu thấp (< 0.05ms).
  - `application/agent/`:
    - `TriBrainOrchestrator`: Điều phối giữa 3 chuyên não (Controller Brain, Voice Brain, Operations Brain).
  - `application/skills/`:
    - `SkillResolver`: Dynamic loading các tool/skill liên quan dựa trên intent thay vì nạp toàn bộ 79+ tools.

### 3. Infrastructure Layer (`src/mateai/infrastructure/`)
- **Nguyên tắc**: Chứa toàn bộ các chi tiết kỹ thuật (Technical Details) và hiện thực hóa các Port/Interface từ Application & Domain layer.
- **Thành phần**:
  - `infrastructure/database/`: Repository implementations (PostgreSQL adapter cho production & SQLite adapter cho local/edge fallback).
  - `infrastructure/cache/`: Redis adapter cho Distributed Session, Lock và Ephemeral state.
  - `infrastructure/llm/`: `LLMProvider` interface với các adapter cho DeepSeek, Groq, OpenAI, 9Router.
  - `infrastructure/tts/`: `TTSProvider` interface (EdgeTTS streaming adapter, offline backup adapter).
  - `infrastructure/stt/`: Whisper API adapter, local Whisper adapter.
  - `infrastructure/storage/`: S3/MinIO Object Storage adapter & local filesystem adapter.
  - `infrastructure/connectors/`: AWS, OCI, Telegram, ERP connectors có timeout, retry và circuit breaker.
  - `infrastructure/security/`: JWT token verification, Hash, Zero-Trust policy enforcement.
  - `infrastructure/observability/`: Prometheus metrics, OpenTelemetry tracing, Structured JSON logging.

### 4. Interfaces Layer (`src/mateai/interfaces/`)
- **Nguyên tắc**: Tầng giao tiếp mạng (Delivery Mechanism). Chỉ nhận request, validate payload, chuyển đến Application use case và trả về response chuẩn hóa.
- **Thành phần**:
  - `interfaces/http/`: Tách 165 endpoint (đo bằng `app.routes`) thành các router module mỏng:
    - `auth_router.py`: Đăng nhập, refresh token, xác thực identity.
    - `admin_router.py`: Quản trị hệ thống, users, logs, telemetry.
    - `voice_router.py`: Cấu hình voice, upload audio, test synthesis.
    - `erp_router.py`: Quản lý nhân sự, máy trạm, KPI.
    - `system_router.py`: Health endpoints (`/livez`, `/readyz`, `/startupz`), metrics.
  - `interfaces/websocket/`:
    - `realtime_voice_endpoint.py`: WebSocket Voice nhị phân cho Web HUD & Mobile.
    - `xiaozhi_iot_endpoint.py`: WebSocket giao thức ESP32 XiaoZhi.
    - `client_agent_endpoint.py`: Kênh kiểm soát máy trạm client.

---

## III. NĂM LUỒNG DỮ LIỆU CHUẨN HOÁ (CANONICAL PIPELINES)

### 1. Canonical Voice Pipeline (Realtime Low-Latency)
```text
Microphone / Audio Input
       │
       ▼
STT Engine (Whisper / Groq API)
       │
       ▼
Voice Command (Domain Event)
       │
       ▼
FastCommandRouter (Application Layer)
 ├── Deterministic Match (Ví dụ: "tăng âm lượng", "chụp màn hình")
 │       │
 │       ▼
 │   Local System Tool ➔ Phản hồi tĩnh ngay lập tức (< 0.05ms)
 │
 └── Complex Intent (Ví dụ: "phân tích báo cáo tài chính", "giải thích quy trình")
         │
         ▼
     Tri-Brain Router (Voice Brain / Ops Brain)
         │
         ▼
     LLMProvider (Streaming Delta Token)
         │
         ▼
     SentenceBuffer (Cắt câu chuẩn tiếng Việt: dấu chấm, phẩy, chấm hỏi)
         │
         ▼
     StreamingTTSWorker (Microsoft Hoài My Neural +50% speed)
         │
         ▼
     Binary Audio Frames (Opus / MP3 Byte Stream)
         │
         ▼
     WebSocket Transport ➔ Web Audio API Gapless Player
```

### 2. Canonical Tool Execution Pipeline
```text
User Intent
     │
     ▼
SkillResolver (Dynamic Filter 10 Domains ➔ Chọn lọc 3-5 Tools cần thiết)
     │
     ▼
Tool Permission & Security Clearance Check (RBAC & Department Clearance)
     │
     ▼
Tool Executor (Infrastructure Adapter / Connector)
     │
     ▼
Audit Logging (Ghi nhận Actor, Action, Target, Status vào Audit Repository)
     │
     ▼
Execution Result ➔ Feedback to LLM Context
```

### 3. Canonical Persistence Pipeline
```text
Application Use Case
       │
       ▼
Repository Interface (domain/repository_ports)
       │
       ▼
Database Adapter (infrastructure/database)
 ├── Staging / Production: PostgreSQL Connection Pool (asyncpg / psycopg3)
 └── Local / Edge / Test: SQLite WAL Adapter
```

### 4. Canonical External Connector Pipeline
```text
Application Workflow
       │
       ▼
Connector Interface
       │
       ▼
Connector Adapter (AWS, OCI, Telegram, ERP)
 ├── Circuit Breaker (Đóng mạch khi fail quá 5 lần trong 60s)
 ├── Strict Timeout (Mặc định 5s - 10s tùy dịch vụ)
 ├── Exponential Backoff Retry (Tối đa 3 lần cho lỗi mạng tạm thời)
 └── Structured Error Mapping
```

### 5. Canonical LLM Pipeline
```text
Application Agent
       │
       ▼
LLMProvider (Interface)
       │
       ├─► DeepSeekAdapter (Streaming, Reasoning)
       ├─► GroqAdapter (Ultra-fast inference)
       ├─► OpenAIAdapter (General capability)
       └─► 9RouterAdapter (Gateway compatibility)
```

---

## IV. MỘT VOICE PIPELINE, NHIỀU TRANSPORT

Năm kênh voice hiện có (portal, HUD, ESP32, mic máy chủ, REST) phải dùng chung **một** use case xử lý lượt nói. Khác biệt giữa các kênh chỉ nằm ở tầng transport:

```text
 web/app.js      web/hud.js     ESP32 (XiaoZhi)    Mic máy chủ       REST
     │               │                │                  │              │
 /ws/v1/voice-stream │       /api/v1/xiaozhi/ws    voice_controller   /api/v1/voice-command
     │               │                │            (thu âm / phát loa)  │
     └───────┬───────┘          XiaoZhi adapter          │              │
             │                  (Opus, LCD, pairing)     │              │
             ▼                        │                  │              │
   ┌──────────────────────────────────▼──────────────────▼──────────────▼──┐
   │ ProcessVoiceTurn (một implementation)                                 │
   │  FastCommandRouter → LLMProvider.stream → SentenceBuffer               │
   │  → StreamingTTSWorkerPipeline(TTSStreamEngine) → audio sink của kênh   │
   │  lịch sử: MemoryManager + history_pruner · hủy: cancellation token     │
   └────────────────────────────────────────────────────────────────────────┘
```

## V. ÁNH XẠ TỪ CODE ĐANG CHẠY SANG TẦNG ĐÍCH

Chỉ di chuyển **bản canonical**. Bản trùng được gộp vào canonical ngay trong `core/` trước (Phase B), sau đó mới di chuyển — để không lúc nào tồn tại hai implementation chạy song song ở hai cây thư mục.

| Code canonical hiện tại | Tầng đích |
|---|---|
| `core/realtime_voice_ws.py` (phần xử lý lượt nói), `core/agent_voice_loop.py` | `application/voice/` |
| `core/realtime_voice_ws.py` (phần nhận/gửi frame), `core/audio/binary_transport.py` | `interfaces/websocket/voice` |
| `core/xiaozhi_gateway.py` (phần giao thức thiết bị) | `interfaces/websocket/device` + `infrastructure/websocket/xiaozhi` |
| `core/audio/sentence_buffer.py`, `sentence_streamer.sanitise_for_tts` | `application/voice/` (thuần Python) |
| `core/audio/tts_stream_engine.py`, `tts_queue_pipeline.py`, `audio_cache.py` | `infrastructure/tts/` |
| `core/audio_processor.py` (STT + VAD) | `infrastructure/stt/` |
| `core/fast_command_router.py` | `application/commands/` (handler gọi tool qua port, không gọi `psutil` trực tiếp) |
| `core/llm_provider.py`, `core/connection_pool.py` | `infrastructure/llm/` |
| `core/llm_engine.py` (prompt, agent loop, Tri-Brain) | `application/agent/` |
| `core/dynamic_skill_router.py`, `plugin_manager.py`, `plugin_registry.py` (sau khi gộp) | `application/skills/` + `infrastructure/plugins/` |
| `core/auth_manager.py`, `zero_trust.py`, `safety_guard.py`, `security_guard.py` (sau khi gộp) | `application/security/` + `infrastructure/security/` |
| `core/database.py`, `db_manager.py` (sau khi gộp) | `infrastructure/database/` sau repository port |
| `core/memory_manager.py`, `history_pruner.py` | `application/conversation/` + `infrastructure/cache/` |
| `core/connectors/*`, `telegram_gateway.py`, `email_gateway.py`, `webhook_gateway.py` | `infrastructure/connectors/` |
| `core/server.py` | `interfaces/http/*` + `create_app()` |
| `core/config_loader.py` | `config/` |

## VI. SƠ ĐỒ KIẾN TRÚC ĐÍCH (TỔNG THỂ)

```text
                         ┌──────────────┐
                         │   Browser    │   ESP32 · Client Agent · Telegram
                         └──────┬───────┘
                                │
                         WebSocket / HTTP
                                │
                      ┌─────────▼─────────┐
                      │    Interfaces     │  http/* · websocket/voice · websocket/device · websocket/client
                      └─────────┬─────────┘
                                │
                      ┌─────────▼─────────┐
                      │   Application     │  ProcessVoiceTurn · FastCommand · Agent · ToolExecution · Policy
                      └─────────┬─────────┘
                                │
                 ┌──────────────┼───────────────┐
                 │              │               │
                 ▼              ▼               ▼
              Voice           Agent          Skills
                 │              │               │
                 └──────────────┼───────────────┘
                                │
                      ┌─────────▼─────────┐
                      │      Domain       │  entities thuần Python
                      └─────────┬─────────┘
                                │ (ports)
                      ┌─────────▼─────────┐
                      │  Infrastructure   │
                      └─┬────┬────┬────┬──┘
                        │    │    │    │
                        ▼    ▼    ▼    ▼
                       PG  Redis LLM  TTS/STT

                         Object Storage (âm thanh, tài liệu)
```
