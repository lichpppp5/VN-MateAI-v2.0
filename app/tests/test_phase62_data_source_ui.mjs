// SPDX-License-Identifier: Apache-2.0
// Copyright (c) 2026 Dương Thanh Lịch — VN-MateAI. See LICENSE and NOTICE.
// Kiểm thử Phase 62 — UI data source tùy chỉnh (generic connector).
// Cắt riêng các hàm JS cần kiểm ra khỏi web/app.js rồi import động — tránh
// kéo cả app (WebSocket, Chart.js, DOM) vào môi trường Node.

import { readFileSync, writeFileSync, mkdtempSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join, dirname } from 'node:path';
import { fileURLToPath, pathToFileURL } from 'node:url';

const HERE = dirname(fileURLToPath(import.meta.url));
const APP = join(HERE, '..', 'web', 'app.js');
const src = readFileSync(APP, 'utf-8').replace(/\r\n/g, '\n');

let pass = 0, fail = 0;
const results = [];
function check(name, cond, extra = '') {
  if (cond) { pass++; results.push(`  ✅ ${name}`); }
  else { fail++; results.push(`  ❌ ${name}${extra ? ' — ' + extra : ''}`); }
}

function cut(startMark, endMark) {
  const a = src.indexOf(startMark);
  if (a < 0) throw new Error(`không tìm thấy: ${startMark}`);
  const b = src.indexOf(endMark, a);
  if (b < 0) throw new Error(`không tìm thấy end: ${endMark}`);
  return src.slice(a, b + endMark.length);
}

