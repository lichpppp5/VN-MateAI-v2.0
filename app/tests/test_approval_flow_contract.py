# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 Dương Thanh Lịch — VN-MateAI. See LICENSE and NOTICE.
"""
tests/test_approval_flow_contract.py
====================================
Supervisor Phase 10 (§198): duyệt / huỷ tác vụ chờ là MỘT use case
(`application/security/approval_decisions.py`) + một luồng báo tin dùng chung
(`interfaces/http/approval_flow.py`) cho REST và HUD — HUD không gọi thẳng hàm endpoint
của router khác nữa.

Lỗi thật đã sửa (cố định bằng test):
  - HUD admin duyệt một yêu cầu đã hết hạn / không có: lỗi 404 bị nuốt trong task nền,
    HUD không nhận được gì;
  - `/ws/hud` "simulate": kết nối CHƯA đăng nhập phát chữ tuỳ ý lên mọi HUD;
  - `/ws/portal-ui` "show_toast" / "switch_tab": mọi tài khoản (kể cả viewer) phát thông
    báo / chuyển tab trên màn hình của mọi người (giả thông báo hệ thống cho admin).
    Giao diện trong repo chỉ gửi "ping" (đã kiểm `web/app.js`, `web/hud.js`).
Không gọi mạng, không chạy tool thật.
"""
from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

import mateai.application.agent.tool_gate as tool_gate
import mateai.interfaces.http.server as server
from mateai.interfaces.http import ws_auth


def _drain_until(ws, typ, key="type", limit=30):
    for _ in range(limit):
        msg = json.loads(ws.receive_text())
        if msg.get(key) == typ:
            return msg
    raise AssertionError(f"không thấy {typ}")


@pytest.fixture
def queue(monkeypatch):
    import mateai.application.security.zero_trust as zt
    q = zt.HumanInTheLoopManager()
    q.register_executor(tool_gate.TOOL_KIND, tool_gate.execute_approved_tool)
    monkeypatch.setattr(zt, "hitl_manager", q)
    monkeypatch.setattr(tool_gate, "hitl_manager", q)
    return q


def _pending(q, tool="write_file"):
    return q.request_approval(action_name=tool, params={"file_path": "a.txt"}, requested_by="req_u",
                              kind=tool_gate.TOOL_KIND,
                              context={"target_client": "master", "query": "ghi tệp", "source_device": "http:fs"})["id"]


def test_find_pending_and_reject_use_case(queue):
    from mateai.application.security import approval_decisions as ad
    with pytest.raises(ad.ApprovalDecisionError) as e:
        ad.find_pending(action_id="khong-co")
    assert e.value.status_code == 404
    act = _pending(queue)
    with pytest.raises(ad.ApprovalDecisionError) as e:
        ad.find_pending(action_id=act, skill_name="delete_item")
    assert e.value.status_code == 409
    p = ad.find_pending(action_id=act)
    assert p["tool_name"] == "write_file"
    assert "write_file" in ad.reject(p, "admin_u")
    assert queue.get_pending(act) is None


def test_telegram_target_from_pending():
    from mateai.application.security.approval_decisions import telegram_chat_of
    assert telegram_chat_of({"chat_id": "77"}) == "77"
    assert telegram_chat_of({"source_device": "telegram:12345"}) == "12345"
    assert telegram_chat_of({"source_device": "hud"}) is None


def test_hud_admin_gets_error_when_pending_is_gone(monkeypatch, queue):
    monkeypatch.setattr(ws_auth, "authenticate_websocket", lambda _ws: {"username": "carol", "role": "admin"})
    with TestClient(server.app).websocket_connect("/ws/hud") as ws:
        _drain_until(ws, "hud_welcome")
        ws.send_text(json.dumps({"action": "confirm_action", "action_id": "het-han", "approved": True}))
        msg = _drain_until(ws, "security_approval_rejected")
        assert msg["action_id"] == "het-han" and "Không tìm thấy" in msg["message"]


@pytest.mark.parametrize("user,allowed", [(None, False), ({"username": "v", "role": "viewer"}, False),
                                          ({"username": "a", "role": "admin"}, True)])
def test_hud_simulate_requires_admin(monkeypatch, user, allowed):
    import mateai.interfaces.http.routers.websockets as wsr
    sent = []

    async def rec(payload):
        sent.append(payload)

    monkeypatch.setattr(wsr, "broadcast_hud", rec)
    monkeypatch.setattr(ws_auth, "authenticate_websocket", lambda _ws: user)
    with TestClient(server.app).websocket_connect("/ws/hud") as ws:
        _drain_until(ws, "hud_welcome")
        ws.send_text(json.dumps({"action": "simulate", "text": "Hệ thống bị xâm nhập!"}))
        ws.send_text(json.dumps({"action": "ping"}))
        _drain_until(ws, "pong")
    assert bool(sent) is allowed


@pytest.mark.parametrize("role,allowed", [("viewer", False), ("manager", False), ("admin", True)])
def test_portal_ui_broadcast_requires_admin(monkeypatch, role, allowed):
    import mateai.interfaces.http.routers.websockets as wsr
    sent = []

    async def rec(event, data):
        sent.append((event, data))

    monkeypatch.setattr(wsr, "broadcast_portal_ui", rec)
    monkeypatch.setattr(ws_auth, "authenticate_websocket", lambda _ws: {"username": "u", "role": role})
    with TestClient(server.app).websocket_connect("/ws/portal-ui?token=x") as ws:
        _drain_until(ws, "connected", key="event")
        ws.send_text(json.dumps({"action": "show_toast", "message": "Hãy duyệt yêu cầu #9"}))
        ws.send_text(json.dumps({"action": "switch_tab", "tab": "security"}))
        ws.send_text(json.dumps({"action": "ping"}))
        _drain_until(ws, "pong", key="event")
    assert (len(sent) == 2) is allowed and (len(sent) == 0) is (not allowed)


async def test_approval_speech_goes_to_hud_as_binary_not_base64(monkeypatch):
    """Prompt cuối §25 / L10: audio realtime là khung NHỊ PHÂN; base64 chỉ còn ở biên REST."""
    from mateai.interfaces.http import approval_flow
    sent_json, sent_bin = [], []

    async def fake_json(payload):
        sent_json.append(payload)

    async def fake_bin(data):
        sent_bin.append(data)

    async def fake_tts(text):
        return b"ID3" + b"\x00" * 300

    async def no_sleep(_s):
        return None

    monkeypatch.setattr(approval_flow, "broadcast_hud", fake_json)
    monkeypatch.setattr(approval_flow, "broadcast_hud_binary", fake_bin)
    monkeypatch.setattr(approval_flow.speech, "tts_bytes", fake_tts)
    monkeypatch.setattr(approval_flow.asyncio, "sleep", no_sleep)
    await approval_flow._speak_on_hud("Dạ, đã duyệt xong.")
    speaking = [p for p in sent_json if p.get("status") == "speaking"][0]
    assert "audio_base64" not in speaking and speaking["has_audio"] is True
    assert sent_bin and sent_bin[0].startswith(b"ID3")
