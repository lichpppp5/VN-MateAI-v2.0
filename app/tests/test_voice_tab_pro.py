# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 Dương Thanh Lịch — VN-MateAI. See LICENSE and NOTICE.
"""
tests/test_voice_tab_pro.py
===========================
Nâng cấp tab #voice (2026-10-05):
  1. Bảng điều khiển từng robot (nói, cử động, âm lượng, khởi động lại, trạng thái) —
     lệnh firmware chưa hỗ trợ trả 409, không báo thành công giả; firmware 54.
  2. Lịch sử lượt thoại với thời gian từng bước.
  3. Micro trình duyệt -> nhận dạng ở MÁY CHỦ (không còn Web Speech / Google).
  4. Công cụ đã chạy trong lượt + duyệt / từ chối tại chỗ.
  5. Kiểm tra âm thanh robot: mức tiếng nói so với ồn nền, nhận dạng, tên gọi.
  6. Tab con.
Không gọi mạng.
"""
from __future__ import annotations

import asyncio
import json
import struct
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import mateai.interfaces.websocket.xiaozhi_gateway as xg

ROOT = Path(__file__).resolve().parents[1]
APP = (ROOT / "web" / "app.js").read_text(encoding="utf-8")
HTML = (ROOT / "web" / "index.html").read_text(encoding="utf-8")


class WS:
    def __init__(self):
        self.text = []

    async def send_text(self, t):
        self.text.append(json.loads(t))

    async def send_bytes(self, b):
        pass


@pytest.fixture
def robot(monkeypatch):
    g = xg.xiaozhi_gateway
    node = xg.XiaozhiNode(device_id="r9", websocket=WS(), client_host="10.0.0.9")
    node.firmware_version = "53.0"
    monkeypatch.setitem(g._nodes, "r9", node)
    ui = []

    async def send_ui(device_id, state=None, emotion=None, text=None, **extra):
        ui.append({"state": state, **extra})
        return True

    said = []

    async def say(n, text):
        said.append(text)

    monkeypatch.setattr(g, "send_ui_payload", send_ui)
    monkeypatch.setattr(g, "_say", say)
    return g, node, ui, said


def _client(role="admin"):
    import mateai.interfaces.http.routers.robots as robots
    from mateai.interfaces.http.auth_dependencies import get_current_user
    app = FastAPI()
    app.include_router(robots.router)
    app.dependency_overrides[get_current_user] = lambda: {"username": f"{role}_u", "role": role}
    return TestClient(app)


# ── 1. Bảng điều khiển robot ────────────────────────────────────────────────

def test_old_firmware_gets_409_not_fake_success(robot):
    g, node, _, _ = robot
    c = _client()
    r = c.post("/api/v1/robots/r9/volume", json={"level": 40})
    assert r.status_code == 409 and "firmware 54" in r.json()["detail"]
    assert c.post("/api/v1/robots/r9/reboot").status_code == 409
    assert node.websocket.text == []                                   # không gửi gì xuống robot


def test_v54_robot_controls_and_status_report(robot):
    g, node, _, said = robot
    node.features = ["i2s_audio", "volume_ctrl", "reboot", "status_report"]
    c = _client()
    assert c.post("/api/v1/robots/r9/volume", json={"level": 40}).status_code == 200
    assert c.post("/api/v1/robots/r9/animate", json={"animation": "wave_hand"}).status_code == 200
    assert c.post("/api/v1/robots/r9/animate", json={"animation": "nhay_mua"}).status_code == 400
    assert c.post("/api/v1/robots/r9/say", json={"text": "Xin chào"}).status_code == 200
    assert node.websocket.text[:2] == [{"type": "set_volume", "level": 40},
                                       {"type": "cmd", "action": "animate", "anim": "wave_hand"}]
    assert said == ["Xin chào"]
    node.status_report = {"volume": 40, "rssi": -55, "heap": 120000, "uptime_s": 600, "at": 1}
    data = c.get("/api/v1/robots").json()["robots"][0]
    assert data["supports"] == {"volume": True, "reboot": True, "status": True}
    assert data["status_report"]["rssi"] == -55
    assert _client("manager").post("/api/v1/robots/r9/reboot").status_code == 403      # điều khiển: chỉ admin
    assert c.post("/api/v1/robots/khong-co/say", json={"text": "x"}).status_code == 404


def test_firmware_54_has_the_new_commands():
    fw = (ROOT / "esp32_firmware" / "src" / "main.cpp").read_text(encoding="utf-8")
    for needle in ('type == "set_volume"', 'type == "get_status"', 'type == "reboot"',
                   "volume_ctrl,reboot,status_report",
                   'd["type"]      = "status_report"', "speakerVolume"):
        assert needle in fw, needle
    import re
    major = int(re.search(r'#define FIRMWARE_VERSION "(\d+)\.\d+"', fw).group(1))
    assert major >= 54                                    # lệnh v54 còn nguyên ở các bản sau
    if major >= 55:                                       # v55: cử động tự nhiên (robot_behavior)
        assert 'type == "behavior"' in fw and "natural_behavior" in fw and "startBehaviorTask()" in fw


# ── 5. Kiểm tra âm thanh ────────────────────────────────────────────────────

