# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 Dương Thanh Lịch — VN-MateAI. See LICENSE and NOTICE.
"""Thương hiệu hiển thị (white-label): mặc định giữ nguyên; kiểm dữ liệu; GET công khai, PUT chỉ admin; dòng bản quyền
không đổi theo thương hiệu."""
from __future__ import annotations

import base64
import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]
PNG = "data:image/png;base64," + base64.b64encode(b"\x89PNG\r\n\x1a\n" + b"0" * 64).decode()


@pytest.fixture
def tmp_config(tmp_path, monkeypatch):
    import mateai.config.loader as loader
    cfg = tmp_path / "config.json"
    cfg.write_text(json.dumps({"llm": {"base_url": "http://localhost:20128/v1", "model_name": "m"}}), encoding="utf-8")
    monkeypatch.setattr(loader, "CONFIG_PATH", cfg)
    yield cfg
    monkeypatch.undo()
    loader.reload_settings()


def test_defaults_not_customized(tmp_config):
    from mateai.application import branding
    b = branding.get()
    assert b["product_name"] == "VN-MateAI" and b["customized"] is False and b["logo_data_url"] == ""


def test_update_and_reset(tmp_config):
    from mateai.application import branding
    b = branding.update({"product_name": "ACME Ops", "tagline": "Vận hành CNTT", "primary_color": "#0F766E", "logo_data_url": PNG})
    assert b["customized"] and b["product_name"] == "ACME Ops" and b["primary_color"] == "#0f766e" and b["logo_data_url"] == PNG
    assert json.loads(tmp_config.read_text(encoding="utf-8"))["branding"]["product_name"] == "ACME Ops"
    b = branding.update({"product_name": "", "tagline": "", "primary_color": "", "logo_data_url": ""})
    assert b["customized"] is False and b["product_name"] == "VN-MateAI"


@pytest.mark.parametrize("changes", [
    {"product_name": "<script>x</script>"},
    {"product_name": "x" * 41},
    {"tagline": "a\x00b"},
    {"primary_color": "red"},
    {"primary_color": "#12345"},
    {"logo_data_url": "https://evil.example/logo.png"},
    {"logo_data_url": "data:text/html;base64,PHNjcmlwdD4="},
    {"logo_data_url": "data:image/png;base64," + "A" * 200_001},
    {"unknown_field": 1},
])
def test_rejects_bad_input(tmp_config, changes):
    from mateai.application import branding
    with pytest.raises(ValueError):
        branding.update(changes)


def test_corrupt_config_falls_back_to_defaults(tmp_config):
    from mateai.application import branding
    tmp_config.write_text(json.dumps({"branding": {"product_name": "<b>", "primary_color": "nope",
                                                   "logo_data_url": "javascript:alert(1)"}}), encoding="utf-8")
    b = branding.get()
    assert b["product_name"] == "VN-MateAI" and b["primary_color"] == "" and b["logo_data_url"] == ""


def test_http_get_public_put_admin_only(tmp_config):
    from mateai.interfaces.http import server
    c = TestClient(server.app)
    assert c.get("/api/v1/branding").status_code == 200
    assert c.put("/api/v1/branding", json={"product_name": "X"}).status_code == 401


def test_attribution_line_not_brandable():
    html = (ROOT / "web" / "index.html").read_text(encoding="utf-8")
    i = html.index('id="login-attribution"')
    block = html[i:html.index("</p>", i)]
    assert "VN-MateAI © 2026 Dương Thanh Lịch" in block and "data-brand" not in block
    js = (ROOT / "web" / "branding.js").read_text(encoding="utf-8")
    assert "login-attribution" not in js
