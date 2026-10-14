# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 Dương Thanh Lịch — VN-MateAI. See LICENSE and NOTICE.
from core.plugin_manager import export_skill

@export_skill(name="worker_health_check", description="Kiểm tra sức khỏe máy trạm (CPU, RAM, ổ đĩa) bằng số đo thật",
              parameters_schema={"type": "object", "properties": {}})
def worker_health_check() -> dict:
    # Trước đây luôn trả "hoàn toàn khỏe mạnh" — không đo gì cả.
    import os
    import psutil
    disk_root = os.environ.get("SystemDrive", "C:") + "\\" if os.name == "nt" else "/"
    cpu = psutil.cpu_percent(interval=0.5)
    ram = psutil.virtual_memory().percent
    disk = psutil.disk_usage(disk_root).percent
    problems = [label for label, val, limit in (("CPU", cpu, 90), ("RAM", ram, 90), ("ổ đĩa", disk, 90)) if val >= limit]
    return {
        "status": "success",
        "healthy": not problems,
        "cpu_percent": cpu, "ram_percent": ram, "disk_percent": disk,
        "message": ("Máy trạm ổn định." if not problems else "Cao bất thường: " + ", ".join(problems)),
    }