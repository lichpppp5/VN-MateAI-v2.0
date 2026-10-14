// SPDX-License-Identifier: Apache-2.0
// Copyright (c) 2026 Dương Thanh Lịch — VN-MateAI. See LICENSE and NOTICE.
// tests/test_hud_conversation_loop.mjs
// Vòng hội thoại HUD: nhận ra "không còn yêu cầu", bỏ qua tiếng vọng của trợ lý,
// không mở mic khi loa còn đang đọc, chờ 30 giây rồi mới chào và đóng.
// Chạy đúng mã trong web/hud.js (cắt các hàm ra rồi eval) — không dựng trình duyệt.
import { readFileSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';

const ROOT = join(dirname(fileURLToPath(import.meta.url)), '..');
const src = readFileSync(join(ROOT, 'web', 'hud.js'), 'utf8').replace(/\r\n/g, '\n');

let passed = 0;
let failed = 0;
const failures = [];
function check(name, cond, detail = '') {
  if (cond) { passed += 1; console.log(`  ✓ ${name}`); } else { failed += 1; failures.push(`${name} ${detail}`); console.log(`  ✗ ${name} ${detail}`); }
}

function grab(re, label) {
  const m = src.match(re);
  if (!m) throw new Error(`không tìm thấy ${label} trong hud.js`);
  return m[0];
}
const constBlock = (name) => grab(new RegExp(`const ${name} = new Set\\(\\[[\\s\\S]*?\\]\\);`), name);
const fnBlock = (name) => grab(new RegExp(`function ${name}\\([^)]*\\) \\{[\\s\\S]*?\\n\\}`), name);

const code = [
  constBlock('HUD_DONE_PHRASES'), constBlock('HUD_POLITE'),
  fnBlock('hudIsDoneReply'), 'let hudRecentSpoken = [];', fnBlock('hudRememberSpoken'),
  fnBlock('hudNormalize'), fnBlock('hudIsEcho'),
  'return { hudIsDoneReply, hudRememberSpoken, hudIsEcho };',
].join('\n');
const H = new Function(code)();

console.log('▸ "Không còn yêu cầu" → đóng lắng nghe ngay');
for (const t of ['không', 'dạ không có gì nữa đâu em', 'cảm ơn em nhé', 'thôi được rồi', 'hết rồi', 'tạm biệt nhé',
  'không cần , cảm ơn bạn', 'dạ không ạ, cảm ơn em nhé', 'thôi, cảm ơn em']) {
  check(`đóng: "${t}"`, H.hudIsDoneReply(t));
}
for (const t of ['không biết hôm nay CPU thế nào', 'mở bài hát Lạc Trôi', 'vâng', 'có cần kiểm tra không cần thiết',
  'không, kiểm tra ổ đĩa giúp anh', 'cảm ơn, giờ xem giúp anh doanh thu']) {
  check(`KHÔNG đóng: "${t}"`, !H.hudIsDoneReply(t));
}

console.log('\n▸ Tiếng vọng: mic thu lại lời trợ lý không phải lệnh');
H.hudRememberSpoken('Anh còn cần em hỗ trợ gì nữa không ạ?');
check('lời trợ lý vừa đọc là tiếng vọng', H.hudIsEcho('anh còn cần em hỗ trợ gì nữa không'));
check('lệnh thật không bị coi là tiếng vọng', !H.hudIsEcho('kiểm tra dung lượng ổ đĩa C'));

console.log('\n▸ Thứ tự trong vòng hội thoại');
const openFn = fnBlock('hudOpenMicWhenQuiet');
check('chưa mở mic khi loa còn đang đọc', /if \(hudIsSpeaking\(\)\)/.test(openFn) && openFn.indexOf('hudIsSpeaking') < openFn.indexOf('hudOpenMicForFollowup'));
check('chờ đúng 30 giây', /const HUD_REPLY_WAIT_MS = 30000;/.test(src));
check('30 giây đếm từ lúc mở mic (không phải từ lúc máy chủ báo)', openFn.includes('hudReplyDeadline = Date.now() + HUD_REPLY_WAIT_MS'));
const timeoutFn = fnBlock('hudOnReplyTimeout');
check('hết 30 giây → máy chủ chào rồi đóng', timeoutFn.includes("reason: 'timeout'"));
check('không còn hỏi lại 2 lần bằng hàm không tồn tại', !src.includes('speakHudText') && !src.includes('HUD_MAX_REASKS'));
const onend = grab(/rec\.onend = \(\) => \{[\s\S]*?\n {8}\};/, 'rec.onend');
check('onend: bỏ tiếng vọng trước khi gửi lệnh', onend.indexOf('hudIsEcho') < onend.indexOf('sendHudVoiceCommand'));
check('onend: "không còn yêu cầu" đóng mà không gửi AI', onend.indexOf('hudEndConversationByUser') < onend.indexOf('sendHudVoiceCommand'));
check('onend: im lặng khi đang chờ → mở lại mic', onend.includes('hudKeepListening()'));

console.log('\n▸ Popup câu trả lời');
check('không đếm lùi khi đang đọc', /if \(hudIsSpeaking\(\)\) \{\s*\/\/ Đang đọc/.test(src));
check('thời gian giữ theo độ dài nội dung (10–60s)', /HUD_CARD_MIN_MS = 10000/.test(src) && /HUD_CARD_MAX_MS = 60000/.test(src) && src.includes('displayText.length * 60'));
check('gói chữ có tiếng đi riêng không bị coi là "không có tiếng"', src.includes('packet.has_audio && !audioB64'));

console.log(`\nTổng: ${passed + failed} | Pass: ${passed} | Fail: ${failed}`);
if (failures.length) { console.log(failures.join('\n')); process.exit(1); }
