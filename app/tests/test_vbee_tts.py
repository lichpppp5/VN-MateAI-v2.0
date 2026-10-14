# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 Dương Thanh Lịch — VN-MateAI. See LICENSE and NOTICE.
"""
Vbee AIVoice (studio.vbee.vn) làm nguồn đọc thứ ba (2026-10-12): Realtime API, dự phòng 9Router / Edge khi Vbee lỗi,
cache âm thanh theo GIỌNG (đổi giọng không phát lại câu đã lưu bằng giọng cũ), token mã hoá + che, giao diện Voice Studio.
Không gọi Vbee thật — giả lập phản hồi HTTP.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from mateai.infrastructure.tts import audio_cache, tts_stream_engine, vbee_tts

ROOT = Path(__file__).resolve().parents[1]
VBEE_CFG = {"tts_engine": "vbee", "vbee_app_id": "app-123", "vbee_token": "tok-xyz",
            "tts_voice": "sg_female_tuongvy_call_44k-fhg", "speech_rate": 15}


class _Resp:
    def __init__(self, status, chunks=(), ctype="audio/mpeg", body=b""):
        self.status_code, self._chunks, self.headers, self._body = status, list(chunks), {"content-type": ctype}, body

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def aread(self):
        return self._body

    async def aiter_bytes(self):
        for c in self._chunks:
            yield c


class _Client:
    def __init__(self, resp):
        self.resp, self.calls = resp, []

    def stream(self, method, url, headers=None, json=None, timeout=None):
        self.calls.append({"method": method, "url": url, "headers": headers, "json": json})
        return self.resp


@pytest.fixture
def vbee(monkeypatch):
    """Cấu hình Vbee + client HTTP giả + ghi lại cache / 9Router."""
    monkeypatch.setattr(vbee_tts, "audio_cfg", lambda: dict(VBEE_CFG))
    state = {"saved": [], "router": []}
    monkeypatch.setattr(tts_stream_engine, "_cache_get", lambda text, tag=None: None)
    monkeypatch.setattr(tts_stream_engine, "_cache_set",
                        lambda text, data, voice="", tag=None: state["saved"].append((text, tag, voice)))

    async def router(text, voice):
        state["router"].append(voice)
        return b"R" * 200
    monkeypatch.setattr(tts_stream_engine, "_synthesise_9router", router)

    def use(resp):
        client = _Client(resp)

        async def get_client():
            return client
        import mateai.infrastructure.http.connection_pool as pool
        monkeypatch.setattr(pool, "get_tts_http_client", get_client)
        state["client"] = client
        return client
    state["use"] = use
    return state


def test_resolve_picks_vbee_only_with_credentials(monkeypatch):
    monkeypatch.setattr(vbee_tts, "audio_cfg", lambda: dict(VBEE_CFG))
    r = vbee_tts.resolve_tts()
    assert r["engine"] == "vbee" and r["voice"] == "sg_female_tuongvy_call_44k-fhg" and r["speed"] == 1.15
    assert r["tag"] == "vbee:sg_female_tuongvy_call_44k-fhg"
    # thiếu token → không gọi Vbee (chắc chắn lỗi), dùng Edge
    monkeypatch.setattr(vbee_tts, "audio_cfg", lambda: {**VBEE_CFG, "vbee_token": ""})
    assert vbee_tts.resolve_tts()["engine"] == "edge"
    # giọng Edge truyền vào khi đang dùng Vbee → vẫn đọc bằng giọng Vbee đã chọn (bộ máy quyết định)
    monkeypatch.setattr(vbee_tts, "audio_cfg", lambda: dict(VBEE_CFG))
    assert vbee_tts.resolve_tts("vi-VN-HoaiMyNeural")["voice"] == "sg_female_tuongvy_call_44k-fhg"


def test_cache_key_includes_voice_but_default_voice_keeps_old_key(monkeypatch):
    norm = audio_cache.normalize_text_for_hash("Anh còn cần em hỗ trợ gì nữa không ạ?")
    legacy = hashlib.md5(norm.encode("utf-8")).hexdigest()
    monkeypatch.setattr(vbee_tts, "audio_cfg", lambda: {"tts_engine": "edge-tts", "tts_voice": "vi-VN-HoaiMyNeural"})
    assert audio_cache.get_audio_hash("Anh còn cần em hỗ trợ gì nữa không ạ?") == legacy      # cache cũ vẫn dùng được
    monkeypatch.setattr(vbee_tts, "audio_cfg", lambda: dict(VBEE_CFG))
    vbee_key = audio_cache.get_audio_hash("Anh còn cần em hỗ trợ gì nữa không ạ?")
    assert vbee_key != legacy                                      # đổi sang Vbee: không phát lại bản Hoài My
    assert audio_cache.get_audio_hash("x", tag="edge:vi-VN-NamMinhNeural") != audio_cache.get_audio_hash("x", tag="")


async def test_stream_uses_vbee_realtime_api(vbee):
    client = vbee["use"](_Resp(200, [b"ID3" + b"a" * 100, b"b" * 100]))
    out = b"".join([c async for c in tts_stream_engine.TTSStreamEngine().stream("Hệ thống CPU ổn định.")])
    assert out == b"ID3" + b"a" * 100 + b"b" * 100
    call = client.calls[0]
    assert call["method"] == "POST" and call["url"] == "https://api.vbee.vn/v1/tts"
    assert call["headers"]["Authorization"] == "Bearer tok-xyz" and call["headers"]["App-Id"] == "app-123"
    body = call["json"]
    assert body["mode"] == "sync" and body["voiceCode"] == "sg_female_tuongvy_call_44k-fhg" and body["outputFormat"] == "mp3"
    assert body["speed"] == 1.15 and "C P U" in body["text"]                       # gợi ý phát âm vẫn áp dụng
    assert vbee["saved"] == [("Hệ thống CPU ổn định.", "vbee:sg_female_tuongvy_call_44k-fhg", "sg_female_tuongvy_call_44k-fhg")]
    assert vbee["router"] == []


@pytest.mark.parametrize("status,body", [(401, b'{"error":{"code":"UNAUTHORIZED","message":"invalid token"}}'),
                                         (400, b'{"error":{"code":"BAD_REQUEST","message":"insufficient credit"}}'),
                                         (429, b'{"error":{"code":"TTS_CCR_MAX_LIMIT_REACHED"}}')])
async def test_vbee_failure_falls_back_to_9router_with_an_edge_voice(vbee, status, body):
    vbee["use"](_Resp(status, ctype="application/json", body=body))
    out = b"".join([c async for c in tts_stream_engine.TTSStreamEngine().stream("Xin chào anh.")])
    assert out == b"R" * 200                                       # không câm
    assert vbee["router"] == ["vi-VN-HoaiMyNeural"]                # KHÔNG gửi mã giọng Vbee sang 9Router
    assert vbee["saved"] == [("Xin chào anh.", "", "vi-VN-HoaiMyNeural")]   # lưu dưới giọng Edge, không dưới giọng Vbee


def test_errors_are_explained():
    assert "hết hạn" in vbee_tts._explain(401, '{"error":{"code":"UNAUTHORIZED"}}')
    assert "hết điểm" in vbee_tts._explain(400, '{"error":{"code":"BAD_REQUEST","message":"Insufficient credit"}}')


def test_long_text_is_split_under_300_chars():
    text = "Doanh thu tháng này tăng mười phần trăm, chi phí giảm. " * 12
    parts = vbee_tts.split_for_vbee(text)
    assert len(parts) > 1 and all(len(p) <= 300 for p in parts)
    assert " ".join(parts).split() == text.split()                  # không mất chữ


def test_speed_mapping_and_voice_detection():
    assert vbee_tts.speed_from_rate(15) == 1.15 and vbee_tts.speed_from_rate("+50%") == 1.5
    assert vbee_tts.speed_from_rate(-90) == 0.25 and vbee_tts.speed_from_rate(200) == 1.9
    assert vbee_tts.is_vbee_voice("hn_female_ngochuyen_full_48k-fhg") and not vbee_tts.is_vbee_voice("vi-VN-HoaiMyNeural")


def test_config_accepts_vbee_and_token_is_secret():
    from mateai.application.administration import config_governance as gov
    from mateai.config.secret_box import SECRET_FIELD_NAMES
    from mateai.interfaces.http.secret_masking import _mask_secrets
    assert "vbee" in gov.TTS_ENGINES and "vbee_token" in SECRET_FIELD_NAMES
    masked = _mask_secrets({"audio": {"vbee_app_id": "app-123", "vbee_token": "tok-xyz"}})
    assert masked["audio"]["vbee_token"] != "tok-xyz" and masked["audio"]["vbee_app_id"] == "app-123"
    src = (ROOT / "src/mateai/application/administration/config_governance.py").read_text(encoding="utf-8")
    assert 'audio.tts_engine") == "vbee"' in src and "vbee_app_id" in src and "vbee_token" in src


def test_engine_reads_settings_every_call_not_once():
    src = (ROOT / "src/mateai/infrastructure/tts/tts_stream_engine.py").read_text(encoding="utf-8")
    assert "_elevenlabs_config" not in src and "resolve_tts(voice or self._voice_override)" in src


def test_voice_studio_has_vbee_controls():
    html = (ROOT / "web/index.html").read_text(encoding="utf-8")
    js = (ROOT / "web/app.js").read_text(encoding="utf-8")
    assert '<option value="vbee">' in html
    for eid in ("ai-vbee-section", "ai-vbee-app-id", "ai-vbee-token", "btn-vbee-voices", "btn-vbee-test", "ai-vbee-status"):
        assert f'id="{eid}"' in html, eid
    assert 'id="ai-vbee-token" placeholder' in html and 'type="password" id="ai-vbee-token"' in html
    for fn in ("async function loadVbeeVoices", "async function testVbeeVoice", "window.loadVbeeVoices", "window.testVbeeVoice"):
        assert fn in js, fn
    assert "vbee_app_id: vbeeAppId" in js and "vbee_token: vbeeToken" in js
    assert "/api/v1/tts/vbee/voices" in js and "/api/v1/tts/vbee/test" in js


def test_audio_settings_come_from_config_json_not_settings_audio(monkeypatch, tmp_path):
    # `settings.audio` luôn None (AppSettings bỏ khoá lạ) — trước 2026-10-12 nhánh ElevenLabs đọc từ đó nên chưa từng chạy.
    import mateai.config.loader as loader
    cfg = tmp_path / "config.json"
    cfg.write_text(json.dumps({"audio": {"tts_engine": "vbee", "vbee_app_id": "a", "vbee_token": "t",
                                         "tts_voice": "hn_female_ngochuyen_full_48k-fhg"}}), encoding="utf-8")
    monkeypatch.setattr(loader, "CONFIG_PATH", cfg)
    monkeypatch.delenv("VNMATEAI_TTS_PAID_PROVIDERS", raising=False)
    vbee_tts._cfg_cache.update(mtime=None, audio={})
    assert vbee_tts.resolve_tts()["engine"] == "vbee"
    src = (ROOT / "src/mateai/infrastructure/tts/vbee_tts.py").read_text(encoding="utf-8")
    assert 'getattr(settings, "audio"' not in src


async def test_batch_used_when_package_has_no_realtime(vbee, monkeypatch):
    # Gói Vbee chưa có Realtime ("not supported in user package") → Batch: gửi async → hỏi trạng thái → tải audioLink.
    import asyncio as _asyncio
    monkeypatch.setattr(vbee_tts, "_realtime_blocked_until", 0.0)

    async def no_sleep(*a, **k):
        return None
    monkeypatch.setattr(_asyncio, "sleep", no_sleep)

    class _J:
        def __init__(self, status, data):
            self.status_code, self._d = status, data

        def json(self):
            return self._d

        @property
        def text(self):
            return json.dumps(self._d)

    class _BatchClient(_Client):
        def __init__(self):
            super().__init__(None)
            self.polls = 0

        def stream(self, method, url, headers=None, json=None, timeout=None, follow_redirects=False):
            self.calls.append({"method": method, "url": url, "json": json})
            if method == "POST":
                return _Resp(400, ctype="application/json",
                             body=b'{"error":{"code":"BAD_REQUEST","message":"This feature is not supported in user package"}}')
            return _Resp(200, [b"M" * 150])                   # tải audioLink

        async def post(self, url, headers=None, json=None, timeout=None):
            self.calls.append({"method": "POST-async", "url": url, "json": json})
            return _J(201, {"requestId": "r-1", "status": "PROCESSING"})

        async def get(self, url, headers=None, timeout=None, params=None):
            self.polls += 1
            return _J(200, {"requestId": "r-1", "status": "COMPLETED" if self.polls >= 2 else "PROCESSING",
                            "audioLink": "https://cdn.vbee.vn/a.mp3"})

    client = _BatchClient()

    async def get_client():
        return client
    import mateai.infrastructure.http.connection_pool as pool
    monkeypatch.setattr(pool, "get_tts_http_client", get_client)
    out = b"".join([c async for c in tts_stream_engine.TTSStreamEngine().stream("Xin chào anh.")])
    assert out == b"M" * 150 and vbee["router"] == []
    batch = [c for c in client.calls if c["method"] == "POST-async"][0]["json"]
    assert batch["mode"] == "async" and batch["webhookUrl"].startswith("https://localhost/")   # không gửi link cho bên thứ ba
    assert not vbee_tts.realtime_available()                    # nhớ: lần sau đi thẳng Batch


async def test_saving_audio_config_warms_fixed_phrases_in_the_new_voice(monkeypatch):
    from mateai.interfaces.http.routers import config as cfg_router
    import mateai.config.loader as loader
    import mateai.infrastructure.tts.acoustic_ack as ack
    called = []

    async def warm(extra_phrases=()):
        called.append(extra_phrases)

    async def nop(*a, **k):
        return None
    monkeypatch.setattr(loader, "reload_settings", lambda: None)
    monkeypatch.setattr(cfg_router, "broadcast_hud", nop)
    monkeypatch.setattr(ack, "warmup_acoustic_ack_cache", warm)
    await cfg_router._apply_written_config({"audio": {"tts_engine": "vbee"}}, {"audio": {"tts_engine": "vbee"}})
    import asyncio as _asyncio
    await _asyncio.sleep(0)
    from mateai.application.voice.voice_session import GOODBYE_PHRASE
    assert called and GOODBYE_PHRASE in called[0]
