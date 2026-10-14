# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 Dương Thanh Lịch — VN-MateAI. See LICENSE and NOTICE.
"""
tests/test_structured_logging.py
================================
Prompt cuối §95: log có cấu trúc (timestamp, service, level, event, request_id, session_id,
trace_id) và KHÔNG ghi token / mật khẩu / API key.

  - LOG_FORMAT=json: mỗi dòng là một JSON;
  - request_id của request HTTP (X-Request-ID) có trong mọi dòng log ghi trong request đó;
  - token trong URL / chuỗi bị che ở cả hai định dạng.
"""
from __future__ import annotations

import io
import json
import logging

from fastapi.testclient import TestClient


def _capture(fmt):
    from mateai.config import log_setup
    buf = io.StringIO()
    handler = log_setup.make_handler(io_stream=buf, fmt=fmt)
    log = logging.getLogger("test.structured")
    log.handlers[:] = [handler]
    log.propagate = False
    log.setLevel(logging.INFO)
    return log, buf


def test_json_lines_carry_context_and_redact_secrets():
    from mateai.config.log_setup import REQUEST_ID
    log, buf = _capture("json")
    tok = REQUEST_ID.set("req-42")
    try:
        log.info("gọi /api/v1/x?token=eyJhbGciOi.SECRET.SIG&a=1 api_key=sk-THATKEY xong")
    finally:
        REQUEST_ID.reset(tok)
    rec = json.loads(buf.getvalue().strip())
    assert rec["service"] == "vn-mateai" and rec["level"] == "INFO" and rec["logger"] == "test.structured"
    assert rec["request_id"] == "req-42" and "ts" in rec
    assert "SECRET" not in rec["event"] and "sk-THATKEY" not in rec["event"] and "[REDACTED]" in rec["event"]


def test_text_format_shows_request_id_only_inside_requests():
    from mateai.config.log_setup import REQUEST_ID
    log, buf = _capture("text")
    log.info("ngoài request")
    tok = REQUEST_ID.set("abc123")
    log.info("trong request password=hunter2")
    REQUEST_ID.reset(tok)
    first, second = buf.getvalue().strip().splitlines()
    assert "[abc123]" not in first and "[abc123]" in second and "hunter2" not in second


def test_http_request_id_reaches_log_records():
    from fastapi import APIRouter

    import mateai.interfaces.http.server as server
    from mateai.config import log_setup
    assert server.REQUEST_ID is log_setup.REQUEST_ID            # một nguồn ngữ cảnh request
    seen = []

    class Grab(logging.Handler):
        def emit(self, record):
            seen.append(record.request_id)

    h = Grab()
    h.addFilter(log_setup.ContextFilter())
    lg = logging.getLogger("test.reqlog")
    lg.addHandler(h)
    lg.setLevel(logging.INFO)
    r = APIRouter()

    @r.get("/livez-test-log", include_in_schema=False)
    async def _logs():
        lg.info("trong request")
        return {"ok": True}

    server.app.include_router(r)
    try:
        TestClient(server.app).get("/livez-test-log", headers={"X-Request-ID": "rid-777"})
        lg.info("ngoài request")
    finally:
        lg.removeHandler(h)
        server.app.router.routes[:] = [x for x in server.app.router.routes
                                       if getattr(x, "path", "") != "/livez-test-log"]
    assert seen == ["rid-777", "-"]


def test_uvicorn_access_record_keeps_its_args_shape():
    """uvicorn.access dựng dòng từ record.args (5 phần) — che token không được phá cấu trúc đó."""
    from mateai.config.log_setup import ContextFilter
    rec = logging.LogRecord("uvicorn.access", logging.INFO, __file__, 1, '%s - "%s %s HTTP/%s" %d',
                            ("1.2.3.4:5", "GET", "/api/v1/hud/audio?token=eyJ.abc.def", "1.1", 200), None)
    ContextFilter().filter(rec)
    assert len(rec.args) == 5 and "eyJ" not in rec.getMessage() and "[REDACTED]" in rec.getMessage()
