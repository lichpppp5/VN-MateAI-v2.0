# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 Dương Thanh Lịch — VN-MateAI. See LICENSE and NOTICE.
"""
tests/test_phase62_data_source.py
=================================
Kiểm thử Phase 62 — data source tùy chỉnh (generic connector).

Trọng tâm là các thứ dễ sai mà không ai thấy cho đến khi hỏng:
  - secret KHÔNG rò ra danh sách/UI
  - không lộ endpoint metadata cloud
  - payload lồng nhau vẫn ra đúng bảng
  - bảng quá lớn bị cắt, có nói rõ bị cắt
  - xoá/nhập diện id độc hại
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

PASSED = 0
FAILED = 0
FAILURES: list = []


def check(name: str, cond: bool, extra: str = "") -> None:
    global PASSED, FAILED
    if cond:
        PASSED += 1
    else:
        FAILED += 1
        FAILURES.append(f"  ✗ {name}" + (f" — {extra}" if extra else ""))


def section(title: str) -> None:
    print(f"\n▸ {title}")


# ── Chuẩn bị: trỏ sổ đăng ký vào file tạm ───────────────────────────────
from mateai.infrastructure.connectors import custom_registry  # noqa: E402

_TMP = tempfile.mkdtemp(prefix="vnmate-ds-")
custom_registry.STORE_PATH = Path(_TMP) / "data_sources.json"


BASE = {
    "id": "misa-amh",
    "title": "MISA AMH",
    "description": "Kế toán MISA lấy sổ cái",
    "category": "reporting",
    "base_url": "https://erp.congty.vn/api/",
    "auth_type": "bearer",
    "auth_value": "tok-secret-123",
    "default_path": "reports/salary",
    "paths": {"doanh thu": "reports/revenue", "tồn kho": "/reports/stock"},
}


section("Khai báo data source")
rec = custom_registry.upsert_source("misa-amh", BASE)
check("tạo mới thành công", rec.get("id") == "misa-amh", str(rec))
check("bỏ dấu / cuối base_url", rec.get("base_url") == "https://erp.congty.vn/api", rec.get("base_url"))
check("path không có / được tự thêm", rec.get("default_path") == "/reports/salary", rec.get("default_path"))
check("paths lưu đủ tên", set(rec.get("available_paths") or []) == {"doanh thu", "tồn kho"},
      str(rec.get("available_paths")))

section("Secret KHÔNG rò ra")
listed = custom_registry.list_sources(include_secrets=False)
check("danh sách không có auth_value", all("auth_value" not in s for s in listed), str(listed[0].keys()))
check("danh sách vẫn báo has_auth", listed[0].get("has_auth") is True)
check("danh sách không lộ paths đầy đủ", "paths" not in listed[0], str(listed[0].keys()))
serialised = json.dumps(listed)
check("không lộ chuỗi secret khi serialize", "tok-secret-123" not in serialised)

got = custom_registry.get_source("misa-amh", include_secrets=True)
check("đường nội bộ vẫn lấy được secret", got.get("auth_value") == "tok-secret-123")

section("File lưu trữ có chmod 600")
check("file được tạo", custom_registry.STORE_PATH.exists())
if custom_registry.STORE_PATH.exists() and os.name == "nt":
    # Windows không có quyền POSIX: os.chmod(0o600) chỉ bật/tắt cờ read-only,
    # file vẫn đọc được theo ACL thư mục. Đây là rủi ro bảo mật thật, chưa xử
    # lý (cần ACL qua icacls) — xem docs/architecture/legacy-candidates.md.
    print("  ⚠ BỎ QUA trên Windows: chmod 600 không giới hạn được quyền đọc (cần ACL)")
elif custom_registry.STORE_PATH.exists():
    mode = custom_registry.STORE_PATH.stat().st_mode & 0o777
    check("quyền file = 600", mode == 0o600, oct(mode))

section("Sửa không mất secret khi để trống")
custom_registry.upsert_source("misa-amh", {**BASE, "title": "MISA AMH (mới)", "auth_value": ""})
after = custom_registry.get_source("misa-amh", include_secrets=True)
check("title đã đổi", after.get("title") == "MISA AMH (mới)", after.get("title"))
check("secret cũ vẫn còn", after.get("auth_value") == "tok-secret-123", str(after.get("auth_value")))

section("Chặn URL nguy hiểm")
for bad, why in [
    ("ftp://x.vn/api", "sai scheme"),
    ("", "rỗng"),
    ("http://169.254.169.254/latest/meta-data", "metadata AWS"),
    ("http://metadata.google.internal/x", "metadata GCP"),
    ("http://100.100.100.200/latest", "metadata Alibaba"),
]:
    try:
        custom_registry.upsert_source("bad-test", {**BASE, "id": "bad-test", "base_url": bad})
        check(f"chặn {why}", False, f"chấp nhận '{bad}'")
    except ValueError:
        check(f"chặn {why}", True)

section("Chặn id độc hại")
for bad_id in ["../etc", "a", "x" * 60, "co dau", "a/b", "", "-bad", "a b"]:
    try:
        custom_registry.upsert_source(bad_id, {**BASE, "id": bad_id})
        check(f"chặn id '{bad_id}'", False, "đã nhận")
    except ValueError:
        check(f"chặn id '{bad_id}'", True)

# Chữ hoa KHÔNG bị chặn — id được hạ chữ thường để ghép URL path nhất quán.
custom_registry.upsert_source("MISA-ERP", {**BASE, "id": "MISA-ERP"})
check("id chữ hoa được hạ về chữ thường",
      any(s["id"] == "misa-erp" for s in custom_registry.list_sources()),
      str([s["id"] for s in custom_registry.list_sources()]))
custom_registry.delete_source("misa-erp")

section("Chặn method ghi")
try:
    custom_registry.upsert_source("misa-amh", {**BASE, "method": "DELETE"})
    check("chặn DELETE", False, "đã nhận")
except ValueError:
    check("chặn DELETE", True)

section("Chặn auth_type lạ")
try:
    custom_registry.upsert_source("misa-amh", {**BASE, "auth_type": "oauth2-magic"})
    check("chặn auth_type lạ", False, "đã nhận")
except ValueError:
    check("chặn auth_type lạ", True)

section("Timeout bị siết vào khoảng hợp lý")
custom_registry.upsert_source("misa-amh", {**BASE, "timeout_seconds": 99999})
t = custom_registry.get_source("misa-amh", True)["timeout_seconds"]
check("timeout trần = 60s", t == 60.0, str(t))
custom_registry.upsert_source("misa-amh", {**BASE, "timeout_seconds": 0})
t2 = custom_registry.get_source("misa-amh", True)["timeout_seconds"]
check("timeout sàn = 1s", t2 == 1.0, str(t2))

section("Dựng URL / header")
from mateai.infrastructure.connectors.generic_connector import GenericConnector  # noqa: E402

src = custom_registry.get_source("misa-amh", True)
c = GenericConnector(src)
check("URL ghép base + default_path",
      c.build_url() == "https://erp.congty.vn/api/reports/salary", c.build_url())
check("URL ghép path tường minh",
      c.build_url("/reports/stock") == "https://erp.congty.vn/api/reports/stock", c.build_url("/reports/stock"))
check("bearer -> header Authorization",
      c._headers.get("Authorization") == "Bearer tok-secret-123", str(c._headers.get("Authorization")))

for kind, payload, expect in [
    ("header", {"auth_type": "header", "auth_value": "K1", "auth_header": "X-Tenant-Key"}, "X-Tenant-Key"),
    ("query", {"auth_type": "query", "auth_value": "Q1", "auth_query": "token"}, "token=Q1"),
]:
    s2 = {**BASE, **payload}
    cc = GenericConnector(s2)
    if kind == "header":
        check("auth header tùy chỉnh", cc._headers.get(expect) == "K1", str(cc._headers))
    else:
        check("auth query nằm trên URL", expect in cc.build_url(), cc.build_url())

c_basic = GenericConnector({**BASE, "auth_type": "basic", "auth_value": "user:pass"})
check("basic -> header Authorization Base64",
      c_basic._headers.get("Authorization", "").startswith("Basic "), str(c_basic._headers.get("Authorization")))

c_none = GenericConnector({**BASE, "auth_type": "none", "auth_value": ""})
check("auth none -> không header Authorization", "Authorization" not in c_none._headers, str(c_none._headers))

section("Tìm danh sách bản ghi trong payload lồng")
from mateai.infrastructure.connectors.generic_connector import _find_rows, _find_total, _extract_error  # noqa: E402

payloads = [
    ([{"a": 1}], "danh sách trực tiếp"),
    ({"data": [{"a": 1}]}, "bọc trong data"),
    ({"result": {"items": [{"a": 1}]}}, "bọc 2 tầng"),
    ({"rows": [{"a": 1}], "total": 99}, "rows + total"),
    ({"records": [{"a": 1}]}, "records"),
]
for payload, why in payloads:
    rows = _find_rows(payload)
    check(f"tìm thấy rows: {why}", rows is not None and len(rows) == 1, str(rows)[:80])

check("payload rỗng -> None (không phải bảng rỗng)", _find_rows({"data": {}}) is None)
check("payload không phải dict/list -> None", _find_rows("text thuần") is None)
check("tìm total", _find_total({"data": [{"a": 1}], "total": 57}, 1) == 57, str(_find_total({"data": [], "total": 57}, 0)))
check("tìm total trong tầng trong", _find_total({"result": {"total_count": "12"}}, 0) == 12)
check("không có total -> None", _find_total({"rows": []}, 0) is None)
check("bool không bị đọc nhầm là total", _find_total({"success": True, "total": 5}, 0) == 5)
check("rút lỗi app", _extract_error({"error": "Token hết hạn"}) == "Token hết hạn", _extract_error({"error": "x"}))
check("rút lỗi lồng", "hạn" in _extract_error({"data": {"error_message": "Token hết hạn"}}))

section("Cắt bảng lớn")
from mateai.infrastructure.connectors.generic_connector import _columns_of, _coerce_limit  # noqa: E402

rows = [{"a": 1} for _ in range(1000)]
check("row_limit mặc định 50", _coerce_limit(None, 50) == 50)
check("row_limit trần 500", _coerce_limit(99999, 50) == 500)
check("row_limit sàn 1", _coerce_limit(0, 50) == 1)
check("row_limit rác -> mặc định", _coerce_limit("abc", 50) == 50)
check("cột giới hạn 12", len(_columns_of([{"c%d" % i: i for i in range(30)}])) == 12)
check("cột hợp nhất nhiều dòng", _columns_of([{"a": 1}, {"b": 2}]) == ["a", "b"])

section("Thiếu cấu hình -> từ chối rõ ràng")
import asyncio  # noqa: E402

no_url = GenericConnector({**BASE, "base_url": ""})
no_auth = GenericConnector({**BASE, "auth_type": "bearer", "auth_value": ""})
check("thiếu base_url -> authenticate False", asyncio.run(no_url.authenticate()) is False)
check("thiếu khoá -> authenticate False", asyncio.run(no_auth.authenticate()) is False)
hc = asyncio.run(no_auth.health_check())
check("health nói rõ thiếu gì", "khoá" in (hc.error or ""), str(hc.error))

section("Tìm nguồn không tồn tại")
from mateai.infrastructure.connectors import probe_data_source, fetch_data_source  # noqa: E402

check("probe nguồn thiếu -> False", asyncio.run(probe_data_source("khong-ton-tai")).success is False)
check("fetch nguồn thiếu -> False", asyncio.run(fetch_data_source("khong-ton-tai")).success is False)

disabled = custom_registry.upsert_source("off", {**BASE, "id": "off", "enabled": False})
check("nguồn tắt vẫn liệt kê", any(s["id"] == "off" for s in custom_registry.list_sources()))
check("probe nguồn tắt -> báo đang tắt",
      "tắt" in (asyncio.run(probe_data_source("off")).error or ""))

section("Xoá")
check("xoá nguồn tồn tại", custom_registry.delete_source("off") is True)
check("xoá lần hai -> False", custom_registry.delete_source("off") is False)
check("xoá xong không còn trong danh sách",
      not any(s["id"] == "off" for s in custom_registry.list_sources()))

section("File hỏng không làm hỏng danh sách")
custom_registry.STORE_PATH.write_text("{ khong phai json", encoding="utf-8")
check("file hỏng -> danh sách rỗng, không raise", custom_registry.list_sources() == [])
check("file hỏng -> upsert vẫn ghi được", custom_registry.upsert_source("moi", {**BASE, "id": "moi"})["id"] == "moi")

section("Ghi đồng thời không mất bản ghi")
import threading  # noqa: E402

custom_registry.STORE_PATH.unlink(missing_ok=True)
errors: list = []


def _writer(i: int) -> None:
    try:
        custom_registry.upsert_source(f"src-{i:02d}", {**BASE, "id": f"src-{i:02d}"})
    except Exception as exc:  # pragma: no cover
        errors.append(str(exc))


threads = [threading.Thread(target=_writer, args=(i,)) for i in range(12)]
for t in threads:
    t.start()
for t in threads:
    t.join()

got_ids = {s["id"] for s in custom_registry.list_sources()}
check("12 ghi song song -> đủ 12 bản ghi", len(got_ids) == 12, f"thiếu: {12 - len(got_ids)}")
check("không có lỗi khi ghi song song", not errors, str(errors[:2]))

section("File không phải dict -> bỏ qua, không làm hỏng cả danh sách")
custom_registry.STORE_PATH.write_text(json.dumps({"sources": "khong-phai-dict"}), encoding="utf-8")
check("sources sai kiểu -> list rỗng", custom_registry.list_sources() == [])


# ── Tổng kết ─────────────────────────────────────────────────────────────
print("\n" + "─" * 60)
if FAILURES:
    print("Các assertion FAIL:")
    for f in FAILURES:
        print(f)
print(f"\nTổng: {PASSED + FAILED} | Pass: {PASSED} | Fail: {FAILED}")
sys.exit(1 if FAILED else 0)
