# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 Dương Thanh Lịch — VN-MateAI. See LICENSE and NOTICE.
"""Tinh chỉnh giao diện (màu / font / mobile / truy cập): chạy offline, đạt tương phản, menu thu gọn, không nút "submit nhầm"."""
from __future__ import annotations

import re
from pathlib import Path

WEB = Path(__file__).resolve().parents[1] / "web"
INDEX = (WEB / "index.html").read_text(encoding="utf-8")
CSS = (WEB / "ui-polish.css").read_text(encoding="utf-8")


def _lum(hex_color: str) -> float:
    h = hex_color.lstrip("#")
    ch = [int(h[i:i + 2], 16) / 255 for i in (0, 2, 4)]
    f = lambda c: c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4
    return 0.2126 * f(ch[0]) + 0.7152 * f(ch[1]) + 0.0722 * f(ch[2])


def _ratio(a: str, b: str) -> float:
    hi, lo = sorted((_lum(a), _lum(b)), reverse=True)
    return (hi + 0.05) / (lo + 0.05)


def test_pages_do_not_depend_on_the_internet_for_fonts_or_charts():
    for page in ("index.html", "hud.html", "roi_dashboard.html"):
        text = (WEB / page).read_text(encoding="utf-8")
        assert "fonts.googleapis.com" not in text and "cdn.jsdelivr.net" not in text, page
    assert (WEB / "vendor" / "chart.umd.min.js").stat().st_size > 100_000


def test_every_local_font_file_in_the_css_exists_and_covers_vietnamese():
    for css in ("fonts-portal.css", "fonts-hud.css", "fonts-roi.css"):
        text = (WEB / css).read_text(encoding="utf-8")
        files = re.findall(r"/static/fonts/([\w.-]+\.woff2)", text)
        assert files and all((WEB / "fonts" / f).is_file() for f in files), css
    assert "vietnamese" in (WEB / "fonts-portal.css").read_text(encoding="utf-8")


def test_override_palette_meets_wcag_aa():
    light_bg, dark_bg = ["#ffffff", "#f8fafc", "#f1f5f9"], ["#1e293b", "#0f172a", "#0c1322"]
    light_rules = re.findall(r"html:not\(\.dark\)\) [^{]+\{ color: (#[0-9a-f]{6}); \}", CSS)
    dark_rules = re.findall(r"html\.dark\)? [^{]*\{ color: (#[0-9a-f]{6}); \}", CSS)
    assert len(light_rules) >= 10 and len(dark_rules) >= 8
    for c in light_rules:
        assert all(_ratio(c, b) >= 4.5 for b in light_bg), (c, [round(_ratio(c, b), 2) for b in light_bg])
    for c in dark_rules:
        assert all(_ratio(c, b) >= 4.5 for b in dark_bg), (c, [round(_ratio(c, b), 2) for b in dark_bg])


def test_minimum_font_size_and_keyboard_focus_rules_exist():
    assert ".text-\[9px\], .text-\[10px\] { font-size: 11px; }" in CSS
    assert ":focus-visible" in CSS and "prefers-reduced-motion" in CSS


def test_sidebar_is_a_drawer_on_narrow_screens():
    assert 'id="app-sidebar"' in INDEX and 'id="btn-sidebar-toggle"' in INDEX and 'id="sidebar-backdrop"' in INDEX
    assert "ui-polish.css" in INDEX and "ui-polish.js" in INDEX
    block = CSS[CSS.index("@media (max-width: 1023.98px)"):]
    assert "#app-sidebar" in block and "translateX(-102%)" in block
    assert "body.sidebar-open" in CSS and "toggleSidebar" in (WEB / "ui-polish.js").read_text(encoding="utf-8")


def test_hud_banner_is_readable_in_light_mode():
    m = re.search(r'<div\s+class="([^"]*border-cyan-500/30[^"]*bg-gradient-to-r[^"]*)"', INDEX)
    assert m and "from-cyan-50" in m.group(1) and "dark:from-cyan-950/40" in m.group(1)


def test_buttons_outside_forms_never_submit_by_accident():
    forms = [(m.start(), m.end()) for m in re.finditer(r"<form\b.*?</form>", INDEX, flags=re.S)]
    bad = [m.start() for m in re.finditer(r"<button\b[^>]*>", INDEX)
           if "type=" not in m.group(0) and not any(a <= m.start() < b for a, b in forms)]
    assert bad == []


def test_raw_javascript_errors_are_not_shown_to_users():
    app = (WEB / "app.js").read_text(encoding="utf-8")
    assert "function friendlyErrorText" in app
    assert "Không tải được: ${_esc(err.message)}" not in app


def test_admin_app_is_offline_safe_and_readable():
    """Ứng dụng admin (Next.js, /admin/topology…): không gọi Google Fonts, cỡ chữ >= 11px, chữ phụ đủ tương phản."""
    admin = WEB.parent / "admin"
    layout = (admin / "app" / "layout.tsx").read_text(encoding="utf-8")
    assert "fonts.googleapis.com" not in layout and "/static/fonts-hud.css" in layout
    sources = [p for p in list((admin / "app").rglob("*.tsx")) + list((admin / "components").rglob("*.tsx"))]
    assert sources
    for p in sources:
        text = p.read_text(encoding="utf-8")
        assert not re.search(r"text-\[(8|9|10)px\]", text), f"{p.name}: chữ nhỏ hơn 11px"
        assert "text-slate-500" not in text, f"{p.name}: text-slate-500 trên nền tối chỉ đạt ~3:1 (dùng slate-400)"
    node = (admin / "components" / "topology" / "CyberNode.tsx").read_text(encoding="utf-8")
    assert "#475569" not in node                      # slate-600 trên nền node: 2,4:1
