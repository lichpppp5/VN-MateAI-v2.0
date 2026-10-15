# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 Dương Thanh Lịch — VN-MateAI. See LICENSE and NOTICE.
"""
skills/sysadmin_skills.py
=========================
Seed Skill 1 — Windows SysAdmin Automation.

Provides:
  - manage_windows_service: Start / stop / restart Windows services via sc.exe / PowerShell.
  - run_local_sql_check:    Execute a T-SQL query against a local SQL Server instance
                            without any GUI, using subprocess + sqlcmd.

All functions are decorated with @export_skill so the PluginManager can
discover and register them automatically at startup / hot-reload.
"""

from __future__ import annotations

import logging
import re
import subprocess
from typing import Any, Dict, List, Optional

from core.plugin_manager import export_skill

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Helper: run a subprocess command safely
# ---------------------------------------------------------------------------


def _run_cmd(
    args: List[str],
    timeout: int = 30,
    shell: bool = False,
) -> Dict[str, Any]:
    """
    Execute a subprocess command and return a structured result dict.

    Returns:
        {
            "returncode": int,
            "stdout": str,
            "stderr": str,
            "success": bool,
        }
    """
    try:
        proc = subprocess.run(
            args,
            capture_output=True,
            text=True,
            timeout=timeout,
            shell=shell,
            encoding="utf-8",
            errors="replace",
        )
        return {
            "returncode": proc.returncode,
            "stdout": proc.stdout.strip(),
            "stderr": proc.stderr.strip(),
            "success": proc.returncode == 0,
        }
    except subprocess.TimeoutExpired:
        return {
            "returncode": -1,
            "stdout": "",
            "stderr": f"Command timed out after {timeout}s.",
            "success": False,
        }
    except FileNotFoundError as exc:
        return {
            "returncode": -1,
            "stdout": "",
            "stderr": f"Executable not found: {exc}",
            "success": False,
        }
    except Exception as exc:  # pylint: disable=broad-except
        return {
            "returncode": -1,
            "stdout": "",
            "stderr": f"Unexpected error: {exc}",
            "success": False,
        }


# ---------------------------------------------------------------------------
# Skill 1: Windows Service Management
# ---------------------------------------------------------------------------


_SERVICE_NAME_RE = re.compile(r"[A-Za-z0-9 _.$@-]{1,256}")


@export_skill(
    name="manage_windows_service",
    description=(
        "Quản lý dịch vụ Windows: khởi động (start), dừng (stop), hoặc "
        "khởi động lại (restart) một dịch vụ theo tên. "
        "Ví dụ: W3SVC (IIS), WinRM, Spooler."
    ),
    parameters_schema={
        "type": "object",
        "properties": {
            "service_name": {
                "type": "string",
                "description": "Tên dịch vụ Windows (ví dụ: 'W3SVC', 'Spooler', 'WinRM').",
            },
            "action": {
                "type": "string",
                "enum": ["start", "stop", "restart", "status"],
                "description": "Hành động cần thực hiện với dịch vụ.",
            },
        },
        "required": ["service_name", "action"],
    },
)
def manage_windows_service(service_name: str, action: str) -> Dict[str, Any]:
    """
    Manage a Windows service using sc.exe / PowerShell.

    Args:
        service_name: The Windows service name (short name, not display name).
        action:       One of: start | stop | restart | status.

    Returns:
        {"success": bool, "output": str, "service": str, "action": str}
    """
    action = action.lower().strip()
    svc = service_name.strip()
    # Tên dịch vụ Windows: chữ, số, khoảng trắng, _ - . $ @ (≤ 256). Chặn ; & | < > ` " ' / \ — lệnh đi dạng danh sách
    # đối số nên không chèn được lệnh, nhưng tên lạ không bao giờ là tên dịch vụ thật (thẩm định thương mại #3).
    if not _SERVICE_NAME_RE.fullmatch(svc):
        return {"success": False, "error": f"Tên dịch vụ không hợp lệ: {svc[:60]!r}", "output": "", "service": svc[:60],
                "action": action}

    if action == "status":
        result = _run_cmd(["sc", "query", svc])
        return {
            "success": result["success"] or "RUNNING" in result["stdout"].upper(),
            "output": result["stdout"] or result["stderr"],
            "service": svc,
            "action": action,
        }

    if action == "restart":
        # Stop first, then start
        stop_result = _run_cmd(["sc", "stop", svc])
        logger.info("Service '%s' stop result: %s", svc, stop_result["returncode"])
        # Brief wait via PowerShell to ensure the service transitions
        _run_cmd(
            ["powershell", "-NonInteractive", "-Command", f"Start-Sleep -Seconds 2"],
            timeout=10,
        )
        start_result = _run_cmd(["sc", "start", svc])
        combined_output = (
            f"STOP: {stop_result['stdout'] or stop_result['stderr']}\n"
            f"START: {start_result['stdout'] or start_result['stderr']}"
        )
        return {
            "success": start_result["success"],
            "output": combined_output,
            "service": svc,
            "action": action,
        }

    if action in ("start", "stop"):
        result = _run_cmd(["sc", action, svc])
        return {
            "success": result["success"],
            "output": result["stdout"] or result["stderr"],
            "service": svc,
            "action": action,
        }

    return {
        "success": False,
        "output": f"Hành động không hợp lệ: '{action}'. Dùng: start | stop | restart | status.",
        "service": svc,
        "action": action,
    }


