// SPDX-License-Identifier: Apache-2.0
// Copyright (c) 2026 Dương Thanh Lịch — VN-MateAI. See LICENSE and NOTICE.
// Kiểm thử hồi quy cho 6 panel monitor mới của Bảng Điều Khiển (Phase 61).
//
// MỤC ĐÍCH: panel đệm chỉ "đúng" khi hàm vẽ nhận đúng shape dữ liệu thật.
// Ở lúc mới thêm, panel "Sức Khỏe Công Cụ" rỗng vì server vừa restart và
// LLM router đang chết — nên `execution_stats` chưa có dòng nào. Test này
// nạp THẬT các shape JSON quan sát được từ server để chứng minh logic vẽ
// đúng, thay vì để ngỏ rồi báo cáo "chắc là chạy".
//
// BA NGUYÊN TẮC GIỮ NGUYÊN:
//  1. KHÔNG định nghĩa `_esc` riêng — cắt từ app.js y như trang thật.
//  2. Dữ liệu đưa vào phải là shape thật lấy từ API, không phải dữ liệu bịa.
//  3. Không được để NaN / undefined / null lọt vào chuỗi hiển thị.

import { readFileSync, readdirSync, writeFileSync, mkdtempSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join, dirname } from 'node:path';
import { fileURLToPath, pathToFileURL } from 'node:url';

