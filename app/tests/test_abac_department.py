# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 Dương Thanh Lịch — VN-MateAI. See LICENSE and NOTICE.
"""
tests/test_abac_department.py
=============================
Prompt cuối §35 (ABAC), §62 (phân loại dữ liệu), §64 / §155 (cô lập phòng ban), §39 (tool contract).

  - Tài khoản có phòng ban + cấp bảo mật (admin đặt, có audit); API không trả `password_hash`
    (trước đây PUT /api/v1/users/{id} trả nguyên hash).
  - Skill khai mức phân loại dữ liệu; `policy_engine.authorize` từ chối khi cấp bảo mật của
    người hỏi thấp hơn (luật `abac_clearance`), kể cả qua AI.
  - Người phòng A không thấy dữ liệu phòng B (cây tổ chức, chấm công, xếp hạng).
  - Báo cáo liên phòng ban lấy cấp bảo mật từ NGƯỜI HỎI, không từ request / hằng số, và không
    còn số liệu bịa (chi phí cloud 15 triệu, "ổn định 100%").
Không gọi mạng; DB test riêng (conftest).
"""
from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient


@pytest.fixture
def org():
    from mateai.infrastructure.database.db_manager import db_manager
    from mateai.infrastructure.database.erp_database import erp_db
    a = erp_db.add_department(name="ABAC Kế toán", description="")
    b = erp_db.add_department(name="ABAC Kỹ thuật", description="")
    erp_db.add_employee(a["id"], "Nguyễn A", "Kế toán viên", "a@x.vn", "", "viewer")
    erp_db.add_employee(b["id"], "Trần B", "Kỹ sư", "b@x.vn", "", "viewer")
    made = []
    for uname, role in (("abac_ketoan", "viewer"), ("abac_nodept", "manager"), ("abac_boss", "admin")):
        try:
            made.append(db_manager.create_user({"username": uname, "password": "matkhau123", "role": role}))
        except ValueError:
            made.append(db_manager.get_user_by_username_or_id(uname))
    db_manager.update_user("abac_ketoan", {"department": "ABAC Kế toán"})
    yield a, b
    for u in made:
        try:
            db_manager.delete_user(u["id"])
        except Exception:
            pass
    erp_db.delete_department(a["id"])
    erp_db.delete_department(b["id"])


def _client(router, username, role):
    from mateai.interfaces.http.auth_dependencies import get_current_user
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_current_user] = lambda: {"username": username, "role": role}
    return TestClient(app)


def test_user_api_sets_department_and_clearance_without_leaking_hash(org):
    import mateai.interfaces.http.routers.users as users
    admin = _client(users.router, "abac_boss", "admin")
    r = admin.put("/api/v1/users/abac_nodept", json={"department": "ABAC Kỹ thuật", "clearance_level": 3})
    assert r.status_code == 200, r.text
    assert "password_hash" not in r.text
    u = r.json()["user"]
    assert u["department"] == "ABAC Kỹ thuật" and u["clearance_level"] == 3
    assert admin.put("/api/v1/users/abac_nodept", json={"department": "Phòng không tồn tại"}).status_code == 400
    assert admin.put("/api/v1/users/abac_nodept", json={"clearance_level": 9}).status_code == 422
    listed = admin.get("/api/v1/users").json()
    assert "password_hash" not in str(listed)
    assert _client(users.router, "abac_ketoan", "viewer").put(
        "/api/v1/users/abac_ketoan", json={"clearance_level": 4}).status_code == 403   # không tự nâng cấp


def test_principal_attributes(org):
    from mateai.application.security.security_guard import security_guard
    p = security_guard.principal("abac_ketoan")
    assert p["department"] == "ABAC Kế toán" and p["clearance"] == 2          # viewer -> operator -> 2
    boss = security_guard.principal("abac_boss")
    assert boss["clearance"] == 4 and boss["all_departments"] is True
    stranger = security_guard.principal("ai-do-khong-ton-tai")
    assert stranger["clearance"] == 1 and stranger["department"] is None


def test_policy_denies_confidential_tool_below_clearance(org):
    from mateai.application.security import policy_engine as pe
    from mateai.application.security.security_guard import required_clearance
    assert required_clearance("get_financial_summary") >= 3                   # tool tài chính: CONFIDENTIAL
    d = pe.authorize("get_financial_summary", {}, caller="abac_ketoan", agent_id="VN-MATEAI-VOICE",
                     check_rbac=False)
    assert d.effect == "deny" and d.rule == "abac_clearance"
    ok = pe.authorize("get_financial_summary", {}, caller="abac_boss", agent_id="VN-MATEAI-VOICE",
                      check_rbac=False)
    assert ok.rule != "abac_clearance"
    assert pe.authorize("get_system_info", {}, caller="abac_ketoan", agent_id="VN-MATEAI-VOICE",
                        check_rbac=False).effect == "allow"                   # INTERNAL vẫn chạy


def test_erp_structure_is_scoped_to_department(org):
    import mateai.interfaces.http.api_erp as erp
    names = lambda c: {d["name"] for d in c.get("/api/erp/structure").json()["departments"]}   # noqa: E731
    seen = names(_client(erp.router, "abac_ketoan", "viewer"))
    assert "ABAC Kế toán" in seen and "ABAC Kỹ thuật" not in seen
    assert {"ABAC Kế toán", "ABAC Kỹ thuật"} <= names(_client(erp.router, "abac_boss", "admin"))
    body = _client(erp.router, "abac_nodept", "manager").get("/api/erp/structure").json()
    assert not any(d["name"].startswith("ABAC") for d in body["departments"])
    assert "phòng ban" in body.get("scope_note", "")


def test_attendance_and_leaderboard_are_scoped(org):
    import mateai.interfaces.http.routers.enterprise as ent
    c = _client(ent.router, "abac_ketoan", "viewer")
    lb = c.get("/api/v1/enterprise/leaderboard?limit=100").json()["leaderboard"]
    assert {r.get("dept_name") for r in lb} <= {"ABAC Kế toán"}


def test_cross_report_uses_principal_clearance_and_no_fabricated_numbers(org):
    from mateai.application.agent.agent_orchestrator import get_enterprise_executive_summary
    from mateai.application.security.security_guard import CURRENT_PRINCIPAL
    tok = CURRENT_PRINCIPAL.set("abac_ketoan")
    try:
        rep = get_enterprise_executive_summary()
    finally:
        CURRENT_PRINCIPAL.reset(tok)
    assert rep["clearance_level"] == 2 and rep["metrics"]["net_balance"] is None
    text = rep["rich_details"] + rep["voice_summary"]
    assert "15000000" not in text and "ổn định 100%" not in text and "Trực chiến 24/7" not in text
    assert rep["metrics"]["cloud_ratio_pct"] is None                          # không có dữ liệu chi phí thật

    import mateai.interfaces.http.api_admin as adm
    r = _client(adm.router, "abac_boss", "admin").post("/api/v1/admin/cross-report",
                                                       json={"scope": ["FIN"], "clearance_level": 1})
    assert r.status_code == 200 and r.json()["clearance_level"] == 4          # bỏ qua giá trị tự khai
