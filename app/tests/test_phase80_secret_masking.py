# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 Dương Thanh Lịch — VN-MateAI. See LICENSE and NOTICE.
"""
tests/test_phase80_secret_masking.py
====================================
Phase 80 — che bí mật ở các endpoint đọc cấu hình.

Vấn đề
------
`GET /api/v1/config` trả NGUYÊN VĂN khoá 9router (`llm.api_key`) và token bot
Telegram cho mọi tài khoản `manager`/`admin`. Cùng một khoá còn bị nhân bản ra
4 đường dẫn nữa nhờ các nhánh tương thích ngược: `API_KEY`,
`routing.primary.api_key`, `routing.primary.api_keys[0]`, `router.*`.

Không chỉ vậy: quét toàn hệ thống phát hiện `GET /api/v1/telegram/config`
còn tệ hơn — nó dùng `get_current_user` nên **role `viewer` chỉ được xem cũng
đọc được token bot**. Tức bất kỳ tài khoản nào cũng chiếm được quyền điều
khiển bot. Sự khác biệt này không đoán được, phải đo.

Cách sửa
--------
Trả ký hiệu `••••••••` thay cho giá trị thật, và khi ghi lại thì ký hiệu đó
được thay bằng giá trị đang lưu — để người dùng vẫn sửa được cấu hình mà
không cần nhìn thấy bí mật.

Vì sao test này quét chứ không liệt kê từng endpoint
-----------------------------------------------------
Bí mật nhân bản ra nhiều đường dẫn, và endpoint mới có thể xuất hiện bất cứ
lúc nào. Test liệt kê danh sách sẽ bỏ sót endpoint thứ ba. Nên:
  - phần logic che/khôi phục: kiểm hàm trực tiếp, kể cả ca dễ sai nhất
    (che nhầm `forbidden_keywords` vì tên chứa chữ "key")
  - phần phạm vi: quét danh sách route lấy từ chính `app.routes`, không liệt
    kê tay
"""

from __future__ import annotations

import json
import re
import sys
import tempfile
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

from mateai.interfaces.http.secret_masking import (  # noqa: E402
    _SECRET_FIELD_NAMES,
    _SECRET_MASK,
    _has_secret_value,
    _is_secret_field,
    _mask_secrets,
    _restore_masked_secrets,
)

# Tầng HTTP: server.py + các router tách ra + module che bí mật dùng chung.
_HTTP_DIR = ROOT / "src" / "mateai" / "interfaces" / "http"
SRC = "\n\n".join(
    p.read_text(encoding="utf-8")
    for p in [_HTTP_DIR / "server.py", _HTTP_DIR / "secret_masking.py", *sorted((_HTTP_DIR / "routers").glob("*.py")),
              # Supervisor P10: chuẩn hoá / ghép cấu hình chuyển sang tầng application.
              ROOT / "src" / "mateai" / "application" / "administration" / "config_service.py"]
)
SRC_CODE = re.sub(r"\"\"\"[\s\S]*?\"\"\"", "", SRC)
SRC_CODE = re.sub(r"^\s*#.*$", "", SRC_CODE, flags=re.M)
JS = (ROOT / "web" / "app.js").read_text(encoding="utf-8")
HTML = (ROOT / "web" / "index.html").read_text(encoding="utf-8")

REAL_KEY = "sk-that-is-a-real-llm-key-0001"
REAL_TG = "123456789:AAHrealTelegramBotTokenValue01"

# ──────────────────────────────────────────────────────────────────────
section("Bí mật thật không lọt ra khỏi hàm che")

cfg = {
    "llm": {"api_key": REAL_KEY, "base_url": "http://x/v1", "model_name": "M"},
    "telegram": {"bot_token": REAL_TG, "enabled": False, "admin_chat_ids": ["1"]},
    # Nhánh tương thích ngược — cùng một khoá ở nhiều đường dẫn.
    "API_KEY": REAL_KEY,
    "routing": {"primary": {"api_key": REAL_KEY, "api_keys": [REAL_KEY]}},
    "security": {"forbidden_keywords": ["rmdir /s", "drop database"]},
    "aws": {"region": "ap-southeast-1", "secret_access_key": "wJalrXUtnFEMI/K7MDENG"},
}
masked = _mask_secrets(cfg)
flat = json.dumps(masked, ensure_ascii=False)

