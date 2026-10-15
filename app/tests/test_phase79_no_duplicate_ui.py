# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 Dương Thanh Lịch — VN-MateAI. See LICENSE and NOTICE.
"""
tests/test_phase79_no_duplicate_ui.py
=====================================
Kiểm thử Phase 79 — cấu trúc tab không còn trùng lặp.

Bối cảnh
---------
Người dùng báo "các tab trùng lặp rất nhiều, cần gom lại". Khi truy nguyên
thì ra hai loại vấn đề khác nhau:

  1. Trùng vì ĐỂ Ý ĐỊNH — cùng một dữ liệu ở nhiều tab:
       - Nhật ký: 5 nơi (tab Nhật Ký, tab Bảo Mật, tab Công Việc,
         Trung Tâm Chỉ Huy, Bảng Điều Khiển)
       - Hàng đợi phê duyệt: 3 nơi, lấy từ 2 endpoint KHÁC NHAU
         (/enterprise/hitl/pending và /security/pending-action) nên số liệu
         có thể lệch nhau
       - Mạch ESP32: 2 nơi
     Nay: nhật ký gom về tab Nhật Ký (3 chế độ), phê duyệt về Bảng Điều Khiển,
     ESP32 về tab Trợ Lý Thoại.

  2. Trùng vì LỖI KỸ THUẬT — HÀM TRÙNG TÊN trong app.js. Trong JS định
     nghĩa sau đè định nghĩa trước, nên bản "thắng" âm thầm khiến bản kia
     chết. Đã phát hiện và sửa 3 cặp:
       - renderDevicesTable      → bảng tab Thiết Bị trống, không giải thích
       - loadAudioNodes          → ô đếm ESP32 kẹt ở số 0 viết cứng trong HTML
       - togglePasswordVisibility → biểu tượng mắt không bao giờ đổi

Loại 2 nguy hiểm hơn nhiều: trùng lặp về mặt giao diện chỉ gây rối, còn trùng
tên hàm âm thầm phá chức năng mà không có lỗi nào hiện ra console. Nên test này
quét toàn file thay vì kiểm từng chỗ.
"""

from __future__ import annotations

import re
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

PASSED = 0
FAILED = 0
FAILURES: list = []


def check(name: str, cond: bool, detail: str = "") -> None:
    global PASSED, FAILED
    if cond:
        PASSED += 1
        print(f"  ✅ {name}")
    else:
        FAILED += 1
        FAILURES.append(f"{name} — {detail}")
        print(f"  ❌ {name}  {detail}")


def section(title: str) -> None:
    print(f"\n▸ {title}")


ROOT = Path(__file__).resolve().parents[1]
HTML = (ROOT / "web" / "index.html").read_text(encoding="utf-8")
JS = (ROOT / "web" / "app.js").read_text(encoding="utf-8")

JS_CODE = re.sub(r"/\*[\s\S]*?\*/", "", JS)
JS_CODE = re.sub(r"^\s*//.*$", "", JS_CODE, flags=re.M)
JS_CODE = re.sub(r"//.*$", "", JS_CODE, flags=re.M)


def section_html(tab: str) -> str:
    m = re.search(r'id="tab-%s"[\s\S]*?\n    </section>' % tab, HTML)
    return m.group(0) if m else ""


# ──────────────────────────────────────────────────────────────────────
section("Không còn hàm trùng tên trong app.js")

# Đây là loại lỗi đã phá chức năng 3 lần mà không có dấu hiệu gì trên UI.
defs: dict[str, list[int]] = defaultdict(list)
for m in re.finditer(r"^(?:async\s+)?function\s+([A-Za-z_$][\w$]*)\s*\(", JS_CODE, re.M):
    defs[m.group(1)].append(JS_CODE[: m.start()].count("\n") + 1)

dupes = {k: v for k, v in defs.items() if len(v) > 1}
check(
    "không còn hàm nào được định nghĩa 2 lần",
    not dupes,
    "; ".join(f"{k} tại dòng {v}" for k, v in list(dupes.items())[:5]),
)
check(
    "số hàm cấp module vẫn hợp lý",
    len(defs) > 200,
    f"chỉ còn {len(defs)} hàm — có thể đã xoá nhầm",
)

