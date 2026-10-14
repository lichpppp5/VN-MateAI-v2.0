// SPDX-License-Identifier: Apache-2.0
// Copyright (c) 2026 Dương Thanh Lịch — VN-MateAI. See LICENSE and NOTICE.
// tests/test_sup_attention.mjs
// Bảng điều khiển → AI Supervisor → "Cần chú ý": danh sách không được kéo dài trang.
//   1. Khung có chiều cao cố định + cuộn dọc (trước đây 15–20 dòng đẩy cả trang xuống).
//   2. Bộ lọc Tất cả / Cần xác nhận / Thất bại + đếm đúng; nút thu gọn.
//   3. Hiện rõ khi chỉ hiển thị một phần (server trả 20 mục mới nhất + tổng số thật).
import { readFileSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';

const ROOT = join(dirname(fileURLToPath(import.meta.url)), '..');
const app = readFileSync(join(ROOT, 'web', 'app.js'), 'utf8').replace(/\r\n/g, '\n');
const html = readFileSync(join(ROOT, 'web', 'index.html'), 'utf8').replace(/\r\n/g, '\n');

const a = app.indexOf('function supAttnView(items, filter) {');
const b = app.indexOf('\n}\n', a);
if (a < 0 || b < 0) throw new Error('không tìm thấy supAttnView');
const supAttnView = new Function(`${app.slice(a, b + 3)}; return supAttnView;`)();

let passed = 0;
function ok(cond, name) {
  if (!cond) { console.error(`FAIL ${name}`); process.exit(1); }
  passed += 1;
}

const mk = (n, status) => Array.from({ length: n }, (_, i) => ({ task_id: `${status}-${i}`, status }));
const items = [...mk(12, 'ESCALATED'), ...mk(2, 'WAITING_AUTHORIZATION'), ...mk(3, 'FAILED')];

let v = supAttnView(items, 'all');
ok(v.counts.all === 17 && v.counts.review === 14 && v.counts.failed === 3 && v.shown.length === 17, 'đếm + tất cả');
v = supAttnView(items, 'review');
ok(v.shown.length === 14 && v.shown.every((t) => t.status !== 'FAILED'), 'lọc cần xác nhận');
v = supAttnView(items, 'failed');
ok(v.shown.length === 3 && v.shown.every((t) => t.status === 'FAILED'), 'lọc thất bại');
ok(supAttnView(items, 'khong-co').shown.length === 17, 'bộ lọc lạ = tất cả');
ok(supAttnView(null, 'all').counts.all === 0 && supAttnView(undefined, 'failed').shown.length === 0, 'không có dữ liệu không nổ');

const box = html.match(/<div id="sup-attention"[^>]*>/)[0];
ok(/max-height:\s*16rem/.test(box) && /overflow-y:\s*auto/.test(box), 'khung có max-height + cuộn dọc');
for (const id of ['sup-attn-count', 'sup-attn-note', 'sup-attn-f-all', 'sup-attn-f-review', 'sup-attn-f-failed', 'sup-attn-toggle']) {
  ok(html.includes(`id="${id}"`), `có #${id}`);
}
ok(/renderSupervisorAttention\(d\.tasks\.attention \|\| \[\], d\.tasks\.attention_total\)/.test(app), 'truyền tổng số thật từ server');
ok(/localStorage\.getItem\('supAttnCollapsed'\)/.test(app) && /try \{ return localStorage/.test(app), 'nhớ thu gọn, bọc try/catch');

console.log(`test_sup_attention: ${passed} passed`);
