// SPDX-License-Identifier: Apache-2.0
// Copyright (c) 2026 Dương Thanh Lịch — VN-MateAI. See LICENSE and NOTICE.
/**
 * web/auth-mfa.js — xác thực hai lớp (TOTP) trên giao diện:
 *   LoginMfa : bước nhập mã sau khi đúng mật khẩu, và màn BẮT BUỘC cài MFA cho vai trò bị yêu cầu (trên màn đăng nhập).
 *   MfaCard  : thẻ "Xác thực hai lớp" trong tab Bảo mật (bật / xem trạng thái / tắt).
 * Mã bí mật chỉ hiện một lần lúc cài; mã khôi phục chỉ hiện một lần lúc bật. Chữ động đều qua _esc.
 * Cần các hàm chung của app.js: _esc, API_BASE, apiFetch, completeLogin, showToast.
 */
const LoginMfa = (() => {
  const e = (s) => _esc(s == null ? '' : String(s));
  /** Ảnh QR của liên kết otpauth (máy chủ tạo sẵn dạng SVG data: URI) — không có thì chỉ hiện khoá nhập tay. */
  const qr = (s) => (typeof s.qr_svg === 'string' && s.qr_svg.startsWith('data:image/svg+xml')
    ? `<img src="${e(s.qr_svg)}" alt="Mã QR để quét bằng ứng dụng xác thực" class="block mx-auto w-48 rounded-lg bg-white p-2" />` : '');
  const INPUT = 'input-dark w-full text-center tracking-[0.4em] text-base py-2.5 font-mono';
  const BTN = 'btn-neon-cyan w-full py-2.5 text-xs font-bold mt-2';
  const GHOST = 'w-full py-2 text-xs text-slate-400 hover:text-slate-200 mt-1';

  async function call(path, body, token) {
    const headers = { 'Content-Type': 'application/json' };
    if (token) headers.Authorization = `Bearer ${token}`;
    const res = await fetch(`${API_BASE}${path}`, { method: 'POST', headers, body: JSON.stringify(body || {}) });
    const data = await res.json().catch(() => ({}));
    return { ok: res.ok, status: res.status, data };
  }
  const errText = (r) => (r.data && (r.data.detail || r.data.message)) || `HTTP ${r.status}`;

  function panel() {
    let p = document.getElementById('login-mfa-panel');
    if (!p) {
      p = document.createElement('div');
      p.id = 'login-mfa-panel';
      p.className = 'space-y-3';
      const form = document.getElementById('login-form');
      if (form && form.parentNode) form.parentNode.insertBefore(p, form.nextSibling);
    }
    const form = document.getElementById('login-form');
    if (form) form.classList.add('hidden');
    p.classList.remove('hidden');
    return p;
  }

  function back() {
    const p = document.getElementById('login-mfa-panel');
    if (p) { p.classList.add('hidden'); p.innerHTML = ''; }
    const form = document.getElementById('login-form');
    if (form) form.classList.remove('hidden');
  }

  const msg = (text) => `<p id="login-mfa-error" class="text-xs text-rose-400 min-h-[1rem]" role="alert">${e(text || '')}</p>`;

  /** Bước 2: nhập mã 6 số (hoặc mã khôi phục). */
  function promptCode(mfaToken) {
    const p = panel();
    p.innerHTML = `
      <p class="text-sm font-bold text-slate-800 dark:text-slate-100 text-center">Xác thực hai lớp</p>
      <p class="text-xs text-slate-400 text-center">Nhập mã 6 số trong ứng dụng xác thực (Google / Microsoft Authenticator…). Mất điện thoại? Dùng một mã khôi phục dạng <span class="font-mono">ABCDE-FGHIJ</span>.</p>
      <input id="login-mfa-code" class="${INPUT}" inputmode="text" autocomplete="one-time-code" maxlength="16" aria-label="Mã xác thực" placeholder="000000" />
      ${msg('')}
      <button type="button" id="login-mfa-submit" class="${BTN}">Xác nhận</button>
      <button type="button" id="login-mfa-back" class="${GHOST}">← Quay lại đăng nhập</button>`;
    const input = document.getElementById('login-mfa-code');
    const submit = async () => {
      const btn = document.getElementById('login-mfa-submit');
      const code = input.value.trim();
      if (!code) { document.getElementById('login-mfa-error').textContent = 'Nhập mã xác thực.'; return; }
      btn.disabled = true;
      try {
        const r = await call('/api/v1/login/mfa', { mfa_token: mfaToken, code });
        if (r.ok && r.data.access_token) { back(); completeLogin(r.data); return; }
        document.getElementById('login-mfa-error').textContent = errText(r);
        if (r.status === 401 && /hết hạn/.test(errText(r))) setTimeout(back, 1500);
      } finally { btn.disabled = false; }
    };
    document.getElementById('login-mfa-submit').addEventListener('click', submit);
    input.addEventListener('keydown', (ev) => { if (ev.key === 'Enter') { ev.preventDefault(); submit(); } });
    document.getElementById('login-mfa-back').addEventListener('click', back);
    input.focus();
  }

  /** Vai trò bắt buộc MFA mà chưa có: chỉ được cài MFA rồi mới vào hệ thống. */
  async function promptSetup(setupToken) {
    const p = panel();
    p.innerHTML = '<p class="text-xs text-slate-400 text-center">Đang tạo mã bí mật…</p>';
    const r = await call('/api/v1/auth/mfa/setup', {}, setupToken);
    if (!r.ok) { p.innerHTML = `${msg(errText(r))}<button type="button" id="login-mfa-back" class="${GHOST}">← Quay lại</button>`; document.getElementById('login-mfa-back').addEventListener('click', back); return; }
    const s = r.data;
    p.innerHTML = `
      <p class="text-sm font-bold text-slate-800 dark:text-slate-100 text-center">Vai trò của bạn bắt buộc xác thực hai lớp</p>
      ${qr(s)}
      <ol class="text-xs text-slate-300 list-decimal pl-5 space-y-1">
        <li>Mở ứng dụng xác thực → thêm tài khoản → <b>${s.qr_svg ? 'quét mã QR ở trên' : 'nhập khoá thủ công'}</b>${s.qr_svg ? ' (hoặc nhập khoá thủ công)' : ''}.</li>
        <li>Khoá: <span class="font-mono text-cyan-300 select-all" id="login-mfa-secret">${e(s.secret_grouped)}</span> (theo thời gian, 6 số, 30 giây).</li>
        <li>Nhập mã 6 số đang hiện để xác nhận.</li></ol>
      <details class="text-[11px] text-slate-400"><summary class="cursor-pointer">Liên kết otpauth (dán vào ứng dụng hỗ trợ)</summary><p class="font-mono break-all select-all mt-1">${e(s.otpauth_uri)}</p></details>
      <input id="login-mfa-code" class="${INPUT}" inputmode="numeric" autocomplete="one-time-code" maxlength="8" aria-label="Mã xác thực" placeholder="000000" />
      ${msg('')}
      <button type="button" id="login-mfa-submit" class="${BTN}">Bật xác thực hai lớp</button>`;
    document.getElementById('login-mfa-submit').addEventListener('click', async () => {
      const btn = document.getElementById('login-mfa-submit');
      btn.disabled = true;
      try {
        const done = await call('/api/v1/auth/mfa/enable', { code: document.getElementById('login-mfa-code').value.trim() }, setupToken);
        if (!done.ok) { document.getElementById('login-mfa-error').textContent = errText(done); return; }
        showRecovery(done.data.recovery_codes, () => { back(); completeLogin(done.data); });
      } finally { btn.disabled = false; }
    });
    document.getElementById('login-mfa-code').focus();
  }

  function showRecovery(codes, onDone) {
    const p = panel();
    p.innerHTML = `
      <p class="text-sm font-bold text-emerald-300 text-center">Đã bật xác thực hai lớp</p>
      <p class="text-xs text-slate-300">Lưu <b>10 mã khôi phục</b> này ở nơi an toàn (trình quản lý mật khẩu / giấy cất két). Mỗi mã dùng <b>một lần</b>, dùng khi mất điện thoại. <b>Sẽ không hiện lại.</b></p>
      <div class="grid grid-cols-2 gap-1 font-mono text-sm text-cyan-200 bg-black/30 rounded-lg p-3 select-all" id="login-mfa-codes">${(codes || []).map((c) => `<span>${e(c)}</span>`).join('')}</div>
      <button type="button" id="login-mfa-saved" class="${BTN}">Tôi đã lưu — tiếp tục</button>`;
    document.getElementById('login-mfa-saved').addEventListener('click', onDone);
  }

  /** Mật khẩu ban đầu / admin vừa đặt lại / mật khẩu dev: đặt mật khẩu mới rồi mới vào (MFA nếu có đi tiếp sau). */
  function promptPasswordChange(changeToken, detail) {
    const p = panel();
    const PW = 'input-dark w-full text-sm py-2.5 px-3';
    p.innerHTML = `
      <p class="text-sm font-bold text-slate-800 dark:text-slate-100 text-center">Đặt mật khẩu mới</p>
      <p class="text-xs text-slate-400 text-center">${e(detail || 'Bạn cần đặt mật khẩu mới trước khi vào hệ thống.')} Ít nhất 10 ký tự, có chữ và số, khác tên đăng nhập.</p>
      <input id="login-pw-new" type="password" class="${PW}" autocomplete="new-password" aria-label="Mật khẩu mới" placeholder="Mật khẩu mới" />
      <input id="login-pw-again" type="password" class="${PW}" autocomplete="new-password" aria-label="Nhập lại mật khẩu mới" placeholder="Nhập lại mật khẩu mới" />
      ${msg('')}
      <button type="button" id="login-pw-submit" class="${BTN}">Lưu mật khẩu mới</button>
      <button type="button" id="login-mfa-back" class="${GHOST}">← Quay lại đăng nhập</button>`;
    const err = (t) => { document.getElementById('login-mfa-error').textContent = t; };
    const submit = async () => {
      const a = document.getElementById('login-pw-new').value;
      const b = document.getElementById('login-pw-again').value;
      if (a !== b) { err('Hai lần nhập không khớp.'); return; }
      const btn = document.getElementById('login-pw-submit');
      btn.disabled = true;
      try {
        const r = await call('/api/v1/login/change-password', { change_token: changeToken, new_password: a });
        if (!r.ok) { err(errText(r)); if (r.status === 401) setTimeout(back, 1500); return; }
        const d = r.data || {};
        if (d.access_token) { back(); completeLogin(d); return; }
        if (d.status === 'mfa_required' && d.mfa_token) { promptCode(d.mfa_token); return; }
        if (d.status === 'mfa_setup_required' && d.setup_token) { promptSetup(d.setup_token); return; }
        err(errText(r));
      } finally { btn.disabled = false; }
    };
    document.getElementById('login-pw-submit').addEventListener('click', submit);
    document.getElementById('login-pw-again').addEventListener('keydown', (ev) => { if (ev.key === 'Enter') { ev.preventDefault(); submit(); } });
    document.getElementById('login-mfa-back').addEventListener('click', back);
    document.getElementById('login-pw-new').focus();
  }

  return { promptCode, promptSetup, showRecovery, back, promptPasswordChange };
})();

