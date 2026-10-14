# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 Dương Thanh Lịch — VN-MateAI. See LICENSE and NOTICE.
"""
tests/test_ad_import.py
=======================
Nhập nhân viên + máy tính từ bản sao AD vào ERP (`ERPDatabase.import_directory`, `ad_import`, API):

  - xem trước (dry run) cho đúng số liệu của lần nhập thật và KHÔNG ghi gì;
  - chạy lại không nhân đôi (người, máy, phòng ban);
  - dữ liệu nhập tay không bị ghi đè; không đổi phòng ban; người mới chỉ có quyền `viewer`;
  - mơ hồ (hai người trùng tên) thì bỏ qua và báo, không đoán;
  - máy khớp tên không phân biệt hoa / thường / hậu tố miền; chủ máy suy từ mô tả AD khi khớp đúng.
Chạy trên CSDL của bộ test (SQLite hoặc PostgreSQL tuỳ `VNMATEAI_TEST_BACKEND`).
"""
from __future__ import annotations

import pytest

from mateai.infrastructure.database.erp_database import erp_db, open_sqlite

PREFIX = "ZZAD"


@pytest.fixture(autouse=True)
def _clean():
    def wipe():
        with open_sqlite(erp_db.db_path) as conn:
            conn.execute("DELETE FROM devices WHERE hostname LIKE ?;", (f"{PREFIX}%",))
            conn.execute("DELETE FROM employees WHERE name LIKE ? OR ad_sam LIKE ?;", (f"{PREFIX}%", f"{PREFIX.lower()}%"))
            conn.execute("DELETE FROM departments WHERE name LIKE ?;", (f"{PREFIX}%",))
            conn.commit()
    wipe()
    yield
    wipe()


def _emp(sam, name, dept, title="", email="", phone=""):
    return {"sam_account_name": sam, "full_name": name, "department": dept, "title": title, "email": email, "phone": phone}


def _pc(host, owner="", ip="", os_name="Windows 11 Pro"):
    return {"hostname": host, "assigned_to": owner, "ip_address": ip, "os_version": os_name}


def _counts():
    with open_sqlite(erp_db.db_path) as conn:
        q = lambda sql: conn.execute(sql, (f"{PREFIX}%",)).fetchone()[0]
        return (q("SELECT COUNT(*) FROM departments WHERE name LIKE ?;"),
                q("SELECT COUNT(*) FROM employees WHERE name LIKE ?;"),
                q("SELECT COUNT(*) FROM devices WHERE hostname LIKE ?;"))


EMPS = [_emp("zzad.an", f"{PREFIX} Nguyễn An", f"{PREFIX} Kế toán", "Kế toán trưởng", "an@x.vn", "0901"),
        _emp("zzad.binh", f"{PREFIX} Trần Bình", f"{PREFIX} Kỹ thuật", "Kỹ sư"),
        _emp("zzad.chi", f"{PREFIX} Lê Chi", "", "Thư ký")]               # không phòng ban
PCS = [_pc(f"{PREFIX}-KT-01.corp.local", f"{PREFIX} Nguyễn An", "10.0.0.5"),
       _pc(f"{PREFIX}-SRV-01", "", "10.0.0.9", "Windows Server 2022"),
       _pc(f"{PREFIX}-LAP-07", "ghi chú tự do", "")]                       # chủ không khớp ai -> cần phòng ban mặc định


def test_dry_run_matches_apply_and_writes_nothing():
    before = _counts()
    dry = erp_db.import_directory(EMPS, PCS, default_dept=f"{PREFIX} Chung", apply=False)
    assert _counts() == before and dry["applied"] is False        # xem trước không ghi gì (kể cả phòng ban mới)
    real = erp_db.import_directory(EMPS, PCS, default_dept=f"{PREFIX} Chung", apply=True)
    assert real["applied"] is True and real["stats"] == dry["stats"]
    st = real["stats"]
    assert st["employees"]["created"] == 3 and st["devices"]["created"] == 3
    assert st["departments_created"] == 3                          # Kế toán, Kỹ thuật, Chung
    assert st["devices"]["owner_matched"] == 1                     # chỉ máy KT-01 khớp đúng tên chủ
    assert _counts() == (3, 3, 3)


def test_rerun_is_idempotent_and_never_duplicates():
    erp_db.import_directory(EMPS, PCS, default_dept=f"{PREFIX} Chung", apply=True)
    again = erp_db.import_directory(EMPS, PCS, default_dept=f"{PREFIX} Chung", apply=True)
    st = again["stats"]
    assert st["employees"]["created"] == 0 and st["devices"]["created"] == 0 and st["departments_created"] == 0
    assert st["employees"]["unchanged"] == 3 and st["devices"]["unchanged"] == 3
    assert _counts() == (3, 3, 3)