check("không còn khoá LLM ở bất kỳ đường dẫn nào", REAL_KEY not in flat)
check("không còn token bot ở bất kỳ đường dẫn nào", REAL_TG not in flat)
check(
    "che đúng các đường dẫn nhân bản",
    masked["llm"]["api_key"] == "••••••••"
    and masked["API_KEY"] == "••••••••"
    and masked["routing"]["primary"]["api_key"] == "••••••••"
    and masked["routing"]["primary"]["api_keys"] == ["••••••••"],
    json.dumps(masked.get("routing"), ensure_ascii=False),
)
check(
    "che khoá của connector",
    masked["aws"]["secret_access_key"] == "••••••••",
)
check(
    "KHÔNG che nhầm chính sách bảo mật",
    masked["security"]["forbidden_keywords"] == ["rmdir /s", "drop database"],
    "tên `forbidden_keywords` chứa chữ 'key' — lọc bằng chuỗi con sẽ che mất "
    "danh sách lệnh nguy hiểm bị chặn, làm hỏng Zero-Trust",
)
check(
    "KHÔNG che nhầm trường không bí mật",
    masked["llm"]["base_url"] == "http://x/v1"
    and masked["llm"]["model_name"] == "M"
    and masked["aws"]["region"] == "ap-southeast-1"
    and masked["telegram"]["enabled"] is False
    and masked["telegram"]["admin_chat_ids"] == ["1"],
    json.dumps(masked, ensure_ascii=False)[:200],
)
check(
    "bản gốc KHÔNG bị sửa tại chỗ",
    cfg["llm"]["api_key"] == REAL_KEY,
    "che tại chỗ là hỏng config.json khi endpoint chỉ đọc",
)

# ──────────────────────────────────────────────────────────────────────
section("Phân biệt 'chưa cấu hình' với 'đã lưu, không tiện hiện'")

check("trường rỗng vẫn hiện rỗng", _mask_secrets({"api_key": ""})["api_key"] == "")
check("trường toàn khoảng trắng vẫn hiện rỗng", _mask_secrets({"api_key": "   "})["api_key"] == "   ")
check(
    "giá trị đã là ký hiệu thì giữ nguyên, không che lần hai",
    _mask_secrets({"api_key": "••••••••"})["api_key"] == "••••••••",
)
check(
    "cờ bool không bị che",
    _mask_secrets({"api_key": True})["api_key"] is True,
    "che cờ bật/tắt thành ký hiệu sẽ làm hỏng công tắc trên giao diện",
)
check("danh sách rỗng không bị che", _mask_secrets({"api_keys": []})["api_keys"] == [])

# ──────────────────────────────────────────────────────────────────────
section("Ghi lại không làm hỏng bí mật đang lưu")

stored = {"llm": {"api_key": REAL_KEY}, "telegram": {"bot_token": REAL_TG}}

out = _restore_masked_secrets({"llm": {"api_key": "••••••••"}}, stored)
check(
    "ký hiệu gửi lên được thay bằng khoá đang lưu",
    out["llm"]["api_key"] == REAL_KEY,
    "thiếu bước này thì mỗi lần bấm Lưu vì lý do khác sẽ ghi ký hiệu đè lên "
    "khoá thật, và LLM hỏng mà không có lỗi nào báo ra",
)

out2 = _restore_masked_secrets({"telegram": {"bot_token": "••••••••"}}, stored)
check("áp dụng cho token bot", out2["telegram"]["bot_token"] == REAL_TG)

out3 = _restore_masked_secrets({"llm": {"api_key": "sk-moi-123"}}, stored)
check(
    "khoá MỚI vẫn được lưu",
    out3["llm"]["api_key"] == "sk-moi-123",
    "nếu lọc nhầm mọi giá trị thành ký hiệu thì không bao giờ đổi được khoá",
)

