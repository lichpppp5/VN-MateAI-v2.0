# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 Dương Thanh Lịch — VN-MateAI. See LICENSE and NOTICE.
"""
core/domain_sync.py
===================
Active Directory Domain Synchronization Manager for VN-MateAI (Phase 18).

Features:
  - Native Windows PowerShell integration via Get-ADUser and Get-ADComputer (Zero-LDAP dependency).
  - SQLite persistence in hr_kpi.db (employees & computers tables).
  - Fast local indexing & UPSERT for sub-millisecond AI retrieval.
  - Robust RSAT error handling & non-Windows dev environment fallbacks.
"""

from __future__ import annotations

import json
import logging
import os
import platform
import sqlite3
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Union

from mateai.infrastructure.database.erp_database import open_sqlite

logger = logging.getLogger(__name__)

# Resolve default database location
# Thư mục gốc dự án (đúng cả bản đóng gói) — KHÔNG suy từ vị trí file mã nguồn:
# chuyển module mà đường dẫn lệch là máy chủ mở một CSDL rỗng mới.
from mateai.config.loader import settings as _settings  # noqa: E402

_PROJECT_ROOT = Path(_settings.PROJECT_ROOT)

DEFAULT_DB_PATH = Path(os.environ.get("VNMATEAI_HR_DB_PATH") or _PROJECT_ROOT / "hr_kpi.db")