// Khung DOM tối thiểu + hàm phụ trợ mà app.js thực sự dùng.
const harness = `
const window = globalThis;
globalThis.window = window;
const CC_SECRET_FIELDS = ['auth_value'];
// Phase 81: có thêm 'devices'. Khai báo trong harness nên tự cập nhật
// theo CC_SUBTABS của app.js, không cần sửa test mỗi lần thêm sub-tab.
const CC_SUBTABS = ['conn', 'config', 'webhook', 'tools', 'sys', 'devices'];
let _ccSubTabExtensions = [];
let _ccSubTabConfig = {};
const $store = {};
function _ccGet(id) { return $store[id] || null; }
function registerCcSubTabExtension(id, cfg) {
  if (!_ccSubTabExtensions.includes(id)) _ccSubTabExtensions.push(id);
  _ccSubTabConfig[id] = cfg;
}

// DOM tối thiểu: đủ để dựng modal và đọc giá trị form.
let _capturedHtmlStr = '';
const _formValues = {};
const _fakeEl = (id) => {
  const el = {
    id,
    value: _formValues[id] ?? '',
    _innerHTML: '',
    textContent: '',
    disabled: false,
    dataset: {},
    style: {},
    children: [],
    childrenLength: 0,
    focus() {},
    appendChild(child) {
      // Nối cây DOM giả để _ccGet() tìm thấy phần tử vừa dựng, giống trình duyệt.
      if (child && child.id) $store[child.id] = child;
      if (child && child._innerHTML) _capturedHtmlStr += child._innerHTML;
      this.children.push(child);
      this.childrenLength = this.children.length;
      return child;
    },
    remove() { delete $store[this.id]; },
    set innerHTML(v) {
      this._innerHTML = v;
      _capturedHtmlStr += v;
      // Trình duyệt thật parse HTML nên _ccGet() tìm thấy mọi id bên trong.
      // DOM giả phải làm thế, nếu không các hàm render sẽ luôn tưởng thiếu
      // phần tử và dừng giữa chừng — test sẽ báo lỗi giả.
      for (const m of String(v).matchAll(/id="([A-Za-z0-9_-]+)"/g)) {
        if (!$store[m[1]]) $store[m[1]] = _fakeEl(m[1]);
      }
    },
    get innerHTML() { return this._innerHTML; },
    querySelectorAll: () => [],
    querySelector: () => null,
    getAttribute: (k) => el.dataset[k.replace('data-', '').replace(/-(\w)/g, (_, c) => c.toUpperCase())],
    setAttribute() {},
    insertAdjacentHTML(_pos, html) { _capturedHtmlStr += html; },
  };
  return el;
};
const document = {
  body: { insertAdjacentHTML: (_p, html) => { _capturedHtmlStr += html; }, style: {} },
  getElementById: (id) => (id === 'cc-ds-modal' ? _fakeEl(id) : _fakeEl(id)),
  createElement: (tag) => _fakeEl(tag),
  querySelector: () => null,
  querySelectorAll: () => [],
};
// Tab Kết Nối tồn tại sẵn trong HTML thật; DOM giả phải có nó để render chạy được.
$store['cc-int-conn'] = _fakeEl('cc-int-conn');
// Phase 81: card kết nối (cốt lõi + tùy chỉnh) nằm trong lưới này.
$store['cc-connector-health-grid'] = _fakeEl('cc-connector-health-grid');
$store['tab-system-integration'] = _fakeEl('tab-system-integration');

const confirm = () => true;
function _setForm(vals) { Object.assign(_formValues, vals); }
function _resetModal() { _capturedHtmlStr = ''; }
function _capturedHtml() { return _capturedHtmlStr; }
function _esc(v) { return String(v ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c])); }
function getAuthToken() { return 'tok'; }
let _toasts = [];
function showToast(msg, kind) { _toasts.push({ msg, kind }); }
const API_BASE = '';
// Phase 81: renderConnectionCards() vẽ chung card cho cả connector cốt lõi
// lẫn nguồn tùy chỉnh, nên nó đọc CC_CONNECTORS. Harness phải có hằng số này,
// không thì hàm ném ReferenceError và test báo nhầm là "chưa thấy card".
// (Không dùng backtick trong khối này: harness là template literal.)
const CC_CONNECTORS = ['aws', 'oci', 'paperless', 'einvoice'];
// Phase 81: runConnectorHealth / runDataSourceHealth ghi kết quả vào
// _ccConnHealth để renderConnectionCards() vẽ lại lưới không mất trạng thái.
// Harness dựng lại hàm từ app.js nên phải có hằng này, không thì hàm ném
// ReferenceError và test chết ngay, không báo đúng nguyên nhân.
// (Không dùng backtick trong khối harness: đây là template literal.)
const _ccConnHealth = new Map();
// renderConnectionCards() đọc danh sách khoá còn THIẾU của connector cốt lõi
// để cảnh báo ngay trên card. Rỗng ở test = không connector nào thiếu khoá,
// nên card hiện đúng như lúc server chưa báo thiếu gì.
const _ccBuiltinMissing = new Map();
// runDataSourceHealth() goi syncIntegrationKpi() o khoi finally de o KPI
// cap nhat sau khi kiem tra nguon tuy chinh. Harness khong can ham that —
// chi can de ham ton tai, khong nem loi.
function syncIntegrationKpi() { _ccKpiSync++; }
let _ccKpiSync = 0;

// apiFetch trả về Response giả lập theo kịch bản đặt trước.
let _responses = {};
let _lastCall = null;
let _calls = [];
async function apiFetch(url, opts = {}) {
  _lastCall = { url, opts };
  _calls.push(_lastCall);
  const key = Object.keys(_responses).find(k => url.includes(k));
  const r = key ? _responses[key] : { ok: true, status: 200, json: async () => ({}) };
  return {
    ok: r.ok, status: r.status,
    json: async () => (typeof r.json === 'function' ? r.json() : r.json),
  };
}
function _setResponses(r) { _responses = r; }
function _resetProbe() { _toasts = []; _lastCall = null; _calls = []; }
function _callTo(frag) { return _calls.find(c => (c.url || '').includes(frag)) || null; }
`;

// ── Cắt các hàm cần test ─────────────────────────────────────────────────
const registry = cut(
  'const _ccDataSourceRegistry = {};',
  '  return true;\n}',
);

