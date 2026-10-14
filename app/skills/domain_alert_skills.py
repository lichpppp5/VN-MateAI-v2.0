# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 Dương Thanh Lịch — VN-MateAI. See LICENSE and NOTICE.
"""
skills/domain_alert_skills.py
==============================
Active Directory & Telegram AI Tool Skills for VN-MateAI — Phase 18.

Tools provided to the AI engine (auto-discovered by PluginManager):
  1. lookup_domain_info   — Search employees/computers from SQLite local cache.
  2. send_telegram_message — Send an alert to incident group or a specific admin.

Design rules:
  - LLM reads from pre-synced SQLite ONLY — never triggers PowerShell directly.
  - Telegram sends are non-blocking and fault-tolerant.
"""

from __future__ import annotations

import logging
from typing import Any, Dict

from core.plugin_manager import export_skill

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Skill 1: Lookup Domain Info
# ---------------------------------------------------------------------------

@export_skill(
    name="lookup_domain_info",
    description=(
        "Tra cứu thông tin nhân sự và máy tính trong hệ thống nội bộ doanh nghiệp từ database đã đồng bộ AD. "
        "Dùng khi cần tìm số điện thoại nhân viên, phòng ban, chức vụ, email, hoặc tìm xem ai đang dùng máy tính nào, "
        "hoặc máy tính có địa chỉ IP nào thuộc về ai. "
        "Ví dụ: 'Số điện thoại nhân viên Nguyễn Văn A', 'Máy tính PC-KETOAN-01 của ai', 'Danh sách máy Windows 11'."
    ),
    parameters_schema={
        "type": "object",
        "properties": {
            "query_string": {
                "type": "string",
                "description": "Chuỗi tìm kiếm — tên nhân viên, tên phòng ban, hostname, địa chỉ IP, hoặc từ khóa liên quan.",
            }
        },
        "required": ["query_string"],
    },
)
def lookup_domain_info(query_string: str) -> Dict[str, Any]:
    """
    Search the local SQLite AD cache for employees or computers matching a query.
    Safe for LLM use — reads pre-synced data only, no PowerShell or network calls.
    """
    if not query_string or not query_string.strip():
        return {
            "status": "error",
            "message": "Vui lòng cung cấp từ khóa tìm kiếm (tên nhân viên, hostname, IP...).",
        }

    try:
        from mateai.infrastructure.directory.domain_sync import domain_manager
        result = domain_manager.lookup_domain_info(query_string.strip())

        total = result.get("total_found", 0)
        emps = result.get("employees", [])
        comps = result.get("computers", [])

        if total == 0:
            return {
                "status": "success",
                "message": (
                    f"Không tìm thấy kết quả nào khớp với '{query_string}'. "
                    "Hãy thử từ khóa khác hoặc đồng bộ dữ liệu AD trước."
                ),
                "total_found": 0,
                "employees": [],
                "computers": [],
            }

        emp_summary = [
            {
                "Tên": e.get("full_name", ""),
                "Phòng ban": e.get("department", ""),
                "Chức vụ": e.get("title", ""),
                "Email": e.get("email", ""),
                "SĐT": e.get("phone", ""),
                "Tài khoản": e.get("sam_account_name", ""),
            }
            for e in emps
        ]

        comp_summary = [
            {
                "Hostname": c.get("hostname", ""),
                "Hệ điều hành": c.get("os_version", ""),
                "IP": c.get("ip_address", ""),
                "Sử dụng bởi": c.get("assigned_to", ""),
            }
            for c in comps
        ]

        return {
            "status": "success",
            "query": query_string,
            "total_found": total,
            "employees": emp_summary,
            "computers": comp_summary,
            "summary": (
                f"Tìm thấy {len(emps)} nhân viên và {len(comps)} máy tính "
                f"khớp với từ khóa '{query_string}'."
            ),
        }

    except Exception as exc:
        logger.error("[DomainAlertSkills] lookup_domain_info error: %s", exc)
        return {"status": "error", "message": f"Lỗi truy vấn dữ liệu nội bộ: {exc}"}


# ---------------------------------------------------------------------------
# Skill 2: Send Telegram Message
# ---------------------------------------------------------------------------

@export_skill(
    name="send_telegram_message",
    description=(
        "Gửi tin nhắn cảnh báo sự cố hoặc thông báo chủ động lên Telegram cho quản trị viên. "
        "Dùng khi phát hiện sự cố nghiêm trọng (CPU quá cao, dịch vụ down, bảo mật bị xâm phạm), "
        "hoặc khi người dùng yêu cầu thông báo cho ai đó qua Telegram. "
        "Target 'incident_group' gửi vào nhóm sự cố chung, 'all_admins' gửi cho tất cả admin, "
        "hoặc nhập chat_id cụ thể của từng admin."
    ),
    parameters_schema={
        "type": "object",
        "properties": {
            "target": {
                "type": "string",
                "description": (
                    "Đích gửi: 'incident_group' để gửi vào nhóm sự cố, "
                    "'all_admins' để gửi cho tất cả admin, "
                    "hoặc chat_id Telegram cụ thể của admin."
                ),
                "default": "incident_group",
            },
            "message": {
                "type": "string",
                "description": "Nội dung tin nhắn cảnh báo, tối đa 4000 ký tự.",
            },
        },
        "required": ["message"],
    },
)
def send_telegram_message(message: str, target: str = "incident_group") -> Dict[str, Any]:
    """
    Send an alert or notification to Telegram via the gateway.
    Non-blocking and fault-tolerant.
    """
    if not message or not message.strip():
        return {"status": "error", "message": "Nội dung tin nhắn không được để trống."}

    try:
        from mateai.interfaces.telegram.telegram_gateway import telegram_gateway

        if target == "all_admins":
            ok = telegram_gateway.send_to_all_admins(message.strip())
        else:
            ok = telegram_gateway.send_incident_alert(message.strip(), target=target)

        if ok:
            return {
                "status": "success",
                "message": f"Đã gửi thành công tin nhắn Telegram đến '{target}'.",
                "target": target,
            }
        else:
            return {
                "status": "warning",
                "message": (
                    "Lệnh gửi đã được tiếp nhận nhưng Telegram chưa được cấu hình Bot Token. "
                    "Vui lòng nhập Bot Token trong Tab Cấu Hình Hệ Thống."
                ),
            }

    except Exception as exc:
        logger.error("[DomainAlertSkills] send_telegram_message error: %s", exc)
        return {"status": "error", "message": f"Lỗi khi gửi Telegram: {exc}"}
