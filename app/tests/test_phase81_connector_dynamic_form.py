# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 Dương Thanh Lịch — VN-MateAI. See LICENSE and NOTICE.
"""
tests/test_phase81_connector_dynamic_form.py
============================================
Phase 81 — form connector trong portal SINH TỰ ĐỘNG từ JSON Schema.

Bối cảnh
---------
Phase 78 dựng app Admin (Next.js) có DynamicForm sinh form từ schema, nhưng
nút Lưu ở đó không chạy (không có endpoint ghi). Phase 79/80 lại gỡ hết
trùng lặp khỏi portal. Phase 81 gỡ app Admin và chuyển phần đáng giữ —
DynamicForm — về portal.

Bản form viết tay trong portal bị thay vì có lỗi thật, không phải vì "dài":
id của 7 trường không khớp tên khóa cấu hình. `saveConnectorConfig` bóc key
từ id, nên:

    cfg-aws-access-key      → ghi khoá "access_key"      (thật: access_key_id)
    cfg-aws-secret-key      → ghi khoá "secret_key"      (thật: secret_access_key)
    cfg-aws-cost-explorer   → ghi khoá "cost_explorer"   (thật: cost_explorer_enabled)
    cfg-oci-compartment     → ghi khoá "compartment"     (thật: compartment_id)
    cfg-paperless-url       → ghi khoá "url"            (thật: base_url)
    cfg-paperless-token     → ghi khoá "token"          (thật: api_token)
    cfg-einvoice-url        → ghi khoá "url"            (thật: base_url)

Những ô đó không bao giờ được nạp cũng không bao giờ được lưu đúng chỗ —
connector đọc khoá khác nên không thấy gì. Form sinh từ schema thì id
lấy từ chính tên khoá nên không thể lệch.

Vì sao test này kiểm hàm JS bằng cách đọc mã nguồn
---------------------------------------------------
`renderConnectorForms` dựng HTML bằng chuỗi, không có React để render.
Cách kiểm duy nhất không nói dối là so khớp mã nguồn + kiểm hàm Python sinh
ra schema thật. Phần "form sinh ra đúng id" đã kiểm bằng trình duyệt; ở đây
chặn tái phạm bằng cách canh các bất biến dễ vỡ.
"""

from __future__ import annotations

import json
import re
import sys
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
JS = (ROOT / "web" / "app.js").read_text(encoding="utf-8")
HTML = (ROOT / "web" / "index.html").read_text(encoding="utf-8")
JS_CODE = re.sub(r"//.*$", "", JS, flags=re.M)

from mateai.infrastructure.connectors import CONNECTOR_REGISTRY  # noqa: E402
from mateai.infrastructure.connectors.base_connector import (  # noqa: E402
    CONNECTOR_DEFAULTS,
    CONNECTOR_REQUIRED_FIELDS,
)
from mateai.application.enterprise.integrations import (  # noqa: E402
    _CONNECTOR_SECRET_FIELDS,
    _build_connector_config_schema,
)
from mateai.interfaces.http.secret_masking import _is_secret_field  # noqa: E402

# ──────────────────────────────────────────────────────────────────────
section("Form không còn viết tay")

check(
    "không còn input connector viết cứng trong HTML",
    not re.search(r'id="cfg-(aws|oci|paperless|einvoice)-', HTML),
    "còn id viết tay thì hai nguồn sự thật cho cùng một form",
)
check(
    "có container để form sinh vào",
    'id="cc-connector-config-forms"' in HTML,
    "thiếu container thì không có chỗ để dựng form",
)
check(
    "container nằm trong sub-tab Cấu Hình",
    re.search(r'id="cc-int-config"[\s\S]{0,2000}?id="cc-connector-config-forms"', HTML) is not None,
    "form sinh ra chỗ khác thì người dùng không thấy",
)
check(
    "không còn form connector trùng lặp trong app Admin",
    not (ROOT / "admin" / "app" / "admin" / "plugins").exists(),
    "trang plugins cũ trong admin còn tồn tại thì vẫn là nguồn trùng lặp",
)
check(
    "không còn nút mở Admin trong menu portal",
    "nav-admin-center" not in HTML and "/admin/dashboard" not in HTML,
    "link chết tới trang đã xoá",
)
check(
    "server không còn phục vụ /admin",
    "_ADMIN_DIST" not in (ROOT / "src" / "mateai" / "interfaces" / "http" / "server.py").read_text(encoding="utf-8")
    or "/admin/{rel:path}" not in (ROOT / "src" / "mateai" / "interfaces" / "http" / "server.py").read_text(encoding="utf-8"),
    "route còn treo thì /admin trả 404 chứ không nói rõ là đã gỡ",
)

