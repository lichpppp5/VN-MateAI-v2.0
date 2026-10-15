// SPDX-License-Identifier: Apache-2.0
// Copyright (c) 2026 Dương Thanh Lịch — VN-MateAI. See LICENSE and NOTICE.
// Tách từ <script> nội tuyến của roi_dashboard.html (CSP chặt không chạy script nội tuyến).
/* ═══════════════════════════════════════════════════
   ROI Dashboard JS — Phase 48
   ═══════════════════════════════════════════════════ */

const API_BASE = '';   // same origin
let ticketChart = null;
let auditChart  = null;
let refreshTimer = null;

// ─── Auth token helper ───────────────────────────────
// ─── Phase 75: quy ước "chờ kết nối" ────────────────
// Dùng CHUNG định nghĩa với web/app.js. Trang này là file độc lập nên phải
// chép lại nguyên văn — test_phase75_no_fake_roi.mjs đối chiếu hai bản phải
// giống hệt nhau, để không có chuyện một bên sửa mà bên kia lệch.
//
// Nguyên tắc: 0 là SỐ ĐO HỢP LỆ và phải hiện là 0 ("0 nhân viên" là sự thật
// kiểm chứng được từ DB). Chỉ null / undefined / NaN / chuỗi rỗng mới là
// "chờ kết nối" — vì đó là lúc chưa hỏi được ai, chứ không phải đã đo ra 0.
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

/** Thoát HTML trước khi nội suy: tiêu đề phiếu là dữ liệu do người dùng nhập. */
function esc(s) {
  return String(s ?? '').replace(/[&<>"']/g, (c) => (
    { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]
  ));
}

/** Lấy element theo id — `_setLiveText` cần element, không phải chuỗi id trần. */
const $ = (id) => document.getElementById(id);

function getToken() {
  return (
    localStorage.getItem('vnmateai_token') ||
    localStorage.getItem('authToken') ||
    sessionStorage.getItem('authToken') ||
    localStorage.getItem('token') ||
    ''
  );
}

async function apiFetch(path, opts = {}) {
  const token = getToken();
  const headers = { 'Content-Type': 'application/json', ...(opts.headers || {}) };
  if (token) headers['Authorization'] = 'Bearer ' + token;
  const res = await fetch(API_BASE + path, { ...opts, headers });
  if (!res.ok) throw new Error(`HTTP ${res.status}: ${res.statusText}`);
  return res.json();
}

// ─── Main loader ─────────────────────────────────────
async function loadDashboard() {
  document.getElementById('headerDate').textContent =
    new Date().toLocaleString('vi-VN', { dateStyle: 'full', timeStyle: 'short' });

  document.getElementById('btnRefresh').disabled = true;
  document.getElementById('btnRefresh').textContent = '⏳ Đang tải...';

  try {
    const data = await apiFetch('/api/v1/roi-dashboard?include_audit_details=true');
    renderKpiCards(data);
    renderTicketChart(data);
    renderAuditChart(data);
    renderTicketTable(data);
    renderAuditTrail(data);
    renderAiTicketTable(data);
    renderReportText(data);
  } catch (e) {
    console.error('Dashboard load error:', e);
    renderDisconnected(e);
  } finally {
    document.getElementById('btnRefresh').disabled = false;
    document.getElementById('btnRefresh').textContent = '↻ Làm mới';
  }
}

