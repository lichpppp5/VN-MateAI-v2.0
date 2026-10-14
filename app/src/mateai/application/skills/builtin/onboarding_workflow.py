# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 Dương Thanh Lịch — VN-MateAI. See LICENSE and NOTICE.
"""
core/skills/onboarding_workflow.py
==================================
Phase 57: Zero-Touch Employee Onboarding (Event-Driven RPA).
Tự động hóa hoàn toàn chuỗi thao tác khi có nhân sự mới gia nhập:
  1. Thêm hồ sơ vào bảng employees trong ERP.
  2. Tạo user Active Directory (AD) / script hệ thống qua CTO Agent.
  3. Khởi tạo không gian lưu trữ tài liệu cá nhân (Enterprise Workspace).
  4. Tự động phân công chuỗi Task Onboarding (Sổ tay nhân sự, Cài đặt thiết bị).
  5. Gửi thông điệp chào mừng và tài liệu hướng dẫn qua Telegram/Email.
  6. Ghi nhật ký bất biến vào audit_logs.
"""

from __future__ import annotations

import json
import logging
import re
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional

from mateai.infrastructure.database.erp_database import erp_db
from core.plugin_manager import export_skill

logger = logging.getLogger(__name__)

# Thư mục gốc dự án — một nguồn (settings.PROJECT_ROOT, đúng cả bản đóng gói),
# không suy từ vị trí file mã nguồn.
from mateai.config.loader import settings as _settings  # noqa: E402
_PROJECT_ROOT = Path(_settings.PROJECT_ROOT)


def _generate_username(name: str) -> str:
    """Tạo username chuẩn từ họ và tên tiếng Việt (ví dụ: 'Lê Văn B' -> 'levanb')."""
    import unicodedata
    nfkd = unicodedata.normalize("NFKD", name)
    no_accent = "".join([c for c in nfkd if not unicodedata.combining(c)])
    no_accent = no_accent.replace("đ", "d").replace("Đ", "D")
    clean = re.sub(r"[^a-zA-Z0-9]", "", no_accent).lower()
    return clean or "user_" + datetime.now().strftime("%f")[:4]


