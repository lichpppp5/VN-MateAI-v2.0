// SPDX-License-Identifier: Apache-2.0
// Copyright (c) 2026 Dương Thanh Lịch — VN-MateAI. See LICENSE and NOTICE.
// Kiểm thử hồi quy cho các hàm vẽ giao diện của khối Phase 59/60.
//
// BUG ĐÃ GẶP: `_esc` trước đây chỉ tồn tại bên trong hai IIFE (`LogViewer`
// và `CommandCenter`). IIFE không tự lộ ra `window`, nên 23 chỗ gọi `_esc(...)`
// trong khối Phase 59/60 ở cấp module đều ném
// `ReferenceError: _esc is not defined` — và vì nhánh `catch` của chính các
// hàm đó cũng gọi `_esc`, lỗi gốc bị che, người dùng chỉ thấy khung
// "Đang tải…" đứng yên.
//
// VÌ SAO TEST CŨ KHÔNG BẮT ĐƯỢC: `test_command_center_phase5960.mjs` tự
// định nghĩa một `_esc` riêng trong khung test của nó. Test xanh, trang chết.
//
// Test này cố ý KHÔNG định nghĩa `_esc`. Nó cắt chính bản `_esc` ở cấp
// module ra từ app.js — nên nếu ai đó xoá dòng đó, test đỏ ngay, đúng như
// trang thật sẽ chết.

import { readFileSync, writeFileSync, mkdtempSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join, dirname } from 'node:path';
import { fileURLToPath, pathToFileURL } from 'node:url';

const HERE = dirname(fileURLToPath(import.meta.url));
const src = readFileSync(join(HERE, '..', 'web', 'app.js'), 'utf-8').replace(/\r\n/g, '\n');

let pass = 0, fail = 0;
const results = [];
function check(name, cond, extra = '') {
  if (cond) { pass++; results.push(`  ✅ ${name}`); }
  else { fail++; results.push(`  ❌ ${name}${extra ? ' — ' + extra : ''}`); }
}

// Cắt từ `from` tới hết khối khai báo (thẻ `}` đóng ở cột 0).
function cutBlock(startMark) {
  const a = src.indexOf(startMark);
  if (a < 0) throw new Error('không tìm thấy: ' + startMark);
  const b = src.indexOf('\n}\n', a);
  if (b < 0) throw new Error('không tìm thấy điểm kết thúc: ' + startMark);
  return src.slice(a, b + 3);
}

