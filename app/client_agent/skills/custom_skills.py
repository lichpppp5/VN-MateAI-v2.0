# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 Dương Thanh Lịch — VN-MateAI. See LICENSE and NOTICE.
"""
skills/custom_skills.py
=======================
Các kỹ năng tự định nghĩa do người dùng thêm thủ công.
"""
from core.plugin_manager import export_skill
import subprocess
import os
import sys



import re


def parse_ping_ms(output: str) -> "float | None":
    """Thời gian phản hồi (ms) từ kết quả lệnh ping Windows / Linux / macOS."""
    m = re.search(r"(?:time|thời gian)[=<]\s*([\d.,]+)\s*ms", output, re.IGNORECASE)
    return float(m.group(1).replace(",", ".")) if m else None


@export_skill(
    name="test_ping_host",
    description="Ping thật tới một máy (mặc định máy chủ Master) và trả thời gian phản hồi.",
    parameters_schema={
    "type": "object",
    "properties": {
        "host": {"type": "string", "description": "IP / tên máy cần ping. Bỏ trống: máy chủ Master."},
    },
    "required": []
},
)
def test_ping_host(host: str = "", **kwargs) -> dict:
    # Trước đây trả cứng {"ping": "15ms"} — số giả, máy chủ có tắt cũng báo 15 ms.
    target = (host or "").strip()
    if not target:
        try:
            import json as _json
            from pathlib import Path as _P
            cfg = _json.loads((_P(__file__).resolve().parent.parent / "config.json").read_text(encoding="utf-8"))
            target = str(cfg.get("master_ip") or "")
        except (OSError, ValueError):
            target = ""
    if not target or not re.match(r"^[A-Za-z0-9.\-:]{1,253}$", target):
        return {"status": "error", "error": "Thiếu hoặc sai địa chỉ cần ping."}
    cmd = ["ping", "-n", "1", "-w", "2000", target] if os.name == "nt" else ["ping", "-c", "1", "-W", "2", target]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=6)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return {"status": "error", "host": target, "error": f"Không chạy được ping: {exc}"}
    ms = parse_ping_ms(proc.stdout or "")
    if proc.returncode != 0 or ms is None:
        return {"status": "error", "host": target, "reachable": False, "error": "Không có phản hồi."}
    return {"status": "success", "host": target, "reachable": True, "ping_ms": ms}