// ─── KPI Cards ───────────────────────────────────────
function renderKpiCards(data) {
  const kpi = data.kpi || {};
  const tickets = data.tickets?.today || {};
  const audit = data.audit?.stats || {};
  const org = data.org || {};

  // `total` là số đo thật: API đã COALESCE nên 0 nghĩa là "hôm nay không có
  // phiếu nào", KHÁC hẳn null nghĩa là "chưa lấy được". Tỷ lệ hoàn thành chỉ
  // có nghĩa khi có ít nhất 1 phiếu — 0/0 không phải "0%" mà là không đo được.
  const totalTickets = tickets.total;
  const hasTickets = _isLive(totalTickets) && Number(totalTickets) > 0;
  const aiTasks = kpi.ai_tasks_today;
  const perTask = kpi.hours_saved_per_task;

  const hoursSub = !_isLive(aiTasks) || Number(aiTasks) === 0
    ? 'Chưa có tác vụ AI nào'
    : (perTask ? `Ước tính ${perTask}h/phiếu (giả định)` : 'Ước tính, không phải số đo');

  const cards = [
    {
      label: 'Phiếu ITSM Hôm Nay',
      value: _isLive(totalTickets) ? String(totalTickets) : null,
      sub: _isLive(tickets.completed) ? `Hoàn thành: ${tickets.completed}` : WAIT_TXT,
      icon: '🎟️', cls: 'accent1'
    },
    {
      label: 'AI Tự Xử Lý',
      value: _isLive(aiTasks) ? String(aiTasks) : null,
      sub: hoursSub, icon: '🤖', cls: 'accent2'
    },
    {
      label: 'Tỷ Lệ Hoàn Thành',
      value: hasTickets ? _liveNum(kpi.ticket_completion_rate, { digits: 1, unit: '%' }) : null,
      sub: hasTickets ? 'Phiếu đóng / tổng phiếu' : 'Chưa có phiếu nào hôm nay',
      icon: '✅', cls: 'accent3'
    },
    {
      label: 'Thao Tác Bị Chặn',
      value: _isLive(audit.blocked_attempts) ? String(audit.blocked_attempts) : null,
      sub: 'RBAC / Zero-Trust', icon: '🚫', cls: 'accent5'
    },
    {
      label: 'Nhân Viên',
      value: _isLive(org.total_employees) ? String(org.total_employees) : null,
      sub: _isLive(org.total_departments) ? `${org.total_departments} phòng ban` : WAIT_TXT,
      icon: '👥', cls: 'accent4'
    },
    {
      label: 'Tổng Audit Logs',
      value: _isLive(audit.total_logs) ? String(audit.total_logs) : null,
      sub: _isLive(audit.success) ? `Thành công: ${audit.success}` : WAIT_TXT,
      icon: '🔐', cls: 'accent6'
    },
  ];

  document.getElementById('kpiGrid').innerHTML = cards.map(c => {
    const wait = c.value === null;
    return `
    <div class="kpi-card ${c.cls}">
      <div class="icon">${c.icon}</div>
      <div class="label">${esc(c.label)}</div>
      <div class="value${wait ? ' is-waiting' : ''}">${esc(wait ? WAIT_TXT : c.value)}</div>
      <div class="sub">${esc(c.sub)}</div>
    </div>`;
  }).join('');
}

