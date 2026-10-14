# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 Dương Thanh Lịch — VN-MateAI. See LICENSE and NOTICE.
"""
tests/test_auto_skill_discovery.py
==================================
Kỹ năng do AI tự tạo phải được trợ lý TỰ dùng, không cần người dùng nhắc tên.

Trước đây (tái hiện với `auto_play_music`, `thong_ke_lo_xsmb` trên máy thật):
  - bộ phân loại ý định là danh sách từ khoá viết tay → "mở bài hát Lạc Trôi"
    bị coi là trò chuyện → model không được đưa tool nào;
  - bộ định tuyến khớp chuỗi con trong các miền đoán theo từ khoá → skill ở
    nhóm chung bị loại, "mở bài hát…" chọn nhầm skill xổ số;
  - mô tả skill do AI sinh bằng tiếng Anh → câu tiếng Việt không khớp;
  - skill vừa tạo không gọi được trong cùng lượt (danh sách tool lấy 1 lần);
  - đường tự tạo khi model nói "không có công cụ" không qua RBAC.
Không gọi mạng, không sinh mã thật.
"""
from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from mateai.application.agent import llm_engine as le
from mateai.application.agent.llm_engine import llm_engine
from mateai.application.security.security_guard import security_guard
from mateai.application.skills.meta_architect import MetaArchitect
from mateai.application.skills.skill_router import STRONG_MATCH_SCORE, DynamicSkillRouter


def _tool(name, desc):
    return {"type": "function", "function": {"name": name, "description": desc,
                                             "parameters": {"type": "object", "properties": {}}}}


CATALOG = [
    _tool("auto_play_music", "Search a song on YouTube and play it. Ví dụ yêu cầu: «mở bài hát Lạc Trôi trên youtube»."),
    _tool("thong_ke_lo_xsmb", "Crawl kết quả XSMB 14 ngày, thống kê tần suất lô tô, lô gan."),
    _tool("get_active_processes", "Liệt kê tiến trình đang chạy, CPU, RAM."),
    _tool("kill_process", "Dừng tiến trình theo PID."),
    _tool("send_telegram_message", "Gửi tin nhắn cảnh báo lên Telegram cho quản trị viên."),
    _tool("create_new_skill", "Tạo KỸ NĂNG MỚI khi người dùng yêu cầu một VIỆC chưa có công cụ."),
]


@pytest.fixture
def router(monkeypatch):
    import core.plugin_manager as pm
    import mateai.application.skills.skill_router as sr
    monkeypatch.setattr(pm.plugin_manager, "get_all_tools", lambda: list(CATALOG))
    r = DynamicSkillRouter()
    r.rebuild_index()
    monkeypatch.setattr(sr, "dynamic_skill_router", r)
    return r


def _names(tools):
    return [t["function"]["name"] for t in tools]


@pytest.mark.parametrize("query,expected", [
    ("mở bài hát Lạc Trôi", "auto_play_music"),
    ("thống kê lô xsmb hai tuần", "thong_ke_lo_xsmb"),
    ("kiểm tra tiến trình chiếm CPU", "get_active_processes"),
])
def test_router_finds_skill_without_its_name(router, query, expected):
    assert _names(router.get_tools_for_query(query))[0] == expected
    assert router.best_match_score(query) >= STRONG_MATCH_SCORE


def test_word_match_not_substring(router):
    # "hat" (hát) không được khớp "chat"/"that" như trước.
    assert router.best_match_score("hát") < STRONG_MATCH_SCORE


def test_casual_talk_still_gets_no_tools(router):
    assert router.get_tools_for_query("Xin chào em nhé") == []


def test_classifier_treats_skill_request_as_operation(router):
    assert llm_engine.classify_intent("mở bài hát Lạc Trôi")["type"] == "operation"
    assert llm_engine.classify_intent("Xin chào Ly Ly")["type"] == "conversation"


