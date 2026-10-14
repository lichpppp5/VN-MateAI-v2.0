# Đánh giá chất lượng trên LLM thật

Cập nhật 2026-10-06. Công cụ: `scripts/eval_llm.py` (20 câu tiếng Việt × 3 lần, router 9Router đang cấu hình,
prompt hệ thống + danh mục tool thật; **không chạy tool** — chỉ chấm tool model đề xuất). Kết quả gốc:
`reports/llm-eval/*.json`. Model phục vụ: họ gemini-3.6/3.7/3.8-flash (Claude trên router đang hết hạn mức).

## Kết quả

| Lần chạy | Tool đưa model | Chọn đúng tool | Hội thoại không gọi tool | Đề xuất tool bị cấm | Câu tấn công không kịp trả lời | p50 / p95 | Token (60 lượt) |
|---|---|---|---|---|---|---|---|
| Cả danh mục | 85 | 42 / 42 | 9 / 9 | 0 / 9 | 6 / 9 | 4,4 / 11,9 s | 1 463 294 |
| Thu hẹp 5 (như đường thoại), trước khi sửa | ≤ 5 + tool danh mục | 36 / 42 | 9 / 9 | 1 / 9 | 6 / 9 | 3,7 / 6,8 s | 414 854 |
| Thu hẹp 5, sau khi sửa | ≤ 5 + tool danh mục | **42 / 42** | 9 / 9 | **0 / 9** | 3 / 9 | 3,8 / 6,2 s | 437 896 |

Lần chạy đầu (1 lượt mỗi câu, cả danh mục): 18 / 20 — câu "ping 8.8.8.8" chọn PowerShell (xem dưới).

## Phát hiện và đã sửa

| Phát hiện | Nguyên nhân | Sửa |
|---|---|---|
| "Ping 8.8.8.8" → model chọn `run_powershell_command` | `test_ping_host` là skill GIẢ: không nhận địa chỉ, luôn trả một độ trễ cố định | Ping thật, không shell, kiểm tra địa chỉ (`tests/test_ping_skill_real.py`) |
| Đường thoại: "máy chủ dùng bao nhiêu CPU/RAM", "ticket nào đang mở" → sai tool 6/42 | Bộ định tuyến skill không đưa tool đúng vào top 5 (mô tả tool viết khác cách người dùng hỏi) | Mô tả tiếng Việt cho `get_system_info`, `get_tickets`; test tất định giữ tool đúng trong top 5 cho mọi câu (`tests/test_skill_router_eval.py`) |

## Phát hiện còn mở

- **Mỗi lượt portal / agent gửi ~24 000 token** (85 tool + prompt hệ thống). Đường thoại đã thu hẹp còn 5 tool:
  cùng bộ câu, token giảm 70 %, chất lượng như nhau sau khi sửa. Đề xuất áp cho portal — cần thêm câu đánh giá
  cho các tool ít dùng trước khi đổi (bộ hiện có 14 câu cần tool).
- **Câu tấn công làm model nghĩ lâu** (13–14 s) vượt giới hạn 8 s / model, 16 s / lượt → không có câu trả lời
  (người dùng nhận thông báo quá tải). Không lần nào model đề xuất tool nguy hiểm; chốt chặn thật vẫn là
  `policy_engine`. Có thể trả lời từ chối nhanh bằng bộ lọc trước LLM cho mẫu câu phá huỷ rõ ràng.
- Bộ câu nhỏ (20 câu) — đủ phát hiện lỗi thô, chưa đủ để kết luận tỉ lệ chính xác chung.

## Chạy lại

```bash
python scripts/eval_llm.py --repeat 3              # cả danh mục, như portal
python scripts/eval_llm.py --repeat 3 --narrow 5   # như đường thoại
```
Tốn token thật (khoảng 0,4–1,5 triệu token mỗi lần × 3); máy chủ cần đang chạy để `skills/registry.json` là danh mục hiện hành.
