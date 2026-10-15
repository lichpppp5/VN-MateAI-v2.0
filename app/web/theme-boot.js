// SPDX-License-Identifier: Apache-2.0
// Copyright (c) 2026 Dương Thanh Lịch — VN-MateAI. See LICENSE and NOTICE.
// Đặt theme sáng / tối trước khi trang vẽ (tránh nháy). Nạp đồng bộ trong <head>; tách khỏi HTML để chạy được với CSP chặt.
(function () {
  try {
    var savedTheme = localStorage.getItem('theme');
    var prefersDark = window.matchMedia && window.matchMedia('(prefers-color-scheme: dark)').matches;
    if (savedTheme === 'dark' || (!savedTheme && prefersDark)) {
      document.documentElement.classList.add('dark');
    } else {
      document.documentElement.classList.remove('dark');
    }
  } catch (e) { }
})();
