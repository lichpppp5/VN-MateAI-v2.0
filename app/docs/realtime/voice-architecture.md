# Kiến trúc thoại realtime — bản tổng hợp

> Phase 0 — 2026-10-05, commit `128c87d`. **Bản chính** cho kiến trúc thoại; thay `docs/voice-architecture-audit.md` (2026-10-01) và tóm tắt `docs/realtime/architecture-audit.md` (2026-10-03). Đường gọi chi tiết: `call-graph.md`. Số đo: `performance-before-after.md`, `bench-2026-10-03-final.json`.

## 1. Đường chuẩn (đã đạt, một bản cho mọi kênh)

```
Mic (portal WAV→STT máy chủ · HUD Web Speech · robot PCM+Silero VAD · mic máy chủ wake word)
  → process_voice_turn                                   app/voice/voice_turn.py:318
      ├─ fast_command_router.dispatch  (tất định, không LLM)          :397
      ├─ classify_intent → câu đệm từ cache (không LLM)               :429–435
      └─ llm_engine.stream_voice_response → provider.stream            agent/llm_engine.py:1299,1444
            token → SentenceBuffer → sanitise_for_tts → câu
            tool_call → ask_async (vòng agent ≤ 6, qua tool_gate)
  → StreamingTTSWorkerPipeline (2 worker, giữ thứ tự, câu maxsize 5, audio ra có giới hạn)   infra/tts/tts_queue_pipeline.py
  → TTSStreamEngine (cache → ElevenLabs? → 9Router → Edge dự phòng)                          infra/tts/tts_stream_engine.py
  → sink theo kênh: binary WS (portal) · broadcast_hud_binary (HUD) · PCM (robot) · loa máy chủ
Huỷ / ngắt lời: RealtimeVoiceSession.cancel_active_turn · hud_voice.cancel_task · xiaozhi_gateway.handle_barge_in
```

Đáp ứng prompt §41–§58: LLM stream ✅, tách câu an toàn (IP, số, URL) ✅, TTS theo câu ✅, hàng đợi có giới hạn ✅, audio nhị phân ✅, ngắt lời ✅ (mỗi transport một cơ chế), lệnh nhanh tất định ✅, câu đệm cache ✅, câu đệm phát song song với agent ✅ (`ack_audio_ms` p50 1 ms ở lệnh vận hành).

## 2. Giao thức

| Kênh | Endpoint | Xác thực | Schema sự kiện |
|---|---|---|---|
| Portal | `/ws/v1/voice-stream` | JWT | `status`, `text_delta`, `tool_call`, `audio_stream_complete` (+`tools`), `cancelled`, `error`; audio = frame nhị phân |
| HUD | `/ws/hud` | JWT | schema riêng (chưa gộp — `duplicate-components.md` D4) |
| Robot | `/api/v1/xiaozhi/ws[/{id}]` (+ alias `/ws/audio-stream`) | token thiết bị riêng hoặc token chung | giao thức Xiaozhi (JSON điều khiển + PCM 16 kHz) |
| REST | `POST /api/v1/voice-command` | JWT | JSON + `audio_base64` (biên tương thích) |

Chưa có so với §108: `event_id`, `request_id` trong mọi sự kiện (portal có trace riêng `VoiceRequestTrace`), sự kiện `approval_required` / `escalation` chuẩn (portal nhận qua `tools[].status = need_confirm`).

## 3. Trạng thái và khả năng mở rộng

| Trạng thái | Nơi giữ | Mất khi khởi động lại? |
|---|---|---|
| Phiên WS portal / HUD / robot | RAM (`RealtimeVoiceSession`, `voice_sessions`, `XiaozhiNode`) | có — client tự nối lại |
| Lịch sử hội thoại | `memory_manager` (cửa sổ 14 tin; thoại 4 lượt / 1 200 ký tự) | có |
| Trace + số đo thoại | `_RECENT_TRACES = deque(maxlen=500)` (`voice_turn.py:94`) | **có** — `/api/v1/voice/metrics` trống sau mỗi lần khởi động (đã gặp ngày 2026-10-05) |
| Hàng đợi duyệt | RAM + khôi phục từ `audit_logs` | không |
| Cache âm thanh | RAM + đĩa | không (đĩa) |

