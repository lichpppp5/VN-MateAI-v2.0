// SPDX-License-Identifier: Apache-2.0
// Copyright (c) 2026 Dương Thanh Lịch — VN-MateAI. See LICENSE and NOTICE.
/**
 * web/dev-fleet.js — tab "Dev Fleet": cấu hình kết nối Ubuntu Master + theo dõi / điều khiển cụm Dev.
 * Dùng các hàm chung của app.js (apiFetch, API_BASE, _esc, showToast, apiErrorText).
 *
 * Nguyên tắc: không bịa số liệu (thiếu = "—"), token không bao giờ hiện lại, mọi chữ động qua _esc,
 * thao tác ghi đều đi qua API (có cổng chính sách + duyệt phía máy chủ) — giao diện KHÔNG tự coi là "đã giao".
 */
const DevFleetUI = (() => {
  const BASE = '/api/v1/dev-fleet';
  const POLL_MS = 10000;
  let timer = null;
  let built = false;
  let canWrite = true;
  let lastPlan = null;

  const $ = (id) => document.getElementById(id);
  const e = (s) => _esc(s == null ? '' : String(s));
  const dash = (v, suffix = '') => (v === null || v === undefined || v === '' ? '—' : `${e(v)}${suffix}`);

  async function api(path, opts = {}) {
    const res = await apiFetch(`${API_BASE}${BASE}${path}`, opts);
    const data = await res.json().catch(() => ({}));
    return { ok: res.ok, status: res.status, data };
  }
  const post = (path, body) => api(path, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body || {}) });
  const fail = (r) => (r.data && r.data.error) || apiErrorText(r.data, r.status);

  const STATE_CLS = {
    IDLE: 'bg-emerald-500/15 text-emerald-600 border-emerald-500/30', ONLINE: 'bg-emerald-500/15 text-emerald-600 border-emerald-500/30',
    BUSY: 'bg-sky-500/15 text-sky-600 border-sky-500/30', DEGRADED: 'bg-amber-500/15 text-amber-600 border-amber-500/30',
    OFFLINE: 'bg-rose-500/15 text-rose-600 border-rose-500/30', UNKNOWN: 'bg-slate-500/15 text-slate-500 border-slate-500/30',
    DISABLED: 'bg-slate-500/15 text-slate-500 border-slate-500/30', MAINTENANCE: 'bg-amber-500/15 text-amber-600 border-amber-500/30',
    DRAINING: 'bg-amber-500/15 text-amber-600 border-amber-500/30', DISCOVERED: 'bg-slate-500/15 text-slate-500 border-slate-500/30',
    COMPLETED: 'bg-emerald-500/15 text-emerald-600 border-emerald-500/30', EXECUTING: 'bg-sky-500/15 text-sky-600 border-sky-500/30',
    COMPLETED_UNVERIFIED: 'bg-amber-500/15 text-amber-600 border-amber-500/30', WAITING_APPROVAL: 'bg-amber-500/15 text-amber-600 border-amber-500/30',
    INTERRUPTED_UNKNOWN: 'bg-rose-500/15 text-rose-600 border-rose-500/30', FAILED: 'bg-rose-500/15 text-rose-600 border-rose-500/30',
    BLOCKED: 'bg-rose-500/15 text-rose-600 border-rose-500/30', CANCELLED: 'bg-slate-500/15 text-slate-500 border-slate-500/30',
  };
  const badge = (text) => `<span class="px-1.5 py-0.5 rounded border text-[10px] font-mono font-bold ${STATE_CLS[text] || STATE_CLS.UNKNOWN}">${e(text)}</span>`;
  const INPUT = 'w-full px-3 py-2 text-xs rounded-lg border border-slate-200 dark:border-slate-600 bg-white dark:bg-slate-900 text-slate-800 dark:text-slate-100';
  const CARD = 'rounded-xl border border-slate-200 dark:border-slate-700 bg-white dark:bg-slate-800/60 shadow-sm p-4';
  const BTN = 'px-3 py-1.5 text-[11px] font-semibold rounded-lg transition active:scale-95 disabled:opacity-40';
  const LBL = 'text-[10px] font-semibold text-slate-600 dark:text-slate-300 mb-1 block';

  const MODE_HINT = {
    disabled: 'Tắt hẳn — không gọi mạng, các chức năng khác không bị ảnh hưởng.',
    read_only: 'Chỉ xem: Master, Mac mini, agent, tác vụ. Không giao / huỷ / chạy lại.',
    controlled: 'Được giao / huỷ / chạy lại — mọi thao tác vẫn qua cổng chính sách; rủi ro từ medium cần người duyệt.',
    autonomous: 'Hiện chạy như "Có điều khiển" (tự trị có giới hạn chưa được làm).',
  };

  function skeleton() {
    return `
    <div class="flex flex-col sm:flex-row sm:items-center justify-between gap-3 pb-3 border-b border-slate-200 dark:border-slate-800">
      <div>
        <p class="text-xs text-slate-600 dark:text-slate-300">Giao việc phát triển cho cụm Mac mini / OpenClaw qua Ubuntu Master (lập lịch, duyệt, kiểm chứng).
          Agent AI / MCP server khác của doanh nghiệp: tab <b>Cổng Agent</b>; hệ thống chỉ có API dữ liệu: tab <b>Tích hợp hệ thống</b>.</p>
      </div>
      <button type="button" data-df="refresh" class="${BTN} bg-primary-600 hover:bg-primary-700 text-white shrink-0">Làm mới</button>
    </div>

    <div id="df-status" class="${CARD} text-xs text-slate-500">Đang tải…</div>

    <div id="df-config-card" class="${CARD} space-y-3">
      <h3 class="text-sm font-bold text-slate-800 dark:text-slate-100">Kết nối Master - WorkNode</h3>
      <div class="grid grid-cols-1 sm:grid-cols-2 gap-3">
        <label class="block"><span class="${LBL}">Địa chỉ Master *</span>
          <input id="df-endpoint" type="url" class="${INPUT} font-mono" placeholder="https://master.congty.local:8443" /></label>
        <label class="block"><span class="${LBL}">Token (Bearer)</span>
          <input id="df-token" type="password" autocomplete="new-password" class="${INPUT} font-mono" placeholder="Nhập token" />
          <span id="df-token-note" class="text-[9px] text-slate-400 mt-1 block"></span></label>
        <label class="block"><span class="${LBL}">Chế độ</span>
          <select id="df-mode" class="${INPUT}">
            <option value="disabled">Tắt</option><option value="read_only">Chỉ xem</option>
            <option value="controlled">Có điều khiển</option><option value="autonomous">Tự trị (chưa khác Có điều khiển)</option>
          </select><span id="df-mode-hint" class="text-[9px] text-slate-400 mt-1 block"></span></label>
        <label class="block"><span class="${LBL}">Tệp CA nội bộ (tuỳ chọn)</span>
          <input id="df-ca" type="text" class="${INPUT} font-mono" placeholder="/etc/ssl/certs/ca-noi-bo.pem" />
          <label class="flex items-center gap-2 mt-2 text-[10px] text-slate-600 dark:text-slate-300">
            <input id="df-tls" type="checkbox" checked /> Kiểm tra chứng chỉ TLS (chỉ tắt cho mạng nội bộ tự ký)</label></label>
      </div>
      <div class="flex flex-wrap items-center gap-2">
        <button type="button" data-df="test" class="${BTN} border border-slate-300 dark:border-slate-600 text-slate-700 dark:text-slate-200">Thử kết nối</button>
        <button type="button" data-df="save" class="${BTN} bg-primary-600 hover:bg-primary-700 text-white">Lưu cấu hình</button>
        <span id="df-test-result" class="text-[11px]"></span>
      </div>
    </div>

    <div id="df-live" class="grid grid-cols-1 xl:grid-cols-2 gap-4">
      <div class="${CARD}"><h3 class="text-sm font-bold mb-2 text-slate-800 dark:text-slate-100">Worker (Mac mini)</h3><div id="df-workers" class="overflow-x-auto"></div></div>
      <div class="${CARD}"><h3 class="text-sm font-bold mb-2 text-slate-800 dark:text-slate-100">Cần chú ý</h3><div id="df-brief" class="text-xs"></div>
        <h3 class="text-sm font-bold mt-4 mb-2 text-slate-800 dark:text-slate-100">Agent</h3><div id="df-agents" class="overflow-x-auto"></div></div>
    </div>
    <div class="${CARD}"><h3 class="text-sm font-bold mb-1 text-slate-800 dark:text-slate-100">Trạm tự báo nhịp tim</h3>
      <p class="text-[11px] text-slate-500 mb-2">Máy chạy <span class="font-mono">workers/remote_worker_daemon.py</span> tự gửi nhịp tim về máy chủ (không qua Master). <b>Chỉ theo dõi</b> —
        giao việc cho Mac mini / OpenClaw đi qua Master ở trên; việc trên máy trạm dùng agent máy trạm (tab Tích hợp → Máy trạm).</p>
      <div id="df-heartbeat" class="overflow-x-auto"></div></div>

    <div class="${CARD}"><h3 class="text-sm font-bold mb-2 text-slate-800 dark:text-slate-100">Tác vụ Dev</h3><div id="df-tasks" class="overflow-x-auto"></div></div>

    <div id="df-new-card" class="${CARD} space-y-3">
      <h3 class="text-sm font-bold text-slate-800 dark:text-slate-100">Giao tác vụ mới</h3>
      <p class="text-[10px] text-slate-500">Phải có mục tiêu cụ thể, tiêu chí chấp nhận và cách kiểm chứng — không nhận "hãy sửa lỗi này". Hãy bấm "Xem trước" để biết máy nào sẽ được chọn.</p>
      <div class="grid grid-cols-1 sm:grid-cols-2 gap-3">
        <label class="block"><span class="${LBL}">Tiêu đề *</span><input id="df-t-title" class="${INPUT}" /></label>
        <label class="block"><span class="${LBL}">Kho mã (repository)</span><input id="df-t-repo" class="${INPUT} font-mono" placeholder="vn-mateai" /></label>
        <label class="block sm:col-span-2"><span class="${LBL}">Mục tiêu * (≥ 10 ký tự)</span><textarea id="df-t-obj" rows="2" class="${INPUT}"></textarea></label>
        <label class="block"><span class="${LBL}">Tiêu chí chấp nhận * (mỗi dòng một tiêu chí)</span><textarea id="df-t-acc" rows="3" class="${INPUT}"></textarea></label>
        <label class="block"><span class="${LBL}">Cách kiểm chứng * (mỗi dòng một bước)</span><textarea id="df-t-ver" rows="3" class="${INPUT}"></textarea></label>
        <label class="block"><span class="${LBL}">Capability cần có (cách nhau bằng dấu phẩy)</span><input id="df-t-caps" class="${INPUT} font-mono" placeholder="git, python" /></label>
        <label class="block"><span class="${LBL}">Nhánh</span><input id="df-t-branch" class="${INPUT} font-mono" /></label>
        <label class="block"><span class="${LBL}">Rủi ro</span><select id="df-t-risk" class="${INPUT}">
          <option value="low">low — tự chạy</option><option value="medium" selected>medium — cần duyệt</option>
          <option value="high">high — cần duyệt</option><option value="critical">critical — cần duyệt</option></select></label>
        <label class="block"><span class="${LBL}">Ưu tiên</span><select id="df-t-prio" class="${INPUT}">
          <option value="low">low</option><option value="medium" selected>medium</option><option value="high">high</option><option value="critical">critical</option></select></label>
        <label class="block sm:col-span-2"><span class="${LBL}">Cách khôi phục (bắt buộc từ rủi ro medium)</span><input id="df-t-rollback" class="${INPUT}" placeholder="git revert …" /></label>
      </div>
      <div class="flex flex-wrap gap-4 text-[11px] text-slate-600 dark:text-slate-300">
        <label class="flex items-center gap-1.5"><input id="df-t-tests" type="checkbox" checked /> Phải có test PASS</label>
        <label class="flex items-center gap-1.5"><input id="df-t-build" type="checkbox" /> Phải có build PASS</label>
        <label class="flex items-center gap-1.5"><input id="df-t-commit" type="checkbox" /> Phải có commit (đối chiếu Git)</label>
      </div>
      <div class="flex flex-wrap items-center gap-2">
        <button type="button" data-df="plan" class="${BTN} border border-slate-300 dark:border-slate-600 text-slate-700 dark:text-slate-200">Xem trước (dry-run)</button>
        <button type="button" data-df="submit" class="${BTN} bg-emerald-600 hover:bg-emerald-700 text-white">Giao tác vụ</button>
      </div>
      <div id="df-plan-result" class="text-[11px]"></div>
    </div>

    <div class="${CARD}"><h3 class="text-sm font-bold mb-2 text-slate-800 dark:text-slate-100">Hoạt động trực tiếp</h3><div id="df-events" class="text-[11px] font-mono space-y-0.5 max-h-56 overflow-y-auto"></div></div>`;
  }

  function build() {
    const root = $('dev-fleet-root');
    if (!root || built) return;
    root.innerHTML = skeleton();
    root.addEventListener('click', onClick);
    $('df-mode').addEventListener('change', () => { $('df-mode-hint').textContent = MODE_HINT[$('df-mode').value] || ''; });
    built = true;
  }

  // ── cấu hình ──────────────────────────────────────────────────────────────
  async function loadConfig() {
    const r = await api('/config');
    canWrite = r.ok;
    ['df-config-card', 'df-new-card'].forEach((id) => { const el = $(id); if (el) el.style.display = r.ok ? '' : 'none'; });
    if (!r.ok) return;
    const c = r.data;
    $('df-endpoint').value = c.endpoint || '';
    $('df-token').value = '';
    $('df-token-note').textContent = c.token_from_env ? 'Token lấy từ biến môi trường VNMATEAI_DEV_FLEET_TOKEN.'
      : (c.has_token ? '•••••••• đã lưu — để trống để giữ nguyên' : 'Chưa có token');
    $('df-mode').value = c.mode || 'disabled';
    $('df-mode-hint').textContent = MODE_HINT[c.mode || 'disabled'] || '';
    $('df-ca').value = c.ca_bundle || '';
    $('df-tls').checked = !!c.tls_verify;
  }

  function formConfig() {
    return { endpoint: $('df-endpoint').value.trim(), api_token: $('df-token').value.trim(), mode: $('df-mode').value,
             ca_bundle: $('df-ca').value.trim(), tls_verify: $('df-tls').checked };
  }

  async function testConnection(btn) {
    const out = $('df-test-result');
    out.className = 'text-[11px] text-slate-500'; out.textContent = 'Đang thử kết nối…'; btn.disabled = true;
    try {
      const f = formConfig();
      const r = await post('/test-connection', { endpoint: f.endpoint, api_token: f.api_token, ca_bundle: f.ca_bundle, tls_verify: f.tls_verify });
      const d = r.data;
      if (!r.ok) { out.className = 'text-[11px] text-rose-600'; out.textContent = `✖ ${fail(r)}`; return; }
      if (d.ok) {
        out.className = 'text-[11px] text-emerald-600';
        out.textContent = `✔ Kết nối được — Master "${(d.master && d.master.name) || '—'}", API ${d.api_version}, ${d.workers} worker, ${d.latency_ms} ms`;
      } else {
        out.className = 'text-[11px] text-rose-600'; out.textContent = `✖ ${d.error}`;
      }
    } finally { btn.disabled = false; }
  }

  async function saveConfig(btn) {
    btn.disabled = true;
    try {
      const r = await post('/config', formConfig());
      if (!r.ok) { showToast(`✖ Lưu thất bại: ${fail(r)}`, 'error'); return; }
      showToast('✔ Đã lưu cấu hình Dev Fleet', 'success');
      await loadConfig();
      await refresh();
    } finally { btn.disabled = false; }
  }

  // ── hiển thị dữ liệu ──────────────────────────────────────────────────────
  function table(head, rows, empty) {
    if (!rows.length) return `<p class="text-[11px] text-slate-400 py-2">${e(empty)}</p>`;
    return `<table class="w-full text-[11px]"><thead><tr class="text-left text-slate-500">${head.map((h) => `<th class="py-1 pr-3 font-semibold">${h}</th>`).join('')}</tr></thead>
      <tbody class="divide-y divide-slate-100 dark:divide-slate-700/50">${rows.join('')}</tbody></table>`;
  }

  function renderStatus(s) {
    const el = $('df-status');
    if (!s.enabled) { el.innerHTML = `${badge('DISABLED')} <span class="ml-2">Module Dev Fleet đang <b>tắt</b>. Điền địa chỉ + token ở bên dưới, "Thử kết nối", rồi chọn chế độ "Chỉ xem".</span>`; return; }
    if (s.configured === false) { el.innerHTML = `${badge('UNKNOWN')} <span class="ml-2">${e(s.error)}</span>`; return; }
    const m = s.master || {};
    const health = !s.reachable ? 'OFFLINE' : (m.health === 'HEALTHY' ? 'IDLE' : m.health === 'DEGRADED' ? 'DEGRADED' : 'UNKNOWN');
    const states = Object.entries((s.workers && s.workers.by_state) || {}).map(([k, v]) => `${badge(k)} ${v}`).join(' &nbsp; ');
    const runs = Object.entries((s.tasks && s.tasks.by_run_status) || {}).map(([k, v]) => `${e(k)} ${v}`).join(' · ');
    el.innerHTML = `<div class="flex flex-wrap items-center gap-x-6 gap-y-2">
      <div><span class="text-[10px] uppercase text-slate-400">Chế độ</span><br><b>${e(s.mode)}</b></div>
      <div><span class="text-[10px] uppercase text-slate-400">Master</span><br>${badge(health)} ${dash(m.name)} ${m.version ? `<span class="text-slate-400">v${e(m.version)}</span>` : ''}</div>
      <div><span class="text-[10px] uppercase text-slate-400">Ansible · 9Router · OpenClaw</span><br>${dash(m.ansible_status)} · ${dash(m.router_status)} · ${dash(m.openclaw_status)}</div>
      <div><span class="text-[10px] uppercase text-slate-400">Worker</span><br>${s.workers ? `${s.workers.total} — ${states || '—'}` : '—'}</div>
      <div><span class="text-[10px] uppercase text-slate-400">Tác vụ (lượt chạy)</span><br>${runs || '—'}</div>
      <div><span class="text-[10px] uppercase text-slate-400">API Master</span><br>${dash(s.api_version)}</div></div>
      ${s.reachable ? '' : `<p class="mt-2 text-rose-600">Master không liên lạc được: ${e(s.error)}. Trạng thái worker bên dưới KHÔNG còn được bảo đảm (hiện UNKNOWN). Lần thấy gần nhất: ${dash(s.last_ok_at)}</p>`}`;
  }

  function renderWorkers(w) {
    const rows = (w.workers || []).map((x) => `<tr>
      <td class="py-1.5 pr-3 font-mono">${e(x.worker_id)}<div class="text-[9px] text-slate-400">${dash(x.hostname)} · ${dash(x.platform)} ${dash(x.architecture)}</div></td>
      <td class="pr-3">${badge(x.state)}<div class="text-[9px] text-slate-400">${x.freshness === 'STALE' ? 'dữ liệu cũ' : x.freshness === 'UNOBSERVED' ? 'chưa có mốc thời gian' : ''}</div></td>
      <td class="pr-3 whitespace-nowrap">CPU ${dash(x.cpu_percent === null ? null : Math.round(x.cpu_percent), '%')} · RAM ${dash(x.memory_percent === null ? null : Math.round(x.memory_percent), '%')} · Đĩa ${dash(x.disk_percent === null ? null : Math.round(x.disk_percent), '%')}</td>
      <td class="pr-3 text-slate-500">${(x.capabilities || []).map(e).join(', ') || '—'}</td>
      <td class="pr-3 font-mono">${dash(x.current_task)}</td>
      <td>${canWrite ? `<button type="button" data-df="toggle" data-id="${e(x.worker_id)}" data-disabled="${x.state === 'DISABLED' ? '0' : '1'}" class="${BTN} border border-slate-300 dark:border-slate-600">${x.state === 'DISABLED' ? 'Bật' : 'Tắt máy'}</button>` : ''}</td></tr>`);
    $('df-workers').innerHTML = table(['Máy', 'Trạng thái', 'Tải', 'Capability', 'Tác vụ', ''], rows,
      w.reachable === false ? 'Master không liên lạc được.' : 'Master chưa báo worker nào.');
  }

  function renderAgents(a) {
    const rows = (a.agents || []).map((x) => `<tr><td class="py-1 pr-3 font-mono">${e(x.agent_id)}</td><td class="pr-3">${dash(x.role)}</td><td class="pr-3">${dash(x.worker_id)}</td><td class="pr-3">${dash(x.status)}</td><td>${dash(x.model)}</td></tr>`);
    $('df-agents').innerHTML = table(['Agent', 'Vai trò', 'Máy', 'Trạng thái', 'Model'], rows, 'Chưa có agent nào.');
  }

  function renderBrief(b) {
    const items = [...(b.recommended || [])];
    $('df-brief').innerHTML = items.length ? `<ul class="list-disc pl-4 space-y-1">${items.map((i) => `<li>${e(i)}</li>`).join('')}</ul>`
      : '<p class="text-slate-400">Không có gì cần chú ý.</p>';
  }

  function renderTasks(t) {
    const rows = (t.tasks || []).map((x) => {
      const done = ['COMPLETED', 'FAILED', 'CANCELLED'].includes(x.status);
      return `<tr><td class="py-1.5 pr-3 font-mono">${e(x.task_id)}</td><td class="pr-3">${e(x.title)}</td>
      <td class="pr-3">${badge(x.display_status)}</td><td class="pr-3">${dash(x.verification_status)}</td><td class="pr-3 text-slate-500">${e(x.result_summary || '')}</td>
      <td class="whitespace-nowrap">${canWrite && !done ? `<button type="button" data-df="cancel" data-id="${e(x.task_id)}" class="${BTN} border border-rose-300 text-rose-600">Huỷ</button>
        <button type="button" data-df="retry" data-id="${e(x.task_id)}" class="${BTN} border border-slate-300 dark:border-slate-600">Chạy lại</button>` : ''}
        <button type="button" data-df="detail" data-id="${e(x.task_id)}" class="${BTN} border border-slate-300 dark:border-slate-600">Chi tiết</button></td></tr>`;
    });
    $('df-tasks').innerHTML = table(['Mã', 'Tiêu đề', 'Trạng thái', 'Kiểm chứng', 'Kết quả', ''], rows, 'Chưa có tác vụ Dev nào.');
  }

  function renderEvents(ev) {
    $('df-events').innerHTML = (ev.events || []).map((x) => `<div><span class="text-slate-400">${e(x.ts)}</span> <span class="text-primary-600">${e(x.kind)}</span> ${e(x.message)}${x.worker_id ? ` <span class="text-slate-400">[${e(x.worker_id)}]</span>` : ''}</div>`).join('')
      || '<span class="text-slate-400">Chưa có hoạt động.</span>';
  }

  async function loadHeartbeat() {
    const res = await apiFetch(`${API_BASE}/api/v1/worknodes/status`);
    const data = await res.json().catch(() => ({}));
    const box = $('df-heartbeat');
    if (!res.ok) { box.innerHTML = `<p class="text-rose-600 text-[11px]">${e(apiErrorText(data, res.status))}</p>`; return; }
    const nodes = ((data.grid || {}).nodes) || [];
    box.innerHTML = nodes.length
      ? `<table class="w-full text-xs"><thead><tr class="text-left text-slate-500 dark:text-slate-400"><th class="py-1 pr-3">Trạm</th><th class="pr-3">IP</th><th class="pr-3">Trạng thái</th><th class="pr-3">CPU / RAM</th><th class="pr-3">Năng lực</th><th>Nhịp tim cuối</th></tr></thead>
        <tbody class="divide-y divide-slate-100 dark:divide-slate-700/50">${nodes.map((n) => `<tr><td class="py-1 pr-3 font-mono">${e(n.node_id)}</td><td class="pr-3 font-mono">${e(n.ip)}</td>
        <td class="pr-3 ${n.is_online ? 'text-emerald-600' : 'text-slate-500'}">${n.is_online ? e(n.status) : 'OFFLINE'}</td><td class="pr-3">${e(n.cpu_percent)}% / ${e(n.ram_percent)}%</td>
        <td class="pr-3 text-[11px]">${e((n.capabilities || []).join(', '))}</td><td>${e(n.last_heartbeat_ago_sec)} s trước</td></tr>`).join('')}</tbody></table>`
      : '<p class="text-xs text-slate-500 dark:text-slate-400">Chưa có trạm nào gửi nhịp tim.</p>';
  }

  async function refresh() {
    if (!built) return;
    loadHeartbeat().catch(() => {});
    const st = await api('/status');
    if (st.status === 403 || st.status === 401) { $('df-status').textContent = 'Bạn không có quyền xem Dev Fleet (cần vai trò manager hoặc admin).'; return; }
    if (!st.ok) { $('df-status').textContent = `Không tải được: ${fail(st)}`; return; }
    renderStatus(st.data);
    if (!st.data.enabled || st.data.configured === false) {
      ['df-workers', 'df-agents', 'df-brief'].forEach((id) => { $(id).innerHTML = ''; });
      $('df-live').classList?.add('hidden');
      const t = await api('/tasks'); if (t.ok) renderTasks(t.data);
      return;
    }
    $('df-live').classList?.remove('hidden');
    const [w, a, b, t, ev] = await Promise.all([api('/workers'), api('/agents'), api('/briefing'), api('/tasks'), api('/events?limit=40')]);
    if (w.ok) renderWorkers(w.data); else $('df-workers').innerHTML = `<p class="text-rose-600 text-[11px]">${e(fail(w))}</p>`;
    if (a.ok) renderAgents(a.data);
    if (b.ok) renderBrief(b.data);
    if (t.ok) renderTasks(t.data);
    if (ev.ok) renderEvents(ev.data);
  }

  // ── giao việc ─────────────────────────────────────────────────────────────
  const lines = (id) => $(id).value.split('\n').map((s) => s.trim()).filter(Boolean);
  function formTask() {
    return {
      title: $('df-t-title').value.trim(), objective: $('df-t-obj').value.trim(), acceptance_criteria: lines('df-t-acc'),
      verification_steps: lines('df-t-ver'), repository: $('df-t-repo').value.trim() || null, branch: $('df-t-branch').value.trim() || null,
      required_capabilities: $('df-t-caps').value.split(',').map((s) => s.trim()).filter(Boolean),
      risk: $('df-t-risk').value, priority: $('df-t-prio').value, rollback: $('df-t-rollback').value.trim(),
      require_tests: $('df-t-tests').checked, require_build: $('df-t-build').checked, require_commit: $('df-t-commit').checked,
    };
  }

  async function plan(btn) {
    const out = $('df-plan-result'); btn.disabled = true;
    try {
      const r = await post('/tasks/plan', formTask());
      if (!r.ok) { out.innerHTML = `<span class="text-rose-600">✖ ${e(fail(r))}</span>`; return; }
      const p = r.data;
      const rej = (p.rejected || []).map((x) => `<li>${e(x.worker_id)}: ${e(x.reason)}</li>`).join('');
      out.innerHTML = p.ok
        ? `<div class="text-emerald-600">✔ Sẽ chọn <b>${e(p.worker_id)}</b> (điểm ${e(p.score)}). Rủi ro ${e(p.risk)} — ${p.requires_approval ? '<b>cần người duyệt</b> trước khi giao' : 'tự chạy (chế độ Có điều khiển)'}.</div>${rej ? `<div class="mt-1 text-slate-500">Máy bị loại:<ul class="list-disc pl-4">${rej}</ul></div>` : ''}`
        : `<div class="text-rose-600">✖ ${e(p.error)}</div>${rej ? `<ul class="list-disc pl-4 text-slate-500">${rej}</ul>` : ''}`;
    } finally { btn.disabled = false; }
  }

  async function submit(btn) {
    const out = $('df-plan-result'); btn.disabled = true;
    try {
      const body = formTask();
      body.idempotency_key = `ui-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`;
      const r = await post('/tasks', body);
      const d = r.data;
      if (r.ok && d.status === 'awaiting_approval') {
        out.innerHTML = `<span class="text-amber-600">⏳ CHƯA giao — cần người có thẩm quyền duyệt phiếu <b>${e(d.approval_id)}</b> (hàng đợi duyệt). Tác vụ ${e(d.task_id)} đang chờ.</span>`;
      } else if (r.ok && (d.status === 'dispatched' || d.status === 'duplicate')) {
        out.innerHTML = `<span class="text-emerald-600">✔ Đã giao ${e(d.task_id)} cho ${e(d.worker_id || '')}.${d.warning ? ' ' + e(d.warning) : ''}</span>`;
      } else {
        out.innerHTML = `<span class="text-rose-600">✖ ${e(d.error || fail(r))}</span>`;
      }
      await refresh();
    } finally { btn.disabled = false; }
  }

  async function detail(id) {
    const r = await api(`/tasks/${encodeURIComponent(id)}`);
    if (!r.ok) { showToast(`✖ ${fail(r)}`, 'error'); return; }
    const t = r.data;
    const runs = (t.runs || []).map((x) => `#${x.attempt} ${x.run_id} → ${x.worker_id || '—'} [${x.status}]`).join('\n');
    const ev = (t.evidence || []).slice(-8).map((x) => `• ${x.summary}`).join('\n');
    window.alert(`${t.title}\nTrạng thái: ${t.display_status}  (kiểm chứng: ${t.verification_status || '—'})\n\nLượt chạy:\n${runs || '—'}\n\nBằng chứng:\n${ev || '—'}`);
  }

  async function onClick(ev) {
    const btn = ev.target.closest('[data-df]');
    if (!btn) return;
    const act = btn.dataset.df, id = btn.dataset.id;
    if (act === 'refresh') return refresh();
    if (act === 'test') return testConnection(btn);
    if (act === 'save') return saveConfig(btn);
    if (act === 'plan') return plan(btn);
    if (act === 'submit') return submit(btn);
    if (act === 'detail') return detail(id);
    if (act === 'toggle') {
      const r = await post(`/workers/${encodeURIComponent(id)}/disable`, { disabled: btn.dataset.disabled === '1' });
      showToast(r.ok ? `✔ Đã ${btn.dataset.disabled === '1' ? 'tắt' : 'bật'} máy ${id}` : `✖ ${fail(r)}`, r.ok ? 'success' : 'error');
      return refresh();
    }
    if (act === 'cancel' || act === 'retry') {
      if (act === 'cancel' && !window.confirm(`Huỷ tác vụ ${id}? Master sẽ được yêu cầu dừng việc đang chạy.`)) return;
      const r = await post(`/tasks/${encodeURIComponent(id)}/${act}`, {});
      const d = r.data;
      const okStatus = ['cancelled', 'dispatched'].includes(d.status);
      showToast(okStatus ? `✔ ${act === 'cancel' ? 'Đã huỷ' : 'Đã giao lại'} ${id}`
        : d.status === 'awaiting_approval' ? `⏳ Cần duyệt phiếu ${d.approval_id}` : `✖ ${d.error || fail(r)}`,
      okStatus ? 'success' : 'warning');
      return refresh();
    }
  }

  function onEnter() {
    build();
    loadConfig().then(refresh);
    if (!timer) timer = setInterval(() => { if (!document.hidden) refresh(); }, POLL_MS);
  }
  function onLeave() { if (timer) { clearInterval(timer); timer = null; } }

  return { onEnter, onLeave, refresh };
})();
window.DevFleetUI = DevFleetUI;
