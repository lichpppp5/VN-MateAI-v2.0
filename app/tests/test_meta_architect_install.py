# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 Dương Thanh Lịch — VN-MateAI. See LICENSE and NOTICE.
"""
tests/test_meta_architect_install.py
====================================
Tạo kỹ năng mới (`meta_architect.create_skill` / `install_skill`) — một quy trình
cho cả công cụ `create_new_skill` và đường tự tạo khi model báo thiếu công cụ.

Các lỗi đã sửa (mỗi test ứng với một lỗi):
  - trả tên công cụ SAI: `get_skill_names()[-1]` (danh sách theo chữ cái);
  - ghi ĐÈ tệp kỹ năng có sẵn (kể cả kỹ năng lõi) khi trùng tên tệp;
  - tên công cụ trùng công cụ có sẵn -> công cụ thật bị thay im lặng;
  - đầu tệp docstring + `from __future__` -> tệp ghi ra sai cú pháp;
  - module không nạp được vẫn báo "thành công", tệp hỏng nằm lại;
  - mã chạy lệnh ngay khi nạp (mỗi lần reload lại chạy);
  - không bật auto_execute -> treo chờ hộp thoại Windows trên máy chủ;
  - bộ kiểm tra an ninh bỏ lọt shell=True, ctypes, os.kill.
Chạy trong thư mục tạm với plugin_manager giả nạp tệp thật — không đụng skills/.
"""
from __future__ import annotations

import importlib.util
import sys

import pytest

import mateai.application.skills.meta_architect as ma
from mateai.config.loader import settings


def _skill(name="play_song_demo", extra="", top=""):
    return f'''{top}from core.plugin_manager import export_skill
{extra}

@export_skill(name="{name}", description="Phát một bài hát.", parameters_schema={{"type": "object", "properties": {{}}}})
def {name}() -> dict:
    return {{"status": "success"}}
'''


class FakePluginManager:
    """Nạp mọi skills/*.py trong thư mục tạm như plugin_manager thật (import tệp, đọc __skill_meta__)."""

    def __init__(self, directory):
        self.dir = directory
        self.core = {"kill_process": "skills.sysadmin_skills", "zero_touch_onboard_employee": "skills.hr"}
        self.reg = dict(self.core)

    def get_skill_names(self):
        return sorted(self.reg)

    def get_skill_modules(self):
        return dict(self.reg)

    def load_plugins(self):
        self.reg = dict(self.core)
        for f in sorted(self.dir.glob("*.py")):
            name = f"skills.{f.stem}"
            spec = importlib.util.spec_from_file_location(name, f)
            mod = importlib.util.module_from_spec(spec)
            try:
                spec.loader.exec_module(mod)
            except Exception:  # noqa: BLE001 — như plugin_manager: module lỗi bị bỏ qua
                continue
            sys.modules[name] = mod
            for obj in vars(mod).values():
                meta = getattr(obj, "__skill_meta__", None)
                if meta:
                    self.reg[meta["name"]] = name
        return len(self.reg)


@pytest.fixture
def env(tmp_path, monkeypatch):
    import core.plugin_manager as pmod
    import mateai.application.skills.skill_router as sr
    pm = FakePluginManager(tmp_path)
    monkeypatch.setattr(pmod, "plugin_manager", pm)
    monkeypatch.setattr(settings, "SKILLS_DIR", tmp_path, raising=False)
    monkeypatch.setattr(ma, "_auto_install_enabled", lambda: True)
    monkeypatch.setattr(sr, "find_existing_skill", lambda intent: None)
    yield pm, tmp_path
    for mod in [m for m in sys.modules if m.startswith("skills.auto_")]:
        sys.modules.pop(mod, None)


def test_returns_the_registered_tool_name_not_the_alphabetically_last(env):
    pm, d = env
    res = ma.meta_architect.install_skill(_skill("play_song_demo"), "auto_play_song", "mở bài hát")
    assert res["status"] == "success"
    # Danh sách theo chữ cái kết thúc bằng zero_touch_onboard_employee — bản cũ trả tên đó.
    assert res["skill_name"] == "play_song_demo" and pm.reg["play_song_demo"] == "skills.auto_play_song"
    text = (d / "auto_play_song.py").read_text(encoding="utf-8")
    assert text.startswith("# Auto-synthesised skill: auto_play_song") and "# Yêu cầu gốc: mở bài hát" in text


def test_never_overwrites_an_existing_skill_file(env):
    pm, d = env
    (d / "auto_play_song.py").write_text(_skill("old_song_tool"), encoding="utf-8")
    pm.load_plugins()
    res = ma.meta_architect.install_skill(_skill("play_song_demo"), "auto_play_song")
    assert res["status"] == "success" and res["file"] == "auto_play_song_2.py"
    assert "old_song_tool" in (d / "auto_play_song.py").read_text(encoding="utf-8")
    assert {"old_song_tool", "play_song_demo"} <= set(pm.reg)


