// SPDX-License-Identifier: Apache-2.0
// Copyright (c) 2026 Dương Thanh Lịch — VN-MateAI. See LICENSE and NOTICE.
// tests/test_api_json_header.mjs
// apiFetch phải gửi JSON đúng kiểu. Lỗi cũ: nút "Tôi đã kiểm tra — xác nhận hoàn thành" (và 3 nút khác)
// gửi body JSON không kèm Content-Type -> trình duyệt đặt text/plain -> máy chủ trả 422 -> tác vụ
// không bao giờ rời danh sách "Cần người xác nhận", còn thông báo lỗi hiện "[object Object]".
import { readFileSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';

const ROOT = join(dirname(fileURLToPath(import.meta.url)), '..');
const app = readFileSync(join(ROOT, 'web', 'app.js'), 'utf8').replace(/\r\n/g, '\n');

function cut(startMark) {
  const a = app.indexOf(startMark);
  const b = app.indexOf('\n}\n', a);
  if (a < 0 || b < 0) throw new Error('không tìm thấy: ' + startMark);
  return app.slice(a, b + 3);
}

let seen = null;
const env = {
  fetch: async (url, opts) => { seen = { url, opts }; return { status: 200, ok: true }; },
  getAuthToken: () => 'tok123',
  handleLogout: () => {},
};
const factory = new Function('fetch', 'getAuthToken', 'handleLogout',
  `${cut('function apiErrorText(data, status) {')}\n${cut('async function apiFetch(url, options = {}) {')}\nreturn { apiFetch, apiErrorText };`);
const { apiFetch, apiErrorText } = factory(env.fetch, env.getAuthToken, env.handleLogout);

let passed = 0;
function ok(cond, name) {
  if (!cond) { console.error(`FAIL ${name}`); process.exit(1); }
  passed += 1;
}

// ── 1. body chuỗi không khai báo kiểu -> JSON ───────────────────────────────
await apiFetch('/api/v1/ops/tasks/OP-1/confirm', { method: 'POST', body: JSON.stringify({ note: '' }) });
ok(seen.opts.headers['Content-Type'] === 'application/json', 'body JSON tự gắn Content-Type: application/json');
ok(seen.opts.headers.Authorization === 'Bearer tok123', 'vẫn gắn Authorization');

// ── 2. đã khai báo (mọi cách viết hoa) thì giữ nguyên ───────────────────────
await apiFetch('/x', { method: 'POST', headers: { 'content-type': 'text/csv' }, body: 'a,b' });
ok(seen.opts.headers['content-type'] === 'text/csv' && !seen.opts.headers['Content-Type'], 'giữ kiểu đã khai báo');
await apiFetch('/x', { method: 'PUT', headers: { 'Content-Type': 'application/json; charset=utf-8' }, body: '{}' });
ok(seen.opts.headers['Content-Type'] === 'application/json; charset=utf-8', 'giữ nguyên giá trị đã khai báo');

// ── 3. không có body / body không phải chuỗi -> không đụng ──────────────────
await apiFetch('/api/v1/health-dashboard');
ok(!('Content-Type' in seen.opts.headers), 'GET không gắn Content-Type');
const form = { append() {} };                      // giả FormData: không phải chuỗi
await apiFetch('/upload', { method: 'POST', body: form });
ok(!('Content-Type' in seen.opts.headers), 'FormData: để trình duyệt tự đặt boundary');

// ── 4. thông báo lỗi đọc được ───────────────────────────────────────────────
ok(apiErrorText({ detail: 'Chỉ xác nhận được tác vụ đang ESCALATED' }, 409) === 'Chỉ xác nhận được tác vụ đang ESCALATED', 'detail chuỗi');
const e422 = { detail: [{ type: 'model_attributes_type', loc: ['body'], msg: 'Input should be a valid dictionary or object' }] };
ok(apiErrorText(e422, 422) === 'dữ liệu: Input should be a valid dictionary or object', 'mảng 422 -> chữ, không "[object Object]"');
ok(apiErrorText({ detail: [{ loc: ['body', 'note'], msg: 'quá dài' }] }, 422) === 'note: quá dài', 'chỉ rõ trường');
ok(apiErrorText({ detail: { message: 'Cấu hình sai' } }, 400) === 'Cấu hình sai', 'detail là đối tượng');
ok(apiErrorText({}, 500) === 'HTTP 500' && apiErrorText(null, 502) === 'HTTP 502', 'không có detail');

// ── 5. nút xác nhận dùng hàm lỗi chung ──────────────────────────────────────
ok(/throw new Error\(apiErrorText\(data, res\.status\)\);\n    showToast\(action === 'confirm'/.test(app), 'decideOpTask dùng apiErrorText');

console.log(`test_api_json_header: ${passed} passed`);