# 3 cặp từng gây lỗi — khẳng định chúng không quay lại.
for name in ("renderDevicesTable", "loadAudioNodes", "togglePasswordVisibility"):
    check(
        f"hàm '{name}' chỉ còn 1 bản định nghĩa",
        len(defs.get(name, [])) <= 1,
        f"định nghĩa tại dòng {defs.get(name)}",
    )
check(
    "bản ERP đã đổi tên thành renderDeptDevicesTable",
    "renderDeptDevicesTable" in JS and "renderDevicesTable(list)" in JS,
    "hai chức năng khác nhau phải có tên khác nhau",
)

# ──────────────────────────────────────────────────────────────────────
section("Nhật ký chỉ còn  một chỗ")

for tab, ids in (
    ("logs", ("audit-table-body", "kpi-logs-table-body", "log-output")),
    ("security", ("audit-table-body",)),
    ("tasks", ("kpi-logs-table-body",)),
    ("command-center", ("cc-ops-log",)),
):
    body = section_html(tab)
    for eid in ids:
        if eid in ("audit-table-body", "kpi-logs-table-body"):
            if tab == "logs":
                check(f"tab Nhật Ký chứa #{eid}", f'id="{eid}"' in body)
            else:
                check(
                    f"tab {tab} KHÔNG còn #{eid}",
                    f'id="{eid}"' not in body,
                    "bảng nhật ký phải ở một chỗ duy nhất",
                )

check(
    "tab Nhật Ký có bộ chuyển 4 chế độ",
    all(
        f'data-log-view="{v}"' in section_html("logs")
        for v in ("stream", "security", "kpi", "recent")
    ),
    "thiếu chế độ nghĩa là bảng nhật ký bị giấu không tìm thấy",
)
check(
    "hàm switchLogView biết chế độ recent",
    "'stream', 'security', 'kpi', 'recent'" in JS,
    "thêm nút mà không thêm vào danh sách thì bấm không chuyển được",
)
check(
    "bảng nhật ký cũ có nút dẫn tới nơi mới",
    "switchLogView('security')" in section_html("security")
    and "switchLogView('kpi')" in section_html("tasks"),
    "bỏ bảng đi mà không có đường dẫn thì người dùng mất tính năng",
)

# ──────────────────────────────────────────────────────────────────────
section("Hai bảng cùng endpoint /logs/recent đã gom một")

# Đây là loại trùng lặp tệ nhất: cùng một endpoint, chắc chắn cùng dữ liệu, ở
# hai màn hình. Trước Phase 79:
#   #mon-log-list  (Bảng Điều Khiển, "Panel 11 — Nhật Ký Hệ Thống")
#   #cc-ops-log    (Trung Tâm Chỉ Huy, "Nhật Ký Vận Hành")
logs_body = section_html("logs")
check(
    "bảng nhật ký vận hành nằm ở tab Nhật Ký",
    'id="log-recent-list"' in logs_body,
    "chủ sở hữu của nhật ký phải là tab Nhật Ký",
)
check(
    "không còn bảng nhật ký thứ hai ở Bảng Điều Khiển",
    'id="mon-log-list"' not in section_html("dashboard"),
    "cùng endpoint /api/v1/logs/recent — dữ liệu chắc chắn giống nhau",
)
check(
    "không còn bảng nhật ký thứ hai ở Trung Tâm Chỉ Huy",
    'id="cc-ops-log"' not in section_html("command-center"),
    "cùng endpoint /api/v1/logs/recent — dữ liệu chắc chắn giống nhau",
)
check(
    "Bảng Điều Khiển không còn khối lối dẫn nhật ký trùng (Phase 82)",
    "switchTab('logs')" not in section_html("dashboard")
    and "switchLogView('recent')" not in section_html("dashboard"),
    "người dùng chủ động bỏ khối 'Nhật Ký Hệ Thống'/'Nhật Ký Vận Hành' — "
    "mục điều hướng bên trái và nhật ký thời gian thực trên trang vẫn đủ đường",
)
check(
    "bộ lọc ẩn heartbeat được giữ lại",
    'id="log-recent-verbose"' in logs_body,
    "bản giữ lại là bản Trung Tâm Chỉ Huy vì có bộ lọc này",
)

