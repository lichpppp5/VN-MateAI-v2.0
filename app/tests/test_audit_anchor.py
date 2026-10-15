# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 Dương Thanh Lịch — VN-MateAI. See LICENSE and NOTICE.
"""Neo đầu chuỗi băm audit ra ngoài CSDL (thẩm định thương mại #17): ghi ra ổ khác + object storage; neo cũ không khớp
(lịch sử bị viết lại rồi tính lại băm, hoặc dòng bị xoá) → phát hiện."""
from __future__ import annotations

from mateai.application.security import audit_anchor
from mateai.infrastructure.database.erp_database import erp_db


class _Store:
    def __init__(self):
        self.items = {}

    def put(self, key, data, content_type="application/octet-stream"):
        self.items[key] = data
        return key


def test_anchor_written_everywhere_and_verifies(tmp_path, monkeypatch):
    erp_db.log_audit_action(action_type="TEST_ANCHOR", employee_id="admin", status="success")
    monkeypatch.setattr(audit_anchor, "_root", lambda: tmp_path)
    store = _Store()
    res = audit_anchor.anchor(store=store, extra_dirs=[str(tmp_path / "o_khac")])
    assert res["anchored"] and len(res["written"]) == 3 and not res["errors"]
    assert (tmp_path / "o_khac" / "anchors" / "audit-anchors.jsonl").is_file() and store.items
    assert audit_anchor.verify()["ok"] and audit_anchor.verify()["anchors_checked"] == 1


def test_rewritten_history_is_detected():
    h = audit_anchor.head()
    forged = [{"at": "2026-10-01T02:00:00", "id": h["id"], "row_hash": "0" * 64, "rows": h["rows"]}]
    res = audit_anchor.verify(forged)
    assert not res["ok"] and "đổi" in res["broken"][0]["reason"]
    deleted = [{"at": "x", "id": h["id"], "row_hash": h["row_hash"], "rows": h["rows"] + 1000}]
    assert "xoá" in audit_anchor.verify(deleted)["broken"][0]["reason"]
