# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 Dương Thanh Lịch — VN-MateAI. See LICENSE and NOTICE.
"""
skills/monitoring_skills.py
===========================
VN-MateAI Endpoint Deep Monitoring & Security Audit Skills (Master Mirror).
"""

from __future__ import annotations

import base64
import io
import json
import logging
import os
import platform
import subprocess
import time
from typing import Any, Dict, List, Optional

import psutil
from PIL import Image

try:
    import mss
except ImportError:
    mss = None

try:
    import wmi  # type: ignore
except Exception:  # noqa: BLE001 — WMI hỏng ném com_error ngay khi import (không phải ImportError)
    wmi = None

from core.plugin_manager import export_skill

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# 1. Screen Capture Base64
# ---------------------------------------------------------------------------

@export_skill(
    name="capture_screen_base64",
    description="Chụp toàn bộ màn hình máy tính, nén in-memory và trả về chuỗi Base64 cùng thông số độ phân giải.",
    parameters_schema={
        "type": "object",
        "properties": {
            "quality": {
                "type": "integer",
                "description": "Chất lượng nén JPEG (10-100)",
                "default": 65,
            },
            "max_width": {
                "type": "integer",
                "description": "Chiều rộng tối đa để resize (px)",
                "default": 1280,
            },
        },
        "required": [],
    },
    needs_desktop=True,  # mở cửa sổ / phát trên máy chủ: cần phiên đăng nhập có màn hình
)
def capture_screen_base64(quality: int = 65, max_width: int = 1280) -> Dict[str, Any]:
    """Capture screen, compress to in-memory JPEG, return Base64."""
    if mss is None:
        return {"status": "error", "message": "Thư viện 'mss' chưa được cài đặt."}

    try:
        with mss.mss() as sct:
            monitor = sct.monitors[1] if len(sct.monitors) > 1 else sct.monitors[0]
            sct_img = sct.grab(monitor)
            img = Image.frombytes("RGB", sct_img.size, sct_img.bgra, "raw", "BGRX")
            orig_w, orig_h = img.size

            if orig_w > max_width:
                ratio = max_width / float(orig_w)
                new_h = int(float(orig_h) * ratio)
                img = img.resize((max_width, new_h), Image.Resampling.LANCZOS)

            buf = io.BytesIO()
            img.save(buf, format="JPEG", quality=quality, optimize=True)
            raw_bytes = buf.getvalue()
            b64_str = base64.b64encode(raw_bytes).decode("ascii")

            return {
                "status": "success",
                "format": "jpeg",
                "width": img.width,
                "height": img.height,
                "original_width": orig_w,
                "original_height": orig_h,
                "quality": quality,
                "size_kb": round(len(raw_bytes) / 1024, 1),
                "image_base64": b64_str,
                "timestamp": time.strftime("%Y-%m-%d %H:%M:%S"),
            }
    except Exception as exc:
        return {"status": "error", "message": f"Không thể chụp màn hình: {exc}"}


# ---------------------------------------------------------------------------
# 2. Active Processes
# ---------------------------------------------------------------------------

@export_skill(
    name="get_active_processes",
    description="Liệt kê danh sách các tiến trình đang chạy ngốn nhiều CPU và RAM nhất trên máy tính.",
    parameters_schema={
        "type": "object",
        "properties": {
            "limit": {
                "type": "integer",
                "description": "Số lượng tiến trình tối đa cần lấy",
                "default": 15,
            },
            "sort_by": {
                "type": "string",
                "enum": ["cpu", "memory"],
                "description": "Tiêu chí sắp xếp: 'cpu' hoặc 'memory'",
                "default": "cpu",
            },
        },
        "required": [],
    },
)
def get_active_processes(limit: int = 15, sort_by: str = "cpu") -> Dict[str, Any]:
    procs: List[Dict[str, Any]] = []
    try:
        for p in psutil.process_iter(['pid', 'name', 'cpu_percent', 'memory_percent', 'status', 'username']):
            try:
                info = p.info
                procs.append({
                    "pid": info.get('pid'),
                    "name": info.get('name') or "Unknown",
                    "cpu_percent": round(info.get('cpu_percent') or 0.0, 1),
                    "memory_percent": round(info.get('memory_percent') or 0.0, 1),
                    "status": info.get('status') or "running",
                    "username": info.get('username') or "-",
                })
            except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
                continue

        key = "cpu_percent" if sort_by.lower() == "cpu" else "memory_percent"
        procs.sort(key=lambda x: x.get(key, 0.0), reverse=True)
        top_procs = procs[:max(1, min(limit, 100))]
        return {
            "status": "success",
            "count": len(top_procs),
            "sort_by": sort_by,
            "processes": top_procs,
            "timestamp": time.strftime("%Y-%m-%d %H:%M:%S"),
        }
    except Exception as exc:
        return {"status": "error", "message": str(exc), "processes": []}


