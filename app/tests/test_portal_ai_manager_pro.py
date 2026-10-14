# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 Dương Thanh Lịch — VN-MateAI. See LICENSE and NOTICE.
"""
tests/test_portal_ai_manager_pro.py
===================================
Portal — tab Quản Lý Trợ Lý AI chia tab con; thẻ Thử trước khi lưu / Hiệu năng /
Lịch sử; Tailwind build sẵn thay cdn.tailwindcss.com (2026-10-05).
"""
from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
HTML = (ROOT / "web" / "index.html").read_text(encoding="utf-8")
APP = (ROOT / "web" / "app.js").read_text(encoding="utf-8")


def test_subtabs_cover_every_card():
    tab = HTML[HTML.index('id="tab-ai-manager"'):HTML.index('id="tab-skills"')]
    subs = set(re.findall(r'data-ai-sub="([a-z]+)"', tab))
    buttons = set(re.findall(r'data-ai-subtab="([a-z]+)"', tab))
    assert subs == {"brain", "persona", "audio", "templates", "ops", "llmops"}   # llmops: Chi phí & Đánh giá (web/llmops.js)
    assert buttons == subs | {"all"}
    assert 'id="ai-col-left"' in tab and 'id="ai-col-right"' in tab
    for fn in ("showAISubTab", "runAIPreview", "loadAIModelStats", "loadConfigHistory",
               "showConfigDiff", "restoreConfigVersion"):
        assert f"function {fn}(" in APP, fn


def test_new_cards_call_real_apis_and_escape_output():
    for api in ("/api/v1/llm/preview", "/api/v1/voice/model-stats", "/api/v1/config/history"):
        assert api in APP, api
    stats = APP[APP.index("async function loadAIModelStats("):APP.index("function _fmtCfgVal(")]
    assert "${_esc(r.model)}" in stats
    hist = APP[APP.index("function _renderCfgChanges("):APP.index("async function loadConfigHistory(")]
    assert "${_esc(c.path)}" in hist and "khoá bí mật" in hist


def test_server_validation_errors_are_readable():
    save = APP[APP.index("async function apiSaveConfig("):APP.index("async function apiGetSkills(")]
    assert "unknown_models" in save and "d.errors" in save          # không còn "[object Object]"


def test_tailwind_is_prebuilt_not_cdn():
    assert '<script src="https://cdn.tailwindcss.com"></script>' not in HTML
    assert "tailwind.config = {" not in HTML
    assert '<link rel="stylesheet" href="/static/tailwind.css' in HTML
    css = (ROOT / "web" / "tailwind.css").read_text(encoding="utf-8")
    # Lớp màu riêng của theme + lớp dark: + lớp mới thêm ở đợt này đều phải có trong CSS build.
    for cls in (".bg-primary-600", r".dark\:bg-white\/\[0\.03\]", ".from-violet-600", ".lg\\:col-span-12"):
        assert cls in css, cls


def test_hud_tailwind_is_prebuilt_not_cdn():
    hud = (ROOT / "web" / "hud.html").read_text(encoding="utf-8")
    assert '<script src="https://cdn.tailwindcss.com"></script>' not in hud and "tailwind.config = {" not in hud
    assert '<link rel="stylesheet" href="/static/tailwind-hud.css' in hud
    css = (ROOT / "web" / "tailwind-hud.css").read_text(encoding="utf-8")
    # Theme riêng của HUD (font Orbitron, màu vnmate) phải có trong CSS build.
    assert ".font-orbitron" in css and "Orbitron" in css and ".text-cyan-300" in css
