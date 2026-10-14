# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 Dương Thanh Lịch — VN-MateAI. See LICENSE and NOTICE.
"""
skills/itsm_skills.py
=====================
Phase 48 — ITSM (IT Service Management) Skills cho VN-MateAI.

Provides:
  - create_system_ticket : Tạo phiếu công việc ITSM khi phát hiện sự cố,
                           khi AI tự xử lý xong vấn đề, hoặc khi IT thực hiện bất kỳ thay đổi.
  - update_ticket_status : Cập nhật trạng thái + ghi chú giải quyết cho phiếu đã tồn tại.
  - get_tickets          : Truy vấn danh sách phiếu (lọc theo trạng thái / phòng ban).
  - generate_daily_report: Tổng hợp nhật ký kiểm toán + phiếu công việc trong ngày,
                           trả về báo cáo văn bản + JSON để Dashboard hiển thị.

Design rules:
  - Mỗi hành động ghi DB đều tuân thủ ACID (BEGIN IMMEDIATE → commit / rollback).
  - Mọi thao tác ghi đều được audit bởi write_audit_log (immutable trail).
  - Hàm generate_daily_report là pure-read, không ghi DB.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

from core.plugin_manager import export_skill

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────────────────────
# Helper: lazy imports để tránh circular import khi module load
# ─────────────────────────────────────────────────────────────────────────────

def _get_erp_db():
    from mateai.infrastructure.database.erp_database import erp_db
    return erp_db


def _get_security_guard():
    from mateai.application.security.security_guard import security_guard
    return security_guard


# ─────────────────────────────────────────────────────────────────────────────
# SKILL 1: create_system_ticket
# ─────────────────────────────────────────────────────────────────────────────

@export_skill(
    name="create_system_ticket",
    description=(
        "Tạo phiếu công việc ITSM khi AI phát hiện sự cố, hoàn tất xử lý hệ thống, "
        "hoặc khi nhân viên IT thực hiện thay đổi cần ghi nhận. "
        "Phiếu được lưu bất biến vào audit trail và hệ thống ERP."
    ),
    parameters_schema={
        "type": "object",
        "properties": {
            "title": {
                "type": "string",
                "description": "Tiêu đề ngắn gọn mô tả công việc hoặc sự cố (tối đa 200 ký tự).",
            },
            "description": {
                "type": "string",
                "description": "Mô tả chi tiết vấn đề, nguyên nhân, hành động đã thực hiện và kết quả.",
            },
            "category": {
                "type": "string",
                "enum": ["incident", "change", "maintenance", "ai_action", "alert", "other"],
                "description": (
                    "Loại phiếu: 'incident'=sự cố, 'change'=thay đổi hệ thống, "
                    "'maintenance'=bảo trì định kỳ, 'ai_action'=AI tự xử lý, "
                    "'alert'=cảnh báo, 'other'=khác."
                ),
            },
            "severity": {
                "type": "string",
                "enum": ["critical", "high", "medium", "low", "info"],
                "description": "Mức độ nghiêm trọng: critical > high > medium > low > info.",
            },
            "assignee_name": {
                "type": "string",
                "description": "Tên nhân viên phụ trách (tùy chọn). Nếu bỏ trống, phiếu ở trạng thái 'unassigned'.",
            },
            "dept_name": {
                "type": "string",
                "description": "Tên phòng ban liên quan (tùy chọn, ví dụ: 'Phòng IT', 'Kế Toán').",
            },
            "created_by_ai": {
                "type": "boolean",
                "description": "True nếu phiếu được tạo tự động bởi AI (không phải do người dùng nhập tay).",
            },
            "resolution_notes": {
                "type": "string",
                "description": "Ghi chú giải quyết nếu vấn đề đã được xử lý xong ngay lúc tạo phiếu.",
            },
            "caller_id": {
                "type": "string",
                "description": "ID nhân viên / user đang ra lệnh. Dùng để ghi vào audit trail.",
            },
        },
        "required": ["title", "category", "severity"],
    },
)
def create_system_ticket(
    title: str,
    category: str = "other",
    severity: str = "medium",
    description: Optional[str] = None,
    assignee_name: Optional[str] = None,
    dept_name: Optional[str] = None,
    created_by_ai: bool = False,
    resolution_notes: Optional[str] = None,
    caller_id: Optional[str] = None,
    **kwargs: Any,
) -> Dict[str, Any]:
    """
    Tạo phiếu ITSM và ghi ngay vào audit_logs (immutable trail).
    Trả về dict chứa ticket_id và metadata.
    """
    erp_db = _get_erp_db()
    sg = _get_security_guard()

    # Chuẩn hóa đầu vào
    title = title.strip()[:200]
    valid_categories = {"incident", "change", "maintenance", "ai_action", "alert", "other"}
    valid_severities = {"critical", "high", "medium", "low", "info"}
    if category not in valid_categories:
        category = "other"
    if severity not in valid_severities:
        severity = "medium"

    # Xác định status ban đầu
    initial_status = "completed" if resolution_notes else "pending"

    # Tìm dept_id nếu có dept_name
    dept_id: Optional[int] = None
    if dept_name:
        try:
            from mateai.infrastructure.database.erp_database import ERPDatabase
            conn = erp_db.get_connection()
            c = conn.cursor()
            c.execute(
                "SELECT id FROM departments WHERE LOWER(name) LIKE ? LIMIT 1;",
                (f"%{dept_name.lower()}%",),
            )
            row = c.fetchone()
            conn.close()
            if row:
                dept_id = row["id"]
        except Exception as _e:
            logger.debug("Không tìm thấy dept '%s': %s", dept_name, _e)

    # Tìm assignee_id nếu có tên
    assignee_id: Optional[int] = None
    if assignee_name:
        try:
            emp = erp_db.get_employee_by_identifier(assignee_name)
            if emp:
                assignee_id = emp["id"]
        except Exception as _e:
            logger.debug("Không tìm thấy assignee '%s': %s", assignee_name, _e)

    # Tổng hợp nội dung phiếu
    full_description = description or f"[{category.upper()}] [{severity.upper()}] {title}"
    if assignee_name and not assignee_id:
        full_description += f"\n\nNgười phụ trách (chưa tìm thấy trong DB): {assignee_name}"

    # Tạo ERP task (ACID transaction)
    try:
        task = erp_db.create_erp_task(
            title=title,
            dept_id=dept_id,
            assignee_id=assignee_id,
            status=initial_status,
            created_by_ai=created_by_ai,
            resolution_notes=resolution_notes,
        )
    except Exception as e:
        logger.error("Không thể tạo ITSM task: %s", e)
        sg.audit_tool_execution(
            "create_system_ticket", "failed",
            employee_id=caller_id,
            payload={"title": title, "error": str(e)},
        )
        return {"status": "error", "error": f"Không thể tạo phiếu: {e}"}

    ticket_id = task["id"]

    # Ghi audit trail (immutable)
    audit_payload = {
        "ticket_id": ticket_id,
        "title": title,
        "category": category,
        "severity": severity,
        "dept_id": dept_id,
        "assignee_id": assignee_id,
        "created_by_ai": created_by_ai,
        "description": full_description[:500],
        "resolution_notes": resolution_notes,
    }
    audit_status = "success"
    sg._write_audit(
        action_type=f"create_ticket:{category}",
        status=audit_status,
        employee_id=caller_id or ("ai_system" if created_by_ai else None),
        payload=audit_payload,
        source_ip=None,
        session_id=None,
    )

    logger.info(
        "[ITSM] Phiếu '%s' tạo thành công | id=%s | severity=%s | by_ai=%s",
        title[:60], ticket_id, severity, created_by_ai,
    )

    return {
        "status": "success",
        "ticket_id": ticket_id,
        "title": title,
        "category": category,
        "severity": severity,
        "ticket_status": initial_status,
        "dept_id": dept_id,
        "assignee_id": assignee_id,
        "created_by_ai": created_by_ai,
        "resolution_notes": resolution_notes,
        "message": (
            f"✅ Đã tạo phiếu ITSM #{ticket_id} — '{title}' "
            f"[{category.upper()} | {severity.upper()}] "
            + (f"và ghi nhận giải quyết thành công." if resolution_notes else "đang chờ xử lý.")
        ),
    }


# ─────────────────────────────────────────────────────────────────────────────
# SKILL 2: update_ticket_status
# ─────────────────────────────────────────────────────────────────────────────

@export_skill(
    name="update_ticket_status",
    description=(
        "Cập nhật trạng thái và ghi chú giải quyết cho một phiếu ITSM đã tồn tại. "
        "Thường dùng sau khi AI hoặc kỹ thuật viên hoàn thành xử lý sự cố."
    ),
    parameters_schema={
        "type": "object",
        "properties": {
            "ticket_id": {
                "type": "string",
                "description": "ID phiếu cần cập nhật (ví dụ: 'erp_abc123def456').",
            },
            "status": {
                "type": "string",
                "enum": ["pending", "in_progress", "completed", "cancelled"],
                "description": "Trạng thái mới của phiếu.",
            },
            "resolution_notes": {
                "type": "string",
                "description": "Ghi chú mô tả cách thức giải quyết vấn đề.",
            },
            "caller_id": {
                "type": "string",
                "description": "ID người thực hiện cập nhật (dùng cho audit trail).",
            },
        },
        "required": ["ticket_id", "status"],
    },
)
def update_ticket_status(
    ticket_id: str,
    status: str,
    resolution_notes: Optional[str] = None,
    caller_id: Optional[str] = None,
    **kwargs: Any,
) -> Dict[str, Any]:
    """Cập nhật trạng thái phiếu ITSM, ghi audit trail."""
    erp_db = _get_erp_db()
    sg = _get_security_guard()

    try:
        ok = erp_db.update_erp_task_status(ticket_id, status, resolution_notes)
    except ValueError as e:
        return {"status": "error", "error": str(e)}
    except Exception as e:
        logger.error("update_ticket_status lỗi: %s", e)
        return {"status": "error", "error": f"Không thể cập nhật phiếu: {e}"}

    audit_payload = {
        "ticket_id": ticket_id,
        "new_status": status,
        "resolution_notes": resolution_notes,
    }
    sg._write_audit(
        "update_ticket_status",
        "success" if ok else "failed",
        employee_id=caller_id,
        payload=audit_payload,
        source_ip=None,
        session_id=None,
    )

    if ok:
        return {
            "status": "success",
            "ticket_id": ticket_id,
            "new_status": status,
            "message": f"✅ Phiếu #{ticket_id} đã chuyển sang trạng thái '{status}'."
            + (f" Ghi chú: {resolution_notes}" if resolution_notes else ""),
        }
    return {
        "status": "error",
        "error": f"Không tìm thấy phiếu với id='{ticket_id}'. Kiểm tra lại ticket_id.",
    }


# ─────────────────────────────────────────────────────────────────────────────
# SKILL 3: get_tickets
# ─────────────────────────────────────────────────────────────────────────────

@export_skill(
    name="get_tickets",
    description=(
        "Xem danh sách ticket / phiếu ITSM (phiếu hỗ trợ, sự cố) từ hệ thống ERP: ticket đang mở, "
        "đang xử lý, đã đóng. Hỗ trợ lọc theo trạng thái, phòng ban, hoặc chỉ xem phiếu do AI tạo."
    ),
    parameters_schema={
        "type": "object",
        "properties": {
            "status_filter": {
                "type": "string",
                "enum": ["all", "pending", "in_progress", "completed", "cancelled"],
                "description": "Lọc theo trạng thái. Mặc định 'all'.",
            },
            "ai_only": {
                "type": "boolean",
                "description": "True để chỉ xem phiếu do AI tự tạo.",
            },
            "limit": {
                "type": "integer",
                "description": "Số phiếu tối đa trả về. Mặc định 20, tối đa 100.",
            },
        },
        "required": [],
    },
)
def get_tickets(
    status_filter: str = "all",
    ai_only: bool = False,
    limit: int = 20,
    **kwargs: Any,
) -> Dict[str, Any]:
    """Truy vấn danh sách phiếu ITSM."""
    erp_db = _get_erp_db()
    limit = max(1, min(limit, 100))

    try:
        conn = erp_db.get_connection()
        c = conn.cursor()

        conditions: List[str] = ["title IS NOT NULL"]
        params: List[Any] = []

        if status_filter and status_filter != "all":
            conditions.append("status = ?")
            params.append(status_filter)

        if ai_only:
            conditions.append("created_by_ai = 1")

        where = "WHERE " + " AND ".join(conditions)
        params.append(limit)

        c.execute(
            f"""
            SELECT t.id, t.title, t.status, t.created_by_ai, t.due_date,
                   t.resolution_notes, t.created_at,
                   e.name AS assignee_name,
                   d.name AS dept_name
            FROM tasks t
            LEFT JOIN employees e ON t.assignee_id = e.id
            LEFT JOIN departments d ON t.dept_id = d.id
            {where}
            ORDER BY t.rowid DESC
            LIMIT ?;
            """,
            params,
        )
        rows = [dict(r) for r in c.fetchall()]
        conn.close()

        return {
            "status": "success",
            "total": len(rows),
            "tickets": rows,
            "message": f"📋 Tìm thấy {len(rows)} phiếu ITSM" + (
                f" đang '{status_filter}'" if status_filter != "all" else ""
            ) + ("." if not ai_only else " (do AI tạo)."),
        }
    except Exception as e:
        logger.error("get_tickets lỗi: %s", e)
        return {"status": "error", "error": str(e)}


# ─────────────────────────────────────────────────────────────────────────────
# SKILL 4: generate_daily_report
# ─────────────────────────────────────────────────────────────────────────────

@export_skill(
    name="generate_daily_report",
    description=(
        "Tổng hợp báo cáo cuối ngày: thống kê phiếu ITSM, nhật ký kiểm toán, "
        "KPI AI và ước tính thời gian tiết kiệm được. "
        "Trả về báo cáo văn bản tiếng Việt và dữ liệu JSON cho Dashboard."
    ),
    parameters_schema={
        "type": "object",
        "properties": {
            "report_date": {
                "type": "string",
                "description": (
                    "Ngày cần báo cáo (ISO format YYYY-MM-DD). "
                    "Nếu để trống, mặc định là hôm nay."
                ),
            },
            "include_audit_details": {
                "type": "boolean",
                "description": "True để đính kèm 10 dòng audit log gần nhất vào báo cáo.",
            },
        },
        "required": [],
    },
)
def generate_daily_report(
    report_date: Optional[str] = None,
    include_audit_details: bool = False,
    **kwargs: Any,
) -> Dict[str, Any]:
    """
    Tổng hợp báo cáo vận hành cuối ngày. Pure-read — không ghi DB.
    """
    erp_db = _get_erp_db()

    # Xác định ngày báo cáo
    try:
        target_date = datetime.fromisoformat(report_date).date() if report_date else datetime.utcnow().date()
    except ValueError:
        target_date = datetime.utcnow().date()

    date_str = target_date.isoformat()
    date_start = f"{date_str}T00:00:00"
    date_end = f"{date_str}T23:59:59"

    try:
        conn = erp_db.get_connection()
        c = conn.cursor()

        # 1. Phiếu trong ngày
        # COALESCE là bắt buộc: SUM() trên tập rỗng trả NULL, không trả 0.
        # Thiếu nó thì `.get('completed', 0)` không cứu được — khoá vẫn TỒN TẠI
        # với giá trị None, và giá trị mặc định của dict.get() chỉ dùng khi khoá
        # vắng mặt. Hậu quả: báo cáo in ra "Thành công: None" ngay trước mắt
        # người đọc, đúng lúc chưa có dữ liệu.
        c.execute(
            """
            SELECT
                COUNT(*) AS total,
                COALESCE(SUM(CASE WHEN status = 'completed' THEN 1 ELSE 0 END), 0) AS completed,
                COALESCE(SUM(CASE WHEN status = 'pending' THEN 1 ELSE 0 END), 0) AS pending,
                COALESCE(SUM(CASE WHEN status = 'in_progress' THEN 1 ELSE 0 END), 0) AS in_progress,
                COALESCE(SUM(CASE WHEN created_by_ai = 1 THEN 1 ELSE 0 END), 0) AS ai_created
            FROM tasks
            WHERE created_at BETWEEN ? AND ? AND title IS NOT NULL;
            """,
            (date_start, date_end),
        )
        task_row = dict(c.fetchone())

        # 2. Audit logs trong ngày — cùng lý do COALESCE như trên.
        c.execute(
            """
            SELECT
                COUNT(*) AS total,
                COALESCE(SUM(CASE WHEN status = 'success' THEN 1 ELSE 0 END), 0) AS success,
                COALESCE(SUM(CASE WHEN status = 'failed' THEN 1 ELSE 0 END), 0) AS failed,
                COALESCE(SUM(CASE WHEN status = 'blocked' THEN 1 ELSE 0 END), 0) AS blocked
            FROM audit_logs
            WHERE timestamp BETWEEN ? AND ?;
            """,
            (date_start, date_end),
        )
        audit_row = dict(c.fetchone())

        # 3. Tổng số nhân viên & phòng ban
        c.execute("SELECT COUNT(*) AS cnt FROM employees;")
        emp_count = c.fetchone()["cnt"]
        c.execute("SELECT COUNT(*) AS cnt FROM departments;")
        dept_count = c.fetchone()["cnt"]

        # 4. Chi tiết audit 10 dòng gần nhất (nếu yêu cầu)
        recent_audits: List[Dict[str, Any]] = []
        if include_audit_details:
            c.execute(
                "SELECT timestamp, employee_id, action_type, status FROM audit_logs "
                "WHERE timestamp BETWEEN ? AND ? ORDER BY id DESC LIMIT 10;",
                (date_start, date_end),
            )
            recent_audits = [dict(r) for r in c.fetchall()]

        conn.close()

        # 5. Tính KPI
        ai_tasks = task_row.get("ai_created") or 0
        # Giả định kinh doanh (1 phiếu AI = 15 phút), KHÔNG phải số đo — xem
        # hằng số trong core/database.py. Trả kèm hệ số để UI dán nhãn "ước tính"
        # thay vì bán nó như một con số đo được.
        from mateai.infrastructure.database.erp_database import HOURS_SAVED_PER_AI_TASK
        hours_saved = round(ai_tasks * HOURS_SAVED_PER_AI_TASK, 2)
        completion_rate = 0.0
        if task_row.get("total", 0) > 0:
            completion_rate = round(
                100 * (task_row.get("completed") or 0) / task_row["total"], 1
            )
        # 0/0 không phải "0% hoàn thành" mà là KHÔNG ĐO ĐƯỢC. In "0 (0.0%)" là
        # bịa ra một tỷ lệ chưa từng được tính — giao diện đã phân biệt hai
        # chuyện này, văn bản báo cáo cũng phải vậy, nếu không hai chỗ nói lệch.
        rate_txt = (
            f" ({completion_rate}%)"
            if task_row.get("total", 0) > 0
            else " (chưa có phiếu nào để tính tỷ lệ)"
        )

        # 6. Soạn báo cáo văn bản
        report_text = f"""