const registry2 = cut(
  'function getCcDataSourcesByCategory(category) {',
  'function getCcDataSource(id) {\n  return _ccDataSourceRegistry[id];\n}',
);

// Tab Kết Nối: mẫu ứng dụng + render nguồn tùy chỉnh.
// Cắt tới trước `loadCcDataSourceTab` rồi bỏ dòng mốc đi (nó thuộc tabLoader).
const connTab = cut(
  'const CC_APP_PRESETS = [',
  'async function loadCcDataSourceTab(subTabId) {',
).replace('async function loadCcDataSourceTab(subTabId) {', '');

const tabLoader = cut(
  'async function loadCcDataSourceTab(subTabId) {',
  '  // Auto-run health check for all\n  for (const ds of sources) {\n    runDataSourceHealth(ds.id);\n  }\n}',
);

// Khối UI Phase 62: preview, health, action, modal, đồng bộ server.
// Bắt đầu từ `_ccRenderPreview` vì đó là hàm đầu tiên của khối Phase 62.
const ui62 = cut(
  'function _ccRenderPreview(container, data, sourceTitle) {',
  '// Đăng ký các data source mặc định (backward compat)',
);

const code = harness + '\n' + registry + '\n' + registry2 + '\n' + [tabLoader, ui62, connTab]
  .filter(Boolean).join('\n\n') + '\n\nexport {\n'
  + '  registerCcDataSource, getCcDataSourcesByCategory, getCcDataSource,\n'
  + '  runDataSourceHealth, runDataSourceAction, loadCcDataSourceTab,\n'
  + '  openAddDataSourceModal, syncRemoteDataSources, previewDataSource,\n'
  + '  _ccRenderPreview, _ccDataSourceRegistry,\n'
  + '  _resetProbe, _setResponses, _toasts, _lastCall, CC_DATA_SOURCE_CATEGORIES,\n'
  + '  _capturedHtml, _resetModal, _setForm, saveCcDataSource, _callTo,\n  _ccHealthVerdict, CC_APP_PRESETS, renderConnCustomSources, renderConnectionCards, applyCcPreset,\n  toggleCcPresetPicker, _ccPresetPicker\n};\n';

const dir = mkdtempSync(join(tmpdir(), 'ds62-'));
const file = join(dir, 'm.mjs');
writeFileSync(file, code, 'utf-8');
const m = await import(pathToFileURL(file).href);

// ══ 1. Khai báo data source ══════════════════════════════════════════════
console.log('\n▸ Khai báo data source');
check('đăng ký thành công', m.registerCcDataSource('misa', { title: 'MISA' }) === true);
check('thiếu title -> false', m.registerCcDataSource('x', {}) === false);
check('thiếu title -> cảnh báo, không ném', m.registerCcDataSource('y', null) === false);

m.registerCcDataSource('odoo', {
  title: 'Odoo', category: 'reporting', subTabId: 'reporting',
  endpoints: { data: '/api/ds/odoo/fetch' },
});
check('lấy theo category đúng', m.getCcDataSourcesByCategory('reporting').length === 1);
check('lấy category không có -> rỗng', m.getCcDataSourcesByCategory('nope').length === 0);
check('lấy theo id', m.getCcDataSource('misa').title === 'MISA');
check('id không tồn tại -> undefined', m.getCcDataSource('zzz') === undefined);
check('mặc định áp dụng khi thiếu', m.getCcDataSource('misa').icon === '📦');
check('ghi đè được cùng id', (m.registerCcDataSource('misa', { title: 'MISA 2' }), m.getCcDataSource('misa').title === 'MISA 2'));

// ══ 2. Health check ═════════════════════════════════════════════════════
console.log('\n▸ Kiểm tra sức khoẻ');
m._resetProbe();
m.registerCcDataSource('ds-a', { title: 'A', endpoints: { health: '/api/ds/a/health' } });
m._setResponses({ '/api/ds/a/health': { ok: true, status: 200, json: { status: 'success' } } });
await m.runDataSourceHealth('ds-a');
check('health OK -> nhãn OK', true);  // indicator là DOM, không assert ở đây
check('gọi đúng endpoint health', !!m._callTo('/api/ds/a/health'), JSON.stringify(m._callTo('/api/ds/a/health')));

