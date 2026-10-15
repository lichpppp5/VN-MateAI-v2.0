// SPDX-License-Identifier: Apache-2.0
// Copyright (c) 2026 Dương Thanh Lịch — VN-MateAI. See LICENSE and NOTICE.
/*
 * "Quên mật khẩu?" trên màn đăng nhập: gửi yêu cầu tới quản trị viên (POST /api/v1/login/forgot-password, không cần đăng
 * nhập). Máy chủ luôn trả cùng một câu — không lộ tài khoản có tồn tại không. Admin thấy yêu cầu trong Hộp thư trên header
 * (header-inbox.js) và qua kênh cảnh báo, xác minh người thật rồi cấp mật khẩu tạm.
 */
const PasswordReset = (() => {
  const $ = (id) => document.getElementById(id);

  function show(forgot) {
    const login = $('login-form');
    const form = $('forgot-form');
    const link = $('forgot-link-row');
    if (!login || !form) return;
    login.classList.toggle('hidden', forgot);
    if (link) link.classList.toggle('hidden', forgot);
    form.classList.toggle('hidden', !forgot);
    const st = $('forgot-status');
    if (st) { st.textContent = ''; st.classList.add('hidden'); }
    if (forgot) {
      const u = $('login-username');
      if ($('forgot-username') && u && u.value && !$('forgot-username').value) $('forgot-username').value = u.value;
      ($('forgot-username') || {}).focus?.();
    }
  }

  function status(msg, ok) {
    const st = $('forgot-status');
    if (!st) return;
    st.textContent = msg;
    st.className = `text-xs ${ok ? 'text-emerald-700 dark:text-emerald-400' : 'text-rose-600 dark:text-rose-400'}`;
  }

  async function submit(ev) {
    if (ev) ev.preventDefault();
    const username = ($('forgot-username').value || '').trim();
    const note = ($('forgot-note').value || '').trim();
    if (!username) { status('Nhập tên đăng nhập.', false); return; }
    const btn = $('btn-forgot-submit');
    btn.disabled = true;
    try {
      const res = await fetch('/api/v1/login/forgot-password', {
        method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ username, note }) });
      const data = await res.json().catch(() => ({}));
      if (res.ok) status(data.message || 'Đã gửi yêu cầu tới quản trị viên.', true);
      else status(data.detail && typeof data.detail === 'string' ? data.detail : `Không gửi được (HTTP ${res.status}).`, false);
    } catch (e) {
      status('Không kết nối được máy chủ. Liên hệ trực tiếp quản trị viên.', false);
    } finally {
      btn.disabled = false;
    }
  }

  function init() {
    $('btn-forgot-password')?.addEventListener('click', () => show(true));
    $('btn-forgot-back')?.addEventListener('click', () => show(false));
    $('forgot-form')?.addEventListener('submit', submit);
  }

  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', init);
  else init();
  return { show, submit };
})();
