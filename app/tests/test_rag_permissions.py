# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 Dương Thanh Lịch — VN-MateAI. See LICENSE and NOTICE.
"""
Quyền đọc kho tri thức theo tài liệu: ẩn đoạn / quan hệ đồ thị / BM25 / danh sách tài liệu với người không đủ cấp hoặc sai phòng ban,
không lộ tên tài liệu bị ẩn, admin thấy tất cả, không rõ người hỏi thì KHÔNG phải admin, lỗi đọc ACL thì chặn (fail-closed).
Dùng bộ sưu tập giả thay ChromaDB thật (không đụng kho tri thức thật).
"""
from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from mateai.application.knowledge import rag_acl
from mateai.application.knowledge.graph_rag import graph_rag
from mateai.application.knowledge.rag_engine import rag_engine
from mateai.application.security.security_guard import security_guard
from mateai.interfaces.http.auth_dependencies import get_current_user
from mateai.interfaces.http.routers import enterprise

DOCS = {
    "nghi-phep.txt": "Nhân viên nghỉ phép năm phải nộp đơn cho quản lý trực tiếp trước ba ngày.",
    "luong-ban-giam-doc.txt": "Bảng lương ban giám đốc: mức lương nghỉ phép năm của giám đốc được tính riêng.",
    "tai-chinh-ke-toan.txt": "Quy trình quyết toán nghỉ phép năm thuộc phòng kế toán, chỉ kế toán được xem.",
}
READERS = {
    "an": {"id": "an", "role": "viewer", "department": "Kinh doanh", "clearance": 1, "all_departments": False},
    "ke-toan": {"id": "ke-toan", "role": "it_support", "department": "Kế toán", "clearance": 3, "all_departments": False},
    "giam-doc": {"id": "giam-doc", "role": "operator", "department": "Ban giám đốc", "clearance": 4, "all_departments": False},
    "boss": {"id": "boss", "role": "admin", "department": None, "clearance": 4, "all_departments": True},
}


class FakeCollection:
    def __init__(self, docs):
        self.docs = docs

    def count(self):
        return len(self.docs)

    def query(self, query_texts, n_results):
        names = list(self.docs)[:n_results]
        return {"documents": [[self.docs[n] for n in names]], "metadatas": [[{"doc_name": n, "category": "t"} for n in names]],
                "distances": [[0.1 * i for i in range(len(names))]]}

    def get(self):
        names = list(self.docs)
        return {"documents": [self.docs[n] for n in names], "metadatas": [{"doc_name": n, "category": "t", "timestamp": ""} for n in names]}


@pytest.fixture(autouse=True)
def setup(monkeypatch):
    monkeypatch.setattr(rag_engine, "collection", FakeCollection(dict(DOCS)))
    monkeypatch.setattr(security_guard, "principal", lambda ident: READERS.get(ident) or {"id": ident, "role": "viewer", "department": None, "clearance": 1, "all_departments": False})
    for name in DOCS:
        rag_acl.reset_acl(name, "t")
    yield
    for name in DOCS:
        rag_acl.reset_acl(name, "t")


def restrict():
    rag_acl.set_acl("luong-ban-giam-doc.txt", "RESTRICTED", [], "boss")
    rag_acl.set_acl("tai-chinh-ke-toan.txt", "CONFIDENTIAL", ["Kế toán"], "boss")


def ask(user, q="nghỉ phép năm"):
    with rag_acl.use_reader(user):
        return rag_engine.query(q, top_k=3)


def docs_of(res):
    return sorted(m["document"] for m in res["matches"])


def test_default_is_unchanged_for_everyone():
    for u in ("an", "ke-toan", "boss", None):
        assert len(ask(u)["matches"]) == 3, u


def test_restricted_documents_are_hidden_without_leaking_their_names():
    restrict()
    an = ask("an")
    assert docs_of(an) == ["nghi-phep.txt"] and an["hidden_by_permission"] == 2
    assert "luong" not in str(an) and "tai-chinh" not in str(an) and "ban giám đốc" not in str(an)


def test_clearance_and_department_both_required():
    restrict()
    assert docs_of(ask("ke-toan")) == ["nghi-phep.txt", "tai-chinh-ke-toan.txt"]        # đủ cấp + đúng phòng, nhưng chưa đủ cấp RESTRICTED
    assert docs_of(ask("giam-doc")) == ["luong-ban-giam-doc.txt", "nghi-phep.txt"]       # đủ cấp 4 nhưng sai phòng ban của tài liệu tài chính
    assert len(ask("boss")["matches"]) == 3 and ask("boss")["hidden_by_permission"] == 0


def test_unknown_reader_is_never_admin():
    restrict()
    assert docs_of(ask(None)) == ["nghi-phep.txt"]
    assert docs_of(ask("nguoi-la-hoan-toan")) == ["nghi-phep.txt"]


def test_principal_from_tool_gate_is_used_when_no_explicit_reader():
    from mateai.application.security.security_guard import CURRENT_PRINCIPAL
    restrict()
    tok = CURRENT_PRINCIPAL.set("ke-toan")
    try:
        assert "tai-chinh-ke-toan.txt" in docs_of(rag_engine.query("nghỉ phép năm", top_k=3))
    finally:
        CURRENT_PRINCIPAL.reset(tok)


def test_hidden_chunks_do_not_starve_visible_results():
    restrict()
    rag_engine.collection = FakeCollection({"luong-ban-giam-doc.txt": DOCS["luong-ban-giam-doc.txt"], "tai-chinh-ke-toan.txt": DOCS["tai-chinh-ke-toan.txt"],
                                            "nghi-phep.txt": DOCS["nghi-phep.txt"]})
    assert docs_of(ask("an")) == ["nghi-phep.txt"]            # hai đoạn đứng đầu bị ẩn, đoạn hợp lệ phía sau vẫn được trả


