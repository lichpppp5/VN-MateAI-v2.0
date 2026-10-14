# Sẵn sàng vận hành — đường thoại realtime

> **2026-10-05:** điểm sẵn sàng toàn hệ thống (kiến trúc, bảo mật, tự trị, dữ liệu…) ở `readiness-score.md`. Tệp này chỉ đánh giá đường thoại realtime, ngày 2026-10-03.

Đánh giá 2026-10-03, sau realtime Phase 0–P6. Số đo: `docs/realtime/performance-before-after.md`, `docs/realtime/bench-2026-10-03-final.json`. Việc cần chủ dự án làm / quyết định: `owner-todo.md` § "Thoại realtime".

**Kết luận:** dùng được cho **một văn phòng, một tiến trình máy chủ**. Chưa đạt mục tiêu độ trễ < 1,5 s tới tiếng đầu với câu cần LLM. Nguyên nhân là nhà cung cấp LLM/TTS, không phải máy chủ. Hai việc nên làm trước khi dùng rộng: nghe thử trên trình duyệt sau khi đổi bộ phát audio, và rút ngắn chuỗi thử model dự phòng.

## Đã kiểm chứng

| Hạng mục | Trạng thái | Bằng chứng |
|---|---|---|
| Một lõi thoại cho mọi kênh | ✅ portal, HUD, robot, mic máy chủ, REST đều qua `voice_turn.process_voice_turn` | `duplicate-components.md` §5; chạy thật REST + HUD |
| Đo đạc mọi lượt | ✅ `VoiceTurnTrace`: LLM-1st, câu đầu, câu xác nhận, tiếng câu trả lời, vòng agent, STT (robot / mic), kích thước prompt / lịch sử / tool; `GET /api/v1/voice/metrics` (chỉ admin) | `tests/test_voice_turn_trace.py`; chạy thật |
| Không rò bộ nhớ / task | ✅ 100 lượt: RSS 132,1 → 132,0 MB, task 1 → 1 | bench final |
| Đồng thời | ✅ 1 / 5 / 10 phiên, 0 lỗi, không suy giảm | bench final |
| Ngắt lời | ✅ máy chủ: không còn task TTS mồ côi (test hành vi); trình duyệt: bộ phát chung bỏ đoạn đang giải mã của lượt cũ | `test_hud_voice_pipeline_behavior.py`, `test_voice_audio_queue.mjs` |
| Áp lực ngược | ✅ hàng đợi câu (5) + hàng đợi audio ra (4) có giới hạn | `test_tts_pipeline_backpressure.py` |
| Bảo mật đường thoại | ✅ WS bắt buộc JWT (`/ws/v1/voice-stream`, `/ws/hud`); metrics chỉ admin; REST RBAC theo người đăng nhập (không theo `source_device`); tool qua cổng chung (RBAC, HITL, audit) | `test_websockets_require_login.py`, `test_confirm_pending_action.py` |
| Không lộ bí mật | ✅ file bench / trace không chứa token, mật khẩu (đã quét) | — |
| Kiến trúc | ✅ RULE-011 (client LLM ngoài provider) = 0; RULE-013/014/015 = 0 | `tests/architecture` |
| Test | ✅ 443 pytest + mọi test `.mjs` | — |

## Rủi ro còn lại

| # | Rủi ro | Mức | Ghi chú / hướng xử lý |
|---|---|---|---|
| R1 | **Chuỗi thử model dự phòng dài**: 2/20 lượt vận hành treo 40,7 s rồi trả câu "quá tải" (mọi model không mở được stream; 5 s mỗi model) | Cao (trải nghiệm) | Giới hạn tổng thời gian thử (vd 10–12 s) rồi trả lời / lời đệm; bỏ model hay lỗi khỏi danh sách router. Chưa làm: đổi hành vi dự phòng cần chủ dự án chọn danh sách model. |
| R2 | TTFA câu cần LLM p50 4,4 s, lệnh vận hành 12,8 s | Trung bình | Do nhà cung cấp (LLM-1st 2,5–4,5 s, TTS 1,3–2,2 s/câu). Giảm thêm cần model / TTS nhanh hơn hoặc chạy cục bộ. |
| R3 | **Bộ phát audio trình duyệt mới chưa nghe thử tai** | Trung bình | Đã kiểm bằng test Node (thứ tự, ngắt lời, kết thúc) + máy chủ phục vụ file đúng. Cần một lần nghe thử portal + HUD (owner-todo). |
| R4 | Trạng thái trong RAM một tiến trình (phiên thoại, hàng đợi TTS, WebSocket) | Thấp cho một văn phòng | Hàng đợi duyệt đã khôi phục sau khởi động lại. Nhiều tiến trình cần Redis (owner-todo). |
| R5 | HUD và portal còn hai schema sự kiện (D4) | Thấp | Chỉ là nợ bảo trì; cần kiểm trên trình duyệt khi gộp. |
| R6 | Robot: ba nhánh kết thúc câu nói (L5), `stt_ms` chưa có số đo | Thấp | Cần robot thật. |
| R7 | Mỗi lần chạy tool ghi hai dòng audit | Thấp | Quyết định của chủ dự án (audit bất biến). |
| R8 | Đồng thời 50 / 100 phiên chưa đo | Thấp cho một văn phòng | Đo sẽ chủ yếu đo giới hạn nhà cung cấp; qua WS cần N tài khoản. |

## Vận hành

- Theo dõi: `GET /api/v1/voice/metrics?channel=hud|portal|xiaozhi|server_mic|web&recent=20` (p50/p95/p99 theo kiểu lượt); log `[VoiceTrace] {...}` mỗi lượt.
- Đo lại sau khi đổi model / TTS: `python scripts/bench_voice.py --ws wss://localhost --ws-rounds 20 --tts-rounds 10 --tts-providers 6` (tài khoản admin qua `--user` / `--password`; mặc định là tài khoản mặc định — sau khi đổi mật khẩu phải truyền mật khẩu mới; script không in token).
- Làm nóng cache TTS chạy lúc khởi động (câu đệm, câu cố định của lệnh nhanh, câu hỏi lại / tạm biệt của HUD).