def test_tool_name_clashing_with_existing_tool_is_refused(env):
    pm, d = env
    res = ma.meta_architect.install_skill(_skill("kill_process"), "auto_kill")
    assert res["status"] == "error" and "kill_process" in res["message"]
    assert not list(d.glob("*.py")) and pm.reg["kill_process"] == "skills.sysadmin_skills"


def test_docstring_and_future_import_still_valid_after_header(env):
    pm, _ = env
    code = '"""Kỹ năng phát nhạc."""\nfrom __future__ import annotations\n' + _skill()
    assert ma.meta_architect.install_skill(code, "auto_music")["status"] == "success"


def test_module_that_fails_to_load_is_removed_and_reported(env):
    pm, d = env
    res = ma.meta_architect.install_skill(_skill(extra="import khong_co_thu_vien_nay"), "auto_broken")
    assert res["status"] == "error" and "không nạp được" in res["message"]
    assert not (d / "auto_broken.py").exists() and "play_song_demo" not in pm.reg


@pytest.mark.parametrize("bad,why", [
    ('import webbrowser\nwebbrowser.open("https://x")', "chạy lệnh ngay khi nạp"),
    ("for _ in range(3):\n    pass", "vòng lặp"),
])
def test_code_running_at_import_is_refused(env, bad, why):
    res = ma.meta_architect.install_skill(_skill(extra=bad), "auto_side_effect")
    assert res["status"] == "error" and why in res["message"]


@pytest.mark.parametrize("code,why", [
    (_skill(extra="import subprocess\n\ndef _r():\n    subprocess.run('dir', shell=True)"), "shell=True"),
    (_skill(extra="import ctypes"), "ctypes"),
    (_skill(extra="import os\n\ndef _k():\n    os.kill(1, 9)"), "os.kill"),
])
def test_security_audit_blocks_dangerous_code(env, code, why):
    res = ma.meta_architect.install_skill(code, "auto_danger")
    assert res["status"] == "error" and why in res["message"]


def test_without_auto_install_code_waits_for_review(env, monkeypatch):
    pm, d = env
    monkeypatch.setattr(ma, "_auto_install_enabled", lambda: False)
    res = ma.meta_architect.install_skill(_skill(), "auto_play_song")
    assert res["status"] == "pending_review"
    assert (d / "pending" / "auto_play_song.py").exists() and "play_song_demo" not in pm.reg


@pytest.mark.parametrize("bad_name", ["Play Song", "a", "phát_nhạc"])
def test_invalid_tool_names_refused(env, bad_name):
    res = ma.meta_architect.install_skill(_skill().replace('"play_song_demo"', repr(bad_name)), "auto_x")
    assert res["status"] == "error" and "tên công cụ không hợp lệ" in res["message"]


def test_file_stem_is_ascii():
    assert ma.skill_file_stem("Mở bài hát Lạc Trôi trên YouTube!") == "auto_mo_bai_hat_lac_troi_tren_youtube"
    assert ma.skill_file_stem("auto_play") == "auto_play"
    assert ma.skill_file_stem("???") == "auto_skill"


def test_create_new_skill_tool_and_auto_path_share_one_flow(env, monkeypatch):
    import importlib
    meta_skills = importlib.import_module("skills.meta_skills")
    from mateai.application.agent.llm_engine import llm_engine
    monkeypatch.setattr(ma.meta_architect, "synthesize_skill", lambda **k: _skill("tra_cuu_gia_vang"))
    out = meta_skills.create_new_skill("tra cứu giá vàng hôm nay")
    assert out["status"] == "success" and out["skill_name"] == "tra_cuu_gia_vang"
    monkeypatch.setattr(ma.meta_architect, "synthesize_skill", lambda **k: _skill("doc_tin_tuc"))
    name = llm_engine._synthesise_and_install(query="đọc tin tức mới", meta_architect=ma.meta_architect,
                                              plugin_manager=None)
    assert name == "doc_tin_tuc"


@pytest.mark.parametrize("tool,level", [
    ("list_available_skills", 1),     # chỉ đọc — trước đây 5 vì "s-KILL-s" khớp "kill"
    ("reload_all_skills", 2),
    ("create_new_skill", 4),          # cài mã mới: vẫn cần duyệt ở kênh không tin cậy
    ("install_skill_from_url", 5),
    ("tra_cuu_skill_moi", 2),         # kỹ năng AI tạo có chữ "skill" trong tên
    ("describe_report", 2),           # "de-SCRIPT-ion" không phải "script"
    ("kill_process", 4), ("delete_item", 5), ("execute_script", 4), ("format_disk", 5),
])
def test_risk_level_matches_words_not_substrings(tool, level):
    from mateai.application.security.zero_trust import hitl_manager
    assert hitl_manager.get_risk_level(tool) == level


# ── 2026-10-12: "tạo kỹ năng mới thì AI báo phải chờ" ────────────────────────────────────────────────────────────────
# Nguyên nhân: mã sinh ra hỏng (bộ nạp từ chối `if`/`try` cấp module, an ninh cấm ctypes…) → lỗi về agent → AI gọi
# create_new_skill lại; mỗi lần hỏng vẫn tốn một lượt hạn mức 3 / 10 phút → "thử lại sau … s", chưa kỹ năng nào được cài.