# Hàm cũ phục vụ #mon-log-list nay là code chết — code chết còn tệ hơn trùng lặp
# vì nó âm thầm chạy tới `if (!box) return;` rồi dừng, tưởng như đang hoạt động.
for dead in ("renderSystemLogs", "paintMonLogs", "setMonLogFilter"):
    check(
        f"hàm chết '{dead}' đã bị gỡ",
        f"function {dead}(" not in JS_CODE,
        "giữ lại = code chết, không ai gọi được mà tưởng còn hoạt động",
    )
check(
    "bộ đệm của panel đã gỡ cũng bị gỡ",
    "monLogCache" not in JS_CODE and "MON_LOG_MAX_ROWS" not in JS_CODE,
    "biến không còn ai đọc",
)
check(
    "vòng poll không còn gọi endpoint nhật ký",
    "/api/v1/logs/recent" not in JS_CODE.split("async function loadExtendedMonitors")[1].split("function renderQueueMonitor")[0],
    "nạp rồi không ai đọc = mỗi vòng poll mất một request vô ích",
)

# ──────────────────────────────────────────────────────────────────────
section("Hàng đợi phê duyệt chỉ có một bản đầy đủ")

check(
    "Bảng Điều Khiển là chủ sở hữu (có danh sách)",
    'id="dash-pending-list"' in section_html("dashboard"),
    "chủ sở hữu phải giữ danh sách đầy đủ",
)
for tab, eid in (("command-center", "cc-pending-list"), ("security", "security-pending-list")):
    if not section_html(tab):
        # Tab đã bị gỡ trong gộp sau — không còn gì để trùng.
        continue
    check(
        f"tab {tab} không còn danh sách trùng",
        f'id="{eid}"' not in section_html(tab),
        "hai bản đầy đủ lấy từ 2 endpoint khác nhau nên số liệu có thể lệch",
    )
check(
    "tab Bảo Mật còn ô đếm + nút dẫn tới chủ sở hữu",
    "dash-pending-list" in section_html("security"),
    "bỏ hẳn thì mất thông tin; phải để lại đường dẫn",
)
check(
    "loadPending() ghi vào id của chủ sở hữu",
    "$('dash-pending-list')" in JS,
    "ghi vào id đã xoá thì danh sách không bao giờ có dữ liệu",
)

# Sau khi gộp tab, ô đếm trung gian `cc-pending-count` bị gỏ cùng thẻ nó nằm
# trong (trên cùng trang với danh sách thì ô đếm chỉ là bản sao). Hàm chép
# KPI `syncCommandCenterKpi` đọc ô đó — để lại thì ô KPI rơi về nhánh dự phòng
# `|| '0'` và hiện 0 bất kể thực tế. Loại "0 bịa" này dễ tái phạm nhất.
check(
    "ô đếm trung gian cc-pending-count đã bị gỡ",
    'id="cc-pending-count"' not in HTML,
    "còn ô này thì syncCommandCenterKpi có thể quay lại chép số chết",
)
check(
    "hàm chép KPI qua ô trung gian đã bị gỡ",
    "function syncCommandCenterKpi(" not in JS_CODE,
    "đọc element đã xoá rồi fallback '0' = số 0 bịa trên ô KPI",
)
check(
    "loadPending() ghi thẳng vào ô KPI, không qua ô trung gian",
    "$('cc-kpi-pending')" in JS and "'cc-pending-count'" not in JS_CODE,
    "bỏ ô trung gian mà không nối lại thì ô KPI chết",
)

# ──────────────────────────────────────────────────────────────────────
section("Mạch ESP32 chỉ ở tab Trợ Lý Thoại")

check("tab Trợ Lý Thoại giữ danh sách ESP32", 'id="audio-nodes-list"' in section_html("voice"))
check(
    "bảng Điều Khiển không còn vẽ lại danh sách ESP32",
    'id="audio-nodes-container"' not in section_html("dashboard"),
    "cùng dữ liệu ở hai tab là chỗ dễ phát sinh lệch số liệu",
)
check(
    "Bảng Điều Khiển không còn dải tóm tắt ESP32 (Phase 82)",
    'id="badge-audio-nodes-count"' not in section_html("dashboard")
    and "switchTab('voice')" not in section_html("dashboard"),
    "dải tóm tắt đã bỏ theo yêu cầu — tab Trợ Lý Thoại là chủ duy nhất",
)
check(
    "ô đếm ESP32 chỉ ở tab Trợ Lý Thoại và chạy một hàm chung",
    JS.count("loadAudioNodes") >= 1
    and "voice-stat-nodes-val" in JS
    and "badge-audio-nodes-count" not in JS,
    "id đã gỡ khỏi HTML thì phải gỡ luôn khỏi JS để không còn code chết",
)