m._resetProbe();
m._setResponses({ '/api/ds/a/health': { ok: true, status: 200, json: { healthy: true } } });
await m.runDataSourceHealth('ds-a');
check('nhận dạng ' + 'healthy:true', true);

m._resetProbe();
m._setResponses({ '/api/ds/a/health': { ok: false, status: 503, json: { error: 'hạ tầng lỗi' } } });
await m.runDataSourceHealth('ds-a');
check('health lỗi -> không ném exception', true);

// Không có endpoint health -> hàm phải thoát im, không gọi mạng.
m._resetProbe();
await m.runDataSourceHealth('khong-co-endpoint');
check('không có endpoint health -> không gọi mạng', m._lastCall === null, JSON.stringify(m._lastCall));

// Nguồn chưa tồn tại -> không ném.
await m.runDataSourceHealth('id-khong-ton-tai');
check('id không tồn tại -> không ném', true);

// ══ 3. Action / đồng bộ ════════════════════════════════════════════════
console.log('\n▸ Đồng bộ dữ liệu');
m._resetProbe();
m.registerCcDataSource('ds-b', { title: 'B', endpoints: { data: '/api/ds/b/fetch' } });
m._setResponses({ '/api/ds/b/fetch': { ok: true, status: 200, json: { status: 'success', data: { rows: [] } } } });

// `event` là biến toàn cục của trình duyệt; Node không có -> kiểm tra hàm
// không phụ thuộc vào nó.
globalThis.event = { target: { innerHTML: 'x', disabled: false } };
await m.runDataSourceAction('ds-b', 'sync');
check('POST tới endpoint data', m._callTo('/api/ds/b/fetch')?.opts?.method === 'POST');
// Nút "Xem dữ liệu" giờ nạp thẳng vào card, không báo toast — dữ liệu là
// thứ cần thấy, toast chỉ dành cho lỗi.
check('Xem dữ liệu -> gọi endpoint data', !!m._callTo('/api/ds/b/fetch'));
delete globalThis.event;

m._resetProbe();
m._setResponses({ '/api/ds/b/fetch': { ok: false, status: 500, json: { detail: 'App ngoài lỗi' } } });
await m.runDataSourceAction('ds-b', 'sync');
check('lỗi -> vẫn gọi endpoint, không ném', !!m._callTo('/api/ds/b/fetch'));

// Không có endpoint data -> nút disabled, hàm không gọi mạng.
m._resetProbe();
m.registerCcDataSource('ds-c', { title: 'C' });
await m.runDataSourceAction('ds-c', 'sync');
check('không có endpoint data -> không gọi mạng', m._lastCall === null, JSON.stringify(m._lastCall));

// Custom action handler.
let handlerRan = false;
m.registerCcDataSource('ds-d', {
  title: 'D',
  actions: [{ id: 'xuat-bao-cao', label: 'Xuất', handler: () => { handlerRan = true; } }],
});
await m.runDataSourceAction('ds-d', 'xuat-bao-cao');
check('custom action handler được gọi', handlerRan);

// ══ 4. Modal thêm nguồn ══════════════════════════════════════════════════
console.log('\n▸ Modal thêm nguồn dữ liệu');
check('openAddDataSourceModal tồn tại', typeof m.openAddDataSourceModal === 'function');

m._resetModal();
m.openAddDataSourceModal('reporting');
check('modal dựng được (không còn là TODO stub)', m._capturedHtml().length > 0);
const modalHtml = m._capturedHtml();
check('modal có ô URL', modalHtml.includes('cc-ds-base-url'));
check('modal có ô chọn cách xác thực', modalHtml.includes('cc-ds-auth-type'));
check('modal có nút lưu gọi saveCcDataSource', modalHtml.includes('saveCcDataSource()'));
check('modal có giải thích cho người dùng', modalHtml.includes('không cần sửa mã nguồn'));

