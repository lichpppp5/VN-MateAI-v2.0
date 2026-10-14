<!-- SPDX-License-Identifier: Apache-2.0
     Copyright (c) 2026 Dương Thanh Lịch — VN-MateAI. See LICENSE and NOTICE. -->
# Đạo đức AI — cơ chế kiểm soát

Tài liệu cho người duyệt / kiểm toán: mỗi nguyên tắc đạo đức AI được thực thi ở đâu trong mã, cấu hình thế nào, kiểm chứng bằng
test nào. Cập nhật 2026-10-12.

| # | Nguyên tắc | Cơ chế | Mã | Test |
|---|---|---|---|---|
| 1 | Kiểm soát đầu ra nhạy cảm | Che dữ liệu cá nhân theo quyền, TRƯỚC khi AI đọc | `application/security/pii_guard.py`, `tool_gate.py` | `tests/test_pii_guard.py` |
| 2 | Giải thích quyết định | Phiếu duyệt có giải thích; AI nêu lý do khuyến nghị quan trọng | `application/security/explain.py`, prompt `[ĐẠO ĐỨC & CÔNG BẰNG]` | `tests/test_explain_and_refusals.py` |
| 3 | Từ chối có lý do | AI từ chối rõ ràng; ghi `AI_REFUSAL` | `application/ethics/refusals.py` | `tests/test_explain_and_refusals.py` |
| 4 | Kiểm soát thiên kiến | AI không tự quyết về con người; báo cáo công bằng | `application/security/hr_guard.py`, `application/ethics/fairness.py` | `tests/test_hr_ethics.py` |
| 5 | Giám sát con người | Rủi ro ≥ L3 cần người duyệt; L5 luôn chặn | `application/security/policy_engine.py`, `zero_trust.py` | `tests/test_adversarial_security.py`, `tests/test_approval_flow_contract.py` |
| 6 | Nhật ký kiểm toán | `audit_logs` chỉ ghi thêm, chuỗi hash chống sửa | `infrastructure/database/erp_database.py` | `GET /api/v1/security/audit-logs/verify` |
| 7 | Giới hạn tự chủ | Mức rủi ro L1–L5, kill switch, giới hạn mỗi lượt | `config.json` → `autonomy`, `risk_engine.py` | `tests/test_agent_loop_limits.py` |

## 1. Dữ liệu cá nhân (Nghị định 13/2023/NĐ-CP)

- **Che gì:** điện thoại, email, CCCD / CMND, hộ chiếu, tài khoản ngân hàng, lương / thu nhập / thưởng, địa chỉ, ngày sinh,
  mã số thuế cá nhân, BHXH — theo tên trường dữ liệu và theo mẫu trong văn bản tiếng Việt. Dạng che vẫn nhận ra được:
  `0912***678`, `n***@congty.vn`, `001******345`, `****9012`, `**/**/1990`. IP máy chủ, thời gian, mã đơn hàng, số liệu vận hành
  giữ nguyên.
- **Ai xem đầy đủ** (`config.json` → `privacy`):

  ```json
  "privacy": {
    "mask_pii": true,
    "pii_full_roles": ["admin"],
    "pii_full_departments": ["Nhân sự", "Hành chính nhân sự", "HCNS", "HR", "Human Resources"],
    "pii_min_clearance": 3
  }
  ```

  Robot, thiết bị, agent ngoài, dịch vụ: **luôn** bản đã che (robot đọc to trong phòng).
- **Che ở đâu:** lớp chính ở cổng công cụ — kết quả che trước khi AI đọc, nên mọi kênh (HUD, robot, portal, Telegram, báo cáo)
  đều an toàn. Lớp phụ ở câu trả lời (giọng nói và chữ). Sổ tác vụ (`/api/v1/ops/tasks`, manager đọc được) luôn lưu bản đã che.
  `audit_logs` ghi số chỗ đã che (`pii_masked`).

## 2. Giải thích quyết định

