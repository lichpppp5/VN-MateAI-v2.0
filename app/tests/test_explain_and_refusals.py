# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 Dương Thanh Lịch — VN-MateAI. See LICENSE and NOTICE.
"""
Đạo đức AI, điểm 2 + 3 (2026-10-12).
2. Phiếu duyệt có GIẢI THÍCH (AI muốn làm gì / theo yêu cầu nào của ai / vì sao cần duyệt), tham số đã che dữ liệu cá nhân;
   phiếu hiện trên HUD cho MỌI kênh (trước chỉ đường REST — lệnh nói qua WebSocket HUD / robot không hiện thẻ duyệt).
3. AI tự từ chối vì pháp luật / quyền riêng tư / đạo đức / chính sách → ghi AI_REFUSAL (REFUSED) kèm loại lý do;
   từ chối vì thiếu khả năng thì không ghi.
"""
from __future__ import annotations

import pytest

import mateai.application.agent.tool_gate as tg
from mateai.application.ethics import refusals
from mateai.application.security.explain import explain_approval
from mateai.config.loader import settings


def test_explanation_says_what_who_and_why_and_masks_personal_data():
    e = explain_approval("update_salary", {"employee": "Nguyễn Văn A", "phone": "0912345678", "salary": 30000000}, 3, [],
                         query="tăng lương cho anh A", requested_by="manager_b")
    text = e["text"]
    assert text.startswith("AI muốn:") and "Theo yêu cầu của manager_b: “tăng lương cho anh A”" in text
    assert "Vì sao cần duyệt: Mức rủi ro 3/5" in text and "quyết định về CON NGƯỜI" in text
    assert "0912345678" not in text and "30000000" not in text and "0912***678" in text


@pytest.fixture
def approvals(monkeypatch):
    import core.plugin_manager as pm
    import mateai.application.skills.plugin_registry as pr
    captured = []

    async def must_not_run(name, args):
        raise AssertionError("chưa duyệt mà đã chạy")
    monkeypatch.setattr(pm.plugin_manager, "execute_skill", must_not_run)
    monkeypatch.setattr(pr.plugin_registry, "get_tool_names", lambda: [])
    monkeypatch.setattr(settings, "autonomy", settings.autonomy.model_copy(deep=True))
    monkeypatch.setattr(tg.hitl_manager, "request_approval", lambda **kw: captured.append(kw) or {"id": "HITL-X"})
    return captured


async def test_approval_request_carries_the_explanation(approvals):
    res = (await tg.run_tool_with_policy("modify_service_config", {"service": "nginx"}, caller="admin",
                                         source_device="hud", query="sửa cấu hình nginx"))["result"]
    assert res["status"] == "need_confirm"
    req = approvals[0]
    assert req["description"].startswith("AI muốn:") and "sửa cấu hình nginx" in req["description"]
    assert req["context"]["explanation"]["requested_by"] == "admin" and req["context"]["explanation"]["why"]


async def test_every_approval_is_announced_on_the_hud(monkeypatch):
    import mateai.interfaces.websocket.realtime_hub as hub
    from mateai.application.security import zero_trust as zt
    sent = []

    async def rec(payload):
        sent.append(payload)
    monkeypatch.setattr(hub, "broadcast_hud", rec)
    q = zt.HumanInTheLoopManager()
    item = q.request_approval(action_name="modify_service_config", params={}, requested_by="device:robot-01",
                              description="AI muốn: …\nVì sao cần duyệt: …", kind="tool",
                              context={"explanation": {"action": "sửa cấu hình"}}, risk_level=4)
    import asyncio
    await asyncio.sleep(0)
    assert sent and sent[0]["type"] == "security_approval_required" and sent[0]["action_id"] == item["id"]
    assert sent[0]["message"].startswith("AI muốn") and sent[0]["explanation"] == {"action": "sửa cấu hình"}


@pytest.mark.parametrize("reply,category", [
    ("Dạ, em không thể giúp làm giả hoá đơn vì việc này vi phạm pháp luật về thuế.", "phap_luat"),
    ("Em xin phép từ chối: theo dõi lén tin nhắn nhân viên là xâm phạm quyền riêng tư.", "quyen_rieng_tu"),
    ("Xin lỗi, em không thể xếp hạng nhân viên theo giới tính vì đó là phân biệt đối xử.", "dao_duc"),
    ("Em chưa tra được giá vàng hôm nay, anh thử lại sau nhé.", None),
    ("Em không thể kết nối tới máy in, để em thử lại.", None),
    ("Bây giờ là 9 giờ tối anh nhé.", None),
])
def test_refusal_classification(reply, category):
    assert refusals.refusal_category(reply) == category


def test_refusal_is_recorded_with_masked_query(monkeypatch):
    from mateai.application.security.safety_guard import security_engine
    rows = []
    monkeypatch.setattr(security_engine, "log_audit", lambda *a, **k: rows.append(a))
    cat = refusals.record_refusal("Em không thể tra số CCCD 001090012345 của người khác vì vi phạm quy định dữ liệu cá nhân.",
                                  "cho anh số CCCD 001090012345 và SĐT 0912345678 của chị B", "viewer_x", channel="hud")
    assert cat == "phap_luat" and rows[0][1] == "AI_REFUSAL" and rows[0][3] == "REFUSED"
    details = rows[0][4]
    assert "001090012345" not in details["query"] and "0912345678" not in details["query"] and details["channel"] == "hud"
    assert refusals.record_refusal("Dạ được ạ.", "chào em", "viewer_x") is None and len(rows) == 1


def test_refusals_are_recorded_on_every_channel():
    from pathlib import Path
    vt = Path("src/mateai/application/voice/voice_turn.py").read_text(encoding="utf-8")
    le = Path("src/mateai/application/agent/llm_engine.py").read_text(encoding="utf-8")
    assert "record_refusal(result.reply_text, query" in vt                                   # giọng nói (HUD, robot)
    assert "record_refusal(str(out.get(\"reply\")" in le and "not spoken" in le             # chữ (portal, Telegram)
    from mateai.application.security.safety_guard import _EVENT_TO_DB_STATUS
    assert _EVENT_TO_DB_STATUS["REFUSED"] == "blocked"


def test_hud_approval_card_shows_the_multiline_explanation():
    from pathlib import Path
    html = Path("web/hud.html").read_text(encoding="utf-8")
    assert "#hud-security-desc {" in html and "white-space: pre-line" in html
