# Baseline hiệu năng voice — Phase 1

Đo 2026-10-03 13:00, sau khi thêm đo đạc (Phase 1), **trước** mọi tối ưu (P2+). Số gốc: `bench-2026-10-03-phase1.json` (lệnh: `python scripts/bench_voice.py --ws wss://localhost --ws-rounds 20 --tts-rounds 10 --concurrency 1,5,10 --memory-turns 100`).

Môi trường: một máy chủ Windows 10 / Python 3.11.9, một tiến trình; LLM qua 9Router (`ag/gemini-3.8-flash-low` + dự phòng), TTS 9Router → Edge. Mạng internet văn phòng. Số phía client đo bằng `time.perf_counter()`; số phía máy chủ lấy từ `VoiceTurnTrace` (`session_ended.metrics`, `GET /api/v1/voice/metrics`).

## Định nghĩa

| Ký hiệu | Nghĩa |
|---|---|
| TTFD | sự kiện đầu tiên client nhận được |
| LLM-1st | token / lời gọi tool đầu tiên từ LLM (máy chủ) |
| TTFT | câu chữ đầu tiên sẵn sàng (máy chủ) / `text_delta` đầu tiên (client) |
| ACK | tiếng câu xác nhận / lời đệm (từ cache) |
| TTFA-answer | tiếng đầu tiên của **câu trả lời** (máy chủ) |
| TTL | hết lượt |

## 1. Qua WebSocket thật (`/ws/v1/voice-stream`), 20 lượt mỗi loại

| Loại lượt | Chỉ số | p50 | p95 | max |
|---|---|---:|---:|---:|
| **Lệnh nhanh** ("mấy giờ rồi", "kiểm tra cpu") | TTFD (client) | 2,6 ms | 2,9 ms | 3,0 ms |
| | TTFT (máy chủ) | 30 ms | 53 ms | 53 ms |
| | tiếng đầu (client) | 64 ms | 1.466 ms | 1.506 ms |
| | TTL (máy chủ) | 57 ms | 1.462 ms | 1.503 ms |
| **Câu cần LLM** ("Giải thích ngắn gọn RAID 1…") | TTFD (client) | 2,5 ms | 3,4 ms | 13 ms |
| | ACK (máy chủ / client) | 1,5 / 6,1 ms | 3,2 / 11,7 ms | 7 / 15 ms |
| | LLM-1st | 2.503 ms | 8.134 ms | 19.210 ms |
| | TTFT | 2.948 ms | 8.238 ms | 19.245 ms |
| | TTS câu đầu | 1.874 ms | 3.041 ms | 3.250 ms |
| | **TTFA-answer** | **4.531 ms** | **10.451 ms** | 22.508 ms |
| | TTL | 5.551 ms | 10.454 ms | 22.511 ms |
| **Lệnh vận hành** (tool chỉ đọc: thông tin hệ thống) | ACK (máy chủ) | 1,0 ms | 1,0 ms | 1 ms |
| | LLM-1st (lần stream chọn tool) | 4.403 ms | 39.769 ms | 39.928 ms |
| | vòng agent | 8.248 ms | 12.081 ms | 16.377 ms |
| | **TTFA-answer** | **15.018 ms** | **48.972 ms** | 48.984 ms |
| | TTL | 15.020 ms | 48.973 ms | 48.985 ms |

Lệnh nhanh: p50 64 ms khi câu trả lời đã có trong cache TTS; câu có thời gian ("mấy giờ rồi") đổi mỗi lần nên phải tổng hợp TTS → p95 ~1,5 s.

## 2. Trong tiến trình

| Phép đo | n | p50 | p95 | max |
|---|---:|---:|---:|---:|
| `FastCommandRouter.dispatch` (5 lệnh) | 1000 | 0,011 ms | 51,2 ms ("kiểm tra cpu" đo CPU thật) | 51,7 ms |
| `SentenceBuffer` một luồng token | 200 | 0,88 ms | 0,89 ms | 3,0 ms |
| TTS engine — đoạn audio đầu, câu mới | 10 | 1.136 ms | 1.826 ms | 1.926 ms |

## 3. Đồng thời (trong tiến trình, `process_voice_turn` + LLM + TTS thật, câu cần LLM)

| Phiên song song | TTFA-answer p50 / p95 | LLM-1st p50 / p95 | TTL p50 / p95 | Lỗi |
|---|---|---|---|---|
| 1 | 5.879 / 5.879 ms (gồm khởi động: ACK 1.436 ms lần đầu) | 3.866 ms | 7.844 ms | 0 |
| 5 | 4.813 / 8.383 ms | 2.986 / 6.662 ms | 6.165 / 10.210 ms | 0 |
| 10 | 5.353 / 6.579 ms | 2.756 / 4.274 ms | 6.763 / 8.426 ms | 0 |

Không thấy suy giảm tới 10 phiên — độ trễ do nhà cung cấp LLM/TTS quyết định, không phải máy chủ. **Không đo 50/100 phiên**: tốn hạn mức 9Router và sẽ đo giới hạn của nhà cung cấp, không phải của hệ thống. Đo qua WebSocket nhiều phiên cần nhiều tài khoản (mỗi người dùng một phiên `/ws/v1/voice-stream`).

## 4. Bộ nhớ (100 lượt liên tiếp, một session, lệnh nhanh + TTS thật)

RSS tiến trình 133,7 MB → 132,3 MB (mẫu mỗi 10 lượt: 132,3 MB không đổi); asyncio task còn sống 1 → 1. Không rò task, không tăng bộ nhớ. Chưa đo trên tiến trình máy chủ qua 100 lượt LLM (tốn hạn mức) — trace sẵn để đo khi cần.

## 5. Phát hiện từ số đo

1. **Lệnh vận hành chậm nhất**: TTFA-answer p50 15 s, p95 49 s. Gồm: lần stream với 5 tool (LLM-1st p50 4,4 s, p95 40 s) chỉ để biết "cần tool" rồi bị bỏ; vòng agent gọi lại model với toàn bộ danh mục (p50 8,2 s). → P3.
2. **Câu hỏi kiến thức bị coi là lệnh vận hành** — lỗi lùi do §54 (bộ định tuyến skill): "Giải thích **ngắn gọn** RAID 1 … hai **câu**" khớp mô tả `prepare_data_source_export` (cụm "ngắn gọn") với 6,6 điểm > ngưỡng 4,5 → câu xác nhận + 5 tool + prompt lớn hơn. → P2.
3. **Prompt mỗi lượt 10.600–11.350 ký tự** (≈ 3.500–3.800 token) kể cả câu trò chuyện; LLM-1st p50 2,5 s, p95 8,1 s. → P2.
4. **TTS câu động** p50 1,1–1,9 s, p95 1,8–3,0 s — sau LLM là phần lớn nhất của TTFA. → P4.
5. Câu xác nhận / sự kiện đầu / tách câu / lệnh nhanh có cache đều < 70 ms — không phải nút thắt.

## 6. Chưa đo (ghi rõ, không điền số)

- STT robot (không có thiết bị trong lúc đo) — `stt_ms` đã có trong trace của kênh robot và mic máy chủ.
- Đường REST `/api/v1/voice-command` (không qua `process_voice_turn`, nên không có trace — P5).
- CPU máy chủ trong lúc tải.