📊 BÁO CÁO VẬN HÀNH NGÀY {date_str}
{'=' * 50}

🎟️  PHIẾU ITSM:
   • Tổng phiếu hôm nay   : {task_row.get('total', 0)}
   • Hoàn thành           : {task_row.get('completed', 0)}{rate_txt}
   • Đang xử lý           : {task_row.get('in_progress', 0)}
   • Chờ xử lý            : {task_row.get('pending', 0)}
   • Do AI tự tạo         : {ai_tasks}

🔐 NHẬT KÝ KIỂM TOÁN:
   • Tổng thao tác ghi log: {audit_row.get('total', 0)}
   • Thành công           : {audit_row.get('success', 0)}
   • Thất bại             : {audit_row.get('failed', 0)}
   • Bị chặn (RBAC)       : {audit_row.get('blocked', 0)}

🤖 KPI AI:
   • Tác vụ AI tự động    : {ai_tasks}
   • Ước tính thời gian   : {hours_saved} giờ tiết kiệm
     (quy đổi {HOURS_SAVED_PER_AI_TASK}h/phiếu — GIẢ ĐỊNH, không phải số đo)

🏢 TỔ CHỨC:
   • Tổng nhân viên       : {emp_count}
   • Tổng phòng ban       : {dept_count}
""".strip()

        if include_audit_details and recent_audits:
            report_text += "\n\n📋 10 thao tác gần nhất:\n"
            for a in recent_audits:
                report_text += (
                    f"  [{a.get('timestamp', '')[:19]}] "
                    f"{a.get('employee_id', 'N/A')} → "
                    f"{a.get('action_type', '?')} [{a.get('status', '?')}]\n"
                )

        return {
            "status": "success",
            "report_date": date_str,
            "report_text": report_text,
            "data": {
                "tickets": task_row,
                "audit": audit_row,
                "kpi": {
                    "ai_tasks_today": ai_tasks,
                    "hours_saved_today": hours_saved,
                    "hours_saved_per_task": HOURS_SAVED_PER_AI_TASK,
                    "ticket_completion_rate": completion_rate,
                    # Phân biệt "hôm nay không có phiếu nào" (0 phiếu thật) với
                    # "chưa lấy được dữ liệu". UI cần biết để không hiện "0%"
                    # như thể đã đo được tỷ lệ hoàn thành.
                    "tickets_total": task_row.get("total", 0),
                },
                "org": {
                    "total_employees": emp_count,
                    "total_departments": dept_count,
                },
                "recent_audits": recent_audits,
            },
            "message": (
                f"📊 Báo cáo ngày {date_str}: "
                f"{task_row.get('total', 0)} phiếu ITSM, "
                f"{audit_row.get('total', 0)} thao tác log, "
                f"AI tiết kiệm ~{hours_saved}h."
            ),
        }

    except Exception as e:
        logger.error("generate_daily_report lỗi: %s", e)
        return {"status": "error", "error": str(e), "report_date": date_str}
