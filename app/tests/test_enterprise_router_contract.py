# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 Dương Thanh Lịch — VN-MateAI. See LICENSE and NOTICE.
"""
tests/test_enterprise_router_contract.py
========================================
Hợp đồng HTTP của `routers/enterprise.py` — khoá hành vi TRƯỚC khi chuyển nghiệp vụ
sang tầng application (prompt Supervisor Phase 10, §198: business logic không nằm
trong HTTP route). Chạy lại sau khi chuyển: hành vi phải giữ nguyên.

Một ngoại lệ có chủ đích: `finances/record` trước đây lấy `created_by` từ body —
người gọi ghi giao dịch dưới tên người khác. Test khoá hành vi ĐÚNG (người ghi = tài
khoản đăng nhập).
Không gọi mạng; phụ thuộc nặng (RAG, ERP, connector) được thay bằng bản giả.
"""
from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import mateai.interfaces.http.routers.enterprise as ent


def _client(role="admin", username=None):
    from mateai.interfaces.http.auth_dependencies import get_current_user
    app = FastAPI()
    app.include_router(ent.router)
    app.dependency_overrides[get_current_user] = lambda: {"username": username or f"{role}_u", "role": role}
    return TestClient(app)


# ── Connector / data source ─────────────────────────────────────────────────

def test_connectors_health_lists_four_without_secrets():
    d = _client("viewer").get("/api/v1/enterprise/connectors/health").json()
    assert d["status"] == "success" and set(d["connectors"]) >= {"aws", "oci", "paperless", "einvoice"}
    for item in d["connectors"].values():
        assert "configured" in item
        for k in ("secret_access_key", "password", "api_token", "client_secret"):
            assert k not in item


def test_connectors_catalog_has_schema_and_alert_channels():
    d = _client("viewer").get("/api/v1/enterprise/connectors/catalog").json()
    assert d["status"] == "success"
    aws = d["connectors"]["aws"]
    assert aws["config_schema"]["type"] == "object" and "required" in aws["config_schema"]
    secret_props = [p for p in aws["config_schema"]["properties"].values() if p.get("format") == "secret"]
    assert secret_props and all("default" not in p or p["default"] in (None, "") for p in secret_props)
    assert any(v.get("kind") == "alert_channel" for v in d["connectors"].values())


def test_data_sources_list_shape():
    d = _client("viewer").get("/api/v1/enterprise/data-sources").json()
    assert d["status"] == "success" and set(d["builtin"]) == {"aws", "oci", "paperless", "einvoice"}
    assert d["total"] == len(d["custom"]) + 4


# ── RAG upload ──────────────────────────────────────────────────────────────

@pytest.fixture
def fake_rag(monkeypatch, tmp_path):
    import mateai.application.knowledge.rag_engine as rag
    calls = []
    monkeypatch.setattr(rag, "_DOCS_DIR", tmp_path / "docs")
    monkeypatch.setattr(rag.rag_engine, "ingest_file",
                        lambda file_path, category="": calls.append((str(file_path), category)) or {"status": "success", "chunks": 3})
    return calls, tmp_path


def test_rag_upload_validation_and_success(fake_rag, monkeypatch):
    calls, tmp = fake_rag
    from mateai.application.security import safety_guard
    audits = []
    monkeypatch.setattr(safety_guard.security_engine, "log_audit", lambda *a, **k: audits.append(a))
    c = _client("manager")
    up = lambda name, data: c.post("/api/v1/enterprise/rag/upload", files={"file": (name, data)}, data={"category": "Nội quy"})
    assert up("virus.py", b"x").status_code == 400
    assert up("rong.txt", b"").status_code == 400
    assert up("to.pdf", b"x" * (20 * 1024 * 1024 + 1)).status_code == 413
    r = up("../../Nội quy công ty.md", "# Nội quy\nĐi làm đúng giờ.".encode("utf-8"))
    assert r.status_code == 200 and r.json()["status"] == "success"
    saved = r.json()["saved_as"]
    assert saved.endswith(".md") and "/" not in saved and ".." not in saved
    assert (tmp / "docs" / saved).read_text(encoding="utf-8").startswith("# Nội quy")
    assert calls and calls[0][1] == "Nội quy"
    assert audits and audits[-1][1] == "KNOWLEDGE_UPLOAD" and audits[-1][4]["saved_as"] == saved   # trước: không bao giờ ghi
    assert _client("viewer").post("/api/v1/enterprise/rag/upload", files={"file": ("a.md", b"x")}).status_code == 403