def test_audio_check_levels_and_verdicts():
    loud = struct.pack("<" + "h" * 1600, *([3000, -3000] * 800))
    lv = xg.pcm_levels(loud)
    assert lv["rms"] == 3000.0 and -21 < lv["rms_dbfs"] < -20 and lv["seconds"] == 0.1
    assert xg.audio_check_verdict("ly ly ơi mấy giờ", True, 3000, 100)["grade"] == "tốt"
    weak = xg.audio_check_verdict("ly ly ơi", True, 300, 150)
    assert weak["grade"] == "yếu" and weak["snr_db"] == 6.0
    assert xg.audio_check_verdict("mấy giờ rồi", False, 3000, 100)["grade"] == "trung bình"
    assert xg.audio_check_verdict("", False, None, None)["grade"] == "kém"


async def test_audio_check_flow_does_not_run_a_turn(robot, monkeypatch):
    g, node, ui, said = robot
    node.audio_stats = {"noise": 100, "peak": 900}

    async def stt(n, audio):
        return "Ly Ly ơi mấy giờ rồi"

    ran = []
    monkeypatch.setattr(g, "_transcribe", stt)
    monkeypatch.setattr(g, "_execute_pipeline", lambda n, t: ran.append(t))
    assert await g.start_audio_check("r9") is True
    assert node.audio_check["status"] == "listening" and ui[-1]["listen_timeout_ms"] == 8000
    pcm = struct.pack("<" + "h" * 16000, *([2000, -2000] * 8000))
    await g._finish_utterance(node, pcm, pcm16_wav=True)
    chk = node.audio_check
    assert chk["status"] == "done" and chk["grade"] == "tốt" and chk["wake_ok"] is True
    assert chk["snr_db"] == 26.0 and ran == []                          # không chạy lượt hội thoại
    assert said[-1].startswith("Kết quả: tốt")


def test_wake_stats_and_status_report_are_kept(robot):
    # Gói điều khiển đi qua handle_websocket; ở đây kiểm phần lưu trên node.
    g, node, _, _ = robot
    assert node.audio_stats == {} and node.status_report == {}
    assert "audio_stats" in xg.XiaozhiNode.__init__.__code__.co_names or hasattr(node, "audio_stats")


# ── 3. Micro trình duyệt -> STT máy chủ ─────────────────────────────────────

def test_transcribe_endpoint_uses_server_stt(monkeypatch):
    import mateai.interfaces.http.routers.voice as voice
    from mateai.interfaces.http.auth_dependencies import get_current_user
    from mateai.infrastructure.audio.audio_processor import audio_engine
    got = {}

    async def fake(audio):
        got["n"] = len(audio)
        return "mấy giờ rồi"

    monkeypatch.setattr(audio_engine, "transcribe_audio", fake)
    app = FastAPI()
    app.include_router(voice.router)
    app.dependency_overrides[get_current_user] = lambda: {"username": "u", "role": "manager"}
    c = TestClient(app)
    r = c.post("/api/v1/voice/transcribe", content=b"RIFF" + b"\0" * 2000, headers={"Content-Type": "audio/wav"})
    assert r.json()["text"] == "mấy giờ rồi" and got["n"] == 2004
    assert c.post("/api/v1/voice/transcribe", content=b"").status_code == 400
    fn = APP[APP.index("async function toggleBrowserSpeechRecognition("):APP.index("// ── Công cụ đã chạy trong lượt")]
    assert "webkitSpeechRecognition" not in fn and "/api/v1/voice/transcribe" in fn


# ── 4. Công cụ đã chạy + duyệt ──────────────────────────────────────────────

def test_tool_summary_hides_args_and_results():
    from mateai.interfaces.websocket.realtime_voice_ws import tool_summary
    out = tool_summary([
        {"skill": "get_system_info", "target_client": "master", "args": {"x": 1}, "result": {"status": "success", "data": "BIG"}},
        {"skill": "kill_process", "target_client": "pc-01", "args": {"pid": 7},
         "result": {"status": "need_confirm", "approval_id": "HITL-1", "message": "cần duyệt"}},
    ])
    assert out == [{"skill": "get_system_info", "target": "master", "status": "success"},
                   {"skill": "kill_process", "target": "pc-01", "status": "need_confirm",
                    "approval_id": "HITL-1", "message": "cần duyệt"}]
    # Lớp bọc của trình chạy plugin báo "success" dù skill thất bại — phải đọc lớp trong.
    wrapped = tool_summary([{"skill": "kill_process", "result": {"success": True, "error": None, "data": {
        "success": False, "data": None, "error": "Không tìm thấy tiến trình với PID=999999."}}}])
    assert wrapped[0]["status"] == "error" and "PID=999999" in wrapped[0]["message"]
    fn = APP[APP.index("function renderVoiceTools("):APP.index("async function decideVoiceApproval(")]
    assert "${_esc(t.skill)}" in fn and "/api/v1/enterprise/hitl/" in APP


# ── 2 / 6. Lịch sử + tab con ────────────────────────────────────────────────

def test_history_and_subtabs_present():
    tab = HTML[HTML.index('id="tab-voice"'):HTML.index("TAB 4: CONFIGURATION")]
    subs = set(__import__("re").findall(r'data-voice-sub="([a-z]+)"', tab))
    assert subs == {"robot", "chat", "history", "studio"}
    assert 'id="voice-history"' in tab and 'id="voice-tools-box"' in tab
    for fn in ("showVoiceSubTab", "loadVoiceHistory", "robotAction", "decideVoiceApproval", "loadRobotPanel"):
        assert f"function {fn}(" in APP, fn
    hist = APP[APP.index("async function loadVoiceHistory("):APP.index("// ── Bảng điều khiển từng robot")]
    assert "/api/v1/voice/metrics" in hist and "_esc(r.model" in hist
