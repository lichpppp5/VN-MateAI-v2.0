# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 Dương Thanh Lịch — VN-MateAI. See LICENSE and NOTICE.
"""
core/safety_guard.py
====================
Enterprise Zero-Trust Security Engine & Audit System for VN-MateAI (Phase 9).

Responsibilities:
  1. Data Sanitizer / Masking:
     - mask_sensitive_data(text): Masks LAN IPs, passwords, tokens, DB connection strings before sending to cloud LLMs.
  2. AST Static Code Inspection:
     - inspect_generated_code(code_str): Parses code using Python's AST module to detect dangerous calls (eval, exec,
       os.system / kill / exec*, shutil.rmtree, shell=True, import ctypes / importlib / marshal /
       pickle, forbidden keywords, protected directory tampering).
  3. Action Risk Assessment:
     - evaluate_action_risk(action_name, params): Returns "SAFE", "NEED_CONFIRM", or "BLOCKED".
  4. Enterprise Audit Logging:
     - log_audit(client_id, action, risk, status, details): ghi vào bảng audit_logs (chỉ INSERT).
     - get_recent_audit_logs(limit): đọc audit_logs cho Web Portal — một kho audit duy nhất.
"""

from __future__ import annotations

import ast
import json
import logging
import os
import re
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from mateai.config.loader import settings

logger = logging.getLogger(__name__)

# Trạng thái sự kiện an ninh → cột status (CHECK) của audit_logs. Tên sự kiện
# gốc vẫn được giữ nguyên trong payload["event"].
_EVENT_TO_DB_STATUS: Dict[str, str] = {
    "SUCCESS": "success", "USER_APPROVED": "success", "RECEIVED": "success",
    "FAILED": "failed", "SYNTAX_ERROR": "failed",
    "BLOCKED": "blocked", "REJECTED": "blocked", "USER_REJECTED": "blocked",
    "UNVERIFIED": "blocked",
    "PENDING_CONFIRMATION": "pending",
    # AI tự từ chối yêu cầu vì pháp luật / đạo đức / quyền riêng tư (ethics/refusals.py)
    "REFUSED": "blocked",
}


def _audit_row_to_event(row: Dict[str, Any]) -> Dict[str, Any]:
    """Một dòng audit_logs → dạng sự kiện portal/StateManager đang dùng."""
    from datetime import datetime, timezone

    raw = row.get("payload")
    try:
        payload = json.loads(raw) if raw else {}
    except (TypeError, ValueError):
        payload = {"raw": raw}
    is_security_event = isinstance(payload, dict) and "event" in payload and "risk" in payload
    try:
        epoch = datetime.fromisoformat(str(row.get("timestamp"))).replace(tzinfo=timezone.utc).timestamp()
    except ValueError:
        epoch = 0.0
    return {
        "id": row.get("id"),
        "timestamp": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(epoch)),
        "timestamp_epoch": epoch,
        "client_id": row.get("employee_id") or "local",
        "action": row.get("action_type"),
        # Dòng ghi thẳng bằng write_audit_log (RBAC, HITL) không có mức rủi ro.
        "risk": payload.get("risk") if is_security_event else "-",
        "status": payload.get("event") if is_security_event else str(row.get("status") or "").upper(),
        # Kết quả chuẩn hoá của bảng (success / failed / pending / blocked) —
        # dùng cho thống kê; `status` ở trên là tên sự kiện chi tiết.
        "outcome": str(row.get("status") or "").lower(),
        "details": payload.get("details", {}) if is_security_event else payload,
    }


# ---------------------------------------------------------------------------
# AST Code Visitor for Deep Static Inspection
# ---------------------------------------------------------------------------

