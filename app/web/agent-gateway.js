// SPDX-License-Identifier: Apache-2.0
// Copyright (c) 2026 Dương Thanh Lịch — VN-MateAI. See LICENSE and NOTICE.
/**
 * web/agent-gateway.js — tab "Cổng Agent" (docs/integrations/agent-gateway.md): khai báo MCP server có sẵn của doanh nghiệp, thử kết
 * nối, đồng bộ công cụ, xem / đặt mức rủi ro và bật tắt từng công cụ. Khoá xác thực không bao giờ hiện lại (máy chủ trả mặt nạ).
 * Ghi chỉ hiện cho admin. Chữ động đều qua _esc.
 */
const AgentGatewayUI = (() => {
  let built = false;
  let canWrite = false;
  let allowStdio = false;
  let servers = [];
  let selected = null;

  const $ = (id) => document.getElementById(id);
  const e = (s) => _esc(s == null ? '' : String(s));
  const CARD = 'rounded-xl border border-slate-200 dark:border-slate-700 bg-white dark:bg-slate-800/60 shadow-sm p-4';
  const BTN = 'px-3 py-1.5 text-xs font-semibold rounded-lg transition active:scale-95 disabled:opacity-40';
  const INPUT = 'w-full px-3 py-2 text-xs rounded-lg border border-slate-200 dark:border-slate-600 bg-white dark:bg-slate-900 text-slate-800 dark:text-slate-100';
  const COLOR = {
    emerald: 'bg-emerald-500/15 text-emerald-600 border-emerald-500/30', amber: 'bg-amber-500/15 text-amber-600 border-amber-500/30',
    rose: 'bg-rose-500/15 text-rose-600 border-rose-500/30', slate: 'bg-slate-500/15 text-slate-500 border-slate-500/30',
  };
  const RISK = { 1: ['L1 chỉ đọc', 'emerald'], 2: ['L2', 'emerald'], 3: ['L3 cần duyệt', 'amber'], 4: ['L4 cần duyệt', 'rose'], 5: ['L5 cấm', 'rose'] };
  const pill = (t, c) => `<span class="px-1.5 py-0.5 rounded border text-[11px] font-bold whitespace-nowrap ${COLOR[c]}">${e(t)}</span>`;

  async function api(path, opts = {}) {
    const res = await apiFetch(`${API_BASE}/api/v1/agent-gateway/mcp${path}`, opts);
    const data = await res.json().catch(() => ({}));
    return { ok: res.ok, status: res.status, data };
  }
  const send = (path, method, body) => api(path, { method, headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body || {}) });
  const err = (r) => apiErrorText(r.data, r.status);
  const role = () => { try { return (JSON.parse(localStorage.getItem('vnmateai_user') || 'null') || {}).role || 'viewer'; } catch (_) { return 'viewer'; } };
  const field = (id, label, input) => `<label class="block"><span class="text-[11px] font-semibold text-slate-600 dark:text-slate-300 mb-1 block">${label}</span>${input}</label>`;

  function skeleton() {
    return `
    <div class="flex flex-col sm:flex-row sm:items-center justify-between gap-3 pb-3 border-b border-slate-200 dark:border-slate-800">
      <p class="text-xs text-slate-600 dark:text-slate-300">Nối <b>MCP server</b> có sẵn của doanh nghiệp (ERP, CRM, ITSM, agent nội bộ…). Mỗi công cụ của server thành công cụ của AI,
        đi qua <b>cùng chính sách</b>: mặc định chỉ admin dùng, rủi ro từ L3 phải duyệt, mọi lời gọi ghi audit.
        <span class="block mt-1 text-[11px] text-slate-500">Hệ thống chỉ có REST / SQL API (không nói MCP): tab <b>Tích hợp hệ thống → Kết nối</b>. Cụm Mac mini / OpenClaw: tab <b>Dev Fleet</b>.</span></p>
      <button type="button" data-ag="refresh" class="${BTN} bg-primary-600 hover:bg-primary-700 text-white shrink-0">Làm mới</button>
    </div>
    <div class="grid grid-cols-1 xl:grid-cols-3 gap-4">
      <div class="${CARD}"><h3 class="text-sm font-bold mb-2 text-slate-800 dark:text-slate-100">MCP server</h3><div id="ag-list" class="space-y-1.5 text-xs"></div>
        <button type="button" data-ag="new" id="ag-new" class="${BTN} border border-slate-300 dark:border-slate-600 text-slate-700 dark:text-slate-200 mt-3 hidden">+ Thêm MCP server</button></div>
      <div class="${CARD} xl:col-span-2 space-y-3" id="ag-form-card"><h3 class="text-sm font-bold text-slate-800 dark:text-slate-100">Khai báo</h3>
        <div class="grid grid-cols-1 sm:grid-cols-2 gap-3">
          ${field('ag-sid', 'Mã (a-z 0-9 _ -)', `<input id="ag-sid" class="${INPUT} font-mono" aria-label="Mã server" placeholder="erp" />`)}
          ${field('ag-name', 'Tên hiển thị', `<input id="ag-name" class="${INPUT}" aria-label="Tên server" placeholder="ERP nội bộ" />`)}
          ${field('ag-transport', 'Kiểu kết nối', `<select id="ag-transport" class="${INPUT}" aria-label="Kiểu kết nối"><option value="http">HTTP (Streamable HTTP)</option><option value="stdio">stdio (chạy chương trình cục bộ)</option></select>`)}
          ${field('ag-url', 'Địa chỉ MCP (http/https)', `<input id="ag-url" class="${INPUT} font-mono" aria-label="Địa chỉ MCP" placeholder="https://erp.congty.vn/mcp" />`)}
          ${field('ag-auth_header', 'Header xác thực', `<input id="ag-auth_header" class="${INPUT} font-mono" aria-label="Header xác thực" value="Authorization" />`)}
          ${field('ag-auth_value', 'Giá trị (vd Bearer …) — để nguyên mặt nạ = giữ khoá cũ', `<input id="ag-auth_value" type="password" autocomplete="off" class="${INPUT} font-mono" aria-label="Khoá xác thực" />`)}
          ${field('ag-command', 'Chương trình (stdio)', `<input id="ag-command" class="${INPUT} font-mono" aria-label="Chương trình stdio" />`)}
          ${field('ag-args', 'Tham số (stdio, mỗi dòng một tham số)', `<textarea id="ag-args" rows="2" class="${INPUT} font-mono" aria-label="Tham số stdio"></textarea>`)}
          ${field('ag-timeout_s', 'Thời gian chờ (giây, 3–120)', `<input id="ag-timeout_s" type="number" min="3" max="120" value="30" class="${INPUT}" aria-label="Thời gian chờ" />`)}
          ${field('ag-default_risk', 'Rủi ro mặc định của công cụ', `<select id="ag-default_risk" class="${INPUT}" aria-label="Rủi ro mặc định">${[1, 2, 3, 4, 5].map((n) => `<option value="${n}" ${n === 3 ? 'selected' : ''}>${e(RISK[n][0])}</option>`).join('')}</select>`)}
          ${field('ag-data_classification', 'Mức dữ liệu', `<select id="ag-data_classification" class="${INPUT}" aria-label="Mức dữ liệu">${['PUBLIC', 'INTERNAL', 'CONFIDENTIAL', 'RESTRICTED'].map((c) => `<option ${c === 'INTERNAL' ? 'selected' : ''}>${c}</option>`).join('')}</select>`)}
          <label class="flex items-center gap-2 text-xs mt-5"><input id="ag-enabled" type="checkbox" checked aria-label="Bật" /> Bật</label>
        </div>
        <p id="ag-stdio-note" class="text-[11px] text-amber-600 hidden">Kiểu stdio đang tắt trên máy chủ (agent_gateway.allow_stdio = false).</p>
        <p id="ag-msg" class="text-xs min-h-[1rem]"></p>
        <div class="flex flex-wrap gap-2" id="ag-actions">
          <button type="button" data-ag="test" class="${BTN} border border-slate-300 dark:border-slate-600 text-slate-700 dark:text-slate-200">Thử kết nối</button>
          <button type="button" data-ag="save" class="${BTN} bg-primary-600 hover:bg-primary-700 text-white">Lưu</button>
          <button type="button" data-ag="discover" class="${BTN} bg-emerald-600 hover:bg-emerald-700 text-white">Đồng bộ công cụ</button>
          <button type="button" data-ag="delete" class="${BTN} border border-rose-300 text-rose-600">Xoá</button></div>
      </div>
    </div>
    <div class="${CARD}"><h3 class="text-sm font-bold mb-2 text-slate-800 dark:text-slate-100">Công cụ <span id="ag-tools-of" class="font-mono text-primary-600 dark:text-primary-400"></span></h3>
      <p class="text-[11px] text-slate-500 mb-2">Mức rủi ro lấy theo gợi ý của server (chỉ đọc → L1, phá huỷ → L4) hoặc mặc định L3; gợi ý có thể sai — hãy xem và đặt lại khi cần.</p>
      <div id="ag-tools" class="overflow-x-auto"></div></div>`;
  }

  function build() {
    const root = $('agent-gateway-root');
    if (!root || built) return;
    root.innerHTML = skeleton();
    root.addEventListener('click', onClick);
    root.addEventListener('change', onChange);
    built = true;
  }

  const table = (head, rows, empty) => rows.length
    ? `<table class="w-full text-xs"><thead><tr class="text-left text-slate-500 dark:text-slate-400">${head.map((h) => `<th class="py-1 pr-3 font-semibold">${h}</th>`).join('')}</tr></thead><tbody class="divide-y divide-slate-100 dark:divide-slate-700/50">${rows.join('')}</tbody></table>`
    : `<p class="text-xs text-slate-500 dark:text-slate-400 py-2">${e(empty)}</p>`;

  function renderList() {
    $('ag-list').innerHTML = servers.length ? servers.map((s) => `
      <button type="button" data-ag="select" data-id="${e(s.id)}" class="w-full text-left px-3 py-2 rounded-lg border ${s.id === selected ? 'border-primary-500 bg-primary-500/10' : 'border-slate-200 dark:border-slate-700'}">
        <span class="font-semibold text-slate-800 dark:text-slate-100">${e(s.name)}</span> ${s.enabled ? '' : '<span class="text-[11px] text-slate-500">(đang tắt)</span>'}
        <span class="block text-[11px] font-mono text-slate-500">${e(s.id)} · ${e(s.transport)} · ${e(s.registered)}/${e((s.tools || []).length)} công cụ${s.discovered_at ? ` · đồng bộ ${e(s.discovered_at)}` : ' · chưa đồng bộ'}</span>
        ${s.last_error ? `<span class="block text-[11px] text-rose-600">✖ ${e(s.last_error)}</span>` : ''}</button>`).join('')
      : '<p class="text-slate-500 dark:text-slate-400">Chưa nối MCP server nào.</p>';
    $('ag-new').classList.toggle('hidden', !canWrite);
    $('ag-actions').classList.toggle('hidden', !canWrite);
  }

  function renderTools() {
    const s = servers.find((x) => x.id === selected);
    $('ag-tools-of').textContent = s ? `«${s.name}»` : '';
    const tools = (s && s.tools) || [];
    $('ag-tools').innerHTML = table(['Công cụ', 'Tên trong VN-MateAI', 'Mô tả', 'Gợi ý của server', 'Rủi ro', 'Bật'], tools.map((t) => {
      const hints = [t.annotations.readOnlyHint ? 'chỉ đọc' : '', t.annotations.destructiveHint ? 'phá huỷ' : ''].filter(Boolean).join(', ') || '—';
      const [label, color] = RISK[t.risk] || [`L${t.risk}`, 'slate'];
      const riskCell = canWrite
        ? `<select data-ag-risk="${e(t.name)}" class="px-1 py-0.5 text-[11px] rounded border border-slate-300 dark:border-slate-600 bg-white dark:bg-slate-900" aria-label="Rủi ro ${e(t.name)}">${[1, 2, 3, 4, 5].map((n) => `<option value="${n}" ${n === t.risk ? 'selected' : ''}>${e(RISK[n][0])}</option>`).join('')}</select>`
        : pill(label, color);
      const onCell = canWrite ? `<input type="checkbox" data-ag-enable="${e(t.name)}" ${t.enabled ? 'checked' : ''} aria-label="Bật ${e(t.name)}" />` : (t.enabled ? '✔' : '—');
      return `<tr><td class="py-1.5 pr-3 font-mono">${e(t.name)}</td><td class="pr-3 font-mono text-[11px] text-slate-500">${e(t.registered_as)}</td>
        <td class="pr-3 max-w-[22rem] truncate" title="${e(t.description)}">${e(t.description)}</td><td class="pr-3">${e(hints)}</td><td class="pr-3">${riskCell}</td><td>${onCell}</td></tr>`;
    }), s ? 'Chưa có công cụ — bấm "Đồng bộ công cụ".' : 'Chọn một MCP server.');
  }

  function fill(s) {
    const v = s || { id: '', name: '', transport: 'http', url: '', auth_header: 'Authorization', auth_value: '', command: '', args: [], timeout_s: 30, default_risk: 3, data_classification: 'INTERNAL', enabled: true };
    $('ag-sid').value = v.id; $('ag-sid').disabled = !!s;
    ['name', 'transport', 'url', 'auth_header', 'auth_value', 'command', 'timeout_s', 'default_risk', 'data_classification'].forEach((k) => { $(`ag-${k}`).value = v[k] ?? ''; });
    $('ag-args').value = (v.args || []).join('\n');
    $('ag-enabled').checked = v.enabled !== false;
    $('ag-msg').textContent = '';
    toggleTransport();
  }

  function toggleTransport() {
    const stdio = $('ag-transport').value === 'stdio';
    $('ag-stdio-note').classList.toggle('hidden', !(stdio && !allowStdio));
  }

  function body() {
    const b = { name: $('ag-name').value.trim(), transport: $('ag-transport').value, timeout_s: Number($('ag-timeout_s').value || 30),
      default_risk: Number($('ag-default_risk').value || 3), data_classification: $('ag-data_classification').value, enabled: !!$('ag-enabled').checked };
    if (b.transport === 'http') Object.assign(b, { url: $('ag-url').value.trim(), auth_header: $('ag-auth_header').value.trim() || 'Authorization', auth_value: $('ag-auth_value').value });
    else Object.assign(b, { command: $('ag-command').value.trim(), args: $('ag-args').value.split('\n').map((x) => x.trim()).filter(Boolean) });
    return b;
  }

  function msg(ok, text) { const m = $('ag-msg'); m.className = `text-xs ${ok ? 'text-emerald-600' : 'text-rose-600'}`; m.textContent = text; }

  async function load() {
    const r = await api('/servers');
    if (r.status === 401 || r.status === 403) { $('ag-list').innerHTML = '<p class="text-slate-500">Bạn không có quyền xem cổng agent (cần vai trò manager hoặc admin).</p>'; return; }
    if (!r.ok) { $('ag-list').innerHTML = `<p class="text-rose-600">Không tải được: ${e(err(r))}</p>`; return; }
    servers = r.data.servers || [];
    allowStdio = !!r.data.allow_stdio;
    canWrite = role() === 'admin';
    if (selected && !servers.some((s) => s.id === selected)) selected = null;
    renderList(); renderTools();
  }

  async function onClick(ev) {
    const btn = ev.target.closest('[data-ag]');
    if (!btn) return;
    const act = btn.dataset.ag;
    if (act === 'refresh') return load();
    if (act === 'select') { selected = btn.dataset.id; fill(servers.find((s) => s.id === selected)); renderList(); return renderTools(); }
    if (act === 'new') { selected = null; fill(null); renderList(); return renderTools(); }
    const sid = ($('ag-sid').value || '').trim();
    btn.disabled = true;
    try {
      if (act === 'test') {
        const r = await send('/test', 'POST', { ...body(), id: sid });
        if (!r.ok) return msg(false, `✖ ${err(r)}`);
        return r.data.ok ? msg(true, `✔ Kết nối được «${(r.data.server_info || {}).name || '?'}» — ${r.data.tools.length} công cụ: ${r.data.tools.slice(0, 8).map((t) => t.name).join(', ')}${r.data.tools.length > 8 ? '…' : ''}`)
          : msg(false, `✖ Không kết nối được: ${r.data.error}`);
      }
      if (act === 'save') {
        if (!sid) return msg(false, 'Cần mã server.');
        const r = await send(`/servers/${encodeURIComponent(sid)}`, 'PUT', body());
        if (!r.ok) return msg(false, `✖ ${err(r)}`);
        selected = r.data.id; await load(); fill(servers.find((s) => s.id === selected));
        return msg(true, '✔ Đã lưu. Bấm "Đồng bộ công cụ" để đọc danh sách công cụ của server.');
      }
      if (act === 'discover' && selected) {
        const r = await send(`/servers/${encodeURIComponent(selected)}/discover`, 'POST');
        await load();
        if (!r.ok) return msg(false, `✖ ${err(r)}`);
        return r.data.ok ? msg(true, `✔ ${r.data.tools} công cụ, đã đăng ký ${r.data.registered}.`) : msg(false, `✖ Không kết nối được: ${r.data.error}`);
      }
      if (act === 'delete' && selected) {
        if (!window.confirm(`Xoá MCP server "${selected}"? Mọi công cụ của nó bị gỡ khỏi AI.`)) return;
        const r = await api(`/servers/${encodeURIComponent(selected)}`, { method: 'DELETE' });
        showToast(r.ok ? 'Đã xoá' : `✖ ${err(r)}`, r.ok ? 'success' : 'error');
        selected = null; fill(null);
        return load();
      }
    } finally { btn.disabled = false; }
  }

  async function onChange(ev) {
    const t = ev.target;
    if (t.id === 'ag-transport') return toggleTransport();
    const tool = t.dataset && (t.dataset.agRisk || t.dataset.agEnable);
    if (!tool || !selected) return;
    const payload = t.dataset.agRisk ? { risk: Number(t.value) } : { enabled: !!t.checked };
    if (payload.risk && payload.risk < 3 && !window.confirm(`Đặt "${tool}" thành ${RISK[payload.risk][0]}: AI sẽ gọi công cụ này KHÔNG cần duyệt. Tiếp tục?`)) { await load(); return; }
    const r = await send(`/servers/${encodeURIComponent(selected)}/tools/${encodeURIComponent(tool)}`, 'PATCH', payload);
    showToast(r.ok ? 'Đã cập nhật công cụ' : `✖ ${err(r)}`, r.ok ? 'success' : 'error');
    await load();
  }

  async function onEnter() { build(); fill(null); await load(); }
  function onLeave() {}

  return { onEnter, onLeave };
})();
window.AgentGatewayUI = AgentGatewayUI;
