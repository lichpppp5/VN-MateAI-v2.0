# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 Dương Thanh Lịch — VN-MateAI. See LICENSE and NOTICE.
"""
mateai/interfaces/http/agent_packages.py
========================================
Đóng gói Agent máy trạm — MỘT nơi cho cả "Tải Agent" lẫn "tự cập nhật".

Ba dạng gói (`package`):
  - "source"      : mã Python client_agent/ (Windows + macOS + Linux; cần Python 3.10+).
                    Kèm install_agent.bat/.ps1 (Windows) và install_agent_macos.sh.
  - "windows-exe" : dist/agent/windows/VNMateAgent.exe — một file, không cần Python.
                    Bấm đúp là tự cài (xem client_agent/agent_runtime.py).
  - "macos-bin"   : dist/agent/macos/VNMateAgent — build trên máy Mac
                    (scripts/build_agent.py); chưa build thì tải gói "source".

Bản build nằm trong dist/agent/<nền tảng>/ cùng file version.txt (phiên bản lúc
build). Gói cập nhật được kiểm bằng SHA-256 công bố qua manifest.
"""
from __future__ import annotations

import hashlib
import io
import json
import zipfile
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

from mateai.config.loader import settings

PROJECT_ROOT = Path(settings.PROJECT_ROOT)
SOURCE_DIR = PROJECT_ROOT / "client_agent"
SERVER_SKILLS_DIR = PROJECT_ROOT / "skills"
DIST_DIR = PROJECT_ROOT / "dist" / "agent"
FROZEN = {
    "windows-exe": DIST_DIR / "windows" / "VNMateAgent.exe",
    "macos-bin": DIST_DIR / "macos" / "VNMateAgent",
}
PACKAGES = ("source", "windows-exe", "macos-bin")

#: Không bao giờ đóng vào gói: cấu hình / khoá / log / venv của bản đang chạy trên máy chủ.
_SKIP_TOP = {"logs", ".venv", "venv", "config.json", "server_cert.pem", "device.json"}

_cache: Dict[str, Tuple[float, bytes]] = {}


def shared_skill_files():
    """Kỹ năng máy chủ chạy nguyên bản trên máy trạm (client_agent/shared_skills.txt) — tệp nào có thật."""
    listing = SOURCE_DIR / "shared_skills.txt"
    if not listing.exists():
        return []
    names = [ln.strip() for ln in listing.read_text(encoding="utf-8").splitlines()]
    return [SERVER_SKILLS_DIR / n for n in names
            if n and not n.startswith("#") and n == Path(n).name and (SERVER_SKILLS_DIR / n).is_file()]


def _source_files():
    seen = set()
    for f in sorted(SOURCE_DIR.rglob("*")):
        if not f.is_file():
            continue
        rel = f.relative_to(SOURCE_DIR)
        if rel.parts[0] in _SKIP_TOP or "__pycache__" in rel.parts or f.suffix in (".pyc", ".pyo", ".log"):
            continue
        seen.add(rel.as_posix())
        yield f, rel
    for f in shared_skill_files():
        rel = Path("skills") / f.name
        if rel.as_posix() not in seen:                      # bản riêng của máy trạm (nếu có) được ưu tiên
            yield f, rel


def source_version() -> Optional[str]:
    from mateai.interfaces.websocket.client_orchestrator import bundled_agent_version
    return bundled_agent_version()


def latest_version(package: str) -> Optional[str]:
    if package == "source":
        return source_version()
    exe = FROZEN.get(package)
    vf = exe.parent / "version.txt" if exe else None
    if not exe or not exe.exists() or not vf.exists():
        return None
    return vf.read_text(encoding="utf-8").strip() or None


def available() -> Dict[str, Dict[str, Any]]:
    out = {}
    for p in PACKAGES:
        v = latest_version(p)
        size = None
        if p != "source" and FROZEN[p].exists():
            size = FROZEN[p].stat().st_size
        out[p] = {"available": v is not None, "version": v, "size": size}
    return out


def _source_zip(extra: Optional[Dict[str, bytes]] = None) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for f, rel in _source_files():
            zf.write(f, arcname=rel.as_posix())
        for name, data in (extra or {}).items():
            zf.writestr(name, data)
    return buf.getvalue()


