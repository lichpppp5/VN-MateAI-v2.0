// SPDX-License-Identifier: Apache-2.0
// Copyright (c) 2026 Dương Thanh Lịch — VN-MateAI. See LICENSE and NOTICE.
// Mục "Chi phí & Đánh giá" (web/llmops.js) với DOM giả + API giả: thoát HTML, token chưa khai giá không bị quy ra tiền, trạng thái ngân sách,
// ca bị TỤT được làm nổi, nút ghi chỉ hiện cho admin, chế độ CHẶN phải xác nhận, 403 báo thiếu quyền, gắn đúng vào tab Quản lý Trợ lý AI.
import { readFileSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';

const ROOT = join(dirname(fileURLToPath(import.meta.url)), '..');
const src = readFileSync(join(ROOT, 'web', 'llmops.js'), 'utf8');
const app = readFileSync(join(ROOT, 'web', 'app.js'), 'utf8').replace(/\r\n/g, '\n');
const html = readFileSync(join(ROOT, 'web', 'index.html'), 'utf8').replace(/\r\n/g, '\n');
let passed = 0;
const ok = (c, n) => { if (!c) { console.error(`FAIL ${n}`); process.exit(1); } passed += 1; };

ok(/data-ai-subtab="llmops"/.test(html) && /data-ai-sub="llmops" id="llmops-root"/.test(html), 'index.html có mục con llmops trong tab AI');
ok(html.indexOf('llmops.js') > 0 && html.indexOf('llmops.js') < html.indexOf('/static/app.js'), 'nạp llmops.js trước app.js');
ok(/name === 'llmops' \|\| all\) LlmOpsUI\.onEnter\(\); else LlmOpsUI\.onLeave\(\)/.test(app), 'showAISubTab gọi onEnter / onLeave');
ok(/tabId !== 'ai-manager'\) LlmOpsUI\.onLeave\(\)/.test(app), 'rời tab AI thì dừng làm mới');

const els = new Map();
const el = (id) => { if (!els.has(id)) els.set(id, { id, value: '', innerHTML: '', textContent: '', className: '', disabled: false, listeners: {}, dataset: {}, classList: { _s: new Set(), add(c) { this._s.add(c); }, remove(c) { this._s.delete(c); }, toggle(c, on) { on ? this._s.add(c) : this._s.delete(c); }, contains(c) { return this._s.has(c); } }, addEventListener(t, f) { this.listeners[t] = f; }, querySelectorAll: () => [] }); return els.get(id); };
let routes = {}; const calls = []; const toasts = []; let userRole = 'admin'; let confirmAnswer = true; let intervals = 0;
const stub = {
  document: { getElementById: el, hidden: false },
  setInterval: () => { intervals += 1; return intervals; }, clearInterval: () => {},
  window: { confirm: () => confirmAnswer },
  localStorage: { getItem: () => JSON.stringify({ role: userRole }) },
  showToast: (m, k) => toasts.push([m, k]),
  _esc: (s) => String(s).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;'),
  API_BASE: '', apiErrorText: (d, s) => (d && d.detail) || `HTTP ${s}`,
  apiFetch: async (url, opts = {}) => {
    const path = url.replace('/api/v1/llmops', '');
    calls.push([opts.method || 'GET', path, opts.body ? JSON.parse(opts.body) : null]);
    const r = routes[`${opts.method || 'GET'} ${path.split('?')[0]}`];
    if (!r) return { ok: false, status: 404, json: async () => ({}) };
    return { ok: (r.status ?? 200) < 400, status: r.status ?? 200, json: async () => r.body };
  },
};
const names = Object.keys(stub);
const LlmOpsUI = new Function(...names, `${src}\nreturn LlmOpsUI;`)(...names.map((n) => stub[n]));
const flush = () => new Promise((r) => setTimeout(r, 0));
const root = el('llmops-root');
const click = async (act, id) => { await root.listeners.click({ target: { closest: () => ({ dataset: { lo: act, id }, disabled: false }) } }); await flush(); };

