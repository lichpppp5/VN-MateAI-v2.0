// SPDX-License-Identifier: Apache-2.0
// Copyright (c) 2026 Dương Thanh Lịch — VN-MateAI. See LICENSE and NOTICE.
// "Quên mật khẩu?": form trên màn đăng nhập gửi đúng API công khai, hiện câu máy chủ trả; admin thấy yêu cầu trong Hộp thư
// với nút cấp mật khẩu tạm (mở modal sẵn có) / bỏ qua. Không handler nội tuyến (CSP chặt).
import { readFileSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';

const ROOT = join(dirname(fileURLToPath(import.meta.url)), '..');
const src = readFileSync(join(ROOT, 'web', 'password-reset.js'), 'utf8');
const inbox = readFileSync(join(ROOT, 'web', 'header-inbox.js'), 'utf8');
const html = readFileSync(join(ROOT, 'web', 'index.html'), 'utf8');
let passed = 0;
const ok = (c, n) => { if (!c) { console.error(`FAIL ${n}`); process.exit(1); } passed += 1; };

ok(/id="btn-forgot-password"/.test(html) && /id="forgot-form"/.test(html) && /src="\/static\/password-reset\.js/.test(html), 'markup + nạp script');
ok(html.indexOf('id="forgot-form"') > html.indexOf('id="login-form"') && html.indexOf('id="forgot-form"') < html.indexOf('id="login-attribution"'), 'form nằm trong thẻ đăng nhập');
ok(!/onclick=|onsubmit=|oninput=/.test(src), 'không handler nội tuyến');
ok(/data-hi="pw-grant"/.test(inbox) && /openChangePasswordModal\(t\.dataset\.id, t\.dataset\.name\)/.test(inbox), 'hộp thư: cấp mật khẩu tạm mở modal sẵn có');
ok(/\/api\/v1\/auth\/password-reset\/requests/.test(inbox) && /role\(\) === 'admin'/.test(inbox), 'hộp thư: chỉ admin tải yêu cầu');

// chạy thử với DOM giả
const els = {};
const el = (id) => (els[id] ||= { id, value: '', textContent: '', className: '', disabled: false, listeners: {},
  classList: { s: new Set(['hidden']), toggle(c, on) { on ? this.s.add(c) : this.s.delete(c); }, add(c) { this.s.add(c); }, contains(c) { return this.s.has(c); } },
  addEventListener(t, f) { this.listeners[t] = f; }, focus() {} });
el('login-form').classList.s.delete('hidden');
el('forgot-link-row').classList.s.delete('hidden');
const calls = [];
const fetch = async (url, opts) => { calls.push([url, JSON.parse(opts.body)]); return { ok: true, status: 200, json: async () => ({ message: 'CÂU TRUNG TÍNH' }) }; };
const document = { readyState: 'complete', getElementById: el, addEventListener() {} };
const PR = new Function('document', 'fetch', `${src}\nreturn PasswordReset;`)(document, fetch);
el('login-username').value = 'nv01';
el('btn-forgot-password').listeners.click();
ok(el('login-form').classList.contains('hidden') && !el('forgot-form').classList.contains('hidden'), 'bấm "Quên mật khẩu?" → hiện form');
ok(el('forgot-username').value === 'nv01', 'điền sẵn tên đã gõ');
el('forgot-note').value = '0901';
await el('forgot-form').listeners.submit({ preventDefault() {} });
ok(calls.length === 1 && calls[0][0] === '/api/v1/login/forgot-password' && calls[0][1].username === 'nv01' && calls[0][1].note === '0901', 'gửi đúng API');
ok(el('forgot-status').textContent === 'CÂU TRUNG TÍNH', 'hiện đúng câu máy chủ trả');
el('btn-forgot-back').listeners.click();
ok(!el('login-form').classList.contains('hidden') && el('forgot-form').classList.contains('hidden'), 'quay lại đăng nhập');
el('forgot-username').value = '';
el('btn-forgot-password').listeners.click();
el('forgot-username').value = '';
await el('forgot-form').listeners.submit({ preventDefault() {} });
ok(calls.length === 1, 'thiếu tên đăng nhập → không gửi');

console.log(`password reset UI: ${passed} kiểm tra đạt`);