// ─── Ticket Donut Chart ──────────────────────────────
function renderTicketChart(data) {
  const t = data.tickets?.today || {};
  const total = t.total;

  // Không vẽ biểu đồ khi chưa có số liệu hoặc hôm nay chưa có phiếu nào:
  // một vòng doughnut toàn số 0 trông như "0% hoàn thành", trong khi sự thật
  // là "chưa có gì để đo". Hai chuyện khác nhau.
  if (!_isLive(total) || Number(total) === 0) {
    if (ticketChart) { ticketChart.destroy(); ticketChart = null; }
    const label = !_isLive(total) ? WAIT_TXT : 'Chưa có phiếu nào hôm nay';
    _setLiveText($('badgeTotalTickets'), _isLive(total) ? '0 phiếu' : null);
    document.getElementById('completionProgress').innerHTML =
      `<div class="progress-label"><span>Tỷ lệ hoàn thành hôm nay</span><span class="is-waiting">${esc(label)}</span></div>`;
    const wrap = document.querySelector('#ticketDonutChart')?.parentElement;
    if (wrap) wrap.innerHTML = `<div class="loading" style="height:220px;">${esc(label)}</div>`;
    return;
  }

  _setLiveText($('badgeTotalTickets'), `${total} phiếu`);

  const completed   = Number(t.completed) || 0;
  const in_progress = Number(t.in_progress) || 0;
  const pending     = Number(t.pending) || 0;
  const cancelled   = Math.max(0, Number(total) - completed - in_progress - pending);

  const wrap = document.querySelector('#ticketDonutChart')?.parentElement;
  if (wrap && !document.getElementById('ticketDonutChart')) {
    wrap.innerHTML = '<canvas id="ticketDonutChart"></canvas>';
  }

  // Chart.js nạp từ CDN. Nếu CDN chặn/offline thì `Chart` không tồn tại và
  // `new Chart()` ném lỗi — mà lỗi này rơi vào catch của loadDashboard(), khiến
  // CẢ TRANG báo "chờ kết nối" dù API vừa trả dữ liệu thật. Vì vậy chặn riêng ở
  // đây: mất biểu đồ thì chỉ mất biểu đồ, các bảng và KPI vẫn phải hiện.
  if (typeof Chart === 'undefined') {
    if (wrap) wrap.innerHTML =
      '<div class="loading" style="height:220px;">Không tải được thư viện biểu đồ (Chart.js)</div>';
  } else {
  if (ticketChart) ticketChart.destroy();
  const ctx = document.getElementById('ticketDonutChart').getContext('2d');
  ticketChart = new Chart(ctx, {
    type: 'doughnut',
    data: {
      labels: ['Hoàn thành', 'Đang xử lý', 'Chờ xử lý', 'Huỷ'],
      datasets: [{
        data: [completed, in_progress, pending, cancelled],
        backgroundColor: ['#10b981','#22d3ee','#6366f1','#ef4444'],
        borderColor: '#111827', borderWidth: 3,
        hoverOffset: 8,
      }]
    },
    options: {
      responsive: true, maintainAspectRatio: false,
      cutout: '68%',
      plugins: {
        legend: { position: 'bottom', labels: { color: '#94a3b8', font: { size: 12 }, padding: 16 } },
        tooltip: { callbacks: {
          label: ctx => ` ${ctx.label}: ${ctx.raw} phiếu`
        }}
      }
    }
  });
  }

  // Completion progress bar
  const rate = Math.round(100 * completed / Number(total));
  document.getElementById('completionProgress').innerHTML = `
    <div class="progress-label">
      <span>Tỷ lệ hoàn thành hôm nay</span>
      <span style="color:#10b981;font-weight:600;">${rate}%</span>
    </div>
    <div class="progress-bar">
      <div class="progress-fill" style="width:${rate}%;background:linear-gradient(90deg,#10b981,#34d399);"></div>
    </div>
  `;
}

// ─── Audit Bar Chart ─────────────────────────────────
function renderAuditChart(data) {
  const stats = data.audit?.stats || {};
  const totalLogs = stats.total_logs;

  if (!_isLive(totalLogs) || Number(totalLogs) === 0) {
    if (auditChart) { auditChart.destroy(); auditChart = null; }
    const label = !_isLive(totalLogs) ? WAIT_TXT : 'Chưa có nhật ký kiểm toán nào';
    _setLiveText($('badgeTotalAudit'), _isLive(totalLogs) ? '0 log' : null);
    const wrap = document.querySelector('#auditBarChart')?.parentElement;
    if (wrap) wrap.innerHTML = `<div class="loading" style="height:220px;">${esc(label)}</div>`;
    return;
  }

  _setLiveText($('badgeTotalAudit'), `${totalLogs} log`);

  const wrap = document.querySelector('#auditBarChart')?.parentElement;
  if (wrap && !document.getElementById('auditBarChart')) {
    wrap.innerHTML = '<canvas id="auditBarChart"></canvas>';
  }

  // Cùng lý do như renderTicketChart: mất CDN Chart.js thì chỉ mất biểu đồ,
  // không được kéo cả trang vào trạng thái "chờ kết nối".
  if (typeof Chart === 'undefined') {
    if (wrap) wrap.innerHTML =
      '<div class="loading" style="height:220px;">Không tải được thư viện biểu đồ (Chart.js)</div>';
    return;
  }

  if (auditChart) auditChart.destroy();
  const ctx = document.getElementById('auditBarChart').getContext('2d');
  auditChart = new Chart(ctx, {
    type: 'bar',
    data: {
      labels: ['Thành công', 'Thất bại', 'Bị chặn', 'Task AI'],
      datasets: [{
        label: 'Số lượng',
        data: [
          Number(stats.success) || 0,
          Number(stats.failed) || 0,
          Number(stats.blocked_attempts) || 0,
          Number(stats.ai_tasks_created) || 0,
        ],
        backgroundColor: ['rgba(16,185,129,.7)', 'rgba(245,158,11,.7)', 'rgba(239,68,68,.7)', 'rgba(99,102,241,.7)'],
        borderColor: ['#10b981','#f59e0b','#ef4444','#6366f1'],
        borderWidth: 2, borderRadius: 8,
      }]
    },
    options: {
      responsive: true, maintainAspectRatio: false,
      plugins: { legend: { display: false }, tooltip: { callbacks: {
        label: ctx => ` ${ctx.raw} mục`
      }}},
      scales: {
        x: { grid: { display: false }, ticks: { color: '#94a3b8' } },
        y: { grid: { color: 'rgba(255,255,255,0.05)' }, ticks: { color: '#94a3b8' } }
      }
    }
  });
}

