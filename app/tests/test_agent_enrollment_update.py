# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 Dương Thanh Lịch — VN-MateAI. See LICENSE and NOTICE.
"""
tests/test_agent_enrollment_update.py
=====================================
Agent máy trạm (yêu cầu 2026-10-05): mã đăng ký RIÊNG từng máy, tự cập nhật,
gói Windows .exe / macOS.

  - mã đăng ký dùng MỘT lần, hết hạn, chép sang máy khác bị từ chối;
  - khoá thiết bị quyết định client_id (Agent không tự xưng máy khác); thu hồi
    một máy không ảnh hưởng máy khác; secret chung chỉ còn nhận từ chính máy chủ;
  - API cập nhật chỉ cho máy có khoá; manifest SHA-256 khớp gói;
  - Agent: không cài gói sai SHA-256, không hạ phiên bản, không đè cấu hình/khoá,
    không ghi ra ngoài thư mục Agent;
  - gói tải về theo nền tảng, có mã đăng ký, không còn secret chung.
Không gọi mạng.
"""
from __future__ import annotations

import asyncio
import hashlib
import io
import json
import sys
import zipfile
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]


# ── Mã đăng ký + khoá thiết bị ──────────────────────────────────────────────

def test_code_is_single_use_and_gives_a_unique_device(monkeypatch):
    from mateai.application.devices import worker_enrollment as we
    code, info = we.create_enroll_code("boss", label="Kế toán")
    dev = we.enroll(code, "KT-PC01", "Windows 11", "windows-exe", "2.1.0")
    assert dev["client_id"].startswith("kt-pc01") and len(dev["device_token"]) > 30
    with pytest.raises(we.EnrollError):
        we.enroll(code, "OTHER-PC")                                   # dùng lại / chép sang máy khác
    code2, _ = we.create_enroll_code("boss")
    dev2 = we.enroll(code2, "KT-PC01")                                # trùng tên máy -> client_id khác
    assert dev2["client_id"] != dev["client_id"]
    found = we.authenticate_device_token(dev["device_token"])
    assert found["client_id"] == dev["client_id"] and found["label"] == "Kế toán"
    assert we.revoke(dev["client_id"], "boss")
    assert we.authenticate_device_token(dev["device_token"]) is None
    assert we.authenticate_device_token(dev2["device_token"])["client_id"] == dev2["client_id"]  # máy kia vẫn chạy


def test_expired_or_forged_code_is_rejected():
    from mateai.application.devices import worker_enrollment as we
    from mateai.infrastructure.database.db_manager import db_manager
    db_manager.add_worker_enroll_code(hashlib.sha256(b"VNM-old").hexdigest(), "", "boss", "2000-01-01T00:00:00")
    for bad in ("VNM-old", "VNM-nope", "khong-dung-dinh-dang", ""):
        with pytest.raises(we.EnrollError):
            we.enroll(bad, "PC")


def test_shared_secret_only_from_the_server_itself(monkeypatch):
    from mateai.interfaces.http import enrollment, ws_auth
    import mateai.config.loader as loader
    from mateai.application.devices import worker_enrollment as we
    monkeypatch.setattr(enrollment, "get_worker_enrollment_secret", lambda: "shared-xyz")
    monkeypatch.setattr(loader, "get_config_section", lambda name: {})
    assert ws_auth.worker_principal("shared-xyz", "127.0.0.1") == {"kind": "shared"}
    assert ws_auth.worker_principal("shared-xyz", "192.168.1.50") is None      # máy trạm thật: phải có khoá riêng
    monkeypatch.setattr(loader, "get_config_section",
                        lambda name: {"allow_shared_worker_secret": True} if name == "security" else {})
    assert ws_auth.worker_principal("shared-xyz", "192.168.1.50") == {"kind": "shared"}
    code, _ = we.create_enroll_code("boss")
    dev = we.enroll(code, "PC9")
    p = ws_auth.worker_principal(dev["device_token"], "192.168.1.50")
    assert p["kind"] == "device" and p["client_id"] == dev["client_id"]


