# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 Dương Thanh Lịch — VN-MateAI. See LICENSE and NOTICE.
"""
mateai/application/devices/elastic_grid_manager.py
==================================================
Sổ trạm TỰ BÁO NHỊP TIM (`workers/remote_worker_daemon.py` → POST /api/v1/worknodes/heartbeat; quá 15 giây → OFFLINE).

CHỈ THEO DÕI. Không giao việc: hàm `dispatch_or_standby` + hàng đợi PENDING_STANDBY cũ chưa từng được gọi ở đâu (rà 2026-10-12), nên
không trạm nào nhận được việc qua đường này — đã gỡ để giao diện không hứa điều không có. Giao việc cho cụm Work Node / OpenClaw đi qua
**Dev Fleet** (Ubuntu Master, docs/integrations/dev-fleet.md); việc trên máy trạm Windows / macOS đi qua **agent máy trạm**
(`client_agent/`, `target_client` của công cụ). Danh sách trạm này hiện trong tab Dev Fleet.
"""

from __future__ import annotations

import logging
import time
from datetime import datetime
from typing import Any, Dict, List, Optional

logger = logging.getLogger("core.worknodes.grid")

# Ngưỡng timeout heartbeat tính bằng giây (quá 15s -> OFFLINE)
HEARTBEAT_TIMEOUT_SEC = 15.0


class WorknodeState:
    """Trạng thái của một trạm thực thi ngoại vi."""

    def __init__(
        self,
        node_id: str,
        ip: str,
        capabilities: Optional[List[str]] = None,
        status: str = "READY",
        cpu_percent: float = 0.0,
        ram_percent: float = 0.0,
        active_tasks: int = 0,
    ) -> None:
        self.node_id = node_id
        self.ip = ip
        self.capabilities = capabilities or ["GUI_OPENCLAW", "LOCAL_OCR"]
        self.status = status
        self.cpu_percent = cpu_percent
        self.ram_percent = ram_percent
        self.active_tasks = active_tasks
        self.last_heartbeat = time.time()
        self.registered_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    def update_heartbeat(
        self,
        ip: Optional[str] = None,
        capabilities: Optional[List[str]] = None,
        status: Optional[str] = None,
        cpu_percent: Optional[float] = None,
        ram_percent: Optional[float] = None,
        active_tasks: Optional[int] = None,
    ) -> None:
        self.last_heartbeat = time.time()
        if ip:
            self.ip = ip
        if capabilities:
            self.capabilities = capabilities
        if status:
            self.status = status
        if cpu_percent is not None:
            self.cpu_percent = cpu_percent
        if ram_percent is not None:
            self.ram_percent = ram_percent
        if active_tasks is not None:
            self.active_tasks = active_tasks

    @property
    def is_online(self) -> bool:
        return (time.time() - self.last_heartbeat) <= HEARTBEAT_TIMEOUT_SEC

    def to_dict(self) -> Dict[str, Any]:
        online = self.is_online
        return {
            "node_id": self.node_id,
            "ip": self.ip,
            "capabilities": self.capabilities,
            "status": self.status if online else "OFFLINE",
            "is_online": online,
            "cpu_percent": self.cpu_percent if online else 0.0,
            "ram_percent": self.ram_percent if online else 0.0,
            "active_tasks": self.active_tasks if online else 0,
            "last_heartbeat_ago_sec": round(time.time() - self.last_heartbeat, 1),
            "registered_at": self.registered_at,
        }


class ElasticGridManager:
    """Quản lý cụm máy trạm co giãn không giới hạn số lượng."""

    def __init__(self) -> None:
        self._nodes: Dict[str, WorknodeState] = {}

    def record_heartbeat(
        self,
        node_id: str,
        ip: str = "127.0.0.1",
        capabilities: Optional[List[str]] = None,
        status: str = "READY",
        cpu_percent: float = 0.0,
        ram_percent: float = 0.0,
        active_tasks: int = 0,
    ) -> Dict[str, Any]:
        """Ghi nhận ping định kỳ từ trạm ngoại vi."""
        node_key = node_id.strip()
        is_new = node_key not in self._nodes

        if is_new:
            node = WorknodeState(
                node_id=node_key,
                ip=ip,
                capabilities=capabilities,
                status=status,
                cpu_percent=cpu_percent,
                ram_percent=ram_percent,
                active_tasks=active_tasks,
            )
            self._nodes[node_key] = node
            logger.info("[ElasticGrid] Trạm mới gửi nhịp tim: %s (%s)", node_key, ip)
        else:
            node = self._nodes[node_key]
            node.update_heartbeat(
                ip=ip,
                capabilities=capabilities,
                status=status,
                cpu_percent=cpu_percent,
                ram_percent=ram_percent,
                active_tasks=active_tasks,
            )

        # `assigned_task` giữ trong phản hồi (luôn None) để daemon cũ không lỗi: đường này không giao việc.
        return {
            "status": "ack",
            "node_id": node_key,
            "grid_online_nodes": self.get_online_count(),
            "assigned_task": None,
        }

    def get_online_nodes(self) -> List[Dict[str, Any]]:
        """Lấy danh sách các node hiện đang Online."""
        return [node.to_dict() for node in self._nodes.values() if node.is_online]

    def get_all_nodes(self) -> List[Dict[str, Any]]:
        """Lấy toàn bộ danh sách node trong registry kèm trạng thái."""
        return [node.to_dict() for node in self._nodes.values()]

    def get_online_count(self) -> int:
        """Đếm số trạm đang online thực tế."""
        return sum(1 for node in self._nodes.values() if node.is_online)

    def get_grid_overview(self) -> Dict[str, Any]:
        """Tổng quan các trạm tự báo nhịp tim (tab Dev Fleet, Topology)."""
        all_nodes = self.get_all_nodes()
        online_count = sum(1 for n in all_nodes if n["is_online"])
        total_cpu = sum(n["cpu_percent"] for n in all_nodes if n["is_online"])
        avg_cpu = round(total_cpu / online_count, 1) if online_count > 0 else 0.0

        return {
            "total_registered_nodes": len(all_nodes),
            "online_nodes_count": online_count,
            "average_cpu_load": avg_cpu,
            "nodes": all_nodes,
        }


# Singleton instance
elastic_grid_manager = ElasticGridManager()
