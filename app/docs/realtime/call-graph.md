# Call graph — đường voice thực tế

Phase 0 (chỉ đọc), 2026-10-03, commit `71efe78`. Lần theo code + đường gọi thật, không suy từ tên tệp. Đường dẫn rút gọn: `app/` = `src/mateai/application`, `infra/` = `src/mateai/infrastructure`, `ifc/` = `src/mateai/interfaces`.

## 0. Đính chính 2026-10-05 (commit `128c87d`) — đọc trước các mục dưới

Các mục 1–6 dưới đây giữ nguyên bản 2026-10-03 để đối chiếu lịch sử. Những điểm sau **đã thay đổi**, kiểm lại bằng mã:

| Bản 2026-10-03 ghi | Hiện trạng 2026-10-05 | Bằng chứng |
|---|---|---|
| Portal / HUD nhận dạng bằng Web Speech API trong trình duyệt | Portal ghi âm WAV 16 kHz rồi gửi **STT máy chủ** `POST /api/v1/voice/transcribe` (manager/admin); HUD chưa đổi | `routers/voice.py` (transcribe), `web/app.js` `toggleBrowserSpeechRecognition` |
| REST `/api/v1/voice-command` đi `ask_async` riêng | Đi `process_voice_turn` (lõi chung) | `routers/voice.py:135` |
| Mic máy chủ có lối dự phòng LLM thứ hai (`process_voice_command_sync`) | Đã gỡ | `voice_controller.py:691` (chú thích) |
| Bí danh `/ws/voice` | Đã gỡ — còn 9 route WS | `routers/websockets.py` |
| Hàng đợi audio đầu ra không giới hạn | Có giới hạn (`maxsize=max(2, max_audio_buffered)`) | `tts_queue_pipeline.py:110–111` |
| System prompt ~11 300 ký tự mọi lượt | Prompt vòng agent **10 603** ký tự; prompt hội thoại thoại **3 523** ký tự (đo `build_system_prompt` ngày 2026-10-05) | `llm_engine.py:192` |
| — | Robot có thêm: kiểm tra âm thanh, báo trạng thái, lệnh âm lượng / khởi động lại (firmware 54, chưa nạp) | `routers/robots.py`, `xiaozhi_gateway.py` |
| — | Kết quả tool gửi về portal: `audio_stream_complete.tools` (tên, đích, trạng thái, mã duyệt — không gửi tham số / kết quả) | `realtime_voice_ws.tool_summary` |

Đường tool và kiểm soát (tool_gate → rủi ro → HITL → RBAC) xem `docs/architecture/current-vs-target.md` §2–§3. Bản tổng hợp kiến trúc thoại mới nhất: `docs/realtime/voice-architecture.md`.

## 1. Năm điểm vào voice

| Kênh | Client | Endpoint | STT | Use case |
|---|---|---|---|---|
| Portal (trang quản trị) | `web/app.js:3670` | WS `/ws/v1/voice-stream` | Web Speech API trong trình duyệt | `process_voice_turn` |
| HUD | `web/hud.js` | WS `/ws/hud` (`action: voice_command`) | Web Speech API trong trình duyệt | `process_voice_turn` |
| Robot ESP32 / Xiaozhi | firmware | WS `/api/v1/xiaozhi/ws/{id}` (+ bí danh `/ws/audio-stream/{id}`, bản không id) | Máy chủ: Silero VAD → `AudioEngine.transcribe_audio` (faster-whisper / Groq / Whisper), theo lô sau khi VAD báo hết câu | `process_voice_turn` |
| Mic máy chủ (wake word) | — | luồng nền trong `main.py` | `wake_word_engine` (Google STT, chỉ để bắt từ đánh thức) → `transcribe_audio` | `process_voice_turn` (+ lối dự phòng riêng, §5) |
| REST | `web/app.js:533`, `web/hud.js:2173` (dự phòng khi WS HUD rớt) | `POST /api/v1/voice-command` | — (văn bản) | `llm_engine.ask_async` — **không** qua `process_voice_turn` |

`/ws/voice` là bí danh của cùng handler `/ws/v1/voice-stream`; trình duyệt không gọi nó (chỉ test và `scripts/bench_voice.py`).

## 2. Lõi chung: `app/voice/voice_turn.process_voice_turn`

```
transport (WS / mic / robot)
  → process_voice_turn(query, sink, session_id, source_device, caller)
     1. fast_command_router.dispatch(query)                         app/commands/fast_command_router.py
          khớp → câu trả lời tất định → cache/TTS → sink.on_audio → XONG (không LLM)
     2. llm_engine.classify_intent(query)                            app/agent/llm_engine.py
          "operation" → câu đệm từ cache (acoustic_ack) → sink.on_audio("ack")   [turn.acked]
     3. lời đệm sau filler_after_s nếu chưa có câu nào (HUD 1 s, mic 18 s)        [turn.acked]
     4. llm_engine.stream_voice_response(...)  ─┐ (producer)
        StreamingTTSWorkerPipeline (2 worker) ──┤ infra/tts/tts_queue_pipeline.py
        iterate_audio_results → sink.on_audio ─┘ (consumer, đúng thứ tự câu)
```

### 2.1 `stream_voice_response` (app/agent/llm_engine.py)

```
classify_intent(query)            ← tính lần 2 (lần 1 ở voice_turn)
"conversation" → 0 tool (khớp yếu một skill → 3 skill gần nhất)
"operation"    → dynamic_skill_router.get_tools_for_query(query, 5)
admin & không skill khớp rõ → thêm create_new_skill
build_system_prompt(source_device)   ≈ 11.300 ký tự (~3.700 token) — mọi lượt
provider = get_provider(role) → provider.stream(messages, tools)        infra/llm/llm_provider.py
  token  → SentenceBuffer.add_token (CodeFenceStripper, tách câu an toàn)  app/voice/sentence_buffer.py
         → sanitise_for_tts → yield câu
  tool_call → (câu xác nhận nếu chưa có) → BREAK stream
           → ask_async(query, ...)    ← BẮT ĐẦU LẠI TỪ ĐẦU (xem §4)
```

