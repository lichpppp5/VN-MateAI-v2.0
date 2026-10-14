// SPDX-License-Identifier: Apache-2.0
// Copyright (c) 2026 Dương Thanh Lịch — VN-MateAI. See LICENSE and NOTICE.
/**
 * tests/test_phase76_no_fake_hud.mjs
 * ==================================
 * Kiểm thử Phase 76 — Standby HUD chỉ hiện dữ liệu thật.
 *
 * HUD là màn hình phụ treo tường, đứng suốt ngày nên ai cũng liếc qua. Chính vì
 * vậy nó tích tụ nhiều tuyên bố không có nguồn hơn mọi màn hình khác trong hệ
 * thống, và người xem không có cách nào kiểm tra lại:
 *
 *   - "QUYỀN: ADMIN // TOÀN QUYỀN" + "ZERO-TRUST SENTINEL // ONLINE" ghi cứng
 *     trong HTML, và phía server còn trả `security_role: "ADMIN"` cho MỌI kết
 *     nối — kể cả khách chưa đăng nhập. Đây là payload telemetry broadcast
 *     chung, không biết ai đang xem, nên không thể gán vai trò vào đó.
 *   - "99.98% OPTIMAL", "HEALTH: 100% EXCELLENT", "FIREWALL OPTIMAL" — không
 *     tồn tại bộ đo nào đặt ra ba con số/từ này.
 *   - Số bịa khi mất dữ liệu: `?? 50` cho số kỹ năng, `?? 0` cho mạch âm
 *     thanh, "NVMe PRIMARY // OPTIMAL" khi không đọc được đĩa. Riêng `?? 50` là
 *     đặc biệt nguy hiểm vì số kỹ năng thật đang là 75 — 50 nhìn rất "hợp lý".
 *   - Hai chấm sáng "mục tiêu vệ tinh mô phỏng" vẽ tay trên radar, trong khi
 *     bảng bên cạnh lại ghi "TARGETS: 0 DETECTED" — tự mâu thuẫn với nhau.
 *   - 3 dòng log bịa có mốc thời gian giả [00:00:01]…[00:00:03], khẳng định
 *     "Neural Handshake established" và "Zero-Trust Sentinel active" trước khi
 *     có bất kỳ kết nối nào.
 *
 * Test này không chỉ grep. Nó **chạy thật** `applyMetrics()` và
 * `renderAuthStatus()` trên DOM giả, rồi đọc lại đúng thứ sẽ hiện ra màn hình.
 * Lý do: lỗi ở đây không phải "có chuỗi xấu" mà là "hàm vẫn chạy trơn tru nhưng
 * in ra số không có nguồn". Grep không bắt được kiểu đó.
 *
 * Ba tình huống phải phân biệt rõ:
 *   A. Chưa nhận được telemetry -> "chờ kết nối", KHÔNG bịa số.
 *   B. Nhận được, giá trị 0     -> hiện 0. "0 client kết nối" là sự thật đếm
 *                                 được, không phải thiếu dữ liệu.
 *   C. Nhận được, có số        -> hiện số thật.
 */

import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const read = (p) => fs.readFileSync(path.join(ROOT, p), 'utf8').replace(/\r\n/g, '\n');

const appJs = read('web/app.js');
const hudHtml = read('web/hud.html');
const hudJs = read('web/hud.js');

let pass = 0;
let fail = 0;
const failures = [];

function check(name, cond, detail = '') {
  if (cond) { pass++; console.log(`  ✅ ${name}`); }
  else { fail++; failures.push(`${name} — ${detail}`); console.log(`  ❌ ${name}  ${detail}`); }
}
function section(title) { console.log(`\n▸ ${title}`); }

/**
 * Mã thuần tuý: bỏ comment, để chú thích giải thích việc đã gỡ không báo động giả.
 * HUD có nhiều comment giải thích dài về chính những giá trị đã bị gỡ, nên nếu
 * không lọc comment thì mọi assertion "không còn chuỗi bịa" đều đỏ.
 */
