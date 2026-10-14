# Kế hoạch chạy nhiều tiến trình (mở rộng ngang)

Cập nhật 2026-10-06. Trạng thái: **chưa làm; chưa cần với tải hiện tại.** Tài liệu này ghi số đo,
kiểm kê trạng thái còn nằm trong RAM một tiến trình, và thứ tự chuyển khi cần.

## 1. Số đo trên máy chủ thật (một tiến trình)

`scripts/load_test.py`: phiên HUD chỉ-xem (máy chủ đẩy telemetry 2 s/lần cho từng phiên) + `/readyz`
(truy vấn PostgreSQL mỗi lần) trong 60 s. Không LLM, không STT/TTS. Kết quả JSON: `reports/load-test/`.

| Tải | Kết nối WS | Rớt | `/readyz` p50 / p95 / p99 | Khoảng cách telemetry p99 |
|---|---|---|---|---|
| Không thêm phiên | — | — | 10,2 / 15,2 / 16,6 ms | — |
| 50 phiên | 50 / 50 (p95 383 ms) | 0 | 10,5 / 19,1 / 162,5 ms | 2 034 ms (chu kỳ 2 000 ms) |
| 100 phiên | 100 / 100 (p95 973 ms) | 0 | 10,4 / 22,8 / 105,0 ms | 2 040 ms |

Kết luận: một tiến trình giữ 100 phiên kết nối không nghẽn vòng sự kiện. Nút thắt của phiên **thoại**
thật là độ trễ LLM (p50 khoảng 4,4 s, `docs/evaluation/llm-evaluation.md`) và CPU cho faster-whisper —
không phải số kết nối. Nhiều tiến trình chỉ đáng làm khi cần chịu lỗi (một tiến trình chết không
mất dịch vụ) hoặc khi STT chiếm hết CPU.

## 2. Trạng thái còn nằm trong RAM một tiến trình

| Trạng thái | Nơi | Hậu quả khi chạy 2 tiến trình |
|---|---|---|
| Hàng chờ duyệt (HITL) | `zero_trust.hitl_manager._pending_approvals` | duyệt ở tiến trình B không thấy yêu cầu của A |
| Mã duyệt một lần | `zero_trust._APPROVED_TOKENS` | dùng lại / mất mã giữa tiến trình |
| Idempotency giao việc | `routers/tasks._IDEMPOTENT` | gửi trùng lệnh qua 2 tiến trình |
| Phiên WS portal / HUD / robot | `realtime_hub`, `websockets.active_hud_websockets`, `xiaozhi_gateway` | phát (broadcast) chỉ tới phiên cùng tiến trình |
| Lượt thoại đang chạy | `hud_voice.active_tasks` | ngắt lời không tới đúng tiến trình |
| Hàng đợi computer-use | `computer_use_plugin._IN_MEMORY_TASK_QUEUE` (đã có Redis khi cấu hình `REDIS_URL`) | — khi có Redis |
| Cảnh báo đã gửi / chống lặp | `alert_dispatcher._last_sent`, `_open_incidents` | gửi trùng cảnh báo |
| Model tạm xếp cuối | `llm_provider._model_down_until` | mỗi tiến trình tự thử lại model hỏng (chấp nhận được) |
| Worker nền | `lifecycle` (Telegram polling, sentinel, beacon UDP, hud telemetry…) | **Telegram 409 Conflict** khi 2 tiến trình cùng poll; sentinel chạy đôi |

Đã dùng chung: CSDL (PostgreSQL), rate limit / khoá đăng nhập / bộ đếm khẩn cấp (Redis), tệp (S3).

## 3. Thứ tự chuyển (khi cần)

1. **Một tiến trình "leader"** giữ worker nền (khoá Redis có hạn, gia hạn định kỳ); tiến trình khác chỉ
   phục vụ HTTP/WS. Giải quyết Telegram 409 và sentinel chạy đôi.
2. Hàng chờ duyệt + mã duyệt + idempotency → PostgreSQL (đã có bảng `approval_grants`) hoặc Redis có hạn.
3. Broadcast tới phiên WS → Redis pub/sub (mỗi tiến trình đẩy tới phiên của mình).
4. Robot và lượt thoại: **sticky session** ở bộ cân bằng tải (theo `device_id` / người dùng) — luồng âm thanh
   không nên đổi tiến trình giữa lượt.
5. Chạy 2 tiến trình sau proxy, bộ test chạy lại với `VNMATEAI_TEST_BACKEND=pg` + Redis thật.

Không làm trước bước 1: mọi bước sau đều cần worker nền chạy đúng một bản.