// ─── Ticket Table ─────────────────────────────────────
function renderTicketTable(data) {
  const open = (data.tickets?.open || []).concat(data.tickets?.in_progress || []);
  _setLiveText($('badgeOpenTickets'), String(open.length));

  if (open.length === 0) {
    document.getElementById('ticketTableWrap').innerHTML =
      '<div class="loading" style="height:120px;">Không có phiếu nào đang mở</div>';
    return;
  }

  document.getElementById('ticketTableWrap').innerHTML = `
    <div style="overflow:auto;max-height:340px;">
    <table>
      <thead><tr>
        <th>ID</th><th>Tiêu đề</th><th>Trạng thái</th><th>Người phụ trách</th>
      </tr></thead>
      <tbody>
        ${open.slice(0, 15).map(t => `
          <tr>
            <td style="font-size:11px;color:#6366f1;font-family:monospace;">${esc(t.id || '-')}</td>
            <td style="max-width:200px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;" title="${esc(t.title)}">${esc(t.title || '-')}</td>
            <td><span class="badge-status ${esc(t.status || 'pending')}">${esc(statusLabel(t.status))}</span></td>
            <td style="color:#94a3b8;">${esc(t.assignee_name || '—')}</td>
          </tr>
        `).join('')}
      </tbody>
    </table></div>
  `;
}

// ─── Audit Trail ─────────────────────────────────────
function renderAuditTrail(data) {
  const logs = data.audit?.recent || [];
  if (logs.length === 0) {
    document.getElementById('auditTrailWrap').innerHTML =
      '<div style="color:#94a3b8;font-size:13px;text-align:center;padding:20px;">Chưa có thao tác nào hôm nay.</div>';
    return;
  }

  document.getElementById('auditTrailWrap').innerHTML = logs.map(l => `
    <div class="audit-row">
      <div class="audit-dot ${esc(l.status || 'pending')}"></div>
      <div class="audit-info">
        <div class="audit-action">${esc(l.action_type || 'Không rõ')}</div>
        <div class="audit-meta">${esc(l.employee_id || 'Không rõ')}</div>
      </div>
      <div class="audit-time">${esc(formatTime(l.timestamp))}</div>
    </div>
  `).join('');
}

// ─── AI Ticket Table ──────────────────────────────────
function renderAiTicketTable(data) {
  const tickets = data.tickets?.ai_created || [];
  _setLiveText($('badgeAiTickets'), String(tickets.length));

  if (tickets.length === 0) {
    document.getElementById('aiTicketTableWrap').innerHTML =
      '<div class="loading" style="height:120px;">Chưa có phiếu AI nào</div>';
    return;
  }

  document.getElementById('aiTicketTableWrap').innerHTML = `
    <div style="overflow:auto;max-height:340px;">
    <table>
      <thead><tr>
        <th>ID</th><th>Tiêu đề</th><th>Trạng thái</th><th>Ghi chú</th>
      </tr></thead>
      <tbody>
        ${tickets.slice(0, 10).map(t => `
          <tr>
            <td style="font-size:11px;color:#22d3ee;font-family:monospace;">${esc(t.id || '-')}</td>
            <td style="max-width:180px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;" title="${esc(t.title)}">${esc(t.title || '-')}</td>
            <td><span class="badge-status ${esc(t.status || 'pending')}">${esc(statusLabel(t.status))}</span></td>
            <td style="font-size:11px;color:#94a3b8;max-width:120px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;"
                title="${esc(t.resolution_notes)}">${esc(t.resolution_notes || '—')}</td>
          </tr>
        `).join('')}
      </tbody>
    </table></div>
  `;
}

