// SPDX-License-Identifier: Apache-2.0
// Copyright (c) 2026 Dương Thanh Lịch — VN-MateAI. See LICENSE and NOTICE.
/**
 * Tailwind cho Portal (web/index.html + web/app.js) — build sẵn thành web/tailwind.css.
 * Trước đây dùng cdn.tailwindcss.com: trình duyệt tự biên dịch CSS mỗi lần mở trang
 * (chậm, cần Internet, Tailwind khuyến cáo không dùng cho môi trường thật).
 *
 * Build lại sau khi đổi giao diện:  python scripts/build_portal_css.py
 * Cấu hình theme dưới đây chép NGUYÊN từ khối `tailwind.config` cũ trong index.html.
 */
module.exports = {
  content: ['../index.html', '../app.js', '../voice-audio-queue.js', '../dev-fleet.js', '../header-inbox.js', '../infra-monitor.js', '../playbooks.js', '../llmops.js', '../agent-gateway.js', '../agent-directory.js', '../auth-mfa.js', '../auth-sso.js'],
  darkMode: 'class',
  theme: {
    extend: {
      colors: {
        primary: { 50: '#eff6ff', 100: '#dbeafe', 500: '#3b82f6', 600: '#2563eb', 700: '#1d4ed8', 900: '#1e3a8a' },
        surface: { light: '#ffffff', dark: '#1e293b' },
        background: { light: '#f8fafc', dark: '#0f172a' },
      },
      fontFamily: {
        sans: ['"Be Vietnam Pro"', '"Plus Jakarta Sans"', 'Inter', '-apple-system', 'BlinkMacSystemFont', 'Segoe UI', 'Roboto', 'sans-serif'],
        mono: ['"JetBrains Mono"', 'ui-monospace', 'SFMono-Regular', 'Menlo', 'Monaco', 'Consolas', 'monospace'],
      },
    },
  },
};