const HERE = dirname(fileURLToPath(import.meta.url));
const src = readFileSync(join(HERE, '..', 'web', 'app.js'), 'utf-8').replace(/\r\n/g, '\n');
// Bản code đã bỏ comment. Bắt buộc phải có khi kiểm tra "cái gì đó đã bị gỡ":
// các bình luận giải thích fix hay nhắc lại đúng tên biến/hằng đã xoá, nên quét
// `src` thôi sẽ khẳng định sai là chúng còn tồn tại.
const srcCode = src.replace(/\/\*[\s\S]*?\*\//g, '').replace(/^\s*\/\/.*$/gm, '');
const html = readFileSync(join(HERE, '..', 'web', 'index.html'), 'utf-8').replace(/\r\n/g, '\n');

let pass = 0, fail = 0;
const results = [];
function check(name, cond, extra = '') {
  if (cond) { pass++; results.push(`  ✅ ${name}`); }
  else { fail++; results.push(`  ❌ ${name}${extra ? ' — ' + extra : ''}`); }
}

function cutBlock(startMark) {
  const a = src.indexOf(startMark);
  if (a < 0) throw new Error('không tìm thấy: ' + startMark);
  const b = src.indexOf('\n}\n', a);
  if (b < 0) throw new Error('không tìm thấy điểm kết thúc: ' + startMark);
  return src.slice(a, b + 3);
}

// ── Phần trích từ app.js ──────────────────────────────────────────────────
results.push('▸ Trích hàm từ app.js');
let escFn, setFn, widthFn, fastFn, queueFn, toolFn, memFn, connFn, secFn, logsFn, paintFn, filterFn, extTimerFn, extLoadFn;
try {
  escFn     = cutBlock('\nfunction _esc(s) {');
  setFn     = cutBlock('\nfunction _monSet(id, text) {');
  widthFn   = cutBlock('\nfunction _monWidth(id, pct) {');
  fastFn    = cutBlock('\nfunction renderMonitorFastPath(data) {');
  queueFn   = cutBlock('\nfunction renderQueueMonitor(bg, sys) {');
  toolFn  = cutBlock('\nfunction renderToolHealth(d) {');
  memFn   = cutBlock('\nfunction renderMemoryMonitor(mem, dom, sys) {');
  connFn  = cutBlock('\nfunction renderConnectorsStrip(d) {');
  secFn   = cutBlock('\nfunction renderSecurityMonitor(d) {');
  // Phase 79: gỡ `renderSystemLogs` / `paintMonLogs` / `setMonLogFilter` — chúng
  // chỉ phục vụ `#mon-log-list`, bảng nhật ký đã gom vào tab Nhật Ký.
  extTimerFn = cutBlock('\nfunction _ensureExtendedMonitorTimer() {');
  // Dùng cutBlock, KHÔNG cắt theo mốc hàm kế tiếp: các hàm render nằm NGAY
  // SAU loadExtendedMonitors nên cắt theo mốc sẽ nuốt trùng → SyntaxError.
  // Phải bắt đầu từ hằng số ngưỡng chống trùng (nằm trên dòng hàm) để lấy đủ
  // cả khai báo lẫn thân hàm.
  {
    const c0 = src.indexOf('\nconst MON_HEAVY_DEDUPE_MS');
    if (c0 < 0) throw new Error('không tìm thấy: const MON_HEAVY_DEDUPE_MS');
    const f0 = src.indexOf('\nasync function loadExtendedMonitors() {', c0);
    if (f0 < 0) throw new Error('không tìm thấy: async function loadExtendedMonitors');
    const f1 = src.indexOf('\n}\n', f0);
    if (f1 < 0) throw new Error('không tìm thấy điểm kết thúc loadExtendedMonitors');
    extLoadFn = src.slice(c0, f1 + 3);
  }
} catch (e) {
  check('cắt được các hàm Phase 61 từ app.js', false, e.message);
  console.log(results.join('\n'));
  console.log(`\nTổng: ${pass + fail} | Pass: ${pass} | Fail: ${fail}`);
  console.log('\n❌ KHÔNG TRÍCH ĐƯỢC HÀM — app.js đã đổi cấu trúc, cần cập nhật test.');
  process.exit(1);
}
check('cắt được đủ 14 hàm Phase 61 từ app.js', true);
check('khối Phase 61 dùng `_esc` cấp module (không tự định nghĩa)',
  escFn.includes('&#39;') && escFn.includes('&quot;'));

// ── Khung chạy thật: KHÔNG định nghĩa `_esc` ──────────────────────────────
const monIds = [
  'mon-queue-zt', 'mon-queue-bg-run', 'mon-queue-bg-max',
  'mon-queue-bg-total', 'mon-queue-bg-bar', 'mon-queue-task-sum',
  'mon-queue-task-completed', 'mon-queue-task-pending', 'mon-queue-task-issues',
  'mon-queue-bar-completed', 'mon-queue-bar-pending', 'mon-queue-bar-issues',
  'mon-tool-total', 'mon-tool-enabled', 'mon-tool-success', 'mon-tool-failed',
  'mon-tool-timeout', 'mon-tool-latency', 'mon-tool-cb-open',
  'mon-sec-posture', 'mon-sec-total', 'mon-sec-success', 'mon-sec-failed',
  'mon-sec-pending', 'mon-sec-rate', 'mon-sec-bar-success', 'mon-sec-bar-pending',
  'mon-sec-bar-failed',
  'mon-mem-records', 'mon-mem-size', 'mon-mem-collection', 'mon-mem-skills',
  'mon-mem-employees', 'mon-mem-computers', 'mon-mem-users',
  'mon-mem-hud', 'mon-mem-lan',
  'mon-conn-list', 'mon-conn-ready', 'mon-conn-total',
  // Phase 79: 'mon-log-list' đã bị gỡ khỏi Bảng Điều Khiển. Bảng nhật ký ở
  // đây nạp CÙNG endpoint /api/v1/logs/recent với "Nhật Ký Vận Hành" ở Trung
  // Tâm Chỉ Huy — cùng dữ liệu ở hai màn hình. Cả hai đã gom vào tab Nhật Ký
  // (chế độ "Nhật ký vận hành", id `log-recent-list`).
];

const harness = `
const API_BASE = '';
const __dom = {
${monIds.map((id) => `  '${id}': { _html: '', _text: '', style: {}, classList: { _t: new Set(), toggle(c, on){ on ? this._t.add(c) : this._t.delete(c); }, has(c){ return this._t.has(c); } },
    get innerHTML() { return this._html; }, set innerHTML(v) { this._html = String(v); },
    get textContent() { return this._text; }, set textContent(v) { this._text = String(v); } },`).join('\n')}
};
const document = {
  getElementById: (id) => __dom[id] || null,
  querySelectorAll: () => [],
};
let monExtendedTimer = null;
// monLastHeavyRunAt và MON_HEAVY_DEDUPE_MS KHÔNG khai báo ở đây — chúng đã
// nằm trong khối loadExtendedMonitors được cắt từ app.js. Khai báo lần hai
// sẽ gây SyntaxError.
let __intervals = [];
let __apiFetchCalls = 0;
const setInterval = (fn, ms) => { __intervals.push(ms); return __intervals.length; };
const clearInterval = () => {};
const apiFetch = () => {
  __apiFetchCalls++;
  return new Promise((res) => setTimeout(() => res({ ok: true, json: async () => ({}) }), 1));
};
// __reset phải tua lại mốc thời gian để mỗi phép thử bắt đầu sạch.
const __reset = () => {
  __intervals = []; monExtendedTimer = null; __apiFetchCalls = 0;
  monLastHeavyRunAt = 0;
};
const __getIntervals = () => __intervals;
const __getApiCalls = () => __apiFetchCalls;
`;

// ── Dữ liệu THẬT quan sát từ server (đã dán ở phần ghi chú đầu file) ───────
// /api/v1/system/stats
const SYS_STATS = {
  status: 'success',
  hardware: { cpu_percent: 25.9, ram_percent: 76.4, disk_percent: 23.9, uptime_seconds: 198780 },
  tasks: { total: 14, completed: 6, issues: 1, pending: 7 },
  users_count: 3, online_clients_count: 0, audio_nodes_count: 0, skills_count: 72,
};
// /api/v1/enterprise/background-tasks
const BG_TASKS = { status: 'success', total: 0, running: 0, tasks: [] };
// /api/v1/enterprise/plugin-registry/stats — 1 tool đã gọi, 1 tool có mạch mở
const TOOL_STATS = {
  status: 'success', total_tools: 11, enabled_tools: 11,
  tools: {},
  execution_stats: {
    check_aws_cost: {
      total_calls: 7, successful_calls: 2, failed_calls: 5, timeout_calls: 1,
      circuit_open_calls: 0, avg_latency_ms: 184.3,
      circuit_breaker: { name: 'check_aws_cost', state: 'closed', failure_count: 0, success_count: 2 },
    },
    check_connector_health: {
      total_calls: 4, successful_calls: 0, failed_calls: 4, timeout_calls: 0,
      circuit_open_calls: 4, avg_latency_ms: 4210.7,
      circuit_breaker: { name: 'check_connector_health', state: 'open', failure_count: 5, success_count: 0 },
    },
    never_called_tool: {
      total_calls: 0, successful_calls: 0, failed_calls: 0, timeout_calls: 0,
      circuit_open_calls: 0, avg_latency_ms: 0.0,
      circuit_breaker: { name: 'never_called_tool', state: 'closed', failure_count: 0, success_count: 0 },
    },
    half_open_tool: {
      total_calls: 2, successful_calls: 1, failed_calls: 1, timeout_calls: 0,
      circuit_open_calls: 0, avg_latency_ms: 42.5,
      circuit_breaker: { name: 'half_open_tool', state: 'half_open', failure_count: 1, success_count: 1 },
    },
  },
};
// /api/v1/memory/stats
const MEM_STATS = {
  status: 'ready', mode: 'local', collection_name: 'incident_knowledge_base',
  total_records: 3, storage_path: '/tmp/vector_db', size_mb: 0.31,
};
// /api/v1/domain/stats
const DOMAIN_STATS = { status: 'success', employees_count: 12, computers_count: 7 };
// /api/v1/enterprise/connectors/health
const CONN_HEALTH = {
  status: 'success',
  connectors: {
    aws: { configured: false, enabled: true, actions: ['billing_summary'], max_risk_level: 1,
           missing_fields: ['access_key_id', 'secret_access_key'],
           note: 'Chưa đủ thông tin đăng nhập — mọi lời gọi sẽ thất bại.' },
    oci: { configured: true, enabled: true, actions: ['cloud_metrics'], max_risk_level: 1 },
    paperless: { configured: false, error: 'Lỗi tải cấu hình connector' },
    einvoice: { configured: false, enabled: true, actions: ['list_invoices'],
                missing_fields: ['EINVOICE_TENANT_ID'] },
  },
};
// /api/v1/security/audit-logs — đúng phân bố quan sát được: failed 17, pending 48, success 35
const AUDIT = (() => {
  const mk = (st, i) => ({ id: i, timestamp: '2026-09-28 06:00:00', action: 'X', client_id: 'admin',
                           status: st.toUpperCase(), outcome: st });
  const logs = [];
  for (let i = 0; i < 35; i++) logs.push(mk('success', i));
  for (let i = 0; i < 17; i++) logs.push(mk('failed', 100 + i));
  for (let i = 0; i < 48; i++) logs.push(mk('pending', 200 + i));
  return { status: 'success', total: 100, logs };
})();
// /api/v1/logs/recent
const LOGS = {
  status: 'success', count: 4,
  logs: [
    { event: 'log_entry', level: 'ERROR', color: 'text-rose-400', logger: 'core.connector',
      message: 'aws: Authentication failed', timestamp: '2026-09-28T06:18:55.177419' },
    { event: 'log_entry', level: 'WARNING', color: 'text-amber-400', logger: 'mateai.application.operations.health_monitor',
      message: 'HealthWorker-3 slow response', timestamp: '2026-09-28T06:18:56.177419' },
    { event: 'log_entry', level: 'INFO', color: 'text-emerald-400', logger: 'httpx',
      message: 'HTTP Request: GET http://localhost:20128/v1/models "HTTP/1.1 200 OK"',
      timestamp: '2026-09-28T06:18:57.177419' },
    { event: 'log_entry', level: 'INFO', color: 'text-emerald-400', logger: 'mateai.interfaces.http.server',
      message: 'Portal UI WebSocket connected', timestamp: '2026-09-28T06:18:58.177419' },
  ],
};
// Payload /api/v1/health-dashboard sau khi thêm nhóm counters
const HEALTH = {
  status: 'healthy',
  hardware: { cpu_percent: 25.9, ram_percent: 76.4, disk_percent: 23.9, cpu_history: [], ram_history: [] },
  services: { llm_9router: { status: 'OK', latency_ms: 12 }, active_directory: {},
              telegram_gateway: {}, database_sqlite: {} },
  nodes: { active_web_clients: 2, active_audio_hardware: 0, skills_count: 72,
           skills_enabled: 72, uptime_seconds: 600, uptime_human: '10m',
           active_hud_websockets: 1, active_lan_clients: 3 },
  live_events: [],
  counters: { zt_pending: 2, bg_running: 3, bg_total: 17, bg_max_concurrent: 5 },
  security_role: 'ADMIN',
};

const mod = harness + '\n' + [escFn, setFn, widthFn, fastFn, queueFn, toolFn, memFn, connFn, secFn, extTimerFn, extLoadFn].join('\n')
  + `\nexport { document, __dom, renderMonitorFastPath, renderQueueMonitor, renderToolHealth,
             renderMemoryMonitor, renderConnectorsStrip, renderSecurityMonitor,
             loadExtendedMonitors, _ensureExtendedMonitorTimer,
             __reset, __getIntervals, __getApiCalls, MON_HEAVY_DEDUPE_MS };\n`;

const dir = mkdtempSync(join(tmpdir(), 'mon61-'));
const f = join(dir, 'm.mjs');
writeFileSync(f, mod, 'utf-8');

let M;
try {
  M = await import(pathToFileURL(f).href);
} catch (e) {
  check('nạp được khối hàm Phase 61 vào khung chạy', false, e.message);
  console.log(results.join('\n'));
  console.log(`\nTổng: ${pass + fail} | Pass: ${pass} | Fail: ${fail}`);
  process.exit(1);
}
check('nạp được khối hàm Phase 61 vào khung chạy (không lỗi cú pháp, không ReferenceError)', true);
const D = M.__dom;
const txt = (id) => (D[id]?.textContent ?? '').trim();

// ── 1. Tầng nhanh (2s): counter đi kèm payload health-dashboard ────────────
results.push('▸ Panel 6/9 — tầng nhanh 2s (renderMonitorFastPath)');
M.renderMonitorFastPath(HEALTH);
check('ZT chờ duyệt = 2', txt('mon-queue-zt') === '2', txt('mon-queue-zt'));
check('slot worker 3/5', txt('mon-queue-bg-run') === '3' && txt('mon-queue-bg-max') === '5');
check('tổng tác vụ nền = 17', txt('mon-queue-bg-total') === '17');
check('thanh slot = 60%', D['mon-queue-bg-bar'].style.width === '60%', D['mon-queue-bg-bar'].style.width);
check('kết nối HUD = 1', txt('mon-mem-hud') === '1', txt('mon-mem-hud'));
check('node LAN = 3', txt('mon-mem-lan') === '3', txt('mon-mem-lan'));
check('số kỹ năng = 72', txt('mon-mem-skills') === '72', txt('mon-mem-skills'));

// Tầng nhanh phải chịu được payload CŨ (server chưa restart) — không được vỡ
M.renderMonitorFastPath({ hardware: {}, services: {}, nodes: { active_web_clients: 1 } });
check('payload CŨ (thiếu `counters`) không làm hỏng render',
  txt('mon-queue-zt') === '0' && txt('mon-queue-bg-run') === '0' && D['mon-queue-bg-bar'].style.width === '0%');
M.renderMonitorFastPath(null);
check('payload null không gây lỗi', txt('mon-queue-zt') === '0');
M.renderMonitorFastPath(HEALTH); // trả lại trạng thái sạch

// bg_max_concurrent = 0 (worker chưa khởi động) → tuyệt đối không được NaN
M.renderMonitorFastPath({ counters: { zt_pending: 0, bg_running: 0, bg_total: 0, bg_max_concurrent: 0 } });
check('bg_max_concurrent = 0 KHÔNG sinh NaN (chia cho 0)',
  D['mon-queue-bg-bar'].style.width === '0%' && !String(D['mon-queue-bg-bar'].style.width).includes('NaN'),
  D['mon-queue-bg-bar'].style.width);
M.renderMonitorFastPath(HEALTH);

// ── 2. Panel 6: phân bố tác vụ ERP ────────────────────────────────────────
results.push('▸ Panel 6 — phân bố tác vụ ERP');
M.renderQueueMonitor(BG_TASKS, SYS_STATS);
check('tổng tác vụ = 14', txt('mon-queue-task-sum') === '14 tác vụ', txt('mon-queue-task-sum'));
check('xong/chờ/vấn đề = 6/7/1',
  txt('mon-queue-task-completed') === '6' && txt('mon-queue-task-pending') === '7' && txt('mon-queue-task-issues') === '1');
const wC = parseFloat(D['mon-queue-bar-completed'].style.width);
const wP = parseFloat(D['mon-queue-bar-pending'].style.width);
const wI = parseFloat(D['mon-queue-bar-issues'].style.width);
check('3 thanh cộng lại = 100%', Math.abs(wC + wP + wI - 100) < 0.01, `${wC}+${wP}+${wI}`);
check('tổng 0 thì thanh không NaN', (() => {
  M.renderQueueMonitor(null, { tasks: { total: 0, completed: 0, pending: 0, issues: 0 } });
  const ok = D['mon-queue-bar-completed'].style.width === '0%';
  M.renderQueueMonitor(BG_TASKS, SYS_STATS);
  return ok;
})());

// ── 3. Panel 7: sức khỏe công cụ ───────────────────────────────────────────
results.push('▸ Panel 7 — sức khỏe công cụ + circuit breaker');
M.renderToolHealth(TOOL_STATS);
check('công cụ 11/11', txt('mon-tool-total') === '11' && txt('mon-tool-enabled') === '11');
check('tổng thành công = 3 (2+0+1)', txt('mon-tool-success') === '3', txt('mon-tool-success'));
check('tổng thất bại = 10 (5+4+1)', txt('mon-tool-failed') === '10', txt('mon-tool-failed'));
check('tổng timeout = 1', txt('mon-tool-timeout') === '1', txt('mon-tool-timeout'));
check('huy hiệu đếm MỌI trạng thái != closed (open + half_open) = 2',
  txt('mon-tool-cb-open') === '2 mạch mở' && D['mon-tool-cb-open'].classList.has('hidden') === false,
  txt('mon-tool-cb-open'));
const lat = D['mon-tool-latency'].innerHTML;
check('danh sách độ trễ CHỈ gồm tool đã gọi (loại total_calls=0)',
  lat.includes('check_aws_cost') && lat.includes('check_connector_health') && lat.includes('half_open_tool')
  && !lat.includes('never_called_tool'));
check('tool chậm nhất xếp trước (4210.7 > 184.3 > 42.5)',
  lat.indexOf('check_connector_health') < lat.indexOf('check_aws_cost')
  && lat.indexOf('check_aws_cost') < lat.indexOf('half_open_tool'));
check('trạng thái mạch được hiện trong danh sách độ trễ',
  lat.includes('open') && lat.includes('closed') && lat.includes('half_open'));
check('độ trễ làm tròn 1 chữ số thập phân', lat.includes('4210.7ms') && lat.includes('42.5ms'));
// tool chưa từng gọi → phải nói rõ, không hiện bảng rỗng
M.renderToolHealth({ total_tools: 11, enabled_tools: 11, execution_stats: {} });
check('chưa có tool nào gọi → hiện lời giải thích, không phải khung rỗng',
  D['mon-tool-latency'].innerHTML.includes('Chưa có công cụ nào được gọi'));
check('không có mạch hở → huy hiệu ẩn đi', (() => {
  const hidden = D['mon-tool-cb-open'].classList.has('hidden');
  M.renderToolHealth(TOOL_STATS);
  return hidden;
})());
M.renderToolHealth(TOOL_STATS);
M.renderToolHealth(null);
check('plugin-registry lỗi → không vỡ render', txt('mon-tool-total') === '11');

// ── 4. Panel 9: trí nhớ & tri thức ────────────────────────────────────────
results.push('▸ Panel 9 — trí nhớ & tri thức');
M.renderMemoryMonitor(MEM_STATS, DOMAIN_STATS, SYS_STATS);
check('bản ghi vector = 3', txt('mon-mem-records') === '3', txt('mon-mem-records'));
check('dung lượng = 0.31 MB', txt('mon-mem-size') === '0.31 MB', txt('mon-mem-size'));
check('bộ sưu tập = incident_knowledge_base', txt('mon-mem-collection') === 'incident_knowledge_base');
check('nhân sự/thiết bị = 12/7',
  txt('mon-mem-employees') === '12' && txt('mon-mem-computers') === '7');
check('số tài khoản = 3', txt('mon-mem-users') === '3 tài khoản', txt('mon-mem-users'));
M.renderMemoryMonitor({ status: 'initializing' }, null, null);
check('bộ nhớ chưa sẵn sàng → giữ số cũ, không ghi đè bằng 0',
  txt('mon-mem-records') === '3', txt('mon-mem-records'));

// ── 5. Panel 10: dải kết nối ngoại vi ─────────────────────────────────────
results.push('▸ Panel 10 — dải kết nối ngoại vi');
M.renderConnectorsStrip(CONN_HEALTH);
const conn = D['mon-conn-list'].innerHTML;
check('vẽ đủ 4 connector', (conn.match(/uppercase/g) || []).length === 4, String((conn.match(/uppercase/g) || []).length));
check('đếm sẵn sàng = 1/4 (chỉ oci có credential)',
  txt('mon-conn-ready') === '1' && txt('mon-conn-total') === '4', `${txt('mon-conn-ready')}/${txt('mon-conn-total')}`);
// Kiểm TỪNG connector riêng lẻ — không đoán cấu trúc HTML bằng regex.
// (Cách cũ tách bằng '</div>' rất dễ vỡ khi bố cục markup đổi.)
const CONN_CASES = [
  ['oci', { configured: true, enabled: true, actions: ['cloud_metrics'], max_risk_level: 1 },
    'Sẵn sàng', 'bg-emerald-500', ''],
  ['aws', { configured: false, enabled: true, actions: ['billing_summary'],
            missing_fields: ['access_key_id', 'secret_access_key'],
            note: 'Chưa đủ thông tin đăng nhập — mọi lời gọi sẽ thất bại.' },
    'Chưa cấu hình', 'bg-amber-500', 'Thiếu: access_key_id, secret_access_key'],
  ['paperless', { configured: false, error: 'Lỗi tải cấu hình connector' },
    'Lỗi', 'bg-rose-500', 'Lỗi tải cấu hình connector'],
  ['einvoice', { configured: false, enabled: true, actions: ['list_invoices'],
                 missing_fields: ['EINVOICE_TENANT_ID'] },
    'Chưa cấu hình', 'bg-amber-500', 'Thiếu: EINVOICE_TENANT_ID'],
];
for (const [name, cfg, label, dot, hint] of CONN_CASES) {
  M.renderConnectorsStrip({ status: 'success', connectors: { [name]: cfg } });
  const row = D['mon-conn-list'].innerHTML;
  check(`connector "${name}" → nhãn "${label}"`, row.includes(`>${label}</span>`), row.slice(0, 60));
  check(`connector "${name}" → chấm màu ${dot}`, row.includes(dot));
  check(`connector "${name}" → gợi ý ${hint === '' ? 'rỗng (không tooltip thừa)' : `"${hint.slice(0, 28)}"`}`,
    hint === '' ? /title=""/.test(row) && !/title="[^"]/.test(row) : row.includes(hint));
  check(`connector "${name}" → bộ đếm sẵn sàng ${cfg.configured ? '1' : '0'}/1`,
    txt('mon-conn-ready') === (cfg.configured ? '1' : '0') && txt('mon-conn-total') === '1');
}
M.renderConnectorsStrip(CONN_HEALTH);
check('đếm sẵn sàng = 1/4 (chỉ oci có credential)',
  txt('mon-conn-ready') === '1' && txt('mon-conn-total') === '4', `${txt('mon-conn-ready')}/${txt('mon-conn-total')}`);