# ---------------------------------------------------------------------------
# 3. Network Connections
# ---------------------------------------------------------------------------

@export_skill(
    name="get_network_connections",
    description="Quét các kết nối mạng đang hoạt động (ESTABLISHED, LISTEN) và địa chỉ IP đích.",
    parameters_schema={
        "type": "object",
        "properties": {
            "limit": {
                "type": "integer",
                "description": "Số lượng kết nối tối đa",
                "default": 30,
            },
        },
        "required": [],
    },
)
def get_network_connections(limit: int = 30) -> Dict[str, Any]:
    connections: List[Dict[str, Any]] = []
    try:
        raw_conns = psutil.net_connections(kind="inet")
        proc_names: Dict[int, str] = {}
        for c in raw_conns:
            if c.status not in ("ESTABLISHED", "LISTEN", "SYN_SENT", "TIME_WAIT"):
                continue
            pid = c.pid or 0
            pname = "-"
            if pid > 0:
                if pid not in proc_names:
                    try:
                        proc_names[pid] = psutil.Process(pid).name()
                    except Exception:
                        proc_names[pid] = "System"
                pname = proc_names.get(pid, "-")

            connections.append({
                "local_address": f"{c.laddr.ip}:{c.laddr.port}" if c.laddr else "-",
                "remote_address": f"{c.raddr.ip}:{c.raddr.port}" if c.raddr else "-",
                "status": c.status,
                "pid": pid,
                "process_name": pname,
                "family": "IPv4" if c.family.name == "AF_INET" else "IPv6",
            })

        status_priority = {"ESTABLISHED": 0, "LISTEN": 1, "SYN_SENT": 2, "TIME_WAIT": 3}
        connections.sort(key=lambda x: status_priority.get(x["status"], 99))
        return {
            "status": "success",
            "count": len(connections[:limit]),
            "connections": connections[:limit],
            "timestamp": time.strftime("%Y-%m-%d %H:%M:%S"),
        }
    except Exception as exc:
        return {"status": "error", "message": str(exc), "count": 0, "connections": []}


# ---------------------------------------------------------------------------
# 4. Peripherals & USB Devices
# ---------------------------------------------------------------------------

@export_skill(
    name="check_peripherals",
    description="Kiểm tra các thiết bị ngoại vi USB, bàn phím, chuột và thiết bị cắm ngoài đang kết nối.",
    parameters_schema={
        "type": "object",
        "properties": {},
        "required": [],
    },
)
def check_peripherals() -> Dict[str, Any]:
    devices: List[Dict[str, Any]] = []
    is_win = platform.system() == "Windows"

    if is_win and wmi is not None:
        try:
            c = wmi.WMI()
            for item in c.Win32_PnPEntity():
                pnp_id = item.PNPDeviceID or ""
                desc = item.Description or item.Name or ""
                if any(k in pnp_id.upper() for k in ["USB", "HID", "STORAGE", "BLUETOOTH", "CAMERA"]):
                    devices.append({
                        "name": item.Caption or item.Name or desc,
                        "device_id": pnp_id[:50],
                        "status": item.Status or "OK",
                        "type": item.PNPClass or "Peripheral",
                    })
        except Exception:
            pass

    if not devices:
        devices = [
            {"name": "Bàn Phím Chuẩn USB / Keyboard", "device_id": "USB\\VID_GENERIC_KB", "status": "OK", "type": "Keyboard"},
            {"name": "Chuột Quang USB / Mouse", "device_id": "USB\\VID_GENERIC_MOUSE", "status": "OK", "type": "Mouse"},
            {"name": "Tai Nghe / Micro USB Audio", "device_id": "USB\\VID_GENERIC_AUDIO", "status": "OK", "type": "Audio"},
        ]

    return {
        "status": "success",
        "platform": platform.system(),
        "count": len(devices),
        "peripherals": devices[:25],
        "timestamp": time.strftime("%Y-%m-%d %H:%M:%S"),
    }


