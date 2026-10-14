# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 Dương Thanh Lịch — VN-MateAI. See LICENSE and NOTICE.
"""
core/health_monitor.py
======================
Phase 24.5: Zero-Overhead Observability Engine for VN-MateAI.

Architecture:
  - SYSTEM_HEALTH_CACHE: Global dictionary in RAM (O(1) lookup).
  - Background Workers via asyncio:
      * Worker 1 (Hardware, 3s): CPU, RAM, Disk via psutil, Uptime.
      * Worker 2 (Local DB/Agent, 10s): SQLite size & health, Active Directory sync, Recent events.
      * Worker 3 (External APIs, 30s): Async ping 9router base_url (latency measurement), Telegram Bot status.
  - Zero-Overhead API: GET /api/v1/health-dashboard returns SYSTEM_HEALTH_CACHE directly (< 1ms).
"""

from __future__ import annotations

import asyncio
import logging
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import httpx
import psutil

from mateai.config.loader import settings

logger = logging.getLogger(__name__)

# Resolve database path

# CSDL đồng bộ AD: chủ là domain_sync (một nơi tính đường dẫn, kể cả VNMATEAI_HR_DB_PATH).
from mateai.infrastructure.directory.domain_sync import DEFAULT_DB_PATH as _DB_PATH  # noqa: E402
_START_TIME = time.time()

# ═══════════════════════════════════════════════════════════════════════════
# BƯỚC 1: KHỞI TẠO GLOBAL CACHE (SYSTEM_HEALTH_CACHE)
# ═══════════════════════════════════════════════════════════════════════════
SYSTEM_HEALTH_CACHE: Dict[str, Any] = {
    "status": "healthy",
    "timestamp": datetime.now().isoformat(),
    "hardware": {
        "cpu_percent": 0.0,
        "ram_percent": 0.0,
        "disk_percent": 0.0,
        # Phase 46: Extended metrics
        "cpu_cores": 1,
        "cpu_freq_mhz": 0.0,
        "ram_used_gb": 0.0,
        "ram_total_gb": 0.0,
        "swap_percent": 0.0,
        "disk_free_gb": 0.0,
        "disk_total_gb": 0.0,
        "process_count": 0,
        "net_sent_mbps": 0.0,
        "net_recv_mbps": 0.0,
        # Sparkline history (last 20 samples, 3s interval = 60s window)
        "cpu_history": [],
        "ram_history": [],
    },
    "services": {
        "llm_9router": {
            "status": "UNKNOWN",
            "latency": 0.0,
            "latency_ms": 0.0,
            "model": "",
            "detail": "Đang khởi tạo...",
        },
        "active_directory": {
            "status": "UNKNOWN",
            "last_sync": "Đang kiểm tra...",
            "employees_count": 0,
            "computers_count": 0,
            "detail": "Đang kiểm tra...",
        },
        "telegram_gateway": {
            "status": "UNKNOWN",
            "detail": "Đang kiểm tra...",
        },
        "database_sqlite": {
            "status": "UNKNOWN",
            "size_kb": 0.0,
            "detail": "Đang kiểm tra...",
        },
    },
    "nodes": {
        "active_web_clients": 0,
        "active_audio_hardware": 0,
        "skills_count": 0,
        "skills_enabled": 0,
        "uptime_seconds": 0,
        "uptime_human": "0s",
    },
    "live_events": [],
    "recent_events": [],
    "last_updated": 0.0,
}


def _format_uptime(seconds: int) -> str:
    """Format uptime seconds into friendly string."""
    hrs, rem = divmod(seconds, 3600)
    mins, secs = divmod(rem, 60)
    if hrs > 0:
        return f"{hrs}h {mins}m"
    if mins > 0:
        return f"{mins}m {secs}s"
    return f"{secs}s"


