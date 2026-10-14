# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 Dương Thanh Lịch — VN-MateAI. See LICENSE and NOTICE.
"""
HUD "buồng lái AI" (/hud, thay HUD cũ 2026-10-12): hai lớp tách bạch.
  - web/hud-visual.js — HÌNH, chỉ hiển thị: không mở WebSocket, không gửi gì, không phát tiếng; chưa đăng nhập thì ô số liệu để trống
    (không có số mô phỏng). Bị điều khiển qua window.HUDVisual.
  - web/hud.js — NÃO: giữ nguyên toàn bộ tính năng của HUD cũ (mic + vòng hội thoại, phát tiếng, phê duyệt, popup, nhật ký, thanh lệnh);
    hành vi chi tiết đã có test riêng (test_phase69_audio_mic.mjs, test_hud_conversation_loop.mjs, test_phase76_no_fake_hud.mjs,
    test_hud_barge_in_and_tts_flow.py, test_phase87_hud_suy_nghi_model.py).
Ở đây kiểm phần NỐI giữa hai lớp và trang HTML: mọi phần tử / nút mà não cần đều có, đúng thứ tự nạp script.
"""
from __future__ import annotations

import re
from pathlib import Path

from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]
JS = (ROOT / "web" / "hud-visual.js").read_text(encoding="utf-8")
BRAIN = (ROOT / "web" / "hud.js").read_text(encoding="utf-8")
HTML = (ROOT / "web" / "hud.html").read_text(encoding="utf-8")


def test_hud_is_the_cockpit_and_old_preview_url_redirects():
    from mateai.interfaces.http.server import app
    client = TestClient(app)
    r = client.get("/hud")
    assert r.status_code == 200 and "Neural Command" in r.text and "no-store" in r.headers["cache-control"]
    assert re.search(r'src="/static/hud-visual\.js\?v=\d+"', r.text) and re.search(r'src="/static/hud\.js\?v=\d+"', r.text)
    r = client.get("/hud-next", follow_redirects=False)
    assert r.status_code in (302, 307) and r.headers["location"] == "/hud"
    assert not (ROOT / "web" / "hud-next.html").exists() and not (ROOT / "web" / "hud-next.js").exists()


def test_scripts_load_audio_then_visual_then_brain():
    # hud.js gọi HUDVisual.setLevelSource ngay khi khởi động → lớp hình phải nạp TRƯỚC.
    order = [HTML.index(f'src="/static/{name}') for name in ("voice-audio-queue.js", "hud-visual.js", "hud.js")]
    assert order == sorted(order)


def test_visual_layer_is_display_only():
    assert not re.search(r"method:\s*['\"](POST|PUT|PATCH|DELETE)", JS)               # chỉ GET số liệu
    assert "new WebSocket" not in JS and ".send(" not in JS                          # /ws/hud chỉ do hud.js giữ
    assert "new Audio" not in JS and "AudioContext" not in JS and ".play(" not in JS
    assert "toDemo" not in JS and "DEMO_" not in JS                                   # không còn số liệu mô phỏng


def test_brain_drives_the_visual_through_one_bridge():
    for api in ("setState", "clearAlert", "setQuery", "transcript", "thoughts", "setLevelSource", "setAssistantName", "robot"):
        assert re.search(rf"\b{api}: ", JS), api
    assert "window.HUDVisual = {" in JS
    assert "function viz(method, ...args)" in BRAIN
    for call in ("viz('setLevelSource', hudAudioLevel)", "viz('setState', VIZ_STATE", "viz('transcript'", "viz('setQuery', query)",
                 "viz('thoughts', text)", "viz('setState', 'alert'", "viz('clearAlert')", "viz('setAssistantName'"):
        assert call in BRAIN, call
    # mức âm thanh THẬT từ AnalyserNode khi đang phát tiếng; không phát → null (lớp hình tự tạo nhịp nhẹ)
    level = BRAIN[BRAIN.index("function hudAudioLevel()"):BRAIN.index("// 10. TYPEWRITER")]
    assert "if (!hudIsSpeaking() || !analyserNode || !freqData) return null;" in level and "getByteFrequencyData" in level
    # bộ vẽ cũ đã gỡ hẳn
    for gone in ("function renderCore", "function renderWaveform", "function renderRadar", "function resizeCanvases", "core-canvas"):
        assert gone not in BRAIN, gone


def test_every_element_the_brain_needs_is_on_the_page():
    needed = ["hud-mic-btn", "hud-cmd-bar", "hud-cmd-input", "hud-cmd-send-btn", "hud-sound-btn", "hud-audio-stream", "hud-toast-container",
              "hud-conn-dot", "hud-conn-text", "hud-permission-badge", "hud-security-status", "hud-voice-stream",
              "hud-security-alert", "hud-security-desc", "hud-sec-approve-btn", "hud-sec-reject-btn",
              "hud-display-card", "hud-display-content", "hud-display-query", "hud-display-progress", "hud-display-pin-btn", "hud-countdown-badge",
              "hud-thinking", "hud-thinking-head", "hud-thinking-dot", "hud-thinking-peek", "hud-thinking-caret", "hud-thinking-body", "hud-thinking-text",
              "hud-log-stream", "hud-log-placeholder", "metric-clients-count", "metric-audio-nodes", "metric-skills-count",
              "hud-robot-dot", "hud-robot-status-text", "hud-robot-code-badge"]
    for eid in needed:
        assert f'id="{eid}"' in HTML, eid
    # popup, phiếu duyệt, khung suy nghĩ ẩn khi mở trang; hiện bằng cách bỏ lớp "hidden" (CSS dùng :not(.hidden), không đè display)
    for eid in ("hud-display-card", "hud-security-alert", "hud-thinking", "hud-thinking-body"):
        assert re.search(rf'id="{eid}" class="hidden', HTML), eid
        assert f"#{eid}:not(.hidden)" in HTML or eid == "hud-thinking-body", eid