out4 = _restore_masked_secrets({"llm": {"base_url": "http://moi/v1"}}, stored)
check("trường thường đi qua nguyên vẹn", out4["llm"]["base_url"] == "http://moi/v1")

out5 = _restore_masked_secrets({"llm": {"api_key": "••••••••"}}, {"llm": {}})
check(
    "không có khoá cũ thì giữ ký hiệu, không bịa giá trị",
    out5["llm"]["api_key"] == "••••••••",
    "tự điền một giá trị khi chưa có khoá nào là bịa dữ liệu",
)

out6 = _restore_masked_secrets(
    {"routing": {"primary": {"api_keys": ["••••••••"]}}},
    {"routing": {"primary": {"api_keys": [REAL_KEY]}}},
)
check(
    "danh sách khoá cũng được khôi phục",
    out6["routing"]["primary"]["api_keys"] == [REAL_KEY],
)

# Trường hợp ĐÃ VỀ THỰC TẾ gây mất dữ liệu: client KHÔNG gửi field bí mật.
# `merged = {**existing, **payload}` ghép NÔNG, nên `payload["telegram"]` thay
# thế trọn khối telegram — token biến mất khỏi config.json trong khi người
# dùng chỉ định sửa một trường khác. Bỏ field ở client KHÔNG cứu được.
out7 = _restore_masked_secrets(
    {"telegram": {"admin_chat_ids": ["6112139112"]}},
    {"telegram": {"bot_token": REAL_TG, "admin_chat_ids": ["999"]}},
)
check(
    "bí mật KHÔNG được gửi lên thì vẫn được giữ",
    out7["telegram"].get("bot_token") == REAL_TG,
    "ghép nông sẽ xoá token bot khỏi config.json mà không hỏi",
)
check(
    "trường thường trong khối đó vẫn cập nhật theo người dùng",
    out7["telegram"]["admin_chat_ids"] == ["6112139112"],
)
out8 = _restore_masked_secrets({"telegram": {}}, {"telegram": {"bot_token": REAL_TG}})
check("khối rỗng cũng giữ bí mật", out8["telegram"].get("bot_token") == REAL_TG)
out9 = _restore_masked_secrets(
    {"telegram": {"bot_token": ""}},
    {"telegram": {"bot_token": REAL_TG}},
)
check(
    "bí mật gửi lên rỗng cũng giữ, không xoá",
    out9["telegram"].get("bot_token") == REAL_TG,
    "gửi chuỗi rỗng khiến khoá bị xoá trong khi người dùng không hề nhập gì",
)
out10 = _restore_masked_secrets({"telegram": {"bot_token": "999:AAAmới"}}, {"telegram": {}})
check(
    "không có bản lưu thì không tự chế giá trị",
    out10["telegram"]["bot_token"] == "999:AAAmới",
    "ghi đè giá trị người dùng vừa nhập là mất dữ liệu",
)
check(
    "không tự thêm bí mật vào chỗ chưa từng có",
    "api_key" not in _restore_masked_secrets(
        {"llm": {"model_name": "M"}}, {"llm": {"model_name": "M"}}
    ),
    "tự điền khoá giả = bịa dữ liệu",
)

# ──────────────────────────────────────────────────────────────────────
section("Endpoint không còn trả giá trị thật")

for name, marker in (
    ("GET /api/v1/config che trước khi trả", "_mask_secrets({k: v for k, v in data.items()"),
    ("GET /api/v1/telegram/config che bot_token", '_mask_secrets(dict(tg_data))'),
):
    check(name, marker in SRC_CODE)

# Che ở CỬA CUỐI: các nhánh tương thích ngược nhân bản cùng một khoá ra nhiều
# chỗ, che từng nhánh thì dễ sót. Che sau khi dựng xong mới đảm bảo hết.
get_cfg = re.search(r"async def get_config\([\s\S]*?return _mask_secrets\(", SRC)
check("get_config che ở bước trả về cuối cùng", get_cfg is not None)
check(
    "get_config không còn tự mô tả là trả khoá thật",
    "Sensitive fields (API keys) are returned" not in SRC,
    "docstring còn nói trả khoá thật thì người đọc tưởng đã an toàn",
)

