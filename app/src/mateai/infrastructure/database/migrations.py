# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 Dương Thanh Lịch — VN-MateAI. See LICENSE and NOTICE.
"""
mateai/infrastructure/database/migrations.py
============================================
Phiên bản cấu trúc CSDL (2026-10-14) — cho máy chủ đã có dữ liệu nâng cấp an toàn lên bản mới.

Hai tầng, mỗi tầng một việc:
  1. `init_db()` (db_manager / erp_database) — "đảm bảo": CREATE TABLE IF NOT EXISTS + thêm cột còn thiếu, chạy mỗi lần
     khởi động. Đủ cho thay đổi THÊM (bảng / cột / chỉ mục mới) — tiếp tục viết ở đó như trước.
  2. Ở ĐÂY — bước có số thứ tự, mỗi bước chạy ĐÚNG MỘT LẦN, ghi vào bảng `schema_migrations`. Dùng cho thay đổi mà
     "thêm cột" không làm được: đổi tên / đổi kiểu cột, chuyển hoặc sửa dữ liệu, xoá cột cũ.

Khi có bước chưa chạy (CSDL đã có dữ liệu): SAO LƯU trước (scripts/backup.py — chụp PostgreSQL nhất quán), lỗi sao lưu thì
KHÔNG nâng cấp. Mỗi bước một transaction: lỗi giữa chừng thì cuộn lại, các bước sau không chạy, /readyz báo `schema`.
CSDL có bước mà mã này không biết (chạy bản cũ trên CSDL đã nâng cấp) → `newer_than_app`: /readyz báo lỗi, không ghi.

Thêm bước mới: nối `Migration("0002", "mô tả", hàm)` vào cuối `MIGRATIONS` — hàm nhận `cursor` (SQL viết kiểu SQLite,
pg_compat dịch sang PostgreSQL như phần còn lại của repo), không tự commit. KHÔNG sửa / xoá / đánh lại số bước đã phát hành.
"""
from __future__ import annotations

import logging
import threading
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class Migration:
    version: str
    description: str
    apply: Callable[[Any], None]


def _baseline(cursor: Any) -> None:
    """Cấu trúc do init_db() tạo ở bản 1.0 — không đổi gì, chỉ đánh dấu điểm xuất phát."""


#: Theo thứ tự. Bước đã phát hành là bất biến.
MIGRATIONS: List[Migration] = [
    Migration("0001", "baseline: cấu trúc bản 1.0 (init_db)", _baseline),
]

TABLE_DDL = """
CREATE TABLE IF NOT EXISTS schema_migrations (
    version TEXT PRIMARY KEY,
    description TEXT NOT NULL,
    applied_at TEXT NOT NULL,
    app_version TEXT
);
"""

#: Kết quả lần chạy gần nhất (cho /readyz) — "ok" | "pending: …" | "newer_than_app: …" | "error: …".
_STATE: Dict[str, Any] = {"status": "not_run", "detail": ""}
_LOCK = threading.Lock()


def app_version() -> str:
    """mateai.__version__ (= version trong pyproject.toml — test_version_is_single_source giữ hai chỗ khớp)."""
    from mateai import __version__
    return __version__


def _known() -> List[str]:
    return [m.version for m in MIGRATIONS]


def applied_versions(conn: Any) -> List[str]:
    cur = conn.cursor()
    cur.execute(TABLE_DDL)
    return sorted(str(r[0]) for r in cur.execute("SELECT version FROM schema_migrations;").fetchall())


def status(conn: Any) -> Dict[str, Any]:
    applied = applied_versions(conn)
    known = _known()
    unknown = [v for v in applied if v not in known]
    pending = [v for v in known if v not in applied]
    return {"applied": applied, "pending": pending, "unknown": unknown,
            "current": applied[-1] if applied else None, "latest": known[-1] if known else None}


def _has_user_data(conn: Any) -> bool:
    try:
        return bool(conn.execute("SELECT COUNT(*) FROM users;").fetchone()[0])
    except Exception:  # noqa: BLE001
        return False


