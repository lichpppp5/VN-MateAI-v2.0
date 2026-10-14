// SPDX-License-Identifier: Apache-2.0
// Copyright (c) 2026 Dương Thanh Lịch — VN-MateAI. See LICENSE and NOTICE.
// Kiểm thử hồi quy cho khối Phase 59/60 của Trung Tâm Chỉ Huy.
// Cắt riêng các hàm cần kiểm ra khỏi web/app.js rồi import động — tránh
// phải kéo cả app (WebSocket, Chart.js, DOM) vào môi trường Node.

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

// ── Cắt khối cần test ──────────────────────────────────────────────────────
function cut(startMark, endMark) {
  const a = src.indexOf(startMark);
  if (a < 0) throw new Error(`không tìm thấy: ${startMark}`);
  const b = src.indexOf(endMark, a);
  if (b < 0) throw new Error(`không tìm thấy end: ${endMark}`);
  return src.slice(a, b + endMark.length);
}

/**
 * Cắt một hằng `const X = ...;` bằng regex, bỏ qua nội dung bên trong.
 *
 * Vì sao không dùng `cut()`: nó khớp CHUỖI, nên mỗi lần thêm một sub-tab vào
 * `CC_SUBTABS` là mọi test cắt theo hằng đó hỏng cùng lúc — thêm mục mới xong
 * thì phải sửa lại hàng loạt test, và dễ quên. Ở đây khớp tên hằng + dấu
 * `=`, nên nội dung mảng tự do thay đổi mà test không cần biết.
 */
function cutConst(name, endMark) {
  const a = src.search(new RegExp('const ' + name + '\\s*='));
  if (a < 0) throw new Error('không tìm thấy hằng: ' + name);
  const b = src.indexOf(endMark, a);
  if (b < 0) throw new Error('không tìm thấy end: ' + endMark);
  return src.slice(a, b + endMark.length);
}

// Phase 81: CC_SUBTABS nay có thêm 'devices' (khối máy trạm tách thành
// sub-tab riêng). Cắt bằng regex tên hằng để lần sau thêm sub-tab nữa,
// test không phải sửa theo.
const subtab = cutConst('CC_SUBTABS', "    if (name === 'webhook') loadWebhookAlerts();\n  }\n}")

const secrets = cut(
  "const CC_SECRET_FIELDS =",
  "'webhook_url', 'hmac_secret', 'password'];",
);

const webhookCard = cut("const CC_WEBHOOK_SEVERITY = {", "    + `</div>`;\n}");

// Khung DOM tối thiểu + hàm phụ trợ mà app.js thực sự dùng.
const harness = `
  const window = globalThis;
const CC_CONNECTORS = ['aws', 'oci', 'paperless', 'einvoice'];
const API_BASE = '';
const CC_CONNECTORS_CONST = CC_CONNECTORS;
// Phase 81: runConnectorHealth / runDataSourceHealth ghi kết quả vào
// _ccConnHealth để renderConnectionCards() vẽ lại lưới không mất trạng thái.
// Harness dựng lại hàm từ app.js nên phải có hằng này, không thì hàm ném
// ReferenceError và test chết ngay, không báo đúng nguyên nhân.
// (Không dùng backtick trong khối harness: đây là template literal.)
const _ccConnHealth = new Map();
const $store = {};
function _ccGet(id) { return $store[id] || null; }
function _esc(v) { return String(v ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c])); }
// apiFetch có thể bị test bên dưới ghi đè qua globalThis.__apiFetch.
function apiFetch(u) {
  return globalThis.__apiFetch
    ? globalThis.__apiFetch(u)
    : Promise.resolve({ ok: true, json: async () => ({}) });
}
function getAuthToken() { return 't'; }
function showToast() {}
`;

