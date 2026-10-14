# Đăng nhập một lần (SSO) bằng OpenID Connect

Nhân viên đăng nhập bằng tài khoản công ty (Microsoft Entra ID / Azure AD, Keycloak, Okta, Google Workspace…). Mật khẩu chỉ nằm ở hệ thống định danh (IdP) của doanh nghiệp; MFA do IdP quản lý.

> **Trạng thái trung thực.** Đã kiểm thử với IdP giả ký RSA thật (`tests/fake_oidc.py`), gồm các đòn tấn công điển hình. **Chưa** thử với Entra ID / Keycloak / Okta thật — khi nối IdP thật, kiểm tra từng bước ở mục 4.

## 1. Cách hoạt động

Luồng *authorization code + PKCE (S256)*: nút "Đăng nhập bằng tài khoản công ty" → IdP → VN-MateAI nhận `code` ở `/api/v1/sso/callback` → đổi lấy `id_token` → kiểm tra → cấp phiên. **Token không bao giờ nằm trên URL**: callback chỉ đưa trình duyệt về `/#sso=<mã một lần, sống 60 giây>`, trang đổi mã bằng POST rồi xoá mã khỏi thanh địa chỉ.

Kiểm tra bắt buộc với `id_token` (thiếu / sai một điều là từ chối): chữ ký **bất đối xứng** theo JWKS của issuer (từ chối `alg: none` và HS* ký bằng client secret), `iss`, `aud`, `exp`, `nonce` của phiên, `sub`; `state` dùng một lần, hết hạn sau 10 phút; email đã được IdP xác minh (nếu IdP báo); IdP có issuer khác trong discovery bị từ chối.

## 2. Cấu hình (`config.json → sso`)

```json
"sso": {
  "enabled": true,
  "display_name": "Đăng nhập bằng tài khoản công ty",
  "issuer": "https://login.microsoftonline.com/<tenant-id>/v2.0",
  "client_id": "<application (client) id>",
  "client_secret": "",
  "redirect_uri": "https://vnmateai.congty.local/api/v1/sso/callback",
  "scopes": "openid profile email",
  "username_claim": "email",
  "groups_claim": "groups",
  "role_map": { "<id nhóm hoặc role của IdP>": "admin", "<nhóm quản lý>": "manager" },
  "default_role": "viewer",
  "allowed_email_domains": ["congty.vn"],
  "auto_provision": true
}
```

- `client_secret` có thể đặt bằng biến môi trường `VNMATEAI_SSO_CLIENT_SECRET` (khuyên dùng); khi lưu vào `config.json` được tự mã hoá.
- `redirect_uri` phải **trùng từng ký tự** với URI chuyển hướng đã đăng ký ở IdP và dùng HTTPS.
- `verify_ssl` / `ca_bundle`: dùng khi IdP nội bộ có chứng chỉ riêng.

## 3. Vai trò và tài khoản

- Vai trò suy từ claim `groups` **và** `roles` qua `role_map` (không phân biệt hoa thường); trùng nhiều nhóm → lấy vai trò **cao nhất**. **Không có ánh xạ = `default_role` (mặc định viewer); hệ thống không bao giờ tự cấp admin nếu bạn chưa ánh xạ rõ.**
- Vai trò được **đồng bộ mỗi lần đăng nhập**: rút người khỏi nhóm ở IdP thì lần sau họ mất quyền. (Phiên đang mở giữ tới khi hết hạn; muốn cắt ngay, vô hiệu tài khoản ở IdP và xoá tài khoản ở VN-MateAI.)
- `auto_provision: true` tự tạo tài khoản lần đầu; `false` = chỉ người đã có tài khoản SSO mới vào được.
- **Tài khoản cục bộ (có mật khẩu) trùng email KHÔNG bị chiếm hay liên kết ngầm** — đăng nhập SSO bị từ chối với thông báo rõ, tài khoản cục bộ giữ nguyên. Muốn chuyển một tài khoản cục bộ sang SSO, xoá nó rồi để SSO tạo lại.
- Tài khoản SSO không đăng nhập bằng mật khẩu được (mật khẩu ngẫu nhiên không ai biết) và không cài MFA cục bộ (MFA do IdP lo).

## 4. Nối IdP thật

**Microsoft Entra ID:** Đăng ký ứng dụng (Web) → URI chuyển hướng = `redirect_uri` → tạo client secret → *Token configuration* → thêm claim **groups** (Entra trả **object ID** của nhóm, nên khoá `role_map` là ID nhóm) hoặc dùng **App roles** (claim `roles`). Issuer: `https://login.microsoftonline.com/<tenant>/v2.0`.

**Keycloak:** Client *confidential*, "Standard flow" bật, "Valid redirect URIs" = `redirect_uri`; thêm mapper **Group Membership** (claim `groups`, tắt "Full group path" nếu muốn tên ngắn). Issuer: `https://<host>/realms/<realm>`.

Kiểm tra: (1) `…/.well-known/openid-configuration` mở được từ máy chủ VN-MateAI; (2) bấm nút SSO → đăng nhập → vào được; (3) tài khoản trong nhóm admin có vai trò admin; (4) tài khoản ngoài nhóm chỉ là viewer; (5) nhật ký kiểm toán có `sso_login`.

## 5. Chưa làm

- Chưa thử với IdP thật; chưa hỗ trợ SAML 2.0 (chỉ OIDC); chưa có đăng xuất đơn nhất (single logout) / thu hồi phiên theo IdP; chưa đồng bộ nhóm theo thời gian thực (chỉ lúc đăng nhập); chưa có SCIM cấp / thu hồi tài khoản tự động.