def test_new_people_are_viewer_and_linked_to_their_ad_account():
    erp_db.import_directory(EMPS, [], default_dept="", apply=True)
    with open_sqlite(erp_db.db_path) as conn:
        rows = conn.execute("SELECT name, role, ad_sam FROM employees WHERE name LIKE ? ORDER BY name;", (f"{PREFIX}%",)).fetchall()
    assert [(r["role"], r["ad_sam"]) for r in rows] == [("viewer", "zzad.an"), ("viewer", "zzad.binh")]   # Lê Chi bị bỏ qua (không phòng ban)


def test_no_default_department_skips_and_reports():
    res = erp_db.import_directory(EMPS, PCS, default_dept="", apply=True)
    st = res["stats"]
    assert st["employees"]["skipped_no_department"] == 1 and st["employees"]["created"] == 2
    assert st["devices"]["skipped_no_department"] == 2             # SRV-01 và LAP-07 không có chủ và không có mặc định
    assert f"{PREFIX} Lê Chi" in res["samples"]["skipped_no_department"]
    assert _counts() == (2, 2, 1)


def test_departments_not_created_when_disallowed():
    res = erp_db.import_directory(EMPS[:2], [], create_departments=False, apply=True)
    assert res["stats"]["employees"]["skipped_department_missing"] == 2 and _counts() == (0, 0, 0)


def test_manual_data_is_never_overwritten_and_department_never_moves():
    dept = erp_db.add_department(name=f"{PREFIX} Nhập tay", description="")
    erp_db.add_employee(dept["id"], f"{PREFIX} Nguyễn An", "Giám đốc (nhập tay)", "", "", "viewer")
    res = erp_db.import_directory(EMPS[:1], [], apply=True)
    assert res["stats"]["employees"]["updated"] == 1               # chỉ điền chỗ trống
    with open_sqlite(erp_db.db_path) as conn:
        r = conn.execute("SELECT dept_id, position, email, phone, ad_sam FROM employees WHERE name = ?;",
                         (f"{PREFIX} Nguyễn An",)).fetchone()
    assert r["dept_id"] == dept["id"] and r["position"] == "Giám đốc (nhập tay)"      # không đổi phòng ban, không ghi đè
    assert (r["email"], r["phone"], r["ad_sam"]) == ("an@x.vn", "0901", "zzad.an")      # chỗ trống được điền + liên kết
    assert _counts()[1] == 1                                       # không nhân đôi


def test_ambiguous_name_is_skipped_not_guessed():
    d1 = erp_db.add_department(name=f"{PREFIX} P1", description="")
    d2 = erp_db.add_department(name=f"{PREFIX} P2", description="")
    erp_db.add_employee(d1["id"], f"{PREFIX} Trùng Tên", "", "", "", "viewer")
    erp_db.add_employee(d2["id"], f"{PREFIX} Trùng Tên", "", "", "", "viewer")
    res = erp_db.import_directory([_emp("zzad.trung", f"{PREFIX} Trùng Tên", f"{PREFIX} P1")], [], apply=True)
    assert res["stats"]["employees"]["skipped_ambiguous"] == 1 and res["warnings"]
    assert _counts()[1] == 2


def test_device_matching_ignores_case_and_domain_suffix_and_fills_blanks():
    dept = erp_db.add_department(name=f"{PREFIX} Máy", description="")
    with open_sqlite(erp_db.db_path) as conn:
        conn.execute("INSERT INTO devices (dept_id, hostname, ip_address, type) VALUES (?, ?, '', 'Workstation');",
                     (dept["id"], f"{PREFIX.lower()}-kt-01"))
        conn.commit()
    res = erp_db.import_directory([], [_pc(f"{PREFIX}-KT-01.CORP.LOCAL", "", "10.0.0.5")], apply=True)
    assert res["stats"]["devices"]["updated"] == 1 and res["stats"]["devices"]["created"] == 0
    with open_sqlite(erp_db.db_path) as conn:
        assert conn.execute("SELECT ip_address FROM devices WHERE hostname LIKE ?;", (f"{PREFIX.lower()}%",)).fetchone()[0] == "10.0.0.5"
    assert _counts()[2] == 1


def test_server_type_from_os():
    erp_db.import_directory([], [_pc(f"{PREFIX}-SRV-01", "", "", "Windows Server 2022")], default_dept=f"{PREFIX} Chung", apply=True)
    with open_sqlite(erp_db.db_path) as conn:
        assert conn.execute("SELECT type FROM devices WHERE hostname = ?;", (f"{PREFIX}-SRV-01",)).fetchone()[0] == "Server"


