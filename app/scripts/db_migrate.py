# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 Dương Thanh Lịch — VN-MateAI. See LICENSE and NOTICE.
"""
Phiên bản cấu trúc CSDL (src/mateai/infrastructure/database/migrations.py).

    python scripts/db_migrate.py status     # đã chạy / còn chờ / CSDL mới hơn ứng dụng
    python scripts/db_migrate.py up         # sao lưu (nếu cần) rồi chạy các bước còn chờ

Máy chủ tự chạy `up` lúc khởi động (bước "schema_migrations"); lệnh này để xem trước hoặc chạy tay khi bảo trì.
Docker:  docker compose exec app python scripts/db_migrate.py status
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT))


def main() -> int:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("action", choices=("status", "up"))
    a = ap.parse_args()
    from mateai.infrastructure.database import migrations
    if a.action == "status":
        with migrations.main_db_connect() as conn:
            st = migrations.status(conn)
        st["app_version"] = migrations.app_version()
        print(json.dumps(st, ensure_ascii=False, indent=2))
        return 1 if st["unknown"] else 0
    res = migrations.migrate(migrations.main_db_connect)
    print(json.dumps(res, ensure_ascii=False, indent=2))
    return 1 if res.get("unknown") or migrations.readiness() != "ok" else 0


if __name__ == "__main__":
    sys.exit(main())
