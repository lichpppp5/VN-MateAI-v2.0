# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 Dương Thanh Lịch — VN-MateAI. See LICENSE and NOTICE.
"""
skills/pc_control_skills.py
============================
PC Management Skill Pack — Quản lý & điều khiển máy tính đa năng.

Cung cấp 10 skills:
  1.  get_system_info          — CPU, RAM, Disk, Uptime, OS version
  2.  list_processes           — Danh sách tiến trình đang chạy
  3.  kill_process             — Kết thúc tiến trình theo tên hoặc PID
  4.  open_application         — Mở ứng dụng / file / URL
  5.  search_files             — Tìm kiếm file theo tên hoặc pattern
  6.  get_clipboard            — Đọc nội dung clipboard
  7.  set_clipboard            — Ghi nội dung vào clipboard
  8.  get_network_info         — IP, trạng thái kết nối mạng, DNS
  9.  set_system_volume        — Điều chỉnh âm lượng hệ thống (0–100)
  10. run_powershell_command   — Thực thi lệnh PowerShell an toàn (blocklist)

Platform: Windows 10/11 (primary) + macOS fallback cho dev.
Zero-Vision: không dùng screenshot, không dùng pyautogui / opencv.
"""

from __future__ import annotations

import logging
import os
import platform
import shutil
import socket
import subprocess
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional

import psutil
import pyperclip

from core.plugin_manager import export_skill

logger = logging.getLogger(__name__)

_IS_WINDOWS = platform.system() == "Windows"

# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _run(args: List[str], timeout: int = 15) -> Dict[str, Any]:
    """Run a subprocess and return structured output."""
    try:
        result = subprocess.run(
            args,
            capture_output=True,
            text=True,
            timeout=timeout,
            encoding="utf-8",
            errors="replace",
        )
        return {
            "returncode": result.returncode,
            "stdout": result.stdout.strip(),
            "stderr": result.stderr.strip(),
            "success": result.returncode == 0,
        }
    except subprocess.TimeoutExpired:
        return {"returncode": -1, "stdout": "", "stderr": f"Timeout after {timeout}s.", "success": False}
    except FileNotFoundError as exc:
        return {"returncode": -1, "stdout": "", "stderr": str(exc), "success": False}
    except Exception as exc:  # pylint: disable=broad-except
        return {"returncode": -1, "stdout": "", "stderr": str(exc), "success": False}


def _bytes_to_gb(b: int) -> float:
    return round(b / (1024 ** 3), 2)


# ---------------------------------------------------------------------------
# Skill 1: System Info
# ---------------------------------------------------------------------------


@export_skill(
    name="get_system_info",
    description=(
        "Lấy thông tin hệ thống máy tính / máy chủ: máy đang dùng bao nhiêu CPU, RAM, ổ đĩa (Disk), "
        "thời gian chạy (Uptime), tên máy, OS version, múi giờ, và nhiệt độ CPU (nếu có)."
    ),
    parameters_schema={
        "type": "object",
        "properties": {
            "include_temps": {
                "type": "boolean",
                "description": "Bao gồm thông tin nhiệt độ CPU/GPU nếu có. Mặc định: false.",
                "default": False,
            }
        },
        "required": [],
    },
)
def get_system_info(include_temps: bool = False) -> Dict[str, Any]:
    """Return comprehensive system hardware and OS information."""
    try:
        cpu_freq = psutil.cpu_freq()
        ram = psutil.virtual_memory()
        disk = psutil.disk_usage("/")
        boot_ts = psutil.boot_time()
        uptime_sec = time.time() - boot_ts
        uptime_str = str(timedelta(seconds=int(uptime_sec)))

        info: Dict[str, Any] = {
            "hostname": socket.gethostname(),
            "os": platform.system(),
            "os_version": platform.version(),
            "architecture": platform.machine(),
            "python_version": platform.python_version(),
            "cpu": {
                "name": platform.processor() or "N/A",
                "physical_cores": psutil.cpu_count(logical=False),
                "logical_cores": psutil.cpu_count(logical=True),
                "usage_pct": psutil.cpu_percent(interval=0.5),
                "frequency_mhz": round(cpu_freq.current, 1) if cpu_freq else None,
            },
            "ram": {
                "total_gb": _bytes_to_gb(ram.total),
                "used_gb": _bytes_to_gb(ram.used),
                "available_gb": _bytes_to_gb(ram.available),
                "usage_pct": ram.percent,
            },
            "disk": {
                "total_gb": _bytes_to_gb(disk.total),
                "used_gb": _bytes_to_gb(disk.used),
                "free_gb": _bytes_to_gb(disk.free),
                "usage_pct": disk.percent,
            },
            "uptime": uptime_str,
            "boot_time": datetime.fromtimestamp(boot_ts).strftime("%Y-%m-%d %H:%M:%S"),
        }

        if include_temps:
            try:
                temps = psutil.sensors_temperatures()
                info["temperatures"] = {
                    sensor: [{"label": t.label, "current_c": t.current} for t in readings]
                    for sensor, readings in (temps or {}).items()
                }
            except AttributeError:
                info["temperatures"] = "Không hỗ trợ trên hệ thống này."

        return info

    except Exception as exc:  # pylint: disable=broad-except
        raise RuntimeError(f"get_system_info failed: {exc}") from exc


