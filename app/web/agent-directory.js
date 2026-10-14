// SPDX-License-Identifier: Apache-2.0
// Copyright (c) 2026 Dương Thanh Lịch — VN-MateAI. See LICENSE and NOTICE.
/**
 * web/agent-directory.js — "Danh bạ Agent" ở đầu tab Cổng Agent (docs/integrations/agent-gateway.md): mọi agent nối với VN-MateAI
 * (inbound gọi vào, A2A được giao việc, MCP server), tình trạng, số lời gọi; kill switch từng agent; cấp token (chỉ hiện MỘT lần);
 * đọc lại agent card. Ghi chỉ hiện cho admin. Chữ động đều qua _esc.
 */
const AgentDirectoryUI = (() => {
  let built = false;
  let canWrite = false;
  let agents = [];

  const $ = (id) => document.getElementById(id);
  const e = (s) => _esc(s == null ? '' : String(s));
  const CARD = 'rounded-xl border border-slate-200 dark:border-slate-700 bg-white dark:bg-slate-800/60 shadow-sm p-4';
  const BTN = 'px-2.5 py-1 text-[11px] font-semibold rounded-lg transition active:scale-95 disabled:opacity-40';
  const INPUT = 'w-full px-3 py-2 text-xs rounded-lg border border-slate-200 dark:border-slate-600 bg-white dark:bg-slate-900 text-slate-800 dark:text-slate-100';
  const COLOR = {
    emerald: 'bg-emerald-500/15 text-emerald-600 border-emerald-500/30', amber: 'bg-amber-500/15 text-amber-600 border-amber-500/30',
    rose: 'bg-rose-500/15 text-rose-600 border-rose-500/30', sky: 'bg-sky-500/15 text-sky-600 border-sky-500/30', slate: 'bg-slate-500/15 text-slate-500 border-slate-500/30',
  };
  const KIND = { inbound: ['Gọi vào', 'sky'], a2a: ['A2A — được giao việc', 'emerald'], mcp: ['MCP server', 'slate'],
    devfleet: ['Dev Fleet', 'amber'], workstations: ['Máy trạm', 'slate'] };
  const READ_ONLY = ['devfleet', 'workstations'];
  const pill = (map, k) => { const [t, c] = map[k] || [k, 'slate']; return `<span class="px-1.5 py-0.5 rounded border text-[11px] font-bold whitespace-nowrap ${COLOR[c]}">${e(t)}</span>`; };
  const field = (label, input) => `<label class="block"><span class="text-[11px] font-semibold text-slate-600 dark:text-slate-300 mb-1 block">${label}</span>${input}</label>`;

  async function api(path, opts = {}) {
    const res = await apiFetch(`${API_BASE}/api/v1/agent-gateway/agents${path}`, opts);
    const data = await res.json().catch(() => ({}));
    return { ok: res.ok, status: res.status, data };
  }
  const send = (path, method, body) => api(path, { method, headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body || {}) });
  const err = (r) => apiErrorText(r.data, r.status);
  const role = () => { try { return (JSON.parse(localStorage.getItem('vnmateai_user') || 'null') || {}).role || 'viewer'; } catch (_) { return 'viewer'; } };

  function skeleton() {
    return `
    <div class="${CARD} space-y-3">
      <div class="flex items-center justify-between gap-2"><h3 class="text-sm font-bold text-slate-800 dark:text-slate-100">Danh bạ Agent</h3>
        <button type="button" data-ad="refresh" class="${BTN} border border-slate-300 dark:border-slate-600">Làm mới</button></div>
      <p class="text-[11px] text-slate-500">Mọi agent nối với VN-MateAI. <b>Gọi vào</b>: agent của doanh nghiệp dùng token riêng để giao việc / gọi công cụ, theo quyền đặt ở đây.
        <b>A2A</b>: agent ngoài mà Ly Ly giao việc tới (một công cụ, mặc định cần duyệt). <b>Tạm dừng</b> có hiệu lực ngay.</p>
      <div id="ad-list" class="overflow-x-auto"></div>
      <div id="ad-token" class="hidden rounded-lg border border-amber-400 bg-amber-500/10 p-3 text-xs"></div>
      <details id="ad-form" class="hidden"><summary class="text-xs font-semibold cursor-pointer text-primary-600 dark:text-primary-400">+ Thêm / sửa agent</summary>
        <div class="grid grid-cols-1 sm:grid-cols-3 gap-3 mt-3">
          ${field('Mã (a-z 0-9 _ -)', `<input id="ad-id" class="${INPUT} font-mono" aria-label="Mã agent" placeholder="crm-bot" />`)}
          ${field('Loại', `<select id="ad-kind" class="${INPUT}" aria-label="Loại agent"><option value="inbound">Gọi vào (agent dùng VN-MateAI)</option><option value="a2a">A2A (Ly Ly giao việc cho agent)</option></select>`)}
          ${field('Tên', `<input id="ad-name" class="${INPUT}" aria-label="Tên agent" />`)}
          ${field('Chủ sở hữu / phòng ban', `<input id="ad-owner" class="${INPUT}" aria-label="Chủ sở hữu" />`)}
          ${field('Quyền (gọi vào)', `<select id="ad-role" class="${INPUT}" aria-label="Quyền agent"><option value="viewer">viewer — chỉ xem</option><option value="operator">operator</option><option value="it_support">it_support</option><option value="admin">admin</option></select>`)}
          ${field('Hạn mức token / ngày (0 = không đặt)', `<input id="ad-limit" type="number" min="0" value="0" class="${INPUT}" aria-label="Hạn mức token" />`)}
          ${field('Địa chỉ agent (A2A)', `<input id="ad-url" class="${INPUT} font-mono" aria-label="Địa chỉ A2A" placeholder="https://agent.congty.vn" />`)}
          ${field('Khoá (A2A, vd Bearer …)', `<input id="ad-auth" type="password" autocomplete="off" class="${INPUT} font-mono" aria-label="Khoá A2A" />`)}
          ${field('Rủi ro công cụ A2A', `<select id="ad-risk" class="${INPUT}" aria-label="Rủi ro A2A"><option value="2">L2 — không cần duyệt</option><option value="3" selected>L3 — cần duyệt</option><option value="4">L4 — cần duyệt</option></select>`)}
        </div>
        <p id="ad-msg" class="text-xs min-h-[1rem] mt-2"></p>
        <button type="button" data-ad="save" class="px-3 py-1.5 text-xs font-semibold rounded-lg bg-primary-600 hover:bg-primary-700 text-white">Lưu</button>
      </details>
    </div>`;
  }

  function build() {
    const root = $('agent-directory-root');
    if (!root || built) return;
    root.innerHTML = skeleton();
    root.addEventListener('click', onClick);
    built = true;
  }

  function render() {
    const rows = agents.map((a) => {
      const st = a.status === 'active' ? '<span class="text-emerald-600 font-semibold">hoạt động</span>' : '<span class="text-rose-600 font-semibold">TẠM DỪNG</span>';
      const extra = a.kind === 'inbound' ? `quyền <b>${e(a.role)}</b>${a.has_token ? '' : ' · <span class="text-amber-600">chưa có token</span>'}${a.daily_token_limit ? ` · ${e(a.daily_token_limit)} token/ngày` : ''}`
        : a.kind === 'a2a' ? `${a.tool ? `công cụ <span class="font-mono">${e(a.tool)}</span>` : '<span class="text-amber-600">chưa đọc được thẻ agent</span>'} · rủi ro L${e(a.risk)}`
          : a.kind === 'devfleet' ? `chế độ <b>${e(a.mode)}</b> · ${e(a.tasks)} tác vụ · quản lý ở tab <b>${e(a.manage_at)}</b>`
            : a.kind === 'workstations' ? `${e(a.online)} máy online${(a.names || []).length ? `: ${e(a.names.slice(0, 5).join(', '))}${a.names.length > 5 ? '…' : ''}` : ''} · quản lý ở <b>${e(a.manage_at)}</b>`
              : `${e(a.registered)}/${e(a.tools)} công cụ`;
      const calls = a.calls ? `${e(a.calls.total)} lượt${a.calls.failed ? `, <span class="text-rose-600">${e(a.calls.failed)} lỗi</span>` : ''}` : '—';
      const actions = (!canWrite || READ_ONLY.includes(a.kind)) ? '' : [
        `<button type="button" data-ad="${a.status === 'active' ? 'suspend' : 'resume'}" data-id="${e(a.agent_id)}" class="${BTN} ${a.status === 'active' ? 'border border-rose-300 text-rose-600' : 'bg-emerald-600 text-white'}">${a.status === 'active' ? 'Tạm dừng' : 'Kích hoạt'}</button>`,
        a.kind === 'inbound' ? `<button type="button" data-ad="token" data-id="${e(a.agent_id)}" class="${BTN} border border-slate-300 dark:border-slate-600">${a.has_token ? 'Xoay token' : 'Cấp token'}</button>` : '',
        a.kind === 'a2a' ? `<button type="button" data-ad="card" data-id="${e(a.agent_id)}" class="${BTN} border border-slate-300 dark:border-slate-600">Đọc lại thẻ</button>` : '',
        a.kind !== 'mcp' ? `<button type="button" data-ad="edit" data-id="${e(a.agent_id)}" class="${BTN} border border-slate-300 dark:border-slate-600">Sửa</button>` : '',
        a.kind !== 'mcp' ? `<button type="button" data-ad="delete" data-id="${e(a.agent_id)}" class="${BTN} border border-rose-300 text-rose-600">Xoá</button>` : '',
      ].join(' ');
      return `<tr><td class="py-1.5 pr-3"><b>${e(a.name)}</b><div class="text-[11px] font-mono text-slate-500">${e(a.agent_id)}${a.owner ? ` · ${e(a.owner)}` : ''}</div></td>
        <td class="pr-3">${pill(KIND, a.kind)}</td><td class="pr-3">${st}</td><td class="pr-3">${extra}${a.last_error ? `<div class="text-[11px] text-rose-600">✖ ${e(a.last_error)}</div>` : ''}</td>
        <td class="pr-3 text-[11px]">${e(a.last_seen_at || '—')}</td><td class="pr-3 text-[11px]">${calls}</td><td class="whitespace-nowrap">${actions}</td></tr>`;
    });
    $('ad-list').innerHTML = rows.length
      ? `<table class="w-full text-xs"><thead><tr class="text-left text-slate-500 dark:text-slate-400">${['Agent', 'Loại', 'Trạng thái', 'Chi tiết', 'Lần cuối', 'Lời gọi', ''].map((h) => `<th class="py-1 pr-3 font-semibold">${h}</th>`).join('')}</tr></thead><tbody class="divide-y divide-slate-100 dark:divide-slate-700/50">${rows.join('')}</tbody></table>`
      : '<p class="text-xs text-slate-500 dark:text-slate-400 py-2">Chưa có agent nào.</p>';
    $('ad-form').classList.toggle('hidden', !canWrite);
  }

  async function load() {
    const r = await api('');
    if (r.status === 401 || r.status === 403) { $('ad-list').innerHTML = '<p class="text-xs text-slate-500">Bạn không có quyền xem danh bạ agent (cần vai trò manager hoặc admin).</p>'; return; }
    if (!r.ok) { $('ad-list').innerHTML = `<p class="text-xs text-rose-600">Không tải được: ${e(err(r))}</p>`; return; }
    agents = r.data.agents || [];
    canWrite = role() === 'admin';
    render();
  }

  function msg(ok, text) { const m = $('ad-msg'); m.className = `text-xs mt-2 ${ok ? 'text-emerald-600' : 'text-rose-600'}`; m.textContent = text; }

  function edit(a) {
    $('ad-form').open = true;
    $('ad-id').value = a.agent_id; $('ad-kind').value = a.kind; $('ad-name').value = a.name || ''; $('ad-owner').value = a.owner || '';
    $('ad-role').value = a.role || 'viewer'; $('ad-limit').value = a.daily_token_limit || 0; $('ad-url').value = a.url || '';
    $('ad-auth').value = a.auth_value || ''; $('ad-risk').value = String(a.risk || 3); $('ad-msg').textContent = '';
  }

  async function save(btn) {
    const id = ($('ad-id').value || '').trim();
    if (!id) return msg(false, 'Cần mã agent.');
    const kind = $('ad-kind').value;
    const body = { kind, name: $('ad-name').value.trim(), owner: $('ad-owner').value.trim(), daily_token_limit: Number($('ad-limit').value || 0) };
    if (kind === 'inbound') body.role = $('ad-role').value;
    else Object.assign(body, { url: $('ad-url').value.trim(), auth_value: $('ad-auth').value, risk: Number($('ad-risk').value) });
    if (kind === 'inbound' && body.role === 'admin' && !window.confirm('Cho agent này quyền ADMIN? Nó gọi được mọi công cụ (tác vụ rủi ro cao vẫn phải duyệt).')) return;
    if (kind === 'a2a' && body.risk < 3 && !window.confirm('Rủi ro L2: Ly Ly giao việc cho agent này KHÔNG cần duyệt. Tiếp tục?')) return;
    btn.disabled = true;
    try {
      const r = await send(`/${encodeURIComponent(id)}`, 'PUT', body);
      if (!r.ok) return msg(false, `✖ ${err(r)}`);
      msg(true, kind === 'a2a' ? (r.data.tool ? `✔ Đã lưu, đọc được thẻ agent → công cụ ${r.data.tool}.` : `✔ Đã lưu nhưng chưa đọc được thẻ agent: ${r.data.last_error || '?'}`)
        : `✔ Đã lưu.${r.data.has_token ? '' : ' Bấm "Cấp token" để agent kết nối được.'}`);
      await load();
    } finally { btn.disabled = false; }
  }

  async function onClick(ev) {
    const btn = ev.target.closest('[data-ad]');
    if (!btn) return;
    const act = btn.dataset.ad, id = btn.dataset.id;
    if (act === 'refresh') return load();
    if (act === 'save') return save(btn);
    if (act === 'edit') return edit(agents.find((a) => a.agent_id === id) || {});
    if (act === 'suspend' || act === 'resume') {
      if (act === 'suspend' && !window.confirm(`Tạm dừng agent "${id}"? Mọi lời gọi của / tới agent này bị chặn ngay.`)) return;
      const r = await send(`/${encodeURIComponent(id)}/status`, 'POST', { status: act === 'suspend' ? 'suspended' : 'active' });
      showToast(r.ok ? (act === 'suspend' ? 'Đã tạm dừng' : 'Đã kích hoạt') : `✖ ${err(r)}`, r.ok ? 'success' : 'error');
      return load();
    }
    if (act === 'token') {
      const a = agents.find((x) => x.agent_id === id) || {};
      if (a.has_token && !window.confirm('Xoay token? Token cũ hết hiệu lực ngay — agent phải dùng token mới.')) return;
      const r = await send(`/${encodeURIComponent(id)}/token`, 'POST');
      const box = $('ad-token');
      box.classList.remove('hidden');
      box.innerHTML = r.ok ? `<b>Token của «${e(id)}» — chỉ hiện MỘT lần, chép ngay:</b><div class="font-mono break-all mt-1 select-all">${e(r.data.token)}</div><div class="mt-1 text-slate-600 dark:text-slate-300">${e(r.data.usage)}</div>`
        : `<span class="text-rose-600">✖ ${e(err(r))}</span>`;
      return load();
    }
    if (act === 'card') {
      const r = await send(`/${encodeURIComponent(id)}/card`, 'POST');
      showToast(r.ok && r.data.ok ? 'Đã đọc thẻ agent' : `✖ ${r.ok ? r.data.error : err(r)}`, r.ok && r.data.ok ? 'success' : 'error');
      return load();
    }
    if (act === 'delete') {
      if (!window.confirm(`Xoá agent "${id}"? Token / công cụ của nó hết hiệu lực.`)) return;
      const r = await api(`/${encodeURIComponent(id)}`, { method: 'DELETE' });
      showToast(r.ok ? 'Đã xoá' : `✖ ${err(r)}`, r.ok ? 'success' : 'error');
      return load();
    }
  }

  async function onEnter() { build(); await load(); }
  return { onEnter };
})();
window.AgentDirectoryUI = AgentDirectoryUI;
