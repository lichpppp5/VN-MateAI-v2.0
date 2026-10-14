# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 Dương Thanh Lịch — VN-MateAI. See LICENSE and NOTICE.
"""
core/schemas/computer_use_schema.py
===================================
Phase 90: Computer-Use & Native OS Action Protocol Schema for VN-MateAI.

Định nghĩa cấu trúc dữ liệu chuẩn cho các tác vụ tương tác giao diện (GUI):
- Chuẩn hóa điều khiển GUI & OS (Native Computer-Use).
- Tương thích cụm Worker phân tán (Mac Mini / Headless nodes).
"""

from __future__ import annotations

import time
import uuid
from enum import Enum
from typing import Any, Dict, List, Optional, Tuple
from pydantic import BaseModel, Field


class ActionType(str, Enum):
    """Các loại hành vi tương tác giao diện được hỗ trợ."""
    CLICK = "CLICK"
    DOUBLE_CLICK = "DOUBLE_CLICK"
    MOVE = "MOVE"
    TYPE_TEXT = "TYPE_TEXT"
    PRESS_KEY = "PRESS_KEY"
    SCROLL = "SCROLL"
    DRAG_DROP = "DRAG_DROP"
    CAPTURE_SCREEN = "CAPTURE_SCREEN"


class GUIActionPayload(BaseModel):
    """
    Payload chuẩn cho một thao tác GUI đơn lẻ.
    Tọa độ được chuẩn hóa theo thang đo 0-1000 (Normalized Coordinates: x, y in [0, 1000])
    để tương thích độc lập với độ phân giải màn hình hoặc viewport trình duyệt.
    """
    action: ActionType
    coordinate: Optional[Tuple[int, int]] = Field(
        default=None,
        description="Tọa độ chuẩn hóa (x, y) trong khoảng [0, 1000]"
    )
    target_query: Optional[str] = Field(
        default=None,
        description="Mô tả ngữ nghĩa mục tiêu (VD: 'Nút Đăng nhập màu xanh')"
    )
    text_input: Optional[str] = Field(
        default=None,
        description="Chuỗi ký tự cần nhập (hỗ trợ tiếng Việt UTF-8 có dấu)"
    )
    key_combination: Optional[List[str]] = Field(
        default=None,
        description="Tổ hợp phím tắt (VD: ['command', 'c'] hoặc ['ctrl', 'v'])"
    )
    session_id: str = Field(
        ...,
        description="ID phiên làm việc độc lập của worker"
    )
    timeout_ms: int = Field(
        default=10000,
        description="Thời gian chờ tối đa cho thao tác (mili-giây)"
    )

    class Config:
        use_enum_values = True


class ActionResult(BaseModel):
    """Kết quả trả về sau khi thực thi một thao tác GUI."""
    success: bool
    action: str
    session_id: str
    message: str = ""
    executed_coordinate: Optional[Tuple[int, int]] = None
    execution_time_ms: float = 0.0
    screenshot_base64: Optional[str] = None
    healed: bool = False
    healing_layer: Optional[str] = None  # "semantic_dom" | "vision_fallback" | None
    error: Optional[str] = None
    metadata: Dict[str, Any] = Field(default_factory=dict)


class GUITaskRequest(BaseModel):
    """
    Task hoàn chỉnh được đóng gói đẩy vào hàng đợi cụm Worker (RabbitMQ / Redis).
    """
    task_id: str = Field(default_factory=lambda: f"gui_task_{uuid.uuid4().hex[:12]}")
    task_goal: str = Field(..., description="Mục tiêu tác vụ tổng quát do LLM chỉ định")
    system_target: str = Field(..., description="Tên hệ thống đích (vd: 'VCB Digibank', 'WebSphere ERP')")
    session_id: str = Field(..., description="Phiên làm việc độc lập")
    actions: List[GUIActionPayload] = Field(default_factory=list)
    risk_level: int = Field(default=1, ge=1, le=5, description="Mức độ rủi ro Zero-Trust (1-5)")
    created_at: float = Field(default_factory=time.time)
    require_approval: bool = False
    approval_id: Optional[str] = None

    class Config:
        use_enum_values = True


class SelfHealingCacheEntry(BaseModel):
    """Dữ liệu vị trí được tự phục hồi lưu vào Redis Cache."""
    target_query: str
    system_target: str
    resolved_selector: Optional[str] = None
    resolved_coordinate: Optional[Tuple[int, int]] = None
    bounding_box: Optional[Tuple[int, int, int, int]] = None  # x1, y1, x2, y2
    healing_source: str = "vision"  # "semantic" | "vision"
    created_at: float = Field(default_factory=time.time)
    ttl_seconds: int = 86400  # 24h cache