def test_enroll_api_is_public_but_rate_limited(monkeypatch):
    import mateai.interfaces.http.server as server
    import mateai.interfaces.http.routers.agent_devices as ad
    from mateai.application.devices import worker_enrollment as we
    ad._FAILS.clear()
    client = TestClient(server.app)
    for _ in range(10):
        assert client.post("/api/v1/agent/enroll", json={"code": "VNM-sai", "hostname": "x"}).status_code == 403
    code, _ = we.create_enroll_code("boss")
    assert client.post("/api/v1/agent/enroll", json={"code": code, "hostname": "x"}).status_code == 429
    ad._FAILS.clear()
    r = client.post("/api/v1/agent/enroll", json={"code": code, "hostname": "PC-API"})
    assert r.status_code == 200 and r.json()["client_id"].startswith("pc-api")


def test_update_api_needs_a_device_key_and_serves_matching_hash(monkeypatch):
    import mateai.interfaces.http.server as server
    from mateai.application.devices import worker_enrollment as we
    client = TestClient(server.app)
    assert client.get("/api/v1/agent/update/manifest?package=source").status_code == 401
    code, _ = we.create_enroll_code("boss")
    token = we.enroll(code, "PC-UPD")["device_token"]
    h = {"Authorization": f"Bearer {token}"}
    m = client.get("/api/v1/agent/update/manifest?package=source", headers=h).json()
    pkg = client.get(m["url"], headers=h)
    assert pkg.status_code == 200 and hashlib.sha256(pkg.content).hexdigest() == m["sha256"] == pkg.headers["X-Agent-SHA256"]
    names = zipfile.ZipFile(io.BytesIO(pkg.content)).namelist()
    assert "agent.py" in names and not {"config.json", "device.json", "server_cert.pem"} & set(names)
    assert client.get("/api/v1/agent/update/manifest?package=windows-exe", headers=h).status_code in (200, 404)
    we.revoke(we.authenticate_device_token(token)["client_id"], "boss")
    assert client.get("/api/v1/agent/update/manifest?package=source", headers=h).status_code == 401


def test_device_key_decides_client_id_on_ws(monkeypatch):
    import mateai.interfaces.http.server as server
    from mateai.application.devices import worker_enrollment as we
    from mateai.interfaces.websocket.client_orchestrator import orchestrator
    code, _ = we.create_enroll_code("boss")
    dev = we.enroll(code, "PC-WS")
    seen = {}
    real_register = orchestrator.register_client

    async def spy(cid, ws, meta):
        seen["cid"] = cid
        await real_register(cid, ws, meta)

    monkeypatch.setattr(orchestrator, "register_client", spy)
    with TestClient(server.app).websocket_connect(f"/ws/client?token={dev['device_token']}") as ws:
        ws.send_text(json.dumps({"action": "register", "client_id": "MAY-GIAM-DOC", "agent_version": "9.9.9"}))
        ws.send_text(json.dumps({"action": "ping"}))
    assert seen["cid"] == dev["client_id"]                          # không phải "MAY-GIAM-DOC"
    we.revoke(dev["client_id"], "boss")
    from starlette.websockets import WebSocketDisconnect
    with pytest.raises(WebSocketDisconnect):
        with TestClient(server.app).websocket_connect(f"/ws/client?token={dev['device_token']}") as ws:
            ws.receive_text()


def _admin_app(router_mod):
    from mateai.interfaces.http.auth_dependencies import get_current_user
    app = FastAPI()
    app.include_router(router_mod.router)
    app.dependency_overrides[get_current_user] = lambda: {"username": "boss", "role": "admin"}
    return TestClient(app)