# ── Tài chính / onboarding / đa tác nhân (qua cổng chính sách) ──────────────

@pytest.fixture
def fake_gate(monkeypatch):
    import mateai.application.security.zero_trust as zt
    seen = {}

    async def gate(action_name, params=None, executor=None, requested_by="", description="", **kw):
        seen.update(action=action_name, params=params, requested_by=requested_by)
        return {"status": "executed", "risk_level": 2, "result": executor()}

    monkeypatch.setattr(zt, "execute_with_hitl", gate)
    return seen


def test_finance_record_uses_logged_in_user_not_body(fake_gate, monkeypatch):
    from mateai.infrastructure.database.erp_database import erp_db
    written = {}
    monkeypatch.setattr(erp_db, "add_finance_record", lambda **kw: written.update(kw) or {"id": 1, **kw})
    c = _client("manager", username="ke_toan")
    r = c.post("/api/v1/enterprise/finances/record",
               json={"type": "expense", "amount": 1000, "category": "VPP", "created_by": "giam_doc"})
    assert r.status_code == 200 and fake_gate["action"] == "record_expense"
    assert written["created_by"] == "ke_toan"                      # không mạo danh được
    assert c.post("/api/v1/enterprise/finances/record", json={"type": "loan", "amount": 1}).status_code == 400


def test_onboarding_manager_cannot_grant_privileged_role(fake_gate, monkeypatch):
    from mateai.application.skills.builtin import onboarding_workflow as ow
    got = {}
    monkeypatch.setattr(ow.onboarding_workflow, "onboard_new_employee", lambda **kw: got.update(kw) or {"ok": True})
    r = _client("manager").post("/api/v1/enterprise/onboarding",
                                json={"name": "An", "position": "Kế toán", "role": "admin"})
    assert r.status_code == 200 and got["role"] == "operator" and got["allow_privileged_role"] is False
    r = _client("admin").post("/api/v1/enterprise/onboarding", json={"name": "Bình", "position": "IT", "role": "it_support"})
    assert got["role"] == "it_support" and got["allow_privileged_role"] is True
    assert _client("manager").post("/api/v1/enterprise/onboarding", json={"name": "C"}).status_code == 400


def test_multi_agent_route_requires_query_and_goes_through_gate(fake_gate, monkeypatch):
    from mateai.application.agent import agent_orchestrator as ao
    monkeypatch.setattr(ao.multi_agent_system, "route_and_execute", lambda query: {"reply": f"ok:{query}"})
    c = _client("manager")
    assert c.post("/api/v1/enterprise/multi-agent/route", json={"query": " "}).status_code == 400
    r = c.post("/api/v1/enterprise/multi-agent/route", json={"query": "doanh thu tháng"}).json()
    assert r["result"] == {"reply": "ok:doanh thu tháng"} and fake_gate["action"] == "delegate_to_multi_agent"


def test_gate_denied_and_pending_map_to_http(monkeypatch):
    import mateai.application.security.zero_trust as zt

    async def denied(**kw):
        return {"status": "denied", "message": "không được"}

    monkeypatch.setattr(zt, "execute_with_hitl", denied)
    c = _client("manager")
    assert c.post("/api/v1/enterprise/multi-agent/route", json={"query": "x"}).status_code == 403

    async def pending(**kw):
        return {"status": "awaiting_approval", "message": "chờ duyệt"}

    monkeypatch.setattr(zt, "execute_with_hitl", pending)
    assert c.post("/api/v1/enterprise/multi-agent/route", json={"query": "x"}).status_code == 202
