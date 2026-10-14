# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 Dương Thanh Lịch — VN-MateAI. See LICENSE and NOTICE.
"""
core/skills/erp_organization.py
===============================
Kỹ năng tra cứu dữ liệu tổ chức doanh nghiệp (ERP Organization Skill) cho VN-MateAI.
Phase 47: ERP Structure & Bulk Data Import Engine.

Phase 6 (2026-10): trước đây hàm này không được đăng ký — LLM chỉ thấy tool qua
một schema viết tay trong llm_engine (ERP_ORGANIZATION_TOOLS) và cổng thực thi
gọi thẳng erp_db. Nay đăng ký bằng @export_skill như mọi skill khác.
"""

from typing import Any, Dict

from mateai.infrastructure.database.erp_database import erp_db

try:
    from core.plugin_manager import export_skill
except Exception:  # pragma: no cover
    def export_skill(*args, **kwargs):
        def decorator(fn):
            return fn
        return decorator


@export_skill(
    name="query_organization_data",
    description=(
        "Tra cứu cơ sở dữ liệu tổ chức ERP của doanh nghiệp: tìm kiếm thông tin phòng ban, "
        "nhân sự (họ tên, email, SĐT, chức vụ), thiết bị/máy tính (hostname, địa chỉ IP, "
        "người sử dụng), công việc (task, hạn chót, trạng thái) và sổ sách/hồ sơ."
    ),
    parameters_schema={
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": (
                    "Từ khóa hoặc câu hỏi cần tra cứu (ví dụ: '192.168.1.10', 'Nguyễn Văn A', "
                    "'Phòng Nhân Sự', 'danh sách máy chủ')."
                ),
            },
        },
        "required": ["query"],
    },
)
def query_organization_data(query: str) -> Dict[str, Any]:
    """
    Tra cứu thông tin cơ sở dữ liệu tổ chức ERP doanh nghiệp:
    Tìm kiếm phòng ban, nhân sự, máy tính/thiết bị theo IP/hostname, công việc và hồ sơ tài liệu.
    """
    try:
        data = erp_db.query_organization(query)
        return {
            "status": "success",
            "query": query,
            "data": data,
        }
    except Exception as exc:
        return {
            "status": "error",
            "query": query,
            "error": str(exc),
        }
