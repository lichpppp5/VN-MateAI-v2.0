// SPDX-License-Identifier: Apache-2.0
// Copyright (c) 2026 Dương Thanh Lịch — VN-MateAI. See LICENSE and NOTICE.
/**
 * Tailwind cho HUD (web/hud.html + web/hud.js) — build sẵn thành web/tailwind-hud.css.
 * Theme chép NGUYÊN từ khối `tailwind.config` cũ trong hud.html (trước đây dùng
 * cdn.tailwindcss.com: trình duyệt tự biên dịch CSS mỗi lần mở HUD, cần Internet).
 * Build lại:  python scripts/build_portal_css.py
 */
module.exports = {
  content: ['../hud.html', '../hud.js', '../voice-audio-queue.js'],
  theme: {
    extend: {
      fontFamily: {
        orbitron: ['Orbitron', 'sans-serif'],
        rajdhani: ['Rajdhani', 'sans-serif'],
        mono: ['"Share Tech Mono"', 'Courier New', 'monospace'],
      },
      colors: {
        vnmate: {
          cyan: '#00f2fe', neon: '#00ffff', blue: '#0284c7', cobalt: '#2563eb',
          amber: '#f59e0b', emerald: '#10b981', dark: '#020813',
        },
      },
    },
  },
};