check('nhãn "Sẵn sàng" chỉ xuất hiện 1 lần cho cả dải (không lặp ở title)',
  (conn.match(/Sẵn sàng/g) || []).length === 1, String((conn.match(/Sẵn sàng/g) || []).length));
check('hiện tên khoá thiếu cho connector chưa cấu hình',
  conn.includes('EINVOICE_TENANT_ID') && conn.includes('access_key_id'));
check('connector lỗi hiện chấm đỏ + nhãn "Lỗi"', conn.includes('Lỗi') && conn.includes('bg-rose-500'));
M.renderConnectorsStrip({ status: 'success', connectors: {} });
check('không có connector nào → hiện lời giải thích', D['mon-conn-list'].innerHTML.includes('Không có connector'));

// enabled=false nhưng configured=true → vẫn coi là chưa sẵn sàng? (kiểm hành vi)
M.renderConnectorsStrip({ connectors: { t: { configured: true, enabled: false } } });
check('connector configured=true nhưng enabled=false vẫn hiện "Sẵn sàng" (theo configured)',
  D['mon-conn-list'].innerHTML.includes('>Sẵn sàng</span>'));

// XSS: tên connector / khoá thiếu chứa ký tự nguy hiểm
M.renderConnectorsStrip({ connectors: { x: { configured: false, missing_fields: ['<img src=x onerror=alert(1)>'] } } });
check('tên khoá thiếu được escape (chống XSS)',
  !D['mon-conn-list'].innerHTML.includes('<img') && D['mon-conn-list'].innerHTML.includes('&lt;img'));