def test_every_button_calls_a_function_the_brain_exposes():
    handlers = set(re.findall(r'onclick="(\w+)\(', HTML))
    assert {"toggleHudMic", "submitHudCmd", "quickCmd", "hudConfirmSecurity", "toggleHudAudio", "toggleFullscreen",
            "hideHudDisplayCard", "togglePinHudDisplay", "copyHudDisplayContent", "toggleHudThinking"} <= handlers
    for fn in handlers:
        assert f"window.{fn} = " in BRAIN, fn
    assert all('type="button"' in b for b in re.findall(r"<button\b[^>]*>", HTML))   # không nút nào vô tình submit


def test_mic_button_states_are_styled_by_data_attribute():
    assert "function hudSetMicBtn(mode, label)" in BRAIN and "micBtn.dataset.state = mode" in BRAIN
    for st in ("listening", "blocked"):
        assert f'#hud-mic-btn[data-state="{st}"]' in HTML
    assert "micBtn.className = 'hud-btn" not in BRAIN                                   # không còn lớp Tailwind của HUD cũ
    # bấm lõi = bật / tắt mic (giống HUD cũ), ngoài phím Cách và nút MIC
    click = JS[JS.index("cv.addEventListener('click'"):JS.index("function clock()")]
    assert "window.toggleHudMic()" in click and "fetch(" not in click


def test_not_logged_in_shows_empty_cells_and_how_to_log_in():
    login = JS[JS.index("function toLogin("):JS.index("function toLogin(") + 900]
    assert "Chưa đăng nhập" in login and "tile(id, '–', '')" in login and "setNodes([])" in login


def test_vietnamese_uses_a_font_with_diacritics_and_page_works_offline():
    assert "fonts-portal.css" in HTML and "--vn: 'Be Vietnam Pro'" in HTML and "font-family: var(--vn)" in HTML
    assert "fonts.googleapis.com" not in HTML and "cdn." not in HTML


def test_effects_use_one_clock_and_a_draw_error_never_stops_the_loop():
    # Lỗi 2026-10-12: t0 hiệu ứng lấy từ performance.now() còn vòng vẽ dùng dấu thời gian requestAnimationFrame → tiến độ âm →
    # arc() bán kính âm ném lỗi → mất mọi lớp vẽ sau đó (nút agent phía trước, tia, mảnh suy nghĩ) ở trạng thái nói / suy nghĩ / sự cố.
    for effect in ("shocks.push({ t0: performance.now()", "beams.push({ n, color, t0: performance.now()", "t0: performance.now(), dur"):
        assert effect not in JS, effect
    assert "clockNow = now;" in JS and "try { draw(now); }" in JS and "if (p < 0) continue;" in JS


def test_neural_interactions_exist():
    for marker in ("function nerveCurve", "function fireNeuron", "cv.addEventListener('mousemove'", "cv.addEventListener('click'", "function showTip"):
        assert marker in JS, marker
    assert 'id="tip"' in HTML and "#frame > main { pointer-events: none; }" in HTML


def test_layout_modules_and_performance():
    for gone in ("2b. bệ phát ba chiều", "4b. vòng chữ dữ liệu", "ringText", "ringData"):
        assert gone not in JS, gone
    for marker in ("MODULE_LINKS", "ring === 'in'", "function adaptQuality", "function setQuality", "BUCKET", "1b. lưới tổ ong", "7b. tia điện", "--px",
                   "function scramble", "function swapLabel"):
        assert marker in JS, marker
    draw = JS[JS.index("  function draw(now) {"):JS.index("  function hexPath(")]
    assert "createRadialGradient(PX" not in draw                            # không tạo gradient cho từng nơ-ron
    assert draw.count("shadowBlur") == 0                                    # không đổ bóng trong vòng vẽ (chỉ nút đang rê)
    assert "for (let i = 0; i < N; i++) {" in draw                          # toạ độ mọi hạt luôn đúng, chế độ nhẹ chỉ vẽ một nửa
    scr = JS[JS.index("function scramble"):JS.index("function scramble") + 900]
    assert "setTimeout" in scr
    assert 'id="perf-chip"' in HTML


def test_state_changes_are_smooth_no_shake():
    # 2026-10-12 người dùng: "chuyển hiệu ứng rung giật màn hình". Nguyên nhân chính: góc quay = t × tốc độ (t = giây từ lúc mở
    # trang) → đổi trạng thái là góc nhảy hàng nghìn độ trong một khung. Nay góc tích luỹ theo dt; bỏ rung khung (glitch).
    draw = JS[JS.index("  function draw(now) {"):JS.index("  function hexPath(")]
    assert not re.search(r"(?<![\w.])t \* (cur\.|0\.22 \* \(0\.5 \+ cur)", draw)     # không còn "t × tốc độ"
    assert "ang.core += dt * cur.spin" in draw and "const rot = ang.core" in draw and "ctx.rotate(ang.reticle)" in draw
    assert "glitch" not in JS.replace("(glitch) mỗi lần đổi trạng thái", "") and "body.glitch" not in HTML
    # màu panel đổi dần theo lõi (không đặt --c ngay khi đổi trạng thái)
    set_state = JS[JS.index("  function setState(s, opts = {}) {"):JS.index("  function swapLabel(")]
    assert "setProperty('--c'" not in set_state and "setProperty('--c', cssHue)" in draw
    assert ".state-pill.swap" in HTML and "#subtitle.soft" in HTML