@pytest.mark.parametrize("top", ['import sys\nif sys.platform == "win32":\n    import winreg\n',
                                 "try:\n    import winreg\nexcept ImportError:\n    winreg = None\n    print('thiếu')\n"])
def test_structure_check_uses_the_same_rule_as_the_loader(env, top):
    from pathlib import Path
    from core.plugin_manager import top_level_side_effects
    code = _skill(top=top)
    res = ma.meta_architect.install_skill(code, "auto_platform")
    p = env[1] / "probe.py"
    p.write_text(code, encoding="utf-8")
    assert top_level_side_effects(Path(p))                       # bộ nạp từ chối …
    assert res["status"] == "error" and res["retryable"] and "chạy lệnh ngay khi nạp" in res["message"]   # … thì ở đây cũng vậy
    assert not (env[1] / "auto_platform.py").exists()


def test_broken_code_is_repaired_within_one_call(env, monkeypatch):
    calls = []

    def synth(intent_description, failed_context=None):
        calls.append(dict(failed_context or {}))
        return _skill("phat_tiep_video", extra="import ctypes") if len(calls) == 1 else _skill("phat_tiep_video")
    monkeypatch.setattr(ma.meta_architect, "synthesize_skill", synth)
    res = ma.meta_architect.create_skill("phát tiếp video đang dừng")
    assert res["status"] == "success" and res["skill_name"] == "phat_tiep_video" and res["attempts"] == 2
    assert "ctypes" in calls[1]["loi_lan_truoc"] and "import ctypes" in calls[1]["ma_lan_truoc_bi_tu_choi"]


def test_gives_up_after_max_attempts_and_tells_the_agent_not_to_retry(env, monkeypatch):
    monkeypatch.setattr(ma.meta_architect, "synthesize_skill", lambda **k: _skill("x_tool_bad", extra="import ctypes"))
    res = ma.meta_architect.create_skill("việc gì đó")
    assert res["status"] == "error" and res["attempts"] == ma.MAX_SYNTH_ATTEMPTS
    assert "Đừng gọi create_new_skill lại" in res["message"]


def test_failed_attempts_do_not_use_up_the_creation_quota(monkeypatch):
    import importlib
    ms = importlib.import_module("skills.meta_skills")
    from mateai.application.security.security_guard import CURRENT_PRINCIPAL
    outcomes = iter([{"status": "error", "message": "hỏng"}] * 4 + [{"status": "success", "skill_name": f"k{i}"} for i in range(4)])

    class Fake:
        def create_skill(self, desc, skill_name=None):
            return next(outcomes)
    monkeypatch.setattr(ma, "meta_architect", Fake())
    monkeypatch.setattr(ms, "_recent", {})
    monkeypatch.setattr(ms, "_failed", {})
    tok = CURRENT_PRINCIPAL.set("admin")
    try:
        outs = [ms.create_new_skill(f"việc {i}") for i in range(8)]
    finally:
        CURRENT_PRINCIPAL.reset(tok)
    # 4 lần hỏng KHÔNG chặn 3 kỹ năng thành công kế tiếp; tới kỹ năng thứ 4 mới chạm hạn mức
    assert [o["status"] for o in outs] == ["error"] * 4 + ["success"] * 3 + ["error"]
    assert outs[-1]["code"] == "SKILL_CREATION_RATE_LIMITED"


def test_failure_quota_stops_endless_retry_loops(monkeypatch):
    import importlib
    ms = importlib.import_module("skills.meta_skills")
    from mateai.application.security.security_guard import CURRENT_PRINCIPAL

    class Fake:
        def create_skill(self, desc, skill_name=None):
            return {"status": "error", "message": "hỏng"}
    monkeypatch.setattr(ma, "meta_architect", Fake())
    monkeypatch.setattr(ms, "_recent", {})
    monkeypatch.setattr(ms, "_failed", {})
    tok = CURRENT_PRINCIPAL.set("device:robot-a")
    try:
        outs = [ms.create_new_skill("việc") for _ in range(ms.FAIL_LIMIT + 1)]
    finally:
        CURRENT_PRINCIPAL.reset(tok)
    assert outs[-1]["code"] == "SKILL_CREATION_RATE_LIMITED" and "thất bại" in outs[-1]["message"]


def test_media_keys_are_a_builtin_skill_not_generated_code():
    from pathlib import Path
    from core.plugin_manager import top_level_side_effects
    src = Path("skills/pc_control_skills.py").read_text(encoding="utf-8")
    assert 'name="media_control"' in src and "needs_desktop=True" in src
    assert not top_level_side_effects(Path("skills/pc_control_skills.py"))
    prompt = ma._CODE_GEN_SYSTEM_PROMPT
    assert "ctypes" in prompt and "CẤP MODULE" in prompt and "media_control" in prompt