# ──────────────────────────────────────────────────────────────────────
section("Gộp Trung Tâm Chỉ Huy vào Bảng Điều Khiển không làm hỏng vòng đời")

# Nguy hiểm nhất của việc gộp tab không phải HTML mà là DÂY NỐI trong JS.
# `CommandCenter` có vòng đời: onEnter nạp dữ liệu + bật bộ hẹn giờ 5s, onLeave
# dừng bộ hẹn giờ. Cả hai móc vào TÊN TAB. Gộp tab mà quên sửa dây thì:
#   - onEnter không chạy  → phần C.E.O không bao giờ có dữ liệu (trắng, im lặng)
#   - onLeave không chạy  → bộ hẹn giờ 5s gọi API chạy mãi sau khi rời đi
# Hai lỗi này đều không để lại dấu vết ở console.
switch_tab = re.search(r"function switchTab\(tabId\) \{([\s\S]*?)\n\}\n", JS)
check("tìm thấy switchTab()", switch_tab is not None)
if switch_tab:
    # Bỏ comment trước khi so khớp: các đoạn giải thích vừa thêm dài hơn
    # mọi khoảng cách đặt trong regex, và chúng chứa đúng những chuỗi cần
    # tìm — so khớp thẳng vào bình luận sẽ ra kết quả ngược.
    st = re.sub(r"//.*$", "", switch_tab.group(1), flags=re.M)
    check(
        "onEnter của CommandCenter bám theo tab Bảng Điều Khiển",
        re.search(r"if \(tabId === 'dashboard'\)[\s\S]{0,300}CommandCenter\.onEnter\(\)", st)
        is not None,
        "không gọi onEnter thì điều hành AI, biểu đồ và cảnh báo không bao giờ có dữ liệu",
    )
    check(
        "onLeave của CommandCenter bám theo tab Bảng Điều Khiển",
        "_prevId === 'dashboard'" in st and "CommandCenter.onLeave()" in st,
        "không gọi onLeave thì bộ hẹn giờ 5s gọi API chạy mãi sau khi rời tab",
    )

pane_visible = re.search(r"function _paneVisible\(\) \{([\s\S]*?)\n  \}", JS_CODE)
check("tìm thấy _paneVisible()", pane_visible is not None)
check(
    "_paneVisible() hỏi đúng tab Bảng Điều Khiển",
    pane_visible is not None and "$('tab-dashboard')" in pane_visible.group(1),
    "hỏi tab đã bị gỡ thì luôn coi là hiện → vẽ biểu đồ vào canvas đang ẩn, "
    "vẽ xong rồi mất, không ai thấy",
)
check(
    "không còn tham chiếu tới pane của tab đã gỡ",
    "tab-command-center" not in JS_CODE,
    "element không tồn tại → điều kiện luôn đúng → hành vi sai âm thầm",
)
check(
    "nội dung C.E.O nằm trong Bảng Điều Khiển",
    all(
        f'id="{i}"' in section_html("dashboard")
        for i in ("cc-audit-btn", "cc-subsystems", "cc-kpi-infra")
    ),
    "mất id = gộp kiểu cắt rồi quên dán, màn hình C.E.O trắng",
)
check(
    "khung biểu đồ & tra cứu quy chế riêng đã bỏ khỏi Bảng Điều Khiển",
    all(
        f'id="{i}"' not in section_html("dashboard")
        for i in ("cc-chart-canvas", "cc-chart-title", "cc-chart-source", "cc-chart-empty",
                  "cc-policy-input", "cc-policy-result")
    ),
    "bỏ khung mà để id chết lặng trong DOM là cố tình giữ code không dùng",
)
check(
    "không còn thẻ tự trỏ tới chính trang đang đứng",
    'id="cc-pending-card"' not in HTML,
    "thẻ 'Chờ Bạn Phê Duyệt' từng trỏ sang Bảng Điều Khiển; sau khi gộp nó nằm "
    "ngay trên trang chứa danh sách mà nó trỏ tới → bấm không có gì xảy ra",
)
check(
    "tiêu đề tab không còn mục của tab đã gộp",
    all(
        f"'{t}':" not in JS_CODE.split("const TAB_TITLES")[1].split("};")[0]
        for t in ("command-center", "devices", "users")
    ),
    "mục cũ làm người đọc code tưởng tab đó vẫn còn",
)