def test_answer_says_how_many_were_hidden_not_which():
    restrict()
    with rag_acl.use_reader("an"):
        out = rag_engine.answer_policy_question("nghỉ phép năm")
    assert out["hidden_by_permission"] == 2 and out["sources"] == ["nghi-phep.txt"] and "luong" not in out["answer"]


def test_document_list_and_bm25_corpus_are_filtered():
    restrict()
    with rag_acl.use_reader("an"):
        assert [d["name"] for d in rag_engine.list_documents()] == ["nghi-phep.txt"]
        assert [c["source"] for c in rag_engine.get_searchable_corpus()] == ["nghi-phep.txt"]
    with rag_acl.use_reader("boss"):
        listed = {d["name"]: d["classification"] for d in rag_engine.list_documents()}
    assert listed == {"nghi-phep.txt": "INTERNAL", "luong-ban-giam-doc.txt": "RESTRICTED", "tai-chinh-ke-toan.txt": "CONFIDENTIAL"}


def test_graph_edges_from_hidden_documents_do_not_leak(monkeypatch):
    restrict()
    edges = [{"subject": "Nhân viên", "predicate": "nộp đơn nghỉ phép", "object": "Quản lý", "context": "", "source": "nghi-phep.txt"},
             {"subject": "Giám đốc", "predicate": "nghỉ phép năm riêng", "object": "Hội đồng", "context": "", "source": "luong-ban-giam-doc.txt"}]
    monkeypatch.setattr(graph_rag, "edges", edges)
    with rag_acl.use_reader("an"):
        got = graph_rag.traverse_graph(["nghỉ", "phép", "năm"])
    assert [e["source"] for e in got] == ["nghi-phep.txt"]
    with rag_acl.use_reader("boss"):
        assert len(graph_rag.traverse_graph(["nghỉ", "phép", "năm"])) == 2


def test_unreadable_acl_table_fails_closed(monkeypatch):
    def boom():
        raise RuntimeError("db down")
    monkeypatch.setattr(rag_acl, "list_acls", boom)
    with rag_acl.use_reader("an"):
        assert rag_engine.query("nghỉ phép năm", top_k=3)["matches"] == []
    with rag_acl.use_reader("boss"):
        assert len(rag_engine.query("nghỉ phép năm", top_k=3)["matches"]) == 3     # admin không phụ thuộc bảng ACL


def test_set_acl_validates_and_audits():
    with pytest.raises(rag_acl.AclError):
        rag_acl.set_acl("a.txt", "TUYET-MAT", [], "boss")
    with pytest.raises(rag_acl.AclError):
        rag_acl.set_acl("", "PUBLIC", [], "boss")
    acl = rag_acl.set_acl("nghi-phep.txt", "public", ["Kế toán", "kế toán", " "], "boss")
    assert acl["classification"] == "PUBLIC" and acl["departments"] == ["Kế toán"]
    assert rag_acl.reset_acl("nghi-phep.txt", "boss") and not rag_acl.reset_acl("nghi-phep.txt", "boss")
    assert rag_acl.get_acl("nghi-phep.txt")["default"] is True


def test_public_document_is_readable_even_with_zero_clearance(monkeypatch):
    monkeypatch.setitem(READERS, "khach", {"id": "khach", "role": "viewer", "department": None, "clearance": 0, "all_departments": False})
    assert ask("khach")["matches"] == []                       # INTERNAL mặc định cần cấp 1
    rag_acl.set_acl("nghi-phep.txt", "PUBLIC", [], "boss")
    assert docs_of(ask("khach")) == ["nghi-phep.txt"]


# ── API ─────────────────────────────────────────────────────────────────────

def client(role, name):
    app = FastAPI()
    app.include_router(enterprise.router)
    app.dependency_overrides[get_current_user] = lambda: {"role": role, "username": name}
    return TestClient(app)


def test_acl_api_is_admin_only_and_query_route_respects_it():
    restrict()
    assert client("manager", "an").get("/api/v1/enterprise/rag/acl").status_code == 403
    assert client("manager", "an").put("/api/v1/enterprise/rag/acl/x.txt", json={"classification": "PUBLIC"}).status_code == 403
    admin = client("admin", "boss")
    listed = admin.get("/api/v1/enterprise/rag/acl").json()
    assert {d["doc_name"]: d["classification"] for d in listed["documents"]}["luong-ban-giam-doc.txt"] == "RESTRICTED"
    assert admin.put("/api/v1/enterprise/rag/acl/nghi-phep.txt", json={"classification": "xx"}).status_code == 422
    assert admin.put("/api/v1/enterprise/rag/acl/nghi-phep.txt", json={"classification": "INTERNAL", "departments": ["Kinh doanh"]}).status_code == 200
    r = client("viewer", "an").post("/api/v1/enterprise/rag/query", json={"question": "nghỉ phép năm"}).json()
    assert r["result"]["sources"] == ["nghi-phep.txt"] and r["result"]["hidden_by_permission"] == 2
    assert [d["name"] for d in client("viewer", "an").get("/api/v1/enterprise/rag/documents").json()["documents"]] == ["nghi-phep.txt"]
    assert admin.delete("/api/v1/enterprise/rag/acl/nghi-phep.txt").status_code == 200
    assert admin.delete("/api/v1/enterprise/rag/acl/nghi-phep.txt").status_code == 404
