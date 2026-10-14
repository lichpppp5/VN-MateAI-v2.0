# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 Dương Thanh Lịch — VN-MateAI. See LICENSE and NOTICE.
"""
core/security_guard.py
======================
Phase 48: Enterprise RBAC Middleware & Immutable Audit Interceptor.

Responsibilities:
  1. Role-Based Access Control (RBAC):
     - Map caller identity (employee_id / username) to role in ERP employees table.
     - Enforce permission matrix per role before any Tool call reaches the executor.
     - Roles: admin > it_support > operator > viewer
     - Permission levels per tool category defined in RBAC_RULES dict.
     - On violation: raise PermissionError with structured Vietnamese message.

  2. Audit Logging Interceptor:
     - Every tool call decision (allowed / blocked) is persisted to audit_logs
       via erp_db.write_audit_log() — INSERT only, immutable.
     - Captures: action_type, payload, status, employee_id, session_id.

  3. Integration point:
     - Called from llm_engine.ask_async() before plugin_manager.execute_skill().
     - Also hooked into server.py's /api/v1/skills/execute endpoint.

Design:
  - Thread-safe singleton: SecurityGuard.
  - Fail-CLOSED: danh tính không tra cứu được (khách, id sai, lỗi DB) → 'viewer',
    tức chỉ được đọc. Không bao giờ tự nâng quyền lên admin.
  - All permission checks are synchronous for minimal latency in the hot path.
"""

from __future__ import annotations

import contextvars
import json
import logging
from typing import Any, Dict, Iterable, List, Optional, Set, Tuple

logger = logging.getLogger("mateai.application.security.security_guard")

# ─────────────────────────────────────────────────────────────────────────────
# RBAC Permission Matrix
# ─────────────────────────────────────────────────────────────────────────────

# Mỗi role được phép gọi các NHÓM tool được liệt kê.
# "*" = toàn quyền (admin only).
# Tool name được so sánh bằng startswith / in để match nhóm.
RBAC_RULES: Dict[str, Any] = {
    "admin": {"allow_all": True},  # Toàn quyền
    "it_support": {
        "allow_all": False,
        # Được phép: quản lý file, PowerShell an toàn, reset mật khẩu, xem audit
        "allowed_prefixes": [
            "read_file", "write_file", "list_directory", "delete_item",
            "run_powershell_command", "get_system_info", "get_active_processes",
            "kill_process", "get_network_info", "get_network_connections",
            "check_peripherals", "get_system_metrics",
            "open_application", "search_files",
            "list_available_skills", "reload_all_skills",
            "query_organization_data", "lookup_domain_info",
            "list_online_workstations",
            # Đọc dữ liệu hạ tầng/ITSM đã khai báo. Thao tác GHI (run_data_source_action)
            # chỉ admin — không có trong danh sách này, và luôn qua duyệt HITL.
            "list_data_sources", "fetch_data_source", "prepare_data_source_export",
            # Dev Fleet: chỉ XEM (create_/cancel_dev_fleet_task không có ở đây = chỉ admin).
            "get_dev_fleet_status", "list_dev_fleet_workers", "get_dev_fleet_task", "get_dev_fleet_briefing",
            # Giám sát hạ tầng (Prometheus / Grafana): chỉ đọc
            "get_infra_status", "get_infra_alerts", "query_prometheus", "list_grafana_dashboards",
            # Kịch bản vận hành: xem + chạy thử (run_playbook chỉ admin)
            "list_playbooks", "get_playbook_plan", "get_playbook_run",
            # Mức dùng AI / ngân sách / kết quả đánh giá: chỉ đọc
            "get_ai_usage", "get_ai_budget_status", "get_ai_eval_results",
        ],
        "blocked_prefixes": [
            "create_new_skill",         # Không tự tạo kỹ năng
            "manage_websphere",         # Không can thiệp WebSphere
            "sql_admin",                # Không can thiệp SQL cấp cao
            "change_user_password",     # Chỉ admin
        ],
    },
    "operator": {
        "allow_all": False,
        # Được phép: đọc cảm biến, điều khiển thiết bị tại chỗ, xem log thường
        "allowed_prefixes": [
            "get_system_info", "get_system_metrics",
            "get_active_processes", "get_network_info",
            "get_clipboard", "check_peripherals",
            "query_organization_data",
            "list_available_skills",
            "lookup_domain_info",
            "list_online_workstations",
            # Phase 63: đọc báo cáo từ nguồn dữ liệu doanh nghiệp. Cùng loại
            # với query_organization_data — chỉ đọc, không ghi.
            "list_data_sources", "fetch_data_source",
            "prepare_data_source_export",
        ],
        "blocked_prefixes": [
            "run_powershell", "write_file", "delete_item",
            "kill_process", "create_new_skill", "reload_all_skills",
        ],
    },
    "viewer": {
        "allow_all": False,
        # Được phép: chỉ đọc thông tin, không thực hiện tác vụ
        "allowed_prefixes": [
            "get_system_info", "get_system_metrics",
            "get_network_info", "query_organization_data",
            "list_available_skills",
            # Phase 63: xem báo cáo. Cả ba tool đều chỉ đọc — danh sách nguồn,
            # kéo dữ liệu, chuẩn bị file tải về. Không có tool nào ghi hay
            # xoá gì trên hệ thống khách hàng, nên cho phép với viewer là
            # đúng vai trò "chỉ đọc". Lời gọi ra ngoài vẫn phải qua cổng
            # HITL (risk_level 2) — RBAC cho phép gọi, HITL quyết định có
            # chạy không. Hai lớp này không thay thế nhau.
            "list_data_sources", "fetch_data_source",
            "prepare_data_source_export",
        ],
        "blocked_prefixes": [
            # Mọi tác vụ ghi, thực thi, xoá đều bị chặn với viewer
        ],
    },
}