def test_generated_skill_carries_user_phrasing():
    code = (
        "from core.plugin_manager import export_skill\n\n"
        "@export_skill(name='play', description='Play a song on YouTube.', parameters_schema={})\n"
        "def play() -> dict:\n    return {}\n"
    )
    out = MetaArchitect._with_user_phrasing(code, "mở bài hát Lạc Trôi")
    assert "«mở bài hát Lạc Trôi»" in out
    assert MetaArchitect._with_user_phrasing(out, "mở bài hát Lạc Trôi") == out


@pytest.mark.parametrize("role,allowed", [("admin", True), ("operator", False), ("viewer", False)])
def test_only_admin_may_create_skills(monkeypatch, role, allowed):
    monkeypatch.setattr(security_guard, "resolve_role", lambda who: role)
    assert le._may_create_skills("someone") is allowed


def _resp(content="", tool_calls=None):
    msg = SimpleNamespace(content=content, tool_calls=tool_calls, reasoning="", reasoning_content="")
    return SimpleNamespace(model="fake", choices=[SimpleNamespace(
        message=msg, finish_reason="tool_calls" if tool_calls else "stop")])


def _call(name, args):
    return SimpleNamespace(id=f"call_{name}", type="function",
                           function=SimpleNamespace(name=name, arguments=json.dumps(args)))


async def test_non_admin_cannot_trigger_automatic_synthesis(monkeypatch):
    called = []
    monkeypatch.setattr(security_guard, "resolve_role", lambda who: "operator")
    monkeypatch.setattr(llm_engine, "_call_llm", _async(lambda **k: _resp("Em không có công cụ để làm việc này.")))
    monkeypatch.setattr(type(llm_engine), "_synthesise_and_install", staticmethod(lambda **k: called.append(k) or "x"))
    await llm_engine.ask_async("mở bài hát Lạc Trôi", source_device="web-widget", caller="viewer_bob", session_id="t1")
    assert called == []


async def test_new_skill_is_callable_in_the_same_turn(monkeypatch):
    """create_new_skill → danh sách tool làm mới → model gọi skill mới ngay."""
    import core.plugin_manager as pm
    import mateai.application.agent.tool_gate as tg

    catalog = [_tool("create_new_skill", "Tạo kỹ năng mới")]
    monkeypatch.setattr(pm.plugin_manager, "get_all_tools", lambda: list(catalog))
    seen_tools, ran = [], []

    async def fake_gate(fn_name, fn_args, **kw):
        ran.append(fn_name)
        if fn_name == "create_new_skill":
            catalog.append(_tool("auto_play_music", "Phát nhạc"))
            return {"target_client": "master", "args": fn_args,
                    "result": {"success": True, "data": {"status": "success", "skill_name": "auto_play_music"}, "error": None}}
        return {"target_client": "master", "args": fn_args, "result": {"success": True, "data": {"status": "success"}, "error": None}}

    script = iter([
        _resp(tool_calls=[_call("create_new_skill", {"intent_description": "mở bài hát Lạc Trôi"})]),
        _resp(tool_calls=[_call("auto_play_music", {})]),
        _resp("Dạ, em đã mở bài hát."),
    ])

    async def fake_call_llm(messages, tools=None, **kw):
        seen_tools.append(_names(tools or []))
        return next(script)

    monkeypatch.setattr(tg, "run_tool_with_policy", fake_gate)
    monkeypatch.setattr(llm_engine, "_call_llm", fake_call_llm)
    res = await llm_engine.ask_async("mở bài hát Lạc Trôi", source_device="portal", caller="admin", session_id="t2")
    assert ran == ["create_new_skill", "auto_play_music"]
    assert "auto_play_music" not in seen_tools[0] and "auto_play_music" in seen_tools[1]
    assert "mở bài hát" in res["reply"]


def _async(fn):
    async def wrapper(*a, **k):
        return fn(*a, **k)
    return wrapper