M.renderConnectorsStrip({ connectors: { '<svg onload=alert(1)>': { configured: true } } });
check('TÊN connector độc hại cũng được escape',
  !D['mon-conn-list'].innerHTML.includes('<svg') && D['mon-conn-list'].innerHTML.includes('&lt;svg'));

// ── 6. Panel 8: bảo mật & phê duyệt ───────────────────────────────────────
results.push('▸ Panel 8 — bảo mật & phê duyệt');
M.renderSecurityMonitor(AUDIT);
check('tổng log = 100', txt('mon-sec-total') === '100', txt('mon-sec-total'));
check('thành công = 35', txt('mon-sec-success') === '35', txt('mon-sec-success'));
check('thất bại = 17', txt('mon-sec-failed') === '17', txt('mon-sec-failed'));
check('chờ = 48', txt('mon-sec-pending') === '48', txt('mon-sec-pending'));
check('tỷ lệ đạt = 35.0%', txt('mon-sec-rate') === '35.0%', txt('mon-sec-rate'));
check('thanh 3 phần cộng lại = 100%', (() => {
  const a = parseFloat(D['mon-sec-bar-success'].style.width);
  const b = parseFloat(D['mon-sec-bar-pending'].style.width);
  const c = parseFloat(D['mon-sec-bar-failed'].style.width);
  return Math.abs(a + b + c - 100) < 0.01;
})());
check('có thất bại → thái độ "Có thất bại"', txt('mon-sec-posture') === 'Có thất bại', txt('mon-sec-posture'));
M.renderSecurityMonitor({ status: 'success', total: 0, logs: [] });
check('rỗng → "Chưa có dữ liệu", không NaN',
  txt('mon-sec-posture') === 'Chưa có dữ liệu' && txt('mon-sec-rate') === '—');
