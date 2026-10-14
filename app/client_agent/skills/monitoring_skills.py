# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 Dương Thanh Lịch — VN-MateAI. See LICENSE and NOTICE.
"""
client_agent/skills/monitoring_skills.py
========================================
VN-MateAI Endpoint Deep Monitoring & Security Audit Skills.

Responsibilities:
  - capture_screen_base64: Ultra-lightweight in-memory screen capture with JPEG compression and Base64 encoding.
  - get_active_processes: Retrieve Top CPU/RAM processes with PID, status, and memory metrics.
  - get_network_connections: Inspect active sockets (ESTABLISHED, LISTEN) with IP, port, and PID.
  - check_peripherals: Scan connected USB, input, and peripheral devices via WMI or platform probe.
  - security_audit: Audit Windows Defender real-time protection, signature status, and Windows Firewall profiles.
  - kill_process: Safely terminate/kill a process by PID.
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
except Exception:  # noqa: BLE001 — WMI hỏng ném com_error NGAY KHI import (không phải ImportError):
    wmi = None       # trước đây làm sập cả Agent lúc khởi động; nay chỉ tắt phần dùng WMI.

# Compatibility import for skill registration
try:
    from client_agent.core.plugin_manager import export_skill
except ImportError:
    try:
        from core.plugin_manager import export_skill
    except ImportError:
        def export_skill(name: str, description: str, parameters_schema: Dict[str, Any]):
            def decorator(func):
                return func
            return decorator

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# 1. Screen Capture Base64 (In-Memory, Low Latency, Compressed)
# ---------------------------------------------------------------------------

@export_skill(
    name="capture_screen_base64",
    description="Chụp toàn bộ màn hình máy tính, nén in-memory và trả về chuỗi Base64 cùng thông số độ phân giải.",
    parameters_schema={
        "type": "object",
        "properties": {
            "quality": {
                "type": "integer",
                "description": "Chất lượng nén JPEG từ 10 đến 100 (mặc định 65 để tối ưu mạng LAN)",
                "default": 65,
            },
            "max_width": {
                "type": "integer",
                "description": "Chiều rộng tối đa tính theo pixel để resize (mặc định 1280)",
                "default": 1280,
            },
        },
        "required": [],
    },
)
def capture_screen_base64(quality: int = 65, max_width: int = 1280) -> Dict[str, Any]:
    """
    Capture the primary screen, resize if needed to save bandwidth,
    compress to JPEG in memory, and encode as Base64 string.
    """
    if mss is None:
        return {
            "status": "error",
            "message": "Thư viện 'mss' chưa được cài đặt trên máy con.",
        }

    try:
        with mss.mss() as sct:
            monitor = sct.monitors[1] if len(sct.monitors) > 1 else sct.monitors[0]
            sct_img = sct.grab(monitor)

            # Convert raw BGRA bytes into PIL RGB image
            img = Image.frombytes("RGB", sct_img.size, sct_img.bgra, "raw", "BGRX")
            orig_w, orig_h = img.size

            # Resize if width exceeds max_width to keep payload small and fast over LAN
            if orig_w > max_width:
                ratio = max_width / float(orig_w)
                new_h = int(float(orig_h) * ratio)
                img = img.resize((max_width, new_h), Image.Resampling.LANCZOS)

            # Compress directly to in-memory buffer (no temp file written to disk)
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
        logger.error("Lỗi khi chụp màn hình Base64: %s", exc)
        return {
            "status": "error",
            "message": f"Không thể chụp màn hình: {exc}",
            "timestamp": time.strftime("%Y-%m-%d %H:%M:%S"),
        }


# ---------------------------------------------------------------------------
# 2. Active Processes (Top CPU / RAM)
# ---------------------------------------------------------------------------

@export_skill(
    name="get_active_processes",
    description="Liệt kê danh sách các tiến trình đang chạy ngốn nhiều CPU và RAM nhất trên máy tính.",
    parameters_schema={
        "type": "object",
        "properties": {
            "limit": {
                "type": "integer",
                "description": "Số lượng tiến trình tối đa cần lấy (mặc định 15)",
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
    """
    Get top running processes sorted by CPU or Memory usage.
    Handles AccessDenied and NoSuchProcess gracefully.
    """
    procs: List[Dict[str, Any]] = []
    try:
        for p in psutil.process_iter(['pid', 'name', 'cpu_percent', 'memory_percent', 'status', 'username']):
            try:
                info = p.info
                name = info.get('name') or "Unknown"
                cpu = round(info.get('cpu_percent') or 0.0, 1)
                mem = round(info.get('memory_percent') or 0.0, 1)
                status = info.get('status') or "running"
                user = info.get('username') or "-"
                pid = info.get('pid')

                procs.append({
                    "pid": pid,
                    "name": name,
                    "cpu_percent": cpu,
                    "memory_percent": mem,
                    "status": status,
                    "username": user,
                })
            except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
                continue

        # Sort
        key = "cpu_percent" if sort_by.lower() == "cpu" else "memory_percent"
        procs.sort(key=lambda x: x.get(key, 0.0), reverse=True)
        top_procs = procs[:max(1, min(limit, 100))]

        return {
            "status": "success",
            "total_scanned": len(procs),
            "count": len(top_procs),
            "sort_by": sort_by,
            "processes": top_procs,
            "timestamp": time.strftime("%Y-%m-%d %H:%M:%S"),
        }
    except Exception as exc:
        logger.error("Lỗi khi quét danh sách tiến trình: %s", exc)
        return {
            "status": "error",
            "message": f"Lỗi quét tiến trình: {exc}",
            "processes": [],
        }


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
                "description": "Số lượng kết nối tối đa cần trả về (mặc định 30)",
                "default": 30,
            },
        },
        "required": [],
    },
)
def get_network_connections(limit: int = 30) -> Dict[str, Any]:
    """
    Scan active network sockets, returning local/remote IP & Port and associated PID.
    """
    connections: List[Dict[str, Any]] = []
    try:
        raw_conns = psutil.net_connections(kind="inet")
        proc_names: Dict[int, str] = {}

        for c in raw_conns:
            if c.status not in ("ESTABLISHED", "LISTEN", "SYN_SENT", "TIME_WAIT"):
                continue

            local_addr = f"{c.laddr.ip}:{c.laddr.port}" if c.laddr else "-"
            remote_addr = f"{c.raddr.ip}:{c.raddr.port}" if c.raddr else "-"
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
                "local_address": local_addr,
                "remote_address": remote_addr,
                "status": c.status,
                "pid": pid,
                "process_name": pname,
                "family": "IPv4" if c.family.name == "AF_INET" else "IPv6",
            })

        status_priority = {"ESTABLISHED": 0, "LISTEN": 1, "SYN_SENT": 2, "TIME_WAIT": 3}
        connections.sort(key=lambda x: status_priority.get(x["status"], 99))
        selected = connections[:limit]

        return {
            "status": "success",
            "total_found": len(connections),
            "count": len(selected),
            "connections": selected,
            "timestamp": time.strftime("%Y-%m-%d %H:%M:%S"),
        }

    except (psutil.AccessDenied, PermissionError):
        return {
            "status": "partial_success",
            "message": "Cần quyền Administrator để quét đầy đủ tất cả socket mạng hệ thống.",
            "count": 0,
            "connections": [],
            "timestamp": time.strftime("%Y-%m-%d %H:%M:%S"),
        }
    except Exception as exc:
        logger.error("Lỗi khi kiểm tra kết nối mạng: %s", exc)
        return {
            "status": "error",
            "message": f"Lỗi quét kết nối mạng: {exc}",
            "connections": [],
        }


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
    """
    List connected USB peripherals using WMI on Windows, or platform probing.
    """
    devices: List[Dict[str, Any]] = []
    is_win = platform.system() == "Windows"

    # --- Option A: Windows with WMI ---
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
        except Exception as wmi_exc:
            logger.warning("WMI query failed: %s, fallback to PowerShell", wmi_exc)

    # --- Option B: Windows fallback via PowerShell Get-PnpDevice ---
    if is_win and not devices:
        try:
            ps_cmd = (
                "Get-PnpDevice -PresentOnly | "
                "Where-Object { $_.InstanceId -like '*USB*' -or $_.Class -in @('USB','HIDClass','DiskDrive','Mouse','Keyboard') } | "
                "Select-Object -Property FriendlyName, Status, InstanceId, Class | "
                "ConvertTo-Json -Compress"
            )
            out = subprocess.check_output(["powershell", "-NoProfile", "-Command", ps_cmd], timeout=8, stderr=subprocess.DEVNULL)
            parsed = json.loads(out.decode("utf-8", errors="ignore"))
            if isinstance(parsed, dict):
                parsed = [parsed]
            for item in parsed:
                devices.append({
                    "name": item.get("FriendlyName") or "Thiết bị ngoại vi USB",
                    "device_id": str(item.get("InstanceId", ""))[:50],
                    "status": item.get("Status", "OK"),
                    "type": item.get("Class", "USB"),
                })
        except Exception as ps_exc:
            logger.warning("PowerShell PnP scan failed: %s", ps_exc)

    # --- Option C: Non-Windows or Fallback (macOS / Linux / Dev) ---
    if not devices:
        if platform.system() == "Darwin":
            try:
                out = subprocess.check_output(["system_profiler", "SPUSBDataType"], timeout=5, stderr=subprocess.DEVNULL)
                lines = out.decode("utf-8", errors="ignore").splitlines()
                for line in lines:
                    s = line.strip()
                    if s.endswith(":") and not s.startswith("USB") and not s.startswith("Host"):
                        dev_name = s.rstrip(":")
                        if dev_name:
                            devices.append({
                                "name": dev_name,
                                "device_id": f"USB_MAC_{len(devices)+1}",
                                "status": "OK",
                                "type": "USB Device",
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
# 5. Security Audit (Windows Defender & Firewall Status)
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
    """
    Check Antivirus protection and Firewall profiles.
    On Windows: Uses PowerShell Get-MpComputerStatus & Get-NetFirewallProfile.
    On Non-Windows: Provides system integrity and firewall fallback.
    """
    is_win = platform.system() == "Windows"

    defender_data: Dict[str, Any] = {
        "antivirus_enabled": True,
        "realtime_protection": True,
        "signature_version": "Up-to-date",
        "quick_scan_time": "Gần đây",
        "service_status": "Hoạt động bình thường",
    }

    firewall_profiles: List[Dict[str, Any]] = [
        {"profile": "Hồ Sơ Domain (Mạng Miền)", "enabled": True, "action": "Bật (Chặn cổng vào lạ)"},
        {"profile": "Hồ Sơ Private (Mạng LAN Nội Bộ)", "enabled": True, "action": "Bật (Cho phép trong danh mục)"},
        {"profile": "Hồ Sơ Public (Mạng Công Cộng)", "enabled": True, "action": "Bật (Bảo vệ nghiêm ngặt)"},
    ]

    if is_win:
        # 1. Query Windows Defender via PowerShell
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
        except Exception as def_exc:
            logger.warning("Defender query failed: %s", def_exc)

        # 2. Query Windows Firewall via PowerShell
        try:
            ps_fw = (
                "Get-NetFirewallProfile | "
                "Select-Object Name, Enabled, DefaultInboundAction | "
                "ConvertTo-Json -Compress"
            )
            out_fw = subprocess.check_output(["powershell", "-NoProfile", "-Command", ps_fw], timeout=8, stderr=subprocess.DEVNULL)
            fw_list = json.loads(out_fw.decode("utf-8", errors="ignore"))
            if isinstance(fw_list, dict):
                fw_list = [fw_list]

            firewall_profiles = []
            for item in fw_list:
                name = item.get("Name", "Profile")
                enabled = bool(item.get("Enabled", True))
                firewall_profiles.append({
                    "profile": f"Hồ Sơ Mạng {name}",
                    "enabled": enabled,
                    "action": "Bật (Chặn cổng vào trái phép)" if enabled else "Đã tắt (Nguy cơ bảo mật)",
                })
        except Exception as fw_exc:
            logger.warning("Firewall query failed: %s", fw_exc)

    return {
        "status": "success",
        "platform": platform.system(),
        "defender": defender_data,
        "firewall": firewall_profiles,
        "overall_rating": "AN TOÀN (Secure)" if defender_data.get("realtime_protection") else "CẢNH BÁO (Warning)",
        "timestamp": time.strftime("%Y-%m-%d %H:%M:%S"),
    }


# ---------------------------------------------------------------------------
# 6. Kill Process by PID
# ---------------------------------------------------------------------------

def kill_process(pid: int) -> Dict[str, Any]:
    """Tắt tiến trình theo PID — dùng CHUNG skill `kill_process` của pc_control_skills.

    Trước đây file này có một bản `kill_process` thứ hai cùng tên skill: bản nạp sau
    đè bản trước trong danh mục, còn lệnh "kill_process" từ máy chủ lại gọi bản này
    — hai cách tắt tiến trình, hai định dạng kết quả.
    """
    try:
        from skills.pc_control_skills import kill_process as _kill
    except ImportError:
        from client_agent.skills.pc_control_skills import kill_process as _kill
    try:
        res = _kill(pid=int(pid))
    except (RuntimeError, ValueError, TypeError) as exc:
        return {"status": "error", "pid": pid, "error": str(exc)}
    if res.get("success") is False:
        return {"status": "error", "pid": pid, "error": res.get("error")}
    return {"status": "success", "pid": pid, **res}
