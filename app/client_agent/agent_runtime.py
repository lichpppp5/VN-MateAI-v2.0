# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 Dương Thanh Lịch — VN-MateAI. See LICENSE and NOTICE.
"""
client_agent/agent_runtime.py
=============================
Phần "vòng đời" của Agent — dùng chung cho bản Python (source), Windows .exe và
macOS (bản build PyInstaller):

  - Tự cài (bản build): bấm đúp VNMateAgent.exe / chạy ./VNMateAgent lần đầu ->
    chép vào thư mục cài, đăng ký tự chạy khi đăng nhập (Windows: HKCU Run +
    mục gỡ trong Settings -> Apps; macOS: LaunchAgent), khởi động, thoát.
    Không cần quyền admin.
  - Đăng ký bằng MÃ DÙNG MỘT LẦN -> khoá thiết bị riêng (device.json).
  - Tự cập nhật: hỏi máy chủ phiên bản + SHA-256, tải qua kết nối ghim chứng
    chỉ, kiểm SHA-256, thay thế rồi khởi động lại. Không bao giờ hạ phiên bản.
  - Gỡ cài đặt.

Không dùng shell: mọi tiến trình con chạy bằng danh sách tham số.
"""
from __future__ import annotations

import hashlib
import io
import json
import logging
import os
import re
import shutil
import ssl
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
import zipfile
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

logger = logging.getLogger("client_agent.runtime")

APP_NAME = "VN-MateAI Agent"
EXE_NAME = "VNMateAgent.exe" if os.name == "nt" else "VNMateAgent"
MAC_LABEL = "vn.mateai.agent"
_RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
_UNINSTALL_KEY = r"Software\Microsoft\Windows\CurrentVersion\Uninstall\VNMateAIAgent"
#: Bản cập nhật source không được đè các file này.
_PROTECTED = {"config.json", "device.json", "server_cert.pem", "logs", ".venv", "venv"}


# ── Nhận diện ───────────────────────────────────────────────────────────────

def is_frozen() -> bool:
    return bool(getattr(sys, "frozen", False))


def package_kind() -> str:
    if not is_frozen():
        return "source"
    return "windows-exe" if os.name == "nt" else ("macos-bin" if sys.platform == "darwin" else "linux-bin")


def bundle_root(agent_root: Path) -> Path:
    """Nơi chứa file đi kèm bản build (popup_ui.py, skills gốc…)."""
    return Path(getattr(sys, "_MEIPASS", agent_root))


def install_dir() -> Path:
    """Thư mục cài. `VNMATE_AGENT_HOME` đổi được (triển khai hàng loạt / thử nghiệm)."""
    if os.environ.get("VNMATE_AGENT_HOME"):
        return Path(os.environ["VNMATE_AGENT_HOME"])
    if os.name == "nt":
        return Path(os.environ.get("LOCALAPPDATA") or Path.home() / "AppData" / "Local") / "VNMateAI" / "Agent"
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / "VNMateAI" / "Agent"
    return Path.home() / ".local" / "share" / "vnmateai-agent"


def needs_install() -> bool:
    if not is_frozen():
        return False
    try:
        return Path(sys.executable).resolve().parent != install_dir().resolve()
    except OSError:
        return True


def version_tuple(v: Optional[str]) -> Tuple[int, ...]:
    return tuple(int(x) for x in re.findall(r"\d+", str(v or ""))[:3])


# ── Thông báo cho người dùng (bản build không có console) ───────────────────

def quiet() -> bool:
    """Cài im lặng (GPO / Intune / script): `--quiet` hoặc VNMATE_AGENT_QUIET=1 — không hiện hộp thoại."""
    return "--quiet" in sys.argv or os.environ.get("VNMATE_AGENT_QUIET") == "1"


def notify_user(title: str, message: str, error: bool = False) -> None:
    logger.info("%s: %s", title, message)
    if quiet():
        return
    if os.name == "nt" and is_frozen():
        try:
            import ctypes
            ctypes.windll.user32.MessageBoxW(None, message, title, 0x10 if error else 0x40)
            return
        except Exception:  # noqa: BLE001
            pass
    print(f"[{title}] {message}")


# ── Tiến trình ──────────────────────────────────────────────────────────────

def _detached(cmd, cwd: Optional[Path] = None) -> None:
    kw: Dict[str, Any] = {"cwd": str(cwd) if cwd else None, "stdin": subprocess.DEVNULL,
                          "stdout": subprocess.DEVNULL, "stderr": subprocess.DEVNULL, "close_fds": True}
    if os.name == "nt":
        kw["creationflags"] = 0x00000008 | 0x00000200  # DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP
    else:
        kw["start_new_session"] = True
    subprocess.Popen([str(c) for c in cmd], **kw)