def test_application_layer_reports_empty_ad_copy(monkeypatch):
    from mateai.application.enterprise import ad_import
    from mateai.infrastructure.directory import domain_sync
    monkeypatch.setattr(domain_sync.domain_manager, "get_employees", lambda limit=0: [])
    monkeypatch.setattr(domain_sync.domain_manager, "get_computers", lambda limit=0: [])
    res = ad_import.run(apply=True)
    assert res["empty"] is True and res["applied"] is False and "Đồng Bộ AD" in res["message"]


def test_api_dry_run_default_then_apply(monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from mateai.infrastructure.directory import domain_sync
    from mateai.interfaces.http import api_erp
    from mateai.interfaces.http.auth_dependencies import get_current_user

    monkeypatch.setattr(domain_sync.domain_manager, "get_employees", lambda limit=0: EMPS)
    monkeypatch.setattr(domain_sync.domain_manager, "get_computers", lambda limit=0: PCS)
    app = FastAPI()
    app.include_router(api_erp.router)
    role = {"v": "manager"}
    app.dependency_overrides[get_current_user] = lambda: {"username": "u", "role": role["v"]}
    c = TestClient(app)
    body = {"default_department": f"{PREFIX} Chung"}
    r = c.post("/api/erp/import-from-ad", json=body).json()                 # dry_run mặc định = true
    assert r["status"] == "success" and r["applied"] is False and r["stats"]["employees"]["created"] == 3
    assert _counts() == (0, 0, 0)
    r = c.post("/api/erp/import-from-ad", json={**body, "dry_run": False}).json()
    assert r["applied"] is True and _counts() == (3, 3, 3)
    role["v"] = "viewer"
    assert c.post("/api/erp/import-from-ad", json=body).status_code == 403    # viewer không được nhập


def _agents(monkeypatch):
    from mateai.application.devices import worker_enrollment
    from mateai.interfaces.websocket import client_orchestrator
    monkeypatch.setattr(client_orchestrator.orchestrator, "get_connected_clients",
                        lambda: [{"client_id": f"{PREFIX}-KT-01", "hostname": f"{PREFIX}-KT-01", "ip": "10.0.0.5"}])
    monkeypatch.setattr(worker_enrollment, "list_devices", lambda: [
        {"client_id": f"{PREFIX}-KT-01", "hostname": f"{PREFIX}-KT-01", "revoked_at": None},
        {"client_id": f"{PREFIX}-NS-02", "hostname": f"{PREFIX}-NS-02", "revoked_at": None}])


def test_structure_api_marks_each_device_with_agent_status(monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from mateai.interfaces.http import api_erp
    from mateai.interfaces.http.auth_dependencies import get_current_user

    _agents(monkeypatch)
    erp_db.import_directory([], [_pc(f"{PREFIX}-kt-01.corp.local"), _pc(f"{PREFIX}-NS-02"), _pc(f"{PREFIX}-NEW-09")],
                            default_dept=f"{PREFIX} Chung", apply=True)
    app = FastAPI()
    app.include_router(api_erp.router)
    app.dependency_overrides[get_current_user] = lambda: {"username": "admin", "role": "admin"}
    data = TestClient(app).get("/api/erp/structure").json()
    devs = {d["hostname"]: d["agent"] for dept in data["departments"] for d in dept["devices"] if d["hostname"].startswith(PREFIX)}
    assert devs[f"{PREFIX}-kt-01.corp.local"]["status"] == "online"      # khác hoa/thường + hậu tố miền vẫn khớp
    assert devs[f"{PREFIX}-NS-02"]["status"] == "offline" and devs[f"{PREFIX}-NEW-09"]["status"] == "none"
    cov = data["agent_coverage"]
    assert cov["online"] >= 1 and cov["offline"] >= 1 and cov["none"] >= 1 and cov["total"] >= 3


def test_domain_computers_api_marks_agent_status(monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from mateai.infrastructure.directory import domain_sync
    from mateai.interfaces.http.auth_dependencies import get_current_user
    from mateai.interfaces.http.routers import domain

    _agents(monkeypatch)
    rows = [{"hostname": f"{PREFIX.lower()}-kt-01", "os_version": "", "ip_address": "", "assigned_to": "", "synced_at": ""},
            {"hostname": f"{PREFIX}-X", "os_version": "", "ip_address": "", "assigned_to": "", "synced_at": ""}]
    monkeypatch.setattr(domain_sync.domain_manager, "get_computers", lambda limit=0: rows)
    app = FastAPI()
    app.include_router(domain.router)
    app.dependency_overrides[get_current_user] = lambda: {"username": "admin", "role": "admin"}
    body = TestClient(app).get("/api/v1/domain/computers").json()
    assert [c["agent"]["status"] for c in body["data"]] == ["online", "none"]
    assert body["agent_coverage"] == {"online": 1, "offline": 0, "revoked": 0, "none": 1, "total": 2}
