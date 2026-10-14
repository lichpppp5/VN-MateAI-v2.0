# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 Dương Thanh Lịch — VN-MateAI. See LICENSE and NOTICE.
"""
tests/test_config_governance.py
===============================
Tối ưu tab Quản Lý Trợ Lý AI (2026-10-05):

  1. Kiểm tra cấu hình Ở MÁY CHỦ trước khi ghi (trước đây chỉ giao diện cảnh báo).
  2. Lịch sử cấu hình: mỗi lần lưu một phiên bản, xem khác biệt, khôi phục.
  3. Thử trước khi lưu: một lượt với model / chỉ thị cá tính đang chỉnh, không ghi gì.
  4. Hiệu năng theo não + model thực tế (p50/p95, lỗi, chuyển dự phòng).
  5. Khoá bí mật trong config.json lưu MÃ HOÁ; đọc qua loader vẫn là bản thật.

Dùng config.json TẠM (không đụng file thật). Không gọi mạng.
"""
from __future__ import annotations

import json

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient


@pytest.fixture
def tmp_config(tmp_path, monkeypatch):
    import mateai.config.loader as loader
    from mateai.config import secret_box
    cfg_file = tmp_path / "config.json"
    cfg_file.write_text(json.dumps({
        "llm": {"base_url": "http://localhost:20128/v1", "model_name": "m-ok", "api_key": "sk-REAL-KEY",
                "routing_mode": "router"},
        "audio": {"tts_engine": "edge-tts", "speech_rate": 15, "volume": 80, "asr_engine": "google"},
        "persona": {"ai_name": "Ly Ly", "system_prompt": ""},
        "security": {"forbidden_keywords": ["format c:"]},
    }), encoding="utf-8")
    monkeypatch.setattr(loader, "CONFIG_PATH", cfg_file)
    monkeypatch.delenv("VNMATEAI_CONFIG_KEY", raising=False)
    monkeypatch.delenv("VNMATEAI_CONFIG_ENCRYPTION", raising=False)
    secret_box.reset_cache()
    yield cfg_file
    secret_box.reset_cache()
    monkeypatch.undo()
    loader.reload_settings()


def _client(monkeypatch, role="admin", models=("m-ok", "m-two")):
    import mateai.interfaces.http.routers.config as config_router
    from mateai.interfaces.http.auth_dependencies import get_current_user

    async def pool():
        return list(models)

    monkeypatch.setattr(config_router, "_router_model_pool", pool)
    app = FastAPI()
    app.include_router(config_router.router)
    app.dependency_overrides[get_current_user] = lambda: {"username": f"{role}_u", "role": role}
    return TestClient(app)


# ── 1. Kiểm tra ─────────────────────────────────────────────────────────────

def test_validate_rules():
    from mateai.application.administration.config_governance import validate
    errors, missing = validate({
        "llm": {"base_url": "localhost:20128", "routing_mode": "router", "model_name": "ghost",
                "ops_model": "m-ok"},
        "audio": {"speech_rate": 300, "volume": "x", "tts_engine": "local", "asr_engine": "abc"},
        "persona": {"ai_name": "", "system_prompt": "a" * 9000},
    }, ["m-ok"])
    joined = " | ".join(errors)
    for needle in ("không phải URL", "Tốc độ đọc", "Âm lượng", "TTS", "nhận dạng", "Tên trợ lý", "tối đa"):
        assert needle in joined, needle
    assert missing == ["Model chính: ghost"]
    assert validate({"telegram": {"enabled": True}}, ["m-ok"]) == ([], [])   # chỉ kiểm khoá có mặt


def test_invalid_save_is_rejected_and_nothing_written(tmp_config, monkeypatch):
    before = tmp_config.read_text(encoding="utf-8")
    c = _client(monkeypatch)
    r = c.post("/api/v1/config", json={"audio": {"speech_rate": 500}})
    assert r.status_code == 400 and "Tốc độ đọc" in json.dumps(r.json(), ensure_ascii=False)
    assert tmp_config.read_text(encoding="utf-8") == before
    r = c.post("/api/v1/config", json={"llm": {"model_name": "khong-co"}})
    assert r.status_code == 400 and r.json()["detail"]["unknown_models"] == ["Model chính: khong-co"]
    assert c.post("/api/v1/config", json={"llm": {"model_name": "khong-co"}, "_force_models": True}).status_code == 200


