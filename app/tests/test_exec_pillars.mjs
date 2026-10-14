// SPDX-License-Identifier: Apache-2.0
// Copyright (c) 2026 Dương Thanh Lịch — VN-MateAI. See LICENSE and NOTICE.
// tests/test_exec_pillars.mjs
// Bảng Chỉ Huy C-Level: 6 ô trạng thái lấy từ dữ liệu THẬT của /api/v1/health-dashboard.
//
// Lỗi cũ: cả 6 ô là chữ gõ cứng ("Trực Tuyến", "Trợ Lý Online", "HITL Active", "WAL · Đồng Bộ"…) nên robot
// ngắt, Telegram / 9Router lỗi vẫn hiện xanh; còn ghi "SQLite WAL" khi hệ thống đã chạy PostgreSQL.
//
// Dữ liệu đưa vào có CẤU TRÚC của SYSTEM_HEALTH_CACHE (application/operations/health_monitor.py).
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
const code = cut('const EXEC_STALE_MS', 'const EXEC_STATE_COLOR');
const execPillarsModel = new Function(`${code}; return execPillarsModel;`)();

let passed = 0;
function ok(cond, name, extra = '') {
  if (!cond) { console.error(`FAIL ${name} ${extra}`); process.exit(1); }
  passed += 1;
}

const NOW = Date.UTC(2026, 9, 7, 3, 0, 0);
const healthy = () => ({
  status: 'healthy', last_updated: NOW / 1000 - 2,
  services: {
    llm_9router: { status: 'OK', latency_ms: 245.3, model: 'ag/gemini-3.8-flash-low', detail: 'Model: ag/gemini-3.8-flash-low (245.3ms)' },
    active_directory: { status: 'OK', last_sync: '3 giờ trước', employees_count: 0, computers_count: 0, detail: 'Chưa đồng bộ AD' },
    telegram_gateway: { status: 'OK', detail: 'Telegram Bot Gateway đang chạy' },
    database_sqlite: { status: 'OK', backend: 'postgresql', size_kb: 12800, detail: 'PostgreSQL · 12.5 MB' },
  },
  nodes: { active_lan_clients: 2, active_audio_hardware: 1 },
  counters: { zt_pending: 0 },
});
const model = (d) => execPillarsModel(d, NOW);

// ── 1. Mọi thứ ổn ────────────────────────────────────────────────────────────
let m = model(healthy());
ok(m.pillars.length === 6 && m.bar === 'ok', 'khoẻ: 6 ô, thanh xanh');
ok(m.pillars[0].title === 'Trực Tuyến' && m.pillars[0].state === 'ok', 'cụm trực tuyến');
ok(m.pillars[1].title === '2 Máy Trạm', 'số máy trạm lấy từ dữ liệu');
ok(m.pillars[2].sub === 'ag/gemini-3.8-flash-low · 245 ms', '9Router hiện model + độ trễ thật', m.pillars[2].sub);
ok(m.pillars[3].title === 'Robot Online', 'robot online khi có thiết bị');
ok(m.pillars[4].title === 'Telegram Online' && m.pillars[4].sub === 'Không có yêu cầu chờ duyệt', 'Telegram + hàng chờ');
ok(/^PostgreSQL · 12\.5 MB · AD chưa đồng bộ$/.test(m.pillars[5].sub), 'CSDL nêu đúng backend', m.pillars[5].sub);

// ── 2. Robot ngắt (đúng tình huống người dùng thấy ảnh sai) ─────────────────
let d = healthy(); d.nodes.active_audio_hardware = 0; d.nodes.active_lan_clients = 0;
m = model(d);
ok(m.pillars[3].title === 'Robot Offline' && m.pillars[3].state === 'idle', 'robot ngắt -> Offline, KHÔNG "Trợ Lý Online"');
ok(m.pillars[1].title === 'Chưa Có Máy Trạm', 'không máy trạm -> không khẳng định "Work Nodes sẵn sàng"');
d.nodes.active_audio_hardware = 3;
ok(model(d).pillars[3].title === '3 Robot Online', 'nhiều robot');