check(
    "save_config khôi phục bí mật TRƯỚC khi chuẩn hoá",
    SRC.index("payload = _restore_masked_secrets(payload, existing)")
    < SRC.index('if "llm" in payload and isinstance(payload["llm"], dict)'),
    "làm sau khi chuẩn hoá thì nhánh `or existing...` sẽ thấy ký hiệu là "
    "chân trị và ghi đè khoá thật",
)

# ──────────────────────────────────────────────────────────────────────
section("Phạm vi: không endpoint nào rò (quét tự động)")

# Liệt kê tay thì sót endpoint thứ ba. Lấy từ chính app.routes.
try:
    from mateai.interfaces.http.server import app

    get_routes = sorted(
        {
            r.path
            for r in app.routes
            if "GET" in (getattr(r, "methods", None) or set())
            and "{" not in getattr(r, "path", "")
            and not getattr(r, "path", "").startswith(("/static", "/docs", "/openapi", "/redoc"))
        }
    )
except Exception as exc:  # noqa: BLE001
    get_routes = []
    check("lấy được danh sách route từ app", False, str(exc))

check("có danh sách route để quét", len(get_routes) > 20, f"chỉ {len(get_routes)} route")

# KHÔNG suy ra "route có đọc config thì phải che" — cách đó báo đỏ oang oát.
# `/api/v1/domain/config` có đọc config.json nhưng chỉ trả `{enabled: bool}`;
# `/api/v1/connectors/catalog` chỉ trả TÊN trường trong JSON Schema;
# `/api/v1/download-agent` nhúng `enrollment_token` — đó là bí mật khác, cố ý
# phát cho admin/manager để agent đăng ký, không phải khoá LLM.
#
# Nên kiểm điều duy nhất thực sự quan trọng: KHÔNG route nào trả giá trị thật
# của bí mật trong config.json. Cách đúng là gọi thật từng route (xem khối
# "quét lúc chạy" ở dưới).
# Route nằm ở server.py hoặc đã tách sang interfaces/http/routers/*.py.
_HTTP_SOURCES = [SRC] + [
    _p.read_text(encoding="utf-8")
    for _p in sorted((ROOT / "src" / "mateai" / "interfaces" / "http" / "routers").glob("*.py"))
]


def _route_block(path: str) -> str:
    for _src in _HTTP_SOURCES:
        m = re.search(
            rf'@(?:app|router)\.get\(\s*"{re.escape(path)}"[\s\S]*?(?=@(?:app|router)\.(?:get|post|put|delete)\(|\Z)',
            _src,
        )
        if m:
            return m.group(0)
    return ""


# Route biết là chỉ trả một phần không nhạy cảm — ghi rõ lý do để người đọc
# thấy đây là kết luận có cơ sở chứ không phải chỗ nào cũng bỏ qua.
for path, why in (
    ("/api/v1/domain/config", "chỉ trả {enabled: bool}"),
    ("/api/v1/enterprise/connectors/catalog", "chỉ trả tên trường trong JSON Schema"),
    ("/api/v1/report-templates", "chỉ trả mẫu báo cáo"),
    ("/api/v1/download-agent", "chỉ nhúng enrollment_token riêng, cố ý cho agent"),
):
    blk = _route_block(path)
    check(
        f"{path} không trả nguyên khối cấu hình ({why})",
        bool(blk) and "_mask_secrets" not in blk,
        "nếu route này bắt đầu trả cả khối cấu hình thì phải thêm _mask_secrets",
    )

# ──────────────────────────────────────────────────────────────────────
section("Quét lúc chạy: gọi thật mọi route, mọi role")