def test_download_embeds_one_time_code_and_falls_back_without_build(monkeypatch, tmp_path):
    import mateai.interfaces.http.routers.workers as workers
    from mateai.interfaces.http import agent_packages
    monkeypatch.setattr(agent_packages, "FROZEN", {"windows-exe": tmp_path / "w" / "VNMateAgent.exe",
                                                    "macos-bin": tmp_path / "m" / "VNMateAgent"})
    c = _admin_app(workers)
    r = c.get("/api/v1/download-agent?platform=windows&label=Lan")
    assert r.headers["X-Agent-Package"] == "source"                 # chưa build .exe -> gói Python
    cfg = json.loads(zipfile.ZipFile(io.BytesIO(r.content)).read("config.json"))
    assert cfg["enroll_code"].startswith("VNM-") and "enrollment_token" not in cfg and cfg["label"] == "Lan"
    r2 = c.get("/api/v1/download-agent?platform=windows")
    assert json.loads(zipfile.ZipFile(io.BytesIO(r2.content)).read("config.json"))["enroll_code"] != cfg["enroll_code"]

    exe = tmp_path / "w" / "VNMateAgent.exe"
    exe.parent.mkdir()
    exe.write_bytes(b"MZ-fake-exe")
    (exe.parent / "version.txt").write_text("2.2.0")
    r3 = c.get("/api/v1/download-agent?platform=windows")
    z = zipfile.ZipFile(io.BytesIO(r3.content))
    assert r3.headers["X-Agent-Package"] == "windows-exe" and r3.headers["X-Agent-Version"] == "2.2.0"
    assert set(z.namelist()) >= {"VNMateAgent.exe", "config.json", "HUONG_DAN_CAI_DAT.txt"}
    assert "agent.py" not in z.namelist()


def test_admin_api_lists_revokes_and_creates_codes(monkeypatch):
    import mateai.interfaces.http.routers.agent_devices as ad
    from mateai.application.devices import worker_enrollment as we
    c = _admin_app(ad)
    created = c.post("/api/v1/agent/enroll-codes", json={"label": "Kho"}).json()
    assert created["code"].startswith("VNM-")
    data = c.get("/api/v1/agent/devices").json()
    assert any(x["label"] == "Kho" and x["state"] == "waiting" for x in data["codes"])
    assert "code" not in json.dumps(data["codes"]).replace("code_ref", "")   # mã thật không bao giờ trả lại
    dev = we.enroll(created["code"], "KHO-PC")
    assert c.post(f"/api/v1/agent/devices/{dev['client_id']}/revoke").json()["status"] == "success"
    assert c.post(f"/api/v1/agent/devices/{dev['client_id']}/revoke").status_code == 404


# ── Phía Agent (agent_runtime) ──────────────────────────────────────────────

@pytest.fixture
def rt():
    saved_modules, saved_path = dict(sys.modules), list(sys.path)
    sys.path.insert(0, str(ROOT / "client_agent"))
    try:
        import agent_runtime
        yield agent_runtime
    finally:
        sys.modules.clear()
        sys.modules.update(saved_modules)
        sys.path[:] = saved_path


def _zip(files):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        for n, d in files.items():
            z.writestr(n, d)
    return buf.getvalue()


def test_source_update_never_touches_config_keys_or_outside(rt, tmp_path):
    (tmp_path / "config.json").write_text("CFG")
    (tmp_path / "device.json").write_text("KEY")
    n = rt.apply_source_update(_zip({"agent.py": "NEW", "skills/a.py": "S", "config.json": "EVIL",
                                     "device.json": "EVIL", "../outside.py": "EVIL", "logs/x.log": "L"}), tmp_path)
    assert n == 2 and (tmp_path / "agent.py").read_text() == "NEW" and (tmp_path / "skills" / "a.py").exists()
    assert (tmp_path / "config.json").read_text() == "CFG" and (tmp_path / "device.json").read_text() == "KEY"
    assert not (tmp_path.parent / "outside.py").exists() and not (tmp_path / "logs").exists()


