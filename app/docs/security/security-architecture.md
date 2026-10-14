# Kiến trúc bảo mật

> Phase 0 — 2026-10-05, commit `128c87d`. Tài liệu **kiến trúc + khoảng trống**. Hướng dẫn vận hành từng kiểm soát (kênh, xác thực, HITL, audit) vẫn ở `docs/production/security.md` — không lặp lại ở đây.

## 1. Ranh giới tin cậy

```
Người dùng (portal / HUD / Telegram)   Thiết bị (robot ESP32, máy trạm)   Hệ thống ngoài (email, webhook, connector, LLM, TTS)
        │ JWT 24 h                              │ token riêng / enrollment         │ chữ ký webhook; email = KHÔNG xác thực
        ▼                                        ▼                                  ▼
  interfaces (204 HTTP, 9 WS) ── auth_dependencies.require_roles / ws_auth
        ▼
  application ── vòng agent (LLM = KHÔNG TIN CẬY: chỉ đề xuất)
        ▼
  CỔNG TOOL tool_gate ── rủi ro (zero_trust) → HITL → RBAC (security_guard) → thực thi → audit_logs (INSERT-only)
        ▼
  skill (máy chủ) · tool registry (connector, computer-use) · máy trạm (/ws/client)
```

## 2. Kiểm soát hiện có (đã kiểm bằng test)

| Kiểm soát | Nơi | Test |
|---|---|---|
| JWT bắt buộc cho REST và WS | `auth_dependencies`, `ws_auth` | `test_public_endpoints_locked`, `test_websockets_require_login` |
| Khoá ký JWT từ biến môi trường hoặc tệp tự sinh (không cài cứng) | `auth_manager.py:38–65` | — |
| Token riêng từng thiết bị (chỉ lưu SHA-256) | `db_manager` | `test_per_device_tokens` |
| Bí mật cấu hình mã hoá Fernet `enc:v1:` | `config/secret_box.py` | test secret_box |
| RBAC fail-closed (danh tính lạ → viewer) | `security_guard.py:119,267–350` | `test_role_resolution_order` |
| HITL bền, không tự cho phép khi hết hạn | `zero_trust.py` | `test_confirm_action_endpoint`, `test_pending_action_lookup` |
| Cờ `confirmed` trong tham số tool bị bỏ | `tool_gate.py` | `test_tool_policy_gate` |
| `read_file` từ chối tệp bí mật | `file_system.py:53–70` | `test_fs_routes_policy` |
| Audit một kho, chỉ INSERT | `erp_database.py:882` | `test_audit_single_store` |
| Che dữ liệu trước khi gửi LLM | `safety_guard.mask_sensitive_data` (5 chỗ trong `llm_engine`) | — |
| Kiểm mã Python do AI sinh (AST) | `safety_guard.inspect_generated_code` | — |
| Mọi lời gọi HTTP ra ngoài có timeout | — | `test_http_timeouts` |
| Chỉ admin xem audit / hàng duyệt / metrics thoại | routers | nhiều test `*_is_admin_only` |
| Khoá TLS, khoá JWT, CSDL không nằm trong git | `.gitignore` | `git ls-files certs *.key *.pem *.db` = rỗng (kiểm 2026-10-05) |

## 3. Khoảng trống (xếp theo mức nghiêm trọng)

| # | Khoảng trống | Mức | Bằng chứng | Hướng xử lý (phase) |
|---|---|---|---|---|
| S1 | Danh sách cấm / bắt duyệt sửa trên Portal **không được áp dụng** ở cổng tool | Cao | `safety_guard.py:257` chỉ được gọi ở `routers/security.py:166` | nối vào control plane (P2) |
| S2 | Không có DENY theo rủi ro; admin ra lệnh qua AI chạy được tool mức 5 không cần duyệt | Cao | `zero_trust.py:781`, `tool_gate.py:128` | mức L5 (P2, quyết định A1) |
| S3 | 3 đường phân quyền tool + đa tác nhân gọi hàm thẳng | Cao | `routers/skills.py:233`, `plugin_registry.py:441`, `agent_orchestrator.py:252,264` | gộp về một cổng (P2–P3) |
| S4 | Prompt injection bền: `identity_core.md` đọc thẳng vào system prompt và ghi được bằng tool `write_file` (mức 3; admin không cần duyệt) | Cao | `llm_engine.py:230`, `file_system.py` (không có danh sách tệp cấm ghi) | danh sách tệp bảo vệ ghi; tách chỉ thị hệ thống khỏi dữ liệu (P11) |
| S5 | Kết quả tool / RAG / email đi vào hội thoại không được đánh dấu là dữ liệu không tin cậy | TB | `llm_engine.py:1173` | bọc nhãn + chỉ dẫn hệ thống (P11) |
| S6 | Không giới hạn số lần đăng nhập sai | Cao | `routers/auth.py:41–45` không có bộ đếm | rate limit theo IP + tài khoản (P11) |
| S7 | Máy chủ đang chạy vẫn nhận mật khẩu mặc định `admin / admin123` | Cao (vận hành) | đăng nhập thành công 2026-10-05 | chủ dự án đổi mật khẩu; cảnh báo trên Portal khi còn mặc định |
| S8 | Email gateway tạo việc + tự trả lời ra ngoài cho người gửi bất kỳ | TB | `email_gateway.py:143–246` | chính sách giao tiếp ngoài (A2) |
| S9 | Không kill switch | Cao (khi tăng tự trị) | — | P3 |
| S10 | Không phân vùng dữ liệu theo phòng ban; viewer đọc được `query_organization_data` toàn công ty | TB | `security_guard.py:80–95` | ABAC theo `department_id` (P2) |
| S11 | Kiểm quyền (RBAC) chạy sau bước tạo yêu cầu duyệt | Thấp | `tool_gate.py:127–186` | đổi thứ tự (P2) |
| S12 | `shell=True` (lệnh hằng số) | Thấp | `domain_sync.py:101,193` | đổi sang danh sách đối số (P11) |
| S13 | Hai mô hình vai trò (portal ↔ RBAC) | Thấp | `security_guard.py:123–130` | quyết định D3 |
| S14 | Thiết bị dùng token chung / id có tiền tố `esp32`… được coi là admin | TB | `security_guard.py:146–167,342` | quyết định của chủ dự án (f389bbe); khuyến nghị bật `require_per_device_token` |

## 4. Danh tính (§11–§12) — hiện trạng

| Loại | Có? | Ở đâu |
|---|---|---|
| Người | có | bảng `users` (bcrypt), JWT |
| Thiết bị | có | `device:<id>` (token riêng), `approval_grants` |
| Dịch vụ | có | `SERVICE_PRINCIPAL_ROLES` (so khớp chính xác) |
| Phiên | một phần | `session_id` theo kênh; không có bảng phiên chung |
| Tác nhân AI | **không** | hành động ghi theo người/thiết bị gọi; không biết tác nhân nào (thoại / Telegram / sentinel / email) thực hiện |