/** Thẻ MFA trong tab Bảo mật. */
const MfaCard = (() => {
  const e = (s) => _esc(s == null ? '' : String(s));
  /** Ảnh QR của liên kết otpauth (máy chủ tạo sẵn dạng SVG data: URI) — không có thì chỉ hiện khoá nhập tay. */
  const qr = (s) => (typeof s.qr_svg === 'string' && s.qr_svg.startsWith('data:image/svg+xml')
    ? `<img src="${e(s.qr_svg)}" alt="Mã QR để quét bằng ứng dụng xác thực" class="block mx-auto w-48 rounded-lg bg-white p-2" />` : '');
  const BOX = 'rounded-xl border border-slate-200 dark:border-slate-700 bg-white dark:bg-slate-800/60 p-4 space-y-2';
  const BTN = 'px-3 py-1.5 text-xs font-semibold rounded-lg transition active:scale-95 disabled:opacity-40';
  const INPUT = 'w-full px-3 py-2 text-xs rounded-lg border border-slate-200 dark:border-slate-600 bg-white dark:bg-slate-900 text-slate-800 dark:text-slate-100 font-mono';

  async function req(path, method = 'GET', body = null) {
    const res = await apiFetch(`${API_BASE}${path}`, body ? { method, headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) } : { method });
    const data = await res.json().catch(() => ({}));
    return { ok: res.ok, status: res.status, data };
  }
  const err = (r) => (r.data && (r.data.detail || r.data.message)) || `HTTP ${r.status}`;
  const root = () => document.getElementById('mfa-card-root');

  async function render() {
    const el = root();
    if (!el) return;
    const r = await req('/api/v1/auth/mfa/status');
    if (!r.ok) { el.innerHTML = `<div class="${BOX}"><p class="text-xs text-rose-500">Không tải được trạng thái MFA: ${e(err(r))}</p></div>`; return; }
    const s = r.data;
    const head = '<h3 class="text-sm font-bold text-slate-800 dark:text-slate-100">Xác thực hai lớp (MFA)</h3>';
    if (s.auth_source === 'sso') {
      el.innerHTML = `<div class="${BOX}">${head}<p class="text-xs text-slate-600 dark:text-slate-300">Tài khoản đăng nhập qua SSO: MFA do hệ thống định danh của doanh nghiệp (IdP) quản lý.</p></div>`;
      return;
    }
    if (s.enabled) {
      el.innerHTML = `<div class="${BOX}">${head}
        <p class="text-xs text-emerald-600 dark:text-emerald-400 font-semibold">Đang bật — còn ${e(s.recovery_codes_left)} mã khôi phục.</p>
        ${s.required_for_my_role ? '<p class="text-xs text-slate-500">Vai trò của bạn bắt buộc MFA nên không tự tắt được.</p>' : `
        <div class="grid grid-cols-1 sm:grid-cols-3 gap-2"><input id="mfa-off-pw" type="password" class="${INPUT}" placeholder="Mật khẩu" autocomplete="current-password" aria-label="Mật khẩu" />
          <input id="mfa-off-code" class="${INPUT}" placeholder="Mã MFA hiện tại" aria-label="Mã MFA" /><button type="button" id="mfa-off" class="${BTN} border border-rose-300 text-rose-600">Tắt MFA</button></div>`}
        <p id="mfa-msg" class="text-xs text-slate-500"></p></div>`;
      const off = document.getElementById('mfa-off');
      if (off) off.addEventListener('click', async () => {
        off.disabled = true;
        const d = await req('/api/v1/auth/mfa/disable', 'POST', { password: document.getElementById('mfa-off-pw').value, code: document.getElementById('mfa-off-code').value.trim() });
        if (d.ok) { showToast('Đã tắt xác thực hai lớp', 'success'); render(); } else { document.getElementById('mfa-msg').textContent = err(d); off.disabled = false; }
      });
      return;
    }
    el.innerHTML = `<div class="${BOX}">${head}
      <p class="text-xs text-slate-600 dark:text-slate-300">Thêm lớp bảo vệ thứ hai bằng ứng dụng xác thực (Google / Microsoft Authenticator…). Nên bật cho tài khoản quản trị.</p>
      <button type="button" id="mfa-on" class="${BTN} bg-primary-600 hover:bg-primary-700 text-white">Bật MFA</button><p id="mfa-msg" class="text-xs text-slate-500"></p></div>`;
    document.getElementById('mfa-on').addEventListener('click', startSetup);
  }

  async function startSetup() {
    const el = root();
    const r = await req('/api/v1/auth/mfa/setup', 'POST', {});
    if (!r.ok) { document.getElementById('mfa-msg').textContent = err(r); return; }
    const s = r.data;
    el.innerHTML = `<div class="${BOX}"><h3 class="text-sm font-bold text-slate-800 dark:text-slate-100">Cài xác thực hai lớp</h3>
      ${qr(s)}
      <ol class="text-xs text-slate-600 dark:text-slate-300 list-decimal pl-5 space-y-1"><li>Trong ứng dụng xác thực: thêm tài khoản → <b>${s.qr_svg ? 'quét mã QR ở trên' : 'nhập khoá thủ công'}</b>${s.qr_svg ? ' (hoặc nhập khoá thủ công)' : ''}.</li>
      <li>Khoá: <span class="font-mono select-all text-primary-600 dark:text-primary-400">${e(s.secret_grouped)}</span></li><li>Nhập mã 6 số đang hiện để xác nhận.</li></ol>
      <details class="text-[11px] text-slate-500"><summary class="cursor-pointer">Liên kết otpauth</summary><p class="font-mono break-all select-all mt-1">${e(s.otpauth_uri)}</p></details>
      <div class="flex gap-2"><input id="mfa-code" class="${INPUT}" inputmode="numeric" maxlength="8" placeholder="000000" aria-label="Mã xác thực" /><button type="button" id="mfa-confirm" class="${BTN} bg-primary-600 text-white shrink-0">Xác nhận</button></div>
      <p id="mfa-msg" class="text-xs text-rose-500"></p></div>`;
    document.getElementById('mfa-confirm').addEventListener('click', async () => {
      const d = await req('/api/v1/auth/mfa/enable', 'POST', { code: document.getElementById('mfa-code').value.trim() });
      if (!d.ok) { document.getElementById('mfa-msg').textContent = err(d); return; }
      el.innerHTML = `<div class="${BOX}"><h3 class="text-sm font-bold text-emerald-600">Đã bật xác thực hai lớp</h3>
        <p class="text-xs text-slate-600 dark:text-slate-300">Lưu 10 mã khôi phục này ở nơi an toàn. Mỗi mã dùng một lần, <b>sẽ không hiện lại</b>.</p>
        <div class="grid grid-cols-2 gap-1 font-mono text-sm bg-slate-100 dark:bg-slate-900 rounded-lg p-3 select-all">${(d.data.recovery_codes || []).map((c) => `<span>${e(c)}</span>`).join('')}</div>
        <button type="button" id="mfa-done" class="${BTN} bg-primary-600 text-white">Tôi đã lưu</button></div>`;
      document.getElementById('mfa-done').addEventListener('click', render);
    });
  }

  return { onEnter: render };
})();
window.LoginMfa = LoginMfa;
window.MfaCard = MfaCard;