- Mỗi phiếu duyệt ghi: **AI muốn làm gì** (mô tả công cụ + tham số đã che), **theo yêu cầu nào của ai**, **vì sao cần duyệt**
  (ý nghĩa mức rủi ro; quyết định về con người). Hiện giống nhau trên HUD, portal, Telegram, nhật ký.
- Phiếu duyệt hiện trên HUD cho **mọi kênh** (lệnh nói ở HUD, robot, Telegram, portal, kịch bản).
- Prompt: khuyến nghị quan trọng phải nêu lý do và dữ liệu đã dựa vào. Khi công cụ đang chờ duyệt, AI phải nói rõ là đang chờ
  duyệt — không được nói là đã xong.

## 3. Từ chối có lý do

- Prompt: yêu cầu trái pháp luật / gây hại / xâm phạm quyền riêng tư / trái đạo đức → từ chối rõ ràng, nêu lý do, gợi ý cách hợp
  pháp nếu có.
- Lời từ chối được ghi `AI_REFUSAL` (trạng thái `REFUSED`) với loại `phap_luat` / `quyen_rieng_tu` / `dao_duc` / `chinh_sach`,
  kèm câu hỏi đã che dữ liệu cá nhân. Xem: Portal → Trung tâm bảo mật → nhật ký kiểm toán, lọc `AI_REFUSAL`.
- Lệnh bị cổng chính sách chặn đã có sẵn trong nhật ký (`REJECTED` + lý do). Từ chối vì thiếu khả năng không ghi.

## 4. Thiên kiến — AI không tự quyết về con người

- Công cụ ra quyết định về người (tuyển / loại, sa thải, kỷ luật, thăng / giáng chức, chấm điểm / xếp hạng nhân viên, đổi lương
  thưởng), **kể cả kỹ năng AI tự tạo sau này** (nhận theo tên): tối thiểu L3, và **không** dùng "duyệt một lần, nhớ 30 ngày" —
  mỗi quyết định được duyệt riêng. Công cụ chỉ đọc (bảng xếp hạng, chấm công, báo cáo) không bị ảnh hưởng.
- Prompt: trình bày số liệu và tiêu chí, không kết luận về người; không dùng giới tính, tuổi, dân tộc, tôn giáo, quê quán, hôn
  nhân, thai sản, khuyết tật, quan điểm chính trị. Kỹ năng AI tự viết cũng bị cấm dùng các tiêu chí này.
- **Báo cáo công bằng** `get_hr_fairness_report` (chỉ đọc): người bị dồn việc (≥ 2 lần trung vị phòng, tính riêng việc do AI
  giao), người bị bỏ sót, phòng ban / chức danh có tỉ lệ hoàn thành lệch ≥ 20 điểm. Đây là danh sách **cần người quản lý xem
  lại**, không phải kết luận có thiên kiến. Chạy định kỳ: tạo Kịch bản vận hành có lịch (vd ngày 1 hằng tháng) gọi công cụ này.
  Hệ thống không thu thập giới tính / tuổi nên báo cáo so theo phòng ban và chức danh.

## 5–7. Đã có từ trước

- **Giám sát con người:** rủi ro ≥ L3 cần người có thẩm quyền duyệt (HUD 1 chạm, Telegram, portal); DENY và L5 luôn thắng, kể
  cả admin; duyệt không mở rộng quyền của người yêu cầu (RBAC theo người yêu cầu).
- **Nhật ký kiểm toán:** mọi lời gọi công cụ, quyết định chính sách, duyệt / từ chối, từ chối của AI → `audit_logs`, chỉ ghi
  thêm, mỗi dòng có hash nối dòng trước; kiểm toàn vẹn: `GET /api/v1/security/audit-logs/verify`.
- **Giới hạn tự chủ** (`config.json` → `autonomy`): `kill_switch` (dừng mọi hành động AI), `never_autonomous_tools` (AI không
  bao giờ tự chạy), `disabled_tools` / `disabled_agents`, `approval_grant_ttl_days`, `max_tool_calls_per_turn`,
  `max_agent_seconds`, chế độ khẩn cấp khi AI tự chạy quá nhiều hành động / phút.
