# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 Dương Thanh Lịch — VN-MateAI. See LICENSE and NOTICE.
"""
Thanh điều hướng portal (2026-10-12): xếp theo cách dùng — Tổng quan → Vận hành hằng ngày → Kết nối doanh nghiệp → Trợ lý AI & Robot
→ Bảo mật & Quản trị. Khoá lại để thêm tab sau này không làm rối:
  - đúng 5 nhóm, mỗi tab đúng một nhóm, mọi tab trong VALID_TABS đều có nút;
  - không hai mục nào dùng chung biểu tượng; mục mở tab trình duyệt mới có `target=_blank`, `rel=noopener` và dấu ↗;
  - tên mục khớp nội dung tab (không còn "Robot - HUD" cho tab giọng nói, không còn nhãn "n8n" sai cho Topology).
"""
from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
HTML = (ROOT / "web" / "index.html").read_text(encoding="utf-8")
APP = (ROOT / "web" / "app.js").read_text(encoding="utf-8")
NAV = HTML[HTML.index('<nav class="p-3'):HTML.index("</nav>")]

EXPECTED = [
    ("TỔNG QUAN", ["dashboard", "roi-dashboard", "topology"]),
    ("VẬN HÀNH HẰNG NGÀY", ["infra-monitor", "tasks", "playbooks"]),
    ("KẾT NỐI DOANH NGHIỆP", ["system-integration", "agent-gateway", "dev-fleet"]),
    ("TRỢ LÝ AI & ROBOT", ["ai-manager", "skills", "voice"]),
    ("BẢO MẬT & QUẢN TRỊ", ["security", "config", "logs"]),
]


def groups():
    out, cur = [], None
    for m in re.finditer(r"<span>([^<]+)</span>\s*<span class=\"w-1\.5|id=\"nav-([a-z-]+)\"", NAV):
        if m.group(1):
            cur = (m.group(1).strip(), [])
            out.append(cur)
        else:
            cur[1].append(m.group(2))
    return out


def item_html(item_id):
    i = NAV.index(f'id="nav-{item_id}"')
    a = max(NAV.rfind("<div", 0, i), NAV.rfind("<a ", 0, i))
    return NAV[a:NAV.index("</span>", NAV.index('whitespace-nowrap', i)) + 7]


def test_five_groups_in_usage_order():
    assert groups() == EXPECTED


def test_every_tab_has_exactly_one_button():
    valid = re.search(r"const VALID_TABS = \[([^\]]+)\]", APP).group(1)
    tabs = set(re.findall(r"'([a-z-]+)'", valid))
    in_nav = [k for _, ks in EXPECTED for k in ks]
    assert len(in_nav) == len(set(in_nav))
    assert tabs <= set(in_nav), tabs - set(in_nav)


def test_icons_are_distinct():
    icons = {}
    for _, ks in EXPECTED:
        for k in ks:
            svg = re.search(r"<svg[^>]*>(.*?)</svg>", item_html(k), re.S).group(1)
            icons.setdefault(re.sub(r"\s+", " ", svg).strip(), []).append(k)
    assert all(len(v) == 1 for v in icons.values()), [v for v in icons.values() if len(v) > 1]


def test_external_links_are_marked_and_safe():
    for k in ("roi-dashboard", "topology"):
        i = NAV.index(f'id="nav-{k}"')
        tag = NAV[NAV.rfind("<a ", 0, i):NAV.index("</a>", i)]
        assert 'target="_blank"' in tag and 'rel="noopener"' in tag and 'd="M8 7h9v9"' in tag and "mở tab mới" in tag, k
    assert ">n8n<" not in NAV


def test_labels_match_tab_content():
    assert "Giọng Nói, Robot &amp; HUD" in NAV or "Giọng Nói, Robot & HUD" in NAV
    assert "Robot - HUD" not in NAV and "Kho Kỹ Năng RPA" not in NAV
