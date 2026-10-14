// SPDX-License-Identifier: Apache-2.0
// Copyright (c) 2026 Dương Thanh Lịch — VN-MateAI. See LICENSE and NOTICE.
// Kiểm thử hồi quy cho tab "Tích Hợp Hệ Thống Báo Cáo".
//
// Phase 59/60 được tách khỏi tab "Trung Tâm Chỉ Huy" sang một tab cấp cao
// riêng. Tách tab là thay đổi cấu trúc, nên rủi ro hồi quy nằm ở chỗ:
//   1. HTML: khối tích hợp nằm nửa trong tab này nửa kia, hoặc quên nút nav.
//   2. JS:   `switchTab` không nạp dữ liệu, hoặc `CommandCenter.onEnter()`
//             vẫn gọi 4 hàm tải của tích hợp (tốn API vô ích).
//   3. KPI:  ô KPI phê duyệt còn được chép qua một ô trung gian đã bị xoá
//             → số hiển thị là số chết, không bao giờ cập nhật.
//
// Cả ba đều là loại lỗi "vẫn chạy, không báo lỗi, chỉ sai âm thầm" — nên
// phải có test chặn thay vì tin vào việc click tay.

import { readFileSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';

const HERE = dirname(fileURLToPath(import.meta.url));
const APP = join(HERE, '..', 'web', 'app.js');
const HTML = join(HERE, '..', 'web', 'index.html');
const src = readFileSync(APP, 'utf-8').replace(/\r\n/g, '\n');
// Bản code đã bỏ comment — bắt buộc khi kiểm tra "cái gì đó đã bị gỡ": các
// bình luận giải thích fix hay nhắc lại đúng tên hàm đã xoá, nên quét `src`
// thôi sẽ khẳng định sai là nó còn tồn tại.
const srcCode = src.replace(/\/\*[\s\S]*?\*\//g, '').replace(/^\s*\/\/.*$/gm, '');
const html = readFileSync(HTML, 'utf-8').replace(/\r\n/g, '\n');

let pass = 0, fail = 0;
const results = [];
function check(name, cond, extra = '') {
  if (cond) { pass++; results.push(`  ✅ ${name}`); }
  else { fail++; results.push(`  ❌ ${name}${extra ? ' — ' + extra : ''}`); }
}

// ── Tiện ích: cắt ra đúng phạm vi một section trong index.html ─────────────
// Bằng cách đếm thẻ mở/đóng thay vì so regex, vì các thẻ lồng nhau cùng
// cấp không cho biết đâu là thẻ đóng tương ứng.
function sectionRange(id) {
  const open = new RegExp(`<section[^>]*\\bid="${id}"[^>]*>`, 'g');
  const m = open.exec(html);
  if (!m) return null;
  let depth = 0, i = m.index;
  const re = /<section\b|<\/section>/g;
  re.lastIndex = i;
  let t;
  while ((t = re.exec(html))) {
    depth += t[0] === '</section>' ? -1 : 1;
    if (depth === 0) return [m.index, t.index + t[0].length];
  }
  return null;
}

// Phase 79: tab "Trung Tâm Chỉ Huy" đã gộp vào "Bảng Điều Khiển", nên
// không còn section riêng. Phần tích hợp nằm trong `tab-dashboard`.
const dash = sectionRange('tab-dashboard');
const si = sectionRange('tab-system-integration');

// Nguồn chân lý cho danh sách tab: VALID_TABS trong app.js. Nếu test tự định
// nghĩa danh sách thì chỗ nào lệch cũng không ai báo.
const VALID_TABS = [
  ...(srcCode.match(/const VALID_TABS = \[([^\]]*)\]/) || [, ''])[1]
    .matchAll(/'([a-z0-9-]+)'/g),
].map(m => m[1]);

results.push('▸ Cấu trúc HTML');
check('tab Trung Tâm Chỉ Huy đã gộp, không còn section riêng',
  sectionRange('tab-command-center') === null);
check('có section #tab-dashboard', dash !== null);
check('có section #tab-system-integration', si !== null);

if (dash && si) {
  const dashHtml = html.slice(dash[0], dash[1]);
  const siHtml = html.slice(si[0], si[1]);

  // Nội dung C.E.O phải nằm trong Bảng Điều Khiển, không mất khi gộp.
  // (Các sub-pane `cc-int-*` thuộc tab tích hợp, không phải phần C.E.O.)
  check('nội dung C.E.O còn nguyên trong Bảng Điều Khiển',
    ['cc-audit-btn', 'cc-subsystems', 'cc-kpi-infra']
      .every(id => dashHtml.includes(`id="${id}"`)),
    'mất id = gộp kiểu cắt rồi quên dán, màn hình C.E.O trắng');
  check('khung biểu đồ & tra cứu quy chế riêng đã bỏ (Phase 82)',
    ['cc-chart-canvas', 'cc-chart-empty', 'cc-policy-input', 'cc-policy-result']
      .every(id => !dashHtml.includes(`id="${id}"`)),
    'khung đã bỏ theo yêu cầu — AI trả lời bằng chữ trong ô kết quả');
  check('tab tích hợp là section riêng biệt, không lồng trong Bảng Điều Khiển',
    si[0] > dash[1] || si[1] < dash[0] || html.slice(Math.min(dash[1], si[1]), Math.max(dash[0], si[0])).indexOf('<section') === -1);

  // 5 sub-pane phải nằm ở đúng một chỗ — tab tích hợp, không phải Bảng Điều Khiển.
  const SUBPANES = ['cc-int-conn', 'cc-int-config', 'cc-int-webhook', 'cc-int-tools', 'cc-int-sys'];
  const missingInSi = SUBPANES.filter(id => !siHtml.includes(`id="${id}"`));
  const leftInDash = SUBPANES.filter(id => dashHtml.includes(`id="${id}"`));
  check('đủ 5 sub-pane trong tab mới', missingInSi.length === 0, missingInSi.join(','));
  check('không sub-pane nào còn sót trong Bảng Điều Khiển', leftInDash.length === 0, leftInDash.join(','));

  // Thanh sub-tab đi kèm phải ở tab mới.
  const subtabBtns = (html.match(/switchCcSubTab\('/g) || []).length;
  // Phase 81: thêm sub-tab 'Máy Trạm' (khối máy trạm 194 dòng trước đây nằm
  // tràn dưới sub-tab Hệ Thống) -> 5 thành 6 nút.
  const siSubtabs = (siHtml.match(/switchCcSubTab\('/g) || []).length;
  // 2026-10-12: gộp 'config' + 'tools' vào 'Kết nối' và gỡ 'Elastic Grid' (chưa từng giao việc) -> còn 5 nút:
  // Kết nối, Phòng ban, Webhook, Máy trạm, Hệ thống.
  check('đủ các nút sub-tab trong tab mới', siSubtabs >= 5,
    `thấy ${siSubtabs}/${subtabBtns} toàn trang`);
  check('có nút sub-tab Máy Trạm', siHtml.includes("data-cc-subtab=\"devices\""));
  check('khối máy trạm nằm trong pane cc-int-devices',
    /id="cc-int-devices"[\s\S]*?devices-table-body/.test(siHtml));

  // Các ô KPI của tích hợp phải ở tab tích hợp, không lẫn sang Bảng Điều Khiển.
  const movedKpi = ['cc-kpi-connectors', 'cc-kpi-plugins'];
  check('2 ô KPI cũ (Kết nối ngoại vi, Công cụ) đã sang tab mới',
    movedKpi.every(id => siHtml.includes(`id="${id}"`))
    && !movedKpi.some(id => dashHtml.includes(`id="${id}"`)));

  // Dải KPI của Trung Tâm Chỉ Huy.
  //
  // Phase 71 đổi dải này: ô "Sức khoẹ quỹ" (báo cáo dòng tiền) bị gỡ theo
  // yêu cầu admin, ô "Cảnh báo" gộp vào chỉ số "Sự cố mở".
  //
  // Phase 72 bỏ ô "Tài nguyên": CPU/RAM đã hiện ở đồng hồ tab Tổng Quan và ở
  // thanh đo ngay dưới, nên trong tab này nó xuất hiện tới ba lần. Theo dõi
  // tài nguyên thuộc về Tổng Quan; tab này dành chỗ cho việc điều hành. Dải
  // KPI không được rỗng — mất nó thì tab không còn trả lời nhanh được "tình
  // hình thế nào", đúng thứ admin cần.
  // Phase 79: dải KPI này nằm trong Bảng Điều Khiển (gộp từ Trung Tâm Chỉ Huy).
  for (const id of ['cc-kpi-infra', 'cc-kpi-bg',
                    'cc-kpi-pending', 'cc-kpi-incident']) {
    check(`Bảng Điều Khiển có ô KPI "${id}"`, dashHtml.includes(`id="${id}"`));
  }
  check('ô KPI "Tài nguyên" đã gỡ khỏi dải chỉ số',
    !dashHtml.includes('cc-kpi-resource'));
  check('ô KPI dòng tiền đã gỡ khỏi dải chỉ số',
    !dashHtml.includes('cc-kpi-cashflow'));

  // Ô KPI mới của tab tích hợp.
  for (const id of ['cc-kpi-bg-tasks', 'cc-kpi-webhook-alerts']) {
    check(`tab mới có ô KPI "${id}"`, siHtml.includes(`id="${id}"`));
  }

  // Nút điều hướng.
  check('có nút nav #nav-system-integration gọi switchTab(\'system-integration\')',
    /id="nav-system-integration"[^>]*onclick="switchTab\('system-integration'\)"/.test(html));
  // Vị trí trong thanh điều hướng: phải kề nhau. So sánh theo thứ tự các
  // thẻ nav-btn chứ không theo byte offset — offset dễ bị lệch chỉ vì
  // `class=` đứng trước `id=` trong cùng một thẻ.
  // Regex phải chịu được class bổ sung (`class="nav-btn active"`) — nút Bảng
  // Điều Khiển có class `active` nên `<div class="nav-btn" id=` không khớp nó.
  // Bỏ các mục không phải tab (`nav-admin-center` là link ra app Admin riêng,
  // `nav-roi-dashboard` là màn hình ROI) — chúng xen vào giữa các tab.
  const navOrder = [...html.matchAll(/<div class="nav-btn[^"]*" id="nav-([a-z-]+)"/g)]
    .map(m => m[1])
    .filter(id => VALID_TABS.includes(id));
  // 2026-10-12: thanh điều hướng xếp theo cách dùng; tab tích hợp đứng ĐẦU nhóm "Kết nối doanh nghiệp",
  // ngay trước Cổng Agent và Dev Fleet (ba tab nối ra ngoài nằm cạnh nhau).
  const iSi = navOrder.indexOf('system-integration');
  check('tab tích hợp mở đầu nhóm kết nối: Tích hợp > Cổng Agent > Dev Fleet',
    iSi >= 0 && navOrder[iSi + 1] === 'agent-gateway' && navOrder[iSi + 2] === 'dev-fleet', navOrder.join(' > '));
  check('mọi nút nav đều trỏ tới một tab thật',
    navOrder.length === VALID_TABS.length,
    `nav=${navOrder.length} VALID_TABS=${VALID_TABS.length}: ${navOrder.join(', ')}`);
  check('không còn nút nav của tab đã gộp',
    !navOrder.includes('command-center') && !navOrder.includes('users') && !navOrder.includes('devices'),
    navOrder.join(', '));
}

// Không được để lại id trùng sau khi di chuyển — trùng id làm phần tử thứ hai
// bị bỏ qua, biểu hiện là "ô này cập nhật, ô kia treo" rất khó tìm.
{
  const ids = [...html.matchAll(/\bid="([^"]+)"/g)].map(m => m[1]);
  const dup = [...new Set(ids.filter((v, i) => ids.indexOf(v) !== i))];
  check('không có id nào bị trùng trong index.html', dup.length === 0, dup.join(','));
}

results.push('▸ Đăng ký tab trong app.js');
check("VALID_TABS có 'system-integration'",
  /const VALID_TABS = \[[^\]]*'system-integration'[^\]]*\]/.test(src));
check("TAB_TITLES['system-integration'] bắt đầu bằng 'Tích Hợp Hệ Thống'",
  src.includes("'system-integration': 'Tích Hợp Hệ Thống — "));
// Khớp theo HÀNH VI, không khớp chuỗi tuyệt đối. Phase 78 đổi dòng này thành
// khối nhiều dòng (tab Tích Hợp nay nạp thêm danh sách máy trạm sau khi gộp
// tab "Thiết Bị"), nên so khớp chuỗi sẽ báo đỏ oan dù hàm vẫn được gọi đúng.
check('tab mới được nạp dữ liệu khi mở',
  /if \(tabId === 'system-integration'\)[\s\S]{0,140}loadSystemIntegration\(\)/.test(src));
check('loadSystemIntegration() nạp đủ 4 nhóm dữ liệu Phase 59/60',
  (() => {
    const m = src.match(/function loadSystemIntegration\(\) \{([\s\S]*?)\n\}/);
    if (!m) return false;
    return ['loadPluginRegistryStats', 'loadBackgroundTasks', 'loadWebhookAlerts', 'loadConnectorConfigAll']
      .every(f => m[1].includes(f + '()'));
  })());

results.push('▸ Không còn tải tích hợp từ Trung Tâm Chỉ Huy');
{
  const m = src.match(/(?:async )?function onEnter\(\) \{([\s\S]*?)\n  \}\n\n  function onLeave\(\)/);
  check('tìm thấy onEnter() của CommandCenter', m !== null);
  if (m) {
    const leaked = ['loadPluginRegistryStats', 'loadBackgroundTasks', 'loadWebhookAlerts', 'loadConnectorConfigAll']
      .filter(f => m[1].includes(f));
    check('CommandCenter.onEnter() KHÔNG gọi hàm tải của Phase 59/60',
      leaked.length === 0, leaked.join(','));
    // Phase 79: `syncCommandCenterKpi()` đã bị gỡ. Sau khi tab Trung Tâm Chỉ
    // Huy gộp vào Bảng Điều Khiển, thẻ ô đếm `cc-pending-count` (chỉ để giữ
    // con số cho màn hình C.E.O) biến mất; hàm đó đọc element không còn rồi rơi
    // vào nhánh dự phòng `|| '0'` — ô KPI sẽ hiện 0 bất kể thực tế.
    // Nay `loadPending()` ghi thẳng vào `cc-kpi-pending`.
    // So trên thân hàm ĐÃ BỎ COMMENT: dòng giải thích ngay tại onEnter có
    // nhắc lại đúng tên hàm, quét cả comment sẽ ra kết quả ngược.
    const onEnterCode = m[1].replace(/\/\*[\s\S]*?\*\//g, '').replace(/^\s*\/\/.*$/gm, '');
    check('CommandCenter.onEnter() KHÔNG còn gọi hàm chép KPI đã gỡ',
      !onEnterCode.includes('syncCommandCenterKpi()'), 'còn gọi hàm không tồn tại');
    check('CommandCenter.onEnter() vẫn nạp hàng đợi phê duyệt', onEnterCode.includes('loadPending()'));
  }
}

results.push('▸ Tách hai dải KPI');
{
  // Phase 79: hàm chép KPI của Trung Tâm Chỉ Huy đã bị gỡ (xem giải thích ở
  // trên). Thay vào đó khẳng định ngược lại: ô KPI phê duyệt phải được
  // `loadPending()` ghi thẳng, và không còn chép từ ô đếm trung gian nào.
  check('syncCommandCenterKpi() đã bị gỡ khỏi app.js',
    !/function syncCommandCenterKpi\(/.test(srcCode));
  const loadPending = src.match(/async function loadPending\(\) \{([\s\S]*?)\n  \}/);
  check('tìm thấy loadPending()', loadPending !== null);
  if (loadPending) {
    check('loadPending() ghi thẳng vào ô KPI cc-kpi-pending',
      loadPending[1].includes('cc-kpi-pending'),
      'ghi qua ô trung gian đã xoá thì ô KPI rơi về số 0 bịa');
    check('loadPending() KHÔNG còn đọc ô đếm trung gian đã bị gỡ',
      !loadPending[1].includes("'cc-pending-count'"));
    const foreign = ['cc-kpi-connectors', 'cc-kpi-plugins', 'cc-kpi-bg-tasks', 'cc-kpi-webhook-alerts']
      .filter(id => loadPending[1].includes(id));
    check('loadPending() KHÔNG đụng vào ô KPI của tab tích hợp',
      foreign.length === 0, foreign.join(','));
  }
  check('không còn ô đếm trung gian cc-pending-count trong HTML',
    !html.includes('id="cc-pending-count"'));

  const siKpi = src.match(/function syncIntegrationKpi\(\) \{([\s\S]*?)\n\}/);
  check('tìm thấy syncIntegrationKpi()', siKpi !== null);
  if (siKpi) {
    for (const id of ['cc-kpi-connectors', 'cc-kpi-plugins', 'cc-kpi-bg-tasks', 'cc-kpi-webhook-alerts']) {
      check(`syncIntegrationKpi() cập nhật "${id}"`, siKpi[1].includes(id));
    }
    // Phase 81: KPI không đọc class `bg-emerald-500` trong DOM nữa mà đọc
    // `_ccConnHealth`. Lý do: lưới card được vẽ lại mỗi lần đổi sub-tab, đọc
    // DOM lúc đó trả về 0 dù đã kiểm tra xong — tức số tụt về 0 không rõ lý do.
    check('syncIntegrationKpi() đếm theo kết quả đã lưu, không đọc class trong DOM',
      siKpi[1].includes('_ccConnHealth') && !siKpi[1].includes('bg-emerald-500'));
    // Chưa kiểm tra gì thì phải nói "chờ", không ghi "0/N sẵn sàng" — 0/N đọc
    // thành "cả N cái đều hỏng", trong khi thực tế là chưa biết.
    check('syncIntegrationKpi() không bịa số 0 khi chưa kiểm tra',
      siKpi[1].includes("'chờ'") && siKpi[1].includes('seen.length === 0'));
    // Mẫu số phải cộng cả nguồn tùy chỉnh, không cứng CC_CONNECTORS.length.
    check('syncIntegrationKpi() tính mẫu số trên cả nguồn tùy chỉnh',
      siKpi[1].includes('CC_CONNECTORS.length + custom.length'));
  }

  // Mọi nơi ghi vào ô nguồn phải gọi lại hàm KPI, nếu không số sẽ kẹt.
  for (const [fn, name] of [
    [/async function runConnectorHealth\(connectorName\) \{([\s\S]*?)\n\}/, 'runConnectorHealth'],
    [/async function loadPluginRegistryStats\(\) \{([\s\S]*?)\n\}/, 'loadPluginRegistryStats'],
    [/async function loadBackgroundTasks\(\) \{([\s\S]*?)\n\}/, 'loadBackgroundTasks'],
    [/async function loadWebhookAlerts\(\) \{([\s\S]*?)\n\}/, 'loadWebhookAlerts'],
    [/function onWebhookAlert\(alert\) \{([\s\S]*?)\n\}/, 'onWebhookAlert'],
  ]) {
    const m = src.match(fn);
    check(`tìm thấy ${name}()`, m !== null);
    if (m) check(`${name}() gọi syncIntegrationKpi()`, m[1].includes('syncIntegrationKpi()'));
  }
}

console.log('=== Tab "Tích Hợp Hệ Thống Báo Cáo" — Test ===');
console.log(results.join('\n'));
console.log(`\nTổng: ${pass + fail} | Pass: ${pass} | Fail: ${fail}`);
if (fail > 0) process.exit(1);
console.log('\n✅ TẤT CẢ PASS');
