# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 Dương Thanh Lịch — VN-MateAI. See LICENSE and NOTICE.
"""
Đạo đức AI, điểm 4 (2026-10-12): AI không tự quyết định về CON NGƯỜI; báo cáo công bằng để người quản lý xem lại.
- Công cụ ra quyết định về người (kể cả kỹ năng AI tự tạo — nhận theo tên): tối thiểu L3, KHÔNG "duyệt một lần nhớ 30 ngày".
- Prompt: trình bày số liệu, không kết luận về người; không dùng đặc điểm được bảo vệ; giữ nguyên dữ liệu đã che.
- Quy tắc 2 cũ ("tự phê duyệt, không nói cần phê duyệt") thay bằng "báo đúng sự thật khi đang chờ duyệt".
"""
from __future__ import annotations

import pytest

from mateai.application.ethics.fairness import build_fairness_report
from mateai.application.security import policy_engine
from mateai.application.security.hr_guard import is_people_decision
from mateai.application.security.risk_engine import assess_risk


@pytest.mark.parametrize("name", ["evaluate_employee_performance", "rank_employees", "reject_candidate", "hire_candidate",
                                  "fire_employee", "update_salary", "set_bonus", "promote_staff", "danh_gia_nhan_vien",
                                  "ky_luat_nhan_vien", "screen_applicants", "adjustSalary"])
def test_people_decisions_need_a_human(name):
    assert is_people_decision(name) and assess_risk(name) >= 3


@pytest.mark.parametrize("name", ["get_executive_leaderboard", "get_attendance_report", "rank_servers", "update_ticket",
                                  "summarize_monthly_kpi", "list_employees", "get_hr_fairness_report"])
def test_reading_data_about_people_is_not_a_decision(name):
    assert not is_people_decision(name)
    if name.startswith(("get_", "list_")):
        assert assess_risk(name) == 1


def test_each_people_decision_is_approved_separately(monkeypatch):
    from mateai.application.security.security_guard import security_guard
    monkeypatch.setattr(policy_engine, "has_delegation", lambda caller, tool: True)       # đã từng "duyệt, nhớ 30 ngày"
    monkeypatch.setattr(security_guard, "check_permission", lambda **kw: (True, ""))
    people = policy_engine.authorize("rank_employees", {}, caller="device:robot-01", agent_id="robot")
    other = policy_engine.authorize("modify_service_config", {}, caller="device:robot-01", agent_id="robot")
    assert people.effect == policy_engine.REQUIRE_APPROVAL                               # vẫn phải duyệt lượt này
    assert other.effect == policy_engine.ALLOW and other.rule == "delegated"


def test_prompts_carry_the_ethics_rules_and_no_longer_hide_approvals():
    from mateai.application.agent import llm_engine as le
    for conversation in (True, False):
        p = le.build_system_prompt("portal", conversation=conversation)
        assert "[ĐẠO ĐỨC & CÔNG BẰNG]" in p and "giới tính" in p and "quyết định thuộc về người quản lý" in p
    agent = le._AGENT_SYSTEM_PROMPT
    assert "KHÔNG YÊU CẦU PHÊ DUYỆT" not in agent and "ĐANG CHỜ" in agent
    from mateai.application.skills.meta_architect import _CODE_GEN_SYSTEM_PROMPT
    assert "giới tính" in _CODE_GEN_SYSTEM_PROMPT and "quyết định do người" in _CODE_GEN_SYSTEM_PROMPT


def _p(name, dept, total, completed=0, ai=0, position="Nhân viên"):
    return {"name": name, "dept_name": dept, "position": position, "total": total, "completed": completed,
            "ai_assigned": ai, "overdue": 0}


def test_fairness_report_flags_overload_left_out_and_group_gaps():
    rows = [_p("An", "Kho", 20, 18, ai=15), _p("Bình", "Kho", 6, 5), _p("Chi", "Kho", 5, 4), _p("Dũng", "Kho", 0),
            _p("Em", "Kế toán", 8, 2), _p("Phương", "Kế toán", 8, 1),
            _p("Giang", "IT", 7, 7), _p("Hà", "IT", 6, 6)]
    r = build_fairness_report(rows)
    kinds = {(f["type"], f.get("employee") or f.get("department")) for f in r["findings"]}
    assert ("overload", "An") in kinds                                                  # gấp ≥ 2 lần trung vị phòng
    assert any(f["type"] == "overload" and "do AI giao" in f["detail"] for f in r["findings"])
    assert ("left_out", "Dũng") in kinds
    assert ("group_gap", "Kế toán") in kinds                                            # 3/16 so với trung bình chung
    assert "KHÔNG có nghĩa là thiên kiến" in r["note"] and r["groups"]["department"]["Kho"]["people"] == 4


def test_balanced_workload_has_no_findings():
    rows = [_p(f"NV{i}", d, 6, 5) for i, d in enumerate(["Kho", "Kho", "IT", "IT", "Kế toán", "Kế toán"])]
    assert build_fairness_report(rows)["findings"] == []


def test_fairness_tool_is_read_only_and_registered():
    from pathlib import Path
    src = Path("skills/ethics_tools.py").read_text(encoding="utf-8")
    assert 'name="get_hr_fairness_report"' in src and assess_risk("get_hr_fairness_report") == 1
