# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 Dương Thanh Lịch — VN-MateAI. See LICENSE and NOTICE.
"""
skills/ethics_tools.py
======================
Đạo đức AI — công cụ cho người quản lý (2026-10-12): báo cáo công bằng khi giao việc / đánh giá.
"""
from __future__ import annotations

from typing import Any, Dict

from core.plugin_manager import export_skill


@export_skill(
    name="get_hr_fairness_report",
    description=(
        "Báo cáo công bằng khi giao việc / đánh giá nhân viên: so khối lượng việc và tỉ lệ hoàn thành theo phòng ban, "
        "chức danh; chỉ ra người bị dồn việc (kể cả việc do AI giao), người bị bỏ sót, nhóm chênh lệch lớn — để NGƯỜI "
        "QUẢN LÝ xem lại, không kết luận thay. Người dùng thường nói: 'báo cáo công bằng', 'kiểm tra thiên kiến giao việc', "
        "'có ai bị dồn việc không', 'đánh giá phân việc có công bằng không'. English: fairness report, bias check, workload."
    ),
    parameters_schema={"type": "object", "properties": {}, "required": []},
)
def get_hr_fairness_report() -> Dict[str, Any]:
    from mateai.application.ethics.fairness import build_fairness_report
    from mateai.infrastructure.database.erp_database import erp_db
    report = build_fairness_report(erp_db.get_workload_by_employee())
    return {"status": "success", **report}
