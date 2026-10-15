// SPDX-License-Identifier: Apache-2.0
// Copyright (c) 2026 Dương Thanh Lịch — VN-MateAI. See LICENSE and NOTICE.
/*
 * Thương hiệu hiển thị (white-label): tên sản phẩm, dòng mô tả, logo, màu chủ đạo — GET /api/v1/branding (công khai, màn
 * hình đăng nhập cần). Áp vào phần tử có data-brand="name|tagline|logo" và lớp màu primary-*; bản lưu gần nhất ở
 * localStorage để lần mở sau không nháy về mặc định. Chưa đổi gì (customized=false) → giữ nguyên giao diện gốc.
 * Admin sửa trong tab Cấu hình (#branding-admin). Dòng bản quyền "VN-MateAI © Dương Thanh Lịch" không đổi theo.
 */
const Branding = (() => {
  const CACHE = 'vnmateai_branding';
  let current = null;

  const hexToRgb = (hex) => [1, 3, 5].map((i) => parseInt(hex.slice(i, i + 2), 16));
  const shade = (hex, f) => `#${hexToRgb(hex).map((c) => Math.max(0, Math.min(255, Math.round(f < 0 ? c * (1 + f) : c + (255 - c) * f)))
    .toString(16).padStart(2, '0')).join('')}`;

  function colorCss(hex) {
    if (!/^#[0-9a-f]{6}$/i.test(hex || '')) return '';
    const [r, g, b] = hexToRgb(hex);
    const dark = shade(hex, -0.15);
    return `.bg-primary-600,.bg-primary-500{background-color:${hex}!important}
.hover\\:bg-primary-700:hover{background-color:${dark}!important}
.text-primary-600,.text-primary-500{color:${hex}!important}.text-primary-700{color:${dark}!important}
.border-primary-500{border-color:${hex}!important}.ring-primary-500{--tw-ring-color:${hex}!important}
.bg-primary-50{background-color:rgba(${r},${g},${b},.08)!important}.bg-primary-100{background-color:rgba(${r},${g},${b},.15)!important}
.from-primary-600{--tw-gradient-from:${hex} var(--tw-gradient-from-position)!important;--tw-gradient-to:rgba(${r},${g},${b},0) var(--tw-gradient-to-position)!important}
.nav-btn.active{color:${hex}!important;border-color:rgba(${r},${g},${b},.35)!important;background:rgba(${r},${g},${b},.08)!important}`;
  }

  function apply(b) {
    if (!b) return;
    current = b;
    let style = document.getElementById('brand-color-style');
    if (!style) {
      style = document.createElement('style');
      style.id = 'brand-color-style';
      document.head.appendChild(style);
    }
    style.textContent = colorCss(b.primary_color);
    if (!b.customized) return;
    document.querySelectorAll('[data-brand="name"]').forEach((el) => { el.textContent = b.product_name; });
    document.querySelectorAll('[data-brand="tagline"]').forEach((el) => { el.textContent = b.tagline; });
    if (b.logo_data_url) {
      document.querySelectorAll('[data-brand="logo"]').forEach((el) => {
        const img = document.createElement('img');
        img.src = b.logo_data_url;
        img.alt = b.product_name;
        img.className = 'w-full h-full object-contain';
        el.replaceChildren(img);
        el.classList.add('bg-white');
      });
    }
    if (document.title.includes('VN-MateAI')) document.title = document.title.replace('VN-MateAI', b.product_name);
  }

  async function load() {
    try {
      const cached = JSON.parse(localStorage.getItem(CACHE) || 'null');
      if (cached) apply(cached);
    } catch (e) { /* bỏ qua cache hỏng */ }
    try {
      const r = await fetch('/api/v1/branding');
      if (!r.ok) return;
      const b = await r.json();
      try { localStorage.setItem(CACHE, JSON.stringify(b)); } catch (e) { /* đầy bộ nhớ */ }
      apply(b);
    } catch (e) { /* mất mạng: giữ bản đang có */ }
  }

  // ── thẻ sửa cho admin (tab Cấu hình) ──────────────────────────────────────
  const esc = (s) => String(s ?? '').replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');
  const isAdmin = () => {
    try { return (JSON.parse(localStorage.getItem('vnmateai_user') || 'null') || {}).role === 'admin'; } catch (e) { return false; }
  };

  function renderAdmin() {
    const root = document.getElementById('branding-admin');
    if (!root) return;
    if (!isAdmin()) { root.innerHTML = ''; return; }
    const b = current || {};
    const color = b.primary_color || '#2563eb';
    root.innerHTML = `
      <div class="glass-panel p-6 space-y-4">
        <div class="flex flex-wrap items-start justify-between gap-3">
          <div><h2 class="text-base font-bold text-slate-800 dark:text-white">Thương hiệu hiển thị</h2>
            <p class="text-xs text-slate-500 dark:text-slate-400 mt-0.5">Tên, dòng mô tả, logo và màu chủ đạo trên portal và màn hình đăng nhập. Dòng bản quyền của phần mềm giữ nguyên.</p></div>
          <div class="flex gap-2">
            <button type="button" id="brand-reset" class="px-3 py-2 rounded-lg text-xs font-semibold border border-slate-300 dark:border-slate-600 text-slate-700 dark:text-slate-200">Về mặc định</button>
            <button type="button" id="brand-save" class="px-3 py-2 rounded-lg text-xs font-semibold bg-primary-600 text-white">Lưu thương hiệu</button>
          </div>
        </div>
        <div class="grid grid-cols-1 md:grid-cols-2 gap-4 text-xs">
          <label class="space-y-1"><span class="font-semibold text-slate-700 dark:text-slate-200">Tên sản phẩm</span>
            <input id="brand-name" class="input-dark" maxlength="40" value="${esc(b.product_name)}" /></label>
          <label class="space-y-1"><span class="font-semibold text-slate-700 dark:text-slate-200">Dòng mô tả</span>
            <input id="brand-tagline" class="input-dark" maxlength="90" value="${esc(b.tagline)}" /></label>
          <label class="space-y-1"><span class="font-semibold text-slate-700 dark:text-slate-200">Màu chủ đạo</span>
            <span class="flex items-center gap-2"><input id="brand-color" type="color" value="${esc(color)}" class="h-9 w-12 rounded border border-slate-300 dark:border-slate-600" />
            <input id="brand-color-text" class="input-dark" maxlength="7" value="${esc(b.primary_color || '')}" placeholder="#2563eb (để trống = mặc định)" /></span></label>
          <label class="space-y-1"><span class="font-semibold text-slate-700 dark:text-slate-200">Logo (PNG / JPEG / WebP / SVG, tối đa ~150 KB)</span>
            <span class="flex items-center gap-3"><span id="brand-logo-preview" class="w-10 h-10 rounded-lg border border-slate-200 dark:border-slate-700 bg-white flex items-center justify-center overflow-hidden text-[10px] text-slate-400">${b.logo_data_url ? '' : '—'}</span>
            <input id="brand-logo" type="file" accept="image/png,image/jpeg,image/webp,image/svg+xml" class="text-xs" />
            <button type="button" id="brand-logo-clear" class="text-xs text-rose-600 hover:underline">Bỏ logo</button></span></label>
        </div>
        <p id="brand-status" class="text-[11px] text-slate-500"></p>
      </div>`;
    const $ = (id) => document.getElementById(id);
    let logo = b.logo_data_url || '';
    const preview = () => {
      const p = $('brand-logo-preview');
      if (logo) { const img = document.createElement('img'); img.src = logo; img.alt = 'logo'; img.className = 'w-full h-full object-contain'; p.replaceChildren(img); } else p.textContent = '—';
    };
    preview();
    $('brand-color').addEventListener('input', (e) => { $('brand-color-text').value = e.target.value; });
    $('brand-logo').addEventListener('change', (e) => {
      const f = e.target.files && e.target.files[0];
      if (!f) return;
      if (f.size > 150 * 1024) { $('brand-status').textContent = 'Logo quá lớn (tối đa khoảng 150 KB).'; return; }
      const rd = new FileReader();
      rd.onload = () => { logo = String(rd.result || ''); preview(); };
      rd.readAsDataURL(f);
    });
    $('brand-logo-clear').addEventListener('click', () => { logo = ''; $('brand-logo').value = ''; preview(); });
    const save = async (body) => {
      $('brand-status').textContent = 'Đang lưu…';
      const res = await apiFetch('/api/v1/branding', { method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) });
      const data = await res.json().catch(() => ({}));
      if (!res.ok) { $('brand-status').textContent = `Không lưu được: ${data.detail || res.status}`; return; }
      try { localStorage.setItem(CACHE, JSON.stringify(data)); } catch (e) { /* đầy bộ nhớ */ }
      $('brand-status').textContent = 'Đã lưu. Tải lại trang để thấy đầy đủ ở mọi nơi.';
      apply(data);
      if (typeof showToast === 'function') showToast('Đã lưu thương hiệu hiển thị', 'success');
    };
    $('brand-save').addEventListener('click', () => save({
      product_name: $('brand-name').value, tagline: $('brand-tagline').value,
      primary_color: $('brand-color-text').value.trim(), logo_data_url: logo,
    }));
    $('brand-reset').addEventListener('click', () => save({ product_name: '', tagline: '', primary_color: '', logo_data_url: '' }));
  }

  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', load);
  else load();

  return { load, apply, renderAdmin, colorCss, get current() { return current; } };
})();
