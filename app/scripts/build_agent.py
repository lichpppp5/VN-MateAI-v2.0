# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 Dương Thanh Lịch — VN-MateAI. See LICENSE and NOTICE.
"""
scripts/build_agent.py
======================
Build Agent máy trạm thành MỘT file chạy, không cần Python trên máy trạm:

  Windows : dist/agent/windows/VNMateAgent.exe   (chạy script này trên Windows)
  macOS   : dist/agent/macos/VNMateAgent         (chạy script này trên máy Mac)

PyInstaller không build chéo nền tảng — bản macOS phải build trên Mac.

    python scripts/build_agent.py              # tạo venv build riêng, cài thư viện, build
    python scripts/build_agent.py --reuse-venv # bỏ qua bước cài lại thư viện

Sau khi build, máy chủ phát gói mới ngay (Portal -> Tải Agent -> Windows / macOS)
và Agent đang chạy tự cập nhật lên bản này (dist/agent/<nền tảng>/version.txt).

Ký số (tuỳ chọn, khuyên dùng — không có chữ ký thì Windows SmartScreen / macOS
Gatekeeper cảnh báo). Khoá KHÔNG để trong code, chỉ đọc biến môi trường:
  Windows: SIGNTOOL (đường dẫn signtool.exe) + WIN_SIGN_CERT_SHA1 (thumbprint chứng
           chỉ code-signing trong kho Windows) [+ WIN_SIGN_TIMESTAMP_URL]
  macOS  : MAC_SIGN_IDENTITY ("Developer ID Application: ...") — sau đó notarize
           bằng `xcrun notarytool submit` (xem docs/integrations/agent-distribution.md).
"""
from __future__ import annotations

import argparse
import hashlib
import os
import re
import subprocess
import sys
import venv
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
AGENT = ROOT / "client_agent"
PLATFORM = "windows" if os.name == "nt" else ("macos" if sys.platform == "darwin" else "linux")
BUILD = ROOT / "build" / "agent"
DIST = ROOT / "dist" / "agent" / PLATFORM
VENV = BUILD / f"venv-{PLATFORM}"

HIDDEN = [
    "agent_runtime", "core", "core.plugin_manager",
    "psutil", "mss", "mss.tools", "PIL", "PIL.Image", "pyperclip", "httpx", "websockets",
    "tkinter", "tkinter.scrolledtext", "networkx", "matplotlib", "matplotlib.pyplot",
    "matplotlib.backends.backend_tkagg", "customtkinter",
]
HIDDEN_WIN = ["pywinauto", "win32api", "win32con", "win32com.client", "pythoncom", "wmi", "winreg"]


def shared_skill_files() -> list:
    """Như `agent_packages.shared_skill_files` (script build không nạp được mateai)."""
    listing = AGENT / "shared_skills.txt"
    names = [ln.strip() for ln in listing.read_text(encoding="utf-8").splitlines()] if listing.exists() else []
    return [ROOT / "skills" / n for n in names
            if n and not n.startswith("#") and n == Path(n).name and (ROOT / "skills" / n).is_file()]


def run(cmd, **kw):
    print("+", " ".join(str(c) for c in cmd))
    subprocess.run([str(c) for c in cmd], check=True, **kw)


def venv_python() -> Path:
    return VENV / ("Scripts/python.exe" if os.name == "nt" else "bin/python")


def agent_version() -> str:
    m = re.search(r'^AGENT_VERSION\s*=\s*"([^"]+)"', (AGENT / "agent.py").read_text(encoding="utf-8"), re.M)
    if not m:
        sys.exit("Không đọc được AGENT_VERSION trong client_agent/agent.py")
    return m.group(1)


def main() -> None:
    # Console Windows mặc định cp1252: in tiếng Việt sẽ làm script dừng giữa chừng.
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")
    ap = argparse.ArgumentParser()
    ap.add_argument("--reuse-venv", action="store_true", help="không cài lại thư viện build")
    args = ap.parse_args()
    version = agent_version()
    print(f"== Build VN-MateAI Agent {version} cho {PLATFORM}")

    if not venv_python().exists():
        venv.EnvBuilder(with_pip=True, clear=False).create(VENV)
    py = venv_python()
    if not args.reuse_venv:
        run([py, "-m", "pip", "install", "--disable-pip-version-check", "-q", "--upgrade", "pip"])
        run([py, "-m", "pip", "install", "--disable-pip-version-check", "-q", "-r", AGENT / "requirements.txt",
             "pyinstaller>=6.6"])

    sep = os.pathsep
    hidden = HIDDEN + (HIDDEN_WIN if os.name == "nt" else [])
    cmd = [py, "-m", "PyInstaller", "--noconfirm", "--clean", "--onefile", "--windowed",
           "--name", "VNMateAgent", "--distpath", DIST, "--workpath", BUILD / "work", "--specpath", BUILD,
           "--paths", AGENT,
           "--add-data", f"{AGENT / 'popup_ui.py'}{sep}.",
           "--add-data", f"{AGENT / 'overlay_ui.py'}{sep}.",
           "--add-data", f"{AGENT / 'skills'}{sep}skills",
           "--collect-all", "customtkinter"]
    for f in shared_skill_files():                          # kỹ năng máy chủ chạy nguyên bản trên máy trạm
        cmd += ["--add-data", f"{f}{sep}skills"]
    for h in hidden:
        cmd += ["--hidden-import", h]
    cmd.append(AGENT / "agent.py")
    run(cmd, cwd=ROOT)

    exe = DIST / ("VNMateAgent.exe" if os.name == "nt" else "VNMateAgent")
    if os.name == "nt" and os.environ.get("SIGNTOOL") and os.environ.get("WIN_SIGN_CERT_SHA1"):
        run([os.environ["SIGNTOOL"], "sign", "/sha1", os.environ["WIN_SIGN_CERT_SHA1"], "/fd", "sha256",
             "/tr", os.environ.get("WIN_SIGN_TIMESTAMP_URL", "http://timestamp.digicert.com"), "/td", "sha256", exe])
    elif sys.platform == "darwin" and os.environ.get("MAC_SIGN_IDENTITY"):
        run(["codesign", "--force", "--options", "runtime", "--timestamp",
             "--sign", os.environ["MAC_SIGN_IDENTITY"], exe])
    else:
        print("!! Chưa ký số (xem đầu file) — SmartScreen / Gatekeeper sẽ cảnh báo khi chạy lần đầu.")

    (DIST / "version.txt").write_text(version, encoding="utf-8")
    data = exe.read_bytes()
    print(f"== Xong: {exe}  ({len(data) / 1e6:.1f} MB)  sha256={hashlib.sha256(data).hexdigest()}")


if __name__ == "__main__":
    main()
