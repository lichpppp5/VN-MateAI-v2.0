# Thành phần trùng lặp — đường voice realtime

Phase 0 (chỉ đọc), 2026-10-03. Đánh giá theo **trách nhiệm, trạng thái, caller, tác dụng phụ, phụ thuộc** — không theo tên. Những cặp "nghe giống" nhưng KHÔNG trùng được ghi riêng ở §3.

## 1. Trùng thật (cùng trách nhiệm, cả hai còn chạy)

| # | Trách nhiệm | Bản A | Bản B | Bằng chứng | Rủi ro |
|---|---|---|---|---|---|
| D1 | Xử lý một lượt thoại | `voice_turn.process_voice_turn` (stream, lệnh nhanh, câu đệm, TTS theo câu) | REST `routers/voice.voice_command` → `ask_async` (không stream, không lệnh nhanh, TTS cả đoạn, Base64) | `routers/voice.py:117`; caller `web/app.js:533`, `web/hud.js:2173` | Cùng câu hỏi, khác hành vi tuỳ kênh |
| D2 | Lượt thoại mic máy chủ khi lõi trả rỗng | `process_voice_turn` | `voice_controller._get_llm_response_with_history_sync` → `llm_engine.process_voice_command_sync` → `ask` | `voice_controller.py:685` | Đường LLM thứ hai, không qua TTS theo câu |
| D3 | Chọn tool cho lệnh vận hành | `stream_voice_response` (router, 5 tool) | `ask_async` (82 tool) — chọn LẠI sau khi stream đã chọn | `llm_engine.py`, nhánh `if has_tool_calls:` | +1 lần gọi LLM mỗi lệnh vận hành; prompt lớn |
| D4 | Giao thức sự kiện voice cho trình duyệt | `/ws/v1/voice-stream`: `session_started, status, text_delta, sentence_ready, audio_start, audio_stream_complete, cancelled, error, session_ended` | `/ws/hud`: `voice_active, voice_state, thinking, …` | `realtime_voice_ws.py`, `hud_voice.py` | Hai client phải hiểu hai schema; sửa một bên, quên bên kia |
| D5 | Lên lịch phát MP3 bằng Web Audio | `web/app.js` `StreamingAudioQueue` | `web/hud.js` `HudAudioQueue` | `app.js:3385`, `hud.js:275` | Sửa lỗi phát ở một trang không áp dụng trang kia |
| D6 | Đọc số liệu phần cứng + đo 9Router định kỳ | `health_monitor` (worker 3 s / 30 s → `SYSTEM_HEALTH_CACHE`) | `autonomous_sentinel` (tự gọi psutil, tự đo 9Router) | `health_monitor.py:221+`, `autonomous_sentinel.py:170` | Hai vòng đo cùng thứ |
| D7 | Dựng client LLM | `llm_provider` | `llm_engine` (3 chỗ), `ai_delegation` (1) | baseline RULE-011 | Cấu hình timeout/pool có thể lệch |

## 2. Trùng nhẹ / cùng việc làm nhiều lần trong một lượt

| # | Việc | Ghi chú |
|---|---|---|
| L1 | `classify_intent` 3 lần/lượt | rẻ (< 1 ms), nhưng nên tính một lần |
| L2 | Hàng đợi base64 `hudSpeechQueue` cạnh `HudAudioQueue` | chỉ phục vụ REST dự phòng và gói cũ có `audio_base64` |
| L3 | Bí danh `/ws/voice` | cùng handler; không client web nào gọi |
| L4 | Bí danh robot `/ws/audio-stream[/{id}]` | cùng handler với `/api/v1/xiaozhi/ws`; giữ cho firmware cũ |
| L5 | STT robot gọi ở 3 nhánh định dạng gói (`xiaozhi_gateway.py:699, 799, 902`) | cùng hàm `transcribe_audio`; logic chuẩn bị audio lặp ở 3 chỗ |

## 3. Nghe giống nhưng KHÔNG trùng (giữ)

