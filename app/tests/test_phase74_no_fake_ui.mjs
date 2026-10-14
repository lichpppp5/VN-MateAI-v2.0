// SPDX-License-Identifier: Apache-2.0
// Copyright (c) 2026 Dương Thanh Lịch — VN-MateAI. See LICENSE and NOTICE.
/**
 * tests/test_phase74_no_fake_ui.mjs
 * ==================================
 * Kiểm thử Phase 74 — giao diện chỉ hiện dữ liệu thật.
 *
 * Ba nhóm lỗi mà mắt thường không thấy, vì khi "có số hiện" thì trông rất thuyết phục:
 *
 *  1. Số bịa ghi thẳng trong HTML. Nếu JS lỗi và không tô được, người đọc vẫn
 *     thấy "31 kỹ năng", "~240ms", "BẬT (Hoạt động)" — những thứ không có nguồn.
 *
 *  2. Số bịa nằm trong fallback của JS. `x || 37` hay `x ?? 31` chỉ lộ ra đúng lúc
 *     server im lặng, tức đúng lúc người đọc đang cần con số nhất.
 *
 *  3. "Không rõ" bị đổi thành trạng thái tốt. `res.risk || 'SAFE'` là fail-open:
 *     thiếu dữ liệu thì hệ thống bảo "AN TOÀN" — ngược hẳn nguyên tắc.
 *
 * Quy ước kiểm thử ở đây: mọi ô số liệu đều phải có một nguồn thật hoặc phải
 * nói "chờ kết nối". Không chấp nhận trạng thái trung gian "không rõ nhưng
 * trông như có số".
 */

import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const read = (p) => fs.readFileSync(path.join(ROOT, p), 'utf8').replace(/\r\n/g, '\n');

const appJs = read('web/app.js');
const indexHtml = read('web/index.html');

let pass = 0;
let fail = 0;
const failures = [];

function check(name, cond, detail = '') {
  if (cond) {
    pass++;
    console.log(`  ✅ ${name}`);
  } else {
    fail++;
    failures.push(`${name} — ${detail}`);
    console.log(`  ❌ ${name}  ${detail}`);
  }
}

function section(title) {
  console.log(`\n▸ ${title}`);
}

// ──────────────────────────────────────────────────────────────────────
section('Quy ước "chờ kết nối" tồn tại và phân biệt được 0 với dữ liệu thiếu');