# Các tool LUÔN LUÔN bị chặn tuyệt đối dù là admin (tránh tai nạn)
GLOBALLY_FORBIDDEN_TOOLS: Set[str] = {
    "format_drive",
    "wipe_all_data",
}

# ─────────────────────────────────────────────────────────────────────────────
# Fail-Closed Constants
# ─────────────────────────────────────────────────────────────────────────────

# Role mặc định khi KHÔNG xác định được danh tính. Chỉ cho phép đọc.
DEFAULT_ROLE = "viewer"

# Ánh xạ role của hệ thống auth portal sang role RBAC nội bộ.
# Role lạ → DEFAULT_ROLE (fail-closed), KHÔNG phải admin.
PORTAL_ROLE_MAP: Dict[str, str] = {
    "admin": "admin",
    "manager": "it_support",
    "viewer": "operator",
    # Bản thân các role RBAC cũng được chấp nhận nếu DB lưu trực tiếp
    "it_support": "it_support",
    "operator": "operator",
}

# Service principal: danh tính MÁY (không phải người) được phép gọi tool.
#
# Zero-Trust: so khớp CHÍNH XÁC, không dùng prefix — vì prefix là kiểu khớp dễ bị
# đoán/giả mạo (ai cũng tự đặt được id bắt đầu bằng "esp32"/"robot"/"root").
# Không service principal nào nhận "admin": thấp nhất phải đủ cho thiết bị điều
# khiển tại chỗ, nhưng không đủ để chạy PowerShell / SQL admin / tạo skill.
#
# Muốn nâng quyền cho một thiết bị: thêm nó vào bảng ERP `employees` với role
# tương ưng (bước 1 trong _resolve_role sẽ thắng bước 0).
#: Tiền tố danh tính thiết bị đã xác thực bằng token riêng (xem _resolve_role).
DEVICE_PRINCIPAL_PREFIX = "device:"
#: Quyền đặt được cho một thiết bị ở Web Portal.
DEVICE_ROLES = ("admin", "it_support", "operator", "viewer")
#: Agent ngoài gọi VÀO VN-MateAI bằng token riêng (Danh bạ Agent, docs/integrations/agent-gateway.md) — máy chủ gán, client không tự đặt.
AGENT_PRINCIPAL_PREFIX = "agent:"

#: Danh tính dịch vụ khai báo tường minh (so khớp CHÍNH XÁC). Trống: bản cũ
#: (f389bbe) cấp "admin" cho "esp32", "robot", "hud", "console", "telegram"… theo
#: tên — prompt Supervisor (2026-10-05) thay thế: thiết bị lấy quyền từ bảng quyền
#: thiết bị (token riêng), Telegram từ `telegram.admin_chat_ids`, còn lại fail-closed.
SERVICE_PRINCIPAL_ROLES: Dict[str, str] = {}


