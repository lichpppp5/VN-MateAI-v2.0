# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 Dương Thanh Lịch — VN-MateAI. See LICENSE and NOTICE.
"""
scripts/migrate_sqlite_to_pg.py — di trú SQLite -> PostgreSQL có kiểm chứng (prompt cuối §66).

  python scripts/migrate_sqlite_to_pg.py plan    --sqlite vnmateai.db
  python scripts/migrate_sqlite_to_pg.py migrate --sqlite vnmateai.db --pg postgresql://u:p@host/db [--schema vnmate] [--replace]
  python scripts/migrate_sqlite_to_pg.py verify  --sqlite vnmateai.db --pg ... [--schema vnmate]
  python scripts/migrate_sqlite_to_pg.py rollback --pg ... [--schema vnmate]

Không sửa / xoá SQLite. Nên chạy trên BẢN SAO LƯU (scripts/backup.py create) khi máy chủ đang chạy.
Mã thoát: 0 = khớp hoàn toàn; 2 = lệch (xem báo cáo JSON).
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))


def main() -> int:
    ap = argparse.ArgumentParser(description="Di trú SQLite -> PostgreSQL có kiểm chứng")
    ap.add_argument("cmd", choices=["plan", "migrate", "verify", "rollback"])
    ap.add_argument("--sqlite", default=str(ROOT / "vnmateai.db"))
    ap.add_argument("--pg", default=os.environ.get("VNMATEAI_PG_DSN", ""))
    ap.add_argument("--schema", default="vnmate")
    ap.add_argument("--replace", action="store_true", help="xoá schema đích cũ rồi chép lại")
    a = ap.parse_args()
    from mateai.infrastructure.database import pg_migration as pm
    if a.cmd == "plan":
        print(json.dumps(pm.schema_map(Path(a.sqlite)), ensure_ascii=False, indent=2))
        return 0
    if not a.pg:
        print("Thiếu --pg (hoặc VNMATEAI_PG_DSN).")
        return 1
    if a.cmd == "rollback":
        pm.rollback(a.pg, a.schema)
        print(f"Đã xoá schema '{a.schema}'. SQLite không bị đụng tới.")
        return 0
    rep = (pm.migrate(Path(a.sqlite), a.pg, a.schema, replace=a.replace) if a.cmd == "migrate"
           else pm.verify(Path(a.sqlite), a.pg, a.schema))
    print(json.dumps(rep, ensure_ascii=False, indent=2))
    return 0 if rep["ok"] else 2


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")   # console Windows (cp1252) không in được tiếng Việt
    sys.exit(main())
