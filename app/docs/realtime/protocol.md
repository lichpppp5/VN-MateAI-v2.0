# Giao thức thoại realtime

> 2026-10-05. Mô tả giao thức **đang chạy** của kênh chuẩn `/ws/v1/voice-stream` (portal) — trích từ `interfaces/websocket/realtime_voice_ws.py` và `infrastructure/websocket/binary_transport.py`. Các kênh khác: mục 4. Kiến trúc: `voice-architecture.md`.

## 1. Kết nối

`wss://<host>/ws/v1/voice-stream?token=<JWT>` — thiếu / sai JWT: đóng 1008 (`test_websockets_require_login`).

## 2. Tin từ client (JSON text)

| `type` | Trường | Ý nghĩa |
|---|---|---|
| `query` (mặc định khi thiếu `type`) | `query` hoặc `text`, `request_id` (tuỳ chọn) | Một lượt hỏi; lượt cũ đang chạy bị huỷ |
| `ping` | `time` | Giữ kết nối → `pong` |
| `cancel_request` / `barge_in` / `stop` | `request_id` | Huỷ lượt đang chạy (LLM, TTS, hàng đợi audio) |

## 3. Sự kiện từ máy chủ

Text frame JSON: luôn có `type`, `timestamp`; các sự kiện của một lượt có `request_id`.

| `type` | Khi nào | Trường chính |
|---|---|---|
| `session_started` | vừa nối | thông tin phiên |
| `status` | đổi trạng thái | `status`: `idle` / `routing` / `thinking` / `executing` / `speaking` / `done`, `request_id` |
| `text_delta` | có chữ để hiện | đoạn chữ |
| `sentence_ready` | xong một câu (trước TTS) | `seq`, câu |
| `audio_start` | trước khi gửi audio một câu | `seq`, định dạng |
| `audio_chunk` | metadata gói audio (frame nhị phân đi kèm) | `sequence`, kích thước |
| `audio_stream_complete` | hết lượt | `tools[]` (tên, đích, trạng thái, mã duyệt, kết quả kiểm chứng — **không** gửi tham số / dữ liệu kết quả), `requires_confirmation` |
| `session_ended` | sau lượt | `metrics` (TTFD / TTFT / TTFA / TTL) |
| `cancelled` | huỷ / ngắt lời | `request_id`, lý do |
| `error` | lỗi | `code` (`INVALID_JSON`, `EMPTY_QUERY`, …), `message` |
| `pong` | trả `ping` | `client_time` |

**Audio**: frame nhị phân riêng, tách khỏi tin điều khiển (§109). Trình duyệt: MP3 thô. Thiết bị cần thứ tự: tiêu đề `b"VM"` + seq + cờ (`FLAG_RAW_MP3`, `FLAG_IS_ACK`, `FLAG_IS_FINAL`, `FLAG_OPUS`). Base64 chỉ còn ở REST `/api/v1/voice-command` (biên tương thích).

Trạng thái chờ duyệt: hiện qua `audio_stream_complete.tools[].status = "need_confirm"` + `approval_id` (duyệt / từ chối tại chỗ: `POST /api/v1/enterprise/hitl/approve|reject`).

## 4. Kênh khác

| Kênh | Endpoint | Ghi chú |
|---|---|---|
| HUD | `/ws/hud` (JWT) | schema sự kiện riêng — chưa gộp (L10, `docs/migration/legacy-removal-plan.md`) |
| Robot ESP32 | `/api/v1/xiaozhi/ws[/{id}]` (+ alias `/ws/audio-stream`) | giao thức Xiaozhi: JSON điều khiển (`hello` có `features`, `status_report`, `wake_stats`, `set_volume`, `get_status`, `reboot`) + PCM 16 kHz |
| REST | `POST /api/v1/voice-command` | JSON + `audio_base64` |
| Máy trạm | `/ws/client` | giao thức agent (enrollment secret / JWT) |

## 5. Khoảng cách so với §108

Chưa có `event_id` riêng từng sự kiện; `approval_required` / `escalation` chưa là sự kiện riêng (đi qua `tools[]`); `stt_partial` không có vì STT chạy theo lô sau khi VAD báo hết câu (robot) hoặc STT trình duyệt / máy chủ theo câu (portal).