# ──────────────────────────────────────────────────────────────────────
section("id form phải khớp đúng tên khóa cấu hình")

# Đây là bất biến quan trọng nhất: `saveConnectorConfig` bóc key từ id
# (`el.id.replace('cfg-<tên>-','').replace(/-/g,'_')`) và `loadConnectorConfig`
# tìm ô theo đúng quy ước ấy. Lệch một ký tự là ô đó không bao giờ được nạp
# cũng không bao giờ được lưu.
check(
    "id sinh từ chính tên khoá, không có bảng tra cứu thủ công",
    re.search(
        r"function _ccFieldId\(connector, key\) \{\s*return `cfg-\$\{connector\}-\$\{key\.replace\(/_/g, '-'\)\}`;",
        JS,
    )
    is not None,
    "nếu id dựng tay thì lại có chỗ để lệch tên",
)
check(
    "mọi connector trong registry đều sinh được id",
    all(
        re.fullmatch(r"cfg-" + re.escape(c) + "-[a-z0-9-]+", f"cfg-{c}-{k.replace('_', '-')}")
        for c in CONNECTOR_REGISTRY
        for k in CONNECTOR_REQUIRED_FIELDS.get(c, ())
    ),
)
check(
    "bộ lưu bóc key từ id theo đúng quy ước đó",
    re.search(
        r"el\.id\.replace\(`cfg-\$\{connectorName\}-`, ''\)\.replace\(/-/g, '_'\)", JS
    )
    is not None,
    "hai bên phải dùng CHUNG một quy ước",
)
check(
    "bộ nạp tìm ô theo cùng quy ước",
    "_ccGet(`cfg-${connectorName}-${key.replace(/_/g, '-')}`)" in JS,
    "lệch ở đây thì ô không bao giờ được điền giá trị",
)

# ──────────────────────────────────────────────────────────────────────
section("Schema sinh ra phải đủ để dựng form")

for name in sorted(CONNECTOR_REGISTRY):
    schema = _build_connector_config_schema(name)
    props = schema.get("properties", {})
    required = set(schema.get("required", []))
    declared = set(CONNECTOR_REQUIRED_FIELDS.get(name, ()))

    check(
        f"{name}: mọi trường khai báo đều có trong schema",
        declared <= set(props),
        f"thiếu: {sorted(declared - set(props))}",
    )
    check(
        f"{name}: field bắt buộc nằm trong required",
        required == declared,
        f"required={sorted(required)} vs khai báo={sorted(declared)}",
    )
    check(
        f"{name}: field bắt buộc được đánh dấu",
        all(props[k].get("format") == "secret" for k in required if k in _CONNECTOR_SECRET_FIELDS)
        or not (required & _CONNECTOR_SECRET_FIELDS),
        "thiếu format=secret thì DynamicForm hiện field bí mật dạng văn bản thường",
    )
    check(
        f"{name}: không lộ giá trị bí mật trong schema",
        not any(
            isinstance(p.get("default"), str) and _is_secret_field(k)
            for k, p in props.items()
        ),
        "schema là dữ liệu công khai cho client — không được chứa khoá thật",
    )
    check(
        f"{name}: kiểu field đều là loại form biết dựng",
        all(
            p.get("type") in ("string", "number", "boolean") or p.get("ui", {}).get("widget") == "hidden"
            for p in props.values()
        ),
        "gặp kiểu lạ mà không có widget thì không dựng được ô",
    )

# ──────────────────────────────────────────────────────────────────────
section("Mặc định từ CONNECTOR_DEFAULTS được đưa vào schema")