const budget = { mode: 'warn', warn_ratio: 0.8, limits: { daily_token_limit: 1000, user_daily_token_limit: 0, daily_cost_limit: 0, monthly_cost_limit: 5 },
  checks: [{ key: 'daily_tokens', label: 'Token trong ngày', used: 1200, limit: 1000, ratio: 1.2, state: 'exceeded' },
    { key: 'monthly_cost', label: 'Chi phí trong tháng', used: 4.2, limit: 5, ratio: 0.84, state: 'warn' }] };
routes = {
  'GET /usage': { body: { since: '2026-09-13', days: 30, note: 'Chi phí chỉ gồm model đã khai giá; 300 token chưa khai giá',
    total: { tokens: 1500, calls: 3, cost: 4.2, unpriced_tokens: 300 },
    by_day: [{ key: '2026-10-11', tokens: 500 }, { key: '2026-10-12', tokens: 1000 }],
    by_model: [{ key: 'gia-co', tokens: 1200, calls: 2, cost: 4.2, unpriced_tokens: 0 }, { key: 'model-<b>la</b>', tokens: 300, calls: 1, cost: 0, unpriced_tokens: 300 }],
    by_user: [{ key: 'an<script>', tokens: 1500, calls: 3, cost: 4.2, unpriced_tokens: 300 }], budget } },
  'GET /eval/sets': { body: { sets: [{ set_id: 'hr', name: 'HR <i>', cases: 2, last_run: { run_id: 'EVR-2', passed: 1, total: 2, status: 'COMPLETED' } }] } },
  'GET /eval/sets/hr': { body: { set_id: 'hr', name: 'HR', cases: [{ id: 'phep', prompt: 'q', checks: [{ type: 'contains', value: 'x' }] }] } },
  'GET /eval/runs': { body: { runs: [{ run_id: 'EVR-2', set_id: 'hr', status: 'COMPLETED', passed: 1, total: 2, pass_rate: 0.5, total_tokens: 60, models: ['gia-co'], created_at: '2026-10-12 10:00:00.123456' }] } },
  'GET /eval/runs/EVR-2': { body: { run_id: 'EVR-2', set_id: 'hr', status: 'COMPLETED', passed: 1, total: 2,
    comparison: { previous_run: 'EVR-1', previous_pass_rate: 1, delta: -0.5, regressed: ['phep'], fixed: [] },
    results: [{ id: 'phep', passed: false, error: null, checks: [{ type: 'contains', value: '12 ngày', ok: false }], tools: [], reply: 'Tôi <img src=x> không biết', latency_ms: 900 },
      { id: 'ha-tang', passed: true, error: null, checks: [{ type: 'tool_called', value: 'get_infra_status', ok: true }], tools: ['get_infra_status'], reply: 'srv-1', latency_ms: 500 }] } },
};

await LlmOpsUI.onEnter(); await flush();
const totals = el('lo-totals').innerHTML;
ok(totals.includes('1.500') && totals.includes('Token chưa khai giá') && totals.includes('không quy ra tiền'), 'ô tổng tách token chưa khai giá');
ok(el('lo-by-model').innerHTML.includes('chưa khai giá') && !el('lo-by-model').innerHTML.includes('<b>la</b>'), 'model chưa khai giá không hiện tiền 0, tên được thoát HTML');
ok(!el('lo-by-user').innerHTML.includes('<script>'), 'tên người được thoát HTML');
ok(el('lo-days-chart').innerHTML.includes('width:100%') && el('lo-days-chart').innerHTML.includes('width:50%'), 'biểu đồ ngày theo tỉ lệ token');
const b = el('lo-budget').innerHTML;
ok(b.includes('ĐÃ VƯỢT') && b.includes('Sắp hết') && b.includes('width:100%') && b.includes('Chỉ cảnh báo'), 'ngân sách: trạng thái + thanh (cắt ở 100 %)');
ok(!el('lo-budget-form').classList.contains('hidden') && el('lo-daily_token_limit').value === 1000 && el('lo-warn_ratio').value === 80, 'admin thấy form ngân sách điền sẵn');
ok(el('lo-sets').innerHTML.includes('HR &lt;i&gt;') && el('lo-sets').innerHTML.includes('1/2'), 'danh sách bộ kiểm + lần chạy cuối');
ok(el('lo-runs').innerHTML.includes('50%') && el('lo-runs').innerHTML.includes('2026-10-12 10:00:00<'), 'bảng lần chạy (giờ không hiện micro giây)');
ok(intervals === 1, 'bật làm mới định kỳ một lần');

