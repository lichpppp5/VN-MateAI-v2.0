# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 Dương Thanh Lịch — VN-MateAI. See LICENSE and NOTICE.
"""
mateai/application/monitoring/performance_report.py
===================================================
Báo cáo hiệu năng theo THÁNG từ dữ liệu lưu bền (thẩm định thương mại 14/10/2026 P1-15: số đo phải đủ cho SLA, không mất khi
khởi động lại). Nguồn: `voice_traces` (giữ 400 ngày), `op_tasks` (sổ tác vụ AI), `llm_usage` (token / chi phí).

Mỗi tháng: số lượt thoại, trung vị / p90 thời gian trọn lượt, trung vị tới tiếng trả lời đầu, tỉ lệ rơi sang model dự phòng,
độ dài prompt trung bình, tỉ lệ tác vụ AI thành công (COMPLETED / đã kết thúc), token và chi phí (theo giá đã khai).
"""
from __future__ import annotations

import csv
import io
import json
import statistics
from collections import defaultdict
from datetime import datetime
from typing import Any, Dict, List, Optional

FINISHED = {"COMPLETED", "FAILED", "ESCALATED", "BLOCKED", "CANCELLED"}


def _pct(values: List[float], q: float) -> Optional[float]:
    if not values:
        return None
    v = sorted(values)
    return v[max(0, min(len(v) - 1, round(q * (len(v) - 1))))]


def _months_back(n: int, now: Optional[datetime] = None) -> List[str]:
    now = now or datetime.now()
    y, m = now.year, now.month
    out = []
    for _ in range(max(1, n)):
        out.append(f"{y:04d}-{m:02d}")
        m -= 1
        if m == 0:
            y, m = y - 1, 12
    return list(reversed(out))


def monthly(months: int = 13, now: Optional[datetime] = None) -> List[Dict[str, Any]]:
    from mateai.infrastructure.database import governance_repo
    keys = _months_back(months, now)
    first = keys[0]
    turns: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    tasks: Dict[str, Dict[str, int]] = defaultdict(lambda: defaultdict(int))
    usage: Dict[str, Dict[str, float]] = defaultdict(lambda: {"tokens": 0, "cost": 0.0})
    trace_rows, task_rows, usage_rows = governance_repo.performance_rows(f"{first}-01")
    for created, data_json in trace_rows:
        try:
            turns[created[:7]].append(json.loads(data_json))
        except (TypeError, ValueError):
            continue
    for created, st in task_rows:
        tasks[created[:7]][st] += 1
    for day, tokens, cost in usage_rows:
        u = usage[day[:7]]
        u["tokens"] += tokens
        u["cost"] += cost
    rows = []
    for k in keys:
        t = turns.get(k, [])
        ttl = [float(x["ttl_ms"]) for x in t if isinstance(x.get("ttl_ms"), (int, float))]
        ttfa = [float(x["ttfa_answer_ms"]) for x in t if isinstance(x.get("ttfa_answer_ms"), (int, float))]
        llm_turns = [x for x in t if x.get("model") and x.get("model_requested")]
        fallback = sum(1 for x in llm_turns if x["model"] != x["model_requested"])
        prompts = [int(x["prompt_chars"]) for x in t if isinstance(x.get("prompt_chars"), int)]
        st = tasks.get(k, {})
        finished = sum(v for s, v in st.items() if s in FINISHED)
        rows.append({
            "month": k, "voice_turns": len(t),
            "turn_ms_p50": _pct(ttl, 0.5), "turn_ms_p90": _pct(ttl, 0.9),
            "first_answer_audio_ms_p50": _pct(ttfa, 0.5),
            "model_fallback_rate": round(fallback / len(llm_turns), 3) if llm_turns else None,
            "prompt_chars_avg": round(statistics.mean(prompts)) if prompts else None,
            "ai_tasks": sum(st.values()), "ai_tasks_finished": finished,
            "ai_success_rate": round(st.get("COMPLETED", 0) / finished, 3) if finished else None,
            "llm_tokens": int(usage[k]["tokens"]) if k in usage else 0,
            "llm_cost": round(usage[k]["cost"], 4) if k in usage else 0.0,
        })
    return rows


def executive(now: Optional[datetime] = None) -> Dict[str, Any]:
    """Tổng quan cho giám đốc (thẩm định #22): tháng này so với tháng trước bằng ngôn ngữ kinh doanh + việc đang chờ
    người quyết định. Không số nào ước đoán ngoài `hours_saved_est` (ghi rõ giả định giờ / tác vụ)."""
    from mateai.application.security.zero_trust import hitl_manager
    from mateai.application.tasks import ledger
    from mateai.infrastructure.database.erp_database import HOURS_SAVED_PER_AI_TASK

    prev, cur = monthly(2, now)
    def _done(r: Dict[str, Any]) -> int:
        return round((r["ai_success_rate"] or 0) * r["ai_tasks_finished"])

    try:
        pending = hitl_manager.get_pending_list()
    except Exception:  # noqa: BLE001 — thiếu HITL không làm hỏng cả trang
        pending = []
    since = f"{cur['month']}-01 00:00:00"
    month_tasks = ledger.list_tasks(since=since, limit=2000)
    open_incidents = [t for t in ledger.list_tasks(kind="incident", limit=500) if t.get("status") not in ledger.TERMINAL]
    attention = [t for t in month_tasks if t.get("status") in ("ESCALATED", "FAILED", "WAITING_AUTHORIZATION")]
    return {
        "month": cur["month"], "prev_month": prev["month"],
        "ai_tasks": cur["ai_tasks"], "ai_tasks_prev": prev["ai_tasks"],
        "ai_completed": _done(cur), "ai_completed_prev": _done(prev),
        "success_rate": cur["ai_success_rate"], "success_rate_prev": prev["ai_success_rate"],
        "hours_saved_est": round(_done(cur) * HOURS_SAVED_PER_AI_TASK, 1), "hours_per_task": HOURS_SAVED_PER_AI_TASK,
        "llm_cost": cur["llm_cost"], "llm_cost_prev": prev["llm_cost"],
        "approvals_pending": len(pending),
        "approvals_high_risk": sum(1 for a in pending if int(a.get("risk_level") or 0) >= 4),
        "incidents_open": len(open_incidents),
        "attention_total": len(attention),
        "attention": [{"task_id": t.get("task_id"), "title": t.get("title"), "status": t.get("status"),
                       "updated_at": t.get("updated_at")} for t in attention[:5]],
    }


def to_csv(rows: List[Dict[str, Any]]) -> bytes:
    buf = io.StringIO()
    if rows:
        w = csv.DictWriter(buf, fieldnames=list(rows[0]))
        w.writeheader()
        w.writerows(rows)
    return ("﻿" + buf.getvalue()).encode("utf-8")
