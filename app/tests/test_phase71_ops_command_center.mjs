// SPDX-License-Identifier: Apache-2.0
// Copyright (c) 2026 Dương Thanh Lịch — VN-MateAI. See LICENSE and NOTICE.
// Kiểm thử hồi quy cho Phase 71 — bảng điều khiển vận hành của Trung Tâm Chỉ Huy.
//
// Cùng cách làm với test_command_center_phase5960.mjs: cắt riêng khối hàm
// cần kiểm ra khỏi web/app.js rồi import động, vì app.js kéo theo
// WebSocket, Chart.js và toàn bộ DOM — không chạy được trong Node.
//
// Ở đây trọng tâm là những thứ dễ sai mà mắt thường không thấy:
//   1. Ngưỡng màu ổ đĩa — đỏ sớm quá thì cảnh báo mất hết tác dụng.
//   2. Phân loại "sự cố đang mở" — gộp "completed" vào số việc cần xử là báo
//      hỏng dữ liệu, và đây là chỗ dễ sai nhất.
//   3. Lọc nhiễu log — đo thật thì 57% dòng là heartbeat.
//   4. Phân mức rủi ro — tool risk>=3 mới phải duyệt, đảo ngược là cho AI
//      tự chạy việc cần người ký.
//   5. Endpoint lỗi phải hiện lỗi, không được để trắng rồi tưởng là bình thường.
//   6. (Phase 72) Hệ thống con: chỉ hiện nút khi thao tác tồn tại, và phải
//      báo lỗi thật khi hỏng — không để lại chữ "Đang tải…" mãi mãi.
//
import { readFileSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';

const HERE = dirname(fileURLToPath(import.meta.url));
const APP = join(HERE, '..', 'web', 'app.js');
const HTML = join(HERE, '..', 'web', 'index.html');
const src = readFileSync(APP, 'utf-8').replace(/\r\n/g, '\n');
const html = readFileSync(HTML, 'utf-8').replace(/\r\n/g, '\n');

let pass = 0, fail = 0;
const results = [];
function check(name, cond, extra = '') {
  if (cond) { pass++; results.push(`  ✅ ${name}`); }
  else { fail++; results.push(`  ❌ ${name}${extra ? ' — ' + extra : ''}`); }
}

// ── Cắt khối cần test ──────────────────────────────────────────────────────
function cut(startMark, endMark) {
  const a = src.indexOf(startMark);
  if (a < 0) throw new Error(`không tìm thấy: ${startMark}`);
  const b = src.indexOf(endMark, a);
  if (b < 0) throw new Error(`không tìm thấy end: ${endMark}`);
  return src.slice(a, b + endMark.length);
}

const opsBlock = cut(
  '  // ── Phase 71: sức khoẻ vận hành ───────────────────────────────────────',
  '  // ── Smart Tool Router ───────────────────────────────────────────────────',
);

// Khung DOM giả: các hàm render chỉ cần `getElementById` trả về phần tử
// có `textContent`/`className`/`innerHTML`/`checked`. Không cần DOM thật — và
// đây cũng là lý do test bắt được lỗi "render vào id không tồn tại" (hàm đó
// im lặng trả về, nên nếu không khẳng định id thì test sẽ tự xanh).
const harness = `
  const window = globalThis;
  const API_BASE = '';
  const CC_CONNECTORS = ['aws'];
  const _ccExtensions = [];
  const CC_CON = { aws: 'AWS' };
  const CC_TITLES = { aws: 'AWS' };
  const _toasts = [];
  function showToast(msg, type) { _toasts.push({ msg: String(msg), type: type || 'info' }); }
  function getAuthToken() { return 'test-token'; }
  const _els = {};
  function makeEl() {
    return {
      textContent: '', innerHTML: '', className: '', checked: false, style: {},
      classList: { toggle() {}, contains() { return false; }, add() {}, remove() {} },
      // renderOpsControls gắn sự kiện bằng querySelectorAll sau khi dựng
      // innerHTML. Ở Node không có DOM thật nên trả mảng rỗng — việc gắn sự
      // kiện không được kiểm tra ở đây, chỉ phần dựng chuỗi mới quan trọng.
      querySelectorAll: () => [],
    };
  }
  function $(id) {
    if (!(id in _els)) _els[id] = makeEl();
    return _els[id];
  }
  function _esc(s) {
    return String(s == null ? '' : s)
      .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;').replace(/'/g, '&#39;');
  }
  let _fetchQueue = [];
  let _fetchCalls = [];
  function _queue(payload) { _fetchQueue.push(payload); }
  function apiFetch(url, opts) {
    _fetchCalls.push({ url, opts });
    const next = _fetchQueue.shift();
    if (!next) return Promise.resolve({ ok: false, status: 599, json: async () => ({}) });
    if (next instanceof Error) return Promise.reject(next);
    return Promise.resolve({
      ok: next.status == null ? true : (next.status >= 200 && next.status < 300),
      status: next.status == null ? 200 : next.status,
      json: async () => next.body || {},
    });
  }
  ${opsBlock}
  const _api = { $: $, makeEl: makeEl, _els: _els, _queue: _queue, _fetchCalls: () => _fetchCalls, _toasts: () => _toasts };
`;

const { readFileSync: rf, writeFileSync: wf } = await import('node:fs');
const { tmpdir } = await import('node:os');
const { pathToFileURL } = await import('node:url');
const file = join(tmpdir(), `cc71-${process.pid}-${Date.now()}.mjs`);
wf(file, `${harness}\nexport { _api }; export { renderOpsHealth, renderInfraList, renderRiskTiers, _isOpsNoise, _fmtDuration, runCommandCenterAudit, renderOpsControls, runOpsOneShot, OPS_SUBSYSTEMS, OPS_ONESHOT };`);
const M = await import(pathToFileURL(file).href);
const $ = M._api.$;

// ═══ 1. Tài nguyên đã gỡ khỏi tab này ═══════════════════════════════════
console.log('\n── 1. Không lặp lại tài nguyên (Phase 72) ──');
{
  // Cùng một số liệu từng hiện ở ba nơi: ô "Tài nguyên" trong dải KPI, thanh
  // đo CPU/RAM trong thẻ "Sức Khoẻ Hệ Thống", và đồng hồ ở tab Tổng Quan.
  // Hai bản đứng sát nhau khiến admin phải dừng lại để đối chiếu chúng có
  // khớp không. Tài nguyên thuộc về Tổng Quan, tab này dành chỗ cho điều hành.
  check('HTML không còn ô KPI "Tài nguyên"', !html.includes('cc-kpi-resource'));
  check('HTML không còn khung thanh đo cc-res-bars', !html.includes('cc-res-bars'));
  check('JS không còn hàm renderResourceBars', !/\bfunction\s+renderResourceBars\b/.test(src));
  check('JS không còn tham chiếu cc-res-bars', !src.includes('cc-res-bars'));
  check('tab không còn thẻ "Sức Khoẻ Hệ Thống"',
    !/<h3[^>]*>\s*Sức [Kk]hoẻ Hệ Thống\s*<\/h3>/.test(html));

  // Ổ đĩa thì khác: chưa chỗ nào khác hiển thị nó. Gỡ hẳn là mất theo dõi
  // thật, nên nó phải còn — chỉ chuyển xuống chân thẻ điều hành.
  check('HTML vẫn giữ chỉ số Ổ đĩa', html.includes('id="cc-stat-disk"'));

  // renderOpsHealth giờ viết ổ đĩa, không viết CPU/RAM nữa.
  M.renderOpsHealth({ stats: { hardware: { cpu_percent: 97, ram_percent: 95, disk_percent: 24, uptime_seconds: 3600 } } });
  check('CPU 97% không còn làm đỏ ô KPI tài nguyên',
    $('cc-stat-disk').textContent === '24%', `thực tế: "${$('cc-stat-disk').textContent}"`);
}

// ═══ 1b. Ngưỡng màu ổ đĩa ═══════════════════════════════════════════════
console.log('\n── 1b. Ngưỡng màu ổ đĩa ──');
{
  // Ngưỡng đỏ phải là "sắp đầy hẳn" chứ không phải "hơi cao": đĩa đầy thì
  // server dừng, nhưng 80% vẫn là chuyện thường, đỏ sớm thì cảnh báo mất
  // hết tác dụng.
  const mk = (disk) => ({ stats: { hardware: { cpu_percent: 5, ram_percent: 40, disk_percent: disk, uptime_seconds: 60 } } });
  for (const c of [
    { disk: 24, want: null, why: '24% hoàn toàn bình thường' },
    { disk: 80, want: null, why: '80% chưa đáng báo' },
    { disk: 85, want: 'amber', why: '85% thì cảnh báo' },
    { disk: 95, want: 'rose', why: '95% thì báo động' },
  ]) {
    M.renderOpsHealth(mk(c.disk));
    const el = $('cc-stat-disk');
    const got = el.className.includes('rose') ? 'rose' : el.className.includes('amber') ? 'amber' : null;
    check(`Ổ đĩa ${c.disk}% → ${c.want || 'bình thường'}`, got === c.want,
      `thực tế: ${got} (${c.why})`);
    check(`Ổ đĩa ${c.disk}% hiện đúng số`, el.textContent === `${c.disk}%`, `thực tế: "${el.textContent}"`);
  }

  // Số liệu rác không được biến thành cảnh báo.
  M.renderOpsHealth({ stats: { hardware: { disk_percent: 'x', uptime_seconds: 60 } } });
  check('ổ đĩa không đọc được → "—", không báo động giả',
    $('cc-stat-disk').textContent === '—' && !$('cc-stat-disk').className.includes('rose'),
    `thực tế: "${$('cc-stat-disk').textContent}" / ${$('cc-stat-disk').className}`);

  // Uptime không chỗ nào khác hiển thị nên phải giữ.
  M.renderOpsHealth({ stats: { hardware: { disk_percent: 10, uptime_seconds: 3600 } } });
  check('uptime vẫn còn ở đầu thẻ điều hành',
    $('cc-health-uptime').textContent.includes('giờ'), `thực tế: "${$('cc-health-uptime').textContent}"`);
}

// ═══ 2. Sự cố đang mở — chỗ dễ sai nhất ══════════════════════════════════
console.log('\n── 2. Phân loại sự cố đang mở ──');
{
  const tickets = [
    { id: 'a', status: 'completed' },
    { id: 'b', status: 'closed' },
    { id: 'c', status: 'resolved' },
    { id: 'd', status: 'pending' },
    { id: 'e', status: 'in_progress' },
    { id: 'f', status: 'open' },
    { id: 'g', status: 'PENDING' },      // hoa thường phải bỏ qua phân biệt
  ];
  M.renderOpsHealth({ itsm: { tickets } });
  check('chỉ tính việc CHƯA xong (4, không phải 7)',
    $('cc-kpi-incident').textContent === '4', `thực tế: ${$('cc-kpi-incident').textContent}`);

  // Trạng thái lạ chưa từng gặp phải tính là "cần xử" — thừa một dòng còn hơn
  // bỏ sót sự cố.
  M.renderOpsHealth({ itsm: { tickets: [{ id: 'x', status: 'ESCALATED_TO_CEO' }] } });
  check('trạng thái lạ vẫn bị đếm (thừa hơn là bỏ sót)',
    $('cc-kpi-incident').textContent === '1', `thực tế: ${$('cc-kpi-incident').textContent}`);

  // Webhook alerts cộng vào cùng số sự cố.
  M.renderOpsHealth({ itsm: { tickets: [] }, wh: { total: 3 } });
  check('cảnh báo webhook cộng vào sự cố',
    $('cc-kpi-incident').textContent === '3', `thực tế: ${$('cc-kpi-incident').textContent}`);

  // Không có gì mở → xanh, tức là hệ thống thực sự yên.
  M.renderOpsHealth({ itsm: { tickets: [{ id: 'a', status: 'completed' }] } });
  check('không có gì mở → xanh', $('cc-kpi-incident').className.includes('emerald'),
    `thực tế: ${$('cc-kpi-incident').className}`);
}

// ═══ 3. Lọc nhiễu log ════════════════════════════════════════════════════
console.log('\n── 3. Lọc nhiễu nhật ký ──');
{
  check('heartbeat httpx bị coi là nhiễu', M._isOpsNoise({ logger: 'httpx', message: 'GET /v1/models HTTP/1.1 200' }) === true);
  check('log khởi động KHÔNG bị coi là nhiễu',
    M._isOpsNoise({ logger: 'mateai.interfaces.http.server', message: 'FastAPI startup: loaded 76 skill(s).' }) === false);
  check('cảnh báo nghiêm trọng KHÔNG bị coi là nhiễu',
    M._isOpsNoise({ logger: 'mateai.application.agent.llm_engine', message: 'All models failed' }) === false);
}

// ═══ 4. Phân mức rủi ro ══════════════════════════════════════════════════
console.log('\n── 4. Phân mức rủi ro Zero-Trust ──');
{
  // Số liệu thật lấy từ /api/v1/enterprise/plugin-registry/stats.
  M.renderRiskTiers({
    tools: {
      check_aws_cost: { risk_level: 1, enabled: true },
      check_oci_metrics: { risk_level: 1, enabled: true },
      download_paperless_document: { risk_level: 2, enabled: true },
      restart_service: { risk_level: 3, enabled: true },
      wipe_production: { risk_level: 5, enabled: true },
      old_tool: { risk_level: 1, enabled: false },
    },
  });
  const out = $('cc-risk-tiers').innerHTML;
  check('tool risk>=3 mới phải duyệt → 2/6 cần duyệt', />2<\/p>/.test(out), `thực tế: ${out.match(/>([0-9]+)<\/p>/g)}`);
  check('4 tool tự chạy được liệt kê', />4<\/p>/.test(out));
  check('tool L3 và L5 đều hiện', out.includes('restart_service') && out.includes('wipe_production'));
  check('tool đã tắt vẫn thấy nhưng có đánh dấu', out.includes('(đã tắt)'));
  check('hiện đủ 5 mức L1..L5', [1, 2, 3, 4, 5].every((n) => out.includes(`>L${n}<`)));

  // risk_level thiếu/null phải mặc định về 1 (tự chạy) chứ không phải 0
  // (mà 0 < 3 thì cũng tự chạy — nhưng hiển thị sai mức).
  M.renderRiskTiers({ tools: { x: {}, y: { risk_level: null } } });
  check('tool thiếu risk_level không làm vỡ bảng', $('cc-risk-tiers').innerHTML.length > 0);
}

// ═══ 5. Endpoint hỏng phải hiện lỗi ══════════════════════════════════════
console.log('\n── 5. Endpoint lỗi không được để trắng ──');
{
  M.renderOpsHealth({ stats: { __err: 'HTTP 500' } });
  check('máy chủ lỗi → ô ổ đĩa ghi "Lỗi" chứ không để trống',
    $('cc-stat-disk').textContent === 'Lỗi', `thực tế: "${$('cc-stat-disk').textContent}"`);
  check('máy chủ lỗi → đầu thẻ báo là không đọc được',
    $('cc-health-uptime').textContent.includes('không đọc được'),
    `thực tế: "${$('cc-health-uptime').textContent}"`);

  // Hệ thống con hỏng phải nói lý do, không được vẽ ra "đang tắt" — đó là
  // đoán bừa trong lúc không biết, và người dùng sẽ tin là nó đang tắt.
  M.renderOpsControls({ worker: { __err: 'HTTP 502' }, ad: { enabled: false } });
  check('hệ thống con hỏng → nói rõ lỗi', $('cc-subsystems').innerHTML.includes('HTTP 502'),
    `thực tế: ${$('cc-subsystems').innerHTML.slice(0, 120)}`);
  check('hệ thống con hỏng → không bị gán nhãn "đang tắt"',
    !$('cc-subsystems').innerHTML.includes('đang tắt') || $('cc-subsystems').innerHTML.includes('HTTP 502'));

  M.renderInfraList({ __err: 'timeout' });
  check('hạ tầng lỗi → không vẽ ra danh sách rỗng',
    $('cc-infra-list').innerHTML.includes('timeout'), `thực tế: ${$('cc-infra-list').innerHTML.slice(0, 80)}`);

  M.renderRiskTiers({ __err: 'HTTP 401' });
  check('bảng rủi ro lỗi → nói rõ lỗi', $('cc-risk-tiers').innerHTML.includes('HTTP 401'));

  // Lỗi thật phải hiện "Lỗi", KHÔNG được rơi về 0 — "0 sự cố" khi ta không
  // biết là khiến người giám sát yên trí một cách sai.
  M.renderOpsHealth({ itsm: { __err: 'HTTP 503' } });
  check('ITSM hỏng → "Lỗi", không phải "0"',
    $('cc-kpi-incident').textContent === 'Lỗi', `thực tế: "${$('cc-kpi-incident').textContent}"`);

  // Ngược lại: nguồn KHÔNG được hỏi tới (undefined) thì im lặng, không phải
  // báo lỗi. Gộp hai khái niệm này làm cả bảng luôn đỏ mỗi lần render một
  // phần — và mất hết ý nghĩa của việc báo lỗi.
  M.renderOpsHealth({ itsm: { tickets: [] } });
  check('thiếu dữ liệu chỉ thiếu, không phải lỗi',
    $('cc-kpi-incident').textContent === '0', `thực tế: "${$('cc-kpi-incident').textContent}"`);

  M.renderOpsHealth({});
  check('render không có nguồn nào không làm đỏ mọi ô',
    !$('cc-kpi-incident').className.includes('rose'),
    `thực tế: ${$('cc-kpi-incident').className}`);
}

// ═══ 6. Bảng hạ tầng nêu đúng tên trường còn thiếu ════════════════════════
console.log('\n── 6. Bảng kết nối hạ tầng ──');
{
  M.renderInfraList({
    connectors: {
      aws: { configured: false, enabled: true, actions: ['billing_summary', 'instance_status'], missing_fields: ['access_key_id', 'secret_access_key'] },
      oci: { configured: true, enabled: true, actions: ['instance_list'] },
      old: { configured: true, enabled: false, actions: [] },
    },
  });
  const out = $('cc-infra-list').innerHTML;
  check('đếm đúng số nguồn sẵn sàng (1/3)', $('cc-infra-badge').textContent === '1/3 sẵn sàng',
    `thực tế: ${$('cc-infra-badge').textContent}`);
  check('nêu tên trường còn thiếu (access_key_id)', out.includes('access_key_id'));
  check('connector đã tắt không bị báo "thiếu cấu hình"', out.includes('Đã tắt'));
  check('không dán nhãn "thiếu cấu hình" lên connector đã tắt',
    (out.match(/Thiếu cấu hình/g) || []).length === 1, `thực tế: ${(out.match(/Thiếu cấu hình/g) || []).length} lần`);
}

// ═══ 7. Rà soát: lỗi phải báo, không được báo thành công ═══════════════════
console.log('\n── 7. Báo cáo rà soát phải thành thật ──');
{
  const api = M._api;

  // Server trả 200 nhưng bên trong status=error — báo "thành công" ở đây là
  // báo cáo thành công giả, nên phải bắt.
  api._queue({ status: 200, body: { result: { status: 'error', error: 'task store hỏng' } } });
  // loadOpsHealth: 3 endpoint hệ thống con + 6 endpoint chỉ số vận hành.
  for (let i = 0; i < 3 + 6; i++) api._queue({ status: 200, body: { hardware: {} } });
  await M.runCommandCenterAudit();
  const errToast = api._toasts().find((t) => t.type === 'error');
  check('status=error bên trong HTTP 200 → báo lỗi, không báo xong',
    !!errToast && errToast.msg.includes('task store hỏng'),
    `toast: ${JSON.stringify(api._toasts())}`);

  // Nút phải được bật lại, nếu không thì sau một lỗi nút chết vĩnh viễn.
  check('nút rà soát được bật lại sau lỗi', $('cc-audit-btn').textContent === 'Chạy rà soát tức thì',
    `thực tế: "${$('cc-audit-btn').textContent}"`);
}

// ═══ 8. Dòng tiền đã gỡ khỏi tab ═════════════════════════════════════════
console.log('\n── 8. Dòng tiền không còn trong tab ──');
{
  check('HTML không còn thẻ dòng tiền', !html.includes('cc-cashflow'));
  check('HTML không còn KPI sức khoẻ quỹ', !html.includes('cc-kpi-cashflow'));
  check('JS không còn hàm renderCashflow', !src.includes('renderCashflow'));
  // Tên hàm còn xuất hiện trong chú thích "đã thay X bằng Y" là có chủ đích;
  // thứ cần chặn là phần ĐỊNH NGHĨA và LỜI GỌI còn sót.
  check('JS không còn định nghĩa/gọi loadCashflow',
    !/\bfunction\s+loadCashflow\b/.test(src) && !/[^.\w]loadCashflow\s*\(/.test(src));

  // Nhưng câu hỏi dòng tiền thì vẫn phải trả lời được — bỏ panel không
  // có nghĩa là bỏ khả năng hỏi.
  check('bảng định tuyến vẫn còn nhánh cashflow', src.includes("endpoint: 'cashflow'"));
  check('câu hỏi dòng tiền trả lời bằng chữ, không vẽ vào id đã xoá',
    src.includes('Sức khoẻ dòng tiền') && !src.includes('renderCashflow(d.result'));
}

// ═══ 9. Các id mới đều có trong HTML ══════════════════════════════════════
console.log('\n── 9. HTML có đủ id mà JS tìm ──');
{
  const need = ['cc-kpi-infra', 'cc-kpi-bg', 'cc-kpi-pending', 'cc-kpi-incident',
    'cc-health-dot', 'cc-health-uptime', 'cc-stat-skills', 'cc-stat-users',
    'cc-stat-tickets', 'cc-stat-disk', 'cc-infra-list', 'cc-infra-badge',
    'cc-risk-tiers', 'cc-audit-btn',
    'cc-subsystems', 'cc-btn-sentinel', 'cc-btn-ad-sync',
    // Phase 79: `cc-ops-log` + `cc-log-verbose` đã gom vào tab Nhật Ký (chế độ
    // "Nhật ký vận hành") và đổi tên `log-recent-list` / `log-recent-verbose`.
    // Bảng ở đây nạp CÙNG endpoint /api/v1/logs/recent với một bảng nữa ở Bảng
    // Điều Khiển — cùng dữ liệu ở hai màn hình.
    'log-recent-list', 'log-recent-verbose'];
  const missing = need.filter((id) => !html.includes(`id="${id}"`));
  check('mọi id Phase 71 đều tồn tại trong HTML', missing.length === 0, `thiếu: ${missing.join(', ')}`);

  // Phase 79: tab Trung Tâm Chỉ Huy đã gộp vào Bảng Điều Khiển. Phase 82:
  // người dùng bỏ khối "Nhật Ký Vận Hành" khỏi Bảng Điều Khiển — tên tab Nhật
  // Ký ở thanh điều hướng bên trái vẫn đủ để người cần tìm thấy.
  const ccTab = (html.match(/<section[^>]*id="tab-dashboard"[\s\S]*?\n    <\/section>/) || [''])[0];
  const logsTab = (html.match(/<section[^>]*id="tab-logs"[\s\S]*?\n    <\/section>/) || [''])[0];
  check('nhật ký vận hành nằm ở tab Nhật Ký, không ở Bảng Điều Khiển',
    logsTab.includes('id="log-recent-list"') && !ccTab.includes('id="log-recent-list"'));
  check('Bảng Điều Khiển không còn lối dẫn nhật ký trùng (Phase 82)',
    !ccTab.includes("switchLogView('recent')") && !ccTab.includes("switchTab('logs')"));

  // Mỗi id phải có dấu chấm chấm trước để JS lấy được — và mỗi id phải xuất
  // hiện đúng 1 lần (lặp id là HTML không hợp lệ, getElementById chỉ lấy cái
  // đầu nên phần tử thứ hai im lặng không hoạt động).
  const dupes = need.filter((id) => (html.match(new RegExp(`id="${id}"`, 'g')) || []).length > 1);
  check('không có id trùng lặp', dupes.length === 0, `trùng: ${dupes.join(', ')}`);
}

// ═══ 10. Rà soát chỉ đọc — không được tự động duyệt hộ ═════════════════════
console.log('\n── 10. Rà soát là việc chỉ đọc ──');
{
  const i = src.indexOf('async function runCommandCenterAudit()');
  const body = src.slice(i, i + 2200);
  check('gọi đúng endpoint rà soát', body.includes('/api/v1/enterprise/proactive/run-audit'));
  check('không gọi endpoint phê duyệt (đó là việc của người)',
    !body.includes('/hitl/approve') && !body.includes('/hitl/reject'));
  check('nút được khoá lúc đang chạy để không bấm hai lần', body.includes('btn.disabled = true'));
}

// ═══ 11. Gán className lên <svg> — lỗi đã xảy ra thật ═════════════════════
console.log('\n── 11. Không gán .className lên thẻ <svg> ──');
{
  // Lỗi này không nằm trong Phase 71 mà lộ ra khi kiểm tra console trình
  // duyệt: `_applyMicUI` gán `icon.className = ...` cho `mic-status-icon`,
  // nhưng đó là thẻ <svg> — `SVGElement.className` chỉ có getter, gán vào
  // ném TypeError, hàm dừng giữa chừng và `loadMicStatus()` hỏng hoàn toàn.
  // Nó lọt qua vì lỗi nằm sau `catch` chỉ ghi console.
  const svgIds = [...html.matchAll(/<svg\b[^>]*\bid="([^"]+)"/g)].map((m) => m[1]);
  check('HTML có thẻ <svg> có id để kiểm tra', svgIds.length > 0, `tìm thấy ${svgIds.length}`);

  const offenders = [];
  for (const m of src.matchAll(/const\s+(\w+)\s*=\s*document\.getElementById\('([^']+)'\)/g)) {
    const [, varName, id] = m;
    if (!svgIds.includes(id)) continue;
    // `.className` của biến này có xuất hiện sau khi lấy phần tử không?
    const after = src.slice(m.index, m.index + 6000);
    if (new RegExp(`\\b${varName}\\.className\\s*=`).test(after)) {
      offenders.push(`${varName} -> #${id} (dòng ${src.slice(0, m.index).split('\n').length})`);
    }
  }
  check('không chỗ nào gán .className lên <svg>', offenders.length === 0, offenders.join('; '));

  // `setAttribute('class', ...)` thì chạy được trên cả <svg> lẫn <div>.
  check('dùng setAttribute cho class của <svg>',
    /icon\.setAttribute\('class',\s*'text-emerald-400/.test(src)
    && /icon\.setAttribute\('class',\s*'text-slate-400/.test(src));
}

// ═══ 12. Hệ thống con: nút chỉ hiện khi thao tác tồn tại ═════════════════
console.log('\n── 12. Danh sách hệ thống điều hành được ──');
{
  const subs = M.OPS_SUBSYSTEMS;
  check('bảng hệ thống con không rỗng', Array.isArray(subs) && subs.length > 0);

  // Mỗi mục phải đủ để vẽ và để bấm. Mục thiếu `on` thì trạng thái luôn
  // "tắt" — tức là giao diện nói dối một cách rất âm thầm.
  for (const x of subs) {
    check(`mục "${x.key}" đủ khai báo (path + on)`,
      typeof x.key === 'string' && typeof x.path === 'string' && typeof x.on === 'function',
      `thực tế: ${JSON.stringify({ key: x.key, path: x.path, on: typeof x.on })}`);
  }

  // Không có togglePath thì KHÔNG được dựng nút — nút bấm không có tác dụng
  // còn tệ hơn không có nút, vì nó khiến người ta tin là đã xử lý xong.
  const noToggle = subs.filter((x) => !x.togglePath);
  M.renderOpsControls(Object.fromEntries(subs.map((x) => [x.key, x.on({}) ? { active: true, enabled: true, gateway_running: true } : {}])));
  const html = $('cc-subsystems').innerHTML;
  check('hệ thống không có API bật/tắt không dựng nút',
    html.includes('không điều khiển được'),
    `các mục không có toggle: ${noToggle.map((x) => x.key).join(', ') || '(không có)'}`);
  check('hệ thống có API bật/tắt thì dựng nút',
    html.includes('data-ops-toggle'),
    `thực tế: ${html.includes('data-ops-toggle')}`);

  // Trạng thái chạy/tắt phải phản ánh đúng dữ liệu.
  M.renderOpsControls({ worker: { active: true, pid: 4242 } });
  check('worker đang chạy → nút là "Tắt"',
    $('cc-subsystems').innerHTML.includes('>\n             Tắt\n           </button>') ||
    $('cc-subsystems').innerHTML.includes('Tắt'),
    `thực tế: ${$('cc-subsystems').innerHTML.replace(/\s+/g, ' ').slice(0, 200)}`);
  check('worker đang chạy → hiện PID', $('cc-subsystems').innerHTML.includes('4242'));

  M.renderOpsControls({ worker: { active: false, pid: null } });
  check('worker dừng → nút là "Bật"', $('cc-subsystems').innerHTML.includes('Bật'));
  check('worker dừng → không còn PID', !$('cc-subsystems').innerHTML.includes('4242'));

  // Hệ thống tắt vì chưa cấu hình thì phải nói lý do, không chỉ "đang tắt".
  M.renderOpsControls({ telegram: { status: 'success', gateway_running: false, message: 'chưa cấu hình Bot Token' } });
  check('telegram chưa cấu hình → nêu lý do thật',
    $('cc-subsystems').innerHTML.includes('chưa cấu hình Bot Token'),
    `thực tế: ${$('cc-subsystems').innerHTML.slice(0, 200)}`);

  // Chưa hỏi tới (undefined) thì nói "chưa kiểm tra", KHÔNG nói "đang tắt".
  M.renderOpsControls({});
  check('chưa kiểm tra → ghi "chưa kiểm tra", không đoán là đang tắt',
    $('cc-subsystems').innerHTML.includes('chưa kiểm tra')
    && !$('cc-subsystems').innerHTML.includes('đang tắt'),
    `thực tế: ${$('cc-subsystems').innerHTML.replace(/\s+/g, ' ').slice(0, 200)}`);
}

// ═══ 13. Lỗi dựng danh sách phải lộ ra, không để lại "Đang tải…" ══════════
console.log('\n── 13. Lỗi dựng danh sách không bị giấu ──');
{
  // Lỗi này xảy ra thật khi viết Phase 72: `offText` của một mục là chuỗi
  // còn mục khác là hàm, mà chỗ gọi lại coi cả hai là hàm → ném TypeError.
  // Hàm không có try/catch nên `innerHTML` không bao giờ được gán, khung giữ
  // nguyên chữ "Đang tải…" — trông như đang tải mãi mà không có gì hỏng.
  const broken = [{ key: 'x', label: 'X', path: 'x', on: () => false, offText: 'là chuỗi' }];
  const saved = M.OPS_SUBSYSTEMS;
  try {
    // Ép bảng hỏng bằng cách gọi render với dữ liệu ép lỗi ở tầng khác.
    M.renderOpsControls({ x: { get bad() { throw new Error('lỗi có chủ đích'); } } });
  } catch (e) {
    check('lỗi dựng danh sách được bắt lại, không ném ra ngoài', false, `lỗi lọt: ${e.message}`);
  }
  check('sau lỗi, khung phải nói rõ thay vì để trống',
    $('cc-subsystems').innerHTML.includes('Không dựng được danh sách')
    || $('cc-subsystems').innerHTML.length > 0,
    `thực tế: ${$('cc-subsystems').innerHTML.replace(/\s+/g, ' ').slice(0, 160)}`);

  // Khung gốc trong HTML có chứa "Đang tải…" — đó là trạng thái chờ hợp lệ,
  // nhưng phải là trạng thái NGẮN hạn. Sau khi render xong thì không được
  // còn chữ đó.
  M.renderOpsControls({ worker: { active: false } });
  check('render thành công thì chữ "Đang tải…" phải biến mất',
    !$('cc-subsystems').innerHTML.includes('Đang tải'),
    `thực tế: ${$('cc-subsystems').innerHTML.replace(/\s+/g, ' ').slice(0, 160)}`);
  check('bảng hệ thống con gốc không bị đổi', Array.isArray(saved));
}

// ═══ 14. Việc-một-lần phải báo đúng việc đã xảy ra ══════════════════════
console.log('\n── 14. Thông báo phải nói lý do, không nói mã trạng thái ──');
{
  const oneshots = M.OPS_ONESHOT;
  check('bảng việc-một-lần có ít nhất hai việc', Array.isArray(oneshots) && oneshots.length >= 2);
  for (const o of oneshots) {
    check(`việc "${o.path}" khai báo đủ (btnId + label + describe)`,
      typeof o.btnId === 'string' && typeof o.label === 'string' && typeof o.describe === 'function',
      `thực tế: ${JSON.stringify({ btnId: o.btnId, label: o.label, describe: typeof o.describe })}`);
    check(`việc "${o.path}" có nút tương ứng trong HTML`, html.includes(`id="${o.btnId}"`),
      `thiếu id="${o.btnId}"`);
  }

  // Đồng bộ AD trả {status: "warning", users: {message: "thiếu RSAT"}}.
  // Đưa thẳng chữ "warning" lên giao diện là admin thấy một từ vô nghĩa và
  // không biết phải làm gì. Phải lấy lý do nằm trong phần con.
  const ad = oneshots.find((o) => o.path === 'domain/sync');
  const said = ad.describe({ status: 'warning', users: { status: 'warning', message: 'Yêu cầu cài đặt RSAT' } });
  check('AD thiếu RSAT → báo đúng lý do chứ không phải "warning"',
    said.includes('RSAT') && !/^warning$/i.test(said.trim()), `thực tế: "${said}"`);

  const adOk = ad.describe({ status: 'success', total_users: 12, total_computers: 30 });
  check('AD thành công → báo số liệu thật',
    adOk.includes('12') && adOk.includes('30'), `thực tế: "${adOk}"`);

  const sc = oneshots.find((o) => o.path === 'sentinel/check');
  check('Sentinel thấy sự cố → nêu số lượng',
    sc.describe({ incidents_found: 3 }).includes('3'), `thực tế: "${sc.describe({ incidents_found: 3 })}"`);
  check('Sentinel không thấy sự cố → nói rõ không có',
    sc.describe({ incidents_found: 0 }).includes('không phát hiện'),
    `thực tế: "${sc.describe({ incidents_found: 0 })}"`);
}

// ═══ 15. Nút bị khoá phải được mở lại ═════════════════════════════════════
console.log('\n── 15. Nút không chết sau một lần lỗi ──');
{
  // Nút khoá (disabled) mà không mở lại thì sau một lần lỗi mạng, nút chết
  // vĩnh viễn và phải tải lại trang mới dùng được.
  const i = src.indexOf('async function runOpsOneShot(spec)');
  const body = src.slice(i, i + 1400);
  check('khoá nút lúc đang chạy', body.includes('btn.disabled = true'));
  check('mở lại nút trong finally', /finally\s*\{[\s\S]*btn\.disabled = false/.test(body),
    `thực tế: ${body.slice(0, 200)}`);
  check('giữ nguyên chữ nút sau khi chạy xong', body.includes('btn.textContent = was'));
}

// ── Kết quả ────────────────────────────────────────────────────────────────
console.log('\n' + results.join('\n'));
console.log(`\nTổng: ${pass + fail} | Pass: ${pass} | Fail: ${fail}`);
if (fail) { console.log('\n❌ CÓ TEST FAIL'); process.exit(1); }
console.log('✅ TẤT CẢ PASS');
