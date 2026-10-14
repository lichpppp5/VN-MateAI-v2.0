# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 Dương Thanh Lịch — VN-MateAI. See LICENSE and NOTICE.
"""
Gọi tên "Ly Ly ơi" (2026-10-12): người dùng báo robot gọi không thức, HUD không có gọi tên.

- Nhận câu gọi HAI BƯỚC, dùng chung robot + HUD (`wake_word_engine.detect_wake`): model nhanh (tiny); nếu chưa thấy tên mà
  đoạn nghe GẦN GIỐNG tên ("Mình đi ở", "Ninh Ly ơi") → model chính xác hơn kiểm lại. Đo bằng giọng tổng hợp nói xa + ồn:
  24 → 29/32 câu gọi, trung vị vẫn ~330 ms.
- HUD: trình duyệt chỉ gửi đoạn CÓ TIẾNG NÓI; máy chủ nhận dạng OFFLINE (âm thanh phòng không ra ngoài — người dùng chọn);
  chỉ phiên đã đăng nhập; mỗi kết nối một đoạn một lúc.
"""
from __future__ import annotations

import base64
import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from mateai.infrastructure.audio import wake_word_engine as wwe

ROOT = Path(__file__).resolve().parents[1]
HUD_JS = (ROOT / "web" / "hud.js").read_text(encoding="utf-8")
HUD_HTML = (ROOT / "web" / "hud.html").read_text(encoding="utf-8")


@pytest.mark.parametrize("heard,near", [
    ("Mình đi ở", True), ("Ninh Ly ơi", True), ("Hãy nhìn đi", True), ("Em đi đi", True),
    ("Hôm nay trời đẹp quá", False), ("Anh đi làm đây nhé em ơi bao giờ về thì gọi", False), ("", False),
])
def test_near_miss_detection_decides_when_to_verify(heard, near):
    assert wwe.sounds_like_name(heard) is near


class _FakeASR:
    def __init__(self, fast, verify):
        self.fast, self.verify, self.calls = fast, verify, []

    async def transcribe_wake_clip(self, pcm, verify=False):
        self.calls.append("verify" if verify else "fast")
        return self.verify if verify else self.fast


@pytest.fixture
def asr(monkeypatch):
    import mateai.infrastructure.audio.audio_processor as ap
    monkeypatch.setattr(wwe, "wake_names", lambda: ["Ly Ly"])

    def use(fast, verify=""):
        fake = _FakeASR(fast, verify)
        monkeypatch.setattr(ap, "audio_engine", fake)
        return fake
    return use


async def test_fast_hit_needs_no_verification(asr):
    fake = asr("Ly Ly ơi, mấy giờ rồi")
    assert await wwe.detect_wake(b"\0" * 9600) == ("mấy giờ rồi", "Ly Ly ơi, mấy giờ rồi", "fast")
    assert fake.calls == ["fast"]


async def test_near_miss_is_rescued_by_the_verify_model(asr):
    fake = asr("Mình đi ở", verify="Ly Ly ơi")
    command, heard, stage = await wwe.detect_wake(b"\0" * 9600)
    assert command == "" and stage == "verify" and fake.calls == ["fast", "verify"]


async def test_ordinary_speech_is_not_sent_to_the_slow_model(asr):
    fake = asr("Hôm nay trời đẹp quá")
    assert (await wwe.detect_wake(b"\0" * 9600))[0] is None and fake.calls == ["fast"]


def test_robot_uses_the_shared_two_stage_detector():
    src = (ROOT / "src/mateai/interfaces/websocket/xiaozhi_gateway.py").read_text(encoding="utf-8")
    body = src[src.index("async def _handle_wake_clip"):src.index("async def _transcribe(")]
    assert "await detect_wake(clip)" in body and "transcribe_wake_clip(clip)" not in body


# ── HUD: /ws/hud nhận đoạn gọi tên ─────────────────────────────────────────────────────────────────────────────────

def _drain(ws, typ, limit=30):
    for _ in range(limit):
        msg = json.loads(ws.receive_text())
        if msg.get("type") == typ:
            return msg
    raise AssertionError(f"không thấy {typ}")


@pytest.fixture
def hud_ws(monkeypatch):
    import mateai.interfaces.http.server as server
    from mateai.interfaces.http import ws_auth
    seen = []

    async def fake_detect(pcm):
        seen.append(len(pcm))
        return "", "Ly Ly ơi", "fast"
    monkeypatch.setattr(wwe, "detect_wake", fake_detect)

    def connect(user):
        monkeypatch.setattr(ws_auth, "authenticate_websocket", lambda _ws: user)
        return TestClient(server.app).websocket_connect("/ws/hud")
    return connect, seen


def _clip(seconds=1.0):
    return base64.b64encode(b"\0\1" * int(16000 * seconds)).decode()


def test_logged_in_hud_gets_wake_detected(hud_ws):
    connect, seen = hud_ws
    with connect({"username": "admin", "role": "admin"}) as ws:
        _drain(ws, "hud_welcome")
        ws.send_text(json.dumps({"action": "wake_clip", "pcm16": _clip()}))
        msg = _drain(ws, "wake_detected")
    assert msg["command"] == "" and seen == [32000]


@pytest.mark.parametrize("payload", [_clip(0.1), _clip(7.0), "không-phải-base64!!", 123],
                         ids=["qua-ngan", "qua-dai", "sai-base64", "khong-phai-chuoi"])
def test_bad_or_out_of_range_clips_are_ignored(hud_ws, payload):
    connect, seen = hud_ws
    with connect({"username": "admin", "role": "admin"}) as ws:
        _drain(ws, "hud_welcome")
        ws.send_text(json.dumps({"action": "wake_clip", "pcm16": payload}))
        ws.send_text(json.dumps({"action": "ping"}))
        _drain(ws, "pong")
    assert seen == []


def test_not_logged_in_hud_cannot_send_room_audio(hud_ws):
    connect, seen = hud_ws
    with connect(None) as ws:
        _drain(ws, "hud_welcome")
        ws.send_text(json.dumps({"action": "wake_clip", "pcm16": _clip()}))
        ws.send_text(json.dumps({"action": "ping"}))
        _drain(ws, "pong")
    assert seen == []


def test_hud_wake_listener_wiring():
    assert 'id="hud-wake-btn"' in HUD_HTML and 'onclick="toggleHudWake()"' in HUD_HTML
    assert "window.toggleHudWake = toggleHudWake" in HUD_JS
    assert "echoCancellation: true" in HUD_JS                                 # không nghe chính giọng Ly Ly từ loa
    paused = HUD_JS[HUD_JS.index("function hudWakePaused()"):HUD_JS.index("function _wakeReset()")]
    for cond in ("hudIsSpeaking()", "isHudListening", "currentAuth.authenticated", "hudAwaitingReply"):
        assert cond in paused, cond
    assert "action: 'wake_clip'" in HUD_JS and "type === 'wake_detected'" in HUD_JS
    assert "recognize" not in HUD_JS[HUD_JS.index("// 11.0. GỌI TÊN"):HUD_JS.index("function handleWakeDetected")]
