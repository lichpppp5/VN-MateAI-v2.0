# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 Dương Thanh Lịch — VN-MateAI. See LICENSE and NOTICE.
"""
tests/test_dual_approval_and_audit_export.py
============================================
Thẩm định thương mại 14/10/2026:
  #16 duyệt hai người cho rủi ro cao (tắt mặc định — một admin thì không duyệt được gì), người yêu cầu không tự duyệt;
  #17 báo cáo kiểm toán xuất CSV / HTML in được, kèm kết quả kiểm tra chuỗi băm, che dữ liệu cá nhân, chỉ admin.
"""
from __future__ import annotations

import asyncio
import uuid

import pytest
from fastapi.testclient import TestClient

from mateai.application.security import zero_trust as zt


@pytest.fixture
def dual(monkeypatch):
    monkeypatch.setattr(zt, "dual_approval_min_risk", lambda: 4)


def _request(risk=4, by="an"):
    ran = []
    item = zt.hitl_manager.request_approval(f"thu_{uuid.uuid4().hex[:6]}", {"x": 1}, requested_by=by,
                                            description="thử duyệt hai người", action_callback=lambda: ran.append(1) or "ok",
                                            risk_level=risk)
    return (item.get("approval_id") or item.get("id")), ran


def test_off_by_default_single_approval_runs():
    assert zt.dual_approval_min_risk() == 0
    aid, ran = _request()
    res = zt.hitl_manager.approve(aid, approved_by="boss")
    assert res["status"] != "error" and ran == [1]


def test_two_different_people_needed(dual):
    aid, ran = _request()
    first = zt.hitl_manager.approve(aid, approved_by="boss")
    assert first["status"] == "error" and "1/2" in first["message"] and ran == []
    again = zt.hitl_manager.approve(aid, approved_by="boss")
    assert "người duyệt khác" in again["message"] and ran == []
    second = zt.hitl_manager.approve(aid, approved_by="ketoan_truong")
    assert second["status"] != "error" and ran == [1]


def test_requester_cannot_approve_own_request(dual):
    aid, ran = _request(by="an")
    res = zt.hitl_manager.approve(aid, approved_by="An")
    assert "KHÁC người yêu cầu" in res["message"] and ran == []


def test_low_risk_stays_single_approval(dual):
    aid, ran = _request(risk=3)
    assert zt.hitl_manager.approve(aid, approved_by="boss")["status"] != "error" and ran == [1]


# ── #17 xuất báo cáo kiểm toán ────────────────────────────────────────────────

def test_csv_and_html_report_content():
    from mateai.application.security import audit_export
    from mateai.infrastructure.database.erp_database import erp_db
    erp_db.log_audit_action(action_type="TOOL_EXEC_RUN_POWERSHELL_COMMAND", employee_id="admin", status="success",
                            payload='{"note": "gọi 0912345678"}')
    rows = audit_export.fetch()
    assert rows and all("0912345678" not in str(r.get("payload")) for r in rows)
    csv_bytes = audit_export.to_csv(rows)
    assert csv_bytes.startswith("﻿".encode("utf-8")) and b"row_hash" in csv_bytes.splitlines()[0]
    page = audit_export.to_html(rows, chain=erp_db.verify_audit_chain(), exported_by="admin", date_from=None, date_to=None)
    assert "Báo cáo kiểm toán" in page and ("TOÀN VẸN" in page or "SAI LỆCH" in page)
    assert "<script" not in page.lower()


def test_export_endpoint_admin_only_and_audited():
    from mateai.infrastructure.database.db_manager import db_manager
    from mateai.interfaces.http.server import app
    c = TestClient(app)
    pw = "Kiemtoan2026x"
    names = {}
    for role in ("admin", "viewer"):
        n = f"ae-{role}-{uuid.uuid4().hex[:6]}"
        db_manager.create_user({"username": n, "password": pw, "role": role, "full_name": "x"})
        names[role] = c.post("/api/v1/login", json={"username": n, "password": pw}).json()["access_token"]
    h = lambda r: {"Authorization": f"Bearer {names[r]}"}  # noqa: E731
    assert c.get("/api/v1/security/audit-logs/export?format=csv", headers=h("viewer")).status_code == 403
    r = c.get("/api/v1/security/audit-logs/export?format=csv&date_from=2020-01-01", headers=h("admin"))
    assert r.status_code == 200 and "attachment" in r.headers["content-disposition"]
    r2 = c.get("/api/v1/security/audit-logs/export?format=html", headers=h("admin"))
    assert r2.status_code == 200 and "text/html" in r2.headers["content-type"]
    assert c.get("/api/v1/security/audit-logs/export?format=pdf", headers=h("admin")).status_code == 422
    from mateai.infrastructure.database.erp_database import erp_db
    with erp_db.get_connection() as conn:
        assert conn.execute("SELECT COUNT(*) FROM audit_logs WHERE action_type = 'AUDIT_EXPORT';").fetchone()[0] >= 2


# ── #18 tách nhiệm vụ: người yêu cầu / người cấu hình không tự duyệt (security.separation_of_duties) ──

@pytest.fixture
def sod(monkeypatch):
    monkeypatch.setattr(zt, "separation_of_duties", lambda: True)


def test_sod_off_by_default_requester_may_approve():
    assert zt.separation_of_duties() is False
    aid, ran = _request(risk=2, by="an")
    assert zt.hitl_manager.approve(aid, approved_by="an")["status"] != "error" and ran == [1]


def test_sod_requester_cannot_approve_even_low_risk(sod):
    aid, ran = _request(risk=2, by="an")
    res = zt.hitl_manager.approve(aid, approved_by="AN")
    assert res["status"] == "error" and "tách nhiệm vụ" in res["message"] and ran == []
    assert zt.hitl_manager.approve(aid, approved_by="boss")["status"] != "error" and ran == [1]


def test_sod_configurer_cannot_approve(sod):
    ran = []
    item = zt.hitl_manager.request_approval(f"playbook:pb_{uuid.uuid4().hex[:6]}", {"run_id": "r"}, requested_by="trigger",
                                            action_callback=lambda: ran.append(1) or "ok", risk_level=3,
                                            context={"configured_by": ["soan_kb", "sua_kb"]})
    aid = item.get("approval_id") or item.get("id")
    res = zt.hitl_manager.approve(aid, approved_by="Sua_KB")
    assert res["status"] == "error" and "người cấu hình" in res["message"] and ran == []
    assert zt.hitl_manager.approve(aid, approved_by="boss")["status"] != "error" and ran == [1]