// ── Test 1: switchCcSubTab ─────────────────────────────────────────────────
{
  const dom = ['cc-int-conn', 'cc-int-config', 'cc-int-webhook', 'cc-int-tools', 'cc-int-sys']
    .map(id => ({ id, hidden: true, classList: { toggle(c, on) { this.owner.hidden = on; }, contains() { return false; } } }));
  dom.forEach((d) => { d.classList.owner = d; });
  // 2026-10-12: 'config' và 'tools' gộp vào mục con "Kết nối" ('conn') — không còn nút riêng.
  const btns = ['conn', 'webhook', 'sys']
    .map(k => ({ dataset: { ccSubtab: k }, className: '' }));
  globalThis.document = {
    getElementById: (id) => dom.find(d => d.id === id) || null,
    querySelectorAll: (sel) => (sel === '[data-cc-subtab]' ? btns : []),
  };
  globalThis.loadConnectorConfigAll = () => {};
  globalThis.loadPluginRegistryStats = () => {};
  globalThis.loadBackgroundTasks = () => {};
  globalThis.loadWebhookAlerts = () => {};

  const mod = harness + subtab + '\nexport { switchCcSubTab, CC_SUBTABS, CC_SUBTAB_GROUPS };';
  const dir = mkdtempSync(join(tmpdir(), 'cc-sub-'));
  const f = join(dir, 'm.mjs');
  writeFileSync(f, mod);
  const { switchCcSubTab, CC_SUBTABS, CC_SUBTAB_GROUPS } = await import(pathToFileURL(f).href);

  results.push('\n▸ switchCcSubTab');
  // Phase 81/Enterprise: CC_SUBTABS có tối thiểu 6 sub-tab (nay có 8 sub-tab).
  check('có đủ các sub-tab', CC_SUBTABS.length >= 6, CC_SUBTABS.join(','));
  check('có sub-tab Máy Trạm', CC_SUBTABS.includes('devices'));
  let configLoads = 0;
  globalThis.loadConnectorConfigAll = () => { configLoads += 1; };
  switchCcSubTab('config');
  check('đường dẫn cũ "config" -> nút "Kết nối" (conn) được làm nổi bật',
    btns.find(b => b.dataset.ccSubtab === 'conn').className.includes('bg-primary-600'));
  check('"Kết nối" gom cả ba khối: danh sách, cấu hình, chạy thử công cụ',
    ['conn', 'config', 'tools'].every((k) => JSON.stringify(CC_SUBTAB_GROUPS[k]) === '["conn","config","tools"]') && !CC_SUBTAB_GROUPS.webhook);
  check('mở mục "Kết nối" -> gọi loadConnectorConfigAll', configLoads === 1);
  check('không còn nút riêng cho config / tools', CC_SUBTABS.includes('config') && !btns.some(b => ['config', 'tools'].includes(b.dataset.ccSubtab)));
  switchCcSubTab('bogus');
  check('tên sub-tab lạ bị bỏ qua (giữ nguyên tab đang mở)',
    btns.find(b => b.dataset.ccSubtab === 'conn').className.includes('bg-primary-600')
    && btns.filter(b => b.className.includes('bg-primary-600')).length === 1);

  switchCcSubTab('sys');
  check('bấm "sys" -> nút sys nổi bật',
    btns.find(b => b.dataset.ccSubtab === 'sys').className.includes('bg-primary-600'));
}

// ── Test 2: danh sách trường bí mật ───────────────────────────────────────
{
  results.push('\n▸ Danh sách trường bí mật');
  const mod = harness + secrets + '\nexport { CC_SECRET_FIELDS };';
  const dir = mkdtempSync(join(tmpdir(), 'cc-sec-'));
  const f = join(dir, 'm.mjs');
  writeFileSync(f, mod);
  const { CC_SECRET_FIELDS } = await import(pathToFileURL(f).href);

  // Kênh cảnh báo (2026-10-05): URL webhook Teams / Slack chính là khoá.
  for (const k of ['secret_access_key', 'api_token', 'client_secret', 'access_key_id',
                   'webhook_url', 'hmac_secret', 'password']) {
    check(`"${k}" được đánh dấu bí mật`, CC_SECRET_FIELDS.includes(k));
  }
  check('"region" KHÔNG bị đánh dấu bí mật', !CC_SECRET_FIELDS.includes('region'));
  check('"verify_ssl" KHÔNG bị đánh dấu bí mật', !CC_SECRET_FIELDS.includes('verify_ssl'));
  check('"provider" KHÔNG bị đánh dấu bí mật', !CC_SECRET_FIELDS.includes('provider'));
}

// ── Test 3: render webhook card ────────────────────────────────────────────
{
  results.push('\n▸ _ccWebhookCard');
  const mod = harness + webhookCard + '\nexport { _ccWebhookCard };';
  const dir = mkdtempSync(join(tmpdir(), 'cc-wh-'));
  const f = join(dir, 'm.mjs');
  writeFileSync(f, mod);
  const { _ccWebhookCard } = await import(pathToFileURL(f).href);

  const full = _ccWebhookCard({
    source: 'webhook:aws', severity: 'critical', title: 'EC2 chết',
    message: 'Máy chủ i-0abc đã dừng', resource_id: 'i-0abc', timestamp: null,
    metadata: { resource_id: 'i-0abc' },
  });
  check('severity critical -> nhãn "Nghịêm trọng"', full.includes('Nghịêm trọng'));
  check('source "webhook:aws" -> hiện "aws" (bỏ tiền tố)', full.includes('>aws<') && !full.includes('webhook:aws'));
  check('resource_id lấy từ metadata.resource_id', full.includes('i-0abc'));

  // Payload thiếu field — hàm cũ ném TypeError vì .toUpperCase() trên undefined.
  let threw = null, out = null;
  try { out = _ccWebhookCard({}); } catch (e) { threw = e; }
  check('payload rỗng KHÔNG ném lỗi', threw === null, threw && threw.message);
  check('payload rỗng -> hiện "(không có tiêu đề)"', typeof out === 'string' && out.includes('không có tiêu đề'));
  check('payload rỗng -> severity mặc định "Thấp"', typeof out === 'string' && out.includes('Thấp'));

  // Chống chèn HTML.
  const xss = _ccWebhookCard({ source: 'webhook:x', severity: 'high', title: '<img src=x onerror=alert(1)>', message: 'm' });
  check('title chứa HTML bị escape', !xss.includes('<img src=x') && xss.includes('&lt;img'));
}

