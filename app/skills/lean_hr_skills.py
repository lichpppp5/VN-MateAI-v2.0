# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 Dương Thanh Lịch — VN-MateAI. See LICENSE and NOTICE.
"""
skills/lean_hr_skills.py
========================
Seed Skill — Lean Micro-Tasking & KPI Tracking Automation.

Provides:
  - send_task_to_client: Dispatch an urgent task or reminder popup to a specific LAN client.
  - summarize_monthly_kpi: Read logs/kpi_logs.csv and aggregate monthly completion metrics.

Both functions are decorated with @export_skill for auto-discovery by PluginManager.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Dict, Optional

from core.plugin_manager import export_skill

logger = logging.getLogger(__name__)


@export_skill(
    name="send_task_to_client",
    description="Giao việc hoặc gửi tin nhắn nhắc việc tương tác dạng popup nổi hai chiều xuống máy con trong mạng LAN.",
    parameters_schema={
        "type": "object",
        "properties": {
            "client_id": {
                "type": "string",
                "description": "Định danh mã máy trạm con (ví dụ: 'TEST-WORKER-01', 'KT-HOAN', 'MAY-KE-TOAN').",
            },
            "message": {
                "type": "string",
                "description": "Nội dung công việc hoặc thông điệp cần nhắc nhở nhân viên thực hiện.",
            },
            "sender": {
                "type": "string",
                "description": "Tên người hoặc phòng ban giao việc (mặc định: 'Ban Giám Đốc').",
            },
        },
        "required": ["client_id", "message"],
    },
)
def send_task_to_client(
    client_id: str,
    message: str,
    sender: str = "Ban Giám Đốc",
) -> Dict[str, Any]:
    """
    Gửi lệnh popup nhắc việc tương tác xuống máy trạm trong mạng LAN.
    Nhân viên trên máy con sẽ thấy popup nổi và có thể bấm [Đã Hoàn Thành] hoặc [Vướng Mắc].
    """
    from mateai.application.devices.task_manager import task_manager
    from mateai.interfaces.websocket.client_orchestrator import orchestrator

    logger.info("AI ra lệnh giao việc: client='%s', sender='%s', message='%s'", client_id, sender, message)

    # Nếu máy trạm chưa kết nối trực tiếp trong process này (ví dụ gọi từ tiến trình con/CLI), gọi qua REST API nội bộ
    if not orchestrator.is_client_online(client_id):
        try:
            import urllib.request
            import json
            from mateai.application.security.auth_manager import auth_manager
            from mateai.config.loader import settings
            token = auth_manager.create_access_token({"sub": "admin", "role": "admin"})
            req = urllib.request.Request(
                f"http://127.0.0.1:{settings.PORT}/api/v1/tasks/send",
                data=json.dumps({"client_id": client_id, "message": message, "sender": sender}).encode(),
                headers={"Content-Type": "application/json", "Authorization": f"Bearer {token}"},
            )
            with urllib.request.urlopen(req, timeout=8.0) as resp:
                return json.loads(resp.read().decode())
        except Exception as http_err:
            logger.debug("Không thể gọi REST fallback: %s", http_err)

    loop = orchestrator._loop
    try:
        if loop and loop.is_running():
            coro = task_manager.dispatch_task(client_id=client_id, message=message, sender=sender)
            future = asyncio.run_coroutine_threadsafe(coro, loop)
            return future.result(timeout=10.0)
        else:
            return asyncio.run(task_manager.dispatch_task(client_id=client_id, message=message, sender=sender))
    except Exception as exc:
        logger.error("Lỗi khi phát lệnh giao việc xuống máy trạm [%s]: %s", client_id, exc)
        return {
            "status": "error",
            "client_id": client_id,
            "error": f"Lỗi phát lệnh giao việc: {exc}",
        }


@export_skill(
    name="summarize_monthly_kpi",
    description="Đọc file nhật ký kpi_logs.csv để đếm tổng số lượng công việc đã hoàn thành hoặc vướng mắc của nhân viên theo tháng.",
    parameters_schema={
        "type": "object",
        "properties": {
            "client_id": {
                "type": "string",
                "description": "Mã máy trạm cần tổng hợp (bỏ trống nếu muốn xem toàn bộ hệ thống).",
            },
            "month": {
                "type": "string",
                "description": "Tháng cần tổng hợp định dạng YYYY-MM hoặc số tháng như '9', '09' (mặc định: tháng hiện tại).",
            },
        },
    },
)
def summarize_monthly_kpi(
    client_id: Optional[str] = None,
    month: Optional[str] = None,
) -> str:
    """
    Đọc dữ liệu nhật ký công việc từ file kpi_logs.csv và tổng hợp báo cáo KPI dạng văn bản súc tích.
    """
    from mateai.application.devices.task_manager import task_manager

    logger.info("AI yêu cầu tổng hợp KPI: client='%s', month='%s'", client_id, month)
    return task_manager.summarize_monthly(client_id=client_id, month=month)
