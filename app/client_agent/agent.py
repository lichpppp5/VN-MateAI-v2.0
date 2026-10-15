# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 Dương Thanh Lịch — VN-MateAI. See LICENSE and NOTICE.
"""
client_agent/agent.py
=====================
VN-MateAI Client Agent — Lightweight Worker Node for Enterprise RPA.

Responsibilities:
  - Establish persistent WebSocket connection to Master Server (ws://[MASTER_IP]:5843/ws/client).
  - Register worker identity (client_id, hostname, platform, local IP, available skills).
  - Listen for automation task payloads from Master and execute locally via ClientPluginManager.
  - Return execution results back to Master asynchronously.
  - Support hot-installation of new skills sent remotely by Master.
  - Automatically reconnect with exponential backoff upon network interruption.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import platform
import socket
import sys
import time
from pathlib import Path
from typing import Any, Dict, Optional

# Ensure client_agent and its parent directory are in sys.path
_AGENT_ROOT = Path(__file__).resolve().parent
if str(_AGENT_ROOT) not in sys.path:
    sys.path.insert(0, str(_AGENT_ROOT))
if str(_AGENT_ROOT.parent) not in sys.path:
    sys.path.insert(0, str(_AGENT_ROOT.parent))

# Setup compatibility alias so skill files importing 'from core.plugin_manager import export_skill' work
try:
    import core.plugin_manager as cpm
except ImportError:
    import client_agent.core.plugin_manager as cpm

import types
if "core" not in sys.modules:
    core_pkg = types.ModuleType("core")
    core_pkg.plugin_manager = cpm
    sys.modules["core"] = core_pkg
    sys.modules["core.plugin_manager"] = cpm
else:
    sys.modules["core.plugin_manager"] = cpm

# Also alias client_agent for skills that import from client_agent
if "client_agent" not in sys.modules:
    ca_pkg = types.ModuleType("client_agent")
    ca_pkg.core = sys.modules.get("core")
    sys.modules["client_agent"] = ca_pkg
    sys.modules["client_agent.core"] = sys.modules.get("core")
    sys.modules["client_agent.core.plugin_manager"] = cpm

try:
    from client_agent.core.plugin_manager import client_plugin_manager
except (ImportError, AttributeError):
    try:
        from core.plugin_manager import client_plugin_manager
    except (ImportError, AttributeError):
        from core.plugin_manager import plugin_manager as client_plugin_manager

try:
    from skills.monitoring_skills import (
        capture_screen_base64,
        get_active_processes,
        get_network_connections,
        check_peripherals,
        security_audit,
        kill_process,
    )
except ImportError:
    from client_agent.skills.monitoring_skills import (
        capture_screen_base64,
        get_active_processes,
        get_network_connections,
        check_peripherals,
        security_audit,
        kill_process,
    )

import websockets

# ---------------------------------------------------------------------------
# Phase 29: End-to-End SSL/TLS Certificate Pinning & Secure Agent
# ---------------------------------------------------------------------------
import ssl

#: Nơi chứa config.json / device.json / server_cert.pem / logs / skills ghi được.
#: Bản build (.exe / macOS): thư mục chứa file chạy (thư mục cài). Bản Python: cạnh agent.py.
_DATA_ROOT = Path(sys.executable).resolve().parent if getattr(sys, "frozen", False) else _AGENT_ROOT

_CERT_FILE_NAME = "server_cert.pem"
_CERT_PATH = _DATA_ROOT / _CERT_FILE_NAME


def get_server_cert_path() -> Optional[str]:
    """
    Resolve path to server_cert.pem.
    Checks:
      1. Agent directory (alongside agent.py)
      2. Parent directory / certs directory (development fallback)
    """
    if _CERT_PATH.exists() and _CERT_PATH.stat().st_size > 0:
        return str(_CERT_PATH)
    dev_cert = _AGENT_ROOT.parent / "certs" / "server.crt"
    if dev_cert.exists() and dev_cert.stat().st_size > 0:
        return str(dev_cert)
    return None


def get_ssl_context() -> Optional[ssl.SSLContext]:
    """
    Build SSLContext enforcing strict certificate validation using pinned server_cert.pem.
    Strictly enforces ssl.CERT_REQUIRED to guarantee anti-MITM protection.
    Never uses cert_reqs=ssl.CERT_NONE or verify=False.
    """
    cert_path = get_server_cert_path()
    if cert_path:
        ctx = ssl.create_default_context(cafile=cert_path)
        ctx.check_hostname = False  # Allows IP-based or custom local domain connection
        ctx.verify_mode = ssl.CERT_REQUIRED
        return ctx
    # Fallback to default CA bundle (still CERT_REQUIRED)
    return ssl.create_default_context()


def get_websocket_sslopt() -> dict:
    """
    Returns sslopt dictionary for websocket-client (websocket.WebSocketApp).
    Strictly verifies server certificate against pinned server_cert.pem.
    """
    cert_path = get_server_cert_path()
    if cert_path:
        return {
            "ca_certs": cert_path,
            "cert_reqs": ssl.CERT_REQUIRED,
            "check_hostname": False,
        }
    return {"cert_reqs": ssl.CERT_REQUIRED}


def http_request(method: str, url: str, **kwargs) -> Any:
    """
    Secure HTTP helper using requests (or httpx) with server_cert.pem verification.
    Ensures verify='server_cert.pem' is passed for HTTPS.
    """
    cert_path = get_server_cert_path()
    if cert_path and url.startswith("https://"):
        kwargs["verify"] = cert_path

    try:
        import requests
        return requests.request(method, url, **kwargs)
    except ImportError:
        import httpx
        return httpx.request(method, url, **kwargs)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-8s | [WORKER] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("client_agent")

#: Phiên bản Agent — gửi lên máy chủ khi đăng ký + mỗi nhịp tim. Máy chủ so với
#: bản đang phát hành (cùng file này) để báo máy trạm nào cần tải lại Agent.
AGENT_VERSION = "2.3.1"
HEARTBEAT_INTERVAL_S = 30
import re as _re  # noqa: E402 — không sửa khối import / bí danh `core` ở đầu file
_SKILL_FILE_RE = _re.compile(r"^[A-Za-z0-9_]{1,64}\.py$")


def _setup_file_log() -> None:
    """Ghi log ra logs/agent.log (xoay vòng 3 x 2 MB). Khi chạy nền bằng pythonw
    (Task Scheduler) không có cửa sổ console — trước đây log mất hết."""
    from logging.handlers import RotatingFileHandler
    if any(isinstance(h, RotatingFileHandler) for h in logging.getLogger().handlers):
        return                                    # đã gắn (gọi lại nhiều lần không ghi trùng dòng)
    try:
        log_dir = _DATA_ROOT / "logs"
        log_dir.mkdir(parents=True, exist_ok=True)
        fh = RotatingFileHandler(log_dir / "agent.log", maxBytes=2_000_000, backupCount=3, encoding="utf-8")
        fh.setFormatter(logging.Formatter("%(asctime)s | %(levelname)-8s | [WORKER] %(message)s",
                                          "%Y-%m-%d %H:%M:%S"))
        logging.getLogger().addHandler(fh)
    except OSError as exc:
        logger.warning("Không mở được file log: %s", exc)


def _runtime():
    """agent_runtime (cài đặt / đăng ký / cập nhật) — import muộn, cùng kiểu bí danh với skills."""
    try:
        import agent_runtime as rt
    except ImportError:
        from client_agent import agent_runtime as rt
    return rt


def _ui_command(script: str, *args: str) -> list:
    """Lệnh mở popup / overlay ở tiến trình riêng. Bản build không có python.exe +
    file .py rời: chạy lại chính nó với `--run-ui <script>`."""
    if getattr(sys, "frozen", False):
        return [sys.executable, "--run-ui", script, *args]
    return [sys.executable, str(_AGENT_ROOT / script), *args]


def _acquire_single_instance(port: int, wait_s: float = 0.0):
    """Chỉ một Agent mỗi máy: giữ cổng 127.0.0.1:<port>. Hai bản cùng chạy sẽ đăng ký
    cùng client_id và giành kết nối của nhau trên máy chủ. Trả socket (giữ suốt đời
    tiến trình) hoặc None nếu đã có bản khác."""
    deadline = time.time() + wait_s
    while True:
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            s.bind(("127.0.0.1", port))
            s.listen(1)
            return s
        except OSError:
            s.close()
            # Vừa tự cập nhật / khởi động lại: bản cũ có thể chưa thoát hẳn.
            if time.time() >= deadline:
                return None
            time.sleep(0.5)


def _succeeded(result: Dict[str, Any]) -> bool:
    """Kỹ năng tự báo lỗi (`status: error` / `success: False`) là lỗi, kể cả khi chỉ có "message" mà không có "error"."""
    if result.get("status") == "error" or result.get("success") is False:
        return False
    return bool(result.get("status") == "success" or result.get("success") is True or not result.get("error"))


def safe_skill_filename(filename: str) -> Optional[str]:
    """Tên file skill hợp lệ (chỉ chữ/số/_ + .py, không thư mục). Trước đây ghi thẳng
    `skills/<filename>` — tên kiểu `../agent.py` ghi đè được file ngoài thư mục skills."""
    name = str(filename or "").strip()
    if name and not name.endswith(".py"):
        name += ".py"
    return name if _SKILL_FILE_RE.match(name) else None


def build_heartbeat(client_id: str, skills_count: int) -> Dict[str, Any]:
    """Số đo THẬT của máy trạm cho máy chủ (trang /admin/topology, cảnh báo)."""
    import psutil
    disk_root = os.environ.get("SystemDrive", "C:") + "\\" if os.name == "nt" else "/"
    try:
        disk = psutil.disk_usage(disk_root).percent
    except OSError:
        disk = None
    boot = psutil.boot_time()
    return {
        "action": "heartbeat",
        "client_id": client_id,
        "agent_version": AGENT_VERSION,
        "package": _runtime().package_kind(),
        "cpu_percent": psutil.cpu_percent(interval=None),
        "ram_percent": psutil.virtual_memory().percent,
        "disk_percent": disk,
        "uptime_s": int(time.time() - boot),
        "skills_count": skills_count,
        "time": time.time(),
    }


def _redact_url(url: str) -> str:
    """URL để ghi log / in ra: che giá trị `token=` (enrollment secret)."""
    import re as _re
    return _re.sub(r"(token=)[^&]+", lambda m: m.group(1) + "***", str(url or ""))


def get_local_ip() -> str:
    """Detect LAN IP address of this machine."""
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        # Doesn't have to be reachable; just selects outbound interface
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return "127.0.0.1"


class ClientAgent:
    """Worker Agent that communicates with VN-MateAI Master Server."""

    def __init__(self, server_url: str, client_id: Optional[str] = None,
                 http_base: str = "", device_token: str = "") -> None:
        self.server_url = server_url
        #: https://<máy chủ> + khoá thiết bị: dùng cho API tự cập nhật.
        self.http_base = http_base
        self.device_token = device_token
        self._updating = False
        self.hostname = socket.gethostname()
        self.client_id = client_id or self.hostname
        self.ip_address = get_local_ip()
        self.platform_str = f"{platform.system()} {platform.release()} ({platform.machine()})"
        self._running = True
        self._skills_dir = _AGENT_ROOT / "skills"
        if getattr(sys, "frozen", False):
            # Bản build: skills gốc nằm trong gói (chỉ đọc, giải nén tạm) -> chép ra thư
            # mục cài để skill cài từ xa vẫn còn sau khi khởi động lại.
            self._skills_dir = _DATA_ROOT / "skills"
            _runtime().seed_skills(_runtime().bundle_root(_AGENT_ROOT) / "skills", self._skills_dir, AGENT_VERSION)
            if hasattr(client_plugin_manager, "_skills_dir"):
                client_plugin_manager._skills_dir = self._skills_dir
        self._skills_dir.mkdir(parents=True, exist_ok=True)
        # Phase 29: Prepare pinned SSL context for secure WSS/HTTPS
        self.ssl_context: Optional[ssl.SSLContext] = None
        if self.server_url.startswith("wss://") or self.server_url.startswith("https://"):
            self.ssl_context = get_ssl_context()

    def init_skills(self) -> int:
        """Load local skills into memory."""
        count = client_plugin_manager.load_plugins()
        logger.info("Đã nạp sẵn %d kỹ năng trên máy con [%s].", count, self.client_id)
        return count

    async def run(self) -> None:
        """Main loop maintaining persistent connection to Master Server."""
        self.init_skills()
        backoff = 2
        max_backoff = 20

        logger.info("=== VN-MateAI Client Agent khởi động ===")
        logger.info("Client ID    : %s", self.client_id)
        logger.info("IP Máy Con   : %s", self.ip_address)
        logger.info("Máy Chủ Đích : %s", _redact_url(self.server_url))

        while self._running:
            try:
                logger.info("Đang kết nối tới Máy Chủ Master: %s ...", _redact_url(self.server_url))
                ws_connect_kwargs: Dict[str, Any] = {
                    "ping_interval": 20,
                    "ping_timeout": 15,
                    "max_size": 10 * 1024 * 1024,  # 10MB max payload
                }
                if self.server_url.startswith("wss://"):
                    ws_connect_kwargs["ssl"] = self.ssl_context or get_ssl_context()

                async with websockets.connect(
                    self.server_url,
                    **ws_connect_kwargs,
                ) as ws:
                    logger.info("✅ Đã kết nối thành công tới Master Server!")
                    backoff = 2  # Reset backoff upon successful connection

                    # 1. Gửi bản tin đăng ký (Handshake)
                    handshake = {
                        "action": "register",
                        "client_id": self.client_id,
                        "hostname": self.hostname,
                        "platform": self.platform_str,
                        "ip": self.ip_address,
                        "skills": client_plugin_manager.get_skill_names(),
                        "status": "online",
                        "agent_version": AGENT_VERSION,
                        "package": _runtime().package_kind(),
                    }
                    await ws.send(json.dumps(handshake, ensure_ascii=False))
                    logger.info("Đã gửi bản tin xác thực danh tính [%s] tới Master.", self.client_id)

                    # 2. Nhịp tim: số đo thật mỗi HEARTBEAT_INTERVAL_S giây
                    hb_task = asyncio.create_task(self._heartbeat_loop(ws))
                    up_task = asyncio.create_task(self._update_loop(ws))

                    # 3. Lắng nghe và xử lý tác vụ từ Master
                    try:
                        async for raw_message in ws:
                            try:
                                data = json.loads(raw_message)
                            except json.JSONDecodeError:
                                logger.warning("Nhận dữ liệu không phải JSON: %s", raw_message[:100])
                                continue

                            await self._dispatch_message(ws, data)
                    finally:
                        hb_task.cancel()
                        up_task.cancel()

            except (websockets.exceptions.ConnectionClosedError, websockets.exceptions.ConnectionClosedOK) as e:
                logger.warning("Mất kết nối tới Master Server: %s. Sẽ thử lại sau %ds...", e, backoff)
            except Exception as e:
                logger.error("Lỗi kết nối tới Master Server: %s. Thử lại sau %ds...", e, backoff)

            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, max_backoff)

    UPDATE_CHECK_INTERVAL_S = 6 * 3600

    async def _update_loop(self, ws: websockets.WebSocketClientProtocol) -> None:
        """Kiểm tra bản mới 1 phút sau khi nối, rồi mỗi 6 giờ (máy chủ cũng chủ động báo)."""
        await asyncio.sleep(60)
        while True:
            await self.try_update(ws)
            await asyncio.sleep(self.UPDATE_CHECK_INTERVAL_S)

    async def try_update(self, ws: Optional[websockets.WebSocketClientProtocol] = None) -> str:
        """Tải + kiểm SHA-256 + cài bản mới nếu có. Trả trạng thái (để test / báo máy chủ)."""
        rt = _runtime()
        if not self.device_token or not self.http_base or not rt.updates_allowed(_AGENT_ROOT):
            return "disabled"
        if getattr(self, "_updating", False):
            return "busy"
        self._updating = True
        loop = asyncio.get_event_loop()
        ctx = get_ssl_context() if self.http_base.startswith("https") else None
        package = rt.package_kind()
        try:
            manifest = await loop.run_in_executor(
                None, rt.check_for_update, self.http_base, self.device_token, ctx, package, AGENT_VERSION)
            if not manifest:
                return "up_to_date"
            logger.info("Có bản Agent mới %s (đang %s) — đang tải.", manifest["version"], AGENT_VERSION)
            data = await loop.run_in_executor(None, rt.download_verified, self.http_base, self.device_token, ctx, manifest)
            if ws is not None:
                try:
                    await ws.send(json.dumps({"action": "update_status", "client_id": self.client_id,
                                              "status": "installing", "from": AGENT_VERSION,
                                              "to": manifest["version"], "package": package}))
                except Exception:  # noqa: BLE001
                    pass
            if rt.is_frozen():
                await loop.run_in_executor(None, rt.apply_frozen_update, data)
                logger.info("Đã giao bản %s cho trình cập nhật — thoát để thay file.", manifest["version"])
                rt.restart_frozen_exit()
            else:
                n = await loop.run_in_executor(None, rt.apply_source_update, data, _AGENT_ROOT)
                logger.info("Đã cập nhật %d file lên bản %s — khởi động lại.", n, manifest["version"])
                rt.restart_source()
            return "applied"
        except Exception as exc:  # noqa: BLE001 — cập nhật hỏng thì chạy tiếp bản cũ
            logger.error("Tự cập nhật thất bại (vẫn chạy bản %s): %s", AGENT_VERSION, exc)
            if ws is not None:
                try:
                    await ws.send(json.dumps({"action": "update_status", "client_id": self.client_id,
                                              "status": "failed", "from": AGENT_VERSION, "error": str(exc)[:200]}))
                except Exception:  # noqa: BLE001
                    pass
            return "failed"
        finally:
            self._updating = False

    async def _heartbeat_loop(self, ws: websockets.WebSocketClientProtocol) -> None:
        """Gửi số đo máy trạm định kỳ; dừng khi mất kết nối."""
        loop = asyncio.get_event_loop()
        while True:
            try:
                hb = await loop.run_in_executor(
                    None, build_heartbeat, self.client_id, client_plugin_manager.get_skill_count())
                await ws.send(json.dumps(hb, ensure_ascii=False))
            except asyncio.CancelledError:
                raise
            except websockets.exceptions.ConnectionClosed:
                return
            except Exception as exc:  # noqa: BLE001 — số đo lỗi không được làm rớt kết nối
                logger.debug("Không gửi được nhịp tim: %s", exc)
            await asyncio.sleep(HEARTBEAT_INTERVAL_S)

    async def _dispatch_message(self, ws: websockets.WebSocketClientProtocol, data: Dict[str, Any]) -> None:
        """Route incoming server instructions."""
        action = data.get("action")
        task_id = data.get("task_id", "")

        # ---- Yêu cầu 1: Thực thi kỹ năng automation / Native File System (Phase 38) ----
        if action in ("execute", "execute_skill", "file_system", "file_io"):
            skill_name = data.get("skill_name") or data.get("skill") or data.get("command", "")
            args = data.get("args") or data.get("parameters") or {}
            logger.info("Nhận lệnh thực thi: skill='%s', args=%s, task_id=%s", skill_name, args, task_id)

            # Chạy skill trong threadpool để không block asyncio loop.
            # Worker chạy NGAY TRÊN máy chủ (MASTER_LOCAL_WORKER) nạp được
            # `core.plugin_manager` của server, nơi `execute_skill` là coroutine;
            # trên máy trạm thật là bản đồng bộ trong client_agent/core. Trước đây
            # luôn đẩy vào executor → nhận về coroutine chưa chạy → `.get` lỗi và
            # agent rớt kết nối ở MỌI lệnh.
            loop = asyncio.get_event_loop()
            _exec = client_plugin_manager.execute_skill
            if asyncio.iscoroutinefunction(_exec):
                result = await _exec(skill_name, args)
            else:
                result = await loop.run_in_executor(None, _exec, skill_name, args)
            if result.get("status") == "error" and "Không tìm thấy kỹ năng" in str(result.get("error", "")):
                if skill_name in ("list_directory", "read_file", "write_file", "delete_item"):
                    try:
                        from client_agent.skills import file_system
                        fn = getattr(file_system, skill_name, None)
                        if fn:
                            result = await loop.run_in_executor(None, lambda: fn(**args))
                    except Exception as fs_err:
                        result = {"status": "error", "error": str(fs_err)}

            response = {
                "action": "result",
                "task_id": task_id,
                "client_id": self.client_id,
                "success": _succeeded(result),
                "result": result,
            }
            await ws.send(json.dumps(response, ensure_ascii=False))
            logger.info("Đã hoàn thành và gửi kết quả task [%s] về Master.", task_id)

        # ---- Yêu cầu 2: Cài đặt kỹ năng mới từ xa ----
        elif action == "install_skill":
            filename = data.get("filename", "")
            code = data.get("code", "")
            logger.info("Nhận kỹ năng mới từ Master: '%s' (%d bytes)", filename, len(code))

            success = False
            err_msg = None
            try:
                safe_name = safe_skill_filename(filename)
                if safe_name is None:
                    raise ValueError(f"Tên file skill không hợp lệ: {filename!r} (chỉ chữ, số, _ và .py)")
                filename = safe_name
                target_file = self._skills_dir / filename
                target_file.write_text(code, encoding="utf-8")
                # Hot reload
                total = client_plugin_manager.load_plugins()
                success = True
                logger.info("Đã cài đặt và nạp nóng kỹ năng '%s'. Tổng kỹ năng: %d", filename, total)
            except Exception as exc:
                err_msg = str(exc)
                logger.error("Lỗi cài đặt kỹ năng '%s': %s", filename, exc)

            response = {
                "action": "install_result",
                "task_id": task_id,
                "client_id": self.client_id,
                "filename": filename,
                "success": success,
                "error": err_msg,
                "skills": client_plugin_manager.get_skill_names(),
            }
            await ws.send(json.dumps(response, ensure_ascii=False))

        # ---- Yêu cầu 3: Ping kiểm tra nhịp tim ----
        elif action == "update_available":
            logger.info("Máy chủ báo có bản Agent %s.", data.get("version"))
            asyncio.create_task(self.try_update(ws))

        elif action == "ping":
            pong = {
                "action": "pong",
                "client_id": self.client_id,
                "timestamp": time.time(),
            }
            await ws.send(json.dumps(pong))

        # ---- Yêu cầu 4: Giám sát chuyên sâu (Deep Endpoint Monitoring) ----
        elif action == "monitor":
            monitor_type = data.get("type", "")
            args = data.get("args", {})
            logger.info("Nhận yêu cầu giám sát: type='%s', task_id=%s", monitor_type, task_id)

            loop = asyncio.get_event_loop()
            if monitor_type == "screen":
                quality = int(args.get("quality", 65))
                max_width = int(args.get("max_width", 1280))
                result = await loop.run_in_executor(None, capture_screen_base64, quality, max_width)
            elif monitor_type == "processes":
                limit = int(args.get("limit", 15))
                sort_by = str(args.get("sort_by", "cpu"))
                result = await loop.run_in_executor(None, get_active_processes, limit, sort_by)
            elif monitor_type == "network":
                limit = int(args.get("limit", 30))
                result = await loop.run_in_executor(None, get_network_connections, limit)
            elif monitor_type == "peripherals":
                result = await loop.run_in_executor(None, check_peripherals)
            elif monitor_type == "security":
                result = await loop.run_in_executor(None, security_audit)
            else:
                result = {"status": "error", "message": f"Loại giám sát '{monitor_type}' không được hỗ trợ."}

            response = {
                "action": "monitor_result",
                "task_id": task_id,
                "type": monitor_type,
                "client_id": self.client_id,
                "success": result.get("status") in ("success", "partial_success"),
                "result": result,
            }
            await ws.send(json.dumps(response, ensure_ascii=False))
            logger.info("Đã gửi dữ liệu giám sát [%s:%s] về Master.", monitor_type, task_id)

        # ---- Yêu cầu 5: Tắt tiến trình (Kill Process) ----
        elif action == "kill_process":
            pid = data.get("pid")
            logger.info("Nhận yêu cầu tắt tiến trình PID %s từ Master (task_id: %s)", pid, task_id)
            loop = asyncio.get_event_loop()
            result = await loop.run_in_executor(None, kill_process, pid)
            response = {
                "action": "kill_result",
                "task_id": task_id,
                "client_id": self.client_id,
                "pid": pid,
                # Từ bản client_template (đã gộp, Phase 6): skill có thể trả
                # {"success": True} thay vì {"status": "success"}.
                "success": _succeeded(result),
                "result": result,
            }
            await ws.send(json.dumps(response, ensure_ascii=False))
            logger.info("Đã hoàn thành lệnh tắt tiến trình PID %s và phản hồi về Master.", pid)

        # ---- Yêu cầu 6: Cửa sổ nhắc việc / Micro-Tasking Popup (Phase 11) ----
        elif action == "task_popup":
            message = data.get("message", "")
            sender = data.get("sender", "Ban Giám Đốc")
            logger.info("Nhận lệnh nhắc việc dạng Popup từ [%s]: '%s' (task_id: %s)", sender, message, task_id)
            asyncio.create_task(self._spawn_task_popup(ws, task_id, message, sender))

        # ---- Yêu cầu 7: Giao diện thị giác Ly Ly / Visual Overlay (Phase 32) ----
        elif action == "show_visual":
            visual_type = data.get("type", "metric_chart")
            visual_data = data.get("data", {})
            title = data.get("title", "TRỢ LÝ AI LY LY — HUD")
            duration = int(data.get("duration", 15))
            logger.info("Nhận lệnh hiển thị Visual Overlay [%s]: '%s' (task_id: %s)", visual_type, title, task_id)
            asyncio.create_task(self._spawn_visual_overlay(ws, task_id, visual_type, visual_data, title, duration))

        else:
            logger.debug("Bỏ qua thông điệp không xác định: %s", action)

    async def _spawn_task_popup(
        self,
        ws: websockets.WebSocketClientProtocol,
        task_id: str,
        message: str,
        sender: str,
    ) -> None:
        """
        Spawn standalone Tkinter popup in its own process.
        Ensures main-thread GUI compatibility across macOS Cocoa & Windows,
        runs asynchronously without blocking event loop, and returns user response.
        """
        try:
            proc = await asyncio.create_subprocess_exec(
                *_ui_command("popup_ui.py", task_id, message, sender),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, _ = await proc.communicate()
            out = stdout.decode(errors="replace").strip().splitlines()
            chosen = out[-1].strip() if out else ""
            # Popup không chạy được (không in gì) -> báo "issue" kèm lý do. Trước đây
            # mặc định "completed": Portal ghi hoàn thành dù nhân viên chưa thấy việc.
            status = chosen if chosen in ("completed", "issue", "dismissed") else "issue"
            error = None if chosen else f"Cửa sổ nhắc việc không mở được (mã thoát {proc.returncode})."
            logger.info("Nhân viên đã phản hồi task [%s]: %s", task_id, status)

            response_payload = {
                "action": "task_response",
                "task_id": task_id,
                "client_id": self.client_id,
                "status": status,
                "message": message,
                "timestamp": time.time(),
            }
            if error:
                response_payload["error"] = error
            await ws.send(json.dumps(response_payload, ensure_ascii=False))
            logger.info("Đã gửi phản hồi task_response [%s] về Master.", task_id)

        except Exception as exc:
            logger.error("Lỗi khi mở giao diện Popup trên máy con: %s", exc)
            response_payload = {
                "action": "task_response",
                "task_id": task_id,
                "client_id": self.client_id,
                "status": "issue",
                "error": str(exc),
            }
            await ws.send(json.dumps(response_payload, ensure_ascii=False))

    async def _spawn_visual_overlay(
        self,
        ws: websockets.WebSocketClientProtocol,
        task_id: Optional[str],
        visual_type: str,
        data: Dict[str, Any],
        title: str = "TRỢ LÝ AI LY LY — HUD",
        duration: int = 15,
    ) -> None:
        """
        Phase 32: Spawn standalone VN-MateAI Visual Overlay in its own process.
        Non-blocking, non-stealing-focus Cyberpunk HUD window.
        """
        payload_json = json.dumps({
            "type": visual_type,
            "title": title,
            "data": data,
            "duration": duration,
        }, ensure_ascii=False)

        import tempfile
        try:
            tmp = tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False, encoding="utf-8")
            json.dump({
                "type": visual_type,
                "title": title,
                "data": data,
                "duration": duration,
            }, tmp, ensure_ascii=False)
            tmp.close()

            proc = await asyncio.create_subprocess_exec(
                *_ui_command("overlay_ui.py", tmp.name),
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL,
            )
            logger.info("Visual Overlay HUD [%s] đã kích hoạt hiển thị (PID: %s).", visual_type, proc.pid)

            if task_id:
                response = {
                    "action": "visual_result",
                    "task_id": task_id,
                    "client_id": self.client_id,
                    "type": visual_type,
                    "status": "displayed",
                }
                await ws.send(json.dumps(response, ensure_ascii=False))

        except Exception as exc:
            logger.error("Lỗi khi mở giao diện Visual Overlay trên máy con: %s", exc)
            if task_id:
                response = {
                    "action": "visual_result",
                    "task_id": task_id,
                    "client_id": self.client_id,
                    "type": visual_type,
                    "status": "error",
                    "error": str(exc),
                }
                await ws.send(json.dumps(response, ensure_ascii=False))


def _handle_lifecycle_args() -> bool:
    """Lệnh vòng đời (bản build + gỡ cài): trả True nếu đã xử lý xong, main() dừng."""
    argv = [a for a in sys.argv[1:] if a != "--quiet"]
    if not argv:
        return False
    cmd = argv[0]
    if cmd == "--run-ui" and len(argv) >= 2:
        import runpy
        script = _runtime().bundle_root(_AGENT_ROOT) / Path(argv[1]).name
        sys.argv = [str(script), *argv[2:]]
        runpy.run_path(str(script), run_name="__main__")
        return True
    if cmd == "--finish-update" and len(argv) >= 3:
        _setup_file_log()
        _runtime().finish_update(Path(argv[1]), int(argv[2]))
        return True
    if cmd == "--uninstall-cleanup" and len(argv) >= 3:
        _runtime().uninstall_cleanup(Path(argv[1]), int(argv[2]))
        return True
    if cmd == "--uninstall":
        _runtime().uninstall()
        _runtime().notify_user("VN-MateAI Agent", "Đã gỡ VN-MateAI Agent khỏi máy này.")
        return True
    if cmd == "--install":
        _install_frozen()
        return True
    return False


def _install_frozen() -> None:
    rt = _runtime()
    if not rt.is_frozen():
        rt.notify_user("VN-MateAI Agent", "Bản Python cài bằng install_agent.bat / install_agent_macos.sh.", error=True)
        return
    if not (_DATA_ROOT / "config.json").exists():
        rt.notify_user("VN-MateAI Agent",
                       "Thiếu config.json cạnh VNMateAgent — hãy giải nén ĐỦ gói tải từ Portal rồi chạy lại.",
                       error=True)
        sys.exit(1)
    dest = rt.install(AGENT_VERSION)
    rt.notify_user("VN-MateAI Agent",
                   f"Đã cài VN-MateAI Agent {AGENT_VERSION} vào:\n{dest}\n\n"
                   "Agent chạy nền, tự chạy khi đăng nhập và tự cập nhật.\n"
                   "Gỡ: Settings -> Apps -> VN-MateAI Agent" if os.name == "nt" else
                   f"Đã cài VN-MateAI Agent {AGENT_VERSION} vào {dest} (LaunchAgent {rt.MAC_LABEL}).")


def main() -> None:
    if _handle_lifecycle_args():
        return
    rt = _runtime()
    # Bản build chạy từ thư mục giải nén (chưa cài): tự cài rồi thoát.
    if rt.needs_install():
        _install_frozen()
        return
    rt.cleanup_after_update()

    # Phase 85: fallback cũ là "wss://127.0.0.1:443/ws/client" — cổng 443 không
    # có gì lắng nghe (máy chủ chạy cổng 8000) và `wss://` cần TLS nên không
    # bắt tay được với máy chủ HTTP. Mặc định nay khớp cách khởi chạy thật.
    default_ws = os.getenv("VNMATE_MASTER_URL", "ws://127.0.0.1:8000/ws/client")

    # Zero-Trust: Master Server yêu cầu enrollment token cho /ws/client.
    # Nạp từ config.json cạnh agent nếu có, hoặc từ biến môi trường.
    _cfg: dict = {}
    _cfg_path = _DATA_ROOT / "config.json"
    if _cfg_path.exists():
        try:
            _loaded = json.loads(_cfg_path.read_text(encoding="utf-8"))
            if isinstance(_loaded, dict):
                _cfg = _loaded
        except Exception:
            pass

    # Phase 85: `ws_url` trong config.json thắng biến môi trường khi có.
    # Lý do: config.json do chính máy chủ sinh ra khi bấm "Tải Agent", ghi
    # đúng địa chỉ + cổng đang chạy; biến môi trường có thể là giá trị cũ
    # để sót lại. Trước đây file này bỏ qua `ws_url` nên chạy tay bằng
    # `python client_agent/agent.py` là luôn trỏ về địa chỉ sai.
    _cfg_ws = str(_cfg.get("ws_url", "") or "").strip()
    if _cfg_ws:
        default_ws = _cfg_ws

    _setup_file_log()
    _http_base = str(_cfg.get("server_url", "") or "").strip().rstrip("/")

    # Khoá RIÊNG của máy này (device.json). Chưa có thì đổi mã đăng ký dùng một lần
    # trong config.json lấy khoá. Secret chung cũ (`enrollment_token`) chỉ còn dùng
    # cho worker chạy ngay trên máy chủ.
    _creds = rt.load_credentials(_DATA_ROOT)
    _enroll_code = str(_cfg.get("enroll_code", "") or "").strip()
    if not _creds and _enroll_code:
        if not _http_base:
            logger.error("config.json thiếu server_url — không đăng ký được. Tải lại gói Agent.")
            sys.exit(3)
        _ctx = get_ssl_context() if _http_base.startswith("https") else None
        _delay = 10
        while _creds is None:
            try:
                _res = rt.enroll(_http_base, _enroll_code, _ctx, socket.gethostname(),
                                 f"{platform.system()} {platform.release()} ({platform.machine()})",
                                 rt.package_kind(), AGENT_VERSION)
                rt.save_credentials(_DATA_ROOT, _res["client_id"], _res["device_token"])
                rt.forget_enroll_code(_cfg_path)
                _creds = rt.load_credentials(_DATA_ROOT)
                logger.info("Đã đăng ký với máy chủ: client_id=%s", _res["client_id"])
            except rt.EnrollRejected as exc:
                rt.notify_user("VN-MateAI Agent",
                               f"Máy chủ từ chối mã đăng ký: {exc}\nHãy tải gói Agent MỚI từ Portal cho máy này.",
                               error=True)
                sys.exit(3)
            except Exception as exc:  # noqa: BLE001 — mạng chưa sẵn sàng: thử lại
                logger.warning("Chưa đăng ký được (%s) — thử lại sau %d s.", exc, _delay)
                time.sleep(_delay)
                _delay = min(_delay * 2, 300)

    _enrollment_token = os.getenv("VNMATE_ENROLLMENT_TOKEN", "")
    if _creds:
        _enrollment_token = _creds["device_token"]
    if not _enrollment_token:
        _enrollment_token = str(_cfg.get("enrollment_token", "") or "")
    def _with_token(ws_url: str) -> str:
        """Gắn enrollment token vào URL, tránh gắn hai lần."""
        if not _enrollment_token or "token=" in ws_url:
            return ws_url
        sep = "&" if "?" in ws_url else "?"
        return f"{ws_url}{sep}token={_enrollment_token}"

    default_ws = _with_token(default_ws)

    parser = argparse.ArgumentParser(description="VN-MateAI Client Agent (Worker Node)")
    parser.add_argument(
        "--server", "-s",
        default=default_ws,
        help=f"WebSocket URL của Master Server (mặc định: {_redact_url(default_ws)})",
    )
    # client_id do Master cấp trong config.json lúc tải agent (từ bản
    # client_template, đã gộp ở Phase 6); không có thì biến môi trường / hostname.
    _cfg_id = str(_cfg.get("client_id", "") or "").strip()
    if _cfg_id in ("", "auto_generate_on_first_run"):
        _cfg_id = ""
    default_id = (_creds or {}).get("client_id") or _cfg_id or os.getenv("VNMATE_CLIENT_ID", socket.gethostname())

    parser.add_argument(
        "--id", "-i",
        default=default_id,
        help=f"Định danh Client ID cho máy con (mặc định: {default_id})",
    )
    args, _unknown = parser.parse_known_args()

    # Token phải được gắn vào URL CUỐI CÙNG, không chỉ giá trị mặc định.
    # Trước đây token chỉ nối vào `default_ws`, nên khi ai đó truyền
    # `--server` tường minh (đúng như toggle Worker Node cục bộ làm) thì
    # args.server là URL trần, không token — và Master trả về HTTP 403.
    # Biểu hiện là worker cứ thử lại mãi mà không bao giờ vào được.
    args.server = _with_token(args.server)

    cert_path = get_server_cert_path()
    cert_status = f"✅ Pinned ({cert_path})" if cert_path else "⚠️ Chế độ mặc định (Không có server_cert.pem)"

    print("=" * 60)
    print("  VN-MateAI Client Agent — Secure Worker Node (Phase 29)")
    print(f"  Master Server : {_redact_url(args.server)}")
    print(f"  Client ID     : {args.id}")
    print(f"  SSL/TLS Cert  : {cert_status}")
    print("=" * 60)

    lock_port = int(os.getenv("VNMATE_AGENT_LOCK_PORT", "58431"))
    _instance_lock = _acquire_single_instance(lock_port, wait_s=15)
    if _instance_lock is None:
        logger.error("Agent đã chạy trên máy này (cổng khoá %d đang bận) — thoát.", lock_port)
        sys.exit(2)

    logger.info("VN-MateAI Agent phiên bản %s", AGENT_VERSION)
    agent = ClientAgent(server_url=args.server, client_id=args.id, http_base=_http_base,
                        device_token=(_creds or {}).get("device_token", ""))
    try:
        asyncio.run(agent.run())
    except KeyboardInterrupt:
        logger.info("Client Agent dừng theo yêu cầu người dùng (Ctrl+C).")


def _run_main() -> None:
    # Chạy nền (Task Scheduler / bản .exe): stdout trỏ vào NUL với bảng mã cp1252 —
    # in "✅" / tiếng Việt ném UnicodeEncodeError và Agent chết ngay sau khi đăng ký.
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError, OSError):
            pass
    _setup_file_log()
    try:
        main()
    except SystemExit:
        raise
    except BaseException:  # noqa: BLE001
        # Bản .exe không có console: lỗi không bắt sẽ hiện hộp thoại MODAL của
        # PyInstaller và treo Agent mãi mãi trên máy không người ngồi. Ghi log rồi thoát.
        logger.exception("Agent dừng vì lỗi không mong đợi")
        sys.exit(1)


if __name__ == "__main__":
    _run_main()