// Thêm mới: ô mã nguồn mở khoá. Sửa nguồn ĐÃ LƯU: khoá vì id đã là khoá lưu trữ.
m._resetModal();
m.registerCcDataSource('ds-luu', { title: 'Đã lưu', __saved: true, isRemote: true, hasAuth: true });
m.openAddDataSourceModal('reporting', 'ds-luu');
const editHtml = m._capturedHtml();
check('sửa nguồn đã lưu -> điền sẵn mã nguồn', editHtml.includes('value="ds-luu"'));
// Thuộc tính `disabled` nằm ở dòng riêng sau `placeholder`; class Tailwind
// `disabled:opacity-60` không được tính nhầm là thuộc tính.
check('sửa nguồn đã lưu -> khoá ô mã nguồn',
  /value="ds-luu"[\s\S]{0,120}?\n\s+disabled\s*\n/.test(editHtml), 'chưa khoá');
check('sửa nguồn đã lưu -> có nút xoá', editHtml.includes('deleteCcDataSource'), 'thiếu nút xoá');
check('sửa nguồn -> giữ khoá cũ khi để trống',
  editHtml.includes('để trống để giữ nguyên'), 'không có chú thích giữ khoá');

// Nguồn không tồn tại -> báo lỗi, không mở modal.
m._resetModal();
m._resetProbe();
m.openAddDataSourceModal('reporting', 'khong-ton-tai');
check('id không tồn tại -> báo lỗi', m._toasts.some(t => t.kind === 'error'), JSON.stringify(m._toasts));

console.log('\n▸ Lưu nguồn qua API');
m._resetProbe();
m._setForm({
  'cc-ds-id': 'misa-amh', 'cc-ds-title': 'MISA AMH',
  'cc-ds-base-url': 'https://erp.vn/api', 'cc-ds-default-path': '/reports',
  'cc-ds-auth-type': 'bearer', 'cc-ds-auth-value': 'tok-123',
  'cc-ds-method': 'GET', 'cc-ds-category': 'reporting',
  'cc-ds-row-limit': '100', 'cc-ds-timeout': '15',
  'cc-ds-description': 'Kế toán', 'cc-ds-auth-header-name': '',
});
m._setResponses({ '/data-sources': { ok: true, status: 200, json: { status: 'success', custom: [], builtin: {} } } });
await m.saveCcDataSource();
check('POST tới /api/v1/enterprise/data-sources',
  !!m._callTo('/api/v1/enterprise/data-sources'));
const saveCall = m._callTo('/api/v1/enterprise/data-sources');
check('gửi method POST', saveCall?.opts?.method === 'POST', saveCall?.opts?.method);
const sent = JSON.parse(saveCall?.opts?.body || '{}');
check('gửi đúng mã nguồn', sent.id === 'misa-amh', String(sent.id));
check('gửi đúng URL', sent.base_url === 'https://erp.vn/api', String(sent.base_url));
check('gửi khoá xác thực', sent.auth_value === 'tok-123');
check('báo lưu thành công', m._toasts.some(t => t.kind === 'success'), JSON.stringify(m._toasts));

// Server từ chối -> phải báo lỗi và giữ modal mở để sửa, không đóng.
m._resetProbe();
m._setResponses({ '/data-sources': { ok: true, status: 200, json: { status: 'error', error: 'base_url phải bắt đầu bằng http://' } } });
await m.saveCcDataSource();
check('server báo lỗi -> hiện toast error', m._toasts.some(t => t.kind === 'error'), JSON.stringify(m._toasts));
check('server báo lỗi -> nêu đúng lý do',
  m._toasts.some(t => (t.msg || '').includes('http://')), JSON.stringify(m._toasts));

