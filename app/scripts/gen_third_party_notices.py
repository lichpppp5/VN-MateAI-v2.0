# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 Dương Thanh Lịch — VN-MateAI. See LICENSE and NOTICE.
"""
scripts/gen_third_party_notices.py
==================================
Sinh THIRD_PARTY_NOTICES.md từ metadata các gói Python đã cài (requirements*.txt) + danh sách tài nguyên web được đóng kèm.

    python scripts/gen_third_party_notices.py

Giấy phép lấy NGUYÊN VĂN từ metadata của từng gói (không tự suy đoán); gói chưa cài ghi rõ "chưa cài" để biết cần kiểm tra thêm.
"""
from __future__ import annotations

import importlib.metadata as md
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

WEB_ASSETS = [
    ("Chart.js 4.4.1", "MIT", "web/vendor/chart.umd.min.js", "https://www.chartjs.org"),
    ("Tailwind CSS (CSS đã build)", "MIT", "web/tailwind.css, web/tailwind-hud.css", "https://tailwindcss.com"),
    ("Be Vietnam Pro", "SIL OFL 1.1", "web/fonts/BeVietnamPro-*", "https://fonts.google.com/specimen/Be+Vietnam+Pro"),
    ("JetBrains Mono", "SIL OFL 1.1", "web/fonts/JetBrainsMono-*", "https://www.jetbrains.com/lp/mono/"),
    ("Inter", "SIL OFL 1.1", "web/fonts/Inter-*", "https://rsms.me/inter/"),
    ("Orbitron", "SIL OFL 1.1", "web/fonts/Orbitron-*", "https://fonts.google.com/specimen/Orbitron"),
    ("Rajdhani", "SIL OFL 1.1", "web/fonts/Rajdhani-*", "https://fonts.google.com/specimen/Rajdhani"),
    ("Share Tech Mono", "SIL OFL 1.1", "web/fonts/ShareTechMono-*", "https://fonts.google.com/specimen/Share+Tech+Mono"),
]


def license_of(name: str) -> str:
    try:
        meta = md.metadata(name)
    except md.PackageNotFoundError:
        return "(chưa cài — cần kiểm tra)"
    text = (meta.get("License-Expression") or "").strip()
    if not text:
        lic = (meta.get("License") or "").strip().replace("\n", " ")
        text = lic if lic and len(lic) < 60 else ""
    if not text:
        cls = [c.split("::")[-1].strip() for c in (meta.get_all("Classifier") or []) if c.startswith("License ::")]
        text = ", ".join(cls) or "(không khai báo)"
    return text


def package_names() -> list[str]:
    names: set[str] = set()
    for req in ("requirements.txt", "requirements-connectors.txt"):
        path = ROOT / req
        if not path.exists():
            continue
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.split("#")[0].strip()
            if line and not line.startswith("-"):
                names.add(re.split(r"[<>=!~\[; ]", line)[0])
    return sorted(names, key=str.lower)


def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    rows = [(n, license_of(n)) for n in package_names()]
    copyleft = [n for n, lic in rows if re.search(r"GPL|SSPL|Commons Clause|Non-?commercial", lic, re.I)]
    out = ["# Third-party notices", "",
           "VN-MateAI © 2026 Dương Thanh Lịch, giấy phép Apache-2.0 (xem `LICENSE`, `NOTICE`). "
           "Các thành phần dưới đây thuộc về tác giả của chúng và giữ giấy phép riêng. "
           "Tệp này được sinh bởi `scripts/gen_third_party_notices.py` từ metadata các gói đã cài.", "",
           "## Thư viện Python (requirements*.txt)", "", "| Gói | Giấy phép (theo metadata) |", "|---|---|"]
    out += [f"| {n} | {lic} |" for n, lic in rows]
    out += ["", "### Lưu ý về LGPL", ""]
    if copyleft:
        out += ["Các gói sau dùng giấy phép LGPL (hoặc tương tự). Chúng được dùng như thư viện Python độc lập (người dùng thay được bằng "
                "`pip install`); nếu bạn đóng gói chúng cùng ứng dụng (ví dụ PyInstaller), hãy giữ nguyên thông báo giấy phép của chúng "
                "và cho phép người nhận thay thế thư viện:", "", ", ".join(f"`{n}`" for n in copyleft), ""]
    else:
        out += ["Không có gói LGPL / GPL trong danh sách trên.", ""]
    out += ["## Tài nguyên giao diện được đóng kèm (`web/`)", "", "| Thành phần | Giấy phép | Vị trí | Nguồn |", "|---|---|---|---|"]
    out += [f"| {a} | {b} | `{c}` | {d} |" for a, b, c, d in WEB_ASSETS]
    out += ["", "Font theo SIL Open Font License 1.1: được dùng, đóng kèm và phân phối cùng phần mềm; không được bán riêng font.", "",
            "## MCP server tham chiếu chính thức (`vendor/mcp-servers/`)", "",
            "`mcp-server-time`, `mcp-server-fetch`, `mcp-server-git` 2026.10.10 của dự án Model Context Protocol "
            "(https://github.com/modelcontextprotocol/servers), đóng kèm nguyên mã đã rà soát. Giấy phép: MIT, đang chuyển dần sang "
            "Apache-2.0 — xem `vendor/mcp-servers/LICENSE` và LICENSE trong từng thư mục. Chạy trong môi trường Python riêng "
            "(`.mcp-venv` / `/opt/mcp-venv`) với các thư viện ghim ở `vendor/mcp-servers/constraints.txt` (mcp, httpx, GitPython, "
            "readabilipy, markdownify, protego…), mỗi thư viện theo giấy phép riêng.", "",
            "## Ứng dụng quản trị Next.js (`admin/`)", "",
            "Dùng các gói npm (Next.js, React, React Flow…) theo giấy phép của từng gói (chủ yếu MIT); danh sách đầy đủ nằm ở "
            "`admin/package.json` / `package-lock.json`.", "",
            "## Firmware ESP32 (`esp32_firmware/`)", "",
            "Xây trên Arduino-ESP32 / PlatformIO và các thư viện khai báo trong `platformio.ini`, mỗi thư viện theo giấy phép riêng.", ""]
    (ROOT / "THIRD_PARTY_NOTICES.md").write_text("\n".join(out), encoding="utf-8")
    print(f"Đã ghi THIRD_PARTY_NOTICES.md ({len(rows)} gói Python, {len(copyleft)} gói LGPL/copyleft)")


if __name__ == "__main__":
    main()
