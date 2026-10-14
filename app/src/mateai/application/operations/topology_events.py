# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 Dương Thanh Lịch — VN-MateAI. See LICENSE and NOTICE.
"""
mateai/application/operations/topology_events.py
================================================
Kênh sự kiện THẬT cho trang giám sát `/admin/topology`: từng bước của mỗi lượt
xử lý (thoại, tool, phê duyệt, robot) và thay đổi trạng thái thành phần.

- `publish(...)` gọi được từ bất kỳ đâu (đồng bộ, không chặn, không ném lỗi).
- Vòng 300 sự kiện gần nhất: trang mới mở thấy ngay lịch sử gần đây.
- Tầng giao diện đăng ký `subscribe(cb)` để đẩy sự kiện qua WebSocket.

Sự kiện là DỮ LIỆU ĐO: không chứa token / mật khẩu; nội dung câu nói cắt ngắn.
Trước đây sơ đồ chỉ có "tool đã chạy" và các luồng MÔ PHỎNG phát lên mọi người
xem dưới nhãn "[Real-time]" (docs/realtime/topology-plan.md).
"""
from __future__ import annotations

import asyncio
import itertools
import logging
import time
from collections import deque
from datetime import datetime
from typing import Any, Callable, Deque, Dict, List, Optional

logger = logging.getLogger(__name__)

_EVENTS: Deque[Dict[str, Any]] = deque(maxlen=300)
_SUBSCRIBERS: List[Callable[[Dict[str, Any]], Any]] = []
_SEQ = itertools.count(1)
_DETAIL_MAX = 120


def publish(
    kind: str,
    *,
    node: Optional[str] = None,
    source: Optional[str] = None,
    target: Optional[str] = None,
    status: str = "ok",
    stage: Optional[str] = None,
    trace_id: Optional[str] = None,
    channel: Optional[str] = None,
    ms: Optional[float] = None,
    detail: Optional[str] = None,
    simulated: bool = False,
) -> Dict[str, Any]:
    """Ghi một sự kiện và báo cho các bên đăng ký.

    kind   : "turn" | "tool" | "approval" | "robot" | "status" | "simulation"
    status : "ok" | "running" | "waiting" | "error" | "down" | "degraded" | "cancelled"
    node / source->target : thành phần / cạnh trên sơ đồ liên quan.
    """
    ev: Dict[str, Any] = {
        "seq": next(_SEQ),
        "kind": kind,
        "status": status,
        "ts": datetime.now().isoformat(timespec="milliseconds"),
        "t": time.time(),
    }
    for key, val in (("node", node), ("source", source), ("target", target), ("stage", stage),
                     ("trace_id", trace_id), ("channel", channel)):
        if val:
            ev[key] = str(val)
    if ms is not None:
        ev["ms"] = round(float(ms), 1)
    if detail:
        d = " ".join(str(detail).split())
        ev["detail"] = d if len(d) <= _DETAIL_MAX else d[: _DETAIL_MAX - 1] + "…"
    if simulated:
        ev["simulated"] = True
    _EVENTS.append(ev)
    for cb in list(_SUBSCRIBERS):
        try:
            res = cb(ev)
            if asyncio.iscoroutine(res):
                try:
                    asyncio.get_running_loop().create_task(res)
                except RuntimeError:     # gọi từ thread không có event loop
                    res.close()
        except Exception as exc:  # noqa: BLE001 — giám sát không được làm hỏng nghiệp vụ
            logger.debug("[Topology] subscriber lỗi: %s", exc)
    return ev


def emit(kind: str, **kw: Any) -> Optional[Dict[str, Any]]:
    """`publish` không bao giờ ném lỗi — gọi từ luồng nghiệp vụ (Telegram, email,
    webhook, sentinel…): giám sát hỏng không được làm hỏng tác vụ."""
    try:
        return publish(kind, **kw)
    except Exception as exc:  # noqa: BLE001
        logger.debug("[Topology] emit lỗi: %s", exc)
        return None


def recent(limit: int = 100, since_seq: int = 0) -> List[Dict[str, Any]]:
    items = [e for e in _EVENTS if e["seq"] > since_seq]
    return items[-limit:]


def subscribe(cb: Callable[[Dict[str, Any]], Any]) -> None:
    if cb not in _SUBSCRIBERS:
        _SUBSCRIBERS.append(cb)


def unsubscribe(cb: Callable[[Dict[str, Any]], Any]) -> None:
    if cb in _SUBSCRIBERS:
        _SUBSCRIBERS.remove(cb)


def clear() -> None:
    """Chỉ dùng trong test."""
    _EVENTS.clear()