# ---------------------------------------------------------------------------
# Skill 2: Local SQL Server Check via sqlcmd
# ---------------------------------------------------------------------------


@export_skill(
    name="run_local_sql_check",
    description=(
        "Thực thi một câu truy vấn T-SQL lên SQL Server cục bộ (không cần GUI) "
        "thông qua tiện ích sqlcmd. Trả về kết quả dưới dạng văn bản."
    ),
    parameters_schema={
        "type": "object",
        "properties": {
            "server": {
                "type": "string",
                "description": "Tên server hoặc instance SQL, ví dụ: 'localhost' hoặc 'localhost\\SQLEXPRESS'.",
                "default": "localhost",
            },
            "database": {
                "type": "string",
                "description": "Tên database cần truy vấn.",
                "default": "master",
            },
            "query": {
                "type": "string",
                "description": "Câu lệnh T-SQL cần thực thi (SELECT, sp_who2, v.v.).",
            },
            "username": {
                "type": "string",
                "description": "SQL login username. Bỏ trống để dùng Windows Authentication.",
                "default": "",
            },
            "password": {
                "type": "string",
                "description": "SQL login password. Bỏ trống khi dùng Windows Authentication.",
                "default": "",
            },
        },
        "required": ["query"],
    },
)
def run_local_sql_check(
    query: str,
    server: str = "localhost",
    database: str = "master",
    username: str = "",
    password: str = "",
) -> Dict[str, Any]:
    """
    Execute a T-SQL query using sqlcmd (must be installed on the Windows host).

    Uses Windows Authentication by default; switches to SQL Auth if
    username + password are both provided.

    Args:
        query:    T-SQL statement to execute.
        server:   SQL Server host / instance name.
        database: Target database.
        username: SQL login (optional).
        password: SQL password (optional).

    Returns:
        {"success": bool, "output": str, "rows": list[str], "server": str}
    """
    cmd: List[str] = [
        "sqlcmd",
        "-S", server,
        "-d", database,
        "-Q", query,
        "-W",   # Remove trailing spaces
        "-s", ",",  # Column separator for CSV-like parsing
    ]

    if username and password:
        cmd += ["-U", username, "-P", password]
    else:
        cmd += ["-E"]  # Windows Authentication (Trusted Connection)

    result = _run_cmd(cmd, timeout=60)

    # Parse output lines, filter empty / separator lines
    raw_lines: List[str] = result["stdout"].splitlines()
    rows: List[str] = [
        line for line in raw_lines
        if line.strip() and not set(line.strip()).issubset({"-", " ", ","})
    ]

    return {
        "success": result["success"],
        "output": result["stdout"] or result["stderr"],
        "rows": rows,
        "server": server,
        "database": database,
    }