// ── 3. Dịch vụ lỗi ──────────────────────────────────────────────────────────
d = healthy(); d.status = 'degraded';
d.services.llm_9router = { status: 'FAIL', latency_ms: 4001, model: 'x', detail: 'ConnectError' };
d.services.telegram_gateway = { status: 'FAIL', detail: 'Chưa cấu hình Bot Token' };
m = model(d);
ok(m.pillars[0].title === 'Suy Giảm' && /9Router/.test(m.pillars[0].sub) && /Telegram/.test(m.pillars[0].sub), 'cụm suy giảm, nêu tên dịch vụ lỗi', m.pillars[0].sub);
ok(m.pillars[2].title === 'Gián Đoạn' && m.pillars[2].state === 'bad', '9Router lỗi -> đỏ');
ok(m.pillars[4].title === 'Telegram Chưa Bật' && m.pillars[4].state === 'idle', 'chưa có token -> "chưa bật", không phải lỗi');
d.services.telegram_gateway = { status: 'FAIL', detail: 'Gateway đang dừng' };
ok(model(d).pillars[4].state === 'bad', 'gateway dừng -> đỏ');
ok(m.bar === 'bad', 'một ô đỏ -> thanh đỏ');

// ── 4. CSDL chính chết (đêm 2026-10-06: PostgreSQL mất kết nối) ──────────────
d = healthy(); d.status = 'degraded';
d.services.database_sqlite = { status: 'FAIL', backend: 'unknown', detail: 'CSDL chính lỗi: OperationalError' };
m = model(d);
ok(m.pillars[5].title === 'CSDL Lỗi' && m.pillars[5].state === 'bad', 'CSDL chính lỗi -> đỏ');
ok(/CSDL/.test(m.pillars[0].sub), 'cụm nêu CSDL');

// ── 5. HITL: hàng chờ duyệt ─────────────────────────────────────────────────
d = healthy(); d.counters.zt_pending = 2;
m = model(d);
ok(m.pillars[4].sub === '2 yêu cầu chờ duyệt' && m.pillars[4].state === 'warn', 'có yêu cầu chờ duyệt -> cảnh báo vàng');

// ── 6. Dữ liệu cũ / mất liên lạc / chưa có dữ liệu ──────────────────────────
d = healthy(); d.last_updated = NOW / 1000 - 120;
m = model(d);
ok(m.pillars[0].title === 'Dữ liệu cũ' && m.bar === 'warn', 'bộ giám sát nền dừng -> "Dữ liệu cũ"');
m = model(null);
ok(m.pillars[0].title === 'Mất liên lạc' && m.bar === 'bad', 'không đọc được máy chủ -> "Mất liên lạc"');
ok(m.pillars.slice(1).every((p) => p.state === 'idle' && p.title === '—'), 'các ô khác không đoán khi mất liên lạc');
d = { status: 'healthy', last_updated: NOW / 1000, services: {}, nodes: {}, counters: {} };
ok(model(d).pillars[0].title === 'Đang kiểm tra…', 'chưa có dịch vụ nào -> "Đang kiểm tra", không báo xanh');

// ── 7. Không còn chữ gõ cứng ────────────────────────────────────────────────
for (const lie of ['Trợ Lý Online', 'HITL Active', 'Work Nodes', 'WAL · Đồng Bộ', 'Claude · GPT · Gemini', 'OpenClaw RPA Sẵn Sàng']) {
  ok(!html.includes(lie), `index.html không còn chữ cứng "${lie}"`);
}
for (const id of ['exec-cluster-status-text', 'exec-p1-sub', 'exec-p1-dot', 'exec-bar-dot', 'exec-p2-title', 'exec-p2-sub', 'exec-p3-title',
  'exec-p3-sub', 'exec-p4-title', 'exec-p4-sub', 'exec-p5-title', 'exec-p5-sub', 'exec-p6-title', 'exec-p6-sub']) {
  ok(html.includes(`id="${id}"`), `index.html có #${id}`);
}
ok(/renderExecutivePillars\(data\);/.test(app) && /async function fetchAndRenderHealthDashboard\(\) \{\n  const data = await apiGetHealthDashboard\(\);\n  renderExecutivePillars\(data\);/.test(app),
  'vòng cập nhật gọi renderExecutivePillars cả khi data = null');

console.log(`test_exec_pillars: ${passed} passed`);
