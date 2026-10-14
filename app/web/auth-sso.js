// SPDX-License-Identifier: Apache-2.0
// Copyright (c) 2026 Dương Thanh Lịch — VN-MateAI. See LICENSE and NOTICE.
/**
 * web/auth-sso.js — nút "Đăng nhập bằng tài khoản công ty" (SSO / OpenID Connect) trên màn đăng nhập.
 * Luồng: nút -> /api/v1/sso/login -> IdP -> /api/v1/sso/callback -> về trang này với `#sso=<mã một lần>` -> POST /sso/exchange -> completeLogin.
 * Token KHÔNG bao giờ nằm trên URL. Lỗi từ máy chủ về qua `?sso_error=` và được hiện trong khung lỗi của màn đăng nhập.
 */
const SsoLogin = (() => {
  const e = (s) => _esc(s == null ? '' : String(s));

  function showError(text) {
    const box = document.getElementById('login-error-box');
    const out = document.getElementById('login-error-text');
    if (box && out) { out.textContent = text; box.classList.remove('hidden'); }
  }

  function cleanUrl() {
    try { window.history.replaceState(null, '', window.location.pathname); } catch (_) { /* bỏ qua */ }
  }

  async function handleReturn() {
    const hash = window.location.hash || '';
    const params = new URLSearchParams(window.location.search || '');
    if (params.get('sso_error')) {
      const msg = params.get('sso_error');
      cleanUrl();
      showError(`Đăng nhập SSO thất bại: ${msg}`);
      return true;
    }
    if (hash.startsWith('#sso=')) {
      const code = decodeURIComponent(hash.slice(5));
      cleanUrl();                                              // xoá mã khỏi thanh địa chỉ NGAY (không để lại trong lịch sử)
      const res = await fetch(`${API_BASE}/api/v1/sso/exchange`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ code }) });
      const data = await res.json().catch(() => ({}));
      if (res.ok && data.access_token) { completeLogin(data); return true; }
      showError(data.detail || 'Không hoàn tất được đăng nhập SSO.');
      return true;
    }
    return false;
  }

  async function renderButton() {
    const form = document.getElementById('login-form');
    if (!form || document.getElementById('sso-login-btn')) return;
    let cfg = null;
    try { cfg = await (await fetch(`${API_BASE}/api/v1/sso/config`)).json(); } catch (_) { return; }
    if (!cfg || !cfg.enabled) return;
    const wrap = document.createElement('div');
    wrap.className = 'space-y-2 mt-3';
    wrap.innerHTML = `<div class="flex items-center gap-2 text-[11px] text-slate-400"><span class="flex-1 h-px bg-white/10"></span>hoặc<span class="flex-1 h-px bg-white/10"></span></div>
      <a id="sso-login-btn" href="/api/v1/sso/login" class="block text-center w-full py-2.5 text-xs font-bold rounded-xl border border-cyan-400/40 text-cyan-300 hover:bg-cyan-400/10 transition">${e(cfg.display_name || 'Đăng nhập bằng SSO')}</a>`;
    if (form.parentNode) form.parentNode.insertBefore(wrap, form.nextSibling);
  }

  async function init() {
    await handleReturn();
    await renderButton();
  }
  return { init, handleReturn, renderButton };
})();
window.SsoLogin = SsoLogin;
window.addEventListener('DOMContentLoaded', () => { SsoLogin.init(); });