def package_bytes(package: str) -> Optional[bytes]:
    """Gói cập nhật (không kèm cấu hình / khoá). Cache theo thời điểm sửa file."""
    if package == "source":
        mtime = max((f.stat().st_mtime for f, _ in _source_files()), default=0)
        hit = _cache.get(package)
        if hit and hit[0] == mtime:
            return hit[1]
        data = _source_zip()
    else:
        exe = FROZEN.get(package)
        if not exe or not exe.exists():
            return None
        mtime = exe.stat().st_mtime
        hit = _cache.get(package)
        if hit and hit[0] == mtime:
            return hit[1]
        data = exe.read_bytes()
    _cache[package] = (mtime, data)
    return data


def manifest(package: str) -> Optional[Dict[str, Any]]:
    version = latest_version(package)
    data = package_bytes(package) if version else None
    if not data:
        return None
    return {"package": package, "version": version, "size": len(data),
            "sha256": hashlib.sha256(data).hexdigest(),
            "url": f"/api/v1/agent/update/package?package={package}"}


_GUIDE = {
    "windows": (
        "VN-MateAI Agent cho Windows\r\n"
        "===========================\r\n\r\n"
        "1. Giải nén cả thư mục (VNMateAgent.exe + config.json + server_cert.pem phải nằm cùng chỗ).\r\n"
        "2. Bấm đúp VNMateAgent.exe. Agent tự cài vào %LOCALAPPDATA%\\VNMateAI\\Agent,\r\n"
        "   tự chạy mỗi lần đăng nhập Windows và tự cập nhật khi máy chủ có bản mới.\r\n"
        "3. Gỡ: Settings -> Apps -> 'VN-MateAI Agent' -> Uninstall.\r\n\r\n"
        "Mã đăng ký trong config.json chỉ dùng được MỘT lần, cho MỘT máy, trong 7 ngày.\r\n"
        "Máy khác cần tải gói mới từ Portal.\r\n"),
    "macos": (
        "VN-MateAI Agent cho macOS\n"
        "=========================\n\n"
        "1. Giải nén cả thư mục (VNMateAgent + config.json + server_cert.pem cùng chỗ).\n"
        "2. Terminal: cd vào thư mục, chạy:  chmod +x VNMateAgent && ./VNMateAgent\n"
        "   Agent tự cài vào ~/Library/Application Support/VNMateAI/Agent và chạy nền\n"
        "   (LaunchAgent) mỗi lần đăng nhập, tự cập nhật khi máy chủ có bản mới.\n"
        "3. Lần đầu: System Settings -> Privacy & Security -> cho phép VNMateAgent\n"
        "   (chưa ký Apple Developer ID), và cấp quyền Screen Recording nếu cần chụp màn hình.\n"
        "4. Gỡ: ./VNMateAgent --uninstall\n\n"
        "Mã đăng ký trong config.json chỉ dùng được MỘT lần, cho MỘT máy, trong 7 ngày.\n"),
}


def build_download(platform: str, config: Dict[str, Any], cert: Optional[bytes]) -> Tuple[str, bytes, str]:
    """Gói tải về cho người dùng: (tên file, nội dung zip, package thực tế)."""
    extra = {"config.json": json.dumps(config, indent=4, ensure_ascii=False).encode("utf-8")}
    if cert:
        extra["server_cert.pem"] = cert
    if platform == "windows" and FROZEN["windows-exe"].exists():
        extra["VNMateAgent.exe"] = package_bytes("windows-exe")
        extra["HUONG_DAN_CAI_DAT.txt"] = _GUIDE["windows"].encode("utf-8")
        return "VN-Mate_Agent_Windows.zip", _zip_only(extra), "windows-exe"
    if platform == "macos" and FROZEN["macos-bin"].exists():
        extra["VNMateAgent"] = package_bytes("macos-bin")
        extra["HUONG_DAN_CAI_DAT.txt"] = _GUIDE["macos"].encode("utf-8")
        return "VN-Mate_Agent_macOS.zip", _zip_only(extra, exec_names={"VNMateAgent"}), "macos-bin"
    return "VN-Mate_Agent.zip", _source_zip(extra), "source"


def _zip_only(files: Dict[str, bytes], exec_names=frozenset()) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for name, data in files.items():
            info = zipfile.ZipInfo(name)
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = (0o755 if name in exec_names else 0o644) << 16
            zf.writestr(info, data)
    return buf.getvalue()
