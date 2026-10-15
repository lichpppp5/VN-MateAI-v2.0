// SPDX-License-Identifier: Apache-2.0
// Copyright (c) 2026 Dương Thanh Lịch — VN-MateAI. See LICENSE and NOTICE.
// Tab / khối theo vai trò: một bảng ROLE_HIDDEN_TABS cho cả menu và điều hướng; viewer không mở được tab chỉ có "không có
// quyền"; khối kỹ thuật trên Bảng điều khiển có data-min-role; CSS không để .nav-btn (display:flex) đè .hidden.
import { readFileSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';

const ROOT = join(dirname(fileURLToPath(import.meta.url)), '..');
const app = readFileSync(join(ROOT, 'web', 'app.js'), 'utf8').replace(/\r\n/g, '\n');
const html = readFileSync(join(ROOT, 'web', 'index.html'), 'utf8').replace(/\r\n/g, '\n');
let passed = 0;
const ok = (c, n) => { if (!c) { console.error(`FAIL ${n}`); process.exit(1); } passed += 1; };

const grab = (name) => {
  const i = app.indexOf(`const ${name} = `);
  const start = i + `const ${name} = `.length;
  const end = app.indexOf(';\n', start);
  return new Function(`return (${app.slice(start, end)});`)();
};
const VALID = grab('VALID_TABS');
const HIDDEN = grab('ROLE_HIDDEN_TABS');
const RANK = grab('ROLE_RANK');

ok(HIDDEN.admin.length === 0, 'admin thấy mọi tab');
for (const role of ['manager', 'viewer']) {
  ok(HIDDEN[role].every((t) => VALID.includes(t)), `${role}: chỉ ẩn tab có thật`);
  ok(['config', 'security'].every((t) => HIDDEN[role].includes(t)), `${role}: không thấy Cấu hình / Bảo mật`);
}
ok(HIDDEN.manager.every((t) => HIDDEN.viewer.includes(t)), 'viewer bị ẩn ít nhất mọi thứ manager bị ẩn');
ok(['dashboard', 'tasks', 'logs'].every((t) => !HIDDEN.viewer.includes(t)), 'viewer vẫn có Bảng điều khiển / Nhật ký công việc / Nhật ký');
ok(RANK.viewer < RANK.manager && RANK.manager < RANK.admin, 'thứ bậc vai trò');

const sw = app.slice(app.indexOf('function switchTab(tabId) {'), app.indexOf('function switchTab(tabId) {') + 400);
ok(/tabAllowedForRole\(tabId, currentRoleName\(\)\)/.test(sw), 'switchTab chặn tab không dành cho vai trò');
ok(/applyRoleVisibility\(/.test(app.slice(app.indexOf('function applyRolePermissions'))), 'applyRolePermissions áp bảng vai trò');

const minRole = [...html.matchAll(/data-min-role="([a-z]+)"/g)].map((m) => m[1]);
ok(minRole.length >= 4 && minRole.every((r) => r in RANK), 'khối kỹ thuật trên Bảng điều khiển có data-min-role hợp lệ');
ok(/\.nav-btn\.hidden, \[data-min-role\]\.hidden \{ display: none !important; \}/.test(html), 'CSS: mục menu / khối bị ẩn không hiện lại');

console.log(`role tabs: ${passed} kiểm tra đạt`);
