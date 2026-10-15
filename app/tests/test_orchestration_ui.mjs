// SPDX-License-Identifier: Apache-2.0
// Copyright (c) 2026 Dương Thanh Lịch — VN-MateAI. See LICENSE and NOTICE.
// Giao diện Lõi điều phối (web/orchestration.js): đội agent + bảng điểm, giao việc, phiếu việc + thẩm định; CSP chặt; thoát HTML.
import { readFileSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';

const ROOT = join(dirname(fileURLToPath(import.meta.url)), '..');
const src = readFileSync(join(ROOT, 'web', 'orchestration.js'), 'utf8');
const app = readFileSync(join(ROOT, 'web', 'app.js'), 'utf8').replace(/\r\n/g, '\n');
const html = readFileSync(join(ROOT, 'web', 'index.html'), 'utf8').replace(/\r\n/g, '\n');
let passed = 0;
const ok = (c, n) => { if (!c) { console.error(`FAIL ${n}`); process.exit(1); } passed += 1; };

ok(/id="tab-agent-gateway"[\s\S]{0,300}id="orchestration-root"/.test(html), 'khối nằm trong tab Cổng Agent');
ok(/orchestration\.js/.test(html) && html.indexOf('orchestration.js') < html.indexOf('/static/app.js'), 'nạp script trước app.js');
ok(/tabId === 'agent-gateway'[\s\S]{0,200}OrchestrationUI\.onEnter\(\)/.test(app), 'vào tab thì nạp');
ok(!/\son[a-z]+="/.test(src), 'không có thuộc tính on* (CSP chặt)');

const els = new Map();
const mk = (id) => ({
  id, value: '', innerHTML: '', textContent: '', className: '', open: false, disabled: false, dataset: {}, listeners: {},
  classList: { _s: new Set(), add(c) { this._s.add(c); }, remove(c) { this._s.delete(c); }, contains(c) { return this._s.has(c); },
    toggle(c, on) { if (on) this._s.add(c); else this._s.delete(c); } },
  addEventListener(t, f) { this.listeners[t] = f; }, closest() { return this; },
});
const el = (id) => { if (!els.has(id)) els.set(id, mk(id)); return els.get(id); };
let user = { role: 'admin', username: 'boss' };
const routes = {};
const calls = [];
const toasts = [];
let confirmAnswer = true;
const stub = {
  document: { getElementById: el, hidden: false }, localStorage: { getItem: () => JSON.stringify(user) },
  _esc: (s) => String(s).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;'),
  API_BASE: '', getAuthToken: () => 't', apiErrorText: (d, s) => d.detail || `HTTP ${s}`, showToast: (m, t) => toasts.push([t, m]),
  confirm: () => confirmAnswer, setTimeout: () => 1, clearTimeout: () => {},
  apiFetch: async (url, opts = {}) => {
    const path = url.split('?')[0];
    calls.push([opts.method || 'GET', url, opts.body ? JSON.parse(opts.body) : null]);
    const r = routes[`${opts.method || 'GET'} ${path}`] || { status: 404, body: { detail: 'không có' } };
    return { ok: r.status < 400, status: r.status, json: async () => r.body };
  },
  window: {},
};
const names = Object.keys(stub);
const { OrchestrationUI } = new Function(...names, `${src}\nreturn { OrchestrationUI };`)(...names.map((n) => stub[n]));
const root = () => el('orchestration-root');
const click = async (attrs) => { await root().listeners.click({ target: { closest: () => ({ dataset: attrs, disabled: false }) } }); };

const EXEC = { executor_id: 'dba', kind: 'local', name: 'Quản trị <b>CSDL</b>', description: 'chỉ đọc', capabilities: ['dba'], tools: ['run_local_sql_check'],
  ref: null, max_risk: 2, max_concurrent: 2, status: 'active', version: 3, available: true, unavailable_reason: null,
  scorecard: { score: 0.75, completed: 2, failed: 0, needs_confirmation: 1, open: 1, median_seconds: 41 } };
routes['GET /api/v1/orchestration/executors'] = { status: 200, body: { executors: [EXEC] } };
routes['GET /api/v1/orchestration/capabilities'] = { status: 200, body: { capabilities: [{ capability: 'dba', executors: [{ name: 'DBA' }] }] } };
routes['GET /api/v1/orchestration/tools'] = { status: 200, body: { tools: ['run_local_sql_check', 'list_data_sources'] } };
const ORDER = { order_id: 'WO-1', objective: 'Tìm nguyên nhân <script>', executor_id: 'dba', executor_name: 'DBA', status: 'NEEDS_CONFIRMATION',
  attempt: 1, requested_by: 'boss', created_at: '2026-10-15 10:00:00', capability: 'dba', risk: 3 };
routes['GET /api/v1/orchestration/orders'] = { status: 200, body: { orders: [ORDER] } };
routes['GET /api/v1/orchestration/orders/WO-1'] = { status: 200, body: { ...ORDER, routing: { reason: 'điểm 0.75' },
  report: { result: 'Thiếu chỉ mục <img src=x>', untrusted: true },
  verification: { checks: [{ criterion: 'có chỉ mục', ok: true, kind: 'FACT', detail: 'đạt' }, { criterion: 'đề xuất đúng', ok: null, kind: 'INFERENCE', detail: 'cần người' }] },
  history: [{ at: '2026-10-15 10:00:01', event: 'dispatched', executor: 'dba' }] } };

user = { role: 'viewer', username: 'nv' };
await OrchestrationUI.onEnter();
ok(!els.has('orchestration-root') || !root().innerHTML, 'nhân viên (viewer): không dựng gì');

user = { role: 'admin', username: 'boss' };
await OrchestrationUI.onEnter();
const ex = el('or-exec').innerHTML;
ok(/Quản trị &lt;b&gt;CSDL/.test(ex) && !/<b>CSDL<\/b>/.test(ex), 'tên agent được thoát HTML');
ok(/<b>75<\/b>\/100 · 2 xong · 0 thất bại · 1 cần người · ~41 s\/việc/.test(ex), 'bảng điểm thật');
ok(/1\/2 đang làm/.test(ex) && /nhận việc được/.test(ex) && /data-or="toggle-exec"/.test(ex), 'tải + khả dụng + nút quản trị');
ok(/<option value="dba">/.test(el('or-o-cap').innerHTML), 'chọn năng lực từ danh mục thật');
ok(/run_local_sql_check/.test(el('or-tools').innerHTML), 'gợi ý công cụ được cấp');
ok(/Tìm nguyên nhân &lt;script&gt;/.test(el('or-orders').innerHTML), 'phiếu: thoát HTML');

await click({ or: 'open', id: 'WO-1' });
const d = el('or-detail').innerHTML;
ok(/agent ngoài — dữ liệu không tin cậy/.test(d) && /Thiếu chỉ mục &lt;img/.test(d), 'kết quả agent ngoài được gắn nhãn + thoát HTML');
ok(/ĐẠT<\/span> <span class="text-slate-500">\[FACT\]/.test(d) && /CHƯA KIỂM ĐƯỢC/.test(d), 'thẩm định từng tiêu chí');
ok(/data-or="accept"/.test(d) && /data-or="retry"/.test(d), 'admin: xác nhận + giao lại');

el('or-note').value = '';
await click({ or: 'accept', id: 'WO-1' });
ok(/Ghi rõ bạn đã kiểm tra gì/.test(el('or-d-msg').textContent) && !calls.some((c) => c[1].includes('/confirm')), 'xác nhận bắt buộc ghi chú');
routes['POST /api/v1/orchestration/orders/WO-1/confirm'] = { status: 200, body: { ...ORDER, status: 'COMPLETED' } };
el('or-note').value = 'đã chạy thử trên bản sao';
await click({ or: 'accept', id: 'WO-1' });
ok(calls.some((c) => c[1].endsWith('/orders/WO-1/confirm') && c[2].accept === true && c[2].note === 'đã chạy thử trên bản sao'), 'gửi xác nhận + ghi chú');

el('or-o-obj').value = 'Tìm nguyên nhân chậm'; el('or-o-cap').value = 'dba'; el('or-o-del').value = 'Nguyên nhân\n\nĐề xuất';
el('or-o-done').value = 'Có chỉ mục'; el('or-o-ctx').value = ''; el('or-o-risk').value = '3'; el('or-o-dl').value = '20';
routes['POST /api/v1/orchestration/orders/preview'] = { status: 200, body: { routing: { executor_id: 'dba', reason: 'điểm 0.75', rejected: [] }, needs_approval: true } };
await click({ or: 'preview' });
ok(/Sẽ giao cho:<\/b> dba/.test(el('or-o-preview').innerHTML) && /Cần người có thẩm quyền duyệt/.test(el('or-o-preview').innerHTML), 'xem trước định tuyến + duyệt');
const pv = calls.find((c) => c[1].endsWith('/orders/preview'))[2];
ok(pv.deliverables.length === 2 && pv.risk === 3 && pv.deadline_s === 1200, 'phiếu gửi đi: dòng trống bị bỏ, rủi ro, thời hạn đổi ra giây');
routes['POST /api/v1/orchestration/orders'] = { status: 200, body: { order_id: 'WO-2', status: 'WAITING_APPROVAL' } };
await click({ or: 'dispatch' });
ok(/CHỜ DUYỆT/.test(el('or-o-msg').textContent), 'giao việc rủi ro cao: nói rõ chờ duyệt');

el('or-x-id').value = 'sys'; el('or-x-kind').value = 'local'; el('or-x-name').value = 'Hệ thống'; el('or-x-caps').value = 'sysadmin, infra';
el('or-x-risk').value = '2'; el('or-x-conc').value = '1'; el('or-x-tools').value = 'get_system_info; list_data_sources'; el('or-x-desc').value = 'chẩn đoán';
routes['PUT /api/v1/orchestration/executors/sys'] = { status: 422, body: { detail: 'Không có công cụ: x' } };
await click({ or: 'save-exec' });
ok(/Không có công cụ/.test(el('or-x-msg').textContent), 'lưu agent lỗi: nói rõ');
const put = calls.find((c) => c[0] === 'PUT')[2];
ok(put.capabilities.join() === 'sysadmin,infra' && put.tools.join() === 'get_system_info,list_data_sources' && put.max_concurrent === 1, 'agent nội bộ: năng lực + công cụ tách đúng');

user = { role: 'manager', username: 'tp' };
OrchestrationUI.renderExecutors();
ok(!/data-or="toggle-exec"/.test(el('or-exec').innerHTML) && el('or-exec-form').classList.contains('hidden'), 'quản lý: không có nút cấu hình agent');
OrchestrationUI.renderDetail({ ...ORDER, requested_by: 'nguoi-khac', status: 'DISPATCHED' });
ok(!/data-or="cancel"/.test(el('or-detail').innerHTML), 'quản lý không huỷ phiếu của người khác');
OrchestrationUI.renderDetail({ ...ORDER, requested_by: 'tp', status: 'DISPATCHED' });
ok(/data-or="cancel"/.test(el('or-detail').innerHTML) && !/data-or="accept"/.test(el('or-detail').innerHTML), 'quản lý huỷ phiếu của mình, không xác nhận');

console.log(`OK ${passed} kiểm tra giao diện điều phối`);
