# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 Dương Thanh Lịch — VN-MateAI. See LICENSE and NOTICE.
"""
tests/test_agent_shared_skills.py
=================================
Máy chủ chạy Docker (Mac / Linux) điều phối máy trạm Windows qua Agent — 2026-10-13:

  - kỹ năng máy chủ đẩy / đóng gói xuống máy trạm từng lỗi TypeError vì `export_skill` của Agent không nhận
    `needs_desktop=…` (auto_play_music) → không nạp được;
  - phím media, mở nhạc / YouTube / Notepad chỉ có trên máy chủ → gọi tới máy trạm báo "không tìm thấy kỹ năng";
  - open_text_file_notepad báo "thành công" khi không thấy file và mở được tệp ngoài Desktop (`..\\..\\x`);
  - gói cài máy chủ không kèm VNMateAgent.exe (dist/ không có trong git) → máy trạm phải tự cài Python.
Không gọi mạng, không mở cửa sổ.
"""
from __future__ import annotations

import ast
import io
import sys
import zipfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SHARED = [ln.strip() for ln in (ROOT / "client_agent" / "shared_skills.txt").read_text(encoding="utf-8").splitlines()
          if ln.strip() and not ln.startswith("#")]


@pytest.fixture
def agent_pm():
    """`core.plugin_manager` của AGENT (không phải của máy chủ) — hoàn nguyên sys.modules sau test."""
    saved_modules, saved_path = dict(sys.modules), list(sys.path)
    for name in [m for m in sys.modules if m == "core" or m.startswith("core.") or m == "skills" or m.startswith("skills.")]:
        del sys.modules[name]
    sys.path.insert(0, str(ROOT / "client_agent"))
    try:
        import core.plugin_manager as pm
        assert Path(pm.__file__).resolve().parent.parent == ROOT / "client_agent"
        yield pm
    finally:
        sys.modules.clear()
        sys.modules.update(saved_modules)
        sys.path[:] = saved_path


def test_agent_accepts_server_contract_kwargs(agent_pm, tmp_path):
    (tmp_path / "s.py").write_text(
        "from core.plugin_manager import export_skill\n"
        "@export_skill(name='x', description='d', parameters_schema={'type': 'object', 'properties': {}},\n"
        "              needs_desktop=True, data_classification='INTERNAL')\n"
        "def x():\n    return {'ok': 1}\n", encoding="utf-8")
    pm = agent_pm.ClientPluginManager(skills_dir=tmp_path)
    assert pm.load_plugins() == 1
    assert pm.execute_skill("x") == {"status": "success", "skill": "x", "ok": 1}


def test_agent_loads_desktop_skills_from_project_checkout(agent_pm):
    pm = agent_pm.ClientPluginManager()
    pm.load_plugins()
    names = set(pm.get_skill_names())
    assert {"media_control", "auto_play_music", "open_youtube", "open_text_file_notepad"} <= names
    assert pm.execute_skill("media_control", {"action": "rm -rf"})["status"] == "error"


def test_shared_skills_exist_and_only_use_stdlib(agent_pm):
    assert SHARED, "shared_skills.txt rỗng"
    for name in SHARED:
        path = ROOT / "skills" / name
        assert path.is_file(), name
        assert not (ROOT / "client_agent" / "skills" / name).exists(), f"{name}: một tính năng một bản mã"
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            mods = [a.name for a in node.names] if isinstance(node, ast.Import) else \
                   [node.module] if isinstance(node, ast.ImportFrom) else []
            for mod in mods:
                top = mod.split(".")[0]
                assert top in sys.stdlib_module_names or mod == "core.plugin_manager", f"{name} import {mod}"


def test_source_package_ships_shared_skills():
    from mateai.interfaces.http import agent_packages
    names = set(zipfile.ZipFile(io.BytesIO(agent_packages._source_zip())).namelist())
    assert {f"skills/{n}" for n in SHARED} <= names
    assert "skills/registry.json" not in names and "shared_skills.txt" in names


def test_notepad_refuses_paths_outside_desktop(monkeypatch, tmp_path):
    import importlib.util
    spec = importlib.util.spec_from_file_location("_np", ROOT / "skills" / "auto_open_notepad_file.py")
    np_mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(np_mod)
    desk = tmp_path / "Desktop"
    desk.mkdir()
    (tmp_path / "secret.txt").write_text("x")
    (desk / "ghi_chu.txt").write_text("y")
    opened = []
    monkeypatch.setattr(np_mod.os, "name", "nt")
    monkeypatch.setattr(np_mod, "_get_desktop_paths", lambda: [str(desk)])
    monkeypatch.setattr(np_mod.subprocess, "Popen", lambda args, **kw: opened.append(args))

    r = np_mod.open_text_file_notepad("..\\secret.txt")
    assert r["status"] == "error" and not opened                      # chỉ tên tệp trên Desktop
    assert np_mod.open_text_file_notepad("khong_co.txt")["status"] == "error"
    ok = np_mod.open_text_file_notepad("ghi_chu.txt")
    assert ok["status"] == "success" and opened == [["notepad.exe", str(desk / "ghi_chu.txt")]]


