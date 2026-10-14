# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 Dương Thanh Lịch — VN-MateAI. See LICENSE and NOTICE.
"""
tests/test_audit_single_store.py
================================
Một kho audit duy nhất: bảng audit_logs (chỉ INSERT).

Trước đây sự kiện Zero-Trust/HITL ghi vào logs/security_audit.log — kho thứ
hai, xoá sạch được qua DELETE /api/v1/security/audit-logs — còn RBAC ghi vào
audit_logs. Dùng DB tạm của conftest (VNMATEAI_DB_PATH), không gọi mạng.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from mateai.infrastructure.database.erp_database import erp_db  # noqa: E402
from mateai.application.security.safety_guard import security_engine  # noqa: E402


def test_security_event_goes_to_audit_logs_and_reads_back():
    before = len(erp_db.get_audit_logs(limit=5000))
    security_engine.log_audit("pc-07", "kill_process", "need_confirm", "pending_confirmation", {"pid": 9})

    rows = erp_db.get_audit_logs(limit=5000)
    assert len(rows) == before + 1
    assert rows[0]["status"] == "pending" and rows[0]["action_type"] == "kill_process"

    ev = security_engine.get_recent_audit_logs(limit=1)[0]
    assert ev["client_id"] == "pc-07"
    assert ev["risk"] == "NEED_CONFIRM" and ev["status"] == "PENDING_CONFIRMATION"
    assert ev["details"] == {"pid": 9}
    assert ev["timestamp_epoch"] > 0


def test_rbac_rows_show_in_the_same_view():
    erp_db.write_audit_log(action_type="read_file", status="blocked", employee_id="eve",
                           payload={"path": "x"})
    ev = security_engine.get_recent_audit_logs(limit=1)[0]
    assert ev["action"] == "read_file" and ev["status"] == "BLOCKED" and ev["risk"] == "-"


def test_pending_action_is_restored_from_audit_logs():
    """Yêu cầu duyệt của cổng tool nằm trong audit_logs và khôi phục được; đã huỷ thì không."""
    from mateai.application.security.zero_trust import HumanInTheLoopManager

    m = HumanInTheLoopManager()
    req = m.request_approval(action_name="restart_service", params={"name": "spooler"}, requested_by="ops",
                             kind="tool", context={"target_client": "pc-restore"})
    restored = HumanInTheLoopManager()
    restored.restore_pending_from_audit()
    got = restored.get_pending(req["id"])
    assert got and got["action_name"] == "restart_service" and got["context"]["target_client"] == "pc-restore"

    m.reject(req["id"], rejected_by="admin")
    again = HumanInTheLoopManager()
    again.restore_pending_from_audit()
    assert again.get_pending(req["id"]) is None


def test_audit_log_cannot_be_cleared_over_http():
    from fastapi.testclient import TestClient
    import mateai.interfaces.http.server as server

    from mateai.application.security.auth_manager import auth_manager

    admin = next(u for u in auth_manager.get_all_users() if u.get("role") == "admin")
    token = auth_manager.create_access_token(data={"sub": admin["username"], "role": "admin"})
    client = TestClient(server.app)  # không `with` → không chạy startup
    res = client.delete("/api/v1/security/audit-logs", headers={"Authorization": f"Bearer {token}"})
    assert res.status_code == 405  # chỉ còn GET trên đường này