# ─────────────────────────────────────────────────────────────────────────────
# ABAC — phòng ban + cấp bảo mật (prompt cuối §35, §62, §64, §155)
# ─────────────────────────────────────────────────────────────────────────────

#: Cấp bảo mật mặc định theo role RBAC (khi tài khoản chưa được đặt riêng).
CLEARANCE_BY_ROLE: Dict[str, int] = {"admin": 4, "it_support": 3, "operator": 2, "viewer": 1}
#: Cấp tối thiểu để dùng dữ liệu theo mức phân loại khai ở `@export_skill`.
CLEARANCE_FOR_CLASSIFICATION: Dict[str, int] = {"PUBLIC": 0, "INTERNAL": 1, "CONFIDENTIAL": 3, "RESTRICTED": 4}

#: Người (danh tính máy chủ đã xác thực) mà lượt hiện tại đang phục vụ — `tool_gate` đặt
#: trước khi chạy tool, để skill đọc dữ liệu tự lọc theo phòng ban / cấp bảo mật.
CURRENT_PRINCIPAL: contextvars.ContextVar[Optional[str]] = contextvars.ContextVar("current_principal", default=None)


def _how_to_grant(employee_id: Optional[str]) -> str:
    """Câu hướng dẫn cấp quyền đúng chỗ, theo loại danh tính (robot token riêng / robot token chung / tài khoản)."""
    eid = str(employee_id or "").strip()
    if eid.lower().startswith(DEVICE_PRINCIPAL_PREFIX):
        dev = eid[len(DEVICE_PRINCIPAL_PREFIX):]
        return (f"Quản trị viên đặt quyền cho thiết bị '{dev}' ở Web Portal → Bảo mật → Thiết bị "
                f"(PUT /api/v1/security/devices/{dev}/role).")
    return ("Nếu đây là robot: nó đang dùng token CHUNG nên chỉ có quyền xem — cấp token riêng ở Web Portal → Bảo mật → Thiết bị, "
            "nạp vào robot rồi đặt quyền cho thiết bị. Nếu là HUD / portal: đăng nhập bằng tài khoản có quyền cao hơn.")


def required_clearance(tool_name: str) -> int:
    """Cấp bảo mật tối thiểu để chạy tool, theo mức phân loại trong tool contract."""
    try:
        from core.plugin_manager import plugin_manager
        contract = plugin_manager.get_tool_contract(tool_name) or {}
    except Exception:  # noqa: BLE001
        contract = {}
    return CLEARANCE_FOR_CLASSIFICATION.get(str(contract.get("data_classification") or "INTERNAL"), 1)


def scope_rows(rows: Iterable[Dict[str, Any]], principal: Dict[str, Any], dept_key: str = "dept_name") -> List[Dict[str, Any]]:
    """Giữ các dòng thuộc phòng ban người hỏi được xem (admin: tất cả; chưa gán: không dòng nào)."""
    rows = list(rows or [])
    if principal.get("all_departments"):
        return rows
    dept = principal.get("department")
    if not dept:
        return []
    return [r for r in rows if str(r.get(dept_key) or "").strip().lower() == dept.strip().lower()]


