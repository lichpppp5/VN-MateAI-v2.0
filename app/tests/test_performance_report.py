# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 Dương Thanh Lịch — VN-MateAI. See LICENSE and NOTICE.
"""Báo cáo hiệu năng theo tháng từ dữ liệu lưu bền (thẩm định thương mại P1-15)."""
from __future__ import annotations

from datetime import datetime

from mateai.application.monitoring import performance_report as pr
from mateai.infrastructure.database.db_manager import db_manager


def test_months_back_crosses_year():
    assert pr._months_back(3, datetime(2027, 1, 15)) == ["2026-11", "2026-12", "2027-01"]


def test_monthly_numbers_from_traces():
    for ttl, model, req in ((4000, "a", "a"), (6000, "b", "a"), (20000, "a", "a")):
        db_manager.add_voice_trace({"trace_id": f"trace_pr_{ttl}_{model}", "channel": "hud", "outcome": "llm", "ttl_ms": ttl, "ttfa_answer_ms": ttl / 2,
                                    "model": model, "model_requested": req, "prompt_chars": 9000})
    row = pr.monthly(1)[-1]
    assert row["month"] == datetime.now().strftime("%Y-%m") and row["voice_turns"] >= 3
    assert row["turn_ms_p50"] is not None and row["model_fallback_rate"] is not None
    csv_bytes = pr.to_csv(pr.monthly(2))
    assert b"turn_ms_p50" in csv_bytes.splitlines()[0]


def test_executive_summary_counts_this_month_and_what_waits_for_a_decision():
    import uuid
    from mateai.application.security.zero_trust import hitl_manager
    from mateai.application.tasks import ledger
    base = pr.executive()
    tid = ledger.open_task(f"Sự cố thử {uuid.uuid4().hex[:6]}", kind="incident")
    ledger.transition(tid, ledger.ESCALATED)
    hitl_manager.request_approval(f"thu_exec_{uuid.uuid4().hex[:6]}", {"x": 1}, requested_by="an", risk_level=4)
    out = pr.executive()
    assert out["month"] == datetime.now().strftime("%Y-%m") and out["prev_month"] < out["month"]
    assert out["incidents_open"] == base["incidents_open"] + 1
    assert out["approvals_pending"] == base["approvals_pending"] + 1
    assert out["approvals_high_risk"] == base["approvals_high_risk"] + 1
    assert out["attention_total"] == base["attention_total"] + 1 and len(out["attention"]) <= 5
    assert out["hours_saved_est"] == round(out["ai_completed"] * out["hours_per_task"], 1)
