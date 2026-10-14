#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 Dương Thanh Lịch — VN-MateAI. See LICENSE and NOTICE.
"""
Phase 81: cảnh báo dòng tiền không được bắn khi CHƯA CÓ DỮ LIỆU.

Lỗi người dùng gặp:
  🚨 CẢNH BÁO ĐỎ TÀI CHÍNH (PREDICTIVE WARNING): Tốc độ chi tiêu
  (0 VND/ngày) đang vượt ngưỡng an toàn. Quỹ dự trữ (0 VND) sẽ cạn trong
  0.0 ngày tới nếu không tăng thu hoặc cắt giảm chi!

Nguyên nhân: `get_financial_summary()` dùng `COALESCE(SUM(amount), 0)` nên
bảng `finances` trống cũng cho ra 0 — không phân biệt được "không có dữ
liệu" với "số đo bằng 0". Bên trên đó, điều kiện `net_balance <= 0` coi 0 là
nguy cấp, và hàm còn BẮN TELEGRAM cho một sự kiện không tồn tại.

Ba điều luật ở đây, mỗi điều một nhóm assert:
  1. Không có dữ liệu -> KHÔNG kết luận gì, nói "chưa có dữ liệu".
  2. Có dữ liệu thật -> cảnh báo vẫn phải bắn đúng, kể cả khi số dư âm.
  3. Cảnh báo phải nói đúng LÝ DO, không dùng một câu chung cho mọi ca.
"""

import os
import sqlite3
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from mateai.infrastructure.database.erp_database import ERPDatabase, open_sqlite  # noqa: E402

PASS = 0
FAIL = 0


def check(label: str, condition: bool, detail: str = "") -> None:
    global PASS, FAIL
    if condition:
        PASS += 1
        print(f"  ok   {label}")
    else:
        FAIL += 1
        print(f"  FAIL {label}" + (f" — {detail}" if detail else ""))


def make_db(rows):
    """DB tạm có bảng `finances` rỗng rồi nạp `rows`."""
    path = Path(tempfile.mkdtemp()) / "t.db"
    con = open_sqlite(path)   # cùng điểm mở với ứng dụng: SQLite hoặc PostgreSQL
    con.execute(
        """CREATE TABLE finances (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            type TEXT, amount REAL, category TEXT,
            description TEXT, created_by TEXT, date TEXT
        )"""
    )
    for r in rows:
        con.execute(
            "INSERT INTO finances (type, amount, category, description,"
            " created_by, date) VALUES (?,?,?,?,?,?)",
            r,
        )
    con.commit()
    con.close()
    return ERPDatabase(path)


def evaluate(rows):
    """Chạy đúng hàm thật `evaluate_predictive_cashflow()` với DB tạm."""
    import mateai.application.analytics.analytics_engine as ae

    old = ae.erp_db
    ae.erp_db = make_db(rows)
    try:
        return ae.AnalyticsEngine.evaluate_predictive_cashflow(ae.AnalyticsEngine())
    finally:
        ae.erp_db = old


EXPENSE = [
    ("expense", 5_000_000.0, "Lương", "lương tháng", "KT", "2026-09-20"),
    ("expense", 3_000_000.0, "Văn phòng", "thuê văn phòng", "KT", "2026-09-22"),
]
INCOME = ("income", 900_000_000.0, "Doanh thu", "bán hàng", "KT", "2026-09-15")


# ══ 1. KHÔNG có dữ liệu ═══════════════════════════════════════════════════
print("\n▸ Bảng tài chính trống")
r = evaluate([])
check("có báo là không có dữ liệu", r.get("has_data") is False, str(r.get("has_data")))
check("mức cảnh báo là NO_DATA", r.get("alert_level") == "NO_DATA", str(r.get("alert_level")))
check("KHÔNG coi là nguy cấp", r.get("is_critical") is False, str(r.get("is_critical")))
check("KHÔNG coi là cảnh báo vàng", r.get("is_warning") is False, str(r.get("is_warning")))
check("runway là None chứ không phải 0.0", r.get("runway_days") is None, str(r.get("runway_days")))
check("số dư là None chứ không phải 0", r.get("net_balance") is None, str(r.get("net_balance")))
check("tốc độ chi là None chứ không phải 0", r.get("daily_burn_rate") is None, str(r.get("daily_burn_rate")))
msg = r.get("message", "")
check("nói rõ là chưa có giao dịch", "chưa có giao dịch" in msg.lower(), msg[:80])
check("KHÔNG dùng số 0 trong câu cảnh báo", "0 VND" not in msg, msg[:80])
check("KHÔNG dùng số 0 ngày trong câu cảnh báo", "0.0 ngày" not in msg and "0 ngày" not in msg, msg[:80])
# Câu của ta có chữ "an toàn" nhưng ở dạng phủ định ("chưa phải lúc kết luận
# quỹ an toàn"), nên không thể so chuỗi thô. Kiểm tra đúng ý: KHÔNG mở
# đầu bằng kết luận an toàn, và phải nói rõ là chưa kết luận được gì.
check("KHÔNG kết luận quỹ an toàn khi chưa biết",
      not msg.lower().startswith("dòng tiền an toàn")
      and "chưa phải lúc kết luận" in msg.lower(), msg[:80])