class CodeSecurityVisitor(ast.NodeVisitor):
    """
    AST Visitor that detects dangerous function calls, forbidden attributes,
    and protected system paths.
    """

    FORBIDDEN_CALLS = {"eval", "exec", "__import__", "globals", "locals", "compile"}
    DANGEROUS_OS_CALLS = {"system", "popen", "popen2", "popen3", "popen4", "spawnl", "spawnle", "spawnlp", "spawnlpe", "spawnv", "spawnve", "spawnvp", "spawnvpe",
                          # Dừng / thay thế tiến trình máy chủ (mã kỹ năng chạy TRONG tiến trình máy chủ).
                          "kill", "killpg", "_exit", "abort",
                          "execl", "execle", "execlp", "execlpe", "execv", "execve", "execvp", "execvpe"}
    DANGEROUS_SHUTIL_CALLS = {"rmtree"}
    #: Module cho gọi API hệ điều hành tuỳ ý / nạp mã tuỳ ý — vượt qua mọi kiểm tra ở đây.
    FORBIDDEN_IMPORTS = {"ctypes", "importlib", "marshal", "pickle"}

    def __init__(self, protected_directories: List[str]) -> None:
        super().__init__()
        self.violations: List[str] = []
        self.protected_directories = [d.lower() for d in protected_directories]

    def _check_module(self, module: str, lineno: int) -> None:
        if (module or "").split(".")[0] in self.FORBIDDEN_IMPORTS:
            self.violations.append(f"Cấm import module '{module}' tại dòng {lineno}")

    def visit_Import(self, node: ast.Import) -> None:
        for alias in node.names:
            self._check_module(alias.name, node.lineno)
        self.generic_visit(node)

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        self._check_module(node.module or "", node.lineno)
        self.generic_visit(node)

    def visit_Call(self, node: ast.Call) -> None:
        # shell=True: chuỗi lệnh đi qua shell (cmd / sh) — chèn lệnh tuỳ ý.
        for kw in node.keywords:
            if kw.arg == "shell" and isinstance(kw.value, ast.Constant) and kw.value.value is True:
                self.violations.append(f"Cấm chạy lệnh qua shell (shell=True) tại dòng {node.lineno}")
        # Check direct calls e.g., eval(...) or exec(...)
        if isinstance(node.func, ast.Name):
            func_name = node.func.id
            if func_name in self.FORBIDDEN_CALLS:
                self.violations.append(f"Cấm sử dụng hàm nguy hiểm: '{func_name}()' tại dòng {node.lineno}")

        # Check attribute calls e.g., os.system(...) or shutil.rmtree(...)
        elif isinstance(node.func, ast.Attribute):
            attr_name = node.func.attr
            # e.g., os.system
            if isinstance(node.func.value, ast.Name):
                module_name = node.func.value.id
                if module_name == "os" and attr_name in self.DANGEROUS_OS_CALLS:
                    self.violations.append(f"Cấm thực thi shell trực tiếp qua 'os.{attr_name}()' tại dòng {node.lineno}")
                elif module_name == "shutil" and attr_name in self.DANGEROUS_SHUTIL_CALLS:
                    self.violations.append(f"Cấm xóa thư mục hàng loạt qua 'shutil.{attr_name}()' tại dòng {node.lineno}")

        self.generic_visit(node)

    def visit_Constant(self, node: ast.Constant) -> None:
        # Inspect string literals for references to protected OS directories
        if isinstance(node.value, str):
            val_lower = node.value.lower()
            for pdir in self.protected_directories:
                if pdir and pdir in val_lower:
                    self.violations.append(f"Phát hiện truy cập đường dẫn hệ thống nhạy cảm '{pdir}' tại dòng {node.lineno}")
                    break
        self.generic_visit(node)


# ---------------------------------------------------------------------------
# Core SecurityEngine (Zero-Trust & Audit)
# ---------------------------------------------------------------------------

