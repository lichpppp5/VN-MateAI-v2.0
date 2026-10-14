# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 Dương Thanh Lịch — VN-MateAI. See LICENSE and NOTICE.
"""
core/skills/business_tools.py
==============================
Phase 56: Enterprise Business & Financial Skills for VN-MateAI.
Cung cấp các công cụ quản lý Sổ quỹ tài chính, Chấm công và Báo cáo điều hành cho C-Level.
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any, Dict, Optional

from mateai.infrastructure.database.erp_database import erp_db
from core.plugin_manager import export_skill

logger = logging.getLogger(__name__)


@export_skill(
    name="record_expense",
    data_classification="CONFIDENTIAL",
    description="Ghi nhận khoản chi tiêu (chi phí) vào Sổ quỹ tài chính doanh nghiệp. Dùng khi CEO hoặc quản lý ra lệnh chi tiền hoặc báo cáo chi phí.",
    parameters_schema={
        "type": "object",
        "properties": {
            "amount": {
                "type": "number",
                "description": "Số tiền chi tiêu tính bằng VND (ví dụ: 15000000, 500000).",
            },
            "reason": {
                "type": "string",
                "description": "Lý do hoặc mô tả chi tiết khoản chi (ví dụ: 'Mua bàn ghế văn phòng', 'Chi phí server AWS').",
            },
            "category": {
                "type": "string",
                "description": "Phân loại chi phí: 'Hạ tầng Cloud & Server', 'Lương & Thưởng', 'Văn phòng phẩm & Tiện ích', 'Nghiên cứu & Phát triển', 'Tiếp khách', 'Marketing', hoặc 'Khác'.",
                "default": "Chi phí hoạt động",
            },
            "created_by": {
                "type": "string",
                "description": "Người hoặc vai trò thực hiện ghi nhận (mặc định: 'CEO').",
                "default": "CEO",
            },
        },
        "required": ["amount", "reason"],
    },
)
def record_expense(
    amount: float,
    reason: str,
    category: str = "Chi phí hoạt động",
    created_by: str = "CEO",
) -> Dict[str, Any]:
    """Ghi khoản chi vào sổ quỹ doanh nghiệp."""
    try:
        val = float(amount)
        if val <= 0:
            return {"status": "error", "message": "Số tiền chi phải lớn hơn 0."}

        rec = erp_db.add_finance_record(
            finance_type="expense",
            amount=val,
            category=category or "Chi phí hoạt động",
            description=reason,
            created_by=created_by,
        )
        return {
            "status": "success",
            "message": f"Đã ghi nhận khoản chi {val:,.0f} VND vào sổ quỹ. Danh mục: {category}. Nội dung: {reason}.",
            "record": rec,
        }
    except Exception as exc:
        logger.error("Lỗi record_expense: %s", exc)
        return {"status": "error", "message": f"Không thể ghi nhận khoản chi: {str(exc)}"}


@export_skill(
    name="record_income",
    data_classification="CONFIDENTIAL",
    description="Ghi nhận khoản thu (doanh thu, thanh toán hợp đồng) vào Sổ quỹ doanh nghiệp.",
    parameters_schema={
        "type": "object",
        "properties": {
            "amount": {
                "type": "number",
                "description": "Số tiền thu tính bằng VND.",
            },
            "source": {
                "type": "string",
                "description": "Nguồn thu hoặc khách hàng thanh toán (ví dụ: 'Thanh toán đợt 2 Hợp đồng công ty ABC').",
            },
            "category": {
                "type": "string",
                "description": "Danh mục: 'Doanh thu hợp đồng', 'Dịch vụ bảo trì', 'Tư vấn chuyển đổi số', hoặc 'Khác'.",
                "default": "Doanh thu hợp đồng",
            },
            "created_by": {
                "type": "string",
                "description": "Người ghi nhận (mặc định: 'CEO').",
                "default": "CEO",
            },
        },
        "required": ["amount", "source"],
    },
)
def record_income(
    amount: float,
    source: str,
    category: str = "Doanh thu hợp đồng",
    created_by: str = "CEO",
) -> Dict[str, Any]:
    """Ghi khoản thu vào sổ quỹ doanh nghiệp."""
    try:
        val = float(amount)
        if val <= 0:
            return {"status": "error", "message": "Số tiền thu phải lớn hơn 0."}

        rec = erp_db.add_finance_record(
            finance_type="income",
            amount=val,
            category=category or "Doanh thu hợp đồng",
            description=source,
            created_by=created_by,
        )
        return {
            "status": "success",
            "message": f"Đã ghi nhận khoản thu {val:,.0f} VND vào sổ quỹ. Nguồn: {source}.",
            "record": rec,
        }
    except Exception as exc:
        logger.error("Lỗi record_income: %s", exc)
        return {"status": "error", "message": f"Không thể ghi nhận khoản thu: {str(exc)}"}


@export_skill(
    name="get_financial_summary",
    data_classification="CONFIDENTIAL",
    description="Lấy báo cáo tổng hợp dòng tiền (Cashflow, Thu, Chi, Số dư quỹ, Tốc độ đốt tiền Burn Rate, Runway) của doanh nghiệp.",
    parameters_schema={
        "type": "object",
        "properties": {
            "days": {
                "type": "integer",
                "description": "Số ngày phân tích gần nhất (mặc định: 30 ngày).",
                "default": 30,
            }
        },
    },
)
def get_financial_summary(days: int = 30) -> Dict[str, Any]:
    """Lấy tóm tắt tình hình tài chính doanh nghiệp."""
    try:
        summary = erp_db.get_financial_summary(days=days)
        total_income = summary["total_income"]
        total_expense = summary["total_expense"]
        net_balance = summary["net_balance"]
        runway = summary["runway_days"]

        formatted_msg = (
            f"📊 BÁO CÁO TÀI CHÍNH TỔNG QUAN ({days} ngày qua):\n"
            f"• Tổng thu: {total_income:,.0f} VND\n"
            f"• Tổng chi: {total_expense:,.0f} VND\n"
            f"• Số dư ròng: {net_balance:,.0f} VND\n"
            f"• Tốc độ chi tiêu trung bình/ngày: {summary['daily_burn_rate']:,.0f} VND\n"
            f"• Dự báo Runway (ngày hoạt động an toàn): {runway} ngày\n"
        )
        if summary.get("is_critical_burn"):
            formatted_msg += "⚠️ CẢNH BÁO ĐỎ: Dòng tiền đang thâm hụt nhanh! Cần kiểm soát chi phí ngay."

        return {
            "status": "success",
            "message": formatted_msg,
            "data": summary,
        }
    except Exception as exc:
        logger.error("Lỗi get_financial_summary: %s", exc)
        return {"status": "error", "message": f"Không thể lấy báo cáo tài chính: {str(exc)}"}


@export_skill(
    name="record_attendance_skill",
    description="Ghi nhận lượt chấm công hoặc điểm danh cho nhân viên.",
    parameters_schema={
        "type": "object",
        "properties": {
            "employee_identifier": {
                "type": "string",
                "description": "Tên, email hoặc ID của nhân viên cần điểm danh.",
            },
            "status": {
                "type": "string",
                "description": "Trạng thái: 'present' (Có mặt), 'late' (Đi muộn), 'early_leave' (Về sớm), 'remote' (Làm từ xa).",
                "default": "present",
            },
            "check_out": {
                "type": "boolean",
                "description": "Nếu là điểm danh ra về (Check-out) thì đặt true, ngược lại vào ca là false.",
                "default": False,
            },
        },
        "required": ["employee_identifier"],
    },
)
def record_attendance_skill(
    employee_identifier: str,
    status: str = "present",
    check_out: bool = False,
) -> Dict[str, Any]:
    """Chấm công cho nhân viên theo tên hoặc ID."""
    try:
        emp = erp_db.get_employee_by_identifier(employee_identifier)
        if not emp:
            return {"status": "error", "message": f"Không tìm thấy nhân viên: '{employee_identifier}'."}

        now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        if check_out:
            rec = erp_db.record_attendance(
                employee_id=emp["id"],
                check_in_time=now_str,
                check_out_time=now_str,
                status=status,
            )
            action_text = "Check-out (Ra về)"
        else:
            rec = erp_db.record_attendance(
                employee_id=emp["id"],
                check_in_time=now_str,
                status=status,
            )
            action_text = "Check-in (Vào ca)"

        return {
            "status": "success",
            "message": f"Đã ghi nhận {action_text} cho nhân viên {emp['name']} ({emp.get('position', 'Nhân viên')}) lúc {now_str}. Trạng thái: {status}.",
            "record": rec,
        }
    except Exception as exc:
        logger.error("Lỗi record_attendance_skill: %s", exc)
        return {"status": "error", "message": f"Lỗi chấm công: {str(exc)}"}


@export_skill(
    name="get_attendance_report",
    description="Tra cứu danh sách chấm công hôm nay hoặc theo ngày cụ thể.",
    parameters_schema={
        "type": "object",
        "properties": {
            "date_str": {
                "type": "string",
                "description": "Ngày cần tra cứu dạng YYYY-MM-DD (bỏ trống để lấy hôm nay).",
            }
        },
    },
)
def get_attendance_report(date_str: str = "") -> Dict[str, Any]:
    """Lấy danh sách chấm công."""
    try:
        target_date = date_str.strip() or datetime.now().strftime("%Y-%m-%d")
        records = erp_db.get_attendance(date_str=target_date)
        return {
            "status": "success",
            "date": target_date,
            "total_records": len(records),
            "records": records,
            "message": f"Tìm thấy {len(records)} lượt chấm công ngày {target_date}.",
        }
    except Exception as exc:
        logger.error("Lỗi get_attendance_report: %s", exc)
        return {"status": "error", "message": f"Không thể lấy báo cáo chấm công: {str(exc)}"}


@export_skill(
    name="get_executive_leaderboard",
    description="Lấy bảng xếp hạng thi đua công việc (Leaderboard) toàn công ty, vinh danh nhân viên hoàn thành nhiều task nhất.",
    parameters_schema={
        "type": "object",
        "properties": {
            "limit": {
                "type": "integer",
                "description": "Số lượng nhân viên top đầu cần lấy (mặc định: 5).",
                "default": 5,
            }
        },
    },
)
def get_executive_leaderboard(limit: int = 5) -> Dict[str, Any]:
    """Lấy bảng xếp hạng năng suất công việc."""
    try:
        leaderboard = erp_db.get_task_leaderboard(limit=limit)
        msg_lines = ["🏆 BẢNG XẾP HẠNG NĂNG SUẤT DOANH NGHIỆP:"]
        for r in leaderboard:
            msg_lines.append(
                f"#{r['rank']} {r['name']} ({r.get('dept_name', 'Chung')} - {r.get('position', 'NV')}): "
                f"Hoàn thành {r['completed_tasks']} việc / Tổng {r['total_assigned']} việc."
            )
        return {
            "status": "success",
            "message": "\n".join(msg_lines),
            "leaderboard": leaderboard,
        }
    except Exception as exc:
        logger.error("Lỗi get_executive_leaderboard: %s", exc)
        return {"status": "error", "message": f"Lỗi lấy bảng xếp hạng: {str(exc)}"}