// ── Test 4: trạng thái cấu hình connector phải trung thực ────────────────
// Trước đây dùng `Object.keys(block).length > 0` để kết luận "Đã cấu hình".
// Khối cấu hình luôn có sẵn region/base_url/provider… nên màn hình LUÔN hiện
// "Đã cấu hình" kể cả khi chưa có token nào, trong khi mọi lời gọi thật đều
// hỏng với "Authentication failed" — báo cáo thành công giả.
{
  results.push('\n▸ Trạng thái cấu hình connector');
  const loader = cut(
    '// ── Trạng thái cấu hình connector',
    "    _ccSetStatus(_ccGet(`${connectorName}-config-status`), false, 'Chưa cấu hình', `Lỗi tải: ${err.message}`);\n  }\n}",
  );

  // Chỉ cần ô trạng thái; harness `_ccGet` luôn trả null nên ta ghi lại
  // thông điệp trong `calls` thay vì đọc DOM.
  const calls = [];
  globalThis.document = { getElementById: () => null, querySelectorAll: () => [] };
  globalThis._ccSetStatus = (el, ok, okText, errText) => {
    calls.push({ ok, okText, errText });
  };

  // apiFetch giả: config CÓ region (nên "có khoá") nhưng health báo thiếu token.
  let healthResponse = {
    connectors: { aws: { configured: false, missing_fields: ['access_key_id', 'secret_access_key'] } },
  };
  let healthBroken = false;
  globalThis.__apiFetch = (url) => {
    if (String(url).includes('connectors/health')) {
      return healthBroken
        ? Promise.reject(new Error('mạng chết'))
        : Promise.resolve({ ok: true, json: async () => healthResponse });
    }
    return Promise.resolve({ ok: true, json: async () => ({ aws: { region: 'ap-southeast-1' } }) });
  };

  const mod = harness + loader + '\nexport { loadConnectorConfig };';
  const dir = mkdtempSync(join(tmpdir(), 'cc-cfg-'));
  const f = join(dir, 'm.mjs');
  writeFileSync(f, mod);
  const { loadConnectorConfig } = await import(pathToFileURL(f).href);

  // Cache trong app.js sống tới hết macrotask — nhường lượt để lần gọi sau
  // thấy được phản hồi mới, giống hành vi thật khi người dùng vừa lưu.
  const tick = () => new Promise((r) => setTimeout(r, 5));

  await loadConnectorConfig('aws');
  await tick();
  let last = calls[calls.length - 1];
  check('config CÓ region nhưng thiếu credential -> KHÔNG báo "Đã cấu hình"',
    last.ok === false, `ok=${last.ok} okText=${last.okText}`);
  check('nêu rõ khoá còn thiếu', String(last.errText).includes('access_key_id'), last.errText);
  check('nói rõ mọi lời gọi sẽ thất bại', String(last.errText).includes('thất bại'), last.errText);

  // Đủ credential -> báo "Đã cấu hình"
  healthResponse = { connectors: { aws: { configured: true, missing_fields: [] } } };
  await loadConnectorConfig('aws');
  await tick();
  last = calls[calls.length - 1];
  check('đủ credential -> báo "Đã cấu hình"', last.ok === true, JSON.stringify(last));

  // Không xác minh được (health lỗi) -> KHÔNG được khẳng định cả
  healthBroken = true;
  await loadConnectorConfig('aws');
  await tick();
  last = calls[calls.length - 1];
  check('không xác minh được -> nói thẳng "chưa xác minh được"',
    /chưa xác minh được/i.test(String(last.okText)), String(last.okText));

  // Health trả về nhưng KHÔNG có khoá missing_fields -> cũng không được
  // khẳng định "Đã cấu hình" một cách mù quáng.
  healthBroken = false;
  healthResponse = { connectors: { aws: { configured: true } } };
  await loadConnectorConfig('aws');
  await tick();
  last = calls[calls.length - 1];
  check('health thiếu khoá missing_fields -> coi như đủ, nhưng KHÔNG gọi tên khoá',
    last.ok === true && !String(last.errText).includes('Còn thiếu:'), JSON.stringify(last));
}

console.log(results.join('\n'));
console.log(`\nTổng: ${pass + fail} | Pass: ${pass} | Fail: ${fail}`);
process.exit(fail ? 1 : 0);