# ── 2. Lịch sử + khôi phục ──────────────────────────────────────────────────

def test_history_diff_and_restore_keep_secrets_out(tmp_config, monkeypatch):
    from mateai.config.loader import read_raw_config
    c = _client(monkeypatch)
    assert c.post("/api/v1/config", json={"audio": {"speech_rate": 40}}).status_code == 200
    assert c.post("/api/v1/config", json={"audio": {"speech_rate": 60}, "llm": {"api_key": "sk-NEW-KEY"}}).status_code == 200
    hist = c.get("/api/v1/config/history").json()["history"]
    assert [h["note"].startswith("Lưu") for h in hist[:2]] == [True, True] and hist[-1]["saved_by"] == "hệ thống"
    assert "sk-NEW-KEY" not in json.dumps(hist) and "sk-REAL-KEY" not in json.dumps(hist)
    newest = hist[0]
    assert {"path": "audio.speech_rate", "old": 40, "new": 60, "secret": False} in newest["changes"]
    assert any(ch["path"] == "llm.api_key" and ch["secret"] and ch["new"] is None for ch in newest["changes"])

    first_save = hist[1]["id"]
    d = c.get(f"/api/v1/config/history/{first_save}/diff").json()["changes"]
    assert {"path": "audio.speech_rate", "old": 60, "new": 40, "secret": False} in d
    assert c.post(f"/api/v1/config/history/{first_save}/restore").json()["status"] == "success"
    now = read_raw_config(strict=True)
    assert now["audio"]["speech_rate"] == 40 and now["llm"]["api_key"] == "sk-NEW-KEY"   # khoá hiện tại giữ nguyên
    assert c.get("/api/v1/config/history").json()["history"][0]["note"] == f"Khôi phục phiên bản #{first_save}"
    assert _client(monkeypatch, role="manager").post(f"/api/v1/config/history/{first_save}/restore").status_code == 403


# ── 3. Thử trước khi lưu ────────────────────────────────────────────────────

def test_preview_uses_draft_persona_and_model_without_saving(tmp_config, monkeypatch):
    import mateai.infrastructure.llm.llm_provider as prov
    seen = {}

    async def fake_complete(client, model, messages, **kw):
        seen.update(model=model, system=messages[0]["content"], user=messages[1]["content"])
        return "Dạ em là Bé Na."

    monkeypatch.setattr(prov, "complete_once", fake_complete)
    monkeypatch.setattr(prov, "make_llm_client", lambda *a, **k: object())
    before = tmp_config.read_text(encoding="utf-8")
    r = _client(monkeypatch).post("/api/v1/llm/preview", json={
        "message": "Em là ai?", "model": "m-two", "system_prompt": "Luôn xưng là Bé Na, nói vui vẻ.", "ai_name": "Bé Na"})
    data = r.json()
    assert data["success"] and data["reply"] == "Dạ em là Bé Na." and seen["model"] == "m-two"
    assert "Luôn xưng là Bé Na" in seen["system"] and "Bé Na" in seen["system"].split("\n")[0]
    assert tmp_config.read_text(encoding="utf-8") == before


# ── 4. Hiệu năng theo não + model ───────────────────────────────────────────

def test_model_stats_group_by_brain_and_actual_model(monkeypatch):
    import mateai.application.voice.voice_turn as vt
    traces = [
        {"brain": "voice", "model": "m-a", "model_requested": "m-a", "outcome": "ok", "llm_first_token_ms": 800, "ttl_ms": 3000},
        {"brain": "voice", "model": "m-a", "model_requested": "m-a", "outcome": "ok", "llm_first_token_ms": 1200, "ttl_ms": 4000},
        {"brain": "ops", "model": "m-b", "model_requested": "m-dead", "outcome": "error", "ttl_ms": 9000},
        {"brain": None, "fast_command": "time", "ttl_ms": 50},
    ]
    monkeypatch.setattr(vt, "_RECENT_TRACES", traces)
    st = vt.model_stats()
    rows = {(r["brain"], r["model"]): r for r in st["rows"]}
    assert rows[("voice", "m-a")]["turns"] == 2 and rows[("voice", "m-a")]["llm_first_token_ms"]["p50"] == 1000
    assert rows[("ops", "m-b")]["fallbacks"] == 1 and rows[("ops", "m-b")]["errors"] == 1
    assert len(rows) == 2                                  # lệnh nhanh không gọi LLM: bỏ qua