# ---------------------------------------------------------------------------
# Skill 2: List Processes
# ---------------------------------------------------------------------------


@export_skill(
    name="list_processes",
    description=(
        "Liệt kê các tiến trình đang chạy trên máy. "
        "Có thể lọc theo tên, sắp xếp theo CPU hoặc RAM."
    ),
    parameters_schema={
        "type": "object",
        "properties": {
            "filter_name": {
                "type": "string",
                "description": "Lọc tiến trình chứa từ khóa này (không phân biệt hoa/thường). Bỏ trống = tất cả.",
                "default": "",
            },
            "sort_by": {
                "type": "string",
                "enum": ["cpu", "memory", "name", "pid"],
                "description": "Tiêu chí sắp xếp. Mặc định: cpu.",
                "default": "cpu",
            },
            "limit": {
                "type": "integer",
                "description": "Số lượng tiến trình trả về tối đa. Mặc định: 20.",
                "default": 20,
            },
        },
        "required": [],
    },
)
def list_processes(
    filter_name: str = "",
    sort_by: str = "cpu",
    limit: int = 20,
) -> Dict[str, Any]:
    """List running processes with resource usage."""
    try:
        procs: List[Dict[str, Any]] = []
        for proc in psutil.process_iter(["pid", "name", "cpu_percent", "memory_info", "status", "create_time"]):
            try:
                info = proc.info
                name: str = info.get("name") or ""
                if filter_name and filter_name.lower() not in name.lower():
                    continue
                mem_info = info.get("memory_info")
                mem_mb = round(getattr(mem_info, "rss", 0) / (1024 ** 2), 1)
                procs.append({
                    "pid": info["pid"],
                    "name": name,
                    "cpu_pct": round(info.get("cpu_percent") or 0.0, 1),
                    "memory_mb": mem_mb,
                    "status": info.get("status", "unknown"),
                })
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                continue

        sort_key = {
            "cpu": lambda p: p["cpu_pct"],
            "memory": lambda p: p["memory_mb"],
            "name": lambda p: p["name"].lower(),
            "pid": lambda p: p["pid"],
        }.get(sort_by, lambda p: p["cpu_pct"])

        procs.sort(key=sort_key, reverse=(sort_by in ("cpu", "memory")))
        procs = procs[:limit]

        return {"processes": procs, "total_shown": len(procs)}
    except Exception as exc:  # pylint: disable=broad-except
        raise RuntimeError(f"list_processes failed: {exc}") from exc


# ---------------------------------------------------------------------------
# Skill 3: Kill Process
# ---------------------------------------------------------------------------


