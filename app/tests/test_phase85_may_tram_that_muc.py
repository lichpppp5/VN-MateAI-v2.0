# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 Dương Thanh Lịch — VN-MateAI. See LICENSE and NOTICE.
"""
tests/test_phase85_may_tram_that_muc.py
=====================================
Phase 85 — sub-tab "Máy Trạm": bố trí lại + bỏ số liệu bịa.

Người dùng báo: tab "Tích Hợp Hệ Thống Báo Cáo" bố trí chưa hợp lý, chưa chắc
chạy đúng, phần Máy Trạm cần bố trí lại. Rà soát tìm ra 3 nhóm lỗi, tất cả
đều thuộc loại "vẫn chạy, không báo lỗi, chỉ sai âm thầm":

1. SỐ BỊA / SỐ TRÙNG
   - Hai ô "TỔNG MÁY TRẠM" và "TRỰC TUYẾN REALTIME" luôn bằng nhau, vì
     `Orchestrator.get_connected_clients()` chỉ trả máy đang mở WebSocket
     (máy rớt kết nối bị xoá khỏi registry ở `unregister_client`).
   - Thẻ "CỔNG MASTER & TLS" ghi cứng ":443 (SSL PINNED)" — không đo được, và
     sai với máy chủ đang chạy.
   - Cột "TRẠNG THÁI" in chuỗi "Trực tuyến" viết cứng, không đọc `c.status`.
   - Cột "THỜI GIAN KẾT NỘI" in `uptime` (thời lượng) — tức thời lượng bị
     đội nhãn thời điểm.
   - `loadDevices()` ghi vào `#badge-online-clients` — phần tử không tồn tại.
   - Bảng ghi "Cập nhật tự động realtime" nhưng không có bộ hẹn giờ nào.
   - Hướng dẫn dạy lệnh `wss://<IP>:443/ws/client` — lệnh không chạy được với
     máy chủ HTTP cổng 8000.

2. LỖI CHẾT DẪN ĐẾN MẤT TÍNH NĂNG
   - `download_agent` đọc `PORT` trong `config.json` (= 443) rồi ghi
     `wss://<ip>:443/ws/client` vào gói Agent. Gói tải về KHÔNG BAO GIỜ kết
     nối được, trong khi mọi thứ trên máy chủ vẫn bình thường.
   - `client_agent/agent.py` fallback về `wss://127.0.0.1:443/ws/client` và bỏ
     qua `ws_url` trong `config.json`.
   - `switchCcSubTab()` ghi tên sub-tab vào `#cc-int-header h3` — không có phần
     tử nào mang id đó, nên tên các sub-tab mở rộng không bao giờ hiện.

3. CHÈN THÔNG TIN KHÔNG ĐƯỢC ESCAPE
   - `client_id` (do Client Agent tự khai báo) nội thẳng vào HTML, và nhúng
     vào chuỗi JS trong `onclick` — máy trạm đăng ký được gửi
     `client_id` chứa `'` là thọát khỏi chuỗi, chạy được mã tuỳ ý.

Cách kiểm tra
-------------
Phần Python gọi HÀM THẬT (`_resolve_master_endpoint`, `_master_ws_url`,
`_local_worker_ws_url`) với request giả lập thay vì so chuỗi — chứng minh URL
dựng ra thực sự bám theo cổng/scheme đang chạy. Phần đọc file kiểm tra các
số liệu bịa đã bị gỡ khỏi HTML/JS.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

PASSED = 0
FAILED = 0
FAILURES: list[str] = []


def check(name: str, cond: bool, extra: str = "") -> None:
    global PASSED, FAILED
    if cond:
        PASSED += 1
    else:
        FAILED += 1
        FAILURES.append(f"  ✗ {name}" + (f" — {extra}" if extra else ""))


def section(title: str) -> None:
    print(f"\n▸ {title}")


# ── Tiện ích: bỏ comment để quét code thật ─────────────────────────────────
def strip_comments(src: str) -> str:
    """Bỏ comment theo đúng ngôn ngữ: `//` + `/* */` (JS), `#` (Python),
    `<!-- -->` (HTML).

    Bắt buộc khi kiểm tra "cái này đã bị gỡ": bình luận giải thích lý do sửa
    thường nhắc lại đúng tên/giá trị cũ (kể cả trích nguyên văn một thẻ HTML
    có `id=`), quét cả chúng sẽ khẳng định sai là nó còn tồn tại.
    """
    src = re.sub(r"/\*[\s\S]*?\*/", "", src)   # comment khối JS
    src = re.sub(r"^\s*//.*$", "", src, flags=re.M)   # comment dòng JS
    src = re.sub(r"^\s*#.*$", "", src, flags=re.M)    # comment dòng Python
    return re.sub(r"<!--[\s\S]*?-->", "", src)        # comment HTML


class _FakeURL:
    def __init__(self, scheme: str, host: str, port: int | None) -> None:
        self.scheme = scheme
        self.hostname = host
        self.port = port


class _FakeRequest:
    def __init__(self, scheme: str, host: str, port: int | None) -> None:
        self.url = _FakeURL(scheme, host, port)


def main() -> None:
    from mateai.interfaces.http.routers.workers import (
        _local_worker_ws_url,
        _master_ws_url,
        _resolve_master_endpoint,
    )

    server_src = strip_comments((ROOT / "src" / "mateai" / "interfaces" / "http" / "routers" / "workers.py").read_text(encoding="utf-8"))
    app_js = strip_comments((ROOT / "web" / "app.js").read_text(encoding="utf-8"))
    index_html = (ROOT / "web" / "index.html").read_text(encoding="utf-8")
    # Các kiểm tra "đã bị gỡ" phải quét bản ĐÃ BỎ COMMENT — nếu không thì chính
    # dòng comment giải thích lý do sửa sẽ làm test đỏ.
    index_html_code = strip_comments(index_html)
    # Chỉ khối máy trạm, để so số "số bịa" trong đúng vùng cần sạch.
    dev_start = index_html.index('<div id="cc-int-devices"')
    nxt = index_html.index("</section>", dev_start)
    # Từ f389bbe, pane Phòng Ban / Elastic Grid nằm ngay sau Máy Trạm trong cùng
    # <section>: khối Máy Trạm kết thúc ở pane kế tiếp, không phải </section>.
    _next_pane = re.search(r'<div id="cc-int-(?!devices")[a-z-]+"', index_html[dev_start + 1:nxt])
    if _next_pane:
        nxt = dev_start + 1 + _next_pane.start()
    devices_html = index_html[dev_start:nxt]
    dev_code_start = index_html_code.index('<div id="cc-int-devices"')
    devices_code = index_html_code[dev_code_start:index_html_code.index("</section>", dev_code_start)]

    # ═══ 1. URL Agent dựng từ request thật, không từ config.json ══════════
    section("URL máy trạm bám theo nơi máy chủ thật đang chạy")

    r_local = _FakeRequest("http", "127.0.0.1", 8000)
    check(
        "HTTP cổng 8000 → agent nối ws:// cổng 8000 (không phải wss://:443)",
        _master_ws_url(r_local) == "ws://127.0.0.1:8000/ws/client",
        _master_ws_url(r_local),
    )
    check(
        "agent tải về nhận đúng cổng đang chạy",
        _resolve_master_endpoint(_FakeRequest("http", "192.168.1.5", 8000))
        == ("ws", "192.168.1.5", 8000),
    )
    check(
        "worker cục bộ luôn quay về loopback kể cả khi truy cập qua IP LAN",
        _local_worker_ws_url(_FakeRequest("http", "192.168.1.5", 8000))
        == "ws://127.0.0.1:8000/ws/client",
        _local_worker_ws_url(_FakeRequest("http", "192.168.1.5", 8000)),
    )
    check(
        "0.0.0.0 (địa chỉ 'mọi giao diện') không bị dùng làm đích nối",
        "0.0.0.0" not in _master_ws_url(_FakeRequest("http", "0.0.0.0", 8000)),
    )
    check(
        "HTTPS không cổng → wss:// + 443",
        _master_ws_url(_FakeRequest("https", "vnmate.vn", None))
        == "wss://vnmate.vn:443/ws/client",
    )
    check(
        "HTTP không cổng → ws:// + 80",
        _master_ws_url(_FakeRequest("http", "vnmate.vn", None))
        == "ws://vnmate.vn:80/ws/client",
    )

    # Không nơi nào trong phần đóng gói còn đọc PORT của config.json.
    dl = server_src[server_src.index("async def download_agent("):]
    dl = dl[: dl.index("\n@app.")] if "\n@app." in dl else dl
    check(
        "download_agent() KHÔNG còn đọc `PORT` từ config.json",
        'cfg_raw.get("PORT"' not in dl and "cfg_raw" not in dl,
        "vẫn lấy cổng từ cấu hình thay vì từ request",
    )
    check(
        "download_agent() dùng cổng/scheme từ request",
        "_resolve_master_endpoint(request)" in dl,
    )
    # Bỏ `server_port = ...` mà quên chỗ dùng còn lại → NameError lúc chạy
    # thật (HTTP 500), trong khi mọi kiểm tra tĩnh vẫn xanh. Bắt riêng.
    check(
        "download_agent() không còn tham chiếu biến `server_port` đã bị gỡ",
        "server_port" not in dl,
        "còn dùng biến không tồn tại → 500 khi bấm nút Tải Agent",
    )
    # Bỏ `server_port = ...` mà quên chỗ dùng còn lại → NameError lúc chạy
    # thật (HTTP 500), trong khi mọi kiểm tra tĩnh vẫn xanh. Bắt riêng bằng AST
    # trên đúng các hàm đã sửa — quét cả file sẽ ra hàng loạt cảnh báo về code
    # cũ không liên quan (import trong `try`, `global`, hàm lồng nhau).
    import ast

    tree = ast.parse((ROOT / "src" / "mateai" / "interfaces" / "http" / "routers" / "workers.py").read_text(encoding="utf-8"))
    module_names: set[str] = set(dir(__builtins__)) | set(globals())
    for node in ast.walk(tree):
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            for a in node.names:
                module_names.add((a.asname or a.name).split(".")[0])
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            module_names.add(node.name)
        elif isinstance(node, ast.Assign):
            for t2 in node.targets:
                if isinstance(t2, ast.Name):
                    module_names.add(t2.id)

    targets = {
        "_resolve_master_endpoint",
        "_master_ws_url",
        "_local_worker_ws_url",
        "download_agent",
    }
    checked = 0
    undefined: list[str] = []
    for fn in [n for n in ast.walk(tree) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))]:
        if fn.name not in targets:
            continue
        checked += 1
        assigned: set[str] = set()
        used: list[ast.Name] = []
        for node in ast.walk(fn):
            if isinstance(node, ast.Name):
                (assigned.add(node.id) if isinstance(node.ctx, ast.Store) else used.append(node))
            elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                if node is not fn:
                    assigned.add(node.name)
            elif isinstance(node, ast.arg):
                assigned.add(node.arg)
            elif isinstance(node, ast.ExceptHandler) and node.name:
                assigned.add(node.name)
        for u in used:
            if u.id not in assigned and u.id not in module_names:
                undefined.append(f"{fn.name}:{u.lineno} {u.id}")
    check(
        "tìm thấy đủ 4 hàm liên quan để quét phạm vi biến",
        checked == 4,
        f"thấy {checked}/4",
    )
    check(
        "các hàm dựng URL/gói Agent không dùng biến chưa định nghĩa",
        not undefined,
        ", ".join(undefined[:6]),
    )
    # URL ws:// chỉ được dựng ở MộT chỗ duy nhất; nƠi khác phải gọi lại
    # hàm dựng chung để không lệch đệt nhau.
    _url_literal = 'f"{scheme}://{host}:{port}/ws/client"'
    check(
        "chề có một chỗ dựng URL ws:// (trong _master_ws_url)",
        server_src.count(_url_literal) == 1,
        f"{server_src.count(_url_literal)} chỗ",
    )
    check(
        "_local_worker_ws_url() dùng chung hàm dựng endpoint",
        re.search(r"def _local_worker_ws_url\(request[^)]*\)[\s\S]{0,900}?"
                  r"_resolve_master_endpoint\(request\)", server_src) is not None
        and "127.0.0.1:{port}" in server_src,
    )

    for f in ("client_agent/agent.py",):  # client_template/ đã gộp vào client_agent/ (Phase 6)
        agent = strip_comments((ROOT / f).read_text(encoding="utf-8"))
        check(
            f"{f} không còn fallback wss://127.0.0.1:443",
            "wss://127.0.0.1:443" not in agent,
            "vẫn trỏ về cổng không có gì lắng nghe",
        )
    ca = strip_comments((ROOT / "client_agent" / "agent.py").read_text(encoding="utf-8"))
    check(
        "client_agent/agent.py đọc `ws_url` từ config.json",
        '_cfg.get("ws_url"' in ca,
        "chạy tay bằng file này là luôn nối sai địa chỉ",
    )

    # ═══ 2. Số liệu bịa đã bị gỡ khỏi khối máy trạm ══════════════════════
    section("Không còn số liệu bịa trong khối Máy Trạm")

    check(
        "ô 'TRỰC TUYẾN REALTIME' (luôn bằng ô tổng) đã bị gỡ",
        "stat-online-clients" not in devices_code and "stat-online-clients" not in app_js,
    )
    check(
        "ô trang trí '0 ONLINE' đã bị gỡ",
        "0 ONLINE" not in devices_code and "0 NODES" not in devices_code,
    )
    check(
        "thẻ ':443 (SSL PINNED)' viết cứng đã bị gỡ",
        "SSL PINNED" not in devices_code and "CỔNG MASTER" not in devices_code,
    )
    check(
        "loadDevices() không còn ghi vào #badge-online-clients (không tồn tại)",
        "badge-online-clients" not in app_js,
    )
    check(
        "cột trạng thái không in chuỗi 'Trực tuyến' viết cứng",
        "Trực tuyến</span>" not in app_js,
        "cột còn khẳng định trạng thái thay vì đọc từ API",
    )
    check(
        "không còn mẫu lệnh agent trỏ cổng 443 trong hướng dẫn",
        "wss://&lt;IP_MAY_CHU&gt;:443" not in index_html_code
        and "wss://<IP_MAY_CHU>:443" not in index_html_code,
        "đang dạy lệnh không kết nối được với máy chủ HTTP",
    )
    check(
        "loadDevices() phân biệt 'API lỗi' với 'không có máy trạm'",
        "apiFetchClients" in app_js and "devices-load-error" in app_js,
        "lỗi mạng hiện thành '0 máy trạm' — khẳng định sai",
    )

    # ═══ 3. Các ô số liệu mới đều lấy từ nguồn thật ══════════════════════
    section("Bốn ô số liệu mới đều có nguồn thật")

    for oid in ("stat-total-clients", "stat-total-skills", "stat-local-worker", "stat-last-sync"):
        check(f"HTML có ô {oid}", f'id="{oid}"' in devices_html)

    m = re.search(r"async function loadDevices\(\) \{(.*?)\n\}", app_js, re.S)
    check("tìm thấy loadDevices()", m is not None)
    if m:
        body = m.group(1)
        check("tổng kỹ năng cộng từ `skills_count` của các máy",
              "skills_count" in body and "reduce(" in body)
        check("mốc 'Cập nhật lúc' lấy từ thời điểm tải thật",
              "stat-last-sync" in body and "toLocaleTimeString" in body)
    m2 = re.search(r"async function checkLocalWorkerStatus\(\) \{(.*?)\n\}", app_js, re.S)
    check("tìm thấy checkLocalWorkerStatus()", m2 is not None)
    if m2:
        check("ô 'Worker cục bộ' đọc từ API trạng thái, không suy đoán",
              "stat-local-worker" in m2.group(1))

    # ═══ 4. Lời hứa "realtime" phải có bộ hẹn giờ thật ═══════════════════
    section("Bảng tự làm mới — lời hứa có bằng chứng")

    check("có bộ hẹn giờ làm mới danh sách máy trạm",
          "_devicesPollStart" in app_js and "_devicesPollTimer" in app_js
          and "setInterval" in app_js)
    check("móc vào switchCcSubTab: chỉ chạy khi sub-tab Máy Trạm đang mở",
          re.search(r"function switchCcSubTab\(name\)[\s\S]{0,2600}?"
                    r"name === 'devices'[\s\S]{0,200}?_devicesPollStart\(\)", app_js) is not None)
    check("móc vào switchTab: rời tab Tích Hợp thì dừng hẳn",
          re.search(r"function switchTab\(tabId\)[\s\S]{0,1800}?_devicesPollStop\(\)", app_js) is not None)
    check("dừng hẳn khi chuyển sang sub-tab khác",
          re.search(r"\} else if \(typeof _devicesPollStop === 'function'\) \{\s*\n\s*"
                    r"_devicesPollStop\(\);", app_js) is not None)
    # switchCcSubTab là hàm DÙNG CHUNG cho mọi sub-tab. Gọi thẳng hàm của khối
    # máy trạm (không có typeof) khiến hàm này ném ReferenceError nếu khối đó
    # không được nạp — làm hỏng cả những sub-tab không liên quan. Đây chính là
    # lỗi đã lộ ra khi chạy test_command_center_phase5960.mjs (cắt riêng khối
    # CC để chạy độc lập).
    check("hàm chuyển sub-tab không phụ thuộc cứng vào phần máy trạm",
          "typeof _devicesPollStart" in app_js and "typeof _devicesPollStop" in app_js)
    check("bảng không còn tự ghi chữ 'Cập nhật tự động realtime'",
          "Cập nhật tự động realtime" not in devices_code)
    check("có ghi rõ nhịp làm mới trên bảng",
          "10s" in devices_html or "10 giây" in devices_html)

    # ═══ 5. Bố trí lại: gọn hơn, bảng là phần chính ════════════════════════
    section("Bố trí lại khối Máy Trạm")

    check("khối máy trạm gọn hơn bản cũ (192 dòng HTML)",
          devices_html.count("\n") < 192,
          f"{devices_html.count(chr(10))} dòng")
    check("hướng dẫn mặc định gập lại, mở khi bấm nút",
          re.search(r'id="devices-guide-body"[^>]*class="[^"]*hidden', devices_html) is not None
          and 'onclick="toggleDevicesGuide()"' in devices_html)
    check("hướng dẫn dựng địa chỉ máy chủ từ trang đang xem, không ghi cứng",
          "location.host" in app_js)
    check("bảng danh sách nằm TRƯỚC khối hướng dẫn trong DOM",
          devices_html.index('id="devices-table-body"') < devices_html.index('id="devices-guide-body"'))
    check("bỏ mã hiệu phát triển 'Phase 59 · Phase 60' khỏi tiêu đề khối",
          "Phase 59 · Phase 60" not in index_html_code)
    check("giữ đủ 4 nút HUD và 3 nút hành động (không mất chức năng cũ)",
          all(x in devices_html for x in (
              "triggerVisualOverlay('network_map')",
              "triggerVisualOverlay('metric_chart')",
              "triggerVisualOverlay('alert')",
              'href="/hud"',
              "downloadClientAgent()",
              "toggleLocalWorkerNode()",
              "loadDevices()",
          )))

    # ═══ 6. Không mất chức năng, không thêm id trùng ═══════════════════════
    section("Không phá chức năng cũ")

    check("vẫn lọc được danh sách", "filterDevices" in app_js
          and 'id="filter-devices-input"' in devices_html)
    check("vẫn giám sát / gửi lệnh tới máy trạm",
          "openLiveMonitor(" in app_js and "openDispatchModal(" in app_js)
    check("vẫn có ô báo lỗi tải + empty state",
          'id="devices-empty-state"' in devices_html
          and 'id="devices-load-error"' in devices_html)

    ids = re.findall(r'\bid="([^"]+)"', index_html)
    dup = sorted({v for v in ids if ids.count(v) > 1})
    check("không có id nào bị trùng trong index.html", not dup, ",".join(dup))

    # ═══ 7. Code chết: tiêu đề sub-tab phải hiện thật ═════════════════════
    section("Tiêu đề sub-tab (trước đây ghi vào phần tử không tồn tại)")

    check("HTML có #cc-int-header và ô phụ #cc-int-header-sub",
          'id="cc-int-header"' in index_html and 'id="cc-int-header-sub"' in index_html)
    check("switchCcSubTab() ghi tên sub-tab vào ô phụ",
          re.search(r"function switchCcSubTab\(name\)[\s\S]{0,2000}?"
                    r"_ccGet\('cc-int-header-sub'\)", app_js) is not None)
    check("switchCcSubTab() KHÔNG còn ghi vào '#cc-int-header h3' không tồn tại",
          "#cc-int-header h3" not in app_js)
    check("có nhãn tiếng Việt cho sub-tab cốt lõi",
          "CC_SUBTAB_LABELS" in app_js and "Máy trạm" in app_js)

    # ═══ 7. Khối máy trạm KHÔNG bị hàm vẽ nguồn dữ liệu xoá mất ══════════
    # Đây là lỗi mất hẳn tính năng, không phải lỗi hiển thị: mở sub-tab Máy
    # Trạm rồi đợi `syncRemoteDataSources()` xong là toàn bộ bảng bị thay bằng
    # "chưa có nguồn dữ liệu". Vì là lời gọi bất đồng bộ nên lúc mất lúc không —
    # kiểu lỗi khó nhất vì nhìn tưởng ngẫu nhiên.
    section("Pane viết tay không bị hàm vẽ nguồn dữ liệu xoá")

    m_hw = re.search(r"const CC_HANDWRITTEN_SUBTABS = \[([^\]]*)\]", app_js)
    check("có danh sách pane viết tay cần bảo vệ", m_hw is not None)
    listed = sorted(re.findall(r"'([a-z-]+)'", m_hw.group(1))) if m_hw else []
    check("khối máy trạm nằm trong danh sách bảo vệ", "devices" in listed, str(listed))
    # So với HTML: mọi pane có sẵn trong index.html (trừ 'conn' — vốn do JS vẽ
    # toàn bộ) đều phải được bảo vệ. Thiếu một là lần sau thêm pane mới sẽ bị
    # xoá mà không ai báo. Pane = div `cc-int-*` khởi đầu ẩn (`hidden` trong
    # class); `cc-int-header` là ô tiêu đề, không phải pane.
    panes_in_html = sorted(set(re.findall(
        r'id="cc-int-([a-z-]+)"[^>]*class="[^"]*\bhidden\b', index_html_code)))
    unprotected = [p for p in panes_in_html if p not in listed and p != "conn"]
    check(
        "mọi pane viết tay trong HTML đều được bảo vệ",
        not unprotected,
        "thiếu: " + ",".join(unprotected) + f" (HTML có: {','.join(panes_in_html)})",
    )
    check(
        "loadCcDataSourceTab() chặn NGAY đầu hàm, trước khi vẽ gì",
        re.search(
            r"async function loadCcDataSourceTab\(subTabId\) \{\s*\n"
            r"(?:[ \t]*(?://[^\n]*)?\n)*?[ \t]*if \(CC_HANDWRITTEN_SUBTABS\.includes\(subTabId\)\) return;",
            app_js,
        ) is not None,
        "chặn phải đứng trước mọi nhánh vẽ, nếu không thì vẫn xoá",
    )
    check(
        "không có chỗ nào tự gắn cờ 'đã vẽ rỗng' vào pane viết tay",
        "data-empty-rendered" not in index_html_code
        and "emptyRendered" in app_js  # cờ vẫn còn dùng, chỉ không áp vào pane này
        and re.search(r"renderEmptyDataSourceTab\('devices'\)", app_js) is None,
    )

    # ═══ 8. Escape dữ liệu máy trạm ══════════════════════════════════════
    section("Dữ liệu do Client Agent khai báo phải được escape")

    m3 = re.search(r"function renderDevicesTable\(list\) \{(.*?)\n\}\n", app_js, re.S)
    check("tìm thấy renderDevicesTable()", m3 is not None)
    if m3:
        rb = m3.group(1)
        check("client_id được escape khi hiển thị",
              "escapeHtml(c.client_id)" in rb or "escapeHtml(String(c.client_id" in rb)
        check("client_id truyền vào onclick được đóng thành chuỗi JSON rồi mới escape",
              "escapeHtml(JSON.stringify(" in rb,
              "escape kiểu HTML không chặn được thoát chuỗi JS trong onclick")
        check("hostname/ip cũng được escape",
              rb.count("escapeHtml(") >= 4)
        check("cột thời gian hiện connected_at (mốc thời gian) thật",
              "connected_at" in rb and "_fmtConnectedAt" in rb)
    check("cột header đã đổi tên đúng nghĩa",
          "KẾT NỐI LÚC" in devices_html and "THỜI GIAN KẾT NỐI" not in devices_html)

    # ── Kết quả ───────────────────────────────────────────────────────────
    print("\n" + "=" * 62)
    if FAILED:
        print("SAI:")
        for f in FAILURES:
            print(f)
    print(f"Tổng: {PASSED + FAILED} | Pass: {PASSED} | Fail: {FAILED}")
    print("=" * 62)
    if FAILED:
        sys.exit(1)
    print("\n✅ TẤT CẢ PASS")


if __name__ == "__main__":
    main()