| Cặp | Vì sao khác |
|---|---|
| `fast_command_router` ↔ `skill_router` ↔ `classify_intent` | trả lời tất định không LLM ↔ chọn tool cho LLM ↔ chọn "bộ não" / có cần câu đệm |
| `tts_stream_engine` ↔ `tts_queue_pipeline` ↔ `acoustic_ack` | nhà cung cấp TTS ↔ hàng đợi/thứ tự/huỷ ↔ câu đệm làm nóng sẵn |
| `memory_manager` ↔ `voice_sessions` ↔ `state_manager` | lịch sử hội thoại ↔ cờ "đang chờ trả lời" của HUD ↔ ký ức tác vụ đã duyệt |
| `plugin_manager` ↔ `plugin_registry` | danh mục + nạp skill ↔ đường thực thi có timeout/breaker/HITL |
| `wake_word_engine` (Google STT) ↔ `transcribe_audio` | bắt từ đánh thức trong clip ngắn ↔ chép lời câu lệnh |
| Silero VAD (robot) ↔ năng lượng (mic) | khác phần cứng, khác luồng audio |
| WS `/ws/hud` ↔ `/ws/portal-ui` ↔ `/ws/topology` | ngoài phần voice của `/ws/hud`, ba kênh này phát sự kiện giao diện khác nhau |

## 4. Mã chết / chỉ test gọi

| Thành phần | Caller | Ghi chú |
|---|---|---|
| `llm_engine.chat`, `process_voice_command`, `generate_response`, `report_action_execution` | chỉ gọi lẫn nhau / không ai gọi | Dead |
| `voice_controller._get_llm_response_async`, `_get_llm_response_sync` | 0 | Dead |
| `llm_engine.stream`, `stream_tokens` | chỉ `tests/test_phase2_llm_streaming.py` | Test-only (lõi dùng `provider.stream`) |

Đã gỡ ở các phase trước (không còn tồn tại, prompt có nhắc): `sentence_streamer.py`, `streaming_tts_pipeline.py`, `core/connection_pool.py` (nay `infra/http/connection_pool.py`), nhánh gTTS, `AudioEngine.text_to_speech_*`, `_sanitise_for_tts` / `clean_text_for_tts`, `_call_llm_router`.


## 5. Trạng thái sau P2–P6 (chạy lại kiểm tra 2026-10-03)

Kiểm lại bằng tìm caller tĩnh trong `src/`, `web/`, `skills/`, `client_agent/`, `scripts/`, `tests/` + chạy thật.

| Mục | Trạng thái | Bằng chứng |
|---|---|---|
| D1 REST → `ask_async` | **ĐÃ GỘP** — REST chạy `process_voice_turn` | `routers/voice.py` không còn gọi `ask_async`; `ask_async` chỉ còn: nhánh tool của `stream_voice_response`, Telegram (kênh chữ, không phải thoại) |
| D2 mic → `process_voice_command_sync` | **ĐÃ GỠ** | hàm không còn tồn tại; lượt rỗng → câu xin lỗi cố định |
| D3 chọn tool hai lần | **ĐÃ GỘP** | `ask_async(first_tool_calls=…)`; đo: 1 lần gọi LLM với 7 tool thay vì 2 lần (một lần 82 tool) |
| D4 hai schema sự kiện (portal / HUD) | **CÒN** | cần viết lại phần nhận sự kiện của `hud.js` + kiểm trên trình duyệt — xem owner-todo |
| D5 hai bộ phát audio JS | **ĐÃ GỘP** — `web/voice-audio-queue.js` | `StreamingAudioQueue`, `HudAudioQueue` không còn; test `tests/test_voice_audio_queue.mjs` |
| D6 sentinel tự đo lại | **ĐÃ GỘP** | sentinel đọc `SYSTEM_HEALTH_CACHE`; không còn import psutil/httpx |
| D7 dựng client LLM nhiều nơi | **ĐÃ GỘP** — `llm_provider.make_llm_client` | RULE-011 baseline `{}` |
| L1 `classify_intent` 3 lần | **ĐÃ GỘP** (1 lần/lượt thoại) | `turn["intent"]` |
| L2 hàng đợi base64 HUD | **GIỮ** | còn dùng cho REST dự phòng của HUD (`audio_base64`) |
| L3 `/ws/voice` | **ĐÃ GỠ** | chạy thật: bị từ chối |
| L4 `/ws/audio-stream` | **GIỮ** | firmware cũ |
| L5 STT robot 3 nhánh | **MỘT PHẦN** — STT + đo thời gian gộp ở `_transcribe` (Phase 1); 3 nhánh gửi thông điệp khác nhau theo firmware, gộp tiếp cần robot thật |
| §4 mã chết `llm_engine` / `voice_controller` | **ĐÃ GỠ** | `chat`, `generate_response`, `process_voice_command(_sync)`, `report_action_execution`, `ask`, `stream`, `stream_tokens`, `_get_client`, `_make_client`, `_get_llm_response_*` |
