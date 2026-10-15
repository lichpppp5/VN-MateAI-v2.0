// SPDX-License-Identifier: Apache-2.0
// Copyright (c) 2026 Dương Thanh Lịch — VN-MateAI. See LICENSE and NOTICE.
// web/branding.js: màu chủ đạo sinh CSS chỉ với #RRGGBB hợp lệ; chưa đổi thương hiệu thì không đụng chữ / logo; có đổi thì
// áp vào data-brand (textContent, không innerHTML); thẻ sửa chỉ cho admin, không onclick nội tuyến.
import { readFileSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';

const ROOT = join(dirname(fileURLToPath(import.meta.url)), '..');
const src = readFileSync(join(ROOT, 'web', 'branding.js'), 'utf8');
const html = readFileSync(join(ROOT, 'web', 'index.html'), 'utf8');
let passed = 0;
const ok = (c, n) => { if (!c) { console.error(`FAIL ${n}`); process.exit(1); } passed += 1; };

function harness(role) {
  const nodes = { name: [{ textContent: 'VN-MateAI' }, { textContent: 'VN-MateAI' }], tagline: [{ textContent: 'x' }], logo: [] };
  const logo = { children: ['V'], classList: { s: new Set(), add(c) { this.s.add(c); } }, replaceChildren(...c) { this.children = c; } };
  nodes.logo.push(logo);
  const head = { appended: [], appendChild(n) { this.appended.push(n); } };
  const document = {
    readyState: 'complete', title: 'VN-MateAI — Portal', head,
    getElementById: (id) => head.appended.find((n) => n.id === id) || (id === 'branding-admin' ? adminRoot : null),
    createElement: (t) => ({ tag: t, id: '', textContent: '', className: '' }),
    querySelectorAll: (sel) => nodes[(sel.match(/data-brand="([a-z]+)"/) || [])[1]] || [],
    addEventListener() {},
  };
  const adminRoot = { innerHTML: 'x' };
  const store = { vnmateai_user: JSON.stringify({ role }) };
  const localStorage = { getItem: (k) => store[k] ?? null, setItem: (k, v) => { store[k] = v; } };
  const fetch = async () => ({ ok: false });
  const B = new Function('document', 'localStorage', 'fetch', `${src}\nreturn Branding;`)(document, localStorage, fetch);
  return { B, nodes, logo, document, adminRoot };
}

{
  const { B } = harness('admin');
  ok(B.colorCss('red') === '' && B.colorCss('#12345') === '' && B.colorCss('') === '', 'màu sai → không sinh CSS');
  const css = B.colorCss('#0f766e');
  ok(css.includes('#0f766e') && css.includes('.bg-primary-600') && !/[<>]/.test(css), 'màu đúng → CSS lớp primary');
}
{
  const { B, nodes, document } = harness('admin');
  B.apply({ customized: false, product_name: 'VN-MateAI', tagline: 'x', primary_color: '', logo_data_url: '' });
  ok(nodes.name[0].textContent === 'VN-MateAI' && document.title === 'VN-MateAI — Portal', 'mặc định: giữ nguyên');
}
{
  const { B, nodes, logo, document } = harness('admin');
  B.apply({ customized: true, product_name: 'ACME <b>Ops</b>', tagline: 'Vận hành', primary_color: '#0f766e', logo_data_url: 'data:image/png;base64,AAAA' });
  ok(nodes.name.every((n) => n.textContent === 'ACME <b>Ops</b>'), 'tên áp bằng textContent (không diễn giải HTML)');
  ok(nodes.tagline[0].textContent === 'Vận hành', 'dòng mô tả');
  ok(logo.children[0].tag === 'img' && logo.children[0].src.startsWith('data:image/png'), 'logo là <img>');
  ok(document.title === 'ACME <b>Ops</b> — Portal', 'tiêu đề tab trình duyệt');
}
{
  const { B, adminRoot } = harness('manager');
  B.renderAdmin();
  ok(adminRoot.innerHTML === '', 'không phải admin: không có thẻ sửa');
}
ok(!/onclick=|onchange=|oninput=/.test(src), 'không handler nội tuyến');
ok(/id="branding-admin"/.test(html) && /src="\/static\/branding\.js/.test(html), 'index.html có chỗ + nạp branding.js');
ok((html.match(/data-brand="name"/g) || []).length >= 3 && /data-brand="logo"/.test(html), 'sidebar / header / đăng nhập gắn data-brand');

console.log(`branding UI: ${passed} kiểm tra đạt`);
