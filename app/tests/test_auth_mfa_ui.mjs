// SPDX-License-Identifier: Apache-2.0
// Copyright (c) 2026 Dương Thanh Lịch — VN-MateAI. See LICENSE and NOTICE.
// Giao diện MFA (web/auth-mfa.js): bước nhập mã khi đăng nhập, cài MFA bắt buộc, thẻ MFA trong tab Bảo mật.
import { readFileSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';

const ROOT = join(dirname(fileURLToPath(import.meta.url)), '..');
const src = readFileSync(join(ROOT, 'web', 'auth-mfa.js'), 'utf8');
const app = readFileSync(join(ROOT, 'web', 'app.js'), 'utf8').replace(/\r\n/g, '\n');
const html = readFileSync(join(ROOT, 'web', 'index.html'), 'utf8').replace(/\r\n/g, '\n');
let passed = 0;
const ok = (c, n) => { if (!c) { console.error(`FAIL ${n}`); process.exit(1); } passed += 1; };

ok(/id="mfa-card-root"/.test(html) && /auth-mfa\.js/.test(html) && html.indexOf('auth-mfa.js') < html.indexOf('/static/app.js'), 'index.html: thẻ MFA + nạp script');
ok(/function completeLogin\(res\)/.test(app) && /status === 'mfa_required'[\s\S]{0,200}LoginMfa\.promptCode/.test(app) && /mfa_setup_required[\s\S]{0,200}LoginMfa\.promptSetup/.test(app), 'app.js: đăng nhập rẽ nhánh MFA');
ok(/MfaCard\.onEnter\(\)/.test(app), 'tab Bảo mật nạp thẻ MFA');

const els = new Map();
const mk = (id) => ({
  id, value: '', innerHTML: '', textContent: '', disabled: false, className: '', listeners: {}, parentNode: null, focused: false,
  classList: { _s: new Set(), add(c) { this._s.add(c); }, remove(c) { this._s.delete(c); }, contains(c) { return this._s.has(c); } },
  addEventListener(t, f) { this.listeners[t] = f; }, insertBefore() {}, focus() { this.focused = true; },
  get nextSibling() { return null; },
});
const el = (id) => { if (!els.has(id)) els.set(id, mk(id)); return els.get(id); };
el('login-form').parentNode = { insertBefore: () => {} };
let routes = {};
const calls = [];
const logins = [];
const toasts = [];
const stub = {
  document: { getElementById: el, createElement: () => mk('login-mfa-panel') },
  _esc: (s) => String(s).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;'),
  API_BASE: '', completeLogin: (r) => logins.push(r), showToast: (m, t) => toasts.push([t, m]),
  fetch: async (url, opts) => { const body = JSON.parse(opts.body || '{}'); calls.push([url, opts.headers.Authorization || '', body]); const r = routes[url]; return { ok: r.status < 400, status: r.status, json: async () => r.body }; },
  apiFetch: async (url, opts = {}) => { const body = opts.body ? JSON.parse(opts.body) : null; calls.push([url, opts.method || 'GET', body]); const r = routes[url]; return { ok: r.status < 400, status: r.status, json: async () => r.body }; },
  window: {},
};
const names = Object.keys(stub);
const { LoginMfa, MfaCard } = new Function(...names, `${src}\nreturn { LoginMfa, MfaCard };`)(...names.map((n) => stub[n]));
const flush = () => new Promise((r) => setTimeout(r, 0));
const panel = () => el('login-mfa-panel');

// 1. nhập mã
LoginMfa.promptCode('MFA.TOKEN.X');
ok(!panel().classList.contains('hidden') && el('login-form').classList.contains('hidden'), 'ẩn form mật khẩu, hiện ô nhập mã');
ok(/Xác thực hai lớp/.test(panel().innerHTML) && /autocomplete="one-time-code"/.test(panel().innerHTML), 'có ô mã, gợi ý tự điền mã một lần');
await el('login-mfa-submit').listeners.click(); await flush();
ok(/Nhập mã xác thực/.test(el('login-mfa-error').textContent) && calls.length === 0, 'ô trống: không gọi máy chủ');
el('login-mfa-code').value = ' 123456 ';
routes['/api/v1/login/mfa'] = { status: 401, body: { detail: 'Mã không đúng hoặc đã dùng rồi.' } };
await el('login-mfa-submit').listeners.click(); await flush();
ok(calls[0][0] === '/api/v1/login/mfa' && calls[0][2].mfa_token === 'MFA.TOKEN.X' && calls[0][2].code === '123456', 'gửi token trung gian + mã đã cắt khoảng trắng');
ok(/Mã không đúng/.test(el('login-mfa-error').textContent) && logins.length === 0, 'sai mã: báo lỗi, chưa đăng nhập');
routes['/api/v1/login/mfa'] = { status: 200, body: { status: 'success', access_token: 'ACCESS', user: { username: 'an', role: 'admin' } } };
await el('login-mfa-submit').listeners.click(); await flush();
ok(logins.length === 1 && logins[0].access_token === 'ACCESS' && !el('login-form').classList.contains('hidden'), 'đúng mã: hoàn tất đăng nhập');
LoginMfa.promptCode('T');
el('login-mfa-code').value = 'x';
routes['/api/v1/login/mfa'] = { status: 401, body: { detail: 'Phiên xác thực đã hết hạn — hãy đăng nhập lại từ đầu.' } };
await el('login-mfa-submit').listeners.click(); await flush();
ok(/hết hạn/.test(el('login-mfa-error').textContent), 'token trung gian hết hạn: nói rõ');

// 2. cài MFA bắt buộc
calls.length = 0; logins.length = 0;
routes['/api/v1/auth/mfa/setup'] = { status: 200, body: { secret: 'ABCDEFGHIJKLMNOP', secret_grouped: 'ABCD EFGH IJKL MNOP', otpauth_uri: 'otpauth://totp/VN-MateAI:an?secret=ABCDEFGHIJKLMNOP' } };
await LoginMfa.promptSetup('SETUP.TOKEN');
ok(calls[0][0] === '/api/v1/auth/mfa/setup' && calls[0][1] === 'Bearer SETUP.TOKEN', 'gọi setup bằng token cài MFA');
ok(/ABCD EFGH IJKL MNOP/.test(panel().innerHTML) && /bắt buộc/.test(panel().innerHTML), 'hiện khoá thủ công + lý do bắt buộc');
ok(!/<img/.test(panel().innerHTML) && /nhập khoá thủ công/.test(panel().innerHTML), 'không có QR: chỉ hướng dẫn nhập khoá');
routes['/api/v1/auth/mfa/setup'].body.qr_svg = 'data:image/svg+xml;charset=utf-8,%3Csvg%3E';
await LoginMfa.promptSetup('SETUP.TOKEN');
ok(/<img src="data:image\/svg\+xml/.test(panel().innerHTML) && /quét mã QR/.test(panel().innerHTML), 'có QR: hiện ảnh để quét');
routes['/api/v1/auth/mfa/setup'].body.qr_svg = 'javascript:alert(1)';
await LoginMfa.promptSetup('SETUP.TOKEN');
ok(!/<img/.test(panel().innerHTML), 'QR không phải ảnh SVG: bỏ qua');
calls.length = 0;
await LoginMfa.promptSetup('SETUP.TOKEN');
el('login-mfa-code').value = '000000';
routes['/api/v1/auth/mfa/enable'] = { status: 400, body: { detail: 'Mã không đúng hoặc đã hết hạn' } };
await el('login-mfa-submit').listeners.click(); await flush();
ok(/Mã không đúng/.test(el('login-mfa-error').textContent) && logins.length === 0, 'mã sai: chưa bật, chưa đăng nhập');
routes['/api/v1/auth/mfa/enable'] = { status: 200, body: { recovery_codes: ['AAAAA-BBBBB', '<b>X</b>'], access_token: 'FULL', user: { username: 'an', role: 'admin' } } };
await el('login-mfa-submit').listeners.click(); await flush();
ok(/AAAAA-BBBBB/.test(panel().innerHTML) && /&lt;b&gt;X/.test(panel().innerHTML) && !/<b>X<\/b>/.test(panel().innerHTML), 'hiện mã khôi phục (thoát HTML)');
ok(/không hiện lại/.test(panel().innerHTML) && logins.length === 0, 'chưa vào hệ thống cho tới khi người dùng xác nhận đã lưu');
el('login-mfa-saved').listeners.click();
ok(logins.length === 1 && logins[0].access_token === 'FULL', 'xác nhận đã lưu -> vào hệ thống bằng phiên đầy đủ');
routes['/api/v1/auth/mfa/setup'] = { status: 409, body: { detail: 'MFA đã bật' } };
await LoginMfa.promptSetup('SETUP.TOKEN');
ok(/MFA đã bật/.test(panel().innerHTML), 'setup lỗi: nói rõ');

// 3. thẻ trong tab Bảo mật
const card = () => el('mfa-card-root').innerHTML;
routes['/api/v1/auth/mfa/status'] = { status: 200, body: { enabled: false, pending_setup: false, recovery_codes_left: null, auth_source: 'local', required_for_my_role: false } };
await MfaCard.onEnter();
ok(/Bật MFA/.test(card()), 'chưa bật: có nút Bật MFA');
routes['/api/v1/auth/mfa/status'] = { status: 200, body: { enabled: true, recovery_codes_left: 7, auth_source: 'local', required_for_my_role: false } };
await MfaCard.onEnter();
ok(/Đang bật — còn 7 mã khôi phục/.test(card()) && /Tắt MFA/.test(card()), 'đã bật: số mã khôi phục còn lại + nút tắt');
routes['/api/v1/auth/mfa/status'] = { status: 200, body: { enabled: true, recovery_codes_left: 7, auth_source: 'local', required_for_my_role: true } };
await MfaCard.onEnter();
ok(!/Tắt MFA/.test(card()) && /bắt buộc MFA/.test(card()), 'vai trò bắt buộc: không có nút tắt');
routes['/api/v1/auth/mfa/status'] = { status: 200, body: { enabled: false, auth_source: 'sso' } };
await MfaCard.onEnter();
ok(/SSO/.test(card()) && !/Bật MFA/.test(card()), 'tài khoản SSO: không cài MFA cục bộ');
routes['/api/v1/auth/mfa/status'] = { status: 500, body: { detail: 'lỗi <script>' } };
await MfaCard.onEnter();
ok(/Không tải được/.test(card()) && !/<script>/.test(card()), 'lỗi máy chủ: báo lỗi, thoát HTML');

console.log(`OK ${passed} kiểm tra giao diện MFA`);