# Đây mới là phép kiểm có giá trị nhất. Phần tĩnh ở trên chỉ bảo đảm hai
# endpoint đã biết; endpoint thứ ba sinh ra sau này thì phải bắt bằng cách
# gọi thật. Mỗi role gọi riêng vì `/api/v1/telegram/config` dùng
# `get_current_user` — hẹp rộng khác nhau không thể suy ra từ decorator.
import urllib.error
import urllib.request

BASE = "http://127.0.0.1:8000"
try:
    with urllib.request.urlopen(f"{BASE}/api/v1/health-dashboard", timeout=4) as _r:
        SERVER_UP = True
except Exception:  # noqa: BLE001
    SERVER_UP = False

if not SERVER_UP:
    # Không giả vờ pass. Ghi rõ là CHƯA kiểm tra — đúng nguyên tắc của dự án:
    # trạng thái chưa biết thì nói chưa biết, đừng đoán là an toàn.
    print("  ⏭  CHƯA kiểm tra: server không chạy ở 127.0.0.1:8000")
    print("      (chạy `python3 -m uvicorn mateai.interfaces.http.server:app --port 8000` rồi chạy lại)")
else:

    def _login(user: str, pwd: str) -> str:
        req = urllib.request.Request(
            f"{BASE}/api/v1/login",
            data=json.dumps({"username": user, "password": pwd}).encode(),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=10) as r:
            return json.loads(r.read()).get("access_token", "")

    # Bí mật lấy từ config.json — chính là thứ không được lọt ra ngoài.
    live_cfg = json.loads((ROOT / "config.json").read_text(encoding="utf-8"))
    live_secrets: dict[str, str] = {}

    def _collect(node, path=""):
        if isinstance(node, dict):
            for k, v in node.items():
                p = f"{path}.{k}" if path else k
                if _is_secret_field(k) and isinstance(v, str) and len(v.strip()) >= 8:
                    live_secrets[p] = v
                _collect(v, p)
        elif isinstance(node, list):
            for i, v in enumerate(node):
                _collect(v, f"{path}[{i}]")

    _collect(live_cfg)
    check("đọc được bí mật thật từ config.json để so sánh", len(live_secrets) > 0)

    leaks: list[tuple[str, str, list[str]]] = []
    scanned = 0
    for role, pwd in (("admin", "admin123"), ("manager", "manager123"), ("viewer", "viewer123")):
        try:
            tok = _login(role, pwd)
        except Exception:  # noqa: BLE001
            continue
        for path in get_routes:
            req = urllib.request.Request(f"{BASE}{path}", headers={"Authorization": f"Bearer {tok}"})
            try:
                with urllib.request.urlopen(req, timeout=10) as r:
                    body = r.read().decode("utf-8", "replace")
            except urllib.error.HTTPError:
                continue
            except Exception:  # noqa: BLE001
                continue
            scanned += 1
            hit = [sp for sp, sv in live_secrets.items() if sv in body]
            if hit:
                leaks.append((role, path, hit))

    check(f"quét được {scanned} lời gọi thật", scanned > 30, f"chỉ {scanned}")
    check(
        "KHÔNG route nào trả giá trị thật của bí mật",
        not leaks,
        "; ".join(f"role={r} {p} → {h}" for r, p, h in leaks),
    )


# ──────────────────────────────────────────────────────────────────────
section("Giao diện không đưa bí mật vào DOM")

