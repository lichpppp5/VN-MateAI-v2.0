// SPDX-License-Identifier: Apache-2.0
// Copyright (c) 2026 Dương Thanh Lịch — VN-MateAI. See LICENSE and NOTICE.
/**
 * web/hud-visual.js — LỚP HÌNH của HUD (/hud) "buồng lái AI". Bộ não (WebSocket, micro, phát tiếng, phê duyệt, thanh lệnh)
 * nằm ở web/hud.js và điều khiển lớp này qua `window.HUDVisual` (setState, setQuery, transcript, thoughts, setLevelSource…).
 *
 * - Lõi AI là nhân vật chính, đổi hình theo trạng thái: chờ (thở chậm, cyan) · đang nghe (hạt hút vào tâm, xanh lá) · suy nghĩ (quỹ đạo
 *   quay nhanh, mạng nơ-ron loé tín hiệu, mảnh suy nghĩ bay ra, tím) · đang nói (lõi đập theo MỨC ÂM THANH THẬT của giọng đọc, hổ phách)
 *   · cần phê duyệt / sự cố (đỏ).
 * - Quỹ đạo TRONG: module của VN-MateAI (mô hình AI, kho tri thức, máy chủ, giám sát, kịch bản, phê duyệt, Telegram, robot), nối với nhau
 *   theo luồng thật. Quỹ đạo NGOÀI: agent của doanh nghiệp (MCP, A2A, gọi vào, Dev Fleet, máy trạm — Danh bạ Agent). Mỗi lần AI gọi công cụ
 *   (sự kiện topology thật) bắn một tia tới đúng nút: tím = đang chạy, xanh = xong, vàng = chờ duyệt, đỏ = lỗi.
 * - Bảng số liệu THẬT khi trình duyệt đã đăng nhập portal: /api/v1/ops/overview, /api/v1/health-dashboard, /api/v1/system/topology/events,
 *   /api/v1/agent-gateway/agents, /api/v1/llmops/budget. Chưa đăng nhập → ô số liệu để trống ("–") và ghi rõ cần đăng nhập; KHÔNG có số mô phỏng.
 * - Tự hạ chất lượng khi máy yếu (FPS < 42); ?lite ép chế độ nhẹ. ?nointro / ?freeze / ?state= / ?hover / ?bench chỉ để chụp ảnh, đo hiệu năng.
 */