function codeOnly(src) {
  return src
    .replace(/\/\*[\s\S]*?\*\//g, '')
    .replace(/^\s*\/\/.*$/gm, '')
    .replace(/\/\/.*$/gm, '');
}
const hudCode = codeOnly(hudJs);
// HTML: gỡ comment và khối script/style, còn lại là phần hiển thị thật
const hudMarkup = hudHtml
  .replace(/<!--[\s\S]*?-->/g, '')
  .replace(/<script\b[\s\S]*?<\/script>/gi, '')
  .replace(/<style\b[\s\S]*?<\/style>/gi, '');

// ──────────────────────────────────────────────────────────────────────
// DOM giả tối thiểu — đủ để các hàm thật chạy rồi ta đọc lại kết quả.
// ──────────────────────────────────────────────────────────────────────
function makeDom() {
  const els = new Map();

  function makeEl(id) {
    const classes = new Set();
    return {
      id,
      textContent: '',
      innerHTML: '',
      title: '',
      parentNode: null,
      style: {},
      classList: {
        add: (...c) => c.forEach((x) => classes.add(x)),
        remove: (...c) => c.forEach((x) => classes.delete(x)),
        contains: (c) => classes.has(c),
        toggle: (c, on) => {
          const want = on === undefined ? !classes.has(c) : !!on;
          want ? classes.add(c) : classes.delete(c);
        },
      },
    };
  }

  function byId(id) {
    if (!els.has(id)) els.set(id, makeEl(id));
    return els.get(id);
  }

  const document = {
    getElementById: byId,
    querySelector: (sel) => (sel && sel.startsWith('#') ? byId(sel.slice(1)) : null),
    querySelectorAll: () => [],
    addEventListener: () => {},
  };
  return { document, byId };
}

/**
 * Nạp các hàm cần kiểm từ web/hud.js.
 *
 * hud.js là IIFE lớn, khai báo canvas, âm thanh và gọi setInterval ngay khi nạp —
 * không chạy được nguyên file trong Node. Nên ta cắt riêng khối cần kiểm:
 * phần đầu (hằng + helper "chờ kết nối") và các hàm applyMetrics /
 * renderAuthStatus. Chúng không chạm canvas, nên chạy thật được.
 */
function loadHud() {
  const helpers = (() => {
    const start = hudJs.indexOf('const WAIT_TXT');
    if (start < 0) return '';
    const fn = hudJs.indexOf('function _setLiveText(');
    if (fn < 0) return '';
    // Cân bằng ngoặc nhọn từ ngoài vào: _setLiveText nhận tham số dạng
    // { waiting = WAIT_TXT } nên phải nhảy qua khối tham số trước.
    let i = hudJs.indexOf('(', fn), depth = 0;
    for (; i < hudJs.length; i++) {
      if (hudJs[i] === '(') depth++;
      else if (hudJs[i] === ')') { depth--; if (depth === 0) break; }
    }
    const body = hudJs.indexOf('{', i);
    depth = 0;
    let k = body;
    for (; k < hudJs.length; k++) {
      if (hudJs[k] === '{') depth++;
      else if (hudJs[k] === '}') { depth--; if (depth === 0) { k++; break; } }
    }
    return hudJs.slice(start, k);
  })();

  const grab = (name) => {
    const at = hudJs.indexOf(`function ${name}(`);
    if (at < 0) return null;
    let i = hudJs.indexOf('(', at), depth = 0;
    for (; i < hudJs.length; i++) {
      if (hudJs[i] === '(') depth++;
      else if (hudJs[i] === ')') { depth--; if (depth === 0) break; }
    }
    const body = hudJs.indexOf('{', i);
    depth = 0;
    for (let k = body; k < hudJs.length; k++) {
      if (hudJs[k] === '{') depth++;
      else if (hudJs[k] === '}') { depth--; if (depth === 0) return hudJs.slice(at, k + 1); }
    }
    return null;
  };

  const applyMetrics = grab('applyMetrics');
  const renderAuthStatus = grab('renderAuthStatus');
  if (!applyMetrics || !renderAuthStatus) return null;

  const { document, byId } = makeDom();
  // Những phần tử applyMetrics/renderAuthStatus chạm tới — trỏ vào DOM giả.
  const IDS = [
    'metric-cpu-text', 'metric-cpu-circle', 'metric-cpu-bar', 'metric-cores-text',
    'metric-procs-text', 'metric-ram-text', 'metric-ram-circle', 'metric-ram-bar',
    'metric-ram-gb', 'metric-ram-total', 'metric-disk-text', 'metric-disk-bar',
    'metric-disk-free', 'metric-clients-count', 'metric-audio-nodes',
    'metric-skills-count', 'metric-net-io', 'hud-permission-badge',
    'hud-security-status', 'hud-voice-stream',
  ];
  const el = Object.fromEntries(IDS.map((id) => [id.replace(/-([a-z])/g, (_, c) => c.toUpperCase()), byId(id)]));

  const factory = new Function(
    'document', 'el',
    `${helpers}
const CIRCLE_CIRCUMFERENCE = 238.76;
const STATES = { LISTENING: {}, SPEAKING: {}, IDLE: {} };
let currentAuth = { known: false, authenticated: false, role: null, username: null };
const cpuTextEl = el.metricCpuText, cpuBarEl = el.metricCpuBar, cpuCircleEl = el.metricCpuCircle;
const coresTextEl = el.metricCoresText, procsTextEl = el.metricProcsText;
const ramTextEl = el.metricRamText, ramBarEl = el.metricRamBar, ramCircleEl = el.metricRamCircle;
const ramGbEl = el.metricRamGb, ramTotalEl = el.metricRamTotal;
const diskTextEl = el.metricDiskText, diskBarEl = el.metricDiskBar, diskFreeEl = el.metricDiskFree;
const clientsCountEl = el.metricClientsCount, audioNodesEl = el.metricAudioNodes;
const skillsCountEl = el.metricSkillsCount, netIoEl = el.metricNetIo;
const permBadgeEl = el.hudPermissionBadge, secStatusEl = el.hudSecurityStatus;
${applyMetrics}
${renderAuthStatus}
renderAuthStatus();
return {
  WAIT_TXT, _isLive, _live, _liveNum, _setLiveText,
  applyMetrics, renderAuthStatus,
  setAuth: (a) => { currentAuth = a; renderAuthStatus(); },
};`
  );

  const api = factory(document, el);
  return { api, byId, text: (id) => byId(id).textContent, html: (id) => byId(id).innerHTML };
}

// Dữ liệu telemetry THẬT (lấy từ /ws/hud trên máy đang chạy, CSDL đã dọn trống).
const REAL_TELEMETRY = {
  cpu_percent: 23.9, cpu_cores: 10, cpu_freq_mhz: 3204,
  ram_percent: 76.4, ram_used_gb: 11.7, ram_total_gb: 16.0,
  disk_percent: 25.2, disk_free_gb: 34.75, disk_total_gb: 228.27,
  processes_count: 412, connected_clients: 0, active_audio_hardware: 0,
  active_web_clients: 0, skills_count: 75, skills_enabled: 74,
  net_sent_mbps: 0.0, net_recv_mbps: 0.012,
};

// ──────────────────────────────────────────────────────────────────────
section('Không còn tuyên bố không có nguồn trong phần hiển thị');

// Những chuỗi này KHÔNG có bộ đo nào đặt ra. Quét cả markup lẫn mã JS.
const UNSOURCED = [
  ['ADMIN', 'vai trò admin ghi cứng cho mọi kết nối, kể cả chưa đăng nhập'],
  ['TOÀN QUYỀN', 'khẳng định toàn quyền khi không biết phiên nào đang xem'],
  ['99.98', 'tỷ lệ 99.98% không có bộ đo nào tính'],
  ['FIREWALL', 'hệ thống không giám sát tường lửa'],
  ['48-ALPHA', 'mã lưới khu vực bịa'],
  ['COORDINATES', 'tọa độ địa lý bịa, không có nguồn GPS'],
  ['NVMe', 'khẳng định loại đĩa mà không ai kiểm tra loại nào'],
  ['TARGETS:', 'bảng đếm mục tiêu bịa'],
  ['LOCKED', 'trạng thái khóa mục tiêu bịa'],
  ['ZERO-LATENCY', 'không đo độ trễ nào cả'],
  ['UNRESTRICTED', 'khẳng định không có hạn chế'],
  ['satellite', 'mục tiêu vệ tinh mô phỏng trên radar'],
  ['simulated', 'dữ liệu mô phỏng'],
];
for (const [needle, why] of UNSOURCED) {
  check(
    `không còn "${needle}" — ${why}`,
    !hudMarkup.includes(needle) && !hudCode.includes(needle),
    'vẫn nằm trong phần sẽ hiện lên màn hình'
  );
}

// Số liệu bịa nhúng thẳng trong HTML, hiện ra trước cả khi JS chưa chạy.
const FAKE_NUMBERS = ['35.4%', '83.4%', '42.0%', '60.0 FPS', '50+ TÍCH HỢP', '100% EXCELLENT'];
for (const n of FAKE_NUMBERS) {
  check(
    `không còn số bịa "${n}" trong HTML`,
    !hudMarkup.includes(n),
    'hiện ngay từ đầu, trước khi có bất kỳ số liệu thật nào'
  );
}

// "60 FPS" là lớp bịa khác: nằm trong CHUỖI LOG, tức nằm trong danh sách sự
// kiện mà người đọc tin là đã xảy ra. Người dùng thấy nó nằm cùng hàng với
// "Neural Link established" và tưởng máy đang chạy ổn định 60 hình/giây —
// trong khi ô FPS bên cạnh có thể đang là 30 hay 45. Số khung hình được đo
// ở vòng lặp render, nên log không được ghi số trước khi đo.
check(
  'log khởi tạo không ghi cứng "60 FPS"',
  !/60\s*FPS/.test(hudCode),
  'khung hình thực tế được đo sau, không ghi trước số bịa'
);
check(
  'log khởi tạo nói rõ giao diện đã khởi tạo, không kèm số đo',
  /HUD khởi tạo xong/.test(hudCode),
  'giữ lại sự kiện thật (giao diện đã nạp), bỏ phần số bịa'
);

// Mọi chuỗi hiển thị có số + đơn vị đứng riên đều đáng nghi: số đo thật luôn
// đến từ biến, không bao giờ viết cứng trong chuỗi.
const hardcodedMeasures = [...hudCode.matchAll(/(['"`])([^'"`\n]{4,160}?)\1/g)]
  .map((m) => m[2])
  .filter((t) => /\b\d{1,3}(?:\.\d+)?\s*(?:%|FPS|ms|GB|MB)\b/.test(t))
  .filter((t) => !t.includes('${') && !t.includes('toFixed'));
check(
  'không chuỗi nào ghi cứng số đo có đơn vị (%, FPS, ms, GB, MB)',
  hardcodedMeasures.length === 0,
  hardcodedMeasures.map((t) => JSON.stringify(t)).join(', ')
);

// 3 dòng log bịa với mốc thời gian giả.
check(
  'không còn 3 dòng log bịa có mốc [00:00:01]…[00:00:03]',
  !/\[00:00:0\d\]/.test(hudMarkup),
  'khẳng định sự kiện chưa từng xảy ra, kèm giờ giả'
);
check(
  'không còn dòng "Zero-Trust Sentinel active. Guard normal."',
  !hudMarkup.includes('Zero-Trust Sentinel active'),
  'tuyên bố hệ thống bảo mật đang hoạt động khi chưa kiểm tra gì'
);
check(
  'không còn dòng "WebSocket Neural Handshake established." viết cứng',
  !hudMarkup.includes('Neural Handshake established'),
  'ghi trước khi WebSocket có kết nối hay không'
);

check('có ô chờ duy nhất trong khung log', /id="hud-log-placeholder"/.test(hudHtml));
check(
  'ô chờ trong khung log nói "chờ kết nối"',
  /hud-log-placeholder[\s\S]{0,200}chờ kết nối/.test(hudMarkup)
);

// ──────────────────────────────────────────────────────────────────────
section('Quy ước "chờ kết nối" giống hệt app.js');

/** Trích nguyên văn một hàm cấp module (bỏ thụt đầu dòng để so sánh nội dung). */
function extractFn(src, name) {
  const start = src.indexOf(`function ${name}(`);
  if (start < 0) return null;
  let i = src.indexOf('(', start), depth = 0;
  for (; i < src.length; i++) {
    if (src[i] === '(') depth++;
    else if (src[i] === ')') { depth--; if (depth === 0) break; }
  }
  const body = src.indexOf('{', i);
  if (body < 0) return null;
  depth = 0;
  for (let k = body; k < src.length; k++) {
    if (src[k] === '{') depth++;
    else if (src[k] === '}') { depth--; if (depth === 0) return src.slice(start, k + 1); }
  }
  return null;
}
const normalize = (s) => (s === null ? null : s.split('\n').map((l) => l.trim()).filter(Boolean).join('\n'));

for (const name of ['_isLive', '_live', '_liveNum', '_setLiveText']) {
  const a = normalize(extractFn(appJs, name));
  const h = normalize(extractFn(hudJs, name));
  check(`hàm ${name} khớp từng ký tự với app.js`, a !== null && a === h, 'sửa một bên mà quên bên kia là lệch hành vi');
}
check(
  'dùng cùng một câu WAIT_TXT',
  /const WAIT_TXT = 'chờ kết nối'/.test(hudJs)
);
check('có CSS .is-waiting để làm mờ ô chờ', /\.is-waiting\s*\{/.test(hudHtml));

// ──────────────────────────────────────────────────────────────────────
section('Ô trong HTML bắt đầu ở trạng thái chờ, không phải số bịa');

// Mỗi ô lấy dữ liệu từ server đều phải nói "chờ kết nối" ngay từ đầu, để
// không có khoảnh khắc nào người xem tưởng đã có số liệu.
const WAIT_FROM_SERVER = [
  ['hud-conn-text', 'chờ kết nối'],
  ['hud-security-status', 'chờ kết nối'],
  ['hud-permission-badge', 'chờ kết nối'],
  ['hud-voice-stream', 'chờ kết nối'],
  ['metric-clients-count', 'chờ kết nối'],
  ['metric-audio-nodes', 'chờ kết nối'],
  ['metric-skills-count', 'chờ kết nối'],
];
/**
 * Lấy nguyên khối HTML bắt đầu từ thẻ mang `id` cho tới thẻ đóng CÙNG cấp.
 *
 * Regex `[\s\S]*?</` là không đủ: metric-net-io chứa một <span> con làm chấm
 * trạng thái, nên khớp tới thẻ đóng kế tiếp sẽ thu được chuỗi rỗng và báo
 * động giả. Ở đây ta đếm thẻ mở/đóng thật.
 */
function elementBlock(id) {
  const at = hudHtml.search(new RegExp(`<(\\w+)[^>]*id="${id}"`));
  if (at < 0) return null;
  const tag = hudHtml.slice(at + 1).match(/^\w+/)[0];
  let depth = 0;
  for (let i = at; i < hudHtml.length; i++) {
    const open = hudHtml.startsWith(`<${tag}`, i);
    const close = hudHtml.startsWith(`</${tag}>`, i);
    if (open) depth++;
    if (close && --depth === 0) return hudHtml.slice(at, i + tag.length + 3);
  }
  return null;
}

for (const [id, want] of WAIT_FROM_SERVER) {
  const block = elementBlock(id);
  const inner = block ? block.replace(/<[^>]+>/g, ' ').replace(/\s+/g, ' ').trim() : '';
  check(
    `${id} mở đầu bằng "${want}"`,
    inner.includes(want) && (block || '').includes('is-waiting'),
    `thấy ${JSON.stringify(inner.slice(0, 60))}`
  );
}

// HUD "buồng lái AI" (2026-10-12): số liệu lấy từ API do lớp hình web/hud-visual.js điền. Mọi ô bắt đầu bằng dấu
// "–" (chưa có số), đồng hồ máy chủ khởi đầu ở 0% — không vẽ sẵn vòng 35% hay thanh 42% rồi mới đo lại.
const visualJs = read('web/hud-visual.js');
const DASH_PLACEHOLDER = [
  ['ai-tokens', 'token đã dùng'],
  ['ai-calls', 'lượt gọi model'],
  ['ai-voice', 'thời gian bắt đầu trả lời'],
  ['host-net', 'lưu lượng mạng máy chủ'],
];
for (const [id, label] of DASH_PLACEHOLDER) {
  const m = hudHtml.match(new RegExp(`id="${id}"[^>]*>([^<]*)<`));
  check(
    `${id} (${label}) mở đầu bằng "–"`,
    m !== null && m[1].trim() === '–',
    m ? `thấy ${JSON.stringify(m[1].trim())}` : 'không tìm thấy phần tử'
  );
}
for (const id of ['t-incidents', 't-approvals', 't-active', 't-success']) {
  check(`ô ${id} mở đầu bằng "–"`, new RegExp(`id="${id}"><div class="v">–</div>`).test(hudHtml));
}
check(
  'đồng hồ CPU / RAM / ổ đĩa khởi đầu ở 0%, không vẽ sẵn số',
  /gauge\('g-cpu', 0, 'CPU'\); gauge\('g-ram', 0, 'RAM'\); gauge\('g-disk', 0, 'Ổ đĩa'\);/.test(visualJs)
);
check(
  'lớp hình không còn chế độ DEMO với số liệu mô phỏng',
  !/toDemo|DEMO_AGENTS|DEMO_TALK|số liệu mô phỏng/.test(visualJs),
  'HUD chính chưa đăng nhập phải để trống ô số liệu, không hiện số bịa'
);
check(
  'chưa đăng nhập: ghi rõ cần đăng nhập, ô số liệu về "–"',
  /function toLogin\(/.test(visualJs) && /tile\(id, '–', ''\)/.test(visualJs)
);

// ──────────────────────────────────────────────────────────────────────
section('applyMetrics(): 0 là số thật, thiếu dữ liệu mới là "chờ kết nối"');

const hud = loadHud();
check('nạp được applyMetrics và renderAuthStatus từ web/hud.js', hud !== null);
check('hàm trả ra được WAIT_TXT', hud && hud.api.WAIT_TXT === 'chờ kết nối');

if (hud) {
  const { api, text, html, byId } = hud;

  // ── A: chưa có telemetry -> mọi ô phải nói "chờ kết nối"
  api.applyMetrics({});
  check('chưa có dữ liệu: CPU nói "--"', text('metric-cpu-text') === '--', text('metric-cpu-text'));
  check('chưa có dữ liệu: RAM nói "--"', text('metric-ram-text') === '--', text('metric-ram-text'));
  check('chưa có dữ liệu: đĩa nói "chờ kết nối"', text('metric-disk-text') === 'chờ kết nối', text('metric-disk-text'));
  check('chưa có dữ liệu: dung lượng trống nói "chờ kết nối"', text('metric-disk-free') === 'FREE: chờ kết nối', text('metric-disk-free'));
  check('chưa có dữ liệu: số kỹ năng nói "chờ kết nối"', text('metric-skills-count') === 'chờ kết nối', text('metric-skills-count'));
  check('chưa có dữ liệu: client nói "chờ kết nối"', text('metric-clients-count') === 'chờ kết nối', text('metric-clients-count'));
  check('chưa có dữ liệu: mạch âm thanh nói "chờ kết nối"', text('metric-audio-nodes') === 'chờ kết nối', text('metric-audio-nodes'));
  check('chưa có dữ liệu: RAM GB nói "chờ kết nối"', text('metric-ram-gb') === 'USED: chờ kết nối', text('metric-ram-gb'));
  check('chưa có dữ liệu: số tiến trình nói "PROCS: --"', text('metric-procs-text') === 'PROCS: --', text('metric-procs-text'));
  check('chưa có dữ liệu: số nhân nói "CORES: --"', text('metric-cores-text') === 'CORES: --', text('metric-cores-text'));
  check('chưa có dữ liệu: mạng nói "chờ kết nối"', /chờ kết nối/.test(html('metric-net-io')), html('metric-net-io'));

  // Không ô nào được giữ số của lần đo trước — server gửi null là phải về
  // trạng thái chờ, không đứng lại số cũ như số liệu tối nay.
  api.applyMetrics(REAL_TELEMETRY);
  const cpuBefore = text('metric-cpu-text');
  api.applyMetrics({ cpu_percent: null });
  check(
    'server gửi null thì ô quay lại chờ, không giữ số cũ đọng lại',
    text('metric-cpu-text') === '--' && cpuBefore === '23.9%',
    `trước=${cpuBefore} sau=${text('metric-cpu-text')}`
  );

  // ── C: có telemetry thật -> hiện số thật
  api.applyMetrics(REAL_TELEMETRY);
  check('CPU hiện số thật 23.9%', text('metric-cpu-text') === '23.9%', text('metric-cpu-text'));
  check('RAM hiện số thật 76.4%', text('metric-ram-text') === '76.4%', text('metric-ram-text'));
  check('đĩa hiện số thật 25.2%', text('metric-disk-text') === '25.2%', text('metric-disk-text'));
  check(
    'dung lượng trống hiện số thật kèm tổng dung lượng',
    text('metric-disk-free') === 'FREE: 34.75 GB / 228.27 GB',
    text('metric-disk-free')
  );
  check('số kỹ năng hiện 75 (không phải 50 bịa)', text('metric-skills-count') === '75 SKILLS ACTIVE', text('metric-skills-count'));
  check('số tiến trình hiện 412', text('metric-procs-text') === 'PROCS: 412', text('metric-procs-text'));
  check('số nhân hiện 10', text('metric-cores-text') === 'CORES: 10', text('metric-cores-text'));
  check('RAM GB hiện số thật', text('metric-ram-gb') === 'USED: 11.7 GB', text('metric-ram-gb'));
  check('mạng hiện lưu lượng thật', /NET: .*0\.0 MB\/s .*0\.0 MB\/s/.test(html('metric-net-io')), html('metric-net-io'));
  check(
    'vòng cung CPU vẽ theo số thật',
    Math.abs(parseFloat(byId('metric-cpu-circle').style.strokeDashoffset) - 238.76 * (1 - 23.9 / 100)) < 0.01,
    `dashoffset=${byId('metric-cpu-circle').style.strokeDashoffset}`
  );
  check(
    'ô đã có dữ liệu thì bỏ class is-waiting',
    !byId('metric-cpu-text').classList.contains('is-waiting')
      && byId('metric-skills-count').classList.contains('is-waiting') === false
  );

  // ── B: giá trị 0 là số đo thật, KHÔNG được đổi thành "chờ kết nối"
  const zeroTelemetry = { ...REAL_TELEMETRY, connected_clients: 0, active_audio_hardware: 0, net_sent_mbps: 0 };
  api.applyMetrics(zeroTelemetry);
  check(
    '0 client kết nối vẫn hiện "0 ACTIVE" (đếm được, không phải thiếu dữ liệu)',
    text('metric-clients-count') === '0 ACTIVE',
    text('metric-clients-count')
  );
  check(
    '0 mạch âm thanh vẫn hiện "0 THIẾT BỊ"',
    text('metric-audio-nodes') === '0 THIẾT BỊ',
    text('metric-audio-nodes')
  );
  check(
    '0 KB/s mạng vẫn hiện số, không phải "chờ kết nối"',
    !/chờ kết nối/.test(html('metric-net-io')),
    html('metric-net-io')
  );
  check(
    '0 client không bị gắn class is-waiting',
    !byId('metric-clients-count').classList.contains('is-waiting')
  );

  // Không ô nào được tự chế ra số khi thiếu dữ liệu (3 kiểu bịa trước đây).
  api.applyMetrics({});
  const allWaiting = ['metric-clients-count', 'metric-audio-nodes', 'metric-skills-count']
    .every((id) => byId(id).classList.contains('is-waiting'));
  check('thiếu dữ liệu thì các ô đều được gắn is-waiting', allWaiting);
  check(
    'không ô nào rơi về giá trị số tự chế (0, 50, 45)',
    !/^(0|50|45)\b/.test(text('metric-skills-count')) && !/^0\b/.test(text('metric-audio-nodes'))
  );
}

// ──────────────────────────────────────────────────────────────────────
section('Vai trò lấy từ máy chủ xác thực, không ghi cứng');

// hud.js phải tự gọi renderAuthStatus() lúc nạp, nếu không ô quyền sẽ giữ
// nguyên chữ ghi trong HTML cho tới gói đầu tiên tới — tức vẫn hiện sai trong
// khoảnh khắc người dùng mở màn hình. Khẳng định này bảo đảm lời gọi trong
// khung chạy của test khớp với thứ file thật làm.
check(
  'hud.js gọi renderAuthStatus() ngay khi nạp trang',
  /function renderAuthStatus\(\)[\s\S]*?\n  \}\n\n  renderAuthStatus\(\);/.test(hudCode),
  'thiếu lời gọi lúc nạp thì ô quyền hiện sai trước gói đầu tiên'
);

if (hud) {
  const { api, text } = hud;

  // Mới mở trang, chưa nhận gói welcome nào -> không biết mình là ai.
  check(
    'chưa nhận gói nào thì badge quyền nói "chờ kết nối"',
    text('hud-permission-badge') === 'QUYỀN: chờ kết nối',
    text('hud-permission-badge')
  );
  check(
    'chưa nhận gói nào thì dòng bảo mật nói "chờ kết nối"',
    text('hud-security-status') === 'chờ kết nối',
    text('hud-security-status')
  );

  // Đã xác thực -> hiện đúng vai trò máy chủ gửi.
  api.setAuth({ known: true, authenticated: true, role: 'admin', username: 'admin' });
  check(
    'xác thực role=admin thì hiện QUYỀN: ADMIN',
    text('hud-permission-badge') === 'QUYỀN: ADMIN',
    text('hud-permission-badge')
  );
  check(
    'dòng bảo mật nêu rõ đã xác thực với tên người dùng',
    text('hud-security-status') === 'ĐÃ XÁC THỰC (admin)',
    text('hud-security-status')
  );

  api.setAuth({ known: true, authenticated: true, role: 'viewer', username: 'khach' });
  check(
    'xác thực role=viewer thì hiện QUYỀN: VIEWER, không phải ADMIN',
    text('hud-permission-badge') === 'QUYỀN: VIEWER',
    text('hud-permission-badge')
  );

  // Chưa đăng nhập -> nói thẳng là chưa xác thực, không đoán.
  api.setAuth({ known: true, authenticated: false, role: null, username: null });
  check(
    'chưa xác thực thì nói "chưa xác thực", KHÔNG mặc định ADMIN',
    text('hud-permission-badge') === 'QUYỀN: chưa xác thực',
    text('hud-permission-badge')
  );
  check(
    'chưa xác thực thì nói rõ chỉ xem được',
    /CHƯA XÁC THỰC/.test(text('hud-security-status')),
    text('hud-security-status')
  );
  check(
    'chưa xác thực thì ô quyền được gắn is-waiting',
    hud.byId('hud-permission-badge').classList.contains('is-waiting')
  );

  // Mất kết nối -> về chờ, không giữ vai trò của phiên đã rớt.
  api.setAuth({ known: false, authenticated: false, role: null, username: null });
  check(
    'mất kết nối thì về "chờ kết nối" chứ không giữ vai trò cũ',
    text('hud-permission-badge') === 'QUYỀN: chờ kết nối',
    text('hud-permission-badge')
  );
}

// Mất WebSocket phải gỡ vai trò, không giữ lại "ADMIN" của phiên trước.
check(
  'onclose gọi renderAuthStatus() để xoá vai trò',
  /hudSocket\.onclose[\s\S]{0,700}renderAuthStatus\(\)/.test(hudCode),
  'giữ lại vai trò của phiên đã rớt là hiện quyền hạn không còn'
);
check(
  'gói hud_welcome ghi vai trò máy chủ gửi vào currentAuth',
  /type === 'hud_welcome'[\s\S]{0,500}packet\.role/.test(hudCode),
  'vai trò phải lấy từ máy chủ chứ không tự chế'
);
check(
  'gói auth_required đánh dấu chưa xác thực',
  /type === 'auth_required'[\s\S]{0,300}authenticated: false/.test(hudCode)
);

// ──────────────────────────────────────────────────────────────────────
section('Không còn radar / mục tiêu vẽ tay — hiệu ứng nằm riêng ở lớp hình');

check(
  'hud.js không còn bộ vẽ radar (từng vẽ hai "mục tiêu" đặt cứng toạ độ)',
  !/radarCtx|renderRadar/.test(hudCode),
  'hai chấm sáng đặt cứng toạ độ, không đến từ dữ liệu nào'
);
check(
  'không còn biến thời gian t1 dùng cho mục tiêu giả',
  !/const t1 = \(timestamp/.test(hudCode)
);
check(
  'tia trên chòm sao agent chỉ đến từ sự kiện topology thật (bỏ sự kiện mô phỏng)',
  /if \(!e\.simulated\) onTopology\(e, first\)/.test(visualJs)
);

// ──────────────────────────────────────────────────────────────────────
section('Nhãn "luồng giọng nói" bám theo trạng thái thật');

check(
  'không còn ghi cứng "VOICE STREAM ACTIVE" trong HTML',
  !/VOICE STREAM ACTIVE/.test(hudMarkup),
  'tuyên bố có luồng âm thanh dù mic chưa từng bật'
);
check(
  'nhãn luồng giọng nói được cập nhật theo trạng thái',
  /VOICE STREAM ACTIVE/.test(hudCode) && /VOICE STREAM IDLE/.test(hudCode)
);

// ──────────────────────────────────────────────────────────────────────
console.log('');
if (fail > 0) {
  console.log('❌ CÓ THẤT BẠI:');
  for (const f of failures) console.log(`   - ${f}`);
  console.log('');
}
console.log(`Tổng: ${pass + fail} | Pass: ${pass} | Fail: ${fail}`);
if (fail === 0) console.log('\n✅ TẤT CẢ PASS');
process.exit(fail === 0 ? 0 : 1);