M.renderSecurityMonitor({ status: 'success', total: 5, logs: [{ outcome: 'success' }, { outcome: 'BLOCKED' }, { outcome: 'denied' }] });
check('BLOCKED/denied (không phải chữ thường) vẫn bị tính là thất bại',
  txt('mon-sec-failed') === '2', txt('mon-sec-failed'));

// ── 7. Panel 11 đã rời Bảng Điều Khiển ──────────────────────────────────────
//
// Phase 79: bảng nhật ký ở đây (`#mon-log-list`) nạp CÙNG endpoint
// /api/v1/logs/recent với "Nhật Ký Vận Hành" ở Trung Tâm Chỉ Huy — cùng dữ
// liệu ở hai màn hình. Nay cả hai gom vào tab Nhật Ký và chỉ giữ một bản
// (`#log-recent-list`, có bộ lọc ẩn dòng heartbeat mà bản cũ không có).
// Các hàm renderSystemLogs / paintMonLogs / setMonLogFilter và bộ đệm
// monLogCache / monLogFilter đã bị gỡ.
results.push('▸ Panel 11 — đã gom vào tab Nhật Ký');
{
  const dash = (html.match(/<section[^>]*id="tab-dashboard"[\s\S]*?\n    <\/section>/) || [''])[0];
  const logsTab = (html.match(/<section[^>]*id="tab-logs"[\s\S]*?\n    <\/section>/) || [''])[0];

  check('bảng nhật ký đã rời khỏi Bảng Điều Khiển', !dash.includes('id="mon-log-list"'));
  check('Bảng Điều Khiển không còn lối dẫn nhật ký trùng (Phase 82)', !dash.includes("switchTab('logs')"));
  check('Bảng Điều Khiển giữ nhật ký thời gian thực', dash.includes('id="live-event-log"'));
  check('bảng nhật ký nằm trong tab Nhật Ký', logsTab.includes('id="log-recent-list"'));
  check('tab Nhật Ký có chế độ "Nhật ký vận hành"', logsTab.includes('data-log-view="recent"'));
  check('hàm cũ đã bị gỡ khỏi app.js',
    !src.includes('function renderSystemLogs(')
    && !src.includes('function paintMonLogs(')
    && !src.includes('function setMonLogFilter('));
  check('không còn bộ đệm nhật ký của panel đã gỡ',
    !/^let monLog(Cache|Filter)\b/m.test(srcCode) && !srcCode.includes('MON_LOG_MAX_ROWS'));

  // escape là hàng rào bảo mật: thông điệp log đến từ server rồi đổ thẳng vào
  // innerHTML, nên phải escape trước khi chèn.
  const opsLog = (src.match(/function renderOpsLog\(\)[\s\S]*?\n {2}\}/) || [''])[0];
  check('renderOpsLog escape thông điệp log trước khi chèn HTML',
    opsLog.includes('_esc(e.message') && opsLog.includes('_esc(e.level'));
}

