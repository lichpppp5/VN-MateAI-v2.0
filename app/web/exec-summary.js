// SPDX-License-Identifier: Apache-2.0
// Copyright (c) 2026 Dương Thanh Lịch — VN-MateAI. See LICENSE and NOTICE.
/**
 * web/exec-summary.js — "Tổng quan điều hành" đầu Bảng điều khiển cho giám đốc (thẩm định thương mại #22).
 * Ngôn ngữ kinh doanh, không thuật ngữ kỹ thuật: AI làm được bao nhiêu việc tháng này (so tháng trước), tỉ lệ thành công,
 * giờ công ước tính tiết kiệm (ghi rõ giả định), chi phí AI, và việc đang chờ người quyết định. Số liệu từ
 * GET /api/v1/reports/executive-summary (admin / manager) — không có số giả; lỗi thì nói rõ.
 */
const ExecSummary = (() => {
  const $ = (id) => document.getElementById(id);
  const e = (s) => _esc(s == null ? '' : String(s));
  const num = (n) => (n == null ? '—' : Number(n).toLocaleString('vi-VN'));
  const pct = (r) => (r == null ? '—' : `${Math.round(r * 1000) / 10}%`);
  const money = (n) => (n == null ? '—' : `$${Number(n).toLocaleString('vi-VN', { maximumFractionDigits: 2 })}`);
  const monthLabel = (k) => { const [y, m] = String(k || '').split('-'); return m ? `tháng ${Number(m)}/${y}` : ''; };
  const STATUS = { ESCALATED: 'Cần người xử lý', FAILED: 'Thất bại', WAITING_AUTHORIZATION: 'Chờ duyệt' };

  /** So với tháng trước: mũi tên + phần trăm thay đổi; `goodUp=false` khi tăng là xấu (chi phí). */
  function delta(cur, prev, goodUp = true) {
    if (cur == null || prev == null || prev === 0) return '<span class="text-slate-400">chưa có tháng trước để so</span>';
    const d = (cur - prev) / Math.abs(prev);
    if (Math.abs(d) < 0.005) return '<span class="text-slate-500">không đổi so với tháng trước</span>';
    const good = (d > 0) === goodUp;
    const cls = good ? 'text-emerald-600 dark:text-emerald-400' : 'text-rose-600 dark:text-rose-400';
    return `<span class="${cls}">${d > 0 ? '▲' : '▼'} ${Math.abs(Math.round(d * 100))}% so với tháng trước</span>`;
  }

  function tile(label, value, sub) {
    return `<div class="rounded-xl border border-slate-200 dark:border-slate-700 bg-white/70 dark:bg-slate-800/60 p-3">
      <p class="text-[11px] font-semibold text-slate-500 dark:text-slate-400">${label}</p>
      <p class="text-xl font-bold text-slate-800 dark:text-white mt-0.5">${value}</p>
      <p class="text-[11px] mt-0.5">${sub}</p></div>`;
  }

  function render(d) {
    const waiting = (d.approvals_pending || 0) + (d.incidents_open || 0) + (d.attention_total || 0);
    const tiles = [
      tile('Việc AI đã làm xong', num(d.ai_completed), delta(d.ai_completed, d.ai_completed_prev)),
      tile('Tỉ lệ thành công', pct(d.success_rate), delta(d.success_rate, d.success_rate_prev)),
      tile('Giờ công tiết kiệm (ước tính)', `${num(d.hours_saved_est)} giờ`,
        `<span class="text-slate-500 dark:text-slate-400">giả định ${num(d.hours_per_task)} giờ / việc</span>`),
      tile('Chi phí AI', money(d.llm_cost), delta(d.llm_cost, d.llm_cost_prev, false)),
    ].join('');
    const rows = (d.attention || []).map((t) => `<li class="flex items-center justify-between gap-2 py-1">
        <span class="truncate text-slate-700 dark:text-slate-200">${e(t.title)}</span>
        <span class="shrink-0 text-[11px] font-semibold text-amber-600 dark:text-amber-400">${e(STATUS[t.status] || t.status)}</span></li>`).join('');
    $('exec-summary-body').innerHTML = `
      <div class="grid grid-cols-2 lg:grid-cols-4 gap-3">${tiles}</div>
      <div class="rounded-xl border ${waiting ? 'border-amber-400/50 bg-amber-500/5' : 'border-emerald-400/40 bg-emerald-500/5'} p-3 space-y-2">
        <div class="flex flex-wrap items-center justify-between gap-2">
          <p class="text-sm font-bold text-slate-800 dark:text-white">${waiting ? 'Đang chờ anh/chị quyết định' : 'Không có việc nào đang chờ anh/chị'}</p>
          ${d.approvals_pending ? '<button type="button" id="exec-open-inbox" class="px-2.5 py-1 rounded-lg text-[11px] font-semibold border border-amber-400/60 text-amber-700 dark:text-amber-300">Mở hộp duyệt</button>' : ''}
        </div>
        <div class="flex flex-wrap gap-x-5 gap-y-1 text-xs text-slate-600 dark:text-slate-300">
          <span><b>${num(d.approvals_pending)}</b> yêu cầu chờ duyệt${d.approvals_high_risk ? ` (<b class="text-rose-600 dark:text-rose-400">${num(d.approvals_high_risk)}</b> rủi ro cao)` : ''}</span>
          <span><b>${num(d.incidents_open)}</b> sự cố đang mở</span>
          <span><b>${num(d.attention_total)}</b> việc cần người xử lý trong tháng</span>
        </div>
        ${rows ? `<ul class="text-xs divide-y divide-slate-200 dark:divide-slate-700">${rows}</ul>` : ''}
      </div>`;
    $('exec-summary-month').textContent = `${monthLabel(d.month)} · cập nhật ${new Date().toLocaleTimeString('vi-VN', { hour: '2-digit', minute: '2-digit' })}`;
    const inbox = $('exec-open-inbox');
    if (inbox) inbox.addEventListener('click', (ev) => { ev.stopPropagation(); if (typeof HeaderInbox !== 'undefined') HeaderInbox.toggle('mail'); });
  }

  async function onEnter() {
    const body = $('exec-summary-body');
    if (!body || !getAuthToken()) return;
    try {
      const res = await apiFetch(`${API_BASE}/api/v1/reports/executive-summary`);
      const data = await res.json().catch(() => ({}));
      if (res.status === 403) { $('exec-summary-card').classList.add('hidden'); return; }
      if (!res.ok) throw new Error(apiErrorText(data, res.status));
      render(data);
    } catch (err) {
      body.innerHTML = `<p class="text-xs text-rose-500">Không tải được tổng quan: ${e(err.message || err)}</p>`;
    }
  }

  document.addEventListener('DOMContentLoaded', () => {
    const btn = $('exec-summary-refresh');
    if (btn) btn.addEventListener('click', onEnter);
  });

  return { onEnter, render };
})();
window.ExecSummary = ExecSummary;
