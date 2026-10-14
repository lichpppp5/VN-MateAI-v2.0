# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 Dương Thanh Lịch — VN-MateAI. See LICENSE and NOTICE.
"""
mateai/application/devfleet/scheduler.py
========================================
Chọn worker cho một tác vụ Dev (prompt §19–§21). Hàm THUẦN, xác định: cùng đầu vào -> cùng thứ hạng; lý do loại /
điểm của từng máy trả kèm để người duyệt thấy. LLM không chọn máy.

Loại cứng: máy bị tắt, không còn tươi, trạng thái không nhận việc, thiếu capability, sai nền tảng, đang bị khoá workspace.
Chấm điểm: ưa máy của dự án, máy rảnh, tải thấp, ít việc đang chạy. Số liệu không có (UNKNOWN) không bị phạt cũng
không được thưởng — và được ghi lại là `unknown_metrics`.
"""
from __future__ import annotations

from typing import Any, Dict, Iterable, List, Optional

from mateai.application.devfleet import models as m

_ELIGIBLE = (m.ONLINE, m.IDLE)


def rank_workers(spec: m.TaskSpec, workers: Iterable[Dict[str, Any]], *, preferred: Optional[List[str]] = None,
                 active_runs: Optional[Dict[str, int]] = None, locked_workers: Optional[Iterable[str]] = None
                 ) -> Dict[str, Any]:
    """-> {"candidates": [{worker_id, score, reasons}], "rejected": [{worker_id, reason}]} (candidates giảm dần theo điểm)."""
    preferred = list(preferred or [])
    active_runs = active_runs or {}
    locked = set(locked_workers or [])
    candidates: List[Dict[str, Any]] = []
    rejected: List[Dict[str, Any]] = []

    for w in sorted(workers, key=lambda x: x["worker_id"]):
        wid = w["worker_id"]

        def reject(reason: str) -> None:
            rejected.append({"worker_id": wid, "reason": reason})

        if spec.worker_id and wid != spec.worker_id:
            reject(f"người giao chỉ định máy {spec.worker_id}")
            continue
        state = w.get("state")
        if state == m.DISABLED:
            reject("máy đang bị tắt thủ công")
            continue
        if w.get("freshness") == m.STALE:
            reject("dữ liệu về máy đã cũ (STALE) — không đủ bằng chứng là còn sống")
            continue
        eligible = _ELIGIBLE + ((m.BUSY,) if spec.allow_busy else ())
        if state not in eligible:
            reject(f"trạng thái {state} không nhận việc")
            continue
        missing = m.missing_capabilities(spec.required_capabilities, w.get("capabilities") or [])
        if missing:
            reject("thiếu capability: " + ", ".join(missing))
            continue
        if spec.required_platform and (w.get("platform") or "") != spec.required_platform:
            reject(f"nền tảng {w.get('platform') or 'không rõ'} ≠ {spec.required_platform}")
            continue
        if wid in locked:
            reject("workspace đang bị tác vụ khác thuê")
            continue

        score, reasons, unknown = 100.0, [], []
        if wid in preferred:
            bonus = 30.0 if preferred.index(wid) == 0 else 15.0
            score += bonus
            reasons.append(f"+{bonus:g} máy ưa dùng của dự án")
        if state == m.IDLE:
            score += 10.0
            reasons.append("+10 đang rảnh")
        elif state == m.BUSY:
            score -= 40.0
            reasons.append("-40 đang bận (task cho phép xếp hàng)")
        cpu = w.get("cpu_percent")
        if cpu is None:
            unknown.append("cpu")
        else:
            score -= cpu / 5.0
            reasons.append(f"-{cpu / 5.0:.1f} CPU {cpu:.0f}%")
        if w.get("memory_percent") is None:
            unknown.append("memory")
        elif w["memory_percent"] > 90:
            score -= 15.0
            reasons.append("-15 RAM > 90%")
        if w.get("disk_percent") is None:
            unknown.append("disk")
        elif w["disk_percent"] > 95:
            score -= 25.0
            reasons.append("-25 đĩa > 95%")
        n_active = int(active_runs.get(wid, 0))
        if n_active:
            score -= 10.0 * n_active
            reasons.append(f"-{10 * n_active} đang có {n_active} lượt chạy")
        candidates.append({"worker_id": wid, "score": round(score, 2), "reasons": reasons, "unknown_metrics": unknown})

    candidates.sort(key=lambda c: (-c["score"], c["worker_id"]))
    return {"candidates": candidates, "rejected": rejected}
