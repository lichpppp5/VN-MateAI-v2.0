# Mức dùng AI, ngân sách và đánh giá chất lượng (LLMOps)

Ba việc: **đo** mỗi lần gọi model tốn bao nhiêu token / tiền, **giới hạn** chi tiêu (ngân sách), và **kiểm tra** chất lượng trả lời sau mỗi
lần đổi model / prompt / tri thức (bộ kiểm + phát hiện ca bị tụt).

> **Trạng thái trung thực.** Đã kiểm thử bằng model giả (`tests/test_llmops.py`, `tests/test_llmops_ui.mjs`). Số token là số **nhà cung cấp
> báo**; chưa đối chiếu với hoá đơn thật của nhà cung cấp. Chi phí chỉ đúng bằng giá bạn khai.

## Mức dùng

- Mỗi lần gọi model có trả `usage` → một dòng trong bảng `llm_usage` (thời điểm, người / thiết bị, model, token vào / ra, chi phí, sổ tác vụ).
  Không có `usage` thì **không ghi, không đoán**.
- **Chi phí** chỉ tính cho model đã khai giá (`price_in_per_1m`, `price_out_per_1m` trong `llm.model_registry`). Token của model chưa khai
  giá được báo riêng (`unpriced_tokens`) và **không** quy ra tiền.
- Hai đường đều được đo: vòng agent (`LLMEngine.ask_async` — portal, Telegram, lượt cần công cụ) và **stream giọng nói**
  (`stream_voice_response` — HUD, robot, mic máy chủ). Stream xin token ở chunk cuối bằng `stream_options.include_usage` (chuẩn OpenAI);
  router / model nào từ chối tham số này thì đặt `llmops.stream_usage = false` — khi đó lượt trò chuyện giọng nói **không** được tính.
- Ghi mức dùng lỗi (DB hỏng) **không** làm hỏng lượt hội thoại: chỉ ghi log.

## Ngân sách

Cấu hình `llmops` (sửa ở portal hoặc `POST /api/v1/llmops/budget`; mọi thay đổi đi qua lịch sử cấu hình):

| Khoá | Ý nghĩa | Mặc định |
|---|---|---|
| `budget_mode` | `off` không theo dõi · `warn` chỉ cảnh báo · `block` từ chối lượt AI mới khi **đã vượt** | `warn` |
| `daily_token_limit` | Token / ngày, toàn hệ thống | 0 = không giới hạn |
| `user_daily_token_limit` | Token / ngày cho mỗi người / thiết bị (chỉ người vượt bị chặn) | 0 |
| `daily_cost_limit`, `monthly_cost_limit` | Chi phí / ngày, / tháng (chỉ model đã khai giá) | 0 |
| `warn_ratio` | Bắt đầu cảnh báo từ tỉ lệ này | 0.8 |
| `eval_max_cases` | Số ca tối đa trong một bộ kiểm | 50 |

- `block`: kiểm **trước** khi gọi model, ở cả vòng agent lẫn stream giọng nói (portal, giọng nói, robot, Telegram, đánh giá). Người dùng nhận câu
  "hạn mức dùng AI đã hết" kèm mức đã dùng; kết quả có `error: "budget_exceeded"`.
- Cảnh báo (`warn` và `block`): gửi qua kênh cảnh báo (`category: llm_budget`) **một lần** mỗi ngưỡng mỗi ngày — `warning` khi chạm
  `warn_ratio`, `critical` khi vượt.
- Không đo được (DB lỗi) thì **không chặn**: ngân sách là kiểm soát chi phí, không phải lớp an ninh.
- Lượt đang chạy dở không bị cắt; ngân sách có thể bị vượt nhẹ bởi lượt cuối cùng trước khi chặn.

## Đánh giá chất lượng

Một **bộ kiểm** là danh sách ca, mỗi ca = câu hỏi + điều kiện đạt. Điều kiện là **dữ liệu**, không có "AI chấm AI":

```json
{
  "name": "Chính sách nhân sự",
  "cases": [
    {"id": "phep-nam", "prompt": "Nghỉ phép năm mấy ngày?",
     "checks": [{"type": "contains", "value": "12 ngày"}, {"type": "not_contains", "value": "không biết"}]},
    {"id": "may-chu-loi", "prompt": "Máy chủ nào đang có cảnh báo?",
     "checks": [{"type": "tool_called", "value": "get_infra_status"}, {"type": "max_chars", "value": 800}]}
  ]
}
```

Loại điều kiện: `contains`, `not_contains` (không phân biệt hoa thường), `regex`, `tool_called`, `tool_not_called`, `min_chars`, `max_chars`.

- Chạy qua **đúng vòng agent thật** nhưng chỉ đưa **công cụ chỉ đọc** (rủi ro L1) và danh tính `ai-eval` quyền thấp: đánh giá không
  thay đổi hệ thống. Ca cần công cụ ghi sẽ không đạt — đó là chủ ý.
- Tuần tự, một lần chạy tại một thời điểm, mỗi ca tối đa 180 s. Một ca lỗi chỉ làm hỏng ca đó.
- Mỗi ca **tốn token thật** và chịu ngân sách; bị từ chối vì hạn mức / quá tải thì tính là **không đạt**, không phải đạt.
- Mỗi lần chạy so với lần **hoàn tất** liền trước của cùng bộ kiểm: tỉ lệ đạt tăng / giảm, ca nào **tụt** (đạt → hỏng), ca nào đã sửa.
- Máy chủ khởi động lại giữa chừng → lần chạy đánh dấu **INTERRUPTED**, không tự chạy tiếp.
- "Đạt" chỉ nghĩa là thỏa điều kiện bạn viết; không chứng minh câu trả lời đúng ngoài điều kiện đó. Model thật không tất định: nên chạy
  lại trước khi kết luận một ca tụt là do thay đổi.

## Dùng

- **Giao diện**: tab *Quản lý Trợ lý AI* → *💰 Chi phí & Đánh giá*: tổng token / lượt / chi phí, theo ngày / model / người, thanh ngân
  sách, form đặt ngân sách (admin; chế độ chặn phải xác nhận), soạn bộ kiểm JSON, chạy, xem chi tiết và ca bị tụt.
- **API** (`/api/v1/llmops/...`): `usage`, `budget`, `eval/sets`, `eval/sets/{id}`, `eval/sets/{id}/run`, `eval/runs`, `eval/runs/{id}`.
  Xem: manager + admin. Đặt ngân sách, lưu / xoá bộ kiểm, chạy đánh giá: chỉ admin.
- **AI** (chỉ đọc, manager + admin): `get_ai_usage`, `get_ai_budget_status`, `get_ai_eval_results`. AI không đặt ngân sách, không chạy
  đánh giá.

## Giới hạn hiện tại

- Lần gọi model phụ ngoài hai đường trên (vd tóm tắt phiếu duyệt trong `approval_decisions`) chưa được tính.
- Lượt giọng nói bị ngắt giữa chừng (người dùng nói chen) đóng stream trước chunk cuối nên không có số token để ghi.
- Chưa có lịch chạy đánh giá tự động (vd sau mỗi lần đổi cấu hình) — admin bấm chạy.
- Hạn mức theo người dựa trên danh tính người gọi / thiết bị; lượt không rõ danh tính gộp vào `unknown`.
- Bảng `llm_usage` chưa tự dọn dữ liệu cũ.
