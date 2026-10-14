#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 Dương Thanh Lịch — VN-MateAI. See LICENSE and NOTICE.
#
# Sao lưu VN-MateAI (Docker): CSDL PostgreSQL + volume dữ liệu (config.json, khoá, kho tri thức, kỹ năng…) → ./backups/
#   ./backup.sh            — nên đặt cron hằng đêm, vd:  0 2 * * *  cd /opt/vnmateai && ./backup.sh
# Bản sao lưu CHỨA KHOÁ GIẢI MÃ và dữ liệu cá nhân: cất nơi an toàn, không gửi qua kênh không mã hoá.
set -euo pipefail
cd "$(dirname "$0")"
ts="$(date +%Y%m%d-%H%M%S)"; mkdir -p backups
ver="$(grep -E '^VNMATE_VERSION=' .env | cut -d= -f2)"; ver="${ver:-1.0.0}"
docker compose exec -T postgres pg_dump -U vnmate -d vnmate --no-owner | gzip > "backups/db-$ts.sql.gz"
docker run --rm --user 0 --entrypoint tar -v vnmateai_appdata:/data:ro -v "$PWD/backups:/b" "vnmateai/app:$ver" \
  czf "/b/appdata-$ts.tgz" -C /data .
for pat in 'db-*.sql.gz' 'appdata-*.tgz'; do                                   # giữ 14 bản gần nhất (Linux + macOS)
  ls -1t backups/$pat 2>/dev/null | tail -n +15 | while IFS= read -r f; do rm -f "$f"; done
done
echo "[VN-MateAI] Đã sao lưu: backups/db-$ts.sql.gz  backups/appdata-$ts.tgz"