# Che ở server là chưa đủ. `loadConfig()` trước đây đổ thẳng giá trị vào
# `value` của ô input — tức bí mật nằm trong DOM của trang, ai mở DevTools
# là thấy dù server đã che. Phải sửa cả hai đầu.
check(
    "ô khoá LLM luôn để trống",
    re.search(r"keyEl\s*=.*\n\s*if \(keyEl\) keyEl\.value = ''", JS) is not None,
    "điền ký hiệu vào ô nhìn không sao, nhưng điền GIÁ TRỊ THẬT thì bí mật "
    "nằm trong DOM — ai mở DevTools là thấy, kể cả khi server đã che",
)
check(
    "ô khoá Groq để trống",
    "setVal('cfg-groq-key', '')" in JS,
    "GROQ_API_KEY cũng là bí mật",
)
check(
    "ô token Telegram để trống",
    "setVal('cfg-tg-token', '')" in JS,
    "token bot là bí mật",
)
check(
    "ô khoá ẩn legacy cũng để trống",
    "primaryKeyEl.value = ''" in JS,
)
check(
    "loadTelegramConfig không điền token từ response",
    not re.search(r"inputToken\.value = cfg\.bot_token", JS),
    "endpoint telegram/config đã che, nhưng điền ký hiệu vào ô vẫn vô nghĩa; "
    "nếu sau này ai đó bỏ che ở endpoint thì bí mật lại nằm trong DOM",
)
check(
    "có ghi chú nói rõ để trống nghĩa là giữ khoá",
    'id="cfg-llm-key-hint"' in HTML and "cfg-llm-key-hint" in JS,
    "không có ghi chú thì người dùng tưởng phải nhập lại khoá mỗi lần mở trang",
)
check(
    "hằng số ký hiệu ở frontend khớp byte-for-byte với server",
    f'const SECRET_MASK = \'{_SECRET_MASK}\';' in JS,
    "lệch một ký tự là server hiểu ký hiệu của client là khoá thật và lưu nó xuống đĩa",
)

# ──────────────────────────────────────────────────────────────────────
section("Nhật ký không ghi bí mật ra")

# PHÁT HIỆN KHI QUÉT RÒ RỈ: `httpx` ở mức INFO ghi nguyên dòng request, mà
# URL Telegram Bot API có dạng `/bot<token>/sendMessage`. Mỗi lần bot gửi tin
# là token nằm trong log, và `/api/v1/logs/recent` phục vụ log đó cho MỌI
# tài khoản — kể cả `viewer`. Che ở endpoint cấu hình không có tác dụng.
import io
import logging

buf = io.StringIO()
handler = logging.StreamHandler(buf)
handler.setLevel(logging.DEBUG)
handler.setFormatter(logging.Formatter("%(name)s | %(message)s"))
root = logging.getLogger()
_saved_handlers, _saved_level = root.handlers[:], root.level
root.handlers = [handler]
root.setLevel(logging.DEBUG)

try:
    from mateai.interfaces.http.log_stream import _install_secret_redaction

    _install_secret_redaction()
    # Ghi từ LOGGER CON — đúng trường hợp của httpx. Bộ lọc gắn lên logger
    # gốc sẽ KHÔNG bắt được; phải gắn lên handler.
    child = logging.getLogger("httpx")
    child.setLevel(logging.INFO)
    child.info(
        "HTTP Request: POST https://api.telegram.org/"
        f"bot{REAL_TG}/sendMessage 200 OK"
    )
    child.info("HTTP Request: POST https://api.router/v1/chat/completions")
    child.info(f"dung khoa {REAL_KEY} de goi")
    out = buf.getvalue()
finally:
    root.handlers = _saved_handlers
    root.setLevel(_saved_level)

check("token bot trong dòng log của httpx đã bị che", REAL_TG not in out, out[:200])
check(
    "khoá dạng sk- trong dòng log cũng bị che",
    REAL_KEY not in out,
    "bỏ sót thì token bot che xong thì khoá LLM lại lộ qua log",
)
check("dòng log bình thường vẫn còn nguyên", "chat/completions" in out)
check("bộ lọc không nuốt mất thông tin", "dung khoa" in out)

# ──────────────────────────────────────────────────────────────────────
section("Ghép sâu: không mất trường mà form không gửi")

# Lỗi đã xảy ra thật: `{**existing, **payload}` ghép nông, `payload["telegram"]`
# thay trọn khối telegram — bấm "Lưu" ở tab Cấu Hình xoá token bot VÀ tắt
# cờ `enabled`, trong khi người dùng chỉ định sửa một trường khác.
from mateai.interfaces.http.routers.config import _deep_merge  # noqa: E402