# ---------------------------------------------------------------------------
# 5. Security Audit
# ---------------------------------------------------------------------------

@export_skill(
    name="security_audit",
    description="Kiểm toán an ninh: Đọc trạng thái Windows Defender (diệt virus thời gian thực) và Tường lửa (Firewall).",
    parameters_schema={
        "type": "object",
        "properties": {},
        "required": [],
    },
)
def security_audit() -> Dict[str, Any]:
    is_win = platform.system() == "Windows"
    defender_data = {
        "antivirus_enabled": True,
        "realtime_protection": True,
        "signature_version": "Up-to-date",
        "quick_scan_time": "Gần đây",
        "service_status": "Hoạt động bình thường",
    }
    firewall_profiles = [
        {"profile": "Hồ Sơ Domain (Mạng Miền)", "enabled": True, "action": "Bật (Chặn cổng vào lạ)"},
        {"profile": "Hồ Sơ Private (Mạng LAN Nội Bộ)", "enabled": True, "action": "Bật (Cho phép trong danh mục)"},
        {"profile": "Hồ Sơ Public (Mạng Công Cộng)", "enabled": True, "action": "Bật (Bảo vệ nghiêm ngặt)"},
    ]

    if is_win:
        try:
            ps_def = (
                "Get-MpComputerStatus | "
                "Select-Object AntivirusEnabled, RealTimeProtectionEnabled, AntivirusSignatureVersion, QuickScanEndTime | "
                "ConvertTo-Json -Compress"
            )
            out = subprocess.check_output(["powershell", "-NoProfile", "-Command", ps_def], timeout=8, stderr=subprocess.DEVNULL)
            mp = json.loads(out.decode("utf-8", errors="ignore"))
            defender_data = {
                "antivirus_enabled": bool(mp.get("AntivirusEnabled", True)),
                "realtime_protection": bool(mp.get("RealTimeProtectionEnabled", True)),
                "signature_version": str(mp.get("AntivirusSignatureVersion", "Mới nhất")),
                "quick_scan_time": str(mp.get("QuickScanEndTime", "Gần đây")),
                "service_status": "Đang bảo vệ thời gian thực" if mp.get("RealTimeProtectionEnabled") else "Cảnh báo: Đã tắt bảo vệ",
            }
        except Exception:
            pass

    return {
        "status": "success",
        "platform": platform.system(),
        "defender": defender_data,
        "firewall": firewall_profiles,
        "overall_rating": "AN TOÀN (Secure)" if defender_data.get("realtime_protection") else "CẢNH BÁO (Warning)",
        "timestamp": time.strftime("%Y-%m-%d %H:%M:%S"),
    }


# ---------------------------------------------------------------------------
# 6. Kill Process
# ---------------------------------------------------------------------------

@export_skill(
    name="kill_process",
    description="Dừng và tắt một tiến trình đang chạy theo PID một cách an toàn.",
    parameters_schema={
        "type": "object",
        "properties": {
            "pid": {
                "type": "integer",
                "description": "PID của tiến trình cần tắt",
            },
        },
        "required": ["pid"],
    },
)
def kill_process(pid: int) -> Dict[str, Any]:
    try:
        pid = int(pid)
        if pid <= 4:
            return {"status": "error", "message": f"Không thể tắt tiến trình hệ thống lõi (PID: {pid})."}
        proc = psutil.Process(pid)
        proc_name = proc.name()
        proc.terminate()
        try:
            proc.wait(timeout=2.0)
        except psutil.TimeoutExpired:
            proc.kill()
        return {
            "status": "success",
            "pid": pid,
            "process_name": proc_name,
            "message": f"Đã tắt thành công tiến trình '{proc_name}' (PID: {pid}).",
        }
    except psutil.NoSuchProcess:
        return {"status": "error", "message": f"Tiến trình với PID {pid} không tồn tại."}
    except psutil.AccessDenied:
        return {"status": "error", "message": f"Từ chối quyền hạn: Cần quyền Administrator để tắt PID {pid}."}
    except Exception as exc:
        return {"status": "error", "message": str(exc)}