@export_skill(
    name="kill_process",
    description=(
        "Kết thúc (terminate/kill) một tiến trình theo tên hoặc PID. "
        "Dùng force=true nếu terminate thông thường không thành công."
    ),
    parameters_schema={
        "type": "object",
        "properties": {
            "name": {
                "type": "string",
                "description": "Tên tiến trình (ví dụ: 'notepad.exe', 'chrome.exe'). Bỏ trống nếu dùng pid.",
                "default": "",
            },
            "pid": {
                "type": "integer",
                "description": "PID của tiến trình. Dùng khi biết chính xác PID.",
                "default": 0,
            },
            "force": {
                "type": "boolean",
                "description": "Nếu true, dùng kill() thay vì terminate() (mạnh hơn).",
                "default": False,
            },
        },
        "required": [],
    },
)
def kill_process(name: str = "", pid: int = 0, force: bool = False) -> Dict[str, Any]:
    """Terminate or kill a process by name or PID."""
    if not name and not pid:
        return {"success": False, "data": None, "error": "Phải cung cấp 'name' hoặc 'pid'."}

    killed: List[Dict[str, Any]] = []
    errors: List[str] = []

    try:
        targets: List[psutil.Process] = []

        if pid:
            try:
                targets.append(psutil.Process(pid))
            except psutil.NoSuchProcess:
                return {"success": False, "data": None, "error": f"Không tìm thấy tiến trình với PID={pid}."}
        else:
            for proc in psutil.process_iter(["pid", "name"]):
                try:
                    if (proc.info.get("name") or "").lower() == name.lower():
                        targets.append(proc)
                except (psutil.NoSuchProcess, psutil.AccessDenied):
                    continue

        if not targets:
            return {
                "success": False,
                "data": None,
                "error": f"Không tìm thấy tiến trình nào khớp với name='{name}'.",
            }

        for proc in targets:
            try:
                proc_name = proc.name()
                proc_pid = proc.pid
                if force:
                    proc.kill()
                else:
                    proc.terminate()
                killed.append({"pid": proc_pid, "name": proc_name})
                logger.info("Process %s (PID=%d) %s.", proc_name, proc_pid, "killed" if force else "terminated")
            except psutil.AccessDenied:
                errors.append(f"PID {proc.pid}: Không đủ quyền để kết thúc tiến trình.")
            except psutil.NoSuchProcess:
                errors.append(f"PID {proc.pid}: Tiến trình đã thoát trước đó.")
            except Exception as exc:  # pylint: disable=broad-except
                errors.append(f"PID {proc.pid}: {exc}")

        if not killed and errors:
            raise RuntimeError("; ".join(errors))
        return {"killed": killed, "errors": errors}
    except RuntimeError:
        raise
    except Exception as exc:  # pylint: disable=broad-except
        raise RuntimeError(f"kill_process failed: {exc}") from exc


# ---------------------------------------------------------------------------
# Skill 4: Open Application / File / URL
# ---------------------------------------------------------------------------


@export_skill(
    name="open_application",
    description=(
        "Mở một ứng dụng, file, hoặc URL bằng lệnh hệ thống mặc định. "
        "Ví dụ: mở Notepad, mở file PDF, mở trang web."
    ),
    parameters_schema={
        "type": "object",
        "properties": {
            "target": {
                "type": "string",
                "description": (
                    "Tên ứng dụng (vd: 'notepad', 'calc', 'mspaint'), "
                    "đường dẫn file (vd: 'C:\\\\report.pdf'), "
                    "hoặc URL (vd: 'https://google.com')."
                ),
            },
            "args": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Danh sách tham số bổ sung truyền cho ứng dụng.",
                "default": [],
            },
        },
        "required": ["target"],
    },
    needs_desktop=True,  # mở cửa sổ / phát trên máy chủ: cần phiên đăng nhập có màn hình
)
def open_application(target: str, args: Optional[List[str]] = None) -> Dict[str, Any]:
    """Open an application, file, or URL using the OS default handler."""
    extra_args: List[str] = args or []
    try:
        if _IS_WINDOWS:
            # On Windows: use 'start' shell command to open anything
            cmd = ["cmd", "/c", "start", "", target] + extra_args
            result = _run(cmd, timeout=10)
        else:
            # macOS/Linux fallback
            opener = "open" if platform.system() == "Darwin" else "xdg-open"
            cmd = [opener, target] + extra_args
            result = _run(cmd, timeout=10)

        if not result["success"]:
            raise RuntimeError(result["stderr"] or f"Could not open: {target}")
        return {"target": target, "command": cmd}
    except RuntimeError:
        raise
    except Exception as exc:  # pylint: disable=broad-except
        raise RuntimeError(f"open_application failed: {exc}") from exc


