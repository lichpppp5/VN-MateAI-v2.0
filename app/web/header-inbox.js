// SPDX-License-Identifier: Apache-2.0
// Copyright (c) 2026 Dương Thanh Lịch — VN-MateAI. See LICENSE and NOTICE.
/**
 * web/header-inbox.js — hai nút trên thanh header:
 *   ✉  "Hộp thư & Chỉ thị điều hành": việc CẦN BẠN QUYẾT — yêu cầu duyệt (HITL) + tác vụ bị leo thang / thất bại.
 *   🔔 "Thông báo an ninh & cảnh báo vận hành": sự cố đang mở + cảnh báo đã phát gần đây (kênh Telegram/Teams/Email…).
 * Mọi số / dòng lấy từ API thật; chấm đếm chỉ hiện khi có việc thật (trước đây chấm đỏ là hiệu ứng cố định, nút không làm gì).
 * Thiếu quyền (403) thì nói rõ "cần quyền …", không hiện số giả.
 */
const HeaderInbox = (() => {
  const POLL_MS = 60000;
  const SEEN_KEY = 'vnmateai_bell_seen';
  let timer = null;
  let openKind = null;
  const state = {
    mail: { pending: null, attention: null, denied: false, error: '' },
    bell: { incidents: null, alerts: null, infra: null, denied: false, error: '' },
  };

  const $ = (id) => document.getElementById(id);
  const e = (s) => _esc(s == null ? '' : String(s));
  const role = () => { try { return (JSON.parse(localStorage.getItem('vnmateai_user') || 'null') || {}).role || 'viewer'; } catch (_) { return 'viewer'; } };

  async function getJson(path) {
    const res = await apiFetch(`${API_BASE}${path}`);
    const data = await res.json().catch(() => ({}));
    return { ok: res.ok, status: res.status, data };
  }

  // ── nạp dữ liệu ────────────────────────────────────────────────────────────
  async function loadMail() {
    const m = state.mail;
    m.error = ''; m.denied = false;
    try {
      const [p, o] = await Promise.all([getJson('/api/v1/enterprise/hitl/pending'), getJson('/api/v1/ops/overview?hours=72')]);
      m.pending = p.ok && Array.isArray(p.data.pending_approvals) ? p.data.pending_approvals : (p.ok ? [] : null);
      if (o.status === 403) { m.attention = null; m.denied = true; }
      else if (o.ok) {
        m.attention = ((o.data.tasks && o.data.tasks.attention) || [])
          .filter((t) => t.kind !== 'incident' && ['ESCALATED', 'FAILED'].includes(t.status));
      } else m.attention = null;
      if (!p.ok && !o.ok && o.status !== 403) m.error = `Không tải được (HTTP ${p.status}/${o.status})`;
    } catch (err) { m.error = friendlyErrorText(err); }
  }

  async function loadBell() {
    const b = state.bell;
    b.error = ''; b.denied = false;
    try {
      const [i, n, m] = await Promise.all([getJson('/api/v1/ops/incidents?limit=50'), getJson('/api/v1/system/notifications'),
        getJson('/api/v1/monitoring/overview')]);
      b.infra = m.ok && m.data && m.data.configured ? (m.data.alerts || []).filter((a) => a.state === 'firing') : null;
      if (i.status === 403 || n.status === 403) { b.denied = true; b.incidents = null; b.alerts = null; return; }
      b.incidents = i.ok ? (i.data.incidents || []).filter((t) => !['COMPLETED', 'FAILED', 'CANCELLED'].includes(t.status)) : null;
      b.alerts = n.ok ? (n.data.history || []) : null;
      if (!i.ok && !n.ok) b.error = `Không tải được (HTTP ${i.status}/${n.status})`;
    } catch (err) { b.error = friendlyErrorText(err); }
  }

  const seenAt = () => Number(localStorage.getItem(SEEN_KEY) || 0);
  const unseenAlerts = () => (state.bell.alerts || []).filter((a) => !a.resolved && a.time > seenAt());

  function counts() {
    const m = state.mail, b = state.bell;
    return {
      mail: (m.pending ? m.pending.length : 0) + (m.attention ? m.attention.length : 0),
      bell: (b.incidents ? b.incidents.length : 0) + unseenAlerts().length,
    };
  }

  function paintBadges() {
    const c = counts();
    [['mail', 'mail-badge'], ['bell', 'bell-badge']].forEach(([k, id]) => {
      const el = $(id);
      if (!el) return;
      el.textContent = c[k] > 99 ? '99+' : String(c[k]);
      el.classList.toggle('hidden', c[k] === 0);
    });
  }

  // ── hiển thị bảng thả xuống ────────────────────────────────────────────────
  const SEV = { critical: 'text-rose-600 dark:text-rose-400', error: 'text-rose-600 dark:text-rose-400',
    warning: 'text-amber-600 dark:text-amber-400', info: 'text-sky-600 dark:text-sky-400' };
  const when = (t) => (typeof t === 'number' ? new Date(t * 1000).toLocaleString('vi-VN') : String(t || ''));
  const empty = (msg) => `<p class="px-4 py-6 text-center text-xs text-slate-500 dark:text-slate-400">${e(msg)}</p>`;
  const head = (title, sub) => `<div class="px-4 py-3 border-b border-slate-200 dark:border-slate-700"><p class="text-sm font-bold text-slate-900 dark:text-white">${e(title)}</p><p class="text-[11px] text-slate-500 dark:text-slate-400">${e(sub)}</p></div>`;
  const section = (title, body) => `<div class="px-4 pt-3 pb-1 text-[11px] font-bold uppercase tracking-wide text-slate-500 dark:text-slate-400">${e(title)}</div>${body}`;
  const link = (label, action) => `<button type="button" data-hi="${e(action)}" class="mt-2 text-[11px] font-semibold text-primary-600 dark:text-primary-400 hover:underline">${e(label)}</button>`;

  function mailHtml() {
    const m = state.mail;
    if (m.error) return head('Hộp thư & Chỉ thị điều hành', 'Việc cần bạn quyết định') + empty(m.error);
    const isAdmin = role() === 'admin';
    const pend = (m.pending || []).map((it) => `
      <div class="mx-3 mb-2 rounded-xl border border-amber-500/30 bg-amber-500/5 dark:bg-amber-500/10 p-2.5">
        <div class="flex justify-between gap-2"><code class="text-[11px] font-mono font-bold text-amber-700 dark:text-amber-400">${e(it.id)}</code>
          <span class="px-1.5 rounded text-[11px] font-bold bg-rose-500/15 text-rose-600 dark:text-rose-400">L${e(it.risk_level ?? '?')}/5</span></div>
        <p class="text-xs font-semibold text-slate-800 dark:text-slate-100 mt-1">${e(it.description || it.action_name)}</p>
        <p class="text-[11px] text-slate-500 dark:text-slate-400 font-mono">${e(it.action_name)} · ${e(it.requested_by || 'AI')}</p>
        ${isAdmin ? `<div class="flex gap-1.5 mt-2">
          <button type="button" data-hi="approve" data-id="${e(it.id)}" class="flex-1 px-2 py-1.5 text-xs font-bold rounded-lg bg-emerald-600 hover:bg-emerald-700 text-white">Duyệt</button>
          <button type="button" data-hi="reject" data-id="${e(it.id)}" class="flex-1 px-2 py-1.5 text-xs font-bold rounded-lg bg-rose-600 hover:bg-rose-700 text-white">Từ chối</button></div>`
          : '<p class="text-[11px] text-slate-500 mt-1">Cần quyền Admin để quyết định.</p>'}
      </div>`).join('');
    const att = (m.attention || []).map((t) => `
      <div class="mx-3 mb-2 rounded-xl border border-slate-200 dark:border-slate-700 p-2.5">
        <div class="flex justify-between gap-2"><code class="text-[11px] font-mono text-slate-500 dark:text-slate-400">${e(t.task_id)}</code>
          <span class="text-[11px] font-bold ${t.status === 'FAILED' ? SEV.error : SEV.warning}">${e(t.status === 'ESCALATED' ? 'Cần người xử lý' : 'Thất bại')}</span></div>
        <p class="text-xs font-semibold text-slate-800 dark:text-slate-100 mt-1">${e(t.title)}</p>
        ${t.result_summary ? `<p class="text-[11px] text-slate-500 dark:text-slate-400">${e(t.result_summary)}</p>` : ''}
      </div>`).join('');
    let body = '';
    const hasItems = !!((m.pending && m.pending.length) || (m.attention && m.attention.length));
    if (m.pending && m.pending.length) body += section(`Chờ bạn duyệt (${m.pending.length})`, pend);
    if (m.attention && m.attention.length) body += section(`Cần xử lý / xác nhận (${m.attention.length})`, att + `<div class="px-4 pb-1">${link('Mở Bảng điều khiển để xác nhận →', 'dashboard')}</div>`);
    if (!hasItems) body = empty('Không có chỉ thị nào đang chờ bạn.');
    if (m.denied) body += `<p class="px-4 pb-2 text-[11px] text-slate-500">Danh sách tác vụ cần chú ý cần quyền manager/admin.</p>`;
    return head('Hộp thư & Chỉ thị điều hành', 'Việc cần bạn quyết định — dữ liệu thật từ hàng đợi duyệt và sổ tác vụ') + `<div class="max-h-96 overflow-y-auto pb-2">${body}</div>`;
  }

  function bellHtml() {
    const b = state.bell;
    if (b.denied) return head('Thông báo an ninh & cảnh báo vận hành', 'Sự cố và cảnh báo hệ thống') + empty('Cần quyền manager hoặc admin để xem thông báo vận hành.');
    if (b.error) return head('Thông báo an ninh & cảnh báo vận hành', 'Sự cố và cảnh báo hệ thống') + empty(b.error);
    const inc = (b.incidents || []).map((t) => `
      <div class="mx-3 mb-2 rounded-xl border border-rose-500/30 bg-rose-500/5 dark:bg-rose-500/10 p-2.5">
        <div class="flex justify-between gap-2"><span class="text-[11px] font-bold ${SEV.critical}">Sự cố · ${e(t.incident_phase || 'DETECTED')}</span>
          <span class="text-[11px] text-slate-500 dark:text-slate-400">${e(t.created_at)}</span></div>
        <p class="text-xs font-semibold text-slate-800 dark:text-slate-100 mt-1">${e(t.title)}</p>
        ${t.affected_assets ? `<p class="text-[11px] text-slate-500 dark:text-slate-400">Tài sản: ${e(t.affected_assets)}</p>` : ''}
      </div>`).join('');
    const alerts = (b.alerts || []).slice(0, 12).map((a) => `
      <div class="mx-3 mb-1.5 flex gap-2 text-xs">
        <span class="shrink-0 font-bold ${a.resolved ? 'text-emerald-600 dark:text-emerald-400' : (SEV[a.severity] || SEV.info)}">${a.resolved ? 'Đã khôi phục' : e(String(a.severity || 'info').toUpperCase())}</span>
        <span class="min-w-0"><span class="text-slate-800 dark:text-slate-100">${e(a.title)}</span>
          <span class="block text-[11px] text-slate-500 dark:text-slate-400">${e(a.source || '')} · ${e(when(a.time))} · gửi ${e(a.delivered ?? 0)} kênh</span></span>
      </div>`).join('');
    const infra = (b.infra || []).slice(0, 10).map((a) => `
      <div class="mx-3 mb-1.5 flex gap-2 text-xs">
        <span class="shrink-0 font-bold ${a.severity === 'critical' ? SEV.critical : (SEV[a.severity] || SEV.info)}">${e(String(a.severity).toUpperCase())}</span>
        <span class="min-w-0"><span class="text-slate-800 dark:text-slate-100">${e(a.name)}</span>
          <span class="block text-[11px] text-slate-500 dark:text-slate-400">${e(a.source)}${a.instance ? ' · ' + e(a.instance) : ''}</span></span>
      </div>`).join('');
    let body = '';
    if (b.incidents && b.incidents.length) body += section(`Sự cố đang mở (${b.incidents.length})`, inc);
    if (b.infra && b.infra.length) body += section(`Hạ tầng đang cảnh báo — Prometheus / Grafana (${b.infra.length})`, infra + `<div class="px-4 pb-1">${link('Mở Giám sát hạ tầng →', 'infra')}</div>`);
    if (b.alerts && b.alerts.length) body += section('Cảnh báo đã phát gần đây', alerts);
    if (!body) body = empty('Không có sự cố đang mở và chưa có cảnh báo nào được phát.');
    body += `<div class="px-4 pb-1">${link('Cấu hình kênh cảnh báo →', 'integration')}</div>`;
    return head('Thông báo an ninh & cảnh báo vận hành', 'Sự cố đang mở và cảnh báo đã gửi qua các kênh') + `<div class="max-h-96 overflow-y-auto pb-2">${body}</div>`;
  }

  function panel(kind) {
    const wrap = $(kind === 'mail' ? 'mail-wrap' : 'bell-wrap');
    if (!wrap) return null;
    let p = wrap.querySelector('[data-hi-panel]');
    if (!p) {
      p = document.createElement('div');
      p.dataset.hiPanel = kind;
      p.setAttribute('role', 'dialog');
      p.setAttribute('aria-label', kind === 'mail' ? 'Hộp thư và chỉ thị điều hành' : 'Thông báo an ninh và cảnh báo vận hành');
      p.className = 'hidden absolute right-0 top-11 z-[70] w-[min(92vw,26rem)] rounded-2xl border border-slate-200 dark:border-slate-700 bg-white dark:bg-slate-800 shadow-2xl text-left';
      p.addEventListener('click', onPanelClick);
      wrap.appendChild(p);
    }
    return p;
  }

  function render(kind) {
    const p = panel(kind);
    if (p) p.innerHTML = kind === 'mail' ? mailHtml() : bellHtml();
  }

  function closeAll() {
    document.querySelectorAll('[data-hi-panel]').forEach((p) => p.classList.add('hidden'));
    ['mail-btn', 'bell-btn'].forEach((id) => { const b = $(id); if (b) b.setAttribute('aria-expanded', 'false'); });
    openKind = null;
  }

  async function toggle(kind) {
    if (openKind === kind) return closeAll();
    closeAll();
    const p = panel(kind);
    if (!p) return;
    p.innerHTML = '<p class="px-4 py-6 text-center text-xs text-slate-500">Đang tải…</p>';
    p.classList.remove('hidden');
    $(kind === 'mail' ? 'mail-btn' : 'bell-btn').setAttribute('aria-expanded', 'true');
    openKind = kind;
    await (kind === 'mail' ? loadMail() : loadBell());
    if (openKind !== kind) return;
    render(kind);
    if (kind === 'bell') { localStorage.setItem(SEEN_KEY, String(Date.now() / 1000)); }   // đã xem cảnh báo tới giờ
    paintBadges();
  }

  async function onPanelClick(ev) {
    const t = ev.target.closest('[data-hi]');
    if (!t) return;
    const act = t.dataset.hi;
    if (act === 'dashboard') { closeAll(); switchTab('dashboard'); return; }
    if (act === 'integration') { closeAll(); switchTab('system-integration'); return; }
    if (act === 'infra') { closeAll(); switchTab('infra-monitor'); return; }
    if (act === 'approve' || act === 'reject') {
      t.disabled = true;
      try { await CommandCenter.decide(t.dataset.id, act === 'approve'); } finally { await refresh(); if (openKind) render(openKind); }
    }
  }

  async function refresh() {
    if (!getAuthToken()) return;
    await Promise.all([loadMail(), loadBell()]);
    paintBadges();
    if (openKind) render(openKind);
  }

  function start() {
    if (timer) return;
    refresh();
    timer = setInterval(() => { if (!document.hidden) refresh(); }, POLL_MS);
  }
  function stop() { if (timer) { clearInterval(timer); timer = null; } closeAll(); }

  document.addEventListener('click', (ev) => { if (openKind && !ev.target.closest('#mail-wrap, #bell-wrap')) closeAll(); });
  document.addEventListener('keydown', (ev) => { if (ev.key === 'Escape') closeAll(); });

  return { start, stop, toggle, refresh };
})();
window.HeaderInbox = HeaderInbox;
