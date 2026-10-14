# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 Dương Thanh Lịch — VN-MateAI. See LICENSE and NOTICE.
"""
scripts/load_test.py — đo tải máy chủ ĐANG CHẠY với N phiên WebSocket đồng thời.

  python scripts/load_test.py --sessions 50 --duration 60
  python scripts/load_test.py --sessions 100 --duration 60 --base https://127.0.0.1

Không dùng tài khoản nào: phiên là HUD ở chế độ chỉ xem (`/ws/hud` không token — máy chủ vẫn
đẩy telemetry định kỳ cho từng phiên) + probe `/readyz` (truy vấn CSDL thật mỗi lần).
Không gọi LLM, không chạy tool, không ghi dữ liệu nghiệp vụ.

Đo:
  1. nền: độ trễ `/readyz` khi không có phiên thêm (15 s);
  2. tải: mở N phiên, giữ `duration` giây, đồng thời gọi `/readyz` liên tục —
     độ trễ kết nối WS, số phiên bị từ chối / rớt, khoảng cách giữa hai gói telemetry
     (vòng sự kiện bị nghẽn thì khoảng cách giãn ra), độ trễ `/readyz` p50 / p95 / p99.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import ssl
import statistics
import sys
import time
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def pct(xs, p):
    if not xs:
        return None
    xs = sorted(xs)
    return round(xs[min(len(xs) - 1, max(0, round(p / 100 * len(xs)) - 1))], 1)


def summary(xs):
    return {"n": len(xs), "p50": pct(xs, 50), "p95": pct(xs, 95), "p99": pct(xs, 99),
            "max": round(max(xs), 1) if xs else None}


async def probe_loop(client, url, stop_at, out, errors):
    while time.monotonic() < stop_at:
        t0 = time.perf_counter()
        try:
            r = await client.get(url, timeout=10)
            if r.status_code == 200:
                out.append((time.perf_counter() - t0) * 1000)
            else:
                errors.append(r.status_code)
        except Exception as exc:  # noqa: BLE001
            errors.append(type(exc).__name__)
        await asyncio.sleep(0.2)


async def hud_session(ws_url, ctx, stop_at, stats):
    import websockets
    t0 = time.perf_counter()
    try:
        async with websockets.connect(ws_url, ssl=ctx, open_timeout=15, max_size=None) as ws:
            stats["connect_ms"].append((time.perf_counter() - t0) * 1000)
            last = None
            while time.monotonic() < stop_at:
                try:
                    raw = await asyncio.wait_for(ws.recv(), timeout=max(0.1, stop_at - time.monotonic()))
                except asyncio.TimeoutError:
                    break
                now = time.perf_counter()
                stats["messages"] += 1
                try:
                    kind = json.loads(raw).get("type")
                except (ValueError, AttributeError):
                    kind = None
                if kind == "metrics_update":
                    if last is not None:
                        stats["gaps_ms"].append((now - last) * 1000)
                    last = now
            stats["completed"] += 1
    except Exception as exc:  # noqa: BLE001
        stats["failed"].append(type(exc).__name__ + (f" {getattr(exc, 'status_code', '')}" or ""))


async def run(base, sessions, duration):
    import httpx
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE                 # chứng chỉ tự ký của máy chủ nội bộ
    ws_url = base.replace("https://", "wss://").replace("http://", "ws://") + "/ws/hud"
    async with httpx.AsyncClient(verify=False) as client:
        base_lat, base_err = [], []
        stop = time.monotonic() + 15
        await asyncio.gather(*(probe_loop(client, base + "/readyz", stop, base_lat, base_err) for _ in range(4)))

        stats = {"connect_ms": [], "gaps_ms": [], "messages": 0, "completed": 0, "failed": []}
        load_lat, load_err = [], []
        stop = time.monotonic() + duration
        tasks = [asyncio.create_task(hud_session(ws_url, ctx, stop, stats)) for _ in range(sessions)]
        probes = [asyncio.create_task(probe_loop(client, base + "/readyz", stop, load_lat, load_err))
                  for _ in range(4)]
        await asyncio.gather(*tasks, *probes)
    return {
        "created_at": datetime.now().isoformat(timespec="seconds"), "base": base,
        "sessions": sessions, "duration_s": duration,
        "baseline_readyz_ms": summary(base_lat), "baseline_errors": len(base_err),
        "load_readyz_ms": summary(load_lat), "load_errors": len(load_err),
        "ws_connect_ms": summary(stats["connect_ms"]),
        "ws_connected": len(stats["connect_ms"]), "ws_failed": len(stats["failed"]),
        "ws_fail_reasons": sorted(set(stats["failed"]))[:5], "ws_completed": stats["completed"],
        "telemetry_messages": stats["messages"], "telemetry_gap_ms": summary(stats["gaps_ms"]),
    }


def main() -> int:
    ap = argparse.ArgumentParser(description="Đo tải máy chủ đang chạy (không tài khoản, không LLM)")
    ap.add_argument("--base", default="https://127.0.0.1")
    ap.add_argument("--sessions", type=int, default=50)
    ap.add_argument("--duration", type=int, default=60)
    args = ap.parse_args()
    rep = asyncio.run(run(args.base.rstrip("/"), args.sessions, args.duration))
    out = ROOT / "reports" / "load-test"
    out.mkdir(parents=True, exist_ok=True)
    path = out / f"{datetime.now().strftime('%Y%m%d-%H%M%S')}-{args.sessions}.json"
    path.write_text(json.dumps(rep, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({k: v for k, v in rep.items() if k != "created_at"}, ensure_ascii=False, indent=1))
    print(f"Đã ghi {path}")
    return 0


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    sys.exit(main())
