# Thành phần canonical — đường voice realtime

Phase 0 (chỉ đọc), 2026-10-03. Chi tiết đường gọi: `call-graph.md`. Trạng thái: **Canonical** (bản chính, đang chạy) · **Alias** (cùng code, tên khác) · **Active-alt** (đường thay thế còn chạy) · **Dead** (không ai gọi) · **Test-only**.

| Chức năng | Canonical | Bản / đường khác | Caller | Chạy thật | Trạng thái | Action |
|---|---|---|---|---|---|---|
| Lượt thoại (use case) | `app/voice/voice_turn.process_voice_turn` | REST `/api/v1/voice-command` → `ask_async`; mic máy chủ dự phòng → `process_voice_command_sync` | portal WS, HUD WS, robot, mic | có | Canonical + 2 Active-alt | MERGE: REST và dự phòng mic đi qua `process_voice_turn` (REST chỉ còn là adapter) |
| STT | `infra/audio/audio_processor.AudioEngine.transcribe_audio` | Web Speech (trình duyệt — client, không trùng); Google STT trong `wake_word_engine` (chỉ bắt từ đánh thức) | robot, mic | có | Canonical | KEEP |
| VAD | Silero trong `xiaozhi_gateway` (robot) | ngưỡng năng lượng `speech_recognition` (mic máy chủ); trình duyệt tự xử lý | robot, mic | có | Canonical theo thiết bị | KEEP (khác phần cứng) |
| Lệnh nhanh | `app/commands/fast_command_router` | — | `voice_turn` | có | Canonical | KEEP; REST chưa đi qua (do REST chưa dùng `voice_turn`) |
| Phân loại ý định | `llm_engine.classify_intent` | — (gọi 3 lần/lượt) | voice_turn, stream_voice_response, ask_async | có | Canonical | Tính 1 lần, truyền qua `turn` |
| Chọn tool | `app/skills/skill_router.dynamic_skill_router` | `ask_async` dùng toàn bộ danh mục | stream_voice_response | có | Canonical | Vòng agent dùng lại lựa chọn (xem migration-plan P3) |
| LLM provider | `infra/llm/llm_provider` (`stream`, `complete`, `complete_text_blocking`) | `llm_engine` tự dựng 3 `AsyncOpenAI`; `ai_delegation` 1; `routers/config` 3 (nút thử — có lý do) | mọi lời gọi LLM | có | Canonical + client dựng rải rác | MERGE việc dựng client vào provider/pool |
| LLM stream API cũ | — | `llm_engine.stream`, `stream_tokens` | chỉ `tests/test_phase2_llm_streaming.py` | không | Test-only | DELETE sau khi đổi test sang `provider.stream` |
| LLM hàm cũ | — | `llm_engine.chat`, `process_voice_command`, `generate_response`, `report_action_execution`; `voice_controller._get_llm_response_async`, `_get_llm_response_sync` | không caller ngoài chuỗi chính chúng | không | Dead | DELETE |
| Pool HTTP | `infra/http/connection_pool` (LLM / STT / TTS / chung) | — | llm_engine, audio_processor, tts | có | Canonical | KEEP |
| Tách câu | `app/voice/sentence_buffer.SentenceBuffer` (+ `speech_text.CodeFenceStripper`) | — (`sentence_streamer.py` đã gỡ) | stream_voice_response | có | Canonical | KEEP |
| Làm sạch lời đọc | `app/voice/speech_text.sanitise_for_tts` | — | mọi kênh | có | Canonical | KEEP |
| TTS engine | `infra/tts/tts_stream_engine.TTSStreamEngine` | skill `ninerouter_skills` gọi `/audio/speech` riêng | mọi kênh | có | Canonical | KEEP; skill: REVIEW |
| Hàng đợi TTS | `infra/tts/tts_queue_pipeline.StreamingTTSWorkerPipeline` | — (`streaming_tts_pipeline.py` đã gỡ) | voice_turn | có | Canonical | Giới hạn hàng đợi audio đầu ra |
| Câu đệm / ACK cache | `infra/tts/acoustic_ack` + `acoustic_ack_catalog` + `audio_cache` | — | voice_turn | có | Canonical | KEEP |
| Truyền audio nhị phân | `infra/websocket/binary_transport.dispatch_binary_audio` (portal); `broadcast_hud_binary` (HUD); PCM (robot) | Base64 ở REST, `confirm-action`, gói HUD cũ | — | có | Canonical theo kênh | Base64 chỉ còn ở biên REST |
| WS voice trình duyệt | `/ws/v1/voice-stream` (`ifc/websocket/realtime_voice_ws`) | `/ws/voice` (Alias, không client web); `/ws/hud` (schema riêng) | portal / HUD | có | 2 giao thức | MERGE: HUD dùng sự kiện của `/ws/v1/voice-stream` |
| WS thiết bị | `/api/v1/xiaozhi/ws[/{id}]` | `/ws/audio-stream[/{id}]` (Alias cho firmware cũ) | robot | có | Canonical + Alias | KEEP alias tới khi firmware cũ không còn |
| Hàng đợi phát (trình duyệt) | — | portal `StreamingAudioQueue`; HUD `HudAudioQueue` (cùng logic, hai bản) + `hudSpeechQueue` (base64) | — | có | Trùng ở frontend | MERGE sang một module JS dùng chung |
| Huỷ / barge-in | `RealtimeVoiceSession.cancel_active_turn` (portal); `hud_voice.cancel_task` (HUD); `xiaozhi_gateway.handle_barge_in` (robot) | — | — | có | Một cơ chế mỗi transport | Thống nhất vào sink/turn |
| Lịch sử hội thoại | `app/conversation/memory_manager` (cửa sổ 14 tin; thoại: `prune_history_for_voice` 4 lượt / 1.200 ký tự) | — | mọi kênh | có | Canonical | Thêm đếm token |
| Trạng thái chờ trả lời (HUD) | `app/voice/voice_session.voice_sessions` | — | hud_voice | có | Canonical | KEEP |
| Cổng thực thi tool | `app/agent/tool_gate.run_tool_with_policy` | — | ask_async, fs, clients, HITL executor | có | Canonical | KEEP |
| Danh mục tool | `core/plugin_manager` | `plugin_registry` (chỉ đường thực thi: timeout, breaker, HITL) | — | có | Canonical | KEEP |
| Hàng đợi duyệt | `app/security/zero_trust.hitl_manager` | — | mọi kênh | có | Canonical | KEEP |
| Giám sát nền | `app/operations/health_monitor` (cache số liệu) | `autonomous_sentinel` cũng tự đọc psutil + đo 9Router | — | có | Chồng trách nhiệm | REVIEW: sentinel đọc cache của health_monitor |
| Trace / số đo | `realtime_voice_ws.VoiceRequestTrace` (request_id, trace_id, TTFD/TTFT/TTFA/TTL) | HUD, robot, mic, REST: chỉ log | portal | một phần | Chưa thống nhất | Đưa trace vào `voice_turn` (mọi kênh) |
