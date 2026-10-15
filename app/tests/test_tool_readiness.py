# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 Dương Thanh Lịch — VN-MateAI. See LICENSE and NOTICE.
"""
tests/test_tool_readiness.py
============================
Thẩm định 14/10/2026 §7 / §5.3: AI gọi công cụ Prometheus / AWS… trên máy chưa khai báo nguồn → phần lớn lượt thất bại;
kịch bản chạy rồi mới hỏng ở bước kiểm chứng. Nay: công cụ chưa có nguồn không tới LLM, kịch bản bị chặn ngay khi lập kế hoạch.
"""
from __future__ import annotations

import pytest

from mateai.application.skills import tool_readiness as tr


@pytest.fixture
def real_checks(monkeypatch):
    calls = {"prom": "chưa khai báo nguồn Prometheus", "aws": ""}
    monkeypatch.setattr(tr, "REQUIREMENTS", {"query_prometheus": lambda: calls["prom"],
                                             "check_aws_cost": lambda: calls["aws"]})
    tr.clear_cache()
    return calls


def _tool(name):
    return {"type": "function", "function": {"name": name, "description": "", "parameters": {"type": "object", "properties": {}}}}


def test_unconfigured_tools_are_not_offered(real_checks):
    tools = [_tool("query_prometheus"), _tool("check_aws_cost"), _tool("list_playbooks")]
    assert [t["function"]["name"] for t in tr.ready_tools(tools)] == ["check_aws_cost", "list_playbooks"]
    assert tr.unready() == {"query_prometheus": "chưa khai báo nguồn Prometheus"}


def test_llm_never_sees_unready_tools(real_checks):
    from mateai.application.agent.llm_engine import LLMEngine
    out = LLMEngine._enrich_tools_with_target_client([_tool("query_prometheus"), _tool("check_aws_cost")])
    assert [t["function"]["name"] for t in out] == ["check_aws_cost"]
    assert "target_client" in out[0]["function"]["parameters"]["properties"]


def test_result_is_cached_then_refreshed(real_checks, monkeypatch):
    assert tr.ready_reason("query_prometheus")
    real_checks["prom"] = ""
    assert tr.ready_reason("query_prometheus")               # còn trong bộ đệm
    tr.clear_cache()
    assert tr.ready_reason("query_prometheus") == ""          # nguồn vừa cấu hình → công cụ quay lại


def test_check_error_does_not_hide_the_tool(monkeypatch):
    def broken():
        raise RuntimeError("registry hỏng")
    monkeypatch.setattr(tr, "REQUIREMENTS", {"x": broken})
    tr.clear_cache()
    assert tr.ready_reason("x") == ""


def test_playbook_blocked_when_a_step_source_is_missing(real_checks):
    from mateai.application.playbooks import engine
    defn = {"id": "kiem-tra-ha-tang", "params": {}, "steps": [
        {"id": "s1", "title": "đo", "tool": "query_prometheus", "args": {"promql": "up"}, "on_failure": "stop",
         "when": None, "rollback": None}], "verify": []}
    pl = engine.plan(defn, {}, caller="admin")
    assert pl["blocked"] and pl["denied"][0]["rule"] == "source_missing"
    assert "Prometheus" in pl["denied"][0]["reason"]


def test_real_requirement_map_covers_connector_tools():
    import importlib
    import mateai.application.skills.tool_readiness as fresh
    importlib.reload(fresh)
    for name in ("query_prometheus", "check_aws_cost", "check_einvoice_daily", "search_paperless_documents",
                 "check_oci_metrics", "fetch_data_source", "send_telegram_message"):
        assert name in fresh.REQUIREMENTS
