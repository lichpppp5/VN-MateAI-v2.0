# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 Dương Thanh Lịch — VN-MateAI. See LICENSE and NOTICE.
"""
tests/test_edition_commercial.py
================================
Thẩm định thương mại 14/10/2026 — các mục sửa ngay trong mã:
  P0-2  bản thương mại: AI không sinh / cài mã (create_new_skill, install_skill_from_url, auto_execute);
  P0-3  ẩn kỹ năng demo / cá nhân;
  P0-4  không model miễn phí; che dữ liệu cá nhân trước khi gửi LLM ngoài, kể cả admin;
  P0-10 /docs /redoc /openapi.json đóng; health-dashboard cần đăng nhập; header bảo mật;
  P0-12 kênh có màn hình chỉ đọc to vài câu đầu (một lượt từng đọc 16 câu, 31 s);
  P1-15 trace thoại giữ 13 tháng.
Bản nội bộ (máy chủ dự án) giữ nguyên hành vi đã chốt.
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from mateai.config import edition


@pytest.fixture
def commercial(monkeypatch):
    monkeypatch.setenv("VNMATEAI_EDITION", "commercial")
    yield


def test_internal_edition_changes_nothing(monkeypatch):
    monkeypatch.delenv("VNMATEAI_EDITION", raising=False)
    assert edition.current() == "internal"
    for tool in ("create_new_skill", "install_skill_from_url", "open_youtube", "get_financial_summary"):
        assert edition.blocked_reason(tool) == ""
    assert edition.auto_execute_allowed() and edition.model_allowed("openrouter/x/y:free")


def test_commercial_blocks_codegen_and_demo_skills(commercial, monkeypatch):
    assert "sinh hay cài mã" in edition.blocked_reason("create_new_skill")
    assert edition.blocked_reason("install_skill_from_url") and edition.blocked_reason("reload_all_skills")
    assert "demo" in edition.blocked_reason("simulate_incoming_customer_email")
    assert edition.blocked_reason("get_infra_status") == ""
    from mateai.config.loader import settings
    monkeypatch.setattr(settings, "edition_allow_skills", ["get_financial_summary"], raising=False)
    assert edition.blocked_reason("get_financial_summary") == ""          # khách hàng có nguồn ERP thật


def test_policy_denies_blocked_tools_for_everyone(commercial):
    from mateai.application.security import policy_engine as pe
    for agent in ("ai", pe.HUMAN_DIRECT):
        d = pe.authorize("create_new_skill", {}, caller="admin", agent_id=agent, check_rbac=False)
        assert d.effect == pe.DENY and d.rule == "edition", (agent, d)
    assert pe.authorize("get_infra_status", {}, caller="admin", agent_id="ai", check_rbac=False).rule != "edition"


def test_catalog_and_execution_hide_blocked_skills(commercial):
    import asyncio
    from core.plugin_manager import plugin_manager
    names = {t["function"]["name"] for t in plugin_manager.get_all_tools()}
    assert "create_new_skill" not in names and "open_youtube" not in names
    assert "get_infra_status" in names or "list_playbooks" in names
    res = asyncio.run(plugin_manager.execute_skill("open_youtube", {}))
    assert res["success"] is False and "demo" in res["error"]


def test_auto_execute_forced_off(commercial, monkeypatch):
    from mateai.application.skills import meta_architect
    monkeypatch.setattr(meta_architect.settings, "auto_execute", True, raising=False)
    assert meta_architect._auto_install_enabled() is False


def test_free_models_never_tried(commercial, monkeypatch):
    from mateai.infrastructure.llm.llm_provider import NineRouterLLMProvider
    p = NineRouterLLMProvider.__new__(NineRouterLLMProvider)
    p.primary_model = "ag/gemini-3.8-flash-low"
    p.router_models = ["openrouter/liquid/lfm-2.5-2.6b:free", "ag/claude-sonnet-4-6"]
    assert p._resolve_candidate_models() == ["ag/gemini-3.8-flash-low", "ag/claude-sonnet-4-6"]


def test_pii_masked_for_admin_before_external_llm(commercial):
    from mateai.application.security.pii_guard import can_view_pii, guard_result
    assert can_view_pii("admin") is False
    out, n = guard_result({"ho_ten": "Nguyễn Văn A", "so_dien_thoai": "0912345678"}, "admin")
    assert n >= 1 and "0912345678" not in str(out)


def test_internal_pii_switch_is_opt_in(monkeypatch):
    monkeypatch.delenv("VNMATEAI_EDITION", raising=False)
    import mateai.config.loader as loader
    real = loader.get_config_section
    monkeypatch.setattr(loader, "get_config_section",
                        lambda name: {"external_llm_mask_pii": True} if name == "privacy" else real(name))
    assert edition.mask_pii_for_llm() is True


# ── HTTP: tài liệu API, health-dashboard, header bảo mật ─────────────────────

@pytest.fixture
def client():
    from mateai.interfaces.http import server
    return TestClient(server.app)


def test_api_docs_closed_and_dashboard_needs_login(client):
    for path in ("/docs", "/redoc", "/openapi.json"):
        assert client.get(path).status_code == 404, path
    assert client.get("/api/v1/health-dashboard").status_code == 401


def test_security_headers_on_every_response(client):
    for path in ("/livez", "/api/v1/health-dashboard"):
        h = client.get(path).headers
        assert "frame-ancestors 'none'" in h["content-security-policy"] and "object-src 'none'" in h["content-security-policy"]
        assert h["x-frame-options"] == "DENY" and h["x-content-type-options"] == "nosniff"
        assert h["referrer-policy"] == "no-referrer" and "microphone=(self)" in h["permissions-policy"]
        assert "strict-transport-security" not in h       # tự ký: HSTS sẽ khoá người dùng ngoài; bật bằng security.hsts


def test_api_version_is_the_project_version(monkeypatch):
    from mateai import __version__
    from mateai.interfaces.http import server
    assert server.app.version == __version__


# ── Giọng nói: chỉ đọc vài câu đầu ở kênh có màn hình ─────────────────────────

def test_spoken_limit_only_for_screen_channels(monkeypatch):
    from mateai.application.voice import voice_turn
    assert voice_turn.spoken_sentence_limit("hud") == 4
    assert voice_turn.spoken_sentence_limit("portal") == 4
    assert voice_turn.spoken_sentence_limit("vnmate_robot_01") == 0      # robot không có màn hình: đọc đủ
    import mateai.config.loader as loader
    monkeypatch.setattr(loader, "get_config_section",
                        lambda name: {"max_spoken_sentences": 0} if name == "voice" else {})
    assert voice_turn.spoken_sentence_limit("hud") == 0


from tests.test_realtime_voice_llm_turn import _run, turn  # noqa: E402,F401  (dùng lại fixture lượt LLM giả lập)

LONG = " ".join(f"Dòng kiểm tra số {i} vẫn bình thường." for i in range(1, 11))


async def test_long_answer_reads_four_sentences_then_points_to_screen(turn, monkeypatch):
    import tests.test_realtime_voice_llm_turn as base
    monkeypatch.setattr(base, "REPLY", LONG)
    spoken = []

    async def record(self, text, *a, **k):
        spoken.append(text)
        yield ("MP3:" + text).encode("utf-8") * 20
    from mateai.infrastructure.tts.tts_stream_engine import TTSStreamEngine
    monkeypatch.setattr(TTSStreamEngine, "stream", record)
    await _run(turn, "Báo cáo tình trạng hệ thống")
    shown = " ".join(e.get("text", "") for e in turn["ws"].events if e["type"] == "sentence_ready")
    assert "số 10" in shown                                               # chữ hiện đủ
    answer = [t for t in spoken if "Dòng kiểm tra" in t]
    assert len(answer) == 4 and spoken[-1] == "Chi tiết em đã hiện trên màn hình."


def test_voice_traces_kept_thirteen_months():
    import inspect
    from mateai.infrastructure.database.db_manager import DatabaseManager
    src = inspect.getsource(DatabaseManager.add_voice_trace)
    assert '"voice_trace_retention_days", 400' in src


def test_host_exec_tools_only_on_workstations(commercial):
    assert "máy trạm" in edition.server_exec_blocked_reason("run_powershell_command")
    assert edition.server_exec_blocked_reason("get_infra_status") == ""
    import inspect
    from mateai.application.agent import tool_gate
    assert "SERVER_EXEC_BLOCKED" in inspect.getsource(tool_gate)


def test_edge_tts_disabled_in_commercial(commercial):
    import asyncio
    from mateai.infrastructure.tts import tts_stream_engine as t
    assert t._edge_allowed() is False
    assert asyncio.run(t._synthesise_9router("xin chào", "vi-VN-HoaiMyNeural")) is None

    async def collect():
        return [c async for c in t._stream_edge_tts("xin chào", "vi-VN-HoaiMyNeural", "+0%")]
    assert asyncio.run(collect()) == []
