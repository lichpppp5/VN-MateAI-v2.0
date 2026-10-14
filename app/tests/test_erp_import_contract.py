# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 Dương Thanh Lịch — VN-MateAI. See LICENSE and NOTICE.
"""
tests/test_erp_import_contract.py
=================================
Supervisor Phase 10 (§198): nhập Excel ERP + file mẫu là use case ở
`application/enterprise/erp_import.py`; `api_erp.py` chỉ nhận file / trả kết quả.

Cố định hành vi + lỗi thật đã sửa:
  - đọc Excel (pandas) + nhập hàng loạt chạy NGAY trong hàm async — file lớn đứng event
    loop vài giây; nay chạy ngoài loop;
  - nhận `.csv` / `.xls` nhưng đọc bằng openpyxl (chỉ đọc được .xlsx) → luôn hỏng với
    lỗi khó hiểu; nay từ chối ngay, nói rõ cần .xlsx.
Không gọi mạng; `erp_db.bulk_import` được thay để không ghi DB thật.
"""
from __future__ import annotations

import io

import pandas as pd
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient


def _client(role="admin"):
    import mateai.interfaces.http.api_erp as erp
    from mateai.interfaces.http.auth_dependencies import get_current_user
    app = FastAPI()
    app.include_router(erp.router)
    app.dependency_overrides[get_current_user] = lambda: {"username": f"{role}_u", "role": role}
    return TestClient(app)


def _xlsx(sheets):
    buf = io.BytesIO()
    with pd.ExcelWriter(buf, engine="openpyxl") as w:
        for name, rows in sheets.items():
            pd.DataFrame(rows).to_excel(w, sheet_name=name, index=False)
    return buf.getvalue()


@pytest.fixture
def captured(monkeypatch):
    from mateai.application.security import safety_guard
    from mateai.infrastructure.database.erp_database import erp_db
    got, audits = {}, []

    def fake_bulk(**kw):
        got.update(kw)
        return {"message": "ok", "stats": {"departments": len(kw["departments_data"])}}

    monkeypatch.setattr(erp_db, "bulk_import", fake_bulk)
    monkeypatch.setattr(safety_guard.security_engine, "log_audit", lambda *a, **k: audits.append(a))
    return got, audits


def test_template_has_five_header_only_sheets():
    r = _client("viewer").get("/api/erp/template")
    assert r.status_code == 200 and "attachment" in r.headers["content-disposition"]
    xls = pd.ExcelFile(io.BytesIO(r.content), engine="openpyxl")
    assert xls.sheet_names == ["PhongBan", "NhanVien", "MayTinh", "CongViec", "SoSach"]
    assert all(len(pd.read_excel(xls, sheet_name=s)) == 0 for s in xls.sheet_names)   # không dữ liệu bịa
    assert list(pd.read_excel(xls, sheet_name="NhanVien").columns) == ["PhongBan", "HoTen", "ChucVu", "Email", "SoDienThoai"]


def test_import_maps_sheets_case_insensitively_and_audits(captured):
    got, audits = captured
    data = _xlsx({"phongban": [{"TenPhongBan": "Kế toán", "MoTa": ""}],
                  "Nhân Viên": [{"PhongBan": "Kế toán", "HoTen": "A", "ChucVu": "NV", "Email": None, "SoDienThoai": ""}]})
    r = _client("manager").post("/api/erp/import", files={"file": ("to-chuc.xlsx", data)})
    assert r.status_code == 200 and r.json()["status"] == "success", r.text
    assert got["departments_data"] == [{"TenPhongBan": "Kế toán", "MoTa": ""}]
    assert got["employees_data"][0]["Email"] == ""                      # ô trống -> "" như cũ
    assert got["devices_data"] == [] and got["records_data"] == []
    assert any(a[1] == "erp_import" and a[0] == "manager_u" for a in audits)


@pytest.mark.parametrize("name", ["du-lieu.csv", "cu.xls", "x.txt"])
def test_only_xlsx_is_accepted(captured, name):
    got, _ = captured
    r = _client().post("/api/erp/import", files={"file": (name, b"a,b\n1,2")})
    assert r.status_code == 400 and ".xlsx" in r.json()["detail"]
    assert got == {}


def test_workbook_without_known_sheets_is_rejected(captured):
    got, _ = captured
    r = _client().post("/api/erp/import", files={"file": ("x.xlsx", _xlsx({"Khac": [{"a": 1}]}))})
    assert r.status_code == 400 and "PhongBan" in r.json()["detail"]
    assert got == {}


def test_row_error_from_bulk_import_is_reported_not_raised(monkeypatch, captured):
    from mateai.infrastructure.database.erp_database import erp_db

    def bad(**kw):
        raise ValueError("Dòng 3 sheet NhanVien: thiếu HoTen")

    monkeypatch.setattr(erp_db, "bulk_import", bad)
    r = _client().post("/api/erp/import", files={"file": ("x.xlsx", _xlsx({"PhongBan": [{"TenPhongBan": "A"}]}))})
    assert r.status_code == 200 and r.json() == {"status": "error", "message": "Dòng 3 sheet NhanVien: thiếu HoTen"}


def test_viewer_cannot_import():
    assert _client("viewer").post("/api/erp/import", files={"file": ("x.xlsx", b"x")}).status_code == 403
