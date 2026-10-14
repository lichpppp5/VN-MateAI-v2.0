# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 Dương Thanh Lịch — VN-MateAI. See LICENSE and NOTICE.
"""
tests/test_voice_tab_honest.py
==============================
Tab Portal #voice (rà soát 2026-10-05). Trước đây:

  - thẻ "Wake Word" ghi cứng "Hey Lyly", thẻ giọng đọc ghi cứng "Hoài My Neural"
    (JS không bao giờ cập nhật); nhãn "MICROSOFT EDGE-TTS 24kHZ" trong khi máy chủ
    dùng 9Router TTS trước, Edge chỉ dự phòng;
  - chờ phản hồi hứa "TTFA < 800ms" (đo thật vài giây); sự kiện `status` của máy
    chủ bị bỏ qua — lệnh chạy công cụ thì màn hình đứng ở "đang kết nối";
  - mở tab gọi mic-status / audio-nodes HAI lần và bật thông báo mỗi lần;
  - robot: "Opus 24kHz" (firmware gửi PCM 16 kHz); lỗi tải hiện "0 THIẾT BỊ";
  - phát thanh: mọi lỗi đều báo "không có robot online".
"""
from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]
HTML = (ROOT / "web" / "index.html").read_text(encoding="utf-8")
APP = (ROOT / "web" / "app.js").read_text(encoding="utf-8")
TAB = HTML[HTML.index('id="tab-voice"'):HTML.index("TAB 4: CONFIGURATION")]


def test_no_hardcoded_identity_or_false_claims():
    assert '"Hey Lyly"</div>' not in TAB and ">Hoài My Neural" not in TAB.split('id="voice-stat-tts-val"')[1][:120]
    assert "MICROSOFT EDGE-TTS 24kHZ" not in TAB
    assert "TTFA &lt; 800ms" not in APP and "Opus 24kHz" not in APP
    assert "'LAN READY'" not in APP


def test_cards_filled_from_server_and_single_load_on_open():
    assert "_applyVoiceIdentity(data)" in APP and "wake_names" in APP and "tts_voice" in APP
    loader = APP[APP.index("if (tabId === 'voice') {"):APP.index("if (tabId === 'logs') {")]
    assert loader.count("(") <= 3 and "updateVoiceTelemetry({ silent: true })" in loader
    fn = APP[APP.index("async function updateVoiceTelemetry("):APP.index("function _applyVoiceIdentity(")]
    assert "if (!opts.silent) showToast" in fn


def test_progress_status_shown_and_errors_honest():
    assert "msg.type === 'status'" in APP and "voice-progress-text" in APP
    assert "'KHÔNG TẢI ĐƯỢC'" in APP
    bc = APP[APP.index("async function broadcastAudioAnnouncement("):APP.index("let _micRec = null;")]
    assert "e.status === 503" in bc


def test_mic_status_reports_wake_names_and_voice(monkeypatch):
    import mateai.interfaces.http.routers.wake_word as ww
    from mateai.interfaces.http.auth_dependencies import get_current_user
    import mateai.infrastructure.audio.wake_word_engine as we
    monkeypatch.setattr(we, "wake_names", lambda: ["Ly Ly", "Bé Na"])
    app = FastAPI()
    app.include_router(ww.router)
    app.dependency_overrides[get_current_user] = lambda: {"username": "u", "role": "viewer"}
    d = TestClient(app).get("/api/v1/voice/mic-status").json()
    assert d["wake_names"] == ["Ly Ly", "Bé Na"] and "tts_voice" in d and d["tts_engine"]
