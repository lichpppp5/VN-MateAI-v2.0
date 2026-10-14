# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 Dương Thanh Lịch — VN-MateAI. See LICENSE and NOTICE.
"""
core/connectors/tool_bridge.py
==============================
Đăng ký 12 connector tool của Phase 59 vào Plugin Registry (Phase 60).

Vì sao cần cầu nối này
---------------------
`core/skills/integration_tools.py` đã export 12 tool qua `@export_skill`, nên
LLM nhìn thấy chúng qua `plugin_manager`. Nhưng `plugin_manager` KHÔNG có:

  - timeout cứng cho mỗi tool (`asyncio.wait_for`),
  - circuit breaker (tự ngắt tool hỏng liên tục để không spam hệ thống ngoài),
  - cổng HITL theo `risk_level` khai báo riêng.

Đó đúng là ba thứ Phase 60 sinh ra để làm. Nếu không đăng ký vào registry thì
`GET /api/v1/enterprise/plugin-registry/stats` luôn trả `total_tools: 0` và
trang Plugin Registry trên Web Portal hiện "Chưa có công cụ nào".

Cách tiếp cận
-------------
Đọc metadata (description + parameters_schema) từ chính `plugin_manager` thay
vì khai lại ở đây. Lý do: `@export_skill` đã là nguồn sự thật duy nhất cho
schema; chép tay tạo ra khả năng hai nơi lệch nhau âm thầm — đúng loại lỗi
"thay đổi ở đây nhưng không có tác dụng" mà không ai phát hiện.

Rủi ro ro chỉ đọc (read-only): toàn bộ action của 4 connector này là
`billing_summary` / `instance_status` / `instance_list` / `cloud_metrics` /
`search` / `details` / `daily_summary` — không action nào ghi hay xoá dữ liệu
ngoại vi. `risk_level` vẫn lấy từ `CONNECTOR_RISK_LEVELS` để đúng với chính
sách Zero-Trust, kể cả khi giá trị đó là 1-2.
"""

from __future__ import annotations

import inspect
import logging
from typing import Any, Dict, List

from mateai.infrastructure.connectors import CONNECTOR_RISK_LEVELS

logger = logging.getLogger(__name__)

#: action của Phase 59 -> tên tool tương ứng trong integration_tools
_CONNECTOR_ACTIONS = {
    "aws:billing_summary": "check_aws_cost",
    "aws:instance_status": "check_aws_instances",
    "oci:instance_list": "check_oci_instances",
    "oci:cloud_metrics": "check_oci_metrics",
    "paperless:search": "search_paperless_documents",
    "paperless:details": "get_paperless_document",
    "paperless:download": "download_paperless_document",
    "einvoice:daily_summary": "check_einvoice_daily",
    "einvoice:search": "search_einvoices",
    "einvoice:details": "get_einvoice_details",
}

#: tool đọc trạng thái connector, không map với action nào trong CONNECTOR_RISK_LEVELS
_HEALTH_TOOL = "check_connector_health"

#: tool gọi ra hệ thống ngoài việc dùng token tài khoản nên cần chờ lâu hơn
#: mặc định 5s của registry (auth + 1 request + 1 request xác minh).
_TIMEOUT_BY_TOOL = {
    _HEALTH_TOOL: 30.0,
    "check_aws_cost": 20.0,
    "check_oci_metrics": 20.0,
}


def _risk_for(tool_name: str) -> int:
    """
    Suy ra `risk_level` của tool.

    `CONNECTOR_RISK_LEVELS` khoá theo `<connector>:<action>`; tra ngược từ tên
    tool. Có công cụ không nằm trong bảng (`check_connector_health`) thì mặc
    định 1 — nó chỉ đọc trạng thái, không mở rộng bề mặt tấn công.
    """
    for key, lvl in CONNECTOR_RISK_LEVELS.items():
        if _CONNECTOR_ACTIONS.get(key) == tool_name:
            return int(lvl)
    return 1


def register_connector_tools(registry=None) -> Dict[str, int]:
    """
    Đăng ký 12 connector tool vào Plugin Registry.

    Trả về số liệu ``{"registered": N, "skipped": M}`` để caller ghi log và để
    test khẳng định được, thay vì phải đếm tay trên HTTP response.
    """
    try:
        from mateai.application.skills.plugin_registry import plugin_registry as _default
    except Exception as exc:  # pragma: no cover
        logger.error("[Phase60] Không import được plugin_registry: %s", exc)
        return {"registered": 0, "skipped": 0}

    registry = registry or _default

    try:
        from mateai.application.skills.builtin import integration_tools
    except Exception as exc:  # pragma: no cover
        logger.error("[Phase60] Không import được integration_tools: %s", exc)
        return {"registered": 0, "skipped": 0}

    # Metadata lấy từ plugin_manager (nguồn của @export_skill)
    schemas: Dict[str, Dict[str, Any]] = {}
    try:
        from core.plugin_manager import plugin_manager

        for tool in plugin_manager.get_all_tools():
            fn = tool.get("function") or {}
            name = fn.get("name")
            if name:
                schemas[name] = fn
    except Exception as exc:  # pylint: disable=broad-except
        logger.error("[Phase60] Không đọc được schema từ plugin_manager: %s", exc)
        return {"registered": 0, "skipped": 0}

    wanted: List[str] = list(_CONNECTOR_ACTIONS.values()) + [_HEALTH_TOOL]
    # loại trùng nếu có (hiện tại không có, nhưng đừng để nó lặp âm thầm)
    seen: set = set()
    ordered = [n for n in wanted if not (n in seen or seen.add(n))]

    registered = 0
    skipped: List[str] = []

    for tool_name in ordered:
        fn_obj = getattr(integration_tools, tool_name, None)
        if fn_obj is None:
            skipped.append(f"{tool_name}: không có hàm trong integration_tools")
            continue

        meta = schemas.get(tool_name)
        if not meta:
            skipped.append(f"{tool_name}: không tìm thấy schema trong plugin_manager")
            continue

        params = meta.get("parameters") or {"type": "object", "properties": {}}
        risk = _risk_for(tool_name)
        try:
            registry.register_tool(
                tool_name=tool_name,
                function=fn_obj,
                description=meta.get("description", ""),
                parameters_schema=params,
                is_async=inspect.iscoroutinefunction(fn_obj),
                risk_level=risk,
                timeout_seconds=_TIMEOUT_BY_TOOL.get(tool_name, 10.0),
                tags=["phase59", "connector"],
                enabled=True,
            )
            registered += 1
        except Exception as exc:  # pylint: disable=broad-except
            skipped.append(f"{tool_name}: {type(exc).__name__}: {exc}")

    if registered:
        logger.info(
            "[Phase60] Đã đăng ký %d/%d connector tool vào Plugin Registry",
            registered, len(ordered),
        )
    for msg in skipped:
        logger.warning("[Phase60] Bỏ qua connector tool — %s", msg)

    return {"registered": registered, "skipped": len(skipped)}
