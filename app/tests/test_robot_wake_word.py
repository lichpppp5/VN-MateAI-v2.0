# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 Dương Thanh Lịch — VN-MateAI. See LICENSE and NOTICE.
"""
tests/test_robot_wake_word.py
=============================
Gọi "hey Ly Ly" để đánh thức robot: robot gửi đoạn có tiếng nói lúc nghỉ, máy chủ
nhận dạng OFFLINE và so khớp tên trợ lý (`wake_word_engine.find_wake_command`,
dùng chung với mic máy chủ).

Câu nhận dạng dưới đây là kết quả THẬT của faster-whisper (tiny / base / small, có
và không hotwords) trên giọng Hoài My / Nam Minh, đo 2026-10-04. Không gọi mạng.
"""
from __future__ import annotations

import asyncio
import json

import pytest

from mateai.infrastructure.audio.wake_word_engine import find_wake_command


@pytest.fixture(autouse=True)
def assistant_name(monkeypatch):
    import mateai.config.loader as loader
    monkeypatch.setattr(loader, "get_assistant_name", lambda: "Ly Ly")


@pytest.mark.parametrize("heard", [
    "Hãy đi Ly Ly", "Hey Ly Ly", "Hây li lì", "Hay Li Li", "Lý Li ai", "Lilia", "Lý Lýer",
    "Ly Ly Ly", "Em lý Ly", "L Ly Ly", "And Lily", "Lili", "Hey Lily!", "Ly Ly ai",
])
def test_name_only_wakes_and_listens(heard):
    assert find_wake_command(heard) == ""


@pytest.mark.parametrize("heard,command", [
    ("Lý Lý ơi, mấy giờ rồi", "mấy giờ rồi"),
    ("Lily ơi, mấy giờ rồi?", "mấy giờ rồi"),
    ("Ly Ly ơi, liệt kê tiến trình", "liệt kê tiến trình"),   # "liệt" không phải âm tiết tên
    ("Ly Ly ơi lịch họp hôm nay", "lịch họp hôm nay"),
])
def test_command_after_name_is_kept(heard, command):
    assert find_wake_command(heard) == command


@pytest.mark.parametrize("heard", [
    "Hôm nay trời đẹp quá", "Không nay tôi đẹp quá", "Lấy cho anh cái bút", "Lý cho anh kai buc",
    "hãy liên hệ với anh", "lịch làm việc tuần này", "cốc ly này", "Hãy đi đi", "",
])
def test_ordinary_speech_does_not_wake(heard):
    assert find_wake_command(heard) is None


def test_name_comes_from_config(monkeypatch):
    import mateai.config.loader as loader
    monkeypatch.setattr(loader, "get_assistant_name", lambda: "Mai Anh")
    monkeypatch.setattr(loader, "get_config_section", lambda name: {})
    monkeypatch.setattr(loader, "read_raw_config", lambda *a, **k: {})
    assert find_wake_command("Mai Anh ơi bật đèn") == "bật đèn"
    assert find_wake_command("Hey Ly Ly") is None


def test_configured_wake_phrase_also_works(monkeypatch):
    """Ô "Câu đánh thức" (Persona) từng chỉ để hiển thị. Nay gọi được bằng tên trong
    câu đó (bỏ "Hey"/"ơi") VÀ bằng tên trợ lý."""
    import mateai.config.loader as loader
    monkeypatch.setattr(loader, "get_assistant_name", lambda: "Ly Ly")
    monkeypatch.setattr(loader, "get_config_section",
                        lambda name: {"wake_word": "Hey Bé Na"} if name == "persona" else {})
    assert find_wake_command("bé na ơi mấy giờ rồi") == "mấy giờ rồi"
    assert find_wake_command("Ly Ly ơi bật đèn") == "bật đèn"
    assert find_wake_command("hôm nay trời đẹp") is None
    # Câu đánh thức chỉ là tên trợ lý viết liền: vẫn tách âm tiết theo tên.
    monkeypatch.setattr(loader, "get_config_section",
                        lambda name: {"wake_word": "Hey Lyly"} if name == "persona" else {})
    from mateai.infrastructure.audio.wake_word_engine import wake_names
    assert wake_names() == ["Ly Ly"] and find_wake_command("ly ly ơi") == ""