# ──────────────────────────────────────────────────────────────────────
section("Số tab đã giảm và không còn tab rỗng")

tabs = re.findall(r'<section[^>]*id="tab-([a-z0-9-]+)"', HTML)
check(
    "còn 14 tab (từ 12; Dev Fleet, Giám sát hạ tầng, Kịch bản, Cổng Agent và Thương mại & Tuân thủ là tab thêm sau)",
    len(tabs) == 14 and "compliance" in tabs and "dev-fleet" in tabs and "infra-monitor" in tabs and "playbooks" in tabs and "agent-gateway" in tabs,
    f"hiện có {len(tabs)}: {tabs}",
)
navs = re.findall(r'id="nav-([a-z0-9-]+)"', HTML)
check(
    "không còn nút nav cho tab đã gộp",
    "users" not in navs and "devices" not in navs and "command-center" not in navs,
    f"còn: {navs}",
)
valid = re.search(r"const VALID_TABS = \[([^\]]*)\]", JS)
valid_tabs = re.findall(r"'([a-z0-9-]+)'", valid.group(1)) if valid else []
check(
    "VALID_TABS khớp đúng các section còn lại",
    set(valid_tabs) == set(tabs),
    f"VALID_TABS={sorted(valid_tabs)} | sections={sorted(tabs)}",
)
check(
    "link cũ #users / #devices tự rơi về tab hợp lệ",
    "users" not in valid_tabs and "devices" not in valid_tabs,
    "tab không có trong VALID_TABS sẽ rơi về dashboard — đây là hành vi muốn",
)

# Không để lại id trùng do di chuyển khối (lỗi rất dễ gây ra khi cắt/dán HTML).
ids = re.findall(r'\bid="([^"]+)"', HTML)
dup = sorted({i for i in ids if ids.count(i) > 1})
check("không có id nào bị trùng trong index.html", not dup, ", ".join(dup[:8]))

# Mỗi section phải cân bằng thẻ div, nếu lệch thì bố cục sẽ vỡ.
for t in tabs:
    b = section_html(t)
    check(
        f"tab {t} cân bằng thẻ",
        b.count("<div") == b.count("</div>"),
        f"thiếu {b.count('<div') - b.count('</div>')} thẻ mở",
    )

# ĐẾM THẺ KHÔNG ĐỦ. Lần gom nhật ký trước nuốt mất thẻ đóng của
# `log-view-stream`, khiến 3 chế độ còn lại lọt vào BÊN TRONG nó: wrapper đóng
# rỗng, còn terminal thời gian thực nằm ngoài mọi wrapper. Tổng số thẻ vẫn
# CÂN BẰNG nên phép đếm không bắt được — phải đo chiều sâu lồng nhau.
depth = 0
log_view_depths: dict[str, int] = {}
for line in section_html("logs").split("\n"):
    depth += line.count("<div") - line.count("</div>")
    m = re.search(r'id="log-view-([a-z]+)"', line)
    if m:
        log_view_depths[m.group(1)] = depth
check(
    "4 chế độ nhật ký nằm cùng cấp (không lồng vào nhau)",
    len(log_view_depths) == 4 and set(log_view_depths.values()) == {1},
    f"chiều sâu từng chế độ: {log_view_depths} — lồng sai nghĩa là ẩn/hiện "
    "không độc lập: chọn chế độ khác thì chế độ cũ vẫn che mất",
)

# ──────────────────────────────────────────────────────────────────────
print("\n" + "─" * 60)
print(f"Tổng: {PASSED + FAILED} | Pass: {PASSED} | Fail: {FAILED}")
if FAILURES:
    print("\n❌ CÓ LỖI:")
    for f in FAILURES:
        print(f"  - {f}")
    sys.exit(1)
else:
    print("\n✅ TẤT CẢ PASS")