# ---------------------------------------------------------------------------
# Skill 4b: Media keys (phát / tạm dừng / bài tiếp / âm lượng)
# ---------------------------------------------------------------------------

#: Mã phím ảo Windows cho các phím media.
_MEDIA_VK = {"play_pause": 0xB3, "next": 0xB0, "previous": 0xB1, "stop": 0xB2,
             "mute": 0xAD, "volume_down": 0xAE, "volume_up": 0xAF}
#: Lệnh playerctl / amixer tương ứng trên Linux.
_MEDIA_LINUX = {"play_pause": ["playerctl", "play-pause"], "next": ["playerctl", "next"],
                "previous": ["playerctl", "previous"], "stop": ["playerctl", "stop"],
                "mute": ["amixer", "-q", "set", "Master", "toggle"],
                "volume_down": ["amixer", "-q", "set", "Master", "5%-"], "volume_up": ["amixer", "-q", "set", "Master", "5%+"]}


@export_skill(
    name="media_control",
    description=(
        "Điều khiển trình phát nhạc / video đang mở trên máy chủ bằng phím media của hệ điều hành "
        "(YouTube trên Chrome, Spotify, VLC…): phát tiếp / tạm dừng, bài tiếp theo, bài trước, dừng, "
        "tắt / bật tiếng, tăng / giảm âm lượng. Người dùng thường nói: 'phát tiếp nhạc', 'tạm dừng video', "
        "'bật lại bài hát đang dừng', 'chuyển bài', 'bài tiếp theo', 'tắt tiếng'. "
        "English: play pause media, resume youtube, next track, previous track, mute."
    ),
    parameters_schema={
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": sorted(_MEDIA_VK),
                "description": "play_pause (phát tiếp / tạm dừng), next, previous, stop, mute, volume_up, volume_down.",
            },
            "times": {"type": "integer", "minimum": 1, "maximum": 10, "default": 1,
                      "description": "Số lần nhấn (vd tăng âm lượng 3 nấc)."},
        },
        "required": ["action"],
    },
    needs_desktop=True,  # phím media đi tới phiên đăng nhập có màn hình; máy chủ chạy nền thì không có tác dụng
)
def media_control(action: str, times: int = 1) -> Dict[str, Any]:
    """
    Nhấn phím media. Mã đã duyệt sẵn — trước 2026-10-12 trợ lý tự viết kỹ năng này bằng ctypes, luôn bị kiểm toán an
    ninh mã tự sinh từ chối (cấm ctypes), rồi tạo lại liên tục tới khi hết hạn mức tạo kỹ năng.
    """
    action = str(action or "").strip().lower()
    if action not in _MEDIA_VK:
        raise ValueError(f"action không hợp lệ: {action!r} (chỉ {', '.join(sorted(_MEDIA_VK))})")
    times = max(1, min(10, int(times or 1)))
    if _IS_WINDOWS:
        import ctypes  # mã lõi đã duyệt (kỹ năng tự sinh không được dùng ctypes)
        user32 = ctypes.windll.user32
        vk, extended, keyup = _MEDIA_VK[action], 0x0001, 0x0002
        for _ in range(times):
            user32.keybd_event(vk, 0, extended, 0)
            user32.keybd_event(vk, 0, extended | keyup, 0)
            time.sleep(0.05)
        return {"action": action, "times": times, "platform": "windows"}
    if platform.system() == "Linux":
        cmd = _MEDIA_LINUX[action]
        if not shutil.which(cmd[0]):
            raise RuntimeError(f"Thiếu '{cmd[0]}' trên máy chủ Linux (cài playerctl / alsa-utils).")
        for _ in range(times):
            result = _run(cmd, timeout=5)
            if not result["success"]:
                raise RuntimeError(result["stderr"] or f"{cmd[0]} lỗi")
        return {"action": action, "times": times, "platform": "linux"}
    raise RuntimeError("media_control hiện hỗ trợ Windows và Linux.")