# ══ 2. CÓ dữ liệu — cảnh báo phải bắn ══════════════════════════════════════
print("\n▸ Có chi phí nhưng không có thu (số dư âm)")
r = evaluate(EXPENSE)
check("có báo là có dữ liệu", r.get("has_data") is True)
check("mức nguy cấp đỏ", r.get("alert_level") == "CRITICAL_RED", str(r.get("alert_level")))
check("đánh dấu nguy cấp", r.get("is_critical") is True)
check("số dư âm thật", (r.get("net_balance") or 0) < 0, str(r.get("net_balance")))
check("nêu lý do là số dư âm", "âm" in r.get("message", "").lower(), r.get("message", "")[:80])

print("\n▸ Có tiền nhưng runway ngắn")
r = evaluate([("income", 40_000_000.0, "DT", "b", "KT", "2026-09-15"),
              ("expense", 30_000_000.0, "CP", "c", "KT", "2026-09-20")])
check("mức nguy cấp đỏ", r.get("alert_level") == "CRITICAL_RED", str(r.get("alert_level")))
check("runway ngắn hơn 15 ngày", (r.get("runway_days") or 99) < 15, str(r.get("runway_days")))
check("nêu số ngày còn đủ", "ngày" in r.get("message", "").lower())

print("\n▸ Thu nhiều, chi ít — tình hình ổn")
r = evaluate(EXPENSE + [INCOME])
check("KHÔNG cảnh báo", r.get("alert_level") == "NORMAL", str(r.get("alert_level")))
check("không nguy cấp", r.get("is_critical") is False)
check("runway là số thật", isinstance(r.get("runway_days"), (int, float)))


# ══ 3. get_financial_summary phải phân biệt được "rỗng" ═══════════════════
print("\n▸ get_financial_summary báo rõ có dữ liệu hay không")
fin_empty = make_db([]).get_financial_summary(30)
check("bảng trống -> has_data False", fin_empty.get("has_data") is False)
check("bảng trống -> đếm 0 giao dịch", fin_empty.get("transaction_count") == 0, str(fin_empty.get("transaction_count")))
fin_full = make_db(EXPENSE).get_financial_summary(30)
check("có dòng -> has_data True", fin_full.get("has_data") is True)
check("đếm đúng số giao dịch", fin_full.get("transaction_count") == 2, str(fin_full.get("transaction_count")))


# ══ 4. Lỗi proxy: ký hiệu che không được làm sập request ══════════════════
print("\n▸ Endpoint lấy model proxy")
server_py = (Path(__file__).resolve().parent.parent / "src" / "mateai" / "interfaces" / "http" / "routers" / "config.py").read_text(encoding="utf-8")
body = server_py.split("async def proxy_models_endpoint", 1)[-1]
body = body.split("\n@router.", 1)[0]
# BỎ COMMENT trước khi quét: bình luận giải thích lỗi lại nhắc lại đúng chuỗi
# cần tìm, quét cả comment sẽ ra kết quả ngược.
code = "\n".join(l.split("#", 1)[0] for l in body.split("\n"))
check("chặn ký hiệu che trước khi dựng header", "_SECRET_MASK" in code,
      "ký hiệu che lọt xuống header -> lỗi ascii codec")
check("bắt lỗi ký tự không hợp lệ", "UnicodeEncodeError" in code)
check("không trả lỗi codec thô cho người dùng", "ascii' codec" not in code)

print(f"\nTổng: {PASS + FAIL} | Pass: {PASS} | Fail: {FAIL}")
sys.exit(1 if FAIL else 0)


def test_truncated_sql_is_not_executed():
    """Câu trả lời LLM bị cắt giữa chừng (thiếu ngoặc) không được gắn ';' rồi chạy."""
    from mateai.application.analytics.analytics_engine import _extract_sql
    assert _extract_sql("SELECT d.name, COUNT(e.id") == ""
    assert _extract_sql("```sql\nSELECT COUNT(*) FROM employees;\n```") == "SELECT COUNT(*) FROM employees;"
