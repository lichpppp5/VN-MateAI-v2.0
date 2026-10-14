// SPDX-License-Identifier: Apache-2.0
// Copyright (c) 2026 Dương Thanh Lịch — VN-MateAI. See LICENSE and NOTICE.
// tests/test_erp_ad_ui.mjs
// Cấu Trúc Tổ Chức & Quản Trị ERP: nút "Nhập từ AD" (xem trước rồi mới nhập), cột Agent cho thiết bị ERP
// và máy AD, nhãn không còn khẳng định "SQLite".
import { readFileSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';

const ROOT = join(dirname(fileURLToPath(import.meta.url)), '..');
const app = readFileSync(join(ROOT, 'web', 'app.js'), 'utf8').replace(/\r\n/g, '\n');
const html = readFileSync(join(ROOT, 'web', 'index.html'), 'utf8').replace(/\r\n/g, '\n');

function cut(startMark, endMark) {
  const a = app.indexOf(startMark);
  const b = app.indexOf(endMark, a);
  if (a < 0 || b < 0) throw new Error('không tìm thấy: ' + startMark);
  return app.slice(a, b);
}
const esc = cut('function _esc(s) {', '\n}\n') + '\n}\n';
const block = cut('const AGENT_BADGE = {', 'let _erpAdPreviewKey');
const { agentBadgeHtml, agentCoverageText, adImportSummaryHtml } =
  new Function(`${esc}\n${block}\nreturn { agentBadgeHtml, agentCoverageText, adImportSummaryHtml };`)();

let passed = 0;
function ok(cond, name) {
  if (!cond) { console.error(`FAIL ${name}`); process.exit(1); }
  passed += 1;
}

// ── 1. Huy hiệu Agent ───────────────────────────────────────────────────────
ok(/Trực tuyến/.test(agentBadgeHtml({ status: 'online', client_id: 'PC-KT-01' })) && /Mã agent: PC-KT-01/.test(agentBadgeHtml({ status: 'online', client_id: 'PC-KT-01' })), 'online + mã agent');
ok(/ngoại tuyến/.test(agentBadgeHtml({ status: 'offline', client_id: 'X' })), 'đã cài nhưng ngoại tuyến');
ok(/thu hồi/.test(agentBadgeHtml({ status: 'revoked', client_id: 'X' })), 'bị thu hồi');
ok(/Chưa cài/.test(agentBadgeHtml({ status: 'none' })) && /Chưa cài/.test(agentBadgeHtml(undefined)) && /Chưa cài/.test(agentBadgeHtml({ status: 'la-hoac' })), 'chưa cài / thiếu dữ liệu / trạng thái lạ -> "Chưa cài"');
ok(!/<script/.test(agentBadgeHtml({ status: 'online', client_id: '<script>x</script>' })), 'mã agent được thoát HTML');

// ── 2. Tóm tắt độ phủ ───────────────────────────────────────────────────────
ok(agentCoverageText({ total: 40, online: 12, offline: 3, revoked: 1, none: 24 }) === 'Agent trên 40 máy: 12 trực tuyến · 3 đã cài nhưng ngoại tuyến · 1 bị thu hồi · 24 chưa cài', 'dòng độ phủ');
ok(agentCoverageText(null) === '' && agentCoverageText({ total: 0 }) === '', 'không có máy -> không hiện');

// ── 3. Báo cáo xem trước / nhập ─────────────────────────────────────────────
const res = {
  applied: false, empty: false, source: { employees: 120, computers: 45 },
  stats: {
    departments_created: 2,
    employees: { created: 100, updated: 5, unchanged: 10, skipped_no_department: 4, skipped_department_missing: 0, skipped_ambiguous: 1, skipped_invalid: 0 },
    devices: { created: 40, updated: 2, unchanged: 3, skipped_no_department: 0, skipped_department_missing: 0, skipped_ambiguous: 0, skipped_invalid: 0, owner_matched: 31 },
  },
  samples: { skipped_no_department: ['Lê Chi', '<b>x</b>'] }, warnings: ['Nhân viên \'An\' khớp nhiều người'], new_departments: ['Kế toán', 'Kỹ thuật'],
};
let h = adImportSummaryHtml(res);
ok(/Xem trước — chưa ghi gì/.test(h) && /120 nhân viên, 45 máy/.test(h), 'xem trước ghi rõ chưa ghi gì + số nguồn');
ok(/Kế toán, Kỹ thuật/.test(h) && />31</.test(h.replace(/<b>/g, '>').replace(/<\/b>/g, '<')) || /Máy gán được chủ: <b>31<\/b>/.test(h), 'phòng ban mới + số máy gán được chủ');
ok(/Bỏ qua: không có phòng ban/.test(h) && /Lê Chi/.test(h) && !/<b>x<\/b>/.test(h), 'mục bị bỏ qua + mẫu, thoát HTML');
ok(/khớp nhiều người/.test(h), 'cảnh báo mơ hồ');
h = adImportSummaryHtml({ ...res, applied: true });
ok(/✅ Đã nhập/.test(h) && !/chưa ghi gì/.test(h), 'đã nhập thật');
ok(/Bản sao AD đang trống/.test(adImportSummaryHtml({ empty: true, message: 'Bản sao AD đang trống — hãy đồng bộ' })), 'bản sao AD trống');
ok(adImportSummaryHtml(null) === '', 'không có dữ liệu');

// ── 4. HTML ─────────────────────────────────────────────────────────────────
for (const id of ['btn-erp-ad-import', 'erp-ad-import-panel', 'erp-ad-default-dept', 'erp-ad-create-depts', 'erp-ad-inc-emp', 'erp-ad-inc-dev',
  'btn-erp-ad-preview', 'btn-erp-ad-apply', 'erp-ad-import-result', 'erp-agent-coverage', 'domain-agent-coverage', 'svc-db-name']) {
  ok(html.includes(`id="${id}"`), `index.html có #${id}`);
}
ok(/id="btn-erp-ad-apply"[^>]*disabled/.test(html), '"Nhập thật" mặc định bị khoá cho tới khi xem trước');
ok(!/SQLite\s+Relational Model/.test(html) && !/SQLite Database<\/div>/.test(html) && !/Active Directory \(SQLite Cache\)/.test(html), 'không còn nhãn SQLite sai sự thật');
const adTable = html.slice(html.indexOf('id="domain-table-computers"'), html.indexOf('id="domain-agent-coverage"'));
ok(/<th class="p-2.5">Agent<\/th>/.test(adTable) && /colspan="6"/.test(adTable) && !/colspan="5"/.test(adTable), 'bảng máy AD: cột Agent + colspan 6');
const adJs = app.slice(app.indexOf('async function fetchDomainData() {'), app.indexOf('function filterDomainTable() {'));
ok(adJs.length > 500 && !/colspan="5"/.test(adJs) && /colspan="6"/.test(adJs), 'JS bảng máy AD: colspan 6 (đã thêm cột Agent)');
ok(/<th class="py-2\.5 px-3">AGENT<\/th>/.test(app) && /agentBadgeHtml\(d\.agent\)/.test(app) && /agentBadgeHtml\(c\.agent\)/.test(app), 'cột Agent ở bảng thiết bị ERP và máy AD');
ok(/erpAdImportRun/.test(app) && /key !== _erpAdPreviewKey/.test(app), 'nhập thật chỉ khi vừa xem trước đúng lựa chọn hiện tại');

console.log(`test_erp_ad_ui: ${passed} passed`);
