# Mô hình rủi ro (Risk Model)

> Phase 0 — 2026-10-05. Mục tiêu §15, §31: rủi ro tính từ hành động + đích + môi trường + dữ liệu + khả năng hoàn tác, không dựa vào độ tự tin của LLM.

## 1. Hiện trạng

Một hàm chuẩn: `zero_trust.HumanInTheLoopManager.get_risk_level(action, params, declared_risk_level)` (`zero_trust.py:164`).

| Đầu vào được xét | Cách xét | Bằng chứng |
|---|---|---|
| Tên tool | bảng `RISK_LEVEL_MAP` (≈ 45 tool) | `:58–125` |
| Từ trong tên tool | `delete/remove/drop/wipe/format/kill/transfer/destroy` → 5; `modify/update/write/exec/script/service/admin` → 4; còn lại 2 | `:196–206` |
| Khai báo của registry | `max(tính toán, khai báo)` — chỉ nâng, không hạ | `:217–229` |
| Tham số | chỉ một luật: `record_expense` ≥ 50 000 000 → 5 | `:209–214` |

**Không xét**: máy đích (máy chủ / máy trạm / sản xuất), môi trường, độ nhạy dữ liệu, số đối tượng bị ảnh hưởng, khả năng hoàn tác, thời điểm, người gọi.

Hàm thứ hai `safety_guard.SecurityEngine.evaluate_action_risk` (`safety_guard.py:257`) trả `BLOCKED` theo từ khoá cấu hình nhưng **không** nằm trên đường thực thi (xem `policy-model.md` P3).

## 2. Mô hình đích — giữ thang 1–5, thêm các trục

```
risk = max(
  base(tool)                         # bảng hiện có + khai báo registry
  target_factor(target, environment) # production / máy chủ chính: +1
  data_factor(classification)        # CONFIDENTIAL +1, RESTRICTED +2
  blast_radius(args)                 # nhiều máy / "*" / toàn bộ: +1
  irreversibility(tool.reversible)   # không hoàn tác được: tối thiểu 4
  amount_rules(args)                 # luật số tiền hiện có, cấu hình được
) cắt trong [1, 5]
```

| Rủi ro | Mức tự trị mặc định | Kiểm chứng (§30) |
|---|---|---|
| 1 | L0 | NONE |
| 2 | L2 | BASIC (tool trả thành công + kết quả đúng dạng) |
| 3 | L3 | STANDARD (đọc lại trạng thái sau hành động) |
| 4 | L3, L4 nếu đã uỷ quyền | STANDARD |
| 5 | L5 nếu tool nằm trong danh sách DENY; ngược lại L3 + duyệt từng lần | STRICT (nhiều nguồn: tiến trình + cổng + health) |

## 3. Khai báo tool (§18) — tận dụng `ToolDefinition` có sẵn

`plugin_registry.ToolDefinition` (`plugin_registry.py:197`) đã có tên, mô tả, schema, `risk_level`, timeout. `@export_skill` (`core/plugin_manager.py:75`) có tên, mô tả, tham số, domain. Thiếu và cần thêm vào **một** định dạng chung: `reversible`, `rollback`, `data_scope`, `environment`, `verification`, `idempotent`, `output_schema`.

## 4. Phân loại dữ liệu (§90) — hiện chưa có

Đề xuất mặc định: dữ liệu nhân sự / tài chính (`hr_kpi.db`, bảng `employees`, `get_financial_summary`) = CONFIDENTIAL; bí mật cấu hình, khoá = RESTRICTED (đã chặn đọc ở `file_system.py:53–70`); số liệu hệ thống = INTERNAL. Chủ dự án duyệt danh sách.