class SecurityEngine:
    """
    Enterprise Zero-Trust Defense Engine for VN-MateAI.
    """

    # Không giữ trạng thái: audit nằm trong audit_logs (mateai.infrastructure.database.erp_database).

    # -----------------------------------------------------------------------
    # 1. Khử Nhiễm Dữ Liệu Đầu Vào (Data Sanitizer / Masking)
    # -----------------------------------------------------------------------

    def mask_sensitive_data(self, text: str) -> str:
        """
        Scan text and mask LAN IP addresses, passwords, secrets, and DB connection strings.
        Prevents internal enterprise secrets from leaking to cloud LLM providers.
        """
        if not text or not isinstance(text, str):
            return text

        masked = text

        # 1. Mask Internal LAN IPv4 Addresses (10.x.x.x, 192.168.x.x, 172.16-31.x.x)
        lan_ip_pattern = re.compile(
            r"\b(10\.\d{1,3}\.\d{1,3}\.\d{1,3}|192\.168\.\d{1,3}\.\d{1,3}|172\.(?:1[6-9]|2\d|3[0-1])\.\d{1,3}\.\d{1,3})\b"
        )
        masked = lan_ip_pattern.sub("[PROTECTED_IP]", masked)

        # 2. Mask Database Connection Strings (postgres, mysql, mongodb, redis, mssql)
        db_pattern = re.compile(
            r"(?i)\b(postgres(?:ql)?|mysql|mongodb(?:\+srv)?|redis|mssql):\/\/[^\s\"'>]+",
        )
        masked = db_pattern.sub("[PROTECTED_DB_URI]", masked)

        # 3. Mask Passwords / Passphrases
        pwd_pattern = re.compile(
            r"(?i)\b(password|passwd|pwd)(\s*[:=]\s*['\"]?)([^\s\"',;]+)(['\"]?)",
        )
        masked = pwd_pattern.sub(r"\1\2[PROTECTED_CREDENTIAL]\4", masked)

        # 4. Mask API Keys / Tokens
        key_pattern = re.compile(
            r"(?i)\b(api[_-]?key|secret[_-]?key|access[_-]?token)(\s*[:=]\s*['\"]?)([^\s\"',;]+)(['\"]?)",
        )
        masked = key_pattern.sub(r"\1\2[PROTECTED_KEY]\4", masked)

        # 5. Mask Bearer Tokens
        bearer_pattern = re.compile(
            r"(?i)\bBearer\s+([a-zA-Z0-9_\-\.]{20,})",
        )
        masked = bearer_pattern.sub("Bearer [PROTECTED_TOKEN]", masked)

        return masked

    # -----------------------------------------------------------------------
    # 2. Kiểm Tra Mã Động (AST Static Code Inspection)
    # -----------------------------------------------------------------------

    def inspect_generated_code(self, code_str: str) -> Tuple[bool, str]:
        """
        Inspect AI-synthesised Python code using Abstract Syntax Tree (AST) analysis
        and Blacklist checks before writing to disk.

        Returns:
            (is_safe: bool, message: str)
        """
        if not code_str or not code_str.strip():
            return False, "Mã nguồn rỗng."

        # Step 1: Check Blacklist / Forbidden Keywords in raw source
        forbidden_keywords = getattr(settings.security, "forbidden_keywords", [])
        code_lower = code_str.lower()
        for kw in forbidden_keywords:
            if kw.lower() in code_lower:
                reason = f"Mã nguồn chứa từ khóa bị cấm trong Danh sách đen (Blacklist): '{kw}'"
                logger.error("Zero-Trust AST Guard: %s", reason)
                self.log_audit("system", "inspect_code", "BLOCKED", "REJECTED", {"reason": reason})
                return False, reason

        # Step 2: Parse Abstract Syntax Tree (AST)
        try:
            tree = ast.parse(code_str)
        except SyntaxError as syn_err:
            reason = f"Mã nguồn sai cú pháp Python: {syn_err}"
            self.log_audit("system", "inspect_code", "BLOCKED", "SYNTAX_ERROR", {"error": str(syn_err)})
            return False, reason

        # Step 3: Walk AST nodes to find dangerous function calls & protected directories
        protected_dirs = getattr(settings.security, "protected_directories", [])
        visitor = CodeSecurityVisitor(protected_dirs)
        visitor.visit(tree)

        if visitor.violations:
            detail_msg = "; ".join(visitor.violations)
            reason = f"Phát hiện mã nguy hiểm qua kiểm toán AST: {detail_msg}"
            logger.error("Zero-Trust AST Guard vi phạm: %s", reason)
            self.log_audit("system", "inspect_code", "BLOCKED", "REJECTED", {"violations": visitor.violations})
            return False, reason

        return True, "Mã nguồn an toàn theo tiêu chuẩn Zero-Trust."

    # -----------------------------------------------------------------------
    # 3. Phân Loại Rủi Ro Lệnh (Risk Assessment: SAFE / NEED_CONFIRM / BLOCKED)
    # -----------------------------------------------------------------------

    def evaluate_action_risk(self, action_name: str, params: Optional[Dict[str, Any]] = None) -> str:
        """
        Evaluate the execution risk of an action or skill tool.
        Returns:
            "BLOCKED"       — Violates blacklist, must be rejected immediately.
            "NEED_CONFIRM"  — High impact, requires administrator confirmation.
            "SAFE"          — Low risk, proceed automatically.
        """
        forbidden_keywords = getattr(settings.security, "forbidden_keywords", [])
        require_confirm = getattr(settings.security, "require_confirmation_actions", [])

        # Build payload string for blacklist inspection
        payload_str = (action_name + " " + json.dumps(params or {}, ensure_ascii=False)).lower()

        # Check 1: Forbidden keywords -> BLOCKED
        for kw in forbidden_keywords:
            if kw.lower() in payload_str:
                logger.warning("Action '%s' BLOCKED do chứa từ khóa cấm '%s'", action_name, kw)
                return "BLOCKED"

        # Phase 38 Zero-Trust: Low Risk actions are SAFE (Auto-Execute)
        if action_name in ("list_directory", "read_file"):
            return "SAFE"

        # Phase 38 Zero-Trust: High Risk actions require confirmation
        if action_name in ("write_file", "delete_item"):
            return "NEED_CONFIRM"

        # Check 2: Require confirmation actions -> NEED_CONFIRM
        if action_name in require_confirm:
            return "NEED_CONFIRM"

        # Check 3: Generic heuristics for destructive actions
        destructive_verbs = ["delete", "drop", "kill", "format", "wipe", "shutdown", "reboot"]
        if any(v in action_name.lower() for v in destructive_verbs):
            return "NEED_CONFIRM"

        return "SAFE"

    # -----------------------------------------------------------------------
    # 4. Ghi Vết Kiểm Toán An Ninh (Audit Logging)
    # -----------------------------------------------------------------------

    def log_audit(
        self,
        client_id: str,
        action: str,
        risk: str,
        status: str,
        details: Optional[Dict[str, Any]] = None,
    ) -> None:
        """
        Ghi một sự kiện an ninh vào audit_logs (bất biến: chỉ INSERT).

        Trước đây ghi vào logs/security_audit.log — kho thứ hai, có API xoá
        sạch. Nay mọi audit (RBAC lẫn Zero-Trust/HITL) nằm chung một bảng.
        """
        event = str(status or "").upper()
        try:
            from mateai.infrastructure.database.erp_database import erp_db
            erp_db.write_audit_log(
                action_type=action,
                status=_EVENT_TO_DB_STATUS.get(event, "failed"),
                employee_id=client_id or "local",
                payload={"risk": str(risk or "").upper(), "event": event, "details": details or {}},
            )
        except Exception as exc:  # pylint: disable=broad-except
            # Audit không được làm hỏng tác vụ chính, nhưng phải để lại dấu vết.
            logger.error("Không ghi được audit '%s' (%s): %s", action, event, exc)

    def get_recent_audit_logs(self, limit: int = 50) -> List[Dict[str, Any]]:
        """Sự kiện audit mới nhất trước (đọc từ audit_logs)."""
        try:
            from mateai.infrastructure.database.erp_database import erp_db
            rows = erp_db.get_audit_logs(limit=limit)
        except Exception as exc:  # pylint: disable=broad-except
            logger.error("Không đọc được audit_logs: %s", exc)
            return []
        return [_audit_row_to_event(r) for r in rows]


# ---------------------------------------------------------------------------
# Module-level singletons
# ---------------------------------------------------------------------------

security_engine = SecurityEngine()