await click('detail', 'EVR-2');
const d = el('lo-run-detail').innerHTML;
ok(d.includes('TỤT: phep') && d.includes('bg-rose-500/10') && d.includes('Không đạt') && d.includes('Đạt'), 'chi tiết làm nổi ca bị tụt');
ok(!d.includes('<img src=x>'), 'câu trả lời model được thoát HTML');

// chế độ CHẶN cần xác nhận; từ chối thì không gửi
el('lo-mode').value = 'block'; el('lo-warn_ratio').value = 90;
confirmAnswer = false; calls.length = 0;
await click('save-budget');
ok(!calls.some((c) => c[0] === 'POST'), 'từ chối xác nhận CHẶN thì không lưu');
confirmAnswer = true;
routes['POST /budget'] = { body: { ...budget, mode: 'block' } };
await click('save-budget');
const post = calls.find((c) => c[0] === 'POST' && c[1] === '/budget');
ok(post && post[2].budget_mode === 'block' && post[2].warn_ratio === 0.9 && post[2].daily_token_limit === 1000, 'gửi ngân sách đúng kiểu (tỉ lệ 0–1)');
ok(el('lo-budget-msg').textContent.includes('Đã lưu'), 'báo đã lưu');

// lưu bộ kiểm: JSON sai không gửi; lỗi máy chủ hiện ra
await click('select-set', 'hr');
ok(el('lo-set-id').value === 'hr' && el('lo-set-json').value.includes('"phep"'), 'chọn bộ kiểm nạp JSON');
el('lo-set-json').value = '{sai';
calls.length = 0;
await click('save-set');
ok(el('lo-set-msg').textContent.includes('JSON không hợp lệ') && !calls.some((c) => c[0] === 'PUT'), 'JSON sai không gửi');
el('lo-set-json').value = '{"name":"HR","cases":[]}';
routes['PUT /eval/sets/hr'] = { status: 422, body: { detail: 'Cần ít nhất một ca kiểm' } };
await click('save-set');
ok(el('lo-set-msg').textContent.includes('Cần ít nhất một ca kiểm'), 'lỗi kiểm tra từ máy chủ hiện ra');

routes['POST /eval/sets/hr/run'] = { body: { run_id: 'EVR-3', status: 'RUNNING', total: 2 } };
routes['GET /eval/runs/EVR-3'] = { body: { run_id: 'EVR-3', set_id: 'hr', status: 'RUNNING', passed: 0, total: 2, comparison: null, results: [] } };
await click('run-set');
ok(el('lo-set-msg').textContent.includes('Đang chạy 2 ca') && el('lo-run-detail').innerHTML.includes('Đang chạy ca đầu tiên'), 'chạy đánh giá hiện tiến độ');

// manager: chỉ xem
userRole = 'manager';
await click('refresh');
ok(el('lo-budget-form').classList.contains('hidden') && el('lo-set-actions').classList.contains('hidden') && el('lo-new-set').classList.contains('hidden'), 'manager không thấy nút ghi');

routes['GET /usage'] = { status: 403, body: {} };
await click('refresh');
ok(el('lo-totals').innerHTML.includes('không có quyền'), '403 báo thiếu quyền');
console.log(`test_llmops_ui: ${passed} passed`);
