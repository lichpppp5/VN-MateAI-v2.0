// SPDX-License-Identifier: Apache-2.0
// Copyright (c) 2026 Dương Thanh Lịch — VN-MateAI. See LICENSE and NOTICE.
// Kiểm thử hồi quy Phase 63 — renderPortalMarkdown: bảng Markdown + XSS.
//
// `renderPortalMarkdown` đưa thẳng ra innerHTML ở 4 nơi gọi. Trước Phase 63
// nó chỉ nhận câu trả lời của LLM; từ khi tool `fetch_data_source` chạy, nó còn
// nhận dữ liệu thật từ ERP của khách hàng — tên hàng, tên khách hàng, ghi chú.
// Những chuỗi đó không ai kiểm duyệt và không phải do người dùng gõ, nên hàm
// phải tự escape chứ không được tin đầu vào.

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

function grab(name) {
  const i = src.indexOf(`function ${name}`);
  if (i < 0) throw new Error(`không tìm thấy ${name}`);
  let d = 0, j = i;
  for (; j < src.length; j++) {
    if (src[j] === '{') d++;
    else if (src[j] === '}') { d--; if (d === 0) break; }
  }
  return src.slice(i, j + 1);
}

const dir = mkdtempSync(join(tmpdir(), 'md63-'));
writeFileSync(join(dir, 'm.mjs'), [
  grab('escapePortalHtml'),
  grab('formatPortalInlineText'),
  grab('renderPortalMarkdown'),
  'export { renderPortalMarkdown };',
].join('\n\n'));

const { renderPortalMarkdown: render } = await import(pathToFileURL(join(dir, 'm.mjs')).href);

// ══ 1. Bảng Markdown ═════════════════════════════════════════════════════
console.log('\n▸ Bảng Markdown tiếng Việt');
const md = `Báo cáo tồn kho tháng 9:

| Mã hàng | Tên hàng | Số lượng |
| --- | --- | --- |
| SP001 | Ghế xoay nội thất | 45 |
| SP002 | Bàn gỗ thông | 12 |

Tổng cộng: 57`;
const out = render(md);
check('render ra <table>', out.includes('<table'));
check('tiêu đề cột thành <th>', out.includes('<th'));
check('ô dữ liệu thành <td>', out.includes('<td'));
check('giữ đủ 3 dòng (tiêu đề + 2 dữ liệu)',
  (out.match(/<tr/g) || []).length === 3, `${(out.match(/<tr/g) || []).length} dòng`);
check('tiếng Việt giữ nguyên dấu', out.includes('Ghế xoay nội thất'));
check('bảng rộng được cuộn ngang', out.includes('overflow-x-auto'));
check('văn bản ngoài bảng vẫn còn', out.includes('Tổng cộng: 57'));

console.log('\n▸ Bảng rộng');
const wide = `| ${Array.from({ length: 12 }, (_, k) => `c${k}`).join(' | ')} |
| ${Array.from({ length: 12 }, () => '---').join(' | ')} |
| ${Array.from({ length: 12 }, (_, k) => k).join(' | ')} |`;
const ow = render(wide);
check('bảng 12 cột vẫn render', (ow.match(/<th/g) || []).length === 12, `${(ow.match(/<th/g) || []).length} cột`);

console.log('\n▸ Bảng nhiều trong một câu trả lời');
const two = `Bảng 1:

| a |
| --- |
| 1 |

Và bảng 2:

| b |
| --- |
| 2 |`;
const ot = render(two);
check('hai bảng tách biệt vẫn render', (ot.match(/<table/g) || []).length === 2,
  `${(ot.match(/<table/g) || []).length} bảng`);