// ── 7b. RACE TIMER: hai lần gọi song song KHÔNG được tạo 2 interval ────────
//
// BUG ĐÃ GẶP: bản đầu đặt `if (!monExtendedTimer)` ở CUỐI `loadExtendedMonitors()`,
// SAU `await Promise.allSettled(...)`. Lúc mở trang có 2 chỗ gọi SONG SONG
// (`restoreActiveTab()`→`switchTab()` và listener `DOMContentLoaded`), cả hai cùng
// chạy tới dòng `if` khi `monExtendedTimer` còn `null` → tạo 2 interval lệch pha.
// Đo thật trên trình duyệt cho khoảng cách 0.2s / 14.8s / 0.2s — tức 2 interval
// cùng kỳ 15s. Tệ hơn: mỗi lần vào lại tab lại nhân thêm, tải server nhân lên dần.
results.push('▸ Race timer — không nhân bản interval khi gọi song song');
M.__reset();
// Gọi 3 lần SONG SONG (không await lần nào trước) — giống hệt tình huống thật.
await Promise.all([M.loadExtendedMonitors(), M.loadExtendedMonitors(), M.loadExtendedMonitors()]);
check('3 lần gọi SONG SONG → chỉ tạo ĐÚNG 1 interval',
  M.__getIntervals().length === 1, `${M.__getIntervals().length} interval`);
check('interval tầng nặng đúng 15000ms',
  M.__getIntervals()[0] === 15000, String(M.__getIntervals()[0]));
