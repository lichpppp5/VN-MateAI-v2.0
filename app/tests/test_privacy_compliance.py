# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 Dương Thanh Lịch — VN-MateAI. See LICENSE and NOTICE.
"""
tests/test_privacy_compliance.py
================================
Dữ liệu cá nhân (thẩm định thương mại 14/10/2026 #24): sổ hoạt động xử lý, tra cứu theo chủ thể, xoá / ẩn danh theo yêu cầu
(audit_logs giữ lại có lý do), không xoá admin cuối cùng, xác nhận bắt buộc, chỉ admin, mọi thao tác vào audit.
"""
from __future__ import annotations

import json
import uuid

import pytest
from fastapi.testclient import TestClient

from mateai.application.privacy import compliance
from mateai.infrastructure.database.db_manager import db_manager
from mateai.infrastructure.database.erp_database import erp_db
from mateai.infrastructure.directory.domain_sync import domain_manager


@pytest.fixture
def person():
    tag = uuid.uuid4().hex[:6]
    sam, email, phone = f"nv{tag}", f"nv{tag}@congty.vn", f"09{uuid.uuid4().int % 10**8:08d}"
    with domain_manager._get_connection() as conn:
        conn.execute("INSERT INTO employees (sam_account_name, full_name, department, title, email, phone) VALUES (?, ?, ?, ?, ?, ?);",
                     (sam, f"Nguyễn Thử {tag}", "IT", "Kỹ sư", email, phone))
        conn.commit()
    db_manager.create_user({"username": sam, "password": "Thu2026xyz!", "role": "viewer", "full_name": f"Nguyễn Thử {tag}"})
    db_manager.add_voice_trace({"trace_id": f"trace_{tag}", "channel": "hud", "outcome": "llm", "query": f"{sam} hỏi lịch họp"})
    return {"sam": sam, "email": email, "phone": phone}


def test_register_covers_every_store():
    stores = " ".join(a["storage"] for a in compliance.REGISTER)
    for needle in ("vnmate.users", "hr.employees", "voice_traces", "audit_logs", "op_tasks", "logs/"):
        assert needle in stores
    assert all(a["retention"] and a["purpose"] and a["legal_basis"] for a in compliance.REGISTER)


def test_lookup_finds_records_by_any_identifier(person):
    for ident in (person["sam"], person["email"], person["phone"]):
        found = compliance.lookup(ident)["found"]
        assert found["hr.employees"], ident
    assert compliance.lookup(person["sam"])["found"]["users"][0]["username"] == person["sam"]
    assert compliance.lookup(person["sam"])["found"]["voice_traces"] >= 1


def test_too_short_or_wildcard_identifier_rejected():
    for bad in ("ab", "%%%", "  _ "):
        with pytest.raises(ValueError):
            compliance.lookup(bad)


def test_erase_anonymises_and_keeps_audit(person):
    before = erp_db.verify_audit_chain()
    res = compliance.erase(person["sam"], "admin")
    assert res["done"]["hr.employees_anonymised"] == 1 and res["done"]["voice_traces_deleted"] >= 1
    assert res["done"]["user_deleted"] == person["sam"] and "audit_logs" in res["kept"]
    assert compliance.lookup(person["email"])["found"]["hr.employees"] == []
    assert db_manager.get_user_by_username_or_id(person["sam"]) is None
    with erp_db.get_connection() as conn:
        row = conn.execute("SELECT payload FROM audit_logs WHERE action_type = 'PRIVACY_ERASURE' ORDER BY id DESC LIMIT 1;").fetchone()
    assert person["sam"] not in row[0] and json.loads(row[0])["subject_hash"]          # audit không lưu lại định danh đã xoá
    assert erp_db.verify_audit_chain()["ok"] == before["ok"]


def test_last_admin_is_never_deleted(monkeypatch):
    name = f"ad{uuid.uuid4().hex[:6]}"
    db_manager.create_user({"username": name, "password": "Admin2026xyz", "role": "admin", "full_name": "Admin duy nhất"})
    monkeypatch.setattr(db_manager, "get_all_users", lambda: [{"username": name, "role": "admin"}])
    res = compliance.erase(name, "admin")
    assert "users" in res["kept"] and db_manager.get_user_by_username_or_id(name)


def test_api_admin_only_and_needs_confirmation(person):
    from mateai.interfaces.http.server import app
    c = TestClient(app)
    tokens = {}
    for role in ("admin", "viewer"):
        n = f"pv-{role}-{uuid.uuid4().hex[:5]}"
        db_manager.create_user({"username": n, "password": "Riengtu2026x", "role": role, "full_name": "x"})
        tokens[role] = {"Authorization": "Bearer " + c.post("/api/v1/login", json={"username": n, "password": "Riengtu2026x"}).json()["access_token"]}
    assert c.get("/api/v1/privacy/register", headers=tokens["viewer"]).status_code == 403
    assert len(c.get("/api/v1/privacy/register", headers=tokens["admin"]).json()["activities"]) >= 6
    r = c.post("/api/v1/privacy/subject/erase", json={"subject": person["sam"], "confirm": "sai"}, headers=tokens["admin"])
    assert r.status_code == 400
    assert c.post("/api/v1/privacy/subject/lookup", json={"subject": person["sam"]}, headers=tokens["admin"]).status_code == 200
    r = c.post("/api/v1/privacy/subject/erase", json={"subject": person["sam"], "confirm": person["sam"]}, headers=tokens["admin"])
    assert r.status_code == 200 and r.json()["done"]["hr.employees_anonymised"] == 1
