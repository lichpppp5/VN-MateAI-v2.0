# Kiến trúc an ninh

> Cập nhật 2026-10-15 (thẩm định thương mại #26). Bản đầu: Phase 0, 2026-10-05, commit `128c87d`.
> Tài liệu này mô tả **kiến trúc, kiểm soát và khoảng trống còn lại**. Hướng dẫn vận hành từng kiểm soát nằm ở
> `docs/production/security.md`; mô hình mối đe doạ ở `threat-model.md`; MFA ở `mfa.md`; quyền đọc kho tri thức ở
> `rag-permissions.md`.

## 1. Ranh giới tin cậy

```
Người dùng (portal / HUD / Telegram)   Thiết bị (robot ESP32, máy trạm)   Hệ thống ngoài (email, webhook, connector, LLM, TTS)
        │ mật khẩu + MFA (TOTP) / SSO OIDC     │ token riêng từng thiết bị         │ chữ ký webhook; email = KHÔNG xác thực
        │ JWT 24 h, CSP chặt                    │ (chỉ lưu SHA-256)                │ nội dung ngoài = DỮ LIỆU, không là chỉ dẫn
        ▼                                        ▼                                  ▼
  interfaces (HTTP + WS) ── auth_dependencies.require_roles / ws_auth ── giới hạn tần suất (rate_limit)
        ▼
  application ── vòng agent (LLM = KHÔNG TIN CẬY: chỉ đề xuất; PII bị che trước khi gửi ra ngoài)
        ▼
  CỔNG TOOL DUY NHẤT tool_gate
        ├─ policy_engine: kill switch · tác nhân / tool bị tắt · từ khoá cấm (Portal) · L5 = DENY cho mọi vai trò
        ├─ RBAC + ABAC (vai trò, phòng ban, cấp bảo mật dữ liệu) — chạy TRƯỚC khi tạo yêu cầu duyệt
        ├─ HITL (zero_trust): mức rủi ro → duyệt; tuỳ chọn duyệt hai người, tách nhiệm vụ
        ├─ thực thi (skill máy chủ · connector · computer-use · máy trạm /ws/client)
        └─ audit_logs: chỉ INSERT, chuỗi băm, neo ra ngoài sau mỗi lần sao lưu
```

## 2. Kiểm soát hiện có

Mỗi dòng có test chạy trong bộ `pytest` (hơn 1.500 test, chạy trước mỗi commit; CI trên GitHub đang khoá vì thanh toán).

### 2.1 Danh tính và xác thực

| Kiểm soát | Nơi | Test |
|---|---|---|
| JWT bắt buộc cho REST và WebSocket; khoá ký từ biến môi trường hoặc tệp tự sinh | `auth_dependencies`, `ws_auth`, `auth_manager` | `test_public_endpoints_locked`, `test_websockets_require_login` |
| MFA TOTP (RFC 6238), bắt buộc theo vai trò (`security.require_mfa_roles`), mã khôi phục băm, chống phát lại; cài bằng QR | `application/security/mfa.py` | `test_mfa.py`, `test_auth_mfa_ui.mjs` |
| SSO OIDC; tài khoản SSO dùng MFA của IdP | `application/security/sso.py` | `test_sso.py` |
| Giới hạn đăng nhập sai theo IP **và** tài khoản → 429 | `routers/auth.py` | `test_adversarial_security.py` |
| Bắt đổi mật khẩu: tài khoản mặc định, mật khẩu admin vừa đặt lại, mật khẩu dev công khai; ≥ 10 ký tự, có chữ và số | `routers/auth.py` | `test_forced_password_change.py` |
| Quên mật khẩu qua quản trị viên (trả lời trung lập, 5 lần / giờ / IP); `scripts/reset_password.py` khi admin bị khoá | `routers/auth.py` | `test_password_reset.py` |
| Token riêng từng thiết bị; tiền tố tên thiết bị (`esp32`, `robot`, `hud`…) **không** còn nâng quyền | `security_guard.py`, `db_manager` | `test_device_auth_requires_token.py`, `test_device_access.py` |
| Danh tính tác nhân AI theo kênh do máy chủ gán (thoại / Telegram / sentinel / email) ghi vào audit | `policy_engine.agent_id_for` | `test_policy_engine.py::test_agent_identity_from_server_assigned_channel` |

### 2.2 Phân quyền và cổng tool

| Kiểm soát | Nơi | Test |
|---|---|---|
| Một cổng cho mọi đường gọi tool (agent, Portal, đa tác nhân, connector, MCP) | `agent/tool_gate.py` | `test_tool_policy_gate`, `test_golden_scenarios.py` |
| L5 (vd. xoá CSDL) bị DENY kể cả admin, kể cả đã duyệt | `policy_engine.py` | `test_policy_engine.py::test_delete_database_is_denied_for_admin_even_when_approved` |
| Từ khoá cấm / danh sách bắt duyệt sửa trên Portal được áp ở cổng | `policy_engine.py` | `test_portal_forbidden_keywords_are_enforced_at_the_gate` |
| RBAC chạy trước khi tạo yêu cầu duyệt | `tool_gate.py` | `test_rbac_runs_before_any_approval_request` |
| ABAC: phòng ban + cấp bảo mật; viewer phòng A không đọc dữ liệu CONFIDENTIAL phòng B | `security_guard.py` | `test_abac_department.py`, `test_g13_cross_department_confidential_data_is_denied` |
| Kill switch (giữ giám sát chỉ đọc), tắt từng tác nhân / tool, ngân sách thời gian / số lần gọi mỗi lượt | `policy_engine.py`, `config.autonomy` | `test_kill_switch_keeps_read_only_monitoring`, `test_g11_kill_switch_mid_operation` |
| HITL bền, không tự cho phép khi hết hạn; cờ `confirmed` trong tham số bị bỏ | `zero_trust.py`, `tool_gate.py` | `test_confirm_action_endpoint`, `test_execute_with_hitl_has_no_substring_bypass` |
| Duyệt hai người cho rủi ro ≥ `security.dual_approval_min_risk` (mặc định tắt) | `zero_trust.py` | `test_dual_approval_and_audit_export.py` |
| Tách nhiệm vụ `security.separation_of_duties` (mặc định tắt): người yêu cầu và người soạn / sửa kịch bản không tự duyệt | `zero_trust.py`, `playbooks/engine.py` | `test_dual_approval_and_audit_export.py`, `test_playbooks.py` |
| Tệp dự án được bảo vệ (mã nguồn, `identity_core.md`, cấu hình) không ghi / xoá được qua tool | `file_system.py` | `test_file_write_protection.py` |
| Mã Python do AI sinh bị kiểm AST (cấm `os.system`, `shell=True`, `ctypes`…); bản thương mại tắt hẳn sinh mã | `safety_guard.py`, `config/edition.py` | `test_edition_commercial.py` |

### 2.3 Dữ liệu và LLM

| Kiểm soát | Nơi | Test |
|---|---|---|
| Bí mật cấu hình mã hoá Fernet (`enc:v1:`), kể cả `DATABASE_URL`, token OAuth Zalo | `config/secret_box.py` | `test_config_governance.py`, `test_phase80_secret_masking.py` |
| Che PII (tên, số điện thoại, tài khoản ngân hàng, lương…) trước khi gửi LLM ngoài; chặn model `:free` ở bản thương mại | `pii_guard.py`, `safety_guard.mask_sensitive_data` | `test_pii_guard.py` |
| Nội dung ngoài là dữ liệu: quy tắc 7 trong prompt hệ thống + lớp chặn thật ở cổng tool | `llm_engine.py`, `tool_gate.py` | `test_system_prompt_identity.py`, `test_g9b_prompt_injection_in_tool_output_cannot_trigger_destruction` |
| `read_file` từ chối tệp bí mật | `file_system.py` | `test_fs_routes_policy` |
| Sổ xử lý dữ liệu cá nhân, tra cứu / xoá theo chủ thể (audit chỉ lưu mã băm), mẫu DPIA | `routers/privacy.py`, `docs/compliance/` | `test_privacy_compliance.py` |
| Mọi lời gọi HTTP ra ngoài có timeout | — | `test_http_timeouts` |

### 2.4 Kiểm toán

| Kiểm soát | Nơi | Test |
|---|---|---|
| Một kho audit, chỉ INSERT; API xoá trả 405 | `erp_database.py` | `test_audit_single_store.py` |
| Chuỗi băm: sửa / xoá một dòng lộ đúng id (`/api/v1/security/audit-logs/verify`) | `erp_database.verify_audit_chain` | `test_audit_integrity.py` |
| Neo đầu chuỗi ra ổ khác và S3 sau mỗi lần sao lưu — tính lại cả chuỗi vẫn lộ | `audit_anchor.py` | `test_audit_anchor.py` |
| Xuất báo cáo kiểm toán CSV / HTML in được, kèm kết quả kiểm chuỗi, che PII, chỉ admin | `routers/security.py` | `test_dual_approval_and_audit_export.py` |

### 2.5 Giao diện web và phát hành

| Kiểm soát | Nơi | Test |
|---|---|---|
| CSP `script-src 'self'` (không inline script / `on*`), header bảo mật, tắt `/docs`, health tối giản | `interfaces/http/csp.py` | `test_csp_strict.py` |
| Menu và khối dữ liệu theo vai trò (viewer / manager / admin) | `web/app.js` (`ROLE_HIDDEN_TABS`, `data-min-role`) | test giao diện portal |
| Gói phát hành ký Ed25519 (`SHA256SUMS.txt`), `install.sh` tự kiểm; SBOM CycloneDX; gói chặn mọi tệp `.key` / `.pem` | `scripts/build_release.py`, `verify_release.py` | `test_release_signing.py` |
| Kiểm thử nâng cấp N → N+1 thật trên Docker (dữ liệu, mật khẩu, chuỗi audit, schema) | `scripts/upgrade_test.py` | chạy tay trước mỗi phát hành |
| Khoá TLS, khoá JWT, khoá ký nhà cung cấp, CSDL không nằm trong git | `.gitignore` | `git ls-files certs *.key *.pem *.db` = rỗng |

## 3. Khoảng trống của bản đầu (2026-10-05) — trạng thái

| # | Khoảng trống | Trạng thái 2026-10-15 |
|---|---|---|
| S1 | Danh sách cấm trên Portal không được áp ở cổng | **Đã đóng** — `policy_engine` |
| S2 | Admin chạy được tool mức 5 không cần duyệt | **Đã đóng** — L5 = DENY mọi vai trò |
| S3 | Ba đường phân quyền tool | **Đã đóng** — một cổng `tool_gate` |
| S4 | `identity_core.md` ghi được bằng tool | **Đã đóng** — tệp bảo vệ ghi |
| S5 | Nội dung ngoài không được đánh dấu là dữ liệu | **Giảm** — quy tắc 7 + cổng tool chặn hành động; nhãn trên từng kết quả tool chưa có |
| S6 | Không giới hạn đăng nhập sai | **Đã đóng** |
| S7 | Máy chủ còn mật khẩu mặc định | **Đã đóng** — bắt đổi mật khẩu, MFA admin |
| S8 | Email tự trả lời cho người gửi bất kỳ | **Đã đóng** — `autonomy.email_auto_reply` mặc định tắt, giới hạn theo tên miền |
| S9 | Không kill switch | **Đã đóng** |
| S10 | Không phân vùng theo phòng ban | **Đã đóng** — ABAC |
| S11 | RBAC chạy sau bước tạo yêu cầu duyệt | **Đã đóng** |
| S12 | `shell=True` | **Đã đóng** — không còn trong mã máy chủ |
| S13 | Hai mô hình vai trò (portal ↔ RBAC) | Còn — chờ quyết định D3 |
| S14 | Tiền tố id thiết bị được coi là admin | **Đã đóng** — chỉ token riêng |

## 4. Khoảng trống còn lại

| Khoảng trống | Vì sao còn | Hướng |
|---|---|---|
| Kiểm thử xâm nhập độc lập | Cần đơn vị ngoài | Trước khi bán cho khách hàng *Regulated* |
| Multi-tenant (`tenant_id`, RLS, khoá theo tenant) | Phụ thuộc phân khúc đầu tiên | Bước nâng cấp CSDL có số thứ tự đã sẵn (`schema_migrations`) |
| Sandbox kỹ năng (gVisor / Firecracker) | Cần thiết kế riêng; bản thương mại đã không chạy kỹ năng mạnh trên máy chủ | Đợt 3 |
| Chính sách khai báo OPA / Cedar | Chính sách hiện ở mã + cấu hình | Đợt 3 |
| Định tuyến LLM theo mức phân loại (CONFIDENTIAL chỉ đi model tự host) | Chờ quyết định nhà cung cấp có DPA | Sau quyết định §6 của phản hồi thẩm định |
| SAML, vai trò tuỳ biến | Đã có OIDC; chưa có khách cần SAML | Theo yêu cầu khách hàng |
| WORM thật cho audit | Neo S3 đã có; Object Lock là cấu hình bucket | Bật Object Lock ở bucket khi triển khai |
| Nhãn "dữ liệu không tin cậy" trên từng kết quả tool | Quy tắc 7 + cổng tool đang chặn hành động | Đo lại bằng bộ đánh giá trước khi đổi định dạng tin nhắn tool |