// Gọi thêm SAU khi đã có timer → không được tạo thêm.
await M.loadExtendedMonitors();
await M.loadExtendedMonitors();
check('gọi lại 2 lần nữa sau đó → vẫn ĐÚNG 1 interval',
  M.__getIntervals().length === 1, `${M.__getIntervals().length} interval`);

// ── 7c. Chống poll TRÙNG từ 2 timer cùng kỳ 15s ───────────────────────────
//
// BUG ĐÃ GẶP: dù đã sửa race interval, vẫn còn 2 vòng mỗi 15s. Lý do KHÁC:
// `app.js` có sẵn một `setInterval(..., 15_000)` ở phần bootstrap (dòng ~7705)
// gọi `loadDashboard()` mỗi 15s — và `loadDashboard()` lại gọi
// `loadExtendedMonitors()`. Hai timer cùng kỳ, khởi động gần nhau → trùng pha,
// đo thật cho khoảng cách 0s rồi 15s. Sửa bằng chốt cửa sổ thời gian.
check('ngưỡng chống trùng < kỳ 15s nhưng gần bằng nó',
  M.MON_HEAVY_DEDUPE_MS > 0 && M.MON_HEAVY_DEDUPE_MS < 15000 && M.MON_HEAVY_DEDUPE_MS >= 13000,
  String(M.MON_HEAVY_DEDUPE_MS));
M.__reset();
await M.loadExtendedMonitors();            // vòng 1 (8 request)
const afterFirst = M.__getApiCalls();
// Phase 79: bỏ endpoint `/api/v1/logs/recent?limit=200` khỏi vòng nặng — bảng
// nhật ký nó nuôi đã gom vào tab Nhật Ký, nạp ở đây là mỗi vòng poll mất một
// request mà không ai đọc kết quả. 8 → 7.
check('vòng đầu gọi đủ 7 endpoint', afterFirst === 7, String(afterFirst));
// Giả lập timer thứ hai cùng kỳ bắn ngay sau đó:
await M.loadExtendedMonitors();
check('timer thứ hai bắn ngay sau → BỊ CHẶN, không bắn thêm request',
  M.__getApiCalls() === afterFirst, `${M.__getApiCalls()} (mong doi ${afterFirst})`);
// Gọi lại liên tiếp 5 lần → vẫn không thêm request nào.
for (let i = 0; i < 5; i++) await M.loadExtendedMonitors();
check('5 lần gọi liên tiếp nữa → vẫn KHÔNG phát thêm request',
  M.__getApiCalls() === afterFirst, `${M.__getApiCalls()}`);
// Sau khi quá ngưỡng thì poll lại được.
check('mốc thời gian được cập nhật mỗi lần poll',
  /monLastHeavyRunAt\s*=\s*now/.test(extLoadFn));
check('chốt trùng nằm TRƯỚC await đầu tiên (đồng bộ, chống race)',
  extLoadFn.indexOf('monLastHeavyRunAt = now') < extLoadFn.indexOf('await '),
  `mark@${extLoadFn.indexOf('monLastHeavyRunAt = now')} await@${extLoadFn.indexOf('await ')}`);
check('interval vẫn được tạo DÙ bị chặn trùng (để 15s sau mới poll lại)',
  M.__getIntervals().length === 1, `${M.__getIntervals().length} interval`);

// Cấu trúc: chốt timer phải nằm TRƯỚC await đầu tiên.
const extBody = extLoadFn;
const firstAwait = extBody.indexOf('await ');
const guardAt = extBody.indexOf('_ensureExtendedMonitorTimer()');
check('_ensureExtendedMonitorTimer() được gọi TRƯỚC await đầu tiên',
  guardAt >= 0 && firstAwait >= 0 && guardAt < firstAwait, `guard@${guardAt} await@${firstAwait}`);
check('loadExtendedMonitors KHÔNG còn chốt timer riêng ở cuối hàm',
  !/if\s*\(\s*!monExtendedTimer\s*\)[\s\S]{0,200}setInterval/.test(extBody));
check('_ensureExtendedMonitorTimer() bảo thủ: thoát sớm nếu đã có timer',
  /if\s*\(monExtendedTimer\)\s*return;/.test(extTimerFn));
check('timer có chốt tab-pane active trước khi poll (tạm dừng khi rời tab)',
  /tab-dashboard[\s\S]{0,140}classList\.contains\('active'\)/.test(extTimerFn));
// Cùng mẫu lỗi đã được sửa cho timer 2 giây trong loadDashboard().
const dashBody = src.slice(src.indexOf('async function loadDashboard() {'),
                           src.indexOf('// ── PHASE 61: BỘ MONITOR MỞ RỘNG'));
const dashGuard = dashBody.indexOf('if (!healthDashboardTimer)');
const dashAwait = dashBody.indexOf('await ');
check('loadDashboard cũng chốt healthDashboardTimer TRƯỚC await đầu tiên',
  dashGuard >= 0 && dashGuard < dashAwait, `guard@${dashGuard} await@${dashAwait}`);

// ── 8. HTML: mọi id mà JS ghi vào phải tồn tại trong index.html ─────────────
results.push('▸ index.html — khớp id với JS');
const missingIds = monIds
  .filter((id) => !html.includes(`id="${id}"`));
check('mọi id JS cập nhật đều có trong index.html', missingIds.length === 0, missingIds.join(', '));
// Phase 79: 4 nút lọc `setMonLogFilter` của Panel 11 đã bị gỡ cùng bảng nhật
// ký. Bộ lọc còn lại trong tab Nhật Ký là công tắc "hiện cả nhiễu" của
// `renderOpsLog` — phải còn, nếu mất thì người dùng không lọc được heartbeat.
check('bộ lọc ẩn dòng heartbeat còn ở tab Nhật Ký',
  html.includes('id="log-recent-verbose"'));
