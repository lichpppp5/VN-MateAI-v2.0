// SPDX-License-Identifier: Apache-2.0
// Copyright (c) 2026 Dương Thanh Lịch — VN-MateAI. See LICENSE and NOTICE.
// Tab "Thương mại & Tuân thủ" (web/compliance.js) với DOM giả + API giả: hiện đúng số máy chủ trả, thoát HTML, phần dữ liệu cá nhân
// chỉ cho admin, xoá cần gõ lại định danh, không dùng onclick inline (sẵn cho CSP chặt).
import { readFileSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';

const ROOT = join(dirname(fileURLToPath(import.meta.url)), '..');
const src = readFileSync(join(ROOT, 'web', 'compliance.js'), 'utf8');
const app = readFileSync(join(ROOT, 'web', 'app.js'), 'utf8').replace(/\r\n/g, '\n');
const html = readFileSync(join(ROOT, 'web', 'index.html'), 'utf8').replace(/\r\n/g, '\n');
let passed = 0;
const ok = (c, n) => { if (!c) { console.error(`FAIL ${n}`); process.exit(1); } passed += 1; };

ok(/id="tab-compliance"/.test(html) && /id="nav-compliance"/.test(html) && /id="compliance-root"/.test(html), 'index.html có tab');
ok(/compliance\.js\?v=/.test(html), 'nạp compliance.js');
ok(/'compliance'/.test(app.match(/const VALID_TABS = \[[^\]]+\]/)[0]) && /ComplianceUI\.onEnter\(\)/.test(app), 'VALID_TABS + onEnter');
ok(!/onclick=/.test(src), 'không onclick inline');

function harness(role) {
  const els = new Map();
  const el = (id) => {
    if (!els.has(id)) {
      els.set(id, { id, value: '', innerHTML: '', textContent: '', listeners: {},
        classList: { _s: new Set(), add(c) { this._s.add(c); }, remove(c) { this._s.delete(c); } },
        addEventListener(t, f) { this.listeners[t] = f; } });
    }
    return els.get(id);
  };
  const calls = [];
  const toasts = [];
  const routes = {
    'GET /api/v1/license/usage': { month: '2026-10', users: 3, workstations: 1, voice_turns_month: 368, llm_tokens_month: 2548744,
      limits: { max_users: 5, max_workstations: 5 }, state: 'missing', message: 'Dùng thử <b>x</b>' },
    'GET /api/v1/reports/performance/monthly': { months: [{ month: '2026-10', voice_turns: 368, turn_ms_p50: 15384, turn_ms_p90: 38286,
      first_answer_audio_ms_p50: 7243, model_fallback_rate: 0.498, ai_success_rate: 0.562, ai_tasks_finished: 144, ai_tasks: 147, llm_tokens: 2548744 },
    { month: '2026-09', voice_turns: 0, ai_tasks: 0, llm_tokens: 0 }] },
    'GET /api/v1/privacy/register': { activities: [{ activity: 'Tài khoản', data: ['họ tên'], purpose: 'xác thực', storage: 'users', retention: 'x', recipients: 'không' }] },
    'GET /api/v1/skills/catalog': { edition: 'internal', count: 2, untested_in_commercial: 0, skills: [
      { name: 'get_infra_status', tier: 'integration', owner: 'Tích hợp', tested: true, commercial: true, blocked_in_edition: null, source_missing: 'chưa khai báo Prometheus' },
      { name: 'open_youtube<script>', tier: 'personal', owner: 'Cá nhân', tested: true, commercial: false, blocked_in_edition: null, source_missing: null }] },
    'POST /api/v1/privacy/subject/erase': { subject: 'nv01', done: { user_deleted: 'nv01' }, kept: {} },
  };
  const stub = {
    document: { getElementById: el, createElement: () => ({ click() {} }), body: { appendChild() {}, removeChild() {} } },
    localStorage: { getItem: () => JSON.stringify({ role }) },
    URL: { createObjectURL: () => 'blob:x', revokeObjectURL() {} }, setTimeout: () => 0,
    _esc: (s) => String(s).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;'),
    API_BASE: '', showToast: (m, t) => toasts.push([t, m]),
    apiFetch: async (url, opts = {}) => {
      const key = `${opts.method || 'GET'} ${url.split('?')[0]}`;
      calls.push([key, opts.body ? JSON.parse(opts.body) : null]);
      const body = routes[key];
      return { ok: !!body, status: body ? 200 : 404, json: async () => body || {}, blob: async () => 'csv' };
    },
  };
  const names = Object.keys(stub);
  const UI = new Function(...names, `${src}\nreturn ComplianceUI;`)(...names.map((n) => stub[n]));
  return { UI, el, calls, toasts };
}
const flush = () => new Promise((r) => setTimeout(r, 0));

{
  const h = harness('admin');
  h.UI.onEnter();
  await flush(); await flush();
  const lic = h.el('cp-lic-body').innerHTML;
  ok(lic.includes('Dùng thử &lt;b&gt;x&lt;/b&gt;') && lic.includes('3') && lic.includes('/ 5'), 'bản quyền: thông điệp đã thoát + hạn mức');
  const perf = h.el('cp-perf-body').innerHTML;
  ok(perf.includes('15.4 s') && perf.includes('49.8%') && perf.includes('56.2%') && !perf.includes('2026-09'), 'hiệu năng: số đúng, bỏ tháng trống');
  const cat = h.el('cp-cat-body').innerHTML;
  ok(cat.includes('open_youtube&lt;script&gt;') && !cat.includes('<script>') && cat.includes('chưa khai báo Prometheus'), 'danh mục: thoát HTML + ghi chú nguồn');
  ok(h.el('cp-register').innerHTML.includes('Tài khoản'), 'admin thấy sổ xử lý');
  h.el('cp-subject').value = 'nv01';
  h.el('cp-confirm').value = 'sai';
  await h.el('cp-erase').listeners.click();
  ok(!h.calls.some(([k]) => k.startsWith('POST /api/v1/privacy/subject/erase')), 'xoá: gõ sai xác nhận thì không gọi API');
  h.el('cp-confirm').value = 'nv01';
  await h.el('cp-erase').listeners.click();
  ok(h.calls.some(([k, b]) => k === 'POST /api/v1/privacy/subject/erase' && b.confirm === 'nv01'), 'xoá: đúng xác nhận thì gọi API');
}
{
  const h = harness('manager');
  h.UI.onEnter();
  await flush(); await flush();
  ok(!h.calls.some(([k]) => k.includes('/privacy/')), 'manager: không gọi API dữ liệu cá nhân');
  ok(h.el('compliance-root').innerHTML.includes('hidden'), 'manager: phần dữ liệu cá nhân ẩn');
}
console.log(`compliance UI: ${passed} kiểm tra đạt`);
