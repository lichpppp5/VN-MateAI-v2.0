# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 Dương Thanh Lịch — VN-MateAI. See LICENSE and NOTICE.
"""
skills/workstation_tools.py
===========================
AI xem được máy trạm nào đang kết nối (Agent trực tuyến) để chọn đúng máy đích cho các công cụ
chạy trên máy trạm (`target_client`). Chỉ đọc.
"""
from typing import Any, Dict

from core.plugin_manager import export_skill


@export_skill(
    name="list_online_workstations",
    description=(
        "Liệt kê các máy trạm / máy tính trong công ty đang kết nối Agent (trực tuyến): mã máy, tên máy, IP, hệ điều hành. "
        "Dùng TRƯỚC khi chạy công cụ trên một máy trạm cụ thể, hoặc khi được hỏi 'máy nào đang bật / đang online'. "
        "Có thể lọc theo một phần tên máy hoặc IP."
    ),
    parameters_schema={
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "Một phần tên máy hoặc IP để lọc (bỏ trống = tất cả máy đang trực tuyến).",
            },
        },
        "required": [],
    },
)
def list_online_workstations(query: str = "", **kwargs: Any) -> Dict[str, Any]:
    from mateai.application.devices import worker_enrollment
    from mateai.application.devices import workstation_directory as wd
    from mateai.interfaces.websocket.client_orchestrator import orchestrator

    index = wd.build_index(orchestrator.get_connected_clients(), worker_enrollment.list_devices())
    rows = wd.online_list(index, query)
    return {
        "status": "success",
        "count": len(rows),
        "workstations": rows,
        "note": ("Dùng `client_id` làm target_client. Máy không có trong danh sách = Agent chưa cài hoặc đang ngoại tuyến; "
                 "không tự suy đoán dữ liệu của máy đó."),
    }