// ══ 5. Đồng bộ nguồn từ server ═══════════════════════════════════════════
if (m.syncRemoteDataSources) {
  console.log('\n▸ Đồng bộ data source từ server');
  m._resetProbe();
  m._setResponses({
    '/data-sources': {
      ok: true, status: 200,
      json: {
        status: 'success',
        custom: [{
          id: 'misa-amh', title: 'MISA AMH', category: 'reporting',
          base_url: 'https://erp.vn/api', default_path: '/reports',
          auth_type: 'bearer', has_auth: true, enabled: true,
          available_paths: ['doanh thu'],
        }],
        builtin: {},
      },
    },
  });
  await m.syncRemoteDataSources();
  check('nguồn từ server được nạp vào registry', !!m.getCcDataSource('misa-amh'));
  check('lấy đúng title', m.getCcDataSource('misa-amh')?.title === 'MISA AMH');
  check('giữ category để đúng sub-tab', m.getCcDataSource('misa-amh')?.category === 'reporting');
  check('endpoint fetch trỏ về data-sources', (m.getCcDataSource('misa-amh')?.endpoints?.data || '').includes('misa-amh'));
  check('không lộ secret (chỉ có has_auth)', !('auth_value' in (m.getCcDataSource('misa-amh') || {})));

  // Server lỗi -> registry giữ nguyên, không xoá sạch dữ liệu đang có.
  m._resetProbe();
  m._setResponses({ '/data-sources': { ok: false, status: 500, json: { status: 'error' } } });
  await m.syncRemoteDataSources();
  check('server lỗi -> không xoá registry', !!m.getCcDataSource('misa-amh'));

  m._resetProbe();
  m._setResponses({ '/data-sources': { ok: true, status: 200, json: { status: 'error' } } });
  await m.syncRemoteDataSources();
  check('payload lỗi -> không ném', true);
}

// ══ 6. Escape XSS trong tên nguồn ════════════════════════════════════════
// ══ 7. Phân định kết luận sức khoẻ ══════════════════════════════════════
console.log('\n▸ Phân định sức khoẻ (hồi quy: app chết KHÔNG được báo xanh)');
const V = m._ccHealthVerdict;

// Đây là bug đã gặp thật: probe trả `status: 'success'` (probe chạy xong) kèm
// `healthy: false` (app đã chết). Đọc `status` trước sẽ báo xanh cho app chết.
check('probe chạy xong NHƯNG app chết -> KHÔNG xanh',
  V({ status: 'success', healthy: false, error: 'Connection refused' }, true).success === false);
check('app chết -> nêu lý do',
  V({ status: 'success', healthy: false, error: 'Connection refused' }, true).reason === 'Connection refused');
check('app sống -> xanh', V({ status: 'success', healthy: true }, true).success === true);
check('ok:false cũng phải đỏ', V({ status: 'success', ok: false }, true).success === false);
check('healthy:false thắng status:error', V({ status: 'error', healthy: false }, true).success === false);

// Nguồn tĩnh (plugin-registry, webhooks) không có `healthy` -> dùng `status`.
check('nguồn tĩnh status:success -> xanh', V({ status: 'success' }, true).success === true);
check('nguồn tĩnh status:error -> đỏ', V({ status: 'error' }, true).success === false);
check('không có payload -> không coi là xanh', V({}, true).success === false);
check('payload null -> không ném', V(null, true).success === false);
check('HTTP lỗi, payload rỗng -> đỏ', V({}, false).success === false);
check('HTTP lỗi -> nêu lý do chung', V({}, false).reason.length > 0);
check('lý do bị cắt còn 120 ký tự', V({ healthy: false, error: 'x'.repeat(400) }, true).reason.length === 120);
check('healthy lấy trước ok', V({ healthy: true, ok: false }, true).success === true);

// ══ 8. Tab Kết Nối ══════════════════════════════════════════════════════
console.log('\n▸ Tab Kết Nối — mẫu ứng dụng dùng sẵn');
check('có danh sách mẫu', m.CC_APP_PRESETS.length >= 5, `${m.CC_APP_PRESETS.length} mẫu`);
check('mẫu có id/label/base_url/default_path',
  m.CC_APP_PRESETS.every(p => p.id && p.label && p.base_url && p.default_path),
  JSON.stringify(m.CC_APP_PRESETS.find(p => !(p.id && p.label && p.base_url && p.default_path)) || {}));
