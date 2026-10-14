// SPDX-License-Identifier: Apache-2.0
// Copyright (c) 2026 Dương Thanh Lịch — VN-MateAI. See LICENSE and NOTICE.
/**
 * web/playbooks.js — tab "Kịch bản vận hành": viết kịch bản (JSON), CHẠY THỬ (dry-run: từng bước sẽ được cho phép / chờ duyệt / bị chặn),
 * chạy thật (qua chính sách + duyệt), theo dõi lượt chạy, huỷ, và xác nhận kết quả chưa kiểm chứng.
 * Giao diện KHÔNG tự coi "đã xong": hiện đúng trạng thái máy chủ báo (SUCCEEDED_UNVERIFIED = chưa kiểm chứng). Chữ động đều qua _esc.
 */
const PlaybooksUI = (() => {
  const POLL_MS = 5000;
  let timer = null;
  let built = false;
  let selected = null;
  let canWrite = false;
  let playbooks = [];

  const $ = (id) => document.getElementById(id);
  const e = (s) => _esc(s == null ? '' : String(s));
  const CARD = 'rounded-xl border border-slate-200 dark:border-slate-700 bg-white dark:bg-slate-800/60 shadow-sm p-4';
  const BTN = 'px-3 py-1.5 text-xs font-semibold rounded-lg transition active:scale-95 disabled:opacity-40';
  const INPUT = 'w-full px-3 py-2 text-xs rounded-lg border border-slate-200 dark:border-slate-600 bg-white dark:bg-slate-900 text-slate-800 dark:text-slate-100 font-mono';

  const SAMPLE = {
    id: 'kiem-tra-ha-tang', name: 'Kiểm tra hạ tầng và xác nhận không còn cảnh báo nghiêm trọng',
    description: 'Mẫu chỉ đọc: đọc tình trạng giám sát, rồi kiểm chứng không có cảnh báo nghiêm trọng.',
    params: {}, steps: [{ id: 'tinh-trang', title: 'Đọc tình trạng hạ tầng', tool: 'get_infra_status', args: {} }],
    verify: [{ id: 'khong-nghiem-trong', tool: 'get_infra_status', args: {}, expect: { path: 'result.summary.alerts_critical', op: 'eq', value: 0 } }],
  };

  const RUN_BADGE = {
    SUCCEEDED: ['Thành công (đã kiểm chứng)', 'emerald'], SUCCEEDED_UNVERIFIED: ['CHƯA kiểm chứng — cần người xác nhận', 'amber'],
    RUNNING: ['Đang chạy', 'sky'], WAITING_APPROVAL: ['Chờ duyệt', 'amber'], PLANNED: ['Đã lên kế hoạch', 'slate'],
    FAILED: ['Thất bại', 'rose'], BLOCKED: ['Bị chính sách chặn', 'rose'], CANCELLED: ['Đã huỷ', 'slate'], INTERRUPTED: ['Bị gián đoạn', 'rose'],
  };
  const TRIGGER = { schedule: 'lịch', alert: 'cảnh báo', webhook: 'webhook' };
  const AUTO = { cron: 'theo lịch', alert: 'theo cảnh báo', webhook: 'qua webhook' };
  const trigText = (p) => {
    const t = (p.definition.triggers || []).map((x) => (x.type === 'schedule' ? `lịch ${x.cron}` : TRIGGER[x.type] || x.type));
    return t.length ? ` · <span class="text-sky-600">tự kích hoạt: ${e(t.join(', '))}</span>` : '';
  };
  const hasWebhook = (p) => !!p && (p.definition.triggers || []).some((x) => x.type === 'webhook');
  const DECISION = { allow: ['Được phép', 'emerald'], require_approval: ['Cần duyệt', 'amber'], deny: ['BỊ CHẶN', 'rose'] };
  const COLOR = {
    emerald: 'bg-emerald-500/15 text-emerald-600 border-emerald-500/30', amber: 'bg-amber-500/15 text-amber-600 border-amber-500/30',
    rose: 'bg-rose-500/15 text-rose-600 border-rose-500/30', sky: 'bg-sky-500/15 text-sky-600 border-sky-500/30', slate: 'bg-slate-500/15 text-slate-500 border-slate-500/30',
  };
  const pill = (map, key) => { const [t, c] = map[key] || [key, 'slate']; return `<span class="px-1.5 py-0.5 rounded border text-[11px] font-bold whitespace-nowrap ${COLOR[c]}">${e(t)}</span>`; };

  async function api(path, opts = {}) {
    const res = await apiFetch(`${API_BASE}/api/v1${path}`, opts);
    const data = await res.json().catch(() => ({}));
    return { ok: res.ok, status: res.status, data };
  }
  const send = (path, method, body) => api(path, { method, headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body || {}) });
  const err = (r) => apiErrorText(r.data, r.status);
  const role = () => { try { return (JSON.parse(localStorage.getItem('vnmateai_user') || 'null') || {}).role || 'viewer'; } catch (_) { return 'viewer'; } };

  function skeleton() {
    return `
    <div class="flex flex-col sm:flex-row sm:items-center justify-between gap-3 pb-3 border-b border-slate-200 dark:border-slate-800">
      <p class="text-xs text-slate-600 dark:text-slate-300">Chuỗi bước có điều kiện, hoàn tác và kiểm chứng. Mọi bước đi qua cùng chính sách / duyệt như AI gọi công cụ. <b>Chạy thử</b> cho biết trước điều gì sẽ xảy ra mà không làm gì cả.</p>
      <button type="button" data-pb="refresh" class="${BTN} bg-primary-600 hover:bg-primary-700 text-white shrink-0">Làm mới</button>
    </div>
    <div class="grid grid-cols-1 xl:grid-cols-3 gap-4">
      <div class="${CARD} xl:col-span-1"><h3 class="text-sm font-bold mb-2 text-slate-800 dark:text-slate-100">Kịch bản</h3><div id="pb-list" class="space-y-1.5 text-xs"></div>
        <button type="button" data-pb="new" id="pb-new" class="${BTN} border border-slate-300 dark:border-slate-600 text-slate-700 dark:text-slate-200 mt-3 hidden">+ Kịch bản mới (chèn mẫu)</button></div>
      <div class="${CARD} xl:col-span-2 space-y-2"><h3 class="text-sm font-bold text-slate-800 dark:text-slate-100">Định nghĩa (JSON)</h3>
        <textarea id="pb-json" rows="14" spellcheck="false" class="${INPUT}" aria-label="Định nghĩa kịch bản dạng JSON" placeholder='{"id": "...", "name": "...", "steps": [...]}'></textarea>
        <p id="pb-save-msg" class="text-xs min-h-[1rem]"></p>
        <div class="flex flex-wrap gap-2" id="pb-edit-actions">
          <button type="button" data-pb="save" class="${BTN} bg-primary-600 hover:bg-primary-700 text-white">Lưu</button>
          <button type="button" data-pb="toggle" class="${BTN} border border-slate-300 dark:border-slate-600">Bật / tắt</button>
          <button type="button" data-pb="delete" class="${BTN} border border-rose-300 text-rose-600">Xoá</button></div></div>
    </div>
    <div class="${CARD} space-y-3"><h3 class="text-sm font-bold text-slate-800 dark:text-slate-100">Chạy kịch bản <span id="pb-selected-name" class="font-mono text-primary-600 dark:text-primary-400"></span></h3>
      <div id="pb-params" class="grid grid-cols-1 sm:grid-cols-3 gap-3"></div>
      <div class="flex flex-wrap gap-2"><button type="button" data-pb="plan" class="${BTN} border border-slate-300 dark:border-slate-600 text-slate-700 dark:text-slate-200">Chạy thử (dry-run)</button>
        <button type="button" data-pb="run" id="pb-run" class="${BTN} bg-emerald-600 hover:bg-emerald-700 text-white">Chạy thật</button>
        <button type="button" data-pb="webhook" id="pb-webhook" class="${BTN} border border-slate-300 dark:border-slate-600 text-slate-700 dark:text-slate-200 hidden">Cấp token webhook</button></div>
      <div id="pb-plan" class="text-xs overflow-x-auto"></div><div id="pb-run-msg" class="text-xs"></div></div>
    <div class="${CARD}"><h3 class="text-sm font-bold mb-2 text-slate-800 dark:text-slate-100">Lượt chạy gần đây</h3><div id="pb-runs" class="overflow-x-auto"></div><div id="pb-run-detail" class="mt-3 text-xs"></div></div>`;
  }

  function build() {
    const root = $('playbooks-root');
    if (!root || built) return;
    root.innerHTML = skeleton();
    root.addEventListener('click', onClick);
    built = true;
  }

  const table = (head, rows, empty) => rows.length
    ? `<table class="w-full text-xs"><thead><tr class="text-left text-slate-500 dark:text-slate-400">${head.map((h) => `<th class="py-1 pr-3 font-semibold">${h}</th>`).join('')}</tr></thead><tbody class="divide-y divide-slate-100 dark:divide-slate-700/50">${rows.join('')}</tbody></table>`
    : `<p class="text-xs text-slate-500 dark:text-slate-400 py-2">${e(empty)}</p>`;

  function renderList() {
    $('pb-list').innerHTML = playbooks.length ? playbooks.map((p) => `
      <button type="button" data-pb="select" data-id="${e(p.id)}" class="w-full text-left px-3 py-2 rounded-lg border ${p.id === selected ? 'border-primary-500 bg-primary-500/10' : 'border-slate-200 dark:border-slate-700'}">
        <span class="font-semibold text-slate-800 dark:text-slate-100">${e(p.name)}</span> ${p.enabled ? '' : '<span class="text-[11px] text-slate-500">(đang tắt)</span>'}
        <span class="block text-[11px] font-mono text-slate-500">${e(p.id)} · v${e(p.version)} · ${e((p.definition.steps || []).length)} bước${(p.definition.verify || []).length ? ' · có kiểm chứng' : ' · chưa có kiểm chứng'}${trigText(p)}</span></button>`).join('')
      : '<p class="text-slate-500 dark:text-slate-400">Chưa có kịch bản nào.</p>';
    $('pb-new').classList.toggle('hidden', !canWrite);
    $('pb-edit-actions').classList.toggle('hidden', !canWrite);
    $('pb-run').classList.toggle('hidden', !canWrite);
    $('pb-webhook').classList.toggle('hidden', !(canWrite && hasWebhook(playbooks.find((p) => p.id === selected))));
  }

  function renderParams() {
    const pb = playbooks.find((p) => p.id === selected);
    $('pb-selected-name').textContent = pb ? `«${pb.name}»` : '';
    const params = (pb && pb.definition.params) || {};
    const names = Object.keys(params);
    $('pb-params').innerHTML = names.length ? names.map((n) => `<label class="block"><span class="text-[11px] font-semibold text-slate-600 dark:text-slate-300 mb-1 block">${e(n)}${params[n].required ? ' *' : ''} <span class="font-normal text-slate-500">(${e(params[n].type)})</span></span>
      <input data-param="${e(n)}" class="${INPUT}" value="${e(params[n].default ?? '')}" aria-label="Tham số ${e(n)}" /></label>`).join('') : '<p class="text-xs text-slate-500">Kịch bản này không có tham số.</p>';
  }

  function collectParams() {
    const out = {};
    const box = $('pb-params');
    (box.querySelectorAll ? [...box.querySelectorAll('[data-param]')] : []).forEach((i) => { if (i.value !== '') out[i.dataset.param] = i.value; });
    return out;
  }

  function select(id) {
    selected = id;
    const pb = playbooks.find((p) => p.id === id);
    $('pb-json').value = pb ? JSON.stringify(pb.definition, null, 2) : '';
    $('pb-plan').innerHTML = ''; $('pb-run-msg').innerHTML = ''; $('pb-save-msg').textContent = '';
    renderList(); renderParams();
  }

  async function loadList() {
    const r = await api('/playbooks');
    if (r.status === 403 || r.status === 401) { $('pb-list').innerHTML = '<p class="text-slate-500">Bạn không có quyền xem kịch bản (cần vai trò manager hoặc admin).</p>'; return false; }
    if (!r.ok) { $('pb-list').innerHTML = `<p class="text-rose-600">Không tải được: ${e(err(r))}</p>`; return false; }
    playbooks = r.data.playbooks || [];
    canWrite = role() === 'admin';
    if (selected && !playbooks.some((p) => p.id === selected)) selected = null;
    renderList(); renderParams();
    return true;
  }

  function renderPlan(p) {
    const rows = (p.steps || []).map((s, i) => `<tr><td class="py-1.5 pr-3 font-mono">${i + 1}</td><td class="pr-3"><b>${e(s.title)}</b>${s.conditional ? ' <span class="text-slate-500">(có điều kiện)</span>' : ''}</td>
      <td class="pr-3 font-mono">${e(s.tool)}</td><td class="pr-3 font-mono text-[11px] max-w-[18rem] truncate" title="${e(JSON.stringify(s.args))}">${e(JSON.stringify(s.args))}</td>
      <td class="pr-3">${pill(DECISION, s.decision)}${s.decision !== 'allow' ? `<div class="text-[11px] text-slate-500 mt-0.5">${e((s.reasons || [])[0] || '')}</div>` : ''}</td>
      <td class="pr-3">${e(s.risk)}/5</td><td>${s.has_rollback ? `có (${e(s.rollback.tool)})` : '—'}</td></tr>`);
    const verify = (p.verify || []).map((v) => `<li><span class="font-mono">${e(v.tool)}</span> → mong đợi <span class="font-mono">${e(v.expect.path)} ${e(v.expect.op)} ${e(JSON.stringify(v.expect.value))}</span></li>`).join('');
    const head = p.blocked ? `<p class="text-rose-600 font-semibold mb-2">✖ KHÔNG chạy được: ${e(p.denied.map((d) => `${d.id}: ${d.reason}`).join(' · '))}</p>`
      : p.needs_approval ? `<p class="text-amber-600 font-semibold mb-2">⏳ Có bước cần duyệt (${e(p.approval_steps.join(', '))}) — sẽ xin MỘT phiếu duyệt cho cả kế hoạch.</p>`
        : '<p class="text-emerald-600 font-semibold mb-2">✔ Mọi bước được phép tự chạy theo chính sách hiện tại.</p>';
    $('pb-plan').innerHTML = `${head}<p class="text-slate-500 mb-1">Rủi ro tối đa ${e(p.max_risk)}/5 · kế hoạch <span class="font-mono">${e(p.plan_hash)}</span> · CHƯA thực thi gì.</p>`
      + table(['#', 'Bước', 'Công cụ', 'Tham số', 'Chính sách', 'Rủi ro', 'Hoàn tác'], rows, 'Không có bước nào.')
      + (verify ? `<p class="mt-2 font-semibold">Kiểm chứng sau khi chạy:</p><ul class="list-disc pl-5">${verify}</ul>` : '')
      + (p.notes || []).map((n) => `<p class="mt-1 text-amber-600">⚠ ${e(n)}</p>`).join('');
  }

  async function plan(btn) {
    if (!selected) { $('pb-plan').innerHTML = '<span class="text-slate-500">Chọn một kịch bản.</span>'; return; }
    btn.disabled = true;
    try {
      const r = await send(`/playbooks/${encodeURIComponent(selected)}/plan`, 'POST', { params: collectParams() });
      if (!r.ok) { $('pb-plan').innerHTML = `<span class="text-rose-600">✖ ${e(err(r))}</span>`; return; }
      renderPlan(r.data);
    } finally { btn.disabled = false; }
  }

  async function run(btn) {
    if (!selected) return;
    if (!window.confirm('Chạy thật kịch bản này? (Nên bấm "Chạy thử" trước để xem từng bước.)')) return;
    btn.disabled = true;
    try {
      const r = await send(`/playbooks/${encodeURIComponent(selected)}/run`, 'POST', { params: collectParams(), idempotency_key: `ui-${Date.now()}-${Math.random().toString(36).slice(2, 8)}` });
      const d = r.data;
      const m = $('pb-run-msg');
      if (!r.ok) { m.innerHTML = `<span class="text-rose-600">✖ ${e(err(r))}</span>`; return; }
      if (d.status === 'awaiting_approval') m.innerHTML = `<span class="text-amber-600">⏳ CHƯA chạy — cần người có thẩm quyền duyệt phiếu <b>${e(d.approval_id)}</b> (hộp thư ✉ ở góc trên). Lượt chạy ${e(d.run_id)} đang chờ.</span>`;
      else if (d.status === 'blocked') m.innerHTML = `<span class="text-rose-600">✖ Bị chính sách chặn: ${e((d.plan.denied || []).map((x) => x.reason).join(' · '))}</span>`;
      else if (d.status === 'duplicate') m.innerHTML = `<span class="text-slate-500">Yêu cầu trùng — lượt ${e(d.run_id)} (${e(d.run_status)}).</span>`;
      else m.innerHTML = `<span class="text-sky-600">▶ Đang chạy lượt ${e(d.run_id)}…</span>`;
      await loadRuns();
    } finally { btn.disabled = false; }
  }

  async function save(btn) {
    const msg = $('pb-save-msg');
    let def;
    try { def = JSON.parse($('pb-json').value); } catch (ex) { msg.className = 'text-xs text-rose-600'; msg.textContent = `JSON không hợp lệ: ${ex.message}`; return; }
    btn.disabled = true;
    try {
      const r = await send('/playbooks', 'PUT', def);
      if (!r.ok) { msg.className = 'text-xs text-rose-600'; msg.textContent = `✖ ${err(r)}`; return; }
      msg.className = 'text-xs text-emerald-600'; msg.textContent = `✔ Đã lưu «${r.data.name}» (phiên bản ${r.data.version}).`;
      selected = r.data.id;
      await loadList(); select(selected);
      $('pb-save-msg').className = 'text-xs text-emerald-600'; $('pb-save-msg').textContent = `✔ Đã lưu «${r.data.name}» (phiên bản ${r.data.version}).`;
    } finally { btn.disabled = false; }
  }

  async function loadRuns() {
    const r = await api(`/playbook-runs?limit=30${selected ? `&playbook_id=${encodeURIComponent(selected)}` : ''}`);
    if (!r.ok) { $('pb-runs').innerHTML = `<p class="text-rose-600 text-xs">${e(err(r))}</p>`; return; }
    const runs = r.data.runs || [];
    $('pb-runs').innerHTML = table(['Thời gian', 'Kịch bản', 'Trạng thái', 'Lỗi / ghi chú', ''], runs.map((x) => `<tr>
      <td class="py-1.5 pr-3 font-mono">${e(x.created_at)}</td><td class="pr-3">${e(x.playbook_id)}</td><td class="pr-3">${pill(RUN_BADGE, x.status)}${x.trigger ? ` <span class="text-[11px] text-sky-600 whitespace-nowrap">tự động ${e(AUTO[x.trigger] || x.trigger)}</span>` : ''}</td>
      <td class="pr-3 text-slate-600 dark:text-slate-300">${e(x.error || '')}</td>
      <td class="whitespace-nowrap"><button type="button" data-pb="detail" data-id="${e(x.run_id)}" class="${BTN} border border-slate-300 dark:border-slate-600">Chi tiết</button>
        ${canWrite && ['RUNNING', 'WAITING_APPROVAL', 'PLANNED'].includes(x.status) ? `<button type="button" data-pb="cancel" data-id="${e(x.run_id)}" class="${BTN} border border-rose-300 text-rose-600">Huỷ</button>` : ''}
        ${canWrite && x.status === 'SUCCEEDED_UNVERIFIED' ? `<button type="button" data-pb="confirm" data-id="${e(x.run_id)}" class="${BTN} bg-amber-600 text-white">Xác nhận kết quả</button>` : ''}</td></tr>`), 'Chưa có lượt chạy nào.');
  }

  async function detail(id) {
    const r = await api(`/playbook-runs/${encodeURIComponent(id)}`);
    if (!r.ok) { $('pb-run-detail').innerHTML = `<span class="text-rose-600">${e(err(r))}</span>`; return; }
    const d = r.data;
    const steps = (d.steps || []).map((s) => `<tr><td class="py-1 pr-3 font-mono">${e(s.id)}</td><td class="pr-3 font-mono">${e(s.tool)}</td><td class="pr-3">${e(s.status)}</td><td class="text-slate-600 dark:text-slate-300">${e(s.error || s.note || '')}</td></tr>`);
    const ver = (d.verify || []).map((v) => `<li>${v.ok ? '✔' : '✖'} <span class="font-mono">${e(v.id)}</span> — ${e(v.detail)}</li>`).join('');
    $('pb-run-detail').innerHTML = `<p class="font-semibold">Lượt ${e(d.run_id)} · ${pill(RUN_BADGE, d.status)} · sổ tác vụ <span class="font-mono">${e(d.task_id)}</span></p>${d.error ? `<p class="text-rose-600">${e(d.error)}</p>` : ''}`
      + table(['Bước', 'Công cụ', 'Kết quả', 'Ghi chú'], steps, 'Chưa có bước nào chạy.') + (ver ? `<p class="mt-2 font-semibold">Kiểm chứng</p><ul class="list-none">${ver}</ul>` : '');
  }

  async function onClick(ev) {
    const btn = ev.target.closest('[data-pb]');
    if (!btn) return;
    const act = btn.dataset.pb, id = btn.dataset.id;
    if (act === 'refresh') { await loadList(); return loadRuns(); }
    if (act === 'select') { select(id); return loadRuns(); }
    if (act === 'new') { selected = null; $('pb-json').value = JSON.stringify(SAMPLE, null, 2); $('pb-save-msg').textContent = ''; renderList(); renderParams(); return; }
    if (act === 'save') return save(btn);
    if (act === 'plan') return plan(btn);
    if (act === 'run') return run(btn);
    if (act === 'webhook' && selected) {
      if (!window.confirm('Cấp token webhook mới? Token cũ (nếu có) hết hiệu lực ngay.')) return;
      const r = await send(`/playbooks/${encodeURIComponent(selected)}/webhook-token`, 'POST');
      $('pb-run-msg').innerHTML = r.ok ? `<div class="rounded-lg border border-amber-400 bg-amber-500/10 p-2"><b>Token webhook — chỉ hiện MỘT lần, chép ngay:</b>
        <div class="font-mono break-all select-all mt-1">${e(r.data.token)}</div><div class="mt-1">${e(r.data.usage)}</div></div>` : `<span class="text-rose-600">✖ ${e(err(r))}</span>`;
      return;
    }
    if (act === 'detail') return detail(id);
    if (act === 'toggle' && selected) {
      const pb = playbooks.find((p) => p.id === selected);
      const r = await send(`/playbooks/${encodeURIComponent(selected)}/enable`, 'POST', { enabled: !(pb && pb.enabled) });
      showToast(r.ok ? 'Đã đổi trạng thái' : `✖ ${err(r)}`, r.ok ? 'success' : 'error');
      return loadList();
    }
    if (act === 'delete' && selected) {
      if (!window.confirm(`Xoá kịch bản "${selected}"? Lịch sử các lượt chạy được giữ.`)) return;
      const r = await api(`/playbooks/${encodeURIComponent(selected)}`, { method: 'DELETE' });
      showToast(r.ok ? 'Đã xoá' : `✖ ${err(r)}`, r.ok ? 'success' : 'error');
      selected = null; $('pb-json').value = '';
      return loadList();
    }
    if (act === 'cancel') {
      if (!window.confirm('Huỷ lượt chạy này? Các bước đã chạy KHÔNG tự hoàn tác.')) return;
      const r = await send(`/playbook-runs/${encodeURIComponent(id)}/cancel`, 'POST');
      showToast(r.ok ? 'Đã yêu cầu huỷ' : `✖ ${err(r)}`, r.ok ? 'success' : 'error');
      return loadRuns();
    }
    if (act === 'confirm') {
      const note = window.prompt('Bạn đã kiểm tra gì để xác nhận kết quả này? (ghi chú sẽ lưu làm bằng chứng)', '');
      if (note === null) return;
      const r = await send(`/playbook-runs/${encodeURIComponent(id)}/confirm`, 'POST', { note });
      showToast(r.ok ? 'Đã xác nhận kết quả' : `✖ ${err(r)}`, r.ok ? 'success' : 'error');
      return loadRuns();
    }
  }

  async function onEnter() {
    build();
    if (await loadList()) await loadRuns();
    if (!timer) timer = setInterval(() => { if (!document.hidden) loadRuns(); }, POLL_MS);
  }
  function onLeave() { if (timer) { clearInterval(timer); timer = null; } }

  return { onEnter, onLeave, loadRuns };
})();
window.PlaybooksUI = PlaybooksUI;
