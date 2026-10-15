// SPDX-License-Identifier: Apache-2.0
// Copyright (c) 2026 Dương Thanh Lịch — VN-MateAI. See LICENSE and NOTICE.
/**
 * tests/test_phase75_no_fake_roi.mjs
 * ==================================
 * Kiểm thử Phase 75 — ROI Dashboard không còn dữ liệu bịa.
 *
 * Test này không chỉ grep chuỗi. Nó **chạy thật** các hàm render của
 * web/roi_dashboard.html trên một DOM giả tối thiểu, rồi đọc lại đúng thứ sẽ
 * hiện ra màn hình. Lý do: lỗi ở đây không phải "có chuỗi xấu" mà là "hàm vẫn
 * chạy trơn tru nhưng in ra số không có nguồn". Grep không bắt được kiểu đó.
 *
 * Ba tình huống phải phân biệt rõ, và trước đây bị trộn làm một:
 *
 *   A. Chưa gọi được API      -> "chờ kết nối", KHÔNG được bịa số.
 *   B. Gọi được, hôm nay 0    -> hiện 0. Đây là số đo thật ("0 nhân viên" là
 *                              sự thật đếm được từ DB, không phải thiếu dữ liệu).
 *   C. Gọi được, có số        -> hiện số thật.
 *
 * Trước Phase 75, (A) rơi vào showFallbackDemo(): 12 phiếu ITSM, 48 audit log,
 * 15 nhân viên, 4 phòng ban, cùng một báo cáo vận hành hoàn chỉnh. Không có
 * cách nào phân biệt với dữ liệu thật.
 */

import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const read = (p) => fs.readFileSync(path.join(ROOT, p), 'utf8').replace(/\r\n/g, '\n');

const appJs = read('web/app.js');
// Script của trang đã tách ra web/roi_dashboard.js (CSP chặt) — ghép lại để các kiểm tra bên dưới đọc như một trang.
const roiHtml = read('web/roi_dashboard.html').replace(
  '<script src="/static/roi_dashboard.js?v=1"></script>', `<script>\n${read('web/roi_dashboard.js')}\n</script>`);

let pass = 0;
let fail = 0;
const failures = [];

function check(name, cond, detail = '') {
  if (cond) { pass++; console.log(`  ✅ ${name}`); }
  else { fail++; failures.push(`${name} — ${detail}`); console.log(`  ❌ ${name}  ${detail}`); }
}
function section(title) { console.log(`\n▸ ${title}`); }

