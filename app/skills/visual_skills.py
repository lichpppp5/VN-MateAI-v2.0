# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 Dương Thanh Lịch — VN-MateAI. See LICENSE and NOTICE.
"""
skills/visual_skills.py
=======================
Phase 32: VN-MateAI Visual Overlay Engine Skill.

Allows the AI Assistant (LLM / 9router) to project Cyberpunk HUD visual overlays
(Network Map, Metric Chart, Image/Screenshot, Incident Alert) directly onto the user's screen
concurrently with voice TTS output.
"""

from __future__ import annotations

import json
import logging
import os
import platform
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Union

import psutil

from mateai.interfaces.websocket.client_orchestrator import orchestrator
from core.plugin_manager import export_skill

logger = logging.getLogger("skills.visual_skills")

_PROJECT_ROOT = Path(__file__).resolve().parent.parent


def _get_local_ip() -> str:
    import socket
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return "127.0.0.1"


def _spawn_local_overlay(visual_type: str, data: Dict[str, Any], title: str, duration: int = 15) -> bool:
    """Spawn HUD overlay locally on the machine."""
    import tempfile
    overlay_script = _PROJECT_ROOT / "client_agent" / "overlay_ui.py"
    if not overlay_script.exists():
        logger.warning("overlay_ui.py not found at %s", overlay_script)
        return False

    try:
        tmp = tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False, encoding="utf-8")
        json.dump({
            "type": visual_type,
            "title": title,
            "data": data,
            "duration": duration,
        }, tmp, ensure_ascii=False)
        tmp.close()

        kwargs: Dict[str, Any] = {
            "stdout": subprocess.DEVNULL,
            "stderr": subprocess.DEVNULL,
        }
        if platform.system() != "Windows":
            kwargs["start_new_session"] = True

        subprocess.Popen([sys.executable, str(overlay_script), tmp.name], **kwargs)
        return True
    except Exception as exc:
        logger.error("Failed to spawn local overlay: %s", exc)
        return False


