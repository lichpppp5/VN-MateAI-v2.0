// SPDX-License-Identifier: Apache-2.0
// Copyright (c) 2026 Dương Thanh Lịch — VN-MateAI. See LICENSE and NOTICE.
// tests/test_dev_fleet_ui.mjs
// Tab Dev Fleet (web/dev-fleet.js) với DOM giả tối thiểu + API giả: không bịa số liệu, thoát HTML, token không hiện lại,
// "chờ duyệt" không bị báo thành "đã giao", ẩn form ghi khi không phải admin.
import { readFileSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';

const ROOT = join(dirname(fileURLToPath(import.meta.url)), '..');
const src = readFileSync(join(ROOT, 'web', 'dev-fleet.js'), 'utf8');
const app = readFileSync(join(ROOT, 'web', 'app.js'), 'utf8').replace(/\r\n/g, '\n');
const html = readFileSync(join(ROOT, 'web', 'index.html'), 'utf8').replace(/\r\n/g, '\n');

let passed = 0;
const ok = (c, n) => { if (!c) { console.error(`FAIL ${n}`); process.exit(1); } passed += 1; };

// tích hợp vào cổng: tab, nav, script, tiêu đề, vòng đời vào / rời
ok(/id="tab-dev-fleet"/.test(html) && /id="nav-dev-fleet"/.test(html) && /id="dev-fleet-root"/.test(html), 'index.html có tab Dev Fleet');
ok(/\/static\/dev-fleet\.js/.test(html) && html.indexOf('dev-fleet.js') < html.indexOf('/static/app.js'), 'nạp dev-fleet.js');
ok(/VALID_TABS = \[[^\]]*'dev-fleet'/.test(app) && /'dev-fleet': 'Dev Fleet/.test(app), 'VALID_TABS + TAB_TITLES');
ok(/DevFleetUI\.onEnter\(\)/.test(app) && /DevFleetUI\.onLeave\(\)/.test(app), 'switchTab gọi onEnter / onLeave');

const elements = new Map();
function el(id) {
  if (!elements.has(id)) elements.set(id, { id, value: '', checked: false, innerHTML: '', textContent: '', className: '', disabled: false, style: {},
    listeners: {}, addEventListener(t, f) { this.listeners[t] = f; } });
  return elements.get(id);
}
let routes = {};
const calls = [];
const stub = {
  document: { getElementById: el, hidden: false },
  window: { confirm: () => true, alert: () => {} },
  setInterval: () => 1, clearInterval: () => {},
  _esc: (s) => String(s).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;'),
  API_BASE: '', showToast: (m, t) => calls.push(['toast', t, m]),
  apiErrorText: (d, s) => (d && d.detail && (d.detail.message || d.detail)) || `HTTP ${s}`,
  apiFetch: async (url, opts = {}) => {
    const path = url.replace('/api/v1/dev-fleet', '');
    calls.push([opts.method || 'GET', path, opts.body ? JSON.parse(opts.body) : null]);
    const hit = routes[`${opts.method || 'GET'} ${path.split('?')[0]}`] ?? routes[`* ${path.split('?')[0]}`];
    const r = typeof hit === 'function' ? hit(opts) : hit;
    if (!r) return { ok: false, status: 404, json: async () => ({ detail: 'không có route giả' }) };
    return { ok: r.status === undefined || r.status < 400, status: r.status ?? 200, json: async () => r.body };
  },
};
const names = Object.keys(stub);
const DevFleetUI = new Function(...names, `${src}\nreturn DevFleetUI;`)(...names.map((n) => stub[n]));
const flush = () => new Promise((r) => setTimeout(r, 0));
const root = el('dev-fleet-root');

const baseRoutes = () => ({
  'GET /config': { body: { enabled: true, mode: 'read_only', endpoint: 'https://m.local:8443', has_token: true, token_from_env: false,
    tls_verify: true, ca_bundle: '' } },
  'GET /status': { body: { enabled: true, mode: 'read_only', configured: true, reachable: true, error: null,
    master: { name: 'Ubuntu-<b>Master</b>', health: 'HEALTHY', version: '0.9', ansible_status: 'online', router_status: 'online', openclaw_status: 'online' },
    api_version: '1.0', workers: { total: 2, by_state: { IDLE: 1, OFFLINE: 1 } }, agents: { total: 1 }, tasks: { by_run_status: { RUNNING: 1 }, total: 1 } } },
  'GET /workers': { body: { reachable: true, workers: [
    { worker_id: 'mac-01', hostname: 'h<img src=x>', platform: 'macos', architecture: 'arm64', state: 'IDLE', freshness: 'FRESH',
      cpu_percent: 12.4, memory_percent: null, disk_percent: null, capabilities: ['git', 'python'], current_task: null },
    { worker_id: 'mac-03', state: 'OFFLINE', freshness: 'FRESH', cpu_percent: null, memory_percent: null, disk_percent: null, capabilities: [] }] } },
  'GET /agents': { body: { agents: [] } },
  'GET /briefing': { body: { recommended: ['Kiểm tra máy mac-03 (không còn bằng chứng là đang chạy)'] } },
  'GET /tasks': { body: { tasks: [{ task_id: 'OP-1', title: 'Sửa <script>x</script>', status: 'EXECUTING', display_status: 'EXECUTING',
    verification_status: null, result_summary: '' }] } },
  'GET /events': { body: { events: [{ ts: '2026-10-07 10:00:00', kind: 'task.dispatched', message: 'a <b>b</b>', worker_id: 'mac-01' }] } },
});

// ── 1. dựng khung + nạp cấu hình, không hiện lại token ─────────────────────
routes = baseRoutes();
DevFleetUI.onEnter();
await flush(); await flush(); await flush();
ok(/Kết nối Master - WorkNode/.test(root.innerHTML) && /Giao tác vụ mới/.test(root.innerHTML), 'khung giao diện được dựng');
ok(el('df-endpoint').value === 'https://m.local:8443' && el('df-token').value === '', 'nạp endpoint, ô token để trống');
ok(/đã lưu/.test(el('df-token-note').textContent) && !/TOKEN/.test(JSON.stringify([...elements.values()].map((x) => x.value))), 'chỉ báo "đã lưu", không có giá trị token');
ok(el('df-mode').value === 'read_only' && /Chỉ xem/.test(el('df-mode-hint').textContent), 'chế độ + gợi ý');

// ── 2. hiển thị: thiếu số liệu = "—", thoát HTML ───────────────────────────
const w = el('df-workers').innerHTML;
ok(/mac-01/.test(w) && /CPU 12%/.test(w) && /RAM —/.test(w) && /Đĩa —/.test(w), 'số đo thiếu hiện "—", không bịa 0');
ok(!/<img src=x>/.test(w) && /&lt;img/.test(w), 'hostname được thoát HTML');
ok(/OFFLINE/.test(w) && /Tắt máy/.test(w), 'máy offline + nút tắt máy');
ok(/&lt;b&gt;Master/.test(el('df-status').innerHTML) && !/<b>Master/.test(el('df-status').innerHTML), 'tên Master được thoát HTML');
ok(/&lt;script&gt;x/.test(el('df-tasks').innerHTML) && !/<script>x/.test(el('df-tasks').innerHTML), 'tiêu đề tác vụ được thoát HTML');
ok(/mac-03/.test(el('df-brief').innerHTML), 'mục cần chú ý');
ok(/task\.dispatched/.test(el('df-events').innerHTML) && !/<b>b<\/b>/.test(el('df-events').innerHTML), 'hoạt động trực tiếp, thoát HTML');

// ── 3. thử kết nối: gửi giá trị đang nhập; kết quả thật, lỗi nói rõ ───────
const click = async (act, id, extra = {}) => {
  const btn = { dataset: { df: act, id, ...extra }, disabled: false };
  await root.listeners.click({ target: { closest: () => btn } });
  await flush();
  return btn;
};
el('df-endpoint').value = 'https://moi.local:8443'; el('df-token').value = 'TOKEN-MOI'; el('df-ca').value = ''; el('df-tls').checked = false;
routes['POST /test-connection'] = { body: { ok: true, api_version: '1.0', workers: 3, latency_ms: 12.5, master: { name: 'Ubuntu-Master' } } };
await click('test');
const sent = calls.filter((c) => c[1] === '/test-connection').at(-1)[2];
ok(sent.endpoint === 'https://moi.local:8443' && sent.api_token === 'TOKEN-MOI' && sent.tls_verify === false, 'thử kết nối gửi giá trị đang nhập');
ok(/Kết nối được/.test(el('df-test-result').textContent) && /3 worker/.test(el('df-test-result').textContent), 'báo thành công kèm số liệu thật');
routes['POST /test-connection'] = { body: { ok: false, error: 'Master từ chối thông tin xác thực', kind: 'rejected' } };
await click('test');
ok(/✖ Master từ chối/.test(el('df-test-result').textContent) && /rose/.test(el('df-test-result').className), 'báo lỗi rõ ràng');

// ── 4. lưu cấu hình ────────────────────────────────────────────────────────
routes['POST /config'] = { body: { enabled: true } };
await click('save');
ok(calls.some((c) => c[0] === 'POST' && c[1] === '/config' && c[2].api_token === 'TOKEN-MOI'), 'lưu cấu hình qua API');
routes['POST /config'] = { status: 422, body: { detail: 'Địa chỉ Master phải dạng https://host[:cổng]' } };
calls.length = 0; await click('save');
ok(calls.some((c) => c[0] === 'toast' && c[1] === 'error' && /Địa chỉ Master/.test(c[2])), 'lỗi 422 hiện cho người dùng');

// ── 5. giao việc: chờ duyệt KHÔNG được báo là đã giao ─────────────────────
el('df-t-title').value = 'Sửa reconnect'; el('df-t-obj').value = 'Sửa lỗi websocket không tự nối lại'; el('df-t-acc').value = 'test pass\n\n';
el('df-t-ver').value = 'pytest'; el('df-t-caps').value = 'git, python'; el('df-t-risk').value = 'high'; el('df-t-prio').value = 'high';
el('df-t-tests').checked = true; el('df-t-build').checked = false; el('df-t-commit').checked = false; el('df-t-rollback').value = 'git revert';
routes['POST /tasks'] = { body: { status: 'awaiting_approval', approval_id: 'AP-9', task_id: 'OP-7' } };
await click('submit');
const body = calls.filter((c) => c[0] === 'POST' && c[1] === '/tasks').at(-1)[2];
ok(body.acceptance_criteria.length === 1 && body.required_capabilities.join() === 'git,python' && /^ui-/.test(body.idempotency_key), 'đặc tả gửi đúng + khoá idempotency');
ok(/CHƯA giao/.test(el('df-plan-result').innerHTML) && /AP-9/.test(el('df-plan-result').innerHTML) && !/Đã giao/.test(el('df-plan-result').innerHTML), 'chờ duyệt không bị báo là đã giao');
routes['POST /tasks'] = { body: { status: 'dispatched', task_id: 'OP-8', worker_id: 'mac-01' } };
await click('submit');
ok(/Đã giao OP-8 cho mac-01/.test(el('df-plan-result').innerHTML), 'giao thành công');
routes['POST /tasks'] = { status: 409, body: { status: 'no_worker', error: 'Không có worker phù hợp' } };
await click('submit');
ok(/✖ Không có worker phù hợp/.test(el('df-plan-result').innerHTML), 'không có máy phù hợp = lỗi, không giả vờ giao');

// ── 6. dry-run hiện lý do loại ────────────────────────────────────────────
routes['POST /tasks/plan'] = { body: { ok: true, worker_id: 'mac-01', score: 118.5, risk: 'high', requires_approval: true,
  rejected: [{ worker_id: 'mac-03', reason: 'trạng thái OFFLINE không nhận việc' }] } };
await click('plan');
ok(/mac-01/.test(el('df-plan-result').innerHTML) && /cần người duyệt/.test(el('df-plan-result').innerHTML) && /mac-03: trạng thái OFFLINE/.test(el('df-plan-result').innerHTML), 'dry-run: máy được chọn + lý do loại');

// ── 7. huỷ / chạy lại / tắt máy ───────────────────────────────────────────
routes['POST /tasks/OP-1/cancel'] = { body: { status: 'cancelled' } };
calls.length = 0; await click('cancel', 'OP-1');
ok(calls.some((c) => c[0] === 'POST' && c[1] === '/tasks/OP-1/cancel') && calls.some((c) => c[0] === 'toast' && c[1] === 'success'), 'huỷ tác vụ');
routes['POST /tasks/OP-1/retry'] = { body: { status: 'refused', error: 'Không chắc lượt trước đã dừng' } };
calls.length = 0; await click('retry', 'OP-1');
ok(calls.some((c) => c[0] === 'toast' && c[1] === 'warning' && /Không chắc/.test(c[2])), 'chạy lại bị từ chối được báo rõ');
routes['POST /workers/mac-01/disable'] = { body: { disabled: true } };
calls.length = 0; await click('toggle', 'mac-01', { disabled: '1' });
ok(calls.some((c) => c[1] === '/workers/mac-01/disable' && c[2].disabled === true), 'tắt máy theo kill switch');

// ── 8. module tắt / Master chết / không phải admin ────────────────────────
routes = baseRoutes();
routes['GET /status'] = { body: { enabled: false, mode: 'disabled', master: null, workers: null, tasks: null } };
DevFleetUI.onEnter(); await flush(); await flush(); await flush();
ok(/đang <b>tắt<\/b>/.test(el('df-status').innerHTML) && el('df-workers').innerHTML === '', 'module tắt: nói rõ, không có bảng giả');
routes['GET /status'] = { body: { enabled: true, mode: 'controlled', configured: true, reachable: false, error: 'Master không liên lạc được: timeout',
  last_ok_at: '2026-10-07T09:00:00+00:00', master: { health: 'OFFLINE' }, workers: { total: 1, by_state: { UNKNOWN: 1 } }, tasks: { by_run_status: {} } } };
await DevFleetUI.refresh();
ok(/KHÔNG còn được bảo đảm/.test(el('df-status').innerHTML) && /timeout/.test(el('df-status').innerHTML), 'Master chết: cảnh báo trạng thái worker không còn bảo đảm');
routes['GET /config'] = { status: 403, body: { detail: 'Quyền hạn không được phép' } };
DevFleetUI.onEnter(); await flush(); await flush(); await flush();
ok(el('df-config-card').style.display === 'none' && el('df-new-card').style.display === 'none', 'không phải admin: ẩn form cấu hình và giao việc');
routes['GET /status'] = { status: 403, body: { detail: 'Quyền hạn không được phép' } };
await DevFleetUI.refresh();
ok(/không có quyền/.test(el('df-status').textContent), 'không có quyền xem: nói rõ');

console.log(`OK ${passed} kiểm tra Dev Fleet UI`);