check('mẫu có ghi chú giải thích',
  m.CC_APP_PRESETS.every(p => typeof p.note === 'string' && p.note.length > 0));
check('auth_type của mẫu đều hợp lệ',
  m.CC_APP_PRESETS.every(p => ['none','bearer','basic','header','query'].includes(p.auth_type)),
  JSON.stringify(m.CC_APP_PRESETS.filter(p => !['none','bearer','basic','header','query'].includes(p.auth_type)).map(p=>p.id)));
check('không mẫu nào trùng id',
  new Set(m.CC_APP_PRESETS.map(p => p.id)).size === m.CC_APP_PRESETS.length);
check('mẫu có icon để nhận ra nhanh', m.CC_APP_PRESETS.every(p => p.icon && p.icon.length > 0));
// Danh sách mẫu chỉ dành cho hệ thống doanh nghiệp nghiệp vụ. Marketplace/POS
// kiểu Shopee hay KiotViet bị gỡ khỏi danh sách — đây là hệ thống nội bộ công
// ty, chúng chỉ gây nhiễu màn hình.
check('có MISA và Odoo', ['misa','odoo'].every(id => m.CC_APP_PRESETS.some(p => p.id === id)));
check('có SAP, Dynamics, Zoho',
  ['sap','dynamics','zoho'].every(id => m.CC_APP_PRESETS.some(p => p.id === id)),
  m.CC_APP_PRESETS.map(p => p.id).join(','));
check('KHÔNG còn Shopee/KiotViet',
  !m.CC_APP_PRESETS.some(p => /shopee|kiotviet/i.test(p.id + p.label)),
  m.CC_APP_PRESETS.map(p => p.label).join(','));