@export_skill(
    name="display_visual_data",
    description="Hiển thị một cửa sổ popup trên màn hình người dùng. CHÚ Ý: Chỉ dùng khi người dùng có nhu cầu xem dữ liệu trực quan hoặc đọc log dài.",
    parameters_schema={
        "type": "object",
        "properties": {
            "type": {
                "type": "string",
                "enum": ["text_board", "network_map", "metric_chart", "security_alert", "screenshot"],
                "description": (
                    "BẮT BUỘC CHỌN 1 TRONG CÁC LOẠI SAU:\n"
                    "- text_board: Dùng để hiển thị danh sách IP, đọc file log, hoặc đoạn text dài.\n"
                    "- network_map: Chỉ dùng khi được yêu cầu xem sơ đồ/cấu trúc mạng.\n"
                    "- metric_chart: Chỉ dùng để xem biểu đồ CPU/RAM.\n"
                    "- security_alert: Chỉ dùng khi có tấn công hoặc rủi ro thực sự.\n"
                    "- screenshot: TUYỆT ĐỐI CHỈ DÙNG khi người dùng có nhắc đến chữ 'chụp ảnh màn hình' hoặc 'xem màn hình'."
                ),
            },
            "context_data": {
                "type": "string",
                "description": "Dữ liệu truyền vào. Nếu type là text_board, đây là chuỗi văn bản hoặc log cần hiển thị.",
            },
            "target_client": {
                "type": "string",
                "description": "ID hoặc tên máy trạm LAN cần hiển thị (mặc định: 'master').",
                "default": "master",
            },
            "title": {
                "type": "string",
                "description": "Tiêu đề của cửa sổ HUD Cyberpunk.",
                "default": "TRỢ LÝ AI LY LY — HUD",
            },
            "duration": {
                "type": "integer",
                "description": "Thời gian hiển thị tự động trước khi mờ dần (giây, mặc định 15s).",
                "default": 15,
            },
        },
        "required": ["type", "context_data"],
    },
)
def display_visual_data(
    type: str,
    context_data: Optional[Union[Dict[str, Any], str]] = None,
    target_client: Optional[str] = None,
    title: Optional[str] = None,
    duration: int = 15,
) -> Dict[str, Any]:
    """
    Execute visual overlay projection onto client screen(s) & Web Portal HUD.
    """
    raw_type = str(type).lower().strip()
    # Normalize aliases to standard types
    if raw_type in ("text_board", "text", "log", "table"):
        visual_type = "text_board"
    elif raw_type in ("security_alert", "alert", "warning"):
        visual_type = "alert"
    elif raw_type in ("screenshot", "image", "screen"):
        visual_type = "image"
    elif raw_type in ("network_map", "network"):
        visual_type = "network_map"
    elif raw_type in ("metric_chart", "metrics", "chart"):
        visual_type = "metric_chart"
    else:
        visual_type = raw_type

    title = title or f"LY LY — {visual_type.replace('_', ' ').upper()}"
    duration = max(5, min(120, int(duration or 15)))

    # Parse context_data gracefully (str, dict, or json string)
    c_data_dict: Dict[str, Any] = {}
    raw_str_content: str = ""
    if isinstance(context_data, str):
        raw_str_content = context_data
        try:
            parsed = json.loads(context_data)
            if isinstance(parsed, dict):
                c_data_dict = parsed
            else:
                c_data_dict = {"text": context_data, "content": context_data}
        except Exception:
            c_data_dict = {"text": context_data, "content": context_data}
    elif isinstance(context_data, dict):
        c_data_dict = dict(context_data)
        raw_str_content = c_data_dict.get("text") or c_data_dict.get("content") or c_data_dict.get("data") or str(c_data_dict)
    else:
        raw_str_content = str(context_data or "")
        c_data_dict = {"text": raw_str_content, "content": raw_str_content}

    prepared_data: Dict[str, Any] = {}

    # -----------------------------------------------------------------------
    # Case 0: Text Board / Log Terminal (Phase 32.1)
    # -----------------------------------------------------------------------
    if visual_type == "text_board":
        prepared_data = {
            "text": raw_str_content,
            "content": raw_str_content,
            "data": raw_str_content,
        }
        if c_data_dict:
            prepared_data.update(c_data_dict)
            prepared_data["text"] = raw_str_content or c_data_dict.get("text", "")

    # -----------------------------------------------------------------------
    # Case 1: Network Map
    # -----------------------------------------------------------------------
    elif visual_type == "network_map":
        connected_clients = orchestrator.get_connected_clients()
        master_ip = _get_local_ip()

        nodes: List[Dict[str, Any]] = [
            {
                "id": "master",
                "label": "Master Server",
                "ip": master_ip,
                "role": "master",
                "status": "online",
            }
        ]
        links: List[Dict[str, Any]] = []

        for client in connected_clients:
            cid = client.get("client_id", "worker")
            hostname = client.get("hostname", cid)
            ip = client.get("ip", "127.0.0.1")
            status = client.get("status", "online")
            nodes.append({
                "id": cid,
                "label": hostname,
                "ip": ip,
                "role": "worker",
                "status": status,
            })
            links.append({
                "source": "master",
                "target": cid,
                "latency": "1-3ms",
            })

        prepared_data = {
            "master": {"ip": master_ip, "port": 443, "ssl": "TLSv1.3"},
            "nodes": nodes,
            "links": links,
            "total_clients": len(connected_clients),
        }
        prepared_data.update(c_data_dict)

    # -----------------------------------------------------------------------
    # Case 2: Metric Chart (System Telemetry HUD)
    # -----------------------------------------------------------------------
    elif visual_type == "metric_chart":
        cpu = psutil.cpu_percent(interval=0.2)
        vmem = psutil.virtual_memory()
        ram_pct = vmem.percent
        ram_used = round(vmem.used / (1024**3), 1)
        ram_total = round(vmem.total / (1024**3), 1)

        try:
            disk = psutil.disk_usage("/").percent
        except Exception:
            disk = 50.0

        procs_count = len(psutil.pids())

        prepared_data = {
            "cpu_percent": cpu,
            "ram_percent": ram_pct,
            "ram_used_gb": ram_used,
            "ram_total_gb": ram_total,
            "disk_percent": disk,
            "processes_count": procs_count,
            "history": c_data_dict.get("history") or [max(10, cpu - 15), max(15, cpu - 5), cpu, min(95, cpu + 10), cpu],
        }
        prepared_data.update(c_data_dict)

    # -----------------------------------------------------------------------
    # Case 3: Image / Screenshot
    # -----------------------------------------------------------------------
    elif visual_type == "image":
        prepared_data = dict(c_data_dict)
        if not prepared_data.get("image_base64") and not prepared_data.get("image_path"):
            # Attempt local screen capture ONLY when screenshot was specifically requested
            try:
                from skills.monitoring_skills import capture_screen_base64
                snap = capture_screen_base64(quality=70, max_width=1000)
                if snap.get("status") == "success":
                    prepared_data["image_base64"] = snap.get("data")
                    prepared_data["caption"] = "Ảnh Chụp Màn Hình Mới Nhất"
            except Exception as e:
                logger.debug("Auto-capture for visual image: %s", e)

    # -----------------------------------------------------------------------
    # Case 4: Security / Incident Alert
    # -----------------------------------------------------------------------
    elif visual_type in ("alert", "warning"):
        prepared_data = {
            "level": c_data_dict.get("level", "WARNING"),
            "service": c_data_dict.get("service", "Zero-Trust Security Engine"),
            "message": c_data_dict.get("message") or c_data_dict.get("description") or raw_str_content or "Phát hiện sự kiện cần người quản trị chú ý.",
            "action": c_data_dict.get("action") or "Đang tiến hành giám sát và cách ly an toàn.",
        }
        prepared_data.update(c_data_dict)

    else:
        prepared_data = dict(c_data_dict)

    # -----------------------------------------------------------------------
    # Dispatching Logic (Remote Clients via WSS + Local Fallback + Web Portal)
    # -----------------------------------------------------------------------
    dispatched_to: List[str] = []
    online_clients = orchestrator.get_client_ids()

    if target_client and target_client in online_clients:
        res = orchestrator.send_visual_to_client_sync(
            client_id=target_client,
            visual_type=visual_type,
            data=prepared_data,
            title=title,
            duration=duration,
        )
        dispatched_to.append(target_client)
    elif online_clients:
        # Broadcast or send to all connected clients
        for cid in online_clients:
            orchestrator.send_visual_to_client_sync(
                client_id=cid,
                visual_type=visual_type,
                data=prepared_data,
                title=title,
                duration=duration,
            )
            dispatched_to.append(cid)

    # Also spawn locally on Master machine so operator sees HUD immediately
    _spawn_local_overlay(visual_type, prepared_data, title, duration)

    # Broadcast to Web Portal In-Browser HUD
    # Trước đây import `broadcast_portal_event` — hàm không tồn tại, lỗi bị nuốt,
    # nên portal không bao giờ hiện visual. Skill chạy trong thread worker nên
    # gửi trên loop của server.
    try:
        import asyncio
        from mateai.interfaces.websocket.realtime_hub import broadcast_portal_ui
        coro = broadcast_portal_ui("show_visual", {
            "type": visual_type,
            "visual_type": visual_type,
            "data": prepared_data,
            "title": title,
            "duration": duration,
        })
        server_loop = orchestrator._loop
        if server_loop is not None and server_loop.is_running():
            asyncio.run_coroutine_threadsafe(coro, server_loop).result(timeout=5.0)
        else:
            coro.close()
    except Exception as exc:
        logger.debug("Broadcast to portal UI non-fatal: %s", exc)

    return {
        "status": "success",
        "visual_type": visual_type,
        "title": title,
        "duration": duration,
        "data": prepared_data,
        "dispatched_to": dispatched_to or ["local_host"],
        "message": f"Đã hiển thị giao diện thị giác [{visual_type}] trên màn hình.",
    }
