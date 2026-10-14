// SPDX-License-Identifier: Apache-2.0
// Copyright (c) 2026 Dương Thanh Lịch — VN-MateAI. See LICENSE and NOTICE.
/**
 * web/llmops.js — mục "Chi phí & Đánh giá" trong tab Quản lý Trợ lý AI (docs/ai/llmops.md): mức dùng AI thật (token / lượt / chi phí theo giá
 * đã khai), ngân sách (off / warn / block), bộ kiểm chất lượng (câu hỏi + điều kiện đạt) và các lần chạy, ca bị TỤT so với lần trước.
 * Không tự đoán tiền: token của model chưa khai giá hiện riêng. Ghi (ngân sách, bộ kiểm, chạy) chỉ hiện cho admin. Chữ động đều qua _esc.
 */
const LlmOpsUI = (() => {
  const POLL_MS = 5000;
  let timer = null;
  let built = false;
  let canWrite = false;
  let selectedSet = null;
  let days = 30;

  const $ = (id) => document.getElementById(id);
  const e = (s) => _esc(s == null ? '' : String(s));
  const CARD = 'rounded-xl border border-slate-200 dark:border-slate-700 bg-white dark:bg-slate-800/60 shadow-sm p-4';
  const BTN = 'px-3 py-1.5 text-xs font-semibold rounded-lg transition active:scale-95 disabled:opacity-40';
  const INPUT = 'w-full px-3 py-2 text-xs rounded-lg border border-slate-200 dark:border-slate-600 bg-white dark:bg-slate-900 text-slate-800 dark:text-slate-100 font-mono';
  const COLOR = {
    emerald: 'bg-emerald-500/15 text-emerald-600 border-emerald-500/30', amber: 'bg-amber-500/15 text-amber-600 border-amber-500/30',
    rose: 'bg-rose-500/15 text-rose-600 border-rose-500/30', sky: 'bg-sky-500/15 text-sky-600 border-sky-500/30', slate: 'bg-slate-500/15 text-slate-500 border-slate-500/30',
  };
  const RUN_BADGE = { COMPLETED: ['Xong', 'emerald'], RUNNING: ['Đang chạy', 'sky'], FAILED: ['Lỗi', 'rose'], INTERRUPTED: ['Bị gián đoạn', 'rose'] };
  const STATE = { ok: ['Trong hạn mức', 'emerald'], warn: ['Sắp hết', 'amber'], exceeded: ['ĐÃ VƯỢT', 'rose'] };
  const PASS = { yes: ['Đạt', 'emerald'], no: ['Không đạt', 'rose'] };
  const BAR = { ok: 'bg-emerald-500', warn: 'bg-amber-500', exceeded: 'bg-rose-500' };
  const MODE_TEXT = { off: 'Tắt — không theo dõi hạn mức', warn: 'Chỉ cảnh báo (không chặn)', block: 'Chặn lượt AI mới khi đã vượt' };
  const LIMITS = [['daily_token_limit', 'Token / ngày (toàn hệ thống)', '1'], ['user_daily_token_limit', 'Token / ngày / người', '1'],
    ['daily_cost_limit', 'Chi phí / ngày', '0.01'], ['monthly_cost_limit', 'Chi phí / tháng', '0.01']];
  const SAMPLE = {
    name: 'Kiểm tra cơ bản',
    cases: [
      { id: 'chao-hoi', prompt: 'Xin chào, bạn là ai?', checks: [{ type: 'min_chars', value: 10 }, { type: 'tool_not_called', value: 'run_powershell_command' }] },
      { id: 'ha-tang', prompt: 'Máy chủ nào đang có cảnh báo?', checks: [{ type: 'tool_called', value: 'get_infra_status' }] },
    ],
  };

  const pill = (map, key) => { const [t, c] = map[key] || [key, 'slate']; return `<span class="px-1.5 py-0.5 rounded border text-[11px] font-bold whitespace-nowrap ${COLOR[c]}">${e(t)}</span>`; };
  const num = (n) => Number(n || 0).toLocaleString('vi-VN');
  const money = (n) => Number(n || 0).toLocaleString('vi-VN', { maximumFractionDigits: 4 });
  const pct = (r) => (r == null ? '—' : `${Math.round(r * 1000) / 10}%`);

  async function api(path, opts = {}) {
    const res = await apiFetch(`${API_BASE}/api/v1/llmops${path}`, opts);
    const data = await res.json().catch(() => ({}));
    return { ok: res.ok, status: res.status, data };
  }
  const send = (path, method, body) => api(path, { method, headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body || {}) });
  const err = (r) => apiErrorText(r.data, r.status);
  const role = () => { try { return (JSON.parse(localStorage.getItem('vnmateai_user') || 'null') || {}).role || 'viewer'; } catch (_) { return 'viewer'; } };

  const table = (head, rows, empty) => rows.length
    ? `<table class="w-full text-xs"><thead><tr class="text-left text-slate-500 dark:text-slate-400">${head.map((h) => `<th class="py-1 pr-3 font-semibold">${h}</th>`).join('')}</tr></thead><tbody class="divide-y divide-slate-100 dark:divide-slate-700/50">${rows.join('')}</tbody></table>`
    : `<p class="text-xs text-slate-500 dark:text-slate-400 py-2">${e(empty)}</p>`;
  const tile = (label, value, sub) => `<div class="rounded-lg border border-slate-200 dark:border-slate-700 p-3"><div class="text-[11px] text-slate-500 dark:text-slate-400">${e(label)}</div>
    <div class="text-lg font-bold text-slate-800 dark:text-slate-100">${e(value)}</div>${sub ? `<div class="text-[11px] text-slate-500">${e(sub)}</div>` : ''}</div>`;

  function skeleton() {
    return `
    <div class="flex flex-col sm:flex-row sm:items-center justify-between gap-3">
      <p class="text-xs text-slate-600 dark:text-slate-300">Số token THẬT do nhà cung cấp báo. Chi phí chỉ tính cho model đã khai giá trong danh mục model — phần chưa khai giá hiện riêng, không đoán.</p>
      <div class="flex gap-2 shrink-0"><select id="lo-days" aria-label="Khoảng thời gian" class="px-2 py-1.5 text-xs rounded-lg border border-slate-200 dark:border-slate-600 bg-white dark:bg-slate-900">
        <option value="1">Hôm nay</option><option value="7">7 ngày</option><option value="30" selected>30 ngày</option><option value="90">90 ngày</option></select>
        <button type="button" data-lo="refresh" class="${BTN} bg-primary-600 hover:bg-primary-700 text-white">Làm mới</button></div>
    </div>
    <div class="${CARD} space-y-3"><h3 class="text-sm font-bold text-slate-800 dark:text-slate-100">Mức dùng AI</h3>
      <div id="lo-totals" class="grid grid-cols-2 lg:grid-cols-4 gap-3"></div><p id="lo-note" class="text-[11px] text-slate-500"></p>
      <div id="lo-days-chart" class="space-y-1"></div>
      <div class="grid grid-cols-1 xl:grid-cols-2 gap-4"><div><h4 class="text-xs font-bold mb-1 text-slate-700 dark:text-slate-200">Theo model</h4><div id="lo-by-model" class="overflow-x-auto"></div></div>
        <div><h4 class="text-xs font-bold mb-1 text-slate-700 dark:text-slate-200">Theo người / thiết bị</h4><div id="lo-by-user" class="overflow-x-auto"></div></div></div></div>
    <div class="${CARD} space-y-3"><h3 class="text-sm font-bold text-slate-800 dark:text-slate-100">Ngân sách</h3>
      <div id="lo-budget" class="space-y-2 text-xs"></div>
      <div id="lo-budget-form" class="hidden space-y-2">
        <div class="grid grid-cols-1 sm:grid-cols-3 gap-3">
          <label class="block"><span class="text-[11px] font-semibold text-slate-600 dark:text-slate-300 mb-1 block">Chế độ</span>
            <select id="lo-mode" class="${INPUT}" aria-label="Chế độ ngân sách">${Object.entries(MODE_TEXT).map(([k, t]) => `<option value="${k}">${e(t)}</option>`).join('')}</select></label>
          ${LIMITS.map(([k, label, step]) => `<label class="block"><span class="text-[11px] font-semibold text-slate-600 dark:text-slate-300 mb-1 block">${e(label)} <span class="font-normal text-slate-500">(0 = không giới hạn)</span></span>
            <input id="lo-${k}" type="number" min="0" step="${step}" class="${INPUT}" aria-label="${e(label)}" /></label>`).join('')}
          <label class="block"><span class="text-[11px] font-semibold text-slate-600 dark:text-slate-300 mb-1 block">Cảnh báo từ (%)</span>
            <input id="lo-warn_ratio" type="number" min="1" max="100" step="1" class="${INPUT}" aria-label="Ngưỡng cảnh báo phần trăm" /></label>
        </div>
        <div class="flex items-center gap-3"><button type="button" data-lo="save-budget" class="${BTN} bg-primary-600 hover:bg-primary-700 text-white">Lưu ngân sách</button><span id="lo-budget-msg" class="text-xs"></span></div>
      </div></div>
    <div class="${CARD} space-y-3"><h3 class="text-sm font-bold text-slate-800 dark:text-slate-100">Đánh giá chất lượng</h3>
      <p class="text-[11px] text-slate-500">Mỗi ca = một câu hỏi + điều kiện đạt (contains, not_contains, regex, tool_called, tool_not_called, min_chars, max_chars). Chạy qua vòng agent thật nhưng CHỈ công cụ chỉ đọc; mỗi lần chạy tốn token thật. "Đạt" chỉ nghĩa là thỏa điều kiện đã viết.</p>
      <div class="grid grid-cols-1 xl:grid-cols-3 gap-4">
        <div><div id="lo-sets" class="space-y-1.5 text-xs"></div>
          <button type="button" data-lo="new-set" id="lo-new-set" class="${BTN} border border-slate-300 dark:border-slate-600 text-slate-700 dark:text-slate-200 mt-3 hidden">+ Bộ kiểm mới (chèn mẫu)</button></div>
        <div class="xl:col-span-2 space-y-2" id="lo-set-editor">
          <input id="lo-set-id" class="${INPUT}" placeholder="mã bộ kiểm (a-z 0-9 _ -)" aria-label="Mã bộ kiểm" />
          <textarea id="lo-set-json" rows="12" spellcheck="false" class="${INPUT}" aria-label="Bộ kiểm dạng JSON" placeholder='{"name": "...", "cases": [...]}'></textarea>
          <p id="lo-set-msg" class="text-xs min-h-[1rem]"></p>
          <div class="flex flex-wrap gap-2" id="lo-set-actions">
            <button type="button" data-lo="save-set" class="${BTN} bg-primary-600 hover:bg-primary-700 text-white">Lưu</button>
            <button type="button" data-lo="run-set" class="${BTN} bg-emerald-600 hover:bg-emerald-700 text-white">Chạy đánh giá</button>
            <button type="button" data-lo="delete-set" class="${BTN} border border-rose-300 text-rose-600">Xoá</button></div></div>
      </div>
      <h4 class="text-xs font-bold text-slate-700 dark:text-slate-200">Các lần chạy</h4><div id="lo-runs" class="overflow-x-auto"></div><div id="lo-run-detail" class="text-xs"></div></div>`;
  }

  function build() {
    const root = $('llmops-root');
    if (!root || built) return;
    root.innerHTML = skeleton();
    root.addEventListener('click', onClick);
    const sel = $('lo-days');
    if (sel && sel.addEventListener) sel.addEventListener('change', () => { days = Number(sel.value) || 30; loadUsage(); });
    built = true;
  }

  // ── mức dùng + ngân sách ──────────────────────────────────────────────────
  function renderBudget(b) {
    const checks = b.checks || [];
    $('lo-budget').innerHTML = `<p>Chế độ: <b>${e(MODE_TEXT[b.mode] || b.mode)}</b> · cảnh báo từ ${e(pct(b.warn_ratio))}</p>`
      + (checks.length ? checks.map((c) => `<div><div class="flex justify-between gap-2"><span>${e(c.label)}</span><span>${e(c.key.includes('cost') ? money(c.used) : num(c.used))} / ${e(c.key.includes('cost') ? money(c.limit) : num(c.limit))} ${pill(STATE, c.state)}</span></div>
          <div class="h-2 rounded-full bg-slate-200 dark:bg-slate-700 overflow-hidden"><div class="h-2 ${BAR[c.state] || 'bg-slate-500'}" style="width:${Math.min(100, Math.round((c.ratio || 0) * 100))}%"></div></div></div>`).join('')
        : '<p class="text-slate-500">Chưa đặt hạn mức nào (0 = không giới hạn).</p>');
    $('lo-budget-form').classList.toggle('hidden', !canWrite);
    if (canWrite) {
      $('lo-mode').value = b.mode;
      LIMITS.forEach(([k]) => { $(`lo-${k}`).value = (b.limits || {})[k] ?? 0; });
      $('lo-warn_ratio').value = Math.round((b.warn_ratio || 0.8) * 100);
    }
  }

  async function loadUsage() {
    const r = await api(`/usage?days=${days}`);
    if (r.status === 401 || r.status === 403) { $('lo-totals').innerHTML = '<p class="text-xs text-slate-500 col-span-4">Bạn không có quyền xem mức dùng AI (cần vai trò manager hoặc admin).</p>'; return false; }
    if (!r.ok) { $('lo-totals').innerHTML = `<p class="text-xs text-rose-600 col-span-4">Không tải được: ${e(err(r))}</p>`; return false; }
    const d = r.data, t = d.total || {};
    canWrite = role() === 'admin';
    $('lo-totals').innerHTML = tile('Token', num(t.tokens), `từ ${d.since}`) + tile('Lượt gọi model', num(t.calls))
      + tile('Chi phí (model đã khai giá)', money(t.cost)) + tile('Token chưa khai giá', num(t.unpriced_tokens), t.unpriced_tokens ? 'không quy ra tiền' : '');
    $('lo-note').textContent = d.note || '';
    const byDay = d.by_day || [];
    const max = Math.max(1, ...byDay.map((x) => x.tokens));
    $('lo-days-chart').innerHTML = byDay.length > 1 ? byDay.map((x) => `<div class="flex items-center gap-2 text-[11px]"><span class="w-20 font-mono text-slate-500">${e(x.key)}</span>
        <div class="flex-1 h-2 rounded-full bg-slate-200 dark:bg-slate-700 overflow-hidden"><div class="h-2 bg-primary-600" style="width:${Math.round((x.tokens / max) * 100)}%"></div></div>
        <span class="w-24 text-right">${e(num(x.tokens))}</span></div>`).join('') : '';
    const row = (x) => `<tr><td class="py-1 pr-3 font-mono">${e(x.key)}</td><td class="pr-3 text-right">${e(num(x.calls))}</td><td class="pr-3 text-right">${e(num(x.tokens))}</td>
      <td class="text-right">${x.unpriced_tokens && x.unpriced_tokens === x.tokens ? '<span class="text-slate-500">chưa khai giá</span>' : e(money(x.cost))}</td></tr>`;
    const head = ['Tên', 'Lượt', 'Token', 'Chi phí'];
    $('lo-by-model').innerHTML = table(head, (d.by_model || []).map(row), 'Chưa có lượt gọi nào.');
    $('lo-by-user').innerHTML = table(head, (d.by_user || []).map(row), 'Chưa có lượt gọi nào.');
    renderBudget(d.budget || {});
    return true;
  }

  async function saveBudget(btn) {
    const body = { budget_mode: $('lo-mode').value, warn_ratio: Number($('lo-warn_ratio').value) / 100 };
    LIMITS.forEach(([k]) => { body[k] = Number($(`lo-${k}`).value || 0); });
    if (body.budget_mode === 'block' && !window.confirm('Chế độ CHẶN: khi vượt hạn mức, mọi lượt AI mới (portal, giọng nói, robot) sẽ bị từ chối. Tiếp tục?')) return;
    btn.disabled = true;
    try {
      const r = await send('/budget', 'POST', body);
      const m = $('lo-budget-msg');
      m.className = `text-xs ${r.ok ? 'text-emerald-600' : 'text-rose-600'}`;
      m.textContent = r.ok ? '✔ Đã lưu ngân sách.' : `✖ ${err(r)}`;
      if (r.ok) renderBudget(r.data);
    } finally { btn.disabled = false; }
  }

  // ── bộ kiểm + lần chạy ────────────────────────────────────────────────────
  async function loadSets() {
    const r = await api('/eval/sets');
    if (!r.ok) { $('lo-sets').innerHTML = `<p class="text-rose-600">${e(err(r))}</p>`; return; }
    const sets = r.data.sets || [];
    if (selectedSet && !sets.some((s) => s.set_id === selectedSet)) selectedSet = null;
    $('lo-sets').innerHTML = sets.length ? sets.map((s) => {
      const lr = s.last_run;
      return `<button type="button" data-lo="select-set" data-id="${e(s.set_id)}" class="w-full text-left px-3 py-2 rounded-lg border ${s.set_id === selectedSet ? 'border-primary-500 bg-primary-500/10' : 'border-slate-200 dark:border-slate-700'}">
        <span class="font-semibold text-slate-800 dark:text-slate-100">${e(s.name)}</span>
        <span class="block text-[11px] font-mono text-slate-500">${e(s.set_id)} · ${e(s.cases)} ca${lr ? ` · lần cuối ${e(lr.passed)}/${e(lr.total)} (${e(RUN_BADGE[lr.status] ? RUN_BADGE[lr.status][0] : lr.status)})` : ' · chưa chạy'}</span></button>`;
    }).join('') : '<p class="text-slate-500 dark:text-slate-400">Chưa có bộ kiểm nào.</p>';
    $('lo-new-set').classList.toggle('hidden', !canWrite);
    $('lo-set-actions').classList.toggle('hidden', !canWrite);
  }

  async function selectSet(id) {
    selectedSet = id;
    $('lo-set-msg').textContent = ''; $('lo-run-detail').innerHTML = '';
    const r = await api(`/eval/sets/${encodeURIComponent(id)}`);
    if (r.ok) { $('lo-set-id').value = r.data.set_id; $('lo-set-json').value = JSON.stringify({ name: r.data.name, cases: r.data.cases }, null, 2); }
    await loadSets();
    await loadRuns();
  }

  function setMsg(ok, text) { const m = $('lo-set-msg'); m.className = `text-xs ${ok ? 'text-emerald-600' : 'text-rose-600'}`; m.textContent = text; }

  async function saveSet(btn) {
    const id = ($('lo-set-id').value || '').trim();
    let body;
    try { body = JSON.parse($('lo-set-json').value); } catch (ex) { setMsg(false, `JSON không hợp lệ: ${ex.message}`); return; }
    if (!id) { setMsg(false, 'Cần mã bộ kiểm.'); return; }
    btn.disabled = true;
    try {
      const r = await send(`/eval/sets/${encodeURIComponent(id)}`, 'PUT', body);
      if (!r.ok) { setMsg(false, `✖ ${err(r)}`); return; }
      selectedSet = r.data.set_id;
      await loadSets();
      setMsg(true, `✔ Đã lưu «${r.data.name}» (${r.data.cases.length} ca).`);
    } finally { btn.disabled = false; }
  }

  async function runSet(btn) {
    if (!selectedSet) { setMsg(false, 'Chọn hoặc lưu một bộ kiểm trước.'); return; }
    if (!window.confirm('Chạy đánh giá? Mỗi ca là một lượt AI thật và tốn token.')) return;
    btn.disabled = true;
    try {
      const r = await send(`/eval/sets/${encodeURIComponent(selectedSet)}/run`, 'POST');
      if (!r.ok) { setMsg(false, `✖ ${err(r)}`); return; }
      setMsg(true, `▶ Đang chạy ${r.data.total} ca (${r.data.run_id})…`);
      await loadRuns();
      await detail(r.data.run_id);
    } finally { btn.disabled = false; }
  }

  let lastDetail = null;
  async function loadRuns() {
    const r = await api(`/eval/runs?limit=30${selectedSet ? `&set_id=${encodeURIComponent(selectedSet)}` : ''}`);
    if (!r.ok) { $('lo-runs').innerHTML = `<p class="text-rose-600 text-xs">${e(err(r))}</p>`; return []; }
    const runs = r.data.runs || [];
    $('lo-runs').innerHTML = table(['Thời gian', 'Bộ kiểm', 'Trạng thái', 'Đạt', 'Token', 'Model', ''], runs.map((x) => `<tr>
      <td class="py-1.5 pr-3 font-mono">${e(String(x.created_at || '').slice(0, 19))}</td><td class="pr-3">${e(x.set_id)}</td><td class="pr-3">${pill(RUN_BADGE, x.status)}</td>
      <td class="pr-3">${e(x.passed)}/${e(x.total)} (${e(pct(x.pass_rate))})</td><td class="pr-3">${e(num(x.total_tokens))}</td><td class="pr-3 font-mono">${e((x.models || []).join(', '))}</td>
      <td><button type="button" data-lo="detail" data-id="${e(x.run_id)}" class="${BTN} border border-slate-300 dark:border-slate-600">Chi tiết</button></td></tr>`), 'Chưa có lần chạy nào.');
    if (lastDetail && runs.some((x) => x.run_id === lastDetail && x.status === 'RUNNING')) await detail(lastDetail);
    return runs;
  }

  async function detail(id) {
    lastDetail = id;
    const r = await api(`/eval/runs/${encodeURIComponent(id)}`);
    if (!r.ok) { $('lo-run-detail').innerHTML = `<span class="text-rose-600">${e(err(r))}</span>`; return; }
    const d = r.data, c = d.comparison;
    const regressed = new Set((c && c.regressed) || []);
    const cmp = c ? `<p class="${c.regressed.length ? 'text-rose-600 font-semibold' : 'text-slate-600 dark:text-slate-300'}">So với lần trước (${e(c.previous_run)}, ${e(pct(c.previous_pass_rate))}): ${c.delta == null ? '—' : `${c.delta >= 0 ? '+' : ''}${e(pct(c.delta))}`}`
      + `${c.regressed.length ? ` · ✖ TỤT: ${e(c.regressed.join(', '))}` : ' · không ca nào tụt'}${c.fixed.length ? ` · ✔ đã sửa: ${e(c.fixed.join(', '))}` : ''}</p>` : '<p class="text-slate-500">Chưa có lần chạy xong trước đó để so.</p>';
    const rows = (d.results || []).map((x) => `<tr class="${regressed.has(x.id) ? 'bg-rose-500/10' : ''}"><td class="py-1 pr-3 font-mono">${e(x.id)}</td><td class="pr-3">${pill(PASS, x.passed ? 'yes' : 'no')}</td>
      <td class="pr-3">${x.error ? `<span class="text-rose-600">${e(x.error)}</span>` : (x.checks || []).map((k) => `<span class="${k.ok ? 'text-emerald-600' : 'text-rose-600'}">${k.ok ? '✔' : '✖'} ${e(k.type)} ${e(k.value)}</span>`).join('<br>')}</td>
      <td class="pr-3 font-mono">${e((x.tools || []).join(', '))}</td><td class="pr-3 max-w-[20rem] truncate" title="${e(x.reply)}">${e(x.reply)}</td><td>${e(x.latency_ms)} ms</td></tr>`);
    $('lo-run-detail').innerHTML = `<p class="font-semibold mt-2">Lần chạy ${e(d.run_id)} · ${pill(RUN_BADGE, d.status)} · ${e(d.passed)}/${e(d.total)} đạt</p>${d.error ? `<p class="text-rose-600">${e(d.error)}</p>` : ''}${cmp}`
      + table(['Ca', 'Kết quả', 'Điều kiện', 'Công cụ đã gọi', 'Trả lời', 'Thời gian'], rows, d.status === 'RUNNING' ? 'Đang chạy ca đầu tiên…' : 'Không có kết quả.');
  }

  async function onClick(ev) {
    const btn = ev.target.closest('[data-lo]');
    if (!btn) return;
    const act = btn.dataset.lo, id = btn.dataset.id;
    if (act === 'refresh') { if (await loadUsage()) { await loadSets(); await loadRuns(); } return; }
    if (act === 'save-budget') return saveBudget(btn);
    if (act === 'select-set') return selectSet(id);
    if (act === 'new-set') { selectedSet = null; $('lo-set-id').value = 'kiem-tra-co-ban'; $('lo-set-json').value = JSON.stringify(SAMPLE, null, 2); $('lo-set-msg').textContent = ''; return loadSets(); }
    if (act === 'save-set') return saveSet(btn);
    if (act === 'run-set') return runSet(btn);
    if (act === 'detail') return detail(id);
    if (act === 'delete-set' && selectedSet) {
      if (!window.confirm(`Xoá bộ kiểm "${selectedSet}"? Lịch sử các lần chạy được giữ.`)) return;
      const r = await api(`/eval/sets/${encodeURIComponent(selectedSet)}`, { method: 'DELETE' });
      showToast(r.ok ? 'Đã xoá' : `✖ ${err(r)}`, r.ok ? 'success' : 'error');
      selectedSet = null; $('lo-set-id').value = ''; $('lo-set-json').value = '';
      return loadSets();
    }
  }

  async function onEnter() {
    build();
    if (!(await loadUsage())) return;
    await loadSets();
    await loadRuns();
    if (!timer) timer = setInterval(() => { if (!document.hidden) loadRuns(); }, POLL_MS);
  }
  function onLeave() { if (timer) { clearInterval(timer); timer = null; } }

  return { onEnter, onLeave };
})();
window.LlmOpsUI = LlmOpsUI;
