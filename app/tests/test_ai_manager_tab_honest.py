# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 Dương Thanh Lịch — VN-MateAI. See LICENSE and NOTICE.
"""
tests/test_ai_manager_tab_honest.py
===================================
Tab "Quản Lý Trợ Lý AI" (/#ai-manager) — rà soát 2026-10-05. Trước đây:

  - công tắc Tri-Brain lưu `... || true`: không bao giờ tắt được;
  - ô Não Vận hành tự điền "VN-MateAi" — model KHÔNG có trên 9Router: bấm Lưu một
    lần là mọi lệnh có tool gọi model không tồn tại; máy chủ cũng dự phòng về tên
    model viết cứng;
  - công tắc "Streaming" không được máy chủ đọc ở đâu;
  - TTS "Local (Offline Piper)" không có trong máy chủ; ASR "Whisper Local" thực ra
    là API, còn Whisper cục bộ thật (local_whisper) không chọn được;
  - câu trả lời của model khi "Kiểm Tra Kết Nối" chèn thẳng vào innerHTML (XSS);
  - ô "Độ trễ" đọc biến không tồn tại -> luôn "—".
"""
from __future__ import annotations

import re
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
APP = (ROOT / "web" / "app.js").read_text(encoding="utf-8")
HTML = (ROOT / "web" / "index.html").read_text(encoding="utf-8")


def _fn(name: str) -> str:
    start = APP.index(f"function {name}(")
    return APP[start:APP.index("\nfunction ", start + 10) if "\nfunction " in APP[start + 10:] else None]


def test_tri_brain_switch_can_be_turned_off():
    save = APP[APP.index("async function saveAIConfig("):APP.index("async function saveAIConfig(") + 9000]
    line = re.search(r"tri_brain_enabled:[^\n]*\n[^\n]*", save).group(0)
    assert "|| true" not in line


def test_no_hardcoded_nonexistent_model():
    assert not re.search(r"\|\|\s*['\"]VN-MateAi['\"]", APP)          # dự phòng viết cứng
    llm = (ROOT / "src/mateai/application/agent/llm_engine.py").read_text(encoding="utf-8")
    body = llm[llm.index("def get_brain_model"):llm.index("def classify_intent")]
    code = " ".join(line.split("#")[0] for line in body.splitlines())   # bỏ chú thích
    assert "VN-MateAi" not in code and "ag/gemini" not in code


def test_unsaved_brains_fall_back_to_main_model(monkeypatch):
    from mateai.application.agent import llm_engine as le
    import mateai.config.loader as loader
    cfg = SimpleNamespace(model_name="main-m", tri_brain_enabled=True,
                          controller_model="", voice_model="voice-m", ops_model="")
    monkeypatch.setattr(loader.settings, "llm", cfg, raising=False)
    monkeypatch.setattr(le, "settings", SimpleNamespace(llm=cfg), raising=False)
    eng = le.llm_engine
    assert eng.get_brain_model("ops") == "main-m" and eng.get_brain_model("voice") == "voice-m"
    cfg.tri_brain_enabled = False
    assert eng.get_brain_model("voice") == "main-m"


def test_dead_or_fake_controls_removed_and_real_ones_offered():
    assert "ai-switch-streaming" not in HTML and "ai-switch-streaming" not in APP
    tts = HTML[HTML.index('id="ai-tts-engine"'):HTML.index("</select>", HTML.index('id="ai-tts-engine"'))]
    assert 'value="local"' not in tts
    asr = HTML[HTML.index('id="ai-asr-engine"'):HTML.index("</select>", HTML.index('id="ai-asr-engine"'))]
    assert 'value="local_whisper"' in asr and "Whisper Local (OpenAI" not in asr


def test_llm_test_result_is_escaped_and_updates_latency():
    fn = APP[APP.index("async function testAILLMConnection("):APP.index("function updateAIManagerTelemetry(")]
    assert "${data.reply}" not in fn and "${_esc(data.reply)}" in fn
    assert "${errMsg}" not in fn and "${e.message}" not in fn
    assert "ai-status-latency" in fn
    tele = APP[APP.index("function updateAIManagerTelemetry("):APP.index("function selectQuickModel(")]
    assert "SYSTEM_HEALTH_CACHE" not in tele and "/api/v1/health-dashboard" in tele


def test_save_warns_about_models_unknown_to_router():
    save = APP[APP.index("async function saveAIConfig("):APP.index("async function saveAIConfig(") + 4000]
    assert "routerModelList" in save and "KHÔNG có trong 9Router" in save


def test_ops_brain_skill_count_is_live_not_hardcoded():
    """Ô Não Vận hành từng ghi cứng "79 SKILLS" trong khi máy chủ có 85 kỹ năng."""
    assert "79 SKILLS" not in HTML and "79 tools" not in HTML
    assert 'id="ai-tribrain-ops-skills"' in HTML
    tele = APP[APP.index("function updateAIManagerTelemetry("):APP.index("function selectQuickModel(")]
    assert "skills_count" in tele and "ai-tribrain-ops-skills" in tele
