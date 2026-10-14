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

_HOST_RE = re.compile(r"^(?=.{1,253}$)[A-Za-z0-9][A-Za-z0-9.\-]*$|^[0-9A-Fa-f:]{2,39}$")


@export_skill(
    name="test_ping_host",
    description=("Ping một địa chỉ IP hoặc tên miền bất kỳ (vd 8.8.8.8, google.com, máy trạm trong LAN) "
                 "để kiểm tra kết nối mạng: số gói nhận được, tỉ lệ mất gói, độ trễ min/trung bình/max (ms). "
                 "Dùng cho mọi yêu cầu 'ping …' hoặc 'kiểm tra mạng tới …'."),
    parameters_schema={
        "type": "object",
        "properties": {
            "host": {"type": "string", "description": "Địa chỉ IP hoặc tên miền cần ping"},
            "count": {"type": "integer", "description": "Số gói gửi (1–10, mặc định 4)"},
        },
        "required": ["host"],
    },
)
def test_ping_host(host: str = "", count: int = 4, **kwargs) -> dict:
    """Ping THẬT bằng lệnh hệ thống (không qua shell). Trước đây hàm này trả cứng
    một độ trễ cố định và không nhận địa chỉ — một số đo bịa."""
    host = str(host or "").strip()
    if not _HOST_RE.match(host):
        return {"status": "error", "error": "Địa chỉ không hợp lệ — cần IP hoặc tên miền."}
    count = max(1, min(int(count or 4), 10))
    flag = "-n" if sys.platform.startswith("win") else "-c"
    try:
        proc = subprocess.run(["ping", flag, str(count), host], capture_output=True,
                              timeout=5 + 2 * count, check=False)
    except subprocess.TimeoutExpired:
        return {"status": "error", "host": host, "error": "Hết thời gian chờ ping."}
    except OSError as exc:
        return {"status": "error", "host": host, "error": f"Không chạy được lệnh ping: {exc}"}
    out = proc.stdout.decode("utf-8", "replace")      # chỉ dò "TTL=" và chữ số — mã trang OEM không ảnh hưởng
    # Dòng trả lời có "TTL=" / "ttl=" ở mọi ngôn ngữ hệ điều hành; độ trễ dạng "=12ms", "<1ms", "=12.3 ms".
    replies = [ln for ln in out.splitlines() if "ttl=" in ln.lower()]
    times = []
    for ln in replies:
        m = re.search(r"[=<]\s*([\d.]+)\s*ms", ln, re.IGNORECASE)
        if m:
            times.append(float(m.group(1)))
    received = len(replies)
    result = {"status": "success" if received else "unreachable", "host": host, "sent": count,
              "received": received, "loss_percent": round((count - received) * 100.0 / count, 1)}
    if times:
        result.update(min_ms=min(times), avg_ms=round(sum(times) / len(times), 1), max_ms=max(times))
    return result