# ── Gateway: đoạn câu gọi -> lắng nghe / chạy lệnh ─────────────────────────

class _WS:
    def __init__(self):
        self.sent = []

    async def send_text(self, text):
        self.sent.append(json.loads(text))


@pytest.fixture
def gw(monkeypatch):
    import mateai.interfaces.websocket.xiaozhi_gateway as xg
    gateway = xg.XiaozhiGateway() if hasattr(xg, "XiaozhiGateway") else type(xg.xiaozhi_gateway)()
    node = xg.XiaozhiNode(device_id="robot_test", websocket=_WS(), client_host="127.0.0.1")
    calls = {"ui": [], "pipeline": [], "main_asr": 0}

    async def ui(device_id, state=None, **kw):
        calls["ui"].append(state)

    async def pipeline(n, text):
        calls["pipeline"].append(text)

    async def main_asr(n, audio):
        calls["main_asr"] += 1
        return calls.get("main_text", "")

    monkeypatch.setattr(gateway, "send_ui_payload", ui)
    monkeypatch.setattr(gateway, "_execute_pipeline", pipeline)
    monkeypatch.setattr(gateway, "_transcribe", main_asr)
    return gateway, node, calls, xg


def _heard(monkeypatch, xg, text):
    async def fake(pcm):
        return text
    monkeypatch.setattr(xg.audio_engine, "transcribe_wake_clip", fake)


async def test_name_only_puts_robot_in_listening(gw, monkeypatch):
    gateway, node, calls, xg = gw
    _heard(monkeypatch, xg, "Hây li lì")
    node.wake_busy = True
    await gateway._handle_wake_clip(node, b"\0" * 20000)
    assert calls["ui"] == ["listening"] and not calls["pipeline"] and calls["main_asr"] == 0
    assert node.wake_busy is False


async def test_name_with_command_runs_it_using_main_asr_text(gw, monkeypatch):
    gateway, node, calls, xg = gw
    _heard(monkeypatch, xg, "Ly Ly ai, máy giả tôi")          # model nhỏ nghe sai phần lệnh
    calls["main_text"] = "Lily ơi, mấy giờ rồi?"               # bộ nhận dạng chính nghe đúng
    await gateway._handle_wake_clip(node, b"\0" * 20000)
    await asyncio.sleep(0)
    assert calls["pipeline"] == ["mấy giờ rồi"] and calls["main_asr"] == 1
    assert node.websocket.sent[-1] == {"type": "asr_result", "text": "mấy giờ rồi"}


async def test_ordinary_speech_is_ignored(gw, monkeypatch):
    gateway, node, calls, xg = gw
    _heard(monkeypatch, xg, "Hôm nay trời đẹp quá")
    await gateway._handle_wake_clip(node, b"\0" * 20000)
    assert calls["ui"] == [] and calls["pipeline"] == [] and calls["main_asr"] == 0


def test_wake_clip_never_uses_cloud_asr(monkeypatch):
    """Âm thanh lúc chưa gọi trợ lý chỉ được nhận dạng cục bộ."""
    import mateai.infrastructure.audio.audio_processor as ap
    monkeypatch.setattr(ap, "_get_local_whisper", lambda: None)

    async def cloud(*a, **k):
        raise AssertionError("không được gửi âm thanh câu gọi lên dịch vụ đám mây")

    monkeypatch.setattr(ap.AudioEngine, "_transcribe_google", cloud, raising=False)
    assert asyncio.run(ap.audio_engine.transcribe_wake_clip(b"\1\0" * 8000)) == ""
