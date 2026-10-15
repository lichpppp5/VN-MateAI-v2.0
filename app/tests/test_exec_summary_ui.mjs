// SPDX-License-Identifier: Apache-2.0
// Copyright (c) 2026 Dương Thanh Lịch — VN-MateAI. See LICENSE and NOTICE.
// Tổng quan điều hành cho giám đốc (web/exec-summary.js, thẩm định #22): số thật từ API, so tháng trước, việc chờ quyết định.
import { readFileSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';

const ROOT = join(dirname(fileURLToPath(import.meta.url)), '..');
const src = readFileSync(join(ROOT, 'web', 'exec-summary.js'), 'utf8');
const app = readFileSync(join(ROOT, 'web', 'app.js'), 'utf8').replace(/\r\n/g, '\n');
const html = readFileSync(join(ROOT, 'web', 'index.html'), 'utf8').replace(/\r\n/g, '\n');
let passed = 0;
const ok = (c, n) => { if (!c) { console.error(`FAIL ${n}`); process.exit(1); } passed += 1; };

ok(/id="exec-summary-card"[^>]*data-min-role="manager"/.test(html), 'thẻ chỉ cho manager / admin');
ok(html.indexOf('id="exec-summary-card"') < html.indexOf('id="supervisor-card"'), 'thẻ nằm đầu Bảng điều khiển, trên phần kỹ thuật');
ok(/exec-summary\.js/.test(html) && html.indexOf('exec-summary.js') < html.indexOf('/static/app.js'), 'nạp script trước app.js');
ok(/tabId === 'dashboard'[\s\S]{0,300}ExecSummary\.onEnter\(\)/.test(app), 'vào Bảng điều khiển thì nạp tổng quan');
ok(!/on[a-z]+="/.test(src), 'không có thuộc tính on* (CSP chặt)');

const els = new Map();
const mk = (id) => ({
  id, innerHTML: '', textContent: '', listeners: {},
  classList: { _s: new Set(), add(c) { this._s.add(c); }, contains(c) { return this._s.has(c); } },
  addEventListener(t, f) { this.listeners[t] = f; },
});
const el = (id) => { if (!els.has(id)) els.set(id, mk(id)); return els.get(id); };
let route = { status: 200, body: {} };
const toggled = [];
const stub = {
  document: { getElementById: (id) => (id === 'exec-open-inbox' && !/exec-open-inbox/.test(el('exec-summary-body').innerHTML) ? null : el(id)),
              addEventListener() {} },
  _esc: (s) => String(s).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;'),
  API_BASE: '', getAuthToken: () => 'tok', apiErrorText: (d, s) => d.detail || `HTTP ${s}`,
  apiFetch: async (url) => { ok(url === '/api/v1/reports/executive-summary', 'gọi đúng API'); return { ok: route.status < 400, status: route.status, json: async () => route.body }; },
  HeaderInbox: { toggle: (k) => toggled.push(k) },
  window: {},
};
const names = Object.keys(stub);
const { ExecSummary } = new Function(...names, `${src}\nreturn { ExecSummary };`)(...names.map((n) => stub[n]));
const body = () => el('exec-summary-body').innerHTML;

route.body = { month: '2026-10', prev_month: '2026-09', ai_completed: 120, ai_completed_prev: 100, success_rate: 0.9, success_rate_prev: 0.95,
  hours_saved_est: 30, hours_per_task: 0.25, llm_cost: 12.5, llm_cost_prev: 10, approvals_pending: 2, approvals_high_risk: 1,
  incidents_open: 1, attention_total: 1, attention: [{ title: 'Ổ đĩa <b>đầy</b>', status: 'ESCALATED' }] };
await ExecSummary.onEnter();
ok(/120/.test(body()) && /▲ 20% so với tháng trước/.test(body()), 'việc làm xong tăng 20%: mũi tên lên');
ok(/90%/.test(body()) && /▼ 5% so với tháng trước/.test(body()), 'tỉ lệ thành công giảm');
ok(/30 giờ/.test(body()) && /giả định 0,25 giờ \/ việc/.test(body()), 'giờ tiết kiệm ghi rõ là ước tính + giả định');
ok(/text-rose-600[^>]*>▲ 25%/.test(body()), 'chi phí tăng tô màu xấu');
ok(/Đang chờ anh\/chị quyết định/.test(body()) && /rủi ro cao/.test(body()) && /Cần người xử lý/.test(body()), 'việc chờ quyết định');
ok(/Ổ đĩa &lt;b&gt;đầy/.test(body()) && !/<b>đầy<\/b>/.test(body()), 'tiêu đề tác vụ được thoát HTML');
ok(/tháng 10\/2026/.test(el('exec-summary-month').textContent), 'nhãn tháng');
el('exec-open-inbox').listeners.click({ stopPropagation() {} });
ok(toggled[0] === 'mail', 'nút mở hộp duyệt');

route.body = { ...route.body, ai_completed_prev: 0, approvals_pending: 0, approvals_high_risk: 0, incidents_open: 0, attention_total: 0, attention: [] };
await ExecSummary.onEnter();
ok(/chưa có tháng trước để so/.test(body()) && /Không có việc nào đang chờ/.test(body()) && !/exec-open-inbox/.test(body()), 'tháng đầu + không có việc chờ');

route = { status: 500, body: { detail: 'lỗi <x>' } };
await ExecSummary.onEnter();
ok(/Không tải được tổng quan/.test(body()) && !/<x>/.test(body()), 'lỗi máy chủ: nói rõ, thoát HTML');
route = { status: 403, body: {} };
await ExecSummary.onEnter();
ok(el('exec-summary-card').classList.contains('hidden'), 'không đủ quyền: ẩn thẻ');

console.log(`OK ${passed} kiểm tra tổng quan điều hành`);
