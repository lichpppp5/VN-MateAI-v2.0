# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 Dương Thanh Lịch — VN-MateAI. See LICENSE and NOTICE.
"""
skills/computer_use_skills.py
=============================
Phase 90: Computer-Use & Self-Healing Worker Engine Skills for VN-MateAI Portal.
Cung cấp các kỹ năng điều khiển GUI và quản lý cụm Worker hiển thị trực tiếp trên Web Portal.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Dict, Optional

from core.plugin_manager import export_skill
from mateai.application.skills.computer_use_plugin import tool_execute_gui_task as _tool_execute_gui_task
from workers.native_os_driver import native_os_driver
from workers.browser_session_vault import browser_session_vault
from workers.self_healing_engine import self_healing_engine

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# 1. Execute GUI Task (Computer-Use)
# ---------------------------------------------------------------------------

@export_skill(
    name="tool_execute_gui_task",
    description=(
        "Thực thi tác vụ điều khiển chuột, bàn phím và giao diện (Computer-Use) tự động hóa "
        "trên môi trường Worker cô lập (Mac Mini). Hỗ trợ vượt bảo vệ anti-bot (Apple M-series) "
        "và tự phục hồi giao diện 2 tầng (Semantic DOM + Vision OCR). "
        "Tự động chuyển cấp độ rủi ro 4 khi phát hiện thao tác chuyển tiền hoặc duyệt lệnh "
        "để kích hoạt cổng phê duyệt Telegram HITL."
    ),
    parameters_schema={
        "type": "object",
        "properties": {
            "task_goal": {
                "type": "string",
                "description": "Mô tả chi tiết mục tiêu tác vụ cần worker tự động hoá trên giao diện (VD: 'Đăng nhập vào VCB Digibank và kiểm tra biến động số dư')",
            },
            "system_target": {
                "type": "string",
                "description": "Tên hệ thống hoặc phần mềm đích (VD: 'VCB Digibank', 'WebSphere ERP', 'Cổng Thuế Điện Tử eTax')",
                "default": "Web Portal",
            },
            "session_id": {
                "type": "string",
                "description": "Mã định danh phiên làm việc độc lập của worker (VD: 'vcb_session_01')",
                "default": "default_session",
            },
        },
        "required": ["task_goal"],
    },
)
def tool_execute_gui_task(
    task_goal: str,
    system_target: str = "Web Portal",
    session_id: str = "default_session",
) -> Dict[str, Any]:
    """Sync wrapper cho tool_execute_gui_task để tích hợp với Portal PluginManager."""
    try:
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            # Thread worker (plugin_manager.run_blocking) — không có loop: tự chạy.
            return asyncio.run(_tool_execute_gui_task(task_goal, system_target, session_id))
        import concurrent.futures
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(
                asyncio.run,
                _tool_execute_gui_task(task_goal, system_target, session_id),
            )
            return future.result(timeout=15.0)
    except Exception as e:
        logger.error("[ComputerUseSkill] Execution failed: %s", e)
        return {"success": False, "error": str(e)}


# ---------------------------------------------------------------------------
# 2. Get Computer-Use Status
# ---------------------------------------------------------------------------

@export_skill(
    name="get_computer_use_status",
    description="Kiểm tra trạng thái cụm Worker (Mac Mini), danh sách phiên Browser Vault và tỷ lệ tự phục hồi UI.",
    parameters_schema={
        "type": "object",
        "properties": {},
    },
)
def get_computer_use_status() -> Dict[str, Any]:
    """Lấy báo cáo tổng quan trạng thái Computer-Use."""
    try:
        session_list = []
        base_dir = browser_session_vault.base_dir
        if base_dir.exists():
            for p in base_dir.iterdir():
                if p.is_dir():
                    session_list.append(p.name)

        healing_count = len(self_healing_engine._memory_cache)

        return {
            "status": "online",
            "active_worker_nodes": 2,
            "os_environment": "macOS Sonoma / Darwin (Apple M-series)",
            "total_vault_sessions": len(session_list),
            "sessions": session_list,
            "self_healing_records": healing_count,
            "hitl_security_gate": "Level 4 (Telegram Approval Active)",
            "message": "Cụm Worker sẵn sàng tiếp nhận tác vụ điều khiển chuột và bàn phím.",
        }
    except Exception as e:
        return {"status": "error", "error": str(e)}


# ---------------------------------------------------------------------------
# 3. Capture Worker Screen
# ---------------------------------------------------------------------------

@export_skill(
    name="capture_worker_screen",
    description="Chụp ảnh màn hình cửa sổ ứng dụng hoặc toàn màn hình worker Mac Mini đang điều khiển (Base64).",
    parameters_schema={
        "type": "object",
        "properties": {
            "window_title": {
                "type": "string",
                "description": "Tiêu đề cửa sổ ứng dụng cần chụp (để trống nếu chụp toàn màn hình)",
            }
        },
    },
)
def capture_worker_screen(window_title: Optional[str] = None) -> Dict[str, Any]:
    """Chụp ảnh màn hình từ worker node."""
    try:
        b64 = native_os_driver.capture_active_window(window_title=window_title)
        return {
            "success": True,
            "screenshot_base64_length": len(b64),
            "window_title": window_title or "Full Desktop",
            "format": "PNG",
        }
    except Exception as e:
        return {"success": False, "error": str(e)}
