# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 Dương Thanh Lịch — VN-MateAI. See LICENSE and NOTICE.
"""
tests/test_api_error_model.py
=============================
Prompt cuối §143 (mô hình lỗi API chuẩn) + §95 (request_id để lần log):

  - mọi phản hồi HTTP có `X-Request-ID` (nhận từ client nếu hợp lệ, không thì tự sinh);
  - lỗi không bắt được trả `{"error": {"code", "message", "request_id"}}`, mã 500,
    KHÔNG lộ stack trace / thông điệp ngoại lệ;
  - lỗi nghiệp vụ (HTTPException) giữ nguyên dạng `detail` mà giao diện đang đọc.
"""
from __future__ import annotations

import pytest
from fastapi import APIRouter, HTTPException
from fastapi.testclient import TestClient

import mateai.interfaces.http.server as server

_router = APIRouter()


@_router.get("/livez-test-boom", include_in_schema=False)
async def _boom():
    raise RuntimeError("mật khẩu DB là hunter2")         # không được lộ ra ngoài


@_router.get("/livez-test-404", include_in_schema=False)
async def _not_found():
    raise HTTPException(status_code=404, detail="Không có")


@pytest.fixture(scope="module")
def client():
    server.app.include_router(_router)
    yield TestClient(server.app, raise_server_exceptions=False)
    server.app.router.routes[:] = [r for r in server.app.router.routes
                                   if getattr(r, "path", "") not in ("/livez-test-boom", "/livez-test-404")]


def test_every_response_has_request_id(client):
    r = client.get("/livez")
    assert len(r.headers["x-request-id"]) >= 8
    assert client.get("/livez", headers={"X-Request-ID": "abc-123_XY"}).headers["x-request-id"] == "abc-123_XY"
    bad = client.get("/livez", headers={"X-Request-ID": "x\" onload=1 " + "a" * 200}).headers["x-request-id"]
    assert bad != "abc" and '"' not in bad and len(bad) <= 64                 # không phản chiếu giá trị lạ


def test_unhandled_error_uses_canonical_model_without_leaking(client):
    r = client.get("/livez-test-boom", headers={"X-Request-ID": "req-777"})
    assert r.status_code == 500
    body = r.json()
    assert body == {"error": {"code": "internal_error", "message": body["error"]["message"], "request_id": "req-777"}}
    assert "hunter2" not in r.text and "Traceback" not in r.text and "RuntimeError" not in r.text
    assert r.headers["x-request-id"] == "req-777"


def test_business_errors_keep_detail_shape(client):
    r = client.get("/livez-test-404")
    assert r.status_code == 404 and r.json() == {"detail": "Không có"} and r.headers.get("x-request-id")


def test_auth_rejections_also_carry_request_id(client):
    r = client.get("/api/v1/config", headers={"X-Request-ID": "no-auth-1"})
    assert r.status_code == 401 and r.headers["x-request-id"] == "no-auth-1"
