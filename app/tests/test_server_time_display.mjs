// SPDX-License-Identifier: Apache-2.0
// Copyright (c) 2026 Dương Thanh Lịch — VN-MateAI. See LICENSE and NOTICE.
// tests/test_server_time_display.mjs
// web/server-time.js — mốc thời gian của máy chủ hiển thị theo giờ địa phương.
//   1. ISO có T, không múi giờ = UTC -> +7 giờ ở Việt Nam (audit hiện sớm 7 giờ là lỗi cũ).
//   2. "YYYY-MM-DD HH:MM:SS" = giờ địa phương -> giữ nguyên.
//   3. Có hậu tố múi giờ -> theo hậu tố; rỗng / lạ -> không bịa.
//   4. Các màn hình portal dùng hàm chung, không còn cắt chuỗi UTC.
process.env.TZ = 'Asia/Ho_Chi_Minh';
import { readFileSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';

const ROOT = join(dirname(fileURLToPath(import.meta.url)), '..');
new Function(readFileSync(join(ROOT, 'web', 'server-time.js'), 'utf8'))();
const { fmtServerTime } = globalThis;

let passed = 0;
function eq(actual, expected, name) {
  if (actual !== expected) {
    console.error(`FAIL ${name}: ${JSON.stringify(actual)} !== ${JSON.stringify(expected)}`);
    process.exit(1);
  }
  passed += 1;
}

eq(fmtServerTime('2026-10-06T12:35:19.380060'), '2026-10-06 19:35:19', 'UTC ISO -> giờ VN');
eq(fmtServerTime('2026-10-06T20:10:00'), '2026-10-07 03:10:00', 'qua nửa đêm');
eq(fmtServerTime('2026-10-06 19:35:19'), '2026-10-06 19:35:19', 'giờ địa phương giữ nguyên');
eq(fmtServerTime('2026-10-06T12:35:19+07:00'), '2026-10-06 12:35:19', 'có múi giờ');
eq(fmtServerTime('2026-10-06T05:35:19Z', { seconds: false }), '2026-10-06 12:35', 'Z, bỏ giây');
eq(fmtServerTime('2026-10-06T05:35:19Z', { date: false }), '12:35:19', 'chỉ giờ');
eq(fmtServerTime(null), '—', 'rỗng');
eq(fmtServerTime('chưa kết nối'), 'chưa kết nối', 'chuỗi lạ giữ nguyên');

const app = readFileSync(join(ROOT, 'web', 'app.js'), 'utf8');
const html = readFileSync(join(ROOT, 'web', 'index.html'), 'utf8');
eq(html.indexOf('/static/server-time.js') > 0 && html.indexOf('/static/server-time.js') < html.indexOf('/static/app.js'),
  true, 'index.html nạp server-time.js trước app.js');
eq(/log\.timestamp\.replace\('T', ' '\)/.test(app), false, 'bảng audit không còn cắt chuỗi UTC');
eq(/saved_at\)\.replace\('T', ' '\)/.test(app), false, 'lịch sử cấu hình không còn cắt chuỗi UTC');

console.log(`test_server_time_display: ${passed} passed`);
