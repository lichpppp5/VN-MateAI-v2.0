// SPDX-License-Identifier: Apache-2.0
// Copyright (c) 2026 Dương Thanh Lịch — VN-MateAI. See LICENSE and NOTICE.
/**
 * web/infra-monitor.js — tab "Giám sát hạ tầng": dữ liệu THẬT từ Prometheus / Grafana của doanh nghiệp (chỉ đọc).
 * Không bịa số liệu: thiếu = "—" kèm lý do; nguồn mất liên lạc nói rõ; chữ động đều qua _esc.
 * Dùng các hàm chung của app.js (apiFetch, API_BASE, _esc, showToast, apiErrorText, switchTab).
 */
const InfraMonitorUI = (() => {
  const POLL_MS = 30000;
  let timer = null;
  let built = false;

  const $ = (id) => document.getElementById(id);
  const e = (s) => _esc(s == null ? '' : String(s));
  const dash = (v, suffix = '') => (v === null || v === undefined || v === '' ? '—' : `${e(v)}${suffix}`);
  const CARD = 'rounded-xl border border-slate-200 dark:border-slate-700 bg-white dark:bg-slate-800/60 shadow-sm p-4';
  const BTN = 'px-3 py-1.5 text-xs font-semibold rounded-lg transition active:scale-95 disabled:opacity-40';
  const INPUT = 'w-full px-3 py-2 text-xs rounded-lg border border-slate-200 dark:border-slate-600 bg-white dark:bg-slate-900 text-slate-800 dark:text-slate-100 font-mono';

  const HEALTH = {
    HEALTHY: ['Ổn định', 'bg-emerald-500/15 text-emerald-600 border-emerald-500/30'],
    DEGRADED: ['Suy giảm', 'bg-amber-500/15 text-amber-600 border-amber-500/30'],
    CRITICAL: ['Nghiêm trọng', 'bg-rose-500/15 text-rose-600 border-rose-500/30'],
    UNKNOWN: ['Chưa rõ', 'bg-slate-500/15 text-slate-500 border-slate-500/30'],
  };
  const SEV = {
    critical: ['Nghiêm trọng', 'bg-rose-500/15 text-rose-600 border-rose-500/30'],
    warning: ['Cảnh báo', 'bg-amber-500/15 text-amber-600 border-amber-500/30'],
    info: ['Thông tin', 'bg-sky-500/15 text-sky-600 border-sky-500/30'],
  };
  const LEVEL_BAR = { ok: 'bg-emerald-500', warn: 'bg-amber-500', crit: 'bg-rose-500' };
  const pill = (map, key) => { const [t, c] = map[key] || map.UNKNOWN || map.info; return `<span class="px-1.5 py-0.5 rounded border text-[11px] font-bold whitespace-nowrap ${c}">${e(t)}</span>`; };

  async function api(path, opts = {}) {
    const res = await apiFetch(`${API_BASE}/api/v1/monitoring${path}`, opts);
    const data = await res.json().catch(() => ({}));
    return { ok: res.ok, status: res.status, data };
  }

  function skeleton() {
    return `
    <div class="flex flex-col sm:flex-row sm:items-center justify-between gap-3 pb-3 border-b border-slate-200 dark:border-slate-800">
      <p class="text-xs text-slate-600 dark:text-slate-300">Dữ liệu thật từ Prometheus và Grafana của doanh nghiệp (chỉ đọc). Cảnh báo nghiêm trọng tự mở sự cố và báo qua kênh cảnh báo.</p>
      <button type="button" data-im="refresh" class="${BTN} bg-primary-600 hover:bg-primary-700 text-white shrink-0">Làm mới</button>
    </div>
    <div id="im-summary" class="${CARD} text-xs text-slate-500">Đang tải…</div>
    <div id="im-empty" class="${CARD} hidden"></div>
    <div id="im-sources" class="grid grid-cols-1 md:grid-cols-2 gap-3"></div>
    <div id="im-data" class="space-y-4">
    <div class="grid grid-cols-1 xl:grid-cols-2 gap-4">
      <div class="${CARD}"><h3 class="text-sm font-bold mb-2 text-slate-800 dark:text-slate-100">Cảnh báo đang bật</h3><div id="im-alerts" class="overflow-x-auto"></div></div>
      <div class="${CARD}"><h3 class="text-sm font-bold mb-2 text-slate-800 dark:text-slate-100">Target không phản hồi (down)</h3><div id="im-targets" class="overflow-x-auto"></div></div>
    </div>
    <div class="${CARD}"><h3 class="text-sm font-bold mb-1 text-slate-800 dark:text-slate-100">CPU · RAM · Ổ đĩa theo máy</h3>
      <p id="im-metrics-note" class="text-[11px] text-slate-500 dark:text-slate-400 mb-2"></p><div id="im-metrics" class="overflow-x-auto"></div></div>
    <div class="grid grid-cols-1 xl:grid-cols-2 gap-4">
      <div class="${CARD}"><h3 class="text-sm font-bold mb-2 text-slate-800 dark:text-slate-100">Dashboard Grafana</h3><div id="im-dashboards" class="text-xs"></div></div>
      <div class="${CARD} space-y-2"><h3 class="text-sm font-bold text-slate-800 dark:text-slate-100">Truy vấn PromQL (chỉ đọc)</h3>
        <div class="flex gap-2"><input id="im-promql" class="${INPUT}" placeholder='up == 0' maxlength="600" aria-label="Biểu thức PromQL" />
          <button type="button" data-im="query" class="${BTN} bg-primary-600 hover:bg-primary-700 text-white shrink-0">Chạy</button></div>
        <div id="im-query-result" class="text-xs overflow-x-auto"></div></div>
    </div>
    </div>`;
  }

  function build() {
    const root = $('infra-monitor-root');
    if (!root || built) return;
    root.innerHTML = skeleton();
    root.addEventListener('click', onClick);
    const q = $('im-promql');
    if (q) q.addEventListener('keydown', (ev) => { if (ev.key === 'Enter') runQuery(root.querySelector('[data-im="query"]')); });
    built = true;
  }

  const table = (head, rows, empty) => rows.length
    ? `<table class="w-full text-xs"><thead><tr class="text-left text-slate-500 dark:text-slate-400">${head.map((h) => `<th class="py-1 pr-3 font-semibold">${h}</th>`).join('')}</tr></thead>
       <tbody class="divide-y divide-slate-100 dark:divide-slate-700/50">${rows.join('')}</tbody></table>`
    : `<p class="text-xs text-slate-500 dark:text-slate-400 py-2">${e(empty)}</p>`;

  function render(d) {
    const sum = d.summary || {};
    if (d.enabled === false) {
      $('im-summary').innerHTML = 'Giám sát hạ tầng đang <b>tắt</b> (cấu hình <code>monitoring.enabled</code>).';
      $('im-data').classList.add('hidden');
      ['im-sources', 'im-alerts', 'im-targets', 'im-metrics', 'im-dashboards'].forEach((id) => { $(id).innerHTML = ''; });
      return;
    }
    const empty = $('im-empty');
    if (!d.configured) {
      $('im-summary').innerHTML = `${pill(HEALTH, 'UNKNOWN')} <span class="ml-2">Chưa có nguồn giám sát nào được khai báo.</span>`;
      empty.classList.remove('hidden');
      $('im-data').classList.add('hidden');
      empty.innerHTML = `<p class="text-sm font-bold text-slate-800 dark:text-slate-100">Kết nối Prometheus / Grafana</p>
        <p class="text-xs text-slate-600 dark:text-slate-300 mt-1">Vào <b>Tích Hợp Hệ Thống → Thêm kết nối</b>, chọn mẫu <b>Prometheus</b> hoặc <b>Grafana</b>, điền địa chỉ (và token cho Grafana), rồi bấm Thử kết nối.</p>
        <button type="button" data-im="goto-integration" class="${BTN} bg-primary-600 hover:bg-primary-700 text-white mt-3">Mở Tích Hợp Hệ Thống</button>`;
      ['im-sources', 'im-alerts', 'im-targets', 'im-metrics', 'im-dashboards'].forEach((id) => { $(id).innerHTML = ''; });
      $('im-metrics-note').textContent = '';
      return;
    }
    empty.classList.add('hidden');
    $('im-data').classList.remove('hidden');
    $('im-summary').innerHTML = `<div class="flex flex-wrap items-center gap-x-6 gap-y-2">
      <div><span class="text-[11px] uppercase text-slate-500">Tình trạng</span><br>${pill(HEALTH, sum.health || 'UNKNOWN')}</div>
      <div><span class="text-[11px] uppercase text-slate-500">Nguồn liên lạc được</span><br><b>${dash(sum.sources_reachable)}</b> / ${dash(sum.sources_total)}</div>
      <div><span class="text-[11px] uppercase text-slate-500">Cảnh báo bật</span><br><b class="text-rose-600">${dash(sum.alerts_critical)}</b> nghiêm trọng · <b class="text-amber-600">${dash(sum.alerts_warning)}</b> cảnh báo</div>
      <div><span class="text-[11px] uppercase text-slate-500">Target</span><br><b>${dash(sum.targets_total)}</b> · <b class="${sum.targets_down ? 'text-rose-600' : ''}">${dash(sum.targets_down)}</b> down</div>
      <div><span class="text-[11px] uppercase text-slate-500">Dashboard</span><br><b>${dash(sum.dashboards)}</b></div>
      <div><span class="text-[11px] uppercase text-slate-500">Cập nhật</span><br>${dash(d.checked_at)}</div></div>`;

    $('im-sources').innerHTML = (d.sources || []).map((s) => `
      <div class="${CARD}"><div class="flex items-center justify-between gap-2">
        <div><b class="text-sm text-slate-800 dark:text-slate-100">${e(s.title || s.id)}</b> <span class="text-[11px] text-slate-500">· ${e(s.type)}</span></div>
        ${s.reachable ? pill(HEALTH, 'HEALTHY') : pill(HEALTH, 'CRITICAL').replace('Nghiêm trọng', 'Mất liên lạc')}</div>
        <p class="text-[11px] text-slate-500 dark:text-slate-400 mt-1">${s.reachable ? `Độ trễ ${dash(s.latency_ms, ' ms')}` : ''}${s.error ? ` ${e(s.error)}` : ''}</p>
        ${s.type === 'grafana' && s.rules ? `<p class="text-[11px] mt-1">Quy tắc cảnh báo: ${e(s.rules.total)} · đang bắn ${e(s.rules.firing)} · chờ ${e(s.rules.pending)} · lỗi ${e(s.rules.error)}</p>` : ''}
        ${s.type === 'grafana' && s.base_url ? `<a class="text-[11px] font-semibold text-primary-600 dark:text-primary-400 hover:underline" href="${e(s.base_url)}" target="_blank" rel="noopener noreferrer">Mở Grafana ↗</a>` : ''}
      </div>`).join('');

    $('im-alerts').innerHTML = table(['Mức', 'Cảnh báo', 'Máy / nhãn', 'Nguồn', 'Tóm tắt'], (d.alerts || []).slice(0, 50).map((a) => `<tr>
      <td class="py-1.5 pr-3">${pill(SEV, a.severity)}${a.state === 'pending' ? ' <span class="text-[11px] text-slate-500">chờ</span>' : ''}</td>
      <td class="pr-3 font-semibold">${e(a.name)}</td><td class="pr-3 font-mono">${dash(a.instance)}</td><td class="pr-3">${e(a.source)}</td>
      <td class="text-slate-600 dark:text-slate-300">${e(a.summary)}</td></tr>`), 'Không có cảnh báo nào đang bật.');

    const down = (d.sources || []).flatMap((s) => (s.targets ? s.targets.down_list : []));
    $('im-targets').innerHTML = table(['Job', 'Instance', 'Lỗi gần nhất'], down.map((t) => `<tr><td class="py-1.5 pr-3">${e(t.job)}</td><td class="pr-3 font-mono">${e(t.instance)}</td><td class="text-slate-600 dark:text-slate-300">${dash(t.error)}</td></tr>`),
      (d.sources || []).some((s) => s.targets) ? 'Mọi target đều phản hồi.' : 'Chưa có dữ liệu target (cần nguồn Prometheus).');

    const metrics = (d.sources || []).flatMap((s) => s.metrics || []);
    const byInst = {};
    metrics.forEach((m) => { (byInst[m.instance] = byInst[m.instance] || {})[m.metric] = m; });
    const cell = (m) => (m ? `<div class="flex items-center gap-2 min-w-[8rem]"><div class="h-1.5 flex-1 rounded bg-slate-200 dark:bg-slate-700"><div class="h-1.5 rounded ${LEVEL_BAR[m.level] || 'bg-slate-400'}" style="width:${Math.max(2, Math.min(100, m.value))}%"></div></div><span class="font-mono w-12 text-right">${e(m.value)}%</span></div>` : '<span class="text-slate-400">—</span>');
    $('im-metrics').innerHTML = table(['Máy', 'CPU', 'RAM', 'Ổ đĩa'], Object.keys(byInst).sort().map((i) => `<tr><td class="py-1.5 pr-3 font-mono">${e(i)}</td><td class="pr-3">${cell(byInst[i].cpu)}</td><td class="pr-3">${cell(byInst[i].memory)}</td><td>${cell(byInst[i].disk)}</td></tr>`),
      'Chưa có số đo CPU / RAM / đĩa.');
    $('im-metrics-note').textContent = (d.sources || []).map((s) => s.metrics_note).filter(Boolean).join(' · ');

    const dashes = (d.sources || []).flatMap((s) => (s.dashboards || []));
    $('im-dashboards').innerHTML = dashes.length
      ? `<ul class="space-y-1">${dashes.slice(0, 40).map((x) => `<li><a class="font-semibold text-primary-600 dark:text-primary-400 hover:underline" href="${e(x.url)}" target="_blank" rel="noopener noreferrer">${e(x.title)}</a> <span class="text-slate-500">${e(x.folder)}</span></li>`).join('')}</ul>`
      : '<p class="text-slate-500 dark:text-slate-400">Chưa có dashboard (cần nguồn Grafana).</p>';
  }

  async function refresh(force = false) {
    if (!built) return;
    const r = await api(force ? '/overview?force=true' : '/overview');
    if (r.status === 403 || r.status === 401) { $('im-summary').textContent = 'Bạn không có quyền xem giám sát hạ tầng (cần vai trò manager hoặc admin).'; return; }
    if (!r.ok) { $('im-summary').textContent = `Không tải được: ${r.data && r.data.error ? r.data.error : apiErrorText(r.data, r.status)}`; return; }
    render(r.data);
  }

  async function runQuery(btn) {
    const out = $('im-query-result');
    const expr = ($('im-promql').value || '').trim();
    if (!expr) { out.innerHTML = '<span class="text-slate-500">Nhập biểu thức PromQL.</span>'; return; }
    if (btn) btn.disabled = true;
    try {
      const r = await api('/query', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ promql: expr }) });
      if (!r.ok) { out.innerHTML = `<span class="text-rose-600">✖ ${e(apiErrorText(r.data, r.status))}</span>`; return; }
      const rows = (r.data.rows || []).map((x) => `<tr><td class="py-1 pr-3 font-mono">${e(Object.entries(x.metric || {}).map(([k, v]) => `${k}="${v}"`).join(', ') || '(không nhãn)')}</td><td class="font-mono">${e(Array.isArray(x.value) ? JSON.stringify(x.value) : x.value)}</td></tr>`);
      out.innerHTML = `<p class="text-slate-500 mb-1">${e(r.data.count)} dòng · ${e(r.data.latency_ms)} ms · nguồn ${e(r.data.source)}</p>` + table(['Nhãn', 'Giá trị'], rows, 'Truy vấn không có kết quả.');
    } finally { if (btn) btn.disabled = false; }
  }

  async function onClick(ev) {
    const btn = ev.target.closest('[data-im]');
    if (!btn) return;
    const act = btn.dataset.im;
    if (act === 'refresh') { btn.disabled = true; try { await refresh(true); } finally { btn.disabled = false; } return; }
    if (act === 'query') return runQuery(btn);
    if (act === 'goto-integration') return switchTab('system-integration');
  }

  function onEnter() {
    build();
    refresh();
    if (!timer) timer = setInterval(() => { if (!document.hidden) refresh(); }, POLL_MS);
  }
  function onLeave() { if (timer) { clearInterval(timer); timer = null; } }

  return { onEnter, onLeave, refresh };
})();
window.InfraMonitorUI = InfraMonitorUI;
