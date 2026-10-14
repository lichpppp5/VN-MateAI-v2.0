# Kế hoạch migration — đường voice realtime

Phase 0 (chỉ đọc), 2026-10-03. Nguồn: `architecture-audit.md` (số đo, nút thắt), `duplicate-components.md` (D1–D7, L1–L5).

Nguyên tắc cho mọi bước: sửa bản canonical, không tạo bản V2; chuyển caller trước, xoá sau; chỉ xoá khi đã kiểm caller tĩnh + động, test qua, chạy thật; đo lại bằng `scripts/bench_voice.py` (cùng truy vấn) trước và sau; mỗi bước một commit có báo cáo theo khung PHASE/STATUS/…/NEXT STEP.

## Thứ tự

| Bước | Nội dung | Giải quyết | Lý do thứ tự |
|---|---|---|---|
| **P1** | Đo đạc: trace (request_id, session_id, trace_id; stt, router, llm_first_token, tool, sentence, tts_first_audio, ws_first_event/text/audio, request_end) đặt trong `voice_turn` cho MỌI kênh (hiện chỉ portal có); bench mở rộng: lệnh vận hành có tool, n ≥ 20, đồng thời 10 phiên, bộ nhớ sau 100 lượt; `docs/realtime/performance-baseline.md` | thiếu số đo | Không tối ưu khi chưa đo được |
| **P2** | Voice Brain gọn: prompt riêng cho câu trò chuyện (persona + tóm tắt ngắn + vài lượt gần), đếm token prompt/lịch sử/tool mỗi lượt | B1 | Nút thắt lớn nhất, không đổi hạ tầng |
| **P3** | Lệnh vận hành không gọi LLM thừa: dùng luôn lựa chọn tool của lần stream (thực thi qua `tool_gate`), không gọi lại `ask_async` với 82 tool; kết quả tool đủ thì trả lời tất định | B3, D3 | Bớt một vòng LLM mỗi lệnh vận hành |
| **P4** | TTS: đo riêng 9Router vs Edge vs ElevenLabs trên câu thật; làm nóng câu trả lời lệnh nhanh hay dùng; giới hạn hàng đợi audio ra | B2, B5 | Sau P2/P3 thì TTS là phần lớn TTFA còn lại |
| **P5** | Một giao thức sự kiện: HUD dùng schema của `/ws/v1/voice-stream` (giữ phần riêng HUD: telemetry, duyệt, hội thoại) ; một module JS phát audio dùng chung portal/HUD; REST `/api/v1/voice-command` thành adapter của `process_voice_turn` (Base64 chỉ ở biên); dự phòng mic máy chủ dùng `process_voice_turn` | D1, D2, D4, D5, L2 | Đụng frontend — làm sau khi lõi ổn định |
| **P6** | Dọn: xoá hàm chết (`duplicate-components.md` §4); đổi test `llm_engine.stream` sang `provider.stream` rồi xoá; dựng client LLM chỉ trong provider/pool (D7); `autonomous_sentinel` đọc cache của `health_monitor` (D6); `classify_intent` một lần (L1); gộp 3 nhánh chuẩn bị audio STT robot (L5); bỏ bí danh `/ws/voice` khi không còn caller | D6, D7, L1, L3, L5 | Chỉ xoá sau khi đường mới chạy |
| **P7** | Báo cáo cuối: `performance-before-after.md`, `docs/production/production-readiness.md`, chạy lại audit trùng lặp | — | Definition of Done |

Không trong kế hoạch (chưa có nhu cầu cụ thể): Redis / tách tiến trình api–realtime–worker (một tiến trình đủ cho một văn phòng; khôi phục hàng đợi duyệt sau khởi động lại đã có), STT stream cho robot (cần đo B4 ở P1 trước), PostgreSQL (kế hoạch riêng: `docs/migration/sqlite-to-postgresql-plan.md`).

## Kiểm tra sau mỗi bước

- `python -m pytest -q` (hiện 408 pass) + test `.mjs`.
- Chạy thật: lệnh nhanh, câu trò chuyện, lệnh vận hành, huỷ giữa chừng, rớt WS — trên portal, HUD, robot (nếu có thiết bị), REST.
- `scripts/bench_voice.py` cùng truy vấn; ghi số trước/sau, không ghi số không đo được.
- Test kiến trúc RULE-011..015 + timeout HTTP không lùi.
