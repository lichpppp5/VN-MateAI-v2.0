# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 Dương Thanh Lịch — VN-MateAI. See LICENSE and NOTICE.
"""
core/autonomous_sentinel.py
===========================
Phase 30 & Phase 43: Autonomous Sentinel Incident Monitor & Xiaozhi Desktop Robot Linkage.

Responsibilities:
  1. Incident Probing:
     - Network Connectivity / hardware: đọc số đo của health_monitor (không tự đo lại).
     - Active Directory Sync: Detects stale AD synchronization, missing employees, or AD schema errors.
     - SQL & Database Health: Detects database lock contention (busy/locked errors), transaction timeouts.
     - Service Failures: Monitors HTTP status (e.g., 503 Service Unavailable, IIS crash).
  2. Multi-Channel Incident Alerting:
     - Telegram Incident Alert: Dispatches rich Markdown alerts to admin Telegram channel.
     - Xiaozhi Desktop Robot Autonomous Wake (Step 4):
       * Actively wakes up the Desktop Robot on the desk.
       * Sets LCD screen to alert face with blinking red eyes: {"type": "ui", "state": "alert", "text": "..."}
       * Voices audible alarm: "Báo cáo anh, hệ thống máy chủ vừa ghi nhận cảnh báo..."
"""

from __future__ import annotations

import asyncio
import logging
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from mateai.config.loader import settings
from mateai.interfaces.websocket.xiaozhi_gateway import xiaozhi_gateway

logger = logging.getLogger("mateai.application.operations.autonomous_sentinel")

# CSDL đồng bộ AD: chủ là domain_sync (một nơi tính đường dẫn, kể cả VNMATEAI_HR_DB_PATH).
from mateai.infrastructure.directory.domain_sync import DEFAULT_DB_PATH as _DB_PATH  # noqa: E402


#: Số đo của health_monitor cũ hơn ngần này giây thì không dùng để báo sự cố.
HEALTH_DATA_MAX_AGE_S = 90.0
#: Bản sao lưu mới nhất cũ hơn mức này (giờ) -> sự cố. Lịch 02:00 hằng ngày + 2 giờ dư.
BACKUP_MAX_AGE_HOURS = 26.0


