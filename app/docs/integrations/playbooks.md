# Kịch bản vận hành (Playbooks)

Một **kịch bản** là chuỗi bước (mỗi bước = một công cụ đã có của VN-MateAI) có tham số, điều kiện, hoàn tác và kiểm chứng. Quản trị viên viết và kiểm tra trước; AI Ly Ly chỉ **chạy kịch bản có sẵn**, không tự chế chuỗi lệnh.

> **Trạng thái trung thực.** Đã kiểm thử bằng công cụ giả (`tests/test_playbooks*.py`, `tests/test_playbooks_ui.mjs`). **Chưa** chạy với hệ thống thật của doanh nghiệp: công cụ nào bạn đưa vào kịch bản vẫn do chính công cụ đó chịu trách nhiệm hiệu lực thực tế.

## Nguyên tắc an toàn

- Mọi bước đi qua **cùng cổng chính sách** như AI gọi công cụ (kill switch, L5, RBAC, ABAC, mức rủi ro L0–L5). Kịch bản **không** là đường vòng quanh chính sách.
- **Chạy thử (dry-run)**: cho biết từng bước sẽ *được phép / cần duyệt / bị chặn*, rủi ro, có hoàn tác không — **không thực thi gì**.
- Kế hoạch có bước cần duyệt → xin **MỘT phiếu duyệt cho cả kế hoạch** (loại `playbook`). Chưa duyệt thì **chưa chạy gì**; giao diện và AI đều nói rõ.
- Mỗi lượt chạy giữ **bản chụp định nghĩa** tại thời điểm chạy: sửa kịch bản sau đó không đổi lượt đang chạy.
- Bước lỗi → các bước đã làm được **hoàn tác theo thứ tự ngược** (nếu có khai báo `rollback`). Bước `optional` lỗi thì bỏ qua và ghi lại.
- Chỉ **SUCCEEDED** khi khối `verify` đạt. Không có `verify` → trạng thái **SUCCEEDED_UNVERIFIED** (chưa kiểm chứng), cần người xác nhận kèm ghi chú.
- Mỗi lượt gắn **Sổ tác vụ**; `idempotency_key` chống chạy trùng; tối đa 3 lượt đồng thời, tối đa 20 bước/kịch bản.
- Máy chủ khởi động lại giữa chừng → lượt đang chạy đánh dấu **INTERRUPTED**, không tự chạy tiếp.

## Định nghĩa (JSON)

```json
{
  "id": "khoi-dong-lai-dich-vu",
  "name": "Khởi động lại dịch vụ và kiểm tra",
  "params": {"host": {"type": "string", "required": true}},
  "steps": [
    {"id": "dung", "title": "Dừng dịch vụ", "tool": "stop_service", "args": {"host": "{{params.host}}"},
     "rollback": {"tool": "start_service", "args": {"host": "{{params.host}}"}}},
    {"id": "chay", "tool": "start_service", "args": {"host": "{{params.host}}"},
     "when": {"path": "steps.dung.status", "op": "eq", "value": "ok"}}
  ],
  "verify": [{"id": "song", "tool": "get_service_status", "args": {"host": "{{params.host}}"},
              "expect": {"path": "result.running", "op": "eq", "value": true}}]
}
```

- Mẫu `{{params.x}}` và `{{steps.<id>.result.<đường dẫn>}}`; tham chiếu không khai báo bị **từ chối khi lưu**.
- Điều kiện chỉ là **dữ liệu** (không có mã chạy được).
- Công cụ phải tồn tại thật khi lưu.

## Tự kích hoạt (lịch / cảnh báo / webhook)

Thêm `triggers` vào định nghĩa (tối đa 5). Tham số lấy từ dữ liệu kích hoạt bằng mẫu; thiếu dữ liệu thì **không chạy** (không chạy với
tham số giả).

```json
"triggers": [
  {"type": "schedule", "cron": "0 8 * * 1-5"},
  {"type": "alert", "match": {"category_prefix": "infra:", "severity": ["critical"], "title_contains": "ổ đĩa"},
   "params": {"host": "{{alert.source}}"}, "cooldown_min": 30},
  {"type": "webhook", "params": {"host": "{{payload.ci.name}}"}, "cooldown_min": 1}
]
```

- **Lịch** (`cron` 5 trường: phút giờ ngày tháng thứ, 0 hoặc 7 = Chủ nhật). Cùng một phút không chạy hai lần (khoá chống trùng).
- **Cảnh báo**: xét **mọi** cảnh báo đi qua bộ gửi cảnh báo (giám sát hạ tầng, sentinel, webhook giám sát), độc lập với ngưỡng / chống
  lặp của kênh gửi. Cần ít nhất một điều kiện `category_prefix` / `title_contains` / `source`; lọc thêm `severity`. Mẫu dùng được:
  `{{alert.title}}`, `message`, `severity`, `category`, `source`. Thông báo *đã khôi phục* và cảnh báo của chính kịch bản (`playbook*`)
  không kích hoạt gì (chống vòng lặp).
- **Webhook**: admin bấm **Cấp token webhook** (chỉ hiện một lần; máy chủ chỉ lưu hash). Hệ thống ngoài gọi
  `POST /api/v1/playbooks/<id>/trigger` với `Authorization: Bearer <token>` và thân JSON (≤ 64 KB) → mẫu `{{payload.<đường dẫn>}}`.
  Header `X-Idempotency-Key` chống chạy trùng khi bên gửi gửi lại.

**An toàn — không có đường vòng:**
- Lượt tự kích hoạt chạy bằng danh tính **admin đã lưu trình kích hoạt** (`triggers_owner`), tác nhân `VN-MATEAI-PLAYBOOK`: bước cần
  duyệt → **một phiếu duyệt** cho cả kế hoạch, chưa duyệt thì chưa chạy; bước bị chặn → không chạy.
- Mỗi trình kích hoạt có thời gian nghỉ (`cooldown_min`, mặc định 10 phút với cảnh báo); toàn hệ thống tối đa
  `playbook_triggers.max_auto_runs_per_hour` (mặc định 20) lượt / giờ.
- Bật **kill switch** hoặc `playbook_triggers.enabled = false` → không tự kích hoạt gì. Kịch bản đang tắt cũng không.
- Mỗi lần kích hoạt (kể cả bị bỏ qua vì lỗi / vượt giới hạn) ghi audit `PLAYBOOK_TRIGGER`; bảng lượt chạy ghi nhãn *tự động theo lịch /
  cảnh báo / webhook*.

## Dùng

- **Giao diện**: tab *Kịch Bản Vận Hành* — danh sách, soạn JSON (nút *Kịch bản mới* chèn mẫu chỉ đọc), *Chạy thử*, *Chạy thật*, bảng lượt chạy, huỷ, xác nhận.
- **API** (`/api/v1/playbooks`, `/api/v1/playbook-runs`): xem + chạy thử cho manager và admin; lưu / xoá / chạy / huỷ / xác nhận chỉ admin.
- **AI**: `list_playbooks`, `get_playbook_plan` (chỉ đọc), `run_playbook` (chỉ admin), `get_playbook_run`.

## Giới hạn hiện tại

- Tự kích hoạt: lịch tính theo giờ máy chủ, độ chính xác khoảng 15 giây; chưa có điều kiện "sự cố kéo dài N phút".
- Hủy lượt chạy **không** tự hoàn tác các bước đã chạy.
- Kiểm chứng chỉ đúng khi công cụ kiểm chứng phản ánh đúng thực tế; chưa thử trên hệ thống thật.
