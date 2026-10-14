// SPDX-License-Identifier: Apache-2.0
// Copyright (c) 2026 Dương Thanh Lịch — VN-MateAI. See LICENSE and NOTICE.
// "Danh bạ Agent" (web/agent-directory.js) với DOM giả + API giả: gắn vào tab Cổng Agent, thoát HTML, ba loại agent, tạm dừng phải
// xác nhận, token chỉ hiện một lần (xoay phải xác nhận), cấp quyền admin / rủi ro L2 phải xác nhận, manager không thấy nút ghi.
import { readFileSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';

const ROOT = join(dirname(fileURLToPath(import.meta.url)), '..');
const src = readFileSync(join(ROOT, 'web', 'agent-directory.js'), 'utf8');
const app = readFileSync(join(ROOT, 'web', 'app.js'), 'utf8').replace(/\r\n/g, '\n');
const html = readFileSync(join(ROOT, 'web', 'index.html'), 'utf8').replace(/\r\n/g, '\n');
let passed = 0;
const ok = (c, n) => { if (!c) { console.error(`FAIL ${n}`); process.exit(1); } passed += 1; };

const tab = html.slice(html.indexOf('id="tab-agent-gateway"'), html.indexOf('</section>', html.indexOf('id="tab-agent-gateway"')));
ok(tab.indexOf('agent-directory-root') > 0 && tab.indexOf('agent-directory-root') < tab.indexOf('agent-gateway-root'), 'danh bạ nằm đầu tab Cổng Agent');
ok(html.indexOf('agent-directory.js') > 0 && html.indexOf('agent-directory.js') < html.indexOf('/static/app.js'), 'nạp agent-directory.js trước app.js');
ok(/AgentDirectoryUI\.onEnter\(\)/.test(app), 'switchTab gọi AgentDirectoryUI.onEnter');

const els = new Map();
const el = (id) => { if (!els.has(id)) els.set(id, { id, value: '', open: false, innerHTML: '', textContent: '', className: '', disabled: false, listeners: {}, dataset: {}, classList: { _s: new Set(), add(c) { this._s.add(c); }, remove(c) { this._s.delete(c); }, toggle(c, on) { on ? this._s.add(c) : this._s.delete(c); }, contains(c) { return this._s.has(c); } }, addEventListener(t, f) { this.listeners[t] = f; } }); return els.get(id); };
let routes = {}; const calls = []; const toasts = []; let userRole = 'admin'; let confirms = []; let confirmAnswer = true;
const stub = {
  document: { getElementById: el },
  window: { confirm: (m) => { confirms.push(m); return confirmAnswer; } },
  localStorage: { getItem: () => JSON.stringify({ role: userRole }) },
  showToast: (m, k) => toasts.push([m, k]),
  _esc: (s) => String(s).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;'),
  API_BASE: '', apiErrorText: (d, s) => (d && d.detail) || `HTTP ${s}`,
  apiFetch: async (url, opts = {}) => {
    const path = url.replace('/api/v1/agent-gateway/agents', '');
    calls.push([opts.method || 'GET', path, opts.body ? JSON.parse(opts.body) : null]);
    const r = routes[`${opts.method || 'GET'} ${path.split('?')[0]}`];
    if (!r) return { ok: false, status: 404, json: async () => ({}) };
    return { ok: (r.status ?? 200) < 400, status: r.status ?? 200, json: async () => r.body };
  },
};
const names = Object.keys(stub);
const UI = new Function(...names, `${src}\nreturn AgentDirectoryUI;`)(...names.map((n) => stub[n]));
const flush = () => new Promise((r) => setTimeout(r, 0));
const root = el('agent-directory-root');
const click = async (act, id) => { await root.listeners.click({ target: { closest: () => ({ dataset: { ad: act, id }, disabled: false }) } }); await flush(); };

const list = [
  { agent_id: 'crm-bot', kind: 'inbound', name: 'Bot <i>CRM</i>', owner: 'Phòng KD', status: 'active', role: 'operator', has_token: false, daily_token_limit: 5000, last_seen_at: null },
  { agent_id: 'ke-toan', kind: 'a2a', name: 'Kế toán', status: 'suspended', risk: 3, tool: null, last_error: 'ConnectError <x>', calls: { total: 4, failed: 1 } },
  { agent_id: 'erp', kind: 'mcp', name: 'ERP', status: 'active', tools: 3, registered: 2, calls: { total: 9, failed: 0 } },
  { agent_id: 'dev-fleet', kind: 'devfleet', name: 'Dev Fleet', status: 'active', mode: 'read_only', tasks: 4, manage_at: 'Dev Fleet' },
  { agent_id: 'may-tram', kind: 'workstations', name: 'Agent máy trạm', status: 'active', online: 2, names: ['PC-1', 'PC-<b>2</b>'], manage_at: 'Tích hợp hệ thống → Máy trạm' },
];
routes = { 'GET ': { body: { agents: list } } };

await UI.onEnter(); await flush();
const t = el('ad-list').innerHTML;
ok(t.includes('Bot &lt;i&gt;CRM&lt;/i&gt;') && t.includes('ConnectError &lt;x&gt;') && !t.includes('<i>CRM'), 'thoát HTML');
ok(t.includes('Gọi vào') && t.includes('A2A') && t.includes('MCP server') && t.includes('chưa có token') && t.includes('5000 token/ngày'), 'ba loại + chi tiết');
ok(t.includes('TẠM DỪNG') && t.includes('chưa đọc được thẻ agent') && t.includes('1 lỗi') && t.includes('2/3 công cụ'), 'trạng thái, lỗi, số lời gọi');
ok(t.includes('data-ad="suspend" data-id="crm-bot"') && t.includes('data-ad="resume" data-id="ke-toan"') && !t.includes('data-ad="delete" data-id="erp"'), 'nút đúng theo loại');
ok(!el('ad-form').classList.contains('hidden'), 'admin thấy form');
ok(t.includes('chế độ <b>read_only</b>') && t.includes('2 máy online: PC-1, PC-&lt;b&gt;2&lt;/b&gt;'), 'Dev Fleet + máy trạm hiện trong danh bạ, thoát HTML');
ok(!t.includes('data-id="dev-fleet"') && !t.includes('data-id="may-tram"'), 'Dev Fleet / máy trạm chỉ xem: không có nút ghi');

confirmAnswer = false; calls.length = 0;
await click('suspend', 'crm-bot');
ok(!calls.some((c) => c[0] === 'POST'), 'từ chối xác nhận thì không tạm dừng');
confirmAnswer = true;
routes['POST /crm-bot/status'] = { body: { status: 'suspended' } };
await click('suspend', 'crm-bot');
ok(calls.some((c) => c[1] === '/crm-bot/status' && c[2].status === 'suspended'), 'tạm dừng gửi đúng');

routes['POST /crm-bot/token'] = { body: { token: 'crm-bot.bi-mat<b>', usage: 'Authorization: Bearer <token> → /api/v1/agents/inbound/ask' } };
confirms = [];
await click('token', 'crm-bot');
ok(el('ad-token').innerHTML.includes('chỉ hiện MỘT lần') && el('ad-token').innerHTML.includes('crm-bot.bi-mat&lt;b&gt;') && confirms.length === 0, 'cấp token lần đầu: hiện một lần, không hỏi');
list[0].has_token = true;
await click('refresh');
confirmAnswer = false; calls.length = 0;
await click('token', 'crm-bot');
ok(confirms.some((m) => m.includes('Xoay token')) && !calls.some((c) => c[0] === 'POST'), 'xoay token phải xác nhận');
confirmAnswer = true;

// thêm agent inbound quyền admin phải xác nhận; A2A rủi ro L2 phải xác nhận
el('ad-id').value = 'bot-moi'; el('ad-kind').value = 'inbound'; el('ad-role').value = 'admin'; el('ad-name').value = 'Bot mới'; el('ad-limit').value = '0';
confirmAnswer = false; calls.length = 0;
await click('save');
ok(!calls.some((c) => c[0] === 'PUT'), 'quyền admin cần xác nhận');
confirmAnswer = true;
routes['PUT /bot-moi'] = { body: { agent_id: 'bot-moi', has_token: false } };
await click('save');
const put = calls.find((c) => c[0] === 'PUT');
ok(put[2].role === 'admin' && put[2].kind === 'inbound' && !('url' in put[2]) && el('ad-msg').textContent.includes('Cấp token'), 'lưu inbound gửi đúng');
el('ad-id').value = 'kt2'; el('ad-kind').value = 'a2a'; el('ad-url').value = 'https://kt.vn'; el('ad-risk').value = '2'; el('ad-auth').value = 'Bearer x';
routes['PUT /kt2'] = { body: { agent_id: 'kt2', tool: null, last_error: 'HTTP 404' } };
confirms = [];
await click('save');
ok(confirms.some((m) => m.includes('KHÔNG cần duyệt')) && el('ad-msg').textContent.includes('chưa đọc được thẻ agent: HTTP 404'), 'A2A L2 cần xác nhận; báo lỗi thẻ');

userRole = 'manager';
await click('refresh');
ok(el('ad-form').classList.contains('hidden') && !el('ad-list').innerHTML.includes('data-ad="suspend"'), 'manager không thấy nút ghi');
routes['GET '] = { status: 403, body: {} };
await click('refresh');
ok(el('ad-list').innerHTML.includes('không có quyền'), '403 báo thiếu quyền');
console.log(`test_agent_directory_ui: ${passed} passed`);