base = {
    "telegram": {"bot_token": REAL_TG, "enabled": True, "admin_chat_ids": ["1"]},
    "llm": {"model_name": "M", "api_key": REAL_KEY, "router_models": ["a", "b"]},
}
merged = _deep_merge(
    base, {"telegram": {"admin_chat_ids": ["9"]}, "llm": {"model_name": "M2"}}
)
check(
    "khoá bot giữ nguyên khi khối telegram được cập nhật",
    merged["telegram"]["bot_token"] == REAL_TG,
    "ghép nông sẽ xoá token khỏi config.json",
)
check(
    "cờ enabled giữ nguyên",
    merged["telegram"]["enabled"] is True,
    "mất cờ này là tắt gateway Telegram khi người dùng chỉ sửa danh sách admin",
)
check(
    "trường người dùng sửa thì vẫn được cập nhật",
    merged["telegram"]["admin_chat_ids"] == ["9"],
)
check("trường lồng sâu cũng cập nhật", merged["llm"]["model_name"] == "M2")
check("khoá LLM giữ nguyên", merged["llm"]["api_key"] == REAL_KEY)
check(
    "danh sách THAY chứ không nối",
    _deep_merge({"a": [1, 2]}, {"a": [3]})["a"] == [3],
    "nối sẽ ra kết quả sai với ý 'danh sách này là danh sách này'",
)
check(
    "khóa mới trong khối con vẫn thêm được",
    _deep_merge({"a": {"x": 1}}, {"a": {"y": 2}})["a"] == {"x": 1, "y": 2},
)
check(
    "save_config dùng ghép sâu",
    "merged = deep_merge(" in SRC,
    "còn `{**existing, **payload}` là mất trường của mọi khối con",
)

# ──────────────────────────────────────────────────────────────────────
section("Tập bí mật không trùng với tập trường của connector")

# Trùng lặp danh sách tên bí mật ở hai nơi thì sớm trôi lệch. Test này
# chỉ khẳng định quan hệ lập phương (subset), không thay đổi hành vi.
from mateai.application.enterprise.integrations import _CONNECTOR_SECRET_FIELDS  # noqa: E402

check(
    "mọi trường bí mật của connector đều nằm trong tập che chung",
    _CONNECTOR_SECRET_FIELDS <= set(_SECRET_FIELD_NAMES),
    f"lệch: {sorted(_CONNECTOR_SECRET_FIELDS - set(_SECRET_FIELD_NAMES))} — "
    "connector khai báo bí mật mà hàm che không biết, tức sẽ lộ",
)



# ══ Phase 81: ô mật khẩu phải NÓI ra là đã lưu, không để người dùng tưởng mất ══
# Người dùng phản ánh: dán API key vào rồi bấm F5 là mất. Đo thật: khoá CÓ
# trong config.json, nhưng ô luôn trống sau mỗi lần tải trang (đúng — không
# gửi bí mật về trình duyệt), nên nhìn hệt lúc chưa lưu.
section("Giao diện báo được tình trạng ô mật khẩu")
appjs2 = (ROOT / "web" / "app.js").read_text(encoding="utf-8")
idx2 = (ROOT / "web" / "index.html").read_text(encoding="utf-8")
check("có hàm báo trạng thái dùng chung", "function _ccSecretStatus" in appjs2)
check("có hàm nhận biết khoá đã lưu", "function _ccHasStoredKey" in appjs2)
# Ô trống mới là lúc KHÔNG có khoá. Ký hiệu che là bằng chứng ĐÃ CÓ — nếu bỏ
# nó đi trước khi kiểm thì hàm luôn trả false và báo sai cho mọi người.
haskey = appjs2.split("function _ccHasStoredKey", 1)[-1].split("\nfunction ", 1)[0]
check("coi ký hiệu che là ĐÃ CÓ khoá", "_ccSafeField" not in haskey,
      "bỏ ký hiệu che trước khi kiểm -> luôn false")
check("chỉ coi là có khoá khi giá trị không rỗng", 'k.trim() !== ""' in haskey)
for oid in ("ai-llm-key-hint", "ai-groq-key-hint", "cfg-llm-key-hint"):
    check(f"có chỗ gợi ý cho ô {oid}", f'id="{oid}"' in idx2)