def test_create_new_skill_returns_existing_skill_instead_of_duplicate(router, monkeypatch):
    import importlib
    import mateai.application.skills.meta_architect as ma
    meta_skills = importlib.import_module("skills.meta_skills")
    monkeypatch.setattr(ma.meta_architect, "synthesize_skill",
                        lambda **k: pytest.fail("không được sinh mã khi đã có kỹ năng"))
    out = meta_skills.create_new_skill("mở bài hát Lạc Trôi trên youtube")
    assert out["status"] == "success" and out["already_exists"] and out["skill_name"] == "auto_play_music"


def test_automatic_synthesis_reuses_existing_skill(router, monkeypatch):
    import mateai.application.skills.meta_architect as ma
    monkeypatch.setattr(ma.meta_architect, "synthesize_skill",
                        lambda **k: pytest.fail("không được sinh mã khi đã có kỹ năng"))
    assert llm_engine._synthesise_and_install(query="mở bài hát Lạc Trôi", meta_architect=ma.meta_architect,
                                              plugin_manager=None) == "auto_play_music"



@pytest.fixture
def style_router(monkeypatch):
    """Danh mục có một skill mà mô tả chứa "ngắn gọn" — như skill xuất dữ liệu thật."""
    import core.plugin_manager as pm
    import mateai.application.skills.skill_router as sr
    extra = [_tool("prepare_data_source_export", "Chuẩn bị bản xuất dữ liệu, kèm tóm tắt ngắn gọn từng câu."),
             _tool("get_executive_standup_briefing", "Tóm tắt tình hình máy chủ và dịch vụ cho lãnh đạo.")]
    monkeypatch.setattr(pm.plugin_manager, "get_all_tools", lambda: list(CATALOG) + extra)
    r = DynamicSkillRouter()
    r.rebuild_index()
    monkeypatch.setattr(sr, "dynamic_skill_router", r)
    return r


@pytest.mark.parametrize("query", [
    "Giải thích ngắn gọn RAID 1 là gì trong hai câu.",
    "Giải thích chi tiết giao thức TCP bằng ví dụ dễ hiểu",
])
def test_answer_style_words_do_not_make_knowledge_question_an_operation(style_router, query):
    # "ngắn gọn", "trong hai câu"… nói CÁCH trả lời — trước đây khớp mô tả skill
    # xuất dữ liệu trên ngưỡng khớp rõ (bench Phase 1: 6,6 điểm).
    assert style_router.best_match_score(query) < STRONG_MATCH_SCORE
    assert llm_engine.classify_intent(query)["type"] != "operation"


def test_answer_style_words_do_not_hide_the_task(style_router):
    names = _names(style_router.get_tools_for_query("tóm tắt ngắn gọn tình hình máy chủ"))
    assert names[0] == "get_executive_standup_briefing"



@pytest.mark.parametrize("intent,expected", [
    ("Lấy dữ liệu ngày âm lịch hôm nay", None),
    ("Tra cứu ngày âm lịch của ngày hôm nay", None),
    ("Tra cứu tỷ giá USD sang VND", None),
    ("gửi tin nhắn telegram cho quản trị viên", "send_telegram_message"),
    ("liệt kê tiến trình đang chạy chiếm CPU", "get_active_processes"),
])
def test_existing_skill_check_on_real_catalog(intent, expected):
    """Danh mục THẬT (skills/ trong repo): động từ chung ("tra cứu", "lấy dữ
    liệu") khớp mô tả một tool dữ liệu đủ để ĐƯA tool cho model, nhưng không
    phải là đã có kỹ năng làm việc đó — trước đây kỹ năng âm lịch không được
    tạo vì bị coi là trùng `fetch_data_source`. Ngưỡng hiệu chỉnh cho danh mục
    cỡ thật (~80 tool); danh mục vài tool có thang điểm khác."""
    from mateai.application.skills.skill_router import dynamic_skill_router, find_existing_skill
    dynamic_skill_router.rebuild_index()
    assert find_existing_skill(intent) == expected
