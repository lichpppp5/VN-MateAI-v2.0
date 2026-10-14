# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 Dương Thanh Lịch — VN-MateAI. See LICENSE and NOTICE.
"""`GET /metrics`: số đo của chính VN-MateAI ở định dạng Prometheus — token riêng, không lộ bí mật, không xuất số giả, một khối lỗi không làm mất cả lần thu thập."""
from __future__ import annotations

import re

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from mateai.application.operations import metrics_export as mx
from mateai.config.loader import settings
from mateai.interfaces.http.routers.monitoring import router

SAMPLE = re.compile(r"^([a-zA-Z_:][a-zA-Z0-9_:]*)(\{(.*)\})? (\S+)$")
LABEL = re.compile(r'([a-zA-Z_][a-zA-Z0-9_]*)="((?:[^"\\]|\\.)*)"')


def parse(text):
    samples, declared = {}, set()
    for ln in text.strip().splitlines():
        if ln.startswith("# HELP ") or ln.startswith("# TYPE "):
            declared.add(ln.split()[2])
            continue
        m = SAMPLE.match(ln)
        assert m, f"dòng không đúng định dạng Prometheus: {ln!r}"
        labels = m.group(3) or ""
        rebuilt = ",".join(f'{k}="{v}"' for k, v in LABEL.findall(labels))
        assert rebuilt == labels, f"nhãn sai định dạng: {ln!r}"
        float(m.group(4))                                              # giá trị phải là số
        samples.setdefault(m.group(1), []).append(ln)
    assert set(samples) <= declared, "metric thiếu HELP/TYPE"
    return samples


def test_output_is_valid_prometheus_text_with_the_core_series():
    text = mx.render()
    s = parse(text)
    for name in ("vnmateai_up", "vnmateai_process_uptime_seconds", "vnmateai_kill_switch", "vnmateai_approvals_pending",
                 "vnmateai_incidents_open", "vnmateai_scrape_duration_seconds"):
        assert name in s, name
    assert 'vnmateai_scrape_error{section="ledger"} 0' in text and 'vnmateai_scrape_error{section="core"} 0' in text


def test_label_escaping_and_unknown_numbers_are_not_exported():
    reg = mx._Registry()
    reg.add("x_metric", "h", "gauge", 1, label='a"b\c\nd')
    reg.add("x_missing", "h", "gauge", None)
    reg.add("x_nan", "h", "gauge", float("nan"))
    reg.add("x_text", "h", "gauge", "không phải số")
    out = reg.text()
    expected = 'label="a' + '\\"' + 'b' + '\\\\' + 'c' + '\\n' + 'd"'            # a \" b \\ c \n d
    assert expected in out and "\nd" not in out                                 # xuống dòng thật không lọt vào nhãn
    assert "x_missing" not in out and "x_nan" not in out and "x_text" not in out


def test_one_failing_section_does_not_lose_the_scrape(monkeypatch):
    def boom(_r):
        raise RuntimeError("hỏng")
    monkeypatch.setattr(mx, "SECTIONS", [("core", mx._section_core), ("ledger", boom)])
    text = mx.render()
    assert 'vnmateai_scrape_error{section="ledger"} 1' in text and "vnmateai_up 1" in text
    parse(text)


def test_cost_series_is_absent_when_no_price_is_known(monkeypatch):
    from mateai.infrastructure.database import db_manager as dbm
    monkeypatch.setattr(dbm.db_manager, "op_stats", lambda since: {"tasks_by_status": {}, "tasks_by_kind": {}, "steps_by_decision": {},
                                                                     "steps_by_verification": {}, "tokens": 120, "llm_calls": 3,
                                                                     "llm_cost": None, "unpriced_tokens": 120})
    text = mx.render()
    assert "vnmateai_llm_tokens_24h 120" in text and "vnmateai_llm_cost_24h" not in text and "vnmateai_llm_unpriced_tokens_24h 120" in text


def client():
    app = FastAPI()
    app.include_router(router)
    return TestClient(app)


def test_endpoint_is_off_without_a_token_and_checks_it_otherwise(monkeypatch):
    c = client()
    monkeypatch.setattr(settings.monitoring, "metrics_token", "")
    assert c.get("/metrics").status_code == 404                                      # chưa đặt token: coi như không tồn tại
    assert c.get("/metrics", headers={"Authorization": "Bearer bat-ky"}).status_code == 404
    monkeypatch.setattr(settings.monitoring, "metrics_token", "TOKEN-SCRAPE-1")
    assert c.get("/metrics").status_code == 401
    assert c.get("/metrics", headers={"Authorization": "Bearer sai"}).status_code == 401
    assert c.get("/metrics", headers={"Authorization": "TOKEN-SCRAPE-1"}).status_code == 401          # thiếu tiền tố Bearer
    ok = c.get("/metrics", headers={"Authorization": "Bearer TOKEN-SCRAPE-1"})
    assert ok.status_code == 200 and ok.headers["content-type"].startswith("text/plain; version=0.0.4")
    parse(ok.text)
    assert "TOKEN-SCRAPE-1" not in ok.text


def test_metrics_path_is_outside_jwt_guard_and_token_is_a_config_secret():
    from mateai.config import secret_box
    assert secret_box.is_secret("metrics_token")
    server = open(__file__.replace("tests\test_metrics_export.py", "src\mateai\interfaces\http\server.py").replace("tests/test_metrics_export.py", "src/mateai/interfaces/http/server.py"),
                  encoding="utf-8").read()
    assert 'guarded_prefixes = ("/api/v1/", "/api/erp/")' in server       # /metrics tự xác thực bằng token riêng, không dùng JWT người dùng