check('có hằng WAIT_TXT', /const WAIT_TXT\s*=\s*'chờ kết nối'/.test(appJs));
check('có _isLive() phân biệt thiếu dữ liệu', /function _isLive\(/.test(appJs));
check('có _live() trả giá trị thật khi có', /function _live\(/.test(appJs));
check('có _liveNum() kèm đơn vị', /function _liveNum\(/.test(appJs));
check('có _setLiveText() gắn class is-waiting', /function _setLiveText\(/.test(appJs));

// Hàm việc thật: 0 là số đo hợp lệ, null/undefined/NaN/rỗng thì không.
// Cắt nguyên khối helper (từ `const WAIT_TXT` tới hết `_setLiveText`) rồi chạy
// thật — đếm dấu ngoặc từng hàm sẽ vướng `{}` trong tham số destructuring.
{
  const start = appJs.indexOf('const WAIT_TXT');
  const endMarker = appJs.indexOf('function _setLiveText(');
  const end = appJs.indexOf('\n}', endMarker);
  if (start >= 0 && endMarker > start && end > endMarker) {
    const src = appJs.slice(start, end + 2).replace(/^\s*\/\/.*$/gm, '');
    const { _isLive, _live, _liveNum } = new Function(
      `${src}\nreturn { _isLive, _live, _liveNum };`
    )();
    const WAIT_TXT = 'chờ kết nối';

    check('_isLive(0) là đúng — 0 là số đo thật', _isLive(0) === true);
    check('_isLive("0") là đúng', _isLive('0') === true);
    check('_isLive(null) là sai', _isLive(null) === false);
    check('_isLive(undefined) là sai', _isLive(undefined) === false);
    check('_isLive("") là sai', _isLive('') === false);
    check('_isLive(NaN) là sai', _isLive(NaN) === false);
    check('_isLive(Infinity) là sai', _isLive(Infinity) === false);

    check('_live(0) giữ nguyên 0, KHÔNG đổi thành "chờ kết nối"', _live(0) === 0, `nhận ${JSON.stringify(_live(0))}`);
    check('_live(null) trả "chờ kết nối"', _live(null) === WAIT_TXT);
    check('_live(undefined) trả "chờ kết nối"', _live(undefined) === WAIT_TXT);

    check('_liveNum(32.4, {digits:1, unit:"%"}) = "32.4%"', _liveNum(32.4, { digits: 1, unit: '%' }) === '32.4%');
    check('_liveNum(32, {digits:1, unit:"%"}) = "32.0%"', _liveNum(32, { digits: 1, unit: '%' }) === '32.0%');
    check('_liveNum(null) kèm đơn vị vẫn là "chờ kết nối" (không sinh "%" rỗng)', _liveNum(null, { unit: '%' }) === WAIT_TXT);
    check('_liveNum(0, {unit:"%"}) = "0%" (0 là thật)', _liveNum(0, { unit: '%' }) === '0%');
    check('_liveNum("abc") = "chờ kết nối"', _liveNum('abc') === WAIT_TXT);
  } else {
    check('cắt được khối helper để chạy thử', false, `start=${start} endMarker=${endMarker} end=${end}`);
  }
}

check('CSS có .is-waiting để làm mờ ô chờ', /\.is-waiting\s*\{/.test(indexHtml));

// ──────────────────────────────────────────────────────────────────────
section('Không còn số liệu bịa nằm thẳng trong HTML');

// (id, giá trị cũ từng hiện sẵn khi JS lỗi)
const FAKE_LITERALS = [
  ['header-skills-count', '31'],
  ['svc-skills-tag', '31 Skills'],
  ['svc-skills-detail', '31 kỹ năng đã nạp vào runtime'],
  ['node-skills-count', '31'],
  ['node-skills-active', '31 đang bật'],
  ['skills-stat-total', '37 SKILLS'],
  ['skills-stat-active', '37 HOẠT ĐỘNG'],
  ['skills-stat-modules', '11 MODULES'],
  ['cat-count-all', '>37<'],
  ['ai-status-latency', '~240ms'],
  ['runner-result-latency', '12ms'],
];
for (const [id, junk] of FAKE_LITERALS) {
  const re = new RegExp(`id="${id}"[^>]*>[^<]*${junk.replace(/[.*+?^${}()|[\]\\<>]/g, (c) => (c === '<' || c === '>' ? c : '\\' + c))}`);
  check(`#${id} không còn chứa "${junk}"`, !re.test(indexHtml));
}

check(
  'badge kho kỹ năng không mặc định "Sẵn sàng"',
  !/id="svc-skills-badge"[^>]*>[^<]*Sẵn sàng/.test(indexHtml),
  'vẫn còn khẳng định sẵn sàng trước khi có dữ liệu'
);
check(
  'trạng thái antivirus không mặc định xanh "BẬT (Hoạt động)"',
  !/id="sec-av-enabled"[^>]*>\s*BẬT \(Hoạt động\)/.test(indexHtml)
);
check(
  'trạng thái realtime không mặc định xanh "ĐANG BẬT"',
  !/id="sec-av-realtime"[^>]*>\s*ĐANG BẬT/.test(indexHtml)
);
check(
  'không còn option backend STT giả lập',
  !/value="mock"/.test(indexHtml) && !/Giả lập in-memory/.test(indexHtml)
);

// ──────────────────────────────────────────────────────────────────────
section('Mật khẩu tài khoản chỉ hiện ở máy local');

check('khối tài khoản mặc định có id để JS điều khiển', /id="dev-accounts-hint"/.test(indexHtml));
check(
  'khối đó mặc định bị ẩn bằng class hidden',
  /id="dev-accounts-hint"[^>]*\bhidden\b/.test(indexHtml),
  'nếu không ẩn sẵn thì lộ mật khẩu trước khi JS chạy'
);
check('có logic chỉ bật khi hostname là local', /isLocal\s*=\s*host\s*===\s*'localhost'/.test(appJs));
check('nhãn nói rõ chỉ hiện ở máy local', /chỉ hiện ở máy local/i.test(indexHtml));

// ──────────────────────────────────────────────────────────────────────
section('Không còn fallback bịa trong JS');

// Kiểm tra trên MÃ THUẦN TÚY. Comment giải thích Phase 73 buộc phải nhắc lại
// đúng những thứ đã gỡ (IP bịa, model chết, "PINNED CERT") — nếu quét cả
// comment thì mọi lần sửa sau này đều bị báo động giả.
const appCode = appJs
  .replace(/\/\*[\s\S]*?\*\//g, '')
  .replace(/^\s*\/\/.*$/gm, '')
  .replace(/\/\/.*$/gm, '');

const FALLBACKS = [
  [/Object\.keys\(skillsData\)\.length\s*:\s*31/, 'số 31 khi không có dữ liệu'],
  [/res\.count\s*\|\|\s*37/, 'số 37 khi server không trả count'],
  [/res\.risk\s*\|\|\s*'SAFE'/, 'rủi ro mặc định SAFE (fail-open)'],
  [/log\.risk\s*\|\|\s*log\.risk_level\s*\|\|\s*'SAFE'/, 'log rủi ro mặc định SAFE'],
  [/gemini-1\.5-flash/, 'model dự phòng chết'],
  [/llama3-8b-8192/, 'model dự phòng chết'],
  [/'sk-dummy'/, 'khoá API giả'],
  [/192\.168\.1\.10[58]/, 'IP máy trạm bịa'],
  [/Client Station 0[12]/, 'tên máy trạm bịa'],
  [/PINNED CERT/, 'badge bảo mật không kiểm chứng'],
  [/ĐỘ TRỄ\s*&lt;\s*3ms/, 'tuyên bố độ trễ không kiểm chứng'],
];
for (const [re, why] of FALLBACKS) {
  check(`không còn ${why} (${re.source})`, !re.test(appCode));
}

// Số có 1 chữ số thập phân nên trông như số đo thật — nguy hiểm nhất.
for (const n of ['24.5', '58.2', '45.0', '148']) {
  const re = new RegExp(`[:(,=]\\s*${n.replace('.', '\\.')}\\b`);
  check(`không còn số đo giả ${n}`, !re.test(appCode), 'số có chữ số thập phân trông như đo thật');
}

check(
  'không còn vẽ đường biểu đồ CPU giả khi thiếu lịch sử',
  !/:\s*\[\s*15,\s*28,\s*42,\s*30/.test(appCode),
  'mảng số giả làm đường biểu đồ trông như lịch sử thật'
);
check(
  'không còn mô tả kỹ năng bịa khi thiếu meta',
  !/Kỹ năng tự động hóa Windows native/.test(appCode)
);
check(
  'không tự khẳng định Telegram "đang chạy" khi server im lặng',
  !/tg\.message\s*\|\|\s*'Telegram Bot Gateway đang chạy'/.test(appCode)
);
check(
  'không báo "Đã hoàn thành tác vụ yêu cầu" khi response rỗng',
  !/res\.reply\s*\|\|\s*'Đã hoàn thành tác vụ yêu cầu\.'/.test(appCode)
    && !/msg\.speech_reply\s*\|\|\s*'Đã hoàn thành tác vụ yêu cầu\.'/.test(appCode)
);
check(
  'không báo "Đã tắt tiến trình" khi server không trả message',
  !/data\.result\?\.message\s*\|\|\s*'Đã tắt tiến trình'/.test(appCode)
);

// ──────────────────────────────────────────────────────────────────────
section('HUD: thiếu dữ liệu thì không vẽ và không bịa');

check('khối bản đồ mạng có chú thích Phase 73', /visualType === 'network_map'[\s\S]{0,400}Phase 73/.test(appJs));
check('khối biểu đồ tải có chú thích Phase 73', /visualType === 'metric_chart'[\s\S]{0,400}Phase 73/.test(appJs));
check(
  'chỉ vẽ polyline khi có `history` thật',
  /const history = \(Array\.isArray\(data\.history\)/.test(appJs)
    && /Chưa có lịch sử tải/.test(appJs)
);
check('trạng thái thiết bị lạ hiện "CHƯA RÕ" thay vì ONLINE', /'CHƯA RÕ'/.test(appCode));
check(
  'không còn tuyên bố độ trễ không kiểm chứng',
  !/ĐỘ TRỄ\s*&lt;\s*3ms/.test(appCode)
);

// ──────────────────────────────────────────────────────────────────────
section('Badge kho kỹ năng bám theo số đo thật');

check('có cập nhật #svc-skills-badge trong JS', /getElementById\('svc-skills-badge'\)/.test(appJs));
check(
  'badge chỉ báo "Hoạt động" khi có skills_count thật',
  /_isLive\(rawSkillsCount\)[\s\S]{0,200}svc-ok/.test(appJs),
  'badge phải phụ thuộc dữ liệu, không phải hằng số'
);

// ──────────────────────────────────────────────────────────────────────
section('updateSkillsTelemetry nói "chờ kết nối" thay vì để trống');

const telem = appJs.slice(appJs.indexOf('function updateSkillsTelemetry('));
const telemBody = telem.slice(0, telem.indexOf('\nfunction '));
check('không còn return sớm khi thiếu dữ liệu', !/if \(!skillsData\) return;/.test(telemBody));
check('khi thiếu dữ liệu thì gọi _setLiveText(el, null)', /if \(!skillsData\)[\s\S]{0,300}_setLiveText/.test(telemBody));

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