def test_bad_hash_is_refused_and_no_downgrade(rt, monkeypatch):
    payload = b"zip-bytes"
    good = {"url": "/u", "size": len(payload), "sha256": hashlib.sha256(payload).hexdigest(), "version": "2.2.0"}
    monkeypatch.setattr(rt, "_request", lambda *a, **k: (200, payload, {}))
    assert rt.download_verified("https://s", "t", None, good) == payload
    with pytest.raises(ValueError):
        rt.download_verified("https://s", "t", None, {**good, "sha256": "0" * 64})
    monkeypatch.setattr(rt, "_request", lambda *a, **k: (200, json.dumps(good).encode(), {}))
    assert rt.check_for_update("https://s", "t", None, "source", "2.1.0")["version"] == "2.2.0"
    assert rt.check_for_update("https://s", "t", None, "source", "2.2.0") is None
    assert rt.check_for_update("https://s", "t", None, "source", "3.0.0") is None     # không hạ phiên bản


def test_enroll_errors_are_classified(rt, monkeypatch):
    monkeypatch.setattr(rt, "_request", lambda *a, **k: (403, b'{"detail": "da dung"}', {}))
    with pytest.raises(rt.EnrollRejected):
        rt.enroll("https://s", "VNM-x", None, "h", "p", "source", "2.1.0")
    monkeypatch.setattr(rt, "_request", lambda *a, **k: (502, b"", {}))
    with pytest.raises(OSError):
        rt.enroll("https://s", "VNM-x", None, "h", "p", "source", "2.1.0")
    monkeypatch.setattr(rt, "_request", lambda *a, **k: (200, b'{"client_id": "pc", "device_token": "tk"}', {}))
    assert rt.enroll("https://s", "VNM-x", None, "h", "p", "source", "2.1.0") == {"client_id": "pc", "device_token": "tk"}


def test_credentials_and_code_cleanup(rt, tmp_path):
    assert rt.load_credentials(tmp_path) is None
    rt.save_credentials(tmp_path, "pc-1", "secret-token")
    assert rt.load_credentials(tmp_path)["client_id"] == "pc-1"
    cfg = tmp_path / "config.json"
    cfg.write_text(json.dumps({"server_url": "https://s", "enroll_code": "VNM-x", "enrollment_token": "old"}))
    rt.forget_enroll_code(cfg)
    assert json.loads(cfg.read_text()) == {"server_url": "https://s"}


def test_seed_skills_keeps_remote_installed_ones(rt, tmp_path):
    bundled, target = tmp_path / "b", tmp_path / "t"
    bundled.mkdir()
    (bundled / "base.py").write_text("v1")
    rt.seed_skills(bundled, target, "2.1.0")
    (target / "remote_skill.py").write_text("R")
    (bundled / "base.py").write_text("v2")
    rt.seed_skills(bundled, target, "2.1.0")
    assert (target / "base.py").read_text() == "v1"                  # cùng phiên bản: không chép lại
    rt.seed_skills(bundled, target, "2.2.0")
    assert (target / "base.py").read_text() == "v2" and (target / "remote_skill.py").read_text() == "R"


def test_dev_checkout_never_self_updates(rt, tmp_path, monkeypatch):
    monkeypatch.delenv("VNMATE_AGENT_NO_UPDATE", raising=False)
    assert rt.updates_allowed(ROOT / "client_agent") is False        # chạy từ mã nguồn dự án (có .git)
    assert rt.updates_allowed(tmp_path / "agent") is True