# ---------------------------------------------------------------------------
# Skill 5: Search Files
# ---------------------------------------------------------------------------


@export_skill(
    name="search_files",
    description=(
        "Tìm kiếm file trên máy tính theo tên hoặc pattern glob. "
        "Có thể giới hạn thư mục tìm kiếm và kết quả trả về."
    ),
    parameters_schema={
        "type": "object",
        "properties": {
            "pattern": {
                "type": "string",
                "description": "Tên file hoặc glob pattern. Ví dụ: '*.pdf', 'report*.xlsx', 'config.json'.",
            },
            "search_dir": {
                "type": "string",
                "description": "Thư mục bắt đầu tìm kiếm. Mặc định: thư mục home của người dùng.",
                "default": "",
            },
            "max_results": {
                "type": "integer",
                "description": "Số kết quả tối đa. Mặc định: 30.",
                "default": 30,
            },
            "include_hidden": {
                "type": "boolean",
                "description": "Bao gồm file/thư mục ẩn. Mặc định: false.",
                "default": False,
            },
        },
        "required": ["pattern"],
    },
)
def search_files(
    pattern: str,
    search_dir: str = "",
    max_results: int = 30,
    include_hidden: bool = False,
) -> Dict[str, Any]:
    """Search for files matching a glob pattern starting from search_dir."""
    try:
        base = Path(search_dir).expanduser() if search_dir else Path.home()
        if not base.exists():
            return {"success": False, "data": None, "error": f"Thư mục không tồn tại: {base}"}

        found: List[Dict[str, Any]] = []
        for match in base.rglob(pattern):
            if not include_hidden and any(part.startswith(".") for part in match.parts):
                continue
            try:
                stat = match.stat()
                found.append({
                    "path": str(match),
                    "name": match.name,
                    "size_kb": round(stat.st_size / 1024, 1),
                    "modified": datetime.fromtimestamp(stat.st_mtime).strftime("%Y-%m-%d %H:%M"),
                    "is_dir": match.is_dir(),
                })
            except PermissionError:
                continue
            if len(found) >= max_results:
                break

        return {
            "results": found,
            "total_found": len(found),
            "search_dir": str(base),
            "pattern": pattern,
            "truncated": len(found) >= max_results,
        }
    except Exception as exc:  # pylint: disable=broad-except
        raise RuntimeError(f"search_files failed: {exc}") from exc


# ---------------------------------------------------------------------------
# Skill 6: Read Clipboard
# ---------------------------------------------------------------------------


@export_skill(
    name="get_clipboard",
    description="Đọc nội dung văn bản hiện tại trong clipboard của hệ thống.",
    parameters_schema={
        "type": "object",
        "properties": {},
        "required": [],
    },
)
def get_clipboard() -> Dict[str, Any]:
    """Return the current clipboard text content."""
    try:
        content = pyperclip.paste()
        return {"content": content, "length": len(content)}
    except Exception as exc:  # pylint: disable=broad-except
        raise RuntimeError(f"get_clipboard failed: {exc}") from exc


# ---------------------------------------------------------------------------
# Skill 7: Write Clipboard
# ---------------------------------------------------------------------------


@export_skill(
    name="set_clipboard",
    description="Ghi một đoạn văn bản vào clipboard của hệ thống.",
    parameters_schema={
        "type": "object",
        "properties": {
            "text": {
                "type": "string",
                "description": "Nội dung văn bản cần ghi vào clipboard.",
            }
        },
        "required": ["text"],
    },
)
def set_clipboard(text: str) -> Dict[str, Any]:
    """Write text to the system clipboard."""
    try:
        pyperclip.copy(text)
        return {"written_length": len(text)}
    except Exception as exc:  # pylint: disable=broad-except
        raise RuntimeError(f"set_clipboard failed: {exc}") from exc