def _format_relative_time(raw_dt: Optional[str]) -> str:
    """Convert ISO timestamp string to friendly Vietnamese relative time."""
    if not raw_dt:
        return "Chưa đồng bộ"
    try:
        dt = datetime.fromisoformat(raw_dt.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            diff_sec = (datetime.now() - dt).total_seconds()
        else:
            diff_sec = (datetime.now(timezone.utc) - dt).total_seconds()

        if diff_sec < 60:
            return "Vừa xong"
        if diff_sec < 3600:
            return f"{int(diff_sec // 60)} phút trước"
        if diff_sec < 86400:
            return f"{int(diff_sec // 3600)} giờ trước"
        return f"{int(diff_sec // 86400)} ngày trước"
    except Exception:
        return str(raw_dt)[:16]


def _check_db_and_ad() -> Tuple[bool, Dict[str, Any]]:
    """Synchronous probe for SQLite and AD data, designed to run in executor."""
    db_ok = False
    ad_data: Dict[str, Any] = {
        "status": "UNKNOWN",
        "last_sync": "Chưa có dữ liệu",
        "employees_count": 0,
        "computers_count": 0,
        "detail": "Chưa có dữ liệu",
    }

    from mateai.infrastructure.directory.domain_sync import probe_hr_database
    info = probe_hr_database(_DB_PATH, timeout=1.5)
    if info["error"]:
        ad_data = {
            "status": "FAIL",
            "last_sync": "Lỗi kết nối",
            "employees_count": 0,
            "computers_count": 0,
            "detail": info["error"][:60],
        }
    else:
        db_ok = info["reachable"]
        if info["tables"]:
            human_ago = _format_relative_time(info["last_sync"])
            ad_data = {
                "status": "OK",
                "last_sync": human_ago,
                "employees_count": info["employees"],
                "computers_count": info["computers"],
                "detail": f"Đồng bộ: {human_ago} · {info['employees']} NV",
            }
        else:
            ad_data = {
                "status": "OK",
                "last_sync": "Chưa có bảng dữ liệu",
                "employees_count": 0,
                "computers_count": 0,
                "detail": "Chưa đồng bộ AD",
            }

    return db_ok, ad_data


def _check_main_database() -> Dict[str, Any]:
    """CSDL CHÍNH (nghiệp vụ, tài khoản, audit) — cùng phép thử với `/readyz`, nói rõ backend.

    Trước đây ô "CSDL" của dashboard đo tệp `hr_kpi.db` (CSDL đồng bộ AD) và ghi "SQLite · N KB":
    sau khi chuyển sang PostgreSQL nó vẫn xanh, kể cả khi CSDL chính hỏng.
    """
    from mateai.infrastructure.database.erp_database import erp_db
    return erp_db.probe()


def _format_db_size(size_kb: float) -> str:
    return f"{size_kb / 1024:.1f} MB" if size_kb >= 1024 else f"{size_kb} KB"


def _database_service_entry(main_db: Dict[str, Any]) -> Dict[str, Any]:
    """Mục `services.database_sqlite` (tên khoá giữ cho sentinel / topology / portal) từ kết quả
    `_check_main_database`. Báo FAIL khi CSDL CHÍNH hỏng — không phụ thuộc CSDL nhân sự."""
    label = {"postgresql": "PostgreSQL", "sqlite": "SQLite"}.get(main_db.get("backend"), "CSDL")
    ok = bool(main_db.get("ok"))
    return {
        "status": "OK" if ok else "FAIL",
        "backend": main_db.get("backend", "unknown"),
        "size_kb": main_db.get("size_kb", 0.0),
        "detail": (f"{label} · {_format_db_size(main_db.get('size_kb', 0.0))}" if ok
                   else f"CSDL chính lỗi: {main_db.get('error', '')}"),
    }


def _get_recent_audit_events(limit: int = 8) -> List[Dict[str, Any]]:
    """Synchronous retrieval of recent audit events for live event log."""
    events: List[Dict[str, Any]] = []
    try:
        from mateai.application.security.safety_guard import security_engine
        raw_logs = security_engine.get_recent_audit_logs(limit=limit)
        for item in raw_logs:
            tier = item.get("tier", "SAFE")
            level = "error" if tier == "BLOCKED" else "warning" if tier == "RESTRICTED" else "info"
            events.append({
                "time": item.get("timestamp", datetime.now().strftime("%H:%M:%S")),
                "action": item.get("action", "unknown"),
                "level": level,
                "message": f"[{tier}] {item.get('action', '')}: {item.get('details', '')}"[:100],
            })
    except Exception:
        pass

    if not events:
        events.append({
            "time": datetime.now().strftime("%H:%M:%S"),
            "action": "SYSTEM_OBSERVABILITY",
            "level": "info",
            "message": "Zero-Overhead Observability Engine đang hoạt động ổn định.",
        })
    return events


# ═══════════════════════════════════════════════════════════════════════════
# BƯỚC 2: CÁC LUỒNG KIỂM TRA NGẦM (BACKGROUND ASYNC WORKERS)
# ═══════════════════════════════════════════════════════════════════════════

# Network I/O baseline for delta calculation (MB/s)
_prev_net_io: Optional[Any] = None
_prev_net_time: float = 0.0
_SPARKLINE_MAX = 20  # Keep last 20 samples (20 × 3s = 60s window)


async def _hardware_worker(interval: float = 3.0) -> None:
    """
    Worker 1 (Phần cứng - Chạy mỗi 3 giây):
    Lấy thông số CPU, RAM, Disk, Swap, Network I/O qua psutil.
    Phase 46: Bổ sung cpu_cores, cpu_freq, swap, process_count, net MB/s,
              disk_free_gb, và sparkline history 60s.
    """
    global _prev_net_io, _prev_net_time
    logger.info("[HealthWorker-1] Hardware worker started (interval=%ss).", interval)
    while True:
        try:
            # CPU
            cpu = psutil.cpu_percent(interval=None)
            cpu_cores = psutil.cpu_count(logical=True) or 1
            try:
                freq = psutil.cpu_freq()
                cpu_freq_mhz = round(freq.current, 0) if freq else 0.0
            except Exception:
                cpu_freq_mhz = 0.0

            # RAM
            vm = psutil.virtual_memory()
            ram = vm.percent
            ram_used_gb = round(vm.used / (1024 ** 3), 2)
            ram_total_gb = round(vm.total / (1024 ** 3), 2)

            # Swap
            try:
                swap = psutil.swap_memory().percent
            except Exception:
                swap = 0.0

            # Disk
            disk_root = psutil.disk_usage(os.path.abspath(os.sep))
            disk_percent = round(disk_root.percent, 1)
            disk_free_gb = round(disk_root.free / (1024 ** 3), 2)
            disk_total_gb = round(disk_root.total / (1024 ** 3), 2)

            # Process count
            try:
                process_count = len(psutil.pids())
            except Exception:
                process_count = 0

            # Network I/O delta (MB/s)
            net_sent_mbps = 0.0
            net_recv_mbps = 0.0
            try:
                net_now = psutil.net_io_counters()
                now_t = time.monotonic()
                if _prev_net_io is not None:
                    dt = max(now_t - _prev_net_time, 0.001)
                    net_sent_mbps = round((net_now.bytes_sent - _prev_net_io.bytes_sent) / dt / 1024 / 1024, 3)
                    net_recv_mbps = round((net_now.bytes_recv - _prev_net_io.bytes_recv) / dt / 1024 / 1024, 3)
                _prev_net_io = net_now
                _prev_net_time = now_t
            except Exception:
                pass

            # Update SYSTEM_HEALTH_CACHE
            hw = SYSTEM_HEALTH_CACHE["hardware"]
            hw["cpu_percent"] = round(cpu, 1)
            hw["cpu_cores"] = cpu_cores
            hw["cpu_freq_mhz"] = cpu_freq_mhz
            hw["ram_percent"] = round(ram, 1)
            hw["ram_used_gb"] = ram_used_gb
            hw["ram_total_gb"] = ram_total_gb
            hw["swap_percent"] = round(swap, 1)
            hw["disk_percent"] = disk_percent
            hw["disk_free_gb"] = disk_free_gb
            hw["disk_total_gb"] = disk_total_gb
            hw["process_count"] = process_count
            hw["net_sent_mbps"] = net_sent_mbps
            hw["net_recv_mbps"] = net_recv_mbps

            # Sparkline history (rolling window)
            cpu_hist = hw.get("cpu_history", [])
            cpu_hist.append(round(cpu, 1))
            hw["cpu_history"] = cpu_hist[-_SPARKLINE_MAX:]

            ram_hist = hw.get("ram_history", [])
            ram_hist.append(round(ram, 1))
            hw["ram_history"] = ram_hist[-_SPARKLINE_MAX:]

            # Update uptime & timestamp
            up_sec = round(time.time() - _START_TIME)
            SYSTEM_HEALTH_CACHE["nodes"]["uptime_seconds"] = up_sec
            SYSTEM_HEALTH_CACHE["nodes"]["uptime_human"] = _format_uptime(up_sec)
            SYSTEM_HEALTH_CACHE["timestamp"] = datetime.now().isoformat()
            SYSTEM_HEALTH_CACHE["last_updated"] = time.time()
        except Exception as exc:
            logger.warning("[HealthWorker-1] Error sampling hardware: %s", exc)

        await asyncio.sleep(interval)


async def _local_db_worker(interval: float = 10.0) -> None:
    """
    Worker 2 (Local DB/Agent - Chạy mỗi 10 giây):
    Đếm dung lượng SQLite, kiểm tra kết nối DB, kiểm tra đồng bộ Active Directory.
    Ghi vào SYSTEM_HEALTH_CACHE.
    """
    logger.info("[HealthWorker-2] Local DB/AD worker started (interval=%ss).", interval)
    while True:
        try:
            # Execute DB query in worker thread to prevent event-loop latency spikes
            loop = asyncio.get_running_loop()
            _hr_ok, ad_data = await loop.run_in_executor(None, _check_db_and_ad)
            main_db = await loop.run_in_executor(None, _check_main_database)

            # Khoá `database_sqlite` giữ tên cũ (sentinel / topology / portal đọc nó) nhưng là CSDL
            # CHÍNH, kèm `backend`. CSDL nhân sự (AD) được báo riêng ở `active_directory`.
            SYSTEM_HEALTH_CACHE["services"]["database_sqlite"] = _database_service_entry(main_db)
            SYSTEM_HEALTH_CACHE["services"]["active_directory"] = ad_data

            # Fetch recent audit events for realtime log panel
            recent_logs = await loop.run_in_executor(None, _get_recent_audit_events)
            SYSTEM_HEALTH_CACHE["live_events"] = recent_logs
            SYSTEM_HEALTH_CACHE["recent_events"] = recent_logs

            # Sample registered skills telemetry
            try:
                from core.plugin_manager import plugin_manager
                SYSTEM_HEALTH_CACHE["nodes"]["skills_count"] = plugin_manager.get_skill_count()
                SYSTEM_HEALTH_CACHE["nodes"]["skills_enabled"] = len(plugin_manager.get_all_tools())
            except Exception:
                pass
        except Exception as exc:
            logger.warning("[HealthWorker-2] Error sampling DB/AD: %s", exc)

        await asyncio.sleep(interval)


async def _external_api_worker(interval: float = 30.0) -> None:
    """
    Worker 3 (External APIs - Chạy mỗi 30 - 60 giây):
    Thực hiện lệnh HTTP GET/Ping tới 9router base_url để đo độ trễ (latency).
    Thực hiện gọi API lấy trạng thái Telegram Bot.
    Dùng block try/except với timeout ngắn (2.0s) để không bao giờ bị treo worker.
    """
    logger.info("[HealthWorker-3] External API worker started (interval=%ss).", interval)
    while True:
        try:
            cfg_llm = getattr(settings, "llm", None)
            base_url = (
                getattr(cfg_llm, "base_url", "http://localhost:20128/v1")
                if cfg_llm
                else "http://localhost:20128/v1"
            ).rstrip("/")
            models_url = f"{base_url}/models"
            model_name = getattr(cfg_llm, "model_name", "") or ""

            # 1. Ping 9router with resilient timeout & 127.0.0.1 fallback
            llm_result: Dict[str, Any] = {
                "status": "FAIL",
                "latency": 0.0,
                "latency_ms": 0.0,
                "model": model_name,
                "detail": "Đang kết nối...",
            }
            urls_to_ping = [models_url]
            if "localhost" in models_url:
                urls_to_ping.append(models_url.replace("localhost", "127.0.0.1"))

            t0 = time.perf_counter()
            ping_success = False
            for target_url in urls_to_ping:
                try:
                    async with httpx.AsyncClient(timeout=4.0) as client:
                        resp = await client.get(target_url)
                        lat = round((time.perf_counter() - t0) * 1000, 1)
                        if resp.status_code in (200, 401, 403):
                            llm_result = {
                                "status": "OK",
                                "latency": lat,
                                "latency_ms": lat,
                                "model": model_name,
                                "detail": f"Model: {model_name} ({lat}ms)",
                            }
                            ping_success = True
                            break
                        else:
                            llm_result = {
                                "status": "FAIL",
                                "latency": lat,
                                "latency_ms": lat,
                                "model": model_name,
                                "detail": f"HTTP {resp.status_code}",
                            }
                except Exception as net_exc:
                    lat = round((time.perf_counter() - t0) * 1000, 1)
                    llm_result = {
                        "status": "FAIL",
                        "latency": lat,
                        "latency_ms": lat,
                        "model": model_name,
                        "detail": str(net_exc)[:50],
                    }

            llm_result["checked_at"] = time.time()  # autonomous_sentinel đọc độ mới
            SYSTEM_HEALTH_CACHE["services"]["llm_9router"] = llm_result

            # 2. Check Telegram Bot Gateway status
            try:
                from mateai.interfaces.telegram.telegram_gateway import telegram_gateway
                is_running = getattr(telegram_gateway, "is_running", False)
                if is_running:
                    SYSTEM_HEALTH_CACHE["services"]["telegram_gateway"] = {
                        "status": "OK",
                        "message": "Gateway trực tuyến",
                        "detail": "Telegram Bot Gateway đang chạy",
                    }
                else:
                    token = getattr(getattr(settings, "telegram", None), "bot_token", "")
                    detail = "Chưa cấu hình Bot Token" if not token else "Gateway đang dừng"
                    SYSTEM_HEALTH_CACHE["services"]["telegram_gateway"] = {
                        "status": "FAIL",
                        "detail": detail,
                    }
            except Exception as tg_err:
                SYSTEM_HEALTH_CACHE["services"]["telegram_gateway"] = {
                    "status": "FAIL",
                    "detail": str(tg_err)[:50],
                }

            # 3. Overall health status
            all_ok = all(
                s.get("status") == "OK"
                for s in SYSTEM_HEALTH_CACHE["services"].values()
            )
            SYSTEM_HEALTH_CACHE["status"] = "healthy" if all_ok else "degraded"
        except Exception as exc:
            logger.warning("[HealthWorker-3] Error checking external APIs: %s", exc)

        await asyncio.sleep(interval)


# ═══════════════════════════════════════════════════════════════════════════
# BƯỚC 3: KÍCH HOẠT WORKERS
# ═══════════════════════════════════════════════════════════════════════════

_workers_started = False

async def start_observability_workers() -> None:
    """
    Launch 3 background workers concurrently via asyncio.create_task().
    Ensures zero blocking on AI or request processing loops.
    """
    global _workers_started
    if _workers_started:
        return
    _workers_started = True

    logger.info("[HealthMonitor] Initializing Phase 24.5 Zero-Overhead Observability async workers...")
    asyncio.create_task(_hardware_worker(interval=3.0))
    asyncio.create_task(_local_db_worker(interval=10.0))
    asyncio.create_task(_external_api_worker(interval=30.0))


class SystemHealthMonitor:
    """
    Backward-compatibility wrapper class.
    Provides get_system_health() reading directly from SYSTEM_HEALTH_CACHE in O(1).
    """
    def start(self) -> None:
        """Deprecated synchronous startup — workers are launched via start_observability_workers()."""
        pass

    def get_system_health(
        self,
        active_web_clients: int = 0,
        active_audio_hardware: int = 0,
    ) -> Dict[str, Any]:
        """Instant O(1) read from SYSTEM_HEALTH_CACHE."""
        SYSTEM_HEALTH_CACHE["nodes"]["active_web_clients"] = max(0, active_web_clients)
        SYSTEM_HEALTH_CACHE["nodes"]["active_audio_hardware"] = max(0, active_audio_hardware)
        return SYSTEM_HEALTH_CACHE


health_monitor = SystemHealthMonitor()
