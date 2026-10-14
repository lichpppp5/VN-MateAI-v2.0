# Audit kiến trúc voice realtime — Phase 0

> **Đã được thay (2026-10-05)** bởi `docs/realtime/voice-architecture.md`. Giữ để đối chiếu; đính chính hiện trạng ở `call-graph.md` §0.

Phase 0 (chỉ đọc), 2026-10-03, commit `71efe78`. Không sửa, xoá, đổi tên code. Tài liệu đi kèm: `call-graph.md`, `canonical-components.md`, `duplicate-components.md`, `migration-plan.md`.

## 1. Hiện trạng tóm tắt

Pipeline đã có dạng mục tiêu ở phần lõi: lệnh nhanh tất định → câu đệm từ cache → LLM stream → tách câu an toàn → TTS theo câu (2 worker, giữ thứ tự) → audio nhị phân. Bốn kênh (portal, HUD, robot, mic máy chủ) dùng chung `process_voice_turn`. Phần còn lệch so với mục tiêu: một đường REST riêng, hai giao thức sự kiện cho trình duyệt, lệnh vận hành gọi LLM thừa một lần, prompt thoại lớn, trace chỉ có ở một kênh, trạng thái nằm trong RAM của một tiến trình.

## 2. Trả lời 12 câu hỏi bắt buộc

1. **Pipeline voice chạy qua những tệp nào?** Transport: `ifc/http/routers/websockets.py` → `ifc/websocket/realtime_voice_ws.py` (portal) / `ifc/http/hud_voice.py` (HUD) / `ifc/websocket/xiaozhi_gateway.py` (robot) / `ifc/desktop/voice_controller.py` (mic). Lõi: `app/voice/voice_turn.py` → `app/commands/fast_command_router.py` → `infra/tts/acoustic_ack.py` → `app/agent/llm_engine.py` (`stream_voice_response`, `ask_async`) → `infra/llm/llm_provider.py` → `app/voice/sentence_buffer.py` + `speech_text.py` → `infra/tts/tts_queue_pipeline.py` → `infra/tts/tts_stream_engine.py` → sink của từng transport (`infra/websocket/binary_transport.py`, `realtime_hub.broadcast_hud_binary`, PCM robot). STT robot/mic: `infra/audio/audio_processor.py`.
2. **LLM thực sự được gọi ở đâu?** Qua `llm_provider` (`stream` / `complete` / `complete_text_blocking`) từ `stream_voice_response`, `ask_async` (`_call_llm`), `confirm_action_endpoint`, `MetaArchitect`, `analytics_engine`, `ai_delegation`; ngoài provider chỉ có nút "thử kết nối" ở `routers/config` (có chủ đích). Bảng đầy đủ: `call-graph.md` §3.
3. **Có bao nhiêu LLM implementation?** Một lớp trừu tượng (`llm_provider`: router / direct / tri-brain). Nhưng client `AsyncOpenAI` được dựng ở 3 nơi ngoài provider (`llm_engine` ×3, `ai_delegation` ×1, `routers/config` ×3) và có 2 đường điều phối dùng LLM cho thoại (`stream_voice_response`, `ask_async`) cùng các hàm cũ đã chết (`duplicate-components.md` §4).
4. **TTS thực sự được gọi ở đâu?** `TTSStreamEngine.stream/synthesise` từ `tts_queue_pipeline`, `voice_turn`, `acoustic_ack`, `ifc/http/speech.tts_bytes`, `xiaozhi_gateway`. Ngoại lệ ngoài engine: skill `ninerouter_skills` (một chỗ, nằm trong baseline RULE-012).
5. **Có bao nhiêu TTS implementation?** Một engine + một hàng đợi. Không còn pipeline TTS thứ hai tự nhận request.
6. **Có bao nhiêu WebSocket implementation?** 10 đường WS, 7 handler. Riêng voice: 3 giao thức — `/ws/v1/voice-stream` (+ bí danh `/ws/voice`), voice trong `/ws/hud`, giao thức thiết bị Xiaozhi (`/api/v1/xiaozhi/ws`, bí danh `/ws/audio-stream`). Hai trong số đó phục vụ trình duyệt với schema khác nhau.
7. **Có bao nhiêu Audio Queue?** Máy chủ: 1 (`StreamingTTSWorkerPipeline`, hàng đợi câu có giới hạn, hàng đợi audio ra không giới hạn) + nhịp gửi PCM của robot. Trình duyệt: 3 (`StreamingAudioQueue` portal, `HudAudioQueue` + `hudSpeechQueue` HUD).
8. **Có bao nhiêu Fast Router?** Một: `fast_command_router` (chỉ `voice_turn` gọi). `skill_router` và `classify_intent` là việc khác (chọn tool / chọn bộ não).
9. **Có bao nhiêu background worker?** 12 nhóm khởi động cùng máy chủ (`call-graph.md` §6). Chồng trách nhiệm: `health_monitor` ↔ `autonomous_sentinel` cùng đọc psutil và đo 9Router.
10. **Có implementation bị thay thế nhưng vẫn được gọi?** Có: REST `/api/v1/voice-command` (đi `ask_async` thay vì `process_voice_turn`); dự phòng mic máy chủ qua `process_voice_command_sync`; hàng đợi base64 của HUD cho REST dự phòng.
11. **Có implementation hoàn toàn dead?** Có: `llm_engine.chat / process_voice_command / generate_response / report_action_execution`, `voice_controller._get_llm_response_async / _get_llm_response_sync`; `llm_engine.stream / stream_tokens` chỉ test gọi. Bí danh `/ws/voice` không có client web.
12. **Có vòng xử lý không cần thiết?** Có: lệnh vận hành gọi LLM ≥ 3 lần (stream chọn tool → bỏ → `ask_async` chọn lại với 82 tool → tổng hợp); `classify_intent` 3 lần/lượt; vòng agent luôn tổng hợp bằng LLM kể cả khi kết quả tool đủ để trả lời tất định.