# ── 5. Mã hoá khoá trong config.json ────────────────────────────────────────

def test_secrets_are_encrypted_on_disk_but_plain_through_loader(tmp_config):
    from mateai.config.loader import encrypt_existing_secrets, read_raw_config, write_raw_config
    from mateai.config import secret_box
    assert encrypt_existing_secrets() is True                       # file mẫu có khoá chữ thường
    disk = json.loads(tmp_config.read_text(encoding="utf-8"))
    assert disk["llm"]["api_key"].startswith("enc:v1:") and "sk-REAL-KEY" not in tmp_config.read_text(encoding="utf-8")
    assert disk["security"]["forbidden_keywords"] == ["format c:"]  # chính sách, không phải bí mật
    assert disk["llm"]["model_name"] == "m-ok"
    assert read_raw_config()["llm"]["api_key"] == "sk-REAL-KEY"
    assert encrypt_existing_secrets() is False                      # đã mã hoá: không ghi lại
    cfg = read_raw_config(strict=True)
    cfg["alert_teams"] = {"webhook_url": "https://hook/secret-sig"}
    write_raw_config(cfg)
    assert "secret-sig" not in tmp_config.read_text(encoding="utf-8")
    assert read_raw_config()["alert_teams"]["webhook_url"] == "https://hook/secret-sig"
    assert (tmp_config.parent / "certs" / "config_secret.key").exists()
    assert not secret_box.has_plaintext_secrets(json.loads(tmp_config.read_text(encoding="utf-8")))


def test_wrong_key_means_missing_secret_not_crash(tmp_config, monkeypatch):
    from cryptography.fernet import Fernet
    from mateai.config.loader import encrypt_existing_secrets, read_raw_config
    from mateai.config import secret_box
    encrypt_existing_secrets()
    monkeypatch.setenv("VNMATEAI_CONFIG_KEY", Fernet.generate_key().decode())
    secret_box.reset_cache()
    cfg = read_raw_config()
    assert cfg["llm"]["api_key"] == "" and cfg["llm"]["model_name"] == "m-ok"


def test_elevenlabs_key_is_masked_in_config_api():
    from mateai.interfaces.http.secret_masking import _mask_secrets
    masked = _mask_secrets({"audio": {"elevenlabs_api_key": "el-REAL", "tts_voice": "vi-VN-HoaiMyNeural"}})
    assert masked["audio"]["elevenlabs_api_key"] != "el-REAL" and masked["audio"]["tts_voice"] == "vi-VN-HoaiMyNeural"


def test_tri_brain_fields_are_actually_saved(tmp_config, monkeypatch):
    """Khối llm từng bị dựng lại với 9 trường cố định: Tri-Brain trên giao diện không bao giờ được lưu."""
    from mateai.config.loader import read_raw_config
    c = _client(monkeypatch)
    r = c.post("/api/v1/config", json={"llm": {"model_name": "m-ok", "tri_brain_enabled": False,
                                               "controller_model": "m-two", "voice_model": "m-ok", "ops_model": "m-two"}})
    assert r.status_code == 200, r.text
    llm = read_raw_config()["llm"]
    assert llm["tri_brain_enabled"] is False and llm["ops_model"] == "m-two" and llm["api_key"] == "sk-REAL-KEY"
    r = c.post("/api/v1/config", json={"llm": {"model_name": "m-ok", "ops_model": "khong-co"}})
    assert r.status_code == 400 and "Não Vận hành: khong-co" in r.json()["detail"]["unknown_models"]
