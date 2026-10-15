# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 Dương Thanh Lịch — VN-MateAI. See LICENSE and NOTICE.
"""
CSP chặt (thẩm định thương mại §8): `script-src 'self'` không 'unsafe-inline' trên portal / HUD / ROI; các trang báo cho
web/inline-handlers.js bằng meta; trang Next.js xuất tĩnh giữ CSP cũ; công tắc khẩn VNMATEAI_CSP_LEGACY; danh sách cho
phép của inline-handlers.js khớp đúng các tên mà mã nguồn dùng trong on*="…".
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]


def _script_src(csp: str) -> str:
    return next(p.strip() for p in csp.split(";") if p.strip().startswith("script-src"))


@pytest.fixture
def client():
    from mateai.interfaces.http import server
    return TestClient(server.app)


def test_portal_pages_strict_and_marked(client):
    for path in ("/", "/hud", "/roi"):
        r = client.get(path)
        assert r.status_code == 200, path
        assert _script_src(r.headers["content-security-policy"]) == "script-src 'self'", path
        assert '<meta name="vnmate-csp" content="strict" />' in r.text, path
        head = r.text[:r.text.index("</head>")]
        assert head.index('name="vnmate-csp"') < head.index("<script"), f"{path}: meta phải đứng trước mọi script"


def test_api_responses_strict(client):
    assert _script_src(client.get("/livez").headers["content-security-policy"]) == "script-src 'self'"


def test_nextjs_export_pages_keep_legacy_csp(client):
    from mateai.interfaces.http.csp import LEGACY_CSP
    r = client.get("/topology")
    if r.status_code == 404:
        pytest.skip("chưa có bản build admin/out")
    assert r.headers["content-security-policy"] == LEGACY_CSP


def test_emergency_legacy_switch(monkeypatch):
    from mateai.interfaces.http import csp
    monkeypatch.setenv("VNMATEAI_CSP_LEGACY", "1")
    assert "'unsafe-inline'" in _script_src(csp.default_csp())
    assert csp.mark_strict("<html><head><title>x</title></head>") == "<html><head><title>x</title></head>"
    monkeypatch.delenv("VNMATEAI_CSP_LEGACY")
    assert csp.default_csp() == csp.STRICT_CSP
    assert csp.mark_strict("<head><title>x</title>").startswith('<head>\n  <meta name="vnmate-csp"')


def test_allowlist_matches_source():
    r = subprocess.run([sys.executable, str(ROOT / "scripts" / "inline_handlers.py")], capture_output=True, text=True,
                       encoding="utf-8", cwd=ROOT)
    assert r.returncode == 0, r.stdout + r.stderr + "\n→ chạy: python scripts/inline_handlers.py --write"


def test_allowlist_has_no_network_or_storage_roots():
    sys.path.insert(0, str(ROOT / "scripts"))
    import inline_handlers
    names = set(inline_handlers.required_roots())
    risky = {"fetch", "apiFetch", "XMLHttpRequest", "WebSocket", "localStorage", "sessionStorage", "navigator", "window",
             "eval", "Function", "setTimeout", "setInterval", "open", "location", "history", "Image"}
    assert not names & risky, sorted(names & risky)


def test_gzip_for_pages_and_static_only(client):
    r = client.get("/static/app.js", headers={"Accept-Encoding": "gzip"})
    assert r.headers.get("content-encoding") == "gzip" and "accept-encoding" in r.headers.get("vary", "").lower()
    assert client.get("/", headers={"Accept-Encoding": "gzip"}).headers.get("content-encoding") == "gzip"
    assert "content-encoding" not in client.get("/livez", headers={"Accept-Encoding": "gzip"}).headers
    ranged = client.get("/static/app.js", headers={"Accept-Encoding": "gzip", "Range": "bytes=0-99"})
    assert "content-encoding" not in ranged.headers