class OnboardingWorkflow:
    """Quản trị luồng Onboarding tự động không chạm (Zero-Touch)."""

    def onboard_new_employee(
        self,
        name: str,
        position: str,
        department_name: str = "Nhân sự",
        email: str = "",
        phone: str = "",
        role: str = "operator",
        allow_privileged_role: bool = False,
    ) -> Dict[str, Any]:
        """
        Thực thi toàn bộ chuỗi hành động tự động hóa onboarding.

        Mỗi bước được báo cáo riêng trong `steps` với trạng thái
        `executed` / `not_executed` / `failed` — không có bước nào được báo
        "thành công" khi thực tế chưa chạy.

        `allow_privileged_role=False` (mặc định) sẽ ép role về "operator".
        Chỉ server (sau khi đã kiểm tra vai trò của người gọi) mới được bật.
        """
        now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        username = _generate_username(name)

        if not email:
            email = f"{username}@vnmateai.enterprise.vn"

        # ── Chống leo thang đặc quyền ──────────────────────────────────────
        # `role` đến từ tham số LLM/HTTP nên không đáng tin. Nếu không có cờ
        # allow_privileged_role thì ép về "operator" bất kể client gửi gì.
        valid_roles = ("admin", "it_support", "operator", "viewer")
        requested_role = str(role or "operator").strip().lower()
        role_downgraded = False
        if allow_privileged_role and requested_role in valid_roles:
            norm_role = requested_role
        else:
            norm_role = "operator"
            if requested_role not in ("operator",):
                role_downgraded = True
                logger.warning(
                    "[OnboardingWorkflow] Từ chối cấp role %r cho '%s' — hạ về 'operator'. "
                    "Chỉ admin được cấp quyền đặc biệt qua server.",
                    requested_role, name,
                )

        steps: List[Dict[str, Any]] = []

        # 1. Tìm hoặc tạo phòng ban
        dept_id, created = erp_db.find_or_create_department(department_name)
        steps.append({"step": "resolve_department", "status": "executed",
                      "detail": (f"Đã tạo phòng ban mới (id={dept_id})." if created
                                 else f"Dùng phòng ban sẵn có (id={dept_id}).")})

        # 2. Thêm nhân viên vào bảng employees
        emp_id = erp_db.add_employee(dept_id, name.strip(), position.strip(), email.strip(), phone.strip(), norm_role)
        steps.append({"step": "create_employee_record", "status": "executed",
                      "detail": f"Đã tạo hồ sơ nhân viên EMP-{emp_id:04d} với role='{norm_role}'."})

        # 3. Tạo thư mục lưu trữ cá nhân cho nhân viên
        emp_workspace = _PROJECT_ROOT / "storage" / "workspaces" / username
        try:
            emp_workspace.mkdir(parents=True, exist_ok=True)
            readme_file = emp_workspace / "WELCOME.txt"
            readme_file.write_text(
                f"Chào mừng {name} ({position}) gia nhập VN-MateAI Enterprise!\n"
                f"Mã nhân viên: EMP-{emp_id:04d}\n"
                f"Username AD: {username}\n"
                f"Email doanh nghiệp: {email}\n"
                f"Ngày tiếp nhận: {now_str}\n",
                encoding="utf-8",
            )
            steps.append({"step": "create_workspace", "status": "executed",
                          "detail": f"Đã tạo workspace: {emp_workspace}"})
        except Exception as exc:
            logger.error("[OnboardingWorkflow] Không tạo được workspace cho %s: %s", username, exc)
            steps.append({"step": "create_workspace", "status": "failed", "detail": str(exc)})

        # 4. Cấp tài khoản Active Directory
        #
        # QUAN TRỌNG: bước này CHƯA ĐƯỢC THỰC THI. Hệ thống chỉ *sinh ra* câu lệnh
        # PowerShell để quản trị viên chạy tay trên Domain Controller. Trước đây
        # workflow trả về ad_script cùng thông điệp "onboarding thành công" khiến
        # CEO tin rằng tài khoản AD đã được tạo — nhưng không có bước nào gọi
        # AD. Nay báo cáo trung thực là "not_executed".
        #
        # Để cấp tài khoản thật cần: (1) kết nối tới Domain Controller qua
        # ldaps/winrm, (2) đi qua cổng HITL vì đây là hành động rủi ro cao.
        ad_script = (
            f"New-ADUser -Name '{name}' -SamAccountName '{username}' "
            f"-UserPrincipalName '{username}@vnmateai.enterprise.vn' "
            f"-Department '{department_name}' -Title '{position}' -Enabled $true"
        )
        steps.append({
            "step": "provision_ad_account",
            "status": "not_executed",
            "detail": (
                "Hệ thống chưa có kết nối tới Active Directory nên KHÔNG tạo tài khoản AD. "
                "Dưới đây là lệnh quản trị viên cần chạy tay trên Domain Controller."
            ),
            "powershell_command": ad_script,
        })

        # 5. Phân công 3 Task Onboarding chuẩn
        due_date_3d = (datetime.now() + timedelta(days=3)).strftime("%Y-%m-%d 17:30:00")
        onboarding_tasks = [
            f"Nghiên cứu Sổ tay văn hóa doanh nghiệp & Quy chế bảo mật thông tin (EMP-{emp_id:04d})",
            f"Tiếp nhận bàn giao thiết bị làm việc, kích hoạt tài khoản AD '{username}'",
            f"Họp định hướng (Orientation) 1-on-1 cùng Trưởng phòng {department_name}",
        ]

        created_tasks = []
        task_errors: List[str] = []
        for t_title in onboarding_tasks:
            try:
                t_res = erp_db.create_erp_task(
                    dept_id=dept_id,
                    assignee_id=emp_id,
                    title=t_title,
                    due_date=due_date_3d,
                    created_by_ai=1,
                    resolution_notes="Tự động phân bổ bởi Zero-Touch Onboarding Workflow (Phase 57)",
                )
                created_tasks.append(t_res)
            except Exception as exc:
                task_errors.append(f"{t_title}: {exc}")
                logger.error("[OnboardingWorkflow] Không tạo được task cho %s: %s", name, exc)

        if task_errors:
            steps.append({"step": "assign_onboarding_tasks", "status": "partial",
                          "detail": f"Tạo được {len(created_tasks)}/{len(onboarding_tasks)} task. Lỗi: {'; '.join(task_errors)}"})
        else:
            steps.append({"step": "assign_onboarding_tasks", "status": "executed",
                          "detail": f"Đã phân bổ {len(created_tasks)} task hội nhập (hạn 3 ngày)."})

        # 6. Gửi thông báo chào mừng qua Telegram / Bot Gateway
        welcome_msg = (
            f"🎉 [CHÀO ĐÓN NHÂN SỰ MỚI - ZERO-TOUCH ONBOARDING]\n"
            f"Chào mừng **{name}** gia nhập đại gia đình VN-MateAI!\n"
            f"• Vị trí: {position} | Phòng ban: {department_name}\n"
            f"• Mã NV: EMP-{emp_id:04d} | Username dự kiến: `{username}`\n"
            f"• Email: {email}\n"
            f"• Workspace & {len(created_tasks)} công việc hội nhập đã được tạo tự động."
        )

        try:
            from mateai.interfaces.telegram.telegram_gateway import telegram_gateway
            if telegram_gateway.send_incident_alert(welcome_msg):
                steps.append({"step": "notify_telegram", "status": "executed",
                              "detail": "Đã gửi tin nhắn chào mừng qua Telegram."})
            else:
                steps.append({"step": "notify_telegram", "status": "not_executed",
                              "detail": "Telegram chưa cấu hình hoặc gửi thất bại (không chặn onboarding)."})
        except Exception as exc:
            steps.append({"step": "notify_telegram", "status": "failed", "detail": str(exc)})

        # 7. Ghi Audit Log bất biến (Phase 48 & 57)
        erp_db.log_audit_action(
            employee_id="ai_onboarding_agent",
            action_type="ZERO_TOUCH_ONBOARDING",
            payload=f"Created Employee ID={emp_id}, Name={name}, Username={username}, Dept={department_name}, "
                    f"Role={norm_role}, AD_Account=NOT_EXECUTED, "
                    f"RoleDowngraded={role_downgraded}, Steps={json.dumps(steps, ensure_ascii=False)}",
            status="success",
        )

        not_executed = [s for s in steps if s["status"] == "not_executed"]
        failed = [s for s in steps if s["status"] == "failed"]
        overall = "partial" if (not_executed or failed or task_errors) else "success"

        logger.info(
            "[OnboardingWorkflow] Onboarding %s cho %s (ID: %d). Trạng thái=%s, chưa thực thi=%d, lỗi=%d.",
            "hoàn tất" if overall == "success" else "một phần",
            name, emp_id, overall, len(not_executed), len(failed),
        )

        return {
            "status": overall,
            "message": welcome_msg,
            "employee_id": emp_id,
            "employee_name": name,
            "username": username,
            "email": email,
            "assigned_role": norm_role,
            "role_downgraded": role_downgraded,
            "department": department_name,
            "workspace_dir": str(emp_workspace),
            "ad_powershell_command": ad_script,
            "tasks_assigned": created_tasks,
            "steps": steps,
            "not_executed_steps": [s["step"] for s in not_executed],
            "note": (
                "Tài khoản Active Directory CHƯA được tạo — hệ thống chỉ sinh lệnh PowerShell "
                "để quản trị viên chạy tay trên Domain Controller. Xem steps[].status."
            ) if not_executed else "",
        }


