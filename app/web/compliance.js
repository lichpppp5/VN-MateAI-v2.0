// SPDX-License-Identifier: Apache-2.0
// Copyright (c) 2026 Dương Thanh Lịch — VN-MateAI. See LICENSE and NOTICE.
/**
 * web/compliance.js — tab "Chính Sách & Tuân Thủ" (thẩm định thương mại 14/10/2026):
 *   Bản quyền + lượng sử dụng · Hiệu năng theo tháng · Dữ liệu cá nhân (sổ xử lý, tra cứu, xoá theo yêu cầu) · Danh mục kỹ năng.
 * Sự kiện gắn bằng addEventListener (không onclick inline — sẵn cho CSP chặt). Chữ động đều qua _esc.
 * Cần các hàm chung của app.js: _esc, API_BASE, apiFetch, showToast, currentUser (vai trò).
 */
const ComplianceUI = (() => {
  const $ = (id) => document.getElementById(id);
  const e = (s) => _esc(s == null ? '' : String(s));
  const CARD = 'rounded-xl border border-slate-200 dark:border-slate-700 bg-white dark:bg-slate-800/60 shadow-sm p-4 space-y-3';
  const BTN = 'px-3 py-1.5 text-xs font-semibold rounded-lg transition active:scale-95 disabled:opacity-40';
  const INPUT = 'w-full px-3 py-2 text-xs rounded-lg border border-slate-200 dark:border-slate-600 bg-white dark:bg-slate-900 text-slate-800 dark:text-slate-100';
  const TH = 'text-left font-semibold text-slate-500 dark:text-slate-400 px-2 py-1 whitespace-nowrap';
  const TD = 'px-2 py-1 border-t border-slate-100 dark:border-slate-700 align-top';
  let built = false;
  let catalog = [];

  async function getJson(path, opts) {
    const res = await apiFetch(`${API_BASE}${path}`, opts);
    const data = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error(data.detail || `HTTP ${res.status}`);
    return data;
  }
  const isAdmin = () => {
    try { return String((JSON.parse(localStorage.getItem('vnmateai_user') || '{}').role) || '').toLowerCase() === 'admin'; } catch (_) { return false; }
  };
  const ms = (v) => (v == null ? '—' : `${(v / 1000).toFixed(1)} s`);
  const pct = (v) => (v == null ? '—' : `${(v * 100).toFixed(1)}%`);

  function build() {
    const root = $('compliance-root');
    if (!root) return;
    root.innerHTML = `
      <div class="${CARD}"><div class="flex items-center justify-between"><h3 class="text-sm font-bold">Bản quyền &amp; lượng sử dụng</h3>
        <button type="button" id="cp-lic-refresh" class="${BTN} bg-slate-100 dark:bg-slate-700">Làm mới</button></div>
        <div id="cp-lic-body" class="text-xs text-slate-600 dark:text-slate-300">Đang tải…</div>
        <details id="cp-lic-upload-box" class="text-xs ${isAdmin() ? '' : 'hidden'}"><summary class="cursor-pointer font-semibold">Nạp tệp bản quyền (license.json)</summary>
          <textarea id="cp-lic-json" rows="5" class="${INPUT} font-mono mt-2" placeholder='{"license": {...}, "signature": "..."}'></textarea>
          <button type="button" id="cp-lic-upload" class="${BTN} bg-cyan-600 text-white mt-2">Kiểm chữ ký &amp; nạp</button></details></div>

      <div class="${CARD}"><div class="flex items-center justify-between"><h3 class="text-sm font-bold">Hiệu năng AI theo tháng</h3>
        <button type="button" id="cp-perf-csv" class="${BTN} bg-slate-100 dark:bg-slate-700">Tải CSV</button></div>
        <div id="cp-perf-body" class="overflow-x-auto text-xs">Đang tải…</div></div>

      <div class="${CARD} ${isAdmin() ? '' : 'hidden'}"><h3 class="text-sm font-bold">Dữ liệu cá nhân</h3>
        <p class="text-xs text-slate-500">Tra cứu / xoá theo yêu cầu của chủ thể (Luật Bảo vệ dữ liệu cá nhân 2025). Nhật ký kiểm toán được giữ lại theo nghĩa vụ an toàn thông tin.</p>
        <div class="grid md:grid-cols-3 gap-2"><input id="cp-subject" class="${INPUT}" placeholder="Tên đăng nhập, email, SĐT hoặc họ tên đầy đủ" />
          <button type="button" id="cp-lookup" class="${BTN} bg-slate-100 dark:bg-slate-700">Tra cứu</button>
          <div class="flex gap-2"><input id="cp-confirm" class="${INPUT}" placeholder="Gõ lại định danh để xoá" />
          <button type="button" id="cp-erase" class="${BTN} bg-rose-600 text-white whitespace-nowrap">Xoá / ẩn danh</button></div></div>
        <pre id="cp-subject-out" class="text-[11px] bg-slate-50 dark:bg-slate-900 rounded-lg p-2 overflow-x-auto max-h-64 hidden"></pre>
        <details class="text-xs"><summary class="cursor-pointer font-semibold">Sổ hoạt động xử lý</summary><div id="cp-register" class="overflow-x-auto mt-2">Đang tải…</div></details></div>

      <div class="${CARD}"><div class="flex items-center justify-between gap-2 flex-wrap"><h3 class="text-sm font-bold">Danh mục kỹ năng</h3>
        <select id="cp-cat-filter" class="${INPUT} max-w-xs"><option value="all">Tất cả</option><option value="commercial">Có trong bản thương mại</option>
          <option value="hidden">Bị ẩn ở phiên bản này</option><option value="missing">Thiếu nguồn dữ liệu</option><option value="untested">Chưa có test</option></select></div>
        <div id="cp-cat-summary" class="text-xs text-slate-500"></div><div id="cp-cat-body" class="overflow-x-auto text-xs max-h-[28rem] overflow-y-auto"></div></div>`;
    $('cp-lic-refresh').addEventListener('click', loadLicense);
    $('cp-lic-upload').addEventListener('click', uploadLicense);
    $('cp-perf-csv').addEventListener('click', downloadPerfCsv);
    $('cp-lookup').addEventListener('click', () => subject('lookup'));
    $('cp-erase').addEventListener('click', () => subject('erase'));
    $('cp-cat-filter').addEventListener('change', renderCatalog);
    built = true;
  }

  async function loadLicense() {
    const box = $('cp-lic-body');
    try {
      const u = await getJson('/api/v1/license/usage');
      const lim = u.limits;
      const row = (label, used, max) => `<div class="flex justify-between"><span>${e(label)}</span><span class="font-mono">${e(used)}${max ? ` / ${e(max)}` : ''}</span></div>`;
      box.innerHTML = `<p class="font-semibold ${u.state === 'valid' || u.state === 'internal' ? 'text-emerald-600' : 'text-amber-600'}">${e(u.message)}</p>
        <div class="grid sm:grid-cols-2 gap-x-6 mt-2">${row('Người dùng', u.users, lim && lim.max_users)}${row('Máy trạm', u.workstations, lim && lim.max_workstations)}
        ${row(`Lượt thoại tháng ${u.month}`, u.voice_turns_month)}${row('Token LLM tháng này', Number(u.llm_tokens_month || 0).toLocaleString('vi-VN'))}</div>`;
    } catch (err) { box.textContent = `Không tải được: ${err.message}`; }
  }

  async function uploadLicense() {
    let doc;
    try { doc = JSON.parse($('cp-lic-json').value); } catch (_) { showToast('Nội dung không phải JSON hợp lệ', 'error'); return; }
    try {
      const st = await getJson('/api/v1/license', { method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(doc) });
      showToast(st.message || 'Đã nạp bản quyền', 'success');
      $('cp-lic-json').value = '';
      loadLicense();
    } catch (err) { showToast(err.message, 'error'); }
  }

  async function loadPerf() {
    const box = $('cp-perf-body');
    try {
      const rows = (await getJson('/api/v1/reports/performance/monthly?months=13')).months.filter((r) => r.voice_turns || r.ai_tasks || r.llm_tokens);
      if (!rows.length) { box.textContent = 'Chưa có dữ liệu.'; return; }
      box.innerHTML = `<table class="w-full"><thead><tr>${['Tháng', 'Lượt thoại', 'Trọn lượt p50', 'p90', 'Tiếng trả lời đầu p50', 'Rơi dự phòng', 'Tác vụ AI thành công', 'Token']
        .map((h) => `<th class="${TH}">${h}</th>`).join('')}</tr></thead><tbody>${rows.map((r) => `<tr>
        <td class="${TD} font-mono">${e(r.month)}</td><td class="${TD}">${e(r.voice_turns)}</td><td class="${TD}">${ms(r.turn_ms_p50)}</td>
        <td class="${TD}">${ms(r.turn_ms_p90)}</td><td class="${TD}">${ms(r.first_answer_audio_ms_p50)}</td><td class="${TD}">${pct(r.model_fallback_rate)}</td>
        <td class="${TD}">${pct(r.ai_success_rate)} <span class="text-slate-400">(${e(r.ai_tasks_finished)})</span></td>
        <td class="${TD} font-mono">${Number(r.llm_tokens || 0).toLocaleString('vi-VN')}</td></tr>`).join('')}</tbody></table>`;
    } catch (err) { box.textContent = `Không tải được: ${err.message}`; }
  }

  async function downloadPerfCsv() {
    const res = await apiFetch(`${API_BASE}/api/v1/reports/performance/monthly?months=13&format=csv`);
    if (!res.ok) { showToast(`Không tải được (HTTP ${res.status})`, 'error'); return; }
    const url = URL.createObjectURL(await res.blob());
    const a = document.createElement('a');
    a.href = url; a.download = 'hieu_nang_theo_thang.csv';
    document.body.appendChild(a); a.click(); document.body.removeChild(a);
    setTimeout(() => URL.revokeObjectURL(url), 30000);
  }

  async function loadRegister() {
    if (!isAdmin()) return;
    const box = $('cp-register');
    try {
      const acts = (await getJson('/api/v1/privacy/register')).activities;
      box.innerHTML = `<table class="w-full"><thead><tr>${['Hoạt động', 'Dữ liệu', 'Mục đích', 'Nơi lưu', 'Thời hạn', 'Bên nhận']
        .map((h) => `<th class="${TH}">${h}</th>`).join('')}</tr></thead><tbody>${acts.map((a) => `<tr><td class="${TD} font-semibold">${e(a.activity)}</td>
        <td class="${TD}">${e((a.data || []).join(', '))}</td><td class="${TD}">${e(a.purpose)}</td><td class="${TD}">${e(a.storage)}</td>
        <td class="${TD}">${e(a.retention)}</td><td class="${TD}">${e(a.recipients)}</td></tr>`).join('')}</tbody></table>`;
    } catch (err) { box.textContent = `Không tải được: ${err.message}`; }
  }

  async function subject(kind) {
    const s = $('cp-subject').value.trim();
    const out = $('cp-subject-out');
    if (s.length < 3) { showToast('Nhập định danh (ít nhất 3 ký tự)', 'warning'); return; }
    if (kind === 'erase' && $('cp-confirm').value.trim() !== s) { showToast('Gõ lại đúng định danh vào ô xác nhận', 'warning'); return; }
    try {
      const body = kind === 'erase' ? { subject: s, confirm: $('cp-confirm').value.trim() } : { subject: s };
      const data = await getJson(`/api/v1/privacy/subject/${kind}`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) });
      out.textContent = JSON.stringify(data, null, 2);
      out.classList.remove('hidden');
      if (kind === 'erase') { showToast('Đã xoá / ẩn danh — xem chi tiết bên dưới', 'success'); $('cp-confirm').value = ''; }
    } catch (err) { showToast(err.message, 'error'); }
  }

  async function loadCatalog() {
    try {
      const data = await getJson('/api/v1/skills/catalog');
      catalog = data.skills || [];
      $('cp-cat-summary').textContent = `Phiên bản: ${data.edition} · ${data.count} kỹ năng · chưa có test trong bản thương mại: ${data.untested_in_commercial}`;
      renderCatalog();
    } catch (err) { $('cp-cat-body').textContent = `Không tải được: ${err.message}`; }
  }

  function renderCatalog() {
    const f = $('cp-cat-filter').value || 'all';
    const rows = catalog.filter((s) => f === 'all' || (f === 'commercial' && s.commercial) || (f === 'hidden' && s.blocked_in_edition)
      || (f === 'missing' && s.source_missing) || (f === 'untested' && !s.tested));
    const yes = (v) => (v ? '✓' : '—');
    $('cp-cat-body').innerHTML = `<table class="w-full"><thead><tr>${['Kỹ năng', 'Nhóm', 'Chủ sở hữu', 'Test', 'Bản thương mại', 'Ghi chú']
      .map((h) => `<th class="${TH}">${h}</th>`).join('')}</tr></thead><tbody>${rows.map((s) => `<tr>
      <td class="${TD} font-mono">${e(s.name)}</td><td class="${TD}">${e(s.tier)}</td><td class="${TD}">${e(s.owner)}</td>
      <td class="${TD}">${yes(s.tested)}</td><td class="${TD}">${yes(s.commercial)}</td>
      <td class="${TD} text-slate-500">${e(s.blocked_in_edition || s.source_missing || '')}</td></tr>`).join('')}</tbody></table>`;
  }

  function onEnter() {
    if (!built) build();
    loadLicense(); loadPerf(); loadRegister(); loadCatalog();
  }

  return { onEnter };
})();
