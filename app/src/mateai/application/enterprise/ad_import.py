# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 Dương Thanh Lịch — VN-MateAI. See LICENSE and NOTICE.
"""
mateai/application/enterprise/ad_import.py
==========================================
Nhập nhân viên + máy tính đã đồng bộ từ Active Directory (kho `hr_kpi`) vào cây tổ chức ERP.

Hai kho vẫn là hai kho: đồng bộ AD chỉ làm mới bản sao AD; việc đưa vào ERP là bước CÓ CHỦ ĐÍCH, xem
trước rồi mới nhập. Quy tắc chi tiết (khớp người, phòng ban, quyền `viewer`, chạy lại an toàn) nằm ở
`ERPDatabase.import_directory`.
"""
from __future__ import annotations

from typing import Any, Dict

#: Bản sao AD có thể lớn hơn giới hạn mặc định của API liệt kê (100 / 200 dòng).
_ALL = 1_000_000


def run(*, default_department: str = "", create_departments: bool = True, apply: bool = False,
        include_employees: bool = True, include_devices: bool = True) -> Dict[str, Any]:
    from mateai.infrastructure.database.erp_database import erp_db
    from mateai.infrastructure.directory.domain_sync import domain_manager

    employees = domain_manager.get_employees(limit=_ALL) if include_employees else []
    computers = domain_manager.get_computers(limit=_ALL) if include_devices else []
    source = {"employees": len(employees), "computers": len(computers)}
    if not employees and not computers:
        return {"applied": False, "empty": True, "source": source,
                "message": "Bản sao AD đang trống — hãy bấm \"Đồng Bộ AD Ngay\" ở tab Active Directory trước."}
    res = erp_db.import_directory(employees, computers, default_dept=default_department,
                                  create_departments=create_departments, apply=apply)
    res["source"] = source
    res["empty"] = False
    return res