# ---------------------------------------------------------------------------
# Skill 8: Network Info
# ---------------------------------------------------------------------------


@export_skill(
    name="get_network_info",
    description=(
        "Lấy thông tin mạng: IP address (local & public), "
        "trạng thái kết nối, tốc độ upload/download, DNS server."
    ),
    parameters_schema={
        "type": "object",
        "properties": {
            "check_internet": {
                "type": "boolean",
                "description": "Kiểm tra kết nối Internet bằng cách ping 8.8.8.8. Mặc định: true.",
                "default": True,
            }
        },
        "required": [],
    },
)
def get_network_info(check_internet: bool = True) -> Dict[str, Any]:
    """Return local network configuration and connectivity status."""
    try:
        # Local IPs
        interfaces: Dict[str, List[str]] = {}
        for iface, addrs in psutil.net_if_addrs().items():
            ips = [a.address for a in addrs if a.family == socket.AF_INET]
            if ips:
                interfaces[iface] = ips

        # Primary local IP (routing to 8.8.8.8)
        primary_ip = "N/A"
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
                s.connect(("8.8.8.8", 80))
                primary_ip = s.getsockname()[0]
        except Exception:
            pass

        # Network I/O counters
        net_io = psutil.net_io_counters()
        io_stats = {
            "bytes_sent_mb": round(net_io.bytes_sent / (1024 ** 2), 2),
            "bytes_recv_mb": round(net_io.bytes_recv / (1024 ** 2), 2),
        }

        # Internet connectivity check
        internet_ok: Optional[bool] = None
        ping_ms: Optional[float] = None
        if check_internet:
            ping_cmd = ["ping", "-n", "1", "8.8.8.8"] if _IS_WINDOWS else ["ping", "-c", "1", "-W", "2", "8.8.8.8"]
            t0 = time.time()
            ping_res = _run(ping_cmd, timeout=5)
            internet_ok = ping_res["success"]
            ping_ms = round((time.time() - t0) * 1000, 1) if internet_ok else None

        return {
            "primary_ip": primary_ip,
            "hostname": socket.gethostname(),
            "interfaces": interfaces,
            "io_stats": io_stats,
            "internet_connected": internet_ok,
            "ping_google_ms": ping_ms,
        }
    except Exception as exc:  # pylint: disable=broad-except
        raise RuntimeError(f"get_network_info failed: {exc}") from exc


# ---------------------------------------------------------------------------
# Skill 9: Set System Volume
# ---------------------------------------------------------------------------