(() => {
  'use strict';
  const $ = (id) => document.getElementById(id);
  const esc = (s) => String(s == null ? '' : s).replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
  const lerp = (a, b, t) => a + (b - a) * t;
  const clamp01 = (x) => Math.max(0, Math.min(1, x));
  const easeOutBack = (x) => { const c1 = 1.70158, c3 = c1 + 1; return 1 + c3 * Math.pow(x - 1, 3) + c1 * Math.pow(x - 1, 2); };
  const TOKEN = (() => { try { return localStorage.getItem('vnmateai_token') || ''; } catch (_) { return ''; } })();

  // ── trạng thái ────────────────────────────────────────────────────────────
  const STATES = {
    idle:      { hue: 190, label: 'STANDBY',        spin: 0.12, ring: 0.25, pull: 0,    noise: 0.02, pulse: 0.03, lattice: 0.35 },
    listening: { hue: 152, label: 'ĐANG NGHE',      spin: 0.22, ring: 0.5,  pull: 0.35, noise: 0.05, pulse: 0.0,  lattice: 0.5 },
    thinking:  { hue: 272, label: 'ĐANG SUY NGHĨ',  spin: 1.1,  ring: 1.6,  pull: 0.1,  noise: 0.16, pulse: 0.02, lattice: 1.0 },
    speaking:  { hue: 36,  label: 'ĐANG TRẢ LỜI',   spin: 0.35, ring: 0.6,  pull: 0,    noise: 0.04, pulse: 0.0,  lattice: 0.55 },
    alert:     { hue: 352, label: 'CẦN PHÊ DUYỆT',          spin: 0.6,  ring: 1.0,  pull: 0,    noise: 0.09, pulse: 0.08, lattice: 0.8 },
  };
  let state = 'idle';
  const cur = { ...STATES.idle };
  let level = 0;
  let speakUntil = 0, wordTimer = null;
  let lastManual = 0;
  let aiName = 'Ly Ly';
  let levelSource = null;                  // hud.js cấp: () => mức âm thanh thật 0..1, hoặc null khi không phát tiếng
  const shocks = [];                       // sóng xung kích khi đổi trạng thái
  // MỘT đồng hồ cho mọi hiệu ứng: dấu thời gian của requestAnimationFrame. Trộn với performance.now() làm tiến độ âm
  // (hai đồng hồ lệch nhau) → bán kính âm → canvas ném lỗi và mọi lớp vẽ sau đó trong khung bị mất.
  let clockNow = performance.now();
  // Góc quay tích luỹ theo dt. Trước đây góc = t × tốc độ (t = giây từ lúc mở trang): đổi trạng thái là tốc độ đổi →
  // góc nhảy cả trăm độ trong vài khung (mở trang càng lâu nhảy càng mạnh) — chính là "rung giật" khi chuyển trạng thái.
  const ang = { core: 0, reticle: 0, rings: [0, 0, 0] };
  let cssHue = -1;

  function setState(s, opts = {}) {
    if (!STATES[s]) return;
    if (s !== state) shocks.push({ t0: clockNow, hue: STATES[s].hue });
    state = s;
    if ($('state-pill').dataset.t !== STATES[s].label) swapLabel($('state-pill'), STATES[s].label);
    document.body.classList.toggle('alert', s === 'alert');
    if (opts.query !== undefined) $('query').textContent = opts.query ? `“${opts.query}”` : '';
    if (s === 'speaking' && opts.text) speak(opts.text, opts.durationMs);
    if (s === 'listening') subtitle(opts.text || 'Em đang lắng nghe…', true);
    if (s === 'thinking') { subtitle(opts.text || 'Em đang suy nghĩ…', true); if (opts.thoughts) thoughts(opts.thoughts); }
    if (s === 'alert' && opts.text) subtitle(opts.text, true);
    if (s === 'idle' && opts.text !== undefined) subtitle(opts.text || 'Sẵn sàng nhận lệnh.', true);
  }

  // chữ chạy ký tự ngẫu nhiên rồi "giải mã" dần từ trái sang phải
  const GLYPHS = '01ABCDEF#%&*<>/\\|=+ΞΣΔ';
  // Đổi chữ trạng thái: mờ dần rồi hiện chữ mới (CSS .swap). Trước đây chữ nhảy ký tự ngẫu nhiên + cả khung rung
  // (glitch) mỗi lần đổi trạng thái — người dùng thấy giật màn hình. scramble chỉ còn chạy một lần lúc khởi động.
  function swapLabel(el, text) {
    el.dataset.t = text;
    el.classList.remove('swap'); void el.offsetWidth; el.classList.add('swap');
    setTimeout(() => { if (el.dataset.t === text) el.textContent = text; }, 160);
  }
  function scramble(el, text, ms = 520) {
    el.dataset.t = text;
    const start = performance.now();
    const step = () => {
      if (el.dataset.t !== text) return;
      // đo bằng performance.now() (cùng đồng hồ với `start`) — không dùng dấu thời gian của requestAnimationFrame
      const p = Math.max(0, Math.min(1, (performance.now() - start) / ms)), k = Math.floor(p * text.length);
      el.textContent = text.slice(0, k) + [...text.slice(k)].map((c) => (c === ' ' ? ' ' : GLYPHS[(Math.random() * GLYPHS.length) | 0])).join('');
      if (p < 1) requestAnimationFrame(step); else el.textContent = text;
    };
    requestAnimationFrame(step);
    setTimeout(() => { if (el.dataset.t === text) el.textContent = text; }, ms + 80);   // chắc chắn hiện chữ đúng khi tab bị giảm khung hình
  }

  // ── phụ đề kiểu điện ảnh ─────────────────────────────────────────────────
  function subtitle(text, allOn) {
    clearInterval(wordTimer);
    const words = String(text || '').split(/\s+/).filter(Boolean);
    const el = $('subtitle'); el.classList.remove('soft'); void el.offsetWidth; el.classList.add('soft');
    el.innerHTML = `<span class="who on">${esc(aiName.toUpperCase())}</span>` + words.map((w) => `<span${allOn ? ' class="on"' : ''}>${esc(w)} </span>`).join('');
  }
  // durationMs = thời lượng THẬT của đoạn audio (hud.js đo từ trình phát) → chữ sáng dần khớp với tiếng
  function speak(text, durationMs) {
    subtitle(text, false);
    const spans = [...$('subtitle').querySelectorAll('span:not(.who)')];
    let i = 0;
    const per = durationMs > 0 ? Math.max(90, Math.min(650, (durationMs * 0.95) / Math.max(spans.length, 1)))
      : Math.max(140, Math.min(320, 5200 / Math.max(spans.length, 1)));
    speakUntil = clockNow + spans.length * per + 600;
    wordTimer = setInterval(() => { if (i < spans.length) spans[i++].classList.add('on'); else clearInterval(wordTimer); }, per);
  }

  // ── canvas ────────────────────────────────────────────────────────────────
  const cv = $('core'), ctx = cv.getContext('2d');
  let W = 0, H = 0, DPR = 1, CX = 0, CY = 0, R = 0;
  function resize() {
    DPR = Math.min(window.devicePixelRatio || 1, quality === 1 ? 1.25 : 1);
    W = window.innerWidth; H = window.innerHeight;
    cv.width = W * DPR; cv.height = H * DPR; cv.style.width = W + 'px'; cv.style.height = H + 'px';
    ctx.setTransform(DPR, 0, 0, DPR, 0, 0);
    CX = W / 2; CY = H * 0.455; R = Math.min(H * 0.27, W * 0.17);   // màn rộng: lõi to theo chiều cao, chặn theo chiều ngang
    buildHex();
    const c = $('center') && $('center').getBoundingClientRect();
    CENTER_HALF = c && c.width ? c.width / 2 : W * 0.25;
  }
  let CENTER_HALF = 0;
  window.addEventListener('resize', resize);

  // hạt trên mặt cầu (Fibonacci) + mạng nơ-ron giữa các hạt gần nhau
  const N = 1700, pts = [];
  for (let i = 0; i < N; i++) {
    const y = 1 - (i / (N - 1)) * 2, r = Math.sqrt(1 - y * y), th = i * 2.399963;
    pts.push({ x: Math.cos(th) * r, y, z: Math.sin(th) * r, s: Math.random() });
  }
  const PX = new Float32Array(N), PY = new Float32Array(N), PD = new Float32Array(N);
  const links = [];
  for (let k = 0; k < 720; k++) {
    const i = Math.floor(Math.random() * N); let best = -1, bd = 1e9;
    for (let j = 0; j < N; j += 1) {
      if (j === i) continue;
      const d = (pts[i].x - pts[j].x) ** 2 + (pts[i].y - pts[j].y) ** 2 + (pts[i].z - pts[j].z) ** 2;
      if (d < bd && d > 0.004) { bd = d; best = j; }
    }
    if (best >= 0) links.push([i, best, Math.random()]);
  }

  const adj = Array.from({ length: N }, () => []);
  links.forEach(([a, b], li) => { adj[a].push(li); adj[b].push(li); });

  // xung synapse (chạy dọc một nhánh), nơ-ron loé sáng, xung trên sợi thần kinh tới agent
  const synapses = [], fires = [], nerves = [];
  function fireNeuron(i, strength = 1) {
    fires.push({ i, t0: clockNow, s: strength });
    for (const li of adj[i].slice(0, 4)) synapses.push({ li, from: i, t0: clockNow + Math.random() * 120, dur: 380 + Math.random() * 260 });
  }

  // chuột: nơ-ron quanh con trỏ bừng sáng; rê lên agent → sợi sáng + thẻ thông tin; bấm lõi / agent → phát xung
  const mouse = { x: -1e4, y: -1e4, on: false };
  let hovered = null;

  // lưới tổ ong năng lượng quanh lõi (dựng lại khi đổi kích thước)
  let hexCells = [];
  function buildHex() {
    hexCells = [];
    const sz = R * 0.17, w = sz * Math.sqrt(3), h = sz * 1.5, lim = R * 3.0;
    for (let row = -30; row <= 30; row++) {
      for (let col = -40; col <= 40; col++) {
        const x = CX + col * w + (row & 1 ? w / 2 : 0), y = CY + row * h, d = Math.hypot(x - CX, (y - CY) * 1.35);
        if (d > R * 1.45 && d < lim) hexCells.push({ x, y, d, sz: sz * 0.92 });
      }
    }
  }
  const bolts = [];                                         // tia điện (khi nói / sự cố)

  // Tối ưu: gom nét vẽ cùng màu vào nhóm, mỗi nhóm đặt màu + stroke MỘT lần (thay vì hàng nghìn lần mỗi khung)
  const BUCKET = Array.from({ length: 12 }, () => []);
  const HEX_COS = [0, 1, 2, 3, 4, 5].map((k) => Math.cos(Math.PI / 3 * k)), HEX_SIN = [0, 1, 2, 3, 4, 5].map((k) => Math.sin(Math.PI / 3 * k));
  // Tự hạ chất lượng khi máy yếu: FPS < 42 suốt 1,5 s → chế độ nhẹ (nửa số hạt / nhánh, tắt lưới tổ ong, độ phân giải thấp hơn);
  // FPS > 57 suốt 6 s → trở lại. ?lite ép chế độ nhẹ.
  let quality = new URLSearchParams(location.search).has('lite') ? 0 : 1, fpsAvg = 60, slowFor = 0, fastFor = 0;
  function adaptQuality(realDt) {
    if (!realDt) return;
    fpsAvg = lerp(fpsAvg, 1 / realDt, 0.05);
    if (quality === 1) { slowFor = fpsAvg < 42 ? slowFor + realDt : 0; if (slowFor > 1.5) setQuality(0); }
    else if (!new URLSearchParams(location.search).has('lite')) { fastFor = fpsAvg > 57 ? fastFor + realDt : 0; if (fastFor > 6) setQuality(1); }
  }
  function setQuality(q) {
    quality = q; slowFor = fastFor = 0; resize();
    const chip = $('perf-chip'); if (chip) { chip.textContent = q ? '' : 'Chế độ nhẹ'; chip.classList.toggle('on', !q); }
  }

  // sao bay sâu phía sau
  const stars = Array.from({ length: 240 }, () => ({ x: (Math.random() - 0.5) * 2, y: (Math.random() - 0.5) * 2, z: Math.random() }));

  // chòm sao agent
  // Module của chính VN-MateAI — quỹ đạo TRONG. Agent / hệ thống ngoài — quỹ đạo NGOÀI.
  const FIXED = [
    { id: 'llm', label: 'Mô hình AI', kind: 'core', icon: 'AI', info: ['Bộ não ngôn ngữ — mỗi lượt hội thoại đi qua đây'] },
    { id: 'rag', label: 'Kho tri thức', kind: 'core', icon: 'K', info: ['Tài liệu nội bộ (RAG), phân quyền theo phòng ban'] },
    { id: 'core-server', label: 'Máy chủ', kind: 'core', icon: 'S', info: ['Công cụ chạy ngay trên máy chủ VN-MateAI'] },
    { id: 'monitor', label: 'Giám sát', kind: 'core', icon: 'G', info: ['Prometheus / Grafana: cảnh báo, sự cố hạ tầng'] },
    { id: 'playbooks', label: 'Kịch bản', kind: 'core', icon: 'P', info: ['Kịch bản vận hành: tự chạy theo lịch / cảnh báo / webhook'] },
    { id: 'hitl', label: 'Phê duyệt', kind: 'core', icon: '✓', info: ['Hàng đợi duyệt: tác vụ rủi ro cao chờ người có thẩm quyền'] },
    { id: 'telegram', label: 'Telegram', kind: 'core', icon: 'T', info: ['Kênh cảnh báo + duyệt từ điện thoại'] },
    { id: 'robot', label: 'Robot', kind: 'core', icon: 'R', info: ['Robot ESP32 / Xiaozhi nói chuyện trực tiếp với Ly Ly'] },
  ];
  // các module liên quan nối với nhau (luồng thật của hệ thống)
  const MODULE_LINKS = [['monitor', 'playbooks'], ['playbooks', 'hitl'], ['hitl', 'telegram'], ['monitor', 'telegram'],
    ['llm', 'rag'], ['llm', 'core-server'], ['robot', 'llm'], ['core-server', 'monitor']];
  const byId = new Map();
  const linkPulses = []; let nextLinkPulse = 0;
  const KIND_ICON = { mcp: 'M', a2a: 'A', inbound: 'I', devfleet: 'D', workstations: 'W' };
  const KIND_HUE = { core: 190, mcp: 200, a2a: 150, inbound: 215, devfleet: 38, workstations: 170 };
  const KIND_NAME = { core: 'Thành phần lõi', mcp: 'MCP server', a2a: 'Agent A2A (được giao việc)', inbound: 'Agent gọi vào', devfleet: 'Dev Fleet', workstations: 'Máy trạm' };
  let nodes = [];
  function setNodes(list) {
    const keep = new Map(nodes.map((n) => [n.id, n]));
    const outAngle = (i, total) => {                    // agent ngoài: chia đều hai cung trái / phải, tránh đỉnh và đáy
      const side = i % 2, idx = i >> 1, m = Math.ceil((total - side) / 2) || 1;
      return (side ? Math.PI : 0) + (m === 1 ? 0 : -0.85 + (1.7 * idx) / (m - 1));
    };
    const make = (n, i, total, ring) => ({ ...n, ring, a0: ring === 'out' ? outAngle(i, total) : (i / total) * Math.PI * 2,
      glow: (keep.get(n.id) || {}).glow || 0, ph: Math.random() * 6.28, nextNerve: 0, info: n.info || (keep.get(n.id) || {}).info });
    const agents = list.map((n) => ({ ...n, icon: KIND_ICON[n.kind] || '•' }));
    nodes = [...FIXED.map((n, i) => make(n, i, FIXED.length, 'in')), ...agents.map((n, i) => make(n, i, Math.max(agents.length, 1), 'out'))];
    byId.clear(); nodes.forEach((n) => byId.set(n.id, n));
  }

  const beams = [], frags = [], ripples = [];
  // Sợi thần kinh lõi → agent: bezier bậc ba, hai điểm điều khiển lắc nhẹ theo thời gian (trông như sợi sống).
  function nerveCurve(n, t) {
    const a = Math.atan2(n.y - CY, n.x - CX), sx = CX + Math.cos(a) * R * 0.92, sy = CY + Math.sin(a) * R * 0.85;
    const dx = n.x - sx, dy = n.y - sy, len = Math.hypot(dx, dy) || 1, px = -dy / len, py = dx / len;
    const w1 = Math.sin(t * 0.9 + n.ph) * R * 0.32, w2 = Math.cos(t * 0.7 + n.ph * 1.3) * R * 0.24;
    return [sx, sy, sx + dx * 0.33 + px * w1, sy + dy * 0.33 + py * w1, sx + dx * 0.68 + px * w2, sy + dy * 0.68 + py * w2, n.x, n.y];
  }
  function bez(c, u) {
    const v = 1 - u;
    return [v * v * v * c[0] + 3 * v * v * u * c[2] + 3 * v * u * u * c[4] + u * u * u * c[6],
      v * v * v * c[1] + 3 * v * v * u * c[3] + 3 * v * u * u * c[5] + u * u * u * c[7]];
  }
  function beam(nodeId, status) {
    const n = nodes.find((x) => x.id === nodeId) || nodes[0];
    if (!n) return;
    const color = { ok: '#34f5a4', error: '#ff4d6d', waiting: '#ffb547', running: '#c39bff' }[status] || '#7fe9ff';
    beams.push({ n, color, t0: clockNow, dur: 1300, arrived: false });
  }
  function thoughts(text) {
    const parts = String(text).split(/[.;:\n]|, /).map((s) => s.trim()).filter((s) => s.length > 3).slice(0, 8);
    const start = Math.random() * Math.PI * 2;
    parts.forEach((p, i) => setTimeout(() => {
      frags.push({ text: p.length > 42 ? p.slice(0, 41) + '…' : p, a: start + (i / parts.length) * Math.PI * 2, d: R * 1.15, t0: clockNow, dur: 5600 });
    }, i * 600));
  }

  const NOINTRO = new URLSearchParams(location.search).has('nointro');
  let BOOT = null, t0 = null;              // lấy từ khung đầu tiên (cùng đồng hồ với `now`)

  // Một lỗi vẽ không được làm đứng cả HUD: ghi lại (data-render-error) và vẫn chạy khung sau.
  function frame(now) {
    try { draw(now); } catch (err) {
      document.body.dataset.renderError = String(err && err.message || err).slice(0, 200);
      if (!frame.warned) { frame.warned = true; console.error('[hud-next] lỗi vẽ:', err); }
    }
    // ?freeze: dừng trên một khung hoàn chỉnh sau 3 giây (chụp ảnh xem trước)
    if (!(FREEZE && BOOT !== null && now - (NOINTRO ? BOOT + 1e6 : BOOT) > 3000)) requestAnimationFrame(frame);
    else if (!frame.snap) {                            // chụp xem trước: thay canvas bằng ảnh của chính nó (ảnh chụp headless bỏ sót lớp vẽ sau)
      frame.snap = true;
      const img = new Image(); img.src = cv.toDataURL('image/png');
      img.style.cssText = 'position:fixed;inset:0;width:100%;height:100%;z-index:1'; cv.replaceWith(img);
    }
  }
  const FREEZE = new URLSearchParams(location.search).has('freeze');

  function draw(now) {
    clockNow = now;
    if (BOOT === null) { BOOT = NOINTRO ? now - 1e6 : now; t0 = now; if (!NOINTRO) shocks.push({ t0: now + 300, hue: 190 }); }
    const realDt = Math.max(0, Math.min(1, (now - t0) / 1000)), dt = Math.min(0.05, realDt); t0 = now;
    adaptQuality(realDt);
    const tgt = STATES[state];
    const ease = 1 - Math.exp(-realDt * 4.5);
    const dh = ((tgt.hue - cur.hue + 540) % 360) - 180; cur.hue = (cur.hue + dh * ease + 360) % 360;
    for (const k of ['spin', 'ring', 'pull', 'noise', 'pulse', 'lattice']) cur[k] = lerp(cur[k], tgt[k], 1 - Math.exp(-realDt * 2.6));
    ang.core += dt * cur.spin; ang.reticle += dt * 0.22 * (0.5 + cur.ring);
    for (let i = 0; i < 3; i++) ang.rings[i] += (i % 2 ? -1 : 1) * dt * cur.ring * (0.6 + i * 0.25);
    // màu panel / chữ đổi DẦN theo lõi (chỉ ghi biến CSS khi lệch ≥ 1°, tránh tính lại style mỗi khung)
    if (Math.abs(((cur.hue - cssHue + 540) % 360) - 180) >= 1) { cssHue = Math.round(cur.hue); document.documentElement.style.setProperty('--c', cssHue); }
    let real = null;
    if (levelSource) { try { real = levelSource(); } catch (_) { real = null; } }
    const speakingNow = state === 'speaking' && (real != null || now < speakUntil);
    const want = real != null && state === 'speaking' ? 0.2 + real * 0.9 :
      speakingNow ? 0.45 + 0.55 * Math.abs(Math.sin(now / 95) * Math.sin(now / 233)) :
      state === 'listening' ? 0.25 + 0.4 * Math.abs(Math.sin(now / 170) * Math.cos(now / 410)) : 0;
    level = lerp(level, want, 1 - Math.exp(-realDt * (real != null ? 18 : 11)));
    // không có tiếng thật (robot / thiết bị khác đang nói, hoặc TTS lỗi): hết chữ thì tự về chờ
    if (state === 'speaking' && real == null && now > speakUntil + 1500 && Date.now() - lastManual > 400) setState('idle');

    const boot = clamp01((now - BOOT) / 1500), bootS = easeOutBack(boot);
    const hue = Math.round(cur.hue), t = now / 1000, HQ = quality === 1;
    ctx.clearRect(0, 0, W, H);

    // 1. sao bay sâu — 4 nhóm độ sáng, mỗi nhóm một lần đặt màu
    const starN = HQ ? stars.length : stars.length >> 1;
    for (let g = 0; g < 4; g++) BUCKET[g].length = 0;
    for (let i = 0; i < starN; i++) {
      const s = stars[i];
      s.z -= dt * (0.035 + cur.spin * 0.03); if (s.z <= 0.02) { s.z = 1; s.x = (Math.random() - 0.5) * 2; s.y = (Math.random() - 0.5) * 2; }
      const k = 0.5 / s.z, x = CX + s.x * k * W * 0.5, y = CY + s.y * k * H * 0.5;
      if (x < 0 || x > W || y < 0 || y > H) continue;
      BUCKET[Math.min(3, ((1 - s.z) * 4) | 0)].push(x, y);
    }
    for (let g = 0; g < 4; g++) {
      const b = BUCKET[g]; if (!b.length) continue;
      ctx.fillStyle = `hsla(${hue},80%,80%,${0.12 + g * 0.17})`; const sz = 0.6 + g * 0.5;
      for (let j = 0; j < b.length; j += 2) ctx.fillRect(b[j], b[j + 1], sz, sz);
    }

    // 1b. lưới tổ ong (chỉ chế độ đẹp): 4 mức sáng, mỗi mức MỘT path
    if (HQ) {
      const shockR = shocks.map((sk) => (now - sk.t0) / 1600).filter((p) => p >= 0 && p <= 1).map((p) => R * (0.9 + (1 - Math.pow(1 - p, 3)) * 2.4));
      for (let g = 0; g < 4; g++) BUCKET[g].length = 0;
      for (const c of hexCells) {
        const wave = Math.max(0, Math.sin(c.d / (R * 0.32) - t * 1.8));
        let al = Math.pow(wave, 6) * 0.11 * (0.6 + cur.lattice);
        for (const sr of shockR) { const dd = Math.abs(c.d - sr); if (dd < R * 0.25) al += (1 - dd / (R * 0.25)) * 0.18; }
        if (al < 0.02) continue;
        BUCKET[Math.min(3, (al * 12) | 0)].push(c);
      }
      ctx.lineWidth = 1;
      for (let g = 0; g < 4; g++) {
        const b = BUCKET[g]; if (!b.length) continue;
        ctx.strokeStyle = `hsla(${hue},100%,65%,${0.04 + g * 0.09})`; ctx.beginPath();
        for (const c of b) { ctx.moveTo(c.x + c.sz, c.y); for (let k = 1; k <= 6; k++) ctx.lineTo(c.x + HEX_COS[k % 6] * c.sz, c.y + HEX_SIN[k % 6] * c.sz); }
        ctx.stroke();
      }
    }

    // 2. quầng sáng nền
    const breathe = (1 + Math.sin(t * 1.6) * cur.pulse + level * 0.18) * bootS;
    const g0 = ctx.createRadialGradient(CX, CY, 0, CX, CY, R * 3.1 * breathe + 1);
    g0.addColorStop(0, `hsla(${hue},95%,62%,0.30)`); g0.addColorStop(0.3, `hsla(${hue},95%,50%,0.10)`); g0.addColorStop(1, 'transparent');
    ctx.fillStyle = g0; ctx.fillRect(0, 0, W, H);

    // 3. hai quỹ đạo: TRONG = module hệ thống, NGOÀI = agent của doanh nghiệp
    // quỹ đạo trong ngay ngoài mép lõi (mép quả cầu nhìn thấy ≈ 0,85R); quỹ đạo ngoài dẹt, gọn giữa thanh tiêu đề và phụ đề
    const A1 = Math.min(R * 1.5, CENTER_HALF * 0.62), B1 = R * 0.98;
    const A2 = Math.max(A1 + 70, Math.min(R * 2.42, CENTER_HALF - 36)), B2 = Math.min(R * 1.35, CY - 110);
    nodes.forEach((n) => {
      const inner = n.ring === 'in', a = inner ? n.a0 - t * 0.045 : n.a0 + Math.sin(t * 0.15 + n.ph) * 0.1;   // module quay; agent lắc nhẹ tại chỗ
      n.x = CX + Math.cos(a) * (inner ? A1 : A2) * bootS; n.y = CY + Math.sin(a) * (inner ? B1 : B2) * bootS;
      n.z = Math.sin(a); n.glow = Math.max(0, (n.glow || 0) - dt * 0.7);
    });
    ctx.lineWidth = 1;
    ctx.strokeStyle = `hsla(${hue},90%,60%,0.10)`;
    ctx.beginPath(); ctx.ellipse(CX, CY, A1 * bootS + 0.1, B1 * bootS + 0.1, 0, 0, Math.PI * 2); ctx.stroke();
    ctx.setLineDash([4, 10]);                          // quỹ đạo ngoài chỉ vẽ hai cung có agent
    for (const c of [0, Math.PI]) { ctx.beginPath(); ctx.ellipse(CX, CY, A2 * bootS + 0.1, B2 * bootS + 0.1, 0, c - 1.0, c + 1.0); ctx.stroke(); }
    ctx.setLineDash([]);
    nodes.forEach((n) => { if (n.z < 0) drawNode(n); });

    // 3b. kết nối: lõi → mọi nút (sợi thần kinh) + module ↔ module liên quan; xung chạy dọc sợi
    ctx.globalCompositeOperation = 'lighter';
    for (const [ia, ib] of MODULE_LINKS) {
      const a = byId.get(ia), b = byId.get(ib); if (!a || !b) continue;
      const mx = (a.x + b.x) / 2 + (CX - (a.x + b.x) / 2) * 0.25, my = (a.y + b.y) / 2 + (CY - (a.y + b.y) / 2) * 0.25;
      const hot = Math.max(a === hovered || b === hovered ? 1 : 0, a.glow, b.glow);
      ctx.strokeStyle = `hsla(${hue},100%,72%,${0.16 + hot * 0.5})`; ctx.lineWidth = 1 + hot;
      ctx.beginPath(); ctx.moveTo(a.x, a.y); ctx.quadraticCurveTo(mx, my, b.x, b.y); ctx.stroke();
    }
    for (const n of nodes) {
      const c = nerveCurve(n, t), hot = n === hovered ? 1 : n.glow, nh = KIND_HUE[n.kind] ?? 190;
      ctx.beginPath(); ctx.moveTo(c[0], c[1]); ctx.bezierCurveTo(c[2], c[3], c[4], c[5], c[6], c[7]);
      if (HQ) { ctx.strokeStyle = `hsla(${(hue + nh) >> 1},100%,60%,${0.13 + hot * 0.3})`; ctx.lineWidth = 6 + hot * 6; ctx.stroke(); }
      ctx.strokeStyle = `hsla(${(hue + nh) >> 1},100%,76%,${(n.ring === 'in' ? 0.6 : 0.5) + hot * 0.4})`; ctx.lineWidth = 1.2 + hot * 1.4; ctx.stroke();
      if (now > n.nextNerve) {
        nerves.push({ n, t0: now, dur: 1400 + Math.random() * 900, back: Math.random() < 0.45 });
        n.nextNerve = now + 1800 + Math.random() * (state === 'thinking' ? 1200 : 4200);
      }
    }
    if (now > nextLinkPulse && MODULE_LINKS.length) {
      const [ia, ib] = MODULE_LINKS[(Math.random() * MODULE_LINKS.length) | 0];
      if (byId.get(ia) && byId.get(ib)) linkPulses.push({ a: byId.get(ia), b: byId.get(ib), t0: now, dur: 1100 });
      nextLinkPulse = now + 700 + Math.random() * 1600;
    }
    ctx.fillStyle = `hsl(${hue},100%,86%)`;
    for (let i = nerves.length - 1; i >= 0; i--) {
      const p = nerves[i], q = (now - p.t0) / p.dur;
      if (q > 1) { nerves.splice(i, 1); continue; }
      if (q < 0) continue;
      const c = nerveCurve(p.n, t);
      ctx.beginPath();
      for (let k = 0; k < 5; k++) {
        const u = p.back ? 1 - (q - k * 0.025) : q - k * 0.025; if (u < 0 || u > 1) continue;
        const [x, y] = bez(c, u), r = 2.6 - k * 0.45; ctx.moveTo(x + r, y); ctx.arc(x, y, r, 0, Math.PI * 2);
      }
      ctx.fill();
    }
    for (let i = linkPulses.length - 1; i >= 0; i--) {
      const p = linkPulses[i], q = (now - p.t0) / p.dur;
      if (q > 1) { linkPulses.splice(i, 1); continue; }
      if (q < 0) continue;
      const mx = (p.a.x + p.b.x) / 2 + (CX - (p.a.x + p.b.x) / 2) * 0.25, my = (p.a.y + p.b.y) / 2 + (CY - (p.a.y + p.b.y) / 2) * 0.25, v = 1 - q;
      const x = v * v * p.a.x + 2 * v * q * mx + q * q * p.b.x, y = v * v * p.a.y + 2 * v * q * my + q * q * p.b.y;
      ctx.beginPath(); ctx.arc(x, y, 2.4, 0, Math.PI * 2); ctx.fill();
    }
    ctx.globalCompositeOperation = 'source-over';

    // 4. khung ngắm quay (4 cung) — không đổ bóng
    const RS = R * 1.2 * bootS;
    ctx.save(); ctx.translate(CX, CY); ctx.rotate(ang.reticle);
    ctx.strokeStyle = `hsla(${hue},100%,72%,0.8)`; ctx.lineWidth = 2.2; ctx.beginPath();
    for (let q = 0; q < 4; q++) { const a = q * Math.PI / 2 + Math.PI / 4; ctx.moveTo(Math.cos(a - 0.17) * RS, Math.sin(a - 0.17) * RS); ctx.arc(0, 0, RS, a - 0.17, a + 0.17); }
    ctx.stroke(); ctx.restore();

    // 5. vòng quỹ đạo quanh lõi + vệ tinh (không đổ bóng)
    for (let i = 0; i < 3; i++) {
      const rr = R * (1.03 + i * 0.06) * bootS, sp = ang.rings[i], ry = rr * 0.9;
      ctx.save(); ctx.translate(CX, CY); ctx.rotate(sp);
      ctx.strokeStyle = `hsla(${hue},95%,${60 + i * 6}%,${0.5 - i * 0.12})`; ctx.lineWidth = i === 0 ? 2 : 1.2;
      ctx.setLineDash(i === 1 ? [3, 9] : [R * 0.45, R * 0.18]);
      ctx.beginPath(); ctx.ellipse(0, 0, rr + 0.1, ry + 0.1, 0, 0, Math.PI * 2); ctx.stroke();
      ctx.setLineDash([]);
      const sa = t * (1.2 + i * 0.5) * (i % 2 ? -1 : 1);
      ctx.fillStyle = '#fff'; ctx.beginPath(); ctx.arc(Math.cos(sa) * rr, Math.sin(sa) * ry, 2.4, 0, Math.PI * 2); ctx.fill();
      ctx.restore();
    }

    // 6. sóng âm tròn — MỘT path
    if (level > 0.02) {
      const bars = HQ ? 120 : 72, base = R * 1.02;
      ctx.strokeStyle = `hsla(${hue},100%,70%,0.85)`; ctx.lineWidth = 2; ctx.beginPath();
      for (let i = 0; i < bars; i++) {
        const a = (i / bars) * Math.PI * 2, amp = level * R * 0.22 * (0.35 + 0.65 * Math.abs(Math.sin(i * 0.37 + t * 7) * Math.cos(i * 0.11 - t * 3)));
        if (amp < 0.6) continue;
        const ca = Math.cos(a), sa = Math.sin(a), x1 = CX + ca * base, y1 = CY + sa * base;
        ctx.moveTo(x1, y1); ctx.lineTo(x1 + ca * amp, y1 + sa * amp);
      }
      ctx.stroke();
    }

    // 7. quả cầu hạt
    const rot = ang.core, tilt = 0.42, cr = Math.cos(rot), sr = Math.sin(rot), ct = Math.cos(tilt), st = Math.sin(tilt);
    const rad = R * (1 - cur.pull * 0.25 * (0.6 + level)) * (1 + level * 0.1 + Math.sin(t * 1.6) * cur.pulse) * bootS;
    // toạ độ chiếu tính cho MỌI hạt (rẻ) để nhánh nơ-ron / synapse luôn đúng chỗ; chế độ nhẹ chỉ VẼ một nửa
    const listening = state === 'listening', step = HQ ? 1 : 2;
    for (let i = 0; i < N; i++) {
      const p = pts[i];
      const wob = 1 + cur.noise * Math.sin(p.s * 40 + t * 3 + p.y * 6) + (listening ? -level * 0.12 * p.s : 0);
      let x = p.x * cr - p.z * sr, z = p.x * sr + p.z * cr, y = p.y;
      const y2 = y * ct - z * st; z = y * st + z * ct; y = y2;
      const persp = 1 / (1.9 - z * 0.6);
      PX[i] = CX + x * rad * wob * persp * 1.3; PY[i] = CY + y * rad * wob * persp * 1.3; PD[i] = (z + 1) / 2;
    }
    ctx.globalCompositeOperation = 'lighter';
    // mạng nơ-ron: 5 mức sáng, mỗi mức MỘT path
    const mNear = mouse.on && Math.hypot(mouse.x - CX, mouse.y - CY) < R * 1.6;
    for (let g = 0; g < 5; g++) BUCKET[g].length = 0;
    for (let li = 0; li < links.length; li += step) {
      const [a, b, ph] = links[li];
      const d = Math.min(PD[a], PD[b]); if (d < 0.42) continue;
      let al = (d - 0.42) * (0.25 + cur.lattice * 0.9) * (0.7 + 0.3 * Math.sin(t * 3 + ph * 20));
      if (mNear) { const md = Math.hypot((PX[a] + PX[b]) / 2 - mouse.x, (PY[a] + PY[b]) / 2 - mouse.y); if (md < 90) al += (1 - md / 90) * 0.9; }
      if (al < 0.03) continue;
      BUCKET[Math.min(4, (al * 6) | 0)].push(a, b);
    }
    ctx.lineWidth = 0.9;
    for (let g = 0; g < 5; g++) {
      const bk = BUCKET[g]; if (!bk.length) continue;
      ctx.strokeStyle = `hsla(${hue},100%,74%,${0.08 + g * 0.18})`; ctx.beginPath();
      for (let j = 0; j < bk.length; j += 2) { ctx.moveTo(PX[bk[j]], PY[bk[j]]); ctx.lineTo(PX[bk[j + 1]], PY[bk[j + 1]]); }
      ctx.stroke();
    }
    // nơ-ron bắn + lan truyền
    const rate = { idle: 2, listening: 4, thinking: 16, speaking: 6, alert: 9 }[state] || 2;
    for (let k = 0; k < rate * dt * (HQ ? 3 : 1.5); k++) { const i = ((Math.random() * N) | 0) & (HQ ? ~0 : ~1); if (PD[i] > 0.55) fireNeuron(i, 0.8); }
    if (mNear && Math.random() < 0.6) {
      let best = -1, bd = 70 * 70;
      for (let k = 0; k < 100; k++) { const i = ((Math.random() * N) | 0) & (HQ ? ~0 : ~1); const dd = (PX[i] - mouse.x) ** 2 + (PY[i] - mouse.y) ** 2; if (dd < bd && PD[i] > 0.5) { bd = dd; best = i; } }
      if (best >= 0) fireNeuron(best, 1.2);
    }
    const synCap = HQ ? 320 : 120;
    if (synapses.length > synCap) synapses.splice(0, synapses.length - synCap);
    for (let g = 0; g < 3; g++) BUCKET[g].length = 0;
    for (let i = synapses.length - 1; i >= 0; i--) {
      const sp = synapses[i], q = (now - sp.t0) / sp.dur;
      if (q > 1) { synapses.splice(i, 1); continue; }
      if (q < 0) continue;
      const [a, b] = links[sp.li], from = sp.from === a ? a : b, to = from === a ? b : a;
      BUCKET[Math.min(2, ((1 - q) * 3) | 0)].push(PX[from], PY[from], lerp(PX[from], PX[to], q), lerp(PY[from], PY[to], q));
      if (q > 0.96 && !sp.chained && Math.random() < 0.35) { sp.chained = true; fireNeuron(to, 0.6); }
    }
    ctx.lineWidth = 1.6;
    for (let g = 0; g < 3; g++) {
      const bk = BUCKET[g]; if (!bk.length) continue;
      ctx.strokeStyle = `hsla(${hue},100%,85%,${0.3 + g * 0.3})`; ctx.beginPath();
      for (let j = 0; j < bk.length; j += 4) { ctx.moveTo(bk[j], bk[j + 1]); ctx.lineTo(bk[j + 2], bk[j + 3]); }
      ctx.stroke();
      ctx.fillStyle = '#fff'; for (let j = 0; j < bk.length; j += 4) ctx.fillRect(bk[j + 2] - 1.2, bk[j + 3] - 1.2, 2.4, 2.4);
    }
    // nơ-ron loé: 2 vòng tròn đồng tâm (không tạo gradient mỗi lần)
    for (let i = fires.length - 1; i >= 0; i--) {
      const f = fires[i], q = (now - f.t0) / 520;
      if (q > 1) { fires.splice(i, 1); continue; }
      if (q < 0) continue;
      const r = 2.5 + q * 8 * f.s;
      ctx.fillStyle = `hsla(${hue},100%,80%,${(1 - q) * 0.22})`; ctx.beginPath(); ctx.arc(PX[f.i], PY[f.i], r, 0, Math.PI * 2); ctx.fill();
      ctx.fillStyle = `hsla(${hue},100%,95%,${(1 - q) * 0.8})`; ctx.beginPath(); ctx.arc(PX[f.i], PY[f.i], r * 0.35, 0, Math.PI * 2); ctx.fill();
    }
    if (mNear) {
      const near = [];
      for (let k = 0; k < 200; k++) { const i = ((Math.random() * N) | 0) & (HQ ? ~0 : ~1); const dd = Math.hypot(PX[i] - mouse.x, PY[i] - mouse.y); if (dd < 110 && PD[i] > 0.5) near.push([dd, i]); }
      ctx.strokeStyle = `hsla(${hue},100%,80%,0.55)`; ctx.lineWidth = 1; ctx.beginPath();
      near.sort((x, y) => x[0] - y[0]).slice(0, 4).forEach(([, i]) => {
        ctx.moveTo(mouse.x, mouse.y); ctx.quadraticCurveTo((mouse.x + PX[i]) / 2 + (Math.random() - 0.5) * 14, (mouse.y + PY[i]) / 2 + (Math.random() - 0.5) * 14, PX[i], PY[i]);
      });
      ctx.stroke();
    }
    // hạt: 6 mức độ sâu × 2 sắc độ = 12 nhóm, mỗi nhóm đặt màu MỘT lần
    for (let g = 0; g < 12; g++) BUCKET[g].length = 0;
    for (let i = 0; i < N; i += step) BUCKET[Math.min(5, (PD[i] * 6) | 0) * 2 + (pts[i].s > 0.5 ? 1 : 0)].push(i);
    for (let g = 0; g < 12; g++) {
      const bk = BUCKET[g]; if (!bk.length) continue;
      const depth = ((g >> 1) + 0.5) / 6, sz = (0.8 + depth * 2.0) * (HQ ? 1 : 1.3);
      ctx.fillStyle = `hsla(${hue + (g & 1 ? 10 : -10)},100%,${58 + depth * 30}%,${0.18 + depth * 0.8})`;
      for (const i of bk) ctx.fillRect(PX[i] - sz / 2, PY[i] - sz / 2, sz, sz);
    }
    const coreR = R * 0.5 * breathe + 0.1;
    const kg = ctx.createRadialGradient(CX, CY, 0, CX, CY, coreR);
    kg.addColorStop(0, 'rgba(255,255,255,0.95)'); kg.addColorStop(0.25, `hsla(${hue},100%,70%,0.8)`); kg.addColorStop(1, 'transparent');
    ctx.fillStyle = kg; ctx.beginPath(); ctx.arc(CX, CY, coreR, 0, Math.PI * 2); ctx.fill();
    ctx.strokeStyle = `hsla(${hue},100%,85%,${0.25 + level * 0.3})`; ctx.lineWidth = 1; ctx.beginPath();
    for (const a of [0.2, 1.77]) { const L = R * (1.4 + level * 0.6); ctx.moveTo(CX - Math.cos(a) * L, CY - Math.sin(a) * L * 0.2); ctx.lineTo(CX + Math.cos(a) * L, CY + Math.sin(a) * L * 0.2); }
    ctx.stroke();

    // 7b. tia điện (đang nói / sự cố) — 2 nét chồng thay cho đổ bóng
    if ((state === 'speaking' && level > 0.5) || state === 'alert') {
      if (Math.random() < (state === 'alert' ? 0.22 : 0.14)) {
        const a = Math.random() * Math.PI * 2, pts2 = [], seg = 9;
        for (let k = 0; k <= seg; k++) {
          const r = R * (0.95 + (k / seg) * 0.55), jit = k && k < seg ? (Math.random() - 0.5) * R * 0.12 : 0;
          pts2.push([CX + Math.cos(a) * r - Math.sin(a) * jit, CY + Math.sin(a) * r + Math.cos(a) * jit]);
        }
        bolts.push({ pts: pts2, t0: now });
      }
    }
    for (let i = bolts.length - 1; i >= 0; i--) {
      const b = bolts[i], q = (now - b.t0) / 180;
      if (q > 1) { bolts.splice(i, 1); continue; }
      if (q < 0) continue;
      ctx.beginPath(); b.pts.forEach(([x, y], k) => (k ? ctx.lineTo(x, y) : ctx.moveTo(x, y)));
      ctx.strokeStyle = `hsla(${hue},100%,65%,${(1 - q) * 0.35})`; ctx.lineWidth = 5; ctx.stroke();
      ctx.strokeStyle = `hsla(${hue},100%,92%,${1 - q})`; ctx.lineWidth = 1.4; ctx.stroke();
    }
    ctx.globalCompositeOperation = 'source-over';

    // 8. sóng xung kích
    for (let i = shocks.length - 1; i >= 0; i--) {
      const s = shocks[i], p = (now - s.t0) / 1600;
      if (p > 1) { shocks.splice(i, 1); continue; }
      if (p < 0) continue;
      const e = 1 - Math.pow(1 - p, 3);                 // lan ra êm (ease-out), mờ dần
      ctx.strokeStyle = `hsla(${s.hue},100%,70%,${(1 - p) * (1 - p) * 0.45})`; ctx.lineWidth = 2 * (1 - p) + 0.5;
      ctx.beginPath(); ctx.arc(CX, CY, R * (0.9 + e * 2.4), 0, Math.PI * 2); ctx.stroke();
    }

    // 9. tia gọi công cụ đi theo sợi thần kinh; tới nơi nút gợn sóng
    for (let i = beams.length - 1; i >= 0; i--) {
      const b = beams[i], p = Math.max(0, (now - b.t0) / b.dur);
      if (p > 1.5) { beams.splice(i, 1); continue; }
      const nc = nerveCurve(b.n, t), fade = Math.max(0, 1 - Math.max(0, p - 0.7) * 1.4);
      ctx.beginPath(); ctx.moveTo(nc[0], nc[1]); ctx.bezierCurveTo(nc[2], nc[3], nc[4], nc[5], nc[6], nc[7]);
      ctx.strokeStyle = b.color; ctx.globalAlpha = fade * 0.25; ctx.lineWidth = 6; ctx.stroke();
      ctx.globalAlpha = fade * 0.8; ctx.lineWidth = 2; ctx.stroke();
      const q = Math.min(1, p); ctx.fillStyle = b.color; ctx.beginPath();
      for (let s = 0; s < 8; s++) { const qq = q - s * 0.03; if (qq < 0) break; const [sx, sy] = bez(nc, qq), r = 2.6 - s * 0.25; ctx.moveTo(sx + r, sy); ctx.arc(sx, sy, r, 0, Math.PI * 2); }
      ctx.globalAlpha = fade * 0.8; ctx.fill();
      const [hx, hy] = bez(nc, q); ctx.globalAlpha = fade; ctx.fillStyle = '#fff'; ctx.beginPath(); ctx.arc(hx, hy, 3.6, 0, Math.PI * 2); ctx.fill();
      ctx.globalAlpha = 1;
      if (p >= 1 && !b.arrived) { b.arrived = true; b.n.glow = 1; b.n.hit = b.color; ripples.push({ n: b.n, color: b.color, t0: now }); }
    }
    for (let i = ripples.length - 1; i >= 0; i--) {
      const r = ripples[i], p = Math.max(0, (now - r.t0) / 900);
      if (p > 1) { ripples.splice(i, 1); continue; }
      ctx.strokeStyle = r.color; ctx.globalAlpha = 1 - p; ctx.lineWidth = 2;
      hexPath(r.n.x, r.n.y, 10 + p * 26); ctx.stroke(); ctx.globalAlpha = 1;
    }

    // 10. nút nửa trước
    nodes.forEach((n) => { if (n.z >= 0) drawNode(n); });

    // 11. mảnh suy nghĩ (không đổ bóng)
    ctx.font = `500 ${Math.max(12, R * 0.08)}px 'Be Vietnam Pro', sans-serif`; ctx.textAlign = 'center'; ctx.textBaseline = 'alphabetic';
    ctx.fillStyle = 'hsl(272,100%,86%)';
    for (let i = frags.length - 1; i >= 0; i--) {
      const f = frags[i], p = (now - f.t0) / f.dur;
      if (p > 1) { frags.splice(i, 1); continue; }
      if (p < 0) continue;
      const d = f.d + p * R * 1.25, x = CX + Math.cos(f.a) * d * 1.35, y = CY + Math.sin(f.a) * d * 0.75;
      ctx.globalAlpha = Math.sin(p * Math.PI) * 0.9; ctx.fillText(f.text, x, y);
    }
    ctx.globalAlpha = 1;
  }

  function hexPath(x, y, r) {
    ctx.beginPath();
    for (let i = 0; i < 6; i++) { const a = Math.PI / 3 * i + Math.PI / 6; ctx.lineTo(x + Math.cos(a) * r, y + Math.sin(a) * r); }
    ctx.closePath();
  }
  function pill(x, y, w, h) {
    const r = h / 2; ctx.beginPath(); ctx.moveTo(x + r, y); ctx.lineTo(x + w - r, y); ctx.arc(x + w - r, y + r, r, -Math.PI / 2, Math.PI / 2);
    ctx.lineTo(x + r, y + h); ctx.arc(x + r, y + r, r, Math.PI / 2, Math.PI * 1.5); ctx.closePath();
  }
  function drawNode(n) {
    const hue = KIND_HUE[n.kind] ?? 190, front = (n.z + 1) / 2, sz = 10 + front * 6 + n.glow * 5 + (n === hovered ? 6 : 0);
    const col = n.glow > 0.05 ? n.hit : n.down ? '#ff4d6d' : `hsl(${hue},100%,66%)`;
    ctx.globalAlpha = 0.4 + front * 0.6;
    const lit = n === hovered || n.glow > 0.05;
    ctx.shadowColor = col; ctx.shadowBlur = lit ? 14 + n.glow * 20 + (n === hovered ? 16 : 0) : 0;
    hexPath(n.x, n.y, sz); ctx.fillStyle = n.down ? 'rgba(255,77,109,.35)' : `hsla(${hue},100%,55%,0.18)`; ctx.fill();
    ctx.strokeStyle = col; ctx.lineWidth = 1.6; ctx.stroke(); ctx.shadowBlur = 0;
    ctx.fillStyle = '#eaffff'; ctx.font = `700 ${n.icon.length > 1 ? 8 + front * 2 : 9 + front * 3}px Orbitron, sans-serif`;
    ctx.textAlign = 'center'; ctx.textBaseline = 'middle'; ctx.fillText(n.icon, n.x, n.y + 0.5);
    const label = n.label.length > 22 ? n.label.slice(0, 21) + '…' : n.label;
    ctx.font = `500 ${11 + front * 1.5}px 'Be Vietnam Pro', sans-serif`;
    const w = ctx.measureText(label).width + 16, ly = n.y + sz + 7;
    pill(n.x - w / 2, ly, w, 19); ctx.fillStyle = 'rgba(2,12,24,.72)'; ctx.fill();
    ctx.strokeStyle = `hsla(${hue},90%,60%,${0.25 + n.glow * 0.6})`; ctx.lineWidth = 1; ctx.stroke();
    ctx.fillStyle = `rgba(222,246,255,${0.55 + front * 0.45})`; ctx.fillText(label, n.x, ly + 10);
    ctx.globalAlpha = 1; ctx.textBaseline = 'alphabetic';
  }

  // ── bảng số liệu ─────────────────────────────────────────────────────────
  function countTo(el, target, suffix = '') {
    const from = Number(el.dataset.v || 0), to = Number(target), t0 = performance.now();
    el.dataset.v = to;
    if (!Number.isFinite(to)) { el.textContent = target; return; }
    const step = (now) => {
      const p = clamp01((now - t0) / 700), v = from + (to - from) * (1 - Math.pow(1 - p, 3));
      el.textContent = (Number.isInteger(to) ? Math.round(v).toLocaleString('vi-VN') : v.toFixed(1)) + suffix;
      if (p < 1) requestAnimationFrame(step);
    };
    requestAnimationFrame(step);
  }
  function tile(id, value, cls, suffix = '') {
    const el = $(id), v = el.querySelector('.v');
    el.className = `tile ${value === 0 ? 'zero' : cls}`;
    if (v.dataset.v === String(value)) return;
    if (value === '–' || value == null) { v.textContent = '–'; v.dataset.v = ''; return; }
    countTo(v, value, suffix);
    const panel = el.closest('.glass'); panel.classList.remove('flash'); void panel.offsetWidth; panel.classList.add('flash');
  }
  function gauge(id, pct, label) {
    const p = Math.max(0, Math.min(100, Number(pct) || 0)), r = 30, c = 2 * Math.PI * r;
    const col = p > 85 ? 'var(--bad)' : p > 70 ? 'var(--warn)' : 'var(--accent)';
    const ticks = Array.from({ length: 24 }, (_, i) => { const a = (i / 24) * Math.PI * 2 - Math.PI / 2;
      return `<line x1="${40 + Math.cos(a) * 36}" y1="${40 + Math.sin(a) * 36}" x2="${40 + Math.cos(a) * 38.5}" y2="${40 + Math.sin(a) * 38.5}" stroke="rgba(255,255,255,${i / 24 <= p / 100 ? '.55' : '.12'})" stroke-width="1"/>`; }).join('');
    $(id).innerHTML = `<svg viewBox="0 0 80 80">${ticks}<circle cx="40" cy="40" r="${r}" fill="none" stroke="rgba(255,255,255,.07)" stroke-width="6"/>
      <circle cx="40" cy="40" r="${r}" fill="none" stroke="${col}" stroke-width="6" stroke-linecap="round" stroke-dasharray="${c * p / 100} ${c}"
        transform="rotate(-90 40 40)" style="filter:drop-shadow(0 0 6px ${col});transition:stroke-dasharray .8s"/>
      <text x="40" y="45" text-anchor="middle" fill="#fff" font-family="Orbitron" font-size="15" font-weight="700">${Math.round(p)}%</text></svg><div class="k">${esc(label)}</div>`;
  }
  function spark(series) {
    const c = $('spark'), x = c.getContext('2d'), w = c.width = c.clientWidth * DPR, h = c.height = c.clientHeight * DPR;
    if (!series || series.length < 2) return;
    const max = Math.max(20, ...series);
    const accent = `hsl(${Math.round(cur.hue)},95%,62%)`;
    const grd = x.createLinearGradient(0, 0, 0, h); grd.addColorStop(0, `hsla(${Math.round(cur.hue)},95%,62%,.35)`); grd.addColorStop(1, 'transparent');
    const ptsS = series.map((v, i) => [(i / (series.length - 1)) * w, h - (v / max) * (h - 6 * DPR) - 3 * DPR]);
    x.beginPath(); ptsS.forEach(([px, py], i) => (i ? x.lineTo(px, py) : x.moveTo(px, py)));
    x.lineTo(w, h); x.lineTo(0, h); x.closePath(); x.fillStyle = grd; x.fill();
    x.beginPath(); ptsS.forEach(([px, py], i) => (i ? x.lineTo(px, py) : x.moveTo(px, py)));
    x.strokeStyle = accent; x.lineWidth = 2 * DPR; x.shadowColor = accent; x.shadowBlur = 8 * DPR; x.stroke();
    const [lx, ly] = ptsS[ptsS.length - 1]; x.fillStyle = '#fff'; x.beginPath(); x.arc(lx - 2 * DPR, ly, 3 * DPR, 0, Math.PI * 2); x.fill();
  }

  const fmtN = (n) => (n == null ? '–' : Number(n).toLocaleString('vi-VN'));
  const fmtMs = (v) => (v == null ? '–' : v >= 1000 ? `${(v / 1000).toFixed(1)} s` : `${Math.round(v)} ms`);

  function applyOverview(d) {
    tile('t-incidents', d.incidents_open ?? 0, 'bad');
    tile('t-approvals', d.approvals_pending ?? 0, 'warn');
    tile('t-active', (d.tasks || {}).active ?? 0, 'info');
    const sr = (d.tasks || {}).success_rate; tile('t-success', sr == null ? '–' : Math.round(sr), 'ok', '%');
    const cost = d.cost || {};
    $('ai-tokens').textContent = fmtN(cost.total_tokens); $('ai-calls').textContent = fmtN(cost.llm_calls);
    const groups = (d.voice && d.voice.by_outcome) || {};
    let p50 = null;
    for (const name of ['llm', 'agent', ...Object.keys(groups)]) { const m = ((groups[name] || {}).metrics || {}).ttfa_answer_ms; if (m) { p50 = m.p50; break; } }
    $('ai-voice').textContent = fmtMs(p50);
    const att = (d.tasks || {}).attention || [];
    $('att-count').textContent = (d.tasks || {}).attention_total || att.length;
    $('attention').innerHTML = att.length ? att.slice(0, 6).map((a) => {
      const st = a.status === 'FAILED' ? 'error' : a.status === 'WAITING_AUTHORIZATION' ? 'waiting' : 'running';
      const lbl = { error: 'thất bại', waiting: 'chờ duyệt', running: 'cần xử lý' }[st];
      return `<li class="${st}"><time>${esc(String(a.updated_at || a.created_at || '').slice(11, 16))}</time><span class="t">${esc(a.title)}</span><span class="tag ${st}">${lbl}</span></li>`;
    }).join('') : '<li class="empty">Không có việc nào cần chú ý.</li>';
  }
  const SERVICE_NAME = { llm_9router: 'Mô hình AI (9Router)', telegram_gateway: 'Telegram', database_sqlite: 'Cơ sở dữ liệu', active_directory: 'Danh bạ nhân sự (AD)' };
  function applyHealth(h) {
    const hw = h.hardware || {};
    gauge('g-cpu', hw.cpu_percent, 'CPU'); gauge('g-ram', hw.ram_percent, 'RAM'); gauge('g-disk', hw.disk_percent, 'Ổ đĩa');
    $('host-net').textContent = `↑ ${(hw.net_sent_mbps ?? 0).toFixed(2)} · ↓ ${(hw.net_recv_mbps ?? 0).toFixed(2)} MB/s`;
    spark(hw.cpu_history);
    const sv = h.services || {};
    const llm = sv.llm_9router; if (llm) $('ai-model').textContent = llm.model || '9Router';
    const setInfo = (id, svc, extra) => { const n = byId.get(id); if (!n || !svc) return; n.down = String(svc.status || '').toUpperCase() !== 'OK';
      n.info = [FIXED.find((f) => f.id === id).info[0], n.down ? `Trạng thái: ${svc.status}` : 'Đang hoạt động', extra].filter(Boolean); };
    setInfo('llm', llm, llm && llm.latency_ms != null ? `Độ trễ ${Math.round(llm.latency_ms)} ms · ${llm.model || ''}` : '');
    setInfo('telegram', sv.telegram_gateway, '');
    const rows = Object.entries(sv).map(([k, v]) => {
      const ok = String(v.status || '').toUpperCase() === 'OK';
      const extra = v.latency_ms != null ? fmtMs(v.latency_ms) : v.backend ? v.backend : ok ? 'trực tuyến' : (v.status || '').toLowerCase();
      return `<div class="svc ${ok ? 'ok' : 'bad'}"><i></i><span>${esc(SERVICE_NAME[k] || k)}</span><b>${esc(extra)}</b></div>`;
    });
    $('services').innerHTML = rows.join('') || '<div class="empty">Chưa có số liệu dịch vụ.</div>';
  }
  function applyBudget(b) {
    const daily = (b.checks || []).find((c) => c.key === 'daily_tokens') || (b.checks || [])[0];
    if (!daily) { $('ai-budget').style.width = '0'; $('ai-budget-text').textContent = `Ngân sách: chưa đặt hạn mức (chế độ ${b.mode || '—'})`; return; }
    $('ai-budget').style.width = `${Math.min(100, daily.ratio * 100)}%`;
    $('ai-budget-text').textContent = `${daily.label}: ${fmtN(Math.round(daily.used))} / ${fmtN(daily.limit)} (${Math.round(daily.ratio * 100)}%)`;
  }
  function addFeed(ev, quiet) {
    const ul = $('feed'); const empty = ul.querySelector('.empty'); if (empty) empty.remove();
    const st = ev.status === 'ok' ? 'ok' : ev.status === 'error' ? 'error' : ev.status === 'waiting' ? 'waiting' : 'running';
    const lbl = { ok: 'xong', error: 'lỗi', waiting: 'chờ duyệt', running: 'đang chạy' }[st];
    const li = document.createElement('li'); li.className = st + (quiet ? ' quiet' : '');
    li.innerHTML = `<time>${esc(String(ev.ts || '').slice(11, 19))}</time><span class="t">${esc(ev.tool)}${ev.where ? ` <small>@ ${esc(ev.where)}</small>` : ''}</span><span class="tag ${st}">${lbl}${ev.ms ? ` · ${fmtMs(ev.ms)}` : ''}</span>`;
    ul.prepend(li);
    while (ul.children.length > 14) ul.lastChild.remove();
    if (!quiet) { const panel = ul.closest('.glass'); panel.classList.remove('flash'); void panel.offsetWidth; panel.classList.add('flash'); }
  }

  function nodeFor(tool, target) {
    const t = String(tool || '');
    if (t.startsWith('mcp_')) { const n = nodes.find((x) => x.kind === 'mcp' && t.startsWith(`mcp_${x.id.replace(/-/g, '_')}_`)); if (n) return n.id; }
    if (t.startsWith('a2a_')) { const n = nodes.find((x) => x.kind === 'a2a' && `a2a_${x.id.replace(/-/g, '_')}` === t); if (n) return n.id; }
    if (String(target || '').startsWith('worker:')) return (nodes.find((x) => x.kind === 'workstations') || {}).id || 'core-server';
    if (/playbook/.test(t)) return 'playbooks';
    if (/infra|prometheus|grafana|incident|monitor/.test(t)) return 'monitor';
    if (/rag|knowledge|document|search_doc|policy/.test(t)) return 'rag';
    if (/telegram|notify/.test(t)) return 'telegram';
    return 'core-server';
  }
  function onTopology(ev, quiet) {
    if (ev.kind === 'tool') {
      const [tool, where] = String(ev.detail || '').split(/ @ |: /);
      const target = ev.stage === 'start' ? ev.target : ev.source;
      if (!quiet) beam(nodeFor(tool, target), ev.stage === 'start' ? 'running' : ev.status === 'ok' ? 'ok' : 'error');
      if (ev.stage === 'end') addFeed({ ts: ev.ts, tool, where: where && !where.includes(' ') ? where : '', status: ev.status === 'ok' ? 'ok' : 'error', ms: ev.ms }, quiet);
    } else if (ev.kind === 'approval') {
      if (!quiet) beam('hitl', ev.stage === 'request' ? 'waiting' : 'ok');
      if (ev.stage === 'request') addFeed({ ts: ev.ts, tool: String(ev.detail || 'Yêu cầu phê duyệt').split(' chờ duyệt')[0], status: 'waiting' }, quiet);
    } else if (!quiet && ev.kind === 'turn') {
      beam('llm', ev.status === 'error' ? 'error' : 'running');
    } else if (!quiet && ev.kind === 'robot') {
      beam('robot', ev.status === 'down' ? 'error' : 'ok');
    }
  }

  // ── dữ liệu thật ─────────────────────────────────────────────────────────
  async function api(path) {
    const r = await fetch(path, { headers: TOKEN ? { Authorization: `Bearer ${TOKEN}` } : {} });
    if (r.status === 401 || r.status === 403) throw Object.assign(new Error('auth'), { auth: true });
    if (!r.ok) throw new Error(`HTTP ${r.status}`);
    return r.json();
  }
  let seq = -1, live = false;
  async function pollFast() {
    try { applyHealth(await api('/api/v1/health-dashboard')); } catch (_) { /* bỏ qua — chưa đăng nhập: endpoint cần token */ }
    if (!live) return;
    try {
      const ev = await api(`/api/v1/system/topology/events?since=${Math.max(seq, 0)}&limit=${seq < 0 ? 60 : 100}`);
      const first = seq < 0;
      (ev.events || []).forEach((e) => { if (!e.simulated) onTopology(e, first); seq = Math.max(seq, e.seq); });  // lần đầu: điền luồng, không bắn tia
      if (first && seq < 0) seq = 0;
    } catch (e) { if (e.auth) toLogin('Phiên đăng nhập hết hạn — đăng nhập lại portal rồi mở lại HUD.'); }
  }
  async function pollSlow() {
    if (!live) return;
    try { applyOverview(await api('/api/v1/ops/overview?hours=24')); } catch (e) { if (e.auth) return toLogin('Tài khoản này không đủ quyền xem số liệu doanh nghiệp (cần manager / admin).'); }
    try { applyBudget(await api('/api/v1/llmops/budget')); } catch (_) { /* tuỳ chọn */ }
    try {
      const d = await api('/api/v1/agent-gateway/agents');
      setNodes((d.agents || []).map((a) => ({ id: a.agent_id, label: a.name, kind: a.kind, down: a.status !== 'active' || !!a.last_error,
        info: [a.status === 'active' ? 'Đang hoạt động' : 'TẠM DỪNG', a.calls ? `${a.calls.total} lời gọi · ${a.calls.failed} lỗi` : '',
          a.role ? `Quyền: ${a.role}` : '', a.tools != null ? `${a.registered}/${a.tools} công cụ` : '', a.last_seen_at ? `Lần cuối: ${a.last_seen_at}` : '',
          a.last_error ? `Lỗi: ${a.last_error}` : ''].filter(Boolean) })));
    } catch (_) { /* tuỳ chọn */ }
  }
  // ── chưa đăng nhập: KHÔNG bịa số liệu — ô để trống, ghi rõ cần đăng nhập ─────
  function toLogin(reason) {
    live = false;
    $('mode-chip').className = 'chip demo'; $('mode-chip').innerHTML = '<a href="/" title="Mở portal để đăng nhập">Chưa đăng nhập</a>';
    $('note').textContent = reason || 'Đăng nhập portal (tài khoản admin) rồi mở lại HUD để ra lệnh và xem số liệu.';
    setNodes([]);
    ['t-incidents', 't-approvals', 't-active', 't-success'].forEach((id) => tile(id, '–', ''));
    $('feed').innerHTML = '<li class="empty">Cần đăng nhập để xem luồng hành động.</li>';
    $('attention').innerHTML = '<li class="empty">Cần đăng nhập để xem việc cần chú ý.</li>';
  }


  // ── xem thử ───────────────────────────────────────────────────────────────
  const PREVIEW = {
    idle: () => setState('idle', { text: 'Sẵn sàng nhận lệnh.', query: '' }),
    listening: () => setState('listening', { text: 'Em đang lắng nghe anh…', query: '' }),
    thinking: () => setState('thinking', { query: 'Tổng hợp tình hình hạ tầng và việc chờ duyệt', thoughts: 'Đọc cảnh báo Prometheus; đối chiếu ticket GLPI; kiểm tra phiếu chờ duyệt; ước lượng mức ảnh hưởng' }),
    speaking: () => setState('speaking', { text: 'Dạ, em đã kiểm tra xong. Hạ tầng ổn định, có một cảnh báo ổ đĩa ở máy chủ cơ sở dữ liệu và một kịch bản dọn dẹp đang chờ anh duyệt.' }),
    alert: () => setState('alert', { text: 'Cần phê duyệt: chạy kịch bản dọn ổ đĩa' }),
    tool: () => { const pick = nodes[Math.floor(Math.random() * nodes.length)]; ['running', 'ok'].forEach((s, i) => setTimeout(() => beam(pick.id, s), i * 900)); },
  };

  const par = { x: 0, y: 0, tx: 0, ty: 0 };
  window.addEventListener('mousemove', (e) => { par.tx = (e.clientX / W) * 2 - 1; par.ty = (e.clientY / H) * 2 - 1; });
  (function parallax() {
    par.x = lerp(par.x, par.tx, 0.06); par.y = lerp(par.y, par.ty, 0.06);
    document.documentElement.style.setProperty('--px', par.x.toFixed(3)); document.documentElement.style.setProperty('--py', par.y.toFixed(3));
    requestAnimationFrame(parallax);
  })();

  function hitNode(x, y) {
    let best = null, bd = 30;
    for (const n of nodes) { const d = Math.hypot(n.x - x, n.y - y); if (d < bd) { bd = d; best = n; } }
    return best;
  }
  function showTip(n) {
    const tip = $('tip');
    if (!n) { tip.classList.remove('on'); return; }
    tip.innerHTML = `<b>${esc(n.label)}</b><small>${esc(KIND_NAME[n.kind] || n.kind)}</small>${(n.info || []).map((l) => `<div>${esc(l)}</div>`).join('')}`;
    // đặt thẻ phía NGOÀI agent (xa lõi) để không che sợi thần kinh
    const left = n.x < CX ? n.x - 300 : n.x + 30;
    tip.style.left = `${Math.max(10, Math.min(W - 290, left))}px`; tip.style.top = `${Math.max(70, n.y - 40)}px`; tip.classList.add('on');
  }
  const toCanvas = (e) => [e.clientX + par.x * 10, e.clientY + par.y * 8];      // bù độ dịch parallax của canvas
  cv.addEventListener('mousemove', (e) => {
    const [x, y] = toCanvas(e);
    mouse.x = x; mouse.y = y; mouse.on = true;
    const n = hitNode(x, y);
    if (n !== hovered) { hovered = n; showTip(n); }
    cv.style.cursor = Math.hypot(x - CX, y - CY) < R ? 'pointer' : 'default';
    cv.title = !n && Math.hypot(x - CX, y - CY) < R ? 'Bấm lõi để nói (hoặc phím Cách)' : '';
  });
  cv.addEventListener('mouseleave', () => { mouse.on = false; hovered = null; showTip(null); });
  cv.addEventListener('click', (e) => {
    const [x, y] = toCanvas(e), n = hitNode(x, y);
    if (n) return;
    if (Math.hypot(x - CX, y - CY) < R * 1.1) {          // bấm lõi: bật / tắt mic (hud.js) + xung lan toả, một loạt nơ-ron bắn
      if (typeof window.toggleHudMic === 'function') window.toggleHudMic();
      shocks.push({ t0: clockNow, hue: Math.round(cur.hue) });
      for (let k = 0; k < 40; k++) { const i = (Math.random() * N) | 0; if (PD[i] > 0.45) fireNeuron(i, 1.4); }
      nodes.forEach((m, i) => nerves.push({ n: m, t0: clockNow + i * 60, dur: 1100, back: false }));
    }
  });

  document.querySelectorAll('[data-tab]').forEach((b) => b.addEventListener('click', () => {
    document.querySelectorAll('[data-tab]').forEach((x) => x.classList.toggle('on', x === b));
    document.querySelectorAll('[data-pane]').forEach((p) => { p.hidden = p.dataset.pane !== b.dataset.tab; });
  }));

  function clock() {
    const d = new Date();
    $('clock').innerHTML = `${d.toLocaleTimeString('vi-VN', { hour12: false })}<small>${d.toLocaleDateString('vi-VN', { weekday: 'long', day: '2-digit', month: '2-digit', year: 'numeric' })}</small>`;
  }

  // ── API cho hud.js (bộ não) ───────────────────────────────────────────────
  window.HUDVisual = {
    setState: (s, opts = {}) => setState(s, opts),
    clearAlert: () => { if (state === 'alert') setState('idle', { text: 'Sẵn sàng nhận lệnh.' }); },
    setQuery: (q) => { $('query').textContent = q ? `“${q}”` : ''; },
    transcript: (t) => { $('query').textContent = t ? `“${t}”` : ''; },
    thoughts: (t) => { if (t) thoughts(t); },
    setLevelSource: (fn) => { levelSource = typeof fn === 'function' ? fn : null; },
    setAssistantName: (name) => {
      if (!name) return;
      aiName = String(name).trim() || aiName;
      const b = document.querySelector('.brand b'); if (b) b.textContent = aiName;
      const who = document.querySelector('#subtitle .who'); if (who) who.textContent = aiName.toUpperCase();
    },
    robot: (online) => { const n = byId.get('robot'); if (n) { n.down = !online; beam('robot', online ? 'ok' : 'error'); } },
    beam: (id, status) => beam(id, status),
  };

  // ── khởi động ─────────────────────────────────────────────────────────────
  resize(); setNodes([]); subtitle('Sẵn sàng nhận lệnh.', true); clock(); scramble($('state-pill'), STATES.idle.label, 900);
  setInterval(clock, 1000);
  gauge('g-cpu', 0, 'CPU'); gauge('g-ram', 0, 'RAM'); gauge('g-disk', 0, 'Ổ đĩa');
  requestAnimationFrame(frame);
  // ?nointro hoặc hệ điều hành bật "giảm chuyển động" → bỏ màn khởi động
  const noIntro = new URLSearchParams(location.search).has('nointro') || (window.matchMedia && matchMedia('(prefers-reduced-motion: reduce)').matches);
  if (noIntro) { document.body.classList.add('no-intro'); document.body.classList.remove('booting'); document.body.classList.add('booted'); }
  else { setTimeout(() => document.body.classList.remove('booting'), 60); setTimeout(() => document.body.classList.add('booted'), 2200); }
  if (TOKEN) {
    live = true;
    $('mode-chip').className = 'chip live'; $('mode-chip').textContent = '● Dữ liệu thật';
    $('note').textContent = 'Phím Cách: nói · / : gõ lệnh · F: toàn màn hình · Esc: đóng bảng';
    pollSlow();
    setInterval(pollSlow, 10000);
  } else {
    toLogin();
  }
  pollFast(); setInterval(pollFast, 2000);
  const qs = new URLSearchParams(location.search).get('state');
  if (qs && PREVIEW[qs]) setTimeout(() => { lastManual = Date.now() + 60000; PREVIEW[qs](); }, 1700);
  // ?hover: giả lập chuột (xem trước tương tác) — con trỏ trên lõi + rê lên một agent
  if (new URLSearchParams(location.search).has('hover')) setTimeout(() => {
    mouse.on = true; mouse.x = CX + R * 0.35; mouse.y = CY - R * 0.25;
    hovered = nodes.find((n) => n.kind !== 'core') || nodes[0]; showTip(hovered);
  }, 1900);
  // ?bench: đo chi phí vẽ thuần — gọi draw() 200 lần liên tiếp (thời gian tăng 16,7 ms mỗi lần), kết quả ở data-bench của <body>
  if (new URLSearchParams(location.search).has("bench")) (() => {
    const v = [], n0 = performance.now();
    for (let k = 0; k < 200; k++) { const a = performance.now(); try { draw(n0 + k * 16.7); } catch (_) { /* bỏ qua */ } v.push(performance.now() - a); }
    v.sort((x, y) => x - y);
    document.body.dataset.bench = `p50=${v[100].toFixed(2)}ms p90=${v[180].toFixed(2)}ms`;
  })();
})();