# Nếu mặc định không vào schema thì ô trống và người dùng phải tự nhớ, hoặc
# form hiện sai khác với giá trị thật đang chạy.
with_default = [
    (c, k)
    for c in CONNECTOR_REGISTRY
    for k, v in CONNECTOR_DEFAULTS.get(c, {}).items()
    if v not in (None, "")
]
for c, k in with_default:
    prop = _build_connector_config_schema(c)["properties"].get(k, {})
    check(
        f"{c}.{k} có mặc định trong schema",
        "default" in prop,
        "thiếu default thì ô trống dù giá trị thật đang có",
    )

# ──────────────────────────────────────────────────────────────────────
section("Ô bí mật không bao giờ điền giá trị vào DOM")

check(
    "bí mật nhận ra từ `format` của schema, không đoán theo tên",
    re.search(
        r"function _ccIsSecretProp\(prop\) \{\s*return prop && \(prop\.format === 'secret' \|\| prop\.format === 'password'\);",
        JS,
    )
    is not None,
    "đoán theo tên sẽ sót, và dễ che nhầm chính sách như forbidden_keywords",
)
check(
    "field bí mật sinh ra là input type=password",
    re.search(r"const type = secret \? 'password'", JS) is not None,
)
check(
    "ô bí mật để trống kèm placeholder, không điền giá trị",
    "•••••••• (đã lưu — để trống để giữ nguyên)" in JS,
    "thiếu thì người dùng không biết đã có khoá hay chưa",
)
check(
    "bộ nạp KHÔNG điền giá trị bí mật vào ô",
    re.search(
        r"if \(el\.type === 'password' \|\| CC_SECRET_FIELDS\.includes\(key\)\) \{\s*"
        r"// Không đưa secret vào DOM\.\s*el\.value = '';",
        JS,
    )
    is not None,
    "điền bí mật vào DOM thì ai mở DevTools cũng thấy",
)
check(
    "bộ lưu không gửi field bí mật khi người dùng không gõ",
    "if (el.value && el.value !== '••••••••') formData[key] = el.value;" in JS,
    "gửi ô trống sẽ xoá bí mật đang lưu",
)

# ──────────────────────────────────────────────────────────────────────
section("Thứ tự nạp: dựng form TRƯỚC rồi mới điền giá trị")

check(
    "loadConnectorConfigAll dựng form trước",
    re.search(
        r"async function loadConnectorConfigAll\(\) \{[\s\S]{0,200}?await renderConnectorForms\(\)", JS
    )
    is not None,
    "nạp trước rồi dựng form sau thì mọi ô đều trống vì id chưa tồn tại",
)
check(
    "form không có dữ liệu thì báo rõ, không để trống im lặng",
    "Chưa kiểm tra được cấu hình" in JS,
    "không có ô để bấm mà không nói gì thì giống lỗi",
)
check(
    "lỗi tải schema hiện ra thay vì nuốt",
    "Không tải được danh mục connector" in JS,
)

# ──────────────────────────────────────────────────────────────────────
section("Trạng thái connector trả lời đúng câu hỏi")

# Phải là "còn thiếu khoá nào", không phải "khối cấu hình có rỗng không" —
# khối cấu hình luôn có sẵn region/base_url nên kiểm tra độ dài luôn cho
# "Đã cấu hình" trong khi mọi lời gọi đều hỏng.
check(
    "dùng danh sách khoá BẮT BUỘC thiếu từ server",
    "missing_fields" in JS and "Còn thiếu:" in JS,
    "thiếu thì báo 'Đã cấu hình' cho connector chưa có token",
)
check(
    "không xác minh được thì nói thẳng thay vì khẳng định",
    "Đã cấu hình (chưa xác minh được)" in JS,
    "không gọi được health thì không được kết luận là cấu hình xong",
)

# ──────────────────────────────────────────────────────────────────────
print("\n" + "─" * 60)
print(f"Tổng: {PASSED + FAILED} | Pass: {PASSED} | Fail: {FAILED}")
if FAILURES:
    print("\n❌ CÓ LỖI:")
    for f in FAILURES:
        print(f"  - {f}")
    sys.exit(1)
print("\n✅ TẤT CẢ PASS")
