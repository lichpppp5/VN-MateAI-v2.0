# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 Dương Thanh Lịch — VN-MateAI. See LICENSE and NOTICE.
"""
tests/test_memory_trust.py
==========================
Prompt cuối §59 (memory trust) + §60 (memory poisoning): mỗi bản ghi trí nhớ dài hạn có
source, created_by, verified, timestamp, expires_at, scope, confidence.

  - bản ghi chưa xác minh có độ tin cậy thấp và được GẮN NHÃN khi tìm (AI không coi là sự thật);
  - bản ghi hết hạn không còn được trả về;
  - chỉ admin xác minh được (có audit); người ghi không tự khai verified / confidence.
Collection ChromaDB được thay bằng bản giả trong RAM — không đụng kho thật.
"""
from __future__ import annotations

from datetime import datetime, timedelta

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient


class FakeCollection:
    def __init__(self):
        self.rows = {}

    def count(self):
        return len(self.rows)

    def add(self, documents, metadatas, ids):
        for d, m, i in zip(documents, metadatas, ids):
            self.rows[i] = (d, dict(m))

    def query(self, query_texts, n_results):
        ids = list(self.rows)[:n_results]
        return {"ids": [ids], "documents": [[self.rows[i][0] for i in ids]],
                "metadatas": [[self.rows[i][1] for i in ids]], "distances": [[0.1] * len(ids)]}

    def get(self, ids=None, **kw):
        ids = ids or list(self.rows)
        return {"ids": ids, "metadatas": [self.rows[i][1] for i in ids if i in self.rows]}

    def update(self, ids, metadatas):
        for i, m in zip(ids, metadatas):
            self.rows[i] = (self.rows[i][0], dict(m))


@pytest.fixture
def mem(monkeypatch):
    import mateai.infrastructure.memory.cognitive_memory as cm
    col = FakeCollection()
    monkeypatch.setattr(cm, "get_collection", lambda *a, **k: col)
    return cm, col


def _client(role, audits=None, monkeypatch=None):
    import mateai.interfaces.http.routers.memory as mr
    from mateai.interfaces.http.auth_dependencies import get_current_user
    app = FastAPI()
    app.include_router(mr.router)
    app.dependency_overrides[get_current_user] = lambda: {"username": f"{role}_u", "role": role}
    return TestClient(app)


def test_records_carry_trust_fields_and_cannot_self_declare(mem, monkeypatch):
    cm, col = mem
    r = _client("manager").post("/api/v1/memory/memorize", json={
        "error_signature": "IIS 503", "root_cause": "pool dừng", "script": "Start-WebAppPool x",
        "metadata": {"verified": True, "confidence": 1.0, "created_by": "admin"}})
    assert r.status_code == 200
    meta = next(iter(col.rows.values()))[1]
    assert meta["created_by"] == "manager_u" and meta["verified"] is False and meta["confidence"] <= 0.5
    assert meta["source"] == "portal" and meta["scope"] and meta["expires_at"] > datetime.utcnow().isoformat()


def test_search_flags_unverified_and_hides_expired(mem):
    cm, col = mem
    cm.memorize_solution("lỗi A", "x", "y", metadata={"created_by": "a", "verified": True, "confidence": 1.0})
    cm.memorize_solution("lỗi B", "x", "y", metadata={"created_by": "m", "verified": False, "confidence": 0.5})
    cm.memorize_solution("lỗi C", "x", "y", metadata={
        "created_by": "m", "verified": True, "confidence": 1.0,
        "expires_at": (datetime.utcnow() - timedelta(days=1)).isoformat()})
    res = cm.search_past_incidents("lỗi", n_results=10)
    sigs = {r["metadata"]["error_signature"]: r["trust"] for r in res}
    assert sigs == {"lỗi A": "verified", "lỗi B": "unverified"}          # C hết hạn: không trả


def test_only_admin_verifies_and_it_is_audited(mem, monkeypatch):
    cm, col = mem
    from mateai.application.security import safety_guard
    audits = []
    monkeypatch.setattr(safety_guard.security_engine, "log_audit", lambda *a, **k: audits.append(a))
    doc = cm.memorize_solution("lỗi D", "x", "y", metadata={"created_by": "m", "verified": False, "confidence": 0.5})
    assert _client("manager").post(f"/api/v1/memory/{doc}/verify").status_code == 403
    r = _client("admin").post(f"/api/v1/memory/{doc}/verify")
    assert r.status_code == 200
    meta = col.rows[doc][1]
    assert meta["verified"] is True and meta["confidence"] == 1.0 and meta["verified_by"] == "admin_u"
    assert any(a[1] == "memory_verify" for a in audits)
    assert _client("admin").post("/api/v1/memory/khong-co/verify").status_code == 404
