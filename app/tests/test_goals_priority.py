# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 Dương Thanh Lịch — VN-MateAI. See LICENSE and NOTICE.
"""
tests/test_goals_priority.py
============================
Prompt cuối §42 (goal hierarchy) + §43 (priority engine) — trên sổ tác vụ có sẵn.

  - Mục tiêu 3 cấp: company -> department -> operational; cấp con phải đúng thứ tự, không vòng.
  - Tác vụ gắn goal_id; tiến độ mục tiêu tính từ tác vụ THẬT (COMPLETED / tổng), gộp cả cấp con.
  - Ưu tiên tất định từ tác động, độ khẩn, hạn SLA, ảnh hưởng bảo mật, số người bị ảnh hưởng;
    không hỏi LLM; giải thích được (trả lý do).
"""
from __future__ import annotations

from datetime import datetime, timedelta

import pytest


def test_goal_hierarchy_levels_and_progress():
    from mateai.application.tasks import ledger
    co = ledger.create_goal("Giảm 30% thời gian xử lý sự cố", level="company", owner="ceo")
    dep = ledger.create_goal("IT: phản hồi sự cố < 15 phút", level="department", parent_id=co,
                             department="Phòng Công Nghệ Thông Tin")
    op = ledger.create_goal("Tự động hoá kiểm tra ổ đĩa", level="operational", parent_id=dep)
    with pytest.raises(ValueError):
        ledger.create_goal("sai cấp", level="company", parent_id=dep)       # company không có cha
    with pytest.raises(ValueError):
        ledger.create_goal("nhảy cấp", level="operational", parent_id=co)   # operational phải dưới department

    t1 = ledger.open_task("kiểm tra ổ C", goal_id=op)
    t2 = ledger.open_task("kiểm tra ổ D", goal_id=op)
    ledger.transition(t1, ledger.EXECUTING)
    ledger.transition(t1, ledger.VERIFYING)
    ledger.transition(t1, ledger.COMPLETED, verification_status="passed")
    tree = ledger.goal_tree(co)
    assert tree["progress"] == {"tasks": 2, "completed": 1, "percent": 50.0}
    assert tree["children"][0]["children"][0]["goal_id"] == op
    assert ledger.get_task(t2)["goal_id"] == op


def test_priority_engine_is_deterministic_and_explained():
    from mateai.application.tasks import ledger
    low = ledger.score_priority(impact="low", urgency="low")
    assert low["priority"] == "LOW" and low["reasons"]
    sla = ledger.score_priority(impact="medium", urgency="medium",
                                deadline=(datetime.now() + timedelta(minutes=20)).isoformat())
    assert sla["priority"] in ("HIGH", "CRITICAL") and any("SLA" in r for r in sla["reasons"])
    sec = ledger.score_priority(impact="low", urgency="low", security_impact=True)
    assert sec["priority"] in ("HIGH", "CRITICAL")
    many = ledger.score_priority(impact="high", urgency="high", affected_users=500)
    assert many["priority"] == "CRITICAL"
    assert ledger.score_priority(impact="high", urgency="high", affected_users=500) == many   # tất định


def test_open_task_uses_priority_engine():
    from mateai.application.tasks import ledger
    tid = ledger.open_task("lộ dữ liệu khách hàng", kind="incident", security_impact=True, affected_users=120)
    assert ledger.get_task(tid)["priority"] == "CRITICAL"


def test_goal_api_roles():
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    import mateai.interfaces.http.routers.tasks as tasks
    from mateai.interfaces.http.auth_dependencies import get_current_user

    def client(role):
        app = FastAPI()
        app.include_router(tasks.router)
        app.dependency_overrides[get_current_user] = lambda: {"username": f"{role}_u", "role": role}
        return TestClient(app)

    assert client("manager").post("/api/v1/ops/goals", json={"title": "x", "level": "company"}).status_code == 403
    r = client("admin").post("/api/v1/ops/goals", json={"title": "Tăng trưởng 20%", "level": "company"})
    assert r.status_code == 200, r.text
    gid = r.json()["goal"]["goal_id"]
    assert client("manager").post("/api/v1/ops/goals", json={"title": "Bán hàng", "level": "department",
                                                              "parent_id": gid}).status_code == 200
    assert client("manager").post("/api/v1/ops/goals", json={"title": "x", "level": "operational",
                                                              "parent_id": gid}).status_code == 400
    assert client("viewer").get("/api/v1/ops/goals").status_code == 403
    tree = client("manager").get("/api/v1/ops/goals").json()["goals"]
    assert any(g["goal_id"] == gid and g["children"] for g in tree)