def _default_backup() -> str:
    """Sao lưu đầy đủ trước khi nâng cấp (cùng công cụ với lịch 02:00)."""
    import importlib.util
    from mateai.config.loader import settings
    root = Path(settings.PROJECT_ROOT)
    spec = importlib.util.spec_from_file_location("_vnmate_backup", root / "scripts" / "backup.py")
    if spec is None or spec.loader is None:
        raise RuntimeError("không tìm thấy scripts/backup.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return str(mod.create(root))


def migrate(connect: Callable[[], Any], *, backup: Optional[Callable[[], str]] = _default_backup) -> Dict[str, Any]:
    """Chạy các bước chưa chạy. `connect()` trả kết nối dùng được với `with` (commit khi thoát khối, rollback khi lỗi)."""
    with _LOCK:
        try:
            with connect() as conn:
                st = status(conn)
                fresh = not _has_user_data(conn)
            if st["unknown"]:
                msg = (f"CSDL đã nâng cấp tới {st['unknown'][-1]} — bản ứng dụng này ({app_version()}) chỉ biết tới "
                       f"{st['latest']}. Chạy bản mới hơn hoặc khôi phục bản sao lưu trước khi nâng cấp.")
                logger.critical("[Schema] %s", msg)
                _STATE.update(status=f"newer_than_app: {st['unknown'][-1]}", detail=msg)
                return {**st, "ran": [], "backup": None}
            ran: List[str] = []
            backup_path = None
            # Bước thật (không phải baseline) trên CSDL đang có dữ liệu → sao lưu trước.
            real = [v for v in st["pending"] if v != "0001"]
            if real and not fresh and backup is not None:
                try:
                    backup_path = backup()
                    logger.info("[Schema] Đã sao lưu trước khi nâng cấp: %s", backup_path)
                except Exception as exc:  # noqa: BLE001
                    msg = f"sao lưu trước nâng cấp lỗi ({exc}) — KHÔNG nâng cấp"
                    logger.critical("[Schema] %s", msg)
                    _STATE.update(status=f"error: {msg}", detail=msg)
                    return {**st, "ran": [], "backup": None}
            for m in MIGRATIONS:
                if m.version not in st["pending"]:
                    continue
                with connect() as conn:                       # một transaction mỗi bước
                    cur = conn.cursor()
                    m.apply(cur)
                    cur.execute("INSERT INTO schema_migrations (version, description, applied_at, app_version) "
                                "VALUES (?, ?, ?, ?);",
                                (m.version, m.description, datetime.now().strftime("%Y-%m-%d %H:%M:%S"), app_version()))
                ran.append(m.version)
                logger.info("[Schema] Đã chạy bước %s: %s", m.version, m.description)
            _STATE.update(status="ok", detail=f"phiên bản {st['latest']}")
            return {**st, "pending": [], "ran": ran, "backup": backup_path,
                    "applied": sorted(set(st["applied"]) | set(ran)), "current": st["latest"]}
        except Exception as exc:  # noqa: BLE001
            logger.critical("[Schema] Nâng cấp CSDL lỗi: %s", exc, exc_info=True)
            _STATE.update(status=f"error: {type(exc).__name__}: {exc}", detail=str(exc))
            raise


def readiness(connect: Optional[Callable[[], Any]] = None) -> str:
    """Cho /readyz: "ok" khi CSDL khớp mã; còn lại là lý do. Chưa chạy migrate() thì đọc trạng thái (không ghi gì)."""
    if _STATE["status"] != "not_run":
        return "ok" if _STATE["status"] == "ok" else str(_STATE["status"])
    try:
        with (connect or main_db_connect)() as conn:
            st = status(conn)
    except Exception as exc:  # noqa: BLE001
        return f"error: {type(exc).__name__}"
    if st["unknown"]:
        return f"newer_than_app: {st['unknown'][-1]}"
    return f"pending: {', '.join(st['pending'])}" if st["pending"] else "ok"


def main_db_connect() -> Any:
    from mateai.infrastructure.database.db_manager import db_manager
    return db_manager._get_connection()