check("báo ngay sau khi bấm Lưu", "✔ Đã lưu khoá" in appjs2)
check("giải thích luôn việc ô trống sau F5",
      "F5" in appjs2 and "vẫn trống" in appjs2,
      "không nói rõ thì người dùng tưởng mất khoá")
# Không được khẳng định "đã lưu khoá" khi người dùng không gõ gì.
check("chỉ nói 'vừa lưu' khi thật sự gõ khoá",
      "vuaGao" in appjs2 and "|| true" not in appjs2.split("vuaGao")[0][-200:],
      "có ép kết quả luôn đúng")

# ──────────────────────────────────────────────────────────────────────
section("Không khoá thật nằm trong template hoặc mã nguồn")

# `config.example.json` là file mang dáng cấu hình DUY NHẤT theo dõi bởi git —
# `config.json` và `users.json` đều bị .gitignore. Khoá thật điền vào example
# là tự đẩy bí mật lên GitHub. Phải kiểm tra bằng mẫu "giống khoá" chứ không
# liệt kê tên trường: khoá nhân bản ra nhiều kiểu (`API_KEY`, `bot_token`,
# `secret_access_key`) và dạng mới xuất hiện bất kỳ lúc nào.
EX = (ROOT / "config.example.json").read_text(encoding="utf-8")

def _key_like_hits(text: str) -> list[str]:
    pats = [
        ("sk-", re.compile(r"sk-[A-Za-z0-9_\-]{15,}")),          # OpenAI / 9router
        ("gsk_", re.compile(r"gsk_[A-Za-z0-9_\-]{15,}")),        # Groq
        ("AKIA", re.compile(r"\bAKIA[0-9A-Z]{16}\b")),           # AWS access key
        ("tg-token", re.compile(r"\d{8,}:[A-Za-z0-9_\-]{30,}")),  # Telegram bot
    ]
    return [name for name, pat in pats if pat.search(text)]

hits_ex = _key_like_hits(EX)
check("config.example.json không chứa chuỗi giống khoá thật", not hits_ex, str(hits_ex))
for marker in ("YOUR_9ROUTER_KEY_HERE", "YOUR_GROQ_API_KEY_HERE", "YOUR_TELEGRAM_BOT_TOKEN_HERE"):
    check(f"template giữ placeholder {marker}", marker in EX)

# Mã nguồn cũng vậy: ghi khoá thẳng vào core/ hay web/ là khoá "cố định trong
# core" — đổi khoá phải sửa code. `sk-dummy` là mặc định rõ ràng, được phép.
hardcoded: list[tuple[str, list[str]]] = []
for _rel in ("src/mateai/application/agent/llm_engine.py", "src/mateai/infrastructure/llm/llm_provider.py",
             "src/mateai/config/loader.py", "src/mateai/interfaces/http/server.py",
             "src/mateai/infrastructure/audio/audio_processor.py", "src/mateai/application/skills/meta_architect.py",
             "src/mateai/application/operations/health_monitor.py", "src/mateai/application/skills/builtin/integration_tools.py",
             "src/mateai/application/skills/builtin/ai_delegation.py", "web/app.js", "web/index.html"):
    _raw = (ROOT / _rel).read_text(encoding="utf-8")
    _hits = _key_like_hits(_raw.replace("sk-dummy", ""))
    if _hits:
        hardcoded.append((_rel, _hits))
check("không file mã nguồn nào ghi cứng chuỗi khoá",
      not hardcoded,
      "; ".join(f"{f} → {h}" for f, h in hardcoded))

# ──────────────────────────────────────────────────────────────────────
print("\n" + "─" * 60)
print(f"Tổng: {PASSED + FAILED} | Pass: {PASSED} | Fail: {FAILED}")
if FAILURES:
    print("\n❌ CÓ LỖI:")
    for f in FAILURES:
        print(f"  - {f}")
    sys.exit(1)
print("\n✅ TẤT CẢ PASS")