@export_skill(
    name="set_system_volume",
    description=(
        "Điều chỉnh âm lượng hệ thống (0–100). "
        "Hỗ trợ Windows (nircmd / PowerShell) và macOS (osascript)."
    ),
    parameters_schema={
        "type": "object",
        "properties": {
            "level": {
                "type": "integer",
                "description": "Mức âm lượng từ 0 (tắt tiếng) đến 100 (tối đa).",
                "minimum": 0,
                "maximum": 100,
            },
            "mute": {
                "type": "boolean",
                "description": "Nếu true, tắt tiếng (mute) bất kể giá trị level.",
                "default": False,
            },
        },
        "required": ["level"],
    },
)
def set_system_volume(level: int, mute: bool = False) -> Dict[str, Any]:
    """Set system master volume level (0-100) or mute."""
    level = max(0, min(100, level))

    try:
        if _IS_WINDOWS:
            if mute:
                # PowerShell mute via COM
                script = (
                    "$obj = New-Object -ComObject WScript.Shell; "
                    "$obj.SendKeys([char]173)"  # VK_VOLUME_MUTE
                )
                result = _run(["powershell", "-NonInteractive", "-Command", script])
            else:
                # Scale 0-100 → 0.0-1.0 for Windows CoreAudio via PowerShell
                ps_script = (
                    f"$vol = {level / 100.0}; "
                    "$audio = New-Object -ComObject WScript.Shell; "
                    f"[audio]::Volume = $vol"
                )
                # Preferred: nircmd (if installed)
                if shutil.which("nircmd"):
                    nircmd_level = int(level * 65535 / 100)
                    result = _run(["nircmd", "setsysvolume", str(nircmd_level)])
                else:
                    # PowerShell via SendKeys (rough but dependency-free)
                    result = _run(
                        ["powershell", "-NonInteractive", "-Command",
                         f"$wsh = New-Object -ComObject WScript.Shell; "
                         f"1..50 | ForEach-Object {{ $wsh.SendKeys([char]174) }}; "   # Vol down x50
                         f"1..{level // 2} | ForEach-Object {{ $wsh.SendKeys([char]175) }}"  # Vol up
                         ],
                        timeout=10,
                    )

        else:
            # macOS
            if mute:
                result = _run(["osascript", "-e", "set volume output muted true"])
            else:
                # macOS volume is 0–7 range via osascript
                mac_vol = round(level / 100 * 7)
                result = _run(["osascript", "-e", f"set volume output volume {level}"])

        if not result.get("success"):
            raise RuntimeError(result.get("stderr") or "Volume command failed")
        return {"level": level, "muted": mute}
    except RuntimeError:
        raise
    except Exception as exc:  # pylint: disable=broad-except
        raise RuntimeError(f"set_system_volume failed: {exc}") from exc


# ---------------------------------------------------------------------------
# Skill 10: Run PowerShell Command (with safety blocklist)
# ---------------------------------------------------------------------------

# Commands that are NEVER allowed regardless of context
_PS_BLOCKLIST = [
    "format-volume", "remove-item -recurse -force c:\\",
    "format c:", "rmdir /s", "shutdown", "bcdedit",
    "diskpart", "del /f /s", "net user", "reg delete hklm",
    "invoke-expression", "iex ", "downloadstring", "webclient",
    "bypass", "encodedcommand",
]


@export_skill(
    name="run_powershell_command",
    description=(
        "Thực thi một lệnh PowerShell trên máy tính. "
        "Có danh sách chặn các lệnh nguy hiểm. "
        "Dùng cho các tác vụ quản trị nhanh: kiểm tra sự kiện, query registry, v.v."
    ),
    parameters_schema={
        "type": "object",
        "properties": {
            "command": {
                "type": "string",
                "description": "Lệnh PowerShell cần thực thi.",
            },
            "timeout_sec": {
                "type": "integer",
                "description": "Thời gian tối đa chờ kết quả (giây). Mặc định: 30.",
                "default": 30,
            },
        },
        "required": ["command"],
    },
)
def run_powershell_command(command: str, timeout_sec: int = 30) -> Dict[str, Any]:
    """Execute a PowerShell command with safety blocklist enforcement."""
    # Safety check — case-insensitive scan
    cmd_lower = command.lower()
    for blocked in _PS_BLOCKLIST:
        if blocked in cmd_lower:
            return {
                "success": False,
                "data": None,
                "error": (
                    f"Lệnh bị chặn vì lý do an toàn: phát hiện từ khoá '{blocked}'. "
                    "Nếu cần thực hiện lệnh này, hãy dùng Safety Guard để xác nhận."
                ),
            }

    try:
        if _IS_WINDOWS:
            result = _run(
                ["powershell", "-NonInteractive", "-NoProfile", "-Command", command],
                timeout=timeout_sec,
            )
        else:
            # macOS fallback — run as bash
            result = _run(["bash", "-c", command], timeout=timeout_sec)

        if not result["success"]:
            raise RuntimeError(result["stderr"] or "PowerShell command failed")
        return {"stdout": result["stdout"], "returncode": result["returncode"]}
    except RuntimeError:
        raise
    except Exception as exc:  # pylint: disable=broad-except
        raise RuntimeError(f"run_powershell_command failed: {exc}") from exc