class SecurityGuard:
    """
    RBAC Middleware & Audit Logging Interceptor cho VN-MateAI Phase 48.
    Singleton — sử dụng qua: from mateai.application.security.security_guard import security_guard
    """

    def __init__(self) -> None:
        self._audit_enabled: bool = True

    # ─── Public API ──────────────────────────────────────────────────────────

    def check_permission(
        self,
        tool_name: str,
        employee_id: Optional[str] = None,
        session_id: Optional[str] = None,
        payload: Optional[Dict[str, Any]] = None,
        source_ip: Optional[str] = None,
    ) -> Tuple[bool, str]:
        """
        Kiểm tra quyền thực thi tool_name cho caller được định danh bởi employee_id.

        Returns:
            (True, "ok") nếu được phép.
            (False, reason_message) nếu bị chặn.
        """
        # 1. Kiểm tra danh sách cấm tuyệt đối
        if tool_name in GLOBALLY_FORBIDDEN_TOOLS:
            reason = f"🔴 Tool '{tool_name}' bị cấm vĩnh viễn trên toàn hệ thống vì nguy cơ gây thiệt hại nghiêm trọng."
            self._write_audit(tool_name, "blocked", employee_id, payload, source_ip, session_id, reason)
            return False, reason

        # 2. Xác định role của employee
        role = self._resolve_role(employee_id)

        # 3. Kiểm tra theo RBAC rule
        allowed, reason = self._evaluate_rbac(tool_name, role, employee_id)

        # 4. Ghi audit log
        audit_status = "success" if allowed else "blocked"
        self._write_audit(tool_name, audit_status, employee_id, payload, source_ip, session_id, reason)

        return allowed, reason

    def audit_tool_execution(
        self,
        tool_name: str,
        execution_status: str,
        employee_id: Optional[str] = None,
        payload: Optional[Dict[str, Any]] = None,
        session_id: Optional[str] = None,
        source_ip: Optional[str] = None,
    ) -> None:
        """
        Ghi nhật ký kết quả thực thi tool (success / failed) vào audit_logs.
        Gọi sau khi plugin_manager.execute_skill() hoàn thành.
        """
        self._write_audit(
            tool_name, execution_status, employee_id,
            payload, source_ip, session_id
        )

    def get_audit_logs(
        self,
        limit: int = 100,
        employee_id: Optional[str] = None,
        action_type: Optional[str] = None,
        status: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """Lấy nhật ký kiểm toán từ DB. Chỉ SELECT."""
        try:
            from mateai.infrastructure.database.erp_database import erp_db
            return erp_db.get_audit_logs(
                limit=limit,
                employee_id=employee_id,
                action_type=action_type,
                status=status,
            )
        except Exception as e:
            logger.error("Không thể truy vấn audit_logs: %s", e)
            return []

    def get_audit_stats(self) -> Dict[str, Any]:
        """Thống kê nhật ký kiểm toán cho ROI Dashboard."""
        try:
            from mateai.infrastructure.database.erp_database import erp_db
            return erp_db.get_audit_stats()
        except Exception as e:
            logger.error("Không thể thống kê audit_logs: %s", e)
            return {}

    # ─── Internal helpers ────────────────────────────────────────────────────

    def resolve_role(self, employee_id: Optional[str]) -> str:
        """Role RBAC hiệu lực của một danh tính (fail-closed, xem `_resolve_role`)."""
        return self._resolve_role(employee_id)

    def principal(self, employee_id: Optional[str]) -> Dict[str, Any]:
        """Thuộc tính ABAC: role, phòng ban, cấp bảo mật (fail-closed như role).

        Phòng ban / cấp bảo mật lấy từ tài khoản Portal (admin đặt ở Quản lý người dùng);
        nhân viên ERP lấy phòng ban theo `dept_id`. Admin xem mọi phòng ban."""
        role = self._resolve_role(employee_id)
        department: Optional[str] = None
        clearance: Optional[int] = None
        if employee_id:
            try:
                from mateai.infrastructure.database.db_manager import db_manager
                user = db_manager.get_user_by_username_or_id(str(employee_id))
                if user:
                    department = (user.get("department") or None)
                    clearance = user.get("clearance_level")
                else:
                    from mateai.infrastructure.database.erp_database import erp_db
                    emp = erp_db.get_employee_by_identifier(str(employee_id))
                    department = (emp or {}).get("dept_name") or None
            except Exception as exc:  # noqa: BLE001 — fail-closed: không phòng ban, cấp theo role
                logger.warning("Không đọc được thuộc tính ABAC của '%s': %s", employee_id, exc)
        level = int(clearance) if clearance is not None else CLEARANCE_BY_ROLE.get(role, 1)
        return {"id": employee_id, "role": role, "department": department,
                "clearance": max(0, min(4, level)), "all_departments": role == "admin"}

    def _resolve_role(self, employee_id: Optional[str]) -> str:
        """
        Xác định role của người dùng từ employee_id. **Fail-closed.**

        Thứ tự ưu tiên tra cứu:
          1. Service principal đã khai báo tường minh trong SERVICE_PRINCIPAL_ROLES
          2. ERP employees table (by email / name / id)
          3. Portal users table (by username / id)
          4. Mặc định: 'viewer' — KHÔNG BAO GIỜ tự nâng quyền

        Lịch sử (đã sửa — lỗ hổng nghiêm trọng):
        - Trước đây employee_id rỗng/None trả về 'admin'.
        - Trước đây có ADMIN_PREFIXES: bất kỳ id nào bắt đầu bằng "root", "local",
          "admin", "system"... đều nhận full quyền admin. Vì employee_id dẫn xuất từ
          `source_device` do CLIENT tự gửi lên, bất kỳ ai cũng chỉ cần gửi
          source_device="root" là giành được toàn quyền.
        - Trước đây role lạ trong DB, hoặc lỗi truy vấn, cũng rơi về 'admin'.

        Cơ chế mới: chỉ nâng quyền khi danh tính tra cứu được từ database, hoặc khi
        khớp CHÍNH XÁC một service principal đã khai báo. Mọi trường hợp còn lại —
        gồm cả id không tồn tại, id rỗng và lỗi truy vấn — đều nhận 'viewer'.
        """
        if not employee_id:
            return DEFAULT_ROLE  # "viewer"

        clean_id = str(employee_id).strip().lower()
        if not clean_id or clean_id in ("none", "null", "anon", "anonymous", "unknown"):
            return DEFAULT_ROLE

        # Thiết bị đã xác thực bằng token RIÊNG: danh tính "device:<id>" chỉ do máy
        # chủ gán (gateway robot) — client không tự đặt được. Quyền lấy từ bảng
        # device_tokens (đặt ở Web Portal); chưa đặt -> quy tắc cũ theo id.
        if clean_id.startswith(DEVICE_PRINCIPAL_PREFIX):
            device_id = str(employee_id).strip()[len(DEVICE_PRINCIPAL_PREFIX):]
            try:
                from mateai.infrastructure.database.db_manager import db_manager
                role = db_manager.get_device_role(device_id)
            except Exception as e:  # noqa: BLE001 — fail-closed
                logger.warning("Không đọc được quyền thiết bị '%s': %s", device_id, e)
                return DEFAULT_ROLE
            if role:
                return self._normalize_role(role)
            employee_id, clean_id = device_id, device_id.lower()

        # Agent ngoài (token riêng): quyền đặt trong Danh bạ Agent; bị tạm dừng / không có -> quyền thấp nhất.
        if clean_id.startswith(AGENT_PRINCIPAL_PREFIX):
            try:
                from mateai.application.agent_gateway import directory
                role = directory.inbound_role(str(employee_id).strip()[len(AGENT_PRINCIPAL_PREFIX):])
            except Exception as e:  # noqa: BLE001 — fail-closed
                logger.warning("Không đọc được quyền agent '%s': %s", employee_id, e)
                return DEFAULT_ROLE
            return self._normalize_role(role) if role else DEFAULT_ROLE

        # Danh tính có trong DB (người dùng / nhân viên) luôn dùng role trong DB,
        # xét TRƯỚC service principal. Trước đây thứ tự ngược lại: tài khoản
        # viewer tên "hudson" (tiền tố "hud") hay "console" được cấp admin.
        try:
            # Ưu tiên 1: ERP employees
            from mateai.infrastructure.database.erp_database import erp_db
            emp = erp_db.get_employee_by_identifier(employee_id)
            if emp and emp.get("role"):
                return self._normalize_role(emp["role"])

            # Ưu tiên 2: Portal users (auth system)
            from mateai.infrastructure.database.db_manager import db_manager
            user = db_manager.get_user_by_username_or_id(employee_id)
            if user and user.get("role"):
                return PORTAL_ROLE_MAP.get(str(user["role"]).strip().lower(), DEFAULT_ROLE)

        except Exception as e:
            # Fail-closed: lỗi hạ tầng/tra cứu KHÔNG được biến thành nâng quyền.
            logger.warning(
                "Không thể tra cứu role cho '%s': %s. Fail-closed về '%s'.",
                employee_id, e, DEFAULT_ROLE,
            )
            return DEFAULT_ROLE

        # Service principal (danh tính máy do server tự gán: kênh Telegram đã
        # lọc admin_chat_ids, thiết bị đã qua device secret) — khai báo tường minh.
        service_role = SERVICE_PRINCIPAL_ROLES.get(clean_id)
        if service_role:
            return service_role

        # Telegram: "telegram:<chat_id>:<tên>" do gateway gán SAU khi lọc chat. Chỉ
        # admin khi chat_id nằm trong danh sách cấu hình (kiểm lại ở đây, không tin
        # tiền tố). Tiền tố tên thiết bị (esp32/robot/hud…) không còn nâng quyền.
        if clean_id.startswith("telegram:"):
            parts = clean_id.split(":")
            try:
                from mateai.config.loader import settings
                allowed = {str(c).strip() for c in (settings.telegram.admin_chat_ids or [])}
            except Exception:  # noqa: BLE001
                allowed = set()
            if len(parts) >= 2 and parts[1].strip() and parts[1].strip() in allowed:
                return "admin"

        logger.info(
            "[RBAC] Không tra cứu được danh tính '%s' → cấp quyền tối thiểu '%s'. "
            "Nếu đây là tài khoản hợp lệ, hãy thêm vào bảng employees hoặc users.",
            clean_id, DEFAULT_ROLE,
        )
        return DEFAULT_ROLE

    @staticmethod
    def _normalize_role(role: Any) -> str:
        """
        Chuẩn hoá role từ DB về một trong các role hợp lệ trong RBAC_RULES.
        Role lạ → 'viewer' (fail-closed) thay vì 'admin'.
        """
        normalized = str(role or "").strip().lower()
        if normalized in RBAC_RULES:
            return normalized
        # Cho phép DB lưu role theo ký hiệu portal
        return PORTAL_ROLE_MAP.get(normalized, DEFAULT_ROLE)

    def _evaluate_rbac(
        self, tool_name: str, role: str, employee_id: Optional[str]
    ) -> Tuple[bool, str]:
        """Đánh giá quyền theo RBAC_RULES."""
        rule = RBAC_RULES.get(role, RBAC_RULES["viewer"])

        # Admin toàn quyền
        if rule.get("allow_all"):
            return True, f"ok (role=admin, employee={employee_id})"

        blocked_prefixes: List[str] = rule.get("blocked_prefixes", [])
        allowed_prefixes: List[str] = rule.get("allowed_prefixes", [])

        # Kiểm tra blacklist trước
        for prefix in blocked_prefixes:
            if tool_name.startswith(prefix) or tool_name == prefix:
                reason = (
                    f"🚫 Lỗi Bảo mật: Tài khoản [{employee_id or 'unknown'}] với vai trò [{role}] "
                    f"không có quyền thực thi tác vụ '{tool_name}'."
                )
                logger.warning("[RBAC] BLOCKED | tool=%s | role=%s | employee=%s", tool_name, role, employee_id)
                return False, reason

        # Kiểm tra whitelist
        for prefix in allowed_prefixes:
            if tool_name.startswith(prefix) or tool_name == prefix:
                return True, f"ok (role={role}, employee={employee_id})"

        # Không khớp whitelist nào → blocked
        reason = (
            f"🚫 Lỗi Bảo mật: Tài khoản [{employee_id or 'unknown'}] với vai trò [{role}] "
            f"không có quyền thực thi tác vụ '{tool_name}'. "
            f"{_how_to_grant(employee_id)}"
        )
        logger.warning("[RBAC] NOT-IN-WHITELIST | tool=%s | role=%s | employee=%s", tool_name, role, employee_id)
        return False, reason

    def _write_audit(
        self,
        action_type: str,
        status: str,
        employee_id: Optional[str],
        payload: Optional[Any],
        source_ip: Optional[str],
        session_id: Optional[str],
        notes: Optional[str] = None,
    ) -> None:
        """Ghi bất đồng bộ vào audit_logs (non-blocking best-effort)."""
        if not self._audit_enabled:
            return
        try:
            from mateai.infrastructure.database.erp_database import erp_db
            payload_str: Optional[str] = None
            if payload is not None:
                try:
                    if isinstance(payload, (dict, list)):
                        payload_str = json.dumps(payload, ensure_ascii=False, default=str)
                    else:
                        payload_str = str(payload)
                    if notes:
                        # Nhúng ghi chú vào payload để tiết kiệm cột DB
                        try:
                            p = json.loads(payload_str) if payload_str else {}
                            p["_rbac_note"] = notes
                            payload_str = json.dumps(p, ensure_ascii=False, default=str)
                        except Exception:
                            payload_str = f"{payload_str} | note={notes}"
                except Exception:
                    payload_str = str(payload)[:500]

            erp_db.write_audit_log(
                action_type=action_type,
                status=status,
                employee_id=employee_id,
                payload=payload_str,
                source_ip=source_ip,
                session_id=session_id,
            )
        except Exception as e:
            # Audit không bao giờ được làm crash hệ thống chính
            logger.debug("Audit write error (non-fatal): %s", e)


# ─────────────────────────────────────────────────────────────────────────────
# Module-level singleton
# ─────────────────────────────────────────────────────────────────────────────

security_guard = SecurityGuard()
