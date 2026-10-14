# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 Dương Thanh Lịch — VN-MateAI. See LICENSE and NOTICE.
"""
mateai/application/ethics/fairness.py
=====================================
Báo cáo CÔNG BẰNG khi giao việc / đánh giá (đạo đức AI, điểm 4 — 2026-10-12).

Hệ thống có giao việc (một phần do AI: `assign_task_intelligently`, `tasks.created_by_ai`) và bảng xếp hạng theo số việc hoàn
thành. Báo cáo này cho người quản lý thấy chỗ CẦN XEM LẠI — không kết luận là có phân biệt đối xử:
  - DỒN VIỆC: một người nhận việc ≥ OVERLOAD_RATIO × trung vị phòng mình (và ≥ MIN_TASKS việc) — tính riêng cả việc do AI giao;
  - CHÊNH LỆCH NHÓM: tỉ lệ hoàn thành của một phòng ban / chức danh lệch trung bình chung ≥ GAP_POINTS điểm %;
  - NGƯỜI KHÔNG ĐƯỢC GIAO VIỆC trong khi cả phòng đều có việc (bị bỏ sót khi phân việc).
Dữ liệu hiện chỉ có phòng ban và chức danh — KHÔNG có giới tính / tuổi (hệ thống không thu thập, và không được dùng để đánh giá).
Chạy định kỳ: tạo Kịch bản vận hành có lịch (vd ngày 1 hằng tháng) gọi công cụ `get_hr_fairness_report`.
"""
from __future__ import annotations

from statistics import median
from typing import Any, Dict, List

OVERLOAD_RATIO = 2.0
MIN_TASKS = 5
GAP_POINTS = 20.0
MIN_GROUP = 2


def _rate(done: int, total: int) -> float | None:
    return round(100.0 * done / total, 1) if total else None


def _group(rows: List[Dict[str, Any]], key: str) -> Dict[str, Dict[str, Any]]:
    out: Dict[str, Dict[str, Any]] = {}
    for r in rows:
        g = out.setdefault(str(r.get(key) or "(chưa rõ)"), {"people": 0, "total": 0, "completed": 0, "ai_assigned": 0, "overdue": 0})
        g["people"] += 1
        for k in ("total", "completed", "ai_assigned", "overdue"):
            g[k] += int(r.get(k) or 0)
    for g in out.values():
        g["completion_rate"] = _rate(g["completed"], g["total"])
        g["tasks_per_person"] = round(g["total"] / g["people"], 1) if g["people"] else 0
    return out


def build_fairness_report(rows: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Từ khối lượng việc từng người (erp_db.get_workload_by_employee) → nhóm + các điểm cần người quản lý xem lại."""
    rows = [dict(r) for r in rows or []]
    findings: List[Dict[str, Any]] = []
    total = sum(int(r.get("total") or 0) for r in rows)
    done = sum(int(r.get("completed") or 0) for r in rows)
    overall = _rate(done, total)

    by_dept: Dict[str, List[Dict[str, Any]]] = {}
    for r in rows:
        by_dept.setdefault(str(r.get("dept_name") or "(chưa rõ)"), []).append(r)
    for dept, people in by_dept.items():
        if len(people) < MIN_GROUP:
            continue
        for field, label in (("total", "việc"), ("ai_assigned", "việc do AI giao")):
            med = median(int(p.get(field) or 0) for p in people)
            for p in people:
                n = int(p.get(field) or 0)
                if n >= MIN_TASKS and n >= OVERLOAD_RATIO * max(med, 1):
                    findings.append({"type": "overload", "severity": "warn", "department": dept, "employee": p.get("name"),
                                     "detail": f"{p.get('name')} nhận {n} {label}, gấp {n / max(med, 1):.1f} lần trung vị phòng ({med:g})."})
        idle = [p for p in people if not int(p.get("total") or 0)]
        busy = [p for p in people if int(p.get("total") or 0) >= MIN_TASKS]
        if idle and len(busy) >= MIN_GROUP:
            findings.append({"type": "left_out", "severity": "info", "department": dept,
                             "employee": ", ".join(str(p.get("name")) for p in idle[:5]),
                             "detail": f"{len(idle)} người chưa được giao việc nào trong khi {len(busy)} người khác của phòng "
                                       f"có từ {MIN_TASKS} việc trở lên."})

    groups = {"department": _group(rows, "dept_name"), "position": _group(rows, "position")}
    if overall is not None:
        for dim, label in (("department", "Phòng"), ("position", "Chức danh")):
            for name, g in groups[dim].items():
                if g["people"] >= MIN_GROUP and g["total"] >= MIN_TASKS and g["completion_rate"] is not None \
                        and abs(g["completion_rate"] - overall) >= GAP_POINTS:
                    findings.append({"type": "group_gap", "severity": "info", dim: name,
                                     "detail": f"{label} {name}: tỉ lệ hoàn thành {g['completion_rate']:g}% so với "
                                               f"trung bình chung {overall:g}% (lệch {g['completion_rate'] - overall:+.1f} điểm)."})
    return {
        "people": len(rows), "tasks": total, "completion_rate": overall,
        "groups": groups, "findings": findings,
        "thresholds": {"overload_ratio": OVERLOAD_RATIO, "min_tasks": MIN_TASKS, "gap_points": GAP_POINTS},
        "note": ("Chênh lệch KHÔNG có nghĩa là thiên kiến — có thể do khác loại việc, thời điểm, năng lực. Đây là danh sách "
                 "CẦN NGƯỜI QUẢN LÝ XEM LẠI; mọi quyết định về con người do người có thẩm quyền đưa ra. Hệ thống không có và "
                 "không dùng giới tính, tuổi, dân tộc… để đánh giá."),
    }
