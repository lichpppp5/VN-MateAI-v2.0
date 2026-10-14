// SPDX-License-Identifier: Apache-2.0
// Copyright (c) 2026 Dương Thanh Lịch — VN-MateAI. See LICENSE and NOTICE.
// tests/test_header_inbox.mjs — hộp thư (✉) và chuông (🔔) trên header: dữ liệu thật, chấm đếm chỉ hiện khi có việc,
// thiếu quyền nói rõ, nút Duyệt chỉ cho admin, thoát HTML, không còn chấm đỏ cố định.
import { readFileSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';

const ROOT = join(dirname(fileURLToPath(import.meta.url)), '..');
const src = readFileSync(join(ROOT, 'web', 'header-inbox.js'), 'utf8');
const html = readFileSync(join(ROOT, 'web', 'index.html'), 'utf8').replace(/\r\n/g, '\n');
const app = readFileSync(join(ROOT, 'web', 'app.js'), 'utf8').replace(/\r\n/g, '\n');
let passed = 0;
const ok = (c, n) => { if (!c) { console.error(`FAIL ${n}`); process.exit(1); } passed += 1; };

ok(/id="mail-btn"[^>]*HeaderInbox\.toggle\('mail'\)/.test(html) && /id="bell-btn"[^>]*HeaderInbox\.toggle\('bell'\)/.test(html), 'hai nút có hàm xử lý');
ok(/id="mail-badge" class="hidden/.test(html) && /id="bell-badge" class="hidden/.test(html), 'chấm đếm mặc định ẩn');
ok(!/ring-2 ring-white dark:ring-slate-900 animate-pulse/.test(html), 'không còn chấm đỏ nhấp nháy cố định');
ok(/header-inbox\.js/.test(html) && /HeaderInbox\.start\(\)/.test(app) && /HeaderInbox\.stop\(\)/.test(app), 'nạp script, bật khi đăng nhập, tắt khi đăng xuất');

const els = new Map();
const mk = (id) => ({
  id, textContent: '', innerHTML: '', className: '', attrs: {}, dataset: {}, children: [], listeners: {},
  classList: {
    _s: new Set(['hidden']),
    add(c) { this._s.add(c); }, remove(c) { this._s.delete(c); },
    toggle(c, f) { if (f) this._s.add(c); else this._s.delete(c); }, contains(c) { return this._s.has(c); },
  },
  setAttribute(k, v) { this.attrs[k] = v; }, addEventListener(t, f) { this.listeners[t] = f; },
  querySelector(sel) { return sel === '[data-hi-panel]' ? this.children[0] || null : null; },
  appendChild(c) { this.children.push(c); },
});
const el = (id) => { if (!els.has(id)) els.set(id, mk(id)); return els.get(id); };
const panels = [];
const store = { vnmateai_user: JSON.stringify({ role: 'admin' }) };
let routes = {};
const calls = [];
const stub = {
  document: {
    getElementById: el, hidden: false, addEventListener: () => {},
    querySelectorAll: () => panels, createElement: () => { const p = mk('panel'); panels.push(p); return p; },
  },
  localStorage: { getItem: (k) => store[k] ?? null, setItem: (k, v) => { store[k] = String(v); } },
  setInterval: () => 1, clearInterval: () => {},
  _esc: (s) => String(s).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;'),
  API_BASE: '', getAuthToken: () => 'tok', friendlyErrorText: (e2) => String((e2 && e2.message) || e2),
  switchTab: (t) => calls.push(['tab', t]),
  CommandCenter: { decide: async (id, a) => { calls.push(['decide', id, a]); } },
  apiFetch: async (url) => {
    calls.push(['GET', url]);
    const r = routes[url.split('?')[0]];
    if (!r) return { ok: false, status: 404, json: async () => ({}) };
    return { ok: (r.status ?? 200) < 400, status: r.status ?? 200, json: async () => r.body };
  },
  window: {},
};
const names = Object.keys(stub);
const HeaderInbox = new Function(...names, `${src}\nreturn HeaderInbox;`)(...names.map((n) => stub[n]));
const flush = () => new Promise((r) => setTimeout(r, 0));
['mail-wrap', 'bell-wrap', 'mail-btn', 'bell-btn', 'mail-badge', 'bell-badge'].forEach(el);

const nowSec = Date.now() / 1000;
const base = () => ({
  '/api/v1/enterprise/hitl/pending': { body: { pending_approvals: [{ id: 'AP-1', action_name: 'dev_fleet_dispatch', requested_by: 'AI_Agent', risk_level: 4, description: 'Giao <b>việc</b>' }] } },
  '/api/v1/ops/overview': {
    body: {
      tasks: {
        attention: [
          { task_id: 'OP-1', kind: 'agent_turn', status: 'ESCALATED', title: 'Cần xác nhận' },
          { task_id: 'OP-2', kind: 'dev_task', status: 'FAILED', title: 'Hỏng' },
          { task_id: 'OP-3', kind: 'incident', status: 'ESCALATED', title: 'Sự cố (thuộc chuông)' },
          { task_id: 'OP-4', kind: 'agent_turn', status: 'WAITING_AUTHORIZATION', title: 'đã đếm ở duyệt' },
        ],
      },
    },
  },
  '/api/v1/ops/incidents': {
    body: {
      incidents: [
        { task_id: 'OP-9', status: 'ESCALATED', title: 'IIS 503', incident_phase: 'DETECTED', created_at: '2026-10-07 10:00:00', affected_assets: 'web-01' },
        { task_id: 'OP-8', status: 'COMPLETED', title: 'đã đóng' },
      ],
    },
  },
  '/api/v1/system/notifications': {
    body: {
      history: [
        { time: nowSec - 60, title: 'Mất kết nối <i>AD</i>', severity: 'critical', source: 'Sentinel', resolved: false, delivered: 2 },
        { time: nowSec - 600, title: 'Đã ổn', severity: 'warning', source: 'Sentinel', resolved: true, delivered: 1 },
      ],
    },
  },
});

// 1. nạp: chấm đếm = số thật (không trùng lặp)
routes = base();
HeaderInbox.start(); await flush(); await flush(); await flush();
ok(el('mail-badge').textContent === '3' && !el('mail-badge').classList.contains('hidden'), 'hộp thư: 1 chờ duyệt + 2 tác vụ cần xử lý (không đếm sự cố, không đếm trùng chờ-duyệt)');
ok(el('bell-badge').textContent === '2' && !el('bell-badge').classList.contains('hidden'), 'chuông: 1 sự cố mở + 1 cảnh báo chưa xem (đã đóng / đã khôi phục không tính)');

// 2. mở hộp thư
await HeaderInbox.toggle('mail');
const mailPanel = el('mail-wrap').children[0];
ok(!mailPanel.classList.contains('hidden') && el('mail-btn').attrs['aria-expanded'] === 'true', 'mở bảng + aria-expanded');
ok(/AP-1/.test(mailPanel.innerHTML) && /Duyệt/.test(mailPanel.innerHTML) && /Từ chối/.test(mailPanel.innerHTML), 'admin thấy nút Duyệt / Từ chối');
ok(/&lt;b&gt;việc/.test(mailPanel.innerHTML) && !/<b>việc/.test(mailPanel.innerHTML), 'mô tả được thoát HTML');
ok(/OP-1/.test(mailPanel.innerHTML) && /OP-2/.test(mailPanel.innerHTML) && !/OP-3/.test(mailPanel.innerHTML) && !/OP-4/.test(mailPanel.innerHTML), 'chỉ hiện tác vụ cần xử lý, không lẫn sự cố / mục chờ duyệt');
await mailPanel.listeners.click({ target: { closest: () => ({ dataset: { hi: 'approve', id: 'AP-1' }, disabled: false }) } });
ok(calls.some((c) => c[0] === 'decide' && c[1] === 'AP-1' && c[2] === true), 'Duyệt gọi CommandCenter.decide');
await mailPanel.listeners.click({ target: { closest: () => ({ dataset: { hi: 'dashboard' } }) } });
ok(calls.some((c) => c[0] === 'tab' && c[1] === 'dashboard') && mailPanel.classList.contains('hidden'), 'liên kết sang Bảng điều khiển và đóng bảng');

// 3. chuông: sự cố + cảnh báo thật; mở = đánh dấu đã xem
await HeaderInbox.toggle('bell');
const bellPanel = el('bell-wrap').children[0];
ok(/IIS 503/.test(bellPanel.innerHTML) && /web-01/.test(bellPanel.innerHTML) && !/đã đóng/.test(bellPanel.innerHTML), 'chuông: sự cố đang mở, bỏ sự cố đã đóng');
ok(/Mất kết nối &lt;i&gt;AD/.test(bellPanel.innerHTML) && /Đã khôi phục/.test(bellPanel.innerHTML) && /gửi 2 kênh/.test(bellPanel.innerHTML), 'chuông: cảnh báo thoát HTML, ghi số kênh đã gửi, đánh dấu đã khôi phục');
ok(el('bell-badge').textContent === '1' && Number(store.vnmateai_bell_seen) > nowSec - 5, 'mở chuông: cảnh báo coi là đã xem, còn lại sự cố đang mở');

// 3b. hạ tầng (Prometheus / Grafana): hiện thành mục riêng, KHÔNG cộng vào số đếm (cảnh báo nặng đã là sự cố)
routes = { ...base(), '/api/v1/monitoring/overview': { body: { configured: true, alerts: [
  { source: 'prom-1', name: 'HighCPU <b>x</b>', severity: 'critical', state: 'firing', instance: 'srv-1:9100' },
  { source: 'prom-1', name: 'DiskLow', severity: 'warning', state: 'pending', instance: 'srv-2:9100' }] } } };
await HeaderInbox.refresh();
await HeaderInbox.toggle('bell');
const infraPanel = el('bell-wrap').children[0];
ok(/Hạ tầng đang cảnh báo/.test(infraPanel.innerHTML) && /HighCPU &lt;b&gt;x/.test(infraPanel.innerHTML) && !/DiskLow/.test(infraPanel.innerHTML), 'chuông: mục hạ tầng chỉ gồm cảnh báo đang bắn, thoát HTML');
ok(el('bell-badge').textContent === '1', 'số đếm không cộng đôi cảnh báo hạ tầng');
await infraPanel.listeners.click({ target: { closest: () => ({ dataset: { hi: 'infra' } }) } });
ok(calls.some((c) => c[0] === 'tab' && c[1] === 'infra-monitor'), 'liên kết mở tab Giám sát hạ tầng');

// 4. rỗng: không hiện số giả
routes = {
  ...base(),
  '/api/v1/enterprise/hitl/pending': { body: { pending_approvals: [] } },
  '/api/v1/ops/overview': { body: { tasks: { attention: [] } } },
  '/api/v1/ops/incidents': { body: { incidents: [] } },
  '/api/v1/system/notifications': { body: { history: [] } },
};
await HeaderInbox.refresh();
ok(el('mail-badge').classList.contains('hidden') && el('bell-badge').classList.contains('hidden'), 'không có việc: ẩn cả hai chấm');
await HeaderInbox.toggle('bell');
ok(/Không có sự cố đang mở/.test(el('bell-wrap').children[0].innerHTML), 'chuông rỗng nói rõ');
await HeaderInbox.toggle('bell');

// 5. phân quyền
store.vnmateai_user = JSON.stringify({ role: 'manager' });
routes = base();
await HeaderInbox.refresh(); await HeaderInbox.toggle('mail');
ok(!/data-hi="approve"/.test(el('mail-wrap').children[0].innerHTML) && /Cần quyền Admin/.test(el('mail-wrap').children[0].innerHTML), 'manager không có nút Duyệt');
await HeaderInbox.toggle('mail');
routes = { ...base(), '/api/v1/ops/incidents': { status: 403, body: {} }, '/api/v1/system/notifications': { status: 403, body: {} }, '/api/v1/ops/overview': { status: 403, body: {} } };
await HeaderInbox.refresh();
ok(el('bell-badge').classList.contains('hidden'), 'thiếu quyền: không hiện số');
await HeaderInbox.toggle('bell');
ok(/Cần quyền manager hoặc admin/.test(el('bell-wrap').children[0].innerHTML), 'thiếu quyền: nói rõ cần quyền gì');
await HeaderInbox.toggle('bell');

// 6. máy chủ lỗi
routes = {};
await HeaderInbox.refresh();
await HeaderInbox.toggle('bell');
ok(/Không tải được/.test(el('bell-wrap').children[0].innerHTML) && el('bell-badge').classList.contains('hidden'), 'API lỗi: báo lỗi, không hiện số');

console.log(`OK ${passed} kiểm tra hộp thư / chuông`);
