# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 Dương Thanh Lịch — VN-MateAI. See LICENSE and NOTICE.
"""
mateai/application/operations/topology_layout.py
================================================
Bố cục sơ đồ hệ thống người dùng đã kéo thả: CHỈ vị trí các ô — trạng thái luôn
đo thật (`interfaces/http/topology.snapshot`). Chuyển từ `routers/system.py`
(Supervisor Phase 10, §198: router không tự ghi tệp).
"""
from __future__ import annotations

import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Iterable

from mateai.config.loader import settings

logger = logging.getLogger(__name__)

LAYOUT_PATH = Path(settings.PROJECT_ROOT) / "storage" / "custom_topology.json"


def load() -> Dict[str, Dict[str, float]]:
    """{node_id: {x, y}} (bản lưu cũ có cả nodes -> lấy position)."""
    if not LAYOUT_PATH.exists():
        return {}
    try:
        data = json.loads(LAYOUT_PATH.read_text(encoding="utf-8"))
    except Exception as exc:  # noqa: BLE001
        logger.warning("Không đọc được bố cục topology đã lưu: %s", exc)
        return {}
    if isinstance(data.get("positions"), dict):
        return data["positions"]
    out = {}
    for n in data.get("nodes") or []:
        pos = n.get("position") if isinstance(n, dict) else None
        if isinstance(pos, dict) and "x" in pos and "y" in pos:
            out[str(n.get("id"))] = {"x": pos["x"], "y": pos["y"]}
    return out


def save(nodes: Iterable[Any], saved_by: str) -> int:
    """Lưu vị trí các ô; trả số ô đã lưu."""
    positions = {
        str(n.get("id")): {"x": n["position"]["x"], "y": n["position"]["y"]}
        for n in nodes
        if isinstance(n, dict) and isinstance(n.get("position"), dict)
        and "x" in n["position"] and "y" in n["position"]
    }
    LAYOUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    data = {"positions": positions, "saved_at": datetime.utcnow().isoformat(), "saved_by": saved_by}
    LAYOUT_PATH.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    logger.info("[Topology] Đã lưu bố cục sơ đồ (%d ô)", len(positions))
    return len(positions)


def reset() -> bool:
    """Xoá bố cục đã lưu (về mặc định). Trả True nếu có tệp để xoá."""
    if LAYOUT_PATH.exists():
        LAYOUT_PATH.unlink()
        logger.info("[Topology] Đã xoá custom_topology.json, khôi phục mặc định")
        return True
    return False