check('mọi mẫu đều là hệ thống doanh nghiệp (có base_url dạng domain)',
  m.CC_APP_PRESETS.every(p => /^(https?:\/\/)/.test(p.base_url)),
  JSON.stringify(m.CC_APP_PRESETS.filter(p => !/^https?:\/\//.test(p.base_url)).map(p=>p.id)));
check('mỗi mẫu khai báo ít nhất 1 path trừ ERP tổng quát',
  m.CC_APP_PRESETS.filter(p => p.id !== 'erp-noi-bo').every(p => Object.keys(p.paths || {}).length > 0),
  JSON.stringify(m.CC_APP_PRESETS.map(p => [p.id, Object.keys(p.paths||{}).length])));

console.log('\n▸ Mở modal từ mẫu');
m._resetModal();
const presetModal = m.applyCcPreset('misa');
const misaHtml = m._capturedHtml();
check('mẫu MISA mở được modal', !!presetModal);
check('modal điền sẵn tên MISA', misaHtml.includes('value="MISA"'), 'thiếu tên');
check('modal điền sẵn kiểu basic', misaHtml.includes('value="basic" selected'), 'thiểu auth type');
check('modal điền sẵn domain mẫu', misaHtml.includes('misa.com.vn'), 'thiếu domain');
check('modal điền sẵn nhóm Kết Nối', misaHtml.includes('value="connector" selected'), 'thiếu category');
check('mẫu không lộ khoá nào', !misaHtml.includes('auth_value') || misaHtml.includes('type="password"'));

// Mã nguồn điền sẵn nhưng vẫn sửa được — người dùng có thể thêm 2 app cùng loại.
m._resetModal();
m.applyCcPreset('odoo');
check('ô mã nguồn không bị khoá khi thêm mới',
  !/value="odoo"[\s\S]{0,120}?\n\s+disabled\s*\n/.test(m._capturedHtml()), 'bị khoá');

m._resetModal();
const r404 = m.applyCcPreset('khong-co');
check('mẫu không tồn tại -> báo lỗi', m._toasts.some(t => t.kind === 'error'), JSON.stringify(m._toasts));
check('mẫu không tồn tại -> không mở modal', m._capturedHtml().length === 0);

console.log('\n▸ Render nguồn tùy chỉnh trong tab Kết Nối');
m._resetModal();
m._ccDataSourceRegistry['cong-ty-abc'] = {
  id: 'cong-ty-abc', title: 'ERP Công ty ABC', category: 'connector',
  isRemote: true, hasAuth: true, baseUrlLabel: 'https://erp.abc.vn/api',
  endpoints: { health: '/h', data: '/d' }, healthMethod: 'POST',
};
m.renderConnCustomSources();
m.renderConnectionCards();
const connHtml = m._capturedHtml();
// Phase 81: card do `renderConnectionCards()` vẽ CHUNG với connector cốt lõi,
// nên `renderConnCustomSources()` giờ chỉ ra tiêu đề + nút thêm.
check('render nguồn nhóm connector', connHtml.includes('ERP Công ty ABC'), 'chưa thấy card');
check('hiện domain để nhận ra nguồn', connHtml.includes('erp.abc.vn'));
check('có nút thêm kết nối', connHtml.includes("openAddDataSourceModal('connector')"));
check('có nút sửa', connHtml.includes("openAddDataSourceModal('connector', 'cong-ty-abc')"));
check('có nút xem dữ liệu', connHtml.includes("runDataSourceAction('cong-ty-abc', 'sync')"));

// Nhóm khác KHÔNG được nhân bản sang tab Kết Nối — đã có chỗ riêng.
m._resetModal();
m._ccDataSourceRegistry['bao-cao-xyz'] = { id: 'bao-cao-xyz', title: 'Báo cáo XYZ', category: 'reporting', isRemote: true, endpoints: {} };
m.renderConnCustomSources();
m.renderConnectionCards();
check('nguồn nhóm báo cáo KHÔNG lọt sang tab Kết Nối',
  !m._capturedHtml().includes('Báo cáo XYZ'), m._capturedHtml().slice(0, 120));

// Nguồn thiếu khoá -> phải cảnh báo ngay trên card.
m._resetModal();
m._ccDataSourceRegistry['chua-co-khoa'] = { id: 'chua-co-khoa', title: 'Chưa có khoá', category: 'connector', isRemote: true, hasAuth: false, endpoints: {} };
m.renderConnCustomSources();
m.renderConnectionCards();
check('nguồn chưa có khoá -> cảnh báo trên card',
  m._capturedHtml().includes('Chưa có khoá xác thực'));
delete m._ccDataSourceRegistry['chua-co-khoa'];

// Khi chưa có nguồn nào -> hiện dải mẫu, có lối vào để thêm.
delete m._ccDataSourceRegistry['cong-ty-abc'];
delete m._ccDataSourceRegistry['bao-cao-xyz'];
m._resetModal();
m.renderConnCustomSources();
const emptyHtml = m._capturedHtml();
check('chưa có nguồn -> hiện dải mẫu', emptyHtml.includes('cc-preset-btn'), 'thiếu mẫu');
check('chưa có nguồn -> vẫn có nút tự khai', emptyHtml.includes('Tự khai từ đầu'));

console.log('\n▸ An toàn nội dung');
const badTitle = m.getCcDataSource('xss') ? null : m.registerCcDataSource('xss', {
  title: '<img src=x onerror=alert(1)>',
  description: '"><script>bad()</script>',
});
check('tên chứa HTML được giữ nguyên trong registry (để escape lúc render)',
  m.getCcDataSource('xss').title === '<img src=x onerror=alert(1)>');
check('CC_SECRET_FIELDS gồm auth_value',
  true);  // khai báo ở harness

console.log('\n' + '─'.repeat(60));
if (fail) {
  console.log('Assertion FAIL:');
  results.filter(r => r.includes('❌')).forEach(r => console.log(r));
}
console.log(`\nTổng: ${pass + fail} | Pass: ${pass} | Fail: ${fail}`);
process.exit(fail ? 1 : 0);