def _stop_running(exe: Path) -> None:
    """Dừng các Agent đang chạy từ đúng file `exe` (trước khi chép đè / gỡ)."""
    try:
        import psutil
    except ImportError:
        return
    me = os.getpid()
    target = str(exe.resolve()).lower()
    victims = []
    for p in psutil.process_iter(["pid", "exe"]):
        try:
            if p.info["pid"] != me and p.info["exe"] and str(Path(p.info["exe"]).resolve()).lower() == target:
                p.terminate()
                victims.append(p)
        except (psutil.Error, OSError):
            continue
    if victims:
        psutil.wait_procs(victims, timeout=8)


def _wait_pid_exit(pid: int, timeout: float = 60.0) -> None:
    try:
        import psutil
    except ImportError:
        time.sleep(5)
        return
    deadline = time.time() + timeout
    while time.time() < deadline and psutil.pid_exists(pid):
        time.sleep(0.5)


# ── Tự chạy khi đăng nhập ───────────────────────────────────────────────────

def _mac_plist(exe: Path) -> Path:
    plist = Path.home() / "Library" / "LaunchAgents" / f"{MAC_LABEL}.plist"
    logs = exe.parent / "logs"
    logs.mkdir(parents=True, exist_ok=True)
    plist.parent.mkdir(parents=True, exist_ok=True)
    plist.write_text(f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>Label</key><string>{MAC_LABEL}</string>
  <key>ProgramArguments</key><array><string>{exe}</string></array>
  <key>WorkingDirectory</key><string>{exe.parent}</string>
  <key>RunAtLoad</key><true/>
  <key>KeepAlive</key><true/>
  <key>ThrottleInterval</key><integer>15</integer>
  <key>StandardErrorPath</key><string>{logs / 'launchd.err.log'}</string>
</dict></plist>
""", encoding="utf-8")
    return plist


def autostart_enabled() -> bool:
    return os.environ.get("VNMATE_AGENT_NO_AUTOSTART") != "1"


def register_autostart(exe: Path, version: str) -> None:
    if not autostart_enabled():
        return
    if os.name == "nt":
        import winreg
        with winreg.CreateKey(winreg.HKEY_CURRENT_USER, _RUN_KEY) as k:
            winreg.SetValueEx(k, APP_NAME, 0, winreg.REG_SZ, f'"{exe}"')
        with winreg.CreateKey(winreg.HKEY_CURRENT_USER, _UNINSTALL_KEY) as k:
            for name, val in (("DisplayName", APP_NAME), ("DisplayVersion", version),
                              ("Publisher", "VN-MateAI"), ("InstallLocation", str(exe.parent)),
                              ("DisplayIcon", str(exe)), ("UninstallString", f'"{exe}" --uninstall')):
                winreg.SetValueEx(k, name, 0, winreg.REG_SZ, val)
            winreg.SetValueEx(k, "NoModify", 0, winreg.REG_DWORD, 1)
            winreg.SetValueEx(k, "NoRepair", 0, winreg.REG_DWORD, 1)
    elif sys.platform == "darwin":
        plist = _mac_plist(exe)
        subprocess.run(["launchctl", "unload", str(plist)], capture_output=True, timeout=15)
        subprocess.run(["launchctl", "load", "-w", str(plist)], capture_output=True, timeout=15)


def unregister_autostart() -> None:
    if os.name == "nt":
        import winreg
        for key, value in ((_RUN_KEY, APP_NAME), (_UNINSTALL_KEY, None)):
            try:
                if value:
                    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, key, 0, winreg.KEY_SET_VALUE) as k:
                        winreg.DeleteValue(k, value)
                else:
                    winreg.DeleteKey(winreg.HKEY_CURRENT_USER, key)
            except OSError:
                pass
    elif sys.platform == "darwin":
        plist = Path.home() / "Library" / "LaunchAgents" / f"{MAC_LABEL}.plist"
        if plist.exists():
            subprocess.run(["launchctl", "unload", "-w", str(plist)], capture_output=True, timeout=15)
            plist.unlink(missing_ok=True)


# ── Cài / gỡ (bản build) ────────────────────────────────────────────────────

def install(version: str) -> Path:
    """Chép bản build + config + chứng chỉ vào thư mục cài, đăng ký tự chạy, khởi động."""
    src_dir = Path(sys.executable).resolve().parent
    dest = install_dir()
    dest.mkdir(parents=True, exist_ok=True)
    exe = dest / EXE_NAME
    _stop_running(exe)
    shutil.copy2(sys.executable, exe)
    if os.name != "nt":
        exe.chmod(0o755)
    for name in ("config.json", "server_cert.pem"):
        if (src_dir / name).exists():
            # Máy đã đăng ký (có device.json) giữ nguyên khoá; config mới chỉ cập nhật địa chỉ.
            shutil.copy2(src_dir / name, dest / name)
    register_autostart(exe, version)
    if os.name == "nt" or not autostart_enabled():
        _detached([exe], cwd=dest)       # macOS có LaunchAgent: launchctl load đã khởi động
    return dest


def uninstall() -> None:
    """Gỡ: bỏ tự chạy, dừng Agent, xoá thư mục cài (bằng bản sao tạm vì không xoá được chính mình)."""
    unregister_autostart()
    dest = install_dir()
    exe = dest / EXE_NAME
    _stop_running(exe)
    if is_frozen() and Path(sys.executable).resolve().parent == dest.resolve():
        tmp = Path(tempfile.gettempdir()) / f"vnmate_uninstall_{os.getpid()}{'.exe' if os.name == 'nt' else ''}"
        shutil.copy2(sys.executable, tmp)
        _detached([tmp, "--uninstall-cleanup", dest, os.getpid()])
    else:
        shutil.rmtree(dest, ignore_errors=True)


def uninstall_cleanup(target: Path, pid: int) -> None:
    _wait_pid_exit(pid)
    for _ in range(10):
        shutil.rmtree(target, ignore_errors=True)
        if not target.exists():
            return
        time.sleep(1)


# ── Đăng ký bằng mã dùng một lần ────────────────────────────────────────────

def load_credentials(data_root: Path) -> Optional[Dict[str, str]]:
    f = data_root / "device.json"
    try:
        d = json.loads(f.read_text(encoding="utf-8"))
        return d if d.get("device_token") and d.get("client_id") else None
    except (OSError, ValueError):
        return None


def save_credentials(data_root: Path, client_id: str, token: str) -> None:
    f = data_root / "device.json"
    f.write_text(json.dumps({"client_id": client_id, "device_token": token,
                             "enrolled_at": time.strftime("%Y-%m-%dT%H:%M:%S")}, indent=2), encoding="utf-8")
    try:
        os.chmod(f, 0o600)
    except OSError:
        pass


class EnrollRejected(Exception):
    """Máy chủ từ chối mã (sai / hết hạn / đã dùng) — thử lại vô ích."""


def _request(url: str, ctx: Optional[ssl.SSLContext], token: str = "", data: Optional[bytes] = None,
             timeout: float = 30.0) -> Tuple[int, bytes, Dict[str, str]]:
    req = urllib.request.Request(url, data=data, method="POST" if data is not None else "GET")
    if data is not None:
        req.add_header("Content-Type", "application/json")
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    try:
        with urllib.request.urlopen(req, timeout=timeout, context=ctx if url.startswith("https") else None) as r:
            return r.status, r.read(), dict(r.headers)
    except urllib.error.HTTPError as e:
        return e.code, e.read(), dict(e.headers or {})


def enroll(server_url: str, code: str, ctx: Optional[ssl.SSLContext], hostname: str, platform: str,
           package: str, version: str) -> Dict[str, str]:
    body = json.dumps({"code": code, "hostname": hostname, "platform": platform,
                       "package": package, "agent_version": version}).encode("utf-8")
    status, data, _ = _request(server_url.rstrip("/") + "/api/v1/agent/enroll", ctx, data=body)
    if status == 200:
        res = json.loads(data)
        return {"client_id": res["client_id"], "device_token": res["device_token"]}
    detail = ""
    try:
        detail = json.loads(data).get("detail", "")
    except ValueError:
        pass
    if status in (400, 403, 422):
        raise EnrollRejected(detail or f"HTTP {status}")
    raise OSError(f"Đăng ký thất bại HTTP {status}: {detail}")


def forget_enroll_code(config_path: Path) -> None:
    """Mã đã dùng thì xoá khỏi config.json (dùng lại cũng bị từ chối)."""
    try:
        cfg = json.loads(config_path.read_text(encoding="utf-8"))
        cfg.pop("enroll_code", None)
        cfg.pop("enrollment_token", None)
        config_path.write_text(json.dumps(cfg, indent=4, ensure_ascii=False), encoding="utf-8")
    except (OSError, ValueError):
        pass


# ── Tự cập nhật ─────────────────────────────────────────────────────────────

def updates_allowed(agent_root: Path) -> bool:
    """Không tự cập nhật bản đang chạy từ mã nguồn dự án (worker cục bộ / máy dev)."""
    if os.environ.get("VNMATE_AGENT_NO_UPDATE") == "1":
        return False
    return not (agent_root.parent / ".git").exists()


def check_for_update(server_url: str, token: str, ctx: Optional[ssl.SSLContext], package: str,
                     current: str) -> Optional[Dict[str, Any]]:
    status, data, _ = _request(f"{server_url.rstrip('/')}/api/v1/agent/update/manifest?package={package}",
                               ctx, token=token)
    if status != 200:
        return None
    m = json.loads(data)
    if version_tuple(m.get("version")) <= version_tuple(current):
        return None
    return m


def download_verified(server_url: str, token: str, ctx: Optional[ssl.SSLContext], manifest: Dict[str, Any]) -> bytes:
    status, data, _ = _request(server_url.rstrip("/") + manifest["url"], ctx, token=token, timeout=300)
    if status != 200:
        raise OSError(f"Tải gói cập nhật lỗi HTTP {status}")
    if len(data) != int(manifest["size"]) or hashlib.sha256(data).hexdigest() != manifest["sha256"]:
        raise ValueError("Gói cập nhật sai SHA-256 / kích thước — bỏ, không cài.")
    return data


def apply_source_update(zip_bytes: bytes, agent_root: Path) -> int:
    """Chép mã mới đè lên thư mục Agent (trừ cấu hình / khoá / log / venv). Trả số file."""
    n = 0
    with zipfile.ZipFile(io.BytesIO(zip_bytes)) as zf:
        for info in zf.infolist():
            rel = Path(info.filename)
            if info.is_dir() or rel.is_absolute() or ".." in rel.parts or rel.parts[0] in _PROTECTED:
                continue
            dest = agent_root / rel
            dest.parent.mkdir(parents=True, exist_ok=True)
            tmp = dest.with_suffix(dest.suffix + ".tmp")
            tmp.write_bytes(zf.read(info))
            os.replace(tmp, dest)
            n += 1
    return n


def _staged_path(current: Path) -> Path:
    """VNMateAgent.exe -> VNMateAgent.new.exe (giữ đuôi .exe để Windows chạy được)."""
    return current.with_name(current.stem + ".new" + current.suffix)


def apply_frozen_update(exe_bytes: bytes) -> None:
    """Ghi bản build mới cạnh bản đang chạy, giao cho nó thay thế sau khi tiến trình này thoát."""
    current = Path(sys.executable).resolve()
    new = _staged_path(current)
    new.write_bytes(exe_bytes)
    if os.name != "nt":
        new.chmod(0o755)
    _detached([new, "--finish-update", current, os.getpid()], cwd=current.parent)


def finish_update(target: Path, old_pid: int) -> None:
    """Chạy từ file .new: đợi bản cũ thoát, chép đè, khởi động bản mới (macOS: launchd tự chạy lại)."""
    _wait_pid_exit(old_pid)
    for _ in range(20):
        try:
            shutil.copy2(sys.executable, target)
            break
        except OSError:
            time.sleep(1)
    if os.name != "nt":
        target.chmod(0o755)
    if os.name == "nt" or sys.platform != "darwin" or not autostart_enabled():
        _detached([target], cwd=target.parent)


def cleanup_after_update() -> None:
    if is_frozen():
        leftover = _staged_path(Path(sys.executable).resolve())
        try:
            leftover.unlink(missing_ok=True)
        except OSError:
            pass


def restart_source() -> None:
    """Khởi động lại bản Python (sau cập nhật mã): tiến trình mới thay tiến trình này."""
    _detached([sys.executable] + sys.argv, cwd=Path(sys.argv[0]).resolve().parent)
    os._exit(0)


def restart_frozen_exit() -> None:
    os._exit(0)


# ── Kỹ năng đi kèm bản build ────────────────────────────────────────────────

def seed_skills(bundled: Path, target: Path, version: str) -> None:
    """Bản build: chép skills gốc ra thư mục ghi được (để cài skill từ xa vẫn giữ sau khởi động lại).
    Mỗi phiên bản mới chép đè skills gốc; skill do máy chủ cài thêm được giữ nguyên."""
    target.mkdir(parents=True, exist_ok=True)
    marker = target / ".bundle_version"
    if marker.exists() and marker.read_text(encoding="utf-8").strip() == version:
        return
    for f in bundled.glob("*.py"):
        shutil.copy2(f, target / f.name)
    marker.write_text(version, encoding="utf-8")