// ══ 2. XSS — đây là lý do hàm này phải escape ═══════════════════════════
console.log('\n▸ XSS qua ô bảng (tên hàng từ ERP)');
const payloads = [
  ['<img src=x onerror=alert(1)>', /<img[^>]*onerror/i],
  ['<svg onload=alert(1)>', /<svg[^>]*onload/i],
  ['<script>alert(1)</script>', /<script/i],
  ['"><script>alert(1)</script>', /<script/i],
  ['<iframe src="javascript:alert(1)">', /<iframe/i],
  ['<a href="javascript:alert(1)">x</a>', /<a[^>]*href="javascript:/i],
  ['<body onload=alert(1)>', /<body[^>]*onload/i],
  ['<div onclick=alert(1)>x</div>', /<div[^>]*onclick/i],
];
for (const [payload, danger] of payloads) {
  const html = `| Mã | Tên |\n| --- | --- |\n| SP001 | ${payload} |`;
  const r = render(html);
  check(`chặn: ${payload.slice(0, 34)}`, !danger.test(r), r.match(danger)?.[0]);
}

console.log('\n▸ XSS qua văn bản thường');
const textPayloads = [
  ['Xin chào <img src=x onerror=alert(1)>', /<img[^>]*onerror/i],
  ['<script>alert(1)</script>', /<script/i],
  ['<svg onload=alert(1)>', /<svg[^>]*onload/i],
];
for (const [payload, danger] of textPayloads) {
  const r = render(payload);
  check(`chặn văn bản: ${payload.slice(0, 32)}`, !danger.test(r), r.match(danger)?.[0]);
}

console.log('\n▸ XSS qua tiêu đề và danh sách');
check('chặn trong tiêu đề #', !/<img[^>]*onerror/i.test(render('# <img src=x onerror=alert(1)>')));
check('chặn trong gạch đầu dòng', !/<img[^>]*onerror/i.test(render('- <img src=x onerror=alert(1)>')));

console.log('\n▸ Payload nằm trong khối code (phải hiện nguyên văn, không thực)');
const codeOut = render('```\n<img src=x onerror=alert(1)>\n```');
check('code block escape nội dung', !/<img[^>]*onerror/i.test(codeOut), codeOut.slice(0, 120));
check('code block vẫn hiện thẻ <pre>', codeOut.includes('<pre'));
check('khối code trong câu có bảng vẫn nguyên', (() => {
  const both = render('| a |\n| --- |\n| 1 |\n\n```\nx = 1\n```');
  return both.includes('<pre') && both.includes('<table');
})(), 'mất một trong hai phần');

// ══ 3. Không làm hỏng nội dung hợp lệ ════════════════════════════════════
console.log('\n▸ Đường kẻ ngang (LLM hay dùng --- để tách mục)');
check('--- thành <hr>', render('a\n\n---\n\nb').includes('<hr'));
check('*** thành <hr>', render('***').includes('<hr'));
check('___ thành <hr>', render('___').includes('<hr'));
check('--- trong nhiều dòng vẫn bị bỏ qua', !render('| --- | --- |').includes('<hr'));
check('dòng kẻ trong bảng KHÔNG sinh <hr>', (() => {
  const t = render('| a | b |\n| --- | --- |\n| 1 | 2 |');
  return !t.includes('<hr') && t.includes('<table');
})(), 'sinh hr nhầm trong bảng');
check('gạch ngang trong câu văn không bị nhầm', !render('giá --- 100').includes('<hr'));
check('chuỗi ---- (4 gạch) cũng là kẻ', render('----').includes('<hr'));
check('- là mục danh sách, KHÔNG phải kẻ', (() => {
  const l = render('- Mục một');
  return !l.includes('<hr') && l.includes('Mục một');
})(), '3 gạch ngang liền nhau bị nhầm');
check('kẻ trước bảng không phá bảng', (() => {
  const t = render('---\n\n| a |\n| --- |\n| 1 |');
  return t.includes('<hr') && t.includes('<table');
})());

console.log('\n▸ Không phá nội dung hợp lệ');
check('văn bản thường', render('Xin chào, tôi cần báo cáo.').includes('Xin chào'));
check('in đậm', render('**quan trọng**').includes('<strong'));
check('in nghiêng', render('*ghi chú*').includes('<em'));
check('mã nội tuyến', render('dùng `pip install`').includes('<code'));
check('tiêu đề cấp 1', render('# Báo cáo').includes('<h2'));
check('tiêu đề cấp 2', render('## Chi tiết').includes('<h3'));
check('tiêu đề cấp 3', render('### Ghi chú').includes('<h4'));
check('danh sách gạch đầu dòng', render('- Mục một\n- Mục hai').includes('Mục một'));
check('ký tự & hiển thị đúng', render('A & B').includes('&amp;'));
check('dấu < hiển thị đúng', render('a < b').includes('&lt;'));
check('ô bảng chứa ký tự &', render('| a |\n| --- |\n| A & B |').includes('&amp;'));
check('ô bảng chứa dấu <', render('| a |\n| --- |\n| a < b |').includes('&lt;'));
check('chuỗi rỗng -> rỗng', render('') === '' && render(null) === '');

console.log('\n' + '─'.repeat(60));
if (fail) {
  console.log('Assertion FAIL:');
  results.filter(r => r.includes('❌')).forEach(r => console.log(r));
}
console.log(`\nTổng: ${pass + fail} | Pass: ${pass} | Fail: ${fail}`);
process.exit(fail ? 1 : 0);
