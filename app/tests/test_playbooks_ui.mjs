// SPDX-License-Identifier: Apache-2.0
// Copyright (c) 2026 Dương Thanh Lịch — VN-MateAI. See LICENSE and NOTICE.
// Tab "Kịch bản vận hành" (web/playbooks.js) với DOM giả + API giả: thoát HTML, dry-run hiển thị đúng quyết định chính sách,
// chạy thật chỉ báo "chờ duyệt" khi chưa duyệt, SUCCEEDED_UNVERIFIED không bị hiển thị như thành công, viewer/manager không thấy nút ghi.
import { readFileSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';

const ROOT = join(dirname(fileURLToPath(import.meta.url)), '..');
const src = readFileSync(join(ROOT, 'web', 'playbooks.js'), 'utf8');
const app = readFileSync(join(ROOT, 'web', 'app.js'), 'utf8').replace(/\r\n/g, '\n');
const html = readFileSync(join(ROOT, 'web', 'index.html'), 'utf8').replace(/\r\n/g, '\n');
let passed = 0;
const ok = (c, n) => { if (!c) { console.error(`FAIL ${n}`); process.exit(1); } passed += 1; };

ok(/id="tab-playbooks"/.test(html) && /id="nav-playbooks"/.test(html) && /id="playbooks-root"/.test(html), 'index.html có tab Kịch bản');
ok(html.indexOf('playbooks.js') > 0 && html.indexOf('playbooks.js') < html.indexOf('/static/app.js'), 'nạp playbooks.js trước app.js');
ok(/'playbooks'/.test(app.split('const VALID_TABS')[1].split(']')[0]) && /'playbooks': 'Kịch Bản/.test(app), 'VALID_TABS + TAB_TITLES');
ok(/PlaybooksUI\.onEnter\(\)/.test(app) && /PlaybooksUI\.onLeave\(\)/.test(app), 'switchTab gọi onEnter / onLeave');

const els = new Map();
const el = (id) => { if (!els.has(id)) els.set(id, { id, value: '', innerHTML: '', textContent: '', className: '', disabled: false, listeners: {}, dataset: {}, classList: { _s: new Set(), add(c) { this._s.add(c); }, remove(c) { this._s.delete(c); }, toggle(c, on) { on ? this._s.add(c) : this._s.delete(c); }, contains(c) { return this._s.has(c); } }, addEventListener(t, f) { this.listeners[t] = f; }, querySelectorAll: () => [] }); return els.get(id); };
let routes = {}; const calls = []; const toasts = []; let userRole = 'admin'; let confirmAnswer = true;
const stub = {
  document: { getElementById: el, hidden: false },
  setInterval: () => 1, clearInterval: () => {},
  window: { confirm: () => confirmAnswer, prompt: () => 'đã xem tay' },
  localStorage: { getItem: () => JSON.stringify({ role: userRole }) },
  showToast: (m, k) => toasts.push([m, k]),
  _esc: (s) => String(s).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;'),
  API_BASE: '', apiErrorText: (d, s) => (d && d.detail) || `HTTP ${s}`,
  apiFetch: async (url, opts = {}) => {
    const path = url.replace('/api/v1', '');
    calls.push([opts.method || 'GET', path, opts.body ? JSON.parse(opts.body) : null]);
    const r = routes[`${opts.method || 'GET'} ${path.split('?')[0]}`];
    if (!r) return { ok: false, status: 404, json: async () => ({}) };
    return { ok: (r.status ?? 200) < 400, status: r.status ?? 200, json: async () => r.body };
  },
};
const names = Object.keys(stub);
const PlaybooksUI = new Function(...names, `${src}\nreturn PlaybooksUI;`)(...names.map((n) => stub[n]));
const flush = () => new Promise((r) => setTimeout(r, 0));
const root = el('playbooks-root');
const click = async (act, id) => { await root.listeners.click({ target: { closest: () => ({ dataset: { pb: act, id }, disabled: false }) } }); await flush(); };

const def = { id: 'pb1', name: 'Kịch bản <b>x</b>', params: {}, steps: [{ id: 's1', tool: 'write_file' }], verify: [] };
const plan = { blocked: false, denied: [], needs_approval: true, approval_steps: ['s1'], max_risk: 3, plan_hash: 'abc123', notes: ['Không có khối verify'],
  steps: [{ id: 's1', title: 'Ghi <img src=x>', tool: 'write_file', args: { path: 'C:/t' }, decision: 'require_approval', reasons: ['Rủi ro L3'], risk: 3, has_rollback: false, conditional: false }], verify: [] };
routes = {
  'GET /playbooks': { body: { playbooks: [{ id: 'pb1', name: def.name, enabled: true, version: 2, definition: def }] } },
  'GET /playbook-runs': { body: { runs: [{ run_id: 'PBR-1', playbook_id: 'pb1', status: 'SUCCEEDED_UNVERIFIED', created_at: '2026-10-07 10:00:00', error: '' }] } },
  'POST /playbooks/pb1/plan': { body: plan },
  'POST /playbooks/pb1/run': { body: { status: 'awaiting_approval', run_id: 'PBR-2', approval_id: 'APR-9' } },
  'POST /playbook-runs/PBR-1/confirm': { body: { status: 'confirmed' } },
};

await PlaybooksUI.onEnter(); await flush();
ok(el('pb-list').innerHTML.includes('Kịch bản &lt;b&gt;x&lt;/b&gt;') && !el('pb-list').innerHTML.includes('<b>x</b>'), 'tên kịch bản được thoát HTML');
ok(el('pb-runs').innerHTML.includes('CHƯA kiểm chứng') && !el('pb-runs').innerHTML.includes('Thành công'), 'UNVERIFIED không hiện như thành công');
ok(el('pb-runs').innerHTML.includes('data-pb="confirm"'), 'admin thấy nút xác nhận');

await click('select', 'pb1');
ok(JSON.parse(el('pb-json').value).id === 'pb1', 'chọn kịch bản nạp JSON');
await click('plan');
ok(el('pb-plan').innerHTML.includes('Cần duyệt') && el('pb-plan').innerHTML.includes('CHƯA thực thi') && el('pb-plan').innerHTML.includes('&lt;img'), 'dry-run hiện quyết định, thoát HTML');
ok(!calls.some((c) => c[1].endsWith('/run')), 'chạy thử không gọi /run');
await click('run');
ok(el('pb-run-msg').innerHTML.includes('CHƯA chạy') && el('pb-run-msg').innerHTML.includes('APR-9'), 'chạy thật báo chờ duyệt, không báo đã chạy');
const runCall = calls.find((c) => c[1].endsWith('/run'));
ok(runCall && /^ui-/.test(runCall[2].idempotency_key), 'gửi idempotency_key');
await click('confirm', 'PBR-1');
ok(calls.some((c) => c[1] === '/playbook-runs/PBR-1/confirm' && c[2].note === 'đã xem tay') && toasts.at(-1)[1] === 'success', 'xác nhận gửi ghi chú');

confirmAnswer = false; const before = calls.length;
await click('run');
ok(calls.length === before, 'không xác nhận thì không chạy');

// tự kích hoạt: danh sách nêu trình kích hoạt, lượt tự động có nhãn, token webhook chỉ hiện một lần (phải xác nhận)
const auto = { ...def, id: 'pb2', name: 'Tự động', triggers: [{ type: 'schedule', cron: '0 8 * * 1-5' }, { type: 'webhook' }] };
routes['GET /playbooks'] = { body: { playbooks: [{ id: 'pb1', name: def.name, enabled: true, version: 2, definition: def }, { id: 'pb2', name: 'Tự động', enabled: true, version: 1, definition: auto }] } };
routes['GET /playbook-runs'] = { body: { runs: [{ run_id: 'PBR-3', playbook_id: 'pb2', status: 'RUNNING', created_at: '2026-10-12 08:00:00', error: '', trigger: 'cron' }] } };
routes['POST /playbooks/pb2/webhook-token'] = { body: { token: 'tok<b>', usage: 'POST /api/v1/playbooks/pb2/trigger' } };
confirmAnswer = true;
await click('refresh');
ok(el('pb-list').innerHTML.includes('tự kích hoạt: lịch 0 8 * * 1-5, webhook') && el('pb-runs').innerHTML.includes('tự động theo lịch'), 'hiện trình kích hoạt + nhãn lượt tự động');
await click('select', 'pb1');
ok(el('pb-webhook').classList.contains('hidden'), 'kịch bản không có webhook: ẩn nút token');
await click('select', 'pb2');
ok(!el('pb-webhook').classList.contains('hidden'), 'kịch bản có webhook: hiện nút token');
await click('webhook');
ok(el('pb-run-msg').innerHTML.includes('chỉ hiện MỘT lần') && el('pb-run-msg').innerHTML.includes('tok&lt;b&gt;'), 'token webhook hiện một lần, thoát HTML');

userRole = 'manager'; await PlaybooksUI.onEnter(); await flush();
ok(!el('pb-runs').innerHTML.includes('data-pb="confirm"') && el('pb-run').classList.contains('hidden'), 'manager không thấy nút ghi');

routes['GET /playbooks'] = { status: 403, body: {} };
await PlaybooksUI.onEnter(); await flush();
ok(el('pb-list').innerHTML.includes('không có quyền'), '403 báo thiếu quyền');
console.log(`test_playbooks_ui: ${passed} passed`);