→ Chỉ chạy được **một tiến trình**. Nhiều tiến trình cần Redis cho phiên/hàng đợi và nơi lưu bền cho trace (§64, §114).

## 4. Số đo thật gần nhất (bench 2026-10-03-final, `scripts/bench_voice.py`)

| Loại lượt | n | TTFT p50 / p95 (ms) | TTFA câu trả lời p50 / p95 (ms) | TTL p50 / p95 (ms) |
|---|---|---|---|---|
| Lệnh nhanh (qua WS) | 20 | 27 / 53 | 51,5 / 1 693,8 | 54 / 1 694,8 |
| Câu cần LLM | 20 | 2 559,5 / 8 227,6 | 4 398,5 / 10 410 | 4 835,5 / 10 445,9 |
| Lệnh vận hành (tool) | 20 | 12 751 / 40 697 | 12 752 / 40 703 | 12 753,5 / 40 705,3 |
| Câu đệm khi vận hành | 20 | — | ack 1 / 1 | — |

| Thành phần | n | p50 / p95 (ms) |
|---|---|---|
| Lệnh nhanh — toàn bộ (in-process) | 1 000 | 0,011 / 51,1 |
| ↳ "kiểm tra cpu" | 200 | 50,8 / 51,4 ← **chặn event loop** (`fast_command_router.py:267`) |
| Tách câu mỗi luồng | 200 | 0,88 / 0,90 |
| TTS câu đầu (engine) | 10 | 1 031,5 / 1 777,1 |
| Đồng thời 10 phiên — TTFA | 10 | 4 669,5 / 5 316,6 |
| RSS sau 100 lượt | — | 132,1 → 132,0 MB |

**p99**: chưa có — mỗi nhóm mới 20 mẫu, p99 không có ý nghĩa thống kê. **50 / 100 phiên**: chưa đo. Số đo trên là trước các thay đổi ngày 2026-10-04/05; cần đo lại (Phase 5).

## 5. Điểm nghẽn (xếp theo tác động đo được)

| # | Điểm nghẽn | Đo được | Nguyên nhân | Hướng xử lý |
|---|---|---|---|---|
| B1 | Lệnh vận hành TTFA p50 12,8 s, p95 40,7 s | bench | LLM chọn tool → vòng agent gọi LLM lại → tổng hợp; chuỗi thử model dự phòng | dùng lại lựa chọn tool của lượt stream; giới hạn tổng thời gian thử model |
| B2 | Câu cần LLM TTFT p50 2,6 s | bench | nhà cung cấp (LLM-1st 2,5 s) | model nhanh hơn / cục bộ — ngoài phạm vi mã |
| B3 | TTS câu đầu p50 1,0 s | bench | 9Router trả nguyên câu, không stream | Edge stream làm đường chính nếu ổn định; câu đầu ngắn hơn (đã có `VOICE_FIRST_SENTENCE_WORDS = 12`) |
| B4 | "kiểm tra cpu" chặn loop 50 ms cho **mọi** phiên | bench | `psutil.cpu_percent(interval=0.05)` trong hàm async | đọc cache của `health_monitor` hoặc `cpu_percent(interval=None)` |
| B5 | Prompt vòng agent 10 603 ký tự | đo 2026-10-05 | chỉ dẫn vận hành đầy đủ | chỉ nạp khi cần tool (đã làm cho đường thoại: 3 523) |

## 6. Đích

Giữ nguyên đường chuẩn (không tạo "Voice Engine V2"). Việc còn lại: B1–B5; gộp schema HUD vào `/ws/v1/voice-stream`; `event_id`/`request_id` cho mọi sự kiện; trace bền; trạng thái phiên ra Redis khi cần nhiều tiến trình; đo lại sau mỗi thay đổi bằng `scripts/bench_voice.py`.
