# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 Dương Thanh Lịch — VN-MateAI. See LICENSE and NOTICE.
"""Ghi công tác giả: giấy phép Apache-2.0 + NOTICE còn nguyên, mọi tệp mã có tiêu đề, giao diện hiện tên tác giả."""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_license_is_apache_and_notice_names_the_author():
    lic = (ROOT / "LICENSE").read_text(encoding="utf-8")
    assert "Apache License" in lic and "Version 2.0, January 2004" in lic and "TERMS AND CONDITIONS" in lic
    notice = (ROOT / "NOTICE").read_text(encoding="utf-8")
    assert "Dương Thanh Lịch" in notice and "Apache License, Version 2.0" in notice
    for name in ("AUTHORS", "CITATION.cff", "CONTRIBUTING.md", "THIRD_PARTY_NOTICES.md", "docs/legal/ATTRIBUTION.md"):
        assert (ROOT / name).is_file(), name
    assert "Dương Thanh Lịch" in (ROOT / "AUTHORS").read_text(encoding="utf-8")
    assert "license: Apache-2.0" in (ROOT / "CITATION.cff").read_text(encoding="utf-8")


def test_every_source_file_carries_the_copyright_header():
    res = subprocess.run([sys.executable, str(ROOT / "scripts" / "license_headers.py"), "--check"],
                         capture_output=True, text=True, encoding="utf-8")
    assert res.returncode == 0, "Thiếu tiêu đề bản quyền (chạy: python scripts/license_headers.py --apply)\n" + res.stdout[-1500:]


def test_the_product_shows_who_made_it():
    for page in ("index.html", "hud.html", "roi_dashboard.html"):
        text = (ROOT / "web" / page).read_text(encoding="utf-8")
        assert "Dương Thanh Lịch" in text, page
    index = (ROOT / "web" / "index.html").read_text(encoding="utf-8")
    assert 'id="app-attribution"' in index and 'id="login-attribution"' in index
    server = (ROOT / "src" / "mateai" / "interfaces" / "http" / "server.py").read_text(encoding="utf-8")
    assert 'license_info={"name": "Apache-2.0"' in server and "Dương Thanh Lịch" in server


def test_readme_no_longer_claims_proprietary_terms():
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    assert "License 2.0" in readme and "Bảo lưu mọi quyền" not in readme
