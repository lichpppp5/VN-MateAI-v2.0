# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 Dương Thanh Lịch — VN-MateAI. See LICENSE and NOTICE.
"""
scripts/bench_codec.py — đo Opus so với MP3 trên câu TTS THẬT trong storage/audio_cache (prompt cuối §27).

  python scripts/bench_codec.py [--n 60]      # cần imageio-ffmpeg (requirements-dev.txt)

Đo cho từng câu: kích thước MP3 hiện tại; kích thước Opus 16 / 24 / 32 kbps (đóng gói Ogg, 20 ms/khung);
thời gian mã hoá (CPU máy chủ) và giải mã, chuẩn hoá theo 1 giây âm thanh. In JSON — không ước lượng.
"""
from __future__ import annotations

import json
import os
import statistics
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _ff() -> str:
    import imageio_ffmpeg
    return imageio_ffmpeg.get_ffmpeg_exe()


def _run(args) -> float:
    t0 = time.perf_counter()
    subprocess.run(args, check=True, capture_output=True)
    return time.perf_counter() - t0


def _duration(ff: str, path: Path) -> float:
    out = subprocess.run([ff, "-i", str(path), "-f", "null", "-"], capture_output=True, text=True, errors="replace")
    for line in out.stderr.splitlines()[::-1]:
        if "time=" in line:
            t = line.split("time=")[1].split()[0]
            h, m, s = t.split(":")
            return int(h) * 3600 + int(m) * 60 + float(s)
    return 0.0


def main(n: int = 60) -> dict:
    ff = _ff()
    files = sorted((ROOT / "storage" / "audio_cache").glob("*.mp3"))[:n]
    rows = []
    with tempfile.TemporaryDirectory() as td:
        for mp3 in files:
            dur = _duration(ff, mp3)
            if dur < 0.3:
                continue
            pcm = Path(td) / "a.wav"
            t_dec_mp3 = _run([ff, "-y", "-i", str(mp3), "-ac", "1", "-ar", "24000", str(pcm)])
            row = {"seconds": round(dur, 2), "mp3_bytes": mp3.stat().st_size,
                   "mp3_decode_ms_per_s": round(t_dec_mp3 * 1000 / dur, 1)}
            for kbps in (16, 24, 32):
                opus = Path(td) / f"a{kbps}.opus"
                t_enc = _run([ff, "-y", "-i", str(pcm), "-c:a", "libopus", "-b:a", f"{kbps}k",
                              "-frame_duration", "20", "-application", "voip", str(opus)])
                t_dec = _run([ff, "-y", "-i", str(opus), "-f", "s16le", "-"])
                row[f"opus{kbps}_bytes"] = opus.stat().st_size
                row[f"opus{kbps}_encode_ms_per_s"] = round(t_enc * 1000 / dur, 1)
                row[f"opus{kbps}_decode_ms_per_s"] = round(t_dec * 1000 / dur, 1)
            rows.append(row)
    if not rows:
        return {"samples": 0}
    med = lambda k: statistics.median(r[k] for r in rows)  # noqa: E731
    summary = {"samples": len(rows), "median_seconds": med("seconds"),
               "mp3_kbps_median": round(statistics.median(r["mp3_bytes"] * 8 / r["seconds"] / 1000 for r in rows), 1)}
    for kbps in (16, 24, 32):
        summary[f"opus{kbps}_size_vs_mp3"] = round(statistics.median(r[f"opus{kbps}_bytes"] / r["mp3_bytes"] for r in rows), 3)
        summary[f"opus{kbps}_encode_ms_per_s_median"] = med(f"opus{kbps}_encode_ms_per_s")
        summary[f"opus{kbps}_decode_ms_per_s_median"] = med(f"opus{kbps}_decode_ms_per_s")
    summary["mp3_decode_ms_per_s_median"] = med("mp3_decode_ms_per_s")
    summary["note"] = ("thời gian gồm khởi động tiến trình ffmpeg (~cố định mỗi lần) — so sánh tương đối "
                       "giữa các codec, không phải chi phí tuyệt đối của thư viện nhúng")
    return summary


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    n = int(sys.argv[sys.argv.index("--n") + 1]) if "--n" in sys.argv else 60
    print(json.dumps(main(n), ensure_ascii=False, indent=2))
