// SPDX-License-Identifier: Apache-2.0
// Copyright (c) 2026 Dương Thanh Lịch — VN-MateAI. See LICENSE and NOTICE.
/**
 * web/orchestration.js — "Đội agent & Phiếu việc" trong tab Cổng Agent (docs/integrations/orchestration.md).
 *  - Đội agent: thẻ năng lực, khả dụng, bảng điểm THẬT (xong đã kiểm chứng / thất bại / đang làm); admin thêm, sửa, tạm dừng, xoá.
 *  - Giao việc: phiếu chuẩn (mục tiêu, năng lực, sản phẩm, tiêu chí "xong"); xem trước định tuyến + có cần duyệt không.
 *  - Phiếu việc: trạng thái, kết quả thẩm định từng tiêu chí, lịch sử; huỷ / giao lại / xác nhận.
 * Không có thuộc tính on* (CSP chặt): một bộ nghe click trên gốc theo data-or. Chữ động đều qua _esc.
 */
const OrchestrationUI = (() => {
  let built = false;
  let executors = [];
  let orders = [];
  let caps = [];
  let openOrder = null;
  let timer = null;

  const $ = (id) => document.getElementById(id);
  const e = (s) => _esc(s == null ? '' : String(s));
  const CARD = 'rounded-xl border border-slate-200 dark:border-slate-700 bg-white dark:bg-slate-800/60 shadow-sm p-4';
  const BTN = 'px-2.5 py-1 text-[11px] font-semibold rounded-lg transition active:scale-95 disabled:opacity-40';
  const INPUT = 'w-full px-3 py-2 text-xs rounded-lg border border-slate-200 dark:border-slate-600 bg-white dark:bg-slate-900 text-slate-800 dark:text-slate-100';
  const COLOR = {
    emerald: 'bg-emerald-500/15 text-emerald-600 border-emerald-500/30', amber: 'bg-amber-500/15 text-amber-600 border-amber-500/30',
    rose: 'bg-rose-500/15 text-rose-600 border-rose-500/30', sky: 'bg-sky-500/15 text-sky-600 border-sky-500/30', slate: 'bg-slate-500/15 text-slate-500 border-slate-500/30',
  };
  const KIND = { local: ['Nội bộ (vai)', 'sky'], a2a: ['A2A — agent ngoài', 'emerald'], devfleet: ['Dev Fleet', 'amber'] };
  const STATUS = {
    PLANNED: ['Chờ giao', 'slate'], WAITING_APPROVAL: ['Chờ duyệt', 'amber'], DISPATCHED: ['Agent đang làm', 'sky'], REVIEWING: ['Đang thẩm định', 'sky'],
    COMPLETED: ['Xong — đã thẩm định', 'emerald'], NEEDS_CONFIRMATION: ['Cần người xác nhận', 'amber'], FAILED: ['Thất bại', 'rose'],
    BLOCKED: ['Bị chặn', 'rose'], CANCELLED: ['Đã huỷ', 'slate'], INTERRUPTED: ['Gián đoạn', 'amber'],
  };
  const OPEN = ['PLANNED', 'WAITING_APPROVAL', 'DISPATCHED', 'REVIEWING'];
  const RETRYABLE = ['FAILED', 'BLOCKED', 'INTERRUPTED', 'CANCELLED', 'NEEDS_CONFIRMATION'];
  const pill = (map, k) => { const [t, c] = map[k] || [k, 'slate']; return `<span class="px-1.5 py-0.5 rounded border text-[11px] font-bold whitespace-nowrap ${COLOR[c]}">${e(t)}</span>`; };
  const field = (label, input) => `<label class="block"><span class="text-[11px] font-semibold text-slate-600 dark:text-slate-300 mb-1 block">${label}</span>${input}</label>`;
  const lines = (v) => String(v || '').split('\n').map((s) => s.trim()).filter(Boolean);
  const list = (v) => String(v || '').split(/[,;\n]/).map((s) => s.trim()).filter(Boolean);
  const role = () => { try { return (JSON.parse(localStorage.getItem('vnmateai_user') || 'null') || {}).role || 'viewer'; } catch (_) { return 'viewer'; } };
  const isAdmin = () => role() === 'admin';
  const me = () => { try { return (JSON.parse(localStorage.getItem('vnmateai_user') || 'null') || {}).username || ''; } catch (_) { return ''; } };

  async function api(path, opts = {}) {
    const res = await apiFetch(`${API_BASE}/api/v1/orchestration${path}`, opts);
    const data = await res.json().catch(() => ({}));
    return { ok: res.ok, status: res.status, data };
  }
  const send = (path, method, body) => api(path, { method, headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body || {}) });
  const err = (r) => apiErrorText(r.data, r.status);
  const msg = (id, text, ok) => { const el = $(id); if (el) { el.textContent = text || ''; el.className = `text-xs min-h-[1rem] mt-2 ${ok ? 'text-emerald-600' : 'text-rose-500'}`; } };

  function skeleton() {
    return `
    <div class="${CARD} space-y-3">
      <div class="flex items-center justify-between gap-2"><h3 class="text-sm font-bold text-slate-800 dark:text-slate-100">Đội agent</h3>
        <button type="button" data-or="refresh" class="${BTN} border border-slate-300 dark:border-slate-600">Làm mới</button></div>
      <p class="text-[11px] text-slate-500">Agent nhận phiếu việc từ VN-MateAI. <b>Nội bộ</b>: một vai (Dev, BA, DBA, Quản trị hệ thống…) chạy bằng AI của
        VN-MateAI chỉ với các công cụ được cấp, dưới quyền của người giao. <b>A2A</b>: agent ngoài trong Danh bạ. <b>Dev Fleet</b>: worker lập trình.
        Điểm = tỉ lệ việc <b>đã thẩm định đạt</b> (90 ngày), dùng để chọn agent.</p>
      <div id="or-exec" class="overflow-x-auto"></div>
      <details id="or-exec-form" class="hidden"><summary class="text-xs font-semibold cursor-pointer text-primary-600 dark:text-primary-400">+ Thêm / sửa agent thực thi</summary>
        <div class="grid grid-cols-1 sm:grid-cols-3 gap-3 mt-3">
          ${field('Mã (a-z 0-9 _ -)', `<input id="or-x-id" class="${INPUT} font-mono" aria-label="Mã agent thực thi" placeholder="dba" />`)}
          ${field('Loại', `<select id="or-x-kind" class="${INPUT}" aria-label="Loại agent thực thi"><option value="local">Nội bộ (vai)</option><option value="a2a">A2A (agent ngoài)</option><option value="devfleet">Dev Fleet</option></select>`)}
          ${field('Tên', `<input id="or-x-name" class="${INPUT}" aria-label="Tên agent thực thi" placeholder="Quản trị CSDL" />`)}
          ${field('Năng lực (cách nhau dấu phẩy)', `<input id="or-x-caps" class="${INPUT} font-mono" aria-label="Năng lực" placeholder="dba, sql" />`)}
          ${field('Trần rủi ro nhận việc', `<select id="or-x-risk" class="${INPUT}" aria-label="Trần rủi ro"><option value="1">1 — chỉ đọc</option><option value="2" selected>2 — soạn thảo / thao tác nhẹ</option><option value="3">3 — thay đổi (cần duyệt)</option><option value="4">4 — thay đổi lớn (cần duyệt)</option></select>`)}
          ${field('Số việc cùng lúc', `<input id="or-x-conc" type="number" min="1" max="10" value="2" class="${INPUT}" aria-label="Số việc cùng lúc" />`)}
          ${field('Mã agent A2A trong Danh bạ (loại A2A)', `<input id="or-x-ref" class="${INPUT} font-mono" aria-label="Mã agent A2A" />`)}
          <div class="sm:col-span-2">${field('Công cụ được cấp (loại Nội bộ — cách nhau dấu phẩy)', `<input id="or-x-tools" list="or-tools" class="${INPUT} font-mono" aria-label="Công cụ được cấp" placeholder="run_local_sql_check, list_data_sources" /><datalist id="or-tools"></datalist>`)}</div>
          <div class="sm:col-span-3">${field('Mô tả vai (agent đọc phần này)', `<textarea id="or-x-desc" rows="2" class="${INPUT}" aria-label="Mô tả vai"></textarea>`)}</div>
        </div>
        <p id="or-x-msg" class="text-xs min-h-[1rem] mt-2"></p>
        <button type="button" data-or="save-exec" class="px-3 py-1.5 text-xs font-semibold rounded-lg bg-primary-600 hover:bg-primary-700 text-white">Lưu agent</button>
      </details>
    </div>
    <div class="${CARD} space-y-3">
      <h3 class="text-sm font-bold text-slate-800 dark:text-slate-100">Giao việc</h3>
      <p class="text-[11px] text-slate-500">Phiếu phải nói rõ <b>thế nào là xong</b>. VN-MateAI tự thẩm định kết quả — agent báo xong chưa phải là xong.
        Rủi ro từ 3 cần người có thẩm quyền duyệt trước khi giao.</p>
      <div class="grid grid-cols-1 sm:grid-cols-3 gap-3">
        <div class="sm:col-span-2">${field('Mục tiêu', `<input id="or-o-obj" class="${INPUT}" aria-label="Mục tiêu" placeholder="Kiểm tra vì sao báo cáo doanh thu chạy chậm" />`)}</div>
        ${field('Năng lực', `<select id="or-o-cap" class="${INPUT}" aria-label="Năng lực cần"></select>`)}
        ${field('Sản phẩm phải nộp (mỗi dòng một mục)', `<textarea id="or-o-del" rows="3" class="${INPUT}" aria-label="Sản phẩm"></textarea>`)}
        ${field('Thế nào là xong (mỗi dòng một tiêu chí)', `<textarea id="or-o-done" rows="3" class="${INPUT}" aria-label="Tiêu chí xong"></textarea>`)}
        ${field('Ngữ cảnh tối thiểu', `<textarea id="or-o-ctx" rows="3" class="${INPUT}" aria-label="Ngữ cảnh"></textarea>`)}
        ${field('Rủi ro', `<select id="or-o-risk" class="${INPUT}" aria-label="Rủi ro phiếu"><option value="1">1 — chỉ đọc</option><option value="2" selected>2 — soạn thảo / thao tác nhẹ</option><option value="3">3 — có thay đổi (cần duyệt)</option><option value="4">4 — thay đổi lớn (cần duyệt)</option></select>`)}
        ${field('Thời hạn (phút)', `<input id="or-o-dl" type="number" min="1" max="1440" value="15" class="${INPUT}" aria-label="Thời hạn" />`)}
      </div>
      <p id="or-o-msg" class="text-xs min-h-[1rem]"></p>
      <div id="or-o-preview" class="hidden rounded-lg border border-slate-200 dark:border-slate-700 p-3 text-xs space-y-1"></div>
      <div class="flex gap-2">
        <button type="button" data-or="preview" class="${BTN} border border-slate-300 dark:border-slate-600">Xem trước</button>
        <button type="button" data-or="dispatch" class="px-3 py-1.5 text-xs font-semibold rounded-lg bg-primary-600 hover:bg-primary-700 text-white">Giao việc</button>
      </div>
    </div>
    <div class="${CARD} space-y-3">
      <div class="flex items-center justify-between gap-2"><h3 class="text-sm font-bold text-slate-800 dark:text-slate-100">Phiếu việc</h3>
        <select id="or-filter" class="${INPUT} w-auto" aria-label="Lọc trạng thái phiếu"><option value="">Tất cả</option>
          ${Object.entries(STATUS).map(([k, v]) => `<option value="${k}">${e(v[0])}</option>`).join('')}</select></div>
      <div id="or-orders" class="overflow-x-auto"></div>
      <div id="or-detail" class="hidden rounded-lg border border-slate-200 dark:border-slate-700 p-3 text-xs space-y-2"></div>
    </div>`;
  }

  function build() {
    const root = $('orchestration-root');
    if (!root || built) return;
    root.innerHTML = skeleton();
    root.addEventListener('click', onClick);
    const f = $('or-filter');
    if (f) f.addEventListener('change', loadOrders);
    built = true;
  }

  function renderExecutors() {
    const rows = executors.map((x) => {
      const sc = x.scorecard || {};
      const avail = x.available ? '<span class="text-emerald-600 font-semibold">nhận việc được</span>'
        : `<span class="text-rose-600 font-semibold">${e(x.unavailable_reason || 'không khả dụng')}</span>`;
      const score = `<b>${e(Math.round((sc.score || 0) * 100))}</b>/100 · ${e(sc.completed || 0)} xong · ${e(sc.failed || 0)} thất bại`
        + (sc.needs_confirmation ? ` · ${e(sc.needs_confirmation)} cần người` : '') + (sc.median_seconds != null ? ` · ~${e(Math.round(sc.median_seconds))} s/việc` : '');
      const caps = (x.capabilities || []).map((c) => `<span class="px-1 rounded bg-slate-100 dark:bg-slate-700 font-mono">${e(c)}</span>`).join(' ');
      const acts = isAdmin() ? `<div class="flex gap-1 justify-end">
          <button type="button" data-or="edit-exec" data-id="${e(x.executor_id)}" class="${BTN} border border-slate-300 dark:border-slate-600">Sửa</button>
          <button type="button" data-or="toggle-exec" data-id="${e(x.executor_id)}" data-to="${x.status === 'active' ? 'suspended' : 'active'}" class="${BTN} ${x.status === 'active' ? 'bg-amber-500/15 text-amber-700' : 'bg-emerald-500/15 text-emerald-700'}">${x.status === 'active' ? 'Tạm dừng' : 'Bật'}</button>
          <button type="button" data-or="delete-exec" data-id="${e(x.executor_id)}" class="${BTN} text-rose-600">Xoá</button></div>` : '';
      return `<tr class="border-t border-slate-100 dark:border-slate-700 align-top">
        <td class="py-2 pr-3"><div class="font-semibold text-slate-800 dark:text-slate-100">${e(x.name)}</div><div class="font-mono text-[11px] text-slate-500">${e(x.executor_id)} · v${e(x.version)}</div></td>
        <td class="py-2 pr-3">${pill(KIND, x.kind)}</td><td class="py-2 pr-3 text-[11px]">${caps}</td>
        <td class="py-2 pr-3 text-[11px]">trần rủi ro ${e(x.max_risk)} · ${e(sc.open || 0)}/${e(x.max_concurrent)} đang làm<br>${avail}</td>
        <td class="py-2 pr-3 text-[11px]">${score}</td><td class="py-2">${acts}</td></tr>`;
    }).join('');
    $('or-exec').innerHTML = executors.length
      ? `<table class="w-full text-xs"><thead><tr class="text-left text-[11px] text-slate-500"><th class="py-1 pr-3">Agent</th><th class="py-1 pr-3">Loại</th><th class="py-1 pr-3">Năng lực</th><th class="py-1 pr-3">Tình trạng</th><th class="py-1 pr-3">Bảng điểm</th><th></th></tr></thead><tbody>${rows}</tbody></table>`
      : '<p class="text-xs text-slate-500">Chưa có agent thực thi nào.' + (isAdmin() ? ' Thêm ở mục bên dưới — vd một vai "Quản trị CSDL" với các công cụ SQL chỉ đọc.' : ' Quản trị viên thêm ở đây.') + '</p>';
    $('or-exec-form').classList.toggle('hidden', !isAdmin());
    const sel = $('or-o-cap');
    if (sel) {
      const cur = sel.value;
      sel.innerHTML = caps.length ? caps.map((c) => `<option value="${e(c.capability)}">${e(c.capability)} (${e(c.executors.map((x) => x.name).join(', '))})</option>`).join('')
        : '<option value="">— chưa có năng lực nào —</option>';
      if (cur) sel.value = cur;
    }
  }

  function renderOrders() {
    const rows = orders.map((o) => `<tr class="border-t border-slate-100 dark:border-slate-700 cursor-pointer hover:bg-slate-50 dark:hover:bg-slate-700/40" data-or="open" data-id="${e(o.order_id)}">
        <td class="py-2 pr-3 font-mono text-[11px]" data-or="open" data-id="${e(o.order_id)}">${e(o.order_id)}</td>
        <td class="py-2 pr-3" data-or="open" data-id="${e(o.order_id)}">${e((o.objective || '').slice(0, 90))}</td>
        <td class="py-2 pr-3 text-[11px]" data-or="open" data-id="${e(o.order_id)}">${e(o.executor_name || o.executor_id || '—')}</td>
        <td class="py-2 pr-3" data-or="open" data-id="${e(o.order_id)}">${pill(STATUS, o.status)}</td>
        <td class="py-2 pr-3 text-[11px] text-slate-500" data-or="open" data-id="${e(o.order_id)}">lượt ${e(o.attempt)} · ${e(o.requested_by)} · ${e(o.created_at)}</td></tr>`).join('');
    $('or-orders').innerHTML = orders.length
      ? `<table class="w-full text-xs"><tbody>${rows}</tbody></table>` : '<p class="text-xs text-slate-500">Chưa có phiếu việc nào.</p>';
  }

  function renderDetail(o) {
    const box = $('or-detail');
    if (!o) { box.classList.add('hidden'); return; }
    const ver = o.verification || {};
    const rep = o.report || {};
    const mark = (ok) => (ok === true ? '<span class="text-emerald-600 font-bold">ĐẠT</span>' : ok === false ? '<span class="text-rose-600 font-bold">KHÔNG ĐẠT</span>'
      : '<span class="text-amber-600 font-bold">CHƯA KIỂM ĐƯỢC</span>');
    const checks = (ver.checks || []).map((c) => `<li>${mark(c.ok)} <span class="text-slate-500">[${e(c.kind)}]</span> ${e(c.criterion)} — ${e(c.detail)}</li>`).join('');
    const hist = (o.history || []).slice(-12).map((h) => `<li><span class="font-mono text-slate-500">${e(h.at)}</span> ${e(h.event)}${h.executor ? ` · ${e(h.executor)}` : ''}${h.reason ? ` · ${e(h.reason)}` : ''}${h.error ? ` · ${e(h.error)}` : ''}</li>`).join('');
    const mine = o.requested_by === me();
    const acts = [];
    if (OPEN.includes(o.status) && (mine || isAdmin())) acts.push(`<button type="button" data-or="cancel" data-id="${e(o.order_id)}" class="${BTN} text-rose-600 border border-rose-300">Huỷ phiếu</button>`);
    if (RETRYABLE.includes(o.status) && (mine || isAdmin())) acts.push(`<button type="button" data-or="retry" data-id="${e(o.order_id)}" class="${BTN} border border-slate-300 dark:border-slate-600">Giao lại</button>`);
    if (o.status === 'NEEDS_CONFIRMATION' && isAdmin()) {
      acts.push(`<input id="or-note" class="${INPUT} w-64" aria-label="Ghi chú xác nhận" placeholder="Bạn đã kiểm tra gì?" />`);
      acts.push(`<button type="button" data-or="accept" data-id="${e(o.order_id)}" class="${BTN} bg-emerald-600 text-white">Chấp nhận kết quả</button>`);
      acts.push(`<button type="button" data-or="reject" data-id="${e(o.order_id)}" class="${BTN} bg-rose-600 text-white">Không chấp nhận</button>`);
    }
    box.innerHTML = `
      <div class="flex flex-wrap items-center gap-2"><b class="font-mono">${e(o.order_id)}</b> ${pill(STATUS, o.status)}
        <span class="text-slate-500">agent ${e(o.executor_name || o.executor_id)} · năng lực ${e(o.capability)} · rủi ro ${e(o.risk)} · lượt ${e(o.attempt)}</span></div>
      <p><b>Mục tiêu:</b> ${e(o.objective)}</p>
      ${o.error ? `<p class="text-rose-600"><b>Lỗi:</b> ${e(o.error)}</p>` : ''}
      ${(o.routing || {}).reason ? `<p class="text-slate-500"><b>Vì sao chọn agent này:</b> ${e(o.routing.reason)}</p>` : ''}
      ${rep.result ? `<div><b>Kết quả agent nộp</b>${rep.untrusted ? ' <span class="text-amber-600">(agent ngoài — dữ liệu không tin cậy)</span>' : ''}:<pre class="whitespace-pre-wrap bg-slate-50 dark:bg-slate-900 rounded p-2 mt-1 max-h-64 overflow-auto">${e(rep.result)}</pre></div>` : ''}
      ${checks ? `<div><b>Thẩm định độc lập</b><ul class="list-disc pl-5 space-y-0.5 mt-1">${checks}</ul></div>` : ''}
      ${hist ? `<details><summary class="cursor-pointer font-semibold">Lịch sử</summary><ul class="pl-2 space-y-0.5 mt-1">${hist}</ul></details>` : ''}
      <div class="flex flex-wrap gap-2 items-center">${acts.join('')}</div><p id="or-d-msg" class="text-xs min-h-[1rem]"></p>`;
    box.classList.remove('hidden');
  }

  async function loadExecutors() {
    const [x, c] = await Promise.all([api('/executors'), api('/capabilities')]);
    if (!x.ok) { $('or-exec').innerHTML = `<p class="text-xs text-rose-500">Không tải được đội agent: ${e(err(x))}</p>`; return; }
    executors = x.data.executors || [];
    caps = c.ok ? (c.data.capabilities || []) : [];
    renderExecutors();
  }

  async function loadOrders() {
    const f = $('or-filter');
    const r = await api(`/orders?limit=100${f && f.value ? `&status=${encodeURIComponent(f.value)}` : ''}`);
    if (!r.ok) { $('or-orders').innerHTML = `<p class="text-xs text-rose-500">Không tải được phiếu việc: ${e(err(r))}</p>`; return; }
    orders = r.data.orders || [];
    renderOrders();
    if (openOrder) await openDetail(openOrder);
    schedule();
  }

  async function openDetail(id) {
    const r = await api(`/orders/${encodeURIComponent(id)}`);
    openOrder = r.ok ? id : null;
    renderDetail(r.ok ? r.data : null);
  }

  function schedule() {
    if (timer) { clearTimeout(timer); timer = null; }
    const visible = () => { const t = $('tab-agent-gateway'); return t && !t.classList.contains('hidden') && t.offsetParent !== null; };
    if (orders.some((o) => OPEN.includes(o.status))) {
      timer = setTimeout(() => { if (!document.hidden && visible()) loadOrders(); else schedule(); }, 10000);
    }
  }

  function orderPayload() {
    return {
      objective: $('or-o-obj').value.trim(), capability: $('or-o-cap').value, deliverables: lines($('or-o-del').value),
      done_criteria: lines($('or-o-done').value), context: $('or-o-ctx').value.trim(), risk: Number($('or-o-risk').value || 2),
      deadline_s: Math.max(60, Math.min(86400, Number($('or-o-dl').value || 15) * 60)),
    };
  }

  function fillExecForm(x) {
    $('or-exec-form').open = true;
    $('or-x-id').value = x.executor_id; $('or-x-kind').value = x.kind; $('or-x-name').value = x.name;
    $('or-x-caps').value = (x.capabilities || []).join(', '); $('or-x-risk').value = String(x.max_risk);
    $('or-x-conc').value = String(x.max_concurrent); $('or-x-ref').value = x.ref || ''; $('or-x-tools').value = (x.tools || []).join(', ');
    $('or-x-desc').value = x.description || '';
  }

  async function onClick(ev) {
    const t = ev.target.closest('[data-or]');
    if (!t) return;
    const act = t.dataset.or;
    const id = t.dataset.id;
    if (act === 'refresh') { await Promise.all([loadExecutors(), loadOrders()]); return; }
    if (act === 'open') { await openDetail(id); return; }
    if (act === 'edit-exec') { const x = executors.find((y) => y.executor_id === id); if (x) fillExecForm(x); return; }
    t.disabled = true;
    try {
      if (act === 'save-exec') {
        const kind = $('or-x-kind').value;
        const body = { kind, name: $('or-x-name').value.trim(), description: $('or-x-desc').value.trim(), capabilities: list($('or-x-caps').value),
          max_risk: Number($('or-x-risk').value), max_concurrent: Number($('or-x-conc').value || 2) };
        if (kind === 'local') body.tools = list($('or-x-tools').value);
        if (kind === 'a2a') body.ref = $('or-x-ref').value.trim();
        const r = await send(`/executors/${encodeURIComponent($('or-x-id').value.trim())}`, 'PUT', body);
        msg('or-x-msg', r.ok ? `Đã lưu (phiên bản ${r.data.version})` : err(r), r.ok);
        if (r.ok) await loadExecutors();
      } else if (act === 'toggle-exec') {
        const r = await send(`/executors/${encodeURIComponent(id)}/status`, 'POST', { status: t.dataset.to });
        if (!r.ok) showToast(err(r), 'error');
        await loadExecutors();
      } else if (act === 'delete-exec') {
        if (!confirm(`Xoá agent thực thi «${id}»? Lịch sử phiếu việc được giữ.`)) return;
        const r = await send(`/executors/${encodeURIComponent(id)}`, 'DELETE');
        if (!r.ok) showToast(err(r), 'error');
        await loadExecutors();
      } else if (act === 'preview') {
        const r = await send('/orders/preview', 'POST', orderPayload());
        const box = $('or-o-preview');
        if (!r.ok) { msg('or-o-msg', err(r), false); box.classList.add('hidden'); return; }
        msg('or-o-msg', '', true);
        const rt = r.data.routing || {};
        const rej = (rt.rejected || []).map((x) => `<li>${e(x.executor_id)}: ${e(x.reason)}</li>`).join('');
        box.innerHTML = rt.executor_id
          ? `<p><b>Sẽ giao cho:</b> ${e(rt.executor_id)} — ${e(rt.reason)}</p><p>${r.data.needs_approval ? '<span class="text-amber-600 font-semibold">Cần người có thẩm quyền duyệt trước khi giao.</span>' : 'Không cần duyệt — giao ngay khi có chỗ.'}</p>${rej ? `<ul class="list-disc pl-5 text-slate-500">${rej}</ul>` : ''}`
          : `<p class="text-rose-600">${e(rt.error)}</p>${rej ? `<ul class="list-disc pl-5 text-slate-500">${rej}</ul>` : ''}`;
        box.classList.remove('hidden');
      } else if (act === 'dispatch') {
        const r = await send('/orders', 'POST', { ...orderPayload(), idempotency_key: `ui-${Date.now()}` });
        msg('or-o-msg', r.ok ? (r.data.status === 'WAITING_APPROVAL' ? `Đã tạo ${r.data.order_id} — CHỜ DUYỆT trước khi giao.` : `Đã giao ${r.data.order_id} cho ${r.data.executor_name || r.data.executor_id}.`) : err(r), r.ok);
        if (r.ok) { openOrder = r.data.order_id; await loadOrders(); }
      } else if (act === 'cancel') {
        if (!confirm('Huỷ phiếu này? Agent ngoài có thể đã làm một phần.')) return;
        const r = await send(`/orders/${encodeURIComponent(id)}/cancel`, 'POST');
        if (!r.ok) showToast(err(r), 'error');
        await loadOrders();
      } else if (act === 'retry') {
        const r = await send(`/orders/${encodeURIComponent(id)}/retry`, 'POST');
        if (!r.ok) { msg('or-d-msg', err(r), false); return; }
        openOrder = r.data.order_id;
        await loadOrders();
      } else if (act === 'accept' || act === 'reject') {
        const note = ($('or-note') || {}).value || '';
        if (!note.trim()) { msg('or-d-msg', 'Ghi rõ bạn đã kiểm tra gì — đây là bằng chứng của việc xác nhận.', false); return; }
        const r = await send(`/orders/${encodeURIComponent(id)}/confirm`, 'POST', { accept: act === 'accept', note });
        if (!r.ok) { msg('or-d-msg', err(r), false); return; }
        await loadOrders();
      }
    } finally {
      t.disabled = false;
    }
  }

  async function loadTools() {
    if (!isAdmin()) return;
    const r = await api('/tools');
    const dl = $('or-tools');
    if (r.ok && dl) dl.innerHTML = (r.data.tools || []).map((n) => `<option value="${e(n)}"></option>`).join('');
  }

  async function onEnter() {
    if (!getAuthToken() || !['manager', 'admin'].includes(role())) return;
    build();
    await Promise.all([loadExecutors(), loadOrders(), loadTools()]);
  }

  return { onEnter, renderDetail, renderExecutors };
})();
window.OrchestrationUI = OrchestrationUI;
