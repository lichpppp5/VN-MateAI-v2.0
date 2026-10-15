// SPDX-License-Identifier: Apache-2.0
// Copyright (c) 2026 Dương Thanh Lịch — VN-MateAI. See LICENSE and NOTICE.
// web/inline-handlers.js (CSP chặt): MỌI on*="…" của giao diện phân tích được; chạy đúng nghĩa (this, event, return false,
// gán, object literal, lời gọi lồng); và KHÔNG chạy được thứ HTML bị chèn hay dùng để lấy dữ liệu (fetch, defaultView,
// constructor, so sánh, template ${}…). Ủy quyền sự kiện thử bằng DOM giả có nổi bọt + stopPropagation.
import { readFileSync, readdirSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';

const ROOT = join(dirname(fileURLToPath(import.meta.url)), '..');
const WEB = join(ROOT, 'web');
const src = readFileSync(join(WEB, 'inline-handlers.js'), 'utf8');
let passed = 0;
const ok = (c, n, extra = '') => { if (!c) { console.error(`FAIL ${n} ${extra}`); process.exit(1); } passed += 1; };

// ── nạp tệp trong "trình duyệt" giả ────────────────────────────────────────
function load({ strict, globals = {} }) {
  const listeners = {};
  const document = {
    querySelector: (sel) => (strict && sel === 'meta[name="vnmate-csp"]' ? { getAttribute: () => 'strict' } : null),
    addEventListener: (type, fn, capture) => { (listeners[type] ||= []).push({ fn, capture: !!capture }); },
    getElementById: (id) => globals.__byId?.[id] ?? null,
    documentElement: { nodeType: 1, getAttribute: () => null, querySelectorAll: () => [] },
  };
  const observers = [];
  class MutationObserver { constructor(cb) { this.cb = cb; observers.push(this); } observe(node, opts) { this.opts = opts; } }
  const window = { document, console, ...globals };
  window.window = window;
  new Function('window', 'document', 'console', 'MutationObserver', src)(window, document, console, MutationObserver);
  return { api: window.VNInlineHandlers, window, document, listeners, observers };
}

// ── 1. mọi handler của giao diện phân tích được ────────────────────────────
function stripTemplateExprs(s) {
  let out = '', i = 0;
  while (i < s.length) {
    if (s.startsWith('${', i)) {
      let d = 1, j = i + 2;
      while (j < s.length && d) { if (s[j] === '{') d += 1; else if (s[j] === '}') d -= 1; j += 1; }
      out += '0'; i = j;
    } else { out += s[i]; i += 1; }
  }
  return out;
}
const decode = (s) => s.replace(/&quot;/g, '"').replace(/&#39;/g, "'").replace(/&amp;/g, '&');
const files = ['index.html', 'hud.html', 'roi_dashboard.html',
  ...readdirSync(WEB).filter((f) => f.endsWith('.js') && f !== 'inline-handlers.js')];
const ATTR = /\son(click|dblclick|change|input|submit|reset|keyup|keydown|keypress|focus|blur|error|load|mouseenter|mouseleave|mouseover|mouseout|contextmenu)=(?:"([^"]*)"|'([^']*)')/g;
const { api } = load({ strict: false });
let count = 0;
const bad = [];
for (const f of files) {
  let s = readFileSync(join(WEB, f), 'utf8');
  if (f.endsWith('.js')) s = stripTemplateExprs(s);
  for (const m of s.matchAll(ATTR)) {
    const code = decode(m[2] ?? m[3]);
    count += 1;
    try { api.parse(code); } catch (e) { bad.push(`${f}: on${m[1]}="${code}" → ${e.message}`); }
  }
}
ok(count > 400, `quét được ${count} handler`);
ok(bad.length === 0, 'mọi handler phân tích được', '\n' + bad.join('\n'));

// không còn <script> nội tuyến trong các trang
for (const page of ['index.html', 'hud.html', 'roi_dashboard.html']) {
  const html = readFileSync(join(WEB, page), 'utf8');
  ok(!/<script(?![^>]*\bsrc=)[^>]*>/.test(html), `${page}: không còn <script> nội tuyến`);
  ok(!/javascript:/i.test(html), `${page}: không có javascript: URL`);
  const first = html.match(/<script[^>]*src="([^"]+)"/)[1];
  ok(page === 'index.html' ? /theme-boot\.js/.test(first) : /inline-handlers\.js/.test(first), `${page}: script đầu tiên là boot / inline-handlers`);
  ok(/src="\/static\/inline-handlers\.js/.test(html), `${page}: nạp inline-handlers.js`);
}

// ── 2. chạy đúng nghĩa ────────────────────────────────────────────────────
const calls = [];
const box = { value: '', classList: { add(c) { calls.push(['add', c]); } }, click() { calls.push(['click']); } };
const env = load({ strict: true, globals: {
  switchTab: (...a) => calls.push(['switchTab', ...a]),
  runIntegrationTool: (...a) => calls.push(['tool', ...a]),
  sendVoiceCommand: () => calls.push(['send', box.value]),
  approvePendingAction: (...a) => { calls.push(['approve', ...a]); return false; },
  fetch: () => calls.push(['FETCH']), apiFetch: () => calls.push(['APIFETCH']),
  __byId: { 'voice-input': box, x: box },
} });
// danh sách cho phép dùng tên thật của mã nguồn; với DOM giả, thêm tên thử nghiệm vào đó
['switchTab', 'runIntegrationTool', 'sendVoiceCommand', 'approvePendingAction', 'document'].forEach((n) => env.api.allowed.add(n));
const self = { value: '42', checked: true, dataset: { id: 'abc' } };
const evt = { key: 'Enter', prevented: false, preventDefault() { this.prevented = true; } };
const run = (code) => env.api.run(env.api.parse(code), self, evt);

run("switchTab('logs'); switchTab('dashboard')");
ok(JSON.stringify(calls.splice(0)) === '[["switchTab","logs"],["switchTab","dashboard"]]', 'nhiều lệnh nối bằng ;');
run("runIntegrationTool('check_connector_health', {connectors: ['aws','oci'], n: -2, ok: true, x: null})");
ok(JSON.stringify(calls.splice(0)) === '[["tool","check_connector_health",{"connectors":["aws","oci"],"n":-2,"ok":true,"x":null}]]', 'object / array literal');
run("document.getElementById('voice-input').value='Mở trình duyệt Edge'; sendVoiceCommand()");
ok(JSON.stringify(calls.splice(0)) === '[["send","Mở trình duyệt Edge"]]', 'gán thuộc tính rồi gọi');
run("document.getElementById('x').classList.add('hidden')");
ok(JSON.stringify(calls.splice(0)) === '[["add","hidden"]]', 'chuỗi member + lời gọi');
run('switchTab(this.value, this.dataset.id, event.key)');
ok(JSON.stringify(calls.splice(0)) === '[["switchTab","42","abc","Enter"]]', 'this / event');
run("switchTab('it\\'s', \"dq\")");
ok(JSON.stringify(calls.splice(0)) === `[["switchTab","it's","dq"]]`, 'chuỗi có thoát ký tự');
ok(run('return approvePendingAction(1)') === false, 'return trả giá trị');

// ── 3. chặn ───────────────────────────────────────────────────────────────
const rejects = (code, why) => {
  let threw = false;
  try { run(code); } catch (e) { threw = true; }
  ok(threw, `chặn: ${why}`, code);
};
rejects("fetch('//evil')", 'fetch không có trong danh sách');
rejects("apiFetch('/api/v1/users/1', {method: 'DELETE'})", 'apiFetch không có trong danh sách');
rejects("document.defaultView.fetch('//evil')", 'defaultView');
rejects('switchTab.constructor(1)', 'constructor');
rejects("document.getElementById('x').innerHTML = '<img>'", 'gán innerHTML');
rejects("switchTab['constructor']('x')", 'constructor qua []');
rejects('switchTab.call(1)', 'call');
ok(!calls.some((c) => c[0] === 'FETCH' || c[0] === 'APIFETCH'), 'không lời gọi mạng nào lọt qua');
for (const code of ['a == b', 'x ? y : z', '`${x}`', '() => 1', 'a + b', 'new Foo()']) {
  let threw = false;
  try { env.api.parse(code); } catch (e) { threw = true; }
  ok(threw, `ngữ pháp từ chối: ${code}`);
}

// ── 4. uỷ quyền sự kiện: nổi bọt, stopPropagation, return false ───────────
function el(attrs, parent) {
  return { nodeType: 1, parentNode: parent, attrs,
    hasAttribute(n) { return n in this.attrs; }, getAttribute(n) { return this.attrs[n] ?? null; } };
}
const outer = el({ onclick: "switchTab('outer')" }, env.document);
const inner = el({ onclick: "switchTab('inner')" }, outer);
const stopper = el({ onclick: "switchTab('stop'); event.stopPropagation()" }, outer);
const linkish = el({ onclick: 'return approvePendingAction(7)' }, env.document);
const fireClick = (target) => {
  const e = { type: 'click', target, cancelBubble: false, prevented: false,
    stopPropagation() { this.cancelBubble = true; }, preventDefault() { this.prevented = true; } };
  env.listeners.click.forEach((l) => l.fn(e));
  return e;
};
env.api.allowed.add('approvePendingAction');
calls.splice(0); fireClick(inner);
{ const got = JSON.stringify(calls.splice(0)); ok(got === '[["switchTab","inner"],["switchTab","outer"]]', 'nổi bọt từ trong ra ngoài', got); }
fireClick(stopper);
ok(JSON.stringify(calls.splice(0)) === '[["switchTab","stop"]]', 'stopPropagation dừng nổi bọt');
const e = fireClick(linkish);
ok(e.prevented, 'return false → preventDefault');
ok(env.listeners.error && env.listeners.error[0].capture, 'onerror bắt ở pha capture (không nổi bọt)');

// on* → data-vn-on* (trình duyệt không còn thấy on* nên không ghi "vi phạm CSP" mỗi lần bấm), vẫn chạy được
{
  const attrs = { onclick: "switchTab('adopted')", class: 'x' };
  const node = { nodeType: 1, parentNode: env.document, querySelectorAll: () => [],
    getAttribute(n) { return n in attrs ? attrs[n] : null; }, hasAttribute(n) { return n in attrs; },
    setAttribute(n, v) { attrs[n] = String(v); }, removeAttribute(n) { delete attrs[n]; } };
  env.api.adopt(node);
  ok(!('onclick' in attrs) && attrs['data-vn-onclick'] === "switchTab('adopted')", 'adopt: chuyển onclick sang data-vn-onclick');
  calls.splice(0);
  fireClick(node);
  ok(JSON.stringify(calls.splice(0)) === '[["switchTab","adopted"]]', 'adopt: vẫn chạy sau khi chuyển');
  const mo = env.observers[0];
  ok(mo && mo.opts.subtree && mo.opts.childList && mo.opts.attributeFilter.includes('onclick'), 'theo dõi phần tử chèn sau (innerHTML)');
}

// không có meta strict → không gắn gì (trình duyệt tự chạy on* như trước, tránh chạy hai lần)
const loose = load({ strict: false });
ok(!loose.api.active && Object.keys(loose.listeners).length === 0 && loose.observers.length === 0, 'không strict: không gắn listener / observer');

console.log(`inline-handlers: ${passed} kiểm tra đạt (${count} handler)`);