### 2.2 `ask_async` (vòng agent)

```
plugin_manager.get_all_tools()       ← TOÀN BỘ ~82 tool
classify_intent(query)               ← lần 3
"đồng ý"/"huỷ" → hitl_manager (hàng đợi duyệt duy nhất)
for round in range(6):
   _call_llm(messages, tools) → provider.complete (không stream)
   tool_calls → song song: tool_gate.run_tool_with_policy (Zero-Trust, HITL, RBAC, audit)
             → plugin_manager.execute_skill / plugin_registry / máy trạm
   stop → _extract_dual_channel → trả {reply, speech_reply}
hết vòng → _call_llm(tools=None) tổng hợp
```

## 3. LLM, TTS, STT — nơi thực sự gọi

**LLM** (mọi lời gọi thật đi qua `infra/llm/llm_provider.py`):

| Gọi từ | Hàm | Stream? |
|---|---|---|
| `stream_voice_response` | `provider.stream` | có |
| `ask_async` (vòng agent, tổng hợp sau duyệt, tổng hợp khi hết vòng) | `_call_llm → provider.complete` | không |
| `routers/security.confirm_action_endpoint` | `llm_engine._call_llm` | không |
| `MetaArchitect.synthesize_skill`, `analytics_engine` | `complete_text_blocking` | không (luồng phụ) |
| `skills/builtin/ai_delegation` | client riêng + `provider.complete` | không |
| `routers/config` (nút "thử kết nối LLM") | `client.chat.completions.create` | không — có chủ đích (thử cấu hình chưa lưu) |

Client `AsyncOpenAI` được dựng ở: `llm_engine` (3 chỗ: client router, client direct, `_make_client`), `ai_delegation` (1), `routers/config` (3). Test RULE-011 chặn chỗ mới.

**TTS**: một engine `infra/tts/tts_stream_engine.TTSStreamEngine` — cache RAM/đĩa → ElevenLabs (nếu cấu hình) → 9Router `/v1/audio/speech` (trả nguyên câu, không stream) → Edge-TTS stream (dự phòng). Gọi từ: `tts_queue_pipeline` (mọi lượt thoại), `voice_turn` (lời đệm, lệnh nhanh), `acoustic_ack` (làm nóng câu đệm), `ifc/http/speech.tts_bytes` (HUD sau duyệt, `/api/v1/tts`, `hud_voice.say`), `xiaozhi_gateway` (đánh thức/cảnh báo robot). Test RULE-012 chặn chỗ tổng hợp ngoài engine (ngoại lệ: skill `ninerouter_skills`).

**STT**: `infra/audio/audio_processor.AudioEngine.transcribe_audio` — gọi từ `xiaozhi_gateway` (3 nhánh định dạng gói) và `voice_controller`. Trình duyệt tự nhận dạng (Web Speech) cho portal và HUD. Google STT chỉ dùng trong `wake_word_engine` để bắt từ đánh thức.

## 4. Vòng xử lý thừa đã xác định

1. **Lệnh vận hành gọi LLM ít nhất 3 lần**: (a) `provider.stream` với 5 tool → model chọn tool → stream bị **bỏ**, lựa chọn tool không dùng; (b) `ask_async` gọi lại model với **82 tool** để chọn lại; (c) gọi model lần nữa để tổng hợp kết quả. Lần (a) chỉ dùng để phát hiện "cần tool".
2. `classify_intent` chạy 3 lần mỗi lượt (rẻ — < 1 ms — nhưng là việc lặp).
3. Vòng agent luôn LLM → tool → LLM, kể cả khi kết quả tool đủ để trả lời tất định.
4. Mỗi lượt thoại gửi system prompt ~3.700 token, kể cả câu chào.

## 5. Đường riêng còn sống ngoài lõi chung

- `POST /api/v1/voice-command` → `ask_async` (không stream, trả `audio_base64`), dùng bởi portal (`app.js:533`) và HUD khi WS rớt.
- Mic máy chủ: `voice_controller._stream_response_and_play` → khi `process_voice_turn` trả rỗng → `_get_llm_response_with_history_sync` → `llm_engine.process_voice_command_sync` → `ask` (bản đồng bộ) — đường LLM thứ hai.
- `routers/security.confirm_action_endpoint` tự tổng hợp câu trả lời + TTS + broadcast HUD sau khi duyệt (`audio_base64`).

## 6. Hàng đợi & tác vụ nền

- Máy chủ: `StreamingTTSWorkerPipeline` — hàng đợi câu có giới hạn (`maxsize`), hàng đợi audio đầu ra **không giới hạn**, 2 worker, sắp lại thứ tự. Robot có nhịp gửi PCM riêng (`_stream_audio_smooth`).
- Trình duyệt: portal `StreamingAudioQueue` (`app.js:3385`), HUD `HudAudioQueue` (`hud.js:275`) + hàng đợi base64 `hudSpeechQueue` (chỉ cho REST dự phòng / gói cũ).
- Tác vụ nền khi khởi động (`ifc/http/server._on_startup`): `ephemeral_cache` sweeper, Telegram bot, `health_monitor` (3 worker), `hud_voice.telemetry_loop`, `autonomous_sentinel`, UDP beacon, `proactive_manager`, làm nóng câu đệm, `background_worker_manager`, email gateway; `main.py`: luồng HTTPS + luồng mic/wake word.