class AutonomousSentinel:
    """
    Continuous proactive sentinel monitor linking enterprise telemetry with
    Xiaozhi Desktop Robot and Telegram alerting.
    """

    def __init__(self) -> None:
        self._running: bool = False
        self._monitor_task: Optional[asyncio.Task] = None
        self._check_interval: float = 30.0  # seconds between routine scans
        self._last_alert_times: Dict[str, float] = {}
        self._cooldown_seconds: float = 120.0  # Alert cooldown per incident category
        self._active_incidents: Dict[str, Dict[str, Any]] = {}  # category -> incident info
        self._failure_streak: Dict[str, int] = {}  # category -> consecutive failure count

    # -----------------------------------------------------------------------
    # Probing Routines
    # -----------------------------------------------------------------------

    async def check_network_health(self) -> Optional[Dict[str, Any]]:
        """Cổng LLM 9router không phản hồi -> sự cố.

        Đọc kết quả đo của health_monitor (worker 30 s, cùng cách đo: GET
        /models, thử lại 127.0.0.1). Trước đây sentinel tự đo lần thứ hai
        (realtime P6, D6). Chưa có số đo mới thì không kết luận.
        """
        from mateai.application.operations.health_monitor import SYSTEM_HEALTH_CACHE
        llm = (SYSTEM_HEALTH_CACHE.get("services") or {}).get("llm_9router") or {}
        if time.time() - float(llm.get("checked_at") or 0) > HEALTH_DATA_MAX_AGE_S:
            return None
        if llm.get("status") != "FAIL":
            return None
        base_url = getattr(settings.llm, "base_url", "http://localhost:20128/v1")
        detail = str(llm.get("detail") or "")
        if detail.startswith("HTTP "):
            return {
                "category": "network",
                "title": f"9router Gateway Error {detail[5:]}",
                "message": f"Cổng proxy LLM 9router phản hồi mã lỗi {detail[5:]}. Mạng AI có thể bị gián đoạn.",
            }
        return {
            "category": "network",
            "title": "Mất Kết Nối 9router LLM",
            "message": f"Không thể kết nối đến cổng 9router tại {base_url}. Vui lòng kiểm tra dịch vụ mạng AI.",
        }

    def check_ad_sync_health(self) -> Optional[Dict[str, Any]]:
        """Check for Active Directory sync staleness or sync errors in local DB."""
        # Nếu tính năng Đồng Bộ AD đang TẮT, bỏ qua hoàn toàn để không ghi log cảnh báo
        try:
            from mateai.config.loader import get_config_section
            if not get_config_section("ad_sync").get("enabled", False):
                return None
        except Exception:
            pass

        if not _DB_PATH.exists():
            return None

        from mateai.infrastructure.directory.domain_sync import probe_hr_database
        info = probe_hr_database(_DB_PATH, timeout=2.0)
        if info["error"]:
            return {
                "category": "ad_sync",
                "title": "Lỗi Truy Vấn Dữ Liệu AD",
                "message": f"Lỗi truy cập cơ sở dữ liệu nhân sự AD: {info['error'][:60]}.",
            }
        last_sync_raw = info["last_sync"]
        if not info["tables"] or not last_sync_raw:
            # Chưa từng đồng bộ AD (môi trường mới hoặc chưa cấu hình) - không phải sự cố khẩn cấp
            return None
        try:
            dt = datetime.fromisoformat(last_sync_raw.replace("Z", "+00:00"))
        except ValueError:
            return None
        diff_hours = (datetime.now(timezone.utc) - dt).total_seconds() / 3600.0
        if diff_hours > 24.0:
            return {
                "category": "ad_sync",
                "title": "Lỗi Đồng Bộ Active Directory",
                "message": f"Dữ liệu Active Directory đã quá hạn {int(diff_hours)} giờ chưa được đồng bộ lại.",
            }
        return None

    def check_sql_health(self) -> Optional[Dict[str, Any]]:
        """Check SQLite database lock contention and file integrity."""
        if not _DB_PATH.exists():
            return None

        from mateai.infrastructure.database.erp_database import check_sqlite_integrity
        state, detail = check_sqlite_integrity(_DB_PATH, timeout=0.8)
        if state == "corrupt":
            return {
                "category": "sql_deadlock",
                "title": "Lỗi Toàn Vẹn Cơ Sở Dữ Liệu SQL",
                "message": f"PRAGMA quick_check báo lỗi: {detail[:120]}",
            }
        if state == "locked":
            return {
                "category": "sql_deadlock",
                "title": "Kẹt Tiến Trình SQL Database!",
                "message": "Cơ sở dữ liệu SQLite bị khóa (Database Locked/Busy). Đang có tiến trình ghi bị tắc nghẽn.",
            }
        if state == "error" and "locked" not in detail.lower():
            return {
                "category": "sql_deadlock",
                "title": "Lỗi Cơ Sở Dữ Liệu SQL",
                "message": f"Kiểm tra tính toàn vẹn dữ liệu SQLite phát hiện lỗi: {detail[:60]}.",
            }
        return None

    def check_backup_freshness(self, now: Optional[float] = None) -> Optional[Dict[str, Any]]:
        """Sao lưu tự động có thật sự chạy không: bản mới nhất ở `backups/` và ở từng thư mục
        `backup.offsite_dirs` phải mới hơn `backup.max_age_hours` (mặc định 26). Chưa từng sao
        lưu (không có thư mục `backups/`) = chưa bật tính năng, không báo."""
        from mateai.config.loader import read_raw_config
        cfg = read_raw_config().get("backup") or {}
        max_age_h = float(cfg.get("max_age_hours", BACKUP_MAX_AGE_HOURS))
        now = time.time() if now is None else now
        local = Path(settings.PROJECT_ROOT) / "backups"
        if not local.is_dir():
            return None
        places = [("máy chủ", local, lambda f: f.is_dir() and (f / "manifest.json").is_file())]
        for d in cfg.get("offsite_dirs") or []:
            places.append((f"bản sao {d}", Path(str(d)), lambda f: f.is_file() and f.suffix == ".zip"))
        stale = []
        for label, folder, wanted in places:
            try:
                ages = [f.stat().st_mtime for f in folder.iterdir()
                        if wanted(f) and f.name[:8].isdigit()]
            except OSError:
                ages = []
            if not ages:
                stale.append(f"{label}: không có bản nào")
            elif (now - max(ages)) / 3600 > max_age_h:
                stale.append(f"{label}: bản mới nhất {(now - max(ages)) / 3600:.0f} giờ trước")
        if not stale:
            return None
        return {
            "category": "backup_stale",
            "title": "Sao lưu tự động không chạy",
            "message": "; ".join(stale) + f" (ngưỡng {max_age_h:.0f} giờ). Kiểm tra logs/backup.log "
                       "và tác vụ VNMateAI-Backup.",
        }

    def check_main_database(self) -> Optional[Dict[str, Any]]:
        """CSDL CHÍNH (nghiệp vụ, tài khoản, audit) còn truy cập được không — cùng phép thử `/readyz`.
        `check_sql_health` chỉ soi CSDL nhân sự (AD): đêm 2026-10-06 PostgreSQL mất kết nối và
        không có sự cố nào được mở."""
        from mateai.infrastructure.database.erp_database import erp_db
        try:
            erp_db.ping()
            return None
        except Exception as exc:  # noqa: BLE001
            return {
                "category": "database_down",
                "title": "CSDL chính không truy cập được",
                "message": f"Truy vấn kiểm tra thất bại ({type(exc).__name__}). Kiểm tra dịch vụ CSDL "
                           "(Docker / PostgreSQL) — audit và đăng nhập sẽ lỗi cho tới khi khôi phục.",
            }

    def check_hardware_limits(self) -> Optional[Dict[str, Any]]:
        """RAM / ổ đĩa ở mức nguy cấp -> sự cố. Số đo của health_monitor (worker 3 s)."""
        try:
            from mateai.application.operations.health_monitor import SYSTEM_HEALTH_CACHE
            if time.time() - float(SYSTEM_HEALTH_CACHE.get("last_updated") or 0) > HEALTH_DATA_MAX_AGE_S:
                return None
            hw = SYSTEM_HEALTH_CACHE.get("hardware") or {}
            ram = float(hw.get("ram_percent") or 0)
            disk = float(hw.get("disk_percent") or 0)

            if ram > 95.0:
                return {
                    "category": "hardware",
                    "title": "Cảnh Báo Quá Tải RAM (>95%)",
                    "message": f"Bộ nhớ RAM máy chủ đã đạt mức nguy cấp {ram:.1f}%. Hệ thống có nguy cơ bị treo.",
                }
            if disk > 95.0:
                return {
                    "category": "hardware",
                    "title": "Cảnh Báo Đầy Dung Lượng Ổ Đĩa",
                    "message": f"Dung lượng ổ đĩa chính đạt mức báo động {disk:.1f}%. Cần giải phóng bộ nhớ ngay.",
                }
        except Exception:
            pass
        return None

    # -----------------------------------------------------------------------
    # Comprehensive Scan & Alert Trigger
    # -----------------------------------------------------------------------

    async def scan_all(self) -> List[Dict[str, Any]]:
        """Run all probe checks and return list of detected incidents."""
        incidents: List[Dict[str, Any]] = []

        # 1. Network check
        net_inc = await self.check_network_health()
        if net_inc:
            incidents.append(net_inc)

        # 2. AD sync check (sync probe in thread executor)
        loop = asyncio.get_event_loop()
        ad_inc = await loop.run_in_executor(None, self.check_ad_sync_health)
        if ad_inc:
            incidents.append(ad_inc)

        # 3. SQL health check
        sql_inc = await loop.run_in_executor(None, self.check_sql_health)
        if sql_inc:
            incidents.append(sql_inc)

        # 3b. CSDL chính (tối đa 8 giây: hàng đợi kết nối PostgreSQL có thể chờ lâu khi dịch vụ chết)
        try:
            db_inc = await asyncio.wait_for(loop.run_in_executor(None, self.check_main_database), timeout=8.0)
        except asyncio.TimeoutError:
            db_inc = {"category": "database_down", "title": "CSDL chính không phản hồi",
                      "message": "Truy vấn kiểm tra quá 8 giây — CSDL treo hoặc dịch vụ đã dừng."}
        if db_inc:
            incidents.append(db_inc)

        # 4. Hardware limits check
        hw_inc = await loop.run_in_executor(None, self.check_hardware_limits)
        if hw_inc:
            incidents.append(hw_inc)

        # 5. Sao lưu tự động còn chạy
        bk_inc = await loop.run_in_executor(None, self.check_backup_freshness)
        if bk_inc:
            incidents.append(bk_inc)

        return incidents

    async def dispatch_incident(
        self,
        title: str,
        message: str,
        category: str = "general",
        force: bool = False,
    ) -> bool:
        """
        Dispatches detected incident across channels:
          1. Cooldown filtering to prevent alarm fatigue.
          2. Telegram Outbound Alert (if enabled).
          3. Xiaozhi Desktop Robot Autonomous Wake (LCD alert + voice prompt).
          4. Global System Health Cache logging.
        """
        now = time.time()
        last_time = self._last_alert_times.get(category, 0.0)
        if not force and (now - last_time) < self._cooldown_seconds:
            logger.debug("[AutonomousSentinel] Incident '%s' dropped due to cooldown (%.1fs remaining).", category, self._cooldown_seconds - (now - last_time))
            return False

        self._last_alert_times[category] = now
        # Sổ tác vụ: sự cố thành tác vụ `incident` (ưu tiên tất định, có bằng chứng,
        # trạng thái ESCALATED — người xử lý). Sentinel KHÔNG tự khắc phục (§156–§159).
        try:
            from mateai.application.tasks import ledger
            await asyncio.to_thread(ledger.open_incident, category, title, message, "critical")
        except Exception as exc:  # noqa: BLE001 — sổ hỏng không chặn cảnh báo
            logger.warning("[AutonomousSentinel] Không ghi được sự cố vào sổ tác vụ: %s", exc)
        from mateai.application.operations.topology_events import emit
        emit("incident", stage="alert", source="sentinel", target="alerts", status="error",
             detail=f"[{category}] {title}")
        logger.warning("[AutonomousSentinel] PHÁT HIỆN SỰ CỐ [%s]: %s — %s", category, title, message)

        # 1. Khâu cảnh báo chung: Telegram + Teams + Email + Outlook + Slack + Webhook
        #    (kênh nào đã kết nối). Trước đây chỉ Telegram, và gửi cú pháp Markdown
        #    vào kênh HTML nên tin hiện nguyên dấu `*`.
        from mateai.application.operations import alert_dispatcher
        await alert_dispatcher.dispatch(title, message, severity="critical", category=f"sentinel:{category}",
                                        source="Autonomous Sentinel", force=force)

        # 2. Step 4: Wake Xiaozhi Desktop Robot with blinking red LCD and voice (async non-blocking)
        try:
            asyncio.create_task(
                xiaozhi_gateway.wake_and_alert(error_title=title, detail_message=message)
            )
        except Exception as xz_err:
            logger.error("[AutonomousSentinel] Xiaozhi Desktop Robot wake error: %s", xz_err)

        # 3. Log to recent events in SYSTEM_HEALTH_CACHE
        try:
            from mateai.application.operations.health_monitor import SYSTEM_HEALTH_CACHE
            SYSTEM_HEALTH_CACHE.setdefault("recent_events", []).insert(0, {
                "time": datetime.now().strftime("%H:%M:%S"),
                "action": f"SENTINEL_{category.upper()}",
                "level": "error",
                "message": f"{title}: {message}"[:120],
            })
            if len(SYSTEM_HEALTH_CACHE["recent_events"]) > 20:
                SYSTEM_HEALTH_CACHE["recent_events"] = SYSTEM_HEALTH_CACHE["recent_events"][:20]
        except Exception:
            pass

        return True

    async def dispatch_resolution(self, category: str, incident: Dict[str, Any]) -> None:
        """
        Dispatches recovery resolution message when an incident has healed:
          1. Broadcast Telegram Resolved Notice.
          2. Log recovery event in SYSTEM_HEALTH_CACHE.
        """
        raw_title = incident.get("title", category)
        from mateai.application.operations.topology_events import emit
        emit("incident", stage="resolved", source="sentinel", target="alerts", status="ok",
             detail=f"[{category}] đã khôi phục: {raw_title}")
        res_title = f"Khôi Phục Kết Nối: {raw_title}"
        res_msg = f"Sự cố [{category}] đã tự động được khôi phục thành công. Dịch vụ AI & Mạng đã trực tuyến và phản hồi bình thường."
        logger.info("[AutonomousSentinel] SỰ CỐ ĐÃ KHÔI PHỤC [%s]: %s", category, res_title)

        from mateai.application.operations import alert_dispatcher
        await alert_dispatcher.dispatch(res_title, res_msg, category=f"sentinel:{category}",
                                        source="Autonomous Sentinel", resolved=True)
        # Sổ tác vụ (§88): đóng sự cố bằng lần đo vừa rồi — trước đây sự cố đã khôi phục vẫn mở mãi.
        try:
            from mateai.application.tasks import ledger
            await asyncio.to_thread(ledger.resolve_incident_by_probe, category,
                                    f"probe '{category}' không còn phát hiện lỗi lúc {datetime.now():%H:%M:%S}")
        except Exception as exc:  # noqa: BLE001
            logger.warning("[AutonomousSentinel] Không đóng được sự cố trong sổ: %s", exc)

        try:
            from mateai.application.operations.health_monitor import SYSTEM_HEALTH_CACHE
            SYSTEM_HEALTH_CACHE.setdefault("recent_events", []).insert(0, {
                "time": datetime.now().strftime("%H:%M:%S"),
                "action": f"SENTINEL_{category.upper()}_RESOLVED",
                "level": "info",
                "message": f"RESOLVED: {res_title}"[:120],
            })
            if len(SYSTEM_HEALTH_CACHE["recent_events"]) > 20:
                SYSTEM_HEALTH_CACHE["recent_events"] = SYSTEM_HEALTH_CACHE["recent_events"][:20]
        except Exception:
            pass

    # -----------------------------------------------------------------------
    # Background Worker Loop
    # -----------------------------------------------------------------------

    async def _sentinel_loop(self) -> None:
        """
        Background async loop executing routine incident scans.
        Requires 2 consecutive failed scans before raising an alarm,
        and automatically sends a recovery notice once the issue is resolved.
        """
        logger.info("[AutonomousSentinel] Background incident monitor started (interval=%.1fs).", self._check_interval)
        while self._running:
            try:
                incidents = await self.scan_all()
                detected_cats = {inc.get("category", "general") for inc in incidents}

                # 1. Process active incidents with consecutive failure confirmation
                for inc in incidents:
                    cat = inc.get("category", "general")
                    self._failure_streak[cat] = self._failure_streak.get(cat, 0) + 1

                    # Only alert if confirmed failed for at least 2 consecutive cycles (prevents momentary blips)
                    if self._failure_streak[cat] >= 2:
                        self._active_incidents[cat] = inc
                        await self.dispatch_incident(
                            title=inc["title"],
                            message=inc["message"],
                            category=cat,
                        )

                # 2. Check for resolved incidents
                for cat in list(self._active_incidents.keys()):
                    if cat not in detected_cats:
                        prev_inc = self._active_incidents.pop(cat)
                        self._failure_streak[cat] = 0
                        await self.dispatch_resolution(category=cat, incident=prev_inc)

                # 3. Reset streak for categories that passed this cycle
                for cat in list(self._failure_streak.keys()):
                    if cat not in detected_cats and cat not in self._active_incidents:
                        self._failure_streak[cat] = 0

            except Exception as exc:
                logger.error("[AutonomousSentinel] Error in sentinel monitoring loop: %s", exc)

            await asyncio.sleep(self._check_interval)

    def start(self) -> None:
        """Start Sentinel monitor in background asyncio task."""
        if self._running:
            return
        self._running = True
        try:
            loop = asyncio.get_event_loop()
            if loop.is_running():
                self._monitor_task = loop.create_task(self._sentinel_loop())
        except Exception as exc:
            logger.warning("[AutonomousSentinel] Could not start background task: %s", exc)

    def stop(self) -> None:
        """Stop Sentinel monitor."""
        self._running = False
        if self._monitor_task and not self._monitor_task.done():
            self._monitor_task.cancel()
            self._monitor_task = None
        logger.info("[AutonomousSentinel] Stopped background monitor.")


# ---------------------------------------------------------------------------
# Global Singleton Instance
# ---------------------------------------------------------------------------
autonomous_sentinel = AutonomousSentinel()
