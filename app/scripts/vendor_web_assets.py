# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 Dương Thanh Lịch — VN-MateAI. See LICENSE and NOTICE.
"""
scripts/vendor_web_assets.py
============================
Tải font Google + Chart.js về `web/` để Portal / HUD / ROI chạy được khi mạng LAN KHÔNG ra Internet
(trước đây tải từ fonts.googleapis.com và cdn.jsdelivr.net: offline là rơi về font hệ thống và mất biểu đồ).

Chạy một lần khi cần đổi / thêm font hoặc nâng phiên bản Chart.js:

    python scripts/vendor_web_assets.py

Ra:  web/fonts/*.woff2 · web/fonts-portal.css · web/fonts-hud.css · web/fonts-roi.css · web/vendor/chart.umd.min.js
Chỉ lấy tập ký tự `latin`, `latin-ext`, `vietnamese` (đủ tiếng Việt + Anh); các tập khác (Cyrillic, Hy Lạp…) bỏ để nhẹ.
"""
from __future__ import annotations

import re
import sys
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WEB = ROOT / "web"
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"
KEEP = {"latin", "latin-ext", "vietnamese"}

SETS = {
    "fonts-portal.css": "https://fonts.googleapis.com/css2?family=Be+Vietnam+Pro:ital,wght@0,300;0,400;0,500;0,600;0,700;0,800;1,400"
                        "&family=JetBrains+Mono:wght@400;500;600;700&display=swap",
    "fonts-hud.css": "https://fonts.googleapis.com/css2?family=Orbitron:wght@500;700;800;900&family=Rajdhani:wght@500;600;700"
                     "&family=Share+Tech+Mono&display=swap",
    "fonts-roi.css": "https://fonts.googleapis.com/css2?family=Inter:wght@300;400;500;600;700;800&display=swap",
}
CHART = ("vendor/chart.umd.min.js", "https://cdn.jsdelivr.net/npm/chart.js@4.4.1/dist/chart.umd.min.js")


def get(url: str, ua: bool = False) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": UA} if ua else {})
    with urllib.request.urlopen(req, timeout=60) as resp:
        return resp.read()


def main() -> None:
    (WEB / "fonts").mkdir(exist_ok=True)
    (WEB / "vendor").mkdir(exist_ok=True)
    total = 0
    for css_name, url in SETS.items():
        css = get(url, ua=True).decode("utf-8")
        out = []
        for subset, block in re.findall(r"/\*\s*([\w-]+)\s*\*/\s*(@font-face\s*\{.*?\})", css, flags=re.S):
            if subset not in KEEP:
                continue
            m = re.search(r"url\((https://[^)]+\.woff2)\)", block)
            if not m:
                continue
            fam = re.search(r"font-family:\s*'([^']+)'", block).group(1).replace(" ", "")
            weight = re.search(r"font-weight:\s*(\d+)", block).group(1)
            style = re.search(r"font-style:\s*(\w+)", block).group(1)
            fname = f"{fam}-{weight}{'i' if style == 'italic' else ''}-{subset}.woff2"
            path = WEB / "fonts" / fname
            if not path.exists():
                path.write_bytes(get(m.group(1)))
            total += path.stat().st_size
            out.append(f"/* {subset} */\n" + block.replace(m.group(1), f"/static/fonts/{fname}"))
        (WEB / css_name).write_text("\n".join(out) + "\n", encoding="utf-8")
        print(f"{css_name}: {len(out)} @font-face")
    rel, url = CHART
    (WEB / rel).write_bytes(get(url))
    print(f"{rel}: {(WEB / rel).stat().st_size // 1024} KB · fonts: {total // 1024} KB")


if __name__ == "__main__":
    try:
        main()
    except OSError as exc:
        sys.exit(f"Cần Internet để tải: {exc}")
