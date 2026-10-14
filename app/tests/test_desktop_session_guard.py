# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 Dương Thanh Lịch — VN-MateAI. See LICENSE and NOTICE.
"""
Sự cố 2026-10-08: máy chủ chạy ở phiên nền, robot ra lệnh mở YouTube -> tool báo thành công nhưng không cửa sổ nào hiện, AI gọi
create_new_skill 39 lần. Bảo vệ:
  1. Tool cần màn hình (`needs_desktop`, khai hoặc suy ra từ mã) bị từ chối RÕ RÀNG trên máy chủ không có màn hình — không chạy.
  2. Tool không cần màn hình, hoặc gửi sang máy trạm, chạy bình thường.
  3. create_new_skill: tối đa 3 lần / 10 phút cho mỗi danh tính.
"""
from __future__ import annotations

import pytest

from core.plugin_manager import _uses_desktop, export_skill, plugin_manager
from mateai.application.agent import tool_gate
from mateai.infrastructure import desktop_session

HEADLESS = {"interactive": False, "session_id": 0, "console_session_id": 1, "reason": "máy chủ đang chạy ở Session 0"}
DESKTOP = {"interactive": True, "session_id": 1, "console_session_id": 1, "reason": "phiên điều khiển (console)"}


def test_needs_desktop_is_inferred_from_code_and_can_be_declared():
    def opens_browser():
        import webbrowser
        webbrowser.open("https://example.com")

    def pure():
        return 1 + 1
    assert _uses_desktop(opens_browser) is True and _uses_desktop(pure) is False
    declared = export_skill(name="t_declared", description="d", parameters_schema={"type": "object", "properties": {}},
                            needs_desktop=False)(opens_browser)
    assert declared.__skill_meta__["contract"]["needs_desktop"] is False          # khai tường minh thắng suy luận


def test_known_gui_skills_are_flagged():
    plugin_manager._ensure_loaded()
    for name in ("open_youtube", "auto_play_music", "open_application", "capture_screen_base64"):
        assert (plugin_manager.get_tool_contract(name) or {}).get("needs_desktop") is True, name
    assert not (plugin_manager.get_tool_contract("get_system_info") or {}).get("needs_desktop")


@pytest.fixture
def ran(monkeypatch):
    calls = []

    async def fake_execute(name, args):
        calls.append(name)
        return {"status": "success"}
    monkeypatch.setattr(plugin_manager, "execute_skill", fake_execute)
    return calls


async def _gate(name, **args):
    out = await tool_gate.run_tool_with_policy(name, args, caller="admin", source_device="portal", registry_names=set())
    return out["result"]


async def test_gui_tool_is_refused_with_a_clear_reason_when_the_server_has_no_desktop(monkeypatch, ran):
    monkeypatch.setattr(desktop_session, "status", lambda refresh=False: dict(HEADLESS))
    res = await _gate("open_youtube", query="hai ba năm")
    assert res["code"] == "DESKTOP_UNAVAILABLE" and res["success"] is False and ran == []
    assert "Session 0" in res["error"] and "đừng tạo kỹ năng mới" in res["error"] and "target_client" in res["error"]


async def test_gui_tool_runs_on_a_desktop_and_non_gui_tools_are_never_blocked(monkeypatch, ran):
    monkeypatch.setattr(desktop_session, "status", lambda refresh=False: dict(DESKTOP))
    assert (await _gate("open_youtube", query="x"))["status"] == "success"
    monkeypatch.setattr(desktop_session, "status", lambda refresh=False: dict(HEADLESS))
    assert (await _gate("get_system_info"))["status"] == "success"
    assert ran == ["open_youtube", "get_system_info"]


def test_desktop_check_failure_never_blocks(monkeypatch):
    def boom(refresh=False):
        raise RuntimeError("ctypes hỏng")
    monkeypatch.setattr(desktop_session, "status", boom)
    assert tool_gate._desktop_problem("open_youtube") is None


def test_probe_reports_a_shape_on_this_machine():
    st = desktop_session.status(refresh=True)
    assert isinstance(st["interactive"], bool) and st["reason"]


def test_skill_creation_is_rate_limited_per_identity(monkeypatch):
    import skills.meta_skills as ms
    from mateai.application.security.security_guard import CURRENT_PRINCIPAL
    created = []

    class FakeArchitect:
        def create_skill(self, desc, skill_name=None):
            created.append(desc)
            return {"status": "success", "skill_name": skill_name}
    import mateai.application.skills.meta_architect as ma
    monkeypatch.setattr(ma, "meta_architect", FakeArchitect())
    monkeypatch.setattr(ms, "_recent", {})
    tok = CURRENT_PRINCIPAL.set("device:robot-a")
    try:
        outs = [ms.create_new_skill(f"việc {i}") for i in range(5)]
    finally:
        CURRENT_PRINCIPAL.reset(tok)
    assert [o["status"] for o in outs] == ["success"] * 3 + ["error"] * 2 and len(created) == 3
    assert outs[3]["code"] == "SKILL_CREATION_RATE_LIMITED" and "báo người dùng lý do" in outs[3]["message"]
    tok = CURRENT_PRINCIPAL.set("admin")                                          # danh tính khác: hạn mức riêng
    try:
        assert ms.create_new_skill("việc khác")["status"] == "success"
    finally:
        CURRENT_PRINCIPAL.reset(tok)
