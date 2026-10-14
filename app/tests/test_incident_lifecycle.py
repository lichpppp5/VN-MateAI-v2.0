# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 Dương Thanh Lịch — VN-MateAI. See LICENSE and NOTICE.
"""
tests/test_incident_lifecycle.py
================================
Prompt cuối §88 (incident management) — trên sổ tác vụ có sẵn (`ledger`, kind = incident):

  - pha DETECTED → TRIAGED → INVESTIGATING → MITIGATING → VERIFYING → RESOLVED (ESCALATED lúc nào
    cũng được); không nhảy cóc tới RESOLVED khi chưa VERIFYING;
  - người phụ trách, tài sản bị ảnh hưởng, dòng thời gian (mỗi bước = một bằng chứng có người làm);
  - Sentinel đo lại thấy dịch vụ đã khôi phục -> sự cố đóng bằng bằng chứng THẬT (trước đây
    sự cố đã khôi phục vẫn mở mãi trong sổ);
  - API: xem (manager), đổi pha (manager / admin, có audit).
"""
from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient


def test_phases_timeline_and_no_jump_to_resolved():
    from mateai.application.tasks import ledger
    tid = ledger.open_incident("p88_net", "Mất mạng VPN", "ping 10.0.0.1 thất bại 3/3", severity="critical")
    t = ledger.get_task(tid)
    assert t["incident_phase"] == "DETECTED" and t["affected_assets"] == "p88_net"
    with pytest.raises(ledger.InvalidTransition):
        ledger.incident_phase(tid, "RESOLVED", actor="mona")
    ledger.incident_phase(tid, "TRIAGED", actor="mona", owner="carol", note="ảnh hưởng chi nhánh HN")
    ledger.incident_phase(tid, "INVESTIGATING", actor="carol", note="router chi nhánh mất nguồn")
    ledger.incident_phase(tid, "MITIGATING", actor="carol", note="chuyển sang đường 4G")
    ledger.incident_phase(tid, "VERIFYING", actor="carol", note="ping lại 3/3 ok")
    ledger.incident_phase(tid, "RESOLVED", actor="carol", note="xác nhận đã ổn")
    t = ledger.get_task(tid)
    assert t["incident_phase"] == "RESOLVED" and t["owner"] == "carol"
    assert t["status"] == "COMPLETED" and t["verification_status"] == "passed"
    timeline = [e["summary"] for e in t["evidence"]]
    assert any("TRIAGED" in s and "mona" in s for s in timeline) and any("RESOLVED" in s for s in timeline)
    with pytest.raises(ledger.InvalidTransition):
        ledger.incident_phase(tid, "INVESTIGATING", actor="x")          # đã đóng


def test_sentinel_recovery_closes_incident_with_real_evidence():
    from mateai.application.tasks import ledger
    tid = ledger.open_incident("p88_sql", "SQL Server không phản hồi", "cổng 1433 đóng")
    closed = ledger.resolve_incident_by_probe("p88_sql", "cổng 1433 mở lại, truy vấn thử OK")
    assert closed == tid
    t = ledger.get_task(tid)
    assert t["incident_phase"] == "RESOLVED" and t["status"] == "COMPLETED"
    assert any("1433 mở lại" in e["summary"] and e["kind"] == "FACT" for e in t["evidence"])
    assert ledger.resolve_incident_by_probe("p88_sql", "lần nữa") is None   # không có sự cố mở


def test_incident_api(monkeypatch):
    import mateai.interfaces.http.routers.tasks as tasks
    from mateai.application.security import safety_guard
    from mateai.application.tasks import ledger
    from mateai.interfaces.http.auth_dependencies import get_current_user
    audits = []
    monkeypatch.setattr(safety_guard.security_engine, "log_audit", lambda *a, **k: audits.append(a))
    tid = ledger.open_incident("p88_api", "Ổ đĩa đầy", "C: còn 1%")

    def client(role):
        app = FastAPI()
        app.include_router(tasks.router)
        app.dependency_overrides[get_current_user] = lambda: {"username": f"{role}_u", "role": role}
        return TestClient(app)

    rows = client("manager").get("/api/v1/ops/incidents").json()["incidents"]
    assert any(r["task_id"] == tid and r["incident_phase"] == "DETECTED" for r in rows)
    assert client("viewer").post(f"/api/v1/ops/incidents/{tid}/phase", json={"phase": "TRIAGED"}).status_code == 403
    r = client("manager").post(f"/api/v1/ops/incidents/{tid}/phase",
                               json={"phase": "TRIAGED", "owner": "it_u", "note": "dọn log"})
    assert r.status_code == 200 and r.json()["incident"]["owner"] == "it_u"
    assert client("manager").post(f"/api/v1/ops/incidents/{tid}/phase", json={"phase": "RESOLVED"}).status_code == 409
    assert any(a[1] == "incident_phase" for a in audits)
