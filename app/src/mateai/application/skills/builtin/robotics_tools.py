# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 Dương Thanh Lịch — VN-MateAI. See LICENSE and NOTICE.
"""
core/skills/robotics_tools.py
==============================
Phase 52: Full Autonomous Robotics (Body-Mind Sync).

Cung cấp bộ kỹ năng vật lý (Physical Action Tools) cho LLM (9router):
  1. move_robot(direction, duration_ms):
     - Điều khiển Motor L298N (tiến, lùi, rẽ trái, rẽ phải, dừng lại).
     - Giới hạn an toàn tối đa 3000ms mỗi lệnh di chuyển.
     - Gửi gói tin JSON xuống ESP32: {"type": "cmd", "action": "move", "dir": direction, "time": duration_ms}.
  2. animate_robot(animation):
     - Kích hoạt cử chỉ động lực học (Kinematics) qua Servo 47 (cánh tay), Servo 3 (cổ) và màn hình OLED.
     - Hoạt ảnh: wave_hand, nod_head, look_around, excited, sad.
     - Gửi gói tin JSON xuống ESP32: {"type": "cmd", "action": "animate", "anim": animation}.
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

# Robust export_skill decorator import
try:
    from core.plugin_manager import export_skill
except Exception:
    def export_skill(*args, **kwargs):
        def decorator(fn):
            return fn
        return decorator


def _dispatch_robot_command(cmd_payload: Dict[str, Any]) -> int:
    """
    Gửi gói lệnh JSON xuống toàn bộ các mạch Robot ESP32 đang kết nối qua WebSocket.
    Hoạt động an toàn cả trong ngữ cảnh Async và Sync.
    """
    dispatched_count = 0
    try:
        from mateai.interfaces.websocket.xiaozhi_gateway import xiaozhi_gateway
        from mateai.interfaces.websocket.realtime_hub import active_audio_nodes

        nodes = xiaozhi_gateway.get_all_nodes()
        ws_list = []
        for dev_id, node in nodes.items():
            if hasattr(node, "websocket") and node.websocket:
                ws_list.append((dev_id, node.websocket))

        # Check active_audio_nodes fallback
        for dev_id, info in active_audio_nodes.items():
            ws = info.get("websocket")
            if ws and not any(d == dev_id for d, _ in ws_list):
                ws_list.append((dev_id, ws))

        if not ws_list:
            logger.info("[Robotics] Không có mạch ESP32 Robot nào đang kết nối. Lệnh được mô phỏng thành công: %s", cmd_payload)
            return 0

        raw_text = json.dumps(cmd_payload, ensure_ascii=False)

        async def _send_all():
            nonlocal dispatched_count
            for dev_id, ws in ws_list:
                try:
                    await ws.send_text(raw_text)
                    dispatched_count += 1
                    logger.info("[Robotics] Đã gửi lệnh xuống robot [%s]: %s", dev_id, cmd_payload)
                except Exception as send_err:
                    logger.warning("[Robotics] Lỗi gửi lệnh xuống robot [%s]: %s", dev_id, send_err)

        try:
            loop = asyncio.get_running_loop()
            # If in async loop, schedule task
            t = loop.create_task(_send_all())
            # Return current known target count
            return len(ws_list)
        except RuntimeError:
            # Thread worker: WebSocket thuộc loop của server — phải gửi trên loop
            # đó (asyncio.run tạo loop mới, gửi trên socket của loop khác sẽ hỏng).
            from mateai.interfaces.websocket.client_orchestrator import orchestrator
            server_loop = orchestrator._loop
            if server_loop is not None and server_loop.is_running():
                asyncio.run_coroutine_threadsafe(_send_all(), server_loop).result(timeout=10.0)
            else:
                asyncio.run(_send_all())
            return dispatched_count

    except Exception as exc:
        logger.error("[Robotics] Lỗi trong quá trình dispatch lệnh: %s", exc)

    return dispatched_count


# ---------------------------------------------------------------------------
# Skill 1: move_robot
# ---------------------------------------------------------------------------
@export_skill(
    name="move_robot",
    description=(
        "Điều khiển cơ thể Robot vật lý (ESP32) di chuyển bằng bánh xe (Motor L298N). "
        "Hướng di chuyển gồm: forward (tiến), backward (lùi), left (rẽ trái), right (rẽ phải), stop (dừng lại). "
        "Thời gian di chuyển tính bằng mili-giây (duration_ms), giới hạn an toàn tối đa 3000ms mỗi lần."
    ),
    parameters_schema={
        "type": "object",
        "properties": {
            "direction": {
                "type": "string",
                "enum": ["forward", "backward", "left", "right", "stop"],
                "description": "Hướng di chuyển của Robot: 'forward', 'backward', 'left', 'right', hoặc 'stop'.",
            },
            "duration_ms": {
                "type": "integer",
                "description": "Thời gian di chuyển tính bằng mili-giây (ms). Tối thiểu 100ms, tối đa 3000ms (mặc định 1000ms).",
                "default": 1000,
            },
        },
        "required": ["direction"],
    },
)
def move_robot(direction: str, duration_ms: int = 1000) -> Dict[str, Any]:
    """
    Thực thi lệnh di chuyển vật lý của Robot qua Motor L298N.
    """
    dir_clean = str(direction).strip().lower()
    valid_dirs = ["forward", "backward", "left", "right", "stop"]
    if dir_clean not in valid_dirs:
        return {
            "status": "error",
            "message": f"Hướng di chuyển '{direction}' không hợp lệ. Các hướng cho phép: {valid_dirs}.",
        }

    # Safety limit: clamp duration between 100ms and 3000ms
    if dir_clean == "stop":
        safe_duration = 0
    else:
        try:
            dur = int(duration_ms)
        except (ValueError, TypeError):
            dur = 1000
        safe_duration = max(100, min(3000, dur))

    cmd_payload = {
        "type": "cmd",
        "action": "move",
        "dir": dir_clean,
        "time": safe_duration,
    }

    nodes_count = _dispatch_robot_command(cmd_payload)

    dir_names_vn = {
        "forward": "tiến lên",
        "backward": "lùi lại",
        "left": "rẽ trái",
        "right": "rẽ phải",
        "stop": "dừng lại",
    }
    action_vn = dir_names_vn.get(dir_clean, dir_clean)

    if dir_clean == "stop":
        msg = "Đã phát lệnh dừng bánh xe khẩn cấp cho Robot."
    else:
        msg = f"Đã phát lệnh cho Robot {action_vn} trong {safe_duration} mili-giây."

    return {
        "status": "success",
        "direction": dir_clean,
        "duration_ms": safe_duration,
        "nodes_dispatched": nodes_count,
        "message": msg,
    }


# ---------------------------------------------------------------------------
# Skill 2: animate_robot
# ---------------------------------------------------------------------------
@export_skill(
    name="animate_robot",
    description=(
        "Kích hoạt cử chỉ hoặc biểu cảm vật lý của Robot (Servo cánh tay, Servo cổ, LED, OLED). "
        "Các cử chỉ hỗ trợ: wave_hand (vẫy tay chào), nod_head (gật đầu đồng ý), look_around (ngó nghiêng quan sát), "
        "excited (phấn khích mừng rỡ), sad (buồn bã hối lỗi). "
        "Chỉ sử dụng khi người dùng yêu cầu điều khiển cử chỉ Robot hoặc đang tương tác trực tiếp trên thiết bị Robot vật lý."
    ),
    parameters_schema={
        "type": "object",
        "properties": {
            "animation": {
                "type": "string",
                "enum": ["wave_hand", "nod_head", "look_around", "excited", "sad"],
                "description": "Cử chỉ hoạt hình cần thực hiện: 'wave_hand', 'nod_head', 'look_around', 'excited', hoặc 'sad'.",
            },
        },
        "required": ["animation"],
    },
)
def animate_robot(animation: str) -> Dict[str, Any]:
    """
    Thực thi cử chỉ Kinematics của Servo & biểu cảm OLED.
    """
    anim_clean = str(animation).strip().lower()
    valid_anims = ["wave_hand", "nod_head", "look_around", "excited", "sad"]
    if anim_clean not in valid_anims:
        return {
            "status": "error",
            "message": f"Cử chỉ '{animation}' không hợp lệ. Các cử chỉ cho phép: {valid_anims}.",
        }

    cmd_payload = {
        "type": "cmd",
        "action": "animate",
        "anim": anim_clean,
    }

    nodes_count = _dispatch_robot_command(cmd_payload)

    anim_names_vn = {
        "wave_hand": "vẫy cánh tay chào",
        "nod_head": "gật đầu đồng ý",
        "look_around": "ngó nghiêng quan sát xung quanh",
        "excited": "phấn khích mừng rỡ",
        "sad": "buồn bã cụp cổ",
    }
    action_vn = anim_names_vn.get(anim_clean, anim_clean)

    return {
        "status": "success",
        "animation": anim_clean,
        "nodes_dispatched": nodes_count,
        "message": f"Robot đã thực hiện cử chỉ {action_vn}.",
    }
