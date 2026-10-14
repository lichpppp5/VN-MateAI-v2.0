// SPDX-License-Identifier: Apache-2.0
// Copyright (c) 2026 Dương Thanh Lịch — VN-MateAI. See LICENSE and NOTICE.
// Nút SSO + xử lý khi quay về từ IdP (web/auth-sso.js): token không nằm trên URL, mã một lần bị xoá khỏi thanh địa chỉ ngay.
import { readFileSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';

const ROOT = join(dirname(fileURLToPath(import.meta.url)), '..');
const src = readFileSync(join(ROOT, 'web', 'auth-sso.js'), 'utf8');
const html = readFileSync(join(ROOT, 'web', 'index.html'), 'utf8').replace(/\r\n/g, '\n');
let passed = 0;
const ok = (c, n) => { if (!c) { console.error(`FAIL ${n}`); process.exit(1); } passed += 1; };
ok(/auth-sso\.js/.test(html) && html.indexOf('auth-sso.js') < html.indexOf('/static/app.js'), 'index.html nạp auth-sso.js trước app.js');

const state = { hash: '', search: '', replaced: [], errors: '', errorHidden: true, inserted: [], logins: [], cfg: { enabled: true, display_name: 'Đăng nhập bằng <Azure>' }, exchange: { status: 200, body: { access_token: 'T', user: { username: 'an' } } } };
const form = { parentNode: { insertBefore: (n) => state.inserted.push(n) }, nextSibling: null };
const stub = {
  document: { getElementById: (id) => (id === 'login-form' ? form : id === 'login-error-box' ? { classList: { remove: () => { state.errorHidden = false; } } } : id === 'login-error-text' ? { set textContent(v) { state.errors = v; } } : null), createElement: () => ({ set innerHTML(v) { this.html = v; }, get innerHTML() { return this.html; } }) },
  window: { get location() { return { hash: state.hash, search: state.search, pathname: '/' }; }, history: { replaceState: (a, b, url) => { state.replaced.push(url); state.hash = ''; state.search = ''; } }, addEventListener: () => {} },
  API_BASE: '', _esc: (s) => String(s).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;'),
  completeLogin: (d) => state.logins.push(d),
  fetch: async (url) => (url.endsWith('/sso/config') ? { ok: true, json: async () => state.cfg } : { ok: state.exchange.status < 400, status: state.exchange.status, json: async () => state.exchange.body }),
};
const names = Object.keys(stub);
const SsoLogin = new Function(...names, `${src}\nreturn SsoLogin;`)(...names.map((n) => stub[n]));

// quay về từ IdP thành công
state.hash = '#sso=' + encodeURIComponent('mã-một-lần_ABC');
await SsoLogin.init();
ok(state.replaced[0] === '/' && state.logins.length === 1 && state.logins[0].access_token === 'T', 'đổi mã -> hoàn tất đăng nhập, xoá mã khỏi URL');
// đổi mã thất bại
state.logins.length = 0; state.hash = '#sso=hetHan1234'; state.exchange = { status: 401, body: { detail: 'Mã đăng nhập SSO không hợp lệ hoặc đã dùng / hết hạn.' } };
await SsoLogin.handleReturn();
ok(state.logins.length === 0 && /hết hạn/.test(state.errors) && !state.errorHidden, 'mã hết hạn: hiện lỗi, không đăng nhập');
// lỗi từ máy chủ
state.errors = ''; state.search = '?sso_error=' + encodeURIComponent('Tên miền email không được phép');
await SsoLogin.handleReturn();
ok(/SSO thất bại: Tên miền email không được phép/.test(state.errors) && state.search === '', 'sso_error được hiện và xoá khỏi URL');
// nút
state.inserted.length = 0;
await SsoLogin.renderButton();
ok(state.inserted.length === 1 && /href="\/api\/v1\/sso\/login"/.test(state.inserted[0].innerHTML), 'SSO bật: có nút trỏ /api/v1/sso/login');
ok(/Đăng nhập bằng &lt;Azure&gt;/.test(state.inserted[0].innerHTML) && !/<Azure>/.test(state.inserted[0].innerHTML), 'tên nút được thoát HTML');
state.cfg = { enabled: false }; state.inserted.length = 0;
const stub2 = { ...stub, document: { ...stub.document, getElementById: (id) => (id === 'sso-login-btn' ? null : stub.document.getElementById(id)) } };
const Sso2 = new Function(...Object.keys(stub2), `${src}\nreturn SsoLogin;`)(...Object.values(stub2));
await Sso2.renderButton();
ok(state.inserted.length === 0, 'SSO tắt: không có nút');
console.log(`OK ${passed} kiểm tra giao diện SSO`);