onboarding_workflow = OnboardingWorkflow()


# ── AI Skills Export ─────────────────────────────────────────────────────────

@export_skill(
    name="zero_touch_onboard_employee",
    data_classification="CONFIDENTIAL",
    description="Quy trình tự động hóa Onboarding nhân sự mới không chạm (Zero-Touch RPA): tạo hồ sơ trong ERP, khởi tạo thư mục Workspace, gán bộ task hội nhập, gửi Telegram chào mừng và ghi nhật ký bất biến Audit Log. LƯU Ý: hệ thống KHÔNG tạo tài khoản Active Directory (chỉ sinh lệnh PowerShell để admin chạy tay) và KHÔNG tạo mailbox Google Workspace/Exchange. Kết quả trả về kèm mảng `steps` đánh dấu từng bước là executed / not_executed / failed.",
    parameters_schema={
        "type": "object",
        "properties": {
            "name": {
                "type": "string",
                "description": "Họ và tên nhân viên mới (ví dụ: 'Lê Văn Bình', 'Nguyễn Thị Mai').",
            },
            "position": {
                "type": "string",
                "description": "Vị trí chức danh (ví dụ: 'Chuyên viên Kế toán', 'Kỹ sư AI').",
            },
            "department_name": {
                "type": "string",
                "description": "Tên phòng ban tiếp nhận (ví dụ: 'Tài chính - Kế toán', 'Phòng Kỹ thuật').",
                "default": "Nhân sự",
            },
            "email": {
                "type": "string",
                "description": "Email cá nhân hoặc email công ty cấp (nếu để trống hệ thống tự sinh).",
                "default": "",
            },
            "phone": {
                "type": "string",
                "description": "Số điện thoại liên hệ.",
                "default": "",
            },
            "role": {
                "type": "string",
                "enum": ["operator", "viewer"],
                "description": "Phân quyền hệ thống. Chỉ 'operator'/'viewer' được phép qua skill này — các quyền đặc biệt ('admin', 'it_support') chỉ admin hệ thống mới cấp được. Mọi giá trị khác sẽ bị hạ về 'operator'.",
                "default": "operator",
            },
        },
        "required": ["name", "position"],
    },
)
def zero_touch_onboard_employee(
    name: str,
    position: str,
    department_name: str = "Nhân sự",
    email: str = "",
    phone: str = "",
    role: str = "operator",
) -> Dict[str, Any]:
    """Khởi động quy trình Onboarding nhân viên mới không chạm.

    Luôn gọi với allow_privileged_role=False: skill này có thể bị LLM kích hoạt
    nên tuyệt đối không được tin tham số `role` do mô hình sinh ra.
    """
    return onboarding_workflow.onboard_new_employee(
        name=name,
        position=position,
        department_name=department_name,
        email=email,
        phone=phone,
        role=role,
        allow_privileged_role=False,
    )
