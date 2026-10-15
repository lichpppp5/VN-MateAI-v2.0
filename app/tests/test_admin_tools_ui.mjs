// SPDX-License-Identifier: Apache-2.0
// Copyright (c) 2026 Dương Thanh Lịch — VN-MateAI. See LICENSE and NOTICE.
// Hai việc của quản trị viên trước đây chỉ có API: gỡ MFA một tài khoản (mất điện thoại) và kiểm tra toàn vẹn chuỗi audit.
import { readFileSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';

const ROOT = join(dirname(fileURLToPath(import.meta.url)), '..');
const app = readFileSync(join(ROOT, 'web', 'app.js'), 'utf8').replace(/\r\n/g, '\n');
const html = readFileSync(join(ROOT, 'web', 'index.html'), 'utf8');
let passed = 0;
const ok = (c, n) => { if (!c) { console.error(`FAIL ${n}`); process.exit(1); } passed += 1; };
const fn = (name) => { const m = app.match(new RegExp(`async function ${name}\\([\\s\\S]*?\\n}\\n`)); ok(m, `có hàm ${name}`); return m[0]; };

ok(/onclick="resetUserMfa\('\$\{escapeHtml\(u\.id\)\}', '\$\{escapeHtml\(u\.username\)\}'\)"/.test(app), 'mỗi dòng tài khoản có nút gỡ MFA');
ok(/onclick="verifyAuditChain\(\)"/.test(html), 'khối nhật ký kiểm toán có nút kiểm tra toàn vẹn');

let route = { status: 200, body: {} };
const calls = [];
const toasts = [];
let confirmAnswer = true;
const stub = {
  API_BASE: '', confirm: () => confirmAnswer, showToast: (m, t) => toasts.push([t, m]), apiErrorText: (d, s) => d.detail || `HTTP ${s}`,
  apiFetch: async (url, opts = {}) => { calls.push([url, opts.method || 'GET']); return { ok: route.status < 400, status: route.status, json: async () => route.body }; },
};
const names = Object.keys(stub);
const { resetUserMfa, verifyAuditChain } = new Function(...names, `${fn('resetUserMfa')}\n${fn('verifyAuditChain')}\nreturn { resetUserMfa, verifyAuditChain };`)(...names.map((n) => stub[n]));

confirmAnswer = false;
await resetUserMfa('u 1', 'an');
ok(calls.length === 0, 'huỷ hộp xác nhận: không gọi API');
confirmAnswer = true;
await resetUserMfa('u 1', 'an');
ok(calls[0][0] === '/api/v1/users/u%201/mfa/reset' && calls[0][1] === 'POST' && toasts.at(-1)[0] === 'success', 'gỡ MFA: POST đúng tài khoản');
route = { status: 404, body: { detail: 'Không có tài khoản này.' } };
await resetUserMfa('x', 'x');
ok(toasts.at(-1)[0] === 'error' && /Không có tài khoản/.test(toasts.at(-1)[1]), 'lỗi: nói rõ');

route = { status: 200, body: { ok: true, verified: 1234, anchors: { ok: true, anchors_checked: 3, broken: [] } } };
await verifyAuditChain();
ok(toasts.at(-1)[0] === 'success' && /nguyên vẹn/.test(toasts.at(-1)[1]) && /3 điểm neo/.test(toasts.at(-1)[1]), 'chuỗi nguyên vẹn');
route = { status: 200, body: { ok: false, verified: 10, first_broken_id: 11, anchors: { ok: true } } };
await verifyAuditChain();
ok(toasts.at(-1)[0] === 'error' && /dòng #11/.test(toasts.at(-1)[1]), 'chuỗi bị sửa: chỉ đúng dòng');
route = { status: 200, body: { ok: true, verified: 10, anchors: { ok: false, broken: [{ id: 5 }] } } };
await verifyAuditChain();
ok(toasts.at(-1)[0] === 'error' && /1 điểm neo/.test(toasts.at(-1)[1]), 'chuỗi tự khớp nhưng lệch neo: vẫn cảnh báo');

console.log(`OK ${passed} kiểm tra công cụ quản trị`);