## 3. Số đo thật (baseline, 2026-10-03 12:26, `scripts/bench_voice.py --ws wss://localhost`)

Máy chủ Windows 10 / Python 3.11.9, LLM qua 9Router (`ag/gemini-3.8-flash-low`), TTS 9Router → Edge. Đo phía client bằng `time.perf_counter()`. WebSocket: 3 lượt mỗi loại — **mẫu nhỏ, p95 không đáng tin**.

| Chỉ số | n | p50 | p95 | max |
|---|---:|---:|---:|---:|
| Lệnh nhanh `dispatch` (trong tiến trình, 5 lệnh) | 1000 | 0,011 ms | 51,2 ms | 52,0 ms |
| — riêng "kiểm tra cpu" (đo CPU thật) | 200 | 50,9 ms | 51,5 ms | 52,0 ms |
| `SentenceBuffer` một luồng token giả lập | 200 | 0,87 ms | 0,89 ms | 2,6 ms |
| TTS engine — đoạn audio đầu, câu mới (không cache) | 3 | 2.643 ms | 3.329 ms | 3.405 ms |
| WS lệnh nhanh — sự kiện đầu | 3 | 2,8 ms | 2,8 ms | 2,8 ms |
| WS lệnh nhanh — chữ đầu | 3 | 7,0 ms | 48,4 ms | 53,0 ms |
| WS lệnh nhanh — **tiếng đầu** | 3 | 1.028 ms | 1.180 ms | 1.197 ms |
| WS câu cần LLM — sự kiện đầu | 3 | 2,7 ms | 2,7 ms | 2,7 ms |
| WS câu cần LLM — tiếng đầu (câu đệm từ cache) | 3 | 6,3 ms | 6,3 ms | 6,3 ms |
| WS câu cần LLM — **chữ đầu từ LLM** | 3 | 2.899 ms | 2.940 ms | 2.945 ms |
| WS câu cần LLM — tổng | 3 | 4.715 ms | 5.783 ms | 5.901 ms |

Chưa đo: STT (robot/mic), lệnh vận hành có tool, đồng thời 10/50/100 phiên, bộ nhớ sau 100 lượt.

## 4. Nút thắt (xếp theo đóng góp vào độ trễ người dùng cảm nhận)

| # | Nút thắt | Bằng chứng | Đóng góp |
|---|---|---|---|
| B1 | LLM ra chữ đầu chậm | chữ đầu p50 2,9 s; prompt thoại ~3.700 token mọi lượt | lớn nhất cho câu cần LLM |
| B2 | TTS câu động chậm | 9Router trả nguyên câu (không stream) / Edge chunk đầu p50 3,8 s (docstring engine) ; engine đo hôm nay 2,6 s | +1–3 s sau chữ đầu; lệnh nhanh 1,0 s chỉ vì TTS |
| B3 | Lệnh vận hành thừa một lần gọi LLM với 82 tool | `call-graph.md` §4 | +1 vòng LLM (chưa đo riêng) |
| B4 | STT robot theo lô sau khi VAD hết câu | `xiaozhi_gateway` | chưa đo |
| B5 | Hàng đợi audio ra không giới hạn | `tts_queue_pipeline.py:106` | rủi ro bộ nhớ khi client chậm, không phải độ trễ |

Không phải nút thắt (đo được < 3 ms): sự kiện đầu WS, câu đệm từ cache, tách câu, lệnh nhanh (trừ phần TTS), chọn tool.

## 5. Kiến trúc mục tiêu (trong khuôn modular monolith hiện có)

```
transport (WS portal / WS HUD / robot / mic / REST adapter)
   └─ một giao thức sự kiện cho trình duyệt (schema của /ws/v1/voice-stream)
        └─ process_voice_turn  ← điểm vào DUY NHẤT (REST thành adapter)
             ├─ fast_command_router (tất định, không LLM)
             ├─ intent (tính 1 lần) → ACK cache
             ├─ Voice Brain: prompt nhỏ, 0–3 tool, provider.stream
             └─ Ops Brain: lựa chọn tool của lần stream được dùng luôn
                   → tool_gate → (trả lời tất định nếu đủ | LLM tổng hợp stream)
             → SentenceBuffer → TTS queue (giới hạn cả hai đầu) → sink
   trace (request_id, TTFD/TTFT/TTFA/TTL) trong voice_turn cho MỌI kênh
```

Không thêm hạ tầng (Redis, tách tiến trình) cho tới khi có nhu cầu chạy nhiều tiến trình thật — đúng §35 của yêu cầu.

## 6. Đã đạt (không cần làm lại)

Một engine TTS + một hàng đợi; một bộ tách câu (không tách sai số thập phân, IP, phiên bản, tên miền, đường dẫn; bỏ code/chú thích trên luồng); TTS theo câu song song, giữ thứ tự; câu đệm làm nóng sẵn (6 ms); audio nhị phân ở portal/HUD; huỷ lượt cũ khi có lệnh mới ở cả 3 transport; một cổng tool + một hàng đợi duyệt + RBAC + audit; mọi WS xác thực; một kho lịch sử có cắt cửa sổ.
