# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 Dương Thanh Lịch — VN-MateAI. See LICENSE and NOTICE.
"""
workers/remote_worker_daemon.py
===============================
Daemon Máy Trạm Ngoại Vi Độc Lập (Remote Standby Worker Daemon).
Chạy độc lập trên từng máy Work Node (OpenClaw) hoặc máy trạm ngoại vi.

Chức năng:
  1. Tự động tìm kiếm / kết nối về Ubuntu Master Server (Zero-Configuration).
  2. Phát nhịp tim (Heartbeat) định kỳ 5 giây kèm chỉ số tải thực tế CPU/RAM.
  3. (Chưa có) nhận việc qua phản hồi heartbeat: máy chủ hiện KHÔNG giao việc qua đường này (`assigned_task` luôn rỗng) —
     daemon chỉ để máy chủ THEO DÕI trạm (tab Dev Fleet → Trạm tự báo nhịp tim). Giao việc cho Work Node / OpenClaw dùng
     Dev Fleet (Ubuntu Master); việc trên máy trạm dùng agent máy trạm (client_agent/).

Biến môi trường bắt buộc: VNMATE_ENROLLMENT_TOKEN — giá trị `enrollment_token`
trong config.json của gói /api/v1/download-agent. Thiếu thì Master trả 401.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import platform
import socket
import sys
import time
from typing import Any, Dict, List, Optional
import httpx

# Cấu hình logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] (WorkerDaemon) %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("worker_daemon")

MASTER_API_URL = os.getenv("MASTER_API_URL", "https://localhost:443").rstrip("/")
NODE_ID = os.getenv("NODE_ID", socket.gethostname() or "mac-mini-worker")
HEARTBEAT_INTERVAL_SEC = float(os.getenv("HEARTBEAT_INTERVAL_SEC", "5.0"))
CAPABILITIES = os.getenv("CAPABILITIES", "GUI_OPENCLAW,LOCAL_OCR,BROWSER_RPA").split(",")
# Enrollment secret do Master phát (cùng secret với client agent, lấy trong
# config.json của gói /api/v1/download-agent). Thiếu thì Master trả 401.
ENROLLMENT_TOKEN = os.getenv("VNMATE_ENROLLMENT_TOKEN", "").strip()


def get_local_ip() -> str:
    """Lấy địa chỉ IP nội bộ của máy trạm."""
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return "127.0.0.1"


def get_system_telemetry() -> Dict[str, float]:
    """Lấy thông số CPU và RAM thực tế."""
    try:
        import psutil
        cpu = psutil.cpu_percent(interval=None)
        ram = psutil.virtual_memory().percent
        return {"cpu_percent": round(cpu, 1), "ram_percent": round(ram, 1)}
    except Exception:
        # Fallback ước tính nếu chưa cài psutil
        return {"cpu_percent": 15.0, "ram_percent": 30.0}


class RemoteWorkerDaemon:
    """Daemon tiến trình nền điều khiển máy trạm ngoại vi."""

    def __init__(self) -> None:
        self.node_id = NODE_ID
        self.ip = get_local_ip()
        self.capabilities = [c.strip() for c in CAPABILITIES if c.strip()]
        self.status = "READY"
        self.active_tasks_count = 0
        self.running = True

    async def execute_task(self, task_data: Dict[str, Any]) -> Dict[str, Any]:
        """Thực thi tác vụ nhận từ Master."""
        task_id = task_data.get("task_id", "unknown")
        task_type = task_data.get("task_type", "")
        payload = task_data.get("payload", {})
        logger.info(">>> Tiếp nhận tác vụ [%s]: %s", task_id, task_type)

        self.status = "BUSY"
        self.active_tasks_count += 1
        t0 = time.time()

        try:
            # Tùy biến thực thi theo driver có sẵn
            if "OCR" in task_type:
                await asyncio.sleep(0.5)  # Giả lập OCR xử lý bóc tách
                result = {"extracted_text": "Hóa đơn giá trị gia tăng - Đã bóc tách thành công qua Local OCR.", "confidence": 0.98}
            elif "GUI" in task_type or "OPENCLAW" in task_type:
                await asyncio.sleep(1.0)  # Giả lập điều khiển giao diện màn hình
                result = {"action_performed": "click_and_type", "screen_status": "completed"}
            else:
                await asyncio.sleep(0.3)
                result = {"status": "success", "message": "Tác vụ hoàn tất"}

            duration = round(time.time() - t0, 2)
            logger.info("<<< Tác vụ [%s] hoàn tất trong %ss", task_id, duration)
            return {"status": "success", "task_id": task_id, "duration": duration, "output": result}

        except Exception as e:
            logger.error("Lỗi khi thực thi tác vụ [%s]: %s", task_id, e)
            return {"status": "error", "task_id": task_id, "error": str(e)}
        finally:
            self.active_tasks_count = max(0, self.active_tasks_count - 1)
            self.status = "READY"

    async def start(self) -> None:
        """Vòng lặp heartbeat và xử lý task."""
        logger.info("=====================================================")
        logger.info("Khởi động Elastic Worknode: %s (%s)", self.node_id, self.ip)
        logger.info("Nền tảng: %s | Master URL: %s", platform.platform(), MASTER_API_URL)
        logger.info("Năng lực xử lý: %s", self.capabilities)
        logger.info("=====================================================")

        if not ENROLLMENT_TOKEN:
            logger.warning("Thiếu VNMATE_ENROLLMENT_TOKEN — Master sẽ từ chối heartbeat (401).")
        auth = {"Authorization": f"Bearer {ENROLLMENT_TOKEN}"} if ENROLLMENT_TOKEN else {}
        async with httpx.AsyncClient(timeout=8.0, verify=False, headers=auth) as client:
            while self.running:
                telemetry = get_system_telemetry()
                ping_payload = {
                    "node_id": self.node_id,
                    "ip": self.ip,
                    "capabilities": self.capabilities,
                    "status": self.status,
                    "cpu_percent": telemetry["cpu_percent"],
                    "ram_percent": telemetry["ram_percent"],
                    "active_tasks": self.active_tasks_count,
                    "platform": platform.platform(),
                }

                try:
                    resp = await client.post(
                        f"{MASTER_API_URL}/api/v1/worknodes/heartbeat",
                        json=ping_payload,
                    )
                    if resp.status_code == 200:
                        data = resp.json()
                        assigned_task = data.get("assigned_task")
                        if assigned_task:
                            asyncio.create_task(self.execute_task(assigned_task))
                    else:
                        logger.warning("Heartbeat bị từ chối (%s): %s", resp.status_code, resp.text)
                except Exception as e:
                    logger.debug("Chưa kết nối được tới Master API (%s): %s", MASTER_API_URL, e)

                await asyncio.sleep(HEARTBEAT_INTERVAL_SEC)


if __name__ == "__main__":
    daemon = RemoteWorkerDaemon()
    try:
        asyncio.run(daemon.start())
    except KeyboardInterrupt:
        logger.info("Đã dừng daemon trạm ngoại vi.")
