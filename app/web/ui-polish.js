// SPDX-License-Identifier: Apache-2.0
// Copyright (c) 2026 Dương Thanh Lịch — VN-MateAI. See LICENSE and NOTICE.
/**
 * web/ui-polish.js — menu trái dạng ngăn kéo trên màn hình hẹp (< 1024px). CSS ở ui-polish.css.
 * Mở bằng nút ☰ ở header; đóng khi bấm nền mờ, bấm một mục menu, nhấn Esc hoặc khi phóng to lại.
 */
(() => {
  const body = document.body;
  const open = () => { body.classList.add('sidebar-open'); sync(true); };
  const close = () => { body.classList.remove('sidebar-open'); sync(false); };
  const sync = (isOpen) => {
    const btn = document.getElementById('btn-sidebar-toggle');
    if (btn) btn.setAttribute('aria-expanded', isOpen ? 'true' : 'false');
  };
  window.toggleSidebar = () => (body.classList.contains('sidebar-open') ? close() : open());
  window.closeSidebar = close;

  document.addEventListener('click', (ev) => {
    if (!body.classList.contains('sidebar-open')) return;
    if (ev.target.closest('#app-sidebar .nav-btn, #app-sidebar a, #sidebar-backdrop')) close();
  });
  document.addEventListener('keydown', (ev) => { if (ev.key === 'Escape') close(); });
  window.matchMedia('(min-width: 1024px)').addEventListener('change', (m) => { if (m.matches) close(); });
})();