def test_download_via_localhost_uses_public_host_not_container_ip(monkeypatch):
    """Máy chủ Docker: mở Portal bằng localhost → IP tự dò là IP container (172.x), máy trạm không tới được."""
    import json
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    import mateai.interfaces.http.routers.workers as workers
    from mateai.interfaces.http.auth_dependencies import get_current_user
    app = FastAPI()
    app.include_router(workers.router)
    app.dependency_overrides[get_current_user] = lambda: {"username": "boss", "role": "admin"}
    c = TestClient(app, base_url="https://127.0.0.1:18443")
    monkeypatch.setattr(workers, "_get_server_local_ip", lambda: "172.19.0.4")
    monkeypatch.setenv("VNMATEAI_PUBLIC_HOST", "192.168.1.10")
    cfg = json.loads(zipfile.ZipFile(io.BytesIO(c.get("/api/v1/download-agent?platform=windows").content)).read("config.json"))
    assert cfg["ws_url"] == "wss://192.168.1.10:18443/ws/client" and cfg["master_ip"] == "192.168.1.10"
    monkeypatch.delenv("VNMATEAI_PUBLIC_HOST")
    c2 = TestClient(app, base_url="https://10.0.0.7")                  # mở bằng IP LAN: giữ nguyên địa chỉ đó
    cfg2 = json.loads(zipfile.ZipFile(io.BytesIO(c2.get("/api/v1/download-agent?platform=windows").content)).read("config.json"))
    assert cfg2["ws_url"] == "wss://10.0.0.7:443/ws/client"


def test_release_refuses_stale_agent_build(monkeypatch, tmp_path):
    import importlib.util
    spec = importlib.util.spec_from_file_location("_br", ROOT / "scripts" / "build_release.py")
    br = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(br)
    fake_root, app = tmp_path / "root", tmp_path / "app"
    (app / "client_agent").mkdir(parents=True)
    (app / "client_agent" / "agent.py").write_text('AGENT_VERSION = "9.9.9"\n')
    win = fake_root / "dist" / "agent" / "windows"
    win.mkdir(parents=True)
    (win / "VNMateAgent.exe").write_bytes(b"MZ")
    (win / "version.txt").write_text("9.9.8")
    monkeypatch.setattr(br, "ROOT", fake_root)
    with pytest.raises(SystemExit):
        br.copy_agent_builds(app)
    (win / "version.txt").write_text("9.9.9")
    br.copy_agent_builds(app)
    assert (app / "dist" / "agent" / "windows" / "VNMateAgent.exe").read_bytes() == b"MZ"


async def test_skill_refusal_is_not_reported_as_success():
    """open_youtube từ chối đường dẫn lạ trả {"status": "error", "message": …} (không có khoá "error"):
    Agent 2.2 gửi success=True và máy chủ từng báo "success" → AI nói "đã mở"."""
    import asyncio
    import json
    from mateai.interfaces.websocket.client_orchestrator import Orchestrator

    class _WS:
        def __init__(self):
            self.sent = []

        async def send_text(self, text):
            self.sent.append(json.loads(text))

    o = Orchestrator()
    ws = _WS()
    await o.register_client("PC1", ws, {"agent_version": "2.2.1"})
    for reply, want in (({"status": "error", "message": "Chỉ mở được đường dẫn YouTube"}, "error"),
                        ({"status": "success", "success": False}, "error"),
                        ({"status": "success", "hostname": "PC1"}, "success")):
        t = asyncio.create_task(o.execute_on_client("PC1", "open_youtube", timeout=5))
        await asyncio.sleep(0.05)
        o.handle_incoming_message("PC1", {"action": "result", "task_id": ws.sent[-1]["task_id"],
                                          "success": True, "result": reply})
        assert (await t)["status"] == want, reply


def test_agent_reports_refusal_as_failure():
    saved_modules, saved_path = dict(sys.modules), list(sys.path)
    sys.path.insert(0, str(ROOT / "client_agent"))
    try:
        import agent
        assert agent._succeeded({"status": "error", "message": "từ chối"}) is False
        assert agent._succeeded({"status": "success", "success": False}) is False
        assert agent._succeeded({"status": "success", "result": "ok"}) is True
    finally:
        sys.modules.clear()
        sys.modules.update(saved_modules)
        sys.path[:] = saved_path
