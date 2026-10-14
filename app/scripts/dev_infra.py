# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 Dương Thanh Lịch — VN-MateAI. See LICENSE and NOTICE.
"""
scripts/dev_infra.py — hạ tầng chạy thử cục bộ trên Windows khi KHÔNG có Docker (prompt cuối §138).

  python scripts/dev_infra.py fetch    # tải Redis (bản Windows) + SeaweedFS (S3) vào .infra/, kiểm SHA-256
  python scripts/dev_infra.py start    # Redis :6390, S3 :8333 (SeaweedFS)
  python scripts/dev_infra.py status
  python scripts/dev_infra.py stop

PostgreSQL: dùng gói dev `pgserver` (requirements-dev.txt) — test tự dựng server riêng.
Có Docker thì dùng `deploy/docker-compose.infra.yml` thay cho script này.
Rồi đặt cho máy chủ:  VNMATEAI_REDIS_URL=redis://127.0.0.1:6390/0
                      config.json -> object_storage: {"backend": "s3", "endpoint": "127.0.0.1:8333", ...}
"""
from __future__ import annotations

import hashlib
import json
import os
import socket
import subprocess
import sys
import urllib.request
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
INFRA = ROOT / ".infra"
PIDS = INFRA / "pids.json"

#: Bản đã kiểm: tải lại phải ra đúng SHA-256 này, không thì dừng (chống tệp bị tráo).
ARTIFACTS = {
    "redis": ("https://github.com/tporadowski/redis/releases/download/v5.0.14.1/Redis-x64-5.0.14.1.zip",
              "018ea18a35876383cbb5f4cd0258adfc87747cf9d619bce1cf73a2e36f720ccf"),
    "seaweedfs": ("https://github.com/seaweedfs/seaweedfs/releases/download/4.48/windows_amd64.zip",
                  "fe90c04c0620ad1a1c756f86cd5e1443773f56a446688077a4bb5ec04c3cc874"),
}
PORTS = {"redis": 6390, "s3": 8333}


def _sha256(p: Path) -> str:
    h = hashlib.sha256()
    with p.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def fetch() -> None:
    INFRA.mkdir(exist_ok=True)
    for name, (url, sha) in ARTIFACTS.items():
        z = INFRA / f"{name}.zip"
        if not z.exists() or _sha256(z) != sha:
            print(f"Tải {name} …")
            urllib.request.urlretrieve(url, z)       # noqa: S310 — URL cố định, kiểm SHA-256 ngay dưới
        got = _sha256(z)
        if got != sha:
            z.unlink()
            raise SystemExit(f"SHA-256 của {name} không khớp ({got}) — đã xoá tệp, dừng.")
        with zipfile.ZipFile(z) as zf:
            zf.extractall(INFRA / name)
        print(f"{name}: OK ({sha[:12]}…)")


def _up(port: int) -> bool:
    try:
        socket.create_connection(("127.0.0.1", port), timeout=0.3).close()
        return True
    except OSError:
        return False


def start() -> None:
    pids = json.loads(PIDS.read_text()) if PIDS.exists() else {}
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    if not _up(PORTS["redis"]):
        p = subprocess.Popen([str(INFRA / "redis" / "redis-server.exe"), "--port", str(PORTS["redis"]),
                              "--bind", "127.0.0.1", "--save", "", "--appendonly", "no"],
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, creationflags=flags)
        pids["redis"] = p.pid
    if not _up(PORTS["s3"]):
        data = INFRA / "seaweedfs" / "data"
        data.mkdir(parents=True, exist_ok=True)
        p = subprocess.Popen([str(INFRA / "seaweedfs" / "weed.exe"), "server", f"-dir={data}", "-ip=127.0.0.1",
                              "-s3", f"-s3.port={PORTS['s3']}", "-filer.port=8889", "-master.port=9333",
                              "-volume.port=8380", "-s3.port.lance=0", "-s3.port.iceberg=0"],
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, creationflags=flags)
        pids["seaweedfs"] = p.pid
    PIDS.write_text(json.dumps(pids))
    status()


def status() -> None:
    for name, port in PORTS.items():
        print(f"{name:6} 127.0.0.1:{port}  {'CHẠY' if _up(port) else 'tắt'}")


def stop() -> None:
    pids = json.loads(PIDS.read_text()) if PIDS.exists() else {}
    for name, pid in pids.items():
        subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"], capture_output=True)
        print(f"Đã dừng {name} (pid {pid})")
    PIDS.unlink(missing_ok=True)


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")   # console Windows (cp1252) không in được tiếng Việt
    cmd = sys.argv[1] if len(sys.argv) > 1 else "status"
    {"fetch": fetch, "start": start, "status": status, "stop": stop}[cmd]()