class WindowsDomainManager:
    """
    Manages Active Directory domain entity extraction and local SQLite caching.
    Uses native PowerShell Get-ADUser / Get-ADComputer commands.
    """

    def __init__(self, db_path: Optional[Union[str, Path]] = None) -> None:
        self.db_path = Path(db_path) if db_path else DEFAULT_DB_PATH
        self._init_db()

    def _get_connection(self) -> sqlite3.Connection:
        """Create and configure SQLite connection with WAL mode and row factory."""
        return open_sqlite(self.db_path, timeout=10.0, synchronous="NORMAL")

    def _init_db(self) -> None:
        """Initialize SQLite database tables for employees and computers."""
        with self._get_connection() as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS employees (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    sam_account_name TEXT UNIQUE NOT NULL,
                    full_name TEXT,
                    department TEXT,
                    title TEXT,
                    email TEXT,
                    phone TEXT,
                    synced_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS computers (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    hostname TEXT UNIQUE NOT NULL,
                    os_version TEXT,
                    ip_address TEXT,
                    assigned_to TEXT,
                    synced_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)
            conn.execute("CREATE INDEX IF NOT EXISTS idx_emp_name ON employees(full_name)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_emp_dept ON employees(department)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_comp_host ON computers(hostname)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_comp_ip ON computers(ip_address)")
            conn.commit()
            logger.info("Initialized domain database schema at %s", self.db_path)

    def sync_users(self) -> Dict[str, Any]:
        """
        Synchronize Active Directory users using native PowerShell Get-ADUser.
        UPSERTs records into the 'employees' table.
        """
        # Danh sách đối số, không qua shell (RULE-025).
        ps_cmd = [
            "powershell", "-NoProfile", "-NonInteractive", "-Command",
            "Get-ADUser -Filter * -Properties DisplayName, Department, Title, EmailAddress, OfficePhone "
            "| ConvertTo-Json -Depth 2",
        ]
        logger.info("Executing Active Directory user synchronization via PowerShell...")

        try:
            res = subprocess.run(
                ps_cmd,
                capture_output=True,
                text=True,
                timeout=60,
            )

            stderr = res.stderr or ""
            if res.returncode != 0 or "Get-ADUser" in stderr and ("not recognized" in stderr or "CommandNotFoundException" in stderr):
                msg = "Yêu cầu cài đặt RSAT: Active Directory Domain Services Tools trên máy chủ để đồng bộ AD."
                logger.warning("[DomainSync] %s. Stderr: %s", msg, stderr.strip())
                return {
                    "status": "warning",
                    "message": msg,
                    "count": 0,
                    "details": stderr.strip(),
                }

            stdout = (res.stdout or "").strip()
            if not stdout:
                logger.info("[DomainSync] PowerShell returned no AD user data.")
                return {"status": "success", "message": "Không tìm thấy người dùng nào trong Domain.", "count": 0}

            try:
                parsed = json.loads(stdout)
            except json.JSONDecodeError as err:
                logger.error("[DomainSync] Failed to parse JSON from Get-ADUser: %s", err)
                return {"status": "error", "message": f"Dữ liệu JSON từ AD không hợp lệ: {err}", "count": 0}

            # Normalize single object to list
            users_list = [parsed] if isinstance(parsed, dict) else (parsed if isinstance(parsed, list) else [])

            upserted = 0
            now_iso = datetime.utcnow().isoformat()

            with self._get_connection() as conn:
                for u in users_list:
                    if not isinstance(u, dict):
                        continue
                    sam = (u.get("SamAccountName") or u.get("UserPrincipalName") or "").strip()
                    if not sam:
                        continue

                    full_name = (u.get("DisplayName") or u.get("Name") or sam).strip()
                    dept = (u.get("Department") or "").strip()
                    title = (u.get("Title") or "").strip()
                    email = (u.get("EmailAddress") or u.get("UserPrincipalName") or "").strip()
                    phone = (u.get("OfficePhone") or "").strip()

                    conn.execute("""
                        INSERT INTO employees (sam_account_name, full_name, department, title, email, phone, synced_at)
                        VALUES (?, ?, ?, ?, ?, ?, ?)
                        ON CONFLICT(sam_account_name) DO UPDATE SET
                            full_name = excluded.full_name,
                            department = excluded.department,
                            title = excluded.title,
                            email = excluded.email,
                            phone = excluded.phone,
                            synced_at = excluded.synced_at
                    """, (sam, full_name, dept, title, email, phone, now_iso))
                    upserted += 1

                conn.commit()

            logger.info("[DomainSync] Successfully synchronized %d AD users to SQLite.", upserted)
            return {
                "status": "success",
                "message": f"Đồng bộ thành công {upserted} người dùng từ Active Directory.",
                "count": upserted,
            }

        except subprocess.TimeoutExpired:
            logger.error("[DomainSync] Active Directory user query timed out after 60s.")
            return {"status": "error", "message": "Quá trình đồng bộ AD User quá thời gian chờ (Timeout 60s).", "count": 0}
        except Exception as exc:
            logger.error("[DomainSync] Unexpected error during sync_users: %s", exc)
            return {"status": "error", "message": f"Lỗi không xác định khi đồng bộ AD: {exc}", "count": 0}

    def sync_computers(self) -> Dict[str, Any]:
        """
        Synchronize Active Directory computers using native PowerShell Get-ADComputer.
        UPSERTs records into the 'computers' table.
        """
        # Danh sách đối số, không qua shell (RULE-025).
        ps_cmd = [
            "powershell", "-NoProfile", "-NonInteractive", "-Command",
            "Get-ADComputer -Filter * -Properties OperatingSystem, IPv4Address, Description "
            "| ConvertTo-Json -Depth 2",
        ]
        logger.info("Executing Active Directory computer synchronization via PowerShell...")

        try:
            res = subprocess.run(
                ps_cmd,
                capture_output=True,
                text=True,
                timeout=60,
            )

            stderr = res.stderr or ""
            if res.returncode != 0 or "Get-ADComputer" in stderr and ("not recognized" in stderr or "CommandNotFoundException" in stderr):
                msg = "Yêu cầu cài đặt RSAT: Active Directory Domain Services Tools trên máy chủ để đồng bộ AD."
                logger.warning("[DomainSync] %s. Stderr: %s", msg, stderr.strip())
                return {
                    "status": "warning",
                    "message": msg,
                    "count": 0,
                    "details": stderr.strip(),
                }

            stdout = (res.stdout or "").strip()
            if not stdout:
                logger.info("[DomainSync] PowerShell returned no AD computer data.")
                return {"status": "success", "message": "Không tìm thấy máy tính nào trong Domain.", "count": 0}

            try:
                parsed = json.loads(stdout)
            except json.JSONDecodeError as err:
                logger.error("[DomainSync] Failed to parse JSON from Get-ADComputer: %s", err)
                return {"status": "error", "message": f"Dữ liệu JSON từ AD không hợp lệ: {err}", "count": 0}

            comp_list = [parsed] if isinstance(parsed, dict) else (parsed if isinstance(parsed, list) else [])

            upserted = 0
            now_iso = datetime.utcnow().isoformat()

            with self._get_connection() as conn:
                for c in comp_list:
                    if not isinstance(c, dict):
                        continue
                    hostname = (c.get("Name") or c.get("DNSHostName") or "").strip()
                    if not hostname:
                        continue

                    os_version = (c.get("OperatingSystem") or "").strip()
                    ip_addr = (c.get("IPv4Address") or "").strip()
                    assigned_to = (c.get("Description") or "").strip()

                    conn.execute("""
                        INSERT INTO computers (hostname, os_version, ip_address, assigned_to, synced_at)
                        VALUES (?, ?, ?, ?, ?)
                        ON CONFLICT(hostname) DO UPDATE SET
                            os_version = excluded.os_version,
                            ip_address = excluded.ip_address,
                            assigned_to = excluded.assigned_to,
                            synced_at = excluded.synced_at
                    """, (hostname, os_version, ip_addr, assigned_to, now_iso))
                    upserted += 1

                conn.commit()

            logger.info("[DomainSync] Successfully synchronized %d AD computers to SQLite.", upserted)
            return {
                "status": "success",
                "message": f"Đồng bộ thành công {upserted} máy tính từ Active Directory.",
                "count": upserted,
            }

        except subprocess.TimeoutExpired:
            logger.error("[DomainSync] Active Directory computer query timed out after 60s.")
            return {"status": "error", "message": "Quá trình đồng bộ AD Computer quá thời gian chờ (Timeout 60s).", "count": 0}
        except Exception as exc:
            logger.error("[DomainSync] Unexpected error during sync_computers: %s", exc)
            return {"status": "error", "message": f"Lỗi không xác định khi đồng bộ AD Computer: {exc}", "count": 0}

    def sync_all(self) -> Dict[str, Any]:
        """Convenience method to synchronize both Users and Computers."""
        user_res = self.sync_users()
        comp_res = self.sync_computers()
        return {
            "status": "success" if (user_res.get("status") == "success" and comp_res.get("status") == "success") else "warning",
            "users": user_res,
            "computers": comp_res,
            "total_users": user_res.get("count", 0),
            "total_computers": comp_res.get("count", 0),
        }

    def get_employees(self, limit: int = 100) -> List[Dict[str, Any]]:
        """Retrieve list of employees from SQLite."""
        with self._get_connection() as conn:
            rows = conn.execute(
                "SELECT id, sam_account_name, full_name, department, title, email, phone, synced_at "
                "FROM employees ORDER BY full_name ASC LIMIT ?",
                (limit,)
            ).fetchall()
            return [dict(row) for row in rows]

    def get_computers(self, limit: int = 100) -> List[Dict[str, Any]]:
        """Retrieve list of domain computers from SQLite."""
        with self._get_connection() as conn:
            rows = conn.execute(
                "SELECT id, hostname, os_version, ip_address, assigned_to, synced_at "
                "FROM computers ORDER BY hostname ASC LIMIT ?",
                (limit,)
            ).fetchall()
            return [dict(row) for row in rows]

    def lookup_domain_info(self, query_string: str) -> Dict[str, Any]:
        """
        Search for employees or computers matching a search string using SQL LIKE.
        Safe for LLM Function Calling tool.
        """
        q = query_string.strip()
        if not q:
            return {"status": "success", "query": q, "employees": [], "computers": []}

        pattern = f"%{q}%"
        with self._get_connection() as conn:
            emp_rows = conn.execute("""
                SELECT id, sam_account_name, full_name, department, title, email, phone
                FROM employees
                WHERE sam_account_name LIKE ?
                   OR full_name LIKE ?
                   OR department LIKE ?
                   OR title LIKE ?
                   OR email LIKE ?
                   OR phone LIKE ?
                LIMIT 10
            """, (pattern, pattern, pattern, pattern, pattern, pattern)).fetchall()

            comp_rows = conn.execute("""
                SELECT id, hostname, os_version, ip_address, assigned_to
                FROM computers
                WHERE hostname LIKE ?
                   OR ip_address LIKE ?
                   OR assigned_to LIKE ?
                   OR os_version LIKE ?
                LIMIT 10
            """, (pattern, pattern, pattern, pattern)).fetchall()

            employees = [dict(r) for r in emp_rows]
            computers = [dict(r) for r in comp_rows]

            return {
                "status": "success",
                "query": q,
                "total_found": len(employees) + len(computers),
                "employees": employees,
                "computers": computers,
            }

    def get_stats(self) -> Dict[str, int]:
        """Return counts of employees and computers in the database."""
        with self._get_connection() as conn:
            emp_count = conn.execute("SELECT COUNT(*) FROM employees").fetchone()[0]
            comp_count = conn.execute("SELECT COUNT(*) FROM computers").fetchone()[0]
            return {
                "employees_count": emp_count,
                "computers_count": comp_count,
            }


# Module singleton
domain_manager = WindowsDomainManager()


def probe_hr_database(db_path: "Path | str", timeout: float = 1.5) -> Dict[str, Any]:
    """
    Thăm dò CSDL đồng bộ AD (hr_kpi.db) cho giám sát: đọc được không, lần đồng bộ
    gần nhất và số bản ghi. Không ném lỗi.

    Trả: {"reachable": bool, "tables": bool, "last_sync": str|None,
          "employees": int, "computers": int, "error": str|None}
    `tables` = False khi file mở được nhưng chưa có bảng (chưa từng đồng bộ).
    """
    out: Dict[str, Any] = {"reachable": False, "tables": False, "last_sync": None,
                           "employees": 0, "computers": 0, "error": None}
    try:
        with open_sqlite(db_path, timeout=timeout, wal=False) as conn:
            row = conn.execute("SELECT 1").fetchone()
            out["reachable"] = bool(row and row[0] == 1)
            try:
                r = conn.execute(
                    "SELECT MAX(synced_at) FROM ("
                    "  SELECT synced_at FROM employees "
                    "  UNION ALL "
                    "  SELECT synced_at FROM computers"
                    ")"
                ).fetchone()
                out["last_sync"] = r[0] if r and r[0] else None
                out["employees"] = conn.execute("SELECT COUNT(*) FROM employees").fetchone()[0]
                out["computers"] = conn.execute("SELECT COUNT(*) FROM computers").fetchone()[0]
                out["tables"] = True
            except sqlite3.OperationalError:
                out["tables"] = False
    except Exception as exc:  # pylint: disable=broad-except
        out["error"] = str(exc)
    return out
