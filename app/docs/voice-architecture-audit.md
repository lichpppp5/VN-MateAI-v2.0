# BÁO CÁO KIỂM TOÁN KIẾN TRÚC VOICE PIPELINE (PHASE 0)

> **Đã được thay (2026-10-05)** bởi `docs/realtime/voice-architecture.md`. Giữ để đối chiếu lịch sử; mô tả bên dưới là hiện trạng ngày 2026-10-01 (trước khi chuyển sang `src/mateai/`).
**Dự án**: VN-MateAI — Realtime Voice Performance Revamp
**Thời gian kiểm toán**: 2026-10-01
**Mục tiêu**: Phân tích toàn diện hiện trạng hệ thống Voice Pipeline, nhận diện các điểm nghẽn độ trễ và chuẩn bị nền tảng chuyển dịch sang chuẩn thời gian thực (Jarvis / XiaoZhi-like responsiveness).

---

## 1. TỔNG QUAN KIẾN TRÚC BACKEND & ENTRYPOINT

* **Framework backend**: FastAPI (Asynchronous ASGI) kết hợp `uvicorn`.
* **Entrypoint chính**: `main.py` -> khởi chạy `core.server:app` trên cổng `8000`.
* **Mô hình kết nối**:
  * REST API: `POST /api/v1/voice-command` (text-based payload từ STT phía client/browser).
  * WebSockets hiện tại:
    * `/ws/v1/voice-stream`: WebSocket streaming voice handler (`core/api_voice_stream.py`).
    * `/ws/hud`: WebSocket HUD broadcast cho desktop display/overlay (`core/server.py`).
    * `/ws/client`: WebSocket LAN worker nodes (`core/orchestrator.py`).
    * `/ws/xiaozhi`: WebSocket IoT gateway cho thiết bị phần cứng ESP32 XiaoZhi (`core/xiaozhi_gateway.py`).
* **Xác thực & Phân quyền**:
  * Zero-Trust Security Module: `core/zero_trust.py` + `core/safety_guard.py` (`security_engine`).
  * WebSocket Auth: Token query param hoặc Bearer token qua `_authenticate_websocket()` trong `core/server.py`.
  * RBAC Guard: `core/rbac_guard.py` kiểm soát quyền thực thi từng công cụ theo vai trò.

---

## 2. HIỆN TRẠNG PIPELINE VÀ CÁC ĐIỂM NGHẼN ĐỘ TRỄ

### Sơ đồ luồng cũ (REST / Non-streaming / Blocking)
```text
User speaks
    ↓ [Web Speech API / Mic]
STT (Client-side)
    ↓ [HTTP POST /api/v1/voice-command] [NETWORK]
FastAPI Server Receive
    ↓
Zero-Trust Masking & RBAC
    ↓
Tool/Skill Schema Enrichment (Nạp hàng chục schema) [PAYLOAD OVERHEAD]
    ↓
LLM Call (Non-streaming hoặc đợi hoàn tất) [LLM LATENCY: 2.0s - 4.0s]
    ↓
Tool Calling Execution (Nếu có) [TOOL LATENCY: 0.5s - 2.0s]
    ↓
LLM Synthesis Round 2 [LLM LATENCY: 1.5s - 3.0s]
    ↓
Text Sanitisation (_sanitise_for_tts)
    ↓
Edge-TTS Full Audio Generation (MP3) [TTS LATENCY: 1.5s - 2.5s]
    ↓
Convert MP3 → Base64 Encoding [CPU & PAYLOAD OVERHEAD]
    ↓
Return JSON Response to Browser
    ↓ [Browser Decode Base64 → Audio Blob → Play]
User nghe thấy âm thanh đầu tiên (TTFA: ~6.0s - 12.0s) [BLOCKING]
```

### Điểm nghẽn (Bottlenecks) được xác định:
1. **[BLOCKING] TTFA phụ thuộc vào toàn bộ chu trình**: Người dùng phải chờ đợi toàn bộ chuỗi: STT -> LLM 1 -> Tool -> LLM 2 -> Full TTS -> Base64 encode -> Download.
2. **[PAYLOAD OVERHEAD] Nạp schema công cụ dư thừa**: Nạp toàn bộ 78+ công cụ vào context của câu chào hỏi giao tiếp thông thường, làm tăng thời gian suy luận (TTFT).
3. **[BASE64 OVERHEAD] Chuỗi Base64 cồng kềnh**: Đóng gói âm thanh MP3 thành Base64 string trong JSON làm tăng dung lượng gói tin 33% và tốn CPU encode/decode.
4. **[LACK OF CANCEL] Không có cơ chế Barge-In/Cancellation**: Khi người dùng ngắt lời hoặc gửi lệnh mới, tác vụ cũ vẫn tiếp tục chạy ngầm tiêu tốn token và tài nguyên server.

---

## 3. PHÂN TÍCH THÀNH PHẦN VOICE & LLM

| Thành phần | File nguồn | Đặc tính hiện tại | Hướng tối ưu |
| :--- | :--- | :--- | :--- |
| **STT Engine** | `web/app.js`, `web/hud.js`, `core/audio_processor.py` | Web Speech API trên Browser + VAD cục bộ | Giữ STT client, chuyển sang gửi delta/final qua WebSocket |
| **Voice Router** | `core/server.py`, `core/api_voice_stream.py` | HTTP endpoint + `/ws/v1/voice-stream` | Nâng cấp nền tảng WebSocket Event Protocol chuẩn thời gian thực |
| **LLM Engine** | `core/llm_engine.py` | Tri-Brain Architecture (Supervisor, Voice, Ops) + Fast Failover | Hỗ trợ Token Stream qua Event Bus (Phase 2) |
| **TTS Engine** | `core/audio_processor.py`, `core/audio/tts_stream_engine.py` | Edge-TTS + Pre-warmed Acoustic Cache | Pipeline gối đầu từng câu -> Binary Frame (Phase 3 & 4) |
| **Tool Execution** | `core/plugin_manager.py`, `core/plugin_registry.py` | Async tool execution, Zero-Trust | Fast Path cho lệnh tất định (Phase 5) |

---

## 4. KẾT LUẬN KIỂM TOÁN
Hệ thống VN-MateAI đã có sẵn hạ tầng WebSocket FastAPI mạnh mẽ và các module xử lý stream ban đầu (`SentenceStreamer`, `tts_stream_engine`). Việc chuyển dịch sang trải nghiệm thời gian thực (Jarvis-like) cần được thực hiện qua từng phase nghiêm ngặt:
* **Phase 1**: Chuẩn hóa tầng Realtime WebSocket Protocol (Session, Tracing, Status, Binary frames, Cancellation).
* **Các Phase tiếp theo**: LLM streaming token, Sentence Buffer tiếng Việt, TTS streaming worker, Fast Command Router.