/** Mã thuần tuý: bỏ comment, để chú thích giải thích việc đã gỡ không báo động giả. */
function codeOnly(src) {
  return src
    .replace(/\/\*[\s\S]*?\*\//g, '')
    .replace(/^\s*\/\/.*$/gm, '')
    .replace(/\/\/.*$/gm, '');
}
const roiCode = codeOnly(roiHtml);

// ──────────────────────────────────────────────────────────────────────
// DOM giả tối thiểu — đủ để các hàm render thật chạy và ta đọc lại kết quả.
// ──────────────────────────────────────────────────────────────────────
function makeDom() {
  const els = new Map();

  function makeEl(id) {
    const classes = new Set();
    const el = {
      id,
      textContent: '',
      innerHTML: '',
      title: '',
      parentElement: null,
      classList: {
        add: (...c) => c.forEach((x) => classes.add(x)),
        remove: (...c) => c.forEach((x) => classes.delete(x)),
        contains: (c) => classes.has(c),
        toggle: (c, on) => {
          const want = on === undefined ? !classes.has(c) : !!on;
          want ? classes.add(c) : classes.delete(c);
        },
      },
      getContext: () => ({ __ctx: id }),
    };
    return el;
  }

  function byId(id) {
    if (!els.has(id)) els.set(id, makeEl(id));
    return els.get(id);
  }

  const document = {
    getElementById: byId,
    querySelector: (sel) => (sel && sel.startsWith('#') ? byId(sel.slice(1)) : null),
    querySelectorAll: () => [],
    addEventListener: () => {}, // chặn loadDashboard tự chạy khi nạp script
  };

  // Hai canvas có "cha" để hàm render có chỗ ghi thông báo thay cho biểu đồ.
  for (const id of ['ticketDonutChart', 'auditBarChart']) {
    byId(id).parentElement = byId(`${id}-wrap`);
  }

  return { document, byId };
}

/** Nạp script nội tuyến của roi_dashboard.html vào một scope riêng. */
function loadRoi({ withChart }) {
  const m = roiHtml.match(/<script(?![^>]*\bsrc=)[^>]*>([\s\S]*?)<\/script>/);
  if (!m) return null;
  const { document, byId } = makeDom();
  const chart = withChart
    ? function Chart(ctx, cfg) { this.ctx = ctx; this.cfg = cfg; this.destroy = () => {}; }
    : undefined;

  const factory = new Function(
    'document', 'Chart', 'fetch', 'localStorage', 'sessionStorage',
    'setInterval', 'clearInterval', 'console', 'location',
    `${m[1]}
return {
  WAIT_TXT, _isLive, _live, _liveNum, esc,
  renderKpiCards, renderTicketChart, renderAuditChart,
  renderTicketTable, renderAiTicketTable, renderAuditTrail,
  renderReportText, renderDisconnected, statusLabel,
};`
  );

  const api = factory(
    document, chart,
    () => Promise.reject(new Error('không gọi mạng trong test')),
    { getItem: () => null, setItem: () => {}, removeItem: () => {} },
    { getItem: () => null, setItem: () => {}, removeItem: () => {} },
    () => 0, () => {}, console, { href: '' }
  );
  return { api, byId, text: (id) => byId(id).textContent, html: (id) => byId(id).innerHTML };
}

// Dữ liệu API THẬT (lấy từ /api/v1/roi-dashboard trên CSDL đã dọn trống).
const REAL_EMPTY = {
  report_text: '📊 BÁO CÁO VẬN HÀNH NGÀY 2026-09-29\n\n🎟️  PHIẾU ITSM:\n   • Tổng phiếu hôm nay   : 0',
  kpi: { ai_tasks_today: 0, hours_saved_today: 0.0, hours_saved_per_task: 0.25, ticket_completion_rate: 0.0, tickets_total: 0 },
  tickets: { today: { total: 0, completed: 0, pending: 0, in_progress: 0, ai_created: 0 }, open: [], in_progress: [], ai_created: [] },
  audit: { stats: { total_logs: 1276, success: 502, failed: 168, blocked_attempts: 72, ai_tasks_created: 0 }, today: {}, recent: [{ timestamp: '2026-09-29T03:36:37', employee_id: 'ceo', action_type: 'HITL_APPROVED', status: 'failed' }] },
  org: { total_employees: 0, total_departments: 0 },
};

// ──────────────────────────────────────────────────────────────────────
section('Không còn hàm dựng dữ liệu giả');

check(
  'không còn showFallbackDemo trong mã',
  !/showFallbackDemo\s*\(/.test(roiCode),
  'hàm này dựng 12 phiếu / 48 log / 15 nhân viên bịa'
);
check('có hàm renderDisconnected thay thế', /function renderDisconnected\s*\(/.test(roiCode));
check(
  'nhánh lỗi của loadDashboard gọi renderDisconnected',
  /catch\s*\(e\)\s*\{[\s\S]{0,200}renderDisconnected\s*\(e\)/.test(roiCode),
  'nếu vẫn gọi showFallbackDemo thì lỗi mạng bị khoác áo dữ liệu thật'
);

const DEMO_JUNK = [
  [/erp_demo00\d/, 'ID phiếu giả erp_demo00x'],
  [/'xyz_user'/, 'nhân viên giả xyz_user'],
  [/'ai_system'/, 'nhân viên giả ai_system'],
  [/total_logs:\s*48/, 'tổng log giả 48'],
  [/ai_tasks_today:\s*7/, 'số tác vụ AI giả 7'],
  [/hours_saved_today:\s*1\.75/, 'giờ tiết kiệm giả 1.75'],
  [/ticket_completion_rate:\s*58\.3/, 'tỷ lệ hoàn thành giả 58.3'],
  [/total_employees:\s*15/, 'số nhân viên giả 15'],
  [/total_departments:\s*4/, 'số phòng ban giả 4'],
  [/total:\s*12,\s*completed:\s*7/, 'phiếu giả 12/7'],
  [/Nginx crash/, 'sự cố giả'],
  [/Cập nhật SSL certificate/, 'công việc giả'],
  [/run_powershell_command/, 'thao tác giả trong nhật ký'],
];
for (const [re, why] of DEMO_JUNK) {
  check(`không còn ${why}`, !re.test(roiCode), re.source);
}

// ──────────────────────────────────────────────────────────────────────
section('Số 0 và "chờ kết nối" phải là hai chuyện khác nhau');

const roi = loadRoi({ withChart: true });
check('nạp được script roi_dashboard.html', roi !== null);
check('script trả ra được các hàm render', roi && typeof roi.api.renderKpiCards === 'function');

if (roi) {
  const { api, html, text } = roi;
  check('WAIT_TXT giống app.js', api.WAIT_TXT === 'chờ kết nối');

  // ── B: API trả dữ liệu thật, hôm nay 0 phiếu -> phải hiện 0, không phải "chờ kết nối"
  api.renderKpiCards(REAL_EMPTY);
  const kpiHtml = html('kpiGrid');
  check(
    'KPI hiện "0" cho nhân viên (đếm được từ DB) chứ không phải "chờ kết nối"',
    /<div class="value">0<\/div>/.test(kpiHtml) && /0 phòng ban/.test(kpiHtml),
    'CSDL trống là sự thật đếm được, khác hẳn không gọi được API'
  );
  check(
    'ô tỷ lệ hoàn thành 0/0 hiện "chờ kết nối" (không đo được), không phải "0%"',
    /class="value is-waiting">chờ kết nối<\/div>/.test(kpiHtml),
    'hai ô "0" và "không đo được" phải khác nhau về hình thức'
  );
  check(
    'tỷ lệ hoàn thành KHÔNG hiện 0% khi chưa có phiếu nào',
    !kpiHtml.includes('0.0%'),
    '0/0 không phải "0% hoàn thành" mà là không đo được'
  );
  check(
    'ô tỷ lệ hoàn thành nói "chưa có phiếu nào hôm nay"',
    /Chưa có phiếu nào hôm nay/.test(kpiHtml)
  );
  check(
    'giờ tiết kiệm không gọi 0 là số đo, mà nói chưa có tác vụ AI',
    /Chưa có tác vụ AI nào/.test(kpiHtml)
  );
  check(
    'KPI không tự nhận "0h tiết kiệm" như một kết quả',
    !/0h tiết kiệm/.test(kpiHtml),
    'trước đây in "~0h tiết kiệm", nghe như đã đo ra 0'
  );

  // ── B: biểu đồ khi hôm nay 0 phiếu -> không vẽ vòng tròn rỗng
  api.renderTicketChart(REAL_EMPTY);
  check(
    'không vẽ doughnut khi hôm nay 0 phiếu',
    /Chưa có phiếu nào hôm nay/.test(html('ticketDonutChart-wrap')),
    'vòng doughnut toàn số 0 trông như "0% hoàn thành"'
  );
  check(
    'nhãn badge nói "0 phiếu" (số thật), không nói "chờ kết nối"',
    text('badgeTotalTickets') === '0 phiếu',
    `nhận ${JSON.stringify(text('badgeTotalTickets'))}`
  );

  // ── B: audit có số thật -> vẫn vẽ
  api.renderAuditChart(REAL_EMPTY);
  check('audit log có dữ liệu nên vẫn vẽ biểu đồ', typeof text('badgeTotalAudit') === 'string');
  check('badge audit hiện số log thật', /^\d+ log$/.test(text('badgeTotalAudit')), text('badgeTotalAudit'));

  // ── A: API lỗi -> "chờ kết nối", tuyệt đối không bịa
  api.renderKpiCards({ kpi: {}, tickets: { today: {} }, audit: {}, org: {} });
  const waitHtml = html('kpiGrid');
  check(
    'KPI thiếu dữ liệu hiện "chờ kết nối"',
    /chờ kết nối/.test(waitHtml),
    'null/undefined khác 0: chưa hỏi được ai thì phải nói vậy'
  );

  api.renderTicketChart({ tickets: { today: {} } });
  check(
    'biểu đồ phiếu thiếu dữ liệu hiện "chờ kết nối"',
    /chờ kết nối/.test(html('ticketDonutChart-wrap'))
  );
  check(
    'badge phiếu thiếu dữ liệu không hiện "0 phiếu"',
    text('badgeTotalTickets') === 'chờ kết nối',
    `nhận ${JSON.stringify(text('badgeTotalTickets'))}`
  );

  api.renderAuditChart({ audit: {} });
  check(
    'badge audit thiếu dữ liệu hiện "chờ kết nối"',
    text('badgeTotalAudit') === 'chờ kết nối',
    `nhận ${JSON.stringify(text('badgeTotalAudit'))}`
  );
  check(
    'biểu đồ audit thiếu dữ liệu hiện "chờ kết nối"',
    /chờ kết nối/.test(html('auditBarChart-wrap'))
  );

  api.renderReportText({});
  check(
    'báo cáo rỗng hiện "chờ kết nối", không phải "Không có dữ liệu."',
    text('reportText').includes('chờ kết nối') && !text('reportText').includes('Không có dữ liệu.'),
    'câu "Không có dữ liệu." nghe như đã tra xong'
  );

  // ── A: mất phiên đăng nhập -> nói đúng việc cần làm
  api.renderDisconnected(new Error('HTTP 401: Unauthorized'));
  const d401 = html('kpiGrid');
  check('401 hiện "chờ kết nối"', /chờ kết nối/.test(d401));
  check('401 nói rõ cần đăng nhập lại', /đăng nhập/i.test(d401), d401.slice(0, 160));
  check('401 không dựng dữ liệu thay thế', !/\d+ phiếu/.test(d401));

  api.renderDisconnected(new Error('HTTP 500: Internal Server Error'));
  check(
    'lỗi máy chủ nói rõ không gọi được API',
    /Không gọi được/i.test(html('kpiGrid')),
    html('kpiGrid').slice(0, 160)
  );
  for (const id of ['badgeTotalTickets', 'badgeTotalAudit', 'badgeOpenTickets', 'badgeAiTickets']) {
    check(`#${id} về "chờ kết nối" khi mất kết nối`, text(id) === 'chờ kết nối', text(id));
  }

  // ── C: có số thật thì hiện đúng
  api.renderKpiCards({ ...REAL_EMPTY, kpi: { ai_tasks_today: 3, hours_saved_today: 0.75, hours_saved_per_task: 0.25, ticket_completion_rate: 100.0, tickets_total: 1 }, tickets: { today: { total: 1, completed: 1, in_progress: 0, pending: 0, ai_created: 1 } }, org: { total_employees: 7, total_departments: 2 } });
  const realHtml = html('kpiGrid');
  check('hiện số nhân viên thật (7)', realHtml.includes('>7<'), 'không được bịa hay hiện 0');
  check('hiện tỷ lệ hoàn thành thật 100.0%', realHtml.includes('100.0%'));
  check('nói rõ giờ tiết kiệm là giả định', /giả định/.test(realHtml), 'không được bán nó như số đo');
}

// ──────────────────────────────────────────────────────────────────────
section('Chart.js lỗi không được kéo cả trang vào "chờ kết nối"');

check(
  'renderTicketChart kiểm tra Chart trước khi vẽ',
  /function renderTicketChart[\s\S]{0,2200}typeof Chart === 'undefined'/.test(roiCode),
  'Chart.js nạp từ CDN; lỗi CDN rơi vào catch của loadDashboard và cả trang báo mất kết nối'
);
check(
  'renderAuditChart kiểm tra Chart trước khi vẽ',
  /function renderAuditChart[\s\S]{0,1600}typeof Chart === 'undefined'/.test(roiCode)
);

if (roi) {
  // Không có Chart -> chỉ mất biểu đồ, dữ liệu vẫn phải hiện.
  const noChart = loadRoi({ withChart: false });
  noChart.api.renderKpiCards({ ...REAL_EMPTY, tickets: { today: { total: 4, completed: 2, in_progress: 1, pending: 1, ai_created: 1 } } });
  check(
    'thiếu Chart.js vẫn hiện KPI thật',
    noChart.html('kpiGrid').includes('>4<'),
    'mất biểu đồ không được làm mất số liệu'
  );
  noChart.api.renderTicketChart({ tickets: { today: { total: 4, completed: 2, in_progress: 1, pending: 1 } } });
  check(
    'thiếu Chart.js báo rõ nguyên nhân thay vì im lặng',
    /Chart\.js/.test(noChart.html('ticketDonutChart-wrap'))
  );
  check(
    'thiếu Chart.js không ghi đè badge bằng "chờ kết nối"',
    noChart.text('badgeTotalTickets') === '4 phiếu',
    noChart.text('badgeTotalTickets')
  );
}

// ──────────────────────────────────────────────────────────────────────
section('Chỗ dễ hỏng khi số liệu đến từ nơi khác');

check(
  'renderReportText dùng report_text thật khi có',
  /el\.textContent = data\.report_text/.test(roiCode)
);
check(
  'nội suy tiêu đề phiếu phải qua esc() (chống HTML trong dữ liệu)',
  /\$\{esc\(t\.title/.test(roiCode) && /\$\{esc\(t\.resolution_notes/.test(roiCode),
  'tiêu đề/ghi chú là văn bản người dùng nhập'
);
check('có định nghĩa esc()', /function esc\(/.test(roiCode));
check(
  'trạng thái thiết bị lạ hiện nhãn rõ, không im lặng',
  /function statusLabel/.test(roiCode)
);

// ──────────────────────────────────────────────────────────────────────
section('Quy ước "chờ kết nối" giống hệt app.js');

function extractFn(src, name) {
  const m = src.match(new RegExp(`function ${name}\\([^\\n]*\\)\\s*\\{[\\s\\S]*?\\n\\}`));
  return m ? m[0] : null;
}
for (const name of ['_isLive', '_live', '_liveNum', '_setLiveText']) {
  const a = extractFn(appJs, name);
  const r = extractFn(roiHtml, name);
  check(`hàm ${name} khớp từng ký tự với app.js`, a !== null && a === r, 'một bên sửa mà bên kia không sửa là lệch hành vi');
}
check(
  'hai nơi dùng cùng một câu WAIT_TXT',
  /const WAIT_TXT = 'chờ kết nối'/.test(appJs) && /const WAIT_TXT = 'chờ kết nối'/.test(roiHtml)
);
check('có CSS .is-waiting để làm mờ ô chờ', /\.is-waiting\s*\{/.test(roiHtml));

// ──────────────────────────────────────────────────────────────────────
section('HTML không hiện số giả trước khi JS chạy');

for (const [id, junk] of [
  ['badgeTotalTickets', '0 phiếu'],
  ['badgeTotalAudit', '0 log'],
  ['badgeOpenTickets', '>0<'],
  ['badgeAiTickets', '>0<'],
]) {
  const re = new RegExp(
    `id="${id}"[^>]*>[^<]*${junk.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')}`
  );
  check(`#${id} không mặc định "${junk}"`, !re.test(roiHtml));
}
check(
  'ô báo cáo không mặc định "Đang tải..."',
  !/id="reportText"[^>]*>\s*Đang tải/.test(roiHtml),
  '"Đang tải..." đứng mãi nếu JS lỗi'
);
check(
  'KPI mặc định là spinner "Đang tải dữ liệu...", không phải 0',
  /Đang tải dữ liệu/.test(roiHtml)
);

// ──────────────────────────────────────────────────────────────────────
console.log('\n' + '─'.repeat(60));
console.log(`Tổng: ${pass + fail} | Pass: ${pass} | Fail: ${fail}`);
if (failures.length) {
  console.log('\n❌ CÓ LỖI:');
  for (const f of failures) console.log('  - ' + f);
  process.exit(1);
} else {
  console.log('\n✅ TẤT CẢ PASS');
}
