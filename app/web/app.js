// SPDX-License-Identifier: Apache-2.0
// Copyright (c) 2026 Dương Thanh Lịch — VN-MateAI. See LICENSE and NOTICE.
/**
 * web/app.js
 * ===========
 * VN-MateAI Autonomous Control Portal — Logic Ứng Dụng (Tiếng Việt Toàn Diện)
 *
 * Tính năng chính:
 *   - Quản lý Tab & Bảng điều khiển (Dashboard)
 *   - Bật/Tắt từng kỹ năng tự động hóa (Skills Toggle)
 *   - Thêm kỹ năng mới thủ công bằng tay (Custom Skills Manager)
 *   - Kiểm thử lệnh thoại & phát âm phản hồi TTS trực tiếp
 *   - Đọc & Lưu cấu hình hệ thống
 */

'use strict';

// ─── Hằng số ──────────────────────────────────────────────────────────────
const API_BASE = '';    // Cùng origin với FastAPI server
const TOAST_DURATION = 3500;  // Thời gian hiển thị thông báo (ms)

/**
 * Escape HTML trước khi chèn chuỗi vào innerHTML.
 *
 * PHẢI khai báo ở cấp module. Trước đây chỉ có 2 bản `_esc` nằm bên trong
 * IIFE `LogViewer` và `CommandCenter` — IIFE không tự lộ ra window, nên toàn
 * bộ khối Phase 59/60 ở cấp module gọi `_esc(...)` sẽ ném
 * `ReferenceError: _esc is not defined`.
 *
 * Hậu quả thực tế (không phải lỗi hiển thị mà là chết hẳn chức năng):
 *   - `loadPluginRegistryStats()` hỏng ngay lập tức vì luôn có ≥1 tool.
 *   - `runIntegrationTool()` hỏng ở dòng hiển thị "Đang gọi..." — tức mọi
 *     lần bấm chạy công cụ đều không ra kết quả.
 *   - `_ccWebhookCard()` / `loadBackgroundTasks()` chỉ hỏng khi có dữ liệu,
 *     nên nhìn bằng mắt ở trạng thái rỗng thì thấy bình thường.
 *   - Nhánh `catch` của các hàm trên cũng gọi `_esc`, nên lỗi gốc bị che
 *     và người dùng chỉ thấy khung "Đang tải…" đứng yên vĩnh viễn.
 *
 * Unit test cũ không bắt được vì chính test tự định nghĩa một `_esc` riêng
 * trong khung test — nên test xanh trong khi trang thật chết. Bản `_esc` ở
 * đây phải là nguồn duy nhất để test tự lấy từ app.js.
 */
function _esc(s) {
  return String(s ?? '').replace(/[&<>"']/g, (c) => (
    { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]
  ));
}

// ═══════════════════════════════════════════════════════════════════════════
// Phase 73 — Quy ước hiển thị dữ liệu thật
// ═══════════════════════════════════════════════════════════════════════════
//
// Nguyên tắc: màn hình chỉ hiện số liệu đo được từ hệ thống đang chạy.
// Khi chưa có kết nối, nói thẳng là "chờ kết nối" — KHÔNG bịa số và cũng
// KHÔNG hiện 0. Vì 0 là một con số thật, người đọc dễ tưởng hệ thống đã đo
// được 0 rồi (0 kỹ năng, 0 người dùng, 0 nhiệm vụ) — trong khi thực tế là
// chưa hỏi được ai.
//
// `WAIT_TXT` được gắn thêm class `is-waiting` để CSS làm mờ, nên người đọc
// phân biệt được "chưa có số liệu" với một giá trị bình thường ngoài đời.

const WAIT_TXT = 'chờ kết nối';

/** Giá trị này có phải số liệu thật không (không phải thiếu/rỗng/NaN). */
function _isLive(v) {
  if (v === null || v === undefined || v === '') return false;
  if (typeof v === 'number' && !Number.isFinite(v)) return false;
  return true;
}

/**
 * Trả về `v` nếu là dữ liệu thật, ngược lại trả `fallback` (mặc định WAIT_TXT).
 * Dùng cho mọi ô đang chờ dữ liệu thay vì `|| 0` hay `?? 100`.
 */
function _live(v, fallback = WAIT_TXT) {
  return _isLive(v) ? v : fallback;
}

/**
 * Định dạng số đo kèm đơn vị, hoặc "chờ kết nối" nếu chưa có dữ liệu.
 * @param {*} v        giá trị thô từ API
 * @param {object} opt {digits: số lẻ thập phân, unit: đơn vị, suffix}
 */
function _liveNum(v, { digits = null, unit = '', suffix = '' } = {}) {
  if (!_isLive(v)) return WAIT_TXT;
  const n = Number(v);
  if (!Number.isFinite(n)) return WAIT_TXT;
  const shown = digits === null ? String(n) : n.toFixed(digits);
  return `${shown}${unit}${suffix}`;
}

/**
 * Ghi giá trị ra phần tử, tự gắn/bỏ class `is-waiting` theo tình trạng dữ liệu.
 * `el` nhận selector hoặc element; không tồn tại thì bỏ qua (không ném lỗi).
 */
function _setLiveText(el, value, { waiting = WAIT_TXT } = {}) {
  const node = typeof el === 'string' ? document.querySelector(el) : el;
  if (!node) return;
  const isWait = !_isLive(value) || value === waiting;
  node.textContent = isWait ? waiting : String(value);
  node.classList.toggle('is-waiting', isWait);
}

// ═══════════════════════════════════════════════════════════════════════════
// ── THEME ENGINE (LIGHT / DARK MODE) ────────────────────────────────────
// ═══════════════════════════════════════════════════════════════════════════

/**
 * Chuyển đổi giữa Light / Dark mode.
 * Cập nhật localStorage và cập nhật icon ngay lập tức.
 */
function toggleTheme() {
  const html = document.documentElement;
  const isDark = html.classList.toggle('dark');
  localStorage.setItem('theme', isDark ? 'dark' : 'light');
  updateThemeUI(isDark);
}

/**
 * Cập nhật trạng thái hiển thị icon Sun/Moon theo theme hiện tại.
 * @param {boolean} isDark - true nếu đang ở chế độ tối.
 */
function updateThemeUI(isDark) {
  const sunIcon = document.getElementById('theme-icon-sun');
  const moonIcon = document.getElementById('theme-icon-moon');
  if (!sunIcon || !moonIcon) return;

  if (isDark) {
    // Dark mode → hiển thị Sun (để bấm chuyển sang Light)
    sunIcon.classList.remove('hidden');
    moonIcon.classList.add('hidden');
  } else {
    // Light mode → hiển thị Moon (để bấm chuyển sang Dark)
    sunIcon.classList.add('hidden');
    moonIcon.classList.remove('hidden');
  }
}

// ─── Trạng thái ứng dụng ──────────────────────────────────────────────────
let currentConfig = {};

/* Phase 68: danh sách model lấy từ router đang phục vụ, thay cho danh sách
   ghi cứng trong mã. Danh sách ghi cứng chỉ đúng vào một thời điểm — khi
   provider hết tiền, nút bấm trên giao diện vẫn hiện nhưng bấm vào chết,
   và người dùng phải tự phát hiện. Nay bấm là chạy. */
let routerModelList = [];

/** Model nào router thực sự phục vụ. Rỗng thì trả mảng rỗng, không đoán. */
async function loadRouterModels() {
  try {
    // apiFetch trả về Response — phải .json() mới đọc được body.
    const res = await apiFetch('/api/v1/config/models');
    const d = res instanceof Response ? await res.json() : res;
    routerModelList = _sortModels(d?.models);
  } catch (e) {
    routerModelList = [];
  }
  // Nạp vào cả hai ô nhập model, để gợi ý luôn khớp với router.
  const opts = routerModelList
    .map(m => `<option value="${m}">${m}</option>`).join('');
  for (const id of ['ai-models-list', 'models-list']) {
    const dl = document.getElementById(id);
    if (dl) dl.innerHTML = opts;
  }
  // Ô chọn model: một select cho mỗi tab, chứa toàn bộ model đã sắp xếp.
  // Trước đây in 30 chip đầy màn hình ở cả hai tab (giai đoạn "bấm nhanh"),
  // làm trang loè loẹt và tràn chiều ngang. Giờ gom vào một ô sổ ra gọn.
  const emptyNote = '<span class="text-[10px] text-amber-400 font-mono">Router chưa phục vụ model nào — kiểm tra khoá API.</span>';
  const selectors = [
    ['ai-proxy-model-select', 'ai-proxy-model-picker-wrap'],
    ['proxy-model-select', 'proxy-model-picker-wrap'],
    ['ai-tribrain-controller-select', null],
    ['ai-tribrain-voice-select', null],
    ['ai-tribrain-ops-select', null],
  ];
  for (const [selId, wrapId] of selectors) {
    const sel = document.getElementById(selId);
    if (!sel) continue;
    const isTri = selId.startsWith('ai-tribrain');
    sel.innerHTML = routerModelList.length
      ? `<option value="">-- ${isTri ? 'Chọn mô hình nhanh' : 'Chọn mô hình từ router (đã sắp xếp)'} --</option>` + opts
      : '<option value="">-- Chưa có model nào từ router --</option>';
    if (wrapId) {
      const wrap = document.getElementById(wrapId);
      if (wrap) wrap.classList.toggle('hidden', routerModelList.length === 0);
    }
    if (isTri) {
      const role = selId.replace('ai-tribrain-', '').replace('-select', '');
      const curVal = document.getElementById(`ai-tribrain-${role}-model`)?.value;
      if (curVal) sel.value = curVal;
    }
  }
  // Hai container chip cũ giờ chỉ là dòng trạng thái gọn, không còn dãy nút.
  for (const id of ['ai-quick-models', 'cfg-quick-models']) {
    const box = document.getElementById(id);
    if (box) {
      box.innerHTML = routerModelList.length
        ? `<span class="text-[10px] text-slate-500 font-mono">Đã có ${routerModelList.length} model — chọn trong ô sổ ra bên trên.</span>`
        : emptyNote;
    }
  }
  renderFallbackChain();
  return routerModelList;
}

/** Vẽ chuỗi auto-fallback: đúng thứ tự sẽ được thử khi model chính lỗi. */
function renderFallbackChain() {
  const chain = document.getElementById('cfg-fallback-chain');
  if (!chain) return;
  const cur = (document.getElementById('cfg-llm-model')?.value || '').trim();
  const all = cur ? [cur, ...routerModelList.filter(m => m !== cur)] : routerModelList;
  chain.innerHTML = all.length
    ? all.slice(0, 5).map((m, i) => `<span class="${i === 0
      ? 'text-cyan-600 dark:text-cyan-400 font-semibold'
      : 'text-slate-500'}">${i ? '➔ ' : ''}${m}</span>`).join('<span class="text-slate-400"> </span>')
    : 'Chưa cấu hình model nào.';
}

/** Danh sách dự phòng, model chính luôn đứng đầu. */
function fallbackModels(primary) {
  const rest = routerModelList.filter(m => m !== primary);
  return primary ? [primary, ...rest] : rest;
}

/** Phase 94: Xử lý khi người dùng chọn model từ dropdown cho 1 trong 3 Bộ Não. */
function onTriBrainSelectChange(role, val) {
  if (!val) return;
  const targetInput = document.getElementById(`ai-tribrain-${role}-model`);
  if (targetInput) {
    targetInput.value = val;
    targetInput.dispatchEvent(new Event('input'));
  }
}
let skillsData = {};
let devicesData = [];
let selectedClientId = '';
let autoExecState = false;
let lastAudioBase64 = null;
let securityBlacklist = [];
let securityAuditLogs = [];
let pendingEmergencyAction = null;

// ─── Tiêu đề các Tab giao diện (Tiếng Việt) ───────────────────────────────
// Phase 79: rút còn 9 mục khớp đúng các section còn lại trong index.html.
// Mục cho tab đã gộp ('command-center' → dashboard, 'devices' → tích hợp,
// 'users' → bảo mật) phải bị gỡ: tra ra vẫn thấy tiêu đề của một tab không
// còn tồn tại, dễ khiến người đọc code tưởng những tab đó vẫn còn.
const TAB_TITLES = {
  dashboard: 'Bảng Điều Khiển',
  'system-integration': 'Tích Hợp Hệ Thống — API dữ liệu · Webhook · Máy trạm · Phòng ban',
  skills: 'Kho Kỹ Năng — công cụ AI dùng được',
  security: 'Trung Tâm Bảo Mật & Kiểm Toán',
  tasks: 'Nhật Ký Công Việc & Báo Cáo KPI',
  voice: 'Giọng Nói, Robot & HUD — kiểm thử lệnh thoại · TTS',
  config: 'Cấu Hình Toàn Bộ Hệ Thống',
  logs: 'Nhật Ký Hệ Thống (Real-time Logs)',
  'ai-manager': 'Quản Lý Trợ Lý AI — LLM · Persona · Audio',
  'dev-fleet': 'Dev Fleet — Cụm Master - WorkNode',
  'infra-monitor': 'Giám Sát Hạ Tầng — Prometheus · Grafana',
  'playbooks': 'Kịch Bản Vận Hành — chạy thử, duyệt, hoàn tác, kiểm chứng',
  'agent-gateway': 'Cổng Agent — nối MCP server / agent có sẵn của doanh nghiệp',
};

// ─── Biểu tượng Kỹ năng SVG ───────────────────────────────────────────────
const SKILL_ICONS = {
  system: `<svg width="18" height="18" fill="none" stroke="currentColor" stroke-width="2" viewBox="0 0 24 24"><rect x="2" y="3" width="20" height="14" rx="2"/><line x1="8" y1="21" x2="16" y2="21"/><line x1="12" y1="17" x2="12" y2="21"/></svg>`,
  process: `<svg width="18" height="18" fill="none" stroke="currentColor" stroke-width="2" viewBox="0 0 24 24"><circle cx="12" cy="12" r="3"/><path d="M12 1v4M12 19v4M4.22 4.22l2.83 2.83M16.95 16.95l2.83 2.83M1 12h4M19 12h4M4.22 19.78l2.83-2.83M16.95 7.05l2.83-2.83"/></svg>`,
  clipboard: `<svg width="18" height="18" fill="none" stroke="currentColor" stroke-width="2" viewBox="0 0 24 24"><path d="M16 4h2a2 2 0 0 1 2 2v14a2 2 0 0 1-2 2H6a2 2 0 0 1-2-2V6a2 2 0 0 1 2-2h2"/><rect x="8" y="2" width="8" height="4" rx="1" ry="1"/></svg>`,
  network: `<svg width="18" height="18" fill="none" stroke="currentColor" stroke-width="2" viewBox="0 0 24 24"><circle cx="12" cy="12" r="10"/><line x1="2" y1="12" x2="22" y2="12"/><path d="M12 2a15.3 15.3 0 0 1 4 10 15.3 15.3 0 0 1-4 10 15.3 15.3 0 0 1-4-10 15.3 15.3 0 0 1 4-10z"/></svg>`,
  file: `<svg width="18" height="18" fill="none" stroke="currentColor" stroke-width="2" viewBox="0 0 24 24"><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><polyline points="14 2 14 8 20 8"/></svg>`,
  volume: `<svg width="18" height="18" fill="none" stroke="currentColor" stroke-width="2" viewBox="0 0 24 24"><polygon points="11 5 6 9 2 9 2 15 6 15 11 19 11 5"/><path d="M19.07 4.93a10 10 0 0 1 0 14.14M15.54 8.46a5 5 0 0 1 0 7.07"/></svg>`,
  excel: `<svg width="18" height="18" fill="none" stroke="currentColor" stroke-width="2" viewBox="0 0 24 24"><rect x="3" y="3" width="18" height="18" rx="2"/><path d="M3 9h18M3 15h18M9 3v18"/></svg>`,
  powershell: `<svg width="18" height="18" fill="none" stroke="currentColor" stroke-width="2" viewBox="0 0 24 24"><polyline points="4 17 10 11 4 5"/><line x1="12" y1="19" x2="20" y2="19"/></svg>`,
  service: `<svg width="18" height="18" fill="none" stroke="currentColor" stroke-width="2" viewBox="0 0 24 24"><circle cx="12" cy="12" r="3"/><path d="M19.4 15a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 0 1 0 2.83 2 2 0 0 1-2.83 0l-.06-.06a1.65 1.65 0 0 0-1.82-.33 1.65 1.65 0 0 0-1 1.51V21a2 2 0 0 1-2 2 2 2 0 0 1-2-2v-.09A1.65 1.65 0 0 0 9 19.4a1.65 1.65 0 0 0-1.82.33l-.06.06a2 2 0 0 1-2.83 0 2 2 0 0 1 0-2.83l.06-.06a1.65 1.65 0 0 0 .33-1.82 1.65 1.65 0 0 0-1.51-1H3a2 2 0 0 1-2-2 2 2 0 0 1 2-2h.09A1.65 1.65 0 0 0 4.6 9a1.65 1.65 0 0 0-.33-1.82l-.06-.06a2 2 0 0 1 0-2.83 2 2 0 0 1 2.83 0l.06.06a1.65 1.65 0 0 0 1.82.33H9a1.65 1.65 0 0 0 1-1.51V3a2 2 0 0 1 2-2 2 2 0 0 1 2 2v.09a1.65 1.65 0 0 0 1 1.51 1.65 1.65 0 0 0 1.82-.33l.06-.06a2 2 0 0 1 2.83 0 2 2 0 0 1 0 2.83l-.06.06a1.65 1.65 0 0 0-.33 1.82V9a1.65 1.65 0 0 0 1.51 1H21a2 2 0 0 1 2 2 2 2 0 0 1-2 2h-.09a1.65 1.65 0 0 0-1.51 1z"/></svg>`,
  globe: `<svg width="18" height="18" fill="none" stroke="currentColor" stroke-width="2" viewBox="0 0 24 24"><circle cx="12" cy="12" r="10"/><path d="M12 2a15.3 15.3 0 0 1 4 10 15.3 15.3 0 0 1-4 10 15.3 15.3 0 0 1-4-10 15.3 15.3 0 0 1 4-10z"/><path d="M2 12h20"/></svg>`,
  image: `<svg width="18" height="18" fill="none" stroke="currentColor" stroke-width="2" viewBox="0 0 24 24"><rect x="3" y="3" width="18" height="18" rx="2"/><circle cx="8.5" cy="8.5" r="1.5"/><polyline points="21 15 16 10 5 21"/></svg>`,
  ai: `<svg width="18" height="18" fill="none" stroke="currentColor" stroke-width="2" viewBox="0 0 24 24"><path d="M12 2v4M12 18v4M4.93 4.93l2.83 2.83M16.24 16.24l2.83 2.83M2 12h4M18 12h4M4.93 19.07l2.83-2.83M16.24 7.76l2.83-2.83"/></svg>`,
  default: `<svg width="18" height="18" fill="none" stroke="currentColor" stroke-width="2" viewBox="0 0 24 24"><polyline points="16 18 22 12 16 6"/><polyline points="8 6 2 12 8 18"/></svg>`,
};

// ═══════════════════════════════════════════════════════════════════════════
// ── XÁC THỰC VÀ GIAO TIẾP API TRUNG TÂM (AUTH & CENTRAL API FETCH LAYER) ──
// ═══════════════════════════════════════════════════════════════════════════

function getAuthToken() {
  return localStorage.getItem('vnmateai_token') || '';
}

function getStoredUser() {
  try {
    return JSON.parse(localStorage.getItem('vnmateai_user') || 'null');
  } catch (_) {
    return null;
  }
}

/**
 * Thông báo lỗi dễ đọc từ phản hồi của máy chủ: `detail` có thể là chuỗi, đối tượng, hoặc MẢNG lỗi
 * kiểm tra dữ liệu (422). Trước đây `new Error(data.detail)` với mảng cho ra "[object Object]".
 */
function apiErrorText(data, status) {
  const d = data && data.detail;
  if (typeof d === 'string' && d) return d;
  if (Array.isArray(d) && d.length) {
    return d.map((e) => (e && e.msg ? `${(e.loc || []).slice(1).join('.') || 'dữ liệu'}: ${e.msg}` : String(e))).join('; ');
  }
  if (d && typeof d === 'object') return d.message || JSON.stringify(d);
  return `HTTP ${status}`;
}

/**
 * Lỗi lập trình (TypeError "Cannot read properties of undefined"…) không phải thứ người dùng cần đọc:
 * hiện câu dễ hiểu, còn chi tiết kỹ thuật đẩy sang console. Lỗi mạng / HTTP đã có chữ tiếng Việt thì giữ nguyên.
 */
function friendlyErrorText(err) {
  const msg = String((err && err.message) || err || '');
  if (/Cannot read propert|undefined|is not a function|is not defined|is not iterable|Unexpected token|JSON/i.test(msg)) {
    console.error('[Portal] Lỗi xử lý dữ liệu:', err);
    return 'Dữ liệu máy chủ trả về chưa đầy đủ hoặc sai định dạng. Hãy bấm Làm mới; nếu vẫn lỗi, báo quản trị viên (chi tiết ở console trình duyệt).';
  }
  return msg || 'lỗi không rõ';
}

async function apiFetch(url, options = {}) {
  const token = getAuthToken();
  const headers = {
    ...(options.headers || {}),
  };
  if (token && !headers['Authorization']) {
    headers['Authorization'] = `Bearer ${token}`;
  }
  // Mọi API ghi của máy chủ nhận JSON. Gửi chuỗi mà không khai báo kiểu thì trình duyệt tự đặt
  // `text/plain` và máy chủ trả 422 — nút "Tôi đã kiểm tra — xác nhận hoàn thành" từng chết vì vậy.
  // Body không phải chuỗi (FormData, Blob…) giữ nguyên để trình duyệt tự đặt kiểu.
  if (typeof options.body === 'string' && !Object.keys(headers).some((k) => k.toLowerCase() === 'content-type')) {
    headers['Content-Type'] = 'application/json';
  }

  try {
    const response = await fetch(url, { ...options, headers });
    if ((response.status === 401 || response.status === 403) && !url.includes('/api/v1/login')) {
      if (response.status === 401) {
        console.warn('[Auth] Phiên đăng nhập hết hạn hoặc tài khoản không tồn tại (401). Đăng xuất...');
        handleLogout('Phiên đăng nhập đã hết hạn hoặc tài khoản đã bị xóa. Vui lòng đăng nhập lại.');
      } else if (response.status === 403 && (url.includes('/api/v1/auth/me') || url.includes('/api/v1/users'))) {
        console.warn('[Auth] Quyền truy cập bị từ chối (403).');
        // Không ép logout ngay nếu là action thông thường bị từ chối quyền, trừ khi bị revoked hoàn toàn
      }
    }
    return response;
  } catch (err) {
    throw err;
  }
}

async function apiLogin(username, password) {
  try {
    const res = await fetch(`${API_BASE}/api/v1/login`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ username, password }),
    });
    const data = await res.json();
    if (!res.ok) throw new Error(data.detail || `HTTP ${res.status}`);
    return data;
  } catch (err) {
    console.error('[Auth] Lỗi đăng nhập:', err);
    return { status: 'error', detail: err.message };
  }
}

async function apiGetMe() {
  try {
    const res = await apiFetch(`${API_BASE}/api/v1/auth/me`);
    if (!res.ok) return null;
    return await res.json();
  } catch (err) {
    return null;
  }
}

async function apiGetAudioNodes() {
  try {
    const res = await apiFetch(`${API_BASE}/api/v1/audio-nodes`);
    if (!res.ok) return null;
    return await res.json();
  } catch (err) {
    console.error('[API] Lỗi lấy danh sách audio nodes:', err);
    return null;
  }
}

async function apiGetKpiLogs(params = {}) {
  try {
    const query = new URLSearchParams(params).toString();
    const res = await apiFetch(`${API_BASE}/api/v1/tasks/kpi-logs${query ? '?' + query : ''}`);
    if (!res.ok) return null;
    return await res.json();
  } catch (err) {
    console.error('[API] Lỗi lấy danh sách nhật ký KPI:', err);
    return null;
  }
}

async function apiSendTask(clientId, message, sender) {
  const res = await apiFetch(`${API_BASE}/api/v1/tasks/send`, {
    method: 'POST',
    body: JSON.stringify({ client_id: clientId, message, sender }),
  });
  if (!res.ok) {
    const err = await res.json();
    throw new Error(err.detail || 'Lỗi gửi tác vụ');
  }
  return await res.json();
}

async function apiGetHealth() {
  try {
    const res = await apiFetch(`${API_BASE}/health`);
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    return await res.json();
  } catch (err) {
    console.error('[API] Lỗi kiểm tra máy chủ:', err);
    return null;
  }
}

async function apiGetSystemStats() {
  try {
    const res = await apiFetch(`${API_BASE}/api/v1/system/stats`);
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    return await res.json();
  } catch (err) {
    console.error('[API] Lỗi lấy thống kê hệ thống:', err);
    return null;
  }
}

async function apiGetHealthDashboard() {
  try {
    const res = await apiFetch(`${API_BASE}/api/v1/health-dashboard`);
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    return await res.json();
  } catch (err) {
    console.error('[API] Lỗi lấy health dashboard:', err);
    return null;
  }
}

// Phase 70: Cache config 30s TTL — switch tab không re-fetch nếu data còn mới.
let _cachedConfig = null;
let _cachedConfigAt = 0;
const _CONFIG_TTL_MS = 30_000;

async function apiGetConfig({ forceRefresh = false } = {}) {
  const now = Date.now();
  if (!forceRefresh && _cachedConfig && (now - _cachedConfigAt) < _CONFIG_TTL_MS) {
    return _cachedConfig;
  }
  try {
    const res = await apiFetch(`${API_BASE}/api/v1/config`);
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    _cachedConfig = await res.json();
    _cachedConfigAt = Date.now();
    return _cachedConfig;
  } catch (err) {
    console.error('[API] Lỗi đọc cấu hình:', err);
    return null;
  }
}

async function apiSaveConfig(configData) {
  try {
    const res = await apiFetch(`${API_BASE}/api/v1/config`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(configData),
    });
    const data = await res.json();
    if (!res.ok) {
      const d = data.detail;
      if (d && typeof d === 'object') {
        const lines = [...(d.errors || []), ...((d.unknown_models || []).map((m) => `Model không có trên 9Router — ${m}`))];
        throw new Error(`${d.message || 'Cấu hình chưa hợp lệ'}\n• ${lines.join('\n• ')}`);
      }
      throw new Error(d || `HTTP ${res.status}`);
    }
    // Phase 70: Bust cache ngay sau khi lưu — lần đọc tiếp theo sẽ lấy
    // data mới từ server thay vì trả về config cũ trong 30s TTL.
    _cachedConfig = null;
    _cachedConfigAt = 0;
    return { success: true, message: data.message || 'Đã lưu cấu hình thành công.' };
  } catch (err) {
    console.error('[API] Lỗi lưu cấu hình:', err);
    return { success: false, message: err.message };
  }
}

async function apiGetSkills() {
  try {
    const res = await apiFetch(`${API_BASE}/api/v1/skills`);
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    return await res.json();
  } catch (err) {
    console.error('[API] Lỗi lấy danh sách kỹ năng:', err);
    return null;
  }
}

async function apiToggleSkill(name, enabled) {
  try {
    const res = await apiFetch(`${API_BASE}/api/v1/skills/toggle`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ name, enabled }),
    });
    const data = await res.json();
    if (!res.ok) throw new Error(data.detail || `HTTP ${res.status}`);
    return { success: true, enabled: data.enabled };
  } catch (err) {
    console.error('[API] Lỗi bật/tắt kỹ năng:', err);
    return { success: false, message: err.message };
  }
}

async function apiCreateSkill(payload) {
  try {
    const res = await apiFetch(`${API_BASE}/api/v1/skills/create`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
    });
    const data = await res.json();
    if (!res.ok) throw new Error(data.detail || `HTTP ${res.status}`);
    return { success: true, message: data.message, skill: data.skill };
  } catch (err) {
    console.error('[API] Lỗi thêm kỹ năng mới:', err);
    return { success: false, message: err.message };
  }
}

async function apiExecuteSkill(name, args = {}) {
  try {
    const res = await apiFetch(`${API_BASE}/api/v1/skills/execute`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ name, arguments: args }),
    });
    const data = await res.json();
    if (!res.ok) throw new Error(data.detail || `HTTP ${res.status}`);
    return data;
  } catch (err) {
    console.error('[API] Lỗi thực thi kỹ năng:', err);
    return { status: 'error', error: err.message, latency_ms: 0 };
  }
}

async function apiBatchToggleSkills(enabled) {
  try {
    const res = await apiFetch(`${API_BASE}/api/v1/skills/batch-toggle`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ enabled }),
    });
    const data = await res.json();
    if (!res.ok) throw new Error(data.detail || `HTTP ${res.status}`);
    return data;
  } catch (err) {
    console.error('[API] Lỗi bật/tắt hàng loạt kỹ năng:', err);
    return { success: false, message: err.message };
  }
}

// Phase 25: In-memory conversation history for multi-turn context (Web Portal session)
let _webChatHistory = [];
const _MAX_HISTORY_TURNS = 10; // Keep last 10 turns (20 messages)

async function apiVoiceCommand(query, includeAudio = true, history = null) {
  const res = await apiFetch(`${API_BASE}/api/v1/voice-command`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      query,
      source_device: 'web',
      include_audio: includeAudio,
      history: history !== null ? history : _webChatHistory.slice(-_MAX_HISTORY_TURNS * 2),
    }),
  });
  if (!res.ok) {
    const err = await res.json().catch(() => ({}));
    throw new Error(err.detail || `HTTP ${res.status}`);
  }
  const data = await res.json();
  // Update local history for multi-turn continuity
  _webChatHistory.push({ role: 'user', content: query });
  if (data.reply) {
    _webChatHistory.push({ role: 'assistant', content: data.reply });
  }
  // Keep history bounded
  if (_webChatHistory.length > _MAX_HISTORY_TURNS * 2) {
    _webChatHistory = _webChatHistory.slice(-_MAX_HISTORY_TURNS * 2);
  }
  return data;
}

async function apiTTS(text) {
  const res = await apiFetch(`${API_BASE}/api/v1/tts`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ text, voice: 'vi-VN-HoaiMyNeural' }),
  });
  if (!res.ok) throw new Error(`Tổng hợp giọng nói thất bại (${res.status})`);
  const blob = await res.blob();
  return URL.createObjectURL(blob);
}

async function apiGetClients() {
  try {
    const res = await apiFetch(`${API_BASE}/api/v1/clients`);
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    return await res.json();
  } catch (err) {
    console.error('[API] Lỗi lấy danh sách máy trạm:', err);
    return [];
  }
}

async function apiExecuteOnClient(clientId, skillName, args = {}) {
  try {
    const res = await apiFetch(`${API_BASE}/api/v1/clients/${encodeURIComponent(clientId)}/execute`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ skill_name: skillName, args: args }),
    });
    const data = await res.json();
    if (!res.ok) throw new Error(data.detail || `HTTP ${res.status}`);
    return data;
  } catch (err) {
    console.error('[API] Lỗi thực thi lệnh máy trạm:', err);
    return { status: 'error', error: err.message };
  }
}

async function apiGetLocalWorkerStatus() {
  try {
    const res = await apiFetch(`${API_BASE}/api/v1/orchestrator/local-worker/status`);
    if (!res.ok) return { active: false };
    return await res.json();
  } catch (err) {
    return { active: false };
  }
}

async function apiToggleLocalWorker() {
  try {
    const res = await apiFetch(`${API_BASE}/api/v1/orchestrator/local-worker/toggle`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
    });
    const data = await res.json();
    if (!res.ok) throw new Error(data.detail || `HTTP ${res.status}`);
    return data;
  } catch (err) {
    console.error('[API] Lỗi toggle local worker:', err);
    return { active: false, message: err.message };
  }
}

async function apiGetBlacklist() {
  try {
    const res = await apiFetch(`${API_BASE}/api/v1/security/blacklist`);
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    return await res.json();
  } catch (err) {
    console.error('[API] Lỗi đọc blacklist:', err);
    return null;
  }
}

async function apiUpdateBlacklist(action, keyword, category = 'blacklist') {
  try {
    const res = await apiFetch(`${API_BASE}/api/v1/security/blacklist`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ action, keyword, category }),
    });
    const data = await res.json();
    if (!res.ok) throw new Error(data.detail || `HTTP ${res.status}`);
    return data;
  } catch (err) {
    console.error('[API] Lỗi cập nhật chính sách bảo mật:', err);
    return { status: 'error', detail: err.message };
  }
}

async function apiInspectSecuritySandbox(type, content, params = null) {
  try {
    const res = await apiFetch(`${API_BASE}/api/v1/security/inspect`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ type, content, params }),
    });
    const data = await res.json();
    if (!res.ok) throw new Error(data.detail || `HTTP ${res.status}`);
    return data;
  } catch (err) {
    console.error('[API] Lỗi phân tích sandbox an ninh:', err);
    return { status: 'error', detail: err.message };
  }
}

async function apiGetAuditLogs(limit = 100) {
  try {
    const res = await apiFetch(`${API_BASE}/api/v1/security/audit-logs?limit=${limit}`);
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    return await res.json();
  } catch (err) {
    console.error('[API] Lỗi đọc audit logs:', err);
    return null;
  }
}

async function apiConfirmAction(clientId, skillName, args, approved) {
  try {
    const res = await apiFetch(`${API_BASE}/api/v1/security/confirm-action`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        client_id: clientId,
        skill_name: skillName,
        args: args,
        approved: approved,
      }),
    });
    const data = await res.json();
    if (!res.ok) throw new Error(data.detail || `HTTP ${res.status}`);
    return data;
  } catch (err) {
    console.error('[API] Lỗi xác nhận khẩn cấp:', err);
    return { status: 'error', detail: err.message };
  }
}

// ═══════════════════════════════════════════════════════════════════════════
// ── ĐIỀU HƯỚNG TAB & GIAO DIỆN ─────────────────────────────────────────────
// ═══════════════════════════════════════════════════════════════════════════

// Phase 78: bỏ 'users' và 'devices' — nội dung hai tab này đã gộp vào
// 'security' (kiểm soát truy cập) và 'system-integration' (kết nối ra ngoài);
// sau đó 'command-center' cũng gộp vào 'dashboard'.
// Danh sách chỉ còn tab thật sự tồn tại; link cũ #users / #devices /
// #command-center sẽ tự rơi về dashboard thay vì mở một tab không có.
const VALID_TABS = ['dashboard', 'infra-monitor', 'playbooks', 'agent-gateway', 'dev-fleet', 'system-integration', 'ai-manager', 'skills', 'voice', 'config', 'security', 'tasks', 'logs'];

function getSavedTab() {
  const hash = (window.location.hash || '').replace('#', '').trim();
  if (hash && VALID_TABS.includes(hash)) {
    return hash;
  }
  try {
    const saved = localStorage.getItem('vnmateai_active_tab');
    if (saved && VALID_TABS.includes(saved)) {
      return saved;
    }
  } catch (e) { }
  return 'dashboard';
}

function restoreActiveTab() {
  let targetTab = getSavedTab();
  const targetNav = document.getElementById(`nav-${targetTab}`);
  if (targetNav && targetNav.classList.contains('hidden')) {
    targetTab = 'dashboard';
  }
  switchTab(targetTab);

  // Global background poll for security approvals
  checkPendingAction();
  if (!window._globalPendingTimer) {
    window._globalPendingTimer = setInterval(checkPendingAction, 3000);
  }
}

/**
 * Chuyển nguồn nhật ký trong tab "Nhật Ký".
 *
 * Phase 78: trước đây nhật ký nằm rải rác ở 5 tab với 5 bảng riêng — luồng
 * thời gian thực ở tab Nhật Ký, kiểm toán an ninh ở tab Bảo Mật, công việc KPI
 * ở tab Công Việc, cộng thêm bản trong Trung Tâm Chỉ Huy và Bảng Điều Khiển.
 * Cùng dữ liệu ở nhiều nơi thì dễ lệch số liệu và người dùng không biết bản
 * nào là chính. Nay gom hết về đây, mỗi nguồn một chế độ.
 */
function switchLogView(view) {
  const views = ['stream', 'security', 'kpi', 'recent'];
  if (!views.includes(view)) view = 'stream';

  for (const v of views) {
    const panel = document.getElementById(`log-view-${v}`);
    if (panel) panel.classList.toggle('hidden', v !== view);
  }

  document.querySelectorAll('.log-view-tab').forEach((btn) => {
    const on = btn.dataset.logView === view;
    btn.setAttribute('aria-selected', on ? 'true' : 'false');
    btn.classList.toggle('border-cyan-500/50', on);
    btn.classList.toggle('bg-cyan-500/10', on);
    btn.classList.toggle('text-cyan-400', on);
    btn.classList.toggle('border-slate-700', !on);
    btn.classList.toggle('bg-slate-900/50', !on);
    btn.classList.toggle('text-slate-400', !on);
  });

  // Mỗi bảng nạp riêng khi người dùng chuyển tới, thay vì cả 4 cùng lúc.
  // `loadSecurityCenter` nạp cả chính sách lẫn nhật ký kiểm toán; `loadKpiLogs`
  // nạp nhật ký công việc. `loadOpsLog` nằm trong IIFE của CommandCenter nên
  // phải gọi qua đối tượng được export — nó đã được nạp khi vào màn hình C.E.O
  // nhưng bảng của nó giờ ở tab Nhật Ký, nạp lại để hiện đúng lúc người dùng
  // nhìn vào nó.
  if (view === 'security' && typeof loadSecurityCenter === 'function') {
    loadSecurityCenter();
  }
  if (view === 'kpi' && typeof loadKpiLogs === 'function') {
    loadKpiLogs();
  }
  if (view === 'recent' && typeof CommandCenter !== 'undefined') {
    CommandCenter.loadOpsLog();
  }
}

function switchTab(tabId) {
  if (!VALID_TABS.includes(tabId)) {
    tabId = 'dashboard';
  }
  // Topology is served as a standalone Next.js page — open in new tab
  // to keep the portal session intact (same pattern as the sidebar link).
  if (tabId === 'topology') {
    window.open('/admin/topology', '_blank', 'noopener,noreferrer');
    return;
  }

  document.querySelectorAll('.nav-btn').forEach(btn => btn.classList.remove('active'));
  const activeNav = document.getElementById(`nav-${tabId}`);
  if (activeNav) activeNav.classList.add('active');

  // Dọn tài nguyên của tab vừa rời (Command Center có timer polling 5 giây;
  // không dừng thì tab ẩn vẫn gọi API liên tục). Phải LÀM TRƯỚC khi bật tab
  // mới, vì biến `tabId` đã bị gán đè ở trên.
  // Phase 79: tab "Trung Tâm Chỉ Huy" đã gộp vào `dashboard`, nên móc vòng
  // đời của CommandCenter bám theo tab Bảng Điều Khiển. Nếu để nguyên
  // 'command-center' thì `onLeave` không bao giờ chạy → bộ hẹn giờ 5s gọi
  // API cứ tiếp tục chạy sau khi người dùng đã rời đi, và `onEnter` không
  // bao giờ chạy → phần C.E.O không bao giờ có dữ liệu.
  const _prevPane = document.querySelector('.tab-pane.active');
  if (_prevPane && typeof CommandCenter !== 'undefined') {
    const _prevId = (_prevPane.id || '').replace(/^tab-/, '');
    if (_prevId === 'dashboard' && _prevId !== tabId) {
      CommandCenter.onLeave();
    }
  }
  // Dev Fleet: dừng vòng làm mới 10 s khi rời tab.
  if (typeof DevFleetUI !== 'undefined' && tabId !== 'dev-fleet') DevFleetUI.onLeave();
  if (typeof InfraMonitorUI !== 'undefined' && tabId !== 'infra-monitor') InfraMonitorUI.onLeave();
  if (typeof PlaybooksUI !== 'undefined' && tabId !== 'playbooks') PlaybooksUI.onLeave();
  if (typeof LlmOpsUI !== 'undefined' && tabId !== 'ai-manager') LlmOpsUI.onLeave();
  // Phase 85: dừng bộ hẹn giờ 10s của danh sách máy trạm khi rời tab Tích Hợp.
  // Bật lại khi quay lại tab và bấm sub-tab Máy Trạm (`switchCcSubTab`).
  if (typeof _devicesPollStop === 'function' && tabId !== 'system-integration') {
    _devicesPollStop();
  }

  document.querySelectorAll('.tab-pane').forEach(pane => pane.classList.remove('active'));
  const activePane = document.getElementById(`tab-${tabId}`);
  if (activePane) activePane.classList.add('active');

  const titleEl = document.getElementById('header-title');
  if (titleEl) titleEl.textContent = TAB_TITLES[tabId] || 'Bảng Điều Khiển';

  // Lưu trạng thái tab hiện tại vào localStorage và cập nhật URL hash để khi F5 giữ nguyên vị trí
  try {
    localStorage.setItem('vnmateai_active_tab', tabId);
    if (window.location.hash !== `#${tabId}`) {
      if (window.history && window.history.replaceState) {
        window.history.replaceState(null, '', `#${tabId}`);
      } else {
        window.location.hash = `#${tabId}`;
      }
    }
  } catch (e) {
    console.warn('[Portal] Lỗi lưu trạng thái tab:', e);
  }

  if (tabId === 'dashboard') {
    loadDashboard();
    loadSupervisorOverview();
    // Phase 79: nội dung C.E.O gộp vào Bảng Điều Khiển nên `onEnter` bám theo
    // tab này. Thiếu dòng này thì phần điều hành AI, biểu đồ và cảnh báo an
    // ninh không bao giờ có dữ liệu.
    if (typeof CommandCenter !== 'undefined') CommandCenter.onEnter();
  }
  // Phase 59/60: khối tích hợp đã tách sang tab riêng nên nạp dữ liệu ở đây,
  // không gắn vào CommandCenter.onEnter() — nếu không, mở Trung Tâm Chỉ Huy sẽ
  // tải 4 API của tích hợp dù trên màn hình đó không còn dòng dữ liệu nào.
  // Phase 81: KHÔNG gọi `loadDevices()` ở đây nữa. Danh sách máy trạm đã
  // chuyển vào sub-tab "Máy Trạm" và chỉ nạp khi người dùng bấm vào
  // (`switchCcSubTab('devices')`). Gọi ở đây nghĩa là mở tab Tích Hợp phải trả
  // thêm một request cho danh sách mà người dùng chưa nhìn tới.
  if (tabId === 'dev-fleet' && typeof DevFleetUI !== 'undefined') DevFleetUI.onEnter();
  if (tabId === 'infra-monitor' && typeof InfraMonitorUI !== 'undefined') InfraMonitorUI.onEnter();
  if (tabId === 'playbooks' && typeof PlaybooksUI !== 'undefined') PlaybooksUI.onEnter();
  if (tabId === 'agent-gateway' && typeof AgentDirectoryUI !== 'undefined') AgentDirectoryUI.onEnter();
  if (tabId === 'agent-gateway' && typeof AgentGatewayUI !== 'undefined') AgentGatewayUI.onEnter();
  if (tabId === 'system-integration') loadSystemIntegration();
  if (tabId === 'ai-manager') { loadAIManagerConfig(); initAISubTabs(); }
  if (tabId === 'skills') loadSkills();
  // Phase 79: `loadConfig()` trước đây KHÔNG được gọi ở đâu cả — không trong
  // switchTab, không trong index.html. Nghĩa là tab Cấu Hình luôn mở ra với
  // một form TRỐNG: base URL, model, tên trợ lý, mức log đều không có, dù
  // server có dữ liệu. Người dùng thấy form rỗng rồi bấm Lưu thì ghi đè
  // cấu hình bằng giá trị rỗng. Đúng loại "giao diện trông như có khả năng
  // nhưng thực sự không có" mà dự án cấm.
  if (tabId === 'config') loadConfig();
  if (tabId === 'tasks') {
    initTaskBoardFilters();
    loadTaskBoard();
    loadErpStructure();
  }
  if (tabId === 'security') {
    if (typeof MfaCard !== 'undefined') MfaCard.onEnter();
    loadAutonomyControls();
    // Phase 78: nội dung tab "Tài Khoản" đã gộp vào đây (cùng miền kiểm soát
    // truy cập), nên bảng tài khoản nạp kèm.
    fetchUsers();
    loadTelegramConfig();
    loadTelegramStatus();
    loadADSyncStatus();
  }
  if (tabId === 'voice') {
    updateVoiceTelemetry({ silent: true });
    initVoiceSubTabs();
  }
  if (tabId === 'logs') {
    if (typeof LogViewer !== 'undefined') {
      if (typeof LogViewer.fetchRecentLogs === 'function') {
        LogViewer.fetchRecentLogs();
      } else if (LogViewer.filterDisplay) {
        LogViewer.filterDisplay();
      }
    }
  }
}

// ═══════════════════════════════════════════════════════════════════════════
// ── HIỂN THỊ DỮ LIỆU BẢNG ĐIỀU KHIỂN (DASHBOARD) ───────────────────────────
// ═══════════════════════════════════════════════════════════════════════════

// ─── Helper: Update circular gauge SVG & text ─────────────────────────────
function updateGauge(pathId, textId, percent) {
  const clamped = Math.min(100, Math.max(0, percent || 0));
  const pathEl = document.getElementById(pathId);
  const textEl = document.getElementById(textId);
  if (pathEl) pathEl.setAttribute('stroke-dasharray', `${clamped.toFixed(1)}, 100`);
  if (textEl) textEl.textContent = `${Math.round(clamped)}%`;
}

let healthDashboardTimer = null;

function updateServiceBadge(badgeId, detailId, svc) {
  const badgeEl = document.getElementById(badgeId);
  const detailEl = document.getElementById(detailId);
  if (!badgeEl) return;

  if (!svc) {
    badgeEl.className = 'svc-badge svc-unknown';
    badgeEl.textContent = '⏳ Chờ';
    if (detailEl) detailEl.textContent = 'Chưa có thông tin';
    return;
  }

  if (svc.status === 'OK') {
    badgeEl.className = 'svc-badge svc-ok';
    const latVal = svc.latency_ms ?? svc.latency;
    const latency = (latVal !== undefined && latVal !== null && latVal > 0) ? ` (${latVal}ms)` : '';
    badgeEl.innerHTML = `<span class="w-1.5 h-1.5 rounded-full bg-emerald-500 inline-block"></span> Hoạt động${latency}`;
  } else if (svc.status === 'FAIL') {
    badgeEl.className = 'svc-badge svc-fail';
    badgeEl.innerHTML = `<span class="w-1.5 h-1.5 rounded-full bg-red-500 inline-block"></span> Gián đoạn`;
  } else {
    badgeEl.className = 'svc-badge svc-unknown';
    badgeEl.textContent = '⏳ Chờ';
  }

  if (detailEl && svc.detail) {
    detailEl.textContent = svc.detail;
  }
}

// ─── Bảng Chỉ Huy C-Level: 6 ô trạng thái THẬT ───────────────────────────────
// Trước đây 6 ô là chữ gõ cứng ("Trực Tuyến", "Trợ Lý Online", "HITL Active"…): robot ngắt, Telegram
// hay 9Router lỗi vẫn xanh. Giờ lấy từ payload /api/v1/health-dashboard (cùng nguồn các panel khác).
// Tách `execPillarsModel` (thuần, không đụng DOM — có test Node) khỏi `renderExecutivePillars` (vẽ).
const EXEC_STALE_MS = 45_000;           // bộ giám sát ghi mỗi 3 s: quá 45 s là dữ liệu cũ
const EXEC_SERVICE_NAMES = {
  llm_9router: '9Router', active_directory: 'AD', telegram_gateway: 'Telegram', database_sqlite: 'CSDL',
};

function execPillarsModel(data, nowMs = Date.now()) {
  const pending = () => ({ title: 'Đang kiểm tra…', sub: '—', state: 'idle', tip: '' });
  if (!data) {      // không đọc được máy chủ: chỉ ô cụm báo lỗi, các ô còn lại không đoán
    const none = { title: '—', sub: '—', state: 'idle', tip: '' };
    return { bar: 'bad', pillars: [{ title: 'Mất liên lạc', sub: 'Không đọc được trạng thái máy chủ', state: 'bad', tip: '' },
      none, none, none, none, none] };
  }
  const svc = data.services || {};
  const nodes = data.nodes || {};
  const counters = data.counters || {};
  const stale = !data.last_updated || (nowMs - data.last_updated * 1000) > EXEC_STALE_MS;

  // 1. Cụm: tổng hợp từ các dịch vụ; dữ liệu cũ = bộ giám sát nền đã dừng.
  const bad = Object.entries(EXEC_SERVICE_NAMES).filter(([k]) => svc[k] && svc[k].status === 'FAIL').map(([, v]) => v);
  const unknown = Object.keys(svc).length === 0;
  let p1;
  if (stale) p1 = { title: 'Dữ liệu cũ', sub: 'Bộ giám sát nền không cập nhật', state: 'warn', tip: '' };
  else if (unknown) p1 = pending();
  else if (data.status === 'healthy' && bad.length === 0) p1 = { title: 'Trực Tuyến', sub: 'Mọi dịch vụ đang hoạt động', state: 'ok', tip: '' };
  else p1 = { title: 'Suy Giảm', sub: bad.length ? `Cần chú ý: ${bad.join(', ')}` : 'Có dịch vụ chưa báo OK', state: 'warn', tip: '' };

  // 2. Máy trạm / worker (agent kết nối qua /ws/client)
  const lan = Number(nodes.active_lan_clients ?? 0);
  const p2 = lan > 0
    ? { title: `${lan} Máy Trạm`, sub: 'Đang kết nối, nhận việc được', state: 'ok', tip: '' }
    : { title: 'Chưa Có Máy Trạm', sub: 'Chưa agent / worker nào kết nối', state: 'idle', tip: '' };

  // 3. 9Router
  const llm = svc.llm_9router;
  let p3;
  if (!llm || llm.status === 'UNKNOWN') p3 = pending();
  else if (llm.status === 'OK') {
    const ms = llm.latency_ms ?? llm.latency;
    p3 = { title: 'Hoạt Động', sub: `${llm.model || 'chưa chọn model'}${ms > 0 ? ` · ${Math.round(ms)} ms` : ''}`, state: 'ok', tip: llm.detail || '' };
  } else p3 = { title: 'Gián Đoạn', sub: llm.detail || 'Không kết nối được 9Router', state: 'bad', tip: llm.detail || '' };

  // 4. Robot trợ lý (thiết bị âm thanh đang nối WebSocket)
  const robots = Number(nodes.active_audio_hardware ?? 0);
  const p4 = robots > 0
    ? { title: robots === 1 ? 'Robot Online' : `${robots} Robot Online`, sub: 'Đã kết nối, sẵn sàng nghe', state: 'ok', tip: '' }
    : { title: 'Robot Offline', sub: 'Chưa có robot kết nối', state: 'idle', tip: '' };

  // 5. Telegram + hàng chờ duyệt HITL (duyệt được cả trên portal khi Telegram tắt)
  const tg = svc.telegram_gateway;
  const waiting = Number(counters.zt_pending ?? 0);
  const waitTxt = waiting > 0 ? `${waiting} yêu cầu chờ duyệt` : 'Không có yêu cầu chờ duyệt';
  let p5;
  if (!tg || tg.status === 'UNKNOWN') p5 = pending();
  else if (tg.status === 'OK') p5 = { title: 'Telegram Online', sub: waitTxt, state: waiting > 0 ? 'warn' : 'ok', tip: tg.detail || '' };
  else if (/Chưa cấu hình/.test(tg.detail || '')) p5 = { title: 'Telegram Chưa Bật', sub: `${tg.detail} · ${waitTxt}`, state: 'idle', tip: tg.detail || '' };
  else p5 = { title: 'Telegram Gián Đoạn', sub: `${tg.detail || 'Gateway đang dừng'} · ${waitTxt}`, state: 'bad', tip: tg.detail || '' };

  // 6. CSDL chính + đồng bộ AD
  const db = svc.database_sqlite || svc.database;
  const ad = svc.active_directory || {};
  const adTxt = ad.status === 'OK'
    ? (ad.employees_count > 0 ? `AD ${ad.last_sync}` : 'AD chưa đồng bộ')
    : (ad.status === 'FAIL' ? 'AD lỗi' : 'AD chưa rõ');
  let p6;
  if (!db || db.status === 'UNKNOWN') p6 = pending();
  else if (db.status === 'OK') p6 = { title: 'CSDL Sẵn Sàng', sub: `${db.detail || 'CSDL'} · ${adTxt}`, state: ad.status === 'FAIL' ? 'warn' : 'ok', tip: `${db.detail || ''} | ${ad.detail || ''}` };
  else p6 = { title: 'CSDL Lỗi', sub: db.detail || 'Không truy vấn được CSDL chính', state: 'bad', tip: db.detail || '' };

  const pillars = [p1, p2, p3, p4, p5, p6];
  const worst = pillars.some((p) => p.state === 'bad') ? 'bad' : pillars.some((p) => p.state === 'warn') ? 'warn' : p1.state;
  return { bar: stale ? 'warn' : worst, pillars };
}

const EXEC_STATE_COLOR = { ok: '', warn: '#f59e0b', bad: '#f43f5e', idle: '#94a3b8' };
const EXEC_DOT_COLOR = { ok: '#10b981', warn: '#f59e0b', bad: '#f43f5e', idle: '#94a3b8' };

function renderExecutivePillars(data) {
  const model = execPillarsModel(data);
  const ids = [['exec-cluster-status-text', 'exec-p1-sub'], ['exec-p2-title', 'exec-p2-sub'], ['exec-p3-title', 'exec-p3-sub'],
    ['exec-p4-title', 'exec-p4-sub'], ['exec-p5-title', 'exec-p5-sub'], ['exec-p6-title', 'exec-p6-sub']];
  model.pillars.forEach((p, i) => {
    const t = document.getElementById(ids[i][0]);
    const s = document.getElementById(ids[i][1]);
    if (t) {
      t.textContent = p.title;
      t.style.color = EXEC_STATE_COLOR[p.state] || '';
      t.title = p.tip || '';
    }
    if (s) {
      s.textContent = p.sub;
      s.style.color = EXEC_STATE_COLOR[p.state] || '';
      s.title = p.sub;
    }
  });
  // Chấm trạng thái: nhấp nháy chỉ khi mọi thứ ổn.
  for (const [id, state] of [['exec-p1-dot', model.pillars[0].state], ['exec-bar-dot', model.bar]]) {
    const dot = document.getElementById(id);
    if (!dot) continue;
    dot.style.backgroundColor = EXEC_DOT_COLOR[state] || EXEC_DOT_COLOR.idle;
    dot.classList.toggle('animate-pulse', state === 'ok');
  }
}

async function fetchAndRenderHealthDashboard() {
  const data = await apiGetHealthDashboard();
  renderExecutivePillars(data);      // data = null khi máy chủ không trả lời -> ô cụm báo "Mất liên lạc"
  if (!data) return;

  // Phase 61: tầng NHANH (2s) — counter rẻ đi kèm payload này, không gọi thêm API
  renderMonitorFastPath(data);

  // 1. Hardware Gauges & Progress Bars (Phase 46 Extended)
  if (data.hardware) {
    const hw = data.hardware;
    updateGauge('gauge-cpu-path', 'gauge-cpu-text', hw.cpu_percent || 0);
    updateGauge('gauge-ram-path', 'gauge-ram-text', hw.ram_percent || 0);
    updateGauge('gauge-disk-path', 'gauge-disk-text', hw.disk_percent || 0);

    // Process count badge
    const procBadge = document.getElementById('hw-process-badge');
    if (procBadge && hw.process_count != null) {
      procBadge.textContent = `${hw.process_count} procs`;
    }

    // Sub-gauge details
    const cpuFreqEl = document.getElementById('hw-cpu-freq');
    if (cpuFreqEl) {
      cpuFreqEl.textContent = hw.cpu_freq_mhz ? `${Math.round(hw.cpu_freq_mhz)}MHz` : '--MHz';
    }

    const ramDetailEl = document.getElementById('hw-ram-detail');
    if (ramDetailEl) {
      ramDetailEl.textContent = (hw.ram_used_gb != null && hw.ram_total_gb != null)
        ? `${hw.ram_used_gb}/${hw.ram_total_gb}GB`
        : '--/--GB';
    }

    const diskFreeEl = document.getElementById('hw-disk-free');
    if (diskFreeEl) {
      diskFreeEl.textContent = (hw.disk_free_gb != null)
        ? `${hw.disk_free_gb} GB free`
        : '-- GB free';
    }

    // CPU Cores & Sparkline History
    const cpuCoresEl = document.getElementById('hw-cpu-cores');
    if (cpuCoresEl) {
      cpuCoresEl.textContent = `${hw.cpu_cores || 1} cores`;
    }

    if (Array.isArray(hw.cpu_history) && hw.cpu_history.length > 0) {
      const sparkLine = document.getElementById('sparkline-cpu-line');
      if (sparkLine) {
        const hist = hw.cpu_history;
        const n = hist.length;
        const pts = hist.map((val, idx) => {
          const x = n > 1 ? (idx / (n - 1)) * 200 : 100;
          const clamped = Math.min(100, Math.max(0, val));
          const y = 26 - (clamped / 100) * 24;
          return `${x.toFixed(1)},${y.toFixed(1)}`;
        }).join(' ');
        sparkLine.setAttribute('points', pts);
      }
    }

    // Progress Bars with Adaptive Threshold Colors
    const cpuBar = document.getElementById('hw-cpu-bar');
    const cpuLbl = document.getElementById('hw-cpu-label');
    const cpuPct = hw.cpu_percent || 0;
    if (cpuBar) {
      cpuBar.style.width = `${Math.min(100, cpuPct)}%`;
      if (cpuPct >= 85) {
        cpuBar.className = 'h-full rounded-full bg-rose-500 transition-all duration-700';
      } else if (cpuPct >= 70) {
        cpuBar.className = 'h-full rounded-full bg-amber-500 transition-all duration-700';
      } else {
        cpuBar.className = 'h-full rounded-full bg-cyan-500 transition-all duration-700';
      }
    }
    if (cpuLbl) cpuLbl.textContent = `${cpuPct.toFixed(1)}%`;

    const ramBar = document.getElementById('hw-ram-bar');
    const ramLbl = document.getElementById('hw-ram-label');
    const ramPct = hw.ram_percent || 0;
    if (ramBar) {
      ramBar.style.width = `${Math.min(100, ramPct)}%`;
      if (ramPct >= 85) {
        ramBar.className = 'h-full rounded-full bg-rose-500 transition-all duration-700';
      } else if (ramPct >= 70) {
        ramBar.className = 'h-full rounded-full bg-amber-500 transition-all duration-700';
      } else {
        ramBar.className = 'h-full rounded-full bg-purple-500 transition-all duration-700';
      }
    }
    if (ramLbl) ramLbl.textContent = `${ramPct.toFixed(1)}%`;

    const diskBar = document.getElementById('hw-disk-bar');
    const diskLbl = document.getElementById('hw-disk-label');
    const diskPct = hw.disk_percent || 0;
    if (diskBar) {
      diskBar.style.width = `${Math.min(100, diskPct)}%`;
      if (diskPct >= 90) {
        diskBar.className = 'h-full rounded-full bg-rose-500 transition-all duration-700';
      } else if (diskPct >= 75) {
        diskBar.className = 'h-full rounded-full bg-amber-500 transition-all duration-700';
      } else {
        diskBar.className = 'h-full rounded-full bg-orange-400 transition-all duration-700';
      }
    }
    if (diskLbl) diskLbl.textContent = `${diskPct.toFixed(1)}%`;

    // Swap Bar & Label
    const swapBar = document.getElementById('hw-swap-bar');
    const swapLbl = document.getElementById('hw-swap-label');
    const swapPct = hw.swap_percent || 0;
    if (swapBar) swapBar.style.width = `${Math.min(100, swapPct)}%`;
    if (swapLbl) swapLbl.textContent = `${swapPct.toFixed(1)}%`;

    // Network I/O
    const netRecvEl = document.getElementById('hw-net-recv');
    const netSentEl = document.getElementById('hw-net-sent');
    if (netRecvEl) {
      netRecvEl.textContent = `↓ ${(hw.net_recv_mbps || 0).toFixed(2)}MB/s`;
    }
    if (netSentEl) {
      netSentEl.textContent = `↑ ${(hw.net_sent_mbps || 0).toFixed(2)}MB/s`;
    }
  }

  // 2. Services Health Badges
  if (data.services) {
    const llm = data.services.llm_9router;
    if (llm && !llm.detail) {
      llm.detail = `Model: ${llm.model || 'chưa đặt'} (${llm.latency_ms || 0}ms)`;
    }
    updateServiceBadge('svc-llm-badge', 'svc-llm-detail', llm);

    const ad = data.services.active_directory;
    if (ad && !ad.detail) {
      ad.detail = `Đồng bộ: ${ad.last_sync || 'Chưa sync'} · ${ad.employees_count || 0} NV`;
    }
    updateServiceBadge('svc-ad-badge', 'svc-ad-detail', ad);

    const tg = data.services.telegram_gateway;
    if (tg && !tg.detail) {
      // Phase 73: không tự khẳng định gateway "đang chạy" khi server không nói.
      tg.detail = tg.message || `${WAIT_TXT} — chưa có trạng thái từ server`;
    }
    updateServiceBadge('svc-tele-badge', 'svc-tele-detail', tg);

    const db = data.services.database_sqlite || data.services.database;
    if (db && !db.detail) {
      db.detail = `Dung lượng: ${db.size_kb || 0} KB`;
    }
    const dbName = document.getElementById('svc-db-name');
    if (dbName && db) {
      dbName.textContent = db.backend === 'postgresql' ? 'PostgreSQL' : db.backend === 'sqlite' ? 'SQLite' : 'Cơ sở dữ liệu';
    }
    updateServiceBadge('svc-db-badge', 'svc-db-detail', db);
  }

  // 3. Active Nodes & Telemetry
  if (data.nodes) {
    const webEl = document.getElementById('node-web-clients');
    if (webEl) webEl.textContent = data.nodes.web_clients ?? data.nodes.active_web_clients ?? 0;

    const audioEl = document.getElementById('node-audio-hw');
    if (audioEl) audioEl.textContent = data.nodes.audio_hardware ?? data.nodes.active_audio_hardware ?? 0;

    const upEl = document.getElementById('node-uptime');
    if (upEl) upEl.textContent = data.nodes.uptime || data.nodes.uptime_human || '0m';

    // Skills Telemetry & Counters
    // Phase 73: bỏ số 31 bịa. Trước đây khi server không trả `skills_count` và
    // cache rỗng, mọi ô hiển thị "31" — con số từng tồn tại trong HTML nhưng
    // không có nguồn. Nay hiện "chờ kết nối" thay vì đoán.
    const cachedCount = (typeof skillsData === 'object' && skillsData && !Array.isArray(skillsData))
      ? Object.keys(skillsData).length
      : null;
    const rawSkillsCount = _live(data.nodes.skills_count ?? cachedCount);
    const rawSkillsEnabled = _live(
      data.nodes.skills_enabled
      ?? ((typeof skillsData === 'object' && skillsData && !Array.isArray(skillsData))
        ? Object.values(skillsData).filter(s => s && s.enabled !== false).length
        : null)
    );

    const skillsCountEl = document.getElementById('node-skills-count');
    if (skillsCountEl) _setLiveText(skillsCountEl, rawSkillsCount);

    const skillsActiveEl = document.getElementById('node-skills-active');
    if (skillsActiveEl) _setLiveText(skillsActiveEl, _liveNum(rawSkillsEnabled, { suffix: ' đang bật' }));

    const headerSkillsEl = document.getElementById('header-skills-count');
    if (headerSkillsEl) _setLiveText(headerSkillsEl, rawSkillsCount);

    const svcSkillsTag = document.getElementById('svc-skills-tag');
    if (svcSkillsTag) _setLiveText(svcSkillsTag, _liveNum(rawSkillsCount, { suffix: ' Skills' }));

    const svcSkillsDetail = document.getElementById('svc-skills-detail');
    if (svcSkillsDetail) {
      _setLiveText(
        svcSkillsDetail,
        _liveNum(rawSkillsCount) === WAIT_TXT
          ? WAIT_TXT
          : `${rawSkillsCount} kỹ năng trong runtime (${rawSkillsEnabled} đang bật)`
      );
    }

    // Badge kho kỹ năng: server không có API trạng thái riêng cho registry,
    // nên trước đây nó kẹt ở "Sẵn sàng" vĩnh viễn — một khẳng định không ai
    // cập nhật. Nay bám theo `nodes.skills_count` (số đo thật): có số thì báo
    // hoạt động, không có thì báo "Chờ".
    const svcSkillsBadge = document.getElementById('svc-skills-badge');
    if (svcSkillsBadge) {
      if (_isLive(rawSkillsCount)) {
        svcSkillsBadge.className = 'svc-badge svc-ok';
        svcSkillsBadge.innerHTML = '<span class="w-1.5 h-1.5 rounded-full bg-emerald-500 inline-block"></span> Hoạt động';
      } else {
        svcSkillsBadge.className = 'svc-badge svc-unknown is-waiting';
        svcSkillsBadge.textContent = '⏳ Chờ';
      }
    }
  }

  // 4. Overall Health Badge & Timestamp
  const overallBadge = document.getElementById('health-overall-badge');
  if (overallBadge) {
    const isDegraded = data.services && Object.values(data.services).some(s => s && s.status === 'FAIL');
    if (isDegraded) {
      overallBadge.className = 'flex items-center gap-1.5 px-3 py-1 rounded-full text-xs font-bold border bg-amber-500/10 border-amber-500/30 text-amber-500 dark:text-amber-400';
      overallBadge.innerHTML = `<span class="w-1.5 h-1.5 rounded-full bg-amber-500 inline-block"></span> Có cảnh báo`;
    } else {
      overallBadge.className = 'flex items-center gap-1.5 px-3 py-1 rounded-full text-xs font-bold border bg-emerald-500/10 border-emerald-500/30 text-emerald-400 dark:text-emerald-300';
      overallBadge.innerHTML = `<span class="w-1.5 h-1.5 rounded-full bg-emerald-500 animate-pulse inline-block"></span> Hệ thống Online`;
    }
  }
  const timeEl = document.getElementById('health-timestamp');
  if (timeEl) {
    const now = new Date();
    timeEl.textContent = `Lúc ${now.toLocaleTimeString('vi-VN')}`;
  }

  // 5. Live Events Log Feed
  if (Array.isArray(data.live_events) && data.live_events.length > 0) {
    const logContainer = document.getElementById('live-event-log');
    if (logContainer) {
      logContainer.innerHTML = data.live_events.map(ev => {
        const levelBadge = ev.level === 'error'
          ? '<span class="text-rose-400 font-semibold">[ERR]</span>'
          : ev.level === 'warning'
            ? '<span class="text-amber-400 font-semibold">[WRN]</span>'
            : '<span class="text-emerald-400 font-semibold">[INF]</span>';
        const msg = (ev.message || '').replace(/</g, '&lt;').replace(/>/g, '&gt;');
        return `<div class="flex items-start gap-2 py-0.5 leading-relaxed hover:bg-slate-800/40 rounded px-1 transition-colors">
          <span class="text-slate-500 shrink-0 select-none">${ev.time || ''}</span>
          ${levelBadge}
          <span class="text-slate-300 dark:text-slate-300 break-all">${msg}</span>
        </div>`;
      }).join('');
    }
  }
}

async function loadDashboard() {
  // Đặt TRƯỚC mọi `await` (xem giải thích ở `_ensureExtendedMonitorTimer`).
  // `loadDashboard()` được gọi từ 6 chỗ và 2 chỗ chạy song song lúc mở trang;
  // nếu chốt `if (!healthDashboardTimer)` sau `await` thì cả hai cùng thấy
  // `null` và tạo 2 vòng 2 giây → tải `/api/v1/health-dashboard` nhân đôi.
  if (!healthDashboardTimer) {
    healthDashboardTimer = setInterval(() => {
      const tabDash = document.getElementById('tab-dashboard');
      if (tabDash && tabDash.classList.contains('active')) {
        fetchAndRenderHealthDashboard();
        checkPendingAction(); // Phase 25: Poll every 2s for pending actions
      }
    }, 2000);
  }

  await fetchAndRenderHealthDashboard();
  loadAudioNodes();
  checkPendingAction(); // Phase 25: Check for pending security approval

  // Phase 61: nạp bộ monitor mở rộng (dữ liệu nặng, nhịp 15s)
  loadExtendedMonitors();
}

// ═══════════════════════════════════════════════════════════════════════════
// ── PHASE 61: BỘ MONITOR MỞ RỘNG (6 PANEL) ─────────────────────────────────
//
// Kiến trúc nhịp cập nhật — cố ý TÁCH 2 tầng để không tự dõi DoS server:
//   • Tầng NHANH (2s)  : chỉ đọc `counters` + `nodes` đã nhúng sẵn trong
//     payload O(1) của /api/v1/health-dashboard. Không phát thêm request nào.
//   • Tầng NẶNG (15s) : gọi 8 endpoint tổng hợp. Worker backend chạy 3s/10s/30s
//     nên poll nhanh hơn 15s cũng không thu được thêm dữ liệu nào.
// ═══════════════════════════════════════════════════════════════════════════

// Phase 79: xoá `MON_LOG_MAX_ROWS`, `monLogFilter`, `monLogCache`.
// Chúng chỉ phục vụ `#mon-log-list` — bảng nhật ký ở Bảng Điều Khiển nạp CÙNG
// endpoint /api/v1/logs/recent với "Nhật Ký Vận Hành" ở Trung Tâm Chỉ Huy.
// Cả hai đã gom vào tab Nhật Ký; bản giữ lại là bản của Trung Tâm Chỉ Huy
// (có bộ lọc ẩn dòng heartbeat), xem `loadOpsLog` / `renderRecentLog`.
let monExtendedTimer = null;

/** Ghi text vào #id nếu phần tử tồn tại. Không tạo DOM rác. */
function _monSet(id, text) {
  const el = document.getElementById(id);
  if (el) el.textContent = text;
}

/** Gán chiều rộng % cho #id nếu phần tử tồn tại. */
function _monWidth(id, pct) {
  const el = document.getElementById(id);
  if (el) el.style.width = `${Math.max(0, Math.min(100, pct))}%`;
}

/**
 * Tầng NHANH (2s): render counter rẻ đi kèm payload health-dashboard.
 * Không phát thêm HTTP request — chỉ đọc field đã có sẵn.
 */
function renderMonitorFastPath(data) {
  const c = (data && data.counters) || {};

  // ── Panel 6: hàng đợi phê duyệt + slot worker nền ──
  _monSet('mon-queue-zt', c.zt_pending ?? 0);

  const bgRun = c.bg_running ?? 0;
  const bgMax = c.bg_max_concurrent ?? 0;
  _monSet('mon-queue-bg-run', bgRun);
  _monSet('mon-queue-bg-max', bgMax);
  _monSet('mon-queue-bg-total', c.bg_total ?? 0);
  // Tránh chia cho 0: khi bgMax = 0 thì hiện 0% thay vì NaN.
  _monWidth('mon-queue-bg-bar', bgMax > 0 ? (bgRun / bgMax) * 100 : 0);

  // ── Panel 9: số kết nối mở rộng + số kỹ năng runtime ──
  const n = (data && data.nodes) || null;
  if (n) {
    _monSet('mon-mem-hud', n.active_hud_websockets ?? 0);
    _monSet('mon-mem-lan', n.active_lan_clients ?? 0);
    _monSet('mon-mem-skills', n.skills_count ?? 0);
  }
}

/**
 * Dựng vòng poll 15s. TÁCH RIÊNG và gọi ở phần ĐỒNG BỘ của
 * `loadExtendedMonitors()` — tức là TRƯỚC mọi `await`.
 *
 * BUG ĐÃ GẶP: bản đầu đặt `if (!monExtendedTimer)` ở CUỐI hàm, sau
 * `await Promise.allSettled(...)`. Trang gọi `loadDashboard()` từ 6 chỗ, và
 * lúc mở trang có 2 chỗ chạy SONG SONG (`restoreActiveTab()`→`switchTab()` và
 * listener `DOMContentLoaded`). Cả hai cùng chạy tới dòng `if` trước khi nào
 * kịp gán `monExtendedTimer` → cùng thấy `null` → tạo 2 interval lệch pha.
 * Đo thật cho thấy request bắn thành cặp cách nhau 0.2s, rồi hở 14.8s.
 * Tệ hơn: mỗi lần vào lại tab lại nhân thêm interval, tải server nhân lên dần.
 * Sửa bằng cách chốt timer ở phần đồng bộ — không lời gọi song song nào kịp
 * nhìn thấy `null`.
 */
function _ensureExtendedMonitorTimer() {
  if (monExtendedTimer) return;
  monExtendedTimer = setInterval(() => {
    const tabDash = document.getElementById('tab-dashboard');
    if (tabDash && tabDash.classList.contains('active')) loadExtendedMonitors();
  }, 15000);
}

/**
 * Chặn poll lặp trong cùng một cửa sổ thời gian.
 *
 * VÌ SAO CẦN: `loadExtendedMonitors()` được gọi từ NHIỀU nơi —
 *   1. interval 15s riêng của Phase 61 (`_ensureExtendedMonitorTimer`),
 *   2. `loadDashboard()` — mà `loadDashboard()` được gọi từ 6 chỗ, gồm một
 *      `setInterval(..., 15_000)` có sẵn từ trước ở phần bootstrap (dòng ~7705)
 *      chạy mỗi 15s và gọi `loadDashboard()` bất kể tab nào đang mở.
 * Hai timer cùng kỳ 15s và khởi động gần nhau → trùng pha → mỗi 15 giây bắn
 * 16 request thay vì 8. Đo thật: khoảng cách giữa các vòng là 0s rồi 15s.
 *
 * Dấu thời gian được đặt ở phần ĐỒNG BỘ (trước mọi `await`) để các lời gọi
 * song song không cùng lọt qua. Ngưỡng 13s < kỳ 15s: vẫn cho phép làm mới
 * đúng 15s, nhưng hai timer trùng pha chỉ được chiếm 1 lần.
 */
const MON_HEAVY_DEDUPE_MS = 13000;
let monLastHeavyRunAt = 0;

/**
 * Tầng NẶNG (15s): 8 endpoint. Dùng Promise.allSettled để MỘT endpoint
 * chết không được làm mất dữ liệu của các endpoint còn lại.
 */
async function loadExtendedMonitors() {
  _ensureExtendedMonitorTimer(); // đồng bộ, xem giải thích ở trên

  const now = Date.now();
  if (now - monLastHeavyRunAt < MON_HEAVY_DEDUPE_MS) return; // vừa poll xong
  monLastHeavyRunAt = now; // đặt TRƯỚC await để chống race

  const paths = [
    ['tasks', '/api/v1/enterprise/background-tasks'],
    ['system', '/api/v1/system/stats'],
    ['tools', '/api/v1/enterprise/plugin-registry/stats'],
    ['memory', '/api/v1/memory/stats'],
    ['domain', '/api/v1/domain/stats'],
    ['conns', '/api/v1/enterprise/connectors/health'],
    ['audit', '/api/v1/security/audit-logs?limit=100'],
    // Phase 79: bỏ `['logs', '/api/v1/logs/recent?limit=200']`. Bảng nhật ký
    // ở Bảng Điều Khiển đã gom vào tab Nhật Ký, nên vẫn nạp endpoint này ở
    // đây nghĩa là mỗi vòng poll mất một request mà không ai đọc kết quả.
  ];

  const results = await Promise.allSettled(
    paths.map(([, p]) => apiFetch(`${API_BASE}${p}`).then((r) => (r.ok ? r.json() : null)))
  );

  const get = (key) => {
    const i = paths.findIndex(([k]) => k === key);
    const r = results[i];
    return r && r.status === 'fulfilled' ? r.value : null;
  };

  renderQueueMonitor(get('tasks'), get('system'));
  renderToolHealth(get('tools'));
  renderMemoryMonitor(get('memory'), get('domain'), get('system'));
  renderConnectorsStrip(get('conns'));
  renderSecurityMonitor(get('audit'));
}

/** Panel 6 — phân bố trạng thái tác vụ ERP. */
function renderQueueMonitor(bg, sys) {
  if (sys && sys.tasks) {
    const t = sys.tasks;
    const total = t.total || 0;
    const pct = (n) => (total > 0 ? (n / total) * 100 : 0);
    _monWidth('mon-queue-bar-completed', pct(t.completed || 0));
    _monWidth('mon-queue-bar-pending', pct(t.pending || 0));
    _monWidth('mon-queue-bar-issues', pct(t.issues || 0));
    _monSet('mon-queue-task-completed', t.completed ?? 0);
    _monSet('mon-queue-task-pending', t.pending ?? 0);
    _monSet('mon-queue-task-issues', t.issues ?? 0);
    _monSet('mon-queue-task-sum', `${total} tác vụ`);
  }
  if (bg && bg.total != null) {
    _monSet('mon-queue-bg-total', bg.total);
  }
}

/** Panel 7 — sức khỏe công cụ + độ trễ + circuit breaker. */
function renderToolHealth(d) {
  if (!d) return;

  _monSet('mon-tool-total', d.total_tools ?? 0);
  _monSet('mon-tool-enabled', d.enabled_tools ?? 0);

  const es = d.execution_stats || {};
  let success = 0, failed = 0, timeout = 0, openCircuits = 0;

  for (const stats of Object.values(es)) {
    if (!stats || typeof stats !== 'object') continue;
    success += stats.successful_calls || 0;
    failed += stats.failed_calls || 0;
    timeout += stats.timeout_calls || 0;
    const cb = stats.circuit_breaker;
    if (cb && cb.state && cb.state !== 'closed') openCircuits += 1;
  }

  _monSet('mon-tool-success', success);
  _monSet('mon-tool-failed', failed);
  _monSet('mon-tool-timeout', timeout);

  const badge = document.getElementById('mon-tool-cb-open');
  if (badge) {
    badge.textContent = `${openCircuits} mạch mở`;
    badge.classList.toggle('hidden', openCircuits === 0);
  }

  // Độ trễ: sắp xếp giảm dần, chỉ hiện công cụ thực sự đã gọi (total_calls > 0).
  const rows = Object.entries(es)
    .filter(([, s]) => s && s.total_calls > 0)
    .sort((a, b) => (b[1].avg_latency_ms || 0) - (a[1].avg_latency_ms || 0))
    .slice(0, 12);

  const box = document.getElementById('mon-tool-latency');
  if (!box) return;
  if (rows.length === 0) {
    box.innerHTML = '<div class="text-[10px] text-slate-400 dark:text-slate-500 italic py-2">Chưa có công cụ nào được gọi — chưa có số liệu độ trễ.</div>';
    return;
  }
  const max = Math.max(...rows.map(([, s]) => s.avg_latency_ms || 0), 1);
  box.innerHTML = rows.map(([name, s]) => {
    const ms = s.avg_latency_ms || 0;
    const cb = s.circuit_breaker || {};
    const state = cb.state || 'closed';
    const cbColor = state === 'open' ? 'text-rose-400' : state === 'half_open' ? 'text-amber-400' : 'text-emerald-500';
    return `<div class="flex items-center gap-2">
      <span class="w-32 shrink-0 truncate text-slate-300" title="${_esc(name)}">${_esc(name)}</span>
      <span class="flex-1 h-1.5 rounded-full bg-slate-800/60 overflow-hidden">
        <span class="block h-full rounded-full ${ms > 3000 ? 'bg-rose-500' : ms > 1000 ? 'bg-amber-500' : 'bg-teal-500'}" style="width:${Math.min(100, (ms / max) * 100)}%"></span>
      </span>
      <span class="w-16 shrink-0 text-right text-slate-400 font-mono">${ms.toFixed(1)}ms</span>
      <span class="w-14 shrink-0 text-right font-mono ${cbColor}">${_esc(state)}</span>
    </div>`;
  }).join('');
}

/** Panel 9 — trí nhớ vector, nhân sự AD, số tài khoản. */
function renderMemoryMonitor(mem, dom, sys) {
  if (mem && mem.status === 'ready') {
    _monSet('mon-mem-records', mem.total_records ?? 0);
    _monSet('mon-mem-size', `${mem.size_mb ?? 0} MB`);
    _monSet('mon-mem-collection', mem.collection_name || '—');
  }
  if (dom) {
    _monSet('mon-mem-employees', dom.employees_count ?? 0);
    _monSet('mon-mem-computers', dom.computers_count ?? 0);
  }
  if (sys) {
    _monSet('mon-mem-users', `${sys.users_count ?? 0} tài khoản`);
  }
}

/** Panel 10 — dải nhỏ 4 connector, bấm để sang tab chi tiết. */
function renderConnectorsStrip(d) {
  if (!d) return;
  const all = d.connectors || {};
  const names = Object.keys(all);
  const box = document.getElementById('mon-conn-list');
  if (!box) return;

  _monSet('mon-conn-total', names.length);

  let ready = 0;
  const rows = names.map((name) => {
    const c = all[name] || {};
    const isReady = c.configured === true;
    if (isReady) ready += 1;
    const missing = Array.isArray(c.missing_fields) && c.missing_fields.length
      ? `Thiếu: ${c.missing_fields.join(', ')}`
      : (c.note || c.error || '');
    const hasErr = !!c.error;
    const dot = isReady ? 'bg-emerald-500' : (hasErr ? 'bg-rose-500' : 'bg-amber-500');
    const label = isReady ? 'Sẵn sàng' : (hasErr ? 'Lỗi' : 'Chưa cấu hình');
    const labelCls = isReady ? 'text-emerald-500' : (hasErr ? 'text-rose-400' : 'text-amber-400');
    const hint = isReady ? '' : (missing || label);
    return `<div class="flex items-center justify-between gap-2 p-2.5 rounded-xl border border-slate-100 dark:border-slate-700/60 bg-slate-50/50 dark:bg-slate-800/30">
      <div class="flex items-center gap-2 min-w-0">
        <span class="w-1.5 h-1.5 rounded-full ${dot} shrink-0"></span>
        <span class="text-[11px] font-bold text-slate-800 dark:text-white uppercase">${_esc(name)}</span>
      </div>
      <div class="flex items-center gap-2 shrink-0">
        <span class="text-[9px] text-slate-400 dark:text-slate-500 truncate max-w-[130px]" title="${_esc(hint)}">${_esc(hint)}</span>
        <span class="text-[10px] font-bold ${labelCls}">${label}</span>
      </div>
    </div>`;
  });

  _monSet('mon-conn-ready', ready);
  box.innerHTML = rows.length
    ? rows.join('')
    : '<div class="text-[10px] text-slate-400 dark:text-slate-500 italic py-3">Không có connector nào được đăng ký.</div>';
}

/** Panel 8 — tổng hợp audit log theo mức độ. */
function renderSecurityMonitor(d) {
  if (!d) return;
  const logs = Array.isArray(d.logs) ? d.logs : [];
  const total = d.total ?? logs.length;

  let success = 0, failed = 0, pending = 0;
  for (const r of logs) {
    const st = String(r?.outcome || '').toLowerCase();
    if (st === 'success') success += 1;
    else if (st === 'failed' || st === 'error' || st === 'denied' || st === 'blocked') failed += 1;
    else pending += 1;
  }

  _monSet('mon-sec-total', total);
  _monSet('mon-sec-success', success);
  _monSet('mon-sec-failed', failed);
  _monSet('mon-sec-pending', pending);

  const denom = success + failed + pending;
  _monWidth('mon-sec-bar-success', denom ? (success / denom) * 100 : 0);
  _monWidth('mon-sec-bar-pending', denom ? (pending / denom) * 100 : 0);
  _monWidth('mon-sec-bar-failed', denom ? (failed / denom) * 100 : 0);
  _monSet('mon-sec-rate', denom ? `${((success / denom) * 100).toFixed(1)}%` : '—');

  const posture = document.getElementById('mon-sec-posture');
  if (posture) {
    if (denom === 0) {
      posture.textContent = 'Chưa có dữ liệu';
      posture.className = 'px-2 py-0.5 rounded-full text-[10px] font-bold bg-slate-500/10 border border-slate-500/30 text-slate-500 dark:text-slate-400';
    } else if (failed > 0) {
      posture.textContent = 'Có thất bại';
      posture.className = 'px-2 py-0.5 rounded-full text-[10px] font-bold bg-rose-500/10 border border-rose-500/30 text-rose-500 dark:text-rose-400';
    } else if (pending > 0) {
      posture.textContent = 'Có việc chờ';
      posture.className = 'px-2 py-0.5 rounded-full text-[10px] font-bold bg-amber-500/10 border border-amber-500/30 text-amber-500 dark:text-amber-400';
    } else {
      posture.textContent = 'Toàn vẹn';
      posture.className = 'px-2 py-0.5 rounded-full text-[10px] font-bold bg-emerald-500/10 border border-emerald-500/30 text-emerald-500 dark:text-emerald-400';
    }
  }
}

// Phase 79: đã gỡ `renderSystemLogs`, `paintMonLogs`, `setMonLogFilter` và
// các bộ đệm `monLogCache` / `monLogFilter`.
// Chúng chỉ vẽ `#mon-log-list` — bảng nhật ký ở Bảng Điều Khiển, nạp cùng
// endpoint /api/v1/logs/recent với "Nhật Ký Vận Hành" ở Trung Tâm Chỉ Huy.
// Hai bảng cùng dữ liệu ở hai màn hình; nay cả hai gom vào tab Nhật Ký và
// chỉ giữ một bản, có bộ lọc ẩn dòng heartbeat (xem `renderRecentLog`).
// Giữ lại hàm chết cũng vô nghĩa: element đã xoá nên chúng chỉ chạy tới
// dòng `if (!box) return;` rồi dừng.
// ═══════════════════════════════════════════════════════════════════════════
// ── PHASE 25: STATE MANAGEMENT & PENDING ACTION APPROVAL ────────────────────
// ═══════════════════════════════════════════════════════════════════════════

let latestPendingActions = [];

/**
 * Phase 25: Poll server for any pending security actions waiting for approval.
 * Updates:
 *   1. Dashboard banner (#pending-action-banner)
 *   2. Security Tab dedicated queue card (#security-pending-list)
 *   3. Sidebar notification badge (#badge-security-alert)
 *   4. Stat counters
 */
async function checkPendingAction() {
  try {
    const token = getAuthToken();
    if (!token) return;
    const res = await fetch(`${API_BASE}/api/v1/security/pending-action`, {
      headers: { 'Authorization': `Bearer ${token}` },
    });
    if (!res.ok) return;
    const data = await res.json();

    const pendingList = data.pending_list || (data.action ? [data.action] : []);
    latestPendingActions = pendingList;
    const count = pendingList.length;

    // 1. Update Dashboard Banner
    const banner = document.getElementById('pending-action-banner');
    const descEl = document.getElementById('pending-action-desc');
    if (banner) {
      if (count > 0) {
        const act = pendingList[0];
        const toolName = act.tool_name || 'Không rõ';
        const targetClient = act.target_client || 'master';
        const query = act.query ? ` ("${act.query.substring(0, 60)}...")` : '';
        if (descEl) {
          descEl.textContent = `Tác vụ "${toolName}" trên [${targetClient}]${query} đang chờ phê duyệt (${count} tác vụ đang chờ).`;
        }
        banner.classList.remove('hidden');
      } else {
        banner.classList.add('hidden');
      }
    }

    // 2. Update Sidebar Badge
    const sideBadge = document.getElementById('badge-security-alert');
    if (sideBadge) {
      if (count > 0) {
        sideBadge.className = 'ml-auto px-2 py-0.5 rounded-full text-[10px] font-bold bg-amber-500 text-white shadow-[0_0_10px_rgba(245,158,11,0.5)] animate-pulse';
        sideBadge.textContent = `${count} CẦN DUYỆT`;
      } else {
        sideBadge.className = 'ml-auto px-1.5 py-0.2 rounded-full text-[10px] font-bold bg-emerald-100 dark:bg-emerald-900/30 text-emerald-700 dark:text-emerald-300 border border-emerald-200 dark:border-emerald-700';
        sideBadge.textContent = 'Zero-Trust';
      }
    }

    // 3. Update Stat Counter on Security Tab
    const pendingStat = document.getElementById('stat-sec-pending');
    if (pendingStat && count > 0) {
      pendingStat.textContent = count;
    }
    const queueBadge = document.getElementById('badge-pending-queue-count');
    if (queueBadge) {
      queueBadge.textContent = count > 0 ? `${count} Đang Chờ` : '0 Đang Chờ';
      if (count > 0) {
        queueBadge.className = 'text-[10px] px-2 py-0.5 rounded font-bold bg-amber-500 text-white shadow-sm animate-pulse';
      } else {
        queueBadge.className = 'text-[10px] px-2 py-0.5 rounded font-bold bg-amber-500/20 text-amber-600 dark:text-amber-400 border border-amber-500/30';
      }
    }

    // 4. Render dedicated Security Queue
    renderPendingApprovalsQueue(pendingList);
  } catch (_) { }
}

/**
 * Render all pending items in the dedicated Security Center queue card.
 */
function renderPendingApprovalsQueue(items) {
  const container = document.getElementById('security-pending-list');
  if (!container) return;

  if (!items || items.length === 0) {
    container.innerHTML = `
      <div class="py-6 text-center text-slate-500 dark:text-slate-400 text-xs border border-dashed border-emerald-500/30 rounded-xl bg-emerald-500/5">
        <div class="text-emerald-500 text-xl mb-1">🛡️</div>
        <div class="font-bold text-slate-700 dark:text-emerald-300 text-sm">Hệ thống an toàn: Không có tác vụ rủi ro nào đang chờ phê duyệt</div>
        <div class="text-[11px] text-slate-500 dark:text-slate-400 mt-0.5">Khi trợ lý AI hoặc máy trạm gặp lệnh thuộc danh mục nguy hiểm (NEED_CONFIRM), lệnh sẽ xuất hiện tại đây để bạn duyệt trước khi chạy.</div>
      </div>
    `;
    return;
  }

  container.innerHTML = '';
  items.forEach(act => {
    const card = document.createElement('div');
    card.className = 'p-4 rounded-xl bg-amber-500/10 dark:bg-amber-950/20 border border-amber-500/40 space-y-3 transition-all hover:border-amber-400';

    const toolName = escapeHtml(act.tool_name || 'Không rõ');
    const targetClient = escapeHtml(act.target_client || 'master');
    const query = act.query ? escapeHtml(act.query) : '';
    const argsJson = escapeHtml(JSON.stringify(act.arguments || {}, null, 2));
    const actId = escapeHtml(act.id || '');
    const timeStr = act.timestamp ? new Date(act.timestamp * 1000).toLocaleTimeString() : '--:--';

    card.innerHTML = `
      <div class="flex flex-col sm:flex-row sm:items-center justify-between gap-2 border-b border-amber-500/20 pb-2.5">
        <div class="flex items-center gap-2 flex-wrap">
          <span class="px-2 py-0.5 rounded text-[10px] font-bold bg-amber-500 text-white uppercase tracking-wider">CẦN PHÊ DUYỆT</span>
          <span class="font-mono font-bold text-slate-900 dark:text-amber-300 text-sm">${toolName}</span>
          <span class="px-2 py-0.5 rounded text-[11px] font-semibold bg-cyan-500/20 text-cyan-700 dark:text-cyan-300 border border-cyan-500/30">Thiết bị: ${targetClient}</span>
        </div>
        <div class="text-[11px] font-mono text-slate-500 dark:text-slate-400">Thời điểm: ${timeStr}</div>
      </div>

      ${query ? `<div class="text-xs text-slate-700 dark:text-slate-300 font-medium"><span class="text-slate-500">Yêu cầu gốc:</span> "${query}"</div>` : ''}

      <div>
        <div class="text-[11px] text-slate-500 dark:text-slate-400 mb-1 font-semibold uppercase tracking-wider">Chi tiết tham số thực thi:</div>
        <pre class="p-2.5 rounded-lg bg-black/60 text-[11px] font-mono text-amber-200/90 whitespace-pre-wrap max-h-36 overflow-y-auto border border-white/10">${argsJson}</pre>
      </div>

      <div class="flex items-center justify-end gap-2.5 pt-1">
        <button onclick="rejectPendingAction('${actId}', '${toolName}', '${targetClient}')" class="px-4 py-2 rounded-xl bg-slate-200 dark:bg-white/10 hover:bg-rose-500/20 text-slate-700 dark:text-slate-300 hover:text-rose-400 text-xs font-semibold transition-all cursor-pointer">
          ❌ Từ Chối & Hủy Bỏ
        </button>
        <button onclick="approvePendingAction('${actId}', '${toolName}', '${targetClient}')" class="px-4 py-2 rounded-xl bg-amber-600 hover:bg-amber-500 text-white text-xs font-bold shadow-[0_0_15px_rgba(245,158,11,0.4)] transition-all flex items-center gap-1.5 cursor-pointer">
          <svg width="14" height="14" fill="none" stroke="currentColor" stroke-width="2.5" viewBox="0 0 24 24"><polyline points="20 6 9 17 4 12"/></svg>
          ✅ Phê Duyệt & Thực Thi Tiếp
        </button>
      </div>
    `;

    container.appendChild(card);
  });
}

/**
 * Phase 25: Approve a pending action by actionId or default.
 */
async function approvePendingAction(actionId, skillName, clientId) {
  try {
    const token = getAuthToken();
    if (!token) return;
    const body = { approved: true };
    if (actionId) {
      body.action_id = actionId;
    } else if (latestPendingActions && latestPendingActions.length > 0) {
      body.action_id = latestPendingActions[0].id || latestPendingActions[0].action_id;
      skillName = skillName || latestPendingActions[0].tool_name;
      clientId = clientId || latestPendingActions[0].target_client;
    }
    if (skillName) body.skill_name = skillName;
    if (clientId) body.client_id = clientId;

    const res = await fetch(`${API_BASE}/api/v1/security/confirm-action`, {
      method: 'POST',
      headers: { 'Authorization': `Bearer ${token}`, 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    });
    const data = await res.json();
    if (res.ok && data.status === 'success') {
      const replyMsg = data.reply || `Đã phê duyệt và thực thi thành công tác vụ "${skillName || 'yêu cầu'}".`;
      showToast(`✅ ${replyMsg.slice(0, 80)}`, 'success');

      // Update in-memory chat history so AI remembers in subsequent turns
      _webChatHistory.push({ role: 'assistant', content: replyMsg });

      // Update Voice Tab UI response card if present
      const card = document.getElementById('voice-response-card');
      const textEl = document.getElementById('voice-response-text');
      if (card && textEl) {
        card.classList.remove('hidden');
        textEl.textContent = replyMsg;
      }

      document.getElementById('pending-action-banner')?.classList.add('hidden');
      await checkPendingAction();
      await loadSecurityCenter();
    } else {
      const msg = data.detail || data.message || 'Đã xảy ra lỗi';
      showToast(`❌ Lỗi phê duyệt: ${msg}`, 'error');
    }
  } catch (err) {
    showToast(`❌ Lỗi kết nối: ${err.message}`, 'error');
  }
}

/**
 * Phase 25: Reject (cancel) a pending action.
 */
async function rejectPendingAction(actionId, skillName, clientId) {
  try {
    const token = getAuthToken();
    if (!token) return;
    const body = { approved: false };
    if (actionId) {
      body.action_id = actionId;
    } else if (latestPendingActions && latestPendingActions.length > 0) {
      body.action_id = latestPendingActions[0].id || latestPendingActions[0].action_id;
      skillName = skillName || latestPendingActions[0].tool_name;
      clientId = clientId || latestPendingActions[0].target_client;
    }
    if (skillName) body.skill_name = skillName;
    if (clientId) body.client_id = clientId;

    const res = await fetch(`${API_BASE}/api/v1/security/confirm-action`, {
      method: 'POST',
      headers: { 'Authorization': `Bearer ${token}`, 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    });
    const data = await res.json();
    if (res.ok) {
      showToast(`🚫 Đã hủy bỏ tác vụ "${skillName || 'yêu cầu'}".`, 'warning');
      document.getElementById('pending-action-banner')?.classList.add('hidden');
      await checkPendingAction();
      await loadSecurityCenter();
    } else {
      const msg = data.detail || data.message || 'Đã xảy ra lỗi';
      showToast(`❌ Lỗi hủy tác vụ: ${msg}`, 'error');
    }
  } catch (err) {
    showToast(`❌ Lỗi kết nối: ${err.message}`, 'error');
  }
}


// ═══════════════════════════════════════════════════════════════════════════
// ── BẬT/TẮT CÔNG TẮC & LƯU CẤU HÌNH NHANH ─────────────────────────────────
// ═══════════════════════════════════════════════════════════════════════════

function toggleSwitch(el, flag) {
  el.classList.toggle('on');
  if (flag === 'autoExec') {
    autoExecState = el.classList.contains('on');
    const cfgSwitch = document.getElementById('cfg-switch-autoexec');
    if (cfgSwitch) {
      if (autoExecState) cfgSwitch.classList.add('on');
      else cfgSwitch.classList.remove('on');
    }
  }
}

function toggleAutoExecConfig(el) {
  el.classList.toggle('on');
  autoExecState = el.classList.contains('on');
  const dashSwitch = document.getElementById('switch-auto-exec');
  if (dashSwitch) {
    if (autoExecState) dashSwitch.classList.add('on');
    else dashSwitch.classList.remove('on');
  }
}

async function saveQuickConfig() {
  const btn = document.getElementById('btn-quick-save');
  const defaultHtml = `<svg width="16" height="16" fill="none" stroke="currentColor" stroke-width="2.5" viewBox="0 0 24 24"><polyline points="20 6 9 17 4 12"/></svg> Lưu Cấu Hình`;
  btn.disabled = true;
  btn.innerHTML = `<svg class="animate-spin" width="16" height="16" fill="none" stroke="currentColor" stroke-width="2" viewBox="0 0 24 24"><path d="M21 12a9 9 0 1 1-6.219-8.56"/></svg> Đang lưu cấu hình...`;

  const modelVal = document.getElementById('quick-model').value;
  const asrVal = document.getElementById('quick-asr').value;

  // Phase 73: KHÔNG tự chế model dự phòng. Trước đây khi cấu hình chưa có
  // `routing`, hệ thống ghi sẵn gemini-1.5-flash + llama3-8b-8192 (không kèm
  // khoá) rồi báo "đã lưu thành công" — nhưng auto-fallback sẽ luôn trượt.
  // Nay chỉ dựng khung primary; phần dự phòng để trống tới khi người dùng
  // tự chọn từ danh sách model thật mà server trả về.
  const existingRouting = currentConfig.routing
    ? { ...currentConfig.routing }
    : { primary: {} };
  if (!existingRouting.primary) existingRouting.primary = {};
  existingRouting.primary.provider_model = modelVal;
  existingRouting.primary.api_key = existingRouting.primary.api_key || currentConfig.API_KEY || '';
  existingRouting.primary.api_base = existingRouting.primary.api_base || currentConfig.BASE_URL || '';

  const updatedConfig = {
    ...currentConfig,
    routing: existingRouting,
    MODEL_NAME: modelVal,
    ASR_BACKEND: asrVal,
    auto_execute: autoExecState,
    AUTO_EXECUTE_UNVERIFIED_CODE: autoExecState,
  };

  const res = await apiSaveConfig(updatedConfig);
  btn.disabled = false;
  btn.innerHTML = defaultHtml;

  if (res.success) {
    currentConfig = updatedConfig;
    showToast('✅ Đã lưu cấu hình hệ thống thành công!', 'success');
  } else {
    showToast(`❌ Lỗi khi lưu cấu hình: ${res.message}`, 'error');
  }
}

// ═══════════════════════════════════════════════════════════════════════════
// ── NẠP, LỌC VÀ BẬT/TẮT KHO KỸ NĂNG (SKILLS CATALOG & INTERACTIVE RUNNER) ──
// ═══════════════════════════════════════════════════════════════════════════

let _currentSkillCategory = 'all';
let _currentSkillSearch = '';
let _activeSkillForRunner = null;
let _lastSkillExecutionResult = null;

const CATEGORY_MODULE_MAP = {
  all: null,
  computer_use_skills: ['computer_use_skills'],
  pc_control_skills: ['pc_control_skills', 'file_system', 'computer_use_skills'],
  domain_alert_skills: ['domain_alert_skills'],
  monitoring_skills: ['monitoring_skills', 'lean_hr_skills'],
  sysadmin_skills: ['sysadmin_skills'],
  excel_records_skill: ['excel_records_skill'],
  visual_skills: ['visual_skills', 'ai_delegation', 'ninerouter_skills'],
  custom_skills: ['custom_skills'],
};

async function loadSkills() {
  const grid = document.getElementById('full-skills-grid');
  if (!grid) return;

  grid.innerHTML = `<div class="col-span-full py-8 text-center text-xs text-slate-400 flex items-center justify-center gap-2">
    <svg class="animate-spin text-cyan-400" width="16" height="16" fill="none" stroke="currentColor" stroke-width="2" viewBox="0 0 24 24"><path d="M21 12a9 9 0 1 1-6.219-8.56"/></svg>
    <span>Đang nạp kho kỹ năng từ runtime...</span>
  </div>`;

  const skills = await apiGetSkills();

  if (!skills || !Object.keys(skills).length) {
    grid.innerHTML = `<div class="col-span-full py-8 text-center text-xs text-slate-400">Không tìm thấy kỹ năng nào đã đăng ký.</div>`;
    return;
  }

  skillsData = skills;
  renderSkillsGridList(skills);
  updateSkillsTelemetry();
  applySkillsFilter();
}

function updateSkillsTelemetry() {
  const totalEl = document.getElementById('skills-stat-total');
  const activeEl = document.getElementById('skills-stat-active');
  const modulesEl = document.getElementById('skills-stat-modules');
  const catAllCount = document.getElementById('cat-count-all');

  // Phase 73: chưa nạp được danh sách kỹ năng thì nói thẳng "chờ kết nối"
  // thay vì để ô trống — để trống dễ bị hiểu là hệ thống đang tính.
  if (!skillsData) {
    [[totalEl, ''], [activeEl, ''], [modulesEl, ''], [catAllCount, '']].forEach(([el]) => {
      if (el) _setLiveText(el, null);
    });
    return;
  }

  const entries = Object.entries(skillsData).filter(([k]) => !k.startsWith('_'));
  const total = entries.length;
  const active = entries.filter(([, v]) => v.enabled !== false).length;
  const moduleSet = new Set(entries.map(([, v]) => v.module ? v.module.split('.').pop() : 'skill'));

  if (totalEl) _setLiveText(totalEl, `${total} SKILLS`);
  if (activeEl) _setLiveText(activeEl, `${active} HOẠT ĐỘNG`);
  if (modulesEl) _setLiveText(modulesEl, `${moduleSet.size} MODULES`);
  if (catAllCount) _setLiveText(catAllCount, total);
}

function renderSkillsGridList(skills) {
  const grid = document.getElementById('full-skills-grid');
  if (!grid) return;

  const entries = Object.entries(skills).filter(([k]) => !k.startsWith('_'));
  grid.innerHTML = entries.map(([name, data], idx) => {
    const isCyan = idx % 2 === 0;
    const borderClass = isCyan ? 'neon-card-cyan' : 'neon-card-purple';
    const accentColor = isCyan ? '#22d3ee' : '#a855f7';
    const meta = data.meta || {};
    // Phase 73: không bịa mô tả. Trước đây mọi kỹ năng thiếu `meta.description`
    // đều hiện cùng một câu "Kỹ năng tự động hóa Windows native." trông như
    // dữ liệu từ server. Nay nói thẳng là chưa có mô tả.
    const desc = meta.description || 'Kỹ năng chưa có mô tả.';
    const icon = resolveSkillIcon(name);
    const isEnabled = data.enabled !== false;
    const opacityStyle = isEnabled ? '' : 'opacity: 0.6; filter: grayscale(35%);';
    const rawModule = data.module ? data.module.split('.').pop() : 'skill';

    return `
      <div class="${borderClass} p-4 flex flex-col justify-between min-h-[175px] skill-item transition-all duration-200"
           style="${opacityStyle}"
           data-name="${name.toLowerCase()}"
           data-desc="${desc.toLowerCase()}"
           data-module="${rawModule}"
           data-module-full="${data.module || ''}">
        <div>
          <div class="flex items-center justify-between mb-2">
            <div class="flex items-center gap-2">
              <div class="w-8 h-8 rounded-lg flex items-center justify-center" style="background: ${accentColor}20; color: ${accentColor}">
                ${icon}
              </div>
              <span class="text-[10px] font-semibold uppercase tracking-wider px-2 py-0.5 rounded font-mono" style="background:${accentColor}15; color:${accentColor}">
                ${rawModule}
              </span>
            </div>

            <!-- Nút bật / tắt từng kỹ năng -->
            <div class="flex items-center gap-1.5" title="${isEnabled ? 'Kỹ năng đang hoạt động' : 'Kỹ năng đã tắt'}">
              <span class="text-[10px] font-medium ${isEnabled ? 'text-cyan-400' : 'text-slate-500'}">${isEnabled ? 'Bật' : 'Tắt'}</span>
              <div class="switch-track ${isEnabled ? 'on' : ''}" style="cursor:pointer;" onclick="handleToggleSkill(event, '${name}', ${!isEnabled})">
                <div class="switch-knob"></div>
              </div>
            </div>
          </div>
          <div class="text-sm font-bold text-slate-800 dark:text-white truncate font-mono" title="${name}">${name}</div>
          <p class="text-xs text-slate-500 dark:text-slate-400 mt-1 line-clamp-2 leading-relaxed">${desc}</p>
        </div>
        <div class="pt-3 mt-2 border-t border-slate-200 dark:border-white/5 flex items-center justify-between text-[10px] text-slate-500 dark:text-slate-500">
          <span class="font-mono truncate max-w-[140px]" title="Hàm: ${data.attr || name}">Hàm: ${data.attr || name}</span>
          <button type="button" onclick="quickRunSkill('${name}')" class="text-cyan-500 dark:text-cyan-400 hover:text-cyan-600 dark:hover:text-cyan-300 font-bold flex items-center gap-1 transition">
            <span>Chạy thử</span>
            <span class="text-xs">→</span>
          </button>
        </div>
      </div>
    `;
  }).join('');
}

async function handleToggleSkill(event, name, targetState) {
  if (event) event.stopPropagation();
  const res = await apiToggleSkill(name, targetState);
  if (res.success) {
    if (skillsData[name]) skillsData[name].enabled = res.enabled;
    renderSkillsGridList(skillsData);
    updateSkillsTelemetry();
    applySkillsFilter();
    showToast(
      res.enabled
        ? `✅ Đã BẬT kỹ năng [${name}]. Trợ lý AI có thể sử dụng.`
        : `⏸️ Đã TẮT kỹ năng [${name}]. Trợ lý AI sẽ bỏ qua kỹ năng này.`,
      'info'
    );
  } else {
    showToast(`❌ Không thể đổi trạng thái kỹ năng: ${res.message}`, 'error');
  }
}

async function batchToggleSkills(enabled) {
  const actionText = enabled ? 'bật tất cả' : 'tắt tất cả';
  const res = await apiBatchToggleSkills(enabled);
  if (res && res.success) {
    if (skillsData) {
      Object.keys(skillsData).forEach(k => {
        if (!k.startsWith('_')) skillsData[k].enabled = enabled;
      });
    }
    renderSkillsGridList(skillsData);
    updateSkillsTelemetry();
    applySkillsFilter();
    // Phase 73: lấy đúng số server trả về. `res.count` rỗng thì báo không rõ
    // số lượng, không thay bằng 37 (số từng nằm cứng trong HTML).
    const affected = _liveNum(res.count, { suffix: ' kỹ năng' });
    showToast(
      enabled
        ? (res.count != null
          ? `✅ Đã BẬT ${affected} trong runtime!`
          : '✅ Đã BẬT toàn bộ kỹ năng trong runtime (server không trả số lượng).')
        : (res.count != null
          ? `⏸️ Đã TẮT ${affected} trong runtime!`
          : '⏸️ Đã TẮT toàn bộ kỹ năng trong runtime (server không trả số lượng).'),
      'info'
    );
  } else {
    showToast(`❌ Không thể ${actionText} kỹ năng: ${res?.message || 'Lỗi server'}`, 'error');
  }
}

function filterSkillsByCategory(category) {
  _currentSkillCategory = category;

  // Highlight active category tab
  const buttons = document.querySelectorAll('.skill-cat-btn');
  buttons.forEach(btn => {
    btn.classList.remove('bg-white', 'dark:bg-slate-800', 'text-cyan-600', 'dark:text-cyan-300', 'border-cyan-500/30', 'shadow-sm');
    btn.classList.add('text-slate-500', 'hover:text-slate-800', 'dark:text-slate-400', 'dark:hover:text-white', 'border-transparent');
  });

  const activeBtn = document.getElementById(`btn-cat-${category}`);
  if (activeBtn) {
    activeBtn.classList.remove('text-slate-500', 'hover:text-slate-800', 'dark:text-slate-400', 'dark:hover:text-white', 'border-transparent');
    activeBtn.classList.add('bg-white', 'dark:bg-slate-800', 'text-cyan-600', 'dark:text-cyan-300', 'border-cyan-500/30', 'shadow-sm');
  }

  applySkillsFilter();
}

function filterSkillsList(query) {
  _currentSkillSearch = (query || '').toLowerCase().trim();
  applySkillsFilter();
}

function applySkillsFilter() {
  const items = document.querySelectorAll('.skill-item');
  const allowedModules = CATEGORY_MODULE_MAP[_currentSkillCategory];

  items.forEach(el => {
    const itemModule = el.getAttribute('data-module') || '';
    const itemModuleFull = el.getAttribute('data-module-full') || '';
    const name = el.getAttribute('data-name') || '';
    const desc = el.getAttribute('data-desc') || '';

    // Check category match
    let matchesCategory = true;
    if (allowedModules) {
      matchesCategory = allowedModules.some(m => itemModule === m || itemModuleFull.includes(m));
    }

    // Check search match
    let matchesSearch = true;
    if (_currentSkillSearch) {
      matchesSearch = name.includes(_currentSkillSearch) || desc.includes(_currentSkillSearch) || itemModule.includes(_currentSkillSearch);
    }

    el.style.display = (matchesCategory && matchesSearch) ? '' : 'none';
  });
}

function resolveSkillIcon(name) {
  const n = name.toLowerCase();
  if (n.includes('gui') || n.includes('computer') || n.includes('worker') || n.includes('screen') || n.includes('mouse') || n.includes('keyboard')) return SKILL_ICONS.robot || SKILL_ICONS.system;
  if (n.includes('excel') || n.includes('sheet') || n.includes('table')) return SKILL_ICONS.excel;
  if (n.includes('powershell') || n.includes('cmd')) return SKILL_ICONS.powershell;
  if (n.includes('volume') || n.includes('sound') || n.includes('audio') || n.includes('tts') || n.includes('stt') || n.includes('speech')) return SKILL_ICONS.volume;
  if (n.includes('image') || n.includes('draw') || n.includes('picture')) return SKILL_ICONS.image;
  if (n.includes('web') || n.includes('fetch') || n.includes('search') || n.includes('url')) return SKILL_ICONS.globe;
  if (n.includes('chat') || n.includes('router') || n.includes('embedding') || n.includes('llm')) return SKILL_ICONS.ai;
  if (n.includes('service')) return SKILL_ICONS.service;
  if (n.includes('process') || n.includes('kill')) return SKILL_ICONS.process;
  if (n.includes('clipboard')) return SKILL_ICONS.clipboard;
  if (n.includes('file')) return SKILL_ICONS.file;
  if (n.includes('open') || n.includes('app') || n.includes('window')) return SKILL_ICONS.system;
  if (n.includes('system') || n.includes('network')) return SKILL_ICONS.system;
  return SKILL_ICONS.default;
}

// ═══════════════════════════════════════════════════════════════════════════
// ── TRÌNH THỰC THI & KIỂM THỬ KỸ NĂNG (INTERACTIVE SKILL RUNNER) ───────────
// ═══════════════════════════════════════════════════════════════════════════

function quickRunSkill(skillName) {
  if (!skillsData || !skillsData[skillName]) {
    showToast(`⚠️ Không tìm thấy kỹ năng [${skillName}] trong kho dữ liệu.`, 'warning');
    return;
  }

  const skill = skillsData[skillName];
  _activeSkillForRunner = skillName;
  _lastSkillExecutionResult = null;

  const modal = document.getElementById('modal-skill-runner');
  if (!modal) return;

  // Header
  const titleEl = document.getElementById('runner-skill-title');
  const moduleEl = document.getElementById('runner-skill-module');
  const descEl = document.getElementById('runner-skill-desc');
  const iconEl = document.getElementById('runner-skill-icon');

  const rawModule = skill.module ? skill.module.split('.').pop() : 'skill';
  if (titleEl) titleEl.textContent = skillName;
  if (moduleEl) moduleEl.textContent = rawModule;
  if (descEl) descEl.textContent = skill.meta?.description || 'Kỹ năng hệ thống tự động hóa native.';
  if (iconEl) iconEl.innerHTML = resolveSkillIcon(skillName);

  // Render Parameters Form
  renderSkillRunnerParamsForm(skill);

  // Reset Results section
  const resultSec = document.getElementById('runner-result-section');
  if (resultSec) resultSec.classList.add('hidden');

  // Open Modal
  modal.classList.remove('hidden');
  modal.classList.add('flex');
}

function renderSkillRunnerParamsForm(skill) {
  const container = document.getElementById('runner-params-fields');
  const countEl = document.getElementById('runner-params-count');
  if (!container) return;

  const params = skill.meta?.parameters || {};
  const props = params.properties || {};
  const required = params.required || [];
  const propKeys = Object.keys(props);

  if (countEl) countEl.textContent = `${propKeys.length} tham số`;

  if (propKeys.length === 0) {
    container.innerHTML = `
      <div class="py-4 text-center text-slate-400 italic text-xs flex flex-col items-center justify-center gap-1.5">
        <span class="text-cyan-400 font-mono text-sm">✓ Zero-Config Skill</span>
        <span>Kỹ năng này không yêu cầu tham số đầu vào. Bấm <b>"THỰC THI KỸ NĂNG NGAY"</b> để kiểm thử trực tiếp trên hệ thống.</span>
      </div>
    `;
    return;
  }

  container.innerHTML = propKeys.map(key => {
    const spec = props[key] || {};
    const isRequired = required.includes(key);
    const label = `${key} ${isRequired ? '<span class="text-rose-400 font-bold">*</span>' : ''}`;
    const desc = spec.description || '';
    const defaultValue = spec.default !== undefined ? spec.default : '';
    const type = (spec.type || 'string').toLowerCase();

    let inputHtml = '';
    if (type === 'boolean') {
      const isTrue = defaultValue === true || defaultValue === 'true';
      inputHtml = `
        <select id="runner-arg-${key}" class="w-full bg-slate-800/90 border border-white/10 rounded-lg p-2 text-xs text-white focus:border-cyan-400 focus:outline-none">
          <option value="true" ${isTrue ? 'selected' : ''}>True (Kích hoạt / Bật)</option>
          <option value="false" ${!isTrue ? 'selected' : ''}>False (Tắt)</option>
        </select>
      `;
    } else if (type === 'integer' || type === 'number') {
      inputHtml = `
        <input type="number" id="runner-arg-${key}" value="${defaultValue}" placeholder="${desc || '0'}"
               class="w-full bg-slate-800/90 border border-white/10 rounded-lg p-2 text-xs text-white focus:border-cyan-400 focus:outline-none font-mono">
      `;
    } else if (desc.length > 50 || key.includes('message') || key.includes('code') || key.includes('context') || key.includes('content') || key.includes('prompt')) {
      inputHtml = `
        <textarea id="runner-arg-${key}" rows="2" placeholder="${desc || 'Nhập nội dung...'}"
                  class="w-full bg-slate-800/90 border border-white/10 rounded-lg p-2 text-xs text-white focus:border-cyan-400 focus:outline-none font-mono">${defaultValue}</textarea>
      `;
    } else {
      inputHtml = `
        <input type="text" id="runner-arg-${key}" value="${defaultValue}" placeholder="${desc || 'Nhập giá trị...'}"
               class="w-full bg-slate-800/90 border border-white/10 rounded-lg p-2 text-xs text-white focus:border-cyan-400 focus:outline-none">
      `;
    }

    return `
      <div class="space-y-1">
        <div class="flex items-center justify-between">
          <label for="runner-arg-${key}" class="text-[11px] font-bold text-slate-200 font-mono">${label}</label>
          <span class="text-[10px] text-cyan-400/80 font-mono">${type}</span>
        </div>
        ${inputHtml}
        ${desc ? `<p class="text-[10px] text-slate-400 italic">${desc}</p>` : ''}
      </div>
    `;
  }).join('');
}

function closeSkillRunnerModal() {
  const modal = document.getElementById('modal-skill-runner');
  if (modal) {
    modal.classList.add('hidden');
    modal.classList.remove('flex');
  }
}

function resetSkillRunnerArgs() {
  if (!_activeSkillForRunner || !skillsData || !skillsData[_activeSkillForRunner]) return;
  renderSkillRunnerParamsForm(skillsData[_activeSkillForRunner]);
  const resultSec = document.getElementById('runner-result-section');
  if (resultSec) resultSec.classList.add('hidden');
}

async function submitExecuteSkill() {
  if (!_activeSkillForRunner || !skillsData || !skillsData[_activeSkillForRunner]) {
    showToast('⚠️ Không có kỹ năng nào đang chọn để thực thi.', 'warning');
    return;
  }

  const skill = skillsData[_activeSkillForRunner];
  const params = skill.meta?.parameters || {};
  const props = params.properties || {};
  const required = params.required || [];

  const args = {};
  for (const key of Object.keys(props)) {
    const el = document.getElementById(`runner-arg-${key}`);
    if (!el) continue;
    const spec = props[key] || {};
    const type = (spec.type || 'string').toLowerCase();
    const val = el.value.trim();

    if (required.includes(key) && !val && type !== 'boolean') {
      showToast(`⚠️ Tham số bắt buộc [${key}] chưa được nhập!`, 'warning');
      el.focus();
      return;
    }

    if (type === 'boolean') {
      args[key] = (val === 'true');
    } else if (type === 'integer') {
      args[key] = val !== '' ? parseInt(val, 10) : (spec.default !== undefined ? spec.default : 0);
    } else if (type === 'number') {
      args[key] = val !== '' ? parseFloat(val) : (spec.default !== undefined ? spec.default : 0.0);
    } else {
      if (val !== '' || spec.default !== undefined) {
        args[key] = val !== '' ? val : (spec.default || '');
      }
    }
  }

  const btn = document.getElementById('btn-execute-skill');
  const originalHtml = btn ? btn.innerHTML : '';
  if (btn) {
    btn.disabled = true;
    btn.innerHTML = `<svg class="animate-spin" width="14" height="14" fill="none" stroke="currentColor" stroke-width="2" viewBox="0 0 24 24"><path d="M21 12a9 9 0 1 1-6.219-8.56"/></svg><span>ĐANG THỰC THI TRÊN RUNTIME...</span>`;
  }

  const t0 = performance.now();
  const res = await apiExecuteSkill(_activeSkillForRunner, args);
  const clientLatency = Math.round(performance.now() - t0);

  if (btn) {
    btn.disabled = false;
    btn.innerHTML = originalHtml;
  }

  _lastSkillExecutionResult = res;

  // Display Result Section
  const resultSec = document.getElementById('runner-result-section');
  const statusEl = document.getElementById('runner-result-status');
  const latencyEl = document.getElementById('runner-result-latency');
  const bodyEl = document.getElementById('runner-result-body');

  if (resultSec) resultSec.classList.remove('hidden');

  const isSuccess = res && res.status !== 'error' && res.success !== false;
  if (statusEl) {
    if (isSuccess) {
      statusEl.className = 'px-2 py-0.5 rounded text-[10px] font-bold uppercase bg-emerald-500/20 text-emerald-400 border border-emerald-500/40';
      statusEl.textContent = 'THÀNH CÔNG (200 OK)';
    } else {
      statusEl.className = 'px-2 py-0.5 rounded text-[10px] font-bold uppercase bg-rose-500/20 text-rose-400 border border-rose-500/40';
      statusEl.textContent = 'THẤT BẠI / LỖI';
    }
  }

  if (latencyEl) {
    const latency = res.latency_ms !== undefined ? res.latency_ms : clientLatency;
    latencyEl.textContent = `⏱️ ${latency}ms latency`;
  }

  if (bodyEl) {
    bodyEl.textContent = JSON.stringify(res, null, 2);
  }

  if (isSuccess) {
    showToast(`⚡ Kỹ năng [${_activeSkillForRunner}] hoàn tất trong ${res.latency_ms || clientLatency}ms`, 'success');
  } else {
    showToast(`❌ Kỹ năng [${_activeSkillForRunner}] báo lỗi: ${res.error || res.message || 'Không xác định'}`, 'error');
  }
}

function copySkillResult() {
  if (!_lastSkillExecutionResult) {
    showToast('Chưa có kết quả thực thi nào để sao chép.', 'info');
    return;
  }
  const text = JSON.stringify(_lastSkillExecutionResult, null, 2);
  if (navigator.clipboard && navigator.clipboard.writeText) {
    navigator.clipboard.writeText(text).then(() => {
      showToast('📋 Đã sao chép kết quả JSON vào clipboard!', 'success');
    }).catch(() => {
      showToast('❌ Không thể truy cập clipboard.', 'error');
    });
  } else {
    showToast('📋 Trình duyệt không hỗ trợ tự động chép clipboard.', 'warning');
  }
}

// ═══════════════════════════════════════════════════════════════════════════
// ── THÊM KỸ NĂNG BẰNG TAY (MANUAL SKILL CREATION MODAL) ─────────────────────
// ═══════════════════════════════════════════════════════════════════════════

function openAddSkillModal() {
  const modal = document.getElementById('add-skill-modal');
  if (!modal) return;
  modal.classList.remove('hidden');

  const nameInput = document.getElementById('new-skill-name');
  if (nameInput) {
    nameInput.value = '';
    nameInput.focus();
  }
  const descInput = document.getElementById('new-skill-desc');
  if (descInput) descInput.value = '';
  const codeInput = document.getElementById('new-skill-code');
  if (codeInput) {
    codeInput.value = `# Viết code xử lý tại đây\nimport os\nimport subprocess\n\n# Thực hiện tác vụ...\nreturn {\n    "status": "thành công",\n    "message": "Đã hoàn thành lệnh tự động hóa."\n}`;
  }
}

function closeAddSkillModal() {
  const modal = document.getElementById('add-skill-modal');
  if (modal) modal.classList.add('hidden');
}

async function submitAddSkill() {
  const name = (document.getElementById('new-skill-name').value || '').trim();
  const desc = (document.getElementById('new-skill-desc').value || '').trim();
  const code = (document.getElementById('new-skill-code').value || '').trim();

  if (!name) {
    showToast('Vui lòng nhập tên hàm kỹ năng (không dấu).', 'error');
    return;
  }
  if (!desc) {
    showToast('Vui lòng nhập mô tả chức năng để AI hiểu.', 'error');
    return;
  }
  if (!code) {
    showToast('Vui lòng nhập mã lệnh Python cho kỹ năng.', 'error');
    return;
  }

  const btn = document.getElementById('btn-submit-skill');
  const originalHtml = btn.innerHTML;
  btn.disabled = true;
  btn.innerHTML = `<svg class="animate-spin" width="14" height="14" fill="none" stroke="currentColor" stroke-width="2" viewBox="0 0 24 24"><path d="M21 12a9 9 0 1 1-6.219-8.56"/></svg> Đang biên dịch & lưu...`;

  const res = await apiCreateSkill({
    name: name,
    description: desc,
    python_code: code,
  });

  btn.disabled = false;
  btn.innerHTML = originalHtml;

  if (res.success) {
    showToast(`✅ ${res.message}`, 'success');
    closeAddSkillModal();
    await loadSkills();
    await loadDashboard();
  } else {
    showToast(`❌ ${res.message}`, 'error');
  }
}

// ═══════════════════════════════════════════════════════════════════════════
// ── QUẢN LÝ THIẾT BỊ MÁY TRẠM (ENTERPRISE ORCHESTRATOR) ────────────────────
// ═══════════════════════════════════════════════════════════════════════════

/**
 * Phase 85: lấy danh sách máy trạm, phân biệt rõ "API lỗi" với "không có máy
 * nào".
 *
 * `apiGetClients()` trả `[]` khi HTTP lỗi — giống hệt lúc không có máy nào
 * kết nối. Giao diện bảy ra "0 máy trạm", tức khẳng định chắc chắn là không
 * có máy nào, trong khi thực tế có thể chỉ là máy chủ không trả lời. Nay
 * `null` = lỗi, mảng = kết quả thật.
 */
async function apiFetchClients() {
  try {
    const res = await apiFetch(`${API_BASE}/api/v1/clients`);
    if (!res.ok) return null;
    const data = await res.json();
    return Array.isArray(data) ? data : null;
  } catch (err) {
    console.error('[API] Lỗi lấy danh sách máy trạm:', err);
    return null;
  }
}

/** Định dạng giờ kết nối: "10:12:19" (ngày đầy đủ nằm ở thuộc tính title). */
function _fmtConnectedAt(iso) {
  if (!_isLive(iso)) return '—';
  const m = String(iso).match(/(\d{2}:\d{2}:\d{2})/);
  return m ? m[1] : String(iso);
}

async function loadDevices() {
  const clients = await apiFetchClients();

  if (clients === null) {
    // Không ghi đè danh sách đang có và không đụng các ô số — nói rõ là
    // chưa lấy được, đừng để màn hình trông như mọi thứ bình thường.
    _setLiveText('#stat-total-clients', null, { waiting: 'lỗi kết nối' });
    _setLiveText('#stat-total-skills', null, { waiting: 'lỗi kết nối' });
    _setLiveText('#devices-row-count', null, { waiting: 'lỗi' });
    const errBox = document.getElementById('devices-load-error');
    if (errBox) errBox.classList.remove('hidden');
    return;
  }
  const errBox = document.getElementById('devices-load-error');
  if (errBox) errBox.classList.add('hidden');

  devicesData = clients;
  const count = devicesData.length;

  // Trước đây có hai ô: "TỔNG MÁY TRẠM" và "TRỰC TUYẾN REALTIME", cả hai đều
  // gán `count`. Ô thứ hai bị gỡ: API chỉ trả máy đang mở WebSocket (máy
  // rớt kết nối bị xoá khỏi registry), nên hai ô luôn bằng nhau — hiện một
  // số dưới hai cái tên khác nhau là thông tin bịa.
  _setLiveText('#stat-total-clients', `${count} máy`);

  // Số kỹ năng thật: cộng `skills_count` của từng máy. 0 máy → 0 kỹ năng là
  // con số đúng, không phải "chờ".
  const totalSkills = devicesData.reduce(
    (sum, d) => sum + (Number(d.skills_count) || (d.skills ? d.skills.length : 0) || 0),
    0
  );
  _setLiveText('#stat-total-skills', `${totalSkills} kỹ năng`);

  const now = new Date();
  _setLiveText('#stat-last-sync', now.toLocaleTimeString('vi-VN', { hour12: false }));

  renderDevicesTable(devicesData);
  await checkLocalWorkerStatus();
}

/**
 * Phase 85: bộ hẹn giờ làm mới danh sách máy trạm.
 *
 * Trước đây bảng ghi "Cập nhật tự động realtime" nhưng không có bộ hẹn giờ
 * nào — lời hứa không có thật. Nay có thật: mỗi 10 giây, và chỉ chạy khi
 * sub-tab Máy Trạm đang mở (gọi `_devicesPollStop()` khi rời đi), để không
 * gọi API liên tục lúc người dùng đang ở mục khác.
 */
const _DEVICES_POLL_MS = 10000;
let _devicesPollTimer = null;

function _devicesPollStart() {
  _devicesPollStop();
  _devicesPollTimer = setInterval(() => {
    // Bỏ qua nếu người dùng đang gõ trong ô lọc — tự nạp lại sẽ mất kết quả lọc.
    if (document.activeElement === document.getElementById('filter-devices-input')) return;
    loadDevices();
  }, _DEVICES_POLL_MS);
}

function _devicesPollStop() {
  if (_devicesPollTimer) {
    clearInterval(_devicesPollTimer);
    _devicesPollTimer = null;
  }
}

/** Mở/đóng khối hướng dẫn ở chân sub-tab (nội dung tĩnh, không luôn cần đọc). */
function toggleDevicesGuide() {
  const body = document.getElementById('devices-guide-body');
  const label = document.getElementById('btn-devices-guide-text');
  if (!body) return;
  const willShow = body.classList.contains('hidden');
  body.classList.toggle('hidden', !willShow);
  if (label) label.textContent = willShow ? 'Ẩn hướng dẫn' : 'Xem hướng dẫn';
  // Địa chỉ thật của máy chủ lấy từ trang đang mở, không ghi cứng cổng 443.
  if (willShow) {
    const url = document.getElementById('devices-guide-url');
    if (url) {
      const scheme = location.protocol === 'https:' ? 'wss' : 'ws';
      url.textContent = `python agent.py --server ${scheme}://${location.host}/ws/client`;
    }
  }
}

async function checkLocalWorkerStatus() {
  const status = await apiGetLocalWorkerStatus();
  const btn = document.getElementById('btn-local-worker-toggle');
  const text = document.getElementById('btn-local-worker-text');
  if (!btn || !text) return;

  // Ô số liệu "Worker cục bộ" — lấy từ cùng API, không phải suy đoán.
  _setLiveText('#stat-local-worker',
    status && status.active ? 'đang chạy' : 'đang tắt',
    { waiting: 'lỗi đọc trạng thái' });

  if (status && status.active) {
    btn.className = 'flex-1 py-1.5 px-2 rounded-lg bg-rose-500/15 hover:bg-rose-500/25 text-rose-400 border border-rose-500/40 text-[10px] font-bold transition flex items-center justify-center gap-1 shadow-[0_0_10px_rgba(244,63,94,0.2)]';
    text.textContent = '⏹ Dừng Local';
    btn.title = 'Worker Node cục bộ đang chạy. Bấm để dừng.';
  } else {
    btn.className = 'flex-1 py-1.5 px-2 rounded-lg bg-emerald-500/10 hover:bg-emerald-500/20 text-emerald-400 border border-emerald-500/30 text-[10px] font-bold transition flex items-center justify-center gap-1';
    text.textContent = '⚡ Bật Local';
    btn.title = 'Bật Worker Node chạy ngầm trên máy chủ để thử nghiệm.';
  }
}

async function toggleLocalWorkerNode() {
  const btn = document.getElementById('btn-local-worker-toggle');
  const originalHtml = btn ? btn.innerHTML : '';
  if (btn) {
    btn.disabled = true;
    btn.innerHTML = `<svg class="animate-spin text-cyan-400" width="12" height="12" fill="none" stroke="currentColor" stroke-width="2" viewBox="0 0 24 24"><path d="M21 12a9 9 0 1 1-6.219-8.56"/></svg><span>Đang xử lý...</span>`;
  }

  const res = await apiToggleLocalWorker();

  if (btn) {
    btn.disabled = false;
    btn.innerHTML = originalHtml;
  }

  if (res && res.message) {
    showToast(res.active ? `✅ ${res.message}` : `⏸️ ${res.message}`, res.active ? 'success' : 'info');
  }

  await checkLocalWorkerStatus();
  setTimeout(async () => {
    await loadDevices();
  }, 1200);
}

function renderDevicesTable(list) {
  const tbody = document.getElementById('devices-table-body');
  const emptyState = document.getElementById('devices-empty-state');
  if (!tbody) return;

  tbody.innerHTML = '';
  // Số dòng đang hiện — phải khớp với bộ lọc đang bật, không phải tổng số máy.
  _setLiveText('#devices-row-count', list && list.length ? `${list.length} máy` : null,
    { waiting: 'chưa có' });
  if (!list || list.length === 0) {
    if (emptyState) emptyState.classList.remove('hidden');
    return;
  }
  if (emptyState) emptyState.classList.add('hidden');

  list.forEach(c => {
    const tr = document.createElement('tr');
    tr.className = 'hover:bg-slate-50 dark:hover:bg-white/[0.03] transition-colors border-b border-slate-100 dark:border-white/5';

    const pLower = (c.platform || '').toLowerCase();
    let osBadge = '<span class="px-2 py-0.5 rounded text-[10px] font-bold bg-slate-500/10 text-slate-300 border border-slate-500/30 font-mono">OS</span>';
    let osIcon = '🖥️';

    if (pLower.includes('darwin') || pLower.includes('mac') || pLower.includes('apple')) {
      osBadge = '<span class="px-2 py-0.5 rounded text-[10px] font-bold bg-purple-500/15 text-purple-300 border border-purple-500/30 font-mono">🍎 macOS</span>';
      osIcon = '🍎';
    } else if (pLower.includes('windows') || pLower.includes('win')) {
      osBadge = '<span class="px-2 py-0.5 rounded text-[10px] font-bold bg-cyan-500/15 text-cyan-300 border border-cyan-500/30 font-mono">🪟 Windows</span>';
      osIcon = '🪟';
    } else if (pLower.includes('linux')) {
      osBadge = '<span class="px-2 py-0.5 rounded text-[10px] font-bold bg-emerald-500/15 text-emerald-300 border border-emerald-500/30 font-mono">🐧 Linux</span>';
      osIcon = '🐧';
    }

    const skillsCount = c.skills_count || (c.skills ? c.skills.length : 0);

    // Phase 85: escape dữ liệu của máy trạm trước khi chèn.
    //
    // Mọi trường ở đây do Client Agent tự khai báo (`register` gửi hostname,
    // client_id, platform...). Trước đây chúng được nội thẳng vào HTML, và
    // `client_id` còn được nhúng vào chuỗi JS trong thuộc tính onclick —
    // một máy trạm đăng ký được (tức đã có enrollment token) gửi
    // client_id chứa `'` hoặc `<script>` là chạy được mã tuỳ ý trong trình
    // duyệt của C.E.O. Nay: `escapeHtml()` cho phần hiển thị, còn tham số
    // truyền vào hàm thì đóng thành chuỗi JSON rồi mới escape — escape kiểu
    // HTML một mình không đủ, vì `&#039;` sẽ bị trình duyệt giải mã trở lại
    // thành `'` đúng trong chỗ cần tránh.
    const clientId = escapeHtml(c.client_id);
    const clientIdArg = escapeHtml(JSON.stringify(String(c.client_id ?? '')));

    // Mốc thời gian: `connected_at` là thời điểm, `uptime` là khoảng thời
    // gian đã kết nối. Trước đây cột tiêu đề ghi "THỜI GIAN KẾT NỐI" nhưng
    // in `uptime` ("5m 20s") — tức thời lượng bị đội nhãn thời điểm. Nay in
    // cả hai, mỗi thứ một nhãn đúng.
    const connectedFull = _isLive(c.connected_at) ? String(c.connected_at) : 'chưa rõ';
    const uptimeText = _isLive(c.uptime) ? `đã kết nối ${c.uptime}` : 'vừa kết nối';

    tr.innerHTML = `
      <td class="py-2.5 px-3 font-semibold text-slate-800 dark:text-white">
        <div class="flex items-center gap-2.5">
          <div class="w-8 h-8 rounded-xl bg-cyan-500/10 border border-cyan-400/30 flex items-center justify-center text-sm flex-shrink-0 shadow-sm">
            ${osIcon}
          </div>
          <div class="min-w-0">
            <div class="font-bold text-slate-800 dark:text-white truncate font-mono text-xs">${clientId}</div>
            <div class="text-[10px] text-slate-500 dark:text-slate-400 font-mono truncate max-w-[180px]">${escapeHtml(c.hostname || c.client_id)}</div>
          </div>
        </div>
      </td>
      <td class="py-2.5 px-3">
        <span class="px-2 py-0.5 rounded font-mono font-bold text-xs bg-cyan-500/10 text-cyan-300 border border-cyan-500/20">${escapeHtml(c.ip || '—')}</span>
      </td>
      <td class="py-2.5 px-3">${osBadge}</td>
      <td class="py-2.5 px-3">
        <span class="px-2 py-0.5 rounded bg-slate-100 dark:bg-white/5 border border-slate-200 dark:border-white/10 text-[11px] font-mono font-bold text-cyan-400">
          ${skillsCount} kỹ năng
        </span>
      </td>
      <td class="py-2.5 px-3">
        <div class="font-mono text-[11px] text-slate-600 dark:text-slate-300" title="Kết nối lúc ${escapeHtml(connectedFull)}">${_esc(_fmtConnectedAt(c.connected_at))}</div>
        <div class="text-[10px] text-slate-400">${escapeHtml(uptimeText)}</div>
      </td>
      <td class="py-2.5 px-3 text-right">
        <div class="flex items-center justify-end gap-1.5">
          <button onclick="openLiveMonitor(${clientIdArg})" class="px-2.5 py-1 rounded-lg bg-emerald-500/15 hover:bg-emerald-500/25 text-emerald-300 border border-emerald-400/30 font-semibold text-[11px] transition-all inline-flex items-center gap-1.5 shadow-[0_0_10px_rgba(16,185,129,0.15)]" title="Giám sát màn hình & an ninh trực tiếp">
            <svg width="12" height="12" fill="none" stroke="currentColor" stroke-width="2" viewBox="0 0 24 24"><path d="M1 12s4-8 11-8 11 8 11 8-4 8-11 8-11-8-11-8z"/><circle cx="12" cy="12" r="3"/></svg>
            <span>Giám Sát</span>
          </button>
          <button onclick="openDispatchModal(${clientIdArg})" class="px-2.5 py-1 rounded-lg bg-cyan-500/15 hover:bg-cyan-500/25 text-cyan-300 border border-cyan-400/30 font-semibold text-[11px] transition-all inline-flex items-center gap-1.5" title="Gửi lệnh thực thi từ xa">
            <svg width="12" height="12" fill="none" stroke="currentColor" stroke-width="2" viewBox="0 0 24 24"><polygon points="5 3 19 12 5 21 5 3"/></svg>
            <span>Gửi Lệnh</span>
          </button>
        </div>
      </td>
    `;
    tbody.appendChild(tr);
  });
}

function filterDevices() {
  const query = (document.getElementById('filter-devices-input')?.value || '').toLowerCase().trim();
  if (!query) {
    renderDevicesTable(devicesData);
    return;
  }
  const filtered = devicesData.filter(d =>
    (d.client_id && d.client_id.toLowerCase().includes(query)) ||
    (d.hostname && d.hostname.toLowerCase().includes(query)) ||
    (d.ip && d.ip.toLowerCase().includes(query)) ||
    (d.platform && d.platform.toLowerCase().includes(query))
  );
  renderDevicesTable(filtered);
}

function openDispatchModal(clientId) {
  selectedClientId = clientId;
  const modal = document.getElementById('dispatch-modal');
  const label = document.getElementById('dispatch-target-label');
  const idInput = document.getElementById('dispatch-client-id');
  const resCard = document.getElementById('dispatch-result-card');
  const skillSelect = document.getElementById('dispatch-skill-name');

  const client = devicesData.find(d => d.client_id === clientId);

  if (label) label.textContent = `Mục tiêu: ${clientId} (${client?.ip || 'LAN'})`;
  if (idInput) idInput.value = clientId;
  if (resCard) resCard.classList.add('hidden');

  // Dynamically populate available skills for this client
  if (skillSelect && client && client.skills && client.skills.length) {
    skillSelect.innerHTML = client.skills.map(s => `<option value="${s}">${s}</option>`).join('');
  }

  if (modal) modal.classList.remove('hidden');
}

function closeDispatchModal() {
  const modal = document.getElementById('dispatch-modal');
  if (modal) modal.classList.add('hidden');
}

async function submitDispatchCommand() {
  const clientId = document.getElementById('dispatch-client-id')?.value || selectedClientId;
  const skillName = document.getElementById('dispatch-skill-name')?.value;
  const rawArgs = document.getElementById('dispatch-skill-args')?.value.trim();

  let parsedArgs = {};
  if (rawArgs) {
    try {
      parsedArgs = JSON.parse(rawArgs);
    } catch (e) {
      showToast('Tham số phải là định dạng JSON hợp lệ (ví dụ: {"name": "test"}).', 'error');
      return;
    }
  }

  const btn = document.getElementById('btn-submit-dispatch');
  const resCard = document.getElementById('dispatch-result-card');
  const resText = document.getElementById('dispatch-result-text');

  if (btn) {
    btn.disabled = true;
    btn.innerHTML = `<svg class="animate-spin" width="14" height="14" fill="none" stroke="currentColor" stroke-width="2" viewBox="0 0 24 24"><path d="M21 12a9 9 0 1 1-6.219-8.56"/></svg> Đang thực thi...`;
  }

  const response = await apiExecuteOnClient(clientId, skillName, parsedArgs);

  if (btn) {
    btn.disabled = false;
    btn.innerHTML = `<svg width="14" height="14" fill="none" stroke="currentColor" stroke-width="2" viewBox="0 0 24 24"><polygon points="5 3 19 12 5 21 5 3"/></svg> Thực Thi Ngay`;
  }

  if (resCard && resText) {
    resCard.classList.remove('hidden');
    resText.textContent = JSON.stringify(response, null, 2);
  }

  if (response.status === 'need_confirm') {
    showToast(`⚠️ Tác vụ "${skillName}" thuộc nhóm rủi ro cao, yêu cầu Quản trị viên duyệt!`, 'warning');
    openEmergencyConfirmModal({
      client_id: clientId,
      skill_name: skillName,
      action: skillName,
      args: parsedArgs,
      risk_level: 'NEED_CONFIRM',
      message: response.message || 'Cần xác nhận từ Quản trị viên',
    });
    return;
  }

  if (response.status === 'blocked') {
    showToast(`🚫 Zero-Trust: ${response.message || 'Tác vụ đã bị chặn do vi phạm chính sách an ninh!'}`, 'error');
    return;
  }

  if (response.status === 'success') {
    showToast(`✅ Lệnh trên máy [${clientId}] thực thi thành công!`, 'success');
  } else {
    showToast(`❌ Lỗi từ máy trạm: ${response.error || response.detail || 'Thất bại'}`, 'error');
  }
}

// ═══════════════════════════════════════════════════════════════════════════
// ── PHASE 16: MICROPHONE HARDWARE TOGGLE ────────────────────────────────────
// ═══════════════════════════════════════════════════════════════════════════

let _micStatusInterval = null;
let _micEnabledState = null;
let _audioWaveInterval = null;
let _studioWaveInterval = null;
let lastStudioAudioUrl = null;

function _applyMicUI(enabled) {
  _micEnabledState = !!enabled;
  const indicator = document.getElementById('mic-status-indicator');
  const icon = document.getElementById('mic-status-icon');
  const statusTitle = document.getElementById('mic-status-title');
  const statusText = document.getElementById('mic-status-text');
  const badge = document.getElementById('mic-state-badge');
  const btn = document.getElementById('btn-mic-toggle');
  const label = document.getElementById('mic-toggle-label');
  const ring1 = document.getElementById('mic-acoustic-ring-1');
  const ring2 = document.getElementById('mic-acoustic-ring-2');

  // Ribbon Cards
  const ribbonMicStatus = document.getElementById('voice-stat-mic-status');
  const ribbonMicBadge = document.getElementById('voice-stat-mic-badge');
  const ribbonWakeBadge = document.getElementById('voice-stat-wakeword-badge');

  if (_micEnabledState) {
    // BẬT — đang lắng nghe ngầm
    if (ring1) ring1.classList.remove('hidden');
    if (ring2) ring2.classList.remove('hidden');
    if (indicator) {
      indicator.className = 'w-20 h-20 rounded-full bg-emerald-500/20 border-4 border-emerald-400 flex items-center justify-center transition-all duration-500 shadow-xl shadow-emerald-500/25 relative z-10';
    }
    if (icon) {
      icon.setAttribute('stroke', '#10b981');
      // PHẢI dùng setAttribute, KHÔNG gán `.className`.
      // `mic-status-icon` là thẻ <svg>, mà `SVGElement.className` là một
      // getter trả về `SVGAnimatedString` — chỉ đọc được. Gán vào đó ném
      // `TypeError`, làm `_applyMicUI` dừng giữa chừng và `loadMicStatus()`
      // hỏng hoàn toàn: trạng thái micro không bao giờ hiện.
      // Lỗi này im lặng vì nó nằm sau một `catch` chỉ ghi console.
      icon.setAttribute('class', 'text-emerald-400 transition-colors duration-300');
    }
    if (statusTitle) statusTitle.textContent = 'Micro Đang Lắng Nghe Ngầm';
    if (statusText) {
      statusText.textContent = 'Đang lắng nghe từ khóa kích hoạt "Hey Lyly" hoặc "Xin chào Lyly"...';
      statusText.className = 'text-xs text-emerald-600 dark:text-emerald-400 font-medium mt-1 max-w-xs';
    }
    if (badge) {
      badge.textContent = 'LISTENING';
      badge.className = 'px-2.5 py-0.5 rounded-full text-[10px] font-bold uppercase tracking-wider bg-emerald-500/10 text-emerald-500 dark:text-emerald-400 border border-emerald-500/30';
    }
    if (ribbonMicStatus) {
      ribbonMicStatus.textContent = 'ĐANG LẮNG NGHE';
      ribbonMicStatus.className = 'text-sm font-mono font-bold text-emerald-400';
    }
    if (ribbonMicBadge) {
      ribbonMicBadge.textContent = 'LISTENING';
      ribbonMicBadge.className = 'px-2 py-0.5 rounded-full text-[9px] font-bold bg-emerald-500/10 text-emerald-400 border border-emerald-500/30 shrink-0';
    }
    if (ribbonWakeBadge) {
      ribbonWakeBadge.textContent = 'ACTIVE';
      ribbonWakeBadge.className = 'px-2 py-0.5 rounded-full text-[9px] font-bold bg-emerald-500/10 text-emerald-400 border border-emerald-500/30 shrink-0';
    }
    if (btn) {
      btn.className = 'px-5 py-2.5 rounded-xl text-sm font-semibold flex items-center gap-2 transition-all duration-300 border bg-rose-50 dark:bg-rose-500/20 border-rose-300 dark:border-rose-500 text-rose-600 dark:text-rose-300 hover:bg-rose-100 dark:hover:bg-rose-500/30 shadow-md';
    }
    if (label) label.textContent = 'Tắt Micro (Giải Phóng)';
  } else {
    // TẮT — phần cứng giải phóng
    if (ring1) ring1.classList.add('hidden');
    if (ring2) ring2.classList.add('hidden');
    if (indicator) {
      indicator.className = 'w-20 h-20 rounded-full bg-slate-100 dark:bg-slate-800 border-4 border-slate-300 dark:border-slate-700 flex items-center justify-center transition-all duration-500 shadow-xl relative z-10';
    }
    if (icon) {
      icon.setAttribute('stroke', '#94a3b8');
      // Xem chú thích ở nhánh BẬT phía trên: `icon` là <svg> nên `.className`
      // chỉ đọc được, gán vào sẽ ném TypeError.
      icon.setAttribute('class', 'text-slate-400 transition-colors duration-300');
    }
    if (statusTitle) statusTitle.textContent = 'Micro Đang Tắt';
    if (statusText) {
      statusText.textContent = 'Phần cứng đã giải phóng hoàn toàn, tiết kiệm tài nguyên CPU.';
      statusText.className = 'text-xs text-slate-500 dark:text-slate-400 mt-1 max-w-xs';
    }
    if (badge) {
      badge.textContent = 'RELEASED';
      badge.className = 'px-2.5 py-0.5 rounded-full text-[10px] font-bold uppercase tracking-wider bg-slate-100 dark:bg-slate-700 text-slate-500 dark:text-slate-400 border border-slate-200 dark:border-slate-600';
    }
    if (ribbonMicStatus) {
      ribbonMicStatus.textContent = 'ĐÃ GIẢI PHÓNG';
      ribbonMicStatus.className = 'text-sm font-mono font-bold text-slate-400';
    }
    if (ribbonMicBadge) {
      ribbonMicBadge.textContent = 'RELEASED';
      ribbonMicBadge.className = 'px-2 py-0.5 rounded-full text-[9px] font-bold bg-slate-500/10 text-slate-400 border border-slate-500/30 shrink-0';
    }
    if (ribbonWakeBadge) {
      ribbonWakeBadge.textContent = 'STANDBY';
      ribbonWakeBadge.className = 'px-2 py-0.5 rounded-full text-[9px] font-bold bg-cyan-500/10 text-cyan-400 border border-cyan-500/30 shrink-0';
    }
    if (btn) {
      btn.className = 'px-5 py-2.5 rounded-xl text-sm font-semibold flex items-center gap-2 transition-all duration-300 border bg-emerald-50 dark:bg-emerald-500/10 border-emerald-300 dark:border-emerald-500/40 text-emerald-600 dark:text-emerald-400 hover:bg-emerald-100 dark:hover:bg-emerald-500/20 shadow-md';
    }
    if (label) label.textContent = 'Bật Lắng Nghe Ngầm';
  }
}

async function loadMicStatus() {
  const statusText = document.getElementById('mic-status-text');
  try {
    const res = await apiFetch('/api/v1/voice/mic-status');
    if (res && res.ok) {
      const data = await res.json();
      _applyVoiceIdentity(data);
      if (typeof data.mic_enabled === 'boolean') {
        _applyMicUI(data.mic_enabled);
        return;
      }
    }
    if (_micEnabledState === null) {
      _applyMicUI(false);
    }
  } catch (err) {
    console.warn('[Mic] Không thể tải trạng thái micro:', err);
    if (_micEnabledState === null) {
      _applyMicUI(false);
    }
    if (statusText && _micEnabledState === null) {
      statusText.textContent = 'Không kết nối được dịch vụ Micro.';
      statusText.className = 'text-xs text-amber-500 dark:text-amber-400';
    }
  }
}

async function toggleMicHardware() {
  const btn = document.getElementById('btn-mic-toggle');
  const label = document.getElementById('mic-toggle-label');
  if (btn) btn.disabled = true;
  if (label) label.textContent = 'Đang xử lý...';

  try {
    const targetState = !(_micEnabledState === true);
    const res = await apiFetch('/api/v1/wake-word/toggle', {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
      },
      body: JSON.stringify({ enabled: targetState }),
    });

    if (res && res.status === 200) {
      const data = await res.json();
      const newEnabled = typeof data.mic_enabled === 'boolean' ? data.mic_enabled : targetState;
      _applyMicUI(newEnabled);
      if (newEnabled) {
        const names = (_wakeNames.length ? _wakeNames : ['tên trợ lý']).map((n) => `"${n} ơi"`).join(' hoặc ');
        showToast(`🎙 Đã BẬT Micro lắng nghe ngầm! Hãy gọi ${names} để đánh thức trợ lý.`, 'warning');
      } else {
        showToast('🎙 Đã TẮT Microphone và giải phóng phần cứng thành công.', 'success');
      }
    } else {
      let errMsg = `HTTP ${res?.status || 'Lỗi server'}`;
      try {
        const errData = await res.json();
        if (errData?.detail) errMsg = errData.detail;
      } catch (_) { }
      throw new Error(errMsg);
    }
  } catch (err) {
    console.error('[Mic] Lỗi điều khiển Microphone:', err);
    showToast('❌ Không thể điều khiển Microphone: ' + err.message, 'error');
    if (_micEnabledState !== null) {
      _applyMicUI(_micEnabledState);
    } else {
      _applyMicUI(false);
    }
  } finally {
    if (btn) btn.disabled = false;
  }
}

async function updateVoiceTelemetry(opts = {}) {
  await Promise.all([
    loadMicStatus(),
    loadAudioNodes(),
    _allTTSVoices.length ? Promise.resolve() : loadVoiceStudioVoices(),
  ]);
  const countEl = document.getElementById('voice-stat-tts-count');
  if (countEl && _allTTSVoices.length) countEl.textContent = `${_allTTSVoices.length} GIỌNG`;
  // Chỉ báo khi người dùng BẤM làm mới (trước đây hiện mỗi lần mở tab).
  if (!opts.silent) showToast('🔄 Đã cập nhật trạng thái âm thanh & telemetry', 'info');
}

/** Tên gọi đánh thức + giọng đọc THẬT từ máy chủ (trước: "Hey Lyly" / "Hoài My Neural" viết cứng). */
let _wakeNames = [];
function _applyVoiceIdentity(data) {
  if (Array.isArray(data.wake_names) && data.wake_names.length) _wakeNames = data.wake_names;
  const wakeEl = document.getElementById('voice-stat-wakeword-val');
  if (wakeEl && _wakeNames.length) wakeEl.textContent = _wakeNames.map((n) => `"${n}"`).join(' / ');
  const ttsEl = document.getElementById('voice-stat-tts-val');
  if (ttsEl && data.tts_voice) {
    const v = String(data.tts_voice);
    const friendly = v.includes('HoaiMy') ? 'Hoài My (Nữ)' : v.includes('NamMinh') ? 'Nam Minh (Nam)' : v;
    ttsEl.textContent = friendly;
    ttsEl.title = `${v} · ${data.tts_engine || ''}`;
  }
}

async function loadAudioNodes() {
  const nodesContainer = document.getElementById('audio-nodes-list');
  const statNodesVal = document.getElementById('voice-stat-nodes-val');
  const statNodesBadge = document.getElementById('voice-stat-nodes-badge');

  try {
    const res = await apiFetch('/api/v1/audio-nodes');
    if (!res || !res.ok) throw new Error(`HTTP ${res?.status || '500'}`);
    const data = await res.json();
    const nodes = data.nodes || [];
    const count = nodes.length;

    if (statNodesVal) statNodesVal.textContent = `${count} THIẾT BỊ`;
    // Phase 82: ô tóm tắt ESP32 ở Bảng Điều Khiển đã bỏ theo yêu cầu — tab
    // Trợ Lý Thoại là nơi duy nhất còn hiện số mạch.
    if (statNodesBadge) {
      if (count > 0) {
        statNodesBadge.textContent = 'ONLINE';
        statNodesBadge.className = 'px-2 py-0.5 rounded-full text-[9px] font-bold bg-emerald-500/10 text-emerald-400 border border-emerald-500/30 shrink-0';
      } else {
        statNodesBadge.textContent = 'CHỜ KẾT NỐI';
        statNodesBadge.className = 'px-2 py-0.5 rounded-full text-[9px] font-bold bg-blue-500/10 text-blue-400 border border-blue-500/30 shrink-0';
      }
    }

    if (!nodesContainer) return;

    if (nodes.length && await loadRobotPanel()) return;

    if (nodes.length === 0) {
      nodesContainer.innerHTML = `
        <div class="p-3.5 rounded-xl bg-slate-50 dark:bg-slate-900/50 border border-slate-200 dark:border-slate-800 text-center">
          <div class="text-xs text-slate-500 dark:text-slate-400 font-medium">Chưa phát hiện Robot Trợ Lý trong LAN</div>
          <div class="text-[11px] text-slate-400 mt-1">Cổng WebSocket <code class="font-mono text-cyan-400">/api/v1/xiaozhi/ws</code> sẵn sàng (âm thanh PCM 16 kHz). Bật robot, nối Wi-Fi rồi nhập mã ghép đôi 6 số bên dưới.</div>
        </div>
      `;
      return;
    }

    nodesContainer.innerHTML = nodes.map(node => {
      let stateBadge = '';
      const state = (node.state || '').toLowerCase();
      if (state === 'listening') {
        stateBadge = `<span class="px-2 py-0.5 rounded-full text-[9px] font-bold uppercase bg-amber-500/20 text-amber-300 border border-amber-500/40 animate-pulse">👂 Lắng nghe</span>`;
      } else if (state === 'processing' || state === 'thinking') {
        stateBadge = `<span class="px-2 py-0.5 rounded-full text-[9px] font-bold uppercase bg-cyan-500/20 text-cyan-300 border border-cyan-500/40 animate-pulse">🤔 Suy nghĩ...</span>`;
      } else if (state === 'speaking') {
        stateBadge = `<span class="px-2 py-0.5 rounded-full text-[9px] font-bold uppercase bg-emerald-500/20 text-emerald-300 border border-emerald-500/40">🗣️ Nói (Lip-sync)</span>`;
      } else if (state === 'alert') {
        stateBadge = `<span class="px-2 py-0.5 rounded-full text-[9px] font-bold uppercase bg-rose-500/20 text-rose-300 border border-rose-500/40 animate-bounce">⚠️ Cảnh báo ToF</span>`;
      } else {
        stateBadge = `<span class="px-2 py-0.5 rounded-full text-[9px] font-bold uppercase bg-slate-500/20 text-slate-300 border border-slate-500/40">💤 Sẵn sàng</span>`;
      }

      const pCode = node.pairing_code ? `<span class="ml-1.5 px-1.5 py-0.2 rounded font-mono text-[9px] bg-cyan-500/10 text-cyan-400 border border-cyan-500/30">MÃ: ${escapeHtml(node.pairing_code)}</span>` : '';

      return `
        <div class="p-3 rounded-xl bg-slate-50 dark:bg-slate-900/60 border border-slate-200 dark:border-slate-800 flex items-center justify-between">
          <div class="flex items-center gap-2.5 min-w-0">
            <div class="w-8 h-8 rounded-lg bg-cyan-500/10 border border-cyan-500/30 flex items-center justify-center text-sm shadow-[0_0_8px_rgba(0,242,254,0.15)]">
              🤖
            </div>
            <div class="min-w-0">
              <div class="text-xs font-mono font-bold text-slate-800 dark:text-slate-200 flex items-center">
                <span class="truncate">${escapeHtml(node.device_id || 'ESP32-Robot')}</span>
                ${pCode}
              </div>
              <div class="text-[10px] text-slate-400">${escapeHtml(node.client_host || 'LAN')} · ${escapeHtml(node.audio_format || 'PCM/MP3')}</div>
            </div>
          </div>
          <div class="shrink-0 flex items-center gap-1.5">
            ${stateBadge}
          </div>
        </div>
      `;
    }).join('');
  } catch (err) {
    console.warn('[AudioNodes] Lỗi lấy danh sách node âm thanh:', err);
    // Không biết thì nói không biết — trước đây hiện "0 THIẾT BỊ" như số đo thật.
    if (statNodesVal) statNodesVal.textContent = 'KHÔNG TẢI ĐƯỢC';
  }
}

async function submitRobotPairingCode() {
  const input = document.getElementById('robot-pairing-code-input');
  const resultBox = document.getElementById('robot-pairing-result');
  if (!input) return;

  const code = (input.value || '').trim();
  if (code.length < 4 || code.length > 10) {
    if (resultBox) {
      resultBox.className = 'text-[11px] mt-2 font-mono text-rose-400 block';
      resultBox.textContent = '⚠️ Vui lòng nhập mã ghép đôi 6 chữ số hiển thị trên màn hình OLED của Robot.';
    }
    input.focus();
    return;
  }

  if (resultBox) {
    resultBox.className = 'text-[11px] mt-2 font-mono text-cyan-400 block';
    resultBox.textContent = '⏳ Đang xác thực mã và kết nối robot...';
  }

  try {
    const res = await apiFetch('/api/v1/pairing/verify', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ code: code }),
    });

    const data = await res.json();
    if (!res.ok) {
      throw new Error(data.detail || 'Không thể ghép đôi. Kiểm tra lại mã trên OLED.');
    }

    if (resultBox) {
      resultBox.className = 'text-[11px] mt-2 font-mono text-emerald-400 block';
      resultBox.innerHTML = `✅ <b>Ghép đôi thành công!</b> Đã kết nối robot <code>[${escapeHtml(data.device_id)}]</code>. Màn hình OLED đã đồng bộ.`;
    }
    input.value = '';
    showToast(`🤖 Robot [${data.device_id}] đã ghép đôi thành công!`, 'success');
    await loadAudioNodes();
  } catch (err) {
    if (resultBox) {
      resultBox.className = 'text-[11px] mt-2 font-mono text-rose-400 block';
      resultBox.textContent = `❌ Lỗi: ${err.message}`;
    }
  }
}

async function broadcastAudioAnnouncement() {
  const announceText = 'Xin chào! Đây là thông báo kiểm tra hệ thống âm thanh VN-MateAI. Loa robot đang hoạt động bình thường.';
  try {
    showToast('📢 Đang phát thông báo ra loa robot...', 'info');
    const res = await apiFetch('/api/v1/xiaozhi/announce', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ text: announceText }),
    });
    const data = await res.json();
    if (!res.ok) {
      const err = new Error(data.detail || 'Lỗi server');
      err.status = res.status;
      throw err;
    }
    showToast(`✅ ${data.message}`, 'success');
    await loadAudioNodes();
  } catch (e) {
    showToast(e.status === 503
      ? '⚠️ Không có robot online — phát trong trình duyệt thay thế.'
      : `⚠️ Phát ra robot lỗi (${e.message}) — phát trong trình duyệt thay thế.`, 'warning');
    try {
      const audioUrl = await apiTTS(announceText);
      const audio = document.getElementById('audio-player');
      if (audio) { audio.src = audioUrl; audio.play().catch(() => {}); }
    } catch (_) {}
  }
}

// ═══════════════════════════════════════════════════════════════════════════
// ── IN-BROWSER SPEECH RECOGNITION (WEB SPEECH API) ─────────────────────────
// ═══════════════════════════════════════════════════════════════════════════

// ── Micro trình duyệt -> nhận dạng bằng bộ nhận dạng của MÁY CHỦ ───────────
// Trước đây dùng Web Speech API: âm thanh gửi thẳng lên Google, chỉ Chrome/Edge,
// không theo cấu hình nhận dạng của hệ thống. Nay: thu PCM 16 kHz trong trình
// duyệt, tự dừng khi im lặng ~1,2 s (tối đa 15 s), gửi WAV lên /api/v1/voice/transcribe.
let _micRec = null;

function _pcmToWav(samples, rate) {
  const buf = new ArrayBuffer(44 + samples.length * 2);
  const v = new DataView(buf);
  const w = (o, str) => { for (let i = 0; i < str.length; i++) v.setUint8(o + i, str.charCodeAt(i)); };
  w(0, 'RIFF'); v.setUint32(4, 36 + samples.length * 2, true); w(8, 'WAVE'); w(12, 'fmt ');
  v.setUint32(16, 16, true); v.setUint16(20, 1, true); v.setUint16(22, 1, true);
  v.setUint32(24, rate, true); v.setUint32(28, rate * 2, true); v.setUint16(32, 2, true); v.setUint16(34, 16, true);
  w(36, 'data'); v.setUint32(40, samples.length * 2, true);
  for (let i = 0; i < samples.length; i++) v.setInt16(44 + i * 2, samples[i], true);
  return new Blob([buf], { type: 'audio/wav' });
}

function _setMicButton(active) {
  const statusEl = document.getElementById('browser-mic-status');
  const btn = document.getElementById('btn-browser-mic');
  const icon = document.getElementById('browser-mic-icon');
  if (statusEl) statusEl.classList.toggle('hidden', !active);
  if (btn) btn.className = active
    ? 'absolute right-2 top-1/2 -translate-y-1/2 p-2 rounded-lg bg-rose-500/20 text-rose-500 border border-rose-500/50 shadow-sm animate-pulse transition'
    : 'absolute right-2 top-1/2 -translate-y-1/2 p-2 rounded-lg bg-slate-100 dark:bg-slate-800 hover:bg-cyan-500/20 text-slate-500 dark:text-slate-400 hover:text-cyan-500 transition active:scale-95';
  if (icon) icon.setAttribute('stroke', active ? '#ef4444' : 'currentColor');
}

async function toggleBrowserSpeechRecognition() {
  if (_micRec) { _micRec.stop('user'); return; }
  if (!navigator.mediaDevices?.getUserMedia) {
    showToast('⚠️ Trình duyệt không cho dùng micro (cần HTTPS và quyền micro).', 'warning');
    return;
  }
  let stream;
  try {
    stream = await navigator.mediaDevices.getUserMedia({ audio: { channelCount: 1, echoCancellation: true, noiseSuppression: true } });
  } catch (err) {
    showToast('❌ Không được dùng micro: ' + (err.name === 'NotAllowedError' ? 'hãy cấp quyền micro trên thanh địa chỉ.' : err.message), 'error');
    return;
  }
  const ctx = new (window.AudioContext || window.webkitAudioContext)();
  const src = ctx.createMediaStreamSource(stream);
  const proc = ctx.createScriptProcessor(4096, 1, 1);
  const chunks = [];
  let heardSpeech = false, lastLoud = performance.now();
  const t0 = performance.now();
  const ratio = ctx.sampleRate / 16000;
  proc.onaudioprocess = (e) => {
    const x = e.inputBuffer.getChannelData(0);
    let sum = 0;
    for (let i = 0; i < x.length; i++) sum += x[i] * x[i];
    const rms = Math.sqrt(sum / x.length);
    if (rms > 0.02) { heardSpeech = true; lastLoud = performance.now(); }
    const out = new Int16Array(Math.floor(x.length / ratio));
    for (let i = 0; i < out.length; i++) out[i] = Math.max(-32768, Math.min(32767, x[Math.floor(i * ratio)] * 32767));
    chunks.push(out);
    const now = performance.now();
    if ((heardSpeech && now - lastLoud > 1200) || now - t0 > 15000) _micRec?.stop('auto');
  };
  src.connect(proc); proc.connect(ctx.destination);
  _setMicButton(true);
  showToast('🎙️ Đang nghe… nói câu lệnh, ngừng nói ~1 giây là tự gửi.', 'info');
  _micRec = {
    stop: async (why) => {
      if (!_micRec) return;
      _micRec = null;
      proc.disconnect(); src.disconnect(); stream.getTracks().forEach((t) => t.stop()); ctx.close();
      _setMicButton(false);
      const n = chunks.reduce((a, c) => a + c.length, 0);
      if (!heardSpeech || n < 16000 * 0.4) { if (why === 'auto') showToast('Không nghe thấy tiếng nói.', 'info'); return; }
      const all = new Int16Array(n);
      let o = 0; for (const c of chunks) { all.set(c, o); o += c.length; }
      const input = document.getElementById('voice-input');
      try {
        if (input) input.value = '… đang nhận dạng';
        const res = await apiFetch('/api/v1/voice/transcribe', { method: 'POST', headers: { 'Content-Type': 'audio/wav' }, body: _pcmToWav(all, 16000) });
        const d = await res.json();
        if (!res.ok) throw new Error(d.detail || `HTTP ${res.status}`);
        if (input) input.value = d.text || '';
        if (d.text && d.text.trim().length >= 2) {
          showToast(`⚡ Nhận dạng (${d.backend || 'máy chủ'}, ${d.ms} ms): "${d.text}"`, 'success');
          sendVoiceCommand();
        } else {
          showToast('Không nhận dạng được câu nói — thử nói rõ / gần micro hơn.', 'warning');
        }
      } catch (err) {
        if (input) input.value = '';
        showToast('❌ Nhận dạng thất bại: ' + err.message, 'error');
      }
    },
  };
}

// ── Công cụ đã chạy trong lượt (+ Duyệt / Từ chối ngay tại chỗ) ─────────────
const _TOOL_STATUS = {
  success: ['đã chạy', 'text-emerald-600 dark:text-emerald-400'],
  done: ['đã chạy', 'text-emerald-600 dark:text-emerald-400'],
  need_confirm: ['chờ phê duyệt', 'text-amber-600 dark:text-amber-400'],
  awaiting_approval: ['chờ phê duyệt', 'text-amber-600 dark:text-amber-400'],
  blocked: ['bị chặn', 'text-rose-500'],
  error: ['lỗi', 'text-rose-500'],
};

function renderVoiceTools(tools, needsConfirm) {
  const box = document.getElementById('voice-tools-box');
  if (!box) return;
  if (!tools.length) { box.classList.add('hidden'); box.innerHTML = ''; return; }
  box.classList.remove('hidden');
  box.innerHTML = `<div class="text-[10px] font-bold text-slate-500 uppercase">Công cụ trong lượt này</div>` + tools.map((t) => {
    const [label, cls] = _TOOL_STATUS[t.status] || [t.status, 'text-slate-500'];
    const where = t.target && t.target !== 'master' ? ` @ ${_esc(t.target)}` : '';
    const actions = t.approval_id
      ? `<span class="ml-auto flex gap-1" id="voice-appr-${_esc(t.approval_id)}">` +
        `<button class="px-2 py-0.5 rounded bg-emerald-600 text-white text-[10px] font-bold" onclick="decideVoiceApproval('${_esc(t.approval_id)}', true)">Duyệt</button>` +
        `<button class="px-2 py-0.5 rounded bg-rose-500/15 text-rose-500 text-[10px] font-bold" onclick="decideVoiceApproval('${_esc(t.approval_id)}', false)">Từ chối</button></span>`
      : '';
    return `<div class="flex items-center gap-2 p-2 rounded-lg bg-slate-50 dark:bg-white/[0.03] border border-slate-200 dark:border-white/10 text-xs">` +
      `<span class="font-mono">${_esc(t.skill)}${where}</span><span class="${cls} font-semibold">${label}</span>` +
      (t.message ? `<span class="text-[10px] text-slate-400 truncate" title="${_esc(t.message)}">${_esc(t.message)}</span>` : '') +
      actions + `</div>`;
  }).join('');
}

async function decideVoiceApproval(id, approve) {
  const slot = document.getElementById(`voice-appr-${id}`);
  try {
    const res = await apiFetch(`/api/v1/enterprise/hitl/${approve ? 'approve' : 'reject'}`, {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ approval_id: id, reason: approve ? '' : 'Từ chối trên Portal (tab Hội thoại)' }),
    });
    const d = await res.json();
    if (!res.ok || d.status === 'error') throw new Error(d.detail || d.error || d.message || `HTTP ${res.status}`);
    if (slot) slot.innerHTML = `<span class="text-[10px] font-bold ${approve ? 'text-emerald-600' : 'text-rose-500'}">${approve ? 'Đã duyệt' : 'Đã từ chối'}</span>`;
    showToast(approve ? '✅ Đã duyệt — tác vụ đang chạy.' : '🚫 Đã từ chối tác vụ.', approve ? 'success' : 'info');
  } catch (err) {
    showToast('❌ ' + err.message, 'error');
  }
}

// ── Tab con ────────────────────────────────────────────────────────────────
function showVoiceSubTab(name) {
  const root = document.getElementById('tab-voice');
  if (!root) return;
  const all = name === 'all';
  root.querySelectorAll('[data-voice-sub]').forEach((el) => el.classList.toggle('hidden', !all && el.dataset.voiceSub !== name));
  for (const [id, span] of [['voice-col-left', 'lg:col-span-5'], ['voice-col-right', 'lg:col-span-7']]) {
    const col = document.getElementById(id);
    if (!col) continue;
    col.classList.toggle(span, all);
    col.classList.toggle('lg:col-span-12', !all);
  }
  root.querySelectorAll('.voice-subtab').forEach((b) => {
    const on = b.dataset.voiceSubtab === name;
    b.classList.toggle('bg-primary-600', on); b.classList.toggle('text-white', on);
    b.classList.toggle('text-slate-600', !on); b.classList.toggle('dark:text-slate-300', !on);
  });
  try { localStorage.setItem('vnmate_voice_subtab', name); } catch (_) { /* bỏ qua */ }
  if (name === 'history' || all) loadVoiceHistory();
}

function initVoiceSubTabs() {
  let saved = 'robot';
  try { saved = localStorage.getItem('vnmate_voice_subtab') || 'robot'; } catch (_) { /* bỏ qua */ }
  showVoiceSubTab(saved);
}

// ── Lịch sử lượt thoại + thời gian từng bước ───────────────────────────────
async function loadVoiceHistory() {
  const box = document.getElementById('voice-history');
  if (!box) return;
  const ch = document.getElementById('voice-history-channel')?.value || '';
  try {
    const res = await apiFetch(`/api/v1/voice/metrics?recent=40${ch ? `&channel=${encodeURIComponent(ch)}` : ''}`);
    if (res.status === 403) { box.innerHTML = '<span class="italic">Chỉ admin xem được lịch sử lượt thoại (có mã phiên / thiết bị).</span>'; return; }
    const d = await res.json();
    const rows = d.recent || [];
    if (!rows.length) { box.innerHTML = '<span class="italic">Chưa có lượt thoại nào từ lúc máy chủ khởi động.</span>'; return; }
    const maxT = Math.max(...rows.map((r) => (r.stt_ms || 0) + (r.ttl_ms || 0)), 1);
    const seg = (ms, cls, title) => (ms > 0 ? `<div class="${cls} h-full" style="width:${(ms / maxT) * 100}%" title="${title}: ${Math.round(ms)} ms"></div>` : '');
    box.className = 'space-y-1.5';
    box.innerHTML = rows.map((r) => {
      const stt = r.stt_ms || 0;
      const router = r.router_ms || 0;
      const llm = Math.max(0, (r.llm_first_token_ms ?? router) - router);
      const firstAudio = Math.max(0, (r.ttfa_answer_ms ?? r.ttfa_ms ?? 0) - Math.max(router, r.llm_first_token_ms || 0));
      const rest = Math.max(0, (r.ttl_ms || 0) - router - llm - firstAudio);
      const fb = r.model && r.model_requested && r.model !== r.model_requested;
      const bad = r.outcome === 'error';
      return `<div class="grid grid-cols-12 gap-2 items-center text-[11px]">` +
        `<div class="col-span-3 truncate"><span class="font-semibold ${bad ? 'text-rose-500' : ''}">${_esc(r.channel || '?')}</span> ` +
        `<span class="text-slate-400">${_esc(r.fast_command ? 'lệnh nhanh' : (r.brain || ''))}${r.used_agent ? ' · công cụ' : ''}</span>` +
        `<div class="text-[10px] text-slate-400 font-mono truncate" title="${_esc(r.model || '')}">${_esc(r.model || '')}${fb ? ' <b class="text-amber-500">(dự phòng)</b>' : ''}</div></div>` +
        `<div class="col-span-7 h-3 rounded bg-slate-100 dark:bg-white/5 flex overflow-hidden">` +
        seg(stt, 'bg-violet-500', 'Nhận dạng giọng') + seg(router, 'bg-sky-500', 'Định tuyến') + seg(llm, 'bg-amber-500', 'Chờ LLM') +
        seg(firstAudio, 'bg-emerald-500', 'Tới tiếng đầu') + seg(rest, 'bg-slate-400', 'Đọc hết') + `</div>` +
        `<div class="col-span-2 text-right font-mono ${bad ? 'text-rose-500' : ''}">${bad ? 'LỖI · ' : ''}${Math.round((r.ttl_ms || 0) + stt)} ms</div></div>`;
    }).join('');
  } catch (err) {
    box.innerHTML = `<span class="text-rose-500">Không tải được: ${_esc(friendlyErrorText(err))}</span>`;
  }
}

// ── Bảng điều khiển từng robot ─────────────────────────────────────────────
let _robotAnimations = {};

function _robotCard(r) {
  const id = _esc(r.device_id);
  const st = r.status_report || {};
  const au = r.audio_stats || {};
  const ago = (t) => (t ? `${Math.max(0, Math.round(Date.now() / 1000 - t))} s trước` : '—');
  const facts = [
    ['Firmware', r.firmware_version],
    ['IP', r.client_host],
    ['Wi-Fi', st.rssi != null ? `${st.rssi} dBm` : '—'],
    ['Âm lượng', st.volume != null ? `${st.volume}%` : '—'],
    ['Chạy được', st.uptime_s != null ? `${Math.floor(st.uptime_s / 60)} phút` : '—'],
    ['Bộ nhớ trống', st.heap != null ? `${Math.round(st.heap / 1024)} KB` : '—'],
    ['Ồn nền / đỉnh', au.noise != null ? `${au.noise} / ${au.peak} (${ago(au.at)})` : '—'],
    ['Nghe tiếp', r.follow_up ? `mức ${r.follow_up}` : 'không'],
    ['Quyền', r.own_token ? (r.role || 'mặc định') : 'chưa có token riêng'],
    ['Đã duyệt sẵn', r.approval_grants && r.approval_grants.length ? r.approval_grants.join(', ') : '—'],
  ];
  const need54 = (ok) => (ok ? '' : ' disabled title="Cần firmware 54 trở lên"');
  const chk = r.audio_check || null;
  const chkHtml = !chk ? '' : chk.status !== 'done'
    ? `<div class="text-[11px] text-sky-500 animate-pulse">Kiểm tra âm thanh: ${chk.status === 'listening' ? 'đang nghe — hãy nói câu mẫu' : 'đang xử lý…'}</div>`
    : `<div class="p-2 rounded-lg text-[11px] ${chk.grade === 'tốt' ? 'bg-emerald-500/10' : 'bg-amber-500/10'}">` +
      `<b>Kiểm tra âm thanh: ${_esc(chk.grade)}</b>${chk.snr_db != null ? ` · giọng hơn ồn ${chk.snr_db} dB` : ''}` +
      `${chk.levels && chk.levels.rms_dbfs != null ? ` · mức ${chk.levels.rms_dbfs} dBFS` : ''} · tên gọi ${chk.wake_ok ? 'nhận ra ✓' : 'không nhận ra ✗'}` +
      `<div>Nghe được: "${_esc(chk.heard || '')}"</div><div class="text-slate-500">${_esc(chk.advice || '')}</div></div>`;
  const animOpts = Object.entries(_robotAnimations).map(([k, v]) => `<option value="${k}">${_esc(v)}</option>`).join('');
  return `<div class="p-3 rounded-xl bg-slate-50 dark:bg-slate-900/60 border border-slate-200 dark:border-slate-800 space-y-2">` +
    `<div class="flex items-center gap-2"><span class="text-lg">🤖</span><span class="font-mono font-bold text-xs">${id}</span>` +
    `<span class="text-[10px] px-1.5 rounded bg-cyan-500/10 text-cyan-500">${_esc(r.state)}${r.busy ? ' · đang xử lý' : ''}</span>` +
    `<button class="ml-auto text-[10px] px-2 py-0.5 rounded border border-slate-200 dark:border-white/10"${need54(r.supports?.status)} onclick="robotAction('${id}','status')">Làm mới</button></div>` +
    `<div class="grid grid-cols-2 gap-x-3 gap-y-0.5 text-[10px]">${facts.map(([k, v]) => `<div class="flex justify-between gap-2"><span class="text-slate-400">${k}</span><span class="font-mono truncate" title="${_esc(String(v))}">${_esc(String(v ?? '—'))}</span></div>`).join('')}</div>` +
    `<div class="flex gap-1"><input id="robot-say-${id}" maxlength="300" placeholder="Câu robot sẽ đọc…" class="flex-1 px-2 py-1 rounded-lg border border-slate-200 dark:border-white/10 bg-transparent text-[11px]">` +
    `<button class="px-2 py-1 rounded-lg bg-cyan-600 text-white text-[10px] font-bold" onclick="robotAction('${id}','say')">Đọc</button></div>` +
    `<div class="flex flex-wrap items-center gap-1 text-[10px]">` +
    `<select id="robot-anim-${id}" class="px-1.5 py-1 rounded-lg border border-slate-200 dark:border-white/10 bg-transparent">${animOpts}</select>` +
    `<button class="px-2 py-1 rounded-lg border border-slate-200 dark:border-white/10" onclick="robotAction('${id}','animate')">Cử động</button>` +
    `<label class="flex items-center gap-1 ml-1">🔊<input type="range" min="0" max="100" value="${st.volume ?? 100}" id="robot-vol-${id}"${need54(r.supports?.volume)} onchange="robotAction('${id}','volume')"></label>` +
    `<button class="px-2 py-1 rounded-lg border border-slate-200 dark:border-white/10" onclick="robotAction('${id}','audio-check')">Kiểm tra âm thanh</button>` +
    `<button class="px-2 py-1 rounded-lg text-rose-500 border border-rose-500/30"${need54(r.supports?.reboot)} onclick="robotAction('${id}','reboot')">Khởi động lại</button>` +
    `</div>${chkHtml}` +
    (r.supports?.volume ? '' : `<div class="text-[10px] text-amber-500">Firmware ${_esc(r.firmware_version)}: chưa có âm lượng / khởi động lại / báo trạng thái — nạp firmware 54 (cắm USB, <code>pio run -e esp32s3 -t upload</code>).</div>`) +
    `</div>`;
}

async function loadRobotPanel() {
  const box = document.getElementById('audio-nodes-list');
  try {
    const res = await apiFetch('/api/v1/robots');
    if (!res.ok) return false;                 // viewer: giữ danh sách đơn giản
    const d = await res.json();
    _robotAnimations = d.animations || {};
    if (!d.robots || !d.robots.length) return false;
    if (box) box.innerHTML = d.robots.map(_robotCard).join('');
    return true;
  } catch (_) { return false; }
}

async function robotAction(id, action) {
  const enc = encodeURIComponent(id);
  let body = null;
  if (action === 'say') {
    const text = (document.getElementById(`robot-say-${id}`)?.value || '').trim();
    if (!text) { showToast('Nhập câu cho robot đọc.', 'info'); return; }
    body = { text };
  } else if (action === 'animate') {
    body = { animation: document.getElementById(`robot-anim-${id}`)?.value };
  } else if (action === 'volume') {
    body = { level: parseInt(document.getElementById(`robot-vol-${id}`)?.value || '100', 10) };
  } else if (action === 'reboot' && !confirm(`Khởi động lại robot ${id}?`)) {
    return;
  }
  try {
    const res = await apiFetch(`/api/v1/robots/${enc}/${action}`, {
      method: 'POST', headers: { 'Content-Type': 'application/json' }, body: body ? JSON.stringify(body) : undefined,
    });
    const d = await res.json();
    if (!res.ok) throw new Error(d.detail || `HTTP ${res.status}`);
    if (d.message) showToast(`✅ ${d.message}`, 'success');
    if (action === 'audio-check') _pollAudioCheck(id);
    setTimeout(loadRobotPanel, action === 'reboot' ? 25000 : 1500);
  } catch (err) {
    showToast('❌ ' + err.message, 'error');
  }
}

function _pollAudioCheck(id, tries = 0) {
  if (tries > 20) return;
  setTimeout(async () => {
    await loadRobotPanel();
    try {
      const d = await (await apiFetch(`/api/v1/robots/${encodeURIComponent(id)}/audio-check`)).json();
      if (d.check && d.check.status !== 'done') _pollAudioCheck(id, tries + 1);
    } catch (_) { /* bỏ qua */ }
  }, 2000);
}

Object.assign(window, { showVoiceSubTab, loadVoiceHistory, robotAction, decideVoiceApproval, toggleBrowserSpeechRecognition });

// ═══════════════════════════════════════════════════════════════════════════
// ── WAVEFORM VISUALIZER HELPERS ───────────────────────────────────────────
// ═══════════════════════════════════════════════════════════════════════════

function startWaveformVisualizer(barClass = 'voice-wave-bar') {
  stopWaveformVisualizer(barClass);
  const bars = document.querySelectorAll(`.${barClass}`);
  if (!bars.length) return;
  _audioWaveInterval = setInterval(() => {
    bars.forEach(bar => {
      const h = Math.floor(Math.random() * 24) + 6;
      bar.style.height = `${h}px`;
    });
  }, 90);
}

function stopWaveformVisualizer(barClass = 'voice-wave-bar') {
  if (_audioWaveInterval) {
    clearInterval(_audioWaveInterval);
    _audioWaveInterval = null;
  }
  const bars = document.querySelectorAll(`.${barClass}`);
  bars.forEach(bar => {
    bar.style.height = '4px';
  });
}

function startStudioWaveform() {
  const container = document.getElementById('studio-waveform');
  if (container) container.classList.remove('hidden');
  const bars = document.querySelectorAll('.studio-wave-bar');
  if (_studioWaveInterval) clearInterval(_studioWaveInterval);
  _studioWaveInterval = setInterval(() => {
    bars.forEach(bar => {
      const h = Math.floor(Math.random() * 18) + 4;
      bar.style.height = `${h}px`;
    });
  }, 100);
}

function stopStudioWaveform() {
  if (_studioWaveInterval) {
    clearInterval(_studioWaveInterval);
    _studioWaveInterval = null;
  }
  const container = document.getElementById('studio-waveform');
  if (container) container.classList.add('hidden');
  const bars = document.querySelectorAll('.studio-wave-bar');
  bars.forEach(bar => {
    bar.style.height = '4px';
  });
}

// ═══════════════════════════════════════════════════════════════════════════
// ── KIỂM THỬ LỆNH THOẠI & PHÁT ÂM THANH TTS ───────────────────────────────
// ═══════════════════════════════════════════════════════════════════════════

function escapePortalHtml(str) {
  if (!str) return '';
  return str.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
}

function formatPortalInlineText(str) {
  if (!str) return '';
  return str
    .replace(/\*\*(.*?)\*\*/g, '<strong class="font-bold text-slate-900 dark:text-cyan-300">$1</strong>')
    .replace(/\*(.*?)\*/g, '<em class="italic text-slate-700 dark:text-slate-300">$1</em>')
    .replace(/`([^`]+)`/g, '<code class="px-1.5 py-0.5 rounded bg-slate-200 dark:bg-slate-800 text-cyan-600 dark:text-cyan-400 font-mono text-xs border border-slate-300 dark:border-slate-700">$1</code>');
}

function renderPortalMarkdown(rawText) {
  if (!rawText) return '';
  let text = rawText.trim();

  // ── Bảo mật ────────────────────────────────────────────────────────
  // Mọi thứ gửi vào đây rồi đều đi thẳng ra `innerHTML` (xem 4 call site
  // ở sendVoiceCommand / HITL / HUD). Escape sớm, trước khi bước 3-5 chèn
  // thẻ HTML của riêng hàm này vào.
  //
  // Không escape sau cùng được: bước 2 đã sinh ra <table>/<th>/<td> và bước 3-5
  // chèn <strong>/<h2>..., escape sau sẽ hỏng chính những thẻ đó.
  //
  // Khối code đã tự escape sẵn ở bước 1 nên phải cất ra chỗ riêng, nếu escape
  // lần nữa thì thẻ của nó cũng bị escape mất.
  // Dùng ký tự NUL làm viền: văn bản người dùng/LLM không chứa NUL, còn
  // chuỗi 6 ký tự "\u0000" do người viết tay sẽ không khớp regex khi khôi phục.
  //
  // Ghi sentinel bằng ESCAPE `\u0000` chứ không phải byte NUL thật: hai cách
  // cho cùng giá trị runtime (đã kiểm chứng: cùng length 3, cùng charCode 0),
  // nhưng escape giữ file là văn bản thuần. Trước đây ghi byte NUL thật khiến
  // git đánh dấu app.js là file NHỊ PHÂN — git diff không hiện nội dung, mỗi
  // thay đổi sau đó đều không review được.
  const CB = '\u0000CB';
  const codeBlocks = [];

  // 1. Code blocks: ```...```
  text = text.replace(/```([a-zA-Z0-9]*)\n?([\s\S]*?)```/g, (match, lang, code) => {
    codeBlocks.push(
      `<pre class="my-2 p-3 rounded-xl bg-slate-900 text-cyan-300 font-mono text-xs overflow-x-auto border border-cyan-500/30"><code>${escapePortalHtml(code.trim())}</code></pre>`
    );
    return `${CB}${codeBlocks.length - 1}${CB}`;
  });

  // 2. Tables: lines containing |
  const rawLines = text.split('\n');
  let inTable = false;
  let tableHtml = '';
  const outputLines = [];

  for (let i = 0; i < rawLines.length; i++) {
    const line = rawLines[i].trim();
    if (line.startsWith('|') && line.endsWith('|')) {
      if (!inTable) {
        inTable = true;
        tableHtml = '<div class="overflow-x-auto my-3 rounded-xl border border-slate-200 dark:border-cyan-500/30 shadow-sm"><table class="w-full text-left border-collapse text-xs"><tbody>';
      }
      if (/^\|(\s*[-:]+[-|\s:]*)\|$/.test(line)) {
        continue; // skip separator row
      }
      const cells = line.split('|').slice(1, -1);
      const isHeader = !tableHtml.includes('<tr');
      const tag = isHeader ? 'th' : 'td';
      const rowClass = isHeader
        ? 'bg-slate-100 dark:bg-cyan-950/50 text-slate-800 dark:text-cyan-300 font-bold border-b border-slate-200 dark:border-cyan-500/30'
        : 'border-b border-slate-100 dark:border-cyan-500/10 hover:bg-slate-50 dark:hover:bg-cyan-950/20 text-slate-700 dark:text-slate-200';
      // Escape ô trước khi bọc thẻ: nội dung báo cáo lấy từ hệ thống khách
      // hàng (tên hàng, tên khách, ghi chú) — không phải do người dùng gõ,
      // nên không thể tin là đã sạch.
      tableHtml += `<tr class="${rowClass}">` + cells.map(c => `<${tag} class="px-3 py-2">${formatPortalInlineText(escapePortalHtml(c.trim()))}</${tag}>`).join('') + '</tr>';
    } else {
      if (inTable) {
        tableHtml += '</tbody></table></div>';
        outputLines.push(tableHtml);
        inTable = false;
        tableHtml = '';
      }
      // Đường kẻ ngang: `---`, `***`, `___` (tối thiểu 3 ký tự).
      // LLM rất hay dùng `---` để tách mục, và không có nhánh này thì người
      // đọc thấy ba gạch ngang thô xen giữa văn bản.
      //
      // Dòng kẻ trong bảng (`| --- | --- |`) đã bị bỏ qua ở nhánh bảng phía
      // trên nên không đụng tới; dấu `>` trong `>` chỉ bị escape ở nhánh
      // dòng thường nên không giả được.
      if (/^([-*_])\1{2,}$/.test(line)) {
        outputLines.push('<hr class="my-3 border-slate-200 dark:border-slate-700" />');
      } else {
        outputLines.push(escapePortalHtml(line));
      }
    }
  }
  if (inTable) {
    tableHtml += '</tbody></table></div>';
    outputLines.push(tableHtml);
  }

  text = outputLines.join('\n');

  // Trả khối code (đã escape từ bước 1) về chỗ cũ.
  if (codeBlocks.length) {
    text = text.replace(new RegExp(`${CB}(\\d+)${CB}`, 'g'), (_, n) => codeBlocks[Number(n)] ?? '');
    // Dọn token sót lại nếu có chỗ hởng — để ký tự NUL không lọt ra DOM.
    text = text.split(CB).join('');
  }

  // 3. Headers: #, ##, ###
  text = text.replace(/^### (.*$)/gim, '<h4 class="text-xs font-bold uppercase tracking-wider text-cyan-600 dark:text-cyan-400 mt-3 mb-1">▸ $1</h4>');
  text = text.replace(/^## (.*$)/gim, '<h3 class="text-sm font-bold text-slate-900 dark:text-cyan-300 mt-4 mb-2 border-b border-slate-200 dark:border-cyan-500/20 pb-1">■ $1</h3>');
  text = text.replace(/^# (.*$)/gim, '<h2 class="text-base font-extrabold text-slate-900 dark:text-cyan-200 mt-4 mb-2 tracking-wide border-b border-cyan-400/40 pb-1">✦ $1</h2>');

  // 4. Bullet lists: - item, * item, • item
  text = text.replace(/^[\*\-•]\s+(.*$)/gim, '<div class="flex items-start gap-2 my-1 pl-1"><span class="text-cyan-500 font-bold shrink-0">▸</span><span>$1</span></div>');

  // 5. Bold & inline elements
  text = formatPortalInlineText(text);

  // 6. Paragraph breaks
  text = text.replace(/\n{2,}/g, '<div class="my-2"></div>');
  text = text.replace(/\n/g, '<br/>');

  return text;
}

// ═══════════════════════════════════════════════════════════════════════════
// ── PHÁT AUDIO CÂU TRẢ LỜI THOẠI ───────────────────────────────────────────
// ═══════════════════════════════════════════════════════════════════════════
// Bộ phát dùng chung với HUD: web/voice-audio-queue.js (VoiceAudioQueue) — realtime
// P5. Trước đây trang này có bản riêng (StreamingAudioQueue) và mỗi lượt tạo
// một AudioContext mới; nay cả trang dùng chung một AudioContext.
let _portalAudioCtx = null;
function _getPortalAudioContext() {
  if (!_portalAudioCtx) {
    const AC = window.AudioContext || window.webkitAudioContext;
    if (AC) {
      try { _portalAudioCtx = new AC(); } catch (e) { console.warn('[Audio] AudioContext lỗi:', e); }
    }
  }
  return _portalAudioCtx;
}

/** Dừng mọi <audio> khác đang phát để không có hai giọng cùng lúc. */
function _stopOtherAudioPlayers() {
  ['audio-player', 'studio-audio-player'].forEach(id => {
    const el = document.getElementById(id);
    if (el && !el.paused) {
      try { el.pause(); el.currentTime = 0; } catch (e) {}
    }
  });
}

function _newVoiceAudioQueue() {
  return new VoiceAudioQueue({
    getContext: _getPortalAudioContext,
    onBeforeChunk: (first) => { if (first) _stopOtherAudioPlayers(); },
  });
}

let _currentAudioStreamQueue = null;
let _currentVoiceWS = null;

function _resetVoiceButton(btn) {
  if (!btn) return;
  btn.disabled = false;
  btn.innerHTML = `
    <svg width="14" height="14" fill="none" stroke="currentColor" stroke-width="2" viewBox="0 0 24 24">
      <line x1="22" y1="2" x2="11" y2="13"/>
      <polygon points="22 2 15 22 11 13 2 9 22 2"/>
    </svg>
    <span>Gửi Lệnh Thoại</span>
  `;
}

async function _fallbackRestVoiceCommand(query, btn, card, textEl) {
  try {
    const res = await apiVoiceCommand(query, true);
    _resetVoiceButton(btn);
    if (card && textEl) {
      card.classList.remove('hidden');
      textEl.innerHTML = renderPortalMarkdown(res.reply || '_(không có nội dung phản hồi)_');
      scheduleAiDownloadCheck();
    }
    if (res.audio_base64) {
      lastAudioBase64 = res.audio_base64;
      playVoiceAudio();
    }
    showToast('✅ Đã nhận phản hồi (Chế độ tương thích REST)', 'success');
  } catch (err) {
    _resetVoiceButton(btn);
    showToast(`❌ Lỗi thực thi: ${err.message}`, 'error');
  }
}

/**
 * Phase 12: Ngắt lời trợ lý ngay lập tức (Barge-In / User Interruption).
 * Ngắt âm thanh đang phát trên Web Audio / MSE và gửi tín hiệu hủy lên server qua WebSocket.
 */
function stopActiveVoiceStream() {
  console.log('[BargeIn] User triggered stop / barge-in.');
  if (_currentAudioStreamQueue) {
    _currentAudioStreamQueue.stop();
  }
  if (_currentVoiceWS && _currentVoiceWS.readyState === WebSocket.OPEN) {
    try {
      _currentVoiceWS.send(JSON.stringify({ type: 'barge_in', reason: 'user_stop_click' }));
    } catch (e) {}
  }
  const stopBtn = document.getElementById('btn-stop-voice-stream');
  if (stopBtn) stopBtn.classList.add('hidden');
  const streamBadge = document.getElementById('voice-stream-badge');
  if (streamBadge) streamBadge.classList.add('hidden');
  const btn = document.getElementById('btn-send-voice');
  if (btn) _resetVoiceButton(btn);
  showToast('Đã dừng phát âm thanh trợ lý.', 'info');
}

async function sendVoiceCommand() {
  const input = document.getElementById('voice-input');
  const query = input?.value?.trim();
  if (!query) {
    showToast('Vui lòng nhập câu lệnh trước khi gửi.', 'info');
    return;
  }

  // Abort any existing voice stream (Barge-In on new query)
  if (_currentVoiceWS && _currentVoiceWS.readyState === WebSocket.OPEN) {
    try {
      _currentVoiceWS.send(JSON.stringify({ type: 'barge_in', reason: 'new_query' }));
      _currentVoiceWS.close();
    } catch (e) {}
  }
  if (_currentAudioStreamQueue) {
    _currentAudioStreamQueue.stop();
  }

  const btn = document.getElementById('btn-send-voice');
  if (btn) {
    btn.disabled = true;
    btn.innerHTML = `<svg class="animate-spin" width="14" height="14" fill="none" stroke="currentColor" stroke-width="2" viewBox="0 0 24 24"><path d="M21 12a9 9 0 1 1-6.219-8.56"/></svg> Đang kết nối...`;
  }

  const card = document.getElementById('voice-response-card');
  const textEl = document.getElementById('voice-response-text');
  renderVoiceTools([], false);
  const streamBadge = document.getElementById('voice-stream-badge');
  const ttfaBadge = document.getElementById('voice-ttfa-badge');
  const stopBtn = document.getElementById('btn-stop-voice-stream');

  if (card && textEl) {
    card.classList.remove('hidden');
    textEl.innerHTML = `<div class="flex items-center gap-2 text-cyan-500 animate-pulse text-xs"><svg class="animate-spin" width="14" height="14" fill="none" stroke="currentColor" stroke-width="2" viewBox="0 0 24 24"><circle cx="12" cy="12" r="10" stroke-opacity="0.25"/><path d="M12 2a10 10 0 0 1 10 10"/></svg><span id="voice-progress-text">Đang kết nối...</span></div>`;
  }
  if (streamBadge) streamBadge.classList.remove('hidden');
  if (stopBtn) stopBtn.classList.remove('hidden');
  if (ttfaBadge) {
    ttfaBadge.classList.add('hidden');
    ttfaBadge.textContent = '';
  }

  // Setup streaming audio queue & unlock AudioContext on user gesture
  _currentAudioStreamQueue = _newVoiceAudioQueue();
  _currentAudioStreamQueue.ensureContext();
  _currentAudioStreamQueue.onFirstAudio = () => {
    startWaveformVisualizer('voice-wave-bar');
  };
  _currentAudioStreamQueue.onPlaybackEnd = () => {
    stopWaveformVisualizer('voice-wave-bar');
  };

  const token = (typeof getAuthToken === 'function') ? getAuthToken() : '';
  const proto = location.protocol === 'https:' ? 'wss:' : 'ws:';
  const wsUrl = `${proto}//${location.host}/ws/v1/voice-stream${token ? '?token=' + encodeURIComponent(token) : ''}`;

  let wsFailed = false;
  let accumulatedText = "";
  const requestStartTime = performance.now();
  let firstAudioReceived = false;

  try {
    const ws = new WebSocket(wsUrl);
    ws.binaryType = 'arraybuffer';
    _currentVoiceWS = ws;

    const connectionTimeout = setTimeout(() => {
      if (ws.readyState !== WebSocket.OPEN) {
        console.warn('[VoiceStream] WebSocket connection timeout, fallback to REST.');
        try { ws.close(); } catch (e) {}
        wsFailed = true;
        _fallbackRestVoiceCommand(query, btn, card, textEl);
      }
    }, 4000);

    ws.onopen = () => {
      clearTimeout(connectionTimeout);
      if (btn) {
        btn.innerHTML = `<svg class="animate-spin" width="14" height="14" fill="none" stroke="currentColor" stroke-width="2" viewBox="0 0 24 24"><path d="M21 12a9 9 0 1 1-6.219-8.56"/></svg> Đang stream giọng nói...`;
      }
      const historyToSend = (typeof _webChatHistory !== 'undefined' && Array.isArray(_webChatHistory))
        ? _webChatHistory.slice(-_MAX_HISTORY_TURNS * 2)
        : [];
      ws.send(JSON.stringify({
        query: query,
        session_id: 'web',
        history: historyToSend,
      }));
    };

    ws.onmessage = async (event) => {
      if (typeof event.data === 'string') {
        try {
          const msg = JSON.parse(event.data);
          if (msg.type === 'status' && !accumulatedText) {
            // Máy chủ báo từng bước (định tuyến / suy nghĩ / đang đọc) — trước đây bị bỏ qua,
            // lệnh chạy công cụ vài giây mà màn hình chỉ có "đang kết nối".
            const label = { routing: 'Đang phân tích yêu cầu…', thinking: 'Đang suy nghĩ / chạy công cụ…', speaking: 'Đang đọc câu trả lời…' }[msg.status];
            const prog = document.getElementById('voice-progress-text');
            if (label && prog) prog.textContent = label;
          } else if (msg.type === 'text_delta' || msg.type === 'text_chunk') {
            accumulatedText += msg.content;
            if (textEl) {
              textEl.innerHTML = renderPortalMarkdown(accumulatedText);
              textEl.scrollTop = textEl.scrollHeight;
            }
          } else if (msg.type === 'tool_call') {
            const toolBadge = `\n\n> ⚙️ **Đang thực thi kỹ năng**: \`${msg.name}\`...\n\n`;
            accumulatedText += toolBadge;
            if (textEl) {
              textEl.innerHTML = renderPortalMarkdown(accumulatedText);
            }
          } else if (msg.type === 'audio_stream_complete' || msg.type === 'stream_end' || msg.type === 'cancelled') {
            if (btn) {
              _resetVoiceButton(btn);
            }
            const stopBtn = document.getElementById('btn-stop-voice-stream');
            if (stopBtn) stopBtn.classList.add('hidden');
            const streamBadge = document.getElementById('voice-stream-badge');
            if (streamBadge) streamBadge.classList.add('hidden');

            if (msg.type === 'cancelled') {
              if (_currentAudioStreamQueue) _currentAudioStreamQueue.stop();
              showToast('Lượt thoại đã được ngắt (Barge-In).', 'info');
              return;
            }
            if (_currentAudioStreamQueue) {
              _currentAudioStreamQueue.markStreamEnded();
            }
            if (typeof _webChatHistory !== 'undefined' && Array.isArray(_webChatHistory)) {
              _webChatHistory.push({ role: 'user', content: query });
              _webChatHistory.push({ role: 'assistant', content: accumulatedText });
              if (_webChatHistory.length > _MAX_HISTORY_TURNS * 2) {
                _webChatHistory = _webChatHistory.slice(-_MAX_HISTORY_TURNS * 2);
              }
            }
            renderVoiceTools(msg.tools || [], !!msg.requires_confirmation);
            // Save combined audio blob for replay & download
            const blob = _currentAudioStreamQueue.getCombinedBlob();
            if (blob) {
              const reader = new FileReader();
              reader.onloadend = () => {
                const base64data = reader.result.split(',')[1];
                lastAudioBase64 = base64data;
              };
              reader.readAsDataURL(blob);
            }
            scheduleAiDownloadCheck();
            const totalMs = Math.round(performance.now() - requestStartTime);
            const ttfaMs = msg.ttfa_ms || (firstAudioReceived ? Math.round(firstAudioReceived - requestStartTime) : null);
            if (ttfaBadge && ttfaMs) {
              ttfaBadge.textContent = `⚡ TTFA: ${ttfaMs}ms (Tổng: ${totalMs}ms)`;
              ttfaBadge.classList.remove('hidden');
            }
            showToast(`✅ Phản hồi hoàn tất (TTFA: ${ttfaMs ? ttfaMs + 'ms' : 'nhanh'})`, 'success');
          } else if (msg.type === 'error') {
            showToast(`⚠️ Lỗi: ${msg.message}`, 'error');
            if (btn) _resetVoiceButton(btn);
          }
        } catch (e) {
          console.warn('[VoiceStream] Parse error:', e);
        }
      } else if (event.data instanceof ArrayBuffer) {
        if (!firstAudioReceived) {
          firstAudioReceived = performance.now();
          const ttfa = Math.round(firstAudioReceived - requestStartTime);
          console.log(`[VoiceStream] First Audio Received (TTFA): ${ttfa}ms`);
          if (ttfaBadge) {
            ttfaBadge.textContent = `⚡ TTFA: ${ttfa}ms`;
            ttfaBadge.classList.remove('hidden');
          }
        }
        await _currentAudioStreamQueue.enqueueChunk(event.data);
      }
    };

    ws.onerror = (err) => {
      console.warn('[VoiceStream] WS Error:', err);
      clearTimeout(connectionTimeout);
      if (!wsFailed && accumulatedText.length === 0) {
        wsFailed = true;
        _fallbackRestVoiceCommand(query, btn, card, textEl);
      }
    };

    ws.onclose = () => {
      if (btn) _resetVoiceButton(btn);
    };

  } catch (err) {
    console.warn('[VoiceStream] Exception launching WS:', err);
    _fallbackRestVoiceCommand(query, btn, card, textEl);
  }
}

function playVoiceAudio() {
  if (!lastAudioBase64) {
    showToast('Chưa có âm thanh phản hồi để phát lại.', 'info');
    return;
  }
  if (_currentAudioStreamQueue) {
    _currentAudioStreamQueue.stop();
  }
  const audio = document.getElementById('audio-player');
  if (!audio) return;
  audio.src = `data:audio/mp3;base64,${lastAudioBase64}`;
  audio.onplay = () => startWaveformVisualizer('voice-wave-bar');
  audio.onpause = () => stopWaveformVisualizer('voice-wave-bar');
  audio.onended = () => stopWaveformVisualizer('voice-wave-bar');
  audio.play().catch(e => console.warn('Lỗi phát audio:', e));
}

function stopVoiceAudio() {
  if (_currentAudioStreamQueue) {
    _currentAudioStreamQueue.stop();
  }
  const audio = document.getElementById('audio-player');
  if (audio) {
    audio.pause();
    audio.currentTime = 0;
  }
  stopWaveformVisualizer('voice-wave-bar');
}

function downloadVoiceMP3() {
  if (!lastAudioBase64) {
    showToast('Chưa có âm thanh phản hồi để tải xuống.', 'warning');
    return;
  }
  const a = document.createElement('a');
  a.href = `data:audio/mp3;base64,${lastAudioBase64}`;
  a.download = `VNMate_Voice_${Date.now()}.mp3`;
  document.body.appendChild(a);
  a.click();
  document.body.removeChild(a);
  showToast('⬇️ Đang tải file MP3 câu trả lời...', 'success');
}

function copyVoiceResponseText() {
  const textEl = document.getElementById('voice-response-text');
  const txt = textEl?.textContent?.trim();
  if (!txt) {
    showToast('Chưa có phản hồi để sao chép.', 'info');
    return;
  }
  navigator.clipboard.writeText(txt).then(() => {
    showToast('📋 Đã sao chép phản hồi vào Clipboard!', 'success');
  }).catch(() => {
    showToast('⚠️ Không thể sao chép vào Clipboard.', 'warning');
  });
}

async function quickPlayTTS() {
  try {
    showToast('Đang tổng hợp giọng nói Hoài My Neural...', 'info');
    const url = await apiTTS('Xin chào! Em là trợ lý VN-MateAI, hệ thống tự động hóa đang hoạt động rất tốt.');
    const audio = document.getElementById('audio-player');
    if (audio) {
      audio.src = url;
      audio.onplay = () => startWaveformVisualizer('voice-wave-bar');
      audio.onpause = () => stopWaveformVisualizer('voice-wave-bar');
      audio.onended = () => stopWaveformVisualizer('voice-wave-bar');
      audio.play();
    }
  } catch (err) {
    showToast(`Lỗi TTS: ${err.message}`, 'error');
  }
}

// ═══════════════════════════════════════════════════════════════════════════
// ── EDGE-TTS STUDIO & VOICE SYNTHESIZER ────────────────────────────────────
// ═══════════════════════════════════════════════════════════════════════════

async function loadVoiceStudioVoices() {
  const btn = document.getElementById('btn-load-studio-voices');
  const studioSelect = document.getElementById('studio-tts-voice');
  const voiceCountLabel = document.getElementById('studio-voice-count');
  const ribbonCount = document.getElementById('voice-stat-tts-count');

  if (btn) {
    btn.disabled = true;
    btn.innerHTML = `<svg class="animate-spin" width="12" height="12" fill="none" stroke="currentColor" stroke-width="2" viewBox="0 0 24 24"><path d="M21 12a9 9 0 1 1-6.219-8.56"/></svg> Đang nạp...`;
  }

  try {
    const res = await apiFetch(`${API_BASE}/api/v1/tts/voices`);
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    const data = await res.json();
    if (!data.success || !data.voices?.length) throw new Error(data.error || 'No voices');

    _allTTSVoices = data.voices;
    const currentStudio = studioSelect?.value || 'vi-VN-HoaiMyNeural';

    _renderVoiceDropdown(data.voices, currentStudio, 'studio-tts-voice');

    if (voiceCountLabel) voiceCountLabel.textContent = `${data.total} Giọng ✓`;
    if (ribbonCount) ribbonCount.textContent = `${data.total} GIỌNG`;
    if (btn) {
      btn.disabled = false;
      btn.innerHTML = `
        <svg width="13" height="13" fill="none" stroke="currentColor" stroke-width="2" viewBox="0 0 24 24">
          <polyline points="20 6 9 17 4 12" />
        </svg>
        <span>Đã Nạp ${data.total} Giọng</span>
      `;
    }
  } catch (e) {
    console.warn('[Studio] Lỗi nạp danh sách giọng:', e);
    if (btn) {
      btn.disabled = false;
      btn.innerHTML = `
        <svg width="13" height="13" fill="none" stroke="currentColor" stroke-width="2" viewBox="0 0 24 24">
          <path d="M21.5 2v6h-6M2.5 22v-6h6M2 11.5a10 10 0 0 1 18.8-4.3M22 12.5a10 10 0 0 1-18.8 4.2"/>
        </svg>
        <span>Tải Lại Giọng</span>
      `;
    }
  }
}

function filterStudioVoices(query) {
  if (!_allTTSVoices.length) return;
  const q = query.toLowerCase().trim();
  const filtered = q ? _allTTSVoices.filter(v =>
    v.short_name.toLowerCase().includes(q) ||
    v.friendly_name.toLowerCase().includes(q) ||
    v.locale.toLowerCase().includes(q) ||
    (v.gender || '').toLowerCase().includes(q)
  ) : _allTTSVoices;

  const studioSelect = document.getElementById('studio-tts-voice');
  const current = studioSelect?.value;
  const countLabel = document.getElementById('studio-voice-count');
  _renderVoiceDropdown(filtered, current, 'studio-tts-voice');
  if (countLabel) countLabel.textContent = `${filtered.length} giọng phù hợp`;
}

function loadSampleStudioText() {
  const samples = [
    'Xin chào quý khách! Hệ thống tự động hóa VN-MateAI đã sẵn sàng hỗ trợ điều phối mạng lưới, quản lý tác vụ thông minh và bảo mật dữ liệu doanh nghiệp.',
    'Chào bạn! Mình là Hoài My, trợ lý ảo giọng nói AI. Mọi tác vụ từ điều khiển máy trạm, báo cáo KPI đến kiểm tra an ninh mạng đều đã hoàn thành xuất sắc!',
    'Hệ thống phát hiện mức tải CPU trung bình toàn hệ thống là mười lăm phần trăm. Tất cả ba mươi bảy kỹ năng tự động hóa đang trong trạng thái sẵn sàng.',
    'Welcome to VN-MateAI Automation Suite. Real-time telemetry, neural voice assistant, and enterprise zero-trust security are active.'
  ];
  const input = document.getElementById('studio-text-input');
  if (!input) return;
  const current = input.value;
  let next = samples[0];
  const idx = samples.indexOf(current);
  if (idx >= 0 && idx < samples.length - 1) {
    next = samples[idx + 1];
  }
  input.value = next;
  showToast('✍️ Đã chèn văn bản mẫu thử giọng', 'info');
}

async function previewStudioVoice() {
  const textInput = document.getElementById('studio-text-input');
  const voiceSelect = document.getElementById('studio-tts-voice');
  const rateSlider = document.getElementById('studio-rate-slider');
  const btn = document.getElementById('btn-studio-play');

  const text = textInput?.value?.trim();
  if (!text) {
    showToast('Vui lòng nhập văn bản cần nghe thử.', 'warning');
    return;
  }

  const voice = voiceSelect?.value || 'vi-VN-HoaiMyNeural';
  const rateVal = parseInt(rateSlider?.value || 0, 10);
  const rate = (rateVal >= 0 ? `+${rateVal}%` : `${rateVal}%`);

  if (btn) {
    btn.disabled = true;
    btn.innerHTML = `<svg class="animate-spin" width="14" height="14" fill="none" stroke="currentColor" stroke-width="2" viewBox="0 0 24 24"><path d="M21 12a9 9 0 1 1-6.219-8.56"/></svg> Đang tổng hợp...`;
  }

  // Dừng voice stream queue nếu đang chạy để tránh 2 giọng phát cùng lúc
  if (_currentAudioStreamQueue) {
    _currentAudioStreamQueue.stop();
  }

  try {
    const res = await apiFetch(`${API_BASE}/api/v1/tts`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ text, voice, rate }),
    });

    if (!res.ok) throw new Error(`Tổng hợp giọng nói thất bại (${res.status})`);
    const blob = await res.blob();
    if (lastStudioAudioUrl) {
      URL.revokeObjectURL(lastStudioAudioUrl);
    }
    lastStudioAudioUrl = URL.createObjectURL(blob);

    const audio = document.getElementById('studio-audio-player');
    if (audio) {
      audio.src = lastStudioAudioUrl;
      audio.onplay = () => startStudioWaveform();
      audio.onpause = () => stopStudioWaveform();
      audio.onended = () => stopStudioWaveform();
      audio.play().catch(e => console.warn('Lỗi phát audio studio:', e));
    }
    showToast(`🔊 Đang phát giọng đọc "${voice}" (${rate})`, 'success');
  } catch (err) {
    console.error('[Studio] Lỗi TTS:', err);
    showToast(`❌ Lỗi tổng hợp giọng nói: ${err.message}`, 'error');
  } finally {
    if (btn) {
      btn.disabled = false;
      btn.innerHTML = `
        <svg width="14" height="14" fill="none" stroke="currentColor" stroke-width="2.5" viewBox="0 0 24 24">
          <polygon points="5 3 19 12 5 21 5 3"/>
        </svg>
        <span>Tổng Hợp & Nghe Thử</span>
      `;
    }
  }
}

function downloadStudioVoiceMP3() {
  if (!lastStudioAudioUrl) {
    showToast('Vui lòng bấm "Tổng Hợp & Nghe Thử" trước khi tải MP3.', 'warning');
    return;
  }
  const a = document.createElement('a');
  a.href = lastStudioAudioUrl;
  a.download = `VNMate_Studio_Voice_${Date.now()}.mp3`;
  document.body.appendChild(a);
  a.click();
  document.body.removeChild(a);
  showToast('⬇️ Đang tải file MP3...', 'success');
}

// ═══════════════════════════════════════════════════════════════════════════
// ── PHASE 20: DYNAMIC CLIENT AGENT DISTRIBUTION ──────────────────────────────
// ═══════════════════════════════════════════════════════════════════════════

// ── Agent máy trạm: tải gói theo nền tảng, mã đăng ký riêng, quản lý máy ──────
//
// Mỗi lần tải là một MÃ ĐĂNG KÝ DÙNG MỘT LẦN cho MỘT máy (hết hạn 7 ngày) — không
// còn secret chung cho mọi máy. Máy đã đăng ký thu hồi được riêng từng máy.
// Zero-Trust: gọi bằng fetch + header Authorization, không nhúng token vào URL.

const AGENT_PLATFORMS = [
  { id: 'windows', pkg: 'windows-exe', label: 'Windows (.exe)', hint: 'Một file, không cần Python. Bấm đúp là tự cài.' },
  { id: 'macos', pkg: 'macos-bin', label: 'macOS', hint: 'Bản build cho Mac; chưa build thì là gói Python + install_agent_macos.sh.' },
  { id: 'source', pkg: 'source', label: 'Python (mọi hệ điều hành)', hint: 'Cần Python 3.10+. Cài bằng install_agent.bat / install_agent_macos.sh.' },
];

async function _agentApi(path, opts = {}) {
  const resp = await fetch(path, {
    ...opts,
    headers: { 'Authorization': `Bearer ${getAuthToken()}`, ...(opts.body ? { 'Content-Type': 'application/json' } : {}) },
  });
  if (!resp.ok) {
    let detail = `HTTP ${resp.status}`;
    try { const b = await resp.json(); if (b && b.detail) detail = b.detail; } catch (_) { /* không phải JSON */ }
    throw new Error(detail);
  }
  return resp;
}

async function downloadClientAgent() {
  if (!getAuthToken()) {
    showToast('⚠️ Vui lòng đăng nhập trước khi tải Client Agent.', 'warning');
    return;
  }
  let box = document.getElementById('agent-dialog');
  if (!box) {
    box = document.createElement('div');
    box.id = 'agent-dialog';
    box.className = 'fixed inset-0 z-[90] flex items-center justify-center bg-black/50 p-4';
    box.addEventListener('click', (e) => { if (e.target === box) box.remove(); });
    document.body.appendChild(box);
  }
  box.innerHTML =
    `<div class="w-full max-w-3xl max-h-[90vh] overflow-y-auto rounded-2xl bg-white dark:bg-slate-900 border border-slate-200 dark:border-slate-700 shadow-2xl p-5 text-sm text-slate-700 dark:text-slate-200">` +
    `<div class="flex items-center mb-3"><h3 class="text-base font-bold">Agent máy trạm</h3>` +
    `<button class="ml-auto text-slate-400 hover:text-rose-500 text-lg" onclick="document.getElementById('agent-dialog').remove()">✕</button></div>` +
    `<div id="agent-dialog-body" class="text-xs text-slate-400 italic">Đang tải danh sách…</div></div>`;
  await _renderAgentDialog();
}

async function _renderAgentDialog(newCode) {
  const body = document.getElementById('agent-dialog-body');
  if (!body) return;
  let data;
  try {
    data = await (await _agentApi('/api/v1/agent/devices')).json();
  } catch (err) {
    body.innerHTML = `<div class="text-rose-500">Không tải được: ${_esc(friendlyErrorText(err))}</div>`;
    return;
  }
  const pk = data.packages || {};
  const platforms = AGENT_PLATFORMS.map((p) => {
    const info = pk[p.pkg] || {};
    const ok = info.available;
    const note = ok ? `bản ${_esc(info.version)}` : (p.id === 'source' ? 'không có' : 'chưa build — sẽ tải gói Python');
    return `<label class="flex items-start gap-2 p-2 rounded-lg border border-slate-200 dark:border-slate-700 cursor-pointer">` +
      `<input type="radio" name="agent-platform" value="${p.id}" ${p.id === 'windows' ? 'checked' : ''} class="mt-0.5">` +
      `<span><b>${_esc(p.label)}</b> <span class="text-[10px] ${ok ? 'text-emerald-500' : 'text-amber-500'}">${note}</span>` +
      `<br><span class="text-[10px] text-slate-400">${_esc(p.hint)}</span></span></label>`;
  }).join('');

  const fmtT = (t) => fmtServerTime(t, { seconds: false });
  const devices = (data.devices || []).map((d) => {
    const st = d.revoked_at ? '<span class="text-rose-500">đã thu hồi</span>'
      : (d.online ? '<span class="text-emerald-500">trực tuyến</span>' : '<span class="text-slate-400">ngoại tuyến</span>');
    const m = d.metrics || {};
    const metr = d.online && m.cpu_percent != null ? `CPU ${Math.round(m.cpu_percent)}% · RAM ${Math.round(m.ram_percent)}%` : '';
    return `<tr class="border-t border-slate-100 dark:border-slate-800">` +
      `<td class="py-1 pr-2 font-mono">${_esc(d.client_id)}<div class="text-[10px] text-slate-400">${_esc(d.label || d.hostname || '')}</div></td>` +
      `<td class="pr-2">${st}<div class="text-[10px] text-slate-400">${_esc(metr)}</div></td>` +
      `<td class="pr-2">${_esc(d.agent_version || '—')}<div class="text-[10px] text-slate-400">${_esc(d.package || '')}</div></td>` +
      `<td class="pr-2 text-[10px]">${fmtT(d.last_seen_at)}</td>` +
      `<td>${d.revoked_at ? '' : `<button class="px-2 py-0.5 rounded bg-rose-500/10 text-rose-500 hover:bg-rose-500/20 text-[10px] font-bold" onclick="revokeAgentDevice('${_esc(d.client_id)}')">Thu hồi</button>`}</td></tr>`;
  }).join('') || '<tr><td colspan="5" class="py-2 text-slate-400 italic">Chưa có máy trạm nào đăng ký.</td></tr>';

  const stateText = { waiting: 'chờ dùng', used: 'đã dùng', expired: 'hết hạn' };
  const codes = (data.codes || []).slice(0, 15).map((c) =>
    `<tr class="border-t border-slate-100 dark:border-slate-800"><td class="py-1 pr-2 font-mono text-[10px]">${_esc(c.code_ref)}…</td>` +
    `<td class="pr-2">${_esc(c.label || '—')}</td><td class="pr-2">${stateText[c.state] || c.state}` +
    `${c.used_by_client ? ` → <span class="font-mono">${_esc(c.used_by_client)}</span>` : ''}</td>` +
    `<td class="pr-2 text-[10px]">${fmtT(c.expires_at)}</td>` +
    `<td>${c.state === 'waiting' ? `<button class="text-[10px] text-rose-500 hover:underline" onclick="deleteAgentEnrollCode('${_esc(c.code_ref)}')">Huỷ</button>` : ''}</td></tr>`
  ).join('') || '<tr><td colspan="5" class="py-2 text-slate-400 italic">Chưa có mã nào.</td></tr>';

  body.className = '';
  body.innerHTML =
    `<div class="grid sm:grid-cols-3 gap-2 mb-2">${platforms}</div>` +
    `<div class="flex gap-2 items-center mb-1">` +
    `<input id="agent-label" maxlength="80" placeholder="Tên gợi nhớ máy (vd: Kế toán - Lan)" class="flex-1 px-2 py-1.5 rounded-lg border border-slate-200 dark:border-slate-700 bg-transparent text-xs">` +
    `<button onclick="downloadAgentPackage(this)" class="px-3 py-1.5 rounded-lg bg-gradient-to-r from-primary-600 to-blue-600 text-white text-xs font-bold">⬇ Tải gói cho 1 máy</button></div>` +
    `<p class="text-[10px] text-slate-400 mb-4">Mỗi gói chứa MỘT mã đăng ký dùng một lần (hết hạn 7 ngày). Máy thứ hai cần tải gói khác. Agent tự cập nhật khi máy chủ có bản mới.</p>` +
    `<h4 class="font-bold text-xs mb-1">Máy trạm đã đăng ký</h4>` +
    `<table class="w-full text-xs mb-4"><thead class="text-[10px] text-slate-400 text-left"><tr><th>Máy</th><th>Trạng thái</th><th>Phiên bản</th><th>Lần cuối</th><th></th></tr></thead><tbody>${devices}</tbody></table>` +
    `<div class="flex items-center mb-1"><h4 class="font-bold text-xs">Mã đăng ký</h4>` +
    `<button onclick="createAgentEnrollCode()" class="ml-auto px-2 py-0.5 rounded bg-cyan-500/10 text-cyan-600 dark:text-cyan-300 text-[10px] font-bold">+ Tạo mã (cài thủ công)</button></div>` +
    (newCode ? `<div class="mb-2 p-2 rounded-lg bg-amber-50 dark:bg-amber-900/30 border border-amber-400/50 text-[11px]">Mã mới (chỉ hiện MỘT lần): ` +
      `<code class="select-all font-mono">${_esc(newCode)}</code><br><span class="text-[10px]">Điền vào <code>"enroll_code"</code> trong config.json của máy cần đăng ký.</span></div>` : '') +
    `<table class="w-full text-xs"><thead class="text-[10px] text-slate-400 text-left"><tr><th>Mã</th><th>Nhãn</th><th>Trạng thái</th><th>Hết hạn</th><th></th></tr></thead><tbody>${codes}</tbody></table>`;
}

async function downloadAgentPackage(btn) {
  const platform = (document.querySelector('input[name="agent-platform"]:checked') || {}).value || 'source';
  const label = (document.getElementById('agent-label') || {}).value || '';
  if (btn) { btn.disabled = true; btn.textContent = 'Đang đóng gói…'; }
  try {
    const resp = await _agentApi(`/api/v1/download-agent?platform=${encodeURIComponent(platform)}&label=${encodeURIComponent(label)}`);
    const cd = resp.headers.get('Content-Disposition') || '';
    const m = cd.match(/filename="([^"]+)"/);
    const blob = await resp.blob();
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = m ? m[1] : 'VN-Mate_Agent.zip';
    a.style.display = 'none';
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
    setTimeout(() => URL.revokeObjectURL(url), 10000);
    const pkg = resp.headers.get('X-Agent-Package');
    showToast(platform !== 'source' && pkg === 'source'
      ? '⬇️ Chưa có bản build cho nền tảng này — đã tải gói Python (kèm script cài).'
      : '⬇️ Đã tải gói Agent (mã đăng ký dùng một lần cho một máy).', 'success');
    await _renderAgentDialog();
  } catch (err) {
    showToast(`❌ Tải Client Agent thất bại: ${err.message || err}`, 'error');
  } finally {
    if (btn) { btn.disabled = false; btn.textContent = '⬇ Tải gói cho 1 máy'; }
  }
}

async function revokeAgentDevice(clientId) {
  if (!confirm(`Thu hồi khoá của máy "${clientId}"? Máy bị ngắt ngay và phải cài lại bằng mã mới.`)) return;
  try {
    await _agentApi(`/api/v1/agent/devices/${encodeURIComponent(clientId)}/revoke`, { method: 'POST' });
    showToast(`✔ Đã thu hồi máy ${clientId}`, 'success');
  } catch (err) {
    showToast(`✖ Thu hồi thất bại: ${err.message}`, 'error');
  }
  await _renderAgentDialog();
}

async function createAgentEnrollCode() {
  const label = prompt('Tên gợi nhớ cho máy sẽ dùng mã này (tuỳ chọn):', '') ?? null;
  if (label === null) return;
  try {
    const d = await (await _agentApi('/api/v1/agent/enroll-codes', { method: 'POST', body: JSON.stringify({ label }) })).json();
    await _renderAgentDialog(d.code);
  } catch (err) {
    showToast(`✖ Tạo mã thất bại: ${err.message}`, 'error');
  }
}

async function deleteAgentEnrollCode(ref) {
  try {
    await _agentApi(`/api/v1/agent/enroll-codes/${encodeURIComponent(ref)}`, { method: 'DELETE' });
  } catch (err) {
    showToast(`✖ Huỷ mã thất bại: ${err.message}`, 'error');
  }
  await _renderAgentDialog();
}

Object.assign(window, { downloadClientAgent, downloadAgentPackage, revokeAgentDevice, createAgentEnrollCode, deleteAgentEnrollCode });

// ═══════════════════════════════════════════════════════════════════════════
// ── LƯU CẤU HÌNH TOÀN DIỆN (TAB CẤU HÌNH) ──────────────────────────────────
// ═══════════════════════════════════════════════════════════════════════════

// Ký hiệu server dùng cho trường bí mật chưa có giá trị thật.
// Phải khớp byte-for-byte với `_SECRET_MASK` trong core/server.py.
const SECRET_MASK = '••••••••';

/**
 * Giá trị an toàn để đổ vào ô nhập.
 *
 * Máy chủ trả khoá đã che (`••••••••`) — đúng, không bao giờ gửi khoá thật ra
 * trình duyệt. Nhưng nếu điền ký hiệu che vào ô mật khẩu thì ô đó chứa rác,
 * và mọi chỗ đọc ô đó để gọi API sẽ gửi ký hiệu che đi như khoá thật.
 *
 * Lỗi đã xảy ra: ô "MÃ BẢO MẬT (API KEY)" nhận `••••••••`, bấm "Tải model
 * từ 9router" thì máy chủ báo
 *   'ascii' codec can't encode characters in position 7-14
 * vì 8 ký tự `•` không encode được trong HTTP header. Người dùng tưởng
 * 9router hỏng, trong khi proxy vẫn trả về 31 model bình thường.
 *
 * Ô bí mật LUÔN để trống. Ô trống nghĩa là "giữ khoá đang lưu" — đúng quy ước
 * đã dùng ở form cấu hình connector.
 */
function _ccSafeField(val) {
  return val === SECRET_MASK ? '' : val;
}

/**
 * Báo trạng thái của một ô mật khẩu: đã có khoá chưa, vừa lưu xong không.
 *
 * Vì sao cần (người dùng phản ánh: "dán API key vào rồi bấm F5 là mất"):
 * Khoá KHÔNG BAO GIỜ được gửi về trình duyệt — đúng, phải vậy. Nhưng ô mật
 * khẩu luôn trống sau mỗi lần tải trang, nên nhìn y hệt lúc chưa lưu. Đo được:
 * dán khoá → bấm Lưu → kiểm tra config.json thì khoá CÓ trong đó, nhưng sau
 * F5 ô trắng xóa. Người dùng không có cách nào phân biệt "đã lưu" với
 * "chưa lưu", nên dán lại y hệt lần trước.
 *
 * Nay ô trống vẫn kèm hai dấu hiệu: placeholder và dòng gợi ý nói rõ đã có
 * khoá, cộng thêm xác nhận ngay sau khi bấm Lưu.
 *
 * Dùng chung một hàm cho mọi ô để ba nơi không lệch nhau — trước đây chỉ ô
 * ở tab Cấu Hình có dòng gợi ý, hai ô ở tab Trợ lý AI không có.
 *
 * @param {string} inputId  id của ô mật khẩu
 * @param {string} hintId   id của dòng gợi ý (bỏ trống = không có)
 * @param {boolean} hasKey  máy chủ đang lưu khoá cho ô này
 * @param {string} sample   gợi ý định dạng, ví dụ "sk-..."
 * @param {boolean} justSaved vừa bấm Lưu xong (đổi câu chữ cho dễ nhận ra)
 */
// Máy chủ đang lưu khoá hay không — nhớ lại sau mỗi lần nạp cấu hình. Cần cho
// lúc bấm Lưu: người dùng để ô trống thì máy chủ giữ khoá cũ, nhưng phía
// giao diện không có gì để khẳng định là còn.
const _ccLastKnownHasKey = { llm: false, groq: false };

function _ccHasStoredKey(block) {
  /*
   * Máy chủ có đang lưu khoá cho khối cấu hình này không?
   *
   * CỐ Ý KHÔNG bỏ ký hiệu che trước khi kiểm. Bản đầu của hàm này có làm vậy
   * và luôn trả về false: máy chủ chỉ thay khoá thật bằng ký hiệu khi bản lưu
   * CÓ giá trị, nên `••••••••` chính là bằng chứng "đã có khoá". Bỏ nó đi rồi
   * hỏi "còn gì không" thì không bao giờ có. Ô trống mới là lúc không có khoá.
   */
  if (!block || typeof block !== "object") return false;
  const k = block.api_key;
  if (typeof k === "string" && k.trim() !== "") return true;
  if (Array.isArray(block.api_keys)) {
    return block.api_keys.some((x) => typeof x === "string" && x.trim() !== "");
  }
  return false;
}

function _ccSecretStatus(inputId, hintId, hasKey, sample, justSaved) {
  const input = document.getElementById(inputId);
  if (input) {
    if (hasKey) {
      input.value = SECRET_MASK;
      input.setAttribute('data-masked', '1');
    } else if (!justSaved) {
      input.value = '';
      input.removeAttribute('data-masked');
    }
    input.placeholder = hasKey
      ? `${sample} — đã có khoá lưu sẵn (••••••••), gõ mới nếu muốn đổi`
      : sample;

    if (!input._hasMaskHandlers) {
      input._hasMaskHandlers = true;
      input.addEventListener('focus', function () {
        if (this.value === SECRET_MASK) {
          this.select();
        }
      });
      input.addEventListener('blur', function () {
        if (!this.value.trim() && this.getAttribute('data-masked') === '1') {
          this.value = SECRET_MASK;
        }
      });
    }
  }
  // Badge element: show when key exists or just saved
  const badge = document.getElementById(inputId + '-badge');
  if (badge) {
    if (hasKey || justSaved) {
      badge.classList.remove('hidden');
    } else {
      badge.classList.add('hidden');
    }
  }
  const hint = hintId ? document.getElementById(hintId) : null;
  if (!hint) return;
  if (justSaved) {
    hint.textContent = '✔ Đã lưu khoá an toàn (hiển thị ••••••••).';
    hint.className = 'text-[10px] text-emerald-600 dark:text-emerald-400';
  } else if (hasKey) {
    hint.textContent = '✔ Đã có khoá.';
    hint.className = 'text-[10px] text-emerald-600 dark:text-emerald-400';
  } else {
    hint.textContent = 'Chưa có khoá nào được lưu.';
    hint.className = 'text-[10px] text-amber-600 dark:text-amber-400 italic';
  }
}

async function loadConfig() {
  const cfg = await apiGetConfig();
  if (!cfg) return;
  currentConfig = cfg;
  loadRouterModels();

  const setVal = (id, val) => {
    const el = document.getElementById(id);
    if (el && val !== undefined && val !== null) el.value = _ccSafeField(val);
  };

  const setKeysVal = (id, target) => {
    const el = document.getElementById(id);
    if (!el || !target) return;
    let keys = [];
    if (Array.isArray(target.api_keys) && target.api_keys.length > 0) {
      keys = target.api_keys;
    } else if (target.api_key) {
      keys = [target.api_key];
    }
    // Lọc TỪNG khoá chứ không so sánh cả chuỗi: 3 khoá đã che nối bằng dấu
    // xuống dòng tạo ra chuỗi `••••••••\n••••••••\n••••••••` — khác hẳn
    // SECRET_MASK nên so sánh chuỗi sẽ bỏ sót.
    keys = keys.filter((k) => k !== SECRET_MASK && k !== undefined && k !== null);
    el.value = keys.join('\n');
  };

  // Phase 22: Thin Client 9router config
  const llm = cfg.llm || {};
  const routing = cfg.routing || cfg.router || {};
  const primary = routing.primary || {};

  const baseUrl = llm.base_url || primary.api_base || cfg.BASE_URL || 'http://localhost:20128/v1';
  const modelName = llm.model_name || primary.provider_model || primary.model || cfg.MODEL_NAME || '';
  const apiKey = llm.api_key || (primary.api_keys && primary.api_keys[0]) || primary.api_key || cfg.API_KEY || '';

  // Populate Thin Client 9router form
  setVal('cfg-llm-base', baseUrl);
  setVal('cfg-llm-model', modelName);

  // Ô khoá: để TRỐNG và ghi chú bên dưới, không điền ký hiệu vào.
  //
  // Phase 79: server không còn trả khoá thật. Trước đây `setVal` đổ thẳng giá
  // trị vào `value`, tức bí mật nằm sẵn trong DOM của trang — ai mở
  // DevTools là thấy, kể cả khi server đã che. Nay để trống: người dùng thấy
  // placeholder "chưa nhập", gõ khoá mới thì mới ghi đè, bỏ trống khi Lưu thì
  // server giữ khoá cũ (xem `_restore_masked_secrets`).
  const keyEl = document.getElementById('cfg-llm-key');
  if (keyEl) keyEl.value = '';
  // Dùng hàm chung để hai tab báo trạng thái giống hệt nhau.
  _ccLastKnownHasKey.llm = _ccHasStoredKey(llm);
  _ccSecretStatus('cfg-llm-key', 'cfg-llm-key-hint', _ccLastKnownHasKey.llm, 'sk-...');
  const chainEl = document.getElementById('cfg-chain-primary');
  if (chainEl) chainEl.textContent = modelName;

  // Sync to legacy hidden inputs
  setVal('cfg-route-primary-model', modelName);
  setVal('cfg-route-primary-base', baseUrl);
  const primaryKeyEl = document.getElementById('cfg-route-primary-key');
  if (primaryKeyEl) primaryKeyEl.value = '';

  // ── Phase 91: Nạp trạng thái Dual-Mode cho Tab Config
  const routingMode = llm.routing_mode || 'router';
  const directUrl = llm.direct_url || 'https://api.deepseek.com';
  const directModel = llm.direct_model || 'deepseek-chat';
  setVal('cfg-direct-url', directUrl);
  setVal('cfg-direct-model', directModel);
  // Như ô khoá 9Router: server đã che khoá, nên để trống; bỏ trống khi Lưu thì
  // server giữ khoá cũ.
  setVal('cfg-direct-key', '');
  switchCfgRoutingMode(routingMode);

  // Nhận diện và highlight provider trên tab Config
  const uCfg = (directUrl || '').toLowerCase();
  let matchedProvCfg = 'deepseek';
  if (uCfg.includes('groq')) matchedProvCfg = 'groq';
  else if (uCfg.includes('openai')) matchedProvCfg = 'openai';
  else if (uCfg.includes('openrouter')) matchedProvCfg = 'openrouter';
  else if (uCfg.includes('11434') || uCfg.includes('ollama')) matchedProvCfg = 'ollama';
  else if (uCfg.includes('1234') || uCfg.includes('lmstudio')) matchedProvCfg = 'lmstudio';
  else if (uCfg.includes('deepseek')) matchedProvCfg = 'deepseek';

  const provCfg = DIRECT_PROVIDERS[matchedProvCfg];
  if (provCfg) {
    const badge = document.getElementById('cfg-direct-provider-badge');
    if (badge) badge.textContent = provCfg.name;
    const guideLink = document.getElementById('cfg-direct-token-guide-link');
    if (guideLink) {
      guideLink.href = provCfg.guideLink;
      guideLink.innerHTML = `<span>${provCfg.guideText}</span> <svg width="10" height="10" fill="none" stroke="currentColor" stroke-width="2" viewBox="0 0 24 24"><path d="M18 13v6a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h6"/><polyline points="15 3 21 3 21 9"/><line x1="10" y1="14" x2="21" y2="3"/></svg>`;
    }
    const hint = document.getElementById('cfg-direct-key-hint');
    if (hint) hint.textContent = provCfg.hint;
    Object.keys(DIRECT_PROVIDERS).forEach(k => {
      const btn = document.getElementById(`btn-cfg-prov-${k}`);
      if (!btn) return;
      if (k === matchedProvCfg) {
        btn.className = 'provider-chip py-2 px-2.5 rounded-xl border text-center transition-all duration-150 flex flex-col items-center gap-1 border-orange-400/80 bg-orange-500/15 text-orange-400 font-bold shadow-sm';
      } else {
        btn.className = 'provider-chip py-2 px-2.5 rounded-xl border border-slate-200 dark:border-white/10 bg-slate-50 dark:bg-white/[0.02] text-slate-700 dark:text-slate-300 hover:border-orange-400/50 text-center transition-all duration-150 flex flex-col items-center gap-1 font-semibold';
      }
    });
  }

  // Auto Execute Switch
  const isAuto = cfg.auto_execute !== undefined ? !!cfg.auto_execute : !!cfg.AUTO_EXECUTE_UNVERIFIED_CODE;
  autoExecState = isAuto;
  const cfgSwitch = document.getElementById('cfg-switch-autoexec');
  if (cfgSwitch) {
    if (isAuto) cfgSwitch.classList.add('on');
    else cfgSwitch.classList.remove('on');
  }

  // System & ASR settings
  //
  // `cfg-groq-key` cũng là bí mật: server trả về ký hiệu chỗ trống. Để trống
  // ô như ô khoá LLM — điền ký hiệu vào là đưa bí mật (dù đã che) vào DOM.
  setVal('cfg-groq-key', '');
  _ccLastKnownHasKey.groq = _ccHasStoredKey({ api_key: cfg.GROQ_API_KEY });
  _ccSecretStatus('cfg-groq-key', 'cfg-groq-key-hint', _ccLastKnownHasKey.groq, 'gsk_...');
  setVal('cfg-groq-url', cfg.GROQ_BASE_URL || '');
  setVal('cfg-asr', cfg.ASR_BACKEND || 'google');
  setVal('cfg-loglevel', cfg.LOG_LEVEL || 'INFO');

  // Phase 18: Telegram Config
  const tg = cfg.telegram || {};
  setVal('cfg-tg-token', '');
  setVal('cfg-tg-admins', Array.isArray(tg.admin_chat_ids) ? tg.admin_chat_ids.join(', ') : (tg.admin_chat_ids || ''));
  setVal('cfg-tg-group', tg.incident_group_id || '');
  loadTelegramConfig();
  loadTelegramStatus();
  loadADSyncStatus();
  loadDomainStats();
  populateReportTemplates(cfg.report_templates);
}

async function saveFullConfig() {
  const btn1 = document.getElementById('btn-save-full-cfg');
  const btn2 = document.getElementById('btn-save-full-cfg-bottom');
  const setLoading = (loading) => {
    [btn1, btn2].forEach(b => {
      if (!b) return;
      b.disabled = loading;
      b.innerHTML = loading
        ? `<svg class="animate-spin" width="16" height="16" fill="none" stroke="currentColor" stroke-width="2" viewBox="0 0 24 24"><path d="M21 12a9 9 0 1 1-6.219-8.56"/></svg> Đang lưu cấu hình...`
        : `<svg width="16" height="16" fill="none" stroke="currentColor" stroke-width="2.5" viewBox="0 0 24 24"><polyline points="20 6 9 17 4 12"/></svg> Lưu Cấu Hình`;
    });
  };

  setLoading(true);

  const getVal = id => {
    const el = document.getElementById(id);
    return el ? el.value.trim() : '';
  };

  const autoExecEl = document.getElementById('cfg-switch-autoexec');
  const isAutoExec = autoExecEl ? autoExecEl.classList.contains('on') : autoExecState;

  const baseUrl = getVal('cfg-llm-base') || getVal('cfg-route-primary-base') || 'http://localhost:20128/v1';
  const modelName = getVal('cfg-llm-model') || getVal('cfg-route-primary-model') || '';

  // Phase 73: KHÔNG tự điền khoá giả. Trước đây bỏ trống ô khoá rồi bấm "Lưu"
  // sẽ ghi chuỗi "sk-dummy" vào config.json, đè lên khoá thật đang chạy được.
  // Nay nếu người dùng không nhập gì thì giữ nguyên khoá đang lưu.
  const typedKey = getVal('cfg-llm-key') || getVal('cfg-route-primary-key');
  const existingKey = currentConfig?.llm?.api_key || currentConfig?.routing?.primary?.api_key || '';
  const apiKeyLeftUntouched = !typedKey || typedKey === SECRET_MASK;
  const apiKey = apiKeyLeftUntouched ? existingKey : typedKey;

  const tgAdminsRaw = getVal('cfg-tg-admins');
  const tgAdmins = tgAdminsRaw ? tgAdminsRaw.split(',').map(s => s.trim()).filter(Boolean) : [];
  // Ô token để TRỐNG nghĩa là GIỮ token đang lưu — không gửi field này lên.
  //
  // Phase 79: trước đây ô token được điền sẵn giá trị thật nên lúc Lưu gửi
  // lại nguyên văn, tự nhiên không mất. Nay server không trả token nữa, ô
  // trống; nếu vẫn gửi `bot_token: ""` thì `merged = {**existing, **payload}`
  // sẽ THAY THẾ cả khối telegram — token bot bị xoá khỏi config.json mà
  // không hỏi, và gateway không khởi động lại vì nhánh `if tg_token` thấy rỗng.
  // Bỏ hẳn field khi không gõ gì, y hệt cách `saveConnectorConfig` làm.
  const telegram = {};
  const typedTgToken = getVal('cfg-tg-token');
  if (typedTgToken && typedTgToken !== SECRET_MASK) telegram.bot_token = typedTgToken;
  telegram.admin_chat_ids = tgAdmins;
  telegram.incident_group_id = getVal('cfg-tg-group') || '';

  const updated = {
    ...currentConfig,
    llm: {
      ...(currentConfig?.llm || {}),
      base_url: baseUrl,
      model_name: modelName,
      api_key: apiKey,
      router_models: (currentConfig?.llm?.router_models && currentConfig.llm.router_models.length > 0)
        ? [modelName, ...currentConfig.llm.router_models.filter(m => m !== modelName)]
        : fallbackModels(modelName),
      specialist_models: (currentConfig?.llm?.specialist_models?.length)
        ? currentConfig.llm.specialist_models : fallbackModels(modelName).slice(0, 4),
      // Phase 91: Lưu cấu hình routing từ tab Cấu Hình Hệ Thống
      routing_mode: getVal('cfg-routing-mode') || currentConfig?.llm?.routing_mode || 'router',
      direct_url: getVal('cfg-direct-url') || currentConfig?.llm?.direct_url || 'https://api.deepseek.com',
      direct_model: getVal('cfg-direct-model') || currentConfig?.llm?.direct_model || 'deepseek-chat',
      direct_api_key: (getVal('cfg-direct-key') && getVal('cfg-direct-key') !== SECRET_MASK)
        ? getVal('cfg-direct-key')
        : '',
    },
    routing: {
      ...(currentConfig?.routing || {}),
      primary: {
        ...(currentConfig?.routing?.primary || {}),
        provider_model: modelName,
        api_key: apiKey,
        api_base: baseUrl,
        api_keys: apiKey ? [apiKey] : [],
      },
    },
    telegram: telegram,
    report_templates: { ...currentReportTemplates },
    auto_execute: isAutoExec,
    AUTO_EXECUTE_UNVERIFIED_CODE: isAutoExec,
    MODEL_NAME: modelName,
    API_KEY: apiKey,
    BASE_URL: baseUrl,
    GROQ_BASE_URL: getVal('cfg-groq-url'),
    ASR_BACKEND: getVal('cfg-asr') || 'google',
    LOG_LEVEL: getVal('cfg-loglevel'),
  };
  // Ô khoá Groq để trống nghĩa là GIỮ khoá đang lưu. Gửi `GROQ_API_KEY: ""`
  // sẽ xoá khoá trên đĩa, vì khối phẳng bị `{**existing, **payload}` thay thế
  // theo từng khoá. Cùng cách với bot_token ở trên.
  const typedGroqKey = getVal('cfg-groq-key');
  if (typedGroqKey && typedGroqKey !== SECRET_MASK) updated.GROQ_API_KEY = typedGroqKey;

  const res = await apiSaveConfig(updated);
  setLoading(false);

  if (res.success) {
    currentConfig = updated;
    autoExecState = isAutoExec;
    showToast(
      apiKeyLeftUntouched
        ? '✅ Đã lưu cấu hình vào config.json (giữ nguyên API key cũ — ô khoá đang trống).'
        : '✅ Đã lưu cấu hình 9router & hệ thống vào config.json!',
      'success',
    );
    loadTelegramStatus();
    await loadDashboard();

    // Đồng bộ tức thì sang các trường của Tab Quản Lý Trợ Lý AI
    const setIf = (id, val) => {
      const el = document.getElementById(id);
      if (el) el.value = _ccSafeField(val);
    };
    setIf('ai-llm-base', baseUrl);
    setIf('ai-llm-model', modelName);
    setIf('ai-llm-key', apiKey);
    setIf('ai-asr-engine', getVal('cfg-asr') || 'google');
    setIf('ai-groq-key', getVal('cfg-groq-key'));
    setIf('ai-groq-url', getVal('cfg-groq-url'));

    // Phase 81: xác nhận ngay ở ô mật khẩu, không chỉ ở toast. Toast biến mất
    // sau vài giây; ô mật khẩu thì vẫn còn, nên người dùng nhìn xuống thấy
    // màn hình trắng và tưởng lưu hỏng.
    const gaoKey = _ccSafeField(apiKey);
    const coKey = gaoKey || _ccLastKnownHasKey.llm;
    _ccSecretStatus('cfg-llm-key', 'cfg-llm-key-hint', coKey, 'sk-...', !!gaoKey);
    _ccSecretStatus('ai-llm-key', 'ai-llm-key-hint', coKey, 'sk-...', !!gaoKey);
    if (gaoKey) _ccLastKnownHasKey.llm = true;

    const typedGroqKey2 = getVal('cfg-groq-key');
    const vuaGaoGroq2 = !!_ccSafeField(typedGroqKey2);
    const coKeyGroq2 = vuaGaoGroq2 || _ccLastKnownHasKey.groq;
    _ccSecretStatus('cfg-groq-key', 'cfg-groq-key-hint', coKeyGroq2, 'gsk_...', vuaGaoGroq2);
    _ccSecretStatus('ai-groq-key', 'ai-groq-key-hint', coKeyGroq2, 'gsk_...', vuaGaoGroq2);
    if (vuaGaoGroq2) _ccLastKnownHasKey.groq = true;
  } else {
    showToast(`❌ Lỗi lưu cấu hình: ${res.message}`, 'error');
  }
}

// ═══════════════════════════════════════════════════════════════════════════
// ── PHASE 27: QUẢN LÝ TRỢ LÝ AI — UNIFIED AI MANAGEMENT HUB ─────────────
// ═══════════════════════════════════════════════════════════════════════════

/**
/**
 * Load all config data into the AI Manager tab fields.
 * Called automatically when switching to the 'ai-manager' tab.
 *
 * Phase 70: Hai tối ưu quan trọng:
 *  1. Cache config 30s — không re-fetch nếu tab vừa mở cách đây < 30s.
 *  2. Song song hoá: `apiGetConfig` và `loadRouterModels` chạy cùng lúc
 *     bằng `Promise.all`. Trước đây tuần tự: đợi config xong rồi mới
 *     fetch models — tổng 2 round-trip nối tiếp (~600ms). Nay đồng thời.
 *  3. Debounce 50ms: tránh double-call khi người dùng click tab nhanh.
 */
let _aiManagerLoadTimer = null;
function loadAIManagerConfig() {
  clearTimeout(_aiManagerLoadTimer);
  _aiManagerLoadTimer = setTimeout(_doLoadAIManagerConfig, 50);
  loadRouterModels();
  updateAIManagerTelemetry();   // số đo (độ trễ, số kỹ năng) tải song song, không chờ cấu hình
}

async function _doLoadAIManagerConfig() {
  // Phase 70: Chạy song song — không đợi config xong mới fetch models.
  const [cfg] = await Promise.all([
    apiGetConfig(),
    loadRouterModels(),   // fire-and-forget: vẽ dropdown ngay khi xong
  ]);
  if (!cfg) return;
  currentConfig = cfg;

  const setVal = (id, val) => {
    const el = document.getElementById(id);
    if (!el || val === undefined || val === null) return;
    // Ký hiệu che không được đổ vào ô — xem giải thích ở `_ccSafeField`.
    const safe = _ccSafeField(val);
    if (el.type === 'range') { el.value = safe; el.dispatchEvent(new Event('input')); }
    else el.value = safe;
  };

  // ── Card 1: LLM
  const llm = cfg.llm || {};
  const routing = cfg.routing || {};
  const primary = routing.primary || {};
  const baseUrl = llm.base_url || primary.api_base || cfg.BASE_URL || 'http://localhost:20128/v1';
  const modelName = llm.model_name || primary.provider_model || cfg.MODEL_NAME || '';
  const apiKey = llm.api_key || (primary.api_keys && primary.api_keys[0]) || cfg.API_KEY || '';
  setVal('ai-llm-base', baseUrl);
  setVal('ai-llm-model', modelName);
  setVal('ai-llm-key', apiKey);

  // ── Phase 91: Dual-Mode Routing (Độc quyền 1 trong 2)
  const routingMode = (llm.routing_mode === 'direct') ? 'direct' : 'router';
  const directUrl   = llm.direct_url   || 'https://api.deepseek.com';
  const directModel = llm.direct_model  || 'deepseek-chat';
  const directKey   = llm.direct_api_key || '';
  setVal('ai-direct-url',     directUrl);
  setVal('ai-direct-model',   directModel);
  setVal('ai-direct-api-key', directKey);
  switchLLMEngineMode(routingMode);

  // ── Phase 94: Tri-Brain Specialized Architecture restore
  const triBrainEnabled = llm.tri_brain_enabled !== false;
  const triSwitch = document.getElementById('ai-switch-tribrain');
  if (triSwitch) {
    if (triBrainEnabled) triSwitch.classList.add('is-active', 'on');
    else triSwitch.classList.remove('is-active', 'on');
  }
  // Chưa đặt riêng thì dùng model chính — đúng như máy chủ (llm_engine.get_brain_model).
  // Trước đây ô "Vận hành" tự điền "VN-MateAi": model KHÔNG có trong 9Router, bấm
  // Lưu một lần là mọi lệnh có tool gọi vào model không tồn tại.
  const cModel = llm.controller_model || modelName;
  const vModel = llm.voice_model || modelName;
  const oModel = llm.ops_model || modelName;

  setVal('ai-tribrain-controller-model', cModel);
  setVal('ai-tribrain-voice-model',      vModel);
  setVal('ai-tribrain-ops-model',        oModel);

  // Đồng bộ giá trị vào dropdown sổ ra
  setVal('ai-tribrain-controller-select', cModel);
  setVal('ai-tribrain-voice-select',      vModel);
  setVal('ai-tribrain-ops-select',        oModel);

  // Tự động nhận diện provider đã cấu hình
  const u = (directUrl || '').toLowerCase();
  let matchedProv = 'deepseek';
  if (u.includes('groq')) matchedProv = 'groq';
  else if (u.includes('openai')) matchedProv = 'openai';
  else if (u.includes('openrouter')) matchedProv = 'openrouter';
  else if (u.includes('11434') || u.includes('ollama')) matchedProv = 'ollama';
  else if (u.includes('1234') || u.includes('lmstudio')) matchedProv = 'lmstudio';
  else if (u.includes('deepseek')) matchedProv = 'deepseek';

  const prov = DIRECT_PROVIDERS[matchedProv];
  if (prov) {
    const badge = document.getElementById('direct-provider-badge');
    if (badge) badge.textContent = prov.name;
    const guideLink = document.getElementById('direct-token-guide-link');
    if (guideLink) {
      guideLink.href = prov.guideLink;
      guideLink.innerHTML = `<span>${prov.guideText}</span> <svg width="10" height="10" fill="none" stroke="currentColor" stroke-width="2" viewBox="0 0 24 24"><path d="M18 13v6a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h6"/><polyline points="15 3 21 3 21 9"/><line x1="10" y1="14" x2="21" y2="3"/></svg>`;
    }
    const hint = document.getElementById('direct-key-hint');
    if (hint) hint.textContent = prov.hint;
    Object.keys(DIRECT_PROVIDERS).forEach(k => {
      const btn = document.getElementById(`btn-prov-${k}`);
      if (!btn) return;
      if (k === matchedProv) {
        btn.className = 'provider-chip py-2 px-2.5 rounded-xl border text-center transition-all duration-150 flex flex-col items-center gap-1 border-orange-400/80 bg-orange-500/15 text-orange-400 font-bold shadow-sm';
      } else {
        btn.className = 'provider-chip py-2 px-2.5 rounded-xl border border-slate-200 dark:border-white/10 bg-slate-50 dark:bg-white/[0.02] text-slate-700 dark:text-slate-300 hover:border-orange-400/50 text-center transition-all duration-150 flex flex-col items-center gap-1 font-semibold';
      }
    });
  }

  // ── Card 2: Persona
  const persona = cfg.persona || {};
  setVal('ai-persona-name', persona.ai_name || cfg.AI_NAME || 'Ly Ly');
  setVal('ai-persona-wake', persona.wake_word || cfg.WAKE_WORD || 'Hey Ly Ly');
  setVal('ai-persona-ai-pronoun', persona.ai_pronoun || 'em');
  setVal('ai-persona-user-pronoun', persona.user_pronoun || 'anh');
  setVal('ai-persona-prompt', persona.system_prompt || cfg.SYSTEM_PROMPT || '');

  // ── Card 3: Audio / TTS / ASR
  const audio = cfg.audio || {};
  // "local" (Piper) từng có trên giao diện nhưng máy chủ chưa từng hỗ trợ.
  const ttsEngine = (audio.tts_engine && audio.tts_engine !== 'local') ? audio.tts_engine : 'edge-tts';
  const ttsVoice = audio.tts_voice || cfg.TTS_VOICE || 'vi-VN-HoaiMyNeural';
  setVal('ai-tts-engine', ttsEngine);
  if (_isVbeeVoice(ttsVoice)) _ensureVbeeVoiceOption(ttsVoice, ttsVoice);
  setVal('ai-tts-voice', ttsVoice);

  // Parse TTS_RATE: "+15%" → 15
  const rateRaw = audio.speech_rate !== undefined ? audio.speech_rate
    : (cfg.TTS_RATE ? parseInt(cfg.TTS_RATE.replace('%', '')) : 15);
  setVal('ai-speech-rate', rateRaw);

  const volume = audio.volume !== undefined ? audio.volume : 80;
  setVal('ai-volume', volume);

  const asrEngine = audio.asr_engine || cfg.ASR_BACKEND || 'google';
  setVal('ai-asr-engine', asrEngine);
  setVal('ai-groq-key', cfg.GROQ_API_KEY || '');
  setVal('ai-groq-url', cfg.GROQ_BASE_URL || 'https://api.groq.com/openai/v1');

  // ElevenLabs WebSocket Streaming config restore
  if (audio.elevenlabs_api_key) setVal('ai-elevenlabs-key', audio.elevenlabs_api_key);
  if (audio.elevenlabs_voice_id) setVal('ai-elevenlabs-voice-id', audio.elevenlabs_voice_id);
  if (audio.elevenlabs_model) setVal('ai-elevenlabs-model', audio.elevenlabs_model);
  // Vbee: token KHÔNG nạp lại vào ô (máy chủ chỉ trả ký hiệu che) — để trống nghĩa là giữ token đã lưu.
  setVal('ai-vbee-app-id', audio.vbee_app_id || '');
  const vbeeTokenEl = document.getElementById('ai-vbee-token');
  if (vbeeTokenEl) {
    vbeeTokenEl.value = '';
    vbeeTokenEl.placeholder = audio.vbee_token ? 'Đã lưu token — để trống để giữ nguyên' : 'Dán token của ứng dụng Vbee';
  }

  // Trạng thái hai ô mật khẩu. Dùng hàm chung với tab Cấu Hình để hai nơi
  // không lệch nhau — trước đây chỉ ô tab Cấu Hình có gợi ý.
  _ccLastKnownHasKey.llm = _ccHasStoredKey(cfg.llm);
  _ccLastKnownHasKey.groq = _ccHasStoredKey({ api_key: cfg.GROQ_API_KEY });
  _ccSecretStatus('ai-llm-key', 'ai-llm-key-hint', _ccLastKnownHasKey.llm, 'sk-...');
  _ccSecretStatus('ai-groq-key', 'ai-groq-key-hint', _ccLastKnownHasKey.groq, 'gsk_...');

  // Show/hide Groq section + ElevenLabs section
  onAIASREngineChange();
  onAITTSEngineChange();

  // Populate report templates (Phase 28)
  populateReportTemplates(cfg.report_templates);

  // Init tooltips
  _initAITooltips();

  // Phase 47: AI Manager Live Telemetry & Prompt Analytics
  updateAIManagerTelemetry();
  updatePromptStats();
  // loadRouterModels() đã được gọi song song ở đầu hàm — không cần gọi lại.
}

/**
 * Save all AI Manager config to backend.
 */
async function saveAIConfig() {
  const btns = [
    document.getElementById('btn-save-ai-config'),
    document.getElementById('btn-save-ai-config-bottom'),
  ];
  const SAVE_ICON = `<svg width="16" height="16" fill="none" stroke="currentColor" stroke-width="2.5" viewBox="0 0 24 24"><path d="M19 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h11l5 5v11a2 2 0 0 1-2 2z"/><polyline points="17 21 17 13 7 13 7 21"/></svg>`;
  const SPIN_ICON = `<svg class="animate-spin" width="16" height="16" fill="none" stroke="currentColor" stroke-width="2" viewBox="0 0 24 24"><path d="M21 12a9 9 0 1 1-6.219-8.56"/></svg>`;

  const setLoading = (loading) => {
    btns.forEach(b => {
      if (!b) return;
      b.disabled = loading;
      b.innerHTML = loading
        ? `${SPIN_ICON} Đang lưu...`
        : `${SAVE_ICON} Lưu Cấu Hình AI`;
    });
  };

  const getVal = id => { const el = document.getElementById(id); return el ? el.value.trim() : ''; };

  let forceModels = false;
  // Model không có trong danh sách 9Router đang phục vụ -> mọi lời gọi sẽ lỗi rồi
  // chuyển dự phòng (chậm thêm vài giây mỗi lượt). Hỏi lại trước khi lưu.
  if ((getVal('ai-routing-mode') || 'router') === 'router' && Array.isArray(routerModelList) && routerModelList.length) {
    const known = new Set(routerModelList);
    const missing = [
      ['Model chính', getVal('ai-llm-model')],
      ['Não Điều phối', getVal('ai-tribrain-controller-model')],
      ['Não Giao tiếp', getVal('ai-tribrain-voice-model')],
      ['Não Vận hành', getVal('ai-tribrain-ops-model')],
    ].filter(([, m]) => m && !known.has(m));
    if (missing.length) forceModels = true;
    if (missing.length && !confirm(
      'Các model sau KHÔNG có trong 9Router đang phục vụ:\n'
      + missing.map(([k, m]) => `• ${k}: ${m}`).join('\n')
      + '\n\nMọi lượt dùng chúng sẽ lỗi rồi chuyển model dự phòng (chậm thêm). Vẫn lưu?')) {
      return;
    }
  }

  setLoading(true);

  const baseUrl = getVal('ai-llm-base') || 'http://localhost:20128/v1';
  const modelName = getVal('ai-llm-model') || '';
  const typedApiKey = getVal('ai-llm-key');
  const apiKey = (typedApiKey && typedApiKey !== SECRET_MASK) ? typedApiKey : (currentConfig?.llm?.api_key || '');

  const aiName = getVal('ai-persona-name') || 'Ly Ly';
  const wakeWord = getVal('ai-persona-wake') || 'Hey Ly Ly';
  const aiPronoun = getVal('ai-persona-ai-pronoun') || 'em';
  const userPronoun = getVal('ai-persona-user-pronoun') || 'anh';
  const systemPrompt = getVal('ai-persona-prompt');

  const ttsEngine = getVal('ai-tts-engine') || 'edge-tts';
  const ttsVoice = getVal('ai-tts-voice') || 'vi-VN-HoaiMyNeural';
  const speechRateNum = parseInt(document.getElementById('ai-speech-rate')?.value || '15');
  const speechRate = (speechRateNum >= 0 ? '+' : '') + speechRateNum + '%';
  const volume = parseInt(document.getElementById('ai-volume')?.value || '80');
  const asrEngine = getVal('ai-asr-engine') || 'google';
  const typedGroqKey = getVal('ai-groq-key');
  const groqKey = (typedGroqKey && typedGroqKey !== SECRET_MASK) ? typedGroqKey : (currentConfig?.GROQ_API_KEY || '');
  const groqUrl = getVal('ai-groq-url');
  // ElevenLabs WebSocket Streaming settings
  const typedElKey = getVal('ai-elevenlabs-key');
  const elKey = (typedElKey && typedElKey !== SECRET_MASK) ? typedElKey : (currentConfig?.audio?.elevenlabs_api_key || '');
  const elVoiceId = getVal('ai-elevenlabs-voice-id') || currentConfig?.audio?.elevenlabs_voice_id || '';
  const elModel = getVal('ai-elevenlabs-model') || 'eleven_turbo_v2_5';
  // Vbee AIVoice: ô token trống / còn ký hiệu che → giữ token đã lưu (máy chủ nhận ký hiệu che là "không đổi")
  const vbeeAppId = (getVal('ai-vbee-app-id') || '').trim();
  const typedVbeeToken = (getVal('ai-vbee-token') || '').trim();
  const vbeeToken = (typedVbeeToken && typedVbeeToken !== SECRET_MASK) ? typedVbeeToken : (currentConfig?.audio?.vbee_token || '');
  if (ttsEngine === 'vbee' && (!vbeeAppId || !vbeeToken)) {
    showToast('Chọn Vbee thì cần nhập App ID và Token (studio.vbee.vn/apps → Tạo ứng dụng).', 'error');
    return;
  }

  if (activeTemplateKey) {
    const curEditor = document.getElementById('tpl-editor-body')?.value;
    if (curEditor !== undefined && currentReportTemplates) {
      currentReportTemplates[activeTemplateKey] = curEditor;
    }
  }

  const typedDirectKey = getVal('ai-direct-api-key');
  const directApiKey = (typedDirectKey && typedDirectKey !== SECRET_MASK) ? typedDirectKey : '';

  const updated = {
    ...currentConfig,
    ...(forceModels ? { _force_models: true } : {}),
    llm: {
      ...(currentConfig?.llm || {}),
      base_url: baseUrl,
      model_name: modelName,
      api_key: apiKey,
      router_models: (currentConfig?.llm?.router_models && currentConfig.llm.router_models.length > 0)
        ? [modelName, ...currentConfig.llm.router_models.filter(m => m !== modelName)]
        : fallbackModels(modelName),
      specialist_models: (currentConfig?.llm?.specialist_models?.length)
        ? currentConfig.llm.specialist_models : fallbackModels(modelName).slice(0, 4),
      // Phase 91: Dual-mode routing (Độc quyền 1 trong 2)
      routing_mode:   getVal('ai-routing-mode') || 'router',
      direct_url:     getVal('ai-direct-url') || '',
      direct_model:   getVal('ai-direct-model') || '',
      direct_api_key: directApiKey,
      // Phase 94: Tri-Brain Specialized Architecture
      // Trước đây `... || true` — công tắc Tri-Brain không bao giờ tắt được.
      tri_brain_enabled: !!(document.getElementById('ai-switch-tribrain')?.classList.contains('is-active')
                         || document.getElementById('ai-switch-tribrain')?.classList.contains('on')),
      controller_model:  getVal('ai-tribrain-controller-model') || modelName,
      voice_model:       getVal('ai-tribrain-voice-model') || modelName,
      ops_model:         getVal('ai-tribrain-ops-model') || modelName,
    },
    persona: {
      ai_name: aiName,
      wake_word: wakeWord,
      ai_pronoun: aiPronoun,
      user_pronoun: userPronoun,
      system_prompt: systemPrompt,
    },
    audio: {
      tts_engine: ttsEngine,
      tts_voice: ttsVoice,
      speech_rate: speechRateNum,
      volume: volume,
      asr_engine: asrEngine,
      // ElevenLabs WebSocket Streaming (Mission Briefing Bước 2)
      elevenlabs_api_key: elKey,
      elevenlabs_voice_id: elVoiceId,
      elevenlabs_model: elModel,
      vbee_app_id: vbeeAppId,
      vbee_token: vbeeToken,
    },
    report_templates: { ...currentReportTemplates },
    // Backward compatibility keys
    TTS_VOICE: ttsVoice,
    TTS_RATE: speechRate,
    ASR_BACKEND: asrEngine,
    GROQ_API_KEY: groqKey,
    GROQ_BASE_URL: groqUrl || 'https://api.groq.com/openai/v1',
    MODEL_NAME: modelName,
    API_KEY: apiKey,
    BASE_URL: baseUrl,
    AI_NAME: aiName,
    WAKE_WORD: wakeWord,
    SYSTEM_PROMPT: systemPrompt,
    // Sync routing.primary as well
    routing: {
      ...(currentConfig?.routing || {}),
      primary: {
        ...(currentConfig?.routing?.primary || {}),
        provider_model: modelName,
        api_key: apiKey,
        api_base: baseUrl,
        api_keys: apiKey ? [apiKey] : [],
      },
    },
    // Preserve telegram block — saveAIConfig không có form nhập Telegram,
    // spread `...currentConfig` đã chép nhưng nếu currentConfig.telegram bị
    // thiếu thì server sẽ merge và giữ nguyên bản lưu sẵn.
    telegram: currentConfig?.telegram || undefined,
  };

  const res = await apiSaveConfig(updated);
  setLoading(false);

  if (res && res.success) {
    currentConfig = updated;
    showToast('✅ Đã cập nhật hệ thống AI thành công!', 'success');
    // Also sync legacy config tab fields if visible
    const legacyBase = document.getElementById('cfg-llm-base');
    if (legacyBase) legacyBase.value = baseUrl;
    const legacyModel = document.getElementById('cfg-llm-model');
    if (legacyModel) legacyModel.value = modelName;
    const legacyKey = document.getElementById('cfg-llm-key');
    if (legacyKey) legacyKey.value = _ccSafeField(apiKey);
    const chainEl = document.getElementById('cfg-chain-primary');
    if (chainEl) chainEl.textContent = modelName;

    // Phase 91: Đồng bộ huy hiệu & banner ở tab Cấu Hình Hệ Thống ngay lập tức
    const modeBadge = document.getElementById('cfg-routing-mode-badge');
    const directBanner = document.getElementById('cfg-direct-active-banner');
    const directInfoUrl = document.getElementById('cfg-direct-info-url');
    const directInfoModel = document.getElementById('cfg-direct-info-model');
    const routerNote = document.getElementById('cfg-router-mode-note');
    const rMode = updated.llm.routing_mode;
    if (rMode === 'direct') {
      if (modeBadge) {
        modeBadge.className = 'px-2 py-0.5 rounded text-[10px] font-bold tracking-wider uppercase font-mono bg-orange-500/20 text-orange-400 border border-orange-400/40';
        modeBadge.textContent = '⚡ CHẾ ĐỘ: DIRECT LLM';
      }
      if (directBanner) directBanner.classList.remove('hidden');
      if (directInfoUrl) directInfoUrl.textContent = updated.llm.direct_url || 'Chưa thiết lập URL';
      if (directInfoModel) directInfoModel.textContent = updated.llm.direct_model || 'Mặc định';
      if (routerNote) routerNote.classList.remove('hidden');
    } else {
      if (modeBadge) {
        modeBadge.className = 'px-2 py-0.5 rounded text-[10px] font-bold tracking-wider uppercase font-mono bg-cyan-500/20 text-cyan-400 border border-cyan-400/40';
        modeBadge.textContent = '🔀 CHẾ ĐỘ: 9ROUTER GATEWAY';
      }
      if (directBanner) directBanner.classList.add('hidden');
      if (routerNote) routerNote.classList.add('hidden');
    }

    // Phase 81: báo ngay "đã lưu" ở từng ô mật khẩu. Không có dòng này thì
    // sau khi bấm Lưu, ô vẫn trống và không có gì cho biết khoá đã vào
    // config.json — người dùng tưởng lưu hỏng rồi dán lại.
    // Chỉ nói "vừa lưu khoá" khi người dùng THẬT SỰ gõ khoá. Nếu ô trống thì
    // máy chủ giữ khoá cũ — ta không biết còn hay không, nên đừng khẳng định
    // là đã có.
    const vuaGao = !!_ccSafeField(apiKey);
    const coKeyLLM = vuaGao || _ccLastKnownHasKey.llm;
    _ccSecretStatus('ai-llm-key', 'ai-llm-key-hint', coKeyLLM, 'sk-...', vuaGao);
    _ccSecretStatus('cfg-llm-key', 'cfg-llm-key-hint', coKeyLLM, 'sk-...', vuaGao);

    const vuaGaoGroq = !!_ccSafeField(groqKey);
    const coKeyGroq = vuaGaoGroq || _ccLastKnownHasKey.groq;
    _ccSecretStatus('ai-groq-key', 'ai-groq-key-hint', coKeyGroq, 'gsk_...', vuaGaoGroq);
    _ccSecretStatus('cfg-groq-key', 'cfg-groq-key-hint', coKeyGroq, 'gsk_...', vuaGaoGroq);
    if (vuaGao) _ccLastKnownHasKey.llm = true;
    if (vuaGaoGroq) _ccLastKnownHasKey.groq = true;
  } else {
    showToast(`❌ Lỗi lưu cấu hình: ${res?.message || 'Không xác định'}`, 'error');
  }
}

// ── AI Manager: tab con ────────────────────────────────────────────────────
// Tab dài ~1.100 dòng: chia theo việc. Cột chứa thẻ đang xem giãn hết chiều rộng
// (không để nửa màn hình trống). "Xem tất cả" = bố cục 2 cột như cũ.
function showAISubTab(name) {
  const root = document.getElementById('tab-ai-manager');
  if (!root) return;
  const all = name === 'all';
  root.querySelectorAll('[data-ai-sub]').forEach((el) => {
    el.classList.toggle('hidden', !all && el.dataset.aiSub !== name);
  });
  for (const [id, span] of [['ai-col-left', 'lg:col-span-7'], ['ai-col-right', 'lg:col-span-5']]) {
    const col = document.getElementById(id);
    if (!col) continue;
    const visible = [...col.querySelectorAll('[data-ai-sub]')].some((el) => !el.classList.contains('hidden'));
    col.classList.toggle('hidden', !visible);
    col.classList.toggle(span, all);
    col.classList.toggle('lg:col-span-12', !all);
  }
  root.querySelectorAll('.ai-subtab').forEach((b) => {
    const on = b.dataset.aiSubtab === name;
    b.classList.toggle('bg-primary-600', on);
    b.classList.toggle('text-white', on);
    b.classList.toggle('text-slate-600', !on);
    b.classList.toggle('dark:text-slate-300', !on);
  });
  try { localStorage.setItem('vnmate_ai_subtab', name); } catch (_) { /* chế độ riêng tư */ }
  if (name === 'ops' || all) { loadAIModelStats(); loadConfigHistory(); }
  if (typeof LlmOpsUI !== 'undefined') { if (name === 'llmops' || all) LlmOpsUI.onEnter(); else LlmOpsUI.onLeave(); }
}

function initAISubTabs() {
  let saved = 'brain';
  try { saved = localStorage.getItem('vnmate_ai_subtab') || 'brain'; } catch (_) { /* bỏ qua */ }
  showAISubTab(saved);
}

// ── Thử trước khi lưu ──────────────────────────────────────────────────────
async function runAIPreview() {
  const btn = document.getElementById('btn-ai-preview');
  const message = (document.getElementById('ai-preview-message')?.value || '').trim();
  if (!message) return;
  const routerMode = (document.getElementById('ai-routing-mode')?.value || 'router') === 'router';
  const draftModel = routerMode
    ? (document.getElementById('ai-tribrain-voice-model')?.value || document.getElementById('ai-llm-model')?.value || '').trim()
    : (document.getElementById('ai-direct-model')?.value || '').trim();
  const draft = {
    message,
    model: draftModel || null,
    system_prompt: document.getElementById('ai-persona-prompt')?.value ?? '',
    ai_name: (document.getElementById('ai-persona-name')?.value || '').trim() || null,
  };
  const call = (body) => apiFetch('/api/v1/llm/preview', {
    method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body),
  }).then((r) => r.json()).catch((e) => ({ success: false, error: e.message }));
  const show = (id, metaId, d) => {
    const box = document.getElementById(id);
    const meta = document.getElementById(metaId);
    if (meta) meta.textContent = d.model ? `${d.model} · ${d.latency_ms ?? '?'}ms` : '';
    if (box) {
      box.textContent = d.success ? d.reply : `Lỗi: ${d.error || d.detail || 'không rõ'}`;
      box.classList.toggle('text-rose-500', !d.success);
    }
  };
  if (btn) { btn.disabled = true; btn.textContent = 'Đang hỏi…'; }
  for (const id of ['ai-preview-current', 'ai-preview-draft']) {
    const el = document.getElementById(id); if (el) el.textContent = '…';
  }
  const [cur, drf] = await Promise.all([call({ message }), call(draft)]);
  show('ai-preview-current', 'ai-preview-current-meta', cur);
  show('ai-preview-draft', 'ai-preview-draft-meta', drf);
  if (btn) { btn.disabled = false; btn.textContent = 'So sánh'; }
}

// ── Hiệu năng theo não & model ─────────────────────────────────────────────
async function loadAIModelStats() {
  const box = document.getElementById('ai-model-stats');
  if (!box) return;
  try {
    const d = await (await apiFetch('/api/v1/voice/model-stats')).json();
    const rows = d.rows || [];
    if (!rows.length) {
      box.innerHTML = `<span class="italic">Chưa có lượt nào gọi LLM từ lúc máy chủ khởi động (${d.turns_total || 0} lượt tổng).</span>`;
      return;
    }
    const brainName = { voice: 'Giao tiếp', ops: 'Vận hành', controller: 'Điều phối' };
    const ms = (o) => (o && o.p50 != null ? `${Math.round(o.p50)} / ${Math.round(o.p95)}` : '—');
    box.className = 'text-xs overflow-x-auto';
    box.innerHTML =
      `<table class="w-full text-left"><thead class="text-[10px] text-slate-400"><tr>` +
      `<th>Não</th><th>Model thực tế</th><th>Lượt</th><th>Lỗi</th><th>Dự phòng</th><th>Chữ đầu p50/p95 (ms)</th><th>Tiếng đầu p50/p95</th><th>Cả lượt p50/p95</th></tr></thead><tbody>` +
      rows.map((r) => `<tr class="border-t border-slate-100 dark:border-white/5">` +
        `<td class="py-1">${_esc(brainName[r.brain] || r.brain)}</td><td class="font-mono text-[10px]">${_esc(r.model)}</td>` +
        `<td>${r.turns}</td><td class="${r.errors ? 'text-rose-500 font-bold' : ''}">${r.errors}</td>` +
        `<td class="${r.fallbacks ? 'text-amber-500 font-bold' : ''}">${r.fallbacks}</td>` +
        `<td class="font-mono">${ms(r.llm_first_token_ms)}</td><td class="font-mono">${ms(r.ttfa_answer_ms)}</td><td class="font-mono">${ms(r.ttl_ms)}</td></tr>`).join('') +
      `</tbody></table>`;
  } catch (err) {
    box.innerHTML = `<span class="text-rose-500">Không tải được: ${_esc(friendlyErrorText(err))}</span>`;
  }
}

// ── Lịch sử cấu hình ───────────────────────────────────────────────────────
function _fmtCfgVal(v) {
  if (v === null || v === undefined) return '∅';
  const s = typeof v === 'string' ? v : JSON.stringify(v);
  return s.length > 60 ? s.slice(0, 57) + '…' : s;
}

function _renderCfgChanges(changes) {
  if (!changes || !changes.length) return '<div class="italic text-slate-400">Không có khác biệt.</div>';
  return changes.map((c) => `<div class="font-mono text-[10px] leading-relaxed"><span class="text-slate-500">${_esc(c.path)}</span>: ` +
    (c.secret ? '<span class="text-amber-500">(khoá bí mật — đã đổi)</span>'
      : `<span class="text-rose-500 line-through">${_esc(_fmtCfgVal(c.old))}</span> → <span class="text-emerald-600 dark:text-emerald-400">${_esc(_fmtCfgVal(c.new))}</span>`) +
    `</div>`).join('');
}

async function loadConfigHistory() {
  const box = document.getElementById('ai-config-history');
  if (!box) return;
  try {
    const d = await (await apiFetch('/api/v1/config/history?limit=30')).json();
    const items = d.history || [];
    if (!items.length) { box.innerHTML = '<span class="italic">Chưa có lần lưu nào được ghi lại.</span>'; return; }
    box.className = 'text-xs space-y-2 max-h-[28rem] overflow-y-auto pr-1';
    box.innerHTML = items.map((h) =>
      `<div class="p-2.5 rounded-xl border border-slate-200 dark:border-white/10">` +
      `<div class="flex items-center gap-2"><b>#${h.id}</b><span class="text-slate-400 text-[10px]">${_esc(fmtServerTime(h.saved_at))} · ${_esc(h.saved_by || '?')}</span>` +
      `<span class="ml-auto flex gap-1">` +
      `<button class="text-[10px] px-2 py-0.5 rounded border border-slate-200 dark:border-white/10" onclick="showConfigDiff(${h.id})">So với hiện tại</button>` +
      `<button class="text-[10px] px-2 py-0.5 rounded bg-amber-500/15 text-amber-600 dark:text-amber-300 font-bold" onclick="restoreConfigVersion(${h.id})">Khôi phục</button></span></div>` +
      `<div class="text-[11px] text-slate-600 dark:text-slate-300 mt-0.5">${_esc(h.note || '')} · ${h.change_count} thay đổi</div>` +
      `<div class="mt-1">${_renderCfgChanges(h.changes)}</div>` +
      `<div id="cfg-diff-${h.id}" class="mt-1"></div></div>`).join('');
  } catch (err) {
    box.innerHTML = `<span class="text-rose-500">Không tải được: ${_esc(friendlyErrorText(err))}</span>`;
  }
}

async function showConfigDiff(id) {
  const el = document.getElementById(`cfg-diff-${id}`);
  if (!el) return;
  try {
    const d = await (await apiFetch(`/api/v1/config/history/${id}/diff`)).json();
    el.innerHTML = `<div class="mt-1 p-2 rounded-lg bg-slate-50 dark:bg-white/[0.03]"><div class="text-[10px] font-bold text-slate-500 mb-1">Khôi phục #${id} sẽ đổi (hiện tại → phiên bản):</div>${_renderCfgChanges(d.changes)}</div>`;
  } catch (err) {
    el.innerHTML = `<span class="text-rose-500">${_esc(err.message)}</span>`;
  }
}

async function restoreConfigVersion(id) {
  if (!confirm(`Khôi phục cấu hình về phiên bản #${id}? Các khoá bí mật hiện tại được giữ nguyên.`)) return;
  try {
    const res = await apiFetch(`/api/v1/config/history/${id}/restore`, { method: 'POST' });
    const d = await res.json();
    if (!res.ok) throw new Error(typeof d.detail === 'object' ? (d.detail.errors || []).join('; ') : (d.detail || `HTTP ${res.status}`));
    showToast(`✔ ${d.message}`, 'success');
    _cachedConfig = null; _cachedConfigAt = 0;
    loadAIManagerConfig();
    loadConfigHistory();
  } catch (err) {
    showToast(`✖ Khôi phục thất bại: ${err.message}`, 'error');
  }
}

Object.assign(window, { showAISubTab, runAIPreview, loadAIModelStats, loadConfigHistory, showConfigDiff, restoreConfigVersion });

// ── AI Manager Helpers ──────────────────────────────────────────────────────

// ─── Phase 91: Bộ Chuyển Đổi Nguồn LLM (Chỉ 1 trong 2 hoạt động) ───────────

/**
 * Chuyển đổi độc quyền 1 trong 2 chế độ:
 *  - 'router': Kết nối qua 9Router Gateway
 *  - 'direct': Kết nối trực tiếp Engine (Ollama/LM Studio/vLLM)
 */
function switchLLMEngineMode(mode) {
  if (mode !== 'direct') mode = 'router';

  // 1. Lưu giá trị vào hidden input
  const hiddenEl = document.getElementById('ai-routing-mode');
  if (hiddenEl) hiddenEl.value = mode;

  // 2. Elements
  const tabRouter = document.getElementById('ai-mode-tab-router');
  const tabDirect = document.getElementById('ai-mode-tab-direct');
  const badgeRouter = document.getElementById('badge-tab-router');
  const badgeDirect = document.getElementById('badge-tab-direct');
  const panelRouter = document.getElementById('panel-llm-router');
  const panelDirect = document.getElementById('panel-llm-direct');
  const statusText = document.getElementById('ai-mode-status-text');

  if (mode === 'direct') {
    // Direct LLM: BẬT
    if (tabDirect) {
      tabDirect.className = 'cursor-pointer p-3.5 rounded-xl border-2 transition-all duration-200 flex items-center justify-between border-orange-400 ring-2 ring-orange-400/30 bg-orange-500/10 shadow-sm opacity-100';
    }
    if (badgeDirect) {
      badgeDirect.className = 'px-2 py-0.5 rounded-full text-[10px] font-mono font-bold bg-orange-500/20 text-orange-300 border border-orange-400/40';
      badgeDirect.textContent = '● ĐANG BẬT';
    }
    // 9Router: TẮT
    if (tabRouter) {
      tabRouter.className = 'cursor-pointer p-3.5 rounded-xl border-2 transition-all duration-200 flex items-center justify-between border-slate-200 dark:border-white/10 opacity-70 hover:opacity-100 bg-slate-50 dark:bg-white/[0.02]';
    }
    if (badgeRouter) {
      badgeRouter.className = 'px-2 py-0.5 rounded-full text-[10px] font-mono font-bold bg-slate-200/60 dark:bg-white/5 text-slate-400 border border-transparent';
      badgeRouter.textContent = 'TẮT';
    }

    if (panelDirect) panelDirect.classList.remove('hidden');
    if (panelRouter) panelRouter.classList.add('hidden');

    if (statusText) {
      statusText.className = 'text-[10px] text-orange-400 font-mono font-semibold flex items-center gap-1';
      statusText.innerHTML = '<span class="inline-block w-1.5 h-1.5 rounded-full bg-orange-400 animate-pulse"></span> Đang dùng: Direct LLM (Bypass Proxy)';
    }
  } else {
    // 9Router: BẬT
    if (tabRouter) {
      tabRouter.className = 'cursor-pointer p-3.5 rounded-xl border-2 transition-all duration-200 flex items-center justify-between border-cyan-400 ring-2 ring-cyan-400/30 bg-cyan-500/10 shadow-sm opacity-100';
    }
    if (badgeRouter) {
      badgeRouter.className = 'px-2 py-0.5 rounded-full text-[10px] font-mono font-bold bg-cyan-500/20 text-cyan-300 border border-cyan-400/40';
      badgeRouter.textContent = '● ĐANG BẬT';
    }
    // Direct LLM: TẮT
    if (tabDirect) {
      tabDirect.className = 'cursor-pointer p-3.5 rounded-xl border-2 transition-all duration-200 flex items-center justify-between border-slate-200 dark:border-white/10 opacity-70 hover:opacity-100 bg-slate-50 dark:bg-white/[0.02]';
    }
    if (badgeDirect) {
      badgeDirect.className = 'px-2 py-0.5 rounded-full text-[10px] font-mono font-bold bg-slate-200/60 dark:bg-white/5 text-slate-400 border border-transparent';
      badgeDirect.textContent = 'TẮT';
    }

    if (panelRouter) panelRouter.classList.remove('hidden');
    if (panelDirect) panelDirect.classList.add('hidden');

    if (statusText) {
      statusText.className = 'text-[10px] text-cyan-400 font-mono font-semibold flex items-center gap-1';
      statusText.innerHTML = '<span class="inline-block w-1.5 h-1.5 rounded-full bg-cyan-400 animate-pulse"></span> Đang dùng: 9Router Gateway';
    }
  }
}

// Alias tương thích ngược
const selectRoutingMode = switchLLMEngineMode;

const DIRECT_PROVIDERS = {
  deepseek: {
    name: 'DeepSeek',
    url: 'https://api.deepseek.com',
    model: 'deepseek-chat',
    placeholder: 'sk-... (Token từ platform.deepseek.com)',
    guideLink: 'https://platform.deepseek.com/api_keys',
    guideText: 'Lấy Token tại DeepSeek',
    hint: 'Chỉ cần dán Token DeepSeek. Hệ thống sẽ kết nối thẳng https://api.deepseek.com',
  },
  groq: {
    name: 'Groq Cloud',
    url: 'https://api.groq.com/openai/v1',
    model: 'llama-3.3-70b-versatile',
    placeholder: 'gsk_... (Token từ console.groq.com)',
    guideLink: 'https://console.groq.com/keys',
    guideText: 'Lấy Token tại Groq Console',
    hint: 'Groq Cloud phản hồi cực nhanh ~500 tokens/giây, chỉ cần dán Token từ Groq Console.',
  },
  openai: {
    name: 'OpenAI',
    url: 'https://api.openai.com/v1',
    model: 'gpt-4o-mini',
    placeholder: 'sk-proj-... (Token từ platform.openai.com)',
    guideLink: 'https://platform.openai.com/api-keys',
    guideText: 'Lấy Token tại OpenAI',
    hint: 'Dán Token OpenAI. Khuyên dùng gpt-4o-mini để phản hồi nhanh và tiết kiệm chi phí.',
  },
  openrouter: {
    name: 'OpenRouter',
    url: 'https://openrouter.ai/api/v1',
    model: 'deepseek/deepseek-chat',
    placeholder: 'sk-or-v1-... (Token từ openrouter.ai)',
    guideLink: 'https://openrouter.ai/keys',
    guideText: 'Lấy Token tại OpenRouter',
    hint: 'OpenRouter tổng hợp mọi mô hình. Dán Token lấy từ openrouter.ai/keys.',
  },
  ollama: {
    name: 'Ollama (Máy)',
    url: 'http://localhost:11434/v1',
    model: 'llama3.2',
    placeholder: 'ollama (không cần token, để trống)',
    guideLink: 'https://ollama.com',
    guideText: 'Trang chủ Ollama',
    hint: 'Chạy trực tiếp mô hình trên máy qua Ollama local server, không tốn chi phí token.',
    defaultKey: 'ollama',
  },
  lmstudio: {
    name: 'LM Studio',
    url: 'http://localhost:1234/v1',
    model: 'local-model',
    placeholder: 'lm-studio (không cần token, để trống)',
    guideLink: 'https://lmstudio.ai',
    guideText: 'Trang chủ LM Studio',
    hint: 'Chạy trực tiếp mô hình trên máy qua LM Studio local server, không tốn chi phí token.',
    defaultKey: 'lm-studio',
  },
};

/** Chọn nhà cung cấp AI khi dùng chế độ Kết Nối Trực Tiếp */
function selectDirectProvider(provId) {
  const p = DIRECT_PROVIDERS[provId];
  if (!p) return;

  const setV = (id, v) => { const el = document.getElementById(id); if (el) el.value = v; };

  // 1. Cập nhật URL & Model tự động
  setV('ai-direct-url', p.url);
  setV('ai-direct-model', p.model);

  // 2. Cập nhật Placeholder & Gợi ý Token
  const keyInput = document.getElementById('ai-direct-api-key');
  if (keyInput) {
    keyInput.placeholder = p.placeholder;
    if (p.defaultKey && !keyInput.value.trim()) {
      keyInput.value = p.defaultKey;
    }
  }

  // 3. Link lấy Token & Badge
  const guideLink = document.getElementById('direct-token-guide-link');
  if (guideLink) {
    guideLink.href = p.guideLink;
    guideLink.innerHTML = `<span>${p.guideText}</span> <svg width="10" height="10" fill="none" stroke="currentColor" stroke-width="2" viewBox="0 0 24 24"><path d="M18 13v6a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h6"/><polyline points="15 3 21 3 21 9"/><line x1="10" y1="14" x2="21" y2="3"/></svg>`;
  }
  const badge = document.getElementById('direct-provider-badge');
  if (badge) badge.textContent = p.name;

  const hint = document.getElementById('direct-key-hint');
  if (hint) hint.textContent = p.hint;

  // 4. Highlight nút provider được chọn
  Object.keys(DIRECT_PROVIDERS).forEach(k => {
    const btn = document.getElementById(`btn-prov-${k}`);
    if (!btn) return;
    if (k === provId) {
      btn.className = 'provider-chip py-2 px-2.5 rounded-xl border text-center transition-all duration-150 flex flex-col items-center gap-1 border-orange-400/80 bg-orange-500/15 text-orange-400 font-bold shadow-sm';
    } else {
      btn.className = 'provider-chip py-2 px-2.5 rounded-xl border border-slate-200 dark:border-white/10 bg-slate-50 dark:bg-white/[0.02] text-slate-700 dark:text-slate-300 hover:border-orange-400/50 text-center transition-all duration-150 flex flex-col items-center gap-1 font-semibold';
    }
  });

  showToast(`⚡ Đã chọn ${p.name}! Chỉ cần dán Token vào ô bên dưới.`, 'info');
}

/** Ẩn/Hiện Token Direct */
function toggleAIDirectKeyVisibility() {
  const input = document.getElementById('ai-direct-api-key');
  const icon = document.getElementById('ai-direct-eye-icon');
  if (!input) return;
  if (input.type === 'password') {
    input.type = 'text';
    if (icon) icon.innerHTML = `<path d="M17.94 17.94A10.07 10.07 0 0 1 12 20c-7 0-11-8-11-8a18.45 18.45 0 0 1 5.06-5.94"/><path d="M9.9 4.24A9.12 9.12 0 0 1 12 4c7 0 11 8 11 8a18.5 18.5 0 0 1-2.16 3.19"/><line x1="1" y1="1" x2="23" y2="23"/>`;
  } else {
    input.type = 'password';
    if (icon) icon.innerHTML = `<path d="M1 12s4-8 11-8 11 8 11 8-4 8-11 8-11-8-11-8z"/><circle cx="12" cy="12" r="3"/>`;
  }
}

// Alias tương thích
const applyDirectPreset = selectDirectProvider;

/**
 * Phase 91: Chuyển đổi độc quyền 2 chế độ ngay trên Tab Cấu Hình Hệ Thống:
 *  - 'router': Dùng 9Router Gateway
 *  - 'direct': Dùng Trực Tiếp Direct LLM Engine
 */
function switchCfgRoutingMode(mode) {
  if (mode !== 'direct') mode = 'router';

  const hiddenEl = document.getElementById('cfg-routing-mode');
  if (hiddenEl) hiddenEl.value = mode;

  const tabRouter = document.getElementById('cfg-mode-tab-router');
  const tabDirect = document.getElementById('cfg-mode-tab-direct');
  const badgeRouter = document.getElementById('cfg-badge-tab-router');
  const badgeDirect = document.getElementById('cfg-badge-tab-direct');
  const panelRouter = document.getElementById('cfg-panel-router');
  const panelDirect = document.getElementById('cfg-panel-direct');
  const modeBadge = document.getElementById('cfg-routing-mode-badge');

  if (mode === 'direct') {
    if (tabDirect) {
      tabDirect.className = 'cursor-pointer p-3.5 rounded-xl border-2 transition-all duration-200 flex items-center justify-between border-orange-400 ring-2 ring-orange-400/30 bg-orange-500/10 shadow-sm opacity-100';
    }
    if (badgeDirect) {
      badgeDirect.className = 'px-2 py-0.5 rounded-full text-[10px] font-mono font-bold bg-orange-500/20 text-orange-300 border border-orange-400/40';
      badgeDirect.textContent = '● ĐANG BẬT';
    }
    if (tabRouter) {
      tabRouter.className = 'cursor-pointer p-3.5 rounded-xl border-2 transition-all duration-200 flex items-center justify-between border-slate-200 dark:border-white/10 opacity-70 hover:opacity-100 bg-slate-50 dark:bg-white/[0.02]';
    }
    if (badgeRouter) {
      badgeRouter.className = 'px-2 py-0.5 rounded-full text-[10px] font-mono font-bold bg-slate-200/60 dark:bg-white/5 text-slate-400 border border-transparent';
      badgeRouter.textContent = 'TẮT';
    }
    if (panelDirect) panelDirect.classList.remove('hidden');
    if (panelRouter) panelRouter.classList.add('hidden');
    if (modeBadge) {
      modeBadge.className = 'px-2 py-0.5 rounded text-[10px] font-bold tracking-wider uppercase font-mono bg-orange-500/20 text-orange-400 border border-orange-400/40';
      modeBadge.textContent = '⚡ CHẾ ĐỘ: DIRECT LLM';
    }
  } else {
    if (tabRouter) {
      tabRouter.className = 'cursor-pointer p-3.5 rounded-xl border-2 transition-all duration-200 flex items-center justify-between border-cyan-400 ring-2 ring-cyan-400/30 bg-cyan-500/10 shadow-sm opacity-100';
    }
    if (badgeRouter) {
      badgeRouter.className = 'px-2 py-0.5 rounded-full text-[10px] font-mono font-bold bg-cyan-500/20 text-cyan-300 border border-cyan-400/40';
      badgeRouter.textContent = '● ĐANG BẬT';
    }
    if (tabDirect) {
      tabDirect.className = 'cursor-pointer p-3.5 rounded-xl border-2 transition-all duration-200 flex items-center justify-between border-slate-200 dark:border-white/10 opacity-70 hover:opacity-100 bg-slate-50 dark:bg-white/[0.02]';
    }
    if (badgeDirect) {
      badgeDirect.className = 'px-2 py-0.5 rounded-full text-[10px] font-mono font-bold bg-slate-200/60 dark:bg-white/5 text-slate-400 border border-transparent';
      badgeDirect.textContent = 'TẮT';
    }
    if (panelRouter) panelRouter.classList.remove('hidden');
    if (panelDirect) panelDirect.classList.add('hidden');
    if (modeBadge) {
      modeBadge.className = 'px-2 py-0.5 rounded text-[10px] font-bold tracking-wider uppercase font-mono bg-cyan-500/20 text-cyan-400 border border-cyan-400/40';
      modeBadge.textContent = '🔀 CHẾ ĐỘ: 9ROUTER GATEWAY';
    }
  }
}

/** Chọn provider trên tab Cấu Hình Hệ Thống */
function selectCfgDirectProvider(provId) {
  const p = DIRECT_PROVIDERS[provId];
  if (!p) return;
  const setV = (id, v) => { const el = document.getElementById(id); if (el) el.value = v; };

  setV('cfg-direct-url', p.url);
  setV('cfg-direct-model', p.model);

  const keyInput = document.getElementById('cfg-direct-key');
  if (keyInput) {
    keyInput.placeholder = p.placeholder;
    if (p.defaultKey && !keyInput.value.trim()) keyInput.value = p.defaultKey;
  }

  const guideLink = document.getElementById('cfg-direct-token-guide-link');
  if (guideLink) {
    guideLink.href = p.guideLink;
    guideLink.innerHTML = `<span>${p.guideText}</span> <svg width="10" height="10" fill="none" stroke="currentColor" stroke-width="2" viewBox="0 0 24 24"><path d="M18 13v6a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h6"/><polyline points="15 3 21 3 21 9"/><line x1="10" y1="14" x2="21" y2="3"/></svg>`;
  }
  const badge = document.getElementById('cfg-direct-provider-badge');
  if (badge) badge.textContent = p.name;

  const hint = document.getElementById('cfg-direct-key-hint');
  if (hint) hint.textContent = p.hint;

  Object.keys(DIRECT_PROVIDERS).forEach(k => {
    const btn = document.getElementById(`btn-cfg-prov-${k}`);
    if (!btn) return;
    if (k === provId) {
      btn.className = 'provider-chip py-2 px-2.5 rounded-xl border text-center transition-all duration-150 flex flex-col items-center gap-1 border-orange-400/80 bg-orange-500/15 text-orange-400 font-bold shadow-sm';
    } else {
      btn.className = 'provider-chip py-2 px-2.5 rounded-xl border border-slate-200 dark:border-white/10 bg-slate-50 dark:bg-white/[0.02] text-slate-700 dark:text-slate-300 hover:border-orange-400/50 text-center transition-all duration-150 flex flex-col items-center gap-1 font-semibold';
    }
  });

  showToast(`⚡ Đã chọn ${p.name}! Dán Token của bạn vào ô bên dưới.`, 'info');
}

/** Ẩn/Hiện Token Direct trên tab Config */
function toggleCfgDirectKeyVisibility() {
  const input = document.getElementById('cfg-direct-key');
  const icon = document.getElementById('cfg-direct-eye-icon');
  if (!input) return;
  if (input.type === 'password') {
    input.type = 'text';
    if (icon) icon.innerHTML = `<path d="M17.94 17.94A10.07 10.07 0 0 1 12 20c-7 0-11-8-11-8a18.45 18.45 0 0 1 5.06-5.94"/><path d="M9.9 4.24A9.12 9.12 0 0 1 12 4c7 0 11 8 11 8a18.5 18.5 0 0 1-2.16 3.19"/><line x1="1" y1="1" x2="23" y2="23"/>`;
  } else {
    input.type = 'password';
    if (icon) icon.innerHTML = `<path d="M1 12s4-8 11-8 11 8 11 8-4 8-11 8-11-8-11-8z"/><circle cx="12" cy="12" r="3"/>`;
  }
}

// ─── End Phase 91 ───────────────────────────────────────────────────────────

function toggleAISwitch(id) {
  const el = document.getElementById(id);
  if (el) el.classList.toggle('on');
}

function toggleAIKeyVisibility() {
  const input = document.getElementById('ai-llm-key');
  const icon = document.getElementById('ai-eye-icon');
  if (!input) return;
  if (input.type === 'password') {
    input.type = 'text';
    if (icon) icon.innerHTML = `<path d="M17.94 17.94A10.07 10.07 0 0 1 12 20c-7 0-11-8-11-8a18.45 18.45 0 0 1 5.06-5.94"/><path d="M9.9 4.24A9.12 9.12 0 0 1 12 4c7 0 11 8 11 8a18.5 18.5 0 0 1-2.16 3.19"/><line x1="1" y1="1" x2="23" y2="23"/>`;
  } else {
    input.type = 'password';
    if (icon) icon.innerHTML = `<path d="M1 12s4-8 11-8 11 8 11 8-4 8-11 8-11-8-11-8z"/><circle cx="12" cy="12" r="3"/>`;
  }
}

function onAIASREngineChange() {
  const engine = document.getElementById('ai-asr-engine')?.value || 'google';
  const groqSection = document.getElementById('ai-groq-section');
  if (groqSection) {
    if (engine === 'groq') groqSection.classList.remove('hidden');
    else groqSection.classList.add('hidden');
  }
}

function onAITTSEngineChange() {
  const engine = document.getElementById('ai-tts-engine')?.value || 'edge-tts';
  const elSection = document.getElementById('ai-elevenlabs-section');
  if (elSection) {
    if (engine === 'elevenlabs') {
      elSection.classList.remove('hidden');
    } else {
      elSection.classList.add('hidden');
    }
  }
  document.getElementById('ai-vbee-section')?.classList.toggle('hidden', engine !== 'vbee');
  // Giọng phải khớp bộ máy: Vbee dùng mã giọng Vbee, Edge dùng tên giọng Microsoft.
  const sel = document.getElementById('ai-tts-voice');
  if (!sel) return;
  if (engine === 'vbee' && !_isVbeeVoice(sel.value)) {
    _ensureVbeeVoiceOption(VBEE_DEFAULT_VOICE, '⚡ HN - Ngọc Huyền');
    sel.value = VBEE_DEFAULT_VOICE;
  } else if (engine !== 'vbee' && _isVbeeVoice(sel.value)) {
    sel.value = 'vi-VN-HoaiMyNeural';
  }
}

// ── Vbee AIVoice (studio.vbee.vn) ───────────────────────────────────────────
const VBEE_DEFAULT_VOICE = 'hn_female_ngochuyen_full_48k-fhg';
const _vbeeVal = (id) => (document.getElementById(id)?.value || '').trim();
function _isVbeeVoice(v) { return /^[a-z]{2,}_(female|male)_/.test(String(v || '')) && !String(v).includes('Neural'); }

/** Nhóm "Vbee AIVoice" trong ô chọn giọng (tạo nếu chưa có); thêm một giọng nếu chưa có. */
function _vbeeGroup() {
  const sel = document.getElementById('ai-tts-voice');
  if (!sel) return null;
  let g = sel.querySelector('optgroup[data-vbee]');
  if (!g) {
    g = document.createElement('optgroup');
    g.label = '🇻🇳 Vbee AIVoice (⚡ = dùng được cho hội thoại)';
    g.dataset.vbee = '1';
    sel.insertBefore(g, sel.firstChild);
  }
  return g;
}
function _ensureVbeeVoiceOption(code, label) {
  const g = _vbeeGroup();
  if (!g || [...g.querySelectorAll('option')].some((o) => o.value === code)) return;
  const o = document.createElement('option');
  o.value = code; o.textContent = label || code;
  g.appendChild(o);
}
function _vbeeCredsBody(extra = {}) {
  const token = (_vbeeVal('ai-vbee-token') || '').trim();
  return JSON.stringify({ app_id: (_vbeeVal('ai-vbee-app-id') || '').trim() || null,
    token: token && token !== SECRET_MASK ? token : null, ...extra });
}
function _vbeeStatus(text, kind = '') {
  const el = document.getElementById('ai-vbee-status');
  if (!el) return;
  el.textContent = text;
  el.className = 'text-[11px] ' + (kind === 'error' ? 'text-rose-500' : kind === 'ok' ? 'text-emerald-600 dark:text-emerald-400' : 'text-slate-500 dark:text-slate-400');
}

/** Tải giọng Vbee của tài khoản (dùng App ID / Token đang gõ, hoặc đã lưu) vào ô chọn giọng. */
async function loadVbeeVoices() {
  _vbeeStatus('Đang tải giọng Vbee…');
  try {
    const res = await apiFetch(`${API_BASE}/api/v1/tts/vbee/voices`, { method: 'POST', body: _vbeeCredsBody() });
    const data = await res.json().catch(() => ({}));
    if (!res.ok || !data.ok) throw new Error(data.detail || data.error || `HTTP ${res.status}`);
    const sel = document.getElementById('ai-tts-voice');
    const keep = sel?.value;
    const g = _vbeeGroup();
    if (g) g.innerHTML = '';
    for (const v of data.voices || []) {
      _ensureVbeeVoiceOption(v.code, `${v.realtime ? '⚡ ' : ''}${v.name}${v.personal ? ' (giọng của bạn)' : ''}`);
    }
    if (sel) sel.value = _isVbeeVoice(keep) && [...sel.options].some((o) => o.value === keep) ? keep : VBEE_DEFAULT_VOICE;
    const rt = (data.voices || []).filter((v) => v.realtime).length;
    _vbeeStatus(`Đã tải ${(data.voices || []).length} giọng (${rt} giọng ⚡ dùng được cho hội thoại).`, 'ok');
  } catch (err) {
    _vbeeStatus(String(err.message || err), 'error');
  }
}

/** Nghe thử giọng Vbee đang chọn — kiểm tra App ID / Token / giọng TRƯỚC khi lưu. */
async function testVbeeVoice() {
  const voice = _vbeeVal('ai-tts-voice');
  _vbeeStatus('Đang đọc thử…');
  try {
    const res = await apiFetch(`${API_BASE}/api/v1/tts/vbee/test`, {
      method: 'POST', body: _vbeeCredsBody({ voice: _isVbeeVoice(voice) ? voice : VBEE_DEFAULT_VOICE }),
    });
    if (!res.ok) {
      const data = await res.json().catch(() => ({}));
      throw new Error(data.detail || `HTTP ${res.status}`);
    }
    const ms = res.headers.get('X-Vbee-Ms');
    const url = URL.createObjectURL(await res.blob());
    const audio = new Audio(url);
    audio.onended = () => URL.revokeObjectURL(url);
    await audio.play();
    const mode = res.headers.get('X-Vbee-Mode');
    const how = mode === 'batch' ? ' · gói hiện tại dùng Batch API (cả câu ~2–4 s); nâng gói có Realtime để nhanh hơn' : '';
    _vbeeStatus(`Vbee đọc được${ms ? ` (${ms} ms)` : ''}${how} — bấm "Lưu" để dùng cho HUD, robot, portal.`, 'ok');
  } catch (err) {
    _vbeeStatus(String(err.message || err), 'error');
  }
}
window.loadVbeeVoices = loadVbeeVoices;
window.testVbeeVoice = testVbeeVoice;

/** Sắp xếp danh sách model theo tên. Giá trị lấy từ đâu ra cũng dùng chung
 *  một thứ tự, để hai tab không lệch nhau. */
function _sortModels(list) {
  return (Array.isArray(list) ? list : [])
    .filter(m => typeof m === 'string' && m.trim())
    .sort((a, b) => a.localeCompare(b, undefined, { numeric: true }));
}

async function loadAIProxyModels() {
  const baseUrl = document.getElementById('ai-llm-base')?.value?.trim() || 'http://localhost:20128/v1';
  const rawKey = document.getElementById('ai-llm-key')?.value?.trim() || '';
  const apiKey = (rawKey && rawKey !== SECRET_MASK) ? rawKey : '';
  const wrap = document.getElementById('ai-proxy-model-picker-wrap');
  const select = document.getElementById('ai-proxy-model-select');
  if (!wrap || !select) return;

  try {
    const res = await apiFetch('/api/v1/llm/proxy-models', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ base_url: baseUrl, api_key: apiKey }),
    });
    const data = await res.json();
    if (data.success && Array.isArray(data.models) && data.models.length > 0) {
      select.innerHTML = '<option value="">-- Chọn mô hình từ 9router --</option>' +
        _sortModels(data.models).map(m => `<option value="${m}">${m}</option>`).join('');
      wrap.classList.remove('hidden');
      showToast(`✅ Đã tải ${data.models.length} model từ 9router!`, 'success');
    } else {
      // Phân biệt hai ca: proxy lỗi/không trả được, và proxy trả về rỗng.
      // Trước đây gộp chung thành "Không tìm thấy model nào" — khi thực ra là
      // request hỏng, người dùng đi tìm vấn đề ở 9router trong khi lỗi nằm ở
      // ô mã bảo mật của chính họ.
      showToast(
        data.success === false
          ? `⚠️ Không lấy được danh sách model từ 9router: ${data.error || 'lỗi không rõ'}`
          : `⚠️ 9router không có model nào để chọn (danh sách rỗng).`,
        'warning',
      );
    }
  } catch (e) {
    showToast('❌ Không kết nối được đến 9router: ' + e.message, 'error');
  }
}

async function testAILLMConnection() {
  const btn = document.getElementById('btn-ai-test-llm');
  const statusEl = document.getElementById('ai-status-test-llm');
  const isDirect = (document.getElementById('ai-routing-mode')?.value === 'direct');

  let baseUrl, modelName, apiKey;
  if (isDirect) {
    baseUrl = document.getElementById('ai-direct-url')?.value?.trim() || 'http://localhost:1234/v1';
    modelName = document.getElementById('ai-direct-model')?.value?.trim() || document.getElementById('ai-llm-model')?.value?.trim() || '';
    // Trống thì server tự dùng khoá đang lưu hoặc khoá giữ chỗ cho LM Studio/Ollama.
    apiKey = document.getElementById('ai-direct-api-key')?.value?.trim() || '';
  } else {
    baseUrl = document.getElementById('ai-llm-base')?.value?.trim() || 'http://localhost:20128/v1';
    modelName = document.getElementById('ai-llm-model')?.value?.trim() || '';
    apiKey = document.getElementById('ai-llm-key')?.value?.trim() || '';
  }

  if (!btn || !statusEl) return;

  const origHTML = btn.innerHTML;
  btn.disabled = true;
  btn.innerHTML = `<svg class="animate-spin inline mr-1" width="13" height="13" fill="none" stroke="currentColor" stroke-width="2" viewBox="0 0 24 24"><path d="M21 12a9 9 0 1 1-6.219-8.56"/></svg> Đang kiểm tra...`;
  statusEl.className = 'mt-3 text-xs p-3 rounded-xl bg-slate-100 dark:bg-white/5 text-slate-500';
  statusEl.textContent = isDirect
    ? 'Đang kết nối trực tiếp đến Direct LLM Engine (bỏ qua 9Router)...'
    : 'Đang kết nối đến 9Router Gateway...';
  statusEl.classList.remove('hidden');

  try {
    const res = await apiFetch('/api/v1/llm/test', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ base_url: baseUrl, model_name: modelName, api_key: apiKey }),
    });
    const data = await res.json();
    if (data.success) {
      const latEl = document.getElementById('ai-status-latency');
      if (latEl && data.latency_ms) latEl.textContent = `${Math.round(data.latency_ms)}ms (vừa đo)`;
      if (data.fallback_triggered) {
        statusEl.className = 'mt-3 text-xs p-3.5 rounded-xl bg-amber-50 dark:bg-amber-950/30 border border-amber-300 dark:border-amber-700/60 text-amber-800 dark:text-amber-200';
        statusEl.innerHTML = `
          <div class="flex items-center gap-1.5 font-bold mb-1 text-amber-600 dark:text-amber-300 text-sm">
            <svg width="16" height="16" fill="none" stroke="currentColor" stroke-width="2.5" viewBox="0 0 24 24"><path d="M13 2L3 14h9l-1 8 10-12h-9l1-8z"/></svg>
            ⚡ Auto-Fallback Đã Kích Hoạt! (Độ trễ: ${data.latency_ms || 0}ms)
          </div>
          <div class="text-[11px] opacity-90 mb-1 leading-relaxed">
            Mô hình chính <code>${_esc(data.requested_model)}</code> gặp sự cố. Hệ thống đã tự động chuyển đổi sang: <strong class="text-emerald-600 dark:text-emerald-400 font-mono">${_esc(data.resolved_model)}</strong>.
          </div>
          ${data.reply ? `<div class="text-[11px] mt-1.5 p-2 rounded bg-black/10 dark:bg-black/30 font-mono">Phản hồi: "${_esc(data.reply)}"</div>` : ''}
        `;
        showToast(`⚡ Chuyển đổi sang ${data.resolved_model}!`, 'info');
      } else {
        const modeBadge = isDirect
          ? '<span class="px-1.5 py-0.5 rounded bg-orange-500/20 text-orange-400 border border-orange-500/30 text-[10px] font-mono font-bold">DIRECT (Bypass Proxy)</span>'
          : '<span class="px-1.5 py-0.5 rounded bg-cyan-500/20 text-cyan-400 border border-cyan-500/30 text-[10px] font-mono font-bold">9ROUTER GATEWAY</span>';
        statusEl.className = 'mt-3 text-xs p-3.5 rounded-xl bg-emerald-50 dark:bg-emerald-950/30 border border-emerald-200 dark:border-emerald-800 text-emerald-700 dark:text-emerald-300';
        statusEl.innerHTML = `
          <div class="flex items-center justify-between font-bold mb-1">
            <div class="flex items-center gap-1.5">
              <svg width="15" height="15" fill="none" stroke="currentColor" stroke-width="2.5" viewBox="0 0 24 24"><polyline points="20 6 9 17 4 12"/></svg>
              Kết nối thành công! (Độ trễ: ${data.latency_ms || 0}ms)
            </div>
            ${modeBadge}
          </div>
          <div class="text-[11px] opacity-80 mt-1">Endpoint: <code>${_esc(baseUrl)}</code> | Mô hình: <code>${_esc(data.resolved_model || modelName || 'mặc định')}</code></div>
          ${data.reply ? `<div class="text-[11px] mt-2 p-2 rounded bg-black/10 dark:bg-black/30 font-mono">Phản hồi: "${_esc(data.reply)}"</div>` : ''}
        `;
        showToast(isDirect ? '⚡ Kết nối Direct LLM siêu tốc thành công!' : '⚡ Kết nối 9Router Gateway thành công!', 'success');
      }
    } else {
      let errMsg = data.error || data.message || 'Mô hình không phản hồi';
      let suggestionHtml = '';
      if (data.suggestion) {
        suggestionHtml = `
          <div class="mt-2.5 p-2 rounded-lg bg-amber-500/10 border border-amber-500/30 text-amber-300 text-[11px] leading-relaxed">
            ${_esc(data.suggestion)}
          </div>
        `;
      }
      statusEl.className = 'mt-3 text-xs p-3.5 rounded-xl bg-red-50 dark:bg-red-950/30 border border-red-200 dark:border-red-800 text-red-700 dark:text-red-300';
      statusEl.innerHTML = `
        <div class="flex items-center gap-1.5 font-bold mb-1 text-red-600 dark:text-red-400">
          <svg width="15" height="15" fill="none" stroke="currentColor" stroke-width="2" viewBox="0 0 24 24"><circle cx="12" cy="12" r="10"/><line x1="15" y1="9" x2="9" y2="15"/><line x1="9" y1="9" x2="15" y2="15"/></svg>
          Kết nối thất bại!
        </div>
        <div class="text-[11px] opacity-90 break-words font-mono bg-black/20 p-2 rounded">${_esc(errMsg)}</div>
        ${suggestionHtml}
      `;
      showToast('❌ Kiểm tra kết nối LLM thất bại', 'error');
      return;
    }
  } catch (e) {
    statusEl.className = 'mt-3 text-xs p-3.5 rounded-xl bg-red-50 dark:bg-red-950/30 border border-red-200 dark:border-red-800 text-red-700 dark:text-red-300';
    statusEl.innerHTML = `
      <div class="flex items-center gap-1.5 font-bold mb-1 text-red-600 dark:text-red-400">
        <svg width="15" height="15" fill="none" stroke="currentColor" stroke-width="2" viewBox="0 0 24 24"><circle cx="12" cy="12" r="10"/><line x1="15" y1="9" x2="9" y2="15"/><line x1="9" y1="9" x2="15" y2="15"/></svg>
        Kết nối thất bại!
      </div>
      <div class="text-[11px] opacity-90 break-words">${_esc(e.message)}</div>
    `;
    showToast('❌ Kiểm tra kết nối LLM thất bại: ' + e.message, 'error');
  } finally {
    btn.disabled = false;
    btn.innerHTML = origHTML;
  }
}

// ── Phase 47: AI Manager Live Telemetry & Quick Presets ─────────────────────

function updateAIManagerTelemetry() {
  const modelEl = document.getElementById('ai-status-active-model');
  const voiceEl = document.getElementById('ai-status-voice');
  const wakeEl = document.getElementById('ai-status-wake-phrase');
  const latencyEl = document.getElementById('ai-status-latency');

  const curModel = document.getElementById('ai-llm-model')?.value?.trim() || currentConfig?.llm?.model_name || currentConfig?.MODEL_NAME || '';
  if (modelEl) modelEl.textContent = curModel;

  const rawVoice = document.getElementById('ai-tts-voice')?.value || currentConfig?.audio?.tts_voice || currentConfig?.TTS_VOICE || 'vi-VN-HoaiMyNeural';
  let friendlyVoice = rawVoice;
  if (rawVoice.includes('HoaiMy')) friendlyVoice = 'Hoài My (Nữ)';
  else if (rawVoice.includes('NamMinh')) friendlyVoice = 'Nam Minh (Nam)';
  else if (rawVoice.includes('Jenny')) friendlyVoice = 'Jenny (US)';
  else if (rawVoice.includes('Guy')) friendlyVoice = 'Guy (US)';
  if (voiceEl) voiceEl.textContent = friendlyVoice;

  const curWake = document.getElementById('ai-persona-wake')?.value?.trim() || currentConfig?.persona?.wake_word || currentConfig?.WAKE_WORD || 'Hey Ly Ly';
  if (wakeEl) wakeEl.textContent = curWake;

  const opsSkillsEl = document.getElementById('ai-tribrain-ops-skills');
  if (latencyEl || opsSkillsEl) {
    apiFetch('/api/v1/health-dashboard')
      .then((r) => (r instanceof Response ? r.json() : r))
      .then((h) => {
        // Số kỹ năng THẬT (trước đây viết cứng "79 SKILLS" trong HTML).
        const n = h?.nodes?.skills_count;
        if (opsSkillsEl) opsSkillsEl.textContent = typeof n === 'number' ? `${n} SKILLS` : '— SKILLS';
        const llmSvc = h?.services?.llm_9router;
        if (!llmSvc || !latencyEl) return;
        latencyEl.textContent = llmSvc.status === 'OK' && llmSvc.latency_ms
          ? `${Math.round(llmSvc.latency_ms)}ms (đo định kỳ)`
          : (llmSvc.status === 'FAIL' ? 'không phản hồi' : '—');
      })
      .catch(() => {});
  }
}

function selectQuickModel(modelName) {
  const input = document.getElementById('ai-llm-model');
  if (input) {
    input.value = modelName;
    input.dispatchEvent(new Event('input'));
  }
  updateAIManagerTelemetry();
  showToast(`✨ Đã chọn mô hình: ${modelName}`, 'info');
}

const PERSONA_PRESETS = {
  it_admin: {
    name: 'Ly Ly',
    wake: 'Hey Ly Ly',
    aiPronoun: 'em',
    userPronoun: 'anh',
    prompt: 'Bạn là Ly Ly — trợ lý AI kiêm kỹ sư IT & tự động hóa RPA của doanh nghiệp. Phong cách: Dứt khoát, chuyên môn kỹ thuật cao, trả lời ngắn gọn, chuẩn xác. Luôn ưu tiên thực thi các công cụ hệ thống, kiểm tra tiến trình, Active Directory, an ninh mạng và đưa ra báo cáo Markdown rõ ràng.'
  },
  enterprise: {
    name: 'Ly Ly',
    wake: 'Hey Ly Ly',
    aiPronoun: 'em',
    userPronoun: 'anh/chị',
    prompt: 'Bạn là Ly Ly — trợ lý AI điều hành hành chính và nhân sự doanh nghiệp. Phong cách: Lịch sự, chu đáo, nhiệt tình, xưng em và gọi anh/chị. Hỗ trợ tra cứu nhân sự, hỗ trợ văn phòng, tổng hợp email và thông báo nội bộ một cách nhanh chóng và thân thiện.'
  },
  security_sentinel: {
    name: 'Ly Ly Sentinel',
    wake: 'Hey Ly Ly',
    aiPronoun: 'tôi',
    userPronoun: 'bạn',
    prompt: 'Bạn là Sentinel — hệ thống trợ lý giám sát phòng thủ an ninh mạng (DevSecOps) cấp doanh nghiệp. Phong cách: Nghiêm túc, kỷ luật, tuân thủ nguyên tắc Zero-Trust. Mọi lệnh can thiệp hệ thống nguy hiểm phải cảnh báo và chờ phê duyệt. Luôn phân tích log và rủi ro an ninh trước khi phản hồi.'
  },
  minimal_speed: {
    name: 'Ly Ly',
    wake: 'Hey Ly Ly',
    aiPronoun: 'em',
    userPronoun: 'anh',
    prompt: 'Bạn là Ly Ly, trợ lý AI giọng nói tốc độ cao. Trả lời cực kỳ ngắn gọn, không giải thích dài dòng, tối đa 1 đến 2 câu. Thẳng thắn, trực diện vào vấn đề.'
  }
};

function applyPersonaPreset(presetKey) {
  const p = PERSONA_PRESETS[presetKey];
  if (!p) return;

  const setEl = (id, val) => {
    const el = document.getElementById(id);
    if (el) el.value = val;
  };
  setEl('ai-persona-name', p.name);
  setEl('ai-persona-wake', p.wake);
  setEl('ai-persona-ai-pronoun', p.aiPronoun);
  setEl('ai-persona-user-pronoun', p.userPronoun);
  setEl('ai-persona-prompt', p.prompt);

  updatePromptStats();
  updateAIManagerTelemetry();
  showToast(`🎭 Đã áp dụng phong cách: ${p.name}`, 'success');
}

function updatePromptStats() {
  const text = document.getElementById('ai-persona-prompt')?.value || '';
  const charCount = text.length;
  // Estimate tokens (~1 token per 3.2 chars for Vietnamese)
  const tokenEst = Math.round(charCount / 3.2);
  const contextPct = ((tokenEst / 8192) * 100).toFixed(1);

  const charEl = document.getElementById('ai-prompt-chars');
  const tokenBadge = document.getElementById('ai-prompt-tokens-badge');
  const ctxEl = document.getElementById('ai-prompt-context-pct');

  if (!charCount) {
    // Để trống: máy chủ dùng prompt mặc định (dựng theo kênh, quyền thiết bị, ngày giờ).
    if (charEl) charEl.textContent = 'Để trống = dùng prompt mặc định của hệ thống';
    if (tokenBadge) tokenBadge.textContent = 'MẶC ĐỊNH';
    if (ctxEl) ctxEl.textContent = '';
    return;
  }
  if (charEl) charEl.textContent = `${charCount} ký tự (bổ sung vào prompt hệ thống)`;
  if (tokenBadge) tokenBadge.textContent = `~${tokenEst} tokens`;
  if (ctxEl) ctxEl.textContent = `${contextPct}% context`;
}

async function previewAITTS() {
  const btn = document.getElementById('btn-preview-tts') || document.querySelector('#tab-ai-manager button[onclick="previewAITTS()"]');
  const voice = document.getElementById('ai-tts-voice')?.value || 'vi-VN-HoaiMyNeural';
  const rateNum = parseInt(document.getElementById('ai-speech-rate')?.value || '15');
  const rate = (rateNum >= 0 ? '+' : '') + rateNum + '%';
  const phraseInput = document.getElementById('ai-tts-preview-phrase');
  const testText = phraseInput?.value?.trim() || 'Xin chào! Em là Ly Ly, trợ lý AI thông minh. Em đã sẵn sàng hỗ trợ anh hôm nay.';
  const statusTag = document.getElementById('ai-tts-status-tag');

  const origHTML = btn ? btn.innerHTML : null;
  if (btn) {
    btn.disabled = true;
    btn.innerHTML = `<svg class="animate-spin inline mr-1" width="13" height="13" fill="none" stroke="currentColor" stroke-width="2" viewBox="0 0 24 24"><path d="M21 12a9 9 0 1 1-6.219-8.56"/></svg> Đang tạo...`;
  }
  if (statusTag) statusTag.classList.remove('hidden');

  // Dừng tất cả audio đang phát để tránh nhiều giọng phát cùng lúc
  if (_currentAudioStreamQueue) _currentAudioStreamQueue.stop();
  ['audio-player', 'studio-audio-player'].forEach(id => {
    const el = document.getElementById(id);
    if (el && !el.paused) { el.pause(); el.currentTime = 0; }
  });

  try {
    const res = await apiFetch(`${API_BASE}/api/v1/tts`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        text: testText,
        voice,
        rate
      }),
    });

    if (!res.ok) {
      const errText = await res.text().catch(() => '');
      throw new Error(`Mã lỗi ${res.status}: ${errText || 'Không thể tạo âm thanh'}`);
    }

    const blob = await res.blob();
    if (!blob || blob.size === 0) {
      throw new Error('Dữ liệu âm thanh nhận về rỗng.');
    }

    const url = URL.createObjectURL(blob);
    const audio = new Audio(url);
    audio.onended = () => {
      URL.revokeObjectURL(url);
      if (statusTag) statusTag.classList.add('hidden');
    };
    audio.onerror = () => {
      if (statusTag) statusTag.classList.add('hidden');
    };
    await audio.play();
    showToast('🔊 Đang phát âm thanh nghe thử...', 'success');
  } catch (e) {
    if (statusTag) statusTag.classList.add('hidden');
    console.error('[TTS Preview] Error:', e);
    showToast('❌ Lỗi nghe thử giọng đọc: ' + e.message, 'error');
  } finally {
    if (btn) {
      btn.disabled = false;
      btn.innerHTML = origHTML;
    }
  }
}

function resetAIPersona() {
  applyPersonaPreset('it_admin');
  showToast('↩️ Đã đặt lại Persona về mặc định.', 'info');
}

/** Initialize tooltip hover behavior for .ai-tooltip-trigger elements */
function _initAITooltips() {
  const popup = document.getElementById('ai-tooltip-popup');
  if (!popup) return;

  document.querySelectorAll('.ai-tooltip-trigger').forEach(trigger => {
    trigger.style.cursor = 'pointer';

    trigger.addEventListener('mouseenter', (e) => {
      const tip = trigger.getAttribute('data-tip');
      if (!tip) return;
      popup.textContent = tip;
      popup.classList.remove('hidden');
      popup.style.opacity = '0';
      requestAnimationFrame(() => { popup.style.opacity = '1'; });
    });

    trigger.addEventListener('mousemove', (e) => {
      const vw = window.innerWidth, vh = window.innerHeight;
      let x = e.clientX + 14, y = e.clientY + 14;
      const pw = popup.offsetWidth + 20;
      const ph = popup.offsetHeight + 10;
      if (x + pw > vw) x = e.clientX - pw;
      if (y + ph > vh) y = e.clientY - ph - 10;
      popup.style.left = x + 'px';
      popup.style.top = y + 'px';
    });

    trigger.addEventListener('mouseleave', () => {
      popup.style.opacity = '0';
      setTimeout(() => popup.classList.add('hidden'), 150);
    });
  });
}

// ═══════════════════════════════════════════════════════════════════════════
// ── PHASE 28: ENTERPRISE REPORTING & TEMPLATE ENGINE ────────────────────────
// ═══════════════════════════════════════════════════════════════════════════

let currentReportTemplates = {
  incident_report: "🔴 **BÁO CÁO SỰ CỐ HỆ THỐNG**\n- **Thời gian:** {thời_gian}\n- **Dịch vụ ảnh hưởng:** {tên_dịch_vụ}\n- **Mức độ:** [Nghiêm trọng/Cảnh báo]\n- **Chi tiết lỗi:** {mô_tả}\n- **Hành động đề xuất:** {đề_xuất}",
  hr_summary: "👤 **THÔNG TIN NHÂN SỰ**\n- **Họ và tên:** {họ_tên}\n- **Phòng ban:** {phòng_ban}\n- **Chức danh:** {chức_danh}\n- **Thiết bị cấp phát:** {tên_máy_tính} ({ip})\n- **Liên hệ:** {email} | {sđt}",
  system_health: "📊 **TRẠNG THÁI HỆ THỐNG ({thời_gian})**\n- **CPU:** {cpu}%\n- **RAM:** {ram}%\n- **Active Nodes:** {số_lượng} client\n- **Cảnh báo bảo mật:** {số_lượng_cảnh_báo}"
};

let activeTemplateKey = 'incident_report';

const TEMPLATE_FRIENDLY_NAMES = {
  incident_report: '🔴 Báo Cáo Sự Cố Hệ Thống',
  hr_summary: '👤 Thông Tin Nhân Sự (AD)',
  system_health: '📊 Trạng Thái Hệ Thống (Health)',
};

function populateReportTemplates(templates) {
  if (templates && typeof templates === 'object' && Object.keys(templates).length > 0) {
    currentReportTemplates = { ...templates };
  }
  const select = document.getElementById('sel-report-template');
  if (!select) return;

  const currentSelection = select.value;
  select.innerHTML = '';

  const keys = Object.keys(currentReportTemplates);
  keys.forEach(k => {
    const opt = document.createElement('option');
    opt.value = k;
    opt.textContent = TEMPLATE_FRIENDLY_NAMES[k] || `📑 ${k}`;
    select.appendChild(opt);
  });

  const nextKey = keys.includes(currentSelection) ? currentSelection : (keys[0] || '');
  select.value = nextKey;
  onSelectReportTemplate(nextKey);
}

function onSelectReportTemplate(key) {
  if (!key) return;
  // Save current editor buffer if activeTemplateKey changed
  if (activeTemplateKey && activeTemplateKey !== key) {
    const currentBody = document.getElementById('tpl-editor-body')?.value;
    if (currentBody !== undefined && currentReportTemplates[activeTemplateKey] !== undefined) {
      currentReportTemplates[activeTemplateKey] = currentBody;
    }
  }

  activeTemplateKey = key;
  const keyInput = document.getElementById('tpl-current-key');
  const bodyInput = document.getElementById('tpl-editor-body');

  if (keyInput) keyInput.value = key;
  if (bodyInput) bodyInput.value = currentReportTemplates[key] || '';
  if (typeof renderTemplatePreview === 'function' && currentTemplateViewMode === 'preview') {
    renderTemplatePreview();
  }
}

function insertTemplateVariable(variableName) {
  const textarea = document.getElementById('tpl-editor-body');
  if (!textarea) return;

  const start = textarea.selectionStart;
  const end = textarea.selectionEnd;
  const text = textarea.value;
  textarea.value = text.substring(0, start) + variableName + text.substring(end);
  textarea.focus();
  textarea.selectionStart = textarea.selectionEnd = start + variableName.length;

  if (activeTemplateKey) {
    currentReportTemplates[activeTemplateKey] = textarea.value;
  }
  if (typeof renderTemplatePreview === 'function' && currentTemplateViewMode === 'preview') {
    renderTemplatePreview();
  }
}

async function saveReportTemplates() {
  const btn = document.getElementById('btn-save-templates');
  const textarea = document.getElementById('tpl-editor-body');

  if (activeTemplateKey && textarea) {
    currentReportTemplates[activeTemplateKey] = textarea.value;
  }

  const origHtml = btn ? btn.innerHTML : '';
  if (btn) {
    btn.disabled = true;
    btn.innerHTML = `<svg class="animate-spin" width="13" height="13" fill="none" stroke="currentColor" stroke-width="2" viewBox="0 0 24 24"><path d="M21 12a9 9 0 1 1-6.219-8.56"/></svg> Đang lưu...`;
  }

  try {
    const res = await apiFetch(`${API_BASE}/api/v1/report-templates`, {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ templates: currentReportTemplates }),
    });
    const data = await res.json();
    if (!res.ok) throw new Error(data.detail || `HTTP ${res.status}`);

    if (currentConfig) {
      currentConfig.report_templates = { ...currentReportTemplates };
    }
    showToast(data.message || '✅ Đã lưu kho biểu mẫu báo cáo tiêu chuẩn thành công!', 'success');
  } catch (err) {
    showToast(`❌ Lỗi lưu biểu mẫu: ${err.message}`, 'error');
  } finally {
    if (btn) {
      btn.disabled = false;
      btn.innerHTML = origHtml;
    }
  }
}

// ── Template Live Preview & Copy (Phase 47) ─────────────────────────────────

let currentTemplateViewMode = 'edit';

function toggleTemplateView(mode) {
  currentTemplateViewMode = mode;
  const editBtn = document.getElementById('btn-tpl-mode-edit');
  const prevBtn = document.getElementById('btn-tpl-mode-preview');
  const editor = document.getElementById('tpl-editor-body');
  const preview = document.getElementById('tpl-preview-box');

  if (mode === 'preview') {
    renderTemplatePreview();
    editor?.classList.add('hidden');
    preview?.classList.remove('hidden');

    editBtn?.classList.remove('bg-white', 'dark:bg-slate-800', 'text-cyan-600', 'dark:text-cyan-300', 'shadow-sm');
    editBtn?.classList.add('text-slate-500', 'dark:text-slate-400');
    prevBtn?.classList.add('bg-white', 'dark:bg-slate-800', 'text-cyan-600', 'dark:text-cyan-300', 'shadow-sm');
    prevBtn?.classList.remove('text-slate-500', 'dark:text-slate-400');
  } else {
    preview?.classList.add('hidden');
    editor?.classList.remove('hidden');

    prevBtn?.classList.remove('bg-white', 'dark:bg-slate-800', 'text-cyan-600', 'dark:text-cyan-300', 'shadow-sm');
    prevBtn?.classList.add('text-slate-500', 'dark:text-slate-400');
    editBtn?.classList.add('bg-white', 'dark:bg-slate-800', 'text-cyan-600', 'dark:text-cyan-300', 'shadow-sm');
    editBtn?.classList.remove('text-slate-500', 'dark:text-slate-400');
  }
}

function renderTemplatePreview() {
  const preview = document.getElementById('tpl-preview-box');
  const editor = document.getElementById('tpl-editor-body');
  if (!preview || !editor) return;

  const raw = editor.value || '';
  if (!raw.trim()) {
    preview.innerHTML = '<span class="text-slate-500 italic">Mẫu biểu rỗng. Nhập nội dung bên tab Soạn Thảo.</span>';
    return;
  }

  // Convert Markdown formatting
  let html = raw
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;');

  // Highlight placeholders like {thời_gian}
  html = html.replace(/\{([^{}]+)\}/g, '<span class="px-1.5 py-0.5 rounded text-[11px] font-mono bg-cyan-500/20 text-cyan-300 border border-cyan-500/40">{$1}</span>');

  // Bold **text**
  html = html.replace(/\*\*([^*]+)\*\*/g, '<strong class="font-bold text-white">$1</strong>');

  // Italic *text*
  html = html.replace(/\*([^*]+)\*/g, '<em class="italic text-slate-300">$1</em>');

  // Headers #, ##, ###
  html = html.replace(/^### (.*$)/gim, '<h4 class="text-xs font-bold text-violet-300 mt-2">$1</h4>');
  html = html.replace(/^## (.*$)/gim, '<h3 class="text-sm font-bold text-cyan-300 mt-2">$1</h3>');
  html = html.replace(/^# (.*$)/gim, '<h2 class="text-base font-bold text-cyan-200 mt-2 pb-1 border-b border-white/10">$1</h2>');

  // Bullet lists
  html = html.replace(/^- (.*$)/gim, '<div class="flex items-start gap-1.5 ml-2 text-slate-200"><span class="text-cyan-400 font-bold">•</span><span>$1</span></div>');

  // Newlines
  html = html.replace(/\n/g, '<br/>');

  preview.innerHTML = html;
}

async function copyTemplateToClipboard() {
  const editor = document.getElementById('tpl-editor-body');
  if (!editor || !editor.value) {
    showToast('⚠️ Không có nội dung để sao chép', 'warning');
    return;
  }
  try {
    await navigator.clipboard.writeText(editor.value);
    showToast('📋 Đã sao chép cấu trúc biểu mẫu vào Clipboard!', 'success');
  } catch (err) {
    showToast('❌ Không thể sao chép: ' + err.message, 'error');
  }
}

function showNewTemplateModal() {
  const modal = document.getElementById('modal-new-template');
  if (!modal) return;
  document.getElementById('input-new-tpl-key').value = '';
  document.getElementById('input-new-tpl-title').value = '';
  document.getElementById('input-new-tpl-body').value = '';
  modal.classList.remove('hidden');
  modal.classList.add('flex');
}

function closeNewTemplateModal() {
  const modal = document.getElementById('modal-new-template');
  if (!modal) return;
  modal.classList.add('hidden');
  modal.classList.remove('flex');
}

function confirmAddTemplate() {
  const rawKey = (document.getElementById('input-new-tpl-key')?.value || '').trim();
  const rawTitle = (document.getElementById('input-new-tpl-title')?.value || '').trim();
  const rawBody = (document.getElementById('input-new-tpl-body')?.value || '').trim();

  if (!rawKey) {
    showToast('⚠️ Vui lòng nhập mã định danh (key) cho biểu mẫu.', 'warning');
    return;
  }

  const cleanKey = rawKey.toLowerCase().replace(/[^a-z0-9_]/g, '_');
  if (rawTitle) {
    TEMPLATE_FRIENDLY_NAMES[cleanKey] = `📑 ${rawTitle}`;
  }

  currentReportTemplates[cleanKey] = rawBody || `📋 **${rawTitle || cleanKey.toUpperCase()}**\n- **Thời gian:** {thời_gian}\n- **Nội dung:** {mô_tả}`;
  closeNewTemplateModal();
  populateReportTemplates(currentReportTemplates);

  const select = document.getElementById('sel-report-template');
  if (select) {
    select.value = cleanKey;
    onSelectReportTemplate(cleanKey);
  }
  showToast(`✅ Đã thêm biểu mẫu '${cleanKey}'. Nhớ bấm 'Lưu Biểu Mẫu' để áp dụng!`, 'info');
}

function deleteCurrentTemplate() {
  if (!activeTemplateKey) return;
  const count = Object.keys(currentReportTemplates).length;
  if (count <= 1) {
    showToast('⚠️ Không thể xóa toàn bộ. Phải giữ lại ít nhất 1 biểu mẫu.', 'warning');
    return;
  }

  if (!confirm(`Bạn có chắc chắn muốn xóa biểu mẫu '${activeTemplateKey}'?`)) return;

  delete currentReportTemplates[activeTemplateKey];
  showToast(`🗑️ Đã xóa mẫu '${activeTemplateKey}'. Nhớ bấm 'Lưu Biểu Mẫu' để đồng bộ.`, 'info');
  populateReportTemplates(currentReportTemplates);
}

function resetDefaultTemplates() {
  if (!confirm('Khôi phục lại 3 biểu mẫu tiêu chuẩn ban đầu (Sự cố, Nhân sự, Hệ thống)?')) return;
  currentReportTemplates = {
    incident_report: "🔴 **BÁO CÁO SỰ CỐ HỆ THỐNG**\n- **Thời gian:** {thời_gian}\n- **Dịch vụ ảnh hưởng:** {tên_dịch_vụ}\n- **Mức độ:** [Nghiêm trọng/Cảnh báo]\n- **Chi tiết lỗi:** {mô_tả}\n- **Hành động đề xuất:** {đề_xuất}",
    hr_summary: "👤 **THÔNG TIN NHÂN SỰ**\n- **Họ và tên:** {họ_tên}\n- **Phòng ban:** {phòng_ban}\n- **Chức danh:** {chức_danh}\n- **Thiết bị cấp phát:** {tên_máy_tính} ({ip})\n- **Liên hệ:** {email} | {sđt}",
    system_health: "📊 **TRẠNG THÁI HỆ THỐNG ({thời_gian})**\n- **CPU:** {cpu}%\n- **RAM:** {ram}%\n- **Active Nodes:** {số_lượng} client\n- **Cảnh báo bảo mật:** {số_lượng_cảnh_báo}"
  };
  populateReportTemplates(currentReportTemplates);
  showToast('🔄 Đã khôi phục 3 biểu mẫu gốc. Bấm "Lưu Biểu Mẫu" để lưu lại.', 'info');
}

// ── Edge-TTS Dynamic Voice Loader (Phase 27.1) ─────────────────────────────

/** All voices loaded from backend, stored for re-filtering */
let _allTTSVoices = [];

/**
 * Fetch all 322 Edge-TTS voices from /api/v1/tts/voices and populate the select.
 */
async function loadEdgeTTSVoices() {
  const btn = document.getElementById('btn-load-voices');
  const select = document.getElementById('ai-tts-voice');
  const searchInput = document.getElementById('ai-tts-voice-search');
  const countLabel = document.getElementById('ai-voice-count-label');
  if (!select) return;

  if (btn) {
    btn.disabled = true;
    btn.innerHTML = `<svg class="animate-spin" width="10" height="10" fill="none" stroke="currentColor" stroke-width="2" viewBox="0 0 24 24"><path d="M21 12a9 9 0 1 1-6.219-8.56"/></svg> Đang tải...`;
  }

  try {
    const res = await apiFetch(`${API_BASE}/api/v1/tts/voices`);
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    const data = await res.json();
    if (!data.success || !data.voices?.length) throw new Error(data.error || 'No voices');

    _allTTSVoices = data.voices;
    const currentVoice = select.value;

    _renderVoiceDropdown(_allTTSVoices, currentVoice);

    if (searchInput) searchInput.classList.remove('hidden');
    if (countLabel) countLabel.textContent = `✅ Đã tải ${data.total} giọng từ 70+ ngôn ngữ. Dùng ô lọc để tìm nhanh.`;
    if (btn) {
      btn.disabled = false;
      btn.innerHTML = `<svg width="10" height="10" fill="none" stroke="currentColor" stroke-width="2" viewBox="0 0 24 24"><path d="M21.5 2v6h-6M2.5 22v-6h6M2 11.5a10 10 0 0 1 18.8-4.3M22 12.5a10 10 0 0 1-18.8 4.2"/></svg> ${data.total} Giọng ✓`;
    }
    showToast(`✅ Đã tải ${data.total} giọng Edge-TTS từ 70+ ngôn ngữ!`, 'success');
  } catch (e) {
    if (btn) {
      btn.disabled = false;
      btn.innerHTML = `<svg width="10" height="10" fill="none" stroke="currentColor" stroke-width="2" viewBox="0 0 24 24"><path d="M21.5 2v6h-6M2.5 22v-6h6M2 11.5a10 10 0 0 1 18.8-4.3M22 12.5a10 10 0 0 1-18.8 4.2"/></svg> Tải Lại`;
    }
    showToast('❌ Không tải được danh sách giọng: ' + e.message, 'error');
  }
}

/**
 * Filter voice dropdown by search term (locale, name, gender).
 */
function filterTTSVoices(query) {
  if (!_allTTSVoices.length) return;
  const q = query.toLowerCase().trim();
  const filtered = q ? _allTTSVoices.filter(v =>
    v.short_name.toLowerCase().includes(q) ||
    v.friendly_name.toLowerCase().includes(q) ||
    v.locale.toLowerCase().includes(q) ||
    v.gender.toLowerCase().includes(q)
  ) : _allTTSVoices;

  const select = document.getElementById('ai-tts-voice');
  const current = select?.value;
  const countLabel = document.getElementById('ai-voice-count-label');
  _renderVoiceDropdown(filtered, current);
  if (countLabel) countLabel.textContent = `${filtered.length} giọng phù hợp (tổng ${_allTTSVoices.length}).`;
}

/**
 * Render voice options grouped by locale into the select element.
 */
function _renderVoiceDropdown(voices, selectedValue, targetSelectId = 'ai-tts-voice') {
  const select = document.getElementById(targetSelectId);
  if (!select) return;

  // Group by locale prefix (vi-VN, en-US, etc.)
  const LOCALE_LABELS = {
    'vi': '🇻🇳 Tiếng Việt', 'en': '🇺🇸🇬🇧 English', 'zh': '🇨🇳 Chinese',
    'ja': '🇯🇵 Japanese', 'ko': '🇰🇷 Korean', 'fr': '🇫🇷 French',
    'de': '🇩🇪 German', 'es': '🇪🇸 Spanish', 'it': '🇮🇹 Italian',
    'pt': '🇧🇷 Portuguese', 'ru': '🇷🇺 Russian', 'ar': '🇸🇦 Arabic',
    'hi': '🇮🇳 Hindi', 'th': '🇹🇭 Thai', 'id': '🇮🇩 Indonesian',
    'ms': '🇲🇾 Malay', 'nl': '🇳🇱 Dutch', 'pl': '🇵🇱 Polish',
    'sv': '🇸🇪 Swedish', 'da': '🇩🇰 Danish', 'fi': '🇫🇮 Finnish',
    'nb': '🇳🇴 Norwegian', 'tr': '🇹🇷 Turkish', 'uk': '🇺🇦 Ukrainian',
    'cs': '🇨🇿 Czech', 'sk': '🇸🇰 Slovak', 'hu': '🇭🇺 Hungarian',
  };

  const groups = {};
  voices.forEach(v => {
    const langCode = v.locale.split('-')[0];
    const groupKey = langCode;
    if (!groups[groupKey]) groups[groupKey] = [];
    groups[groupKey].push(v);
  });

  // Sort: Vietnamese first, then English, then rest alphabetically
  const sortedKeys = Object.keys(groups).sort((a, b) => {
    if (a === 'vi') return -1;
    if (b === 'vi') return 1;
    if (a === 'en') return -1;
    if (b === 'en') return 1;
    return a.localeCompare(b);
  });

  select.innerHTML = sortedKeys.map(langCode => {
    const label = LOCALE_LABELS[langCode] || `🌐 ${langCode.toUpperCase()}`;
    const options = groups[langCode].map(v => {
      const genderIcon = v.gender === 'Female' ? '♀' : v.gender === 'Male' ? '♂' : '';
      const isSelected = v.short_name === selectedValue ? ' selected' : '';
      return `<option value="${v.short_name}"${isSelected}>${genderIcon} ${v.friendly_name.replace('Microsoft ', '').replace(' Online (Natural)', '')} (${v.locale})</option>`;
    }).join('');
    return `<optgroup label="${label}">${options}</optgroup>`;
  }).join('');

  // Restore selection
  if (selectedValue) select.value = selectedValue;
}

// ── End Phase 27 ────────────────────────────────────────────────────────────


function toggleKeyVisibility() {
  const input = document.getElementById('cfg-llm-key');
  const icon = document.getElementById('eye-icon');
  if (!input) return;
  if (input.type === 'password') {
    input.type = 'text';
    if (icon) icon.innerHTML = `<path d="M17.94 17.94A10.07 10.07 0 0 1 12 20c-7 0-11-8-11-8a18.45 18.45 0 0 1 5.06-5.94"/><path d="M9.9 4.24A9.12 9.12 0 0 1 12 4c7 0 11 8 11 8a18.5 18.5 0 0 1-2.16 3.19"/><line x1="1" y1="1" x2="23" y2="23"/>`;
  } else {
    input.type = 'password';
    if (icon) icon.innerHTML = `<path d="M1 12s4-8 11-8 11 8 11 8-4 8-11 8-11-8-11-8z"/><circle cx="12" cy="12" r="3"/>`;
  }
}

async function loadProxyModels() {
  const btn = document.getElementById('btn-load-proxy-models');
  const baseInput = document.getElementById('cfg-llm-base');
  const keyInput = document.getElementById('cfg-llm-key');
  const selectWrap = document.getElementById('proxy-model-picker-wrap');
  const select = document.getElementById('proxy-model-select');
  const datalist = document.getElementById('models-list');

  const baseUrl = (baseInput ? baseInput.value.trim() : '') || 'http://localhost:20128/v1';
  const rawKey = keyInput ? keyInput.value.trim() : '';
  const apiKey = (rawKey && rawKey !== SECRET_MASK) ? rawKey : '';

  if (btn) {
    btn.disabled = true;
    btn.innerHTML = `<svg class="animate-spin" width="10" height="10" fill="none" stroke="currentColor" stroke-width="2" viewBox="0 0 24 24"><path d="M21 12a9 9 0 1 1-6.219-8.56"/></svg> Đang tải...`;
  }

  try {
    const res = await apiFetch('/api/v1/llm/proxy-models', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ base_url: baseUrl, api_key: apiKey }),
    });
    const data = await res.json();
    if (data.success && Array.isArray(data.models) && data.models.length > 0) {
      if (select) {
        select.innerHTML = '<option value="">-- Chọn mô hình từ 9router --</option>' +
          _sortModels(data.models).map(m => `<option value="${m}">${m}</option>`).join('');
      }
      if (datalist) {
        datalist.innerHTML = _sortModels(data.models).map(m => `<option value="${m}">${m} (9router)</option>`).join('');
      }
      if (selectWrap) selectWrap.classList.remove('hidden');
      showToast(`✅ Đã tải ${data.models.length} model từ 9router!`, 'success');
    } else {
      showToast(
        data.success === false
          ? `⚠️ Không lấy được danh sách model từ proxy: ${data.error || 'lỗi không rõ'}`
          : `⚠️ Proxy không có model nào để chọn (danh sách rỗng).`,
        'warning',
      );
    }
  } catch (err) {
    showToast(`❌ Lỗi kết nối 9router để lấy model: ${err.message}`, 'error');
  } finally {
    if (btn) {
      btn.disabled = false;
      btn.innerHTML = `<svg width="11" height="11" fill="none" stroke="currentColor" stroke-width="2" viewBox="0 0 24 24"><path d="M21.5 2v6h-6M2.5 22v-6h6M2 11.5a10 10 0 0 1 18.8-4.3M22 12.5a10 10 0 0 1-18.8 4.2"/></svg> Tải từ 9router`;
    }
  }
}

async function testLLMConnection() {
  const btn = document.getElementById('btn-test-llm');
  const statusEl = document.getElementById('status-test-llm');
  const currentMode = document.getElementById('cfg-routing-mode')?.value || 'router';
  const isDirect = currentMode === 'direct';

  let baseUrl = '';
  let modelName = '';
  let apiKey = '';

  if (isDirect) {
    const directUrlInput = document.getElementById('cfg-direct-url');
    const directModelInput = document.getElementById('cfg-direct-model');
    const directKeyInput = document.getElementById('cfg-direct-key');
    baseUrl = (directUrlInput ? directUrlInput.value.trim() : '') || 'https://api.deepseek.com/v1';
    modelName = (directModelInput ? directModelInput.value.trim() : '') || 'deepseek-chat';
    const typedKey = directKeyInput ? directKeyInput.value.trim() : '';
    const storedKey = currentConfig?.llm?.direct_api_key || '';
    apiKey = typedKey || storedKey;
  } else {
    const baseInput = document.getElementById('cfg-llm-base');
    const modelInput = document.getElementById('cfg-llm-model');
    const keyInput = document.getElementById('cfg-llm-key');
    baseUrl = (baseInput ? baseInput.value.trim() : '') || 'http://localhost:20128/v1';
    modelName = (modelInput ? modelInput.value.trim() : '') || '';
    const typedKey = keyInput ? keyInput.value.trim() : '';
    const storedKey = currentConfig?.llm?.api_key || currentConfig?.routing?.primary?.api_key || '';
    apiKey = typedKey || storedKey;
  }

  if (!modelName) {
    showToast('Vui lòng nhập tên mô hình trước khi kiểm tra.', 'warning');
    return;
  }

  if (!apiKey) {
    showToast('Chưa có API key nào để kiểm tra. Nhập khoá ở ô bên trên rồi thử lại.', 'warning');
    return;
  }

  if (btn) {
    btn.disabled = true;
    btn.innerHTML = `<svg class="animate-spin" width="14" height="14" fill="none" stroke="currentColor" stroke-width="2" viewBox="0 0 24 24"><path d="M21 12a9 9 0 1 1-6.219-8.56"/></svg><span>Đang kiểm tra kết nối...</span>`;
  }
  if (statusEl) {
    statusEl.classList.remove('hidden');
    statusEl.className = 'mt-4 text-xs p-3.5 rounded-xl bg-cyan-500/10 text-cyan-600 dark:text-cyan-400 border border-cyan-500/30 animate-pulse';
    statusEl.innerHTML = `Đang gửi truy vấn thử nghiệm đến <strong>${modelName}</strong> qua <code>${baseUrl}</code>...`;
  }

  try {
    const res = await apiFetch('/api/v1/llm/test', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        base_url: baseUrl,
        model_name: modelName,
        api_key: apiKey,
      })
    });

    const data = await res.json();
    if (data.success) {
      if (statusEl) {
        if (data.fallback_triggered) {
          statusEl.className = 'mt-4 text-xs p-3.5 rounded-xl bg-amber-500/10 text-amber-700 dark:text-amber-300 border border-amber-500/30';
          statusEl.innerHTML = `
            <div class="flex items-center gap-1.5 font-bold mb-1 text-amber-600 dark:text-amber-400">
              <svg width="15" height="15" fill="none" stroke="currentColor" stroke-width="2.5" viewBox="0 0 24 24"><path d="M13 2L3 14h9l-1 8 10-12h-9l1-8z"/></svg>
              ⚡ Auto-Fallback Đã Kích Hoạt Thành Công! (Độ trễ: ${data.latency_ms}ms)
            </div>
            <div class="text-[11px] opacity-90 leading-relaxed mb-1">
              Mô hình chính <code>${data.requested_model}</code> gặp sự cố. Hệ thống đã <strong>tự động chuyển đổi dự phòng</strong> sang mô hình: <strong class="text-emerald-500 dark:text-emerald-400 font-mono">${data.resolved_model}</strong>.
            </div>
            ${data.reply ? `<div class="text-[11px] mt-1.5 text-slate-600 dark:text-slate-300 font-mono bg-black/20 p-2 rounded">AI phản hồi mẫu: "${data.reply}"</div>` : ''}
            <div class="mt-2 text-[10px] text-slate-500 dark:text-slate-400">
              👉 Khuyên dùng: Bạn có thể nhấn <button type="button" onclick="selectConfigQuickModel('${data.resolved_model}')" class="underline text-cyan-600 dark:text-cyan-400 hover:opacity-80 font-bold">chọn ${data.resolved_model}</button> làm mô hình chính.
            </div>
          `;
          showToast(`⚡ Auto-Fallback: Đã chuyển sang ${data.resolved_model}!`, 'info');
        } else {
          statusEl.className = 'mt-4 text-xs p-3.5 rounded-xl bg-emerald-500/10 text-emerald-700 dark:text-emerald-400 border border-emerald-500/30';
          statusEl.innerHTML = `
            <div class="flex items-center gap-1.5 font-bold mb-1">
              <svg width="15" height="15" fill="none" stroke="currentColor" stroke-width="2.5" viewBox="0 0 24 24"><polyline points="20 6 9 17 4 12"/></svg>
              Kết nối 9router thành công! (Độ trễ: ${data.latency_ms}ms)
            </div>
            <div class="text-[11px] opacity-90">Mô hình: <code>${data.resolved_model}</code> | Điểm nối: <code>${baseUrl}</code></div>
            ${data.reply ? `<div class="text-[11px] mt-1.5 text-slate-600 dark:text-slate-300 font-mono bg-black/20 p-2 rounded">AI phản hồi mẫu: "${data.reply}"</div>` : ''}
          `;
          showToast(`Mô hình ${data.resolved_model} hoạt động hoàn hảo (${data.latency_ms}ms)`, 'success');
        }
      }
    } else {
      if (statusEl) {
        statusEl.className = 'mt-4 text-xs p-3.5 rounded-xl bg-rose-500/10 text-rose-700 dark:text-rose-400 border border-rose-500/30';
        statusEl.innerHTML = `
          <div class="flex items-center gap-1.5 font-bold mb-1">
            <svg width="15" height="15" fill="none" stroke="currentColor" stroke-width="2" viewBox="0 0 24 24"><circle cx="12" cy="12" r="10"/><line x1="15" y1="9" x2="9" y2="15"/><line x1="9" y1="9" x2="15" y2="15"/></svg>
            Kiểm tra kết nối thất bại (${data.latency_ms || 0}ms)
          </div>
          <div class="text-[11px] break-words">Chi tiết: ${data.error}</div>
          ${data.suggestion ? `<div class="text-[11px] mt-1.5 font-semibold text-amber-600 dark:text-amber-400">💡 Gợi ý: ${data.suggestion}</div>` : ''}
        `;
      }
      showToast(`Không thể kết nối 9router: ${data.error}`, 'error');
    }
  } catch (err) {
    if (statusEl) {
      statusEl.className = 'mt-4 text-xs p-3.5 rounded-xl bg-rose-500/10 text-rose-700 dark:text-rose-400 border border-rose-500/30';
      statusEl.innerHTML = `<div class="font-bold">Lỗi mạng / máy chủ:</div><div class="text-[11px]">${err.message}</div>`;
    }
    showToast(`Lỗi kiểm tra kết nối: ${err.message}`, 'error');
  } finally {
    if (btn) {
      btn.disabled = false;
      btn.innerHTML = `<svg width="14" height="14" fill="none" stroke="currentColor" stroke-width="2" viewBox="0 0 24 24"><path d="M13 2L3 14h9l-1 8 10-12h-9l1-8z"/></svg><span>⚡ Kiểm Tra Kết Nối Điểm Nối</span>`;
    }
  }
}

// Backward compatibility alias for any caller
function testLLMTier(tier) {
  testLLMConnection();
}

/**
 * Quick selection of popular models in Tab Cấu Hình (#config).
 */
function selectConfigQuickModel(modelId) {
  const modelInput = document.getElementById('cfg-llm-model');
  if (modelInput) {
    modelInput.value = modelId;
    onConfigModelChanged();
  }
  // Synchronize immediately to AI Manager tab field
  const aiModelInput = document.getElementById('ai-llm-model');
  if (aiModelInput) aiModelInput.value = modelId;
  showToast(`⚡ Đã chọn mô hình: ${modelId}`, 'info');
}

/**
 * Sync the auto-fallback chain preview whenever model input changes.
 */
function onConfigModelChanged() {
  renderFallbackChain();
}

/**
 * Khi người dùng chọn model trong ô sổ ra ở tab Trợ lý AI:
 * đồng bộ sang tab Cấu Hình và cập nhật telemetry. Trước đây ô select tham
 * chiếu `onModelChanged` nhưng hàm này CHƯA TỪNG được định nghĩa — chọn model
 * xong không có gì xảy ra ngoài việc điền ô nhập.
 */
function onModelChanged() {
  const val = document.getElementById('ai-llm-model')?.value?.trim() || '';
  const cfgInput = document.getElementById('cfg-llm-model');
  if (cfgInput && val) cfgInput.value = val;
  renderFallbackChain();
  updateAIManagerTelemetry();
}


// ═══════════════════════════════════════════════════════════════════════════
// ── THÔNG BÁO TOAST THÂN THIỆN ─────────────────────────────────────────────
// ═══════════════════════════════════════════════════════════════════════════

function showToast(message, type = 'info') {
  const container = document.getElementById('toast-container');
  if (!container) return;

  const toast = document.createElement('div');
  const typeClass = type === 'success' ? 'toast-success' : type === 'error' ? 'toast-error' : 'toast-info';
  toast.className = `toast-item ${typeClass}`;
  toast.textContent = message;

  container.appendChild(toast);
  setTimeout(() => {
    toast.style.opacity = '0';
    toast.style.transform = 'translateY(10px)';
    toast.style.transition = 'all 0.3s ease';
    setTimeout(() => toast.remove(), 300);
  }, TOAST_DURATION);
}

// ═══════════════════════════════════════════════════════════════════════════
// ── GIÁM SÁT ĐIỂM CUỐI (LIVE MONITOR & SECURITY AUDIT) ─────────────────────
// ═══════════════════════════════════════════════════════════════════════════

let currentMonitorClientId = null;
let currentMonitorSubTab = 'screen';
let monitorAutoRefreshTimer = null;
let processesCache = [];
let processesSortKey = 'cpu';

function openLiveMonitor(clientId) {
  currentMonitorClientId = clientId;
  const modal = document.getElementById('live-monitor-modal');
  const labelId = document.getElementById('monitor-client-id-label');
  const labelIp = document.getElementById('monitor-client-ip-label');

  if (labelId) labelId.textContent = clientId;

  // Find client info from devicesData
  const clientInfo = devicesData.find(d => d.client_id === clientId);
  if (labelIp) labelIp.textContent = clientInfo ? (clientInfo.ip || '127.0.0.1') : '--';

  if (modal) modal.classList.remove('hidden');

  // Reset to screen tab by default
  switchMonitorSubTab('screen');
}

function closeLiveMonitor() {
  const modal = document.getElementById('live-monitor-modal');
  if (modal) modal.classList.add('hidden');

  // Stop auto refresh if running
  const switchAuto = document.getElementById('switch-monitor-auto');
  if (switchAuto && switchAuto.classList.contains('on')) {
    switchAuto.classList.remove('on');
  }
  if (monitorAutoRefreshTimer) {
    clearInterval(monitorAutoRefreshTimer);
    monitorAutoRefreshTimer = null;
  }
  currentMonitorClientId = null;
}

function toggleMonitorAutoRefresh(trackEl) {
  if (!trackEl) return;
  trackEl.classList.toggle('on');
  const isOn = trackEl.classList.contains('on');

  if (isOn) {
    showToast('Đã bật tự động làm mới mỗi 3 giây', 'info');
    if (monitorAutoRefreshTimer) clearInterval(monitorAutoRefreshTimer);
    monitorAutoRefreshTimer = setInterval(() => {
      refreshCurrentMonitorTab();
    }, 3000);
  } else {
    showToast('Đã tắt tự động làm mới', 'info');
    if (monitorAutoRefreshTimer) {
      clearInterval(monitorAutoRefreshTimer);
      monitorAutoRefreshTimer = null;
    }
  }
}

function switchMonitorSubTab(subTabName) {
  currentMonitorSubTab = subTabName;
  const tabs = ['screen', 'processes', 'security', 'network'];

  tabs.forEach(t => {
    const btn = document.getElementById(`subtab-btn-${t}`);
    const content = document.getElementById(`subtab-content-${t}`);

    if (t === subTabName) {
      if (btn) {
        btn.className = 'px-3.5 py-1.5 rounded-lg font-bold bg-cyan-500/20 text-cyan-300 border border-cyan-400/40 flex items-center gap-1.5 transition-all';
      }
      if (content) content.classList.remove('hidden');
    } else {
      if (btn) {
        btn.className = 'px-3.5 py-1.5 rounded-lg font-semibold text-slate-400 hover:text-white hover:bg-white/5 flex items-center gap-1.5 transition-all';
      }
      if (content) content.classList.add('hidden');
    }
  });

  // Fetch data immediately for the selected subtab
  refreshCurrentMonitorTab();
}

function refreshCurrentMonitorTab() {
  if (!currentMonitorClientId) return;

  const spinIcon = document.getElementById('icon-refresh-spin');
  if (spinIcon) spinIcon.classList.add('animate-spin');

  let promise;
  if (currentMonitorSubTab === 'screen') {
    promise = fetchLiveScreen();
  } else if (currentMonitorSubTab === 'processes') {
    promise = fetchLiveProcesses();
  } else if (currentMonitorSubTab === 'security') {
    promise = fetchLiveSecurity();
  } else if (currentMonitorSubTab === 'network') {
    promise = fetchLiveNetwork();
  }

  if (promise) {
    promise.finally(() => {
      setTimeout(() => {
        if (spinIcon) spinIcon.classList.remove('animate-spin');
      }, 400);
    });
  } else {
    if (spinIcon) spinIcon.classList.remove('animate-spin');
  }
}

// ── 1. LIVE SCREEN ──
async function fetchLiveScreen() {
  if (!currentMonitorClientId) return;
  const imgEl = document.getElementById('monitor-screen-img');
  const loadingOverlay = document.getElementById('screen-loading-overlay');
  const emptyHint = document.getElementById('screen-empty-hint');
  const resMeta = document.getElementById('screen-meta-res');
  const sizeMeta = document.getElementById('screen-meta-size');
  const timeMeta = document.getElementById('screen-meta-time');

  // If no image is displayed yet, show loading overlay
  if (!imgEl.src || imgEl.src === '') {
    if (loadingOverlay) loadingOverlay.classList.remove('hidden');
  }

  try {
    const res = await apiFetch(`/api/v1/clients/${encodeURIComponent(currentMonitorClientId)}/monitor/screen?quality=65&max_width=1280`);
    if (loadingOverlay) loadingOverlay.classList.add('hidden');

    if (!res.ok) {
      const errText = await res.text().catch(() => '');
      throw new Error(`HTTP ${res.status}: ${errText || 'Lỗi chụp màn hình'}`);
    }
    const data = await res.json();

    if (data && data.status === 'success' && data.result && data.result.image_base64) {
      if (emptyHint) emptyHint.classList.add('hidden');
      imgEl.src = `data:image/jpeg;base64,${data.result.image_base64}`;
      if (resMeta) resMeta.textContent = `${data.result.width} × ${data.result.height} px`;
      if (sizeMeta) sizeMeta.textContent = `${data.result.size_kb || '--'} KB`;
      if (timeMeta) timeMeta.textContent = data.result.timestamp || new Date().toLocaleTimeString('vi-VN');
    } else {
      const err = (data && data.result && data.result.message) || (data && data.error) || 'Lỗi chụp màn hình';
      showToast(`⚠️ Không thể chụp màn hình: ${err}`, 'error');
    }
  } catch (err) {
    if (loadingOverlay) loadingOverlay.classList.add('hidden');
    logger_error('Lỗi khi lấy ảnh màn hình:', err);
  }
}

// ── 2. LIVE PROCESSES ──
async function fetchLiveProcesses() {
  if (!currentMonitorClientId) return;
  const tbody = document.getElementById('proc-table-body');
  if (!tbody) return;

  try {
    const res = await apiFetch(`/api/v1/clients/${encodeURIComponent(currentMonitorClientId)}/monitor/processes?limit=25&sort_by=${processesSortKey}`);
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    const data = await res.json();
    if (data && data.status === 'success' && data.result && data.result.processes) {
      processesCache = data.result.processes;
      renderProcessesTable(processesCache);
    } else {
      showToast('⚠️ Không thể tải danh sách tiến trình', 'error');
    }
  } catch (err) {
    logger_error('Lỗi tải tiến trình:', err);
  }
}

function sortProcessesBy(key) {
  processesSortKey = key;
  const btnCpu = document.getElementById('btn-sort-cpu');
  const btnMem = document.getElementById('btn-sort-mem');

  if (key === 'cpu') {
    if (btnCpu) btnCpu.className = 'px-2.5 py-1 rounded-lg bg-cyan-500/20 text-cyan-300 border border-cyan-400/40 font-semibold';
    if (btnMem) btnMem.className = 'px-2.5 py-1 rounded-lg bg-white/5 text-slate-400 hover:text-white border border-white/10 font-semibold';
  } else {
    if (btnCpu) btnCpu.className = 'px-2.5 py-1 rounded-lg bg-white/5 text-slate-400 hover:text-white border border-white/10 font-semibold';
    if (btnMem) btnMem.className = 'px-2.5 py-1 rounded-lg bg-purple-500/20 text-purple-300 border border-purple-400/40 font-semibold';
  }
  fetchLiveProcesses();
}

function filterProcessesList() {
  const query = (document.getElementById('filter-proc-input')?.value || '').toLowerCase().trim();
  if (!query) {
    renderProcessesTable(processesCache);
    return;
  }
  const filtered = processesCache.filter(p =>
    (p.name && p.name.toLowerCase().includes(query)) ||
    (String(p.pid).includes(query))
  );
  renderProcessesTable(filtered);
}

function renderProcessesTable(list) {
  const tbody = document.getElementById('proc-table-body');
  if (!tbody) return;
  tbody.innerHTML = '';

  if (!list || list.length === 0) {
    tbody.innerHTML = `<tr><td colspan="7" class="py-6 text-center text-slate-400 text-xs">Không tìm thấy tiến trình nào phù hợp.</td></tr>`;
    return;
  }

  list.forEach(p => {
    const tr = document.createElement('tr');
    tr.className = 'hover:bg-white/[0.02] transition-colors';

    const cpuPct = Number(p.cpu_percent || 0);
    const memPct = Number(p.memory_percent || 0);

    const cpuColor = cpuPct > 20 ? 'text-rose-400 font-bold' : cpuPct > 5 ? 'text-amber-300' : 'text-slate-300';
    const memColor = memPct > 20 ? 'text-rose-400 font-bold' : memPct > 5 ? 'text-purple-300' : 'text-slate-300';

    tr.innerHTML = `
      <td class="py-2.5 px-3 font-mono text-cyan-300 font-semibold">${p.pid}</td>
      <td class="py-2.5 px-3 font-medium text-white max-w-[200px] truncate" title="${p.name}">${p.name}</td>
      <td class="py-2.5 px-3 font-mono ${cpuColor}">
        <div class="flex items-center gap-2">
          <span>${cpuPct}%</span>
          <div class="w-14 bg-white/10 h-1.5 rounded-full overflow-hidden hidden sm:block">
            <div class="bg-cyan-400 h-full rounded-full" style="width: ${Math.min(cpuPct, 100)}%"></div>
          </div>
        </div>
      </td>
      <td class="py-2.5 px-3 font-mono ${memColor}">
        <div class="flex items-center gap-2">
          <span>${memPct}%</span>
          <div class="w-14 bg-white/10 h-1.5 rounded-full overflow-hidden hidden sm:block">
            <div class="bg-purple-400 h-full rounded-full" style="width: ${Math.min(memPct, 100)}%"></div>
          </div>
        </div>
      </td>
      <td class="py-2.5 px-3 text-slate-400 text-[11px]">${p.username || '-'}</td>
      <td class="py-2.5 px-3">
        <span class="px-2 py-0.5 rounded text-[10px] font-bold bg-white/5 border border-white/10 text-slate-300">${p.status || 'running'}</span>
      </td>
      <td class="py-2.5 px-3 text-right">
        <button onclick="killClientProcess(${p.pid}, '${p.name.replace(/'/g, "\\'")}')" class="px-2 py-0.5 rounded bg-rose-500/15 hover:bg-rose-500/30 text-rose-300 border border-rose-500/30 text-[10px] font-semibold transition-all">
          Tắt PID
        </button>
      </td>
    `;
    tbody.appendChild(tr);
  });
}

async function killClientProcess(pid, name) {
  if (!currentMonitorClientId) return;
  if (!confirm(`Bạn có chắc chắn muốn kết thúc tiến trình "${name}" (PID: ${pid}) trên máy trạm?`)) {
    return;
  }

  try {
    const res = await apiFetch(`/api/v1/clients/${encodeURIComponent(currentMonitorClientId)}/kill-process`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ pid: pid }),
    });
    const data = await res.json();

    if (data && data.status === 'success') {
      // Phase 73: báo đúng nội dung server trả về. Trước đây response rỗng vẫn
      // hiện "Đã tắt tiến trình" — tức khẳng định việc đã xảy ra khi chưa có bằng chứng.
      showToast(data.result?.message ? `✅ ${data.result.message}` : '✅ Server báo thành công, nhưng không trả về nội dung cụ thể.', 'success');
      setTimeout(fetchLiveProcesses, 500);
    } else {
      const err = (data && data.result && data.result.message) || (data && data.error) || 'Không thể tắt tiến trình';
      showToast(`❌ ${err}`, 'error');
    }
  } catch (err) {
    showToast(`❌ Lỗi gửi lệnh tắt tiến trình: ${err.message}`, 'error');
  }
}

// ── 3. LIVE SECURITY AUDIT ──
async function fetchLiveSecurity() {
  if (!currentMonitorClientId) return;
  try {
    const res = await apiFetch(`/api/v1/clients/${encodeURIComponent(currentMonitorClientId)}/monitor/security`);
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    const data = await res.json();
    if (data && data.status === 'success' && data.result) {
      const r = data.result;

      // Rating banner
      const ratingBanner = document.getElementById('sec-rating-banner');
      const ratingTitle = document.getElementById('sec-rating-title');
      const ratingDesc = document.getElementById('sec-rating-desc');
      const ratingBadge = document.getElementById('sec-rating-badge');

      const isSecure = r.overall_rating && r.overall_rating.includes('AN TOÀN');
      if (isSecure) {
        if (ratingBanner) ratingBanner.className = 'p-4 rounded-xl bg-emerald-950/30 border border-emerald-500/40 flex items-center justify-between';
        if (ratingTitle) ratingTitle.textContent = 'ĐÁNH GIÁ: AN TOÀN (SECURE)';
        if (ratingDesc) ratingDesc.textContent = 'Hệ thống diệt virus và tường lửa đang hoạt động bảo vệ toàn diện.';
        if (ratingBadge) {
          ratingBadge.className = 'px-3 py-1 rounded-full text-xs font-bold bg-emerald-500/20 text-emerald-300 border border-emerald-500/40 font-mono';
          ratingBadge.textContent = 'BẢO VỆ TỐI ĐA';
        }
      } else {
        if (ratingBanner) ratingBanner.className = 'p-4 rounded-xl bg-amber-950/30 border border-amber-500/40 flex items-center justify-between';
        if (ratingTitle) ratingTitle.textContent = 'ĐÁNH GIÁ: CẢNH BÁO (WARNING)';
        if (ratingDesc) ratingDesc.textContent = 'Phát hiện một số tính năng bảo vệ thời gian thực hoặc tường lửa đang bị tắt.';
        if (ratingBadge) {
          ratingBadge.className = 'px-3 py-1 rounded-full text-xs font-bold bg-amber-500/20 text-amber-300 border border-amber-500/40 font-mono';
          ratingBadge.textContent = 'CẦN KIỂM TRA';
        }
      }

      // Defender
      const def = r.defender || {};
      const avEnabled = document.getElementById('sec-av-enabled');
      const avRealtime = document.getElementById('sec-av-realtime');
      const avSig = document.getElementById('sec-av-sig');
      const avScan = document.getElementById('sec-av-scan');

      if (avEnabled) {
        avEnabled.textContent = def.antivirus_enabled ? 'BẬT (Hoạt động)' : 'ĐÃ TẮT';
        avEnabled.className = def.antivirus_enabled ? 'font-bold text-emerald-400' : 'font-bold text-rose-400';
      }
      if (avRealtime) {
        avRealtime.textContent = def.realtime_protection ? 'ĐANG BẬT' : 'ĐÃ TẮT';
        avRealtime.className = def.realtime_protection ? 'font-bold text-emerald-400' : 'font-bold text-rose-400';
      }
      if (avSig) avSig.textContent = def.signature_version || 'Mới nhất';
      if (avScan) avScan.textContent = def.quick_scan_time || 'Gần đây';

      // Firewall
      const fwContainer = document.getElementById('sec-firewall-list');
      if (fwContainer) {
        fwContainer.innerHTML = '';
        const fwProfiles = r.firewall || [];
        fwProfiles.forEach(f => {
          const row = document.createElement('div');
          row.className = 'flex justify-between items-center py-1';
          row.innerHTML = `
            <span class="text-slate-400">${f.profile}:</span>
            <span class="${f.enabled ? 'text-emerald-400 font-bold' : 'text-rose-400 font-bold'}">${f.action}</span>
          `;
          fwContainer.appendChild(row);
        });
      }
    }
  } catch (err) {
    logger_error('Lỗi tải dữ liệu kiểm toán an ninh:', err);
  }
}

// ── 4. LIVE NETWORK & PERIPHERALS ──
async function fetchLiveNetwork() {
  if (!currentMonitorClientId) return;

  // A. Peripherals
  try {
    const resPeriph = await apiFetch(`/api/v1/clients/${encodeURIComponent(currentMonitorClientId)}/monitor/peripherals`);
    const grid = document.getElementById('periph-grid');
    const countEl = document.getElementById('periph-count');

    if (resPeriph.ok) {
      const dataPeriph = await resPeriph.json();
      if (dataPeriph && dataPeriph.status === 'success' && dataPeriph.result) {
        const devs = dataPeriph.result.peripherals || [];
        if (countEl) countEl.textContent = `${devs.length} thiết bị`;
        if (grid) {
          grid.innerHTML = '';
          devs.forEach(d => {
            const card = document.createElement('div');
            card.className = 'p-2.5 rounded-xl bg-white/[0.02] border border-white/5 flex items-start gap-2.5';
            card.innerHTML = `
              <div class="w-7 h-7 rounded-lg bg-cyan-500/20 border border-cyan-400/30 flex items-center justify-center text-cyan-400 flex-shrink-0 mt-0.5">
                <svg width="14" height="14" fill="none" stroke="currentColor" stroke-width="2" viewBox="0 0 24 24"><rect x="2" y="3" width="20" height="14" rx="2"/><line x1="8" y1="21" x2="16" y2="21"/></svg>
              </div>
              <div class="min-w-0 flex-1">
                <div class="font-bold text-white text-[11px] truncate" title="${d.name}">${d.name}</div>
                <div class="text-[10px] text-slate-400 font-mono truncate">${d.device_id || '-'}</div>
                <div class="flex items-center gap-1 mt-1 text-[10px] text-emerald-400">
                  <span class="w-1.5 h-1.5 rounded-full bg-emerald-400"></span>
                  <span>${d.status || 'OK'} (${d.type || 'USB'})</span>
                </div>
              </div>
            `;
            grid.appendChild(card);
          });
        }
      }
    }
  } catch (err) {
    logger_error('Lỗi tải thiết bị ngoại vi:', err);
  }

  // B. Network Sockets
  try {
    const resNet = await apiFetch(`/api/v1/clients/${encodeURIComponent(currentMonitorClientId)}/monitor/network?limit=30`);
    const tbody = document.getElementById('net-table-body');
    const countEl = document.getElementById('net-count');

    if (resNet.ok) {
      const dataNet = await resNet.json();
      if (dataNet && dataNet.result) {
        const conns = dataNet.result.connections || [];
        if (countEl) countEl.textContent = `${conns.length} kết nối`;
        if (tbody) {
          tbody.innerHTML = '';
          if (conns.length === 0) {
            tbody.innerHTML = `<tr><td colspan="4" class="py-4 text-center text-slate-500 text-[11px]">${dataNet.result.message || 'Không có kết nối nào'}</td></tr>`;
          } else {
            conns.forEach(c => {
              const tr = document.createElement('tr');
              tr.className = 'hover:bg-white/[0.02] transition-colors';
              const statusColor = c.status === 'ESTABLISHED' ? 'text-emerald-400 font-semibold' : c.status === 'LISTEN' ? 'text-cyan-300' : 'text-slate-400';
              tr.innerHTML = `
                <td class="py-2 px-3 text-cyan-300">${c.local_address}</td>
                <td class="py-2 px-3 text-slate-300">${c.remote_address}</td>
                <td class="py-2 px-3 ${statusColor}">${c.status}</td>
                <td class="py-2 px-3 text-slate-400">${c.process_name} (PID: ${c.pid})</td>
              `;
              tbody.appendChild(tr);
            });
          }
        }
      }
    }
  } catch (err) {
    logger_error('Lỗi tải kết nối mạng:', err);
  }
}

function logger_error(...args) {
  console.error('[VN-MateAI Monitor]', ...args);
}

// ═══════════════════════════════════════════════════════════════════════════
// ── TRUNG TÂM PHÒNG THỦ AN NINH & KIỂM TOÁN (SECURITY CENTER) ──────────────
// ═══════════════════════════════════════════════════════════════════════════

function escapeHtml(str) {
  if (str === null || str === undefined) return '';
  return String(str)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#039;');
}

let currentSecurityPolicyTab = 'blacklist';
let securityPolicies = {
  blacklist: [],
  confirm_actions: [],
  protected_dirs: []
};

// ─── Thiết bị & Robot: quyền + phê duyệt đã nhớ ─────────────────────────────
const DEVICE_ROLE_LABELS = {
  '': 'Mặc định (theo mã thiết bị)',
  admin: 'Quản trị (admin)',
  it_support: 'Hỗ trợ IT',
  operator: 'Vận hành',
  viewer: 'Chỉ xem',
};

async function loadDeviceAccess() {
  const box = document.getElementById('device-access-list');
  if (!box) return;
  try {
    const res = await apiFetch(`${API_BASE}/api/v1/security/devices`);
    if (!res.ok) {
      box.textContent = res.status === 403 ? 'Chỉ tài khoản admin xem được mục này.' : `Lỗi tải (${res.status}).`;
      return;
    }
    const devices = (await res.json()).devices || [];
    if (!devices.length) {
      box.textContent = 'Chưa có thiết bị nào được cấp token riêng.';
      return;
    }
    const approvals = await Promise.all(devices.map(async (d) => {
      try {
        const r = await apiFetch(`${API_BASE}/api/v1/security/devices/${encodeURIComponent(d.device_id)}/approvals`);
        return r.ok ? ((await r.json()).approvals || []) : [];
      } catch (e) { return []; }
    }));
    box.innerHTML = devices.map((d, i) => {
      const id = escapeHtml(d.device_id);
      const options = Object.entries(DEVICE_ROLE_LABELS).map(([v, label]) =>
        `<option value="${v}" ${(d.role || '') === v ? 'selected' : ''}>${escapeHtml(label)}</option>`).join('');
      const grants = approvals[i];
      const grantHtml = grants.length
        ? grants.map((g) => `<span class="inline-flex items-center gap-1 px-2 py-0.5 rounded bg-emerald-500/10 text-emerald-700 dark:text-emerald-300 font-mono text-[10px]">${escapeHtml(g.tool_name)}
            <button type="button" title="Thu hồi" class="text-rose-500 hover:text-rose-700" data-device="${id}" data-tool="${escapeHtml(g.tool_name)}" onclick="revokeDeviceApproval(this.dataset.device, this.dataset.tool)">✕</button></span>`).join(' ')
          + ` <button type="button" class="text-[10px] text-rose-500 hover:underline" data-device="${id}" onclick="revokeDeviceApproval(this.dataset.device, null)">Thu hồi tất cả</button>`
        : '<span class="text-slate-400">Chưa có tác vụ nào được duyệt.</span>';
      return `<div class="p-3 rounded-lg border border-slate-200 dark:border-slate-700 space-y-2">
          <div class="flex flex-wrap items-center justify-between gap-2">
            <div>
              <div class="font-mono font-semibold text-slate-800 dark:text-slate-100">${id}</div>
              <div class="text-[10px] text-slate-400">Kết nối gần nhất: ${escapeHtml(d.last_seen_at ? fmtServerTime(d.last_seen_at) : 'chưa kết nối')}</div>
            </div>
            <label class="flex items-center gap-2 text-[11px] text-slate-600 dark:text-slate-300">Quyền
              <select class="px-2 py-1 rounded border border-slate-300 dark:border-slate-600 bg-white dark:bg-slate-800"
                data-device="${id}" onchange="setDeviceRole(this.dataset.device, this.value)">${options}</select>
            </label>
          </div>
          <div class="text-[11px]"><span class="text-slate-500">Đã duyệt (không hỏi lại):</span> ${grantHtml}</div>
        </div>`;
    }).join('');
  } catch (err) {
    console.error('[DeviceAccess]', err);
    box.textContent = 'Lỗi tải danh sách thiết bị.';
  }
}

async function setDeviceRole(deviceId, role) {
  const res = await apiFetch(`${API_BASE}/api/v1/security/devices/${encodeURIComponent(deviceId)}/role`, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ role: role || null }),
  });
  if (res.ok) {
    showToast(`Đã đặt quyền "${DEVICE_ROLE_LABELS[role || ''] || role}" cho ${deviceId}`, 'success');
  } else {
    const err = await res.json().catch(() => ({}));
    showToast(err.detail || `Không đặt được quyền (${res.status})`, 'error');
  }
  loadDeviceAccess();
}

async function revokeDeviceApproval(deviceId, toolName) {
  const what = toolName ? `tác vụ "${toolName}"` : 'TẤT CẢ tác vụ đã duyệt';
  if (!confirm(`Thu hồi ${what} của ${deviceId}? Lần sau thiết bị sẽ phải chờ duyệt lại.`)) return;
  const q = toolName ? `?tool_name=${encodeURIComponent(toolName)}` : '';
  const res = await apiFetch(`${API_BASE}/api/v1/security/devices/${encodeURIComponent(deviceId)}/approvals${q}`, {
    method: 'DELETE',
  });
  showToast(res.ok ? 'Đã thu hồi.' : `Không thu hồi được (${res.status})`, res.ok ? 'success' : 'error');
  loadDeviceAccess();
}
window.loadDeviceAccess = loadDeviceAccess;
window.setDeviceRole = setDeviceRole;
window.revokeDeviceApproval = revokeDeviceApproval;

async function loadSecurityCenter() {
  try {
    const [blData, auditData] = await Promise.all([
      apiGetBlacklist(),
      apiGetAuditLogs(100),
    ]);

    if (blData && blData.status === 'success') {
      securityPolicies.blacklist = blData.forbidden_keywords || [];
      securityPolicies.confirm_actions = blData.require_confirmation_actions || [];
      securityPolicies.protected_dirs = blData.protected_directories || [];
      securityBlacklist = securityPolicies.blacklist;
      renderSecurityChips();
      updateSecurityPoliciesCount();
    }

    loadDeviceAccess();

    // Always fetch pending queue first so audit logs and stats can match with active pending state
    await checkPendingAction();

    if (auditData && auditData.status === 'success') {
      securityAuditLogs = auditData.logs || [];
      renderAuditLogsTable(securityAuditLogs);
      updateSecurityStats(securityAuditLogs);
    }
  } catch (err) {
    console.error('[Security] Lỗi tải trung tâm bảo mật:', err);
    showToast('Lỗi khi tải dữ liệu bảo mật', 'error');
  }
}

function updateSecurityPoliciesCount() {
  const policiesEl = document.getElementById('stat-sec-policies');
  if (policiesEl) {
    const total = (securityPolicies.blacklist?.length || 0) +
      (securityPolicies.confirm_actions?.length || 0) +
      (securityPolicies.protected_dirs?.length || 0);
    policiesEl.textContent = total;
  }
}

function updateSecurityStats(logs) {
  const totalEl = document.getElementById('stat-sec-total');
  const blockedEl = document.getElementById('stat-sec-blocked');
  const pendingEl = document.getElementById('stat-sec-pending');

  if (totalEl) totalEl.textContent = logs.length;

  const blockedCount = logs.filter(l => {
    const r = (l.risk || l.risk_level || '').toUpperCase();
    const s = (l.status || '').toUpperCase();
    return r === 'BLOCKED' || s.includes('BLOCKED') || s === 'REJECTED';
  }).length;
  if (blockedEl) blockedEl.textContent = blockedCount;

  // Count active pending actions from server queue + pending logs
  const pendingFromLogs = logs.filter(l => {
    const r = (l.risk || l.risk_level || '').toUpperCase();
    const s = (l.status || '').toUpperCase();
    return (r === 'NEED_CONFIRM' || s.includes('CONFIRM') || s.includes('PENDING')) &&
      (s.includes('WAITING') || s.includes('PENDING') || s.includes('NEED_CONFIRM')) &&
      !s.includes('USER_APPROVED') && !s.includes('USER_REJECTED') && !s.includes('SUCCESS');
  }).length;

  const totalPending = Math.max(pendingFromLogs, (latestPendingActions ? latestPendingActions.length : 0));
  if (pendingEl) pendingEl.textContent = totalPending;

  updateSecurityPoliciesCount();
}

function selectSecurityPolicyTab(tab) {
  currentSecurityPolicyTab = tab;

  // Update tab buttons styling
  const tabs = ['blacklist', 'confirm_actions', 'protected_dirs'];
  tabs.forEach(t => {
    const btn = document.getElementById(`tab-btn-sec-${t}`);
    if (btn) {
      if (t === tab) {
        btn.className = `px-2.5 py-1 text-xs font-semibold rounded-lg shadow-sm transition ${t === 'blacklist' ? 'bg-white dark:bg-slate-700 text-rose-600 dark:text-rose-400' :
          t === 'confirm_actions' ? 'bg-white dark:bg-slate-700 text-amber-600 dark:text-amber-400' :
            'bg-white dark:bg-slate-700 text-sky-600 dark:text-sky-400'
          }`;
      } else {
        btn.className = 'px-2.5 py-1 text-xs font-semibold rounded-lg text-slate-600 dark:text-slate-300 hover:text-slate-900 dark:hover:text-white transition';
      }
    }
  });

  // Update descriptions and input placeholders
  const descEl = document.getElementById('sec-policy-tab-desc');
  const inputEl = document.getElementById('input-new-security-item');
  const labelEl = document.getElementById('sec-chips-label');
  const btnTextEl = document.getElementById('btn-text-add-policy');

  if (tab === 'blacklist') {
    if (descEl) descEl.textContent = 'Danh sách đen: Mọi câu lệnh, tham số hoặc mã nguồn chứa từ khóa này sẽ bị từ chối tức thì (BLOCKED).';
    if (inputEl) inputEl.placeholder = 'Nhập từ khóa cấm mới (vd: rmdir /s, format c:, eval(, Remove-Item)...';
    if (labelEl) labelEl.textContent = 'TỪ KHÓA BỊ CẤM TỨC THÌ (NHẤN × ĐỂ GỠ BỎ):';
    if (btnTextEl) btnTextEl.textContent = 'Thêm Từ Khóa Cấm';
  } else if (tab === 'confirm_actions') {
    if (descEl) descEl.textContent = 'Hành động cần duyệt (HITL): Các tác vụ nhạy cảm này sẽ kích hoạt hàng đợi phê duyệt của Quản trị viên trước khi chạy.';
    if (inputEl) inputEl.placeholder = 'Nhập tên hàm/tác vụ cần duyệt (vd: kill_process, deploy_skill, delete_records)...';
    if (labelEl) labelEl.textContent = 'HÀNH ĐỘNG CẦN PHÊ DUYỆT (NHẤN × ĐỂ GỠ BỎ):';
    if (btnTextEl) btnTextEl.textContent = 'Thêm Tác Vụ Cần Duyệt';
  } else if (tab === 'protected_dirs') {
    if (descEl) descEl.textContent = 'Thư mục hệ thống: Mã nguồn AI bị cấm tuyệt đối can thiệp hoặc truy xuất các đường dẫn này.';
    if (inputEl) inputEl.placeholder = 'Nhập đường dẫn thư mục bảo vệ (vd: C:\\Windows, /etc, /var, C:\\Program Files)...';
    if (labelEl) labelEl.textContent = 'THƯ MỤC ĐƯỢC BẢO VỆ (NHẤN × ĐỂ GỠ BỎ):';
    if (btnTextEl) btnTextEl.textContent = 'Thêm Thư Mục Bảo Vệ';
  }

  renderSecurityChips();
}

function renderSecurityChips() {
  const container = document.getElementById('security-chips-container');
  const countEl = document.getElementById('sec-chips-count');
  if (!container) return;
  container.innerHTML = '';

  const list = securityPolicies[currentSecurityPolicyTab] || [];
  if (countEl) countEl.textContent = `${list.length} mục`;

  if (list.length === 0) {
    container.innerHTML = '<span class="text-xs text-slate-500 italic p-1">Chưa có mục nào trong danh mục này.</span>';
    return;
  }

  const isBlacklist = currentSecurityPolicyTab === 'blacklist';
  const isConfirm = currentSecurityPolicyTab === 'confirm_actions';
  const colorClass = isBlacklist
    ? 'bg-rose-500/15 border-rose-500/30 text-rose-300 hover:border-rose-400'
    : isConfirm
      ? 'bg-amber-500/15 border-amber-500/30 text-amber-300 hover:border-amber-400'
      : 'bg-sky-500/15 border-sky-500/30 text-sky-300 hover:border-sky-400';

  const btnColor = isBlacklist
    ? 'text-rose-400 hover:bg-rose-500/30'
    : isConfirm
      ? 'text-amber-400 hover:bg-amber-500/30'
      : 'text-sky-400 hover:bg-sky-500/30';

  list.forEach(item => {
    const chip = document.createElement('div');
    chip.className = `inline-flex items-center gap-1.5 px-2.5 py-1 rounded-lg border text-xs font-mono transition-all ${colorClass}`;
    chip.innerHTML = `
      <span>${escapeHtml(item)}</span>
      <button type="button" class="font-bold px-1 rounded transition-colors cursor-pointer ${btnColor}" title="Gỡ bỏ">&times;</button>
    `;
    const btn = chip.querySelector('button');
    if (btn) {
      btn.addEventListener('click', () => removeSecurityPolicyItem(item));
    }
    container.appendChild(chip);
  });
}

async function addSecurityPolicyItem() {
  const input = document.getElementById('input-new-security-item');
  if (!input) return;
  const val = input.value.trim();
  if (!val) {
    showToast('Vui lòng nhập giá trị cần thêm!', 'warning');
    return;
  }

  const res = await apiUpdateBlacklist('add', val, currentSecurityPolicyTab);
  if (res && res.status === 'success') {
    showToast(`Đã thêm "${val}" vào chính sách an ninh`, 'success');
    input.value = '';
    securityPolicies.blacklist = res.forbidden_keywords || [];
    securityPolicies.confirm_actions = res.require_confirmation_actions || [];
    securityPolicies.protected_dirs = res.protected_directories || [];
    securityBlacklist = securityPolicies.blacklist;
    renderSecurityChips();
    updateSecurityPoliciesCount();
  } else {
    showToast(res.detail || 'Không thể cập nhật chính sách!', 'error');
  }
}

async function removeSecurityPolicyItem(item) {
  if (!confirm(`Bạn có chắc muốn gỡ bỏ "${item}" khỏi danh mục này?`)) return;

  const res = await apiUpdateBlacklist('remove', item, currentSecurityPolicyTab);
  if (res && res.status === 'success') {
    showToast(`Đã gỡ bỏ "${item}"`, 'info');
    securityPolicies.blacklist = res.forbidden_keywords || [];
    securityPolicies.confirm_actions = res.require_confirmation_actions || [];
    securityPolicies.protected_dirs = res.protected_directories || [];
    securityBlacklist = securityPolicies.blacklist;
    renderSecurityChips();
    updateSecurityPoliciesCount();
  } else {
    showToast(res.detail || 'Không thể xóa mục này!', 'error');
  }
}

// Backward compatible aliases
function renderBlacklistChips() { renderSecurityChips(); }
function addBlacklistKeyword() { addSecurityPolicyItem(); }
function removeBlacklistKeyword(kw) { removeSecurityPolicyItem(kw); }

// ── Interactive AST Sandbox Inspector ───────────────────────────────────────
function updateSandboxPlaceholder() {
  const type = document.querySelector('input[name="sandbox-type"]:checked')?.value || 'code';
  const input = document.getElementById('sec-sandbox-input');
  if (!input) return;
  if (type === 'code') {
    input.placeholder = 'Ví dụ:\nimport os\nos.system("rmdir /s /q C:\\\\Windows")';
  } else if (type === 'action') {
    input.placeholder = 'Ví dụ: kill_process hoặc write_file hoặc restart_service';
  } else {
    input.placeholder = 'Ví dụ: Hãy chạy lệnh xóa cơ sở dữ liệu trên máy chủ 192.168.1.100';
  }
}

function loadSandboxPreset(presetKey) {
  const input = document.getElementById('sec-sandbox-input');
  if (!input) return;

  if (presetKey === 'dangerous') {
    setSandboxType('code');
    input.value = `import os\nimport shutil\n\n# Thử nghiệm mã nguy hiểm\nos.system("rmdir /s /q C:\\\\Windows")\nshutil.rmtree("/etc")`;
  } else if (presetKey === 'confirm') {
    setSandboxType('action');
    input.value = `kill_process`;
  } else if (presetKey === 'masking') {
    setSandboxType('text');
    input.value = `Kết nối cơ sở dữ liệu postgresql://root:supersecretpassword123@192.168.1.50:5432/hr_kpi và gửi token Bearer eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9...`;
  } else if (presetKey === 'safe') {
    setSandboxType('code');
    input.value = `def calculate_kpi(score, target):\n    ratio = (score / target) * 100\n    return f"Tỷ lệ hoàn thành: {ratio:.1f}%"\n\nprint(calculate_kpi(95, 100))`;
  }
  updateSandboxPlaceholder();
  analyzeSecuritySandbox();
}

function setSandboxType(val) {
  const radio = document.querySelector(`input[name="sandbox-type"][value="${val}"]`);
  if (radio) radio.checked = true;
}

async function analyzeSecuritySandbox() {
  const input = document.getElementById('sec-sandbox-input');
  const resultBox = document.getElementById('sec-sandbox-result');
  const btn = document.getElementById('btn-analyze-sandbox');
  if (!input || !resultBox) return;

  const content = input.value.trim();
  if (!content) {
    showToast('Vui lòng nhập nội dung cần phân tích!', 'warning');
    return;
  }

  const type = document.querySelector('input[name="sandbox-type"]:checked')?.value || 'code';

  if (btn) {
    btn.disabled = true;
    btn.innerHTML = `<span class="animate-spin inline-block mr-1">⏳</span> Đang phân tích...`;
  }

  try {
    const res = await apiInspectSecuritySandbox(type, content);
    resultBox.classList.remove('hidden');

    if (res && res.status === 'success') {
      // Phase 73: KHÔNG mặc định "SAFE" khi server không nói. Trước đây thiếu
      // trường rủi ro thì hệ thống hiện "✅ AN TOÀN" — tức fail-open sang phía
      // an toàn giả. Nay hiện "KHÔNG RÕ" và nói rõ cần kiểm tra lại.
      const risk = (res.risk || '').toUpperCase();
      let borderColor = 'border-slate-500/50 bg-slate-500/10 text-slate-300';
      let badgeHtml = '<span class="px-2.5 py-0.5 rounded font-bold bg-slate-600 text-white text-xs">❔ KHÔNG RÕ — server không trả mức rủi ro</span>';

      if (risk === 'SAFE') {
        borderColor = 'border-emerald-500/50 bg-emerald-500/10 text-emerald-300';
        badgeHtml = '<span class="px-2.5 py-0.5 rounded font-bold bg-emerald-500 text-white text-xs">✅ SAFE (AN TOÀN)</span>';
      } else if (risk === 'BLOCKED') {
        borderColor = 'border-rose-500/50 bg-rose-500/10 text-rose-300';
        badgeHtml = '<span class="px-2.5 py-0.5 rounded font-bold bg-rose-600 text-white text-xs">🚫 BLOCKED (BỊ TỪ CHỐI TỨC THÌ)</span>';
      } else if (risk === 'NEED_CONFIRM') {
        borderColor = 'border-amber-500/50 bg-amber-500/10 text-amber-300';
        badgeHtml = '<span class="px-2.5 py-0.5 rounded font-bold bg-amber-500 text-white text-xs">⚠️ NEED_CONFIRM (CẦN QUẢN TRỊ VIÊN DUYỆT)</span>';
      }

      resultBox.className = `mt-3 p-4 rounded-xl border ${borderColor} space-y-2.5 transition-all`;

      let violationsHtml = '';
      if (res.violations && res.violations.length > 0) {
        violationsHtml = `
          <div class="mt-2 p-2.5 rounded-lg bg-black/40 border border-white/10 space-y-1">
            <div class="font-bold text-[11px] uppercase tracking-wider text-rose-400">Chi tiết vi phạm an ninh:</div>
            <ul class="list-disc list-inside space-y-0.5 font-mono text-[11px] text-rose-300">
              ${res.violations.map(v => `<li>${escapeHtml(v)}</li>`).join('')}
            </ul>
          </div>
        `;
      }

      let maskingHtml = '';
      if (res.has_sensitive_data) {
        maskingHtml = `
          <div class="mt-2 p-2.5 rounded-lg bg-cyan-950/40 border border-cyan-500/30 space-y-1">
            <div class="font-bold text-[11px] uppercase tracking-wider text-cyan-400">🛡️ Lá Chắn Khử Nhiễm (Data Sanitizer Triggered):</div>
            <div class="text-[11px] text-slate-300">Phát hiện dữ liệu nội bộ nhạy cảm (IP LAN / Secrets / Tokens). Dữ liệu gửi ra ngoài sẽ được mã hóa an toàn thành:</div>
            <pre class="p-2 rounded bg-black/60 font-mono text-[11px] text-cyan-200 whitespace-pre-wrap">${escapeHtml(res.masked_content)}</pre>
          </div>
        `;
      }

      resultBox.innerHTML = `
        <div class="flex items-center justify-between border-b border-white/10 pb-2">
          <div class="flex items-center gap-2">
            ${badgeHtml}
            <span class="text-xs text-slate-300 font-semibold">${escapeHtml(res.message || '')}</span>
          </div>
          <span class="text-[10px] font-mono text-slate-400">Phân loại: ${escapeHtml(type.toUpperCase())}</span>
        </div>
        ${violationsHtml}
        ${maskingHtml}
      `;
    } else {
      resultBox.className = 'mt-3 p-4 rounded-xl border border-rose-500/50 bg-rose-500/10 text-rose-300';
      resultBox.innerHTML = `❌ Lỗi phân tích: ${escapeHtml(res?.detail || 'Không xác định')}`;
    }
  } catch (err) {
    resultBox.classList.remove('hidden');
    resultBox.className = 'mt-3 p-4 rounded-xl border border-rose-500/50 bg-rose-500/10 text-rose-300';
    resultBox.innerHTML = `❌ Lỗi phân tích: ${escapeHtml(err.message || String(err))}`;
  } finally {
    if (btn) {
      btn.disabled = false;
      btn.innerHTML = `<svg width="14" height="14" fill="none" stroke="currentColor" stroke-width="2" viewBox="0 0 24 24"><polygon points="13 2 3 14 12 14 11 22 21 10 12 10 13 2"/></svg> <span>⚡ Phân Tích An Ninh Ngay</span>`;
    }
  }
}

// ── Export Audit Logs (audit_logs bất biến — không có nút xoá) ────────────────────────────────────────────────
function exportAuditLogsJson() {
  if (!securityAuditLogs || securityAuditLogs.length === 0) {
    showToast('Chưa có bản ghi nhật ký nào để xuất!', 'warning');
    return;
  }
  const jsonStr = JSON.stringify(securityAuditLogs, null, 2);
  const blob = new Blob([jsonStr], { type: 'application/json' });
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url;
  a.download = `security_audit_logs_${new Date().toISOString().slice(0, 10)}.json`;
  document.body.appendChild(a);
  a.click();
  document.body.removeChild(a);
  URL.revokeObjectURL(url);
  showToast('✅ Đã xuất tệp nhật ký kiểm toán thành công!', 'success');
}

function filterAuditLogs() {
  const filterEl = document.getElementById('filter-audit-risk');
  const level = filterEl ? filterEl.value : 'ALL';
  if (level === 'ALL') {
    renderAuditLogsTable(securityAuditLogs);
  } else {
    const filtered = securityAuditLogs.filter(log => (log.risk || log.risk_level || '').toUpperCase() === level);
    renderAuditLogsTable(filtered);
  }
}

function renderAuditLogsTable(logs) {
  const tbody = document.getElementById('audit-table-body');
  if (!tbody) return;
  tbody.innerHTML = '';

  if (!logs || logs.length === 0) {
    tbody.innerHTML = '<tr><td colspan="6" class="py-6 text-center text-slate-500 text-xs">Không tìm thấy bản ghi kiểm toán an ninh nào.</td></tr>';
    return;
  }

  // Pre-calculate which pending actions have already been resolved by subsequent log entries
  const resolvedSignatures = new Set();
  for (let i = logs.length - 1; i >= 0; i--) {
    const l = logs[i];
    const st = (l.status || '').toUpperCase();
    const sig = `${l.client_id || 'master'}:${l.action || l.skill_name || ''}`;
    if (st.includes('APPROVED') || st.includes('REJECTED') || st.includes('SUCCESS')) {
      resolvedSignatures.add(sig);
    }
  }

  logs.forEach(log => {
    const tr = document.createElement('tr');
    tr.className = 'hover:bg-white/[0.03] transition-colors';

    // Risk badge styling
    // Phase 73: thiếu trường rủi ro thì hiện "KHÔNG RÕ", không mặc định SAFE
    // (xem runSecuritySandbox — cùng lỗi fail-open sang phía an toàn giả).
    let riskBadge = '';
    const risk = (log.risk || log.risk_level || '').toString().toUpperCase();
    if (risk === 'BLOCKED') {
      riskBadge = '<span class="px-2 py-0.5 rounded text-[10px] font-bold bg-rose-500/20 text-rose-300 border border-rose-500/40">BLOCKED</span>';
    } else if (risk === 'NEED_CONFIRM') {
      riskBadge = '<span class="px-2 py-0.5 rounded text-[10px] font-bold bg-amber-500/20 text-amber-300 border border-amber-500/40">NEED_CONFIRM</span>';
    } else if (risk === 'SAFE') {
      riskBadge = '<span class="px-2 py-0.5 rounded text-[10px] font-bold bg-emerald-500/20 text-emerald-300 border border-emerald-500/40">SAFE</span>';
    } else {
      riskBadge = '<span class="px-2 py-0.5 rounded text-[10px] font-bold bg-slate-500/20 text-slate-400 border border-slate-500/40">KHÔNG RÕ</span>';
    }

    // Status badge
    let statusBadge = '';
    const st = (log.status || '').toUpperCase();
    const sig = `${log.client_id || 'master'}:${log.action || log.skill_name || ''}`;
    const isActuallyPending = (st.includes('WAITING') || st.includes('PENDING_CONFIRMATION') || st === 'NEED_CONFIRM') && !resolvedSignatures.has(sig);

    if (st.includes('SUCCESS') || st === 'USER_APPROVED') {
      statusBadge = `<span class="text-emerald-400 font-semibold">${escapeHtml(log.status)}</span>`;
    } else if (st.includes('BLOCKED') || st === 'USER_REJECTED' || st.includes('ERROR') || st === 'REJECTED') {
      statusBadge = `<span class="text-rose-400 font-semibold">${escapeHtml(log.status)}</span>`;
    } else if (isActuallyPending) {
      statusBadge = `<button type="button" class="btn-review-log px-2 py-0.5 rounded bg-amber-500/20 text-amber-300 hover:bg-amber-500/40 border border-amber-500/50 font-bold transition-all flex items-center gap-1 cursor-pointer"><span>⚠️ Phê duyệt ngay</span></button>`;
    } else if (st.includes('CONFIRM') || st.includes('PENDING')) {
      statusBadge = `<span class="text-slate-400 font-mono text-[10px]">Đã xử lý (Lịch sử)</span>`;
    } else {
      statusBadge = `<span class="text-slate-400">${escapeHtml(log.status)}</span>`;
    }

    const timeFormatted = log.timestamp ? fmtServerTime(log.timestamp) : '--';   // audit lưu UTC
    const detailObj = log.details || log.args || {};
    const argsPretty = typeof detailObj === 'object' ? JSON.stringify(detailObj) : String(detailObj);

    tr.innerHTML = `
      <td class="py-2 px-3 text-slate-500 dark:text-slate-400 whitespace-nowrap">${escapeHtml(timeFormatted)}</td>
      <td class="py-2 px-3 text-primary-600 dark:text-cyan-300 font-bold whitespace-nowrap">${escapeHtml(log.client_id || 'master')}</td>
      <td class="py-2 px-3 text-slate-800 dark:text-white font-semibold whitespace-nowrap">${escapeHtml(log.action || log.skill_name || '--')}</td>
      <td class="py-2 px-3 whitespace-nowrap">${riskBadge}</td>
      <td class="py-2 px-3 whitespace-nowrap">${statusBadge}</td>
      <td class="py-2 px-3 text-slate-500 dark:text-slate-400 max-w-xs truncate" title="${escapeHtml(argsPretty)}">${escapeHtml(argsPretty)}</td>
    `;

    const reviewBtn = tr.querySelector('.btn-review-log');
    if (reviewBtn) {
      reviewBtn.addEventListener('click', () => openEmergencyConfirmModal({
        client_id: log.client_id,
        skill_name: log.action || log.skill_name,
        action: log.action || log.skill_name,
        args: detailObj,
        risk_level: risk,
      }));
    }

    tbody.appendChild(tr);
  });
}

function openEmergencyConfirmModal(riskData) {
  pendingEmergencyAction = riskData;
  const modal = document.getElementById('emergency-confirm-modal');
  const targetEl = document.getElementById('emergency-target-client');
  const skillEl = document.getElementById('emergency-skill-name');
  const argsEl = document.getElementById('emergency-args-json');

  if (targetEl) targetEl.textContent = riskData.client_id || 'master';
  if (skillEl) skillEl.textContent = riskData.action || riskData.skill_name || '--';
  if (argsEl) {
    const rawArgs = riskData.args || riskData.details || {};
    argsEl.textContent = typeof rawArgs === 'string' ? rawArgs : JSON.stringify(rawArgs, null, 2);
  }

  if (modal) modal.classList.remove('hidden');
}

function closeEmergencyConfirmModal() {
  const modal = document.getElementById('emergency-confirm-modal');
  if (modal) modal.classList.add('hidden');
  pendingEmergencyAction = null;
}

async function submitEmergencyApproval(approved) {
  if (!pendingEmergencyAction) {
    closeEmergencyConfirmModal();
    return;
  }

  const act = pendingEmergencyAction;
  const clientId = act.client_id || 'master';
  const skillName = act.action || act.skill_name;
  let args = act.args || {};
  if (typeof args === 'string') {
    try { args = JSON.parse(args); } catch (_) { args = { raw: args }; }
  }

  closeEmergencyConfirmModal();

  const res = await apiConfirmAction(clientId, skillName, args, approved);
  if (approved) {
    if (res && res.status === 'success') {
      showToast(`Đã phê duyệt và thực thi thành công tác vụ "${skillName}"!`, 'success');
    } else {
      showToast(res.detail || res.message || 'Thực thi sau phê duyệt thất bại!', 'error');
    }
  } else {
    showToast(`Đã từ chối thực thi tác vụ "${skillName}"`, 'info');
  }

  // Reload logs & stats
  await loadSecurityCenter();
}

// ═══════════════════════════════════════════════════════════════════════════
// ── XỬ LÝ ĐĂNG NHẬP & PHÂN QUYỀN RBAC (PHASE 10 AUTH CONTROLLER) ───────────
// ═══════════════════════════════════════════════════════════════════════════

let currentUser = null;
let audioNodesData = [];

/** Hoàn tất đăng nhập (dùng chung cho mật khẩu, bước mã MFA và cài MFA bắt buộc). */
function completeLogin(res) {
  localStorage.setItem('vnmateai_token', res.access_token);
  localStorage.setItem('vnmateai_user', JSON.stringify(res.user));
  currentUser = res.user;

  hideLoginScreen();
  applyRolePermissions(currentUser.role, currentUser);
  showToast(`Xin chào ${currentUser.full_name || currentUser.username}! Đăng nhập thành công.`, 'success');
  restoreActiveTab();
  if (getSavedTab() !== 'dashboard') {
    loadDashboard();
  }
  loadAudioNodes();
}

async function handleLoginSubmit(event) {
  if (event) event.preventDefault();
  const uInput = document.getElementById('login-username');
  const pInput = document.getElementById('login-password');
  const errBox = document.getElementById('login-error-box');
  const errText = document.getElementById('login-error-text');
  const btn = document.getElementById('btn-login-submit');

  const username = uInput ? uInput.value.trim() : '';
  const password = pInput ? pInput.value : '';

  if (!username || !password) {
    if (errBox && errText) {
      errText.textContent = 'Vui lòng nhập đầy đủ tên đăng nhập và mật khẩu.';
      errBox.classList.remove('hidden');
    }
    return;
  }

  if (btn) {
    btn.disabled = true;
    btn.innerHTML = `<svg class="animate-spin" width="15" height="15" fill="none" stroke="currentColor" stroke-width="2" viewBox="0 0 24 24"><circle cx="12" cy="12" r="10"/><path d="M12 2a10 10 0 0 1 10 10"/></svg> Đang xác thực...`;
  }
  if (errBox) errBox.classList.add('hidden');

  const res = await apiLogin(username, password);

  if (btn) {
    btn.disabled = false;
    btn.innerHTML = `<svg width="15" height="15" fill="none" stroke="currentColor" stroke-width="2.5" viewBox="0 0 24 24"><path d="M15 3h4a2 2 0 0 1 2 2v14a2 2 0 0 1-2 2h-4"/><polyline points="10 17 15 12 10 7"/><line x1="15" y1="12" x2="3" y2="12"/></svg> Đăng Nhập Vào Hệ Thống`;
  }

  if (res && res.status === 'success' && res.access_token) {
    completeLogin(res);
  } else if (res && res.status === 'mfa_required' && res.mfa_token) {
    LoginMfa.promptCode(res.mfa_token);                    // đúng mật khẩu nhưng CHƯA đăng nhập: cần mã xác thực hai lớp
  } else if (res && res.status === 'mfa_setup_required' && res.setup_token) {
    LoginMfa.promptSetup(res.setup_token);                 // vai trò bắt buộc MFA: chỉ được cài MFA
  } else {
    if (errBox && errText) {
      errText.textContent = res.detail || 'Tên đăng nhập hoặc mật khẩu không chính xác.';
      errBox.classList.remove('hidden');
    }
  }
}

function fillQuickLogin(u, p) {
  const uInput = document.getElementById('login-username');
  const pInput = document.getElementById('login-password');
  if (uInput) uInput.value = u;
  if (pInput) pInput.value = p;
  const errBox = document.getElementById('login-error-box');
  if (errBox) errBox.classList.add('hidden');
}

function showLoginScreen(errorMsg = '') {
  const screen = document.getElementById('login-screen');
  if (screen) screen.classList.remove('hidden');
  const errBox = document.getElementById('login-error-box');
  const errText = document.getElementById('login-error-text');
  if (errorMsg && errBox && errText) {
    errText.textContent = errorMsg;
    errBox.classList.remove('hidden');
  } else if (errBox) {
    errBox.classList.add('hidden');
  }
}

function hideLoginScreen() {
  const screen = document.getElementById('login-screen');
  if (screen) screen.classList.add('hidden');
  if (typeof HeaderInbox !== 'undefined') HeaderInbox.start();      // hộp thư + chuông thông báo (dữ liệu thật)
}

function handleLogout(optionalMessage = '') {
  if (typeof HeaderInbox !== 'undefined') HeaderInbox.stop();
  localStorage.removeItem('vnmateai_token');
  localStorage.removeItem('vnmateai_user');
  localStorage.removeItem('vnmateai_active_tab');
  currentUser = null;
  showLoginScreen(optionalMessage);
  showToast(optionalMessage || 'Đã đăng xuất khỏi hệ thống.', 'info');
}

function applyRolePermissions(role, user) {
  const roleName = (role || 'viewer').toLowerCase();

  // Header badge update
  const nameEl = document.getElementById('header-user-name');
  const roleEl = document.getElementById('header-user-role');
  const avatarEl = document.getElementById('header-user-avatar');

  if (nameEl && user) nameEl.textContent = user.full_name || user.username || 'Người dùng';
  if (roleEl) roleEl.textContent = roleName.toUpperCase();
  if (avatarEl && user) avatarEl.textContent = (user.username || 'US').substring(0, 2).toUpperCase();

  // Role visibility checks
  const navConfig = document.getElementById('nav-config');
  const navSecurity = document.getElementById('nav-security');
  const navVoice = document.getElementById('nav-voice');
  const navUsers = document.getElementById('nav-users');
  const quickModelSelect = document.getElementById('quick-model');
  const btnQuickSave = document.getElementById('btn-quick-save');
  const btnAddSkill = document.querySelector('[onclick="openAddSkillModal()"]');

  if (roleName === 'admin') {
    if (navConfig) navConfig.classList.remove('hidden');
    if (navSecurity) navSecurity.classList.remove('hidden');
    if (navVoice) navVoice.classList.remove('hidden');
    if (navUsers) navUsers.classList.remove('hidden');
    if (quickModelSelect) quickModelSelect.disabled = false;
    if (btnQuickSave) btnQuickSave.classList.remove('hidden');
    if (btnAddSkill) btnAddSkill.classList.remove('hidden');
    const btnSendTask = document.getElementById('btn-send-task');
    if (btnSendTask) btnSendTask.disabled = false;
  } else if (roleName === 'manager') {
    // Manager: Ẩn cấu hình hệ thống & trung tâm an ninh blacklist & quản lý user
    if (navConfig) navConfig.classList.add('hidden');
    if (navSecurity) navSecurity.classList.add('hidden');
    if (navVoice) navVoice.classList.remove('hidden');
    if (navUsers) navUsers.classList.add('hidden');
    if (quickModelSelect) quickModelSelect.disabled = true;
    if (btnQuickSave) btnQuickSave.classList.add('hidden');
    if (btnAddSkill) btnAddSkill.classList.remove('hidden');
    const btnSendTask = document.getElementById('btn-send-task');
    if (btnSendTask) btnSendTask.disabled = false;
  } else {
    // Viewer: Chỉ xem dashboard và danh sách thiết bị
    if (navConfig) navConfig.classList.add('hidden');
    if (navSecurity) navSecurity.classList.add('hidden');
    if (navVoice) navVoice.classList.add('hidden');
    if (navUsers) navUsers.classList.add('hidden');
    if (quickModelSelect) quickModelSelect.disabled = true;
    if (btnQuickSave) btnQuickSave.classList.add('hidden');
    if (btnAddSkill) btnAddSkill.classList.add('hidden');
    const btnSendTask = document.getElementById('btn-send-task');
    if (btnSendTask) {
      btnSendTask.disabled = true;
      btnSendTask.title = 'Tài khoản Viewer chỉ có quyền xem, không được gửi việc';
    }
  }

  // Nếu người dùng không phải admin mà đang ở tab Quản lý tài khoản -> tự động chuyển về Dashboard
  const activePane = document.querySelector('.tab-pane.active');
  if (roleName !== 'admin' && activePane && activePane.id === 'tab-users') {
    switchTab('dashboard');
  }
}

async function checkAuthAndInit() {
  const token = getAuthToken();
  const storedUser = getStoredUser();

  if (!token || !storedUser) {
    showLoginScreen();
    return;
  }

  // Xác thực token với backend
  const meRes = await apiGetMe();
  if (meRes && meRes.status === 'success' && meRes.user) {
    currentUser = meRes.user;
    localStorage.setItem('vnmateai_user', JSON.stringify(currentUser));
    hideLoginScreen();
    applyRolePermissions(currentUser.role, currentUser);
    restoreActiveTab();
    if (getSavedTab() !== 'dashboard') {
      loadDashboard();
    }
    loadAudioNodes();
    loadMicStatus();
    initPortalWebSocket(); // Connect real-time server-push WebSocket
  } else {
    handleLogout('Phiên đăng nhập đã hết hạn. Vui lòng đăng nhập lại.');
  }
}

// ═══════════════════════════════════════════════════════════════════════════
// ── QUẢN LÝ MẠCH ÂM THANH (AUDIO NODES CONTROLLER) ─────────────────────────
// ═══════════════════════════════════════════════════════════════════════════

// Phase 78: đã gỡ bản `loadAudioNodes` + `renderAudioNodes` ở đây.
//
// Trước đây có HAI hàm `loadAudioNodes` trong file. Bản này (sau) đè bản ở
// trên, nên nó là bản chạy thật — và nó chỉ cập nhật vùng UI của tab dashboard.
// Bản ở trên (phục vụ tab "Trợ Lý Thoại", mục "MẠCH THOẠI ESP32") không bao
// giờ được gọi, nên `#voice-stat-nodes-val` kẹt ở chữ "0 THIẾT BỊ" viết cứng
// trong HTML dù máy có bao nhiêu mạch.
//
// Cùng một tính năng (mạch ESP32) bị vẽ ở hai tab là trùng lặp — nên gom về
// MỘT chỗ: tab "Trợ Lý Thoại" là chủ sở hữu, có mục riêng và UI đầy đủ.
// Tab dashboard giờ chỉ hiện một dòng tóm tắt kèm link sang đó.
// Các hàm `triggerXiaozhi*` bên dưới giữ nguyên, chúng điều khiển thiết bị.

async function triggerXiaozhiInterrupt(deviceId) {
  try {
    const res = await apiFetch(`${API_BASE}/api/v1/xiaozhi/interrupt`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ device_id: deviceId }),
    });
    if (res.ok) {
      showToast(`⚡ Đã kích hoạt ngắt lời trên [${deviceId}]. AI phát câu đệm 0ms.`, 'success');
      loadAudioNodes();
    }
  } catch (err) {
    showToast(`Lỗi kích hoạt ngắt lời: ${err}`, 'error');
  }
}

async function triggerXiaozhiUi(deviceId, state, emotion) {
  try {
    const res = await apiFetch(`${API_BASE}/api/v1/xiaozhi/ui`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ device_id: deviceId, state: state, emotion: emotion }),
    });
    if (res.ok) {
      showToast(`Đã đổi trạng thái LCD [${deviceId}] -> ${state} (${emotion})`, 'info');
      loadAudioNodes();
    }
  } catch (err) {
    showToast(`Lỗi gửi lệnh LCD: ${err}`, 'error');
  }
}

async function triggerXiaozhiWake(deviceId) {
  try {
    const res = await apiFetch(`${API_BASE}/api/v1/xiaozhi/wake`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        device_id: deviceId,
        title: 'CẢNH BÁO SỰ CỐ MÁY CHỦ',
        message: 'Hệ thống vừa phát hiện cảnh báo khẩn cấp từ Sentinel.',
      }),
    });
    if (res.ok) {
      showToast(`🚨 Đã gửi lệnh Wake cảnh báo tới Robot [${deviceId}]!`, 'warning');
      loadAudioNodes();
    }
  } catch (err) {
    showToast(`Lỗi gửi lệnh cảnh báo: ${err}`, 'error');
  }
}

// ═══════════════════════════════════════════════════════════════════════════
// ── LEAN MICRO-TASKING & KPI CONTROLLER (PHASE 11) ─────────────────────────
// ═══════════════════════════════════════════════════════════════════════════

let kpiLogsData = [];

async function loadKpiLogs() {
  const filterStatus = document.getElementById('kpi-filter-status');
  const statusVal = filterStatus ? filterStatus.value : '';
  const params = {};
  if (statusVal) params.status = statusVal;

  const data = await apiGetKpiLogs(params);
  if (data && data.status === 'success') {
    kpiLogsData = data.logs || [];

    const totalEl = document.getElementById('kpi-stat-total');
    const compEl = document.getElementById('kpi-stat-completed');
    const issueEl = document.getElementById('kpi-stat-issue');
    const rateEl = document.getElementById('kpi-stat-rate');
    const barEl = document.getElementById('kpi-progress-bar');
    const badgeEl = document.getElementById('badge-kpi-count');

    if (totalEl) totalEl.textContent = data.total;
    if (compEl) compEl.textContent = data.completed;
    if (issueEl) issueEl.textContent = data.issue;
    if (rateEl) rateEl.textContent = `${data.completion_rate}%`;
    if (barEl) barEl.style.width = `${Math.min(100, Math.max(0, data.completion_rate))}%`;
    if (badgeEl) badgeEl.textContent = `${data.total}`;

    renderKpiLogs(kpiLogsData);
  }

  await populateTaskClientsDropdown();
}

async function populateTaskClientsDropdown() {
  const select = document.getElementById('task-target-client');
  if (!select) return;

  const currentVal = select.value;
  const clients = await apiGetClients();
  select.innerHTML = '<option value="">-- Chọn máy trạm online --</option>';

  if (clients && clients.length > 0) {
    clients.forEach(c => {
      const opt = document.createElement('option');
      opt.value = c.client_id;
      opt.textContent = `${c.client_id} (${c.ip || 'LAN'})`;
      if (c.client_id === currentVal) opt.selected = true;
      select.appendChild(opt);
    });
  } else {
    const opt = document.createElement('option');
    opt.value = '';
    opt.textContent = 'Không có máy trạm nào online';
    opt.disabled = true;
    select.appendChild(opt);
  }
}

function renderKpiLogs(logs) {
  const tbody = document.getElementById('kpi-logs-table-body');
  if (!tbody) return;
  tbody.innerHTML = '';

  if (!logs || logs.length === 0) {
    tbody.innerHTML = `
      <tr>
        <td colspan="4" class="py-6 text-center text-slate-500 italic">Chưa có nhật ký công việc nào được ghi nhận trong file kpi_logs.csv.</td>
      </tr>
    `;
    return;
  }

  logs.forEach(item => {
    const tr = document.createElement('tr');
    tr.className = 'hover:bg-slate-50 dark:hover:bg-white/[0.02] transition-colors';

    let statusBadge = '';
    const st = (item.status || '').toLowerCase();
    if (st === 'completed') {
      statusBadge = `
        <span class="inline-flex items-center gap-1 px-2.5 py-0.5 rounded-full text-[10px] font-bold bg-emerald-500/20 text-emerald-300 border border-emerald-400/40 shadow-[0_0_8px_rgba(16,185,129,0.2)]">
          <svg width="12" height="12" fill="none" stroke="currentColor" stroke-width="2.5" viewBox="0 0 24 24"><polyline points="20 6 9 17 4 12"/></svg>
          Đã hoàn thành
        </span>
      `;
    } else if (st === 'issue' || st === 'error') {
      statusBadge = `
        <span class="inline-flex items-center gap-1 px-2.5 py-0.5 rounded-full text-[10px] font-bold bg-rose-500/20 text-rose-300 border border-rose-400/40 shadow-[0_0_8px_rgba(244,63,94,0.2)]">
          <svg width="12" height="12" fill="none" stroke="currentColor" stroke-width="2.5" viewBox="0 0 24 24"><circle cx="12" cy="12" r="10"/><line x1="12" y1="8" x2="12" y2="12"/><line x1="12" y1="16" x2="12.01" y2="16"/></svg>
          Vướng mắc
        </span>
      `;
    } else {
      statusBadge = `
        <span class="inline-flex items-center gap-1 px-2.5 py-0.5 rounded-full text-[10px] font-bold bg-amber-500/20 text-amber-300 border border-amber-400/40">
          <svg width="12" height="12" fill="none" stroke="currentColor" stroke-width="2.5" viewBox="0 0 24 24"><circle cx="12" cy="12" r="10"/><polyline points="12 6 12 12 16 14"/></svg>
          ${escapeHtml(item.status)}
        </span>
      `;
    }

    tr.innerHTML = `
      <td class="py-2.5 px-3 font-mono text-slate-500 dark:text-slate-400 whitespace-nowrap">${escapeHtml(item.timestamp || '--')}</td>
      <td class="py-2.5 px-3 font-mono font-bold text-primary-600 dark:text-cyan-400 whitespace-nowrap">${escapeHtml(item.client_id || '--')}</td>
      <td class="py-2.5 px-3 text-slate-800 dark:text-white font-medium break-words">${escapeHtml(item.task_message || '--')}</td>
      <td class="py-2.5 px-3 whitespace-nowrap">${statusBadge}</td>
    `;
    tbody.appendChild(tr);
  });
}

// ── AI Supervisor (Bảng điều khiển) — /api/v1/ops/overview, /api/v1/ops/tasks/{id} ──
const _OP_STATUS_VI = {
  NEW: 'Mới', ANALYZING: 'Đang phân tích', PLANNED: 'Đã lập kế hoạch', WAITING_AUTHORIZATION: 'Chờ duyệt',
  AUTHORIZED: 'Đã duyệt', EXECUTING: 'Đang thực thi', VERIFYING: 'Đang kiểm chứng', COMPLETED: 'Hoàn thành',
  FAILED: 'Thất bại', BLOCKED: 'Bị chặn', CANCELLED: 'Đã huỷ', ESCALATED: 'Cần người xác nhận',
};

async function loadSupervisorOverview() {
  const card = document.getElementById('supervisor-card');
  if (!card) return;
  try {
    const res = await apiFetch(`${API_BASE}/api/v1/ops/overview?hours=24`);
    if (res.status === 403) { card.classList.add('hidden'); return; }
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    const d = await res.json();
    const set = (id, v) => { const el = document.getElementById(id); if (el) el.textContent = v; };
    const ai = d.ai_status || {};                       // máy chủ cũ / thiếu trường: không làm hỏng cả thẻ
    const ks = !!ai.kill_switch;
    const nOff = (ai.disabled_agents || []).length;
    const st = document.getElementById('sup-ai-status');
    if (st) {
      st.textContent = ks ? 'DỪNG KHẨN CẤP — chỉ đọc' : (nOff ? `Đang chạy · tắt ${nOff} tác nhân` : 'Đang chạy');
      st.className = `px-2.5 py-1 rounded-full text-[11px] font-bold border ${ks ? 'bg-rose-500/15 text-rose-500 border-rose-500/40' : 'bg-emerald-500/15 text-emerald-600 dark:text-emerald-400 border-emerald-500/40'}`;
    }
    set('sup-active', d.tasks.active);
    set('sup-success', d.tasks.success_rate == null ? '—' : `${d.tasks.success_rate}%`);
    const denied = d.actions.by_decision.deny || 0;
    set('sup-actions', `${d.actions.total} / ${denied}`);
    set('sup-pending', d.approvals_pending ?? '—');
    set('sup-incidents', d.incidents_open);
    // Chi phí chỉ có khi model đã khai giá trong model registry; phần token chưa có giá ghi riêng.
    const cost = d.cost || {};
    set('sup-tokens', (cost.total_tokens || 0).toLocaleString('vi-VN') + (cost.currency_cost == null ? ''
      : ` · ${cost.currency_cost.toLocaleString('en-US', { style: 'currency', currency: cost.currency || 'USD', maximumFractionDigits: 4 })}`));
    const tokEl = document.getElementById('sup-tokens');
    if (tokEl) tokEl.title = cost.unpriced_tokens ? `${cost.unpriced_tokens.toLocaleString('vi-VN')} token thuộc model chưa khai giá (model registry)` : '';
    // Ưu tiên lượt cần LLM (con số người dùng cảm nhận); không có thì nhóm nhiều lượt nhất.
    const groups = (d.voice && d.voice.by_outcome) || {};
    let pick = null;
    for (const name of ['llm', 'agent', ...Object.keys(groups)]) {
      const m = ((groups[name] || {}).metrics || {}).ttfa_answer_ms;
      if (m) { pick = { name, m }; break; }
    }
    const fmt = v => (v >= 1000 ? `${(v / 1000).toFixed(1)} s` : `${Math.round(v)} ms`);
    set('sup-voice', pick ? fmt(pick.m.p50) : '—');
    const vEl = document.getElementById('sup-voice');
    if (vEl) vEl.title = pick ? `nhóm "${pick.name}", ${pick.m.n} lượt, p95 ${fmt(pick.m.p95)}` : 'chưa có lượt thoại';
    set('sup-updated', `Cập nhật ${new Date().toLocaleTimeString('vi-VN')} · chính sách ${ai.policy_version || "—"}`);
    renderSupervisorAttention(d.tasks.attention || [], d.tasks.attention_total);
  } catch (err) {
    const box = document.getElementById('sup-attention');
    if (box) box.innerHTML = `<div class="text-rose-400">Không tải được: ${_esc(friendlyErrorText(err))}</div>`;
  }
}

// Danh sách "Cần chú ý": khung cao cố định + cuộn, bộ lọc, thu gọn (nhớ lựa chọn của người xem).
let _supAttnItems = [];
let _supAttnTotal = 0;
let _supAttnFilter = 'all';

/** Thuần (có test Node): đếm theo nhóm và lọc. FAILED = thất bại; còn lại = cần người xác nhận / duyệt. */
function supAttnView(items, filter) {
  const list = Array.isArray(items) ? items : [];
  const failed = list.filter((t) => t.status === 'FAILED');
  const review = list.filter((t) => t.status !== 'FAILED');
  const shown = filter === 'failed' ? failed : filter === 'review' ? review : list;
  return { counts: { all: list.length, review: review.length, failed: failed.length }, shown };
}

function renderSupervisorAttention(items, total) {
  _supAttnItems = Array.isArray(items) ? items : [];
  _supAttnTotal = Math.max(Number(total) || 0, _supAttnItems.length);
  paintSupervisorAttention();
}

function setSupAttnFilter(filter) {
  _supAttnFilter = ['all', 'review', 'failed'].includes(filter) ? filter : 'all';
  paintSupervisorAttention();
}

function _supAttnCollapsed() {
  try { return localStorage.getItem('supAttnCollapsed') === '1'; } catch (e) { return false; }
}

function toggleSupAttn() {
  const next = !_supAttnCollapsed();
  try { localStorage.setItem('supAttnCollapsed', next ? '1' : '0'); } catch (e) { /* trình duyệt chặn lưu: chỉ mất nhớ lựa chọn */ }
  paintSupervisorAttention();
}

function paintSupervisorAttention() {
  const box = document.getElementById('sup-attention');
  if (!box) return;
  const v = supAttnView(_supAttnItems, _supAttnFilter);
  const collapsed = _supAttnCollapsed();

  const cnt = document.getElementById('sup-attn-count');
  if (cnt) cnt.textContent = String(_supAttnTotal);
  const note = document.getElementById('sup-attn-note');
  if (note) note.textContent = _supAttnTotal > _supAttnItems.length ? ` — hiện ${_supAttnItems.length} mục mới nhất` : '';
  for (const [key, label] of [['all', 'Tất cả'], ['review', 'Cần xác nhận'], ['failed', 'Thất bại']]) {
    const b = document.getElementById(`sup-attn-f-${key}`);
    if (!b) continue;
    b.textContent = `${label} ${v.counts[key]}`;
    b.style.background = _supAttnFilter === key ? 'rgba(6,182,212,0.18)' : '';
    b.setAttribute('aria-pressed', String(_supAttnFilter === key));
  }
  const tg = document.getElementById('sup-attn-toggle');
  if (tg) {
    tg.textContent = collapsed ? 'Mở rộng ▾' : 'Thu gọn ▴';
    tg.setAttribute('aria-expanded', String(!collapsed));
  }
  box.style.display = collapsed ? 'none' : '';
  if (collapsed) return;

  if (!_supAttnItems.length) {
    box.innerHTML = '<div class="text-slate-500 italic">Không có tác vụ nào cần người xử lý.</div>';
    return;
  }
  if (!v.shown.length) {
    box.innerHTML = '<div class="text-slate-500 italic">Không có mục nào trong bộ lọc này.</div>';
    return;
  }
  box.innerHTML = v.shown.map(t => `
    <div class="rounded-xl border border-slate-200 dark:border-white/10">
      <button type="button" onclick="toggleOpTask('${_esc(t.task_id)}')" class="w-full flex flex-wrap items-center gap-2 px-3 py-1.5 text-left">
        <span class="px-2 py-0.5 rounded-full text-[10px] font-bold border ${t.status === 'FAILED' ? 'text-rose-400 border-rose-400/40' : 'text-amber-400 border-amber-400/40'}">${_esc(_OP_STATUS_VI[t.status] || t.status)}</span>
        <span class="text-[10px] text-slate-400">${_esc(t.priority)} · ${_esc(t.kind === 'incident' ? 'sự cố' : 'lượt AI')} · ${_esc(t.agent_id || '')}</span>
        <span class="flex-1 min-w-0 truncate text-slate-700 dark:text-slate-200">${_esc(t.title)}</span>
        <span class="text-[10px] text-slate-400 font-mono">${_esc(t.created_at)}</span>
      </button>
      <div id="op-detail-${_esc(t.task_id)}" class="hidden px-3 pb-3"></div>
    </div>`).join('');
}

async function toggleOpTask(taskId) {
  const box = document.getElementById(`op-detail-${taskId}`);
  if (!box) return;
  if (!box.classList.contains('hidden')) { box.classList.add('hidden'); return; }
  box.classList.remove('hidden');
  box.innerHTML = '<div class="text-slate-500">Đang tải…</div>';
  try {
    const res = await apiFetch(`${API_BASE}/api/v1/ops/tasks/${encodeURIComponent(taskId)}`);
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    const t = (await res.json()).task;
    const steps = (t.steps || []).map(s => `
      <tr class="border-t border-slate-100 dark:border-white/5">
        <td class="py-1 pr-2">${s.seq}</td>
        <td class="py-1 pr-2 font-mono">${_esc(s.tool)}</td>
        <td class="py-1 pr-2">${_esc(s.decision || '')} <span class="text-slate-400">(${_esc(s.policy_rule || '')}, rủi ro ${_esc(s.risk ?? '')}, ${_esc(s.level || '')})</span></td>
        <td class="py-1 pr-2">${_esc(s.outcome || '')}${s.message ? ` — ${_esc(s.message)}` : ''}</td>
        <td class="py-1">${_esc(s.verification_level || '')} · ${_esc(s.verification_status || '')}</td>
      </tr>`).join('');
    const ev = (t.evidence || []).map(e => `<li><span class="text-slate-400">${_esc(e.kind)} · ${_esc(e.source)}:</span> ${_esc(e.summary)}</li>`).join('');
    const canConfirm = t.status === 'ESCALATED' && _isAdmin();
    box.innerHTML = `
      <div class="text-slate-500 mb-1">${_esc(t.result_summary || '')}</div>
      ${steps ? `<table class="w-full text-[11px] mb-2"><thead><tr class="text-slate-400 text-left"><th>#</th><th>Tool</th><th>Quyết định chính sách</th><th>Kết quả</th><th>Kiểm chứng</th></tr></thead><tbody>${steps}</tbody></table>` : ''}
      ${ev ? `<div class="text-slate-400">Bằng chứng:</div><ul class="list-disc pl-5 space-y-0.5">${ev}</ul>` : ''}
      ${canConfirm ? `<div class="pt-2 flex gap-2"><button type="button" onclick="decideOpTask('${_esc(t.task_id)}','confirm')" class="px-2.5 py-1 rounded-lg bg-emerald-600 text-white text-[11px] font-bold">Tôi đã kiểm tra — xác nhận hoàn thành</button>
        <button type="button" onclick="decideOpTask('${_esc(t.task_id)}','cancel')" class="px-2.5 py-1 rounded-lg border border-slate-400/40 text-[11px]">Huỷ tác vụ</button></div>` : ''}`;
  } catch (err) {
    box.innerHTML = `<div class="text-rose-400">Không tải được: ${_esc(friendlyErrorText(err))}</div>`;
  }
}

async function decideOpTask(taskId, action) {
  const note = prompt(action === 'confirm' ? 'Ghi chú xác nhận (bạn đã kiểm tra gì):' : 'Lý do huỷ:', '');
  if (note === null) return;
  try {
    const res = await apiFetch(`${API_BASE}/api/v1/ops/tasks/${encodeURIComponent(taskId)}/${action}`, { method: 'POST', body: JSON.stringify({ note }) });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error(apiErrorText(data, res.status));
    showToast(action === 'confirm' ? 'Đã xác nhận kết quả' : 'Đã huỷ tác vụ', 'success');
    loadSupervisorOverview();
  } catch (err) {
    showToast(err.message || 'Thao tác thất bại', 'error');
  }
}

// ── Kiểm soát tự trị AI (Trung tâm Bảo mật) — /api/v1/security/autonomy ──
let _autonomy = null;
const _AGENT_LABELS = {
  'VN-MATEAI-VOICE': 'Thoại (portal, HUD, robot)',
  'VN-MATEAI-TELEGRAM': 'Telegram',
  'VN-MATEAI-PORTAL-OPS': 'Thao tác Portal (tệp, máy trạm)',
  'VN-MATEAI-ORCHESTRATOR': 'Điều phối đa tác nhân',
  'VN-MATEAI-CONNECTOR': 'Connector / tích hợp',
  'HUMAN-DIRECT': 'Người bấm trực tiếp',
};

function _isAdmin() {
  return !!(currentUser && String(currentUser.role || '').toLowerCase() === 'admin');
}

async function loadAutonomyControls() {
  const card = document.getElementById('autonomy-card');
  if (!card) return;
  try {
    const res = await apiFetch(`${API_BASE}/api/v1/security/autonomy`);
    if (res.status === 403) { card.classList.add('hidden'); return; }
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    const data = await res.json();
    _autonomy = data;
    renderAutonomyControls();
  } catch (err) {
    const hint = document.getElementById('autonomy-hint');
    if (hint) hint.textContent = `Không tải được: ${friendlyErrorText(err)}`;
  }
}

function renderAutonomyControls() {
  if (!_autonomy) return;
  const a = _autonomy.autonomy || {};
  const admin = _isAdmin();
  const on = !!a.kill_switch;
  const btn = document.getElementById('btn-kill-switch');
  if (btn) {
    btn.textContent = on ? 'TẮT DỪNG KHẨN CẤP' : 'DỪNG KHẨN CẤP AI';
    btn.className = `px-4 py-2 rounded-xl text-xs font-black border transition ${on
      ? 'bg-emerald-600 text-white border-emerald-500 hover:bg-emerald-500'
      : 'bg-rose-600 text-white border-rose-500 hover:bg-rose-500'}`;
    btn.disabled = !admin;
    btn.title = admin ? '' : 'Chỉ admin';
  }
  document.getElementById('autonomy-kill-banner')?.classList.toggle('hidden', !on);
  const ver = document.getElementById('autonomy-version');
  if (ver) ver.textContent = _autonomy.policy_version || '—';
  const disabled = new Set(a.disabled_agents || []);
  const agents = document.getElementById('autonomy-agents');
  if (agents) {
    agents.innerHTML = (_autonomy.known_agents || []).map(id => `
      <label class="inline-flex items-center gap-1.5 px-2.5 py-1 rounded-lg border border-slate-200 dark:border-white/10">
        <input type="checkbox" value="${_esc(id)}" ${disabled.has(id) ? '' : 'checked'} ${admin ? '' : 'disabled'} class="accent-cyan-500" />
        <span>${_esc(_AGENT_LABELS[id] || id)}</span>
      </label>`).join('');
  }
  const setVal = (id, v) => { const el = document.getElementById(id); if (el) { el.value = v; el.disabled = !admin; } };
  setVal('autonomy-disabled-tools', (a.disabled_tools || []).join('\n'));
  setVal('autonomy-never', (a.never_autonomous_tools || []).join('\n'));
  setVal('autonomy-ttl', a.approval_grant_ttl_days ?? '');
  setVal('autonomy-seconds', a.max_agent_seconds ?? '');
  setVal('autonomy-calls', a.max_tool_calls_per_turn ?? '');
  setVal('autonomy-email-domains', (a.email_auto_reply_domains || []).join('\n'));
  const er = document.getElementById('autonomy-email-reply');
  if (er) { er.checked = !!a.email_auto_reply; er.disabled = !admin; }
  const save = document.getElementById('btn-autonomy-save');
  if (save) save.disabled = !admin;
  const hint = document.getElementById('autonomy-hint');
  if (hint) hint.textContent = admin ? '' : 'Chỉ admin được thay đổi.';
}

async function _putAutonomy(body) {
  const res = await apiFetch(`${API_BASE}/api/v1/security/autonomy`, { method: 'PUT', body: JSON.stringify(body) });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) {
    const d = data.detail;
    throw new Error(typeof d === 'string' ? d : (Array.isArray(d) ? d.map(x => x.msg).join('; ') : `HTTP ${res.status}`));
  }
  _autonomy = data;
  renderAutonomyControls();
  return data;
}

async function toggleKillSwitch() {
  if (!_autonomy) return;
  const on = !(_autonomy.autonomy || {}).kill_switch;
  const reason = prompt(on ? 'Lý do bật DỪNG KHẨN CẤP (ghi vào nhật ký):' : 'Lý do tắt dừng khẩn cấp:', '');
  if (reason === null) return;
  try {
    await _putAutonomy({ kill_switch: on, reason });
    showToast(on ? 'Đã bật dừng khẩn cấp — AI chỉ còn tác vụ chỉ đọc' : 'Đã tắt dừng khẩn cấp', on ? 'warning' : 'success');
  } catch (err) {
    showToast(err.message || 'Không đổi được', 'error');
  }
}

async function saveAutonomyControls() {
  const lines = id => (document.getElementById(id)?.value || '').split('\n').map(s => s.trim()).filter(Boolean);
  const num = id => { const v = document.getElementById(id)?.value; return v === '' || v == null ? null : Number(v); };
  const enabled = new Set([...document.querySelectorAll('#autonomy-agents input:checked')].map(i => i.value));
  const body = {
    disabled_agents: (_autonomy?.known_agents || []).filter(id => !enabled.has(id)),
    disabled_tools: lines('autonomy-disabled-tools'),
    never_autonomous_tools: lines('autonomy-never'),
    approval_grant_ttl_days: num('autonomy-ttl'),
    max_agent_seconds: num('autonomy-seconds'),
    max_tool_calls_per_turn: num('autonomy-calls'),
    email_auto_reply: !!document.getElementById('autonomy-email-reply')?.checked,
    email_auto_reply_domains: lines('autonomy-email-domains'),
    reason: (document.getElementById('autonomy-reason')?.value || '').trim(),
  };
  Object.keys(body).forEach(k => { if (body[k] === null) delete body[k]; });
  try {
    await _putAutonomy(body);
    showToast('Đã lưu giới hạn tự trị', 'success');
    const r = document.getElementById('autonomy-reason');
    if (r) r.value = '';
  } catch (err) {
    showToast(err.message || 'Lưu thất bại', 'error');
  }
}

// ── Giám sát giao việc (tab #tasks) — số liệu thật từ /api/v1/tasks/board ──
let _taskBoard = null;
let _taskStatusFilter = '';
let _taskBoardTimer = null;
let _taskBoardReload = null;
let _taskBoardFetchedAt = 0;

function _tasksTabActive() {
  const pane = document.getElementById('tab-tasks');
  return !!(pane && pane.classList.contains('active'));
}

function _canWriteTasks() {
  return !!(currentUser && ['manager', 'admin'].includes(String(currentUser.role || '').toLowerCase()));
}

function _fmtDur(sec) {
  if (sec === null || sec === undefined) return '—';
  const s = Math.abs(Math.round(sec));
  if (s < 60) return `${s} giây`;
  if (s < 3600) return `${Math.round(s / 60)} phút`;
  if (s < 86400) return `${Math.floor(s / 3600)} giờ ${Math.round((s % 3600) / 60)} phút`;
  return `${Math.floor(s / 86400)} ngày ${Math.round((s % 86400) / 3600)} giờ`;
}

function _setTasksLive(ok, text) {
  const dot = document.getElementById('tasks-live-dot');
  const txt = document.getElementById('tasks-live-text');
  if (dot) dot.className = `w-2 h-2 rounded-full ${ok ? 'bg-emerald-400 animate-pulse' : 'bg-rose-500'}`;
  if (txt) txt.textContent = text;
}

async function loadTaskBoard() {
  const days = document.getElementById('tasks-range')?.value || '30';
  try {
    const res = await apiFetch(`${API_BASE}/api/v1/tasks/board?days=${encodeURIComponent(days)}`);
    if (!res.ok) {
      const err = await res.json().catch(() => ({}));
      throw new Error(err.detail || `HTTP ${res.status}`);
    }
    _taskBoard = await res.json();
    _taskBoardFetchedAt = Date.now();
    _setTasksLive(true, `Cập nhật lúc ${new Date().toLocaleTimeString('vi-VN')} · ${_taskBoard.online_clients.length} máy trạm online · tự cập nhật khi máy phản hồi`);
    renderTaskTotals();
    renderTaskTargets();
    renderTaskBoard();
    renderTaskMachines();
  } catch (err) {
    _setTasksLive(false, `Không tải được số liệu: ${err.message || 'lỗi kết nối'}`);
  }
  _startTaskBoardTicker();
}

function _startTaskBoardTicker() {
  if (_taskBoardTimer) return;
  // Đếm ngược hạn mỗi 15 giây; tải lại số liệu mỗi 60 giây phòng khi mất sự kiện WebSocket.
  _taskBoardTimer = setInterval(() => {
    if (!_tasksTabActive()) return;
    if (Date.now() - _taskBoardFetchedAt > 60000) loadTaskBoard();
    else renderTaskBoard();
  }, 15000);
}

function scheduleTaskBoardReload() {
  if (!_tasksTabActive()) return;
  clearTimeout(_taskBoardReload);
  _taskBoardReload = setTimeout(loadTaskBoard, 400);
}

function renderTaskTotals() {
  const t = (_taskBoard && _taskBoard.totals) || {};
  const set = (id, v) => { const el = document.getElementById(id); if (el) el.textContent = v; };
  set('tb-sent', t.sent ?? 0);
  set('tb-sent-sub', `${t.failed || 0} lỗi gửi · ${t.cancelled || 0} đã huỷ`);
  set('tb-completed', t.completed ?? 0);
  set('tb-issue', t.issue ?? 0);
  set('tb-pending', t.pending ?? 0);
  const od = document.getElementById('tb-overdue');
  if (od) {
    od.textContent = t.overdue ? `${t.overdue} việc quá hạn` : 'không có việc quá hạn';
    od.className = `text-[10px] mt-0.5 ${t.overdue ? 'text-rose-400 font-bold' : 'text-slate-500'}`;
  }
  set('tb-rate', t.completion_rate === null || t.completion_rate === undefined ? '—' : `${t.completion_rate}%`);
  const bar = document.getElementById('tb-rate-bar');
  if (bar) bar.style.width = `${Math.min(100, Math.max(0, t.completion_rate || 0))}%`;
  set('tb-resp', _fmtDur(t.avg_response_s));
  set('tb-resp-sub', t.median_response_s === null || t.median_response_s === undefined
    ? 'chưa có phản hồi nào' : `trung vị ${_fmtDur(t.median_response_s)}`);
  const badge = document.getElementById('badge-kpi-count');
  if (badge) badge.textContent = `${t.pending || 0}`;
  const hint = document.getElementById('task-write-hint');
  if (hint) hint.classList.toggle('hidden', _canWriteTasks());
  const btn = document.getElementById('btn-send-task');
  if (btn) btn.disabled = !_canWriteTasks();
}

function renderTaskTargets() {
  const box = document.getElementById('task-targets');
  if (!box || !_taskBoard) return;
  const chosen = new Set([...box.querySelectorAll('input:checked')].map(i => i.value));
  const online = _taskBoard.online_clients || [];
  if (!online.length) {
    box.innerHTML = '<span class="text-xs text-slate-500 italic">Không có máy trạm nào online.</span>';
  } else {
    box.innerHTML = online.map(cid => `
      <label class="inline-flex items-center gap-1.5 px-2.5 py-1 rounded-lg border border-slate-200 dark:border-white/10 text-xs cursor-pointer hover:border-cyan-500/50">
        <input type="checkbox" value="${_esc(cid)}" ${chosen.has(cid) ? 'checked' : ''} onchange="updateTaskTargetCount()" class="accent-cyan-500" />
        <span class="w-1.5 h-1.5 rounded-full bg-emerald-400"></span>
        <span class="font-mono">${_esc(cid)}</span>
      </label>`).join('');
  }
  updateTaskTargetCount();
  const sel = document.getElementById('task-filter-client');
  if (sel) {
    const cur = sel.value;
    const ids = [...new Set([...(_taskBoard.machines || []).map(m => m.client_id), ...online])].sort();
    sel.innerHTML = '<option value="">Mọi máy</option>' + ids.map(c =>
      `<option value="${_esc(c)}" ${c === cur ? 'selected' : ''}>${_esc(c)}</option>`).join('');
  }
  const senderEl = document.getElementById('task-sender');
  if (senderEl && currentUser) senderEl.placeholder = currentUser.full_name || currentUser.username || 'Tên tài khoản';
}

function selectAllTaskTargets(on) {
  document.querySelectorAll('#task-targets input[type=checkbox]').forEach(i => { i.checked = on; });
  updateTaskTargetCount();
}

function updateTaskTargetCount() {
  const n = document.querySelectorAll('#task-targets input:checked').length;
  const el = document.getElementById('task-target-count');
  if (el) el.textContent = n ? `(${n} máy)` : '';
}

function _taskStatusBadge(t) {
  const map = {
    completed: ['Hoàn thành', 'bg-emerald-500/20 text-emerald-300 border-emerald-400/40'],
    issue: ['Vướng mắc', 'bg-rose-500/20 text-rose-300 border-rose-400/40'],
    failed: ['Lỗi gửi', 'bg-slate-500/20 text-slate-300 border-slate-400/40'],
    cancelled: ['Đã huỷ', 'bg-slate-500/20 text-slate-400 border-slate-400/30'],
  };
  if (t.status === 'pending') {
    return t.overdue
      ? '<span class="px-2 py-0.5 rounded-full text-[10px] font-bold border bg-rose-600/30 text-rose-200 border-rose-500/60">Quá hạn</span>'
      : '<span class="px-2 py-0.5 rounded-full text-[10px] font-bold border bg-amber-500/20 text-amber-300 border-amber-400/40">Đang chờ</span>';
  }
  const [label, cls] = map[t.status] || [t.status, 'bg-slate-500/20 text-slate-300 border-slate-400/40'];
  return `<span class="px-2 py-0.5 rounded-full text-[10px] font-bold border ${cls}">${_esc(label)}</span>`;
}

function _taskTiming(t) {
  if (t.status === 'pending') {
    if (!t.due_at) return '<span class="text-slate-500">không đặt hạn</span>';
    // Đếm ngược tính lại theo thời gian trôi kể từ lần tải số liệu.
    const left = (t.due_in_s ?? 0) - (Date.now() - _taskBoardFetchedAt) / 1000;
    return left >= 0
      ? `<span class="text-amber-300">còn ${_fmtDur(left)}</span>`
      : `<span class="text-rose-400 font-bold">trễ ${_fmtDur(-left)}</span>`;
  }
  if (t.response_s !== null && t.response_s !== undefined) {
    return `<span class="text-slate-300">trả lời sau ${_fmtDur(t.response_s)}</span>`;
  }
  return '<span class="text-slate-500">—</span>';
}

function renderTaskBoard() {
  const body = document.getElementById('task-board-body');
  if (!body || !_taskBoard) return;
  const client = document.getElementById('task-filter-client')?.value || '';
  const q = (document.getElementById('task-filter-text')?.value || '').trim().toLowerCase();
  document.querySelectorAll('.task-st-btn').forEach(b => {
    const on = b.dataset.st === _taskStatusFilter;
    b.classList.toggle('bg-cyan-500/20', on);
    b.classList.toggle('text-cyan-700', on);
    b.classList.toggle('dark:text-cyan-300', on);
    b.classList.toggle('font-bold', on);
  });
  const now = Date.now();
  let rows = (_taskBoard.tasks || []).map(t => {
    if (t.status === 'pending' && t.due_at && !t.overdue) {
      const left = (t.due_in_s ?? 0) - (now - _taskBoardFetchedAt) / 1000;
      if (left < 0) return { ...t, overdue: true };
    }
    return t;
  });
  rows = rows.filter(t => {
    if (client && t.client_id !== client) return false;
    if (q && !String(t.task_message || '').toLowerCase().includes(q)) return false;
    switch (_taskStatusFilter) {
      case 'pending': return t.status === 'pending';
      case 'overdue': return t.overdue;
      case 'completed': return t.status === 'completed';
      case 'issue': return t.status === 'issue';
      case 'closed': return t.status === 'failed' || t.status === 'cancelled';
      default: return true;
    }
  });
  const total = rows.length;
  rows = rows.slice(0, 300);
  if (!rows.length) {
    body.innerHTML = `<tr><td colspan="7" class="py-6 text-center text-slate-500 italic">${
      (_taskBoard.tasks || []).length ? 'Không có việc nào khớp bộ lọc.' : 'Chưa giao việc nào trong khoảng thời gian này.'}</td></tr>`;
  } else {
    const canWrite = _canWriteTasks();
    body.innerHTML = rows.map(t => {
      const acts = (t.status === 'pending' && canWrite) ? `
        <button type="button" onclick="taskAction('${_esc(t.task_id)}','remind')" ${t.online ? '' : 'disabled title="Máy đang ngoại tuyến"'}
          class="px-2 py-0.5 rounded border border-cyan-500/40 text-cyan-300 text-[10px] hover:bg-cyan-500/10 disabled:opacity-40">Nhắc lại</button>
        <button type="button" onclick="taskAction('${_esc(t.task_id)}','cancel')"
          class="px-2 py-0.5 rounded border border-slate-500/40 text-slate-300 text-[10px] hover:bg-white/5">Huỷ</button>` : '';
      return `
        <tr class="${t.overdue ? 'bg-rose-500/5' : ''}">
          <td class="py-2 px-2 font-mono text-slate-400 whitespace-nowrap">${_esc(t.timestamp || '—')}</td>
          <td class="py-2 px-2 whitespace-nowrap"><span class="inline-block w-1.5 h-1.5 rounded-full mr-1 ${t.online ? 'bg-emerald-400' : 'bg-slate-500'}" title="${t.online ? 'online' : 'ngoại tuyến'}"></span><span class="font-mono font-bold text-cyan-400">${_esc(t.client_id)}</span></td>
          <td class="py-2 px-2 text-slate-800 dark:text-white break-words max-w-[28rem]">${_esc(t.task_message || '—')}${
            t.resolution_notes ? `<div class="text-[10px] text-slate-500 mt-0.5">${_esc(t.resolution_notes)}</div>` : ''}</td>
          <td class="py-2 px-2 whitespace-nowrap text-slate-400">${_esc(t.dispatched_by || '—')}${
            t.sender && t.sender !== t.dispatched_by ? `<div class="text-[10px] text-slate-500">nhãn: ${_esc(t.sender)}</div>` : ''}</td>
          <td class="py-2 px-2 whitespace-nowrap">${_taskTiming(t)}</td>
          <td class="py-2 px-2 whitespace-nowrap">${_taskStatusBadge(t)}</td>
          <td class="py-2 px-2 whitespace-nowrap text-right space-x-1">${acts}</td>
        </tr>`;
    }).join('');
  }
  const more = document.getElementById('task-board-more');
  if (more) more.textContent = total > rows.length ? `Hiển thị ${rows.length}/${total} việc — dùng bộ lọc hoặc Xuất CSV để xem đủ.` : '';
}

function renderTaskMachines() {
  const body = document.getElementById('task-machines-body');
  if (!body || !_taskBoard) return;
  const ms = _taskBoard.machines || [];
  if (!ms.length) {
    body.innerHTML = '<tr><td colspan="8" class="py-6 text-center text-slate-500 italic">Chưa có số liệu.</td></tr>';
    return;
  }
  body.innerHTML = ms.map(m => {
    const r = m.completion_rate;
    const color = r === null ? 'bg-slate-500' : r >= 80 ? 'bg-emerald-400' : r >= 50 ? 'bg-amber-400' : 'bg-rose-500';
    return `
      <tr>
        <td class="py-2 px-2 whitespace-nowrap"><span class="inline-block w-1.5 h-1.5 rounded-full mr-1 ${m.online ? 'bg-emerald-400' : 'bg-slate-500'}"></span><button type="button" class="font-mono font-bold text-cyan-400 hover:underline" onclick="filterTasksByClient('${_esc(m.client_id)}')">${_esc(m.client_id)}</button></td>
        <td class="py-2 px-2 text-right">${m.sent}</td>
        <td class="py-2 px-2 text-right text-emerald-400">${m.completed}</td>
        <td class="py-2 px-2 text-right ${m.issue ? 'text-rose-400 font-bold' : ''}">${m.issue}</td>
        <td class="py-2 px-2 text-right">${m.pending}</td>
        <td class="py-2 px-2 text-right ${m.overdue ? 'text-rose-400 font-bold' : ''}">${m.overdue}</td>
        <td class="py-2 px-2"><div class="flex items-center gap-2"><div class="w-24 bg-white/10 h-1.5 rounded-full overflow-hidden"><div class="h-full ${color}" style="width:${Math.min(100, r || 0)}%"></div></div><span>${r === null ? '—' : r + '%'}</span></div></td>
        <td class="py-2 px-2 text-right whitespace-nowrap">${_fmtDur(m.avg_response_s)}</td>
      </tr>`;
  }).join('');
}

function filterTasksByClient(cid) {
  const sel = document.getElementById('task-filter-client');
  if (sel) sel.value = cid;
  renderTaskBoard();
  document.getElementById('task-board-body')?.scrollIntoView({ behavior: 'smooth', block: 'center' });
}

function initTaskBoardFilters() {
  document.querySelectorAll('.task-st-btn').forEach(b => {
    if (b.dataset.bound) return;
    b.dataset.bound = '1';
    b.addEventListener('click', () => { _taskStatusFilter = b.dataset.st; renderTaskBoard(); });
  });
}

async function taskAction(taskId, action) {
  if (action === 'cancel' && !confirm('Huỷ việc này? Phản hồi muộn của máy trạm sẽ bị bỏ qua.')) return;
  try {
    const res = await apiFetch(`${API_BASE}/api/v1/tasks/${encodeURIComponent(taskId)}/${action}`, { method: 'POST' });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error(data.detail || `HTTP ${res.status}`);
    showToast(data.message || 'Đã thực hiện', 'success');
    loadTaskBoard();
  } catch (err) {
    showToast(err.message || 'Thao tác thất bại', 'error');
  }
}

async function exportTasksCsv() {
  const days = document.getElementById('tasks-range')?.value || '30';
  try {
    const res = await apiFetch(`${API_BASE}/api/v1/tasks/export.csv?days=${encodeURIComponent(days)}`);
    if (!res.ok) {
      const err = await res.json().catch(() => ({}));
      throw new Error(err.detail || `HTTP ${res.status}`);
    }
    const blob = await res.blob();
    const a = document.createElement('a');
    a.href = URL.createObjectURL(blob);
    a.download = `giao_viec_${days}ngay.csv`;
    document.body.appendChild(a);
    a.click();
    a.remove();
    setTimeout(() => URL.revokeObjectURL(a.href), 2000);
  } catch (err) {
    showToast(err.message || 'Xuất CSV thất bại', 'error');
  }
}

async function handleSendTask(e) {
  if (e) e.preventDefault();
  const targets = [...document.querySelectorAll('#task-targets input:checked')].map(i => i.value);
  const msgInput = document.getElementById('task-message');
  const message = msgInput ? msgInput.value.trim() : '';
  const sender = (document.getElementById('task-sender')?.value || '').trim();
  const due = document.getElementById('task-due')?.value || '';
  const btn = document.getElementById('btn-send-task');
  if (!targets.length) { showToast('Chọn ít nhất một máy trạm nhận việc', 'warning'); return; }
  if (!message) { showToast('Nhập nội dung công việc cần giao', 'warning'); return; }

  if (btn) { btn.disabled = true; btn.textContent = 'Đang gửi…'; }
  try {
    const res = await apiFetch(`${API_BASE}/api/v1/tasks/send`, {
      method: 'POST',
      headers: { 'Idempotency-Key': (crypto.randomUUID ? crypto.randomUUID() : `${Date.now()}-${Math.random()}`) },
      body: JSON.stringify({ client_ids: targets, message, sender: sender || null,
                             due_minutes: due ? Number(due) : null }),
    });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error(data.detail || `HTTP ${res.status}`);
    if (data.results) {
      const bad = data.results.filter(r => r.status !== 'success');
      showToast(`Đã gửi ${data.sent}/${targets.length} máy${bad.length ? ' — lỗi: ' + bad.map(r => r.client_id).join(', ') : ''}`,
        bad.length ? 'warning' : 'success');
    } else {
      showToast(`Đã gửi nhắc việc tới máy ${targets[0]}`, 'success');
    }
    if (msgInput) msgInput.value = '';
    loadTaskBoard();
  } catch (err) {
    showToast(err.message || 'Lỗi gửi việc', 'error');
  } finally {
    if (btn) { btn.disabled = !_canWriteTasks(); btn.textContent = 'Gửi nhắc việc'; }
  }
}

// ═══════════════════════════════════════════════════════════════════════════
// ── PHASE 47: ERP STRUCTURE & BULK DATA IMPORT CONTROLLER ────────────────
// ═══════════════════════════════════════════════════════════════════════════

let g_erp_departments = [];
let g_erp_expanded_depts = new Set();
let g_erp_active_subtabs = {}; // deptId -> 'employees' | 'devices' | 'tasks' | 'records'

async function refreshTasksAndErp() {
  await Promise.all([loadTaskBoard(), loadErpStructure()]);
  showToast('Đã làm mới dữ liệu công việc và cấu trúc tổ chức ERP!', 'info');
}

// ─── Trạng thái Agent của máy + nhập từ AD (hàm thuần: có test Node) ─────────────
const AGENT_BADGE = {
  online: ['Trực tuyến', '#10b981'],
  offline: ['Đã cài · ngoại tuyến', '#f59e0b'],
  revoked: ['Đã thu hồi khoá', '#f43f5e'],
  none: ['Chưa cài', '#94a3b8'],
};

function agentBadgeHtml(agent) {
  const st = (agent && agent.status) in AGENT_BADGE ? agent.status : 'none';
  const [label, color] = AGENT_BADGE[st];
  const tip = agent && agent.client_id ? `Mã agent: ${agent.client_id}` : 'Không thấy Agent nào khớp tên máy này (chưa cài, hoặc cài với tên khác)';
  return `<span class="px-2 py-0.5 rounded-full text-[10px] font-bold border whitespace-nowrap" style="color:${color};border-color:${color}66" title="${_esc(tip)}">${label}</span>`;
}

function agentCoverageText(cov) {
  if (!cov || !cov.total) return '';
  return `Agent trên ${cov.total} máy: ${cov.online} trực tuyến · ${cov.offline} đã cài nhưng ngoại tuyến · ${cov.revoked} bị thu hồi · ${cov.none} chưa cài`;
}

const AD_IMPORT_LABELS = {
  created: 'Tạo mới', updated: 'Bổ sung chỗ trống', unchanged: 'Không đổi',
  skipped_no_department: 'Bỏ qua: không có phòng ban', skipped_department_missing: 'Bỏ qua: phòng ban chưa có',
  skipped_ambiguous: 'Bỏ qua: trùng tên, không rõ ai', skipped_invalid: 'Bỏ qua: thiếu tên',
};

/** Báo cáo xem trước / kết quả nhập từ AD (HTML). Hàm thuần. */
function adImportSummaryHtml(res) {
  if (!res) return '';
  if (res.empty) return `<div class="text-amber-400">${_esc(res.message || 'Bản sao AD đang trống.')}</div>`;
  const st = res.stats || {};
  const row = (title, b) => `<tr class="border-t border-slate-200 dark:border-white/10"><td class="py-1 pr-3 font-semibold">${title}</td>` +
    Object.keys(AD_IMPORT_LABELS).map((k) => `<td class="py-1 pr-3 tabular-nums ${k.startsWith('skipped') && b[k] ? 'text-amber-400' : ''}">${b[k] || 0}</td>`).join('') + '</tr>';
  const head = Object.values(AD_IMPORT_LABELS).map((l) => `<th class="pr-3 font-normal text-slate-400">${l}</th>`).join('');
  const dev = st.devices || {};
  const samples = Object.entries(res.samples || {}).map(([k, v]) => `<div><span class="text-slate-400">${_esc(k)}:</span> ${v.map(_esc).join(', ')}</div>`).join('');
  const warns = (res.warnings || []).map((w) => `<li>${_esc(w)}</li>`).join('');
  return `<div class="font-semibold ${res.applied ? 'text-emerald-400' : 'text-cyan-300'}">${res.applied ? '✅ Đã nhập' : '👁 Xem trước — chưa ghi gì'} ` +
    `(bản sao AD: ${res.source ? res.source.employees : 0} nhân viên, ${res.source ? res.source.computers : 0} máy)</div>` +
    `<table class="text-left"><thead><tr><th class="pr-3"></th>${head}</tr></thead><tbody>${row('Nhân viên', st.employees || {})}${row('Máy tính', dev)}</tbody></table>` +
    `<div>Phòng ban tạo mới: <b>${st.departments_created || 0}</b>${(res.new_departments || []).length ? ` (${res.new_departments.map(_esc).join(', ')})` : ''} · ` +
    `Máy gán được chủ: <b>${dev.owner_matched || 0}</b></div>` +
    (samples ? `<div class="text-[11px] space-y-0.5">${samples}</div>` : '') +
    (warns ? `<ul class="list-disc pl-5 text-amber-400">${warns}</ul>` : '');
}

let _erpAdPreviewKey = null;

function _erpAdOptions() {
  return {
    default_department: (document.getElementById('erp-ad-default-dept')?.value || '').trim(),
    create_departments: !!document.getElementById('erp-ad-create-depts')?.checked,
    include_employees: !!document.getElementById('erp-ad-inc-emp')?.checked,
    include_devices: !!document.getElementById('erp-ad-inc-dev')?.checked,
  };
}

function toggleErpAdImport() {
  const panel = document.getElementById('erp-ad-import-panel');
  if (!panel) return;
  panel.classList.toggle('hidden');
  const list = document.getElementById('erp-ad-dept-list');
  if (list) list.innerHTML = (g_erp_departments || []).map((d) => `<option value="${_esc(d.name)}"></option>`).join('');
}

/** dryRun=true: xem trước (không ghi). false: nhập thật — chỉ khi vừa xem trước ĐÚNG các lựa chọn hiện tại. */
async function erpAdImportRun(dryRun) {
  const out = document.getElementById('erp-ad-import-result');
  const apply = document.getElementById('btn-erp-ad-apply');
  const opts = _erpAdOptions();
  const key = JSON.stringify(opts);
  if (!dryRun && key !== _erpAdPreviewKey) {
    if (out) out.innerHTML = '<div class="text-amber-400">Hãy bấm "Xem trước" với các lựa chọn hiện tại trước khi nhập.</div>';
    return;
  }
  if (out) out.innerHTML = '<div class="text-slate-400">Đang xử lý…</div>';
  try {
    const res = await apiFetch(`${API_BASE}/api/erp/import-from-ad`, { method: 'POST', body: JSON.stringify({ ...opts, dry_run: dryRun }) });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error(apiErrorText(data, res.status));
    if (out) out.innerHTML = adImportSummaryHtml(data);
    if (dryRun) {
      _erpAdPreviewKey = key;
      const s = data.stats || {};
      const anything = !data.empty && ((s.employees && (s.employees.created + s.employees.updated)) || (s.devices && (s.devices.created + s.devices.updated)) || s.departments_created);
      if (apply) apply.disabled = !anything;
    } else {
      _erpAdPreviewKey = null;
      if (apply) apply.disabled = true;
      showToast('Đã nhập dữ liệu từ Active Directory vào ERP', 'success');
      loadErpStructure();
    }
  } catch (err) {
    if (out) out.innerHTML = `<div class="text-rose-400">Không thực hiện được: ${_esc(err.message)}</div>`;
    showToast(err.message || 'Nhập từ AD thất bại', 'error');
  }
}

async function loadErpStructure() {
  const container = document.getElementById('erp-departments-container');
  if (!container) return;

  try {
    const res = await apiFetch(`${API_BASE}/api/erp/structure`);
    if (!res.ok) {
      const err = await res.json().catch(() => ({}));
      throw new Error(apiErrorText(err, res.status));
    }
    const data = await res.json();
    if (data && data.status === 'success') {
      g_erp_departments = data.departments || [];
      const covEl = document.getElementById('erp-agent-coverage');
      if (covEl) covEl.textContent = agentCoverageText(data.agent_coverage);
      // Mặc định mở rộng phòng ban đầu tiên nếu chưa có phòng nào mở
      if (g_erp_departments.length > 0 && g_erp_expanded_depts.size === 0) {
        g_erp_expanded_depts.add(g_erp_departments[0].id);
      }
      renderErpTree(g_erp_departments);
    }
  } catch (err) {
    console.error('[ERP] Lỗi tải cấu trúc dữ liệu:', err);
    if (container) {
      container.innerHTML = `
        <div class="p-6 text-center text-rose-400 bg-rose-500/10 border border-rose-500/20 rounded-xl text-xs">
          ⚠️ Không thể tải cấu trúc dữ liệu ERP: ${escapeHtml(err.message || 'Lỗi kết nối')}
        </div>
      `;
    }
  }
}

function renderErpTree(departments) {
  const container = document.getElementById('erp-departments-container');
  if (!container) return;

  if (!departments || departments.length === 0) {
    container.innerHTML = `
      <div class="p-8 text-center glass-panel border border-dashed border-slate-300 dark:border-white/10 rounded-2xl space-y-3">
        <div class="w-12 h-12 rounded-2xl bg-indigo-500/10 border border-indigo-400/20 flex items-center justify-center text-indigo-400 mx-auto">
          <svg width="24" height="24" fill="none" stroke="currentColor" stroke-width="1.5" viewBox="0 0 24 24">
            <rect x="2" y="7" width="20" height="14" rx="2" ry="2"/>
            <path d="M16 21V5a2 2 0 0 0-2-2h-4a2 2 0 0 0-2 2v16"/>
          </svg>
        </div>
        <div class="text-sm font-bold text-slate-800 dark:text-white">Chưa có dữ liệu phòng ban & tổ chức</div>
        <p class="text-xs text-slate-400 max-w-md mx-auto">
          Hệ thống đang sẵn sàng. Hãy bấm nút <span class="font-semibold text-slate-200">"📥 Tải File Mẫu"</span> để lấy mẫu Excel chuẩn, sau đó điền thông tin và bấm <span class="font-semibold text-cyan-400">"📤 Import Dữ Liệu"</span> để khởi tạo siêu tốc.
        </p>
      </div>
    `;
    return;
  }

  let html = '';
  departments.forEach((dept, idx) => {
    const isExpanded = g_erp_expanded_depts.has(dept.id);
    const activeSubTab = g_erp_active_subtabs[dept.id] || 'employees';

    const empCount = (dept.employees || []).length;
    const devCount = (dept.devices || []).length;
    const taskCount = (dept.tasks || []).length;
    const recCount = (dept.records || []).length;

    html += `
      <div class="border border-slate-200 dark:border-white/10 rounded-2xl bg-white dark:bg-slate-900/60 overflow-hidden shadow-sm transition-all erp-dept-card" data-dept-id="${dept.id}" data-dept-name="${escapeHtml(dept.name)}">
        <!-- Accordion Header -->
        <div onclick="toggleDeptAccordion(${dept.id})" class="p-4 flex flex-col md:flex-row md:items-center justify-between gap-3 cursor-pointer hover:bg-slate-50 dark:hover:bg-white/[0.03] transition-colors select-none">
          <div class="flex items-start gap-3">
            <div class="w-10 h-10 rounded-xl bg-gradient-to-br from-indigo-500/20 to-purple-500/20 border border-indigo-400/30 flex items-center justify-center text-indigo-400 text-lg flex-shrink-0 mt-0.5">
              🏢
            </div>
            <div>
              <div class="flex items-center gap-2">
                <h4 class="text-sm font-bold text-slate-900 dark:text-white">${escapeHtml(dept.name)}</h4>
                <span class="text-[10px] px-2 py-0.2 rounded-full font-mono text-slate-400 bg-slate-100 dark:bg-white/5 border border-slate-200 dark:border-white/10">ID #${dept.id}</span>
              </div>
              <p class="text-xs text-slate-400 mt-0.5">${escapeHtml(dept.description || 'Không có mô tả')}</p>
            </div>
          </div>

          <!-- Badges & Action -->
          <div class="flex items-center gap-2 flex-wrap">
            <span class="text-[11px] font-semibold px-2.5 py-1 rounded-lg bg-blue-500/10 text-blue-400 border border-blue-400/20 flex items-center gap-1">
              👥 ${empCount} <span class="hidden sm:inline">Nhân sự</span>
            </span>
            <span class="text-[11px] font-semibold px-2.5 py-1 rounded-lg bg-emerald-500/10 text-emerald-400 border border-emerald-400/20 flex items-center gap-1">
              💻 ${devCount} <span class="hidden sm:inline">Thiết bị</span>
            </span>
            <span class="text-[11px] font-semibold px-2.5 py-1 rounded-lg bg-purple-500/10 text-purple-400 border border-purple-400/20 flex items-center gap-1">
              📋 ${taskCount} <span class="hidden sm:inline">Công việc</span>
            </span>
            <span class="text-[11px] font-semibold px-2.5 py-1 rounded-lg bg-amber-500/10 text-amber-400 border border-amber-400/20 flex items-center gap-1">
              📂 ${recCount} <span class="hidden sm:inline">Sổ sách</span>
            </span>

            <button onclick="event.stopPropagation(); deleteDepartmentPrompt(${dept.id}, '${escapeHtml(dept.name)}')" 
              title="Xóa phòng ban này"
              class="w-7 h-7 rounded-lg bg-rose-500/10 hover:bg-rose-500/20 border border-rose-500/20 text-rose-400 flex items-center justify-center transition">
              <svg width="13" height="13" fill="none" stroke="currentColor" stroke-width="2" viewBox="0 0 24 24">
                <polyline points="3 6 5 6 21 6"/>
                <path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6m3 0V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2"/>
              </svg>
            </button>

            <div class="w-7 h-7 rounded-lg bg-slate-100 dark:bg-white/5 border border-slate-200 dark:border-white/10 flex items-center justify-center text-slate-400 transition-transform duration-200 ${isExpanded ? 'rotate-180 text-cyan-400 border-cyan-400/30' : ''}">
              <svg width="14" height="14" fill="none" stroke="currentColor" stroke-width="2" viewBox="0 0 24 24">
                <polyline points="6 9 12 15 18 9"/>
              </svg>
            </div>
          </div>
        </div>

        <!-- Accordion Body (Sub-Tabs & Data Tables) -->
        <div id="erp-dept-body-${dept.id}" class="${isExpanded ? 'block' : 'hidden'} border-t border-slate-200 dark:border-white/10 bg-slate-50/50 dark:bg-slate-950/40 p-4 space-y-4">
          
          <!-- Sub-Tab Navigation Toolbar -->
          <div class="flex items-center gap-1.5 border-b border-slate-200 dark:border-white/10 pb-2.5 overflow-x-auto">
            <button onclick="switchDeptSubTab(${dept.id}, 'employees')" class="px-3 py-1.5 rounded-lg text-xs font-semibold flex items-center gap-1.5 transition ${activeSubTab === 'employees' ? 'bg-indigo-600 text-white shadow-sm' : 'text-slate-400 hover:text-white hover:bg-white/5'}">
              <span>👥 Nhân Viên</span>
              <span class="text-[10px] px-1.5 py-0.2 rounded-full ${activeSubTab === 'employees' ? 'bg-white/20 text-white' : 'bg-white/10 text-slate-300'}">${empCount}</span>
            </button>
            <button onclick="switchDeptSubTab(${dept.id}, 'devices')" class="px-3 py-1.5 rounded-lg text-xs font-semibold flex items-center gap-1.5 transition ${activeSubTab === 'devices' ? 'bg-indigo-600 text-white shadow-sm' : 'text-slate-400 hover:text-white hover:bg-white/5'}">
              <span>💻 Thiết Bị & IP</span>
              <span class="text-[10px] px-1.5 py-0.2 rounded-full ${activeSubTab === 'devices' ? 'bg-white/20 text-white' : 'bg-white/10 text-slate-300'}">${devCount}</span>
            </button>
            <button onclick="switchDeptSubTab(${dept.id}, 'tasks')" class="px-3 py-1.5 rounded-lg text-xs font-semibold flex items-center gap-1.5 transition ${activeSubTab === 'tasks' ? 'bg-indigo-600 text-white shadow-sm' : 'text-slate-400 hover:text-white hover:bg-white/5'}">
              <span>📋 Công Việc</span>
              <span class="text-[10px] px-1.5 py-0.2 rounded-full ${activeSubTab === 'tasks' ? 'bg-white/20 text-white' : 'bg-white/10 text-slate-300'}">${taskCount}</span>
            </button>
            <button onclick="switchDeptSubTab(${dept.id}, 'records')" class="px-3 py-1.5 rounded-lg text-xs font-semibold flex items-center gap-1.5 transition ${activeSubTab === 'records' ? 'bg-indigo-600 text-white shadow-sm' : 'text-slate-400 hover:text-white hover:bg-white/5'}">
              <span>📂 Sổ Sách Tài Liệu</span>
              <span class="text-[10px] px-1.5 py-0.2 rounded-full ${activeSubTab === 'records' ? 'bg-white/20 text-white' : 'bg-white/10 text-slate-300'}">${recCount}</span>
            </button>
          </div>

          <!-- Sub-Tab Content 1: Employees -->
          <div id="erp-subtab-employees-${dept.id}" class="${activeSubTab === 'employees' ? 'block' : 'hidden'} overflow-x-auto">
            ${renderEmployeesTable(dept.employees)}
          </div>

          <!-- Sub-Tab Content 2: Devices -->
          <div id="erp-subtab-devices-${dept.id}" class="${activeSubTab === 'devices' ? 'block' : 'hidden'} overflow-x-auto">
            ${renderDeptDevicesTable(dept.devices)}
          </div>

          <!-- Sub-Tab Content 3: Tasks -->
          <div id="erp-subtab-tasks-${dept.id}" class="${activeSubTab === 'tasks' ? 'block' : 'hidden'} overflow-x-auto">
            ${renderTasksTable(dept.tasks)}
          </div>

          <!-- Sub-Tab Content 4: Records -->
          <div id="erp-subtab-records-${dept.id}" class="${activeSubTab === 'records' ? 'block' : 'hidden'} overflow-x-auto">
            ${renderRecordsTable(dept.records)}
          </div>

        </div>
      </div>
    `;
  });

  container.innerHTML = html;
}

function renderEmployeesTable(employees) {
  if (!employees || employees.length === 0) {
    return `<div class="py-6 text-center text-xs text-slate-400 italic">Chưa có nhân sự nào trong phòng ban này.</div>`;
  }
  return `
    <table class="w-full text-left text-xs border-collapse">
      <thead>
        <tr class="border-b border-slate-200 dark:border-white/10 text-slate-400 font-semibold bg-white/5">
          <th class="py-2.5 px-3">HỌ VÀ TÊN</th>
          <th class="py-2.5 px-3">CHỨC VỤ</th>
          <th class="py-2.5 px-3">EMAIL</th>
          <th class="py-2.5 px-3">SỐ ĐIỆN THOẠI</th>
        </tr>
      </thead>
      <tbody class="divide-y divide-white/5">
        ${employees.map(e => `
          <tr class="hover:bg-white/[0.02] transition-colors">
            <td class="py-2 px-3 font-semibold text-slate-800 dark:text-slate-200 flex items-center gap-2">
              <span class="w-6 h-6 rounded-full bg-blue-500/20 text-blue-300 flex items-center justify-center text-[10px] font-bold">
                ${(e.name || 'N').charAt(0).toUpperCase()}
              </span>
              <span>${escapeHtml(e.name)}</span>
            </td>
            <td class="py-2 px-3 text-slate-300">${escapeHtml(e.position || '—')}</td>
            <td class="py-2 px-3 text-cyan-400 font-mono text-[11px]">${escapeHtml(e.email || '—')}</td>
            <td class="py-2 px-3 text-slate-400 font-mono text-[11px]">${escapeHtml(e.phone || '—')}</td>
          </tr>
        `).join('')}
      </tbody>
    </table>
  `;
}

/**
 * Bảng thiết bị theo PHÒNG BAN (ERP).
 *
 * Phase 78: hàm này trước đây tên là `renderDevicesTable` — trùng đúng tên với
 * hàm cùng chức năng khác ở trên. Trong JS, định nghĩa sau đè định nghĩa trước,
 * nên bản này (trả về CHUỖI HTML) đã đè bản kia (ghi thẳng vào DOM). Hậu quả:
 * `loadDevices()` gọi vào đây, nhận về một chuỗi rồi vứt đi, còn bảng
 * `#devices-table-body` không bao giờ được điền và ô "chưa có thiết bị" cũng
 * không hiện — người dùng thấy một bảng trống không giải thích.
 *
 * Đổi tên để hai chức năng không còn tranh nhau một tên.
 */
function renderDeptDevicesTable(devices) {
  if (!devices || devices.length === 0) {
    return `<div class="py-6 text-center text-xs text-slate-400 italic">Chưa có thiết bị nào được ghi nhận cho phòng ban này.</div>`;
  }
  return `
    <table class="w-full text-left text-xs border-collapse">
      <thead>
        <tr class="border-b border-slate-200 dark:border-white/10 text-slate-400 font-semibold bg-white/5">
          <th class="py-2.5 px-3">TÊN THIẾT BỊ (HOSTNAME)</th>
          <th class="py-2.5 px-3">ĐỊA CHỈ IP</th>
          <th class="py-2.5 px-3">LOẠI THIẾT BỊ</th>
          <th class="py-2.5 px-3">NGƯỜI PHỤ TRÁCH</th>
          <th class="py-2.5 px-3">AGENT</th>
        </tr>
      </thead>
      <tbody class="divide-y divide-white/5">
        ${devices.map(d => `
          <tr class="hover:bg-white/[0.02] transition-colors">
            <td class="py-2 px-3 font-semibold text-slate-800 dark:text-slate-200 flex items-center gap-2">
              <span class="text-emerald-400">💻</span>
              <span class="font-mono text-[11px]">${escapeHtml(d.hostname)}</span>
            </td>
            <td class="py-2 px-3">
              <span class="px-2 py-0.5 rounded font-mono text-[11px] bg-slate-100 dark:bg-white/5 border border-slate-200 dark:border-white/10 text-cyan-400">
                ${escapeHtml(d.ip_address || 'Chưa gán IP')}
              </span>
            </td>
            <td class="py-2 px-3 text-slate-300">
              <span class="px-2 py-0.5 rounded text-[10px] font-semibold bg-emerald-500/10 text-emerald-300 border border-emerald-400/20">
                ${escapeHtml(d.type || 'Workstation')}
              </span>
            </td>
            <td class="py-2 px-3 text-slate-400 flex items-center gap-1.5">
              <span>👤</span>
              <span>${escapeHtml(d.owner_name || 'Chưa gán')}</span>
            </td>
            <td class="py-2 px-3">${agentBadgeHtml(d.agent)}</td>
          </tr>
        `).join('')}
      </tbody>
    </table>
  `;
}

function renderTasksTable(tasks) {
  if (!tasks || tasks.length === 0) {
    return `<div class="py-6 text-center text-xs text-slate-400 italic">Không có công việc nào đang chờ thực hiện.</div>`;
  }
  return `
    <table class="w-full text-left text-xs border-collapse">
      <thead>
        <tr class="border-b border-slate-200 dark:border-white/10 text-slate-400 font-semibold bg-white/5">
          <th class="py-2.5 px-3">TIÊU ĐỀ CÔNG VIỆC</th>
          <th class="py-2.5 px-3">NGƯỜI PHỤ TRÁCH</th>
          <th class="py-2.5 px-3">TRẠNG THÁI</th>
          <th class="py-2.5 px-3">HẠN CHÓT</th>
        </tr>
      </thead>
      <tbody class="divide-y divide-white/5">
        ${tasks.map(t => {
    let statusBadge = `<span class="px-2 py-0.5 rounded-full text-[10px] font-bold bg-amber-500/20 text-amber-300 border border-amber-400/30">⏳ Đang Chờ</span>`;
    if (t.status === 'completed' || t.status === 'done') {
      statusBadge = `<span class="px-2 py-0.5 rounded-full text-[10px] font-bold bg-emerald-500/20 text-emerald-300 border border-emerald-400/30">✔ Hoàn Thành</span>`;
    } else if (t.status === 'in_progress') {
      statusBadge = `<span class="px-2 py-0.5 rounded-full text-[10px] font-bold bg-cyan-500/20 text-cyan-300 border border-cyan-400/30">⚡ Đang Thực Hiện</span>`;
    }

    return `
            <tr class="hover:bg-white/[0.02] transition-colors">
              <td class="py-2 px-3 font-semibold text-slate-800 dark:text-slate-200">${escapeHtml(t.title)}</td>
              <td class="py-2 px-3 text-slate-300 flex items-center gap-1.5">
                <span>👤</span>
                <span>${escapeHtml(t.assignee_name || 'Chưa phân công')}</span>
              </td>
              <td class="py-2 px-3">${statusBadge}</td>
              <td class="py-2 px-3 text-slate-400 font-mono text-[11px]">${escapeHtml(t.due_date || 'Không có')}</td>
            </tr>
          `;
  }).join('')}
      </tbody>
    </table>
  `;
}

function renderRecordsTable(records) {
  if (!records || records.length === 0) {
    return `<div class="py-6 text-center text-xs text-slate-400 italic">Chưa có sổ sách hoặc tài liệu nào được lưu trữ.</div>`;
  }
  return `
    <table class="w-full text-left text-xs border-collapse">
      <thead>
        <tr class="border-b border-slate-200 dark:border-white/10 text-slate-400 font-semibold bg-white/5">
          <th class="py-2.5 px-3">TÊN TÀI LIỆU / HỒ SƠ</th>
          <th class="py-2.5 px-3">ĐƯỜNG DẪN LƯU TRỮ</th>
          <th class="py-2.5 px-3">NGÀY TẠO</th>
        </tr>
      </thead>
      <tbody class="divide-y divide-white/5">
        ${records.map(r => `
          <tr class="hover:bg-white/[0.02] transition-colors">
            <td class="py-2 px-3 font-semibold text-slate-800 dark:text-slate-200 flex items-center gap-2">
              <span class="text-amber-400">📄</span>
              <span>${escapeHtml(r.document_name)}</span>
            </td>
            <td class="py-2 px-3 font-mono text-[11px] text-cyan-400">${escapeHtml(r.file_path || '—')}</td>
            <td class="py-2 px-3 text-slate-400 font-mono text-[11px]">${escapeHtml(r.date_created || '—')}</td>
          </tr>
        `).join('')}
      </tbody>
    </table>
  `;
}

function toggleDeptAccordion(deptId) {
  if (g_erp_expanded_depts.has(deptId)) {
    g_erp_expanded_depts.delete(deptId);
  } else {
    g_erp_expanded_depts.add(deptId);
  }
  renderErpTree(g_erp_departments);
}

function switchDeptSubTab(deptId, tabName) {
  g_erp_active_subtabs[deptId] = tabName;
  const tabs = ['employees', 'devices', 'tasks', 'records'];
  tabs.forEach(t => {
    const pane = document.getElementById(`erp-subtab-${t}-${deptId}`);
    if (pane) {
      if (t === tabName) {
        pane.classList.remove('hidden');
        pane.classList.add('block');
      } else {
        pane.classList.remove('block');
        pane.classList.add('hidden');
      }
    }
  });
  renderErpTree(g_erp_departments);
}

function filterErpTree() {
  const input = document.getElementById('erp-search-input');
  const q = (input ? input.value : '').toLowerCase().trim();

  if (!q) {
    renderErpTree(g_erp_departments);
    return;
  }

  const filtered = g_erp_departments.filter(d => {
    const matchDept = (d.name || '').toLowerCase().includes(q) || (d.description || '').toLowerCase().includes(q);
    const matchEmp = (d.employees || []).some(e => (e.name || '').toLowerCase().includes(q) || (e.position || '').toLowerCase().includes(q) || (e.email || '').toLowerCase().includes(q));
    const matchDev = (d.devices || []).some(dev => (dev.hostname || '').toLowerCase().includes(q) || (dev.ip_address || '').toLowerCase().includes(q));
    const matchTask = (d.tasks || []).some(t => (t.title || '').toLowerCase().includes(q));
    const matchRec = (d.records || []).some(r => (r.document_name || '').toLowerCase().includes(q));
    return matchDept || matchEmp || matchDev || matchTask || matchRec;
  });

  // Mở rộng tất cả các phòng ban tìm thấy
  filtered.forEach(d => g_erp_expanded_depts.add(d.id));
  renderErpTree(filtered);
}

async function handleErpBulkImport(event) {
  const fileInput = event.target;
  const file = fileInput.files ? fileInput.files[0] : null;
  if (!file) return;

  const banner = document.getElementById('erp-import-status-banner');
  if (banner) {
    banner.className = 'p-3 rounded-xl border text-xs bg-cyan-500/10 border-cyan-400/30 text-cyan-300 flex items-center gap-2';
    banner.innerHTML = `
      <div class="animate-spin w-4 h-4 border-2 border-cyan-400 border-t-transparent rounded-full flex-shrink-0"></div>
      <span>Đang tải lên và xử lý tệp <b>${escapeHtml(file.name)}</b>... Kiểm tra tính toàn vẹn và Transaction Rollback...</span>
    `;
    banner.classList.remove('hidden');
  }

  const formData = new FormData();
  formData.append('file', file);

  try {
    const token = getAuthToken();
    const headers = {};
    if (token) headers['Authorization'] = `Bearer ${token}`;

    const res = await fetch(`${API_BASE}/api/erp/import`, {
      method: 'POST',
      headers: headers,
      body: formData,
    });

    const data = await res.json().catch(() => ({}));

    if (res.ok && data.status === 'success') {
      const stats = data.stats || {};
      if (banner) {
        banner.className = 'p-3.5 rounded-xl border text-xs bg-emerald-500/10 border-emerald-400/30 text-emerald-300 flex items-start gap-2.5';
        banner.innerHTML = `
          <span class="text-base">✅</span>
          <div>
            <div class="font-bold text-emerald-200">Import Dữ Liệu ERP Thành Công Rực Rỡ!</div>
            <div class="text-[11px] text-emerald-300/80 mt-0.5">
              Đã nạp: <b>+${stats.departments || 0}</b> phòng ban, <b>+${stats.employees || 0}</b> nhân sự, <b>+${stats.devices || 0}</b> thiết bị, <b>+${stats.tasks || 0}</b> công việc, <b>+${stats.records || 0}</b> sổ sách tài liệu.
            </div>
          </div>
        `;
      }
      showToast('Import dữ liệu ERP thành công!', 'success');
      await Promise.all([loadErpStructure(), loadKpiLogs()]);
    } else {
      const errMsg = data.message || data.detail || `Lỗi HTTP ${res.status}`;
      if (banner) {
        banner.className = 'p-3.5 rounded-xl border text-xs bg-rose-500/10 border-rose-400/30 text-rose-300 flex items-start gap-2.5';
        banner.innerHTML = `
          <span class="text-base">❌</span>
          <div>
            <div class="font-bold text-rose-200">Quá Trình Import Bị Hủy Bỏ (Đã Rollback An Toàn)</div>
            <div class="text-[11px] text-rose-300/80 mt-0.5">${escapeHtml(errMsg)}</div>
          </div>
        `;
      }
      showToast(errMsg, 'error');
    }
  } catch (err) {
    console.error('[ERP] Lỗi khi upload import file:', err);
    if (banner) {
      banner.className = 'p-3 rounded-xl border text-xs bg-rose-500/10 border-rose-400/30 text-rose-300';
      banner.innerHTML = `❌ Lỗi kết nối hoặc xử lý file: ${escapeHtml(err.message || 'Lỗi không xác định')}`;
    }
    showToast(err.message || 'Lỗi xử lý file', 'error');
  } finally {
    fileInput.value = ''; // Reset file input
  }
}

// ── QUẢN LÝ THÊM & XÓA PHÒNG BAN TRỰC TIẾP (PHASE 47.1) ──────────────────────

function openAddDepartmentModal() {
  const modal = document.getElementById('modal-add-department');
  const nameInput = document.getElementById('new-dept-name');
  const descInput = document.getElementById('new-dept-desc');
  if (nameInput) nameInput.value = '';
  if (descInput) descInput.value = '';
  if (modal) {
    modal.classList.remove('hidden');
    if (nameInput) setTimeout(() => nameInput.focus(), 100);
  }
}

function closeAddDepartmentModal() {
  const modal = document.getElementById('modal-add-department');
  if (modal) modal.classList.add('hidden');
}

async function handleCreateDepartment(event) {
  event.preventDefault();
  const nameInput = document.getElementById('new-dept-name');
  const descInput = document.getElementById('new-dept-desc');
  const submitBtn = document.getElementById('btn-submit-create-dept');

  const name = (nameInput ? nameInput.value : '').trim();
  const description = (descInput ? descInput.value : '').trim();

  if (!name) {
    showToast('Vui lòng nhập tên phòng ban!', 'warning');
    return;
  }

  if (submitBtn) {
    submitBtn.disabled = true;
    submitBtn.innerHTML = `
      <div class="animate-spin w-3.5 h-3.5 border-2 border-white border-t-transparent rounded-full"></div>
      <span>Đang lưu...</span>
    `;
  }

  try {
    const res = await apiFetch(`${API_BASE}/api/erp/department`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ name, description }),
    });

    const data = await res.json().catch(() => ({}));
    if (res.ok && data.status === 'success') {
      showToast(`Đã tạo phòng ban "${name}" thành công!`, 'success');
      closeAddDepartmentModal();
      await loadErpStructure();
      // Mở rộng phòng ban mới tạo
      if (data.department && data.department.id) {
        g_erp_expanded_depts.add(data.department.id);
        renderErpTree(g_erp_departments);
      }
    } else {
      const errMsg = data.detail || data.message || `Lỗi HTTP ${res.status}`;
      showToast(errMsg, 'error');
    }
  } catch (err) {
    console.error('[ERP] Lỗi tạo phòng ban:', err);
    showToast(err.message || 'Lỗi khi lưu phòng ban', 'error');
  } finally {
    if (submitBtn) {
      submitBtn.disabled = false;
      submitBtn.innerHTML = `
        <svg width="14" height="14" fill="none" stroke="currentColor" stroke-width="2" viewBox="0 0 24 24"><polyline points="20 6 9 17 4 12"/></svg>
        Lưu Phòng Ban
      `;
    }
  }
}

async function deleteDepartmentPrompt(deptId, deptName) {
  const confirmed = confirm(`Bạn có chắc chắn muốn xóa phòng ban "${deptName}"?\nLưu ý: Hành động này sẽ xóa phòng ban khỏi cơ sở dữ liệu.`);
  if (!confirmed) return;

  try {
    const res = await apiFetch(`${API_BASE}/api/erp/department/${deptId}`, {
      method: 'DELETE',
    });
    const data = await res.json().catch(() => ({}));
    if (res.ok && data.status === 'success') {
      showToast(`Đã xóa phòng ban "${deptName}" thành công!`, 'success');
      g_erp_expanded_depts.delete(deptId);
      await loadErpStructure();
    } else {
      const errMsg = data.detail || data.message || `Lỗi HTTP ${res.status}`;
      showToast(errMsg, 'error');
    }
  } catch (err) {
    console.error('[ERP] Lỗi xóa phòng ban:', err);
    showToast(err.message || 'Lỗi khi xóa phòng ban', 'error');
  }
}

// ═══════════════════════════════════════════════════════════════════════════
// ── QUẢN TRỊ TÀI KHOẢN NGƯỜI DÙNG (USER ADMIN CONTROLLER - PHASE 14) ──────
// ═══════════════════════════════════════════════════════════════════════════

let usersList = [];

async function apiGetUsers() {
  try {
    const res = await apiFetch(`${API_BASE}/api/v1/users`);
    if (!res.ok) {
      const err = await res.json();
      throw new Error(err.detail || `HTTP ${res.status}`);
    }
    return await res.json();
  } catch (err) {
    console.error('[UserAdmin] Lỗi lấy danh sách user:', err);
    throw err;
  }
}

async function apiCreateUser(userData) {
  const res = await apiFetch(`${API_BASE}/api/v1/users`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(userData),
  });
  const data = await res.json();
  if (!res.ok) throw new Error(data.detail || 'Lỗi tạo tài khoản mới');
  return data;
}

async function apiUpdateUser(userId, userData) {
  const res = await apiFetch(`${API_BASE}/api/v1/users/${encodeURIComponent(userId)}`, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(userData),
  });
  const data = await res.json();
  if (!res.ok) throw new Error(data.detail || 'Lỗi cập nhật tài khoản');
  return data;
}

async function apiDeleteUser(userId) {
  const res = await apiFetch(`${API_BASE}/api/v1/users/${encodeURIComponent(userId)}`, {
    method: 'DELETE',
  });
  const data = await res.json();
  if (!res.ok) throw new Error(data.detail || 'Lỗi xóa tài khoản');
  return data;
}

async function apiChangeUserPassword(userId, newPassword) {
  const res = await apiFetch(`${API_BASE}/api/v1/users/${encodeURIComponent(userId)}/password`, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ new_password: newPassword }),
  });
  const data = await res.json();
  if (!res.ok) throw new Error(data.detail || 'Lỗi đổi mật khẩu');
  return data;
}

// ═══════════════════════════════════════════════════════════════════════════
// ── USER MANAGEMENT & RBAC CONTROLS (PHASE 14 ENTERPRISE) ─────────────────
// ═══════════════════════════════════════════════════════════════════════════

async function fetchUsers() {
  const tbody = document.getElementById('users-table-body');
  const countTag = document.getElementById('users-count-tag');
  // Phase 78: badge-users-count từng nằm trên nút nav "Quản Lý Tài Khoản".
  // Nút đó bị gỡ khi gộp tab Tài Khoản vào Bảo Mật, nên badge cũng không còn.
  // Số tài khoản nay hiện ở `users-count-tag` trong khối Tài khoản — cùng một
  // con số, hiện đúng một lần.
  const badgeCount = null;

  try {
    const data = await apiGetUsers();
    if (data && data.status === 'success') {
      usersList = data.users || [];
      if (countTag) countTag.textContent = `${usersList.length} tài khoản`;
      if (badgeCount) badgeCount.textContent = `${usersList.length}`;
      updateUsersKPIs(usersList);
      filterUsersList();
    }
  } catch (err) {
    if (tbody) {
      tbody.innerHTML = `<tr><td colspan="7" class="py-8 text-center text-rose-400">Lỗi tải danh sách người dùng: ${escapeHtml(err.message)}</td></tr>`;
    }
    showToast(`Không thể tải danh sách tài khoản: ${err.message}`, 'error');
  }
}

function updateUsersKPIs(users) {
  const total = users.length;
  let admins = 0;
  let managers = 0;
  let viewers = 0;

  for (const u of users) {
    const r = (u.role || '').toLowerCase();
    if (r === 'admin') admins++;
    else if (r === 'manager') managers++;
    else viewers++;
  }

  const setEl = (id, val) => {
    const el = document.getElementById(id);
    if (el) el.textContent = val;
  };

  setEl('kpi-total-users', total);
  setEl('kpi-admin-users', admins);
  setEl('kpi-manager-users', managers);
  setEl('kpi-viewer-users', viewers);
}

function filterUsersList() {
  const query = (document.getElementById('user-search-input')?.value || '').toLowerCase().trim();
  const roleFilter = (document.getElementById('user-role-filter')?.value || 'ALL').toLowerCase();

  const filtered = usersList.filter(u => {
    // 1. Role filter
    if (roleFilter !== 'all' && (u.role || '').toLowerCase() !== roleFilter) {
      return false;
    }
    // 2. Query filter
    if (query) {
      const combined = `${u.username || ''} ${u.full_name || ''} ${u.id || ''}`.toLowerCase();
      if (!combined.includes(query)) return false;
    }
    return true;
  });

  renderUsersTable(filtered);
}

function setRoleFilter(role) {
  const select = document.getElementById('user-role-filter');
  if (select) {
    select.value = role;
    filterUsersList();
  }
}

function exportUsersList() {
  if (!usersList || usersList.length === 0) {
    showToast('Chưa có dữ liệu người dùng để xuất.', 'info');
    return;
  }
  // Xuất thông tin an toàn (loại bỏ các trường nhạy cảm nếu có)
  const safeData = usersList.map(u => ({
    id: u.id,
    username: u.username,
    full_name: u.full_name,
    role: u.role,
    created_at: u.created_at || null,
  }));

  const jsonStr = JSON.stringify(safeData, null, 2);
  const blob = new Blob([jsonStr], { type: 'application/json' });
  const a = document.createElement('a');
  a.href = URL.createObjectURL(blob);
  a.download = `vnmateai_users_${new Date().toISOString().slice(0, 10)}.json`;
  a.click();
  showToast('📥 Đã tải xuống danh sách tài khoản!', 'success');
}

function renderUsersTable(users) {
  const tbody = document.getElementById('users-table-body');
  if (!tbody) return;

  if (!users || users.length === 0) {
    tbody.innerHTML = `<tr><td colspan="7" class="py-8 text-center text-slate-500 italic">Không tìm thấy tài khoản nào khớp với điều kiện tìm kiếm.</td></tr>`;
    return;
  }

  const roleBadges = {
    admin: '<span class="inline-flex items-center gap-1.5 px-2.5 py-1 rounded-full text-[11px] font-bold bg-rose-500/15 text-rose-300 border border-rose-500/30 shadow-[0_0_8px_rgba(244,63,94,0.15)]"><span class="w-1.5 h-1.5 rounded-full bg-rose-400 animate-pulse"></span>Quản Trị Viên</span>',
    manager: '<span class="inline-flex items-center gap-1.5 px-2.5 py-1 rounded-full text-[11px] font-bold bg-purple-500/15 text-purple-300 border border-purple-500/30"><span class="w-1.5 h-1.5 rounded-full bg-purple-400"></span>Trưởng Phòng</span>',
    viewer: '<span class="inline-flex items-center gap-1.5 px-2.5 py-1 rounded-full text-[11px] font-bold bg-emerald-500/15 text-emerald-300 border border-emerald-500/30"><span class="w-1.5 h-1.5 rounded-full bg-emerald-400"></span>Nhân Viên</span>',
  };

  tbody.innerHTML = users.map((u, idx) => {
    const roleKey = (u.role || 'viewer').toLowerCase();
    const badge = roleBadges[roleKey] || `<span class="px-2 py-0.5 rounded text-[11px] bg-slate-500/20 text-slate-300 border border-slate-500/30">${escapeHtml(u.role)}</span>`;
    const isCurrent = currentUser && (currentUser.username === u.username || currentUser.id === u.id);

    // Formatted created_at date
    let createdDisplay = '--';
    if (u.created_at) {
      try {
        const d = parseServerTime(u.created_at);   // users.created_at lưu UTC
        if (!d) throw new Error('bad date');
        const day = String(d.getDate()).padStart(2, '0');
        const month = String(d.getMonth() + 1).padStart(2, '0');
        const year = d.getFullYear();
        const hours = String(d.getHours()).padStart(2, '0');
        const mins = String(d.getMinutes()).padStart(2, '0');
        createdDisplay = `${day}/${month}/${year} ${hours}:${mins}`;
      } catch (e) {
        createdDisplay = String(u.created_at).slice(0, 10);
      }
    }

    return `
      <tr class="hover:bg-slate-50 dark:hover:bg-white/[0.02] transition-colors">
        <td class="py-3 px-4 text-center text-slate-500 dark:text-slate-400 font-mono">${idx + 1}</td>
        <td class="py-3 px-4">
          <div class="flex items-center gap-2.5">
            <span class="w-8 h-8 rounded-lg bg-slate-100 dark:bg-white/5 border border-slate-200 dark:border-white/10 flex items-center justify-center text-[11px] font-bold text-primary-600 dark:text-cyan-300 font-mono">
              ${escapeHtml((u.username || 'US').substring(0, 2).toUpperCase())}
            </span>
            <div>
              <div class="font-mono font-semibold text-slate-800 dark:text-white flex items-center gap-1.5">
                ${escapeHtml(u.username)}
                ${isCurrent ? '<span class="text-[9px] px-1.5 py-0.5 rounded bg-primary-100 dark:bg-cyan-500/20 text-primary-600 dark:text-cyan-300 border border-primary-200 dark:border-cyan-400/40 font-sans font-bold">Bạn</span>' : ''}
              </div>
              <div class="text-[10px] text-slate-500 font-mono flex items-center gap-1 cursor-pointer hover:text-cyan-400 transition" onclick="copyText('${escapeHtml(u.id || '')}', 'Đã sao chép User ID!')" title="Bấm để sao chép User ID">
                ID: ${escapeHtml(u.id || '')}
                <svg width="10" height="10" fill="none" stroke="currentColor" stroke-width="2" viewBox="0 0 24 24"><rect x="9" y="9" width="13" height="13" rx="2" ry="2"/><path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"/></svg>
              </div>
            </div>
          </div>
        </td>
        <td class="py-3 px-4 text-slate-700 dark:text-slate-200 font-medium">${escapeHtml(u.full_name || u.username)}</td>
        <td class="py-3 px-4 text-center">${badge}<div class="mt-1 text-[11px] text-slate-500">${u.department ? escapeHtml(u.department) : 'chưa gán phòng ban'} · cấp ${u.clearance_level || 'theo vai trò'}</div></td>
        <td class="py-3 px-4 text-center">
          <span class="inline-flex items-center gap-1.5 px-2 py-0.5 rounded-full text-[10px] font-bold bg-emerald-500/15 text-emerald-400 border border-emerald-500/30">
            <span class="w-1.5 h-1.5 rounded-full bg-emerald-400 animate-pulse"></span>
            Hoạt động
          </span>
        </td>
        <td class="py-3 px-4 text-center text-slate-400 font-mono text-[11px]">${createdDisplay}</td>
        <td class="py-3 px-4 text-center">
          <div class="flex items-center justify-center gap-2">
            <!-- Sửa (Icon Bút) -->
            <button onclick="openEditUserModal('${escapeHtml(u.id)}')" title="Chỉnh sửa thông tin / phân quyền" class="w-8 h-8 rounded-lg bg-cyan-500/10 hover:bg-cyan-500/25 border border-cyan-500/20 hover:border-cyan-400/50 text-cyan-300 flex items-center justify-center transition shadow-sm">
              <svg width="14" height="14" fill="none" stroke="currentColor" stroke-width="2" viewBox="0 0 24 24"><path d="M11 4H4a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h14a2 2 0 0 0 2-2v-7"/><path d="M18.5 2.5a2.121 2.121 0 0 1 3 3L12 15l-4 1 1-4 9.5-9.5z"/></svg>
            </button>
            <!-- Đổi Pass (Icon Chìa khóa) -->
            <button onclick="openChangePasswordModal('${escapeHtml(u.id)}', '${escapeHtml(u.username)}')" title="Cấp lại mật khẩu mới" class="w-8 h-8 rounded-lg bg-purple-500/10 hover:bg-purple-500/25 border border-purple-500/20 hover:border-purple-400/50 text-purple-300 flex items-center justify-center transition shadow-sm">
              <svg width="14" height="14" fill="none" stroke="currentColor" stroke-width="2" viewBox="0 0 24 24"><circle cx="7.5" cy="15.5" r="5.5"/><path d="m21 2-9.6 9.6"/><path d="m15.5 7.5 3 3L22 7l-3-3"/></svg>
            </button>
            <!-- Xóa (Icon Thùng rác đỏ) -->
            ${isCurrent
        ? `<button disabled title="Không thể xóa tài khoản của chính bạn đang đăng nhập" class="w-8 h-8 rounded-lg bg-slate-500/10 border border-slate-500/20 text-slate-500 flex items-center justify-center cursor-not-allowed opacity-40">
                  <svg width="14" height="14" fill="none" stroke="currentColor" stroke-width="2" viewBox="0 0 24 24"><polyline points="3 6 5 6 21 6"/><path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6m3 0V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2"/><line x1="10" y1="11" x2="10" y2="17"/><line x1="14" y1="11" x2="14" y2="17"/></svg>
                </button>`
        : `<button onclick="openDeleteUserModal('${escapeHtml(u.id)}', '${escapeHtml(u.username)}', '${escapeHtml(u.full_name || '')}')" title="Xóa tài khoản" class="w-8 h-8 rounded-lg bg-rose-500/10 hover:bg-rose-500/25 border border-rose-500/20 hover:border-rose-400/50 text-rose-400 flex items-center justify-center transition shadow-sm">
                  <svg width="14" height="14" fill="none" stroke="currentColor" stroke-width="2" viewBox="0 0 24 24"><polyline points="3 6 5 6 21 6"/><path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6m3 0V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2"/><line x1="10" y1="11" x2="10" y2="17"/><line x1="14" y1="11" x2="14" y2="17"/></svg>
                </button>`
      }
          </div>
        </td>
      </tr>
    `;
  }).join('');
}

// Password eye toggle helper
/**
 * Bật/tắt hiển thị ô mật khẩu — xem bản đầy đủ ở dưới (hàm đã gộp Phase 78).
 */

// Password strength evaluator
function checkPasswordStrength(inputId, barId, textId, containerId) {
  const input = document.getElementById(inputId);
  const bar = document.getElementById(barId);
  const text = document.getElementById(textId);
  const container = document.getElementById(containerId);

  if (!input || !bar || !text || !container) return;
  const val = input.value;

  if (!val) {
    container.classList.add('hidden');
    return;
  }

  container.classList.remove('hidden');

  let score = 0;
  if (val.length >= 6) score++;
  if (val.length >= 8) score++;
  if (/[0-9]/.test(val)) score++;
  if (/[A-Z]/.test(val) || /[^A-Za-z0-9]/.test(val)) score++;

  if (val.length < 6) {
    bar.style.width = '20%';
    bar.className = 'h-full bg-rose-500 transition-all duration-300';
    text.textContent = 'Quá ngắn (Tối thiểu 6 ký tự)';
    text.className = 'font-bold text-rose-400';
  } else if (score <= 2) {
    bar.style.width = '45%';
    bar.className = 'h-full bg-amber-500 transition-all duration-300';
    text.textContent = 'Trung bình';
    text.className = 'font-bold text-amber-400';
  } else if (score === 3) {
    bar.style.width = '75%';
    bar.className = 'h-full bg-cyan-400 transition-all duration-300';
    text.textContent = 'Khá an toàn';
    text.className = 'font-bold text-cyan-300';
  } else {
    bar.style.width = '100%';
    bar.className = 'h-full bg-emerald-500 transition-all duration-300';
    text.textContent = 'Rất mạnh & an toàn';
    text.className = 'font-bold text-emerald-400';
  }
}

function copyText(txt, msg = 'Đã sao chép!') {
  if (navigator.clipboard && navigator.clipboard.writeText) {
    navigator.clipboard.writeText(txt).then(() => {
      showToast(msg, 'info');
    });
  }
}

// ABAC: danh sách phòng ban lấy từ cơ cấu ERP thật (admin thấy toàn bộ).
async function fillUserAbacFields(user) {
  const sel = document.getElementById('user-modal-department');
  const cl = document.getElementById('user-modal-clearance');
  if (cl) cl.value = user && user.clearance_level ? String(user.clearance_level) : '';
  if (!sel) return;
  try {
    const res = await fetch(`${API_BASE}/api/erp/structure`, { headers: { Authorization: `Bearer ${getAuthToken()}` } });
    const data = await res.json();
    const names = (data.departments || []).map(d => d.name);
    sel.innerHTML = '<option value="">— Chưa gán —</option>' +
      names.map(n => `<option value="${escapeHtml(n)}">${escapeHtml(n)}</option>`).join('');
  } catch (e) { /* giữ ô trống: vẫn lưu được các trường khác */ }
  sel.value = (user && user.department) || '';
}

function openAddUserModal() {
  document.getElementById('user-modal-title').textContent = 'Thêm Tài Khoản Mới';
  document.getElementById('user-modal-subtitle').textContent = 'Thiết lập tài khoản người dùng và vai trò phân quyền';
  document.getElementById('user-modal-id').value = '';
  const uInput = document.getElementById('user-modal-username');
  uInput.value = '';
  uInput.readOnly = false;
  uInput.classList.remove('opacity-60', 'cursor-not-allowed');
  document.getElementById('user-modal-fullname').value = '';
  document.getElementById('user-modal-role').value = 'viewer';
  fillUserAbacFields(null);
  const pwGroup = document.getElementById('user-modal-password-group');
  if (pwGroup) pwGroup.classList.remove('hidden');
  const pwInput = document.getElementById('user-modal-password');
  if (pwInput) {
    pwInput.value = '';
    pwInput.type = 'password';
    pwInput.required = true;
  }
  const strBox = document.getElementById('user-pw-strength-container');
  if (strBox) strBox.classList.add('hidden');
  const errEl = document.getElementById('user-modal-error');
  if (errEl) errEl.classList.add('hidden');
  document.getElementById('user-modal').classList.remove('hidden');
}

function openEditUserModal(userId) {
  const user = usersList.find(u => u.id === userId || u.username === userId);
  if (!user) {
    showToast('Không tìm thấy thông tin tài khoản cần sửa', 'error');
    return;
  }
  document.getElementById('user-modal-title').textContent = 'Chỉnh Sửa Tài Khoản';
  document.getElementById('user-modal-subtitle').textContent = `Cập nhật thông tin và vai trò cho tài khoản: ${user.username}`;
  document.getElementById('user-modal-id').value = user.id;
  const uInput = document.getElementById('user-modal-username');
  uInput.value = user.username;
  uInput.readOnly = true;
  uInput.classList.add('opacity-60', 'cursor-not-allowed');
  document.getElementById('user-modal-fullname').value = user.full_name || '';
  document.getElementById('user-modal-role').value = user.role || 'viewer';
  fillUserAbacFields(user);
  const pwGroup = document.getElementById('user-modal-password-group');
  if (pwGroup) pwGroup.classList.add('hidden');
  const pwInput = document.getElementById('user-modal-password');
  if (pwInput) {
    pwInput.value = '';
    pwInput.required = false;
  }
  const errEl = document.getElementById('user-modal-error');
  if (errEl) errEl.classList.add('hidden');
  document.getElementById('user-modal').classList.remove('hidden');
}

function closeUserModal() {
  const m = document.getElementById('user-modal');
  if (m) m.classList.add('hidden');
}

async function handleUserFormSubmit(e) {
  e.preventDefault();
  const id = document.getElementById('user-modal-id').value.trim();
  const username = document.getElementById('user-modal-username').value.trim();
  const full_name = document.getElementById('user-modal-fullname').value.trim();
  const role = document.getElementById('user-modal-role').value;
  const password = document.getElementById('user-modal-password').value;
  const department = (document.getElementById('user-modal-department') || {}).value || '';
  const clRaw = (document.getElementById('user-modal-clearance') || {}).value || '';
  const abac = { department, clearance_level: clRaw ? Number(clRaw) : null };
  const errEl = document.getElementById('user-modal-error');
  const btn = document.getElementById('btn-save-user');

  if (errEl) errEl.classList.add('hidden');
  if (btn) btn.disabled = true;

  try {
    if (!id) {
      // Thêm mới tài khoản
      if (!password || password.length < 6) {
        throw new Error('Mật khẩu khởi tạo phải có độ dài tối thiểu 6 ký tự.');
      }
      const created = await apiCreateUser({ username, full_name, role, password });
      const newId = (created && (created.user || created).id) || username;
      if (abac.department || abac.clearance_level) await apiUpdateUser(newId, abac);
      showToast('Đã tạo tài khoản thành công.', 'success');
    } else {
      // Cập nhật thông tin tài khoản
      await apiUpdateUser(id, { full_name, role, ...abac });
      showToast('Cập nhật tài khoản thành công.', 'success');
    }
    closeUserModal();
    await fetchUsers();
  } catch (err) {
    if (errEl) {
      errEl.textContent = err.message || 'Có lỗi xảy ra.';
      errEl.classList.remove('hidden');
    }
    showToast(err.message || 'Thao tác thất bại.', 'error');
  } finally {
    if (btn) btn.disabled = false;
  }
}

function openChangePasswordModal(userId, username) {
  document.getElementById('change-password-user-id').value = userId;
  document.getElementById('cp-user-display').textContent = username || userId;
  const pInput = document.getElementById('change-password-input');
  if (pInput) {
    pInput.value = '';
    pInput.type = 'password';
  }
  const cInput = document.getElementById('change-password-confirm');
  if (cInput) {
    cInput.value = '';
    cInput.type = 'password';
  }
  const strBox = document.getElementById('cp-strength-container');
  if (strBox) strBox.classList.add('hidden');
  const errEl = document.getElementById('change-password-error');
  if (errEl) errEl.classList.add('hidden');
  document.getElementById('change-password-modal').classList.remove('hidden');
}

function closeChangePasswordModal() {
  const m = document.getElementById('change-password-modal');
  if (m) m.classList.add('hidden');
}

async function handleChangePasswordSubmit(e) {
  e.preventDefault();
  const userId = document.getElementById('change-password-user-id').value;
  const newPass = document.getElementById('change-password-input').value;
  const confirmPass = document.getElementById('change-password-confirm').value;
  const errEl = document.getElementById('change-password-error');

  if (errEl) errEl.classList.add('hidden');

  if (newPass !== confirmPass) {
    if (errEl) {
      errEl.textContent = 'Mật khẩu xác nhận không khớp.';
      errEl.classList.remove('hidden');
    }
    return;
  }

  if (newPass.length < 6) {
    if (errEl) {
      errEl.textContent = 'Mật khẩu mới phải có tối thiểu 6 ký tự.';
      errEl.classList.remove('hidden');
    }
    return;
  }

  try {
    await apiChangeUserPassword(userId, newPass);
    showToast('Đã cấp lại mật khẩu thành công.', 'success');
    closeChangePasswordModal();
  } catch (err) {
    if (errEl) {
      errEl.textContent = err.message || 'Lỗi đổi mật khẩu.';
      errEl.classList.remove('hidden');
    }
    showToast(err.message || 'Đổi mật khẩu thất bại.', 'error');
  }
}

function openDeleteUserModal(userId, username, fullName) {
  document.getElementById('delete-user-id').value = userId;
  document.getElementById('delete-user-name-display').textContent = `${username} (${fullName || username})`;
  document.getElementById('delete-user-modal').classList.remove('hidden');
}

function closeDeleteUserModal() {
  const m = document.getElementById('delete-user-modal');
  if (m) m.classList.add('hidden');
}

async function submitDeleteUser() {
  const userId = document.getElementById('delete-user-id').value;
  const btn = document.getElementById('btn-confirm-delete-user');
  if (!userId) return;

  if (btn) btn.disabled = true;

  try {
    await apiDeleteUser(userId);
    showToast('Đã xóa tài khoản thành công.', 'success');
    closeDeleteUserModal();

    // Nếu người dùng tự xóa tài khoản của chính mình -> Đăng xuất ngay
    if (currentUser && (currentUser.id === userId || currentUser.username === userId)) {
      handleLogout('Bạn vừa xóa tài khoản của chính mình. Phiên làm việc đã kết thúc.');
      return;
    }

    await fetchUsers();
  } catch (err) {
    showToast(err.message || 'Xóa tài khoản thất bại.', 'error');
  } finally {
    if (btn) btn.disabled = false;
  }
}

// ═══════════════════════════════════════════════════════════════════════════
// ── PORTAL WEBSOCKET (Server-Push Real-Time UI Sync) ────────────────────────
// ═══════════════════════════════════════════════════════════════════════════

let _portalWs = null;
let _portalWsRetryTimeout = null;

function initPortalWebSocket() {
  if (_portalWs && (_portalWs.readyState === WebSocket.OPEN || _portalWs.readyState === WebSocket.CONNECTING)) {
    return; // Already connected
  }

  const proto = location.protocol === 'https:' ? 'wss:' : 'ws:';
  // Zero-Trust: server yêu cầu JWT khi mở Portal-UI WebSocket (stream log hệ thống
  // và cảnh báo bảo mật). Browser WebSocket API không cho set header nên token
  // truyền qua query param.
  const _wsToken = getAuthToken();
  if (!_wsToken) {
    console.warn('[PortalWS] Chưa đăng nhập — bỏ qua kết nối Portal-UI WebSocket.');
    return;
  }
  const wsUrl = `${proto}//${location.host}/ws/portal-ui?token=${encodeURIComponent(_wsToken)}`;

  _portalWs = new WebSocket(wsUrl);

  _portalWs.onopen = () => {
    console.info('[PortalWS] Kết nối Portal-UI thành công.');
    LogViewer.setWsStatus(true);
    // Start heartbeat ping every 20s to keep connection alive
    _portalWs._pingInterval = setInterval(() => {
      if (_portalWs.readyState === WebSocket.OPEN) {
        _portalWs.send(JSON.stringify({ action: 'ping' }));
      }
    }, 20_000);
  };

  _portalWs.onmessage = (evt) => {
    let msg;
    try { msg = JSON.parse(evt.data); } catch { return; }
    const event = msg.event;

    // Máy trạm vừa phản hồi / việc mới giao -> làm mới bảng giám sát #tasks.
    if (event === 'autonomy_changed') {
      loadAutonomyControls();
      showToast(msg.kill_switch ? 'Dừng khẩn cấp AI đã BẬT' : 'Dừng khẩn cấp AI đã tắt', msg.kill_switch ? 'warning' : 'info');
      return;
    }
    if (event === 'task_update') {
      scheduleTaskBoardReload();
      return;
    }

    // --- Phase 21: Route real-time log entries to LogViewer ---
    if (event === 'log_entry') {
      LogViewer.push(msg);
      return;
    }
    if (event === 'log_history' && Array.isArray(msg.logs)) {
      LogViewer.loadHistory(msg.logs);
      return;
    }

    // --- Phase 32: Visual Overlay HUD event ---
    if (event === 'show_visual') {
      if (typeof renderInBrowserHUD === 'function') {
        renderInBrowserHUD(msg.visual_type || msg.type, msg.data || {}, msg.title, msg.duration);
      }
      return;
    }

    if (event === 'action_approved_result') {
      const replyMsg = msg.reply || `Tác vụ "${msg.skill}" đã được phê duyệt và hoàn tất.`;
      _webChatHistory.push({ role: 'assistant', content: replyMsg });
      const card = document.getElementById('voice-response-card');
      const textEl = document.getElementById('voice-response-text');
      if (card && textEl) {
        card.classList.remove('hidden');
        textEl.innerHTML = renderPortalMarkdown(replyMsg);
        scheduleAiDownloadCheck();
      }
      showToast(`✅ Đã phê duyệt: ${replyMsg.slice(0, 80)}`, 'success');
      document.getElementById('pending-action-banner')?.classList.add('hidden');
      checkPendingAction();
      return;
    }

    // Phase 47: Sync voice response from HUD or mic across Web Portal
    if (event === 'voice_response') {
      const card = document.getElementById('voice-response-card');
      const textEl = document.getElementById('voice-response-text');
      const inputEl = document.getElementById('voice-input');
      if (inputEl && msg.query) {
        inputEl.value = msg.query;
      }
      if (card && textEl) {
        card.classList.remove('hidden');
        textEl.innerHTML = renderPortalMarkdown(msg.reply || msg.speech_reply || '_(không có nội dung phản hồi)_');
        scheduleAiDownloadCheck();
      }
      if (msg.audio_base64) {
        lastAudioBase64 = msg.audio_base64;
      }
      return;
    }

    // --- Phase 58 BƯỚC 2: biểu đồ & dòng tiền đẩy qua WebSocket ---
    // Nhờ đây, ra lệnh bằng giọng nói từ điện thoại thì biểu đồ tự hiện trên
    // mọi màn hình đang mở, không cần ai bấm "Làm mới".
    if (event === 'analytics_chart' && msg.chart_config) {
      if (typeof CommandCenter !== 'undefined') {
        CommandCenter.renderChart(msg.chart_config, {
          title: msg.title || 'Biểu Đồ',
          sqlSource: msg.sql_source,
          llmError: msg.llm_error,
        });
      }
      // Không ép chuyển sang tab: người đang xem màn hình khác không bị giật mình.
      // Chỉ báo nhỏ để họ biết có gì mới ở Bảng Điều Khiển.
      // Phase 79: tab kia nay cũng là Bảng Điều Khiển — phần C.E.O đã gộp vào
      // cùng một section, nên điều kiện trước đây ('không phải tab Trung Tâm
      // Chỉ Huy' ⇒ đang ở nơi khác) phải theo `tab-dashboard` mới đúng.
      if (!document.getElementById('tab-dashboard')?.classList.contains('active')) {
        console.info('[PortalWS] Có biểu đồ mới:', msg.title);
      }
      return;
    }

    if (event === 'cashflow_health') {
      // Phase 71: admin bỏ bảng "Sức Khoẻ Dòng Tiền" khỏi Trung Tâm Chỉ Huy —
      // tab đó nay phục vụ giám sát/vận hành, không phải báo cáo tài chính.
      // Endpoint và sự kiện phía server vẫn còn nguyên (không phá gì khác),
      // nhưng giữ lại đúng MỘT thứ: cảnh báo mức nghiêm trọng.
      //
      // Bỏ hẳn cả toast thì lúc dòng tiền thật sự kiệt, im lặng là tệ hơn là
      // hiện thêm một bảng. Báo động thì không tốn chỗ nào trên tab.
      if (msg && msg.is_critical) {
        showToast(`🚨 ${msg.message || 'Cảnh báo dòng tiền nghiêm trọng'}`, 'error');
      }
      return;
    }

    // ── Phase 59: External Alert from Webhook Gateway (AWS SNS, OCI Alarms, etc.) ──
    if (event === 'external_alert' && msg) {
      const alert = msg;
      if (typeof onWebhookAlert === 'function') {
        onWebhookAlert({
          source: alert.source || 'unknown',
          title: alert.title || 'Cảnh báo hệ thống',
          message: alert.message || '',
          severity: alert.severity || 'info',
          resource_id: alert.resource_id || '',
          resource_type: alert.resource_type || '',
          timestamp: alert.timestamp || new Date().toISOString(),
        });
      }
      // Toast notification regardless of tab
      const sevColor = { critical: 'error', high: 'error', medium: 'warning', low: 'info', info: 'info' }[alert?.severity] || 'info';
      showToast(`🚨 [${alert?.source?.toUpperCase()}] ${alert?.title || 'Cảnh báo'}`, sevColor);
      return;
    }

    if (event === 'switch_tab' && msg.tab) {
      switchTab(msg.tab);
    } else if (event === 'show_toast' && msg.message) {
      showToast(msg.message, msg.type || 'info');
    } else if (event === 'pong') {
      // Heartbeat response — silently ignore
    } else if (event === 'connected') {
      console.info('[PortalWS]', msg.message);
    } else if (event === 'system_stats' && msg.hardware) {
      // Real-time hardware push (optional future feature)
      const hw = msg.hardware;
      updateGauge('gauge-cpu-path', 'gauge-cpu-text', hw.cpu_percent);
      updateGauge('gauge-ram-path', 'gauge-ram-text', hw.ram_percent);
    }
  };

  _portalWs.onerror = (err) => {
    console.warn('[PortalWS] Lỗi kết nối:', err);
    LogViewer.setWsStatus(false);
  };

  _portalWs.onclose = () => {
    clearInterval(_portalWs?._pingInterval);
    LogViewer.setWsStatus(false);
    console.info('[PortalWS] Mất kết nối. Thử lại sau 5 giây...');
    // Auto-reconnect after 5s if user is still authenticated
    _portalWsRetryTimeout = setTimeout(() => {
      if (getAuthToken()) initPortalWebSocket();
    }, 5_000);
  };
}

// ═══════════════════════════════════════════════════════════════════════════
// ── PHASE 18: NATIVE DOMAIN SYNC & TELEGRAM GATEWAY ─────────────────────────
// ═══════════════════════════════════════════════════════════════════════════

let domainEmployeesList = [];
let domainComputersList = [];
let currentAdSubTab = 'users';

/**
 * Bật/tắt hiển thị một ô mật khẩu.
 *
 * Phase 78: trước đây có HAI hàm cùng tên. Bản sau (chỉ nhận `inputId`) đè bản
 * trước (nhận `inputId` + `btnEl` để đổi biểu tượng mắt). Hệ quả: 3 chỗ gọi
 * truyền nút vào như mong đổi biểu tượng, nhưng biểu tượng mắt KHÔNG BAO GIỜ
 * đổi — mật khẩu hiện ra mà vẫn tưởng đang ẩn.
 *
 * Nay gộp còn một hàm: `btnEl` là tuỳ chọn, có thì đổi biểu tượng.
 */
function togglePasswordVisibility(inputId, btnEl) {
  const input = document.getElementById(inputId);
  if (!input) return;
  const show = input.type === 'password';
  input.type = show ? 'text' : 'password';
  if (!btnEl) return;
  btnEl.innerHTML = show
    ? `<svg width="16" height="16" fill="none" stroke="currentColor" stroke-width="2" viewBox="0 0 24 24"><path d="M17.94 17.94A10.07 10.07 0 0 1 12 20c-7 0-11-8-11-8a18.45 18.45 0 0 1 5.06-5.94M9.9 4.24A9.12 9.12 0 0 1 12 4c7 0 11 8 11 8a18.5 18.5 0 0 1-2.16 3.19m-6.72-1.07a3 3 0 1 1-4.24-4.24"/><line x1="1" y1="1" x2="23" y2="23"/></svg>`
    : `<svg width="16" height="16" fill="none" stroke="currentColor" stroke-width="2" viewBox="0 0 24 24"><path d="M1 12s4-8 11-8 11 8 11 8-4 8-11 8-11-8-11-8z"/><circle cx="12" cy="12" r="3"/></svg>`;
}

async function loadTelegramConfig() {
  const toggle = document.getElementById('toggle-tg-gateway');
  try {
    const res = await apiFetch(`${API_BASE}/api/v1/telegram/config`);
    if (res.ok) {
      const data = await res.json();
      const cfg = data.config || {};
      const enabled = data.enabled !== undefined ? !!data.enabled : !!cfg.enabled;
      if (toggle) toggle.checked = enabled;

      // Ô token luôn để trống (bảo mật), nhưng hiện badge + hint nếu đã có token
      const inputToken = document.getElementById('cfg-tg-token');
      if (inputToken) inputToken.value = '';

      // Determine if a token is saved (server returns masked '••••••••' when it has a value)
      const hasToken = !!(data.bot_token && data.bot_token.trim() !== '' && data.bot_token !== '');
      const tgTokenBadge = document.getElementById('cfg-tg-token-badge');
      const tgTokenHint = document.getElementById('cfg-tg-token-hint');
      if (inputToken) {
        if (hasToken) {
          inputToken.value = SECRET_MASK;
          inputToken.setAttribute('data-masked', '1');
        } else {
          inputToken.value = '';
          inputToken.removeAttribute('data-masked');
        }
        inputToken.placeholder = hasToken
          ? '•••••••• — đã có token lưu sẵn, để nguyên hoặc gõ mới để đổi'
          : '123456789:ABCdefGhIJKlmNoPQRsTUVwxyZ...';

        if (!inputToken._hasMaskHandlers) {
          inputToken._hasMaskHandlers = true;
          inputToken.addEventListener('focus', function () {
            if (this.value === SECRET_MASK) this.select();
          });
          inputToken.addEventListener('blur', function () {
            if (!this.value.trim() && this.getAttribute('data-masked') === '1') {
              this.value = SECRET_MASK;
            }
          });
        }
      }
      if (tgTokenBadge) {
        if (hasToken) tgTokenBadge.classList.remove('hidden');
        else tgTokenBadge.classList.add('hidden');
      }
      if (tgTokenHint) {
        if (hasToken) {
          tgTokenHint.textContent = '✔ Đã có Bot Token lưu sẵn (••••••••) — để nguyên hoặc gõ token mới.';
          tgTokenHint.className = 'text-[10px] text-emerald-600 dark:text-emerald-400 mt-1';
        } else {
          tgTokenHint.textContent = 'Chưa có Bot Token nào được lưu.';
          tgTokenHint.className = 'text-[10px] text-amber-600 dark:text-amber-400 italic mt-1';
        }
      }

      const inputAdmins = document.getElementById('cfg-tg-admins');
      const inputGroup = document.getElementById('cfg-tg-group');
      if (inputAdmins && !inputAdmins.value && cfg.admin_chat_ids) inputAdmins.value = Array.isArray(cfg.admin_chat_ids) ? cfg.admin_chat_ids.join(', ') : cfg.admin_chat_ids;
      if (inputAdmins && !inputAdmins.value && data.admin_chat_ids) inputAdmins.value = Array.isArray(data.admin_chat_ids) ? data.admin_chat_ids.join(', ') : data.admin_chat_ids;
      if (inputGroup && !inputGroup.value && cfg.incident_group_id) inputGroup.value = cfg.incident_group_id;
      if (inputGroup && !inputGroup.value && data.incident_group_id) inputGroup.value = data.incident_group_id;
    }
  } catch (err) {
    console.debug('[Telegram] Error loading config:', err);
  }
}

async function loadTelegramStatus() {
  const badge = document.getElementById('tg-gateway-status-badge');
  const toggle = document.getElementById('toggle-tg-gateway');
  if (!badge) return;
  try {
    const res = await apiFetch(`${API_BASE}/api/v1/telegram/status`);
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    const data = await res.json();
    const enabled = data.enabled !== undefined ? !!data.enabled : (toggle ? toggle.checked : false);
    if (toggle && data.enabled !== undefined) toggle.checked = enabled;

    if (!enabled) {
      badge.className = 'px-2 py-0.5 rounded text-[10px] font-bold bg-slate-100 dark:bg-slate-700 text-slate-500 dark:text-slate-400 border border-slate-200 dark:border-slate-600';
      badge.textContent = '○ ĐANG TẮT';
    } else if (data.gateway_running) {
      badge.className = 'px-2 py-0.5 rounded text-[10px] font-bold bg-emerald-500/20 text-emerald-400 border border-emerald-500/40';
      badge.textContent = '● ĐANG HOẠT ĐỘNG';
    } else {
      badge.className = 'px-2 py-0.5 rounded text-[10px] font-bold bg-amber-500/20 text-amber-400 border border-amber-500/40';
      badge.textContent = '⚠️ CHƯA KẾT NỐI';
    }
  } catch (err) {
    badge.className = 'px-2 py-0.5 rounded text-[10px] font-bold bg-slate-500/20 text-slate-400 border border-slate-500/40';
    badge.textContent = 'Ngoại tuyến';
  }
}

async function handleTelegramToggleChange(enabled) {
  const badge = document.getElementById('tg-gateway-status-badge');
  try {
    const res = await apiFetch(`${API_BASE}/api/v1/telegram/toggle`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ enabled }),
    });
    const data = await res.json();
    if (!res.ok) throw new Error(data.detail || `HTTP ${res.status}`);
    showToast(data.message || (enabled ? '✅ Đã kích hoạt Telegram Gateway' : 'ℹ️ Đã dừng Telegram Gateway'), enabled ? 'success' : 'info');
    await loadTelegramStatus();
  } catch (err) {
    showToast(`❌ Lỗi bật/tắt Telegram: ${err.message}`, 'error');
    const toggle = document.getElementById('toggle-tg-gateway');
    if (toggle) toggle.checked = !enabled;
  }
}

async function loadADSyncStatus() {
  const toggle = document.getElementById('toggle-ad-sync');
  const badge = document.getElementById('ad-sync-status-badge');
  const btnSync = document.getElementById('btn-sync-domain');
  try {
    const res = await apiFetch(`${API_BASE}/api/v1/domain/config`);
    if (res.ok) {
      const data = await res.json();
      const enabled = !!data.enabled;
      if (toggle) toggle.checked = enabled;
      if (badge) {
        badge.className = enabled
          ? 'px-2 py-0.5 rounded text-[10px] font-bold bg-blue-500/20 text-blue-500 dark:text-blue-400 border border-blue-500/40'
          : 'px-2 py-0.5 rounded text-[10px] font-bold bg-slate-100 dark:bg-slate-700 text-slate-500 dark:text-slate-400 border border-slate-200 dark:border-slate-600';
        badge.textContent = enabled ? '● ĐANG BẬT' : '○ ĐANG TẮT';
      }
      if (btnSync) {
        btnSync.disabled = !enabled;
        btnSync.classList.toggle('opacity-50', !enabled);
        btnSync.classList.toggle('cursor-not-allowed', !enabled);
      }
    }
  } catch (err) {
    console.debug('[Domain] Error loading config:', err);
  }
}

async function handleADToggleChange(enabled) {
  const badge = document.getElementById('ad-sync-status-badge');
  const btnSync = document.getElementById('btn-sync-domain');
  try {
    const res = await apiFetch(`${API_BASE}/api/v1/domain/toggle`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ enabled }),
    });
    const data = await res.json();
    if (!res.ok) throw new Error(data.detail || `HTTP ${res.status}`);
    showToast(data.message || (enabled ? '✅ Đã kích hoạt Active Directory' : 'ℹ️ Đã tắt Active Directory'), enabled ? 'success' : 'info');
    if (badge) {
      badge.className = enabled
        ? 'px-2 py-0.5 rounded text-[10px] font-bold bg-blue-500/20 text-blue-500 dark:text-blue-400 border border-blue-500/40'
        : 'px-2 py-0.5 rounded text-[10px] font-bold bg-slate-100 dark:bg-slate-700 text-slate-500 dark:text-slate-400 border border-slate-200 dark:border-slate-600';
      badge.textContent = enabled ? '● ĐANG BẬT' : '○ ĐANG TẮT';
    }
    if (btnSync) {
      btnSync.disabled = !enabled;
      btnSync.classList.toggle('opacity-50', !enabled);
      btnSync.classList.toggle('cursor-not-allowed', !enabled);
    }
  } catch (err) {
    showToast(`❌ Lỗi bật/tắt Active Directory: ${err.message}`, 'error');
    const toggle = document.getElementById('toggle-ad-sync');
    if (toggle) toggle.checked = !enabled;
  }
}

async function saveTelegramConfig() {
  const btn = document.getElementById('btn-save-telegram');
  const toggle = document.getElementById('toggle-tg-gateway');
  const token = (document.getElementById('cfg-tg-token')?.value || '').trim();
  const adminsRaw = (document.getElementById('cfg-tg-admins')?.value || '').trim();
  const group = (document.getElementById('cfg-tg-group')?.value || '').trim();
  const admins = adminsRaw ? adminsRaw.split(',').map(s => s.trim()).filter(Boolean) : [];

  if (btn) {
    btn.disabled = true;
    btn.innerHTML = `<svg class="animate-spin" width="14" height="14" fill="none" stroke="currentColor" stroke-width="2" viewBox="0 0 24 24"><path d="M21 12a9 9 0 1 1-6.219-8.56"/></svg> Đang lưu...`;
  }

  try {
    const payload = {
      bot_token: token,
      admin_chat_ids: admins,
      incident_group_id: group,
    };
    if (toggle) payload.enabled = toggle.checked;

    const res = await apiFetch(`${API_BASE}/api/v1/telegram/config`, {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
    });
    const data = await res.json();
    if (!res.ok) throw new Error(data.detail || `HTTP ${res.status}`);
    showToast(data.message || '✅ Đã lưu cấu hình Telegram thành công!', 'success');
    // Update badge immediately if user just typed a token
    if (token) {
      const tgTokenBadge = document.getElementById('cfg-tg-token-badge');
      const tgTokenHint = document.getElementById('cfg-tg-token-hint');
      const inputToken = document.getElementById('cfg-tg-token');
      if (tgTokenBadge) tgTokenBadge.classList.remove('hidden');
      if (inputToken) {
        inputToken.value = '';
        inputToken.placeholder = '••••••••••• — đã có token lưu sẵn, để trống nếu không đổi';
      }
      if (tgTokenHint) {
        tgTokenHint.textContent = '✔ Đã lưu Bot Token. F5 xong ô này vẫn trống — đó là bảo mật, token vẫn còn.';
        tgTokenHint.className = 'text-[10px] text-emerald-600 dark:text-emerald-400 mt-1';
      }
    } else {
      // Reload to confirm existing token state
      await loadTelegramConfig();
    }
    await loadTelegramStatus();
  } catch (err) {
    showToast(`❌ Lỗi lưu cấu hình Telegram: ${err.message}`, 'error');
  } finally {
    if (btn) {
      btn.disabled = false;
      btn.innerHTML = `<svg width="14" height="14" fill="none" stroke="currentColor" stroke-width="2.5" viewBox="0 0 24 24"><polyline points="20 6 9 17 4 12"/></svg> Lưu Cấu Hình Telegram`;
    }
  }
}

async function detectTelegramChatId() {
  const btn = document.getElementById('btn-detect-tg-chat');
  const token = (document.getElementById('cfg-tg-token')?.value || '').trim();
  const origHtml = btn ? btn.innerHTML : '';
  if (btn) {
    btn.disabled = true;
    btn.innerHTML = `<svg class="animate-spin" width="12" height="12" fill="none" stroke="currentColor" stroke-width="2" viewBox="0 0 24 24"><path d="M21 12a9 9 0 1 1-6.219-8.56"/></svg> Đang dò...`;
  }

  try {
    const res = await apiFetch(`${API_BASE}/api/v1/telegram/detect-chat`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ bot_token: token }),
    });
    const data = await res.json();
    if (!res.ok) throw new Error(data.detail || `HTTP ${res.status}`);

    if (data.chats && data.chats.length > 0) {
      const topChat = data.chats[0];
      const adminInput = document.getElementById('cfg-tg-admins');
      if (adminInput) {
        const currentVals = adminInput.value.split(',').map(s => s.trim()).filter(Boolean);
        if (!currentVals.includes(String(topChat.chat_id))) {
          currentVals.push(String(topChat.chat_id));
          adminInput.value = currentVals.join(', ');
        }
      }
      showToast(`🎯 Đã tìm thấy Chat ID: ${topChat.chat_id} (${topChat.name || topChat.username || 'Người dùng'}) và tự động điền!`, 'success', 7000);
    } else {
      showToast('ℹ️ Chưa có tin nhắn nào tới bot! Hãy mở Telegram, tìm bot và gửi tin nhắn hoặc bấm /start, sau đó bấm lại nút này.', 'info', 7000);
    }
  } catch (err) {
    showToast(`❌ Không thể dò Chat ID: ${err.message}`, 'error');
  } finally {
    if (btn) {
      btn.disabled = false;
      btn.innerHTML = origHtml;
    }
  }
}

async function testTelegramAlert() {
  const btn = document.getElementById('btn-test-telegram');
  const token = (document.getElementById('cfg-tg-token')?.value || '').trim();
  const adminsRaw = (document.getElementById('cfg-tg-admins')?.value || '').trim();
  const group = (document.getElementById('cfg-tg-group')?.value || '').trim();
  const admins = adminsRaw ? adminsRaw.split(',').map(s => s.trim()).filter(Boolean) : [];

  if (btn) {
    btn.disabled = true;
    btn.innerHTML = `<svg class="animate-spin" width="14" height="14" fill="none" stroke="currentColor" stroke-width="2" viewBox="0 0 24 24"><path d="M21 12a9 9 0 1 1-6.219-8.56"/></svg> Đang kiểm tra & gửi...`;
  }

  try {
    const res = await apiFetch(`${API_BASE}/api/v1/telegram/test-alert`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        bot_token: token,
        admin_chat_ids: admins,
        incident_group_id: group,
      }),
    });
    const data = await res.json();
    if (!res.ok) throw new Error(data.detail || `HTTP ${res.status}`);
    if (data.status === 'success') {
      showToast(data.message || '✅ Đã gửi tin nhắn kiểm thử thành công!', 'success', 6000);
      loadTelegramStatus();
    } else if (data.status === 'warning') {
      showToast(data.message || '⚠️ Cảnh báo gửi tin nhắn kiểm thử.', 'warning', 8000);
    } else {
      showToast(data.message || '❌ Gửi tin nhắn kiểm thử thất bại.', 'error', 8000);
    }
  } catch (err) {
    showToast(`❌ Lỗi gửi test alert: ${err.message}`, 'error');
  } finally {
    if (btn) {
      btn.disabled = false;
      btn.innerHTML = `<svg width="14" height="14" fill="none" stroke="currentColor" stroke-width="2" viewBox="0 0 24 24"><path d="M18 8A6 6 0 0 0 6 8c0 7-3 9-3 9h18s-3-2-3-9"/><path d="M13.73 21a2 2 0 0 1-3.46 0"/></svg> Gửi Test Alert`;
    }
  }
}

async function loadDomainStats() {
  try {
    const res = await apiFetch(`${API_BASE}/api/v1/domain/stats`);
    if (!res.ok) return;
    const data = await res.json();
    const empCount = data.employees_count || 0;
    const compCount = data.computers_count || 0;

    const elEmp = document.getElementById('stat-domain-employees');
    const elComp = document.getElementById('stat-domain-computers');
    const mEmp = document.getElementById('count-modal-employees');
    const mComp = document.getElementById('count-modal-computers');

    if (elEmp) elEmp.textContent = empCount;
    if (elComp) elComp.textContent = compCount;
    if (mEmp) mEmp.textContent = empCount;
    if (mComp) mComp.textContent = compCount;
  } catch (err) {
    console.debug('[Domain] Không thể tải thống kê AD:', err);
  }
}

async function syncDomainData() {
  const btn = document.getElementById('btn-sync-domain');
  if (btn) {
    btn.disabled = true;
    btn.innerHTML = `<svg class="animate-spin" width="15" height="15" fill="none" stroke="currentColor" stroke-width="2" viewBox="0 0 24 24"><path d="M21 12a9 9 0 1 1-6.219-8.56"/></svg> Đang đồng bộ AD qua PowerShell...`;
  }

  try {
    const res = await apiFetch(`${API_BASE}/api/v1/domain/sync`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
    });
    const data = await res.json();
    if (!res.ok) throw new Error(data.detail || `HTTP ${res.status}`);

    const totalUsers = data.total_users || 0;
    const totalComps = data.total_computers || 0;
    const userMsg = data.users?.message || '';
    const compMsg = data.computers?.message || '';

    if (data.status === 'warning') {
      showToast(`⚠️ Cảnh báo đồng bộ: ${userMsg || compMsg}`, 'info');
    } else {
      showToast(`✅ Đồng bộ hoàn tất: ${totalUsers} người dùng & ${totalComps} máy tính AD!`, 'success');
    }

    await loadDomainStats();
    if (!document.getElementById('domain-viewer-modal')?.classList.contains('hidden')) {
      await fetchDomainData();
    }
  } catch (err) {
    showToast(`❌ Lỗi đồng bộ AD: ${err.message}`, 'error');
  } finally {
    if (btn) {
      btn.disabled = false;
      btn.innerHTML = `<svg width="15" height="15" fill="none" stroke="currentColor" stroke-width="2" viewBox="0 0 24 24"><path d="M21.5 2v6h-6M21.34 15.57a10 10 0 1 1-.57-8.38l5.67-5.67"/></svg> Đồng Bộ AD Ngay`;
    }
  }
}

function openDomainViewerModal() {
  const modal = document.getElementById('domain-viewer-modal');
  if (!modal) return;
  modal.classList.remove('hidden');
  fetchDomainData();
}

function closeDomainViewerModal() {
  const modal = document.getElementById('domain-viewer-modal');
  if (!modal) return;
  modal.classList.add('hidden');
}

function switchAdSubTab(tab) {
  currentAdSubTab = tab;
  const btnUsers = document.getElementById('tab-ad-btn-users');
  const btnComps = document.getElementById('tab-ad-btn-comps');
  const tblUsers = document.getElementById('domain-table-employees');
  const tblComps = document.getElementById('domain-table-computers');

  if (tab === 'users') {
    btnUsers.className = 'px-3.5 py-1.5 rounded-lg font-bold bg-primary-600 text-white shadow-sm transition';
    btnComps.className = 'px-3.5 py-1.5 rounded-lg font-bold bg-slate-100 dark:bg-slate-800 text-slate-600 dark:text-slate-300 hover:bg-slate-200 dark:hover:bg-slate-700 transition';
    tblUsers.classList.remove('hidden');
    tblComps.classList.add('hidden');
  } else {
    btnComps.className = 'px-3.5 py-1.5 rounded-lg font-bold bg-primary-600 text-white shadow-sm transition';
    btnUsers.className = 'px-3.5 py-1.5 rounded-lg font-bold bg-slate-100 dark:bg-slate-800 text-slate-600 dark:text-slate-300 hover:bg-slate-200 dark:hover:bg-slate-700 transition';
    tblComps.classList.remove('hidden');
    tblUsers.classList.add('hidden');
  }
  filterDomainTable();
}

async function fetchDomainData() {
  const tbodyEmp = document.getElementById('domain-tbody-employees');
  const tbodyComp = document.getElementById('domain-tbody-computers');

  if (tbodyEmp) tbodyEmp.innerHTML = `<tr><td colspan="6" class="p-4 text-center text-slate-400">Đang đọc bản sao đồng bộ AD...</td></tr>`;
  if (tbodyComp) tbodyComp.innerHTML = `<tr><td colspan="6" class="p-4 text-center text-slate-400">Đang đọc bản sao đồng bộ AD...</td></tr>`;

  try {
    const [resEmp, resComp] = await Promise.all([
      apiFetch(`${API_BASE}/api/v1/domain/employees?limit=200`),
      apiFetch(`${API_BASE}/api/v1/domain/computers?limit=200`),
    ]);

    if (resEmp.ok) {
      const dataEmp = await resEmp.json();
      domainEmployeesList = dataEmp.data || [];
      const mEmp = document.getElementById('count-modal-employees');
      if (mEmp) mEmp.textContent = domainEmployeesList.length;
    }

    if (resComp.ok) {
      const dataComp = await resComp.json();
      domainComputersList = dataComp.data || [];
      const mComp = document.getElementById('count-modal-computers');
      if (mComp) mComp.textContent = domainComputersList.length;
      const covEl = document.getElementById('domain-agent-coverage');
      if (covEl) covEl.textContent = agentCoverageText(dataComp.agent_coverage);
    }

    renderDomainTables();
  } catch (err) {
    if (tbodyEmp) tbodyEmp.innerHTML = `<tr><td colspan="6" class="p-4 text-center text-rose-400">Lỗi tải dữ liệu: ${err.message}</td></tr>`;
    if (tbodyComp) tbodyComp.innerHTML = `<tr><td colspan="6" class="p-4 text-center text-rose-400">Lỗi tải dữ liệu: ${err.message}</td></tr>`;
  }
}

function renderDomainTables(empFilter = domainEmployeesList, compFilter = domainComputersList) {
  const tbodyEmp = document.getElementById('domain-tbody-employees');
  const tbodyComp = document.getElementById('domain-tbody-computers');

  if (tbodyEmp) {
    if (empFilter.length === 0) {
      tbodyEmp.innerHTML = `<tr><td colspan="6" class="p-6 text-center text-slate-400">Chưa có người dùng nào được đồng bộ từ Active Directory. Hãy nhấn nút "Đồng Bộ AD Ngay".</td></tr>`;
    } else {
      tbodyEmp.innerHTML = empFilter.map(e => `
        <tr class="hover:bg-slate-50 dark:hover:bg-white/5 transition-colors">
          <td class="p-2.5 font-mono text-cyan-600 dark:text-cyan-300 font-bold">${escapeHtml(e.sam_account_name || '--')}</td>
          <td class="p-2.5 font-semibold text-slate-800 dark:text-slate-100">${escapeHtml(e.full_name || '--')}</td>
          <td class="p-2.5 text-slate-600 dark:text-slate-300">${escapeHtml(e.department || '--')}</td>
          <td class="p-2.5 text-slate-600 dark:text-slate-300">${escapeHtml(e.title || '--')}</td>
          <td class="p-2.5 text-slate-500 font-mono">${escapeHtml(e.email || '--')}</td>
          <td class="p-2.5 text-slate-500">${escapeHtml(e.phone || '--')}</td>
        </tr>
      `).join('');
    }
  }

  if (tbodyComp) {
    if (compFilter.length === 0) {
      tbodyComp.innerHTML = `<tr><td colspan="6" class="p-6 text-center text-slate-400">Chưa có máy tính nào được đồng bộ từ Active Directory. Hãy nhấn nút "Đồng Bộ AD Ngay".</td></tr>`;
    } else {
      tbodyComp.innerHTML = compFilter.map(c => `
        <tr class="hover:bg-slate-50 dark:hover:bg-white/5 transition-colors">
          <td class="p-2.5 font-mono text-purple-600 dark:text-purple-300 font-bold">${escapeHtml(c.hostname || '--')}</td>
          <td class="p-2.5 text-slate-700 dark:text-slate-200">${escapeHtml(c.os_version || '--')}</td>
          <td class="p-2.5 font-mono text-emerald-600 dark:text-emerald-400">${escapeHtml(c.ip_address || '--')}</td>
          <td class="p-2.5 text-slate-600 dark:text-slate-300">${escapeHtml(c.assigned_to || '--')}</td>
          <td class="p-2.5">${agentBadgeHtml(c.agent)}</td>
          <td class="p-2.5 text-slate-400 text-[10px]">${escapeHtml(c.synced_at || '--')}</td>
        </tr>
      `).join('');
    }
  }
}

function filterDomainTable() {
  const query = (document.getElementById('ad-search-input')?.value || '').trim().toLowerCase();
  if (!query) {
    renderDomainTables();
    return;
  }

  const filteredEmps = domainEmployeesList.filter(e => {
    return (
      (e.full_name || '').toLowerCase().includes(query) ||
      (e.sam_account_name || '').toLowerCase().includes(query) ||
      (e.department || '').toLowerCase().includes(query) ||
      (e.title || '').toLowerCase().includes(query) ||
      (e.email || '').toLowerCase().includes(query) ||
      (e.phone || '').toLowerCase().includes(query)
    );
  });

  const filteredComps = domainComputersList.filter(c => {
    return (
      (c.hostname || '').toLowerCase().includes(query) ||
      (c.os_version || '').toLowerCase().includes(query) ||
      (c.ip_address || '').toLowerCase().includes(query) ||
      (c.assigned_to || '').toLowerCase().includes(query)
    );
  });

  renderDomainTables(filteredEmps, filteredComps);
}

// ═══════════════════════════════════════════════════════════════════════════
// ── KHỞI CHẠY (BOOTSTRAP) ──────────────────────────────────────────────────
// ═══════════════════════════════════════════════════════════════════════════

document.addEventListener('DOMContentLoaded', () => {
  // Sync theme icon with already-applied class (set by inline <head> script)
  updateThemeUI(document.documentElement.classList.contains('dark'));

  // Phase 73: bảng tài khoản mặc định (kèm mật khẩu) chỉ hiện trên máy local.
  // Trước đây nó nằm sẵn trong HTML nên lộ ra ở mọi môi trường.
  const devHint = document.getElementById('dev-accounts-hint');
  if (devHint) {
    const host = window.location.hostname;
    const isLocal = host === 'localhost' || host === '127.0.0.1' || host === '::1' || host.endsWith('.local');
    if (isLocal) devHint.classList.remove('hidden');
  }

  // Pre-activate saved tab visually so there's zero layout shift on F5
  const preTab = getSavedTab();
  if (preTab && preTab !== 'dashboard') {
    const preNav = document.getElementById(`nav-${preTab}`);
    const prePane = document.getElementById(`tab-${preTab}`);
    if (preNav && prePane) {
      document.querySelectorAll('.nav-btn').forEach(btn => btn.classList.remove('active'));
      preNav.classList.add('active');
      document.querySelectorAll('.tab-pane').forEach(pane => pane.classList.remove('active'));
      prePane.classList.add('active');
      const titleEl = document.getElementById('header-title');
      if (titleEl && TAB_TITLES[preTab]) titleEl.textContent = TAB_TITLES[preTab];
    }
  }

  // Handle browser back/forward navigation with hashchange
  window.addEventListener('hashchange', () => {
    const hash = (window.location.hash || '').replace('#', '').trim();
    if (hash && VALID_TABS.includes(hash)) {
      switchTab(hash);
    }
  });

  checkAuthAndInit();

  setInterval(() => {
    if (getAuthToken()) {
      loadDashboard();
      loadAudioNodes();
      const activeTab = document.querySelector('.nav-btn.active');
      if (activeTab && activeTab.id === 'nav-tasks') {
        loadKpiLogs();
      }
      if (activeTab && activeTab.id === 'nav-voice') {
        loadMicStatus();
      }
    }
  }, 15_000);

  // Setup enter key on input-new-blacklist
  const newBlInput = document.getElementById('input-new-blacklist');
  if (newBlInput) {
    newBlInput.addEventListener('keydown', (e) => {
      if (e.key === 'Enter') {
        e.preventDefault();
        addBlacklistKeyword();
      }
    });
  }
});


// ============================================================
// PHASE 21 — REAL-TIME LOG VIEWER (Cyberpunk High-Tech Terminal)
// ============================================================

const LogViewer = (() => {
  // --- State ---
  let _allLogs = [];          // Raw log store (max 2000 entries)
  let _currentFilter = 'ALL'; // Active level filter: ALL, DEBUG, INFO, WARNING, ERROR
  let _paused = false;        // Pause streaming to UI
  let _autoScroll = true;     // Cuộn xuống đáy khi có dòng log mới
  let _stats = { total: 0, info: 0, warn: 0, error: 0 };
  const MAX_LOG_ENTRIES = 2000;
  const MAX_DOM_ROWS = 600;   // Keep DOM lean and ultra fast

  // --- Level Badge HTML Generator ---
  function _getLevelBadge(level) {
    const lvl = (level || 'INFO').toUpperCase();
    if (lvl === 'DEBUG') {
      return '<span class="px-1.5 py-0.5 rounded text-[10px] font-bold bg-slate-800 text-slate-400 border border-slate-700/60 select-none">DEBUG</span>';
    } else if (lvl === 'INFO') {
      return '<span class="px-1.5 py-0.5 rounded text-[10px] font-bold bg-emerald-950/80 text-emerald-400 border border-emerald-800/60 select-none">INFO </span>';
    } else if (lvl === 'WARNING' || lvl === 'WARN') {
      return '<span class="px-1.5 py-0.5 rounded text-[10px] font-bold bg-amber-950/80 text-amber-300 border border-amber-800/60 select-none">WARN </span>';
    } else if (lvl === 'ERROR') {
      return '<span class="px-1.5 py-0.5 rounded text-[10px] font-bold bg-rose-950/80 text-rose-300 border border-rose-800/60 select-none">ERROR</span>';
    } else if (lvl === 'CRITICAL' || lvl === 'FATAL') {
      return '<span class="px-1.5 py-0.5 rounded text-[10px] font-bold bg-rose-600 text-white font-black animate-pulse select-none">FATAL</span>';
    }
    return `<span class="px-1.5 py-0.5 rounded text-[10px] font-bold bg-slate-800 text-slate-300 select-none">${lvl}</span>`;
  }

  function _esc(s) {
    return String(s || '').replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
  }

  function _highlight(text, query) {
    if (!query) return text;
    const escapedQuery = query.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
    return text.replace(new RegExp(`(${escapedQuery})`, 'gi'), '<mark class="bg-amber-400/90 text-black px-0.5 rounded font-semibold">$1</mark>');
  }

  // --- Render a single log row ---
  function _makeRow(entry) {
    const div = document.createElement('div');
    div.className = 'log-row group flex items-start gap-2 py-0.5 px-1.5 rounded hover:bg-slate-800/60 transition-colors font-mono cursor-pointer';
    div.dataset.level = entry.level;
    div.title = 'Bấm để sao chép dòng log này';

    const search = (document.getElementById('log-search-input')?.value || '').toLowerCase();

    // Timestamp
    let ts = '';
    if (entry.timestamp) {
      if (entry.timestamp.includes('T')) {
        ts = entry.timestamp.split('T')[1]?.split('.')[0] || '';
      } else {
        ts = entry.timestamp;
      }
    }
    if (!ts) {
      const now = new Date();
      ts = `${String(now.getHours()).padStart(2, '0')}:${String(now.getMinutes()).padStart(2, '0')}:${String(now.getSeconds()).padStart(2, '0')}`;
    }

    const badge = _getLevelBadge(entry.level);
    const loggerName = entry.logger ? `[${entry.logger}]` : '[sys]';
    const cleanMsg = _esc(entry.message || '');
    const highlightedMsg = search ? _highlight(cleanMsg, search) : cleanMsg;

    div.innerHTML = `
      <span class="text-slate-500 text-[11px] select-none shrink-0">${ts}</span>
      <div class="shrink-0">${badge}</div>
      <span class="text-indigo-400 text-[11px] font-medium shrink-0 max-w-[180px] truncate select-none" title="${_esc(entry.logger || '')}">${loggerName}</span>
      <span class="text-slate-200 text-[11px] whitespace-pre-wrap break-all select-text flex-1">${highlightedMsg}</span>
    `;

    // Click to copy single row
    div.onclick = (e) => {
      // Don't copy if user is selecting text
      const selection = window.getSelection().toString();
      if (selection.length > 0) return;

      const rowText = `${ts} [${entry.level}] ${entry.logger || ''} | ${entry.message || ''}`;
      if (navigator.clipboard && navigator.clipboard.writeText) {
        navigator.clipboard.writeText(rowText).then(() => {
          showToast('📋 Đã sao chép dòng log vào clipboard!', 'info');
        });
      }
    };

    return div;
  }

  function _matchesFilter(entry) {
    // 1. Level filter
    const f = _currentFilter;
    if (f !== 'ALL') {
      const eLevel = (entry.level || '').toUpperCase();
      if (f === 'WARNING' && eLevel !== 'WARNING' && eLevel !== 'WARN') return false;
      if (f !== 'WARNING' && eLevel !== f) return false;
    }

    // 2. Module filter
    const modFilter = (document.getElementById('log-module-filter')?.value || '').toLowerCase();
    if (modFilter) {
      const logName = (entry.logger || '').toLowerCase();
      if (!logName.includes(modFilter)) return false;
    }

    // 3. Search query
    const search = (document.getElementById('log-search-input')?.value || '').toLowerCase();
    if (search) {
      const combined = `${entry.message || ''} ${entry.logger || ''} ${entry.level || ''}`.toLowerCase();
      if (!combined.includes(search)) return false;
    }

    return true;
  }

  function _appendRow(entry) {
    const output = document.getElementById('log-output');
    if (!output) return;

    // Clear placeholder
    const placeholder = output.querySelector('.italic');
    if (placeholder) placeholder.remove();

    output.appendChild(_makeRow(entry));

    // Trim DOM
    while (output.children.length > MAX_DOM_ROWS) {
      output.removeChild(output.firstChild);
    }

    // Auto-scroll — `_autoScroll` là nguồn sự thật. Trước đây code đọc
    // `getElementById('log-autoscroll')` (một checkbox không tồn tại trong
    // index.html) nên luôn rơi vào nhánh `!autoScroll` => luôn cuộn, kể cả
    // khi người dùng đã bấm tắt. Nút "Auto-scroll" vì thế là nút chết.
    if (_autoScroll) {
      output.scrollTop = output.scrollHeight;
    }
  }

  function _updateStats() {
    const set = (id, val) => { const el = document.getElementById(id); if (el) el.textContent = val; };
    set('log-stat-total', _stats.total);
    set('log-stat-info', _stats.info);
    set('log-stat-warn', _stats.warn);
    set('log-stat-error', _stats.error);
  }

  function _recalcStats() {
    _stats = { total: _allLogs.length, info: 0, warn: 0, error: 0 };
    for (const e of _allLogs) {
      const lvl = (e.level || '').toUpperCase();
      if (lvl === 'INFO') _stats.info++;
      else if (lvl === 'WARNING' || lvl === 'WARN') _stats.warn++;
      else if (lvl === 'ERROR' || lvl === 'CRITICAL' || lvl === 'FATAL') _stats.error++;
    }
    _updateStats();

    const badge = document.getElementById('badge-log-errors');
    if (badge) {
      if (_stats.error > 0) {
        badge.textContent = _stats.error;
        badge.classList.remove('hidden');
      } else {
        badge.classList.add('hidden');
      }
    }
  }

  function _rebuildDisplay() {
    const output = document.getElementById('log-output');
    if (!output) return;
    output.innerHTML = '';

    const filtered = _allLogs.filter(e => _matchesFilter(e));
    const slice = filtered.slice(-MAX_DOM_ROWS);

    if (slice.length === 0) {
      output.innerHTML = '<div class="text-slate-500 italic p-3 text-center">Không có bản ghi log nào khớp với bộ lọc hiện tại.</div>';
      return;
    }

    const frag = document.createDocumentFragment();
    slice.forEach(e => frag.appendChild(_makeRow(e)));
    output.appendChild(frag);

    if (_autoScroll) {
      output.scrollTop = output.scrollHeight;
    }
  }

  // --- Public API ---
  return {
    push(entry) {
      // Update stats
      _stats.total++;
      const lvl = (entry.level || '').toUpperCase();
      if (lvl === 'INFO') _stats.info++;
      if (lvl === 'WARNING' || lvl === 'WARN') _stats.warn++;
      if (lvl === 'ERROR' || lvl === 'CRITICAL' || lvl === 'FATAL') {
        _stats.error++;
        const badge = document.getElementById('badge-log-errors');
        if (badge) {
          badge.textContent = _stats.error;
          badge.classList.remove('hidden');
        }
      }
      _updateStats();

      // Store in memory
      _allLogs.push(entry);
      if (_allLogs.length > MAX_LOG_ENTRIES) _allLogs.shift();

      // Render if not paused and matches filter
      if (!_paused && _matchesFilter(entry)) {
        _appendRow(entry);
      }
    },

    loadHistory(logs) {
      if (!Array.isArray(logs) || logs.length === 0) return;
      _allLogs = logs.slice(-MAX_LOG_ENTRIES);
      _recalcStats();
      _rebuildDisplay();
    },

    async fetchRecentLogs(manual = false) {
      try {
        const token = getAuthToken();
        const headers = token ? { 'Authorization': `Bearer ${token}` } : {};
        const res = await fetch('/api/v1/logs/recent?limit=300', { headers });
        if (!res.ok) throw new Error(`HTTP ${res.status}`);
        const data = await res.json();
        if (data.status === 'success' && Array.isArray(data.logs)) {
          this.loadHistory(data.logs);
          if (manual) {
            showToast(`🔄 Đã làm mới ${data.count || data.logs.length} dòng log máy chủ!`, 'success');
          }
        }
      } catch (err) {
        console.warn('[LogViewer] Lỗi tải lịch sử log:', err);
        if (manual) {
          showToast('❌ Không thể tải log gần đây từ server.', 'error');
        }
      }
    },

    setFilter(level) {
      _currentFilter = level;
      document.querySelectorAll('.log-filter-btn').forEach(btn => {
        const isActive = btn.id === `log-filter-${level.toLowerCase()}`;
        btn.classList.toggle('bg-primary-600', isActive);
        btn.classList.toggle('text-white', isActive);
      });
      _rebuildDisplay();
    },

    togglePause() {
      _paused = !_paused;
      const btn = document.getElementById('log-pause-btn');
      const termStatus = document.getElementById('log-terminal-status');

      if (btn) {
        if (_paused) {
          btn.innerHTML = `<svg width="12" height="12" fill="currentColor" viewBox="0 0 24 24"><polygon points="5 3 19 12 5 21 5 3"/></svg> Tiếp tục`;
          btn.className = 'flex items-center gap-1 px-2.5 py-1.5 rounded-lg text-xs font-semibold bg-emerald-100 dark:bg-emerald-900/30 text-emerald-700 dark:text-emerald-400 border border-emerald-200 dark:border-emerald-700 hover:bg-emerald-200 transition-colors';
          if (termStatus) {
            termStatus.textContent = '⏸ PAUSED';
            termStatus.className = 'text-[10px] font-mono px-2 py-0.5 rounded-full bg-amber-500/10 text-amber-400 border border-amber-500/20 ml-2';
          }
        } else {
          btn.innerHTML = `<svg width="12" height="12" fill="currentColor" viewBox="0 0 24 24"><rect x="6" y="4" width="4" height="16"/><rect x="14" y="4" width="4" height="16"/></svg> Tạm dừng`;
          btn.className = 'flex items-center gap-1 px-2.5 py-1.5 rounded-lg text-xs font-semibold bg-amber-100 dark:bg-amber-900/30 text-amber-700 dark:text-amber-400 border border-amber-200 dark:border-amber-700 hover:bg-amber-200 transition-colors';
          if (termStatus) {
            termStatus.textContent = '● LIVE';
            termStatus.className = 'text-[10px] font-mono px-2 py-0.5 rounded-full bg-emerald-500/10 text-emerald-400 border border-emerald-500/20 ml-2';
          }
          _rebuildDisplay();
        }
      }
    },

    toggleAutoScroll(btn) {
      _autoScroll = !_autoScroll;
      if (btn) {
        btn.className = _autoScroll
          ? 'flex items-center gap-1 px-2 py-0.5 rounded text-[10px] font-semibold border bg-emerald-500/10 border-emerald-500/30 text-emerald-400 hover:bg-emerald-500/20 transition'
          : 'flex items-center gap-1 px-2 py-0.5 rounded text-[10px] font-semibold border bg-slate-500/10 border-slate-500/30 text-slate-400 hover:bg-slate-500/20 transition';
        btn.lastChild.textContent = _autoScroll ? ' Auto-scroll ON' : ' Auto-scroll OFF';
      }
      if (_autoScroll) _rebuildDisplay();  // cuộn ngay xuống đáy
      showToast(_autoScroll ? '⏬ Bật tự động cuộn log.' : '⏹ Đã dừng tự động cuộn log.', 'info');
      return _autoScroll;
    },

    clear() {
      _allLogs = [];
      _stats = { total: 0, info: 0, warn: 0, error: 0 };
      _updateStats();
      const output = document.getElementById('log-output');
      if (output) output.innerHTML = '<div class="text-slate-500 italic p-3 text-center">🗑 Đã làm sạch màn hình log.</div>';
      const badge = document.getElementById('badge-log-errors');
      if (badge) { badge.textContent = '0'; badge.classList.add('hidden'); }
      showToast('🧹 Đã xóa sạch log hiển thị.', 'info');
    },

    copyVisibleLogs() {
      const filtered = _allLogs.filter(e => _matchesFilter(e));
      if (filtered.length === 0) {
        showToast('Không có bản ghi log nào để sao chép.', 'info');
        return;
      }
      const lines = filtered.map(e =>
        `${e.timestamp || ''} [${(e.level || 'INFO').padEnd(7)}] ${e.logger || 'sys'} | ${e.message || ''}`
      ).join('\n');

      if (navigator.clipboard && navigator.clipboard.writeText) {
        navigator.clipboard.writeText(lines).then(() => {
          showToast(`📋 Đã sao chép ${filtered.length} dòng log vào clipboard!`, 'success');
        }).catch(err => {
          console.error(err);
          showToast('❌ Không thể sao chép vào clipboard.', 'error');
        });
      }
    },

    exportLogs() {
      const lines = _allLogs.map(e =>
        `${e.timestamp || ''} [${(e.level || 'INFO').padEnd(7)}] ${e.logger || 'sys'} | ${e.message || ''}`
      ).join('\n');
      const blob = new Blob([lines], { type: 'text/plain;charset=utf-8' });
      const a = document.createElement('a');
      a.href = URL.createObjectURL(blob);
      a.download = `vn-mate-ai_${new Date().toISOString().slice(0, 19).replace(/:/g, '-')}.log`;
      a.click();
      showToast('📥 Đang tải tệp vn-mate-ai.log...', 'success');
    },

    filterDisplay() {
      _rebuildDisplay();
    },

    setWsStatus(connected) {
      const dot = document.getElementById('log-ws-dot');
      const text = document.getElementById('log-ws-status');
      const termStatus = document.getElementById('log-terminal-status');

      if (dot && text) {
        if (connected) {
          dot.className = 'inline-block w-2 h-2 rounded-full bg-emerald-400 animate-pulse';
          text.textContent = 'Đang kết nối — nhận log trực tiếp';
          text.className = 'text-emerald-500';
          if (termStatus && !_paused) {
            termStatus.textContent = '● LIVE';
            termStatus.className = 'text-[10px] font-mono px-2 py-0.5 rounded-full bg-emerald-500/10 text-emerald-400 border border-emerald-500/20 ml-2';
          }
        } else {
          dot.className = 'inline-block w-2 h-2 rounded-full bg-rose-500 animate-pulse';
          text.textContent = 'Mất kết nối — đang thử lại...';
          text.className = 'text-rose-500';
          if (termStatus) {
            termStatus.textContent = '✕ OFFLINE';
            termStatus.className = 'text-[10px] font-mono px-2 py-0.5 rounded-full bg-rose-500/10 text-rose-400 border border-rose-500/20 ml-2';
          }
        }
      }
    },
  };
})();

// --- Global helpers called from HTML onclick ---
function setLogFilter(level) { LogViewer.setFilter(level); }
function toggleLogPause() { LogViewer.togglePause(); }
function clearLogViewer() { LogViewer.clear(); }
function exportLogs() { LogViewer.exportLogs(); }
function filterLogDisplay() { LogViewer.filterDisplay(); }
function refreshRecentLogs() { LogViewer.fetchRecentLogs(true); }
function copyVisibleLogs() { LogViewer.copyVisibleLogs(); }

// --- Hook into existing portal WebSocket message handler ---
// Patch the existing onmessage to also route log_entry events to LogViewer
(function patchPortalWS() {
  const _origSetup = window._setupPortalWebSocket;

  // Override the WS message handler after DOM ready
  document.addEventListener('DOMContentLoaded', () => {
    setTimeout(() => {
      // Find the portal WS and patch its onmessage
      if (window._portalWS && window._portalWS.addEventListener) {
        window._portalWS.addEventListener('message', _handleLogWsMessage);
      }
      // Also patch via event re-dispatch
    }, 1000);
  });
})();

// Central log WS message handler — called both by patched WS and by existing handler
function _handleLogWsMessage(ev) {
  try {
    const data = JSON.parse(ev.data || ev);
    if (data.event === 'log_entry') {
      LogViewer.push(data);
    }
  } catch (_) { }
}

// Extend the existing portal WS handler to forward log events
// We hook into the established pattern in app.js
if (typeof window !== 'undefined') {
  window._logViewerHandler = _handleLogWsMessage;
}

// ---------------------------------------------------------------------------
// Phase 32: VN-MateAI In-Browser Cyberpunk Visual Overlay Engine
// ---------------------------------------------------------------------------
let _hudTimerInterval = null;
let _hudDuration = 15;
let _hudRemaining = 15;
let _hudIsPaused = false;

function closeSystemHUD() {
  if (_hudTimerInterval) {
    clearInterval(_hudTimerInterval);
    _hudTimerInterval = null;
  }
  const overlay = document.getElementById('system-hud-overlay');
  if (!overlay) return;
  overlay.classList.add('opacity-0', 'translate-y-4', 'scale-95');
  setTimeout(() => {
    overlay.classList.add('hidden');
    overlay.classList.remove('opacity-0', 'translate-y-4', 'scale-95');
  }, 250);
}

function renderInBrowserHUD(type = 'network_map', data = {}, customTitle = null, duration = 15) {
  const overlay = document.getElementById('system-hud-overlay');
  const titleEl = document.getElementById('hud-title');
  const badgeEl = document.getElementById('hud-badge');
  const bodyEl = document.getElementById('hud-body');
  const progressEl = document.getElementById('hud-progress-bar');
  if (!overlay || !bodyEl) return;

  const curName = currentConfig?.persona?.ai_name || currentConfig?.AI_NAME || 'Ly Ly';

  // Normalize aliases
  let visualType = (type || 'network_map').toLowerCase().trim();
  if (visualType === 'security_alert') visualType = 'alert';
  if (visualType === 'screenshot') visualType = 'image';
  if (visualType === 'text' || visualType === 'log' || visualType === 'table') visualType = 'text_board';

  const titleMap = {
    network_map: `${curName} — SƠ ĐỒ MẠNG LAN`,
    metric_chart: `${curName} — TẢI HỆ THỐNG`,
    alert: `${curName} — CẢNH BÁO AN NINH`,
    image: `${curName} — HÌNH ẢNH GIÁM SÁT`,
    text_board: `${curName} — BẢNG DỮ LIỆU / LOG VĂN BẢN`
  };

  if (titleEl) {
    titleEl.textContent = customTitle || titleMap[visualType] || `TRỢ LÝ AI ${curName.toUpperCase()} — HUD`;
  }
  if (badgeEl) {
    badgeEl.textContent = visualType.toUpperCase().replace('_', ' ');
    if (visualType === 'alert') {
      badgeEl.className = 'text-[9px] uppercase font-bold px-1.5 py-0.5 rounded bg-red-950 text-red-300 border border-red-800';
    } else if (visualType === 'text_board') {
      badgeEl.className = 'text-[9px] uppercase font-bold px-1.5 py-0.5 rounded bg-emerald-950 text-emerald-300 border border-emerald-800';
    } else {
      badgeEl.className = 'text-[9px] uppercase font-bold px-1.5 py-0.5 rounded bg-cyan-950 text-cyan-300 border border-cyan-800';
    }
  }

  // --- Render Body HTML based on Type ---
  if (visualType === 'network_map') {
    // Phase 73: KHÔNG bịa thiết bị. Trước đây khi server không trả `nodes`,
    // HUD tự vẽ 2 máy trạm với IP 192.168.1.105/.108 kèm trạng thái
    // online/standby, cộng thêm "ĐỘ TRỄ < 3ms" và badge "PINNED CERT" — toàn
    // bộ đều là thông tin không có nguồn. Nay chỉ hiện thiết bị thật, phần
    // còn lại báo rõ "chờ kết nối".
    const master = data.master || null;
    const nodes = Array.isArray(data.nodes) ? data.nodes : null;
    const hasNodes = !!(nodes && nodes.length);

    const masterHtml = master
      ? `
        <div class="flex items-center justify-between p-2 rounded-lg bg-cyan-950/60 border border-cyan-500/40">
          <div class="flex items-center gap-2">
            <span class="w-2 h-2 rounded-full bg-cyan-400"></span>
            <div>
              <div class="font-bold text-slate-200 text-[11px]">${_esc(master.label || 'Máy chủ chính')}</div>
              <div class="text-[10px] text-slate-400">${_live(master.ip, WAIT_TXT)} • MASTER</div>
            </div>
          </div>
        </div>`
      : `
        <div class="flex items-center justify-between p-2 rounded-lg bg-slate-900/80 border border-slate-800">
          <div class="flex items-center gap-2">
            <span class="w-2 h-2 rounded-full bg-amber-400"></span>
            <div>
              <div class="font-bold text-slate-300 text-[11px] is-waiting">Máy chủ chính</div>
              <div class="text-[10px] text-slate-400 is-waiting">${WAIT_TXT}</div>
            </div>
          </div>
        </div>`;

    const nodesList = hasNodes
      ? nodes.map(n => {
        const isMaster = n.role === 'master' || n.id === 'master';
        const status = (n.status || '').toString().toLowerCase();
        // Trạng thái lạ thì nói "chưa rõ", không tự coi là online.
        const isOnline = status === 'online';
        const isDown = status === 'standby' || status === 'offline' || status === 'disconnected';
        const stateLabel = isOnline ? 'ONLINE' : (isDown ? status.toUpperCase() : 'CHƯA RÕ');
        const dot = isOnline ? 'bg-emerald-400' : (isDown ? 'bg-amber-400' : 'bg-slate-500');
        const chip = isOnline
          ? 'bg-emerald-950 text-emerald-300 border-emerald-800'
          : (isDown ? 'bg-amber-950 text-amber-300 border-amber-800' : 'bg-slate-900 text-slate-400 border-slate-700');
        return `
          <div class="flex items-center justify-between p-2 rounded-lg ${isMaster ? 'bg-cyan-950/60 border border-cyan-500/40' : 'bg-slate-900/80 border border-slate-800'}">
            <div class="flex items-center gap-2">
              <span class="w-2 h-2 rounded-full ${dot}"></span>
              <div>
                <div class="font-bold text-slate-200 text-[11px]">${_esc(n.label || n.id || 'Thiết bị')}</div>
                <div class="text-[10px] text-slate-400">${_live(n.ip, WAIT_TXT)} ${isMaster ? '• MASTER' : '• WORKER'}</div>
              </div>
            </div>
            <span class="text-[10px] font-bold px-1.5 py-0.5 rounded ${chip}">${stateLabel}</span>
          </div>
        `;
      }).join('')
      : `<div class="p-2.5 rounded-lg bg-slate-900/60 border border-slate-800 text-center">
           <div class="text-[11px] text-slate-400 is-waiting">Chưa nhận được danh sách thiết bị — ${WAIT_TXT}</div>
         </div>`;

    const totalLabel = _isLive(data.total_clients)
      ? `${data.total_clients} máy trạm`
      : 'chưa có máy trạm nào kết nối';

    bodyEl.innerHTML = `
      <div class="space-y-2.5 font-mono text-xs">
        <div class="flex items-center justify-between bg-cyan-950/40 p-2.5 rounded-xl border border-cyan-800/40">
          <div class="flex items-center gap-2">
            <div class="w-2.5 h-2.5 rounded-full bg-cyan-400"></div>
            <div>
              <div class="font-bold text-cyan-300 tracking-wider">MẠNG LAN BẢO MẬT E2E</div>
              <div class="text-[10px] text-slate-400">${master
        ? `Master: ${_esc(master.ip || 'chưa rõ IP')}${_isLive(master.port) ? ` • Port ${master.port}` : ''}`
        : `<span class="is-waiting">Thông tin máy chủ: ${WAIT_TXT}</span>`}</div>
            </div>
          </div>
        </div>

        <div class="text-[10px] text-cyan-400/80 font-bold uppercase tracking-wider flex items-center justify-between">
          <span>DANH SÁCH THIẾT BỊ (${hasNodes ? nodes.length : 0})</span>
        </div>

        <div class="space-y-1.5 max-h-[170px] overflow-y-auto pr-1">
          ${masterHtml}
          ${nodesList}
        </div>

        <div class="pt-2 border-t border-cyan-900/40 flex items-center justify-between text-[10px] text-slate-400">
          <span>Kênh đồng bộ: WSS WebSocket Secure</span>
          <span class="${_isLive(data.total_clients) ? 'text-cyan-400' : 'is-waiting'}">Client: ${totalLabel}</span>
        </div>
      </div>
    `;
  } else if (visualType === 'metric_chart') {
    // Phase 73: KHÔNG bịa số đo. Trước đây thiếu dữ liệu thì hiện
    // CPU 24.5% / RAM 58.2% / 9.3GB / 16.0GB / Disk 45.0% / 148 tiến trình
    // (đều có 1 chữ số thập phân nên trông như số đo thật), và vẽ luôn
    // đường biểu đồ CPU giả [15, 28, 42, 30]. Nay mỗi ô hiện "chờ kết nối"
    // và KHÔNG vẽ đường khi thiếu lịch sử thật.
    const cpu = _liveNum(data.cpu_percent, { digits: 1, unit: '%' });
    const ramPct = _liveNum(data.ram_percent, { digits: 1, unit: '%' });
    const ramUsed = _liveNum(data.ram_used_gb, { digits: 1, unit: 'GB' });
    const ramTotal = _liveNum(data.ram_total_gb, { digits: 1, unit: 'GB' });
    const disk = _liveNum(data.disk_percent, { digits: 1, unit: '%' });
    const procs = _liveNum(data.processes_count);

    const cpuBar = _isLive(data.cpu_percent)
      ? `<div class="h-full bg-gradient-to-r from-cyan-500 via-sky-400 to-indigo-500 transition-all duration-500" style="width: ${Math.min(100, Number(data.cpu_percent))}%"></div>`
      : '';
    const ramBar = _isLive(data.ram_percent)
      ? `<div class="h-full bg-gradient-to-r from-sky-400 to-indigo-500 transition-all duration-500" style="width: ${Math.min(100, Number(data.ram_percent))}%"></div>`
      : '';
    const diskBar = _isLive(data.disk_percent)
      ? `<div class="h-full bg-gradient-to-r from-emerald-500 to-teal-400 transition-all duration-500" style="width: ${Math.min(100, Number(data.disk_percent))}%"></div>`
      : '';

    const history = (Array.isArray(data.history) && data.history.length) ? data.history : null;
    const chartHtml = history
      ? (() => {
        const maxVal = Math.max(...history, 100);
        const pts = history.map((val, idx) => {
          const x = (idx / (history.length - 1 || 1)) * 100;
          const y = 30 - ((val / maxVal) * 26);
          return `${x.toFixed(1)},${y.toFixed(1)}`;
        }).join(' ');
        return `
          <svg class="w-full h-9 overflow-visible" viewBox="0 0 100 30" preserveAspectRatio="none">
            <polyline fill="none" stroke="#06b6d4" stroke-width="2" points="${pts}" />
            <polyline fill="rgba(6, 182, 212, 0.15)" stroke="none" points="0,30 ${pts} 100,30" />
          </svg>`;
      })()
      : `<div class="h-9 flex items-center justify-center text-[10px] text-slate-500 is-waiting">Chưa có lịch sử tải — ${WAIT_TXT}</div>`;

    const chartNote = history
      ? `<span class="text-cyan-400">${history.length} mẫu</span>`
      : `<span class="is-waiting">${WAIT_TXT}</span>`;

    bodyEl.innerHTML = `
      <div class="space-y-3 font-mono text-xs">
        <div>
          <div class="flex justify-between text-[11px] mb-1">
            <span class="text-slate-300 font-bold ${_isLive(data.processes_count) ? '' : 'is-waiting'}">CPU LOAD${_isLive(data.processes_count) ? ` (${procs} tiến trình)` : ''}</span>
            <span class="font-bold ${_isLive(data.cpu_percent) ? 'text-cyan-400' : 'is-waiting'}">${cpu}</span>
          </div>
          <div class="w-full bg-slate-900 rounded-full h-2 overflow-hidden border border-cyan-950">
            ${cpuBar}
          </div>
        </div>

        <div>
          <div class="flex justify-between text-[11px] mb-1">
            <span class="text-slate-300 font-bold ${_isLive(data.ram_used_gb) ? '' : 'is-waiting'}">RAM MEMORY${_isLive(data.ram_used_gb) ? ` (${ramUsed} / ${ramTotal})` : ''}</span>
            <span class="font-bold ${_isLive(data.ram_percent) ? 'text-sky-400' : 'is-waiting'}">${ramPct}</span>
          </div>
          <div class="w-full bg-slate-900 rounded-full h-2 overflow-hidden border border-cyan-950">
            ${ramBar}
          </div>
        </div>

        <div>
          <div class="flex justify-between text-[11px] mb-1">
            <span class="text-slate-300 font-bold">DISK STORAGE</span>
            <span class="font-bold ${_isLive(data.disk_percent) ? 'text-emerald-400' : 'is-waiting'}">${disk}</span>
          </div>
          <div class="w-full bg-slate-900 rounded-full h-2 overflow-hidden border border-cyan-950">
            ${diskBar}
          </div>
        </div>

        <div class="p-2.5 bg-[#080e1a] rounded-xl border border-cyan-900/50">
          <div class="text-[10px] text-slate-400 mb-1.5 flex justify-between">
            <span class="font-bold text-cyan-300">BIỂU ĐỒ TẢI THỜI GIAN THỰC</span>
            ${chartNote}
          </div>
          ${chartHtml}
        </div>
      </div>
    `;
  } else if (visualType === 'alert' || visualType === 'warning') {
    const level = (data.level || 'WARNING').toUpperCase();
    const service = data.service || 'Zero-Trust Endpoint Security';
    const message = data.message || 'Phát hiện hoạt động bất thường hoặc kích hoạt kiểm tra an ninh.';
    const action = data.action || 'Hệ thống đã kích hoạt cơ chế cách ly và ghi nhật ký kiểm toán an toàn.';

    bodyEl.innerHTML = `
      <div class="space-y-2.5 font-mono text-xs">
        <div class="flex items-center gap-3 p-3 bg-red-950/40 rounded-xl border border-red-500/60 shadow-lg shadow-red-500/20">
          <div class="text-2xl animate-bounce">⚠️</div>
          <div>
            <div class="text-red-400 font-bold text-xs tracking-wider">${level} ALERT DETECTED</div>
            <div class="text-slate-300 text-[10px]">${service}</div>
          </div>
        </div>

        <div class="p-2.5 bg-slate-900/80 rounded-xl border border-slate-800 text-slate-300 text-[11px] leading-relaxed">
          <div class="text-red-300 font-bold mb-1">NỘI DUNG SỰ CỐ:</div>
          ${message}
        </div>

        <div class="p-2.5 bg-cyan-950/30 rounded-xl border border-cyan-800/40 text-cyan-300 text-[11px] leading-relaxed">
          <div class="text-cyan-400 font-bold mb-1">HÀNH ĐỘNG ĐÃ THI HÀNH:</div>
          ${action}
        </div>

        <button onclick="closeSystemHUD()" class="w-full py-2 rounded-lg bg-red-500/20 hover:bg-red-500/30 border border-red-500/60 text-red-300 font-bold transition text-xs tracking-wider">
          ✕ XÁC NHẬN VÀ ĐÓNG CẢNH BÁO
        </button>
      </div>
    `;
  } else if (visualType === 'image') {
    const imgSrc = data.image_base64 ? `data:image/png;base64,${data.image_base64}` : (data.image_path || '/static/favicon.ico');
    const caption = data.caption || `Ảnh chụp màn hình từ giám sát ${curName}`;

    bodyEl.innerHTML = `
      <div class="space-y-2 text-center font-mono text-xs">
        <div class="rounded-xl overflow-hidden border border-cyan-500/40 relative">
          <img src="${imgSrc}" class="w-full max-h-[220px] object-cover" alt="HUD Visual">
          <div class="absolute inset-0 bg-cyan-500/5 pointer-events-none"></div>
        </div>
        <div class="text-slate-400 text-[11px]">${caption}</div>
      </div>
    `;
  } else if (visualType === 'text_board') {
    const rawText = typeof data === 'string' ? data : (data.text || data.content || data.data || JSON.stringify(data, null, 2));
    const linesCount = (rawText.trim().match(/\n/g) || []).length + 1;

    bodyEl.innerHTML = `
      <div class="space-y-2 font-mono text-xs">
        <div class="flex justify-between items-center text-[10px] text-emerald-400 font-bold px-1">
          <span class="flex items-center gap-1.5"><span class="w-2 h-2 rounded-full bg-emerald-400 animate-pulse"></span>TERMINAL TEXT BUFFER (${linesCount} DÒNG)</span>
          <span class="text-slate-400 text-[9px]">AUTO-SCROLL: ON</span>
        </div>
        <pre id="hud-text-board-pre" class="p-3 bg-[#050811] text-[#00FF66] rounded-xl border border-emerald-500/40 text-[11px] leading-relaxed overflow-y-auto max-h-[220px] whitespace-pre-wrap break-all shadow-inner font-mono select-text"></pre>
      </div>
    `;
    const pre = document.getElementById('hud-text-board-pre');
    if (pre) {
      pre.textContent = rawText.trim() || '(Không có dữ liệu văn bản)';
      pre.scrollTop = pre.scrollHeight;
    }
  }

  // --- Display Overlay with Smooth Animation ---
  overlay.classList.remove('hidden');
  overlay.classList.remove('opacity-0', 'translate-y-4', 'scale-95');

  // --- Setup 15-second Auto-dismiss Timer with hover pause ---
  if (_hudTimerInterval) {
    clearInterval(_hudTimerInterval);
    _hudTimerInterval = null;
  }
  _hudDuration = Math.max(5, Number(duration || 15));
  _hudRemaining = _hudDuration;
  _hudIsPaused = false;

  overlay.onmouseenter = () => { _hudIsPaused = true; };
  overlay.onmouseleave = () => { _hudIsPaused = false; };

  if (progressEl) {
    progressEl.style.width = '100%';
  }

  _hudTimerInterval = setInterval(() => {
    if (!_hudIsPaused) {
      _hudRemaining -= 0.1;
      if (progressEl) {
        const pct = Math.max(0, (_hudRemaining / _hudDuration) * 100);
        progressEl.style.width = pct + '%';
      }
      if (_hudRemaining <= 0) {
        closeSystemHUD();
      }
    }
  }, 100);
}

async function triggerVisualOverlay(type = 'network_map') {
  const curName = currentConfig?.persona?.ai_name || currentConfig?.AI_NAME || 'Ly Ly';
  const titleMap = {
    network_map: `${curName} — SƠ ĐỒ MẠNG LAN`,
    metric_chart: `${curName} — TẢI HỆ THỐNG`,
    alert: `${curName} — CẢNH BÁO AN NINH`,
    image: `${curName} — HÌNH ẢNH GIÁM SÁT`
  };
  const title = titleMap[type] || `TRỢ LÝ AI ${curName.toUpperCase()} — HUD`;

  // 1. Immediately render in-browser HUD for instant visual feedback!
  renderInBrowserHUD(type, {}, title, 15);

  if (typeof showToast === 'function') {
    showToast(`⚡ Kích hoạt giao diện HUD [${curName} - ${type}]`, 'info');
  }

  // 2. Concurrently call backend API to broadcast over WebSocket and fetch real-time server telemetry
  try {
    const token = (typeof getAuthToken === 'function' ? getAuthToken() : '') || localStorage.getItem('token') || '';
    const resp = await fetch('/api/v1/visual/broadcast', {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        'Authorization': `Bearer ${token}`
      },
      body: JSON.stringify({
        type: type,
        title: title,
        duration: 15
      })
    });

    if (resp.ok) {
      const data = await resp.json();
      if (data && data.data) {
        // Re-render HUD with live server data
        renderInBrowserHUD(type, data.data, title, 15);
      }
    }
  } catch (err) {
    console.warn('[HUD] Backend broadcast notice:', err);
  }
}

if (typeof window !== 'undefined') {
  window.closeSystemHUD = closeSystemHUD;
  window.renderInBrowserHUD = renderInBrowserHUD;
  window.triggerVisualOverlay = triggerVisualOverlay;
}



// ═══════════════════════════════════════════════════════════════════════════
// PHASE 58 BƯỚC 1 — TRUNG TÂM CHỈ HUY (COMMAND CENTER)
// ═══════════════════════════════════════════════════════════════════════════
//
// Ba cột, mỗi cột trả lời đúng một câu hỏi của C.E.O:
//   1. Trái  — "Có gì tôi phải quyết?"        → hàng đợi HITL
//   2. Giữa  — "Tiền đang thế nào?"           → biểu đồ + sức khoẻ quỹ
//   3. Phải  — "Có rủi ro gì, quy chế ra sao?" → cảnh báo + tra cứu quy chế
//
// Nguyên tắc trong suốt module này: **không hiển thị gì mà không có bằng
// chứng**. Mọi nơi dữ liệu có thể thiếu đều hiện rõ "chưa có" thay vì để
// trống hoặc — tệ hơn — điền số 0 và bắt người xem tin là có số liệu.

const CommandCenter = (() => {
  let _chart = null;          // instance Chart.js hiện tại
  let _pendingChart = null;   // cấu hình biểu đồ chờ, chờ tab hiện ra mới vẽ
  let _pollTimer = null;      // timer nạp lại hàng đợi duyệt
  let _slowPoll = false;      // xen kẽ: nhịp chậm của loadOpsHealth (Phase 71)
  const POLL_MS = 5000;

  // ── Tiện ích ────────────────────────────────────────────────────────────
  const $ = (id) => document.getElementById(id);

  function _fmtVND(n) {
    const v = Number(n);
    if (!isFinite(v)) return '—';
    if (Math.abs(v) >= 1e9) return (v / 1e9).toFixed(2) + ' tỷ';
    if (Math.abs(v) >= 1e6) return (v / 1e6).toFixed(1) + ' tr';
    return v.toLocaleString('vi-VN') + ' đ';
  }

  // `_esc` cố ý KHÔNG khai báo ở đây: dùng bản ở cấp module để CommandCenter
  // và khối Phase 59/60 cùng escape theo một quy tắc — tránh tình trạng mỗi
  // IIFE giữ một bản riêng rồi lệch nhau.

  // ── Cột 1: hàng đợi duyệt HITL ──────────────────────────────────────────
  //
  // Phase 78: bản đầy đủ của hàng đợi chuyển sang Bảng Điều Khiển (chủ sở
  // hữu) nên danh sách ghi vào `dash-pending-list`.
  // Phase 79: tab Trung Tâm Chỉ Huy cũng gộp vào Bảng Điều Khiển, nên thẻ ô
  // đếm `cc-pending-count` (chỉ để "giữ con số cho màn hình C.E.O") bị gỡ —
  // trên cùng một trang thì ô đếm cạnh danh sách chỉ là bản sao. Con số giờ
  // hiện ở ô KPI `cc-kpi-pending` ngay dải chỉ số trên cùng.
  //
  // Sửa `cc-pending-count` mà quên `syncCommandCenterKpi` thì ô KPI sẽ rơi
  // về nhánh dự phòng `|| '0'` và hiện 0 bất kể thực tế — đúng loại số 0 bịa
  // mà cả dự án cấm. Vì vậy `loadPending` ghi thẳng vào ô KPI.
  async function loadPending() {
    const box = $('dash-pending-list');
    const kpiEl = $('cc-kpi-pending');
    const navBadge = $('badge-cc-pending');
    if (!box && !kpiEl && !navBadge) return;
    try {
      const res = await apiFetch(`${API_BASE}/api/v1/enterprise/hitl/pending`);
      const data = await res.json();
      const list = (data && data.pending_approvals) || [];

      // Ô KPI trên dải chỉ số + badge trên thanh điều hướng (nếu có).
      [kpiEl, navBadge].forEach((b) => {
        if (!b) return;
        b.textContent = String(list.length);
        b.classList.toggle('hidden', list.length === 0);
      });

      if (!box) return;
      if (!list.length) {
        box.innerHTML = '<p class="text-xs text-slate-400 dark:text-slate-500 text-center py-6">'
          + 'Không có tác vụ nào chờ duyệt.</p>';
        return;
      }

      box.innerHTML = list.map((it) => {
        const lv = Number(it.risk_level ?? 0);
        // Chỉ hiện nút Duyệt cho tài khoản có quyền, vì server chỉ cho admin
        // duyệt. Hiện nút cho người không có quyền rồi bấm mới nhận 403 là
        // trải nghiệm tệ hơn nhiều so với ẩn luôn.
        const canApprove = _currentUserRole() === 'admin';
        const params = Object.entries(it.params || {})
          .map(([k, v]) => `<span class="inline-block mr-2 mb-1 px-1.5 py-0.5 rounded bg-slate-100 dark:bg-slate-700/60 font-mono text-[10px]">${_esc(k)}=${_esc(JSON.stringify(v).slice(0, 30))}</span>`)
          .join('') || '<span class="text-slate-400 text-[10px]">không có tham số</span>';

        return `
        <div class="rounded-xl border border-amber-500/30 bg-amber-500/5 dark:bg-amber-500/10 p-2.5">
          <div class="flex items-start justify-between gap-2">
            <code class="text-[10px] font-mono font-bold text-amber-700 dark:text-amber-400">${_esc(it.id)}</code>
            <span class="shrink-0 px-1.5 py-0.5 rounded text-[9px] font-bold bg-rose-500/15 text-rose-600 dark:text-rose-400">L${lv}/5</span>
          </div>
          <p class="text-[11px] font-semibold text-slate-800 dark:text-slate-200 mt-1">${_esc(it.description || it.action_name)}</p>
          <p class="text-[10px] text-slate-500 dark:text-slate-400 font-mono mt-0.5">${_esc(it.action_name)} · ${_esc(it.requested_by || 'AI')}</p>
          <div class="mt-1.5">${params}</div>
          ${canApprove
            ? `<div class="flex gap-1.5 mt-2">
                 <button type="button" onclick="CommandCenter.decide('${_esc(it.id)}', true)"
                   class="flex-1 px-2 py-1.5 text-[11px] font-bold rounded-lg bg-emerald-600 hover:bg-emerald-700 text-white transition">Duyệt</button>
                 <button type="button" onclick="CommandCenter.decide('${_esc(it.id)}', false)"
                   class="flex-1 px-2 py-1.5 text-[11px] font-bold rounded-lg bg-rose-600 hover:bg-rose-700 text-white transition">Từ chối</button>
               </div>`
            : `<p class="text-[9px] text-slate-400 dark:text-slate-500 mt-2">Cần quyền Admin để quyết định.</p>`}
        </div>`;
      }).join('');
    } catch (err) {
      box.innerHTML = `<p class="text-xs text-rose-500 text-center py-6">Không tải được danh sách: ${_esc(err.message || err)}</p>`;
    }
  }

  function _currentUserRole() {
    try {
      const raw = localStorage.getItem('vnmateai_user');
      if (raw) return (JSON.parse(raw).role) || null;
    } catch { /* bỏ qua */ }
    // Dự phòng: đọc từ badge header nếu có.
    const el = $('header-user-role');
    return el ? el.textContent.trim().toLowerCase() : null;
  }

  async function decide(approvalId, approve) {
    const path = approve ? 'approve' : 'reject';
    const body = approve ? { approval_id: approvalId } : { approval_id: approvalId, reason: 'Từ chối tại Trung Tâm Chỉ Huy' };
    try {
      const res = await apiFetch(`${API_BASE}/api/v1/enterprise/hitl/${path}`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', 'Authorization': `Bearer ${getAuthToken()}` },
        body: JSON.stringify(body),
      });
      const data = await res.json();

      // Báo cáo phải phản ánh đúng trạng thái. Server phân biệt 3 ca:
      //   executed=true                 → duyệt + thực thi xong
      //   status=approved_not_executed  → duyệt xong nhưng KHÔNG thực thi được
      //   status=error                  → hỏng
      // Gộp cả 3 thành "thành công" là báo cáo thành công giả.
      if (!res.ok || (data && data.status === 'error')) {
        showToast(`❌ ${(data && data.message) || 'Không thực hiện được'}`, 'error');
      } else if (approve) {
        if (data.executed) {
          showToast('✅ Đã duyệt và thực thi thành công', 'success');
        } else if (data.status === 'approved_not_executed') {
          showToast(
            `⚠️ Đã ghi nhận duyệt nhưng CHƯA thực thi được: ${data.execution_error || 'không rõ nguyên nhân'}. `
            + 'Tác vụ cần chạy lại thủ công.', 'warning');
        } else {
          showToast('✅ Đã ghi nhận phê duyệt', 'success');
        }
      } else {
        showToast('⛔ Đã từ chối', 'info');
      }
      await loadPending();
    } catch (err) {
      showToast(`❌ Lỗi mạng: ${err.message || err}`, 'error');
    }
  }

  // ── Cột 2: biểu đồ ──────────────────────────────────────────────────────
  //
  // Vì sao phải hoãn vẽ thay vì vẽ ngay (lỗi thật đã gặp):
  //
  //   Biểu đồ tới từ WebSocket, tức là có thể xuất hiện **khi C.E.O đang ở
  //   tab khác**. Lúc đó pane Command Center đang `display: none`, canvas có
  //   kích thước 0×0. Chart.js vẽ xong thì hẹn giờ các khung hình hoạt ảnh
  //   (requestAnimationFrame) để nới chiều cao cột từ 0 lên giá trị thật —
  //   nhưng trình duyệt **dừng** requestAnimationFrame với phần tử không hiển
  //   thị. Kết quả: khi C.E.O mở sang tab, biểu đồ đã hiện nhưng mọi cột đều
  //   bẹt, trông giống hệt "không có dữ liệu".
  //
  //   Cách xử lý: nhận cấu hình, nhưng chỉ vẽ khi pane thật sự hiện. Nếu chưa,
  //   cất lại và vẽ bù lúc người dùng mở tab.
  function renderChart(chartConfig, meta) {
    if (!_paneVisible()) {
      _pendingChart = { config: chartConfig, meta: meta };
      console.info('[CommandCenter] Biểu đồ mới được giữ lại, sẽ vẽ khi mở Bảng Điều Khiển.');
      return;
    }
    _drawChart(chartConfig, meta);
  }

  function _paneVisible() {
    // Phase 79: nội dung C.E.O gộp vào Bảng Điều Khiển, nên phải hỏi
    // `tab-dashboard`. Hỏi `tab-command-center` (không còn tồn tại) thì
    // `!pane` luôn đúng → coi như luôn hiện → vẽ biểu đồ vào canvas đang bị
    // ẩn, tức vẽ xong rồi mất, không ai thấy.
    const pane = $('tab-dashboard');
    // Không tìm thấy pane (bản HTML cũ chưa có tab này) thì coi như hiện —
    // để biểu đồ vẫn được thử vẽ thay vì im lặng bỏ qua.
    return !pane || pane.classList.contains('active');
  }

  function _drawChart(chartConfig, meta) {
    const canvas = $('cc-chart-canvas');
    const empty = $('cc-chart-empty');
    if (!canvas) return;

    if (!chartConfig || !chartConfig.data) {
      if (empty) empty.classList.remove('hidden');
      return;
    }

    // Chart.js có thể chưa tải xong (CDN bị chặn trong mạng nội bộ). Nói rõ ra
    // thay vì im lặng để người dùng tưởng hệ thống treo.
    if (typeof window.Chart === 'undefined') {
      if (empty) {
        empty.classList.remove('hidden');
        empty.innerHTML = '<p class="text-xs text-amber-500">Không tải được thư viện Chart.js — '
          + 'biểu đồ không hiển thị được. Dữ liệu vẫn trả về qua API.</p>';
      }
      return;
    }

    if (empty) empty.classList.add('hidden');

    const cfg = JSON.parse(JSON.stringify(chartConfig));
    cfg.options = cfg.options || {};
    // Hoạt ảnh ngắn thôi: 350ms đủ để có cảm giác sống mà không khiến người
    // dùng phải chờ mỗi lần giọng nói đẩy biểu đồ mới.
    cfg.options.animation = { duration: 350 };

    try {
      if (_chart) { _chart.destroy(); _chart = null; }
      _chart = new window.Chart(canvas.getContext('2d'), cfg);
      _scheduleStuckCheck(_chart);
    } catch (err) {
      console.error('[CommandCenter] Lỗi vẽ biểu đồ:', err);
      if (empty) {
        empty.classList.remove('hidden');
        empty.innerHTML = `<p class="text-xs text-rose-500">Dữ liệu có nhưng không vẽ được biểu đồ: ${_esc(err.message || err)}</p>`;
      }
    }

    if (meta && meta.title) {
      const t = $('cc-chart-title');
      if (t) t.textContent = meta.title;
    }
    if (meta && meta.sqlSource) {
      const badge = $('cc-chart-source');
      if (badge) {
        // Nhãn dự phòng phải nổi bật: đó là tín hiệu "biểu đồ này KHÔNG chắc
        // là trả lời đúng câu hỏi của bạn".
        const fallback = meta.sqlSource !== 'llm';
        badge.textContent = fallback ? '⚠ CHẾ ĐỘ DỰ PHÒNG' : 'AI';
        badge.className = fallback
          ? 'shrink-0 px-2 py-0.5 rounded-md text-[10px] font-bold bg-amber-100 dark:bg-amber-900/40 text-amber-700 dark:text-amber-400'
          : 'shrink-0 px-2 py-0.5 rounded-md text-[10px] font-bold bg-cyan-100 dark:bg-cyan-900/40 text-cyan-700 dark:text-cyan-400';
      }
      if (meta.llmError) {
        const note = $('cc-ask-result');
        if (note) {
          note.innerHTML = `<div class="p-2 rounded-lg bg-amber-50 dark:bg-amber-900/20 border border-amber-500/30 text-[10px] text-amber-700 dark:text-amber-400">`
            + `<strong>⚠ Biểu đồ dùng câu SQL dự phòng theo từ khoá, không phải do AI hiểu câu hỏi.</strong><br>`
            + `<span class="opacity-80">${_esc(String(meta.llmError).slice(0, 200))}</span></div>`;
        }
      }
    }
  }

  // Ép Chart.js dựng lại hình học theo kích thước thật.
  //
  // Gọi khi: mở tab, đổi kích thước cửa sổ, hoặc sau khi vẽ. `update('none')`
  // nhảy thẳng tới trạng thái cuối (bỏ hoạt ảnh) — cần thiết vì nếu hoạt ảnh bị
  // treo ở giữa chừng thì cột đứng yên ở chiều cao 0.
  function syncChartLayout() {
    if (!_chart) return;
    try {
      _chart.resize();
      _chart.update('none');
    } catch (err) {
      console.warn('[CommandCenter] Không đồng bộ được bố cục biểu đồ:', err);
    }
  }

  // Tự chữa biểu đồ bị kẹt do trình duyệt dừng requestAnimationFrame.
  //
  // Khi tab bị đưa xuống nền, trình duyệt dừng gọi requestAnimationFrame cho
  // trang đó. Chart.js dựng cột bằng cách hẹn giờ nới chiều cao qua rAF, nên
  // nếu có biểu đồ tới đúng lúc tab bị ẩn, nó sẽ đứng yên ở chiều cao 0 vĩnh
  // viễn — trông y hệt "không có dữ liệu" dù dữ liệu có đầy đủ.
  //
  // Không thể phát hiện thẳng "rAF có chạy không" một cách đáng tin, nên kiểm
  // tra **kết quả**: nếu thang dữ liệu có giá trị khác 0 mà mọi phần tử vẫn
  // nằm sát đường cơ sở, thì hoạt ảnh chắc chắn đã bị treo → vẽ lại không
  // hoạt ảnh. Bình thường hàm này không làm gì cả, nên không mất hoạt ảnh đẹp.
  function _scheduleStuckCheck(chart) {
    setTimeout(() => {
      if (chart !== _chart) return;              // đã bị thay thế
      try {
        const yScale = chart.scales && chart.scales.y;
        if (!yScale) return;
        const coGiaTri = Math.abs(yScale.max) > 0 || Math.abs(yScale.min) > 0;
        if (!coGiaTri) return;                   // dữ liệu rỗng, không phải lỗi

        const elems = (chart.getDatasetMeta(0) || {}).data || [];
        if (!elems.length) return;
        const coCao = elems.some((e) => Math.abs(e.base - e.y) > 0.5);
        if (!coCao) {
          console.warn('[CommandCenter] Biểu đồ bị kẹt ở chiều cao 0 (requestAnimationFrame bị dừng) — vẽ lại.');
          chart.update('none');
        }
      } catch (err) {
        console.debug('[CommandCenter] Bỏ qua kiểm tra biểu đồ bị kẹt:', err);
      }
    }, 700);
  }

  // ── Phase 71: sức khoẻ vận hành ───────────────────────────────────────
  //
  // Tab này đổi vai từ "báo cáo dòng tiền cho C.E.O" sang "giám sát và vận
  // hành bằng AI". Số liệu lấy thẳng từ các endpoint đang chạy sẵn thay vì
  // dựng thêm một lớp tổng hợp: server đã có đủ, thêm lớp tổng hợp chỉ
  // thêm một chỗ có thể hỏng và thêm một vòng độ trễ.
  //
  // Nguyên tắc xuyên suốt: ô nào không tải được thì nói rõ KHÔNG TẢI ĐƯỢC.
  // Để trắng im lặng khiến "chưa cấu hình kết nối" và "server đã chết" trông
  // giống hệt nhau, mà hai kết luận đó thì đối ngược nhau.

  // Nhật ký server lẫn nhiễu nặng. Đo thật: 120 dòng gần nhất thì 69 dòng
  // (57%) là heartbeat `GET /v1/models` lặp mỗi 30 giây của router. Đổ nguyên
  // si vào khung "Nhật Ký Vận Hành" thì người dùng không bao giờ thấy dòng
  // nào quan trọng. Mặc định ẩn; có ô tích để mở lại khi cần soi nhiều.
  const OPS_LOG_NOISE = [
    /httpx/i,
    /GET\s+\/v1\/models/i,
    /heartbeat/i,
  ];

  function _isOpsNoise(entry) {
    const hay = `${entry?.logger || ''} ${entry?.message || ''}`;
    return OPS_LOG_NOISE.some((re) => re.test(hay));
  }

  // ═══ HỆ THỐNG CON ĐIỀU HÀNH ĐƯỢC ══════════════════════════════════
  //
  // Mỗi mục là một thứ admin bật/tắt được từ tab này. Ghi bảng khai báo ở
  // đây thay vì ghi thẻ vào HTML: thêm một hệ thống điều hành được là thêm
  // một dòng ở đây, không phải sửa giao diện.
  //
  //   path        — endpoint GET lấy trạng thái (khoá của dòng này)
  //   togglePath  — endpoint POST bật/tắt. Không có = không điều khiển
  //                 được, hiển thị lý do thay vì nút bấm không có tác dụng.
  //   on          — đọc cờ bật/tắt ra từ phản hồi
  //   reason      — lấy câu giải thích khi không chạy (nếu phản hồi có)
  //   body        — thân gửi khi bật/tắt (POST toggle cần `enabled`)
  //   next        — trạng thái sau khi bấm, để đoán trước trạng thái mới
  //                 và vẽ nút ngay (nếu không có thì đọc lại từ server)
  // Giữ lại các trạng thái đã tải, để vẽ lại một dòng không làm mất các
  // dòng còn lại. Chỉ có ý nghĩa trong phiên hiện tại — tải lại trang là
  // `loadOpsHealth()` gọi lại hết.
  let _opsSubs = {};

  const OPS_SUBSYSTEMS = [
    {
      key: 'worker',
      label: 'Worker Node cục bộ',
      hint: 'Máy con chạy skill thay máy chủ, dùng cho tác vụ dài.',
      path: 'orchestrator/local-worker/status',
      togglePath: 'orchestrator/local-worker/toggle',
      on: (d) => !!d.active,
      onText: (d) => (d.pid ? `đang chạy (PID ${d.pid})` : 'đang chạy'),
      offText: () => 'đang dừng',
      next: (d) => !d.active,
    },
    {
      key: 'ad',
      label: 'Đồng bộ Active Directory',
      hint: 'Kéo danh sách nhân viên và máy tính từ máy chủ domain về.',
      path: 'domain/config',
      togglePath: 'domain/toggle',
      on: (d) => !!d.enabled,
      onText: () => 'đang bật',
      offText: () => 'đang tắt',
      body: (d) => ({ enabled: !d.enabled }),
      next: (d) => !d.enabled,
    },
    {
      key: 'telegram',
      label: 'Telegram Gateway',
      hint: 'Cầu nối nhận tin nhắn Telegram.',
      path: 'telegram/status',
      // Không có toggle: phải cấu hình Bot Token trước, bật nút ở đây sẽ
      // là một nút bấm không bao giờ có tác dụng.
      on: (d) => !!d.gateway_running,
      onText: () => 'đang chạy',
      offText: (d) => d.message || 'chưa cấu hình',
    },
  ];

  async function loadOpsHealth() {
    // Gộp song song: các endpoint độc lập, tuần tự sẽ cộng dồn độ trễ.
    const get = async (path) => {
      const res = await apiFetch(`${API_BASE}/api/v1/${path}`);
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      return res.json();
    };

    // Trạng thái từng hệ thống con lấy theo bảng khai báo, không ghi cứng
    // từng lệnh — bảng thêm bao nhiêu mục thì gọi bấy nhiêu.
    const subs = {};
    await Promise.all(OPS_SUBSYSTEMS.map(async (s) => {
      subs[s.key] = await get(s.path).catch((e) => ({ __err: e.message }));
    }));

    const [stats, conns, bg, itsm, wh, plugins] = await Promise.all([
      get('system/stats').catch((e) => ({ __err: e.message })),
      get('enterprise/connectors/health').catch((e) => ({ __err: e.message })),
      get('enterprise/background-tasks').catch((e) => ({ __err: e.message })),
      get('itsm/tickets').catch((e) => ({ __err: e.message })),
      get('enterprise/webhooks/recent').catch((e) => ({ __err: e.message })),
      get('enterprise/plugin-registry/stats').catch((e) => ({ __err: e.message })),
    ]);

    _opsSubs = subs;
    renderOpsHealth({ stats, conns, bg, itsm, wh });
    renderOpsControls(subs);
    renderInfraList(conns);
    renderRiskTiers(plugins);
    loadOpsLog();
  }

  function _setKpi(id, text, tone) {
    const el = $('cc-kpi-' + id);
    if (el) {
      el.textContent = text;
      el.className = 'text-xl font-bold leading-none '
        + (tone === 'bad' ? 'text-rose-600 dark:text-rose-400'
          : tone === 'warn' ? 'text-amber-600 dark:text-amber-400'
            : tone === 'ok' ? 'text-emerald-600 dark:text-emerald-400'
              : 'text-slate-400 dark:text-slate-500');
    }
    const dot = $('cc-kpi-' + id + '-dot');
    if (dot) dot.className = 'w-1.5 h-1.5 rounded-full ' + (
      tone === 'bad' ? 'bg-rose-500'
        : tone === 'warn' ? 'bg-amber-500'
          : tone === 'ok' ? 'bg-emerald-500'
            : 'bg-slate-300 dark:bg-slate-600');
  }

  // Phân biệt ba trạng thái, đây là cả chìa khoá của toàn bng bảng vận hành:
  //   undefined      → nguồn này không được hỏi tới (ví dụ gọi hàm render một
  //                    phần) — im lặng, KHÔNG phải lỗi.
  //   { __err: ... } → nguồn này hỏng thật — phải nói ra.
  //   payload        → dùng số liệu.
  //
  // Gộp hai trạng thái đầu là "hiện Lỗi cho mọi thứ", còn gộp hai trạng thái
  // sau là hiện "0 sự cố" cho một thứ ta thực ra không biết. Cả hai đều nói
  // dối người đang giám sát, và cái thứ hai nguy hiểm hơn: "0 sự cố" khiến
  // người ta yên trí.
  function _srcErr(d) { return d && d.__err ? String(d.__err) : null; }

  // Số liệu nhỏ ở chân thẻ điều hành. `tone` tuỳ chọn: bỏ trống thì giữ màu
  // mặc định, truyền 'warn'/'bad' thì tô màu cảnh báo.
  function _setStat(id, value, tone) {
    const e = $('cc-stat-' + id);
    if (!e) return;
    e.textContent = value;
    // Luôn gán lại className, kể cả khi tone rỗng. Chỉ gán khi có tone thì
    // màu cảnh báo của lần render trước sẽ bám lại mãi: ổ đĩa từng 95% rồi
    // hỏng, ô vẫn hiện đỏ trong khi số đã là "—" — tức là đang báo động
    // một thứ vốn đã hết.
    e.className = 'text-sm font-bold mt-0.5 ' + (
      tone === 'bad' ? 'text-rose-600 dark:text-rose-400'
        : tone === 'warn' ? 'text-amber-600 dark:text-amber-400'
          : 'text-slate-800 dark:text-slate-100');
  }

  function renderOpsHealth(d) {
    const { stats, conns, bg, itsm, wh } = d || {};

    // ── Máy chủ: uptime + ổ đĩa ──
    //
    // Ở đây KHÔNG vẽ CPU/RAM nữa. Cùng số liệu đó đã hiện ở đồng hồ tab Tổng
    // Quan và trong bản dựng trước của chính tab này — ba chỗ cạnh nhau,
    // admin phải dừng lại để đối chiếu chúng có khớp không. Dung lượng ổ
    // đĩa thì không chỗ nào khác hiện, nên giữ lại ở chân thẻ điều hành.
    if (stats && !_srcErr(stats) && stats.hardware) {
      const hw = stats.hardware;
      const up = $('cc-health-uptime');
      if (up) up.textContent = `chạy ${_fmtDuration(hw.uptime_seconds)}`;
      const disk = Number(hw.disk_percent);
      const diskOk = isFinite(disk);
      // Ngưỡng 85/90 giống hệt phần còn lại của bảng điều hành. Lệch ngưỡng
      // giữa các ô cùng một thẻ khiến người đọc không biết số nào mới đáng
      // lo — mà phải đoán.
      _setStat('disk', diskOk ? `${Math.round(disk)}%` : '—',
        diskOk && disk >= 90 ? 'bad' : diskOk && disk >= 85 ? 'warn' : '');
    } else if (stats) {
      const up = $('cc-health-uptime');
      if (up) up.textContent = 'không đọc được';
      const dot = $('cc-health-dot');
      if (dot) dot.className = 'w-1.5 h-1.5 rounded-full bg-rose-500';
      _setStat('disk', 'Lỗi', 'bad');
    }

    // ── Hạ tầng ──
    // "Sẵn sàng" = đủ thông tin đăng nhập VÀ không bị tắt. Connector đã có
    // đủ khoá nhưng bị tắt thì mọi lời gọi cũng không chạy — đếm nó là sẵn
    // sàng là nói dối đúng thứ mà bảng này tồn tại để phản ánh.
    if (conns && !_srcErr(conns) && conns.connectors) {
      const all = Object.entries(conns.connectors);
      const ready = all.filter(([, c]) => c.configured && c.enabled !== false).length;
      _setKpi('infra', `${ready}/${all.length}`, ready === all.length ? 'ok' : 'warn');
    } else if (conns) {
      _setKpi('infra', 'Lỗi', 'bad');
    }

    // ── Tác vụ nền ──
    if (bg && !_srcErr(bg)) {
      const running = Number(bg.running || 0);
      _setKpi('bg', String(running), running > 0 ? 'ok' : '');
    } else if (bg) {
      _setKpi('bg', 'Lỗi', 'bad');
    }

    // ── Chờ phê duyệt ──
    // `loadPending()` là nguồn duy nhất ghi ô này, không tính lại ở đây.

    // ── Sự cố đang mở ──
    let incidents = 0, incErr = null;
    if (itsm) {
      if (!_srcErr(itsm) && Array.isArray(itsm.tickets)) {
        const CLOSED = new Set(['completed', 'closed', 'resolved', 'cancelled']);
        incidents += itsm.tickets.filter((t) => !CLOSED.has(String(t.status || '').toLowerCase())).length;
      } else {
        incErr = _srcErr(itsm) || 'phản hồi thiếu danh sách ticket';
      }
    }
    if (wh) {
      if (!_srcErr(wh)) incidents += Number(wh.total || 0);
      else if (!incErr) incErr = _srcErr(wh);
    }

    if (incErr) _setKpi('incident', 'Lỗi', 'bad');
    else if (itsm || wh) _setKpi('incident', String(incidents), incidents > 0 ? 'warn' : 'ok');

    // ── Số liệu phụ ──
    if (stats && !_srcErr(stats)) {
      _setStat('skills', String(stats.skills_count ?? '—'));
      _setStat('users', String(stats.users_count ?? '—'));
    }
    if (itsm && !_srcErr(itsm) && Array.isArray(itsm.tickets)) {
      _setStat('tickets', String(itsm.tickets.length));
    } else if (itsm) {
      _setStat('tickets', 'Lỗi');
    }
  }

  function _fmtDuration(sec) {
    const s = Number(sec);
    if (!isFinite(s) || s < 0) return '—';
    const d = Math.floor(s / 86400);
    const h = Math.floor((s % 86400) / 3600);
    const m = Math.floor((s % 3600) / 60);
    if (d > 0) return `${d} ngày ${h} giờ`;
    if (h > 0) return `${h} giờ ${m} phút`;
    return `${m} phút`;
  }

  // ═══ VIỆC CHẠY MỘT LẦN ════════════════════════════════════════════
  //
  // Khác bật/tắt ở trên: đây là việc bấm xong là xong, không có trạng thái
  // để giữ. Không khoá sau kết quả — kết quả nói lại trong thông báo, và
  // chạy lại lúc sự cố đang xảy ra thì hữu ích hơn là cấm.
  //
  // `describe` bắt buộc nói ra ĐIỀU ĐÃ XẢY RA, không phải mã trạng thái.
  // Ví dụ đồng bộ AD trả `status: "warning"` kèm lý do "thiếu RSAT" — đưa
  // thẳng chữ "warning" lên giao diện thì admin thấy một từ vô nghĩa và
  // không biết phải làm gì.
  const OPS_ONESHOT = [
    {
      btnId: 'cc-btn-sentinel',
      label: 'Quét Sentinel',
      path: 'sentinel/check',
      describe: (j) => {
        const n = Number(j.incidents_found);
        if (!Number.isFinite(n)) return 'đã quét xong.';
        return n > 0
          ? `phát hiện ${n} sự cố, đã gửi cảnh báo.`
          : 'không phát hiện sự cố.';
      },
    },
    {
      btnId: 'cc-btn-ad-sync',
      label: 'Đồng bộ AD',
      path: 'domain/sync',
      describe: (j) => {
        // Lý do cụ thể luôn nằm trong phần con, không nằm ở trạng thái
        // tổng. Ưu tiên lấy lý do trước, số liệu sau.
        const why = j.users?.message || j.computers?.message || j.message || j.detail;
        if (why) return String(why);
        const u = Number(j.total_users);
        const c = Number(j.total_computers);
        if (Number.isFinite(u) && Number.isFinite(c)) {
          return `xong: ${u} nhân viên, ${c} máy tính.`;
        }
        return 'đã đồng bộ xong.';
      },
    },
  ];

  async function runOpsOneShot(spec) {
    const btn = $(spec.btnId);
    if (!btn || btn.disabled) return;
    const was = btn.textContent;
    btn.disabled = true;
    btn.textContent = 'Đang chạy…';
    try {
      const res = await apiFetch(`${API_BASE}/api/v1/${spec.path}`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
      });
      const j = await res.json().catch(() => ({}));
      if (!res.ok) throw new Error(j.detail || j.message || `HTTP ${res.status}`);
      showToast(`${spec.label}: ${spec.describe(j)}`);
      // Bảng điều hành có thể đã đổi số sau khi chạy (tác vụ nền, sự cố).
      await loadOpsHealth();
    } catch (e) {
      showToast(`${spec.label} không chạy được: ${e.message || e}`);
    } finally {
      btn.disabled = false;
      btn.textContent = was;
    }
  }

  function runOpsSentinelScan() {
    return runOpsOneShot(OPS_ONESHOT.find((o) => o.path === 'sentinel/check'));
  }

  function runOpsDomainSync() {
    return runOpsOneShot(OPS_ONESHOT.find((o) => o.path === 'domain/sync'));
  }

  // ═══ DANH SÁCH HỆ THỐNG CON ĐIỀU HÀNH ĐƯỢC ═════════════════════════
  //
  // Mỗi dòng: tên, trạng thái thật, nút bật/tắt (nếu điều khiển được).
  //
  // Nguyên tắc ở đây: nút chỉ hiện khi thao tác đó THỰC SỰ tồn tại. Hệ thống
  // nào không có API bật/tắt thì hiện lý do cấu hình, chứ không hiện một nút
  // bấm xong không có gì xảy ra — nút giả còn tệ hơn không có nút, vì nó khiến
  // người ta tin là đã xử lý xong.
  function renderOpsControls(subs) {
    const box = $('cc-subsystems');
    if (!box) return;
    // Bọc lỗi: nếu một mục trong bảng khai báo sai, khung này sẽ giữ nguyên
    // chữ "Đang tải…" mãi mãi và trông như đang tải — người dùng chờ hoài
    // không bao giờ biết là lỗi. Hiện lỗi ra thay vì nuốt im.
    try {
      box.innerHTML = OPS_SUBSYSTEMS.map((s) => {
        const d = (subs && subs[s.key]) || undefined;
        const err = _srcErr(d);

        // Không có dữ liệu: nói thẳng là chưa biết, không suy ra "đang tắt".
        if (!d) {
          return `<div class="flex items-center gap-2 px-2.5 py-2 rounded-lg bg-slate-50 dark:bg-slate-800/40">
          <span class="w-1.5 h-1.5 rounded-full bg-slate-300 dark:bg-slate-600 shrink-0"></span>
          <span class="text-[11px] font-semibold text-slate-600 dark:text-slate-300 truncate">${_esc(s.label)}</span>
          <span class="ml-auto text-[10px] text-slate-400 dark:text-slate-500 shrink-0">chưa kiểm tra</span>
        </div>`;
        }

        if (err) {
          return `<div class="px-2.5 py-2 rounded-lg bg-rose-50 dark:bg-rose-900/20 border border-rose-200 dark:border-rose-900/40">
          <div class="flex items-center gap-2">
            <span class="w-1.5 h-1.5 rounded-full bg-rose-500 shrink-0"></span>
            <span class="text-[11px] font-semibold text-slate-700 dark:text-slate-200 truncate">${_esc(s.label)}</span>
            <span class="ml-auto text-[10px] font-semibold text-rose-600 dark:text-rose-400 shrink-0">không đọc được trạng thái</span>
          </div>
          <p class="text-[10px] text-rose-500 dark:text-rose-400 mt-1 break-words">${_esc(err)}</p>
        </div>`;
        }

        const on = !!s.on(d);
        const stateText = on
          ? (s.onText ? s.onText(d) : 'đang chạy')
          : (s.offText ? s.offText(d) : 'đang tắt');

        // Không có API bật/tắt → hiện lý do, không hiện nút.
        const action = s.togglePath
          ? `<button type="button" data-ops-toggle="${_esc(s.key)}" title="${_esc(s.hint || '')}"
             class="shrink-0 px-2.5 py-1 text-[10px] font-bold rounded-md transition ${on ? 'bg-rose-100 dark:bg-rose-900/40 text-rose-700 dark:text-rose-300 hover:bg-rose-200 dark:hover:bg-rose-900/60'
            : 'bg-emerald-100 dark:bg-emerald-900/40 text-emerald-700 dark:text-emerald-300 hover:bg-emerald-200 dark:hover:bg-emerald-900/60'}">
             ${on ? 'Tắt' : 'Bật'}
           </button>`
          : `<span class="shrink-0 text-[10px] text-slate-400 dark:text-slate-500">không điều khiển được</span>`;

        return `<div class="flex items-center gap-2 px-2.5 py-2 rounded-lg bg-slate-50 dark:bg-slate-800/40 ${on ? 'border border-emerald-200 dark:border-emerald-900/30'
          : 'border border-transparent'}">
        <span class="w-1.5 h-1.5 rounded-full shrink-0 ${on ? 'bg-emerald-500' : 'bg-slate-300 dark:bg-slate-600'}"></span>
        <div class="min-w-0 flex-1">
          <div class="flex items-baseline gap-1.5">
            <span class="text-[11px] font-semibold text-slate-700 dark:text-slate-200 truncate">${_esc(s.label)}</span>
            <span class="text-[10px] truncate ${on ? 'text-emerald-600 dark:text-emerald-400'
            : 'text-slate-400 dark:text-slate-500'}">${_esc(stateText)}</span>
          </div>
          <p class="text-[9px] text-slate-400 dark:text-slate-500 truncate" title="${_esc(s.hint || '')}">${_esc(s.hint || '')}</p>
        </div>
        ${action}
      </div>`;
      }).join('');

      // Gắn sự kiện sau khi dựng innerHTML — không dùng onclick nội tuyến để
      // tránh phải dựng lại chuỗi onclick mỗi lần vẽ lại.
      box.querySelectorAll('[data-ops-toggle]').forEach((btn) => {
        btn.addEventListener('click', () => toggleOpsSubsystem(btn.dataset.opsToggle, btn));
      });
    } catch (err) {
      box.innerHTML = `<p class="text-[11px] text-rose-500 text-center py-2">`
        + `Không dựng được danh sách hệ thống: ${_esc(err.message || String(err))}</p>`;
      console.error('[CommandCenter] renderOpsControls lỗi:', err);
    }
  }

  async function toggleOpsSubsystem(key, btn) {
    const s = OPS_SUBSYSTEMS.find((x) => x.key === key);
    if (!s || !s.togglePath) return;

    const was = btn.textContent.trim();
    btn.disabled = true;
    btn.textContent = '…';
    try {
      const init = { method: 'POST', headers: { 'Content-Type': 'application/json' } };
      if (s.body) init.body = JSON.stringify(s.body({}));
      const res = await apiFetch(`${API_BASE}/api/v1/${s.togglePath}`, init);
      const j = await res.json().catch(() => ({}));
      if (!res.ok) throw new Error(j.detail || `HTTP ${res.status}`);

      // Dựa vào `active` mà máy chủ trả về, không tự đoán trạng thái mới.
      // Với endpoint toggle trả về active=false khi hỏng dù không kết nối
      // được thì đây chính là chỗ phải tin máy chủ.
      const nowOn = typeof j.active === 'boolean'
        ? j.active
        : (typeof j.enabled === 'boolean' ? j.enabled : null);
      showToast(j.message || (nowOn === true ? 'Đã bật.' : 'Đã tắt.'));

      // Vẽ lại để hàng này lấy trạng thái mới từ server — nếu chỉ đổi chữ
      // nút thì sẽ hiện "đang chạy" cho một worker chết lặng lẽ.
      const d = await apiFetch(`${API_BASE}/api/v1/${s.path}`)
        .then((r) => r.json())
        .catch((e) => ({ __err: e.message }));
      _opsSubs = { ..._opsSubs, [key]: d };
      renderOpsControls(_opsSubs);
    } catch (e) {
      showToast('Không thực hiện được: ' + (e.message || e));
      btn.disabled = false;
      btn.textContent = was;
    }
  }

  // Bảng kết nối hạ tầng. Cột "thiếu gì" là phần quan trọng nhất: connector
  // không cấu hình thì mọi lời gọi sẽ thất bại, mà người dùng chỉ thấy lỗi
  // chung chung. Ghi rõ tên trường còn thiếu giúp họ điền được ngay.
  function renderInfraList(conns) {
    const box = $('cc-infra-list');
    if (!box) return;
    const badge = $('cc-infra-badge');

    if (!conns || _srcErr(conns) || !conns.connectors) {
      if (badge) { badge.textContent = 'Lỗi'; badge.className = 'ml-auto px-2 py-0.5 rounded-md text-[10px] font-bold bg-rose-100 dark:bg-rose-900/40 text-rose-600 dark:text-rose-400'; }
      box.innerHTML = `<p class="text-[11px] text-rose-500 text-center py-4">`
        + `Không đọc được trạng thái kết nối: ${_esc(_srcErr(conns) || 'phản hồi thiếu dữ liệu')}</p>`;
      return;
    }

    const entries = Object.entries(conns.connectors);
    const ready = entries.filter(([, c]) => c.configured && c.enabled !== false).length;
    if (badge) {
      badge.textContent = `${ready}/${entries.length} sẵn sàng`;
      badge.className = 'ml-auto px-2 py-0.5 rounded-md text-[10px] font-bold ' + (
        ready === entries.length ? 'bg-emerald-100 dark:bg-emerald-900/40 text-emerald-700 dark:text-emerald-400'
          : 'bg-amber-100 dark:bg-amber-900/40 text-amber-700 dark:text-amber-400');
    }

    if (!entries.length) {
      box.innerHTML = '<p class="text-[11px] text-slate-400 dark:text-slate-500 text-center py-4">Chưa có nguồn kết nối nào.</p>';
      return;
    }

    box.innerHTML = entries.map(([name, c]) => {
      const ok = !!c.configured;
      const off = c.enabled === false;
      const missing = (c.missing_fields || []).slice(0, 3);
      const tone = off
        ? 'border-slate-200 dark:border-slate-700 bg-slate-50 dark:bg-slate-800/40'
        : ok
          ? 'border-emerald-500/25 bg-emerald-500/5'
          : 'border-amber-500/30 bg-amber-500/5';
      const dot = off ? 'bg-slate-400' : ok ? 'bg-emerald-500' : 'bg-amber-500';
      const label = off ? 'Đã tắt' : ok ? 'Sẵn sàng' : 'Thiếu cấu hình';
      return `
        <div class="rounded-lg border ${tone} p-2.5">
          <div class="flex items-center gap-2">
            <span class="w-1.5 h-1.5 rounded-full ${dot} shrink-0"></span>
            <span class="text-[11px] font-bold text-slate-800 dark:text-slate-100 font-mono">${_esc(name)}</span>
            <span class="ml-auto text-[9px] font-bold ${ok && !off ? 'text-emerald-600 dark:text-emerald-400' : 'text-amber-600 dark:text-amber-400'}">${_esc(label)}</span>
          </div>
          ${missing.length && !off
          ? `<p class="text-[10px] text-amber-700 dark:text-amber-400 mt-1 font-mono">còn thiếu: ${_esc(missing.join(', '))}</p>`
          : `<p class="text-[10px] text-slate-500 dark:text-slate-400 mt-1">${_esc((c.actions || []).length)} thao tác dùng được</p>`}
        </div>`;
    }).join('');
  }

  // Thang Zero-Trust 1–5 đã có sẵn trong core/plugin_registry.py, và mọi tool
  // risk >= 3 đều phải qua cổng HITL. Hiện bảng đó ra để admin thấy rõ AI
  // được tự làm gì và phải xin phép việc gì, thay vì phải nhớ từng lệnh.
  //
  // Số liệu lấy từ registry thật chứ không gõ cứng — tool bị tắt hoặc thêm
  // mới thì bảng tự đúng theo, không bao giờ lệch với server.
  function renderRiskTiers(plugins) {
    const box = $('cc-risk-tiers');
    if (!box) return;

    if (!plugins || _srcErr(plugins) || !plugins.tools) {
      box.innerHTML = `<p class="text-[11px] text-rose-500 text-center py-3">`
        + `Không đọc được danh mục tool: ${_esc(_srcErr(plugins) || 'phản hồi thiếu dữ liệu')}</p>`;
      return;
    }

    const tools = Object.entries(plugins.tools);
    const byLevel = new Map();
    tools.forEach(([name, t]) => {
      const lv = Math.max(1, Math.min(5, Number(t.risk_level) || 1));
      if (!byLevel.has(lv)) byLevel.set(lv, []);
      byLevel.get(lv).push([name, t]);
    });

    const needApproval = tools.filter(([, t]) => (Number(t.risk_level) || 1) >= 3).length;
    const auto = tools.length - needApproval;

    box.innerHTML = `
      <div class="grid grid-cols-3 gap-2 text-center mb-2">
        <div class="rounded-lg bg-emerald-500/10 px-2 py-1.5">
          <p class="text-base font-bold text-emerald-600 dark:text-emerald-400 leading-none">${auto}</p>
          <p class="text-[9px] text-slate-500 dark:text-slate-400 mt-1">AI tự chạy</p>
        </div>
        <div class="rounded-lg bg-rose-500/10 px-2 py-1.5">
          <p class="text-base font-bold text-rose-600 dark:text-rose-400 leading-none">${needApproval}</p>
          <p class="text-[9px] text-slate-500 dark:text-slate-400 mt-1">cần bạn duyệt</p>
        </div>
        <div class="rounded-lg bg-slate-100 dark:bg-slate-700/50 px-2 py-1.5">
          <p class="text-base font-bold text-slate-700 dark:text-slate-200 leading-none">${tools.length}</p>
          <p class="text-[9px] text-slate-500 dark:text-slate-400 mt-1">tổng tool</p>
        </div>
      </div>
      ${[1, 2, 3, 4, 5].map((lv) => {
      const list = byLevel.get(lv) || [];
      const need = lv >= 3;
      const head = need
        ? 'bg-rose-500/10 text-rose-600 dark:text-rose-400'
        : lv === 2 ? 'bg-amber-500/10 text-amber-600 dark:text-amber-400'
          : 'bg-emerald-500/10 text-emerald-600 dark:text-emerald-400';
      return `
          <details class="rounded-lg border border-slate-200 dark:border-slate-700/60 overflow-hidden">
            <summary class="flex items-center gap-2 px-2.5 py-1.5 cursor-pointer select-none hover:bg-slate-50 dark:hover:bg-slate-700/30">
              <span class="px-1.5 py-0.5 rounded text-[9px] font-bold ${head}">L${lv}</span>
              <span class="text-[10px] text-slate-600 dark:text-slate-300">${need ? 'Phải qua bạn duyệt' : 'AI tự chạy'}</span>
              <span class="ml-auto text-[10px] font-mono text-slate-400 dark:text-slate-500">${list.length}</span>
            </summary>
            ${list.length
          ? `<div class="px-2.5 pb-2 space-y-0.5">${list.map(([name, t]) => `
                  <p class="text-[10px] font-mono text-slate-500 dark:text-slate-400 truncate">
                    · ${_esc(name)}${t.enabled === false ? ' <span class="text-slate-400">(đã tắt)</span>' : ''}
                  </p>`).join('')}</div>`
          : `<p class="px-2.5 pb-2 text-[10px] text-slate-400 dark:text-slate-500">Không có tool nào ở mức này.</p>`}
          </details>`;
    }).join('')}`;
  }

  /**
   * Nhật ký vận hành — ảnh chụp gần đây của /api/v1/logs/recent.
   *
   * Phase 79: bảng này trước đây nằm ở Trung Tâm Chỉ Huy (`#cc-ops-log`), còn
   * Bảng Điều Khiển có một bảng thứ hai (`#mon-log-list`) nạp CÙNG endpoint —
   * cùng dữ liệu ở hai màn hình. Nay cả hai gom vào tab Nhật Ký (chế độ
   * "Nhật ký vận hành") và chỉ giữ bản này, vì nó có bộ lọc ẩn dòng
   * heartbeat mà bản kia không có.
   */
  async function loadOpsLog() {
    try {
      const res = await apiFetch(`${API_BASE}/api/v1/logs/recent`);
      const data = await res.json();
      _opsLogCache = (data && data.logs) || [];
    } catch (err) {
      _opsLogCache = null;
      const box = $('log-recent-list');
      if (box) box.innerHTML = `<p class="text-[11px] text-rose-500 text-center py-4 font-sans">`
        + `Không tải được nhật ký: ${_esc(err.message || err)}</p>`;
      return;
    }
    renderOpsLog();
  }

  let _opsLogCache = null;

  function renderOpsLog() {
    const box = $('log-recent-list');
    if (!box) return;
    if (_opsLogCache === null) {
      box.innerHTML = '<p class="text-xs text-slate-400 dark:text-slate-500 text-center py-6 font-sans">Đang tải…</p>';
      return;
    }

    const verbose = !!$('log-recent-verbose')?.checked;
    const rows = verbose ? _opsLogCache : _opsLogCache.filter((e) => !_isOpsNoise(e));
    const hidden = _opsLogCache.length - rows.length;

    if (!rows.length) {
      box.innerHTML = `<p class="text-[11px] text-slate-400 dark:text-slate-500 text-center py-4 font-sans">`
        + `Không có dòng nào khác nhiễu. Đã ẩn ${hidden} dòng heartbeat.</p>`;
      return;
    }

    const tone = { ERROR: 'text-rose-400', CRITICAL: 'text-rose-400', WARNING: 'text-amber-400' };
    box.innerHTML = rows.slice(0, 120).map((e) => `
      <div class="flex gap-1.5 leading-relaxed">
        <span class="shrink-0 text-slate-600 dark:text-slate-500">${_esc(_hhmm(e.timestamp))}</span>
        <span class="shrink-0 font-bold ${tone[e.level] || 'text-slate-500'}">${_esc(e.level || '')}</span>
        <span class="min-w-0 break-all text-slate-500 dark:text-slate-400">${_esc(e.message || '')}</span>
      </div>`).join('')
      + (hidden ? `<p class="text-[9px] text-slate-500 dark:text-slate-500 pt-1 mt-1 border-t border-slate-200 dark:border-slate-700/60 font-sans">`
        + `Đang ẩn ${hidden} dòng heartbeat/health-check — bật "hiện cả nhiễu" để xem.</p>` : '');
  }

  function _hhmm(ts) {
    if (!ts) return '--:--';
    const d = new Date(ts);
    if (isNaN(d.getTime())) return String(ts).slice(0, 5);
    return `${String(d.getHours()).padStart(2, '0')}:${String(d.getMinutes()).padStart(2, '0')}`;
  }

  // Rà soát là việc CHỈ ĐỌC (quét task quá hạn/sắp đến hạn, không ghi gì), nên
  // để admin bấm tay chạy được mà không cần qua HITL — cũng đúng với nguyên
  // tắc "AI tự làm việc rủi ro thấp".
  async function runCommandCenterAudit() {
    const btn = $('cc-audit-btn');
    const box = $('cc-risk-tiers');
    if (btn) { btn.disabled = true; btn.textContent = 'Đang rà soát…'; }
    try {
      const res = await apiFetch(`${API_BASE}/api/v1/enterprise/proactive/run-audit`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', 'Authorization': `Bearer ${getAuthToken()}` },
        body: '{}',
      });
      const data = await res.json();
      const r = data?.result || {};
      if (!res.ok || r.status === 'error') {
        showToast(`❌ Rà soát lỗi: ${(data && data.message) || r.error || 'không rõ nguyên nhân'}`, 'error');
        return;
      }
      const quá = Number(r.total_overdue || 0);
      const sắp = Number(r.total_upcoming || 0);
      showToast(quá
        ? `🔴 Rà soát xong: ${quá} việc quá hạn, ${sắp} sắp đến hạn.`
        : `✅ Rà soát xong: không có việc quá hạn, ${sắp} việc sắp đến hạn.`,
        quá ? 'warning' : 'success');
      await loadOpsHealth();
      if (box) {
        const lines = (r.details || []).slice(0, 3).map((d) => _esc(d.message || '').slice(0, 160));
        if (lines.length) showToast(lines.join('<br>'), 'info');
      }
    } catch (err) {
      showToast(`❌ Lỗi mạng: ${err.message || err}`, 'error');
    } finally {
      if (btn) { btn.disabled = false; btn.textContent = 'Chạy rà soát tức thì'; }
    }
  }

  // ── Smart Tool Router ───────────────────────────────────────────────────
  // Thay vì thêm 20 nút "hỏi về X", cho người dùng gõ lệnh tự nhiên rồi định
  // tuyến tới đúng endpoint. Bảng định tuyến đặt ở client vì nó là quyết
  // định về *trải nghiệm* (cái nào đáng gợi ý), còn quyền vẫn do server ép.
  //
  // So khớp chạy trên dạng **đã bỏ dấu**. Đây không phải chi tiết thừa: người
  // dùng Việt gõ "quy con bao lau", "thai san nghi may thang" hằng ngày, và
  // ký tự có dấu khác nhau ở mỗi từ ("Quỹ" với y móc, "quỹ" với y ngã). Bảng
  // định tuyến viết bằng dấu đầy đủ sẽ trượt trên chính các nút gợi ý do
  // chính hệ thống hiển thị — đã xảy ra: nút "Quỹ còn bao lâu nữa" bị định
  // tuyến sang biểu đồ vì "Quỹ" ≠ "quỹ".
  /* ROUTER_START — đoạn từ đây tới ROUTER_END được
     tests/test_command_center_router.mjs cắt ra chạy ngoài trình duyệt. */
  function _deaccent(s) {
    return String(s || '')
      .normalize('NFD')
      .replace(/[̀-ͯ]/g, '');   // bỏ dấu thanh/sắc/hỏi/ngã
  }

  // Khớp theo TỪ, không phải theo chuỗi con.
  //
  // Vì sao: `includes('quy')` khớp "quy trình", "quy định", "quyết định" —
  // nên câu "Quy trình xin nghỉ phép năm" bị định tuyến về dòng tiền (vì có
  // "quỹ" trong từ khoá) thay vì về quy chế. Ranh giới từ loại bỏ hẳng nhóm
  // lỗi này: "quỹ" chỉ khớp khi đứng riêng.
  const _kwCache = new Map();
  function _kwRegex(kw) {
    let re = _kwCache.get(kw);
    if (!re) {
      const safe = String(kw).replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
      re = new RegExp(`(?:^|[^a-z0-9])${safe}(?:$|[^a-z0-9])`, 'i');
      _kwCache.set(kw, re);
    }
    return re;
  }

  // Thứ tự bảng này CÓ Ý NGHĨA: khoản lương ("lương thưởng") và khoản nghỉ
  // ("nghỉ phép") có thể chạm cả từ khoá của nhau, nên từ khoá đặc hơn phải
  // đứng trước. Từ khoá nào quá rộng thì thu hẹp lại cho tới khi không còn
  // bắt nhầm — đừng thêm từ đơn lẻ.
  //
  // Riêng "quỹ" là ví dụ điển hình của từ khoá quá rộng: bỏ dấu xong nó
  // trùng với "quy" của "quy trình", "quy chế", "nội quy" và cả "quý 2"
  // (quý = quý II, không phải quỹ tiền). Ranh giới từ KHÔNG cứu được vì
  // "quy" trong cả bốn trường hợp đều đứng riêng. Cách duy nhất đúng là
  // ghép nó với từ đứng sau: "quỹ con", "quỹ tiền", "quỹ vốn"...
  const ROUTES = [
    {
      id: 'cashflow', endpoint: 'cashflow',
      keywords: ['runway', 'quy con', 'quy tien', 'quy von', 'quy ngan',
        'quy doanh nghiep', 'quy dau tu', 'dong tien', 'thanh toan',
        'tai chinh', 'het tien', 'tien het', 'tien con', 'con bao lau',
        'chiu khuc', 'thieu tien']
    },
    {
      id: 'policy', endpoint: 'policy',
      keywords: ['quy che', 'quy dinh', 'quy trinh', 'chinh sach', 'thu tuc',
        'noi quy', 'nghi phep', 'thai san', 'bao hiem', 'nghi hong',
        'ky luat', 'phu cap', 'don xin', 'don nghi', 'don tom tat',
        'cham cong', 'hop dong lao dong', 'om']
    },
    {
      id: 'people', endpoint: 'chart',
      keywords: ['nhan vien', 'nhan su', 'phong ban', 'bo phan', 'headcount',
        'ho so', 'tuyen', 'so luong', 'nhan luc']
    },
    {
      id: 'task', endpoint: 'chart',
      keywords: ['cong viec', 'task', 'tien do', 'nhiem vu', 'deadline', 'hoan thanh',
        'ke hoach', 'tien bo']
    },
    {
      id: 'finance', endpoint: 'chart',
      keywords: ['chi phi', 'doanh thu', 'thu chi', 'loi nhuan', 'ngan sach',
        'gia vang', 'hoa don', 'lai suat', 'cong no', 'doanh so']
    },
  ];

  function classify(text) {
    const t = _deaccent(String(text || '')).toLowerCase();
    for (const r of ROUTES) {
      if (r.keywords.some((k) => _kwRegex(k).test(t))) return r;
    }
    // Không khớp gì: mặc định vẽ biểu đồ — đây là phần lớn câu hỏi trên
    // Command Center, và biểu đồ rỗng vẫn hơn báo lỗi.
    return { id: 'chart', endpoint: 'chart', keywords: [] };
  }
  /* ROUTER_END */

  async function ask(text) {
    const q = (text ?? '').trim();
    if (!q) return;
    const out = $('cc-ask-result');
    if (out) out.innerHTML = '<span class="text-slate-400">Đang xử lý…</span>';

    const route = classify(q);
    try {
      if (route.endpoint === 'cashflow') {
        // Phase 71: bảng dòng tiền đã gỡ khỏi tab, nhưng câu hỏi thì vẫn
        // phải trả lời được — bỏ panel không có nghĩa bỏ khả năng hỏi. Trả
        // lời bằng chữ ngay trong ô kết quả thay vì âm thầm im lặng hoặc vẽ
        // vào một phần tử không còn tồn tại (lỗi im lặng khó chịu nhất).
        const res = await apiFetch(`${API_BASE}/api/v1/enterprise/analytics/cashflow-health`);
        const d = await res.json();
        const r = d?.result || {};
        if (out) {
          const parts = [];
          if (r.net_balance != null) parts.push(`Số dư ròng: <strong>${_fmtVND(r.net_balance)}</strong>`);
          if (r.runway_days != null) parts.push(`Runway: <strong>${Number(r.runway_days).toFixed(0)} ngày</strong>`);
          if (r.message) parts.push(_esc(r.message));
          out.innerHTML = `<div class="p-2.5 rounded-lg bg-slate-50 dark:bg-slate-900/50 border border-slate-200 dark:border-slate-700">`
            + `<p class="text-[9px] uppercase tracking-wider text-slate-400 dark:text-slate-500 mb-1">Sức khoẻ dòng tiền</p>`
            + `<p class="text-slate-700 dark:text-slate-200">${parts.join('<br>') || 'Chưa có dữ liệu.'}</p>`
            + `<p class="text-[9px] text-slate-400 dark:text-slate-500 mt-1.5">Bảng này không còn nằm trên Trung Tâm Chỉ Huy — chỉ trả lời khi bạn hỏi.</p>`
            + `</div>`;
        }
        return;
      }
      if (route.endpoint === 'policy') {
        await policyLookup(q);
        return;
      }
      const res = await apiFetch(`${API_BASE}/api/v1/enterprise/analytics/chart`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', 'Authorization': `Bearer ${getAuthToken()}` },
        body: JSON.stringify({ prompt: q }),
      });
      const d = await res.json();
      const r = d.result || {};
      if (r.status === 'error' || d.status === 'error') {
        if (out) out.innerHTML = `<span class="text-rose-500">✖ ${_esc(r.message || d.error || 'Lỗi không rõ')}</span>`;
        return;
      }
      // Phase 82: khung "Biểu Đồ Số Liệu" đã bỏ khỏi Bảng Điều Khiển theo yêu
      // cầu nên không còn chỗ vẽ. Câu trả lời vẫn về nguyên vẹn — trình bày
      // dạng chữ để không hứa một biểu đồ không ai nhìn thấy.
      const n = Number(r.records_count || 0);
      if (out) {
        out.innerHTML = n
          ? `<span class="text-emerald-500">✔ Truy vấn hoàn thành — <strong>${n}</strong> điểm dữ liệu.</span>`
          : '<span class="text-amber-500">⚠ Truy vấn trả về 0 dòng.</span>';
      }
    } catch (err) {
      if (out) out.innerHTML = `<span class="text-rose-500">✖ ${_esc(err.message || err)}</span>`;
    }
  }

  // ── Tra cứu quy chế (GraphRAG) ──────────────────────────────────────────
  async function policyLookup(question) {
    // Phase 82: khung "Tra Cứu Quy Chế" riêng đã bỏ — trả lời hiện ngay trong
    // ô kết quả của Điều Hành AI, không còn ô hiển thị phải duy trì riêng.
    const out = $('cc-ask-result');
    if (!out) return;
    out.innerHTML = '<span class="text-slate-400">Đang tra cứu…</span>';
    try {
      const res = await apiFetch(`${API_BASE}/api/v1/enterprise/graph-rag/query`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', 'Authorization': `Bearer ${getAuthToken()}` },
        body: JSON.stringify({ question }),
      });
      const d = await res.json();
      const r = d.result || {};

      if (r.status === 'no_evidence') {
        // Đây là hành vi ĐÚNG, không phải lỗi: hệ thống từ chối bịa. Hiển thị
        // nó như một trạng thái bình thường (màu trung tính), không phải lỗi đỏ.
        //
        // `suggested_action` là mã máy ("upload_document") — hiện nguyên
        // văn ra là C.E.O tưởng hệ thống lỗi. Ưu tiên bản tiếng Việt, và
        // nếu không có thì dùng lời nhắc mặc định thay vì in mã.
        const goiY = d.suggested_action_text || r.suggested_action_text
          || 'Hãy tải tài liệu quy chế liên quan lên mục Tra Cứu Quy Chế.';
        out.innerHTML = `<div class="p-2 rounded-lg bg-slate-50 dark:bg-slate-800 border border-slate-200 dark:border-slate-700">`
          + `<p class="text-[11px] text-slate-600 dark:text-slate-300 font-semibold">Không tìm thấy căn cứ trong tài liệu.</p>`
          + `<p class="text-[10px] text-slate-500 dark:text-slate-400 mt-1">${_esc(goiY)}</p></div>`;
        return;
      }

      const paths = r.graph_paths || [];
      const text = r.answer || '';
      out.innerHTML = `<div class="p-2 rounded-lg bg-emerald-50 dark:bg-emerald-900/20 border border-emerald-500/30">`
        + `<div class="text-[11px] text-slate-700 dark:text-slate-200 leading-relaxed">${renderPortalMarkdown(text)}</div>`
        + (paths.length ? `<p class="text-[9px] text-slate-500 dark:text-slate-400 mt-1.5">${paths.length} đường dẫn tri thức khớp</p>` : '')
        + `</div>`;
    } catch (err) {
      out.innerHTML = `<p class="text-[11px] text-rose-500">Lỗi tra cứu: ${_esc(err.message || err)}</p>`;
    }
  }

  // ── Cột 3: cảnh báo an ninh ─────────────────────────────────────────────
  //
  // Nguồn là nhật ký kiểm toán bất biến `/api/v1/security/audit-logs`, lọc ra các sự
  // kiện bị từ chối hoặc thất bại. Không có endpoint "trạng thái an ninh"
  // riêng — trước đây có thể đã giả định một endpoint không tồn tại và mọi
  // lần gọi đều 404, khiến cột này trống trơn mà không ai biết vì sao.
  async function loadSecurity() {
    const box = $('cc-security-list');
    const badge = $('cc-security-count');
    if (!box) return;
    try {
      const res = await apiFetch(`${API_BASE}/api/v1/security/audit-logs?limit=50`);
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const d = await res.json();
      const logs = d.logs || d.results || d.audit_logs || [];

      // Chỉ hiện sự kiện BẤT THƯỜNG. Dùng whitelist, không dùng phép trừ
      // "không phải SUCCESS thì hiện" — vì `PENDING` cũng là trạng thái bình
      // thường (một yêu cầu duyệt đang chờ, đã hiện ở cột 1 rồi). Cách lọc
      // cũ biến cột này thành 12 dòng PENDING vô nghĩa, người dùng quen mất
      // cảnh báo thật.
      //
      // Whitelist còn có một ưu điểm cho bảng an ninh: một trạng thái MỚI (do
      // module khác thêm vào) sẽ lọt vào danh sách cảnh báo thay vì bị giấu.
      // Thừa một dòng còn hơn bỏ sót sự cố.
      const ANOMALY = new Set(['BLOCKED', 'FAILED', 'DENIED', 'ERROR', 'SUSPICIOUS']);
      const alerts = logs.filter((l) => ANOMALY.has(String(l.outcome || '').toUpperCase()))
        .map((l) => ({
          title: l.action || 'Sự kiện bất thường',
          detail: `${l.client_id || 'unknown'} · ${l.timestamp || ''}`,
          status: l.status,
        }));

      if (badge) {
        badge.textContent = String(alerts.length);
        badge.classList.toggle('hidden', !alerts.length);
      }
      if (!alerts.length) {
        const scanned = logs.length;
        box.innerHTML = `<p class="text-xs text-slate-400 dark:text-slate-500 text-center py-6">`
          + `✔ Không có sự kiện bất thường`
          + (scanned ? `<br><span class="text-[10px]">(${scanned} dòng nhật ký gần nhất đều bình thường)</span>` : '')
          + `</p>`;
        return;
      }
      box.innerHTML = alerts.slice(0, 10).map((a) => `
        <div class="rounded-xl border border-rose-500/30 bg-rose-500/5 dark:bg-rose-500/10 p-2.5">
          <div class="flex items-start justify-between gap-2">
            <p class="text-[11px] font-semibold text-slate-800 dark:text-slate-200">${_esc(a.title)}</p>
            <span class="shrink-0 px-1.5 py-0.5 rounded text-[9px] font-bold bg-rose-500/20 text-rose-600 dark:text-rose-400">${_esc(a.status)}</span>
          </div>
          <p class="text-[10px] text-slate-500 dark:text-slate-400 mt-0.5">${_esc(a.detail)}</p>
        </div>`).join('');
    } catch (err) {
      // Hiện rõ lỗi thay vì im lặng — "không tải được" và "không có cảnh báo"
      // là hai kết luận hoàn toàn khác nhau, nhầm lẫn là nguy hiểm.
      box.innerHTML = `<p class="text-[11px] text-slate-400 dark:text-slate-500 text-center py-4">`
        + `Không tải được nhật ký: ${_esc(err.message || err)}</p>`;
    }
  }


  function onEnter() {
    loadPending();
    loadSecurity();
    // Phase 71: `loadCashflow` bị gỡ, thay bằng lớp sức khoẻ vận hành. Gộp 6
    // endpoint chạy song song nên độ trễ bằng một lần gọi, không phải sáu.
    loadOpsHealth();

    // Vẽ biểu đồ đã bị hoãn lúc tab còn ẩn — giờ mới có kích thước thật.
    if (_pendingChart) {
      const p = _pendingChart;
      _pendingChart = null;
      _drawChart(p.config, p.meta);
    } else {
      syncChartLayout();
    }

    // Phase 79: bỏ `syncCommandCenterKpi()` — `loadPending()` ở trên đã ghi
    // thẳng vào ô KPI `cc-kpi-pending`.

    if (!_pollTimer) _pollTimer = setInterval(() => {
      // Ô chờ duyệt phải cập nhật nhanh (admin đang ngồi duyệt), còn số liệu
      // vận hành thì chậm một nhịp cũng không sao — 6 endpoint mỗi 5 giây
      // là 7 req/s vô ích khi RAM/CPU thay đổi theo phút chứ không theo giây.
      loadPending();
      _slowPoll = !_slowPoll;
      if (_slowPoll) loadOpsHealth();
    }, POLL_MS);
  }

  function onLeave() {
    if (_pollTimer) { clearInterval(_pollTimer); _pollTimer = null; }
  }

  // Gọi lại khi quay lại tab (dữ liệu có thể đã cũ).
  function refresh() { onEnter(); }

  // Đổi kích thước cửa sổ kéo dài canvas -> biểu đồ phải vẽ lại theo.
  // Gộp (debounce) vì sự kiện này bắn liên tục lúc người dùng kéo thanh trượt.
  let _resizeTimer = null;
  window.addEventListener('resize', () => {
    if (_resizeTimer) clearTimeout(_resizeTimer);
    _resizeTimer = setTimeout(() => {
      _resizeTimer = null;
      if (_paneVisible()) syncChartLayout();
    }, 200);
  });

  return {
    onEnter, onLeave, refresh, syncChartLayout,
    loadPending, loadSecurity, loadOpsHealth, loadOpsLog, renderOpsLog,
    // Bảng hệ thống con điều hành được và hàm vẽ nó: thêm một hệ thống mới
    // là thêm một mục vào OPS_SUBSYSTEMS, không cần sửa chỗ khác.
    OPS_SUBSYSTEMS, renderOpsControls, toggleOpsSubsystem,
    OPS_ONESHOT, runOpsOneShot, runOpsSentinelScan, runOpsDomainSync,
    ask, policyLookup, renderChart, decide, runCommandCenterAudit,
    // `classify` và `_deaccent` được xuất ra để kiểm thử được bảng định tuyến
    // mà không phải bấm từng nút trên UI. Bảng này sai một ký tự là người
    // dùng bị định tuyến nhầm — đáng để có test riêng.
    classify, _deaccent,
  };
})();

// ── Phase 59/60: Connector Health & Integration Tools ──────────────────────

// Hằng số dùng chung cho toàn bộ khối Phase 59/60.
const CC_CONNECTORS = ['aws', 'oci', 'paperless', 'einvoice'];

// Phase 81: lưới card giờ do `renderConnectionCards()` vẽ lại MỖI LẦN bấm
// sub-tab "Kết Nối" (trước đây 4 card nằm cứng trong HTML, không mất gì khi
// đổi sub-tab). Nếu không nhớ kết quả ra ngoài DOM thì bấm "Kiểm tra" xong
// chuyển sang tab khác rồi quay lại -> mọi chấm sức khoẻ về lại "Chưa kiểm tra",
// tức mất thông tin vừa tốn công lấy.
//
// Ghi ở đây mỗi khi có kết quả; `renderConnectionCards()` đọc lại khi vẽ.
// Còn "Đang kiểm tra…" thì không lưu — trạng thái tạm, vẽ lại thì chạy mới.
const _ccConnHealth = new Map();

/**
 * Khoá còn THIẾU của từng connector cốt lõi, lấy từ
 * `GET /api/v1/enterprise/data-sources` (mảng `builtin`).
 *
 * Vì sao cần: server đã tính sẵn danh sách khoá bắt buộc còn thiếu
 * (`missing_fields`) — nhưng giao diện trước đây chỉ đọc mảng `custom`, bỏ
 * qua `builtin`. Hậu quả: bấm "Kiểm tra" thì API trả về
 * "Authentication failed", và người dùng tưởng khoá họ nhập sai.
 * Thực tế là họ CHƯA NHẬP khoá nào cả.
 *
 * Chỉ lưu TÊN khoá, không lưu giá trị — server cũng chỉ trả tên.
 */
const _ccBuiltinMissing = new Map();

/**
 * Connector đang được nhấn mạnh ở sub-tab Cấu Hình.
 *
 * Vì sao là cờ chứ không sửa phần tử: `loadConnectorConfigAll()` gọi API rồi
 * gán lại `innerHTML` cho cả khối, nên phần tử đã tô sáng bị thay mới và mất
 * luôn class. Đo thực tế: bấm nút, 400ms sau khối đã có nhưng KHÔNG còn vòng
 * tô sáng. Đọc cờ lúc vẽ thì tồn tại đúng lâu như khối đang hiện.
 */
let _ccConfigFocus = null;
// Extensions registered by enterprise plugins (populated at runtime).
let _ccExtensions = [];
// Tên trường bí mật — KHÔNG bao giờ chép vào value của <input>, chỉ ghi "đã lưu".
// Kênh cảnh báo: URL webhook Teams / Slack chính là khoá — không đưa vào DOM.
const CC_SECRET_FIELDS = ['secret_access_key', 'api_token', 'client_secret', 'access_key_id',
  'webhook_url', 'hmac_secret', 'password'];

function _ccGet(id) { return document.getElementById(id); }

function _ccSetStatus(el, ok, okText, idleText) {
  if (!el) return;
  el.textContent = ok ? okText : idleText;
  el.className = ok
    ? 'ml-auto px-1.5 py-0.5 rounded text-[9px] font-bold bg-emerald-100 dark:bg-emerald-900/40 text-emerald-600 dark:text-emerald-400'
    : 'ml-auto px-1.5 py-0.5 rounded text-[9px] font-bold bg-slate-100 dark:bg-slate-700 text-slate-500 dark:text-slate-400';
}

// ── Sub-tab của khối "Trung Tâm Tích Hợp Doanh Nghiệp" ───────────────────
// Phase 81: thêm 'devices' — khối máy trạm 194 dòng trước đây nằm tràn dưới
// sub-tab "Hệ Thống", kéo dài màn hình Tích Hợp bằng nội dung không liên quan.
// Nay là sub-tab riêng và chỉ nạp dữ liệu khi bấm vào.
const CC_SUBTABS = ['conn', 'departments', 'config', 'webhook', 'tools', 'sys', 'devices'];
// 2026-10-12: ba mục cùng nói về connector (danh sách + sức khoẻ, cấu hình khoá, chạy thử công cụ) gộp thành MỘT mục con
// "Kết nối": mở 'conn' / 'config' / 'tools' đều hiện cả ba khối, nút sáng là 'conn' (đường dẫn cũ vẫn dùng được).
const CC_SUBTAB_GROUPS = { conn: ['conn', 'config', 'tools'], config: ['conn', 'config', 'tools'], tools: ['conn', 'config', 'tools'] };
const CC_SUBTAB_BUTTON = { config: 'conn', tools: 'conn' };
// Tên hiển thị của sub-tab cốt lõi (dùng cho ô phụ `cc-int-header-sub`).
// Sub-tab mở rộng lấy tên từ `_ccSubTabConfig[id].title`.
const CC_SUBTAB_LABELS = {
  conn: 'Kết nối ngoại vi (danh sách, cấu hình, chạy thử)',
  departments: 'Quản trị phòng ban (No-Code)',
  config: 'Cấu hình kết nối',
  webhook: 'Webhook',
  tools: 'Công cụ',
  sys: 'Hệ thống',
  devices: 'Máy trạm',
};
// Daftar sub-tab có thể mở rộng bởi enterprise plugins.
let _ccSubTabExtensions = [];
let _ccSubTabConfig = {};

// Đăng ký sub-tab mở rộng từ enterprise plugin.
// Mỗi extension cung cấp: id (ký tự duy nhất), title (hiển thị), loadFn (hàm tải dữ liệu).
function registerCcSubTabExtension(id, cfg) {
  _ccSubTabExtensions.push(id);
  _ccSubTabConfig[id] = cfg;
}


// ═══════════════════════════════════════════════════════════════════════════
// ── DATA SOURCE REGISTRY (Enterprise Reporting Apps) ──────────────────────
// Mục đích: Cho phép tích hợp thêm các ứng dụng doanh nghiệp (ERP, CRM, HR, BI...)
// để lấy dữ liệu báo cáo. Mỗi data source tự định nghĩa:
//   - id: mã định danh duy nhất
//   - title: tên hiển thị
//   - icon: SVG hoặc emoji
//   - color: màu theme
//   - category: 'connector' | 'reporting' | 'analytics' | 'custom'
//   - endpoints: { health, data, config, actions }
//   - renderFn: hàm render UI (tự động gọi khi sub-tab được mở)
//   - subTabId: id của sub-tab nơi hiển thị (tự tạo nếu chưa có)
// ═══════════════════════════════════════════════════════════════════════════

const _ccDataSourceRegistry = {};

// Danh mục mặc định cho data source
const CC_DATA_SOURCE_CATEGORIES = {
  connector: { label: 'Kết Nối', color: 'primary', icon: '🔗', where: 'tab Kết Nối' },
  reporting: { label: 'Báo Cáo', color: 'emerald', icon: '📊', where: 'tab Báo Cáo' },
  analytics: { label: 'Phân Tích', color: 'violet', icon: '📈', where: 'tab Phân Tích' },
  custom: { label: 'Tùy Chỉnh', color: 'amber', icon: '⚙️', where: 'tab Kết Nối' }
};

/**
 * Đăng ký một data source mới (Enterprise App).
 * Ví dụ:
 *   registerCcDataSource('sap-erp', {
 *     title: 'SAP ERP',
 *     icon: '🏢',
 *     color: '#0FAAFF',
 *     category: 'reporting',
 *     endpoints: {
 *       health: '/api/v1/enterprise/sap/health',
 *       data: '/api/v1/enterprise/sap/report',
 *       config: '/api/v1/enterprise/sap/config'
 *     },
 *     renderFn: async (container) => { ... },
 *     subTabId: 'reporting'
 *   });
 */
function registerCcDataSource(id, cfg) {
  if (!cfg || !cfg.title) {
    console.warn('[CC Registry] Data source must have a title');
    return false;
  }
  const defaults = {
    category: 'custom',
    endpoints: {},
    renderFn: null,
    subTabId: 'reporting',
    icon: '📦',
    color: '#64748b',
    description: '',
    actions: []
  };
  _ccDataSourceRegistry[id] = { ...defaults, ...cfg, id };

  // Tự động đăng ký sub-tab nếu chưa có
  if (cfg.subTabId && !_ccSubTabExtensions.includes(cfg.subTabId)) {
    registerCcSubTabExtension(cfg.subTabId, {
      title: CC_DATA_SOURCE_CATEGORIES[cfg.category]?.label || cfg.category,
      loadFn: () => loadCcDataSourceTab(cfg.subTabId)
    });
  }
  return true;
}

/**
 * Lấy danh sách data source theo category.
 */
function getCcDataSourcesByCategory(category) {
  return Object.values(_ccDataSourceRegistry).filter(ds => ds.category === category);
}

/**
 * Lấy data source theo ID.
 */
function getCcDataSource(id) {
  return _ccDataSourceRegistry[id];
}

/**
 * Load tab data source (gọi khi sub-tab được mở).
 */
/**
 * Mẫu ứng dụng doanh nghiệp phổ biến.
 *
 * Người dùng không biết path API của MISA/Odoo là gì, và cũng không nên phải
 * tra tài liệu mỗi khi thêm một app. Mẫu chỉ điền sẵn phần *hình dạng* — đường
 * dẫn tương đối và kiểu xác thực — còn domain + khoá thì khách tự điền vì
 * mỗi hệ thống lại một.
 *
 * Chỉ gồm hệ thống doanh nghiệp nghiệp vụ (kế toán, ERP, CRM). Cố tình KHÔNG
 * có marketplace/pos kiểu Shopee hay KiotViet: đây là hệ thống nội bộ công
 * ty, thêm bán lẻ vào danh sách làm nhiễu màn hình mà không ai dùng tới.
 */
const CC_APP_PRESETS = [
  {
    id: 'misa', label: 'MISA', icon: '📒', base_url: 'https://<ten-cong-ty>.misa.com.vn',
    default_path: '/api/v1/', auth_type: 'basic',
    paths: { 'sổ cái': '/api/v1/hr/payroll', 'tồn kho': '/api/v1/inventory/stock', 'công nợ': '/api/v1/finance/payable' },
    note: 'Kế toán MISA AMH — thay <ten-cong-ty> bằng tenant'
  },
  {
    id: 'odoo', label: 'Odoo', icon: '🧩', base_url: 'https://<domain>.odoo.com',
    default_path: '/json/1', auth_type: 'basic',
    paths: { 'bán hàng': '/json/1/sale.order', 'khách hàng': '/json/1/res.partner', 'kho': '/json/1/stock.quant' },
    note: 'Odoo — user:pass, giao diện JSON-RPC'
  },
  {
    id: 'sap', label: 'SAP', icon: '🏭', base_url: 'https://<host>:44300/sap/opu/odata',
    default_path: '/API_BUSINESS_PARTNER', auth_type: 'basic',
    paths: { 'đối tác': '/API_BUSINESS_PARTNER', 'đơn hàng': '/API_SALES_ORDER' },
    note: 'SAP OData — Basic auth, chứng thư số'
  },
  {
    id: 'dynamics', label: 'Dynamics 365', icon: '🔷', base_url: 'https://<org>.crm.dynamics.com/api/data/v9.2',
    default_path: '/accounts', auth_type: 'bearer',
    paths: { 'khách hàng': '/accounts', 'cơ hội': '/opportunities', 'hóa đơn': '/invoices' },
    note: 'Dynamics 365 / Dataverse — bearer token'
  },
  {
    id: 'zoho', label: 'Zoho', icon: '🟠', base_url: 'https://www.zohoapis.com/crm/v2',
    default_path: '/Accounts', auth_type: 'bearer',
    paths: { 'khách hàng': '/Accounts', 'giao dịch': '/Deals' },
    note: 'Zoho CRM — bearer token'
  },
  {
    id: 'sheets', label: 'Google Sheets', icon: '📗', base_url: 'https://sheets.googleapis.com/v4/spreadsheets',
    default_path: '/<id-file>/values/A1', auth_type: 'bearer',
    paths: { 'dữ liệu': '/<id-file>/values/A1' },
    note: 'Google Sheets — báo cáo nằm trên sheet'
  },
  {
    id: 'erp-noi-bo', label: 'ERP nội bộ', icon: '🏢', base_url: 'http://erp-noi-bo.congty.vn/api',
    default_path: '/reports', auth_type: 'bearer',
    paths: {},
    note: 'Hệ thống tự viết — chỉ cần URL và khoá'
  },
];

/**
 * Vẽ phần nguồn tùy chỉnh trong tab Kết Nối, nằm DƯỚI 4 card connector cốt lõi.
 * Không đụng vào card nào trong HTML — chúng dùng `runConnectorHealth` riêng.
 */
function renderConnCustomSources() {
  // Phase 81: hàm này chỉ còn giữ phần TIÊU ĐỀ + nút thêm.
  //
  // Trước đây nó vẽ luôn danh sách card, tách khỏi 4 connector cốt lõi nằm
  // cứng trong HTML — nên kết nối người dùng tự thêm trông khác hẳn loại cốt
  // lõi, dù server trả về cùng một cấu trúc cho cả hai. Nay `renderConnectionCards()`
  // vẽ chung một lưới, một bố cục.
  let box = _ccGet('cc-conn-custom');
  if (!box) {
    const connPane = _ccGet('cc-int-conn');
    if (!box && !connPane) return;
    box = document.createElement('div');
    box.id = 'cc-conn-custom';
    box.className = 'mt-4 pt-3 border-t border-slate-200 dark:border-slate-700';
    connPane.appendChild(box);
  }

  // Nhóm "connector"/"custom" mà người dùng tự khai = kết nối mới của họ.
  // Nhóm khác (báo cáo/phân tích) đã có chỗ riêng, không nhân bản ở đây.
  const custom = Object.values(_ccDataSourceRegistry).filter(
    ds => ds.isRemote && (ds.category === 'connector' || ds.category === 'custom')
  );

  box.innerHTML = `
    <div class="flex flex-wrap items-center justify-between gap-2">
      <div class="flex items-center gap-2 min-w-0">
        <span class="text-base">🔌</span>
        <h4 class="text-xs font-bold text-slate-800 dark:text-slate-100">Kết nối tùy chỉnh</h4>
        <span class="text-[9px] text-slate-400 dark:text-slate-500">
          ${custom.length ? `${custom.length} nguồn` : 'chưa có nguồn nào'}
        </span>
      </div>
      <div class="flex items-center gap-2">
        ${custom.length ? `
          <button type="button" onclick="toggleCcPresetPicker()"
            class="px-3 py-1.5 text-[10px] font-medium rounded-lg border border-slate-200 dark:border-slate-700 text-slate-600 dark:text-slate-300 hover:bg-slate-50 dark:hover:bg-slate-900/50 transition">
            + Thêm từ mẫu ứng dụng
          </button>` : ''}
        <button type="button" onclick="openAddDataSourceModal('connector')"
          class="px-3 py-1.5 text-[10px] font-semibold rounded-lg bg-primary-600 hover:bg-primary-700 text-white transition">
          + Thêm kết nối
        </button>
      </div>
    </div>
    ${custom.length === 0 ? _ccPresetPicker() : ''}
  `;
}

/** Icon SVG cho connector cốt lõi. Nguồn tùy chỉnh dùng emoji của chính nó. */
const _CC_CONNECTOR_ICON = {
  aws: '<path d="M13 2L3 14h9l-1 8 10-12h-9l1-8z"/>',
  oci: '<path d="M12 2l9 5v10l-9 5-9-5V7l9-5z"/><path d="M12 7v10M7 9.5v5M17 9.5v5"/>',
  paperless: '<path d="M14 2H6a2 2 0 00-2 2v16a2 2 0 002 2h12a2 2 0 002-2V8z"/><polyline points="14 2 14 8 20 8"/>',
  einvoice: '<path d="M9 7h6M9 11h6M9 15h4"/><rect x="4" y="3" width="16" height="18" rx="2"/>',
};
const _CC_CONNECTOR_TINT = {
  aws: 'text-amber-500',
  oci: 'text-red-500',
  paperless: 'text-blue-500',
  einvoice: 'text-emerald-500',
};
const _CC_CONNECTOR_LABEL = {
  aws: 'Cost Explorer · EC2',
  oci: 'Compute · Object Storage',
  paperless: 'Tài liệu OCR',
  einvoice: 'Hóa đơn điện tử',
};

/**
 * Từ card ở tab "Kết Nối" sang đúng khối cấu hình của connector đó.
 *
 * Vì sao cần hàm riêng: trước đây nút "Cấu hình" chỉ gọi
 * `switchCcSubTab('config')` — sang đúng tab nhưng không dẫn tới đúng
 * connector. Người dùng bấm ở card AWS thì phải tự nhìn xem khối nào là
 * của AWS trong 4 khối chồng nhau. Đó là nút trông như dùng được nhưng
 * không dẫn tới đâu, nên nay nó tự cuộn và tô sáng đúng khối.
 */
async function gotoConnectorConfig(name) {
  // Bật cờ TRƯỚC khi chuyển tab: `switchCcSubTab('config')` gọi API rồi mới vẽ
  // khối, nên cờ phải sẵn sàng từ đầu. Đặt sau sẽ tô sáng một phần tử sắp bị
  // thay mới — đúng lỗi đã gặp.
  _ccConfigFocus = name;
  switchCcSubTab('config');

  // Đợi khối thực sự xuất hiện rồi mới cuộn tới. Cuộn trước khi có phần tử
  // sẽ cuộn sai chỗ hoặc không cuộn gì.
  let card = null;
  for (let i = 0; i < 40 && !card; i += 1) {
    card = _ccGet(`${name}-config-card`);
    if (!card) await new Promise((r) => setTimeout(r, 150));
  }
  if (!card) {
    showToast(`Chưa thấy khối cấu hình của ${name.toUpperCase()} — xem thẻ trên.`, 'warning');
    _ccConfigFocus = null;
    return;
  }
  card.scrollIntoView({ behavior: 'smooth', block: 'center' });

  // Bỏ nhấn mạnh sau một lúc, nhưng phải xoá cờ TRƯỚC rồi vẽ lại — bỏ cờ
  // một mình thì lần vẽ sau vẫn còn vòng sáng vĩnh viễn.
  setTimeout(() => {
    _ccConfigFocus = null;
    const again = _ccGet(`${name}-config-card`);
    if (again) renderConnectorForms().then(() => loadConnectorConfigAll());
  }, 3200);
}

/**
 * Vẽ card cho MỌI kết nối ngoại vi — cốt lõi lẫn tùy chỉnh, trong MỘT lưới.
 *
 * Một bộ vẽ cho cả hai loại vì chúng là cùng một thứ: đều là kết nối ra
 * ngoài hệ thống, đều kiểm tra sức khoẻ được, đều cần cấu hình. Trước đây có
 * hai bộ vẽ nên người dùng thêm một ERP của hệ thống rồi thấy nó trông như
 * thuộc loại khác.
 */
function renderConnectionCards() {
  const grid = _ccGet('cc-connector-health-grid');
  if (!grid) return;

  const custom = Object.values(_ccDataSourceRegistry).filter(
    ds => ds.isRemote && (ds.category === 'connector' || ds.category === 'custom')
  );

  const cards = [];

  for (const name of CC_CONNECTORS) {
    const icon = _CC_CONNECTOR_ICON[name] || _CC_CONNECTOR_ICON.einvoice;
    const tint = _CC_CONNECTOR_TINT[name] || _CC_CONNECTOR_TINT.einvoice;
    const miss = _ccBuiltinMissing.get(name) || [];
    cards.push(`
      <div class="connector-card cc-conn-card flex flex-col rounded-xl border border-slate-200 dark:border-slate-700 p-3.5 transition hover:border-slate-300 dark:hover:border-slate-600"
           data-connector="${name}">
        <div class="flex items-start gap-2 mb-2">
          <div class="w-8 h-8 shrink-0 rounded-lg bg-slate-100 dark:bg-slate-800 flex items-center justify-center">
            <svg width="16" height="16" fill="none" stroke="currentColor" stroke-width="2" viewBox="0 0 24 24" class="${tint}">${icon}</svg>
          </div>
          <div class="min-w-0 flex-1">
            <div class="text-xs font-bold text-slate-800 dark:text-slate-100 truncate">${_esc(name.toUpperCase())}</div>
            <div class="text-[9px] text-slate-400 dark:text-slate-500 truncate">${_esc(_CC_CONNECTOR_LABEL[name] || '')}</div>
          </div>
        </div>
        ${miss.length ? `
          <div class="mb-2 px-2 py-1.5 rounded-md bg-amber-50 dark:bg-amber-900/20 border border-amber-200 dark:border-amber-800">
            <p class="text-[9px] text-amber-800 dark:text-amber-200 leading-snug">
              Chưa nhập khoá: <span class="font-mono">${_esc(miss.join(', '))}</span> — mọi lời gọi sẽ thất bại.
            </p>
          </div>` : ''}
        ${(() => {
        const h = _ccConnHealth.get(name);
        if (!h) return `
        <div class="flex items-center gap-1.5 mb-2.5">
          <span class="w-2 h-2 rounded-full bg-slate-300 dark:bg-slate-600 shrink-0" id="${name}-health-indicator"></span>
          <span class="text-[10px] text-slate-500 dark:text-slate-400 truncate" id="${name}-health-text">Chưa kiểm tra</span>
        </div>`;
        return `
        <div class="flex items-center gap-1.5 mb-2.5">
          <span class="w-2 h-2 rounded-full shrink-0 ${h.ok ? 'bg-emerald-500' : 'bg-rose-500'}" id="${name}-health-indicator"></span>
          <span class="text-[10px] truncate ${h.ok ? 'text-emerald-600 dark:text-emerald-400' : 'text-rose-600 dark:text-rose-400'}" id="${name}-health-text">${_esc(h.text)}</span>
        </div>`;
      })()}
        <div class="mt-auto flex items-center gap-1.5">
          <button type="button" onclick="runConnectorHealth('${name}')"
            class="flex-1 px-2 py-1.5 text-[10px] font-medium rounded-lg border border-slate-200 dark:border-slate-700 hover:bg-slate-50 dark:hover:bg-slate-900/50 transition">Kiểm tra</button>
          <button type="button" onclick="gotoConnectorConfig('${name}')"
            class="px-2 py-1.5 text-[10px] font-medium rounded-lg border border-slate-200 dark:border-slate-700 hover:bg-slate-50 dark:hover:bg-slate-900/50 transition"
            title="Sang tab Cấu Hình và tới đúng khối của ${_esc(name.toUpperCase())}">Cấu hình</button>
        </div>
      </div>`);
  }

  for (const ds of custom) {
    const h = _ccConnHealth.get(ds.id);
    cards.push(`
      <div class="connector-card cc-conn-card cc-ds-card flex flex-col rounded-xl border border-slate-200 dark:border-slate-700 p-3.5 transition hover:border-slate-300 dark:hover:border-slate-600"
           data-connector="${_esc(ds.id)}" data-ds-id="${_esc(ds.id)}">
        <div class="flex items-start gap-2 mb-2">
          <div class="w-8 h-8 shrink-0 rounded-lg bg-slate-100 dark:bg-slate-800 flex items-center justify-center text-base leading-none">${ds.icon || '🔌'}</div>
          <div class="min-w-0 flex-1">
            <div class="text-xs font-bold text-slate-800 dark:text-slate-100 truncate">${_esc(ds.title)}</div>
            <div class="text-[9px] text-slate-400 dark:text-slate-500 truncate font-mono">${_esc(ds.baseUrlLabel || '')}</div>
          </div>
          <button type="button" onclick="openAddDataSourceModal('connector', '${_esc(ds.id)}')"
            class="shrink-0 text-slate-400 hover:text-primary-500 transition" title="Sửa cấu hình">
            <svg width="13" height="13" fill="none" stroke="currentColor" stroke-width="2" viewBox="0 0 24 24"><path d="M11 4H4a2 2 0 00-2 2v14a2 2 0 002 2h14a2 2 0 002-2v-7"/><path d="M18.5 2.5a2.121 2.121 0 013 3L12 15l-4 1 1-4 9.5-9.5z"/></svg>
          </button>
        </div>
        ${!ds.hasAuth ? `
          <div class="mb-2 px-2 py-1.5 rounded-md bg-amber-50 dark:bg-amber-900/20 border border-amber-200 dark:border-amber-800">
            <p class="text-[9px] text-amber-800 dark:text-amber-200 leading-snug">Chưa có khoá xác thực — mọi lời gọi sẽ thất bại.</p>
          </div>` : ''}
        <div class="flex items-center gap-1.5 mb-2.5">
          <span class="w-2 h-2 rounded-full shrink-0 ${h ? (h.ok ? 'bg-emerald-500' : 'bg-rose-500') : 'bg-slate-300 dark:bg-slate-600'}"
            id="${_esc(ds.id)}-health" title="${h ? _esc(h.text) : 'Chưa kiểm tra'}"></span>
          <span id="${_esc(ds.id)}-health-text" class="text-[10px] truncate ${h ? (h.ok ? 'text-emerald-600 dark:text-emerald-400' : 'text-rose-600 dark:text-rose-400') : 'text-slate-500 dark:text-slate-400'}">${h ? _esc(h.text) : 'Chưa kiểm tra'}</span>
        </div>
        <div id="${_esc(ds.id)}-preview"></div>
        <div class="mt-auto flex items-center gap-1.5">
          <button type="button" onclick="runDataSourceHealth('${_esc(ds.id)}')"
            class="flex-1 px-2 py-1.5 text-[10px] font-medium rounded-lg border border-slate-200 dark:border-slate-700 hover:bg-slate-50 dark:hover:bg-slate-900/50 transition">Kiểm tra</button>
          <button type="button" onclick="runDataSourceAction('${_esc(ds.id)}', 'sync')"
            class="flex-1 px-2 py-1.5 text-[10px] font-medium rounded-lg bg-primary-600 hover:bg-primary-700 text-white transition disabled:opacity-40">
            Xem dữ liệu
          </button>
        </div>
        ${_ccExportButtons(ds.id)}
      </div>`);
  }

  grid.innerHTML = cards.join('');

  // KHÔNG tự kiểm tra sức khoẻ lúc vẽ. Lý do: lưới được vẽ lại mỗi lần bấm
  // sub-tab, nên tự gọi ở đây là mỗi lần bấm lại lại bắn request ra ngoài —
  // và kết quả mới ghi đè luôn kết quả đã lưu trong `_ccConnHealth`.
  // Người dùng bấm "Kiểm tra" thì mới chạy, như connector cốt lõi vốn vậy.
  //
  // Nguồn chưa từng kiểm tra sẽ hiện "Chưa kiểm tra" — đúng thực trạng, không
  // phải số 0 bịa. Xem `_ccConnHealth` khai báo ở trên.
}

/** Thêm nút sub-tab vào thanh điều hướng, nếu chưa có. */
function _ccAddSubTabButton(subTabId) {
  const subTabBar = document.querySelector('.ml-auto.flex.flex-wrap.items-center.gap-1');
  if (!subTabBar || document.querySelector(`[data-cc-subtab="${subTabId}"]`)) return;

  const btn = document.createElement('button');
  btn.type = 'button';
  btn.setAttribute('data-cc-subtab', subTabId);
  btn.onclick = () => switchCcSubTab(subTabId);
  btn.className = 'px-3 py-1.5 text-[11px] font-medium rounded-lg text-slate-500 dark:text-slate-400 hover:bg-slate-100 dark:hover:bg-slate-700/60 transition';
  const catInfo = CC_DATA_SOURCE_CATEGORIES[subTabId] || { label: subTabId, icon: '📦' };
  btn.innerHTML = '<span class="text-xl mr-1">' + catInfo.icon + '</span> ' + catInfo.label;
  subTabBar.appendChild(btn);
}

/** Dải mẫu ứng dụng, hiện khi chưa có kết nối tùy chỉnh nào. */
function _ccPresetPicker() {
  return `
    <p class="text-[10px] text-slate-500 dark:text-slate-400 mb-2.5 leading-relaxed">
      Bấm một mẫu để điền sẵn đường dẫn và kiểu xác thực — chỉ cần sửa domain và
      khoá của hệ thống bạn đang dùng. Muốn tự khai từ đầu thì bấm
      <strong>+ Thêm kết nối</strong>.
    </p>
    <div class="grid grid-cols-2 sm:grid-cols-3 gap-2">
      ${CC_APP_PRESETS.map(p => `
        <button type="button" onclick="applyCcPreset('${p.id}')" title="${_esc(p.note)}"
          class="cc-preset-btn flex items-center gap-2 px-2.5 py-2 rounded-lg border border-slate-200 dark:border-slate-700 bg-white dark:bg-slate-800/60 hover:border-primary-400 dark:hover:border-primary-600 hover:bg-primary-50 dark:hover:bg-primary-900/20 transition text-left">
          <span class="text-base shrink-0">${p.icon}</span>
          <span class="min-w-0">
            <span class="block text-[10px] font-bold text-slate-700 dark:text-slate-200 truncate">${_esc(p.label)}</span>
            <span class="block text-[8px] text-slate-400 dark:text-slate-500 truncate">${_esc(p.note)}</span>
          </span>
        </button>`).join('')}
    </div>
    <button type="button" onclick="openAddDataSourceModal('connector')"
      class="mt-3 text-[10px] font-medium text-primary-600 dark:text-primary-400 hover:underline transition">
      Không có trong danh sách? Tự khai từ đầu →
    </button>`;
}

/** Mở/ẩy dải mẫu khi đã có ít nhất một kết nối tùy chỉnh. */
function toggleCcPresetPicker() {
  const box = _ccGet('cc-conn-custom');
  if (!box) return;
  const existing = box.querySelector('.cc-preset-grid');
  if (existing) { existing.remove(); return; }

  const grid = document.createElement('div');
  grid.className = 'cc-preset-grid mt-3';
  grid.innerHTML = `<div class="grid grid-cols-2 sm:grid-cols-3 gap-2">
    ${CC_APP_PRESETS.map(p => `
      <button type="button" onclick="applyCcPreset('${p.id}')" title="${_esc(p.note)}"
        class="flex items-center gap-2 px-2.5 py-2 rounded-lg border border-slate-200 dark:border-slate-700 bg-white dark:bg-slate-800/60 hover:border-primary-400 dark:hover:border-primary-600 transition text-left">
        <span class="text-base shrink-0">${p.icon}</span>
        <span class="min-w-0">
          <span class="block text-[10px] font-bold text-slate-700 dark:text-slate-200 truncate">${_esc(p.label)}</span>
          <span class="block text-[8px] text-slate-400 dark:text-slate-500 truncate">${_esc(p.note)}</span>
        </span>
      </button>`).join('')}
  </div>`;
  box.appendChild(grid);
}

/**
 * Mở modal đã điền sẵn từ mẫu ứng dụng.
 * Mã nguồn sinh từ tên mẫu để không phải gõ tay, nhưng vẫn sửa được trong modal.
 */
function applyCcPreset(presetId) {
  const preset = CC_APP_PRESETS.find(p => p.id === presetId);
  if (!preset) {
    showToast('Không tìm thấy mẫu ứng dụng này', 'error');
    return null;
  }

  const paths = {};
  Object.entries(preset.paths || {}).forEach(([k, v]) => { paths[k] = v; });

  return _ccDataSourceModal({
    id: preset.id,
    title: preset.label,
    description: preset.note,
    category: 'connector',
    base_url: preset.base_url,
    default_path: preset.default_path,
    auth_type: preset.auth_type,
    auth_header: preset.auth_header || 'X-Api-Key',
    auth_query: preset.auth_query || 'api_key',
    method: 'GET',
    timeout_seconds: 10,
    row_limit: 50,
    enabled: true,
    has_auth: false,
  });
}

/**
 * Khung cho nhóm chưa có nguồn nào — vẫn phải hiện nút thêm, nếu không người
 * dùng mới vào app sẽ thấy nhóm trống trơn mà không có lối vào.
 */
function renderEmptyDataSourceTab(subTabId) {
  let container = _ccGet(`cc-int-${subTabId}`);
  if (!container) {
    const mainPane = _ccGet('tab-system-integration');
    if (!mainPane) return;
    container = document.createElement('div');
    container.id = `cc-int-${subTabId}`;
    container.className = 'p-4 hidden';
    mainPane.appendChild(container);
    if (!_ccSubTabExtensions.includes(subTabId)) _ccSubTabExtensions.push(subTabId);
    if (!CC_SUBTABS.includes(subTabId)) CC_SUBTABS.push(subTabId);
    _ccAddSubTabButton(subTabId);
  }
  if (!container || container.dataset.emptyRendered === '1') return;

  const cat = CC_DATA_SOURCE_CATEGORIES[subTabId] || { label: subTabId, icon: '📦' };
  container.dataset.emptyRendered = '1';
  container.innerHTML = `
    <div class="flex flex-wrap items-center justify-between gap-2 mb-4">
      <div class="flex items-center gap-2">
        <span class="text-xl">${cat.icon}</span>
        <h3 class="text-sm font-bold text-slate-800 dark:text-slate-100">${_esc(cat.label)}</h3>
        <span class="text-[9px] text-slate-400 dark:text-slate-500">chưa có nguồn dữ liệu</span>
      </div>
      <button type="button" onclick="openAddDataSourceModal('${_esc(subTabId)}')"
        class="px-3 py-1.5 text-[10px] font-semibold rounded-lg bg-primary-600 hover:bg-primary-700 text-white transition">
        + Thêm nguồn dữ liệu
      </button>
    </div>`;
}

/**
 * Phase 85: các sub-tab có HTML viết tay trong `index.html`.
 *
 * `loadCcDataSourceTab()` là hàm vẽ các NGUỒN DỮ LIỆU. Trước đây nó nhận bất
 * kỳ tên sub-tab nào, và gọi nó với tên đang mở là xoá sạch pane đó:
 *
 *   `initCcDataSourceRegistry()` → `syncRemoteDataSources().then(...)` đọc
 *   sub-tab đang active rồi gọi lại `loadCcDataSourceTab(active)`. `sync` là
 *   lời gọi mạng nên nó xong SAU khi người dùng đã bấm sang Máy Trạm; nhóm
 *   'devices' không có nguồn dữ liệu nào → `renderEmptyDataSourceTab('devices')`
 *   → toàn bộ bảng máy trạm bị thay bằng "chưa có nguồn dữ liệu".
 *
 *   Cùng lỗi ở `saveCcDataSource()`: lưu xong nguồn dữ liệu thì mở lại sub-tab
 *   đang xem — nếu đang xem Máy Trạm thì xoá luôn.
 *
 * Vì là lời gọi bất đồng bộ nên lúc nào cũng có lúc bị, lúc không: đúng kiểu
 * "chạy được nhưng không ổn định" khó nhất. Chặn ở đúng một chỗ
 * (`loadCcDataSourceTab`) thay vì vá từng nơi gọi — sau này thêm nơi gọi thứ ba
 * thì vẫn an toàn.
 */
const CC_HANDWRITTEN_SUBTABS = ['config', 'webhook', 'tools', 'sys', 'devices', 'departments'];

async function loadCcDataSourceTab(subTabId) {
  // Chặn trước mọi thứ: không vẽ nguồn dữ liệu lên pane viết tay (xem
  // CC_HANDWRITTEN_SUBTABS để biết vì sao). Phải trả về `undefined` chứ không
  // phải lỗi, vì hai nơi gọi đều gọi không await.
  if (CC_HANDWRITTEN_SUBTABS.includes(subTabId)) return;

  // Phase 81: tab 'conn' KHÔNG còn 4 card viết cứng trong HTML nữa — cả 4
  // connector cốt lõi lẫn nguồn tùy chỉnh đều do `renderConnectionCards()`
  // vẽ vào cùng một lưới, nên kết nối người dùng tự thêm trông giống hệt
  // loại cốt lõi. Hàm này chỉ còn dựng phần tiêu đề + nút thêm.
  //
  // Phải gọi ở ĐÂY chứ không thêm vào `switchCcSubTab`: 'conn' nằm trong
  // `_ccSubTabExtensions` nên nó đi nhánh `loadFn` chứ không tới nhánh tab
  // cốt lõi — thêm nhánh còn lại sẽ không bao giờ chạy.
  if (subTabId === 'conn') {
    renderConnCustomSources();
    return renderConnectionCards();
  }

  const sources = getCcDataSourcesByCategory(subTabId);
  // Nhóm không có nguồn nào: vẫn giữ lại khung + nút thêm, để người dùng
  // biết chỗ này dùng để làm gì. Trả sớm sẽ khiến nhóm biến mất lúc khởi
  // tạo, và không có lối vào để thêm nguồn đầu tiên.
  if (sources.length === 0) return renderEmptyDataSourceTab(subTabId);

  // Get or create container
  let container = _ccGet(`cc-int-${subTabId}`);
  if (!container) {
    const mainPane = _ccGet('tab-system-integration');
    if (!mainPane) return;

    container = document.createElement('div');
    container.id = `cc-int-${subTabId}`;
    container.className = 'p-4 hidden';
    mainPane.appendChild(container);

    // Register the sub-tab if not already registered
    if (!_ccSubTabExtensions.includes(subTabId)) {
      _ccSubTabExtensions.push(subTabId);
    }
    if (!CC_SUBTABS.includes(subTabId)) {
      CC_SUBTABS.push(subTabId);
    }

    // Add button to the sub-tab bar if not exists
    _ccAddSubTabButton(subTabId);
  }

  if (!container) return;

  // Render header
  const catInfo = CC_DATA_SOURCE_CATEGORIES[subTabId] || { label: subTabId, icon: '📦', color: 'slate' };
  container.innerHTML = `
    <div class="flex flex-wrap items-center justify-between gap-2 mb-4">
      <div class="flex items-center gap-2">
        <span class="text-xl">${catInfo.icon}</span>
        <h3 class="text-sm font-bold text-slate-800 dark:text-slate-100">${catInfo.label}</h3>
        <span class="text-[9px] text-slate-400 dark:text-slate-500">${sources.length} nguồn dữ liệu</span>
      </div>
      <button type="button" onclick="openAddDataSourceModal('${subTabId}')"
        class="px-3 py-1.5 text-[10px] font-semibold rounded-lg bg-primary-600 hover:bg-primary-700 text-white transition">
        + Thêm nguồn dữ liệu
      </button>
    </div>
    <div class="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-3" id="cc-ds-grid-${subTabId}"></div>
  `;

  const grid = _ccGet(`cc-ds-grid-${subTabId}`);
  if (!grid) return;

  // Render each data source card
  for (const ds of sources) {
    const card = document.createElement('div');
    card.className = 'cc-ds-card rounded-xl border border-slate-200 dark:border-slate-700 p-3.5 bg-white dark:bg-slate-800/60';
    card.dataset.dsId = ds.id;
    card.innerHTML = `
      <div class="flex items-start justify-between gap-2 mb-2">
        <div class="flex items-center gap-2 min-w-0">
          <span class="text-xl shrink-0">${ds.icon}</span>
          <div class="min-w-0">
            <div class="text-xs font-bold text-slate-800 dark:text-slate-100 truncate">${_esc(ds.title)}</div>
            <div class="text-[9px] text-slate-400 dark:text-slate-500 truncate">${_esc(ds.description || 'Nguồn dữ liệu doanh nghiệp')}</div>
          </div>
        </div>
        <div class="flex items-center gap-1.5 shrink-0">
          ${ds.isRemote ? `<button type="button" onclick="openAddDataSourceModal('${_esc(ds.category || 'custom')}', '${_esc(ds.id)}')"
            class="text-slate-400 hover:text-primary-500 transition" title="Sửa cấu hình nguồn này">
            <svg width="13" height="13" fill="none" stroke="currentColor" stroke-width="2" viewBox="0 0 24 24"><path d="M11 4H4a2 2 0 00-2 2v14a2 2 0 002 2h14a2 2 0 002-2v-7"/><path d="M18.5 2.5a2.121 2.121 0 013 3L12 15l-4 1 1-4 9.5-9.5z"/></svg>
          </button>` : ''}
          <span class="w-2 h-2 rounded-full bg-slate-300 dark:bg-slate-600" id="${ds.id}-health"></span>
        </div>
      </div>

      ${ds.isRemote && !ds.hasAuth ? `
        <div class="mb-2 px-2 py-1.5 rounded-md bg-amber-50 dark:bg-amber-900/20 border border-amber-200 dark:border-amber-800">
          <p class="text-[9px] text-amber-800 dark:text-amber-200 leading-snug">Chưa có khoá xác thực — mọi lời gọi sẽ thất bại.</p>
        </div>` : ''}

      ${ds.availablePaths && ds.availablePaths.length > 0 ? `
        <div class="mb-2 flex flex-wrap gap-1">
          ${ds.availablePaths.map(p => `<button type="button" onclick="previewDataSource('${_esc(ds.id)}', '${_esc(p)}')"
            class="px-1.5 py-0.5 text-[9px] rounded bg-slate-100 dark:bg-slate-700/60 text-slate-600 dark:text-slate-300 hover:bg-primary-100 dark:hover:bg-primary-900/30 transition">${_esc(p)}</button>`).join('')}
        </div>` : ''}

      <div id="${ds.id}-preview" class="mb-2"></div>

      <div class="flex items-center gap-2">
        <button type="button" onclick="runDataSourceHealth('${ds.id}')"
          class="flex-1 px-2 py-1.5 text-[10px] font-medium rounded-lg border border-slate-200 dark:border-slate-700 hover:bg-slate-50 dark:hover:bg-slate-900/50 transition">
          Kiểm tra
        </button>
        <button type="button" onclick="runDataSourceAction('${ds.id}', 'sync')"
          class="flex-1 px-2 py-1.5 text-[10px] font-medium rounded-lg bg-primary-600 hover:bg-primary-700 text-white transition disabled:opacity-40 disabled:cursor-not-allowed"
          ${!ds.endpoints.data ? 'disabled' : ''}>
          Xem dữ liệu
        </button>
      </div>
      ${ds.actions && ds.actions.length > 0 ? `
        <div class="mt-2 flex flex-wrap gap-1">
          ${ds.actions.map(a => `<button type="button" onclick="runDataSourceAction('${ds.id}', '${a.id}')" class="px-2 py-1 text-[9px] rounded bg-slate-100 dark:bg-slate-700/60 text-slate-600 dark:text-slate-300 hover:bg-primary-100 dark:hover:bg-primary-900/30 transition">${_esc(a.label)}</button>`).join('')}
        </div>
      ` : ''}
      ${_ccExportButtons(ds.id)}
    `;
    grid.appendChild(card);
  }

  // Auto-run health check for all
  for (const ds of sources) {
    runDataSourceHealth(ds.id);
  }
}

/**
 * Dựng bảng xem trước từ `{rows, columns, total}` mà server chuẩn hoá về.
 *
 * Server đã cắt bảng và báo `truncated`, nên phải nói rõ "còn N dòng" — hiện
 * mấy dòng mà không nói tổng sẽ khiến người đọc tưởng đó là toàn bộ báo cáo.
 */
function _ccRenderPreview(container, data, sourceTitle) {
  if (!container) return;
  const rows = Array.isArray(data?.rows) ? data.rows : [];
  const columns = Array.isArray(data?.columns) && data.columns.length
    ? data.columns
    : Object.keys(rows[0] || {}).slice(0, 8);

  if (!rows.length) {
    container.innerHTML = `<p class="text-[9px] text-slate-400 dark:text-slate-500">${_esc(sourceTitle)}: không có bản ghi nào.</p>`;
    return;
  }

  const head = columns
    .map(c => `<th class="px-2 py-1 text-left font-semibold text-slate-500 dark:text-slate-400 whitespace-nowrap">${_esc(c)}</th>`)
    .join('');
  const body = rows.slice(0, 8).map(row => `
    <tr class="border-t border-slate-100 dark:border-slate-700/60">
      ${columns.map(c => `<td class="px-2 py-1 text-slate-700 dark:text-slate-300 max-w-[160px] truncate">${_esc(_ccCell(row?.[c]))}</td>`).join('')}
    </tr>`).join('');

  const total = data.total ?? rows.length;
  const shown = data.returned ?? rows.length;
  const more = data.truncated && total > shown
    ? `<span class="text-amber-600 dark:text-amber-400">đang hiện ${shown}/${total} dòng</span>`
    : `<span>${total} dòng</span>`;

  container.innerHTML = `
    <div class="rounded-lg border border-slate-200 dark:border-slate-700 overflow-hidden">
      <div class="px-2 py-1 bg-slate-50 dark:bg-slate-700/40 text-[9px] text-slate-500 dark:text-slate-400 flex justify-between gap-2">
        <span class="truncate font-medium">${_esc(sourceTitle)}</span>${more}
      </div>
      <div class="overflow-x-auto">
        <table class="w-full text-[9px]">
          <thead class="bg-slate-50 dark:bg-slate-700/40">${head ? `<tr>${head}</tr>` : ''}</thead>
          <tbody>${body}</tbody>
        </table>
      </div>
    </div>`;
}

/**
 * Tải dữ liệu nguồn về máy dưới dạng Excel (.xlsx) hoặc CSV.
 *
 * Xuất ở server chứ không dựng file ngay trong trình duyệt:
 *   - CSV do Excel mở cần BOM UTF-8, nếu không tiếng Việt ra ký tự lỗi;
 *   - không phải kéo thêm thư viện ~1MB vào trang.
 *
 * Số dòng xuất mặc định 1000, nhiều hơn hẳn con số 8 dòng xem trước trên
 * màn hình — xuất là để đưa đi xử lý, không phải để ngắm.
 */
async function exportDataSource(id, format, previewPath) {
  const ds = _ccDataSourceRegistry[id];
  if (!ds || !ds.endpoints.data) return;

  const exportUrl = `/api/v1/enterprise/data-sources/${encodeURIComponent(id)}/export`;
  const label = format === 'csv' ? 'CSV' : 'Excel';

  // Nút nào bấm thì hiện trạng thái ngay trên nút đó, không chỉ chung.
  const btns = Array.from(document.querySelectorAll(`[data-export-for="${id}"]`))
    .filter(b => b.dataset.exportFmt === format);
  const original = btns.map(b => b.innerHTML);
  btns.forEach(b => { b.disabled = true; b.textContent = '…'; });

  try {
    const res = await apiFetch(`${API_BASE}${exportUrl}`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', 'Authorization': `Bearer ${getAuthToken()}` },
      // `title` để file tải về mang tên nguồn thật. Không gửi thì server rơi
      // về mã kỹ thuật ("erp-abc"), người dùng phải tự đổi tên sau khi mở.
      body: JSON.stringify({
        format,
        title: ds.title,
        path: previewPath || undefined,
        limit: 1000
      })
    });

    const ctype = res.headers?.get?.('content-type') || '';
    // Server trả JSON khi lỗi, trả file khi thành công. Phân biệt bằng
    // content-type vì HTTP status vẫn là 200 trong cả hai trường hợp.
    if (!ctype.includes('spreadsheet') && !ctype.includes('csv')) {
      let msg = `HTTP ${res.status}`;
      try { msg = (await res.json()).error || msg; } catch (_) { /* không phải JSON */ }
      throw new Error(msg);
    }

    const blob = await res.blob();
    const name = _ccFileNameFromDisposition(
      res.headers?.get?.('content-disposition') || '', ds.title, format
    );
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = name;
    document.body.appendChild(a);
    a.click();
    a.remove();
    // Giải phóng object URL sau khi trình duyệt đã nhận — bỏ sớm thì tải
    // về được file rỗng ở một số trình duyệt.
    setTimeout(() => URL.revokeObjectURL(url), 10000);

    showToast(`✔ Đã tải "${name}" (${label})`, 'success');
  } catch (err) {
    showToast(`✖ Xuất ${label} thất bại: ${err.message}`, 'error');
  } finally {
    btns.forEach((b, i) => { b.disabled = false; b.innerHTML = original[i]; });
  }
}

/** Rút tên file từ header `Content-Disposition`, có đuôi làm dự phòng. */
function _ccFileNameFromDisposition(header, fallbackTitle, format) {
  const ext = format === 'csv' ? 'csv' : 'xlsx';
  // Ưu tiên `filename*` (UTF-8) — tên có dấu mà người dùng Việt thấy quen thuộc.
  const utf8 = /filename\*=UTF-8''([^;]+)/i.exec(header);
  if (utf8) {
    try { return decodeURIComponent(utf8[1]); } catch (_) { /* rơi xuống bản ASCII */ }
  }
  const ascii = /filename="?([^";]+)"?/i.exec(header);
  if (ascii) return ascii[1];
  return `${_esc(fallbackTitle || 'bao-cao')}.${ext}`;
}

/** Cặp nút xuất Excel / CSV, dùng lại cho mọi card. */
function _ccExportButtons(id) {
  return `
    <div class="flex items-center gap-1.5 mt-2 pt-2 border-t border-slate-100 dark:border-slate-700/60">
      <span class="text-[9px] text-slate-400 dark:text-slate-500">Xuất:</span>
      <button type="button" data-export-for="${_esc(id)}" data-export-fmt="xlsx"
        onclick="exportDataSource('${_esc(id)}', 'xlsx')"
        class="px-2 py-1 text-[9px] font-medium rounded bg-emerald-50 dark:bg-emerald-900/25 text-emerald-700 dark:text-emerald-300 hover:bg-emerald-100 dark:hover:bg-emerald-900/40 transition">
        Excel
      </button>
      <button type="button" data-export-for="${_esc(id)}" data-export-fmt="csv"
        onclick="exportDataSource('${_esc(id)}', 'csv')"
        class="px-2 py-1 text-[9px] font-medium rounded bg-slate-100 dark:bg-slate-700/60 text-slate-600 dark:text-slate-300 hover:bg-slate-200 dark:hover:bg-slate-600/60 transition">
        CSV
      </button>
    </div>`;
}

// ═══════════════════════════════════════════════════════════════════════════
// ── Phase 64: Tự động tải file AI đã dựng ─────────────────────────────────
//
// AI xuất báo cáo xong sẽ xếp file vào hàng đợi trên máy chủ. Giao diện poll
// hàng đợi rồi tự tải về — người dùng chỉ cần ra lệnh, không phải bấm gì.
//
// Vì sao không đưa link có token cho người dùng:
//   - link đó cần Bearer token, trình duyệt không gắn token khi bấm link
//     thường -> đã kiểm chứng là 401;
//   - link còn nằm trong lịch sử hội thoại và được gửi lại cho nhà cung cấp
//     LLM ở lượt sau, tức rò bí mật ra ngoài dù token có hạn.
// Ở đây dùng `job_id` — mã ngẫu nhiên, không phải bí mật.
// ═══════════════════════════════════════════════════════════════════════════

/** Các job đã xử lý, để không tải trùng mỗi lần poll. */
const _ccDownloadedJobs = new Set();

/**
 * Tải một job về máy.
 *
 * Gọi bằng phiên đăng nhập sẵn có (Bearer token trong localStorage) — cùng
 * cơ chế với mọi lệnh gọi khác, không cần mở tab mới.
 */
async function _ccDownloadJob(job) {
  try {
    const res = await apiFetch(
      `${API_BASE}/api/v1/enterprise/downloads/${encodeURIComponent(job.job_id)}`,
      { headers: { 'Authorization': `Bearer ${getAuthToken()}` } }
    );

    if (!res.ok) {
      // Job hết hạn giữa chừng là chuyện bình thường, không phải lỗi hệ thống.
      let msg = `HTTP ${res.status}`;
      try { msg = (await res.json()).error || msg; } catch (_) { /* không phải JSON */ }
      showToast(`⚠ Không tải được "${job.filename}": ${msg}`, 'error');
      return;
    }

    const blob = await res.blob();
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = job.filename;
    document.body.appendChild(a);
    a.click();
    a.remove();
    // Giải phóng muộn: thả sớm thì một số trình duyệt tải về file rỗng.
    setTimeout(() => URL.revokeObjectURL(url), 15000);

    showToast(
      `✔ Đã tải "${job.filename}" — ${job.row_count} dòng, định dạng ${(job.format || '').toUpperCase()}`,
      'success'
    );
  } catch (err) {
    showToast(`✖ Tải file lỗi: ${err.message}`, 'error');
  }
}

/**
 * Kiểm tra hàng đợi tải và tự động tải những file mới.
 *
 * Gọi sau mỗi câu trả lời của AI. Job đã xử lý được đánh dấu nên poll lặp lại
 * không tải trùng — nếu không, mỗi vòng poll sẽ tải lại cùng một file.
 */
async function pollAiDownloads() {
  try {
    const res = await apiFetch(`${API_BASE}/api/v1/enterprise/downloads/pending`, {
      headers: { 'Authorization': `Bearer ${getAuthToken()}` }
    });
    const d = await res.json().catch(() => ({}));
    if (!res.ok || d?.status === 'error' || !Array.isArray(d.jobs)) return;

    for (const job of d.jobs) {
      if (!job?.job_id || _ccDownloadedJobs.has(job.job_id)) continue;
      _ccDownloadedJobs.add(job.job_id);
      await _ccDownloadJob(job);
    }
  } catch (_) {
    // Im lặng: không có mạng thì không tải được, nhưng người dùng vẫn đọc
    // được câu trả lời của AI. Báo lỗi ở đây sẽ thành nhiễu mỗi lượt chat.
  }
}

/**
 * Gọi sau khi AI trả lời: chờ một nhịp cho tool chạy xong rồi kiểm tra hàng đợi.
 *
 * Tool `prepare_data_source_export` đẩy job vào hàng đợi ngay trước khi LLM
 * sinh câu trả lời, nên khi câu trả lời tới nơi thì job đã có mặt. Nhịp chờ
 * ngắn chỉ để chắc chắn thứ tự mạng không đảo chiều.
 */
function scheduleAiDownloadCheck() {
  setTimeout(pollAiDownloads, 700);
}

/** Rút giá trị ô về chuỗi ngắn, gọn — bảng báo cáo thường có object lồng. */
function _ccCell(v) {
  if (v === null || v === undefined) return '';
  if (typeof v === 'object') return JSON.stringify(v);
  const s = String(v);
  return s.length > 60 ? `${s.slice(0, 60)}…` : s;
}

/**
 * Xem trước dữ liệu của một nguồn, gọi tới `previewPath` (nếu có).
 * Kết quả chèn thẳng vào card tương ứng.
 */
async function previewDataSource(id, previewPath) {
  const ds = _ccDataSourceRegistry[id];
  if (!ds || !ds.endpoints.data) return;

  const box = _ccGet(`${id}-preview`);
  if (box) box.innerHTML = '<p class="text-[9px] text-slate-400">Đang tải dữ liệu…</p>';

  try {
    const res = await apiFetch(`${API_BASE}${ds.endpoints.data}`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', 'Authorization': `Bearer ${getAuthToken()}` },
      body: JSON.stringify(previewPath ? { path: previewPath } : {})
    });
    const d = await res.json().catch(() => ({}));

    if (d?.status === 'error' || !res.ok) {
      if (box) box.innerHTML = `<p class="text-[9px] text-rose-600 dark:text-rose-400 leading-snug">${_esc(d?.error || `HTTP ${res.status}`)}</p>`;
      return;
    }
    _ccRenderPreview(_ccGet(`${id}-preview`), d.data, ds.title);
  } catch (err) {
    if (box) box.innerHTML = `<p class="text-[9px] text-rose-600 dark:text-rose-400">${_esc(err.message)}</p>`;
  }
}

/**
 * Chốt kết luận sức khoẻ từ phản hồi của endpoint.
 *
 * Tách riêng để kiểm thử được: đây là chỗ dễ sai nhất của màn hình này.
 * Endpoint probe trả `{"status":"success","healthy":false}` khi app đã chết —
 * `status: success` chỉ có nghĩa "probe đã chạy xong". Đọc `status` trước sẽ
 * đánh dấu xanh cho cả app đã tắt, tức báo cáo sai lệch khiến người vận
 * hành tin nhầm là hệ thống đang chạy.
 */
function _ccHealthVerdict(payload, httpOk) {
  const d = payload || {};
  const hasVerdict = typeof d.healthy === 'boolean' || typeof d.ok === 'boolean';
  const success = hasVerdict ? (d.healthy ?? d.ok) === true : d.status === 'success';
  let reason = d.error || d.detail;
  if (!reason) reason = httpOk === false ? 'Máy chủ từ chối yêu cầu' : 'Thất bại';
  return { success: success === true, reason: String(reason).slice(0, 120) };
}

/**
 * Kiểm tra sức khoẻ một data source.
 */
async function runDataSourceHealth(id) {
  const ds = _ccDataSourceRegistry[id];
  if (!ds || !ds.endpoints.health) return;

  const indicator = _ccGet(`${id}-health`);
  // Phase 81: cập nhật CẢ chữ chứ không chỉ màu chấm. Trước đây hàm này chỉ
  // đổi `className` của chấm và đặt `title`, còn dòng chữ "Chưa kiểm tra" là
  // HTML tĩnh — nên bấm "Kiểm tra" xong nhìn vào không có gì thay đổi, tưởng
  // nút hỏng. Chấm đổi màu mà chữ vẫn "chưa kiểm tra" là hai nơi nói hai lệch.
  const textEl = _ccGet(`${id}-health-text`);
  if (indicator) {
    indicator.className = 'w-2 h-2 rounded-full bg-amber-400 animate-pulse';
    indicator.title = 'Đang kiểm tra…';
  }
  if (textEl) {
    textEl.textContent = 'Đang kiểm tra…';
    textEl.className = 'text-[10px] truncate text-amber-600 dark:text-amber-400';
  }

  try {
    // Probe của data source tùy chỉnh là POST (nó gọi ra app ngoài — GET sẽ
    // khiến proxy/cache tự kích hoạt). Nguồn chỉ đọc trạng thái vẫn là GET.
    const res = await apiFetch(`${API_BASE}${ds.endpoints.health}`, {
      method: ds.healthMethod || 'GET',
      headers: { 'Authorization': `Bearer ${getAuthToken()}` }
    });
    const d = await res.json().catch(() => ({}));
    const { success, reason } = _ccHealthVerdict(d, res.ok);

    if (indicator) {
      indicator.className = success
        ? 'w-2 h-2 rounded-full bg-emerald-500'
        : 'w-2 h-2 rounded-full bg-rose-500';
      indicator.title = success ? 'OK' : reason;
    }
    if (textEl) {
      textEl.textContent = success ? 'OK' : reason;
      textEl.className = 'text-[10px] truncate ' + (success
        ? 'text-emerald-600 dark:text-emerald-400'
        : 'text-rose-600 dark:text-rose-400');
    }
    _ccConnHealth.set(id, { ok: success, text: success ? 'OK' : reason, dot: true });
  } catch (err) {
    if (indicator) {
      indicator.className = 'w-2 h-2 rounded-full bg-rose-500';
      indicator.title = `Lỗi: ${err.message}`;
    }
    if (textEl) {
      textEl.textContent = `Lỗi: ${err.message}`;
      textEl.className = 'text-[10px] truncate text-rose-600 dark:text-rose-400';
    }
    _ccConnHealth.set(id, { ok: false, text: `Lỗi: ${err.message}`, dot: true });
  } finally {
    // Ô KPI đếm cả nguồn tùy chỉnh, nên kiểm tra xong phải cập nhật — thiếu
    // bước này thì bấm "Kiểm tra" trên nguồn tùy chỉnh, KPI vẫn giữ nguyên
    // "chờ / chưa kiểm tra" dù đã có kết quả. `runConnectorHealth` đã có
    // bước này từ trước; đây là chỗ tương ứng bị bỏ sót.
    syncIntegrationKpi();
  }
}

/**
 * Chạy action trên data source (sync, custom action...).
 */
async function runDataSourceAction(id, actionId) {
  const ds = _ccDataSourceRegistry[id];
  if (!ds) return;

  // Action mặc định: xem dữ liệu. Card có sẵn khung xem trước nên kết quả
  // hiện tại chỗ — toast chỉ để báo lỗi, không thay cho dữ liệu.
  if (actionId === 'sync' && ds.endpoints.data) {
    await previewDataSource(id);
  }

  // Action riêng do từng nguồn định nghĩa (Phase 59 connector dùng cái này).
  const action = ds.actions?.find(a => a.id === actionId);
  if (action && action.handler) {
    await action.handler(ds);
  }
}

/**
 * Mở modal thêm data source mới (chỉ manager+).
 */
// ═══════════════════════════════════════════════════════════════════════════
// ── PHASE 62: UI quản lý data source tùy chỉnh ─────────────────────────────
// Điền form → POST /api/v1/enterprise/data-sources → nguồn mới hiện ngay.
// Không cần sửa code cho mỗi app doanh nghiệp mới.
// ═══════════════════════════════════════════════════════════════════════════

/** Bảng tra cứu nhãn tiếng Việt cho các kiểu xác thực. */
const CC_AUTH_TYPE_LABELS = {
  none: 'Không cần (app công khai)',
  bearer: 'Bearer Token',
  basic: 'Basic (user:pass)',
  header: 'Header tuỳ chỉnh',
  query: 'Tham số trên URL',
  oauth2_client: 'OAuth2 (client credentials) — khai báo trong ô nâng cao',
  login: 'Đăng nhập lấy token (vCenter, GLPI, Veeam…) — khai báo trong ô nâng cao'
};

/** Các trường khai báo nâng cao nằm trong ô JSON (không có ô riêng trên form). */
const CC_DS_ADVANCED_KEYS = ['kind', 'connection', 'login', 'oauth2', 'pagination', 'queries', 'actions', 'rows_path',
  'health_path', 'extra_headers', 'verify_ssl', 'ca_bundle', 'client_cert', 'client_key', 'max_rows'];

let _ccInfraPresets = [];
async function _ccLoadInfraPresets() {
  try {
    const res = await apiFetch(`${API_BASE}/api/v1/enterprise/data-sources/presets`, {
      headers: { 'Authorization': `Bearer ${getAuthToken()}` }
    });
    const d = await res.json().catch(() => ({}));
    _ccInfraPresets = Array.isArray(d?.presets) ? d.presets : [];
  } catch (_) { _ccInfraPresets = []; }
  const sel = document.getElementById('cc-ds-preset');
  if (sel && _ccInfraPresets.length) {
    sel.innerHTML = '<option value="">— Tự khai báo —</option>' + _ccInfraPresets
      .map(p => `<option value="${_esc(p.key)}">${_esc(p.group)} · ${_esc(p.label)}</option>`).join('');
  }
}

/** Điền form từ mẫu hạ tầng: chỉ còn phải nhập địa chỉ máy chủ + khoá. */
function applyCcInfraPreset(key) {
  const p = _ccInfraPresets.find(x => x.key === key);
  const note = document.getElementById('cc-ds-preset-note');
  if (!p) { if (note) note.textContent = ''; return; }
  const d = p.declaration || {};
  const set = (id, v) => { const el = document.getElementById(id); if (el && v !== undefined && v !== null) el.value = v; };
  if (!document.getElementById('cc-ds-id')?.disabled && !document.getElementById('cc-ds-id').value) set('cc-ds-id', p.key);
  set('cc-ds-title', d.title || p.label);
  set('cc-ds-auth-type', d.auth_type || 'none');
  set('cc-ds-category', d.category || 'connector');
  set('cc-ds-default-path', d.default_path || '/');
  const base = document.getElementById('cc-ds-base-url');
  if (base) base.placeholder = p.base_url_hint || '';
  toggleCcDsAuthFields();
  if (d.auth_header) set('cc-ds-auth-header-name', d.auth_header);
  const adv = {};
  CC_DS_ADVANCED_KEYS.forEach(k => { if (d[k] !== undefined) adv[k] = d[k]; });
  set('cc-ds-advanced', JSON.stringify(adv, null, 2));
  if (note) note.textContent = `${p.note || ''} — Mẫu theo tài liệu hãng, CHƯA thử trên hệ thống thật: bấm "Thử kết nối" sau khi lưu.`;
}

/**
 * Dựng (hoặc lấy lại) modal thêm/sửa nguồn dữ liệu.
 * `existing` = null -> thêm mới; có object -> sửa nguồn đó.
 */
function _ccDataSourceModal(existing, category) {
  const MODAL_ID = 'cc-ds-modal';
  let modal = document.getElementById(MODAL_ID);

  const ds = existing || {
    id: '', title: '', description: '', category: category || 'custom',
    base_url: '', default_path: '/', auth_type: 'none', auth_header: 'X-Api-Key',
    auth_query: 'api_key', method: 'GET', timeout_seconds: 10, row_limit: 50, enabled: true,
  };
  // Mẫu ứng dụng truyền vào một object nhưng KHÔNG phải nguồn đã lưu — mã
  // nguồn phải sửa được, vì một khách hàng có thể cần nhiều app cùng loại
  // (ERP bán hàng + ERP kế toán chẳng hạn).
  const isEdit = !!existing && existing.__saved === true;

  const authOptions = Object.entries(CC_AUTH_TYPE_LABELS)
    .map(([k, label]) => `<option value="${k}"${ds.auth_type === k ? ' selected' : ''}>${label}</option>`)
    .join('');

  const catOptions = Object.entries(CC_DATA_SOURCE_CATEGORIES)
    .map(([k, c]) => {
      // Nhãn phải nói *nơi hiển thị*, không chỉ tên nhóm — "Kết Nối" mới
      // giúp người dùng biết nguồn sẽ hiện ở đâu sau khi lưu.
      const where = c.where || '';
      return `<option value="${k}"${ds.category === k ? ' selected' : ''}>${c.icon} ${c.label}${where ? ` — ${where}` : ''}</option>`;
    })
    .join('');

  const html = `
    <div class="fixed inset-0 z-[100] flex items-center justify-center p-4 bg-slate-900/60 backdrop-blur-sm" id="${MODAL_ID}">
      <div class="w-full max-w-2xl max-h-[90vh] overflow-y-auto rounded-2xl bg-white dark:bg-slate-800 shadow-2xl border border-slate-200 dark:border-slate-700">
        <div class="flex items-center justify-between px-5 py-3.5 border-b border-slate-200 dark:border-slate-700">
          <h3 class="text-sm font-bold text-slate-800 dark:text-slate-100">
            ${isEdit ? 'Sửa nguồn dữ liệu' : 'Thêm nguồn dữ liệu doanh nghiệp'}
          </h3>
          <button type="button" onclick="closeCcDataSourceModal()" class="text-slate-400 hover:text-slate-600 dark:hover:text-slate-200 transition" aria-label="Đóng">
            <svg width="18" height="18" fill="none" stroke="currentColor" stroke-width="2" viewBox="0 0 24 24"><path d="M18 6L6 18M6 6l12 12"/></svg>
          </button>
        </div>

        <div class="px-5 py-4 space-y-4">
          <div class="rounded-lg bg-primary-50 dark:bg-primary-900/20 border border-primary-200 dark:border-primary-800 px-3.5 py-2.5">
            <p class="text-[10px] text-primary-800 dark:text-primary-200 leading-relaxed">
              Chỉ cần biết <strong>URL</strong> và <strong>cách xác thực</strong> của app là tích hợp được —
              không cần sửa mã nguồn. MISA, Odoo, SAP, Dynamics, sổ kho nội bộ… đều dùng chung khuôn này.
            </p>
          </div>

          ${isEdit ? '' : `<label class="block">
            <span class="text-[10px] font-semibold text-slate-600 dark:text-slate-300 mb-1 block">Mẫu hạ tầng có sẵn</span>
            <select id="cc-ds-preset" onchange="applyCcInfraPreset(this.value)"
              class="w-full px-3 py-2 text-xs rounded-lg border border-slate-200 dark:border-slate-600 bg-white dark:bg-slate-900 text-slate-800 dark:text-slate-100">
              <option value="">— Tự khai báo —</option>
            </select>
            <span id="cc-ds-preset-note" class="text-[9px] text-amber-600 dark:text-amber-400 mt-1 block"></span>
          </label>`}

          <div class="grid grid-cols-1 sm:grid-cols-2 gap-3">
            <label class="block">
              <span class="text-[10px] font-semibold text-slate-600 dark:text-slate-300 mb-1 block">Mã nguồn *</span>
              <input id="cc-ds-id" type="text" value="${_esc(ds.id)}" placeholder="misa-amh"
                ${isEdit ? 'disabled' : ''}
                class="w-full px-3 py-2 text-xs rounded-lg border border-slate-200 dark:border-slate-600 bg-white dark:bg-slate-900 text-slate-800 dark:text-slate-100 disabled:opacity-60" />
              <span class="text-[9px] text-slate-400 mt-1 block">Chữ thường, số, gạch dưới. Không khoảng trắng.</span>
            </label>
            <label class="block">
              <span class="text-[10px] font-semibold text-slate-600 dark:text-slate-300 mb-1 block">Tên hiển thị *</span>
              <input id="cc-ds-title" type="text" value="${_esc(ds.title)}" placeholder="MISA AMH"
                class="w-full px-3 py-2 text-xs rounded-lg border border-slate-200 dark:border-slate-600 bg-white dark:bg-slate-900 text-slate-800 dark:text-slate-100" />
            </label>
          </div>

          <label class="block">
            <span class="text-[10px] font-semibold text-slate-600 dark:text-slate-300 mb-1 block">Mô tả</span>
            <input id="cc-ds-description" type="text" value="${_esc(ds.description)}" placeholder="Kế toán — sổ cái, doanh thu"
              class="w-full px-3 py-2 text-xs rounded-lg border border-slate-200 dark:border-slate-600 bg-white dark:bg-slate-900 text-slate-800 dark:text-slate-100" />
          </label>

          <label class="block">
            <span class="text-[10px] font-semibold text-slate-600 dark:text-slate-300 mb-1 block">Địa chỉ API *</span>
            <input id="cc-ds-base-url" type="url" value="${_esc(ds.base_url)}" placeholder="https://erp.congty.vn/api"
              class="w-full px-3 py-2 text-xs rounded-lg border border-slate-200 dark:border-slate-600 bg-white dark:bg-slate-900 text-slate-800 dark:text-slate-100 font-mono" />
            <span class="text-[9px] text-slate-400 mt-1 block">Phần còn lại của endpoint báo cáo điền ở "Đường dẫn".</span>
          </label>

          <div class="grid grid-cols-1 sm:grid-cols-2 gap-3">
            <label class="block">
              <span class="text-[10px] font-semibold text-slate-600 dark:text-slate-300 mb-1 block">Đường dẫn báo cáo</span>
              <input id="cc-ds-default-path" type="text" value="${_esc(ds.default_path)}" placeholder="/reports/salary"
                class="w-full px-3 py-2 text-xs rounded-lg border border-slate-200 dark:border-slate-600 bg-white dark:bg-slate-900 text-slate-800 dark:text-slate-100 font-mono" />
            </label>
            <label class="block">
              <span class="text-[10px] font-semibold text-slate-600 dark:text-slate-300 mb-1 block">Phương thức</span>
              <select id="cc-ds-method"
                class="w-full px-3 py-2 text-xs rounded-lg border border-slate-200 dark:border-slate-600 bg-white dark:bg-slate-900 text-slate-800 dark:text-slate-100">
                <option value="GET"${ds.method === 'GET' ? ' selected' : ''}>GET — chỉ đọc</option>
                <option value="POST"${ds.method === 'POST' ? ' selected' : ''}>POST — app cần body</option>
              </select>
            </label>
          </div>

          <div class="grid grid-cols-1 sm:grid-cols-2 gap-3">
            <label class="block">
              <span class="text-[10px] font-semibold text-slate-600 dark:text-slate-300 mb-1 block">Cách xác thực</span>
              <select id="cc-ds-auth-type" onchange="toggleCcDsAuthFields()"
                class="w-full px-3 py-2 text-xs rounded-lg border border-slate-200 dark:border-slate-600 bg-white dark:bg-slate-900 text-slate-800 dark:text-slate-100">
                ${authOptions}
              </select>
            </label>
            <label class="block">
              <span class="text-[10px] font-semibold text-slate-600 dark:text-slate-300 mb-1 block">Khoá / Token</span>
              <input id="cc-ds-auth-value" type="password" value="" placeholder="${(ds.hasAuth ?? ds.has_auth) ? '•••••••• (đã lưu — để trống để giữ nguyên)' : 'Nhập khoá API'}"
                autocomplete="new-password"
                class="w-full px-3 py-2 text-xs rounded-lg border border-slate-200 dark:border-slate-600 bg-white dark:bg-slate-900 text-slate-800 dark:text-slate-100 font-mono" />
            </label>
          </div>

          <div id="cc-ds-auth-extra" class="grid grid-cols-1 sm:grid-cols-2 gap-3"></div>

          <div class="grid grid-cols-1 sm:grid-cols-3 gap-3">
            <label class="block">
              <span class="text-[10px] font-semibold text-slate-600 dark:text-slate-300 mb-1 block">Hiển thị ở</span>
              <select id="cc-ds-category"
                class="w-full px-3 py-2 text-xs rounded-lg border border-slate-200 dark:border-slate-600 bg-white dark:bg-slate-900 text-slate-800 dark:text-slate-100">
                ${catOptions}
              </select>
              <span class="text-[9px] text-slate-400 mt-1 block">Nơi nguồn này xuất hiện trong tab.</span>
            </label>
            <label class="block">
              <span class="text-[10px] font-semibold text-slate-600 dark:text-slate-300 mb-1 block">Số dòng tối đa</span>
              <input id="cc-ds-row-limit" type="number" min="1" max="500" value="${ds.row_limit || 50}"
                class="w-full px-3 py-2 text-xs rounded-lg border border-slate-200 dark:border-slate-600 bg-white dark:bg-slate-900 text-slate-800 dark:text-slate-100" />
            </label>
            <label class="block">
              <span class="text-[10px] font-semibold text-slate-600 dark:text-slate-300 mb-1 block">Timeout (giây)</span>
              <input id="cc-ds-timeout" type="number" min="1" max="60" value="${ds.timeout_seconds || 10}"
                class="w-full px-3 py-2 text-xs rounded-lg border border-slate-200 dark:border-slate-600 bg-white dark:bg-slate-900 text-slate-800 dark:text-slate-100" />
            </label>
          </div>

          <details class="rounded-lg border border-slate-200 dark:border-slate-700 px-3 py-2">
            <summary class="text-[10px] font-semibold text-slate-600 dark:text-slate-300 cursor-pointer">Khai báo nâng cao (JSON): đăng nhập, truy vấn, thao tác, phân trang, TLS, CSDL</summary>
            <textarea id="cc-ds-advanced" rows="10" spellcheck="false"
              class="mt-2 w-full px-3 py-2 text-[11px] rounded-lg border border-slate-200 dark:border-slate-600 bg-white dark:bg-slate-900 text-slate-800 dark:text-slate-100 font-mono"
              placeholder='{"queries": {"ten truy van": {"method": "GET", "path": "/api/..."}}}'>${_esc(_ccAdvancedFrom(ds))}</textarea>
            <span class="text-[9px] text-slate-400 mt-1 block">Thao tác ghi (actions) luôn cần người có thẩm quyền duyệt. Khoá không bao giờ nằm trong ô này.</span>
          </details>
        </div>

        <div class="flex items-center justify-between gap-2 px-5 py-3.5 border-t border-slate-200 dark:border-slate-700">
          <div>
            ${isEdit ? `<button type="button" onclick="deleteCcDataSource('${_esc(ds.id)}')"
              class="px-3 py-2 text-[10px] font-semibold rounded-lg text-rose-600 hover:bg-rose-50 dark:hover:bg-rose-900/20 transition">Xoá nguồn</button>` : ''}
          </div>
          <div class="flex items-center gap-2">
            <button type="button" onclick="closeCcDataSourceModal()"
              class="px-3.5 py-2 text-[10px] font-semibold rounded-lg border border-slate-200 dark:border-slate-600 text-slate-600 dark:text-slate-300 hover:bg-slate-50 dark:hover:bg-slate-700/50 transition">Huỷ</button>
            <button type="button" id="cc-ds-save-btn" onclick="saveCcDataSource()"
              class="px-3.5 py-2 text-[10px] font-semibold rounded-lg bg-primary-600 hover:bg-primary-700 text-white transition">Lưu nguồn</button>
          </div>
        </div>
      </div>
    </div>
  `;

  if (modal) modal.remove();
  document.body.insertAdjacentHTML('beforeend', html);
  document.body.style.overflow = 'hidden';
  toggleCcDsAuthFields();
  if (!isEdit) _ccLoadInfraPresets();

  const first = document.getElementById(isEdit ? 'cc-ds-title' : 'cc-ds-id');
  if (first) first.focus();
  return document.getElementById(MODAL_ID);
}

/** Ẩn/hiện ô phụ tuỳ kiểu xác thực — chỉ hỏi đúng thứ cần điền. */
function toggleCcDsAuthFields() {
  const type = document.getElementById('cc-ds-auth-type')?.value;
  const box = document.getElementById('cc-ds-auth-extra');
  if (!box) return;

  if (type === 'header' || type === 'query') {
    const label = type === 'header' ? 'Tên header' : 'Tên tham số';
    const def = type === 'header' ? 'X-Api-Key' : 'api_key';
    const val = document.getElementById('cc-ds-auth-header-name')?.value || def;
    box.innerHTML = `
      <label class="block">
        <span class="text-[10px] font-semibold text-slate-600 dark:text-slate-300 mb-1 block">${label}</span>
        <input id="cc-ds-auth-header-name" type="text" value="${_esc(val)}" placeholder="${def}"
          class="w-full px-3 py-2 text-xs rounded-lg border border-slate-200 dark:border-slate-600 bg-white dark:bg-slate-900 text-slate-800 dark:text-slate-100 font-mono" />
      </label>
      <div></div>`;
  } else {
    box.innerHTML = '';
  }
}

function closeCcDataSourceModal() {
  const modal = document.getElementById('cc-ds-modal');
  if (modal) modal.remove();
  document.body.style.overflow = '';
}

/**
 * Mở modal thêm/sửa nguồn dữ liệu.
 * `sourceId` rỗng -> thêm mới; có id -> sửa nguồn đang có trong registry.
 */
function openAddDataSourceModal(category, sourceId) {
  if (sourceId) {
    const ds = getCcDataSource(sourceId);
    if (!ds) {
      showToast(`Không tìm thấy nguồn "${sourceId}"`, 'error');
      return null;
    }
    return _ccDataSourceModal(ds, category);
  }
  return _ccDataSourceModal(null, category);
}

/** Nội dung ô nâng cao cho một nguồn đã có (rỗng nếu chỉ dùng khai báo đơn giản). */
function _ccAdvancedFrom(ds) {
  const adv = {};
  CC_DS_ADVANCED_KEYS.forEach(k => {
    const v = ds?.[k];
    if (v === undefined || v === null || v === '' || (typeof v === 'object' && !Object.keys(v).length)) return;
    if (k === 'kind' && v === 'rest') return;
    if ((k === 'verify_ssl' && v === true) || (k === 'max_rows' && v === 500)) return;
    adv[k] = v;
  });
  return Object.keys(adv).length ? JSON.stringify(adv, null, 2) : '';
}

/** Gom dữ liệu form thành payload cho API. */
function _ccDataSourceFormPayload() {
  const val = (id) => document.getElementById(id)?.value?.trim() ?? '';
  const authType = val('cc-ds-auth-type') || 'none';
  const extraName = val('cc-ds-auth-header-name');

  let advanced = {};
  const rawAdv = document.getElementById('cc-ds-advanced')?.value?.trim();
  if (rawAdv) {
    try { advanced = JSON.parse(rawAdv); } catch (e) { throw new Error(`Ô nâng cao không phải JSON hợp lệ: ${e.message}`); }
    if (!advanced || typeof advanced !== 'object' || Array.isArray(advanced)) throw new Error('Ô nâng cao phải là một đối tượng JSON {…}');
  }

  return {
    ...advanced,
    id: val('cc-ds-id'),
    title: val('cc-ds-title'),
    description: val('cc-ds-description'),
    base_url: val('cc-ds-base-url'),
    default_path: val('cc-ds-default-path') || '/',
    method: val('cc-ds-method') || 'GET',
    auth_type: authType,
    // Để trống = giữ khoá đang lưu (xử lý phía server).
    auth_value: document.getElementById('cc-ds-auth-value')?.value?.trim() ?? '',
    auth_header: authType === 'header' ? (extraName || 'X-Api-Key') : 'X-Api-Key',
    auth_query: authType === 'query' ? (extraName || 'api_key') : 'api_key',
    category: val('cc-ds-category') || 'custom',
    row_limit: parseInt(val('cc-ds-row-limit'), 10) || 50,
    timeout_seconds: parseFloat(val('cc-ds-timeout')) || 10,
  };
}

/** Lưu nguồn dữ liệu rồi nạp lại danh sách. */
async function saveCcDataSource() {
  let payload;
  try { payload = _ccDataSourceFormPayload(); } catch (err) { showToast(`✖ ${err.message}`, 'error'); return; }
  const btn = document.getElementById('cc-ds-save-btn');
  if (btn) { btn.disabled = true; btn.textContent = 'Đang lưu…'; }

  try {
    const res = await apiFetch(`${API_BASE}/api/v1/enterprise/data-sources`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', 'Authorization': `Bearer ${getAuthToken()}` },
      body: JSON.stringify(payload)
    });
    const d = await res.json().catch(() => ({}));
    if (d?.status === 'error' || !res.ok) {
      throw new Error(d?.error || `HTTP ${res.status}`);
    }

    closeCcDataSourceModal();
    showToast(`✔ Đã lưu nguồn "${payload.title || payload.id}"`, 'success');
    await syncRemoteDataSources();
    // Mở lại sub-tab đang xem để card mới hiện ngay.
    const active = document.querySelector('[data-cc-subtab].bg-primary-600')?.dataset.ccSubtab;
    if (active) await loadCcDataSourceTab(active);
  } catch (err) {
    showToast(`✖ Lưu thất bại: ${err.message}`, 'error');
  } finally {
    if (btn) { btn.disabled = false; btn.textContent = 'Lưu nguồn'; }
  }
}

/** Xoá một nguồn dữ liệu. Chỉ admin — endpoint server tự chặn. */
async function deleteCcDataSource(sourceId) {
  if (!sourceId) return;
  if (!confirm(`Xoá nguồn "${sourceId}"? Cấu hình sẽ mất và không khôi phục được.`)) return;

  try {
    const res = await apiFetch(`${API_BASE}/api/v1/enterprise/data-sources/${encodeURIComponent(sourceId)}`, {
      method: 'DELETE',
      headers: { 'Authorization': `Bearer ${getAuthToken()}` }
    });
    const d = await res.json().catch(() => ({}));
    if (d?.status === 'error' || !res.ok) throw new Error(d?.error || `HTTP ${res.status}`);

    closeCcDataSourceModal();
    showToast(`✔ Đã xoá nguồn "${sourceId}"`, 'success');
    await syncRemoteDataSources();
    const active = document.querySelector('[data-cc-subtab].bg-primary-600')?.dataset.ccSubtab;
    if (active) await loadCcDataSourceTab(active);
  } catch (err) {
    showToast(`✖ Xoá thất bại: ${err.message}`, 'error');
  }
}

/**
 * Nạp data source từ server vào registry.
 *
 * Nguồn do người dùng khai báo nằm ở máy chủ, nên phải nạp lại mỗi lần vào
 * tab — registry trong JS chỉ là bản sao để render. Nếu server lỗi thì giữ
 * nguyên registry cũ: xoá sạch rồi báo lỗi sẽ làm mất hết card đang hiện.
 */
async function syncRemoteDataSources() {
  try {
    const res = await apiFetch(`${API_BASE}/api/v1/enterprise/data-sources`, {
      headers: { 'Authorization': `Bearer ${getAuthToken()}` }
    });
    const d = await res.json().catch(() => ({}));
    if (!res.ok || d?.status === 'error') return;

    const remote = Array.isArray(d.custom) ? d.custom : [];
    const seen = new Set();

    // `builtin` là dict { tên: {has_auth, missing_fields, title} }.
    _ccBuiltinMissing.clear();
    for (const [name, info] of Object.entries(d.builtin || {})) {
      if (Array.isArray(info?.missing_fields) && info.missing_fields.length) {
        _ccBuiltinMissing.set(name, info.missing_fields);
      }
    }

    for (const src of remote) {
      if (!src?.id) continue;
      seen.add(src.id);
      const category = src.category || 'custom';
      registerCcDataSource(src.id, {
        title: src.title,
        description: src.description || '',
        category,
        // Nhóm `connector` hiển thị ở tab Kết Nối (render riêng), các nhóm
        // còn lại ở tab Báo Cáo/Phân Tích. Gán `subTabId` cốt lõi để không
        // sinh thêm sub-tab mới cho nguồn người dùng tự tạo.
        subTabId: category === 'connector' || category === 'custom' ? 'conn' : category,
        icon: CC_DATA_SOURCE_CATEGORIES[category]?.icon || '🔗',
        color: '#0F172A',
        enabled: src.enabled !== false,
        hasAuth: !!src.has_auth,
        isRemote: true,
        __saved: true,
        availablePaths: src.available_paths || [],
        // Card tab Kết Nối hiện domain để dễ nhận ra nguồn nào là nguồn nào.
        baseUrlLabel: src.base_url || '',
        // Probe gọi ra app ngoài nên dùng POST; endpoint `health` của nguồn
        // tĩnh (plugin-registry, webhooks...) là GET nên để nguyên mặc định.
        healthMethod: 'POST',
        endpoints: {
          health: `/api/v1/enterprise/data-sources/${encodeURIComponent(src.id)}/probe`,
          data: `/api/v1/enterprise/data-sources/${encodeURIComponent(src.id)}/fetch`
        }
      });
    }

    // Gỡ nguồn từ chừa đã bị xoá trên server — nếu không, xoá xong reload
    // trang vẫn thấy card cũ và tưởng chưa xoá.
    for (const id of Object.keys(_ccDataSourceRegistry)) {
      const ds = _ccDataSourceRegistry[id];
      if (ds.isRemote && !seen.has(id)) delete _ccDataSourceRegistry[id];
    }

    return seen.size;
  } catch (err) {
    // Im lặng: registry cũ vẫn dùng được, và lỗi mạng đã hiện ở nơi khác.
    console.warn('[DataSource] không nạp được danh sách từ server:', err?.message);
  }
}

// Đăng ký các data source mặc định (backward compat)
function registerDefaultDataSources() {
  // AWS
  registerCcDataSource('aws', {
    title: 'AWS',
    icon: '☁️',
    color: '#FF9900',
    category: 'connector',
    subTabId: 'conn',
    description: 'Cost Explorer · EC2 · CloudWatch',
    endpoints: {
      health: '/api/v1/enterprise/connectors/health/aws',
      config: '/api/v1/enterprise/connectors/config/aws'
    },
    actions: [
      { id: 'cost', label: 'Chi phí', handler: () => runIntegrationTool('check_aws_cost', {}) },
      { id: 'instances', label: 'EC2', handler: () => runIntegrationTool('check_aws_instances', {}) }
    ]
  });

  // OCI
  registerCcDataSource('oci', {
    title: 'OCI',
    icon: '☁️',
    color: '#E81123',
    category: 'connector',
    subTabId: 'conn',
    description: 'Compute · Monitoring · Object Storage',
    endpoints: {
      health: '/api/v1/enterprise/connectors/health/oci',
      config: '/api/v1/enterprise/connectors/config/oci'
    },
    actions: [
      { id: 'instances', label: 'Instances', handler: () => runIntegrationTool('check_oci_instances', {}) },
      { id: 'metrics', label: 'Metrics', handler: () => runIntegrationTool('check_oci_metrics', {}) }
    ]
  });

  // Paperless
  registerCcDataSource('paperless', {
    title: 'Paperless-ngx',
    icon: '📄',
    color: '#3B82F6',
    category: 'connector',
    subTabId: 'conn',
    description: 'Tìm kiếm · OCR · Quản lý tài liệu',
    endpoints: {
      health: '/api/v1/enterprise/connectors/health/paperless',
      config: '/api/v1/enterprise/connectors/config/paperless'
    },
    actions: [
      { id: 'search', label: 'Tìm tài liệu', handler: () => runIntegrationTool('search_paperless_documents', { query: '' }) }
    ]
  });

  // eInvoice
  registerCcDataSource('einvoice', {
    title: 'eInvoice',
    icon: '🧾',
    color: '#10B981',
    category: 'connector',
    subTabId: 'conn',
    description: 'Hóa đơn điện tử · Tra cứu · Thống kê',
    endpoints: {
      health: '/api/v1/enterprise/connectors/health/einvoice',
      config: '/api/v1/enterprise/connectors/config/einvoice'
    },
    actions: [
      { id: 'daily', label: 'Thống kê ngày', handler: () => runIntegrationTool('check_einvoice_daily', {}) },
      { id: 'search', label: 'Tra cứu HĐĐT', handler: () => runIntegrationTool('search_einvoices', {}) }
    ]
  });

  // Plugin Registry (reporting)
  registerCcDataSource('plugin-registry', {
    title: 'Plugin Registry',
    icon: '🔌',
    color: '#8B5CF6',
    category: 'reporting',
    subTabId: 'reporting',
    description: '11 công cụ, circuit breaker, độ trễ',
    endpoints: {
      health: '/api/v1/enterprise/plugin-registry/stats',
      data: '/api/v1/enterprise/plugin-registry/stats'
    }
  });

  // Background Tasks (analytics)
  registerCcDataSource('background-tasks', {
    title: 'Tác Vụ Nền',
    icon: '⚙️',
    color: '#06B6D4',
    category: 'analytics',
    subTabId: 'analytics',
    description: 'Enterprise Orchestrator jobs',
    endpoints: {
      health: '/api/v1/enterprise/background-tasks',
      data: '/api/v1/enterprise/background-tasks'
    }
  });

  // Webhooks (analytics)
  registerCcDataSource('webhooks', {
    title: 'Webhook Events',
    icon: '🔗',
    color: '#F59E0B',
    category: 'analytics',
    subTabId: 'analytics',
    description: 'Cảnh báo từ hệ thống ngoài',
    endpoints: {
      health: '/api/v1/enterprise/webhooks/recent',
      data: '/api/v1/enterprise/webhooks/recent'
    }
  });

  // Cashflow Health (reporting)
  registerCcDataSource('cashflow-health', {
    title: 'Sức Khoẻ Quỹ',
    icon: '💰',
    color: '#10B981',
    category: 'reporting',
    subTabId: 'reporting',
    description: 'Dòng tiền · Dự báo · Cảnh báo',
    endpoints: {
      health: '/api/v1/enterprise/analytics/cashflow-health',
      data: '/api/v1/enterprise/analytics/cashflow-health'
    }
  });
}

// Khởi tạo registry khi load tab system-integration
function initCcDataSourceRegistry() {
  registerDefaultDataSources();
  // Load sub-tab 'conn' mặc định (kết nối)
  loadCcDataSourceTab('conn');
  // Tạo trước pane/button cho reporting và analytics
  loadCcDataSourceTab('reporting');
  loadCcDataSourceTab('analytics');
  // Nguồn do người dùng khai báo nằm ở máy chủ -> nạp thêm để không mất khi reload.
  // Gọi nền, không chặn: tab phải hiện ngay, nguồn tuỳ chỉnh thêm vào sau.
  // `reloadActive` mặc định false vì `loadSystemIntegration()` vừa render xong —
  // render lại ở đây sẽ xoá kết quả các hàm load khác vừa ghi vào cùng pane.
  syncRemoteDataSources().then((n) => {
    // Phase 81: luôn vẽ lại lưới card, kể cả khi không có nguồn tùy chỉnh nào
    // mới. Lý do: `syncRemoteDataSources()` nạp cả danh sách khoá còn THIẾU của
    // connector cốt lõi (`_ccBuiltinMissing`) — mà lần vẽ đầu tiên xảy ra TRƯỚC
    // khi nạp xong, nên cảnh báo "chưa nhập khoá" sẽ không bao giờ hiện nếu
    // chỉ vẽ lại khi có nguồn mới. Điều kiện `if (!n) return` là chính xác
    // chỗ đã giấu lỗi này.
    renderConnCustomSources();
    renderConnectionCards();
    const active = document.querySelector('[data-cc-subtab].bg-primary-600')?.dataset.ccSubtab;
    if (active && active !== 'conn') loadCcDataSourceTab(active);
  });
}

// Export cho window
window.registerCcDataSource = registerCcDataSource;
window.getCcDataSourcesByCategory = getCcDataSourcesByCategory;
window.getCcDataSource = getCcDataSource;
window.loadCcDataSourceTab = loadCcDataSourceTab;
window.runDataSourceHealth = runDataSourceHealth;
window.runDataSourceAction = runDataSourceAction;
window.initCcDataSourceRegistry = initCcDataSourceRegistry;
window.syncRemoteDataSources = syncRemoteDataSources;
window.openAddDataSourceModal = openAddDataSourceModal;
window.closeCcDataSourceModal = closeCcDataSourceModal;
window.saveCcDataSource = saveCcDataSource;
window.deleteCcDataSource = deleteCcDataSource;
window.previewDataSource = previewDataSource;
window.toggleCcDsAuthFields = toggleCcDsAuthFields;
window.renderConnCustomSources = renderConnCustomSources;
window.toggleCcPresetPicker = toggleCcPresetPicker;
window.applyCcPreset = applyCcPreset;
window.applyCcInfraPreset = applyCcInfraPreset;
window.CC_APP_PRESETS = CC_APP_PRESETS;
window._ccDataSourceRegistry = _ccDataSourceRegistry;
window._ccSubTabConfig = _ccSubTabConfig;
window._ccSubTabExtensions = _ccSubTabExtensions;
window.CC_SUBTABS = CC_SUBTABS;
window.CC_DATA_SOURCE_CATEGORIES = CC_DATA_SOURCE_CATEGORIES;

function switchCcSubTab(name) {
  // Chỉ chạy nếu name là tab hợp lệ (cốt lõi hoặc extension).
  if (!CC_SUBTABS.includes(name) && !_ccSubTabExtensions.includes(name)) return;

  // Cập nhật button active state.
  const _btnName = CC_SUBTAB_BUTTON[name] || name;
  const _shown = CC_SUBTAB_GROUPS[name] || [name];
  document.querySelectorAll('[data-cc-subtab]').forEach((b) => {
    const on = b.dataset.ccSubtab === _btnName;
    b.className = on
      ? 'px-3 py-1.5 text-[11px] font-semibold rounded-lg bg-primary-600 text-white shadow-sm transition'
      : 'px-3 py-1.5 text-[11px] font-medium rounded-lg text-slate-500 dark:text-slate-400 hover:bg-slate-100 dark:hover:bg-slate-700/60 transition';
  });

  // Hide/show sub-tab panes (cả CC_SUBTABS lẫn extensions).
  [...CC_SUBTABS, ..._ccSubTabExtensions].forEach((k) => {
    const pane = _ccGet('cc-int-' + k);
    if (pane) pane.classList.toggle('hidden', !_shown.includes(k));
  });

  // Phase 85: hiện tên sub-tab đang mở cạnh tiêu đề chung.
  //
  // Trước đây khối này cố ghi `cfg.title` vào `#cc-int-header h3` — nhưng
  // không có phần tử nào mang id `cc-int-header` trong HTML, nên lệnh ghi rơi
  // vào hư không và tên các sub-tab mở rộng ("Báo Cáo", "Phân Tích") không bao
  // giờ hiện. Nay HTML đã có `cc-int-header` + `cc-int-header-sub`; tên nằm ở
  // ô phụ, còn tiêu đề chung giữ nguyên (đổi nó đi thì mất tên Trung Tâm).
  // Ghi cả sub-tab cốt lõi lẫn mở rộng, để lúc nào cũng biết đang ở mục nào.
  const _ccSubLabel = _ccGet('cc-int-header-sub');
  if (_ccSubLabel) {
    const _cfg = _ccSubTabConfig[name];
    _ccSubLabel.textContent = (_cfg && _cfg.title && !CC_SUBTAB_GROUPS[name]) ? _cfg.title : (CC_SUBTAB_LABELS[_btnName] || name);
  }

  // Bộ hẹn giờ làm mới danh sách máy trạm chỉ chạy khi sub-tab đang mở.
  // Gọi qua `typeof` vì đây là hàm dùng chung cho MỌI sub-tab — không nên
  // phụ thuộc cứng vào phần máy trạm. Nếu không có kiểm tra này, một khối
  // máy trạm bị tách/gỡ là hàm chuyển sub-tab ném ReferenceError, kéo theo
  // mọi sub-tab khác chết theo mà báo lỗi rất khó hiểu.
  if (name === 'devices') {
    if (typeof _devicesPollStart === 'function') _devicesPollStart();
  } else if (typeof _devicesPollStop === 'function') {
    _devicesPollStop();
  }

  // Tải dữ liệu cho tab đang mở.
  // Với extension tab (reporting, analytics) mà pane chưa tồn tại,
  // gọi loadFn để TẠO pane trước khi hide/show.
  const pane = _ccGet('cc-int-' + name);
  const isExtension = _ccSubTabExtensions.includes(name);
  const cfg2 = _ccSubTabConfig[name];

  if (_shown.includes('config') && typeof loadConnectorConfigAll === 'function') loadConnectorConfigAll();
  if (_shown.includes('conn') && name !== 'conn' && _ccSubTabConfig.conn && _ccSubTabConfig.conn.loadFn) _ccSubTabConfig.conn.loadFn();
  if (isExtension && cfg2 && cfg2.loadFn) {
    // Gọi loadFn để tạo/populate pane cho extension tab.
    cfg2.loadFn();
  } else if (pane && !pane.classList.contains('hidden')) {
    // Với tab cốt lõi: chỉ load khi pane đã tồn tại và đang hiển thị.
    // 'conn' KHÔNG ở đây: nó thuộc `_ccSubTabExtensions` nên đã đi nhánh
    // `loadFn` = `loadCcDataSourceTab`, và hàm đó gọi `renderConnectionCards()`.
    // Thêm lệnh ở đây sẽ là code chết — không chạy mà nhìn tưởng có chạy.
    if (name === 'departments') loadEnterpriseDepartments();
    if (name === 'devices') loadDevices();
    if (name === 'sys') { loadPluginRegistryStats(); loadBackgroundTasks(); }
    if (name === 'webhook') loadWebhookAlerts();
  }
}

// ── Dải KPI ───────────────────────────────────────────────────────────────
// Gom số liệu từ các ô đã hiển thị sẵn thay vì gọi thêm API.
//
// Phase 79: đã gỡ `syncCommandCenterKpi`.
// Nó chép số từ `cc-pending-count` sang `cc-kpi-pending` — nhưng sau khi gộp
// tab Trung Tâm Chỉ Huy vào Bảng Điều Khiển, thẻ ô đếm đó đã bị gỏ (trên cùng
// một trang thì ô đếm cạnh danh sách chỉ là bản sao). Hàm vẫn chạy, đọc
// element không còn → rơi vào nhánh dự phòng `|| '0'` → ô KPI hiện 0 bất kể
// thực tế. Chính là loại số 0 bịa mà dự án cấm.
// `loadPending()` nay ghi thẳng vào `cc-kpi-pending` nên không cần chép nữa.


// ── Dải KPI của tab "Tích Hợp Hệ Thống Báo Cáo" ───────────────────────────
// Tách khỏi `syncCommandCenterKpi` vì 4 ô này nằm ở tab khác: nếu để chung,
// số liệu tích hợp sẽ phải nạp cả khi người dùng chỉ mở Trung Tâm Chỉ Huy.
function syncIntegrationKpi() {
  const kpiConn = _ccGet('cc-kpi-connectors');
  const kpiLabel = _ccGet('cc-kpi-connectors-label');

  // Phase 81: đếm trên ĐÚNG những gì lưới card đang hiện — 4 connector cốt lõi
  // cộng nguồn tùy chỉnh. Trước đây mẫu số cứng `CC_CONNECTORS.length` nên thêm
  // một ERP của hệ thống thành "3/4" — tức là 5 kết nối nhưng báo thành 4, và
  // ERP mới không bao giờ được tính. Số liệu phải khớp với thứ nhìn thấy.
  const custom = Object.values(_ccDataSourceRegistry).filter(
    ds => ds.isRemote && (ds.category === 'connector' || ds.category === 'custom')
  );
  const total = CC_CONNECTORS.length + custom.length;

  // Trạng thái lấy từ bộ nhớ, không đọc class trong DOM: lưới card được vẽ
  // lại mỗi lần đổi sub-tab, đọc DOM lúc đó sẽ ra 0 dù đã kiểm tra xong.
  const all = [...CC_CONNECTORS, ..._ccExtensions.map((n) => n), ...custom.map((d) => d.id)];
  const seen = all.filter((n) => _ccConnHealth.has(n));
  const healthy = seen.filter((n) => _ccConnHealth.get(n).ok).length;

  if (kpiConn) {
    if (seen.length === 0) {
      // Chưa kiểm tra gì thì KHÔNG ghi "0/N sẵn sàng": đọc thành "cả N cái
      // đều hỏng", trong khi thực tế là chưa biết. Phải nói đúng là chưa rõ.
      kpiConn.textContent = 'chờ';
      kpiConn.className = 'text-xl font-bold leading-none text-slate-400 dark:text-slate-500 italic';
    } else {
      kpiConn.textContent = `${healthy}/${total}`;
      kpiConn.className = 'text-xl font-bold leading-none text-slate-800 dark:text-slate-100';
    }
  }
  if (kpiLabel) kpiLabel.textContent = seen.length === 0 ? 'chưa kiểm tra' : 'sẵn sàng';

  // `cc-plugin-total` / `cc-bg-task-count` / `cc-webhook-count` do các hàm tải
  // tương ứng ghi vào. Ô KPI chỉ chép lại, không tự tính — để hai nơi không
  // lệch nhau. Ô nào báo '!' (lỗi tải) thì giữ nguyên dấu hiệu lỗi.
  const copy = (fromId, toId) => {
    const src = _ccGet(fromId)?.textContent?.trim();
    const dst = _ccGet(toId);
    if (dst && src) dst.textContent = src;
  };
  copy('cc-plugin-total', 'cc-kpi-plugins');
  copy('cc-bg-task-count', 'cc-kpi-bg-tasks');
  copy('cc-webhook-count', 'cc-kpi-webhook-alerts');
}

// ── Nạp dữ liệu nền cho tab "Tích Hợp Hệ Thống Báo Cáo" ───────────────────
// Gọi khi mở tab. Chỉ tải sub-tab "Kết Nối" là thị giác, các sub-tab còn lại
// tự tải lại khi bấm vào (xem `switchCcSubTab`) để không đốt 4 lượt gọi API
// cho những màn hình người dùng không mở.
function loadSystemIntegration() {
  // Khởi tạo Data Source Registry (hệ thống mới có thể mở rộng).
  initCcDataSourceRegistry();

  // Giữ tương thích ngược: vẫn load 4 nhóm dữ liệu Phase 59/60.
  loadPluginRegistryStats();
  loadBackgroundTasks();
  loadWebhookAlerts();
  loadConnectorConfigAll();
  syncIntegrationKpi();
}

// ── Health check thật (gọi skill, đi qua cổng HITL) ───────────────────────
async function runConnectorHealth(connectorName) {
  const indicator = _ccGet(`${connectorName}-health-indicator`);
  const textEl = _ccGet(`${connectorName}-health-text`);
  if (indicator) indicator.className = 'w-2 h-2 rounded-full bg-amber-400 animate-pulse';
  if (textEl) textEl.textContent = 'Đang kiểm tra…';
  try {
    const res = await apiFetch(`${API_BASE}/api/v1/skills/execute`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', 'Authorization': `Bearer ${getAuthToken()}` },
      body: JSON.stringify({ name: 'check_connector_health', arguments: { connectors: [connectorName] } })
    });
    const d = await res.json();
    const c = (d?.data?.connectors || {})[connectorName];
    if (c?.success) {
      if (indicator) indicator.className = 'w-2 h-2 rounded-full bg-emerald-500';
      if (textEl) textEl.textContent = `OK (${(c.latency_ms || 0).toFixed(0)}ms)`;
      _ccConnHealth.set(connectorName, { ok: true, text: `OK (${(c.latency_ms || 0).toFixed(0)}ms)` });
    } else {
      if (indicator) indicator.className = 'w-2 h-2 rounded-full bg-rose-500';
      if (textEl) textEl.textContent = c?.error || 'Thất bại';
      _ccConnHealth.set(connectorName, { ok: false, text: c?.error || 'Thất bại' });
    }
  } catch (err) {
    if (indicator) indicator.className = 'w-2 h-2 rounded-full bg-rose-500';
    if (textEl) textEl.textContent = `Lỗi: ${err.message}`;
    _ccConnHealth.set(connectorName, { ok: false, text: `Lỗi: ${err.message}` });
  } finally {
    // Ô KPI "Kết nối ngoại vi" đếm theo màu của chấm sức khoẻ vừa cập nhật.
    syncIntegrationKpi();
  }
}

async function runConnectorHealthAll() {
  // Phase 81: trước đây toast ghi cứng "4 kết nối ngoại vi". Nay lưới card
  // chứa cả nguồn tùy chỉnh, nên con số phải đếm thật — ghi cứng sẽ thành lời
  // nói sai ngay khi người dùng thêm ERP/CRM của hệ thống.
  const custom = Object.values(_ccDataSourceRegistry).filter(
    ds => ds.isRemote && (ds.category === 'connector' || ds.category === 'custom')
  );

  for (const name of CC_CONNECTORS) await runConnectorHealth(name);
  for (const ds of custom) await runDataSourceHealth(ds.id);

  const total = CC_CONNECTORS.length + custom.length;
  // Nói số nào hỏng, không chỉ "xong" — nếu không người dùng tưởng tất cả ổn.
  const failed = [..._ccConnHealth.entries()].filter(([, h]) => !h.ok).length;
  showToast(
    failed
      ? `Kiểm tra ${total} kết nối: ${total - failed} ổn, ${failed} lỗi`
      : `✔ Đã kiểm tra ${total} kết nối ngoại vi — tất cả OK`,
    failed ? 'warning' : 'success'
  );
  syncIntegrationKpi();
}

// ── Plugin Registry ───────────────────────────────────────────────────────
async function loadPluginRegistryStats() {
  const box = _ccGet('cc-plugin-stats');
  if (!box) return;
  box.innerHTML = '<p class="text-[11px] text-slate-400 dark:text-slate-500 text-center py-4">Đang tải…</p>';
  try {
    const res = await apiFetch(`${API_BASE}/api/v1/enterprise/plugin-registry/stats`, {
      method: 'GET',
      headers: { 'Authorization': `Bearer ${getAuthToken()}` }
    });
    const d = await res.json();
    if (d.status !== 'success') {
      box.innerHTML = `<p class="text-[11px] text-rose-500 text-center py-4">${_esc(d.error || 'Không tải được')}</p>`;
      return;
    }
    const tools = d.tools || {};
    const names = Object.keys(tools);
    const stats = d.execution_stats || {};
    if (names.length === 0) {
      box.innerHTML = '<p class="text-[11px] text-slate-400 dark:text-slate-500 text-center py-4">'
        + 'Chưa có công cụ nào đăng ký vào Plugin Registry.</p>';
      return;
    }
    let html = '<div class="grid grid-cols-3 gap-2 mb-3 text-center">'
      + `<div><p id="cc-plugin-total" class="text-lg font-bold text-slate-800 dark:text-slate-100">${d.total_tools}</p>`
      + '<p class="text-[9px] text-slate-400 dark:text-slate-500">đã đăng ký</p></div>'
      + `<div><p class="text-lg font-bold text-emerald-600 dark:text-emerald-400">${d.enabled_tools}</p>`
      + '<p class="text-[9px] text-slate-400 dark:text-slate-500">đang bật</p></div>'
      + `<div><p class="text-lg font-bold text-slate-800 dark:text-slate-100">${Object.keys(stats).length}</p>`
      + '<p class="text-[9px] text-slate-400 dark:text-slate-500">đã chạy</p></div></div>';

    html += '<div class="space-y-1 max-h-48 overflow-y-auto">';
    names.forEach((n) => {
      const t = tools[n];
      const s = stats[n] || {};
      const cb = s.circuit_breaker || {};
      const st = (cb.state || 'closed').toUpperCase();
      const stCls = st === 'CLOSED' ? 'text-emerald-600 dark:text-emerald-400'
        : st === 'OPEN' ? 'text-rose-600 dark:text-rose-400'
          : 'text-amber-600 dark:text-amber-400';
      html += `<div class="flex items-center justify-between gap-2 px-2 py-1.5 rounded-lg border border-slate-200 dark:border-slate-700">`
        + `<span class="text-[10px] font-medium text-slate-700 dark:text-slate-300 truncate">${_esc(n)}</span>`
        + `<span class="flex items-center gap-1.5 shrink-0">`
        + (t.risk_level >= 3 ? '<span class="px-1 py-0.5 rounded text-[8px] font-bold bg-rose-100 dark:bg-rose-900/40 text-rose-600 dark:text-rose-400">HITL</span>' : '')
        + `<span class="text-[9px] ${t.enabled ? 'text-slate-400 dark:text-slate-500' : 'text-slate-300 dark:text-slate-600 line-through'}">${t.enabled ? 'bật' : 'tắt'}</span>`
        + `<span class="text-[9px] font-bold ${stCls}">${st}</span>`
        + `<span class="text-[9px] text-slate-400 dark:text-slate-500">${s.total_calls || 0}✓ ${s.failed_calls || 0}✗</span>`
        + `</span></div>`;
    });
    html += '</div>';
    box.innerHTML = html;
    syncIntegrationKpi();
  } catch (err) {
    box.innerHTML = `<p class="text-[11px] text-rose-500 text-center py-4">Lỗi: ${_esc(err.message)}</p>`;
  }
}

// ── Tác vụ nền (Phase 60) ─────────────────────────────────────────────────
async function loadBackgroundTasks() {
  const box = _ccGet('cc-bg-task-list');
  const badge = _ccGet('cc-bg-task-count');
  if (!box) return;
  try {
    const res = await apiFetch(`${API_BASE}/api/v1/enterprise/background-tasks`, {
      method: 'GET',
      headers: { 'Authorization': `Bearer ${getAuthToken()}` }
    });
    const d = await res.json();
    if (d.status !== 'success') {
      box.innerHTML = `<p class="text-[11px] text-rose-500 text-center py-4">${_esc(d.error || 'Không tải được')}</p>`;
      if (badge) badge.textContent = '!';
      return;
    }
    const tasks = d.tasks || [];
    if (badge) badge.textContent = String(tasks.length);
    if (tasks.length === 0) {
      box.innerHTML = '<p class="text-[11px] text-slate-400 dark:text-slate-500 text-center py-6">Chưa có tác vụ nền.</p>';
      return;
    }
    const META = {
      pending: ['Chờ chạy', 'bg-slate-100 dark:bg-slate-700 text-slate-600 dark:text-slate-300'],
      running: ['Đang chạy', 'bg-amber-100 dark:bg-amber-900/40 text-amber-700 dark:text-amber-400'],
      completed: ['Xong', 'bg-emerald-100 dark:bg-emerald-900/40 text-emerald-700 dark:text-emerald-400'],
      failed: ['Lỗi', 'bg-rose-100 dark:bg-rose-900/40 text-rose-600 dark:text-rose-400'],
      cancelled: ['Đã hủy', 'bg-slate-100 dark:bg-slate-700 text-slate-500 dark:text-slate-400'],
    };
    box.innerHTML = tasks.map((t) => {
      const [label, cls] = META[t.status] || [t.status, 'bg-slate-100 text-slate-600'];
      const pct = Math.round((t.progress || 0) * 100);
      return `<div class="rounded-lg border border-slate-200 dark:border-slate-700 p-2.5">`
        + `<div class="flex items-center justify-between gap-2">`
        + `<span class="text-[10px] font-semibold text-slate-700 dark:text-slate-300 truncate">${_esc(t.name)}</span>`
        + `<span class="px-1.5 py-0.5 rounded text-[9px] font-bold ${cls}">${_esc(label)}</span>`
        + `</div>`
        + `<div class="mt-1.5 h-1 rounded-full bg-slate-200 dark:bg-slate-700 overflow-hidden">`
        + `<div class="h-full rounded-full ${t.status === 'failed' ? 'bg-rose-500' : 'bg-primary-500'}" style="width:${Math.max(2, pct)}%"></div>`
        + `</div>`
        + `<p class="text-[9px] text-slate-400 dark:text-slate-500 mt-1 truncate">`
        + `${_esc(t.progress_message || t.description || '')}${pct}%</p>`
        + (t.error ? `<p class="text-[9px] text-rose-500 mt-0.5 truncate">${_esc(t.error)}</p>` : '')
        + `</div>`;
    }).join('');
  } catch (err) {
    box.innerHTML = `<p class="text-[11px] text-rose-500 text-center py-4">Lỗi: ${_esc(err.message)}</p>`;
    if (badge) badge.textContent = '!';
  } finally {
    // `finally` chứ không phải cuối `try`: hai nhánh `return` sớm ở trên cũng
    // phải chép số sang ô KPI, nếu không ô sẽ kẹt ở giá trị cũ.
    syncIntegrationKpi();
  }
}

// ── Webhook ───────────────────────────────────────────────────────────────
const CC_WEBHOOK_SEVERITY = {
  critical: ['Nghịêm trọng', 'border-rose-300 dark:border-rose-700 bg-rose-50 dark:bg-rose-900/20', 'text-rose-600 dark:text-rose-400'],
  high: ['Cao', 'border-amber-300 dark:border-amber-700 bg-amber-50 dark:bg-amber-900/20', 'text-amber-600 dark:text-amber-400'],
  medium: ['Trung bình', 'border-sky-300 dark:border-sky-700 bg-sky-50 dark:bg-sky-900/20', 'text-sky-600 dark:text-sky-400'],
  low: ['Thấp', 'border-slate-200 dark:border-slate-700 bg-slate-50 dark:bg-slate-900/40', 'text-slate-500 dark:text-slate-400'],
};

function _ccWebhookCard(a) {
  const sev = String(a.severity || 'low').toLowerCase();
  const [sevLabel, boxCls, sevCls] = CC_WEBHOOK_SEVERITY[sev] || CC_WEBHOOK_SEVERITY.low;
  const src = String(a.source || 'webhook:custom').replace(/^webhook:/, '');
  const rid = (a.metadata && a.metadata.resource_id) || '';
  const ts = a.timestamp ? new Date(a.timestamp) : null;
  const timeStr = ts && !isNaN(ts) ? ts.toLocaleTimeString('vi-VN') : '';
  return `<div class="rounded-xl border ${boxCls} p-2.5">`
    + `<div class="flex items-center justify-between gap-2">`
    + `<span class="text-[9px] font-bold uppercase tracking-wider text-slate-500 dark:text-slate-400">${_esc(src)}</span>`
    + `<span class="text-[9px] ${sevCls} font-bold">${_esc(sevLabel)}</span>`
    + `</div>`
    + `<p class="text-[10px] font-semibold text-slate-800 dark:text-slate-200 mt-0.5">${_esc(a.title || '(không có tiêu đề)')}</p>`
    + `<p class="text-[10px] text-slate-500 dark:text-slate-400 mt-0.5 line-clamp-2">${_esc(a.message || '')}</p>`
    + (rid ? `<p class="text-[9px] text-slate-400 dark:text-slate-500 font-mono truncate mt-0.5">${_esc(rid)}</p>` : '')
    + (timeStr ? `<p class="text-[9px] text-slate-400 dark:text-slate-600 mt-0.5">${_esc(timeStr)}</p>` : '')
    + `</div>`;
}

async function loadWebhookAlerts() {
  const box = _ccGet('cc-webhook-list');
  if (!box) return;
  try {
    const res = await apiFetch(`${API_BASE}/api/v1/enterprise/webhooks/recent`, {
      method: 'GET',
      headers: { 'Authorization': `Bearer ${getAuthToken()}` }
    });
    const d = await res.json();
    if (d.status !== 'success') {
      box.innerHTML = `<p class="col-span-full text-[11px] text-rose-500 text-center py-4">${_esc(d.error || 'Không tải được')}</p>`;
      return;
    }
    const alerts = d.alerts || [];
    if (alerts.length === 0) {
      box.innerHTML = '<p class="col-span-full text-[11px] text-slate-400 dark:text-slate-500 text-center py-8">Chưa có webhook nào.</p>';
    } else {
      box.innerHTML = alerts.map(_ccWebhookCard).join('');
    }
    const badge = _ccGet('cc-webhook-count');
    if (badge) badge.textContent = String(alerts.length);
  } catch (err) {
    box.innerHTML = `<p class="col-span-full text-[11px] text-rose-500 text-center py-4">Lỗi: ${_esc(err.message)}</p>`;
  } finally {
    // Nhánh `return` sớm khi API lỗi không set badge → ô KPI giữ số cũ có
    // thể đã sai. `finally` đảm bảo chép lại đúng trạng thái hiện tại.
    syncIntegrationKpi();
  }
}

// Webhook listener: cập nhật UI khi có webhook mới (qua WebSocket).
function onWebhookAlert(alert) {
  const list = _ccGet('cc-webhook-list');
  const count = _ccGet('cc-webhook-count');
  if (!list) return;
  if (!alert || typeof alert !== 'object') return;

  // Nhánh "chưa có dữ liệu" đang chứa <p> con — thay bằng danh sách thật.
  const empty = list.querySelector('p');
  if (empty) list.innerHTML = '';

  list.insertAdjacentHTML('afterbegin', _ccWebhookCard(alert));
  while (list.children.length > 20) list.removeChild(list.lastChild);
  if (count) count.textContent = String(list.children.length);
  // Webhook đẩy tới qua WebSocket: ô KPI "Cảnh báo webhook" phải tăng theo,
  // nếu không số trên dải chỉ số sẽ kẹt ở lúc mở tab.
  syncIntegrationKpi();
}

// ── Gọi tool tích hợp ─────────────────────────────────────────────────────
function _ccToolBox(html) {
  const box = _ccGet('cc-tool-output');
  if (box) box.innerHTML = html;
}

function clearToolOutput() {
  _ccToolBox('Chọn một công cụ ở trên để chạy.');
}

// Gom lý do thất bại của một lần gọi tool Phase 59/60.
//
// `POST /api/v1/skills/execute` có HAI tầng `success`:
//   - `d.success`      : skill có chạy được không (tầng skill).
//   - `d.data.success` : BẢN THÂN connector có làm được việc không.
// Với tool Phase 59/60, `d.success` là `true` ngay cả khi connector hỏng —
// ví dụ thật khi chưa điền credential AWS:
//     { "success": true, "data": { "success": false, "data": null,
//       "error": "Not authenticated" } }
// Nếu chỉ nhìn `d.success`, màn hình hiện "✔ check_aws_cost 177ms null" —
// dấu tích xanh cho lệnh đã thất bại, và câu "Not authenticated" bị mất.
//
// Riêng `check_connector_health` còn khó hơn: nó GỘP 4 connector nên không có
// `error` chung, lỗi nằm ở từng connector. Không gom lại thì người dùng chỉ
// thấy "Thất bại không rõ lý do" dù server đã nói rõ từng nguyên nhân.
function _ccToolFailureReason(d, inner) {
  if (inner && inner.error) return String(inner.error);
  if (d && d.error) return String(d.error);

  const conns = inner && inner.connectors;
  if (conns && typeof conns === 'object') {
    const parts = Object.entries(conns)
      .filter(([, v]) => v && v.success === false)
      .map(([k, v]) => `${k}: ${v.error || 'thất bại'}`);
    if (parts.length) return parts.join(' · ');
  }
  return 'Thất bại không rõ lý do';
}

// Dựng khung kết quả dùng chung cho cả nhánh ✔ và nhánh ✖.
function _ccToolFrame(mark, markCls, toolName, latencyMs, bodyHtml) {
  return `<div class="flex items-center justify-between gap-2 mb-1">`
    + `<span class="${markCls} font-semibold">${mark} ${_esc(toolName)}</span>`
    + `<span class="text-[9px] text-slate-400">${_esc(String(latencyMs ?? 0))}ms</span></div>`
    + bodyHtml;
}

function _ccToolPreview(data) {
  // `null`/`undefined` KHÔNG phải dữ liệu. In thẳng ra sẽ ra chữ "null" trông
  // như kết quả thật — phải nói thẳng là không có.
  if (data === null || data === undefined) return '(không có dữ liệu)';
  const text = typeof data === 'object' ? JSON.stringify(data, null, 2) : String(data);
  return `<pre class="text-[10px] text-slate-600 dark:text-slate-300 p-2 rounded bg-white dark:bg-slate-800 border border-slate-200 dark:border-slate-700 overflow-auto max-h-72 whitespace-pre-wrap break-all">${_esc(text.slice(0, 4000))}</pre>`;
}

async function runIntegrationTool(toolName, args) {
  _ccToolBox(`<span class="text-slate-400">Đang gọi ${_esc(toolName)}…</span>`);
  try {
    const res = await apiFetch(`${API_BASE}/api/v1/skills/execute`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', 'Authorization': `Bearer ${getAuthToken()}` },
      body: JSON.stringify({ name: toolName, arguments: args || {} })
    });
    const d = await res.json();

    const inner = (d.data && typeof d.data === 'object') ? d.data : null;
    if (d.success === false || (inner && inner.success === false)) {
      const reason = _ccToolFailureReason(d, inner);
      // Vẫn kèm phần chi tiết nếu còn: lỗi từng connector nằm trong đó, bỏ
      // đi thì người dùng phải đoán mò mới biết hỏng ở đâu.
      const payload = (inner && 'data' in inner) ? inner.data : inner;
      const hasDetail = payload && typeof payload === 'object' && Object.keys(payload).length > 0;
      _ccToolBox(_ccToolFrame(
        '✖', 'text-rose-500', toolName, d.latency_ms,
        `<p class="text-[10px] text-rose-500 mb-1">${_esc(reason.slice(0, 500))}</p>`
        + (hasDetail ? _ccToolPreview(payload) : ''),
      ));
      return;
    }

    const data = d.data?.data !== undefined ? d.data.data : d.data;
    _ccToolBox(_ccToolFrame(
      '✔', 'text-emerald-500', toolName, d.latency_ms,
      _ccToolPreview(data),
    ));
  } catch (err) {
    _ccToolBox(`<span class="text-rose-500">✖ ${_esc(err.message)}</span>`);
  }
}

// ── Cấu hình kết nối ngoại vi ─────────────────────────────────────────────
//
// Phase 81: form KHÔNG viết tay nữa. Cấu trúc trường lấy từ
// `GET /api/v1/enterprise/connectors/catalog` — server sinh JSON Schema từ
// CONNECTOR_REQUIRED_FIELDS / CONNECTOR_DEFAULTS. Thêm connector mới chỉ cần
// khai báo ở đó, không sửa HTML.
//
// Bản form viết tay trước đây LỆCH TÊN với khóa cấu hình ở 7 trường, nên
// những ô đó không bao giờ được nạp cũng không bao giờ được lưu đúng chỗ:
//   cfg-aws-access-key      → thực ra là access_key_id
//   cfg-aws-secret-key      → thực ra là secret_access_key
//   cfg-aws-cost-explorer   → thực ra là cost_explorer_enabled
//   cfg-oci-compartment     → thực ra là compartment_id
//   cfg-paperless-url       → thực ra là base_url
//   cfg-paperless-token     → thực ra là api_token
//   cfg-einvoice-url        → thực ra là base_url
// `saveConnectorConfig` bóc key từ id, nên `url` được ghi thành khoá `url` —
// connector đọc `base_url` nên không thấy gì. Nay id sinh từ chính tên khoá
// nên không thể lệch nữa.
//
// Bảo mật: ô bí mật LUÔN để trống, chỉ ghi "đã lưu". Ô trống khi bấm Lưu =
// giữ nguyên giá trị cũ (server tự khôi phục), nên sửa một trường khác không
// làm mất bí mật.

const _ccFieldInput =
  'w-full px-2.5 py-1.5 text-xs rounded-lg border border-slate-200 dark:border-slate-700 ' +
  'bg-slate-50 dark:bg-slate-900 text-slate-800 dark:text-slate-200 focus:outline-none focus:ring-1 focus:ring-primary-500';

/** Trường này có phải bí mật không — theo schema, không đoán theo tên. */
function _ccIsSecretProp(prop) {
  return prop && (prop.format === 'secret' || prop.format === 'password');
}

/** id phải BẰNG `cfg-{connector}-{key}` để bộ nạp và bộ lưu dùng chung quy ước. */
function _ccFieldId(connector, key) {
  return `cfg-${connector}-${key.replace(/_/g, '-')}`;
}

function _ccRenderField(connector, key, prop, required) {
  const id = _ccFieldId(connector, key);
  const secret = _ccIsSecretProp(prop);
  const bool = prop.type === 'boolean';
  const title = _esc(prop.title || key);
  const hint = prop.description
    ? `<div class="text-[9px] text-slate-400 dark:text-slate-500 mt-0.5">${_esc(prop.description)}</div>`
    : '';

  let control;
  if (bool) {
    const cur = prop.default === true ? ' selected' : '';
    control =
      `<select id="${id}" class="${_ccFieldInput}">` +
      `<option value="true"${cur}>Bật</option>` +
      `<option value="false"${cur ? '' : ' selected'}>Tắt</option>` +
      `</select>`;
  } else if (prop.ui && prop.ui.widget === 'hidden') {
    // `ui.widget: hidden` = trường có giá trị mặc định, người dùng không cần thấy.
    return `<input type="hidden" id="${id}" value="${_esc(String(prop.default ?? ''))}" />`;
  } else {
    const type = secret ? 'password' : (prop.type === 'number' ? 'number' : 'text');
    const ph = secret
      ? '••••••••'
      : (prop.default !== undefined && prop.default !== null ? String(prop.default) : '');
    control =
      `<input type="${type}" id="${id}" class="${_ccFieldInput}"` +
      (ph ? ` placeholder="${_esc(ph)}"` : '') + ` />`;
  }

  return (
    `<div>` +
    `<label class="block text-[9px] uppercase tracking-wider text-slate-400 dark:text-slate-500 mb-1">` +
    `${title}${required ? ' <span class="text-rose-400">*</span>' : ''}</label>` +
    control + hint +
    `</div>`
  );
}

function _ccRenderCard(c) {
  const schema = c.config_schema || {};
  const props = schema.properties || {};
  const required = new Set(schema.required || []);
  const fields = Object.keys(props)
    .map((k) => _ccRenderField(c.id, k, props[k], required.has(k)))
    .join('');

  // Được nhấn mạnh không: thêm class NGAY TRONG CHUỖI HTML, không sửa phần tử
  // sau khi vẽ — xem giải thích ở khai báo `_ccConfigFocus`.
  const focusCls = c.id === _ccConfigFocus
    ? ' ring-2 ring-primary-500 border-primary-400'
    : '';

  return (
    `<div id="${c.id}-config-card" class="config-section rounded-xl border border-slate-200 dark:border-slate-700 p-3.5 transition${focusCls}">` +
    `<div class="flex items-center gap-2 mb-3 pb-2 border-b border-slate-100 dark:border-slate-700/60">` +
    `<span class="text-xs font-bold text-slate-700 dark:text-slate-300">${_esc(c.display_name || c.id.toUpperCase())}</span>` +
    (c.description ? `<span class="text-[9px] text-slate-400 dark:text-slate-500 truncate">${_esc(c.description)}</span>` : '') +
    `<span id="${c.id}-config-status" class="ml-auto px-1.5 py-0.5 rounded text-[9px] font-bold bg-slate-100 dark:bg-slate-700 text-slate-500 dark:text-slate-400">Chưa cấu hình</span>` +
    `</div>` +
    (fields
      ? `<div class="grid grid-cols-1 sm:grid-cols-2 gap-2">${fields}</div>`
      : `<div class="text-[10px] text-slate-400 dark:text-slate-500 italic py-2">Connector này không có tham số cấu hình.</div>`) +
    `<button type="button" onclick="saveConnectorConfig('${c.id}', this)"` +
    ` class="mt-3 w-full px-3 py-1.5 text-[10px] font-semibold rounded-lg bg-emerald-600 hover:bg-emerald-700 text-white transition">` +
    `Lưu cấu hình ${_esc(c.display_name || c.id)}</button>` +
    (c.kind === 'alert_channel'
      ? `<button type="button" onclick="testAlertChannel('${c.id}', this)"` +
        ` class="mt-2 w-full px-3 py-1.5 text-[10px] font-semibold rounded-lg border border-sky-500/50 text-sky-600 dark:text-sky-300 hover:bg-sky-50 dark:hover:bg-sky-900/30 transition">` +
        `Gửi thử cảnh báo</button>`
      : '') +
    `</div>`
  );
}

/** Dựng lại toàn bộ form connector từ schema. */
/** Danh mục lần dựng gần nhất — kênh cảnh báo lấy trạng thái "chờ kết nối" từ đây
 *  (endpoint health chỉ có 4 connector dữ liệu). */
let _ccCatalog = {};

async function renderConnectorForms() {
  const box = _ccGet('cc-connector-config-forms');
  if (!box) return;
  try {
    const res = await apiFetch(`${API_BASE}/api/v1/enterprise/connectors/catalog`, {
      headers: { 'Authorization': `Bearer ${getAuthToken()}` },
    });
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    const body = await res.json();
    const list = Object.values(body?.connectors || {});
    _ccCatalog = body?.connectors || {};
    if (!list.length) {
      box.innerHTML =
        `<div class="col-span-full py-6 text-center text-xs text-amber-600 dark:text-amber-400 italic">` +
        `Server không trả về connector nào. Chưa kiểm tra được cấu hình.</div>`;
      return;
    }
    box.innerHTML = list.map(_ccRenderCard).join('');
  } catch (err) {
    // Không có form nào để bấm — nói rõ thay vì để trống rồi im lặng.
    box.innerHTML =
      `<div class="col-span-full py-6 text-center text-xs text-rose-500 italic">` +
      `Không tải được danh mục connector: ${_esc(err.message)}. ` +
      `Chưa kiểm tra được cấu hình — không phải đã cấu hình xong.</div>`;
  }
}

async function loadConnectorConfigAll() {
  // Phải dựng form TRƯỚC rồi mới nạp giá trị: `loadConnectorConfig` tìm ô
  // theo id, mà id chỉ tồn tại sau khi form được sinh ra.
  await renderConnectorForms();
  // Mọi thẻ server trả về (4 connector dữ liệu + kênh cảnh báo), không chỉ danh sách cố định.
  const names = Object.keys(_ccCatalog).length ? Object.keys(_ccCatalog) : CC_CONNECTORS;
  for (const name of names) {
    await loadConnectorConfig(name);
  }
}

// ── Trạng thái cấu hình connector ───────────────────────────────────────────
// Cache kết quả `/enterprise/connectors/health` để 4 form không phải gọi
// 4 lần. Chỉ đọc cấu hình trong bộ nhớ, không gọi ra ngoài.
let _ccConnHealthPromise = null;
function _ccFetchConnectorHealth() {
  if (!_ccConnHealthPromise) {
    _ccConnHealthPromise = apiFetch(`${API_BASE}/api/v1/enterprise/connectors/health`, {
      headers: { 'Authorization': `Bearer ${getAuthToken()}` }
    })
      .then((r) => (r.ok ? r.json() : {}))
      .catch(() => ({}))
      .finally(() => {
        // Cho phép gọi lại sau khi người dùng vừa lưu cấu hình.
        setTimeout(() => { _ccConnHealthPromise = null; }, 0);
      });
  }
  return _ccConnHealthPromise;
}

async function loadConnectorConfig(connectorName) {
  try {
    const res = await apiFetch(`${API_BASE}/api/v1/config`, {
      method: 'GET',
      headers: { 'Authorization': `Bearer ${getAuthToken()}` }
    });
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    const body = await res.json();
    // Endpoint trả object phẳng; bọc trong `result` ở một số bản cũ.
    const cfg = (body && body.result && typeof body.result === 'object') ? body.result : (body || {});
    const c = cfg[connectorName];
    const block = (c && typeof c === 'object') ? c : {};

    let filled = 0;
    for (const [key, value] of Object.entries(block)) {
      const el = _ccGet(`cfg-${connectorName}-${key.replace(/_/g, '-')}`);
      if (!el) continue;
      if (value === null || value === undefined || value === '') continue;
      if (el.type === 'password' || CC_SECRET_FIELDS.includes(key)) {
        // Không đưa secret vào DOM.
        el.value = '';
        el.placeholder = '•••••••• (đã lưu — để trống để giữ nguyên)';
        el.dataset.hasValue = '1';
      } else {
        el.value = String(value);
        filled += 1;
      }
    }

    // "Đã cấu hình" phải trả lời đúng câu hỏi "connector này có dùng được
    // không". Trước đây dùng `Object.keys(block).length > 0`, nhưng khối cấu
    // hình luôn có sẵn `region`/`base_url`/`provider`… nên màn hình luôn hiện
    // "Đã cấu hình" kể cả khi chưa có token nào — mọi lời gọi thật đều hỏng
    // với "Authentication failed". Giờ lấy danh sách khoá BẮT BUỘC còn
    // thiếu từ server (server chỉ trả TÊN khoá, không trả giá trị bí mật).
    let missing = null;
    try {
      const h = await _ccFetchConnectorHealth();
      const info = (h?.connectors || {})[connectorName] || _ccCatalog[connectorName];
      if (info) missing = Array.isArray(info.missing_fields) ? info.missing_fields : [];
    } catch (_e) {
      missing = null; // Không gọi được health -> không được khẳng định cả
    }

    const statusEl = _ccGet(`${connectorName}-config-status`);
    if (missing === null) {
      // Không xác minh được: nói thẳng thay vì đoán.
      _ccSetStatus(statusEl, Object.keys(block).length > 0, 'Đã cấu hình (chưa xác minh được)', 'Chưa cấu hình');
    } else if (missing.length === 0) {
      _ccSetStatus(statusEl, true, 'Đã cấu hình', 'Chưa cấu hình');
    } else {
      _ccSetStatus(
        statusEl,
        false,
        'Chưa cấu hình',
        `Còn thiếu: ${missing.join(', ')} — mọi lời gọi sẽ thất bại`
      );
    }
    void filled;
  } catch (err) {
    _ccSetStatus(_ccGet(`${connectorName}-config-status`), false, 'Chưa cấu hình', `Lỗi tải: ${err.message}`);
  }
}

/** Gửi một cảnh báo THỬ qua kênh — kết quả là phản hồi thật của Teams / SMTP / Graph… */
async function testAlertChannel(channelId, btn) {
  const label = btn ? btn.textContent : '';
  if (btn) { btn.disabled = true; btn.textContent = 'Đang gửi…'; }
  try {
    const res = await apiFetch(`${API_BASE}/api/v1/system/notifications/test`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', 'Authorization': `Bearer ${getAuthToken()}` },
      body: JSON.stringify({ channel: channelId }),
    });
    if (!res.ok) {
      let detail = `HTTP ${res.status}`;
      try { detail = (await res.json()).detail || detail; } catch (_) { /* không phải JSON */ }
      throw new Error(detail);
    }
    const d = await res.json();
    const r = (d.results || [])[0];
    if (!r) throw new Error('Kênh chưa kết nối hoặc đang tắt — điền đủ ô bắt buộc (*) rồi Lưu.');
    if (r.status !== 'ok') throw new Error(r.detail || r.status);
    showToast(`✔ Đã gửi thử: ${r.detail}`, 'success');
  } catch (err) {
    showToast(`✖ Gửi thử thất bại: ${err.message}`, 'error');
  } finally {
    if (btn) { btn.disabled = false; btn.textContent = label; }
  }
}
window.testAlertChannel = testAlertChannel;

async function saveConnectorConfig(connectorName, btn) {
  const label = btn ? btn.dataset.label || btn.textContent.trim() : `Lưu cấu hình ${connectorName.toUpperCase()}`;
  if (btn) { btn.disabled = true; btn.textContent = 'Đang lưu…'; }

  const formData = {};
  const sel = `#cc-connector-config-forms input[id^="cfg-${connectorName}-"], #cc-connector-config-forms select[id^="cfg-${connectorName}-"]`;
  document.querySelectorAll(sel).forEach((el) => {
    const key = el.id.replace(`cfg-${connectorName}-`, '').replace(/-/g, '_');
    const isSecret = el.type === 'password' || CC_SECRET_FIELDS.includes(key);
    if (isSecret) {
      // Ô trống = giữ giá trị đã lưu. Chỉ gửi khi người dùng gõ thật.
      if (el.value && el.value !== '••••••••') formData[key] = el.value;
      return;
    }
    if (el.tagName === 'SELECT') {
      formData[key] = (el.value === 'true') ? true : (el.value === 'false' ? false : el.value);
    } else {
      formData[key] = el.value.trim();
    }
  });

  try {
    const res = await apiFetch(`${API_BASE}/api/v1/config`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', 'Authorization': `Bearer ${getAuthToken()}` },
      body: JSON.stringify({ [connectorName]: formData })
    });
    if (!res.ok) {
      let detail = `HTTP ${res.status}`;
      try { detail = (await res.json()).detail || detail; } catch (_) { /* không phải JSON */ }
      throw new Error(detail);
    }
    const d = await res.json();
    if (d.success === false) throw new Error(d.error || d.message || 'Không rõ nguyên nhân');

    showToast(`✔ Đã lưu cấu hình ${connectorName.toUpperCase()} — connector đã nạp lại ngay`, 'success');
    await loadConnectorConfig(connectorName);
  } catch (err) {
    showToast(`✖ Lưu thất bại: ${err.message}`, 'error');
  } finally {
    if (btn) { btn.disabled = false; btn.textContent = label; }
  }
}

// ── Cầu nối inline onclick của tab Trung Tâm Chỉ Huy ──────────────────────
// Các hàm `ask` / `policyLookup` / `decide` sống trong IIFE `CommandCenter`,
// nên phải bọc lại ở cấp window thì `onclick="runCommandCenterAsk()"` trong
// index.html mới gọi được. Thiếu đoạn này thì nút "Hỏi" báo
// "runCommandCenterAsk is not defined" — vì `const` ở top-level script không
// tự tạo property trên `window`.
function runCommandCenterAsk() {
  const input = document.getElementById('cc-ask-input');
  return CommandCenter.ask(input ? input.value : '');
}

function askCommandCenter(text) {
  const input = document.getElementById('cc-ask-input');
  if (input) input.value = text;
  return CommandCenter.ask(text);
}

// Phase 82: khung "Tra Cứu Quy Chế" riêng đã bỏ — hàm ngoài cũ không còn chỗ
// gọi nên đã gỡ luôn. Hỏi quy chế qua Điều Hành AI (ô nhập/kết quả cc-ask).
// Phase 71: rà soát là việc chỉ đọc nên để admin bấm tay chạy ngay, không
// cần qua cổng HITL — đúng nguyên tắc "AI tự làm việc rủi ro thấp".
function runCommandCenterAudit() {
  return CommandCenter.runCommandCenterAudit();
}

// Hai nút việc-một-lần trong thẻ "Điều Hành Hệ Thống". Gọi bằng inline
// onclick nên phải có ở phạm vi module; CommandCenter là IIFE nên các hàm
// bên trong không tự lộ ra window.
function runOpsSentinelScan() {
  return CommandCenter.runOpsSentinelScan();
}

function runOpsDomainSync() {
  return CommandCenter.runOpsDomainSync();
}

// Live Event Log: hai hàm này được gọi bằng inline onclick trong index.html
// nhưng LogViewer là IIFE nên không tự lộ ra window.
function toggleLogAutoScroll(btn) {
  return LogViewer.toggleAutoScroll(btn);
}

function clearEventLog() {
  return LogViewer.clear();
}

// Expose for WebSocket handler + inline onclick
if (typeof window !== 'undefined') {
  window.onWebhookAlert = onWebhookAlert;
  window.CommandCenter = CommandCenter;
  window.runCommandCenterAsk = runCommandCenterAsk;
  window.askCommandCenter = askCommandCenter;
  // Phase 71: nút "Chạy rà soát tức thì" trong khung "AI Được Phép Làm Gì".
  window.runCommandCenterAudit = runCommandCenterAudit;
  // Phase 72: các nút điều hành trong thẻ "Điều Hành Hệ Thống".
  window.runOpsSentinelScan = runOpsSentinelScan;
  window.runOpsDomainSync = runOpsDomainSync; window.toggleLogAutoScroll = toggleLogAutoScroll;
  window.clearEventLog = clearEventLog;
  window.switchCcSubTab = switchCcSubTab;
  window.syncIntegrationKpi = syncIntegrationKpi;
  window.loadSystemIntegration = loadSystemIntegration;
  window.loadConnectorConfigAll = loadConnectorConfigAll;
  window.renderConnectorForms = renderConnectorForms;
  window.renderConnectionCards = renderConnectionCards;
  window.gotoConnectorConfig = gotoConnectorConfig;
  window.saveConnectorConfig = saveConnectorConfig;
  window.loadPluginRegistryStats = loadPluginRegistryStats;
  window.loadBackgroundTasks = loadBackgroundTasks;
  window.loadWebhookAlerts = loadWebhookAlerts;
  window.clearToolOutput = clearToolOutput;
  window.runIntegrationTool = runIntegrationTool;
  window.loadEnterpriseDepartments = loadEnterpriseDepartments;
  window.openAddDeptModal = openAddDeptModal;
  window.submitDepartmentForm = submitDepartmentForm;
  window.syncDeptDataNow = syncDeptDataNow;
  window.triggerEnterpriseCrossReport = triggerEnterpriseCrossReport;
  window.playCurrentVoiceSummary = playCurrentVoiceSummary;
}

// ═══════════════════════════════════════════════════════════════════════════
// ── ENTERPRISE EVOLUTION: PHÒNG BAN ────────────────────────────────────────
// ═══════════════════════════════════════════════════════════════════════════

let _currentVoiceSummaryText = '';

async function loadEnterpriseDepartments() {
  const container = document.getElementById('departments-list-grid');
  if (!container) return;
  try {
    const res = await apiFetch('/api/v1/admin/departments/overview');
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    const data = await res.json();
    const depts = data.departments || [];

    if (depts.length === 0) {
      container.innerHTML = `
        <div class="col-span-full py-10 text-center text-xs text-slate-400 dark:text-slate-500">
          Chưa có phòng ban nào được khai báo. Bấm <b>+ Thêm Phòng Ban Mới</b> để bắt đầu cấu hình No-Code.
        </div>`;
      return;
    }

    container.innerHTML = depts.map(d => {
      const clearanceLabels = {
        1: { text: 'Level 1: Public', bg: 'bg-emerald-500/10 text-emerald-600 border-emerald-500/30' },
        2: { text: 'Level 2: Internal', bg: 'bg-blue-500/10 text-blue-600 border-blue-500/30' },
        3: { text: 'Level 3: Confidential', bg: 'bg-amber-500/10 text-amber-600 border-amber-500/30' },
        4: { text: 'Level 4: Strictly Secret', bg: 'bg-red-500/10 text-red-600 border-red-500/30' }
      };
      const cl = clearanceLabels[d.data_clearance_level] || clearanceLabels[1];
      const sourcesCount = d.sources ? d.sources.length : 0;
      const sourcesNames = (d.sources || []).map(s => s.source_name).join(', ') || 'Chưa gắn nguồn';

      return `
        <div class="rounded-xl border border-slate-200 dark:border-slate-700 bg-white dark:bg-slate-800/80 p-4 shadow-sm hover:border-primary-500/40 transition space-y-3">
          <div class="flex items-start justify-between gap-2">
            <div>
              <div class="flex items-center gap-2">
                <span class="font-mono font-bold text-sm text-slate-900 dark:text-white uppercase">${escapeHtml(d.dept_code)}</span>
                <span class="px-2 py-0.5 text-[9px] font-mono font-bold rounded border ${cl.bg}">${cl.text}</span>
              </div>
              <h5 class="text-xs font-semibold text-slate-700 dark:text-slate-200 mt-1">${escapeHtml(d.dept_name)}</h5>
            </div>
            <span class="w-2 h-2 rounded-full ${d.is_active ? 'bg-emerald-500' : 'bg-slate-400'}"></span>
          </div>

          <div class="text-[11px] text-slate-500 dark:text-slate-400">
            <span class="font-medium text-slate-700 dark:text-slate-300">Nguồn dữ liệu (${sourcesCount}):</span>
            <span class="italic">${escapeHtml(sourcesNames)}</span>
          </div>

          <div class="p-2.5 rounded-lg bg-slate-50 dark:bg-slate-900/60 border border-slate-100 dark:border-slate-800 text-[11px] text-slate-600 dark:text-slate-300 leading-relaxed">
            <span class="font-semibold text-slate-700 dark:text-slate-200 block mb-0.5">🧠 Tóm tắt ngữ nghĩa (30m Cache):</span>
            ${escapeHtml(d.cached_summary || 'Chờ quét dữ liệu...')}
          </div>

          <div class="flex items-center justify-between pt-2 border-t border-slate-100 dark:border-slate-700/60">
            <span class="text-[10px] text-slate-400 font-mono">Cập nhật: ${escapeHtml((d.updated_at || '').substring(0, 16))}</span>
            <button type="button" onclick="syncDeptDataNow('${escapeHtml(d.dept_code)}')"
              class="px-2.5 py-1 text-[11px] font-medium rounded-md bg-slate-100 dark:bg-slate-700 hover:bg-primary-600 hover:text-white dark:hover:bg-primary-600 text-slate-700 dark:text-slate-200 transition">
              🔄 Quét Ngay
            </button>
          </div>
        </div>
      `;
    }).join('');
  } catch (err) {
    console.error('[Departments] Lỗi tải danh sách phòng ban:', err);
    container.innerHTML = `
      <div class="col-span-full py-8 text-center text-xs text-red-500">
        Không thể tải danh sách phòng ban: ${escapeHtml(err.message)}
      </div>`;
  }
}

function openAddDeptModal() {
  const box = document.getElementById('add-dept-form-box');
  if (box) {
    box.classList.remove('hidden');
    box.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
  }
}

async function submitDepartmentForm() {
  const code = (document.getElementById('dept-form-code')?.value || '').trim();
  const name = (document.getElementById('dept-form-name')?.value || '').trim();
  const clearance = parseInt(document.getElementById('dept-form-clearance')?.value || '1', 10);
  const srcName = (document.getElementById('dept-form-src-name')?.value || '').trim();
  const srcType = document.getElementById('dept-form-src-type')?.value || 'REST_API';
  const srcCron = (document.getElementById('dept-form-src-cron')?.value || '').trim() || null;

  if (!code || !name) {
    alert('Vui lòng nhập đầy đủ Mã và Tên phòng ban.');
    return;
  }

  const payload = {
    dept_code: code,
    dept_name: name,
    data_clearance_level: clearance,
    config_metadata: {},
    is_active: true,
  };

  if (srcName) {
    payload.data_source = {
      source_name: srcName,
      source_type: srcType,
      sync_cron: srcCron,
      connection_config: {},
    };
  }

  try {
    const res = await apiFetch('/api/v1/admin/departments/save', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
    });
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    const data = await res.json();
    document.getElementById('add-dept-form-box')?.classList.add('hidden');
    loadEnterpriseDepartments();
    alert(`Đã lưu phòng ban ${code} thành công.`);
  } catch (err) {
    alert(`Lỗi khi lưu phòng ban: ${err.message}`);
  }
}

async function syncDeptDataNow(deptCode) {
  try {
    const res = await apiFetch('/api/v1/admin/departments/save', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ dept_code: deptCode, dept_name: deptCode }),
    });
    loadEnterpriseDepartments();
  } catch (err) {
    console.error(err);
  }
}

async function triggerEnterpriseCrossReport() {
  const modal = document.getElementById('cross-report-modal');
  const voiceEl = document.getElementById('cross-report-voice-text');
  const richEl = document.getElementById('cross-report-rich-text');
  if (modal) modal.classList.remove('hidden');
  if (voiceEl) voiceEl.textContent = 'Đang kích hoạt Bộ Não Điều Hành phân tích đối soát đa phòng ban...';
  if (richEl) richEl.textContent = 'Vui lòng chờ...';

  try {
    const res = await apiFetch('/api/v1/admin/cross-report', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ scope: ['FIN', 'HR', 'CTO'], clearance_level: 4 }),
    });
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    const data = await res.json();

    _currentVoiceSummaryText = data.voice_summary || '';
    if (voiceEl) voiceEl.textContent = _currentVoiceSummaryText;
    if (richEl) richEl.textContent = data.rich_details || '';

    // Tự động phát âm thanh tóm tắt nếu có voice controller
    playCurrentVoiceSummary();
  } catch (err) {
    if (voiceEl) voiceEl.textContent = `Lỗi: ${err.message}`;
  }
}

function playCurrentVoiceSummary() {
  if (!_currentVoiceSummaryText) return;
  try {
    if (typeof playTtsAudio === 'function') {
      playTtsAudio(_currentVoiceSummaryText);
    } else if (typeof window.speechSynthesis !== 'undefined') {
      const u = new SpeechSynthesisUtterance(_currentVoiceSummaryText);
      u.lang = 'vi-VN';
      window.speechSynthesis.speak(u);
    }
  } catch (e) {
    console.warn(e);
  }
}