// ── Phần trích từ app.js ──────────────────────────────────────────────────
results.push('▸ Khai báo');
check('app.js CÓ bản `_esc` ở cấp module (cột 0)', /^function _esc\(s\) \{/m.test(src));
check('CommandCenter KHÔNG còn tự định nghĩa `_esc` (tránh 2 nguồn lệch nhau)',
  !/\n  function _esc\(s\) \{[\s\S]{0,220}?\n  \}/.test(src.slice(src.indexOf('const CommandCenter = (() => {'))));

// Nếu thiếu bất kỳ khối nào thì dừng luôn — chạy tiếp chỉ ra lỗi "không tìm
// thấy" giả lừa, che mất nguyên nhân thật là cấu trúc app.js đã đổi.
let escFn, cards, plugin, bgTask, webhook, toolRun, toolBox, reason, frame, preview;
try {
  escFn   = cutBlock('\nfunction _esc(s) {');
  cards   = cutBlock('\nfunction _ccWebhookCard(a) {');
  plugin  = cutBlock('\nasync function loadPluginRegistryStats() {');
  bgTask  = cutBlock('\nasync function loadBackgroundTasks() {');
  webhook = cutBlock('\nasync function loadWebhookAlerts() {');
  toolRun = cutBlock('\nasync function runIntegrationTool(toolName, args) {');
  toolBox = cutBlock('\nfunction _ccToolBox(html) {');
  reason  = cutBlock('\nfunction _ccToolFailureReason(d, inner) {');
  frame   = cutBlock('\nfunction _ccToolFrame(mark, markCls, toolName, latencyMs, bodyHtml) {');
  preview = cutBlock('\nfunction _ccToolPreview(data) {');
} catch (e) {
  check('cắt được các khối hàm cần test từ app.js', false, e.message);
  console.log(results.join('\n'));
  console.log(`\nTổng: ${pass + fail} | Pass: ${pass} | Fail: ${fail}`);
  console.log('\n❌ KHÔNG THỂ TRÍCH ĐƯỢC HÀM TỪ app.js — cấu trúc đã đổi, cần cập nhật test.');
  process.exit(1);
}
check('cắt được các khối hàm cần test từ app.js', true);
check('`_esc` ở cấp module escape đủ cả dấu nháy đơn và kép',
  escFn.includes('&#39;') && escFn.includes('&quot;'));

// ── Khung chạy thật: KHÔNG định nghĩa `_esc` ──────────────────────────────
// `store` đóng vai trò DOM: mỗi id là một ô có `innerHTML`/`textContent`.
const harness = `
const API_BASE = '';
let __kpi = 0;
function _ccGet(id) { return __dom[id] || null; }
function _escProbeMissing() { /* để ngỏ */ }
const __dom = {
  'cc-plugin-stats':   { innerHTML: '' },
  'cc-plugin-total':   { textContent: '' },
  'cc-bg-task-list':   { innerHTML: '' },
  'cc-bg-task-count':  { textContent: '' },
  'cc-webhook-list':   { innerHTML: '' },
  'cc-webhook-count':  { textContent: '' },
  'cc-kpi-plugins':    { textContent: '' },
  'cc-kpi-bg-tasks':   { textContent: '' },
  'cc-kpi-webhook-alerts': { textContent: '' },
  'cc-tool-output':    { innerHTML: '' },
};
function syncIntegrationKpi() { __kpi += 1; }
function showToast() {}
function getAuthToken() { return 't'; }
function __setBox(id, html) { __dom[id].innerHTML = html; }
function __read(id) { return __dom[id].innerHTML || __dom[id].textContent || ''; }
// Route API theo URL để mỗi hàm gặp đúng dữ liệu như thật.
function apiFetch(url) {
  const u = String(url);
  let body = {};
  if (u.includes('plugin-registry/stats')) {
    body = { status: 'success', total_tools: 11, enabled_tools: 9, tools: {
      check_aws_cost:    { description: 'Chi phí <AWS>', enabled: true,  risk_level: 1, is_async: true },
      send_invoice_email:{ description: 'Gửi hoá đơn',  enabled: false, risk_level: 4, is_async: true },
    }, execution_stats: { check_aws_cost: { total_calls: 7, failed_calls: 1, circuit_breaker: { state: 'closed' } } } };
  } else if (u.includes('background-tasks')) {
    body = { status: 'success', tasks: [
      { name: 'rag_ingest <file.pdf>', status: 'running', progress: 0.42, progress_message: 'Đang đọc &amp; tách trang' },
      { name: 'sync_aws_billing',      status: 'failed',  progress: 0.9,  error: 'HTTP 403 & thiếu quyền' },
    ] };
  } else if (u.includes('webhooks/recent')) {
    body = { status: 'success', alerts: [
      { source: 'webhook:aws', severity: 'critical', title: '<img src=x onerror=alert(1)>',
        message: 'Máy chủ "i-0abc" đã dừng', resource_id: 'i-0abc', timestamp: null,
        metadata: { resource_id: 'i-0abc' } },
    ] };
  } else if (u.includes('skills/execute')) {
    body = { success: true, latency_ms: 128, data: { total_cost: 12.5, note: 'a < b & c' } };
  }
  return Promise.resolve({ ok: true, status: 200, json: async () => body });
}
`;

const SEV = src.slice(
  src.indexOf('const CC_WEBHOOK_SEVERITY = {'),
  src.indexOf('const CC_WEBHOOK_SEVERITY = {') + src.slice(src.indexOf('const CC_WEBHOOK_SEVERITY = {')).indexOf('\n};\n') + 4,
);

const mod = [
  escFn, harness, SEV, cards, plugin, bgTask, webhook, toolBox,
  reason, frame, preview, toolRun,
  'export { loadPluginRegistryStats, loadBackgroundTasks, loadWebhookAlerts,',
  '         runIntegrationTool, _ccToolFailureReason, _ccToolPreview,',
  '         __read, __dom, __kpi };',
].join('\n');

const dir = mkdtempSync(join(tmpdir(), 'cc-esc-'));
const f = join(dir, 'm.mjs');
writeFileSync(f, mod);

const M = await import(pathToFileURL(f).href);

// ── Chạy thật từng hàm ────────────────────────────────────────────────────
results.push('▸ Hàm vẽ giao diện không được ném ReferenceError');

async function run(label, fn, after) {
  try { await fn(); check(label, true); }
  catch (e) { check(label, false, `${e.constructor.name}: ${e.message}`); }
  after?.();
}

await run('loadPluginRegistryStats() vẽ được danh sách tool', M.loadPluginRegistryStats, () => {
  const html = M.__read('cc-plugin-stats');
  check('  danh sách tool có nội dung (không kẹt "Đang tải…")', !html.includes('Đang tải'), html.slice(0, 60));
  check('  hiện tổng 11 tool đã đăng ký', /11/.test(html));
  check('  escape tên tool chứa ký tự HTML', html.includes('&lt;') || !html.includes('<AWS>'));
});

await run('loadBackgroundTasks() vẽ được tác vụ nền', M.loadBackgroundTasks, () => {
  const html = M.__read('cc-bg-task-list');
  check('  có cả tác vụ đang chạy lẫn tác vụ lỗi', html.includes('rag_ingest') && html.includes('sync_aws_billing'), html.slice(0, 60));
  check('  escape tên tác vụ chứa "<file.pdf>"', html.includes('&lt;file.pdf&gt;'));
  check('  escape thông báo lỗi có ký tự "&"', html.includes('HTTP 403 &amp;'));
  check('  huy hiệu đếm đúng 2 tác vụ', M.__read('cc-bg-task-count') === '2', M.__read('cc-bg-task-count'));
});

await run('loadWebhookAlerts() vẽ được thẻ cảnh báo', M.loadWebhookAlerts, () => {
  const html = M.__read('cc-webhook-list');
  check('  thẻ cảnh báo đã render', html.includes('webhook') || html.includes('Nghịêm trọng'), html.slice(0, 60));
  check('  escape tiêu đề chứa HTML (chống chèn)', !html.includes('<img src=x') && html.includes('&lt;img'));
  check('  escape dấu nháy kép trong message', !html.includes('"i-0abc"') || html.includes('&quot;'));
  check('  huy hiệu đếm đúng 1 cảnh báo', M.__read('cc-webhook-count') === '1', M.__read('cc-webhook-count'));
});

await run('runIntegrationTool() chạy và hiện kết quả', () => M.runIntegrationTool('check_aws_cost', { days: 7 }), () => {
  const html = M.__read('cc-tool-output');
  check('  khung kết quả không còn "Đang gọi"', !html.includes('Đang gọi'), html.slice(0, 60));
  check('  hiện tên tool + độ trễ', html.includes('check_aws_cost') && html.includes('128ms'), html.slice(0, 80));
  check('  escape JSON kết quả (a < b & c)', html.includes('a &lt; b &amp; c'), html.slice(0, 120));
});

results.push('▸ Hai tầng `success`: không báo ✔ khi connector đã hỏng');
{
  // Payload thật của `check_aws_cost` khi CHƯA điền credential AWS — lấy
  // nguyên văn từ server. Đây là cái bẫy: `d.success` là `true` vì skill
  // chạy được, nhưng `d.data.success` là `false` vì connector không xác thực
  // được. Code cũ chỉ nhìn `d.success` nên hiện "✔ ... null" — dấu tích xanh
  // cho lệnh đã thất bại, và câu "Not authenticated" bị mất.
  const realFailure = JSON.stringify({
    success: true, error: null, latency_ms: 119, skill_name: 'check_aws_cost',
    data: { success: false, data: null, error: 'Not authenticated', latency_ms: 0, source: 'aws' },
  });

  // (a) Tầng trong báo hỏng → phải hiện ✖ kèm lỗi cụ thể.
  const dirA = mkdtempSync(join(tmpdir(), 'cc-esc-nested-'));
  const fA = join(dirA, 'm.mjs');
  writeFileSync(fA, mod.replace(
    "return Promise.resolve({ ok: true, status: 200, json: async () => body });",
    `return Promise.resolve({ ok: true, status: 200, json: async () => (${realFailure}) });`,
  ));
  const A = await import(pathToFileURL(fA).href);
  await A.runIntegrationTool('check_aws_cost', { days: 7 });
  const aHtml = A.__read('cc-tool-output');
  check('tầng trong success:false → hiện ✖, KHÔNG hiện ✔',
    aHtml.includes('✖') && !aHtml.includes('✔'), aHtml.slice(0, 90));
  check('  hiện đúng lỗi "Not authenticated" thay vì bỏ mất', aHtml.includes('Not authenticated'), aHtml.slice(0, 120));
  check('  KHÔNG hiện chữ "null" (dữ liệu rỗng bị báo như kết quả)', !aHtml.includes('null'), aHtml.slice(0, 120));

  // (b) Tầng ngoài báo hỏng → vẫn phải hiện ✖ (không regress).
  const dirB = mkdtempSync(join(tmpdir(), 'cc-esc-outer-'));
  const fB = join(dirB, 'm.mjs');
  writeFileSync(fB, mod.replace(
    "return Promise.resolve({ ok: true, status: 200, json: async () => body });",
    `return Promise.resolve({ ok: true, status: 200, json: async () => ({ success: false, error: 'Tool không tồn tại' }) });`,
  ));
  const B = await import(pathToFileURL(fB).href);
  await B.runIntegrationTool('khong_ton_tai', {});
  const bHtml = B.__read('cc-tool-output');
  check('tầng ngoài success:false → hiện ✖ "Tool không tồn tại"',
    bHtml.includes('✖') && bHtml.includes('Tool không tồn tại'), bHtml.slice(0, 90));

  // (c) Tool thật sự chạy được → vẫn hiện ✔ (không biến lỗi thành luôn luôn ✖).
  const okHtml = M.__read('cc-tool-output');
  check('tool chạy được vẫn hiện ✔ (không quá tay)', okHtml.includes('✔'), okHtml.slice(0, 60));

  // (d) Skill chạy được nhưng không trả dữ liệu: `d.data` là null và không có
  //     `d.data.success` để soi. Code cũ in ra chữ "null" — người dùng tưởng
  //     đó là dữ liệu thật. Phải nói thẳng là không có dữ liệu.
  const dirD = mkdtempSync(join(tmpdir(), 'cc-esc-null-'));
  const fD = join(dirD, 'm.mjs');
  writeFileSync(fD, mod.replace(
    "return Promise.resolve({ ok: true, status: 200, json: async () => body });",
    "return Promise.resolve({ ok: true, status: 200, json: async () => ({ success: true, data: null, latency_ms: 5 }) });",
  ));
  const D = await import(pathToFileURL(fD).href);
  await D.runIntegrationTool('rỗng', {});
  const dHtml = D.__read('cc-tool-output');
  check('data=null mà không có lỗi → nói "không có dữ liệu", KHÔNG in chữ "null"',
    dHtml.includes('không có dữ liệu') && !dHtml.includes('>null<'), dHtml.slice(0, 120));

  // (e) `check_connector_health` GỘP 4 connector: `d.data.success` là false
  //     nhưng KHÔNG có `error` chung — lỗi nằm ở từng connector. Payload lấy
  //     nguyên văn từ server khi chưa điền credential.
  const health = JSON.stringify({
    success: true, error: null, latency_ms: 56, skill_name: 'check_connector_health',
    data: {
      success: false, overall_healthy: false, timestamp: '2026-09-28T04:32:31',
      connectors: {
        aws:       { success: false, data: null, error: 'Authentication failed', latency_ms: 0 },
        oci:       { success: false, data: null, error: 'Authentication failed', latency_ms: 0 },
        paperless: { success: false, data: null, error: 'Not authenticated',  latency_ms: 0 },
        einvoice:  { success: false, data: null, error: 'Not authenticated',  latency_ms: 0 },
      },
    },
  });
  const dirE = mkdtempSync(join(tmpdir(), 'cc-esc-health-'));
  const fE = join(dirE, 'm.mjs');
  writeFileSync(fE, mod.replace(
    "return Promise.resolve({ ok: true, status: 200, json: async () => body });",
    `return Promise.resolve({ ok: true, status: 200, json: async () => (${health}) });`,
  ));
  const E2 = await import(pathToFileURL(fE).href);
  await E2.runIntegrationTool('check_connector_health', { connectors: ['aws'] });
  const eHtml = E2.__read('cc-tool-output');
  check('health check hỏng → hiện ✖', eHtml.includes('✖') && !eHtml.includes('✔'), eHtml.slice(0, 90));
  check('  gom được lỗi của TỪNG connector, không chỉ "không rõ lý do"',
    eHtml.includes('aws: Authentication failed') && eHtml.includes('paperless: Not authenticated'),
    eHtml.slice(0, 200));
  check('  KHÔNG rơi về câu "Thất bại không rõ lý do"',
    !eHtml.includes('không rõ lý do'), eHtml.slice(0, 200));
  check('  vẫn hiện phần chi tiết JSON để thấy đủ 4 connector', eHtml.includes('einvoice'), eHtml.slice(0, 260));

  // Gọi trực tiếp hàm gom lý do cho các hình dạng payload.
  const cases = [
    [{ success: false, data: { success: false, data: null, error: 'Not authenticated' } }, 'Not authenticated'],
    [{ success: false, error: 'Tool không tồn tại' }, 'Tool không tồn tại'],
    [{ success: true, data: { success: false, connectors: { aws: { success: false, error: 'X' } } } }, 'aws: X'],
    // Không suy ra lý do từ dữ liệu rỗng — phải nói thẳng là không rõ.
    [{ success: true, data: { success: false, connectors: { aws: { success: true } } } }, 'không rõ lý do'],
    [{ success: true, data: null }, 'không rõ lý do'],
    [{ success: true }, 'không rõ lý do'],
  ];
  for (const [d, want] of cases) {
    const got = E2._ccToolFailureReason(d, (d.data && typeof d.data === 'object') ? d.data : null);
    check(`_ccToolFailureReason(${JSON.stringify(d).slice(0, 46)}…) → có "${want}"`, got.includes(want), `thực tế: "${got}"`);
  }
  // Ngược lại: KHÔNG được bịa lý do từ payload rỗng.
  check('_ccToolFailureReason KHÔNG bịa lý do khi không có thông tin',
    E2._ccToolFailureReason({ success: true, data: { success: false } }, { success: false })
      === 'Thất bại không rõ lý do');
}

results.push('▸ Nhánh lỗi cũng phải hiện được, không để kẹt');
{
  // Ghi đè apiFetch để mọi endpoint trả lỗi → ép nhánh `catch` chạy.
  M.__dom['cc-plugin-stats'].innerHTML = '';
  const dir2 = mkdtempSync(join(tmpdir(), 'cc-esc-err-'));
  const f2 = join(dir2, 'm.mjs');
  writeFileSync(f2, mod.replace(
    "return Promise.resolve({ ok: true, status: 200, json: async () => body });",
    "return Promise.resolve({ ok: true, status: 200, json: async () => ({ status: 'error', error: 'Máy chủ <hỏng> & treo' }) });",
  ));
  const E = await import(pathToFileURL(f2).href);
  for (const [name, fn, box] of [
    ['loadPluginRegistryStats()', E.loadPluginRegistryStats, 'cc-plugin-stats'],
    ['loadBackgroundTasks()',    E.loadBackgroundTasks,    'cc-bg-task-list'],
    ['loadWebhookAlerts()',      E.loadWebhookAlerts,      'cc-webhook-list'],
  ]) {
    try { await fn(); } catch (err) { check(name + ' nhánh lỗi không ném', false, err.message); }
    const html = E.__read(box);
    check(`${name} nhánh lỗi hiện thông báo (không kẹt "Đang tải…")`,
      html.includes('Máy chủ') && !html.includes('Đang tải'), html.slice(0, 70));
    check(`${name} nhánh lỗi escape thông báo`,
      html.includes('&lt;hỏng&gt;') && html.includes('&amp;'), html.slice(0, 90));
  }
}

console.log('=== Phase 59/60 — hàm vẽ giao diện dùng `_esc` ===');
console.log(results.join('\n'));
console.log(`\nTổng: ${pass + fail} | Pass: ${pass} | Fail: ${fail}`);
if (fail > 0) process.exit(1);
console.log('\n✅ TẤT CẢ PASS');
