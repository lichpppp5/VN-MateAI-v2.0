// SPDX-License-Identifier: Apache-2.0
// Copyright (c) 2026 Dương Thanh Lịch — VN-MateAI. See LICENSE and NOTICE.
// Tab "Giám sát hạ tầng" (web/infra-monitor.js) với DOM giả + API giả: không bịa số liệu, thoát HTML, nói rõ khi chưa cấu hình /
// nguồn mất liên lạc / thiếu quyền, PromQL chỉ gọi API chỉ-đọc, liên kết ngoài có rel an toàn.
import { readFileSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';

const ROOT = join(dirname(fileURLToPath(import.meta.url)), '..');
const src = readFileSync(join(ROOT, 'web', 'infra-monitor.js'), 'utf8');
const app = readFileSync(join(ROOT, 'web', 'app.js'), 'utf8').replace(/\r\n/g, '\n');
const html = readFileSync(join(ROOT, 'web', 'index.html'), 'utf8').replace(/\r\n/g, '\n');
let passed = 0;
const ok = (c, n) => { if (!c) { console.error(`FAIL ${n}`); process.exit(1); } passed += 1; };

ok(/id="tab-infra-monitor"/.test(html) && /id="nav-infra-monitor"/.test(html) && /id="infra-monitor-root"/.test(html), 'index.html có tab Giám sát hạ tầng');
ok(/infra-monitor\.js/.test(html) && html.indexOf('infra-monitor.js') < html.indexOf('/static/app.js'), 'nạp infra-monitor.js trước app.js');
ok(/VALID_TABS = \['dashboard', 'infra-monitor'/.test(app) && /'infra-monitor': 'Giám Sát Hạ Tầng/.test(app), 'VALID_TABS + TAB_TITLES');
ok(/InfraMonitorUI\.onEnter\(\)/.test(app) && /InfraMonitorUI\.onLeave\(\)/.test(app), 'switchTab gọi onEnter / onLeave');

const els = new Map();
const el = (id) => { if (!els.has(id)) els.set(id, { id, value: '', innerHTML: '', textContent: '', disabled: false, listeners: {}, classList: { _s: new Set(), add(c) { this._s.add(c); }, remove(c) { this._s.delete(c); }, contains(c) { return this._s.has(c); } }, addEventListener(t, f) { this.listeners[t] = f; }, querySelector: () => ({ dataset: { im: 'query' } }) }); return els.get(id); };
let routes = {};
const calls = [];
const stub = {
  document: { getElementById: el, hidden: false },
  setInterval: () => 1, clearInterval: () => {}, window: {},
  _esc: (s) => String(s).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;'),
  API_BASE: '', apiErrorText: (d, s) => (d && d.detail) || `HTTP ${s}`,
  switchTab: (t) => calls.push(['tab', t]),
  apiFetch: async (url, opts = {}) => {
    const path = url.replace('/api/v1/monitoring', '');
    calls.push([opts.method || 'GET', path, opts.body ? JSON.parse(opts.body) : null]);
    const r = routes[`${opts.method || 'GET'} ${path.split('?')[0]}`];
    if (!r) return { ok: false, status: 404, json: async () => ({}) };
    return { ok: (r.status ?? 200) < 400, status: r.status ?? 200, json: async () => r.body };
  },
};
const names = Object.keys(stub);
const InfraMonitorUI = new Function(...names, `${src}\nreturn InfraMonitorUI;`)(...names.map((n) => stub[n]));
const flush = () => new Promise((r) => setTimeout(r, 0));
const root = el('infra-monitor-root');
const click = async (act) => { await root.listeners.click({ target: { closest: () => ({ dataset: { im: act }, disabled: false }) } }); await flush(); };

const data = {
  configured: true, enabled: true, checked_at: '2026-10-07 14:30:00',
  summary: { health: 'CRITICAL', sources_total: 2, sources_reachable: 1, alerts_firing: 2, alerts_critical: 1, alerts_warning: 1, targets_total: 3, targets_down: 1, dashboards: 1 },
  alerts: [
    { id: 'a1', source: 'prom-1', source_type: 'prometheus', name: 'HighCPU <b>x</b>', severity: 'critical', state: 'firing', summary: 'CPU 97% <img src=x>', instance: 'srv-1:9100' },
    { id: 'a2', source: 'prom-1', source_type: 'prometheus', name: 'DiskLow', severity: 'warning', state: 'pending', summary: '', instance: 'srv-2:9100' }],
  sources: [
    { id: 'prom-1', title: 'Prometheus', type: 'prometheus', reachable: true, latency_ms: 12.3, error: null,
      targets: { total: 3, up: 2, down: 1, down_list: [{ source: 'prom-1', job: 'node', instance: 'srv-2:9100', error: 'connection refused' }] },
      metrics: [{ instance: 'srv-1:9100', metric: 'cpu', value: 97.3, level: 'crit' }, { instance: 'srv-1:9100', metric: 'memory', value: 55, level: 'ok' },
        { instance: 'srv-2:9100', metric: 'disk', value: 88.3, level: 'warn' }], metrics_note: 'memory: chưa có số đo', alerts: [] },
    { id: 'graf-1', title: 'Grafana', type: 'grafana', reachable: false, latency_ms: null, error: 'HTTP 401', base_url: 'https://grafana.local', alerts: [], dashboards: [] },
    { id: 'graf-2', title: 'Grafana 2', type: 'grafana', reachable: true, latency_ms: 30, base_url: 'https://g2.local', rules: { total: 3, firing: 1, pending: 1, error: 1 },
      dashboards: [{ title: 'Máy chủ <i>Linux</i>', folder: 'Hạ tầng', url: 'https://g2.local/d/x' }], alerts: [] }],
};

// 1. dựng khung + hiển thị dữ liệu thật
routes = { 'GET /overview': { body: data } };
InfraMonitorUI.onEnter(); await flush(); await flush();
ok(/Giám sát|PromQL/.test(root.innerHTML) && /Truy vấn PromQL \(chỉ đọc\)/.test(root.innerHTML), 'khung giao diện được dựng');
ok(/Nghiêm trọng/.test(el('im-summary').innerHTML) && />1<\/b> \/ <b>?|1<\/b> \/ 2/.test(el('im-summary').innerHTML.replace(/<[^>]+>/g, (m) => m)), 'tóm tắt: tình trạng + nguồn liên lạc được');
ok(/HighCPU &lt;b&gt;x/.test(el('im-alerts').innerHTML) && !/<b>x<\/b>/.test(el('im-alerts').innerHTML) && !/<img src=x>/.test(el('im-alerts').innerHTML), 'cảnh báo được thoát HTML');
ok(/srv-2:9100/.test(el('im-targets').innerHTML) && /connection refused/.test(el('im-targets').innerHTML), 'target down kèm lỗi');
const m = el('im-metrics').innerHTML;
ok(/srv-1:9100/.test(m) && /97\.3%/.test(m) && /bg-rose-500/.test(m) && /bg-amber-500/.test(m) && /bg-emerald-500/.test(m), 'số đo thật + màu theo mức');
ok(/>—</.test(m) && /memory: chưa có số đo/.test(el('im-metrics-note').textContent), 'số đo thiếu hiện "—" và có lý do, không điền 0');
const s = el('im-sources').innerHTML;
ok(/Mất liên lạc/.test(s) && /HTTP 401/.test(s), 'nguồn mất liên lạc nói rõ');
ok(/Quy tắc cảnh báo: 3 · đang bắn 1/.test(s), 'trạng thái quy tắc Grafana');
ok(/rel="noopener noreferrer"/.test(s) && /target="_blank"/.test(s), 'liên kết ngoài có rel an toàn');
ok(/Máy chủ &lt;i&gt;Linux/.test(el('im-dashboards').innerHTML) && /rel="noopener noreferrer"/.test(el('im-dashboards').innerHTML), 'dashboard được thoát HTML, liên kết an toàn');

// 2. PromQL: gọi API chỉ-đọc, kết quả thoát HTML, lỗi nói rõ
routes['POST /query'] = { body: { count: 1, latency_ms: 8.1, source: 'prom-1', rows: [{ metric: { instance: 'srv-<1>' }, value: [1700000000, '0'] }] } };
el('im-promql').value = 'up == 0';
await click('query');
const q = calls.filter((c) => c[1] === '/query').at(-1);
ok(q[0] === 'POST' && q[2].promql === 'up == 0', 'gửi đúng biểu thức');
ok(/srv-&lt;1&gt;/.test(el('im-query-result').innerHTML) && /1 dòng/.test(el('im-query-result').innerHTML), 'kết quả truy vấn thoát HTML');
routes['POST /query'] = { status: 422, body: { detail: 'PromQL không hợp lệ' } };
await click('query');
ok(/✖ PromQL không hợp lệ/.test(el('im-query-result').innerHTML), 'lỗi truy vấn hiện rõ');
el('im-promql').value = '';
calls.length = 0; await click('query');
ok(!calls.some((c) => c[1] === '/query') && /Nhập biểu thức/.test(el('im-query-result').innerHTML), 'ô trống: không gọi máy chủ');

// 3. chưa cấu hình
routes = { 'GET /overview': { body: { configured: false, enabled: true, summary: { health: 'UNKNOWN' }, sources: [], alerts: [] } } };
await click('refresh');
ok(/Chưa có nguồn giám sát/.test(el('im-summary').innerHTML) && !el('im-empty').classList.contains('hidden') && /Prometheus/.test(el('im-empty').innerHTML), 'chưa cấu hình: hướng dẫn thêm nguồn');
ok(el('im-alerts').innerHTML === '' && el('im-metrics').innerHTML === '', 'chưa cấu hình: không có bảng giả');
await click('goto-integration');
ok(calls.some((c) => c[0] === 'tab' && c[1] === 'system-integration'), 'nút mở Tích hợp');

// 4. tắt / thiếu quyền / lỗi máy chủ
routes = { 'GET /overview': { body: { configured: false, enabled: false, summary: {}, sources: [], alerts: [] } } };
await click('refresh');
ok(/đang <b>tắt<\/b>/.test(el('im-summary').innerHTML), 'monitoring.enabled=false: nói rõ');
routes = { 'GET /overview': { status: 403, body: { detail: 'x' } } };
await click('refresh');
ok(/không có quyền/.test(el('im-summary').textContent), 'thiếu quyền: nói rõ');
routes = {};
await click('refresh');
ok(/Không tải được/.test(el('im-summary').textContent), 'API lỗi: báo lỗi');

console.log(`OK ${passed} kiểm tra giao diện giám sát hạ tầng`);