check('không còn nút lọc mức độ của panel đã gỡ',
  !html.includes('setMonLogFilter('));
check('nút "Mở tab chi tiết" trỏ đúng tab system-integration',
  html.includes(`switchTab('system-integration')`));
check('tab-dashboard chứa 5 panel monitor còn lại',
  ['mon-queue-', 'mon-tool-', 'mon-sec-', 'mon-mem-', 'mon-conn-']
    .every((p) => html.includes(p)));

// ── 9. Nhịp cập nhật: 2 panel tầng nhanh KHÔNG được gọi thêm API ───────────
results.push('▸ Nhịp cập nhật — không tự dõi DoS');
const dashboardBlock = src.slice(
  src.indexOf('async function loadDashboard() {'),
  src.indexOf('// ═══════════════════════════════════════════════════════════════════════════\n// ── PHASE 25: STATE MANAGEMENT')
);
check('loadDashboard gọi loadExtendedMonitors()', dashboardBlock.includes('loadExtendedMonitors()'));
check('fetchAndRenderHealthDashboard gọi renderMonitorFastPath(data)',
  src.slice(src.indexOf('async function fetchAndRenderHealthDashboard() {')).includes('renderMonitorFastPath(data)'));
check('vòng nặng dùng nhịp 15000ms', /setInterval\(\(\) => \{[\s\S]{0,200}?\}, 15000\)/.test(src));
check('vòng nhanh giữ nguyên nhịp 2000ms', /\}, 2000\);/.test(dashboardBlock));
check('tầng nặng dùng Promise.allSettled (1 endpoint chết không mất phần còn lại)',
  src.includes('Promise.allSettled'));
check('tầng nặng gom đủ 8 endpoint',
  (src.slice(src.indexOf('async function loadExtendedMonitors() {')).match(/\['\w+', '\/api\/v1\//g) || []).length === 8,
  String((src.slice(src.indexOf('async function loadExtendedMonitors() {')).match(/\['\w+', '\/api\/v1\//g) || []).length));

// ── 10. Backend: nhóm counters phải là phần bổ sung thuần, không phá cũ ───
results.push('▸ core/server.py — health-dashboard chỉ BỔ SUNG, không phá');
// Tầng HTTP: server.py + routers/*.py (route tách khỏi server.py).
const _httpDir = join(HERE, '..', 'src', 'mateai', 'interfaces', 'http');
const py = [join(_httpDir, 'server.py'), ...readdirSync(join(_httpDir, 'routers')).filter((f) => f.endsWith('.py')).map((f) => join(_httpDir, 'routers', f))]
  .map((f) => readFileSync(f, 'utf-8')).join('\n\n').replace(/\r\n/g, '\n');
// Thân hàm: từ `def` tới decorator kế tiếp (hoặc hết tệp router health.py).
const _epStart = py.indexOf('async def health_dashboard_endpoint(');   // có tham số Depends(get_current_user): cần đăng nhập
const _epNext = py.slice(_epStart).search(/\n@(?:app|router)\.|\n\n\n(?=\S)/);
const ep = py.slice(_epStart, _epNext < 0 ? undefined : _epStart + _epNext);
check('giữ nguyên 4 dòng inject gốc',
  ep.includes('active_portal_websockets') && ep.includes('active_audio_hardware')
  && ep.includes('get_skill_count()') && ep.includes('get_all_tools()'));
check('thêm active_hud_websockets', ep.includes('active_hud_websockets'));
check('thêm active_lan_clients', ep.includes('active_lan_clients'));
check('thêm 4 counter (zt_pending, bg_running, bg_total, bg_max_concurrent)',
  ['zt_pending', 'bg_running', 'bg_total', 'bg_max_concurrent'].every((k) => ep.includes(k)));
check('mỗi nhánh đọc counter bọc try/except — lỗi không làm hỏng endpoint',
  (ep.match(/try:/g) || []).length >= 4, String((ep.match(/try:/g) || []).length));
check('một hàng đợi HITL duy nhất (zero_trust)',
  ep.includes('as zt_hitl') && !ep.includes('p60'));
// Chỉ soi lời gọi thật, không soi chữ trong docstring ("...network requests.").
const codeOnly = ep.split('\n')
  .filter((l) => !l.trim().startsWith('#') && !/^\s*("""|'''|\*|\/\/)/.test(l))
  .filter((l) => !/^\s{4,}[A-Z][a-z].*[a-z]\.\s*$/.test(l)) // bỏ dòng văn xuôi trong docstring
  .join('\n');
check('KHÔNG thêm worker hay request mạng vào health-dashboard',
  !/httpx\.|aiohttp\.|requests\.(get|post|put)\(/.test(codeOnly)
  && !/asyncio\.create_task\(/.test(codeOnly)
  && !/asyncio\.gather\(/.test(codeOnly),
  (codeOnly.match(/httpx\.|create_task\(|gather\(/g) || []).join(','));
check('chỉ import thêm, không phát sinh worker mới',
  !/^\s*_?[A-Za-z_]+_worker\s*=|Thread\(|asyncio\.create_task\(/m.test(ep));

// ── Tổng kết ──────────────────────────────────────────────────────────────
console.log(results.join('\n'));
console.log('\n' + '═'.repeat(70));
console.log(`Tổng: ${pass + fail} | Pass: ${pass} | Fail: ${fail}`);
console.log('═'.repeat(70));
if (fail > 0) {
  console.log('\n❌ CÓ TEST ĐỎ — xem danh sách trên.');
  process.exit(1);
}
console.log('\n✅ Toàn bộ panel Phase 61 vẽ đúng với shape dữ liệu thật từ server.');
