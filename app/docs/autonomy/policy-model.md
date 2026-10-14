# Mô hình chính sách (Policy Model)

> Phase 0 — 2026-10-05. Mục tiêu §13–§14, §128: một Policy Engine độc lập với LLM, có phiên bản, mọi thay đổi vào audit.

## 1. Hiện trạng — luật đang nằm ở 4 nơi

| # | Nguồn luật | Nội dung | Ai sửa được | Có được áp dụng ở cổng tool? | Bằng chứng |
|---|---|---|---|---|---|
| P1 | `zero_trust.RISK_LEVEL_MAP` + heuristic theo từ trong tên | rủi ro 1–5; ≥ 3 phải duyệt | sửa mã | **có** (`tool_gate.py:127`) | `zero_trust.py:43,58–125,164–231` |
| P2 | `security_guard.RBAC_RULES` + `GLOBALLY_FORBIDDEN_TOOLS` + `PORTAL_ROLE_MAP` + `SERVICE_PRINCIPAL_ROLES` | tiền tố tool được / bị cấm theo role; 2 tool cấm tuyệt đối | sửa mã | **có**, nhưng chạy **sau** bước tạo yêu cầu duyệt (`tool_gate.py:180`) | `security_guard.py:45–167` |
| P3 | `config.json → security.forbidden_keywords`, `security.require_confirmation_actions` | từ khoá chặn, danh sách bắt duyệt | Portal (trang Bảo mật) | **không** — chỉ `safety_guard.evaluate_action_risk`, mà hàm này chỉ được gọi từ nút thử `routers/security.py:166` | `safety_guard.py:257–296` |
| P4 | Luật cài cứng trong mã từng chỗ | admin bỏ qua duyệt (`tool_gate.py:128`), "duyệt rồi nhớ" (`:265`), `read_file` chặn tệp bí mật (`file_system.py:53–70`), ngưỡng chi tiêu 50 triệu (`zero_trust.py:209`) | sửa mã | có | các dòng nêu |

**Hệ quả quan sát được**

1. Người quản trị thêm "drop database" vào danh sách cấm trên Portal và tin là đã chặn — thực tế cổng tool không đọc danh sách đó (P3).
2. Không có kết quả **DENY** cho mức rủi ro: mức 5 chỉ là "phải duyệt"; nhánh `BLOCKED` ở `tool_gate.py:133` không bao giờ chạy vì `zero_trust.evaluate_action_risk` (`:781`) chỉ trả `SAFE` / `NEED_CONFIRM`.
3. Tài khoản admin (theo lựa chọn của chủ dự án ngày 2026-10-05) bỏ qua duyệt cho **mọi** mức, kể cả 5.
4. Thay đổi P3 có audit (router bảo mật ghi audit); thay đổi P1/P2/P4 là thay đổi mã — chỉ truy được qua git.

## 2. Thiết kế đích — một Policy Engine, gộp từ cái có sẵn

**Không tạo engine mới bên cạnh.** `ControlPlane.authorize()` thay thế phần quyết định trong `tool_gate._run_tool_with_policy`, đọc **một** bộ luật hợp nhất từ P1–P4.

### 2.1 Đầu vào / đầu ra

```
authorize(
  actor:   {human_id | device_id | service_id, role, department_id?}
  agent:   {agent_id, autonomy_ceiling}
  action:  {tool_id, args, target, environment}
  context: {channel, session_id, trace_id, approved_by?}
) -> Decision{
  effect:  ALLOW | DENY | REQUIRE_APPROVAL | READ_ONLY
  level:   L0..L5
  risk:    1..5
  policy_id, policy_version, reasons[]
}
```

### 2.2 Thứ tự đánh giá (DENY luôn thắng)

1. **Kill switch** (toàn cục → tác nhân → tool → phiên) → DENY
2. **Danh sách DENY** (`GLOBALLY_FORBIDDEN_TOOLS` + `forbidden_keywords` cấu hình + L5) → DENY
3. **RBAC / ABAC** (role, phòng ban, môi trường) → DENY nếu không có quyền — **trước** khi sinh yêu cầu duyệt
4. **Rủi ro** (`risk-model.md`) → mức tự trị
5. **Uỷ quyền đã có** (`approval_grants`, có hạn) → ALLOW cho L4
6. Còn lại: L0/L2 → ALLOW; L3 → REQUIRE_APPROVAL

`approved=True` từ HITL chỉ gỡ bước 6, **không** gỡ 1–3.

### 2.3 Định dạng luật (lưu trong cấu hình, có phiên bản)

```yaml
policy_id: tools.default
version: 3
approved_by: admin
effective_at: 2026-10-10
rules:
  - action: restart_service
    allowed_roles: [admin, it_support]
    environment: {development: auto, production: approval}
    risk: 4
  - action: drop_database
    effect: deny            # L5 — không bao giờ chạy qua AI
```

Mọi thay đổi chính sách → audit (`policy_change`, phiên bản cũ / mới, người sửa).

## 3. Thứ tự di chuyển (không phá hành vi đang có)

1. Test khoá hành vi hiện tại của `tool_gate` (đã có `test_tool_policy_gate.py`) + test mới cho P3 (đang **không** chặn — test ghi nhận rồi đổi kỳ vọng ở bước 3).
2. Chuyển P1 + P2 + P4 vào module control plane (di chuyển, không chép); `tool_gate` gọi `authorize()`.
3. Nối P3 vào bước 2 của §2.2; đổi thứ tự RBAC trước HITL.
4. Chuyển `routers/skills.py` và nhánh HITL trong `plugin_registry` sang `authorize()`; `agent_orchestrator` gọi tool qua cổng.
5. Thêm L5 + kill switch (cần quyết định A1, A4 trong `autonomy-model.md`).
