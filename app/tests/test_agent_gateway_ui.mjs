// SPDX-License-Identifier: Apache-2.0
// Copyright (c) 2026 Dương Thanh Lịch — VN-MateAI. See LICENSE and NOTICE.
// Tab "Cổng Agent" (web/agent-gateway.js) với DOM giả + API giả: gắn đúng vào portal, thoát HTML, khoá không hiện lại, thử kết nối /
// lưu / đồng bộ gửi đúng dữ liệu, hạ rủi ro dưới L3 phải xác nhận, manager không thấy nút ghi, 403 báo thiếu quyền.
import { readFileSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';

const ROOT = join(dirname(fileURLToPath(import.meta.url)), '..');
const src = readFileSync(join(ROOT, 'web', 'agent-gateway.js'), 'utf8');
const app = readFileSync(join(ROOT, 'web', 'app.js'), 'utf8').replace(/\r\n/g, '\n');
const html = readFileSync(join(ROOT, 'web', 'index.html'), 'utf8').replace(/\r\n/g, '\n');
let passed = 0;
const ok = (c, n) => { if (!c) { console.error(`FAIL ${n}`); process.exit(1); } passed += 1; };

ok(/id="tab-agent-gateway"/.test(html) && /id="nav-agent-gateway"/.test(html) && /id="agent-gateway-root"/.test(html), 'index.html có tab Cổng Agent');
ok(html.indexOf('agent-gateway.js') > 0 && html.indexOf('agent-gateway.js') < html.indexOf('/static/app.js'), 'nạp agent-gateway.js trước app.js');
ok(/'agent-gateway'/.test(app.split('const VALID_TABS')[1].split(']')[0]) && /'agent-gateway': 'Cổng Agent/.test(app), 'VALID_TABS + TAB_TITLES');
ok(/AgentGatewayUI\.onEnter\(\)/.test(app), 'switchTab gọi onEnter');

const els = new Map();
const el = (id) => { if (!els.has(id)) els.set(id, { id, value: '', checked: false, innerHTML: '', textContent: '', className: '', disabled: false, listeners: {}, dataset: {}, classList: { _s: new Set(), add(c) { this._s.add(c); }, remove(c) { this._s.delete(c); }, toggle(c, on) { on ? this._s.add(c) : this._s.delete(c); }, contains(c) { return this._s.has(c); } }, addEventListener(t, f) { this.listeners[t] = f; } }); return els.get(id); };
let routes = {}; const calls = []; const toasts = []; let userRole = 'admin'; let confirmAnswer = true;
const stub = {
  document: { getElementById: el },
  window: { confirm: () => confirmAnswer },
  localStorage: { getItem: () => JSON.stringify({ role: userRole }) },
  showToast: (m, k) => toasts.push([m, k]),
  _esc: (s) => String(s).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;'),
  API_BASE: '', apiErrorText: (d, s) => (d && d.detail) || `HTTP ${s}`,
  apiFetch: async (url, opts = {}) => {
    const path = url.replace('/api/v1/agent-gateway/mcp', '');
    calls.push([opts.method || 'GET', path, opts.body ? JSON.parse(opts.body) : null]);
    const r = routes[`${opts.method || 'GET'} ${path.split('?')[0]}`];
    if (!r) return { ok: false, status: 404, json: async () => ({}) };
    return { ok: (r.status ?? 200) < 400, status: r.status ?? 200, json: async () => r.body };
  },
};
const names = Object.keys(stub);
const UI = new Function(...names, `${src}\nreturn AgentGatewayUI;`)(...names.map((n) => stub[n]));
const flush = () => new Promise((r) => setTimeout(r, 0));
const root = el('agent-gateway-root');
const click = async (act, id) => { await root.listeners.click({ target: { closest: () => ({ dataset: { ag: act, id }, disabled: false }) } }); await flush(); };
const change = async (target) => { await root.listeners.change({ target }); await flush(); };

const server = { id: 'erp', name: 'ERP <b>nội bộ</b>', transport: 'http', url: 'https://erp.congty.vn/mcp', auth_header: 'Authorization', auth_value: '••••••••',
  enabled: true, timeout_s: 30, default_risk: 3, data_classification: 'INTERNAL', registered: 2, discovered_at: '2026-10-12 11:00:00', last_error: null,
  tools: [{ name: 'get_order', registered_as: 'mcp_erp_get_order', description: 'Tra đơn <img src=x>', risk: 1, enabled: true, annotations: { readOnlyHint: true }, override: {} },
    { name: 'cancel_order', registered_as: 'mcp_erp_cancel_order', description: 'Huỷ đơn', risk: 4, enabled: true, annotations: { destructiveHint: true }, override: {} }] };
routes = { 'GET /servers': { body: { servers: [server], allow_stdio: false } } };

await UI.onEnter(); await flush();
ok(el('ag-list').innerHTML.includes('ERP &lt;b&gt;nội bộ&lt;/b&gt;') && el('ag-list').innerHTML.includes('2/2 công cụ'), 'danh sách server, tên được thoát HTML');
ok(!el('ag-new').classList.contains('hidden') && !el('ag-actions').classList.contains('hidden'), 'admin thấy nút ghi');

await click('select', 'erp');
ok(el('ag-sid').value === 'erp' && el('ag-sid').disabled === true && el('ag-auth_value').value === '••••••••', 'chọn server: điền form, khoá chỉ là mặt nạ');
const tools = el('ag-tools').innerHTML;
ok(tools.includes('mcp_erp_get_order') && tools.includes('chỉ đọc') && tools.includes('phá huỷ') && !tools.includes('<img src=x>'), 'bảng công cụ: tên đăng ký, gợi ý, thoát HTML');

routes['POST /test'] = { body: { ok: true, server_info: { name: 'erp-gia' }, tools: [{ name: 'get_order' }, { name: 'cancel_order' }] } };
await click('test');
const test = calls.find((c) => c[0] === 'POST' && c[1] === '/test');
ok(test[2].id === 'erp' && test[2].url === 'https://erp.congty.vn/mcp' && test[2].auth_value === '••••••••', 'thử kết nối gửi khai báo hiện tại (mặt nạ = giữ khoá cũ)');
ok(el('ag-msg').textContent.includes('erp-gia') && el('ag-msg').textContent.includes('2 công cụ'), 'báo kết quả thử kết nối');

routes['POST /servers/erp/discover'] = { body: { ok: false, error: 'ConnectError: từ chối', tools: 2, registered: 2 } };
await click('discover');
ok(el('ag-msg').textContent.includes('Không kết nối được') && el('ag-msg').textContent.includes('ConnectError'), 'đồng bộ lỗi hiện lý do');

// hạ rủi ro xuống dưới L3 phải xác nhận; từ chối thì không gửi
confirmAnswer = false; calls.length = 0;
await change({ id: 'x', value: '1', dataset: { agRisk: 'cancel_order' } });
ok(!calls.some((c) => c[0] === 'PATCH'), 'từ chối xác nhận thì không hạ rủi ro');
confirmAnswer = true;
routes['PATCH /servers/erp/tools/cancel_order'] = { body: server };
await change({ id: 'x', value: '4', dataset: { agRisk: 'cancel_order' } });
await change({ id: 'x', checked: false, dataset: { agEnable: 'cancel_order' } });
const patches = calls.filter((c) => c[0] === 'PATCH').map((c) => c[2]);
ok(JSON.stringify(patches) === JSON.stringify([{ risk: 4 }, { enabled: false }]), 'đặt rủi ro / tắt công cụ gửi đúng');

// lưu server mới: thiếu mã thì không gửi; lỗi máy chủ hiện ra
await click('new');
calls.length = 0;
await click('save');
ok(el('ag-msg').textContent.includes('Cần mã') && !calls.some((c) => c[0] === 'PUT'), 'thiếu mã không gửi');
el('ag-sid').value = 'crm'; el('ag-name').value = 'CRM'; el('ag-url').value = 'ftp://x';
routes['PUT /servers/crm'] = { status: 422, body: { detail: 'base_url phải bắt đầu bằng http:// hoặc https://' } };
await click('save');
ok(el('ag-msg').textContent.includes('http://'), 'lỗi kiểm tra từ máy chủ hiện ra');

userRole = 'manager';
await click('refresh');
ok(el('ag-new').classList.contains('hidden') && el('ag-actions').classList.contains('hidden'), 'manager không thấy nút ghi');
await click('select', 'erp');
ok(!el('ag-tools').innerHTML.includes('<select') && el('ag-tools').innerHTML.includes('L4 cần duyệt'), 'manager chỉ xem mức rủi ro');

routes['GET /servers'] = { status: 403, body: {} };
await click('refresh');
ok(el('ag-list').innerHTML.includes('không có quyền'), '403 báo thiếu quyền');
console.log(`test_agent_gateway_ui: ${passed} passed`);
