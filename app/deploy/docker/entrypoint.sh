#!/bin/sh
# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 Dương Thanh Lịch — VN-MateAI. See LICENSE and NOTICE.
#
# Khởi động container VN-MateAI. Mọi thứ cần GIỮ qua các lần cập nhật image nằm trong volume /data:
#   config.json (tạo lần đầu từ biến môi trường — init_config.py), certs/ (khoá JWT / mã hoá cấu hình / TLS — tự sinh lần
#   đầu), storage/ (kho tri thức, vector DB, cache giọng), logs/, backups/, memory/, reports/, data/, config/ (nguồn dữ liệu,
#   MCP server đã khai báo), skills/ (kỹ năng AI tự tạo). Thư mục cùng tên trong /app trỏ (symlink) vào /data. CSDL nằm ở PostgreSQL (service postgres).
set -eu

DATA="${VNMATEAI_DATA_DIR:-/data}"
APP=/app
mkdir -p "$DATA"

for d in certs storage logs backups memory reports data config; do
  mkdir -p "$DATA/$d"
  if [ ! -L "$APP/$d" ]; then rm -rf "${APP:?}/$d"; ln -s "$DATA/$d" "$APP/$d"; fi
done

# Kỹ năng: kỹ năng có sẵn của bản này luôn được cập nhật theo image; kỹ năng AI tự tạo (auto_*) và pending/ được giữ.
mkdir -p "$DATA/skills"
for f in "$APP"/skills.dist/*.py; do
  name=$(basename "$f")
  case "$name" in
    auto_*) [ -e "$DATA/skills/$name" ] || cp "$f" "$DATA/skills/$name" ;;
    *) cp "$f" "$DATA/skills/$name" ;;
  esac
done
[ -e "$DATA/skills/registry.json" ] || cp "$APP/skills.dist/registry.json" "$DATA/skills/registry.json" 2>/dev/null || true
# Danh mục kỹ năng (chủ sở hữu, kiểm thử) đi theo bản phát hành — luôn cập nhật theo image.
cp "$APP/skills.dist/catalog.json" "$DATA/skills/catalog.json" 2>/dev/null || true
if [ ! -L "$APP/skills" ]; then rm -rf "${APP:?}/skills"; ln -s "$DATA/skills" "$APP/skills"; fi

# Cấu hình: bảo toàn cấu hình người dùng qua các lần restart/cập nhật container
if [ ! -L "$APP/config.json" ] && [ -f "$APP/config.json" ]; then
  if [ ! -s "$DATA/config.json" ] || [ "$APP/config.json" -nt "$DATA/config.json" ]; then
    cp -f "$APP/config.json" "$DATA/config.json"
  fi
  rm -f "$APP/config.json"
fi
if [ ! -s "$DATA/config.json" ]; then
  python "$APP/deploy/docker/init_config.py" "$APP/config.example.json" "$DATA/config.json"
fi
[ -L "$APP/config.json" ] || { rm -f "$APP/config.json"; ln -s "$DATA/config.json" "$APP/config.json"; }

# Chờ PostgreSQL sẵn sàng (compose đã có healthcheck; đây là chốt chặn khi chạy lẻ).
if [ -n "${VNMATEAI_DATABASE_URL:-}" ]; then
  python - <<'PY'
import os, sys, time
import psycopg
url = os.environ["VNMATEAI_DATABASE_URL"]
for i in range(60):
    try:
        psycopg.connect(url, connect_timeout=3).close()
        sys.exit(0)
    except Exception as exc:  # noqa: BLE001
        if i == 0:
            print(f"[entrypoint] chờ PostgreSQL… ({exc.__class__.__name__})", flush=True)
        time.sleep(2)
print("[entrypoint] Không kết nối được PostgreSQL sau 120 s", file=sys.stderr)
sys.exit(1)
PY
fi

# MCP server tham chiếu (giờ, đọc trang web) — đăng ký lại mỗi lần khởi động (an toàn khi lặp; giữ quy tắc admin đặt trên Portal).
# Tắt: VNMATE_MCP_REFERENCE=0. Lỗi không chặn khởi động.
if [ "${VNMATE_MCP_REFERENCE:-1}" = "1" ] && [ -x /opt/mcp-venv/bin/python ]; then
  python "$APP/scripts/setup_mcp_servers.py" --no-git || echo "[entrypoint] Không đăng ký được MCP server tham chiếu (bỏ qua)" >&2
fi

exec "$@"