// ─── Report Text ──────────────────────────────────────
function renderReportText(data) {
  const el = document.getElementById('reportText');
  // Báo cáo do server soạn; rỗng nghĩa là server không soạn được — nói thẳng
  // thay vì in "Không có dữ liệu." (câu đó nghe như đã tra xong và không có gì).
  if (!_isLive(data.report_text)) {
    el.textContent = `${WAIT_TXT}\n\nMáy chủ chưa trả về nội dung báo cáo vận hành.`;
    el.classList.add('is-waiting');
    return;
  }
  el.textContent = data.report_text;
  el.classList.remove('is-waiting');
}

// ─── Phase 75: trạng thái chưa lấy được dữ liệu ──────
// Trước đây nhánh lỗi gọi showFallbackDemo() — dựng ra 12 phiếu ITSM, 48 audit
// log, 15 nhân viên, 4 phòng ban và một báo cáo vận hành hoàn chỉnh. Trông y
// hệt dữ liệu thật, và người đọc không có cách nào phân biệt. Nay nói thẳng ra.
//
// `reason` nhận object Error hoặc chuỗi; tách 401/403 để báo đúng việc cần làm.
function renderDisconnected(reason) {
  const msg = String(reason?.message || reason || '');
  const needLogin = /\b401\b|\b403\b/.test(msg);
  const detail = needLogin
    ? 'Phiên đăng nhập đã hết hạn hoặc chưa đăng nhập. Mở trang chính để đăng nhập lại.'
    : 'Không gọi được /api/v1/roi-dashboard. Kiểm tra máy chủ rồi bấm Làm mới.';

  document.getElementById('kpiGrid').innerHTML = `
    <div class="loading" style="height:120px;grid-column:1/-1;flex-direction:column;gap:8px;">
      <div>⏳ ${esc(WAIT_TXT)}</div>
      <div style="font-size:12px;">${esc(detail)}</div>
    </div>`;

  for (const id of ['badgeTotalTickets', 'badgeTotalAudit', 'badgeOpenTickets', 'badgeAiTickets']) {
    const el = document.getElementById(id);
    if (el) { el.textContent = WAIT_TXT; el.classList.add('is-waiting'); }
  }

  const note = `<div class="loading" style="height:120px;">⏳ ${esc(WAIT_TXT)}</div>`;
  document.getElementById('ticketTableWrap').innerHTML = note;
  document.getElementById('aiTicketTableWrap').innerHTML = note;
  document.getElementById('auditTrailWrap').innerHTML = note;
  document.getElementById('completionProgress').innerHTML =
    `<div class="progress-label is-waiting"><span>Tỷ lệ hoàn thành hôm nay</span><span>${esc(WAIT_TXT)}</span></div>`;

  if (ticketChart) { ticketChart.destroy(); ticketChart = null; }
  if (auditChart) { auditChart.destroy(); auditChart = null; }

  const reportEl = document.getElementById('reportText');
  reportEl.textContent = `${WAIT_TXT}\n\n${detail}`;
  reportEl.classList.add('is-waiting');
}

// ─── Helpers ─────────────────────────────────────────
function statusLabel(s) {
  return { pending: 'Chờ xử lý', in_progress: 'Đang xử lý', completed: 'Hoàn thành', cancelled: 'Huỷ' }[s] || s || 'N/A';
}

function formatTime(iso) {
  if (!iso) return '?';
  try {
    const d = new Date(iso);
    return d.toLocaleTimeString('vi-VN', { hour: '2-digit', minute: '2-digit' });
  } catch { return iso.slice(11, 16); }
}

// ─── Init ─────────────────────────────────────────────
document.addEventListener('DOMContentLoaded', () => {
  loadDashboard();
  // Auto-refresh every 30 seconds
  refreshTimer = setInterval(loadDashboard, 30_000);
});
