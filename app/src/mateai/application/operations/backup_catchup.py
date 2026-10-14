# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 Dương Thanh Lịch — VN-MateAI. See LICENSE and NOTICE.
"""
mateai/application/operations/backup_catchup.py
===============================================
Sao lưu CHẠY BÙ từ chính máy chủ: khi máy chủ đang chạy (tức CSDL đang lên) mà bản sao lưu mới nhất ở `backups/` cũ hơn
`backup.max_age_hours` (mặc định 26 giờ) → chạy `scripts/backup.py create --push` ở tiến trình con, ghi vào `logs/backup.log`.

Vì sao: tác vụ đặt lịch 02:00 của Windows chỉ chạy khi người dùng đã đăng nhập và cần Docker đang bật; 2026-10-09 → 12 không có bản
nào suốt 85 giờ dù máy chủ có lúc vẫn chạy. Chạy bù không thay tác vụ đặt lịch — hai bên dùng chung khoá `backups/.backup.lock` nên
không bao giờ sao lưu cùng lúc. Tắt bằng `"backup": {"auto_catch_up": false}`.
"""
from __future__ import annotations

import asyncio
import logging
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

CHECK_EVERY_S = 30 * 60
FIRST_CHECK_AFTER_S = 5 * 60
_task: Optional["asyncio.Task[Any]"] = None
_last: Dict[str, Any] = {}


def _cfg() -> Dict[str, Any]:
    from mateai.config.loader import read_raw_config
    return read_raw_config().get("backup") or {}


def _root() -> Path:
    from mateai.config.loader import settings
    return Path(settings.PROJECT_ROOT)


def latest_local_age_hours(now: Optional[float] = None) -> Optional[float]:
    """Tuổi (giờ) của bản sao lưu hoàn chỉnh mới nhất ở `backups/` (thư mục `YYYYMMDD-…` có manifest.json). None = chưa có bản nào."""
    folder = _root() / "backups"
    try:
        ages = [f.stat().st_mtime for f in folder.iterdir() if f.is_dir() and f.name[:8].isdigit() and (f / "manifest.json").is_file()]
    except OSError:
        return None
    if not ages:
        return None
    return ((time.time() if now is None else now) - max(ages)) / 3600


def due(now: Optional[float] = None) -> bool:
    cfg = _cfg()
    if cfg.get("auto_catch_up", True) is False:
        return False
    if not (_root() / "backups").is_dir():
        return False                                  # chưa từng bật sao lưu
    age = latest_local_age_hours(now)
    return age is None or age > float(cfg.get("max_age_hours", 26))


def run_backup() -> int:
    """Chạy đồng bộ một lần sao lưu (tiến trình con). Trả mã thoát."""
    root = _root()
    log = root / "logs" / "backup.log"
    log.parent.mkdir(parents=True, exist_ok=True)
    with open(log, "ab") as out:
        out.write("\n── chạy bù từ máy chủ (bản mới nhất cũ hơn ngưỡng)\n".encode("utf-8"))
        out.flush()
        proc = subprocess.run([sys.executable, str(root / "scripts" / "backup.py"), "create", "--push"], cwd=str(root),
                              stdout=out, stderr=subprocess.STDOUT, timeout=3600)
    _last.update(at=time.time(), rc=proc.returncode)
    logger.info("[BackupCatchUp] Sao lưu chạy bù xong, mã thoát %s", proc.returncode)
    return proc.returncode


async def _loop() -> None:
    await asyncio.sleep(FIRST_CHECK_AFTER_S)
    while True:
        try:
            if due():
                logger.warning("[BackupCatchUp] Bản sao lưu mới nhất cũ hơn ngưỡng — chạy bù.")
                await asyncio.to_thread(run_backup)
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001
            logger.warning("[BackupCatchUp] Lỗi: %s", exc)
        await asyncio.sleep(CHECK_EVERY_S)


def start() -> str:
    global _task
    if _cfg().get("auto_catch_up", True) is False:
        return "tắt (backup.auto_catch_up = false)"
    if _task is None or _task.done():
        _task = asyncio.get_running_loop().create_task(_loop())
    age = latest_local_age_hours()
    return "bật" + (f" — bản mới nhất {age:.0f} giờ trước" if age is not None else " — chưa có bản nào")
