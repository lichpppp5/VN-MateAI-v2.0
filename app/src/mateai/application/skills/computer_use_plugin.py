# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 Dương Thanh Lịch — VN-MateAI. See LICENSE and NOTICE.
"""
core/plugins/computer_use_plugin.py
==================================
Phase 90: Computer-Use & RPA Worker Plugin for VN-MateAI.

Tích hợp vào Plugin Registry (Phase 60 Enterprise Middleware):
1. Đăng ký Tool: tool_execute_gui_task(task_goal, system_target, session_id)
2. Khi LLM gọi tool này:
   - Đóng gói Task thành JSON chuyển vào hàng đợi RabbitMQ/Redis của cụm Worker.
   - Đặt cờ risk_level=4 nếu tác vụ chứa hành vi chuyển tiền/duyệt lệnh, tự động kích hoạt
     phê duyệt qua Telegram HITL (Phase 60) trước khi Worker thực thi click chuột.
   - Trả về phản hồi cho luồng Voice:
     "Em đã giao lệnh tự động hóa giao diện cho worker xử lý trong phiên làm việc an toàn."
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import time
from typing import Any, Dict, List, Optional

from mateai.application.skills.plugin_registry import plugin_registry
from mateai.application.skills.computer_use_schema import GUITaskRequest
from mateai.application.security.zero_trust import hitl_manager

logger = logging.getLogger(__name__)

# Từ khóa kích hoạt rủi ro cao (Zero-Trust Level 4 -> Telegram HITL Gate)
FINANCIAL_SENSITIVE_KEYWORDS = [
    "chuyển tiền",
    "chuyen tien",
    "duyệt lệnh",
    "duyet lenh",
    "phê duyệt",
    "phe duyet",
    "thanh toán",
    "thanh toan",
    "chuyển khoản",
    "chuyen khoan",
    "rút tiền",
    "rut tien",
    "transfer",
    "approve",
    "payout",
    "payment",
    "disburse",
]

REDIS_WORKER_QUEUE = "vn_mate:worker:gui_tasks"

# In-memory queue fallback khi không có Redis
_IN_MEMORY_TASK_QUEUE: List[Dict[str, Any]] = []


def evaluate_task_risk(task_goal: str) -> int:
    """
    Đánh giá mức độ rủi ro của tác vụ GUI:
    - Nếu chứa hành vi chuyển tiền / duyệt lệnh -> Level 4 (Bắt buộc phê duyệt qua Telegram HITL)
    - Tác vụ thông thường -> Level 2
    """
    clean_goal = task_goal.lower()
    for kw in FINANCIAL_SENSITIVE_KEYWORDS:
        if kw in clean_goal:
            logger.warning("[ComputerUsePlugin] High-risk financial keyword detected: '%s' -> Setting risk_level=4", kw)
            return 4
    return 2


async def _enqueue_task_to_worker(task: GUITaskRequest) -> bool:
    """
    Đẩy tác vụ vào hàng đợi cụm Worker (Redis / RabbitMQ).
    """
    payload_json = task.model_dump_json() if hasattr(task, "model_dump_json") else task.json()

    # 1. Thử gửi qua Redis Queue
    try:
        import redis
        redis_url = os.environ.get("REDIS_URL", "redis://localhost:6379/0")
        r = redis.Redis.from_url(redis_url, socket_timeout=2)
        r.rpush(REDIS_WORKER_QUEUE, payload_json)
        logger.info("[ComputerUsePlugin] Enqueued task %s to Redis queue '%s'", task.task_id, REDIS_WORKER_QUEUE)
        return True
    except Exception as e:
        logger.debug("[ComputerUsePlugin] Redis queue not reachable (%s). Falling back to memory queue.", e)

    # 2. Lưu vào in-memory fallback
    _IN_MEMORY_TASK_QUEUE.append(json.loads(payload_json))
    logger.info("[ComputerUsePlugin] Enqueued task %s to in-memory worker queue (len: %d)",
                task.task_id, len(_IN_MEMORY_TASK_QUEUE))
    return True


async def tool_execute_gui_task(
    task_goal: str,
    system_target: str,
    session_id: str,
) -> Dict[str, Any]:
    """
    Đăng ký Tool: tool_execute_gui_task
    Thực thi tác vụ giao diện tự động hóa chuyên sâu trên cụm Worker (Mac Mini).
    """
    logger.info("[ComputerUsePlugin] Received GUI task: '%s' on '%s' (session: %s)",
                task_goal, system_target, session_id)

    # Đánh giá rủi ro
    risk_level = evaluate_task_risk(task_goal)

    # Đóng gói task
    task = GUITaskRequest(
        task_goal=task_goal,
        system_target=system_target,
        session_id=session_id,
        risk_level=risk_level,
        require_approval=(risk_level >= 4),
    )

    # Nếu tác vụ có rủi ro cao (Level 4), kích hoạt cổng phê duyệt Telegram HITL
    if risk_level >= 4:
        logger.info("[ComputerUsePlugin] Task %s requires HITL approval (risk_level=4)", task.task_id)

        # Task chỉ vào hàng đợi worker KHI ĐÃ ĐƯỢC DUYỆT: worker không tự kiểm tra
        # trạng thái duyệt, nên đẩy vào hàng đợi trước (như trước đây) nghĩa là
        # thao tác tài chính chạy luôn, phê duyệt chỉ còn là hình thức.
        async def _enqueue_after_approval() -> Dict[str, Any]:
            await _enqueue_task_to_worker(task)
            return {"task_id": task.task_id, "enqueued": True}

        try:
            # request_approval là hàm ĐỒNG BỘ (gửi Telegram ở thread nền). Trước
            # đây bị `await` → TypeError bị nuốt → rơi xuống nhánh chạy thẳng.
            approval_res = hitl_manager.request_approval(
                action_name="tool_execute_gui_task",
                params={
                    "task_goal": task_goal,
                    "system_target": system_target,
                    "session_id": session_id,
                    "task_id": task.task_id,
                },
                requested_by="VoiceUser",
                description=f"Thao tác GUI nhạy cảm: '{task_goal}' trên hệ thống {system_target}",
                action_callback=_enqueue_after_approval,
                risk_level=risk_level,
            )
        except Exception as hitl_err:  # pylint: disable=broad-except
            # Fail-closed: không tạo được yêu cầu duyệt thì KHÔNG chạy thao tác rủi ro.
            logger.error("[ComputerUsePlugin] Failed to dispatch HITL request: %s", hitl_err)
            return {
                "success": False,
                "status": "error",
                "task_id": task.task_id,
                "risk_level": risk_level,
                "error": f"Không tạo được yêu cầu phê duyệt: {hitl_err}",
                "voice_reply": "Em không gửi được yêu cầu phê duyệt nên chưa thực hiện thao tác này ạ.",
                "message": "Em không gửi được yêu cầu phê duyệt nên chưa thực hiện thao tác này ạ.",
            }

        task.approval_id = approval_res.get("id")
        reply = ("Thao tác này cần cấp quản lý phê duyệt. Em đã gửi yêu cầu, "
                 "khi được duyệt em sẽ thực hiện ngay ạ.")
        return {
            "success": True,
            "status": "awaiting_approval",
            "task_id": task.task_id,
            "approval_id": task.approval_id,
            "risk_level": risk_level,
            "voice_reply": reply,
            "message": reply,
            "detail": f"Tác vụ có rủi ro cấp {risk_level} (chứa thao tác tài chính/phê duyệt): "
                      f"chưa chạy, chờ phê duyệt {task.approval_id}.",
        }

    # Tác vụ thông thường hoặc đã qua kiểm duyệt: đưa trực tiếp vào worker queue
    await _enqueue_task_to_worker(task)

    voice_response = "Em đã giao lệnh tự động hóa giao diện cho worker xử lý trong phiên làm việc an toàn."

    return {
        "success": True,
        "task_id": task.task_id,
        "risk_level": risk_level,
        "voice_reply": voice_response,
        "message": voice_response,
        "system_target": system_target,
        "session_id": session_id,
    }


def register_computer_use_tool(registry=None) -> Dict[str, Any]:
    """
    Đăng ký tool_execute_gui_task vào Plugin Registry của VN-MateAI.
    """
    target_registry = registry or plugin_registry

    tool_name = "tool_execute_gui_task"
    description = (
        "Thực thi tác vụ điều khiển chuột, bàn phím và giao diện (Computer-Use) tự động hóa "
        "trên môi trường Worker cô lập (Mac Mini). Hỗ trợ vượt bảo vệ anti-bot và tự phục hồi giao diện. "
        "Tự động chuyển cấp độ rủi ro 4 khi phát hiện thao tác chuyển tiền hoặc duyệt lệnh."
    )

    parameters_schema = {
        "type": "object",
        "properties": {
            "task_goal": {
                "type": "string",
                "description": "Mô tả chi tiết mục tiêu tác vụ cần worker tự động hoá trên giao diện (VD: 'Đăng nhập vào VCB Digibank và chuyển tiền 5 triệu')",
            },
            "system_target": {
                "type": "string",
                "description": "Tên hệ thống hoặc phần mềm đích (VD: 'VCB Digibank', 'WebSphere ERP', 'Hệ thống Kế toán')",
            },
            "session_id": {
                "type": "string",
                "description": "Mã định danh phiên làm việc độc lập của worker (VD: 'vcb_session_01')",
            },
        },
        "required": ["task_goal", "system_target", "session_id"],
    }

    try:
        target_registry.register_tool(
            tool_name=tool_name,
            function=tool_execute_gui_task,
            description=description,
            parameters_schema=parameters_schema,
            is_async=True,
            risk_level=2,  # Mặc định là 2, tool_execute_gui_task tự nâng lên 4 khi chứa thao tác tài chính
            timeout_seconds=15.0,
            tags=["phase90", "computer_use", "rpa", "worker"],
            enabled=True,
        )
        logger.info("[ComputerUsePlugin] Successfully registered '%s' in Plugin Registry.", tool_name)
        return {"registered": 1, "tool_name": tool_name}
    except Exception as e:
        logger.error("[ComputerUsePlugin] Failed to register tool: %s", e)
        return {"registered": 0, "error": str(e)}


# Đăng ký do server gọi lúc khởi động (core/server.py, Phase 90). Trước Phase 6
# module còn tự đăng ký khi import -> tool bị đăng ký hai lần.
