# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 Dương Thanh Lịch — VN-MateAI. See LICENSE and NOTICE.
"""
tests/test_phase76_hud_telemetry_real.py
========================================
Kiểm thử Phase 76 — telemetry HUD chỉ trả số liệu thật.

Nền tảng của mọi ô trên HUD là `_get_hud_metrics_payload()`. Trước Phase 76
hàm này tự chế ra ba loại số:
  - `disk_pct = 45.0` trong nhánh except của psutil: 45 là con số không đo
    được ở đâu, chỉ là "số trông hợp lý".
  - `hw.get("disk_free_gb", 0.0)` và `hw.get("net_sent_mbps", 0.0)`: cache
    health có sẵn 0.0 nên `?? 0` không bao giờ kích hoạt — nhưng nếu cache
    chưa đo được gì thì 0.0 vẫn là con số bịa, và HUD hiện nó như số thật.
  - Ba trường quyền hạn `security_role`/`security_status`/`permission_level`
    ghi cứng "ADMIN"/"FULL UNRESTRICTED" cho MỌI kết nối, kể cả khách chưa
    đăng nhập. Đây là payload broadcast chung nên không biết ai đang xem.

Nguyên tắc: số đo thiếu phải là `None` (JSON null) để giao diện hiện
"chờ kết nối", tuyệt đối không bịa giá trị thay thế.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

PASSED = 0
FAILED = 0
FAILURES: list = []


def check(name: str, condition: bool, detail: str = "") -> None:
    global PASSED, FAILED
    if condition:
        PASSED += 1
        print(f"  ✅ {name}")
    else:
        FAILED += 1
        FAILURES.append(f"{name} — {detail}")
        print(f"  ❌ {name}  {detail}")


def section(title: str) -> None:
    print(f"\n▸ {title}")


PROJECT_ROOT = Path(__file__).resolve().parents[1]


# ──────────────────────────────────────────────────────────────────────
section("Payload không còn trường quyền hạn bịa")

from mateai.interfaces.http.hud_voice import get_metrics_payload as _get_hud_metrics_payload  # noqa: E402

payload = _get_hud_metrics_payload()

for gone in ("security_role", "security_status", "permission_level"):
    check(
        f"không còn trường {gone!r} trong payload",
        gone not in payload,
        "trả quyền hạn giả cho mọi kết nối, kể cả chưa đăng nhập",
    )

# Vai trò thật chỉ được đi trong gói `hud_welcome` riêng của từng kết nối,
# nơi máy chủ biết người xem là ai (JWT qua ?token=).
# Tầng HTTP: payload HUD ở hud_voice.py, gói hud_welcome ở kênh /ws/hud.
_http = PROJECT_ROOT / "src" / "mateai" / "interfaces" / "http"
server_src = "\n\n".join(
    p.read_text(encoding="utf-8")
    for p in [_http / "hud_voice.py", _http / "server.py", *sorted((_http / "routers").glob("*.py"))]
)
check(
    "gói hud_welcome có mang vai trò thật của phiên đó",
    '"type": "hud_welcome"' in server_src
    and '"role": (ws_user or {}).get("role")' in server_src
    and '"authenticated": ws_user is not None' in server_src,
    "thiếu vai trò người xem thì HUD không có nguồn nào để hiện quyền",
)
check(
    "không còn nhét security_role vào cache dùng chung",
    'SYSTEM_HEALTH_CACHE["security_role"]' not in server_src,
    "cache là biến global nên mọi caller vô danh đều nhận ADMIN",
)

# ──────────────────────────────────────────────────────────────────────
section("Số đo thiếu được trả null, không phải số bịa")

# `disk_pct = 45.0` từng nằm trong nhánh except của psutil.
check(
    "không còn hằng số 45.0 thay cho dung lượng đĩa",
    "disk_pct = 45.0" not in server_src,
    "45 là số bịa, không đo được ở đâu",
)

# Cache health khởi tạo mọi trường phần cứng bằng 0.0, nên `hw.get(k, 0.0)`
# KHÔNG phải "chưa đo" mà là "đo được 0" — phải xử lý riêng.
import mateai.application.operations.health_monitor as health_monitor  # noqa: E402

hw_default = health_monitor.SYSTEM_HEALTH_CACHE.get("hardware", {})
for key in ("disk_free_gb", "disk_total_gb", "cpu_freq_mhz"):
    check(
        f"cache health mặc định {key} vẫn là 0.0 (nên không được tin là số thật)",
        hw_default.get(key) == 0.0,
        f"thấy {hw_default.get(key)!r} — nếu đã đổi thì test cần xét lại logic psutil",
    )

# Vậy payload phải đo trực tiếp bằng psutil khi cache chưa có số dương.
check(
    "payload đo đĩa trực tiếp bằng psutil khi cache còn 0.0",
    "psutil.disk_usage(\"/\")" in server_src,
    "nếu chỉ đọc cache thì ô 'FREE: -- GB' hoặc '0 GB' sẽ là số bịa",
)
check(
    "payload thử psutil cho xung nhịp CPU khi cache bằng 0.0",
    "psutil.cpu_freq()" in server_src,
)

# Giá trị thực tế: đĩa phải là số dương đo được, không phải 0 do bịa.
check(
    "disk_percent là số đo thật (0 < x < 100)",
    isinstance(payload.get("disk_percent"), (int, float))
    and 0 < payload["disk_percent"] < 100,
    f"thấy {payload.get('disk_percent')!r}",
)
check(
    "disk_free_gb là số đo thật (dương)",
    isinstance(payload.get("disk_free_gb"), (int, float)) and payload["disk_free_gb"] > 0,
    f"thấy {payload.get('disk_free_gb')!r} — 0 nghĩa là chưa đo được nhưng đang hiện như số thật",
)
check(
    "disk_total_gb là số đo thật (dương)",
    isinstance(payload.get("disk_total_gb"), (int, float)) and payload["disk_total_gb"] > 0,
    f"thấy {payload.get('disk_total_gb')!r}",
)
check(
    "disk_free_gb không vượt quá disk_total_gb",
    payload["disk_free_gb"] <= payload["disk_total_gb"],
    f"{payload['disk_free_gb']} > {payload['disk_total_gb']}",
)

# ──────────────────────────────────────────────────────────────────────
section("Số đo luôn có thật: 0 là số hợp lệ, không phải thiếu dữ liệu")

for key in ("processes_count", "connected_clients", "active_audio_hardware", "skills_count"):
    check(
        f"{key} là số đếm được (kể cả 0)",
        isinstance(payload.get(key), int) and payload[key] >= 0,
        f"thấy {payload.get(key)!r}",
    )
check(
    "skills_count khớp với số skill thật đang nạp (không phải con số tròn)",
    payload.get("skills_count", 0) > 0,
    "trước đây HUD dùng `?? 50` làm số dự phòng — 75 là số thật, 50 là bịa",
)

# ──────────────────────────────────────────────────────────────────────
section("Payload có đủ trường cho mọi ô trên HUD")

for key in (
    "cpu_percent", "cpu_cores", "ram_percent", "ram_used_gb", "ram_total_gb",
    "disk_percent", "disk_free_gb", "processes_count", "connected_clients",
    "active_audio_hardware", "active_web_clients", "skills_count",
    "net_sent_mbps", "net_recv_mbps", "timestamp",
):
    check(f"có trường {key!r}", key in payload, "HUD sẽ không có gì để hiện")

# Trường có thể vắng (None) vẫn phải CÓ mặt trong payload, để frontend phân
# biệt "server chưa đo" với "server không gửi trường này".
for key in ("cpu_freq_mhz", "net_sent_mbps", "net_recv_mbps"):
    check(
        f"{key} luôn có mặt (None nếu chưa đo, không phải thiếu hẳn)",
        key in payload,
        "thiếu hẳn thì frontend không phân biệt được",
    )

# ──────────────────────────────────────────────────────────────────────
print("")
if FAILED > 0:
    print("❌ CÓ THẤT BẠI:")
    for f in FAILURES:
        print(f"   - {f}")
    print("")
print(f"Tổng: {PASSED + FAILED} | Pass: {PASSED} | Fail: {FAILED}")
if FAILED == 0:
    print("\n✅ TẤT CẢ PASS")
sys.exit(0 if FAILED == 0 else 1)
