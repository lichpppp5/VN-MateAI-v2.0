# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 Dương Thanh Lịch — VN-MateAI. See LICENSE and NOTICE.
"""
tests/test_robot_behavior.py
============================
Cử động tự nhiên của robot (application/devices/robot_behavior.py + xiaozhi_gateway):

  - cấu hình mặc định, giới hạn an toàn (nhịp bánh 60–300 ms), giờ yên lặng qua nửa đêm;
  - giờ yên lặng / tắt -> khung `behavior` tắt mọi cử động và đèn;
  - cử chỉ theo nội dung câu: chào -> vẫy tay, buồn -> sad, phấn khích -> excited, ...;
  - gateway gửi khung khi bắt tay, chỉ gửi lại khi giá trị đổi.
"""
from __future__ import annotations

import asyncio
import json
from datetime import datetime

from mateai.application.devices import robot_behavior as rb
from mateai.application.voice.emotion import emotion_for_text


def test_defaults_and_limits():
    s = rb.settings({})
    assert s["enabled"] and s["idle_wheels"] and s["wheel_ms"] == 120 and (s["idle_min_s"], s["idle_max_s"]) == (30, 120)
    s = rb.settings({"wheel_ms": 5000, "idle_min_s": 90, "idle_max_s": 10, "khoa_la": 1})
    assert s["wheel_ms"] == 300                       # nhịp bánh tối đa 300 ms
    assert s["idle_max_s"] == 90                      # max không nhỏ hơn min
    assert "khoa_la" not in s
    assert rb.settings({"wheel_ms": "abc"})["wheel_ms"] == 120


def test_quiet_hours():
    at = lambda h, m=0: datetime(2026, 10, 6, h, m)
    assert rb.in_quiet_hours("22:00-07:00", at(23)) and rb.in_quiet_hours("22:00-07:00", at(6, 59))
    assert not rb.in_quiet_hours("22:00-07:00", at(7)) and not rb.in_quiet_hours("22:00-07:00", at(12))
    assert rb.in_quiet_hours("12:00-13:30", at(13, 15)) and not rb.in_quiet_hours("12:00-13:30", at(13, 30))
    assert not rb.in_quiet_hours("", at(23)) and not rb.in_quiet_hours("sai", at(23))


def test_frame_quiet_or_disabled_turns_everything_off():
    day = rb.behavior_frame({"quiet_hours": "22:00-07:00"}, datetime(2026, 10, 6, 10))
    assert day["type"] == "behavior" and day["idle"] and day["idle_wheels"] and day["led"] and not day["quiet"]
    night = rb.behavior_frame({"quiet_hours": "22:00-07:00"}, datetime(2026, 10, 6, 23))
    assert night["quiet"] and not any(night[k] for k in ("idle", "idle_wheels", "speech_gestures", "led"))
    off = rb.behavior_frame({"enabled": False}, datetime(2026, 10, 6, 10))
    assert not any(off[k] for k in ("idle", "idle_wheels", "speech_gestures", "led"))
    no_wheels = rb.behavior_frame({"idle_wheels": False}, datetime(2026, 10, 6, 10))
    assert no_wheels["idle"] and not no_wheels["idle_wheels"]


def test_gesture_follows_sentence_content():
    def g(text):
        return rb.gesture_for_text(text, emotion_for_text(text))
    assert g("Xin chào anh, em là trợ lý VN-MateAI.") == "wave_hand"
    assert g("Tạm biệt anh, hẹn gặp lại!") == "wave_hand"
    assert g("Chúc mừng anh, doanh thu tháng này tăng 20%.") == "excited"
    assert g("Em xin lỗi, em không thể kết nối tới máy in.") == "sad"
    assert g("Vâng, em đã ghi khoản chi 500 nghìn.") == "nod_head"
    assert g("Máy chủ đang dùng 35% CPU.") is None            # câu thường: để nhịp gật tự nhiên
    assert set(rb.GESTURES) >= {"wave_hand", "nod_head", "look_around", "excited", "sad"}


class _WS:
    def __init__(self):
        self.sent = []

    async def send_text(self, text):
        self.sent.append(json.loads(text))


def test_gateway_pushes_behavior_once_until_changed(monkeypatch):
    from mateai.interfaces.websocket.xiaozhi_gateway import XiaozhiGateway, XiaozhiNode
    gw = XiaozhiGateway()
    ws = _WS()
    gw._nodes["robot_t"] = XiaozhiNode("robot_t", ws, "127.0.0.1")
    cfg = {"idle_wheels": True}
    monkeypatch.setattr("mateai.config.loader.get_config_section",
                        lambda name: dict(cfg) if name == "robot_behavior" else {})

    async def run():
        assert await gw.push_behavior("robot_t", force=True) is True
        assert await gw.push_behavior("robot_t") is False          # không đổi -> không gửi lại
        cfg["idle_wheels"] = False
        assert await gw.push_behavior("robot_t") is True
        assert await gw.push_behavior("khong_co") is False
    asyncio.run(run())
    frames = [m for m in ws.sent if m.get("type") == "behavior"]
    assert len(frames) == 2 and frames[0]["idle_wheels"] is True and frames[1]["idle_wheels"] is False
    # Firmware cũ (không khai báo natural_behavior) chạy cử chỉ chặn luồng âm thanh -> không gửi.
    assert gw.behavior_allows_gestures("robot_t") is False
    gw._nodes["robot_t"].capabilities = "motor_l298n,natural_behavior,rgb_led"
    assert gw.behavior_allows_gestures("robot_t") is True


async def test_real_handshake_pushes_behavior_and_gates_gestures(monkeypatch):
    """Đi đúng vòng `handle_client` như firmware thật (frame `hello` giao thức XiaoZhi).
    Lỗi đã gặp 2026-10-06: khung `behavior` chỉ gắn ở nhánh hello cũ — robot v55 không nhận."""
    import mateai.infrastructure.audio.audio_processor as ap
    import mateai.interfaces.websocket.xiaozhi_gateway as xg
    from types import SimpleNamespace
    from starlette.websockets import WebSocketDisconnect

    class FakeWS:
        def __init__(self, msgs):
            self.msgs, self.sent, self.client = list(msgs), [], SimpleNamespace(host="127.0.0.1")

        async def accept(self):
            return None

        async def receive(self):
            if self.msgs:
                return self.msgs.pop(0)
            raise WebSocketDisconnect(code=1000)

        async def send_text(self, text):
            self.sent.append(json.loads(text))

        async def send_bytes(self, data):
            return None

    monkeypatch.setattr(ap, "warm_local_whisper", lambda: True)
    monkeypatch.setattr("mateai.config.loader.get_config_section",
                        lambda name: {"wheel_ms": 150} if name == "robot_behavior" else {})
    for version, feats, gestures in (("55.0", "motor_l298n,natural_behavior,rgb_led", True),
                                     ("54.0", "motor_l298n,volume_ctrl", False)):
        gw = xg.XiaozhiGateway()
        seen = {}
        orig = gw.behavior_allows_gestures

        async def ui(device_id, state=None, **kw):
            return True
        monkeypatch.setattr(gw, "send_ui_payload", ui)
        hello = {"type": "hello", "version": version, "features": feats, "pairing_code": "123456",
                 "audio_params": {"format": "pcm", "sample_rate": 16000}}

        async def probe():
            seen["gestures"] = orig("robot_t")
            return None
        ws = FakeWS([{"text": json.dumps(hello)}, {"text": json.dumps({"type": "get_status_probe"})}])
        monkeypatch.setattr(ws, "receive", (lambda real: (lambda: _wrap(real, probe)))(ws.receive))
        await gw.handle_client(ws, "robot_t")
        frames = [m for m in ws.sent if m.get("type") == "behavior"]
        assert len(frames) == 1 and frames[0]["wheel_ms"] == 150, version
        assert seen["gestures"] is gestures, version
        if gw._behavior_task:
            gw._behavior_task.cancel()


async def _wrap(real, probe):
    msg = await real()
    if "get_status_probe" in str(msg):
        await probe()
    return msg