def test_agent_applies_update_and_reports(monkeypatch, tmp_path):
    saved_modules, saved_path = dict(sys.modules), list(sys.path)
    sys.path.insert(0, str(ROOT / "client_agent"))
    try:
        import agent
        rt = agent._runtime()
        calls = []
        monkeypatch.setattr(rt, "updates_allowed", lambda root: True)
        monkeypatch.setattr(rt, "check_for_update", lambda *a: {"version": "9.0.0", "url": "/u", "size": 1, "sha256": "x"})
        monkeypatch.setattr(rt, "download_verified", lambda *a: b"zip")
        monkeypatch.setattr(rt, "apply_source_update", lambda data, root: calls.append(("apply", data)) or 3)
        monkeypatch.setattr(rt, "restart_source", lambda: calls.append(("restart",)))
        a = agent.ClientAgent.__new__(agent.ClientAgent)
        a.client_id, a.http_base, a.device_token, a._updating = "pc", "https://s", "tk", False

        class WS:
            sent = []
            async def send(self, t):
                WS.sent.append(json.loads(t))

        assert asyncio.run(a.try_update(WS())) == "applied"
        assert calls == [("apply", b"zip"), ("restart",)]
        assert WS.sent[0]["status"] == "installing" and WS.sent[0]["to"] == "9.0.0"
        a.device_token = ""
        assert asyncio.run(a.try_update(WS())) == "disabled"            # chưa đăng ký -> không tự cập nhật
    finally:
        sys.modules.clear()
        sys.modules.update(saved_modules)
        sys.path[:] = saved_path


async def test_server_offers_update_only_to_older_agents(monkeypatch):
    from mateai.interfaces.websocket.client_orchestrator import Orchestrator
    from mateai.interfaces.http import agent_packages

    class WS:
        def __init__(self):
            self.sent = []
        async def send_text(self, t):
            self.sent.append(json.loads(t))

    monkeypatch.setattr(agent_packages, "latest_version", lambda pkg: "2.2.0" if pkg == "windows-exe" else None)
    o = Orchestrator()
    old, new, src = WS(), WS(), WS()
    await o.register_client("a", old, {"agent_version": "2.1.0", "package": "windows-exe"})
    await o.register_client("b", new, {"agent_version": "2.2.0", "package": "windows-exe"})
    await o.register_client("c", src, {"agent_version": "1.0.0", "package": "source"})
    assert [await o.offer_update(c) for c in "abc"] == [True, False, False]
    assert old.sent == [{"action": "update_available", "version": "2.2.0", "package": "windows-exe"}]


def test_windows_autostart_and_uninstall_entry(rt, monkeypatch, tmp_path):
    """HKCU Run + mục gỡ trong Settings -> Apps (không cần admin); gỡ thì xoá hết. winreg giả lập."""
    store = {}

    class Key:
        def __init__(self, path):
            self.path = path
        def __enter__(self):
            store.setdefault(self.path, {})
            return self
        def __exit__(self, *a):
            return False

    fake = type(sys)("winreg")
    fake.HKEY_CURRENT_USER, fake.REG_SZ, fake.REG_DWORD, fake.KEY_SET_VALUE = "HKCU", 1, 4, 2
    fake.CreateKey = lambda root, path: Key(path)
    fake.OpenKey = lambda root, path, res=0, access=0: Key(path)
    fake.SetValueEx = lambda k, name, res, typ, val: store[k.path].__setitem__(name, val)
    fake.DeleteValue = lambda k, name: store[k.path].pop(name)
    fake.DeleteKey = lambda root, path: store.pop(path)
    monkeypatch.setitem(sys.modules, "winreg", fake)
    monkeypatch.setattr(rt.os, "name", "nt")
    monkeypatch.delenv("VNMATE_AGENT_NO_AUTOSTART", raising=False)
    exe = tmp_path / "VNMateAgent.exe"
    rt.register_autostart(exe, "2.2.0")
    assert store[rt._RUN_KEY][rt.APP_NAME] == f'"{exe}"'
    entry = store[rt._UNINSTALL_KEY]
    assert entry["DisplayVersion"] == "2.2.0" and entry["UninstallString"] == f'"{exe}" --uninstall'
    rt.unregister_autostart()
    assert rt.APP_NAME not in store[rt._RUN_KEY] and rt._UNINSTALL_KEY not in store


def test_staged_update_keeps_exe_extension(rt):
    assert rt._staged_path(Path("C:/A/VNMateAgent.exe")).name == "VNMateAgent.new.exe"
    assert rt._staged_path(Path("/Apps/VNMateAgent")).name == "VNMateAgent.new"
