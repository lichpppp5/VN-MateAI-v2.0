# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 Dương Thanh Lịch — VN-MateAI. See LICENSE and NOTICE.
"""
skills/infra_monitor_tools.py
=============================
Công cụ cho AI Ly Ly trả lời về tình trạng hạ tầng bằng dữ liệu Prometheus / Grafana THẬT (docs/integrations/monitoring.md).
Tên công cụ bắt đầu bằng `get_` / `list_` / `query_` nên Risk Engine xếp L0 (chỉ đọc) — vẫn dùng được khi bật kill switch.
Chưa khai báo nguồn giám sát -> nói thẳng là chưa có, không bịa số liệu.
"""
from __future__ import annotations

import logging
from typing import Any, Dict, Optional

from core.plugin_manager import export_skill

logger = logging.getLogger(__name__)

NOT_CONFIGURED = ("Chưa có nguồn giám sát nào. Thêm Prometheus / Grafana ở Tích hợp → Thêm kết nối "
                  "(chọn mẫu Prometheus hoặc Grafana).")


def _im():
    from mateai.application.monitoring import infra_monitor
    return infra_monitor


async def _snap() -> Dict[str, Any]:
    return await _im().snapshot()


@export_skill(
    name="get_infra_status",
    description=("Tình trạng hạ tầng theo Prometheus / Grafana của doanh nghiệp: cảnh báo đang bắn, target down, máy đang quá tải "
                 "(CPU/RAM/đĩa), nguồn nào mất liên lạc. Dùng khi hỏi 'hạ tầng đang thế nào', 'có cảnh báo gì không', 'máy nào quá tải'."),
    parameters_schema={"type": "object", "properties": {}, "required": []},
)
async def get_infra_status() -> Dict[str, Any]:
    try:
        snap = await _snap()
        if not snap.get("enabled"):
            return {"success": False, "error": "Giám sát hạ tầng đang tắt (monitoring.enabled)."}
        if not snap.get("configured"):
            return {"success": False, "configured": False, "error": NOT_CONFIGURED}
        hot = [m for s in snap["sources"] for m in s.get("metrics", []) if m["level"] != "ok"]
        down = [t for s in snap["sources"] if s.get("targets") for t in s["targets"]["down_list"]]
        return {"success": True, "checked_at": snap["checked_at"], "summary": snap["summary"],
                "critical_alerts": [a for a in snap["alerts"] if a["severity"] == "critical" and a["state"] == "firing"][:10],
                "targets_down": down[:15], "hot_metrics": sorted(hot, key=lambda m: -m["value"])[:15],
                "unreachable_sources": [{"id": s["id"], "error": s["error"]} for s in snap["sources"] if not s["reachable"]]}
    except Exception as exc:  # noqa: BLE001
        logger.exception("[infra tool] get_infra_status")
        return {"success": False, "error": f"{type(exc).__name__}: {exc}"}


@export_skill(
    name="get_infra_alerts",
    description="Danh sách cảnh báo hạ tầng (Prometheus + Grafana), lọc theo mức nghiêm trọng. Chỉ đọc.",
    parameters_schema={"type": "object", "properties": {
        "severity": {"type": "string", "enum": ["critical", "warning", "info"], "description": "Chỉ lấy mức này trở lên. Bỏ trống = tất cả."}},
        "required": []},
)
async def get_infra_alerts(severity: Optional[str] = None) -> Dict[str, Any]:
    try:
        snap = await _snap()
        if not snap.get("configured"):
            return {"success": False, "configured": False, "error": NOT_CONFIGURED}
        rank = _im().SEVERITY_RANK
        need = rank.get((severity or "").lower(), 0)
        alerts = [a for a in snap["alerts"] if rank.get(a["severity"], 0) >= need]
        return {"success": True, "count": len(alerts), "alerts": alerts[:30], "checked_at": snap["checked_at"]}
    except Exception as exc:  # noqa: BLE001
        return {"success": False, "error": f"{type(exc).__name__}: {exc}"}


@export_skill(
    name="query_prometheus",
    description=("Chạy MỘT biểu thức PromQL (instant query, chỉ đọc) trên Prometheus của doanh nghiệp, ví dụ 'up == 0' hoặc "
                 "'rate(http_requests_total[5m])'. Trả các dòng nhãn + giá trị thật; không đoán số."),
    parameters_schema={"type": "object", "properties": {
        "promql": {"type": "string", "description": "Biểu thức PromQL (tối đa 600 ký tự)"},
        "source_id": {"type": "string", "description": "Mã nguồn Prometheus nếu có nhiều (bỏ trống = nguồn đầu tiên)"}},
        "required": ["promql"]},
)
async def query_prometheus(promql: str, source_id: Optional[str] = None) -> Dict[str, Any]:
    im = _im()
    try:
        return {"success": True, **(await im.query_promql(promql, source_id=source_id))}
    except im.MonitorError as exc:
        return {"success": False, "error": str(exc)}
    except Exception as exc:  # noqa: BLE001
        return {"success": False, "error": f"{type(exc).__name__}: {exc}"}


@export_skill(
    name="list_grafana_dashboards",
    description="Liệt kê dashboard Grafana (tên, thư mục, đường dẫn mở) để chỉ cho người dùng xem biểu đồ chi tiết. Chỉ đọc.",
    parameters_schema={"type": "object", "properties": {
        "keyword": {"type": "string", "description": "Lọc theo từ khoá trong tên / thư mục. Bỏ trống = tất cả."}}, "required": []},
)
async def list_grafana_dashboards(keyword: Optional[str] = None) -> Dict[str, Any]:
    try:
        snap = await _snap()
        dashes = [d | {"source": s["id"]} for s in snap.get("sources", []) if s["type"] == "grafana" for d in s.get("dashboards", [])]
        if not any(s["type"] == "grafana" for s in snap.get("sources", [])):
            return {"success": False, "configured": False, "error": "Chưa khai báo nguồn Grafana nào (Tích hợp → Thêm kết nối → Grafana)."}
        if keyword:
            k = keyword.lower()
            dashes = [d for d in dashes if k in d["title"].lower() or k in d["folder"].lower()]
        return {"success": True, "count": len(dashes), "dashboards": dashes[:30]}
    except Exception as exc:  # noqa: BLE001
        return {"success": False, "error": f"{type(exc).__name__}: {exc}"}
