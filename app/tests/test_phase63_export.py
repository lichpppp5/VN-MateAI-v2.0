# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 Dương Thanh Lịch — VN-MateAI. See LICENSE and NOTICE.
"""
tests/test_phase63_export.py
============================
Kiểm thử phần xuất báo cáo Excel/CSV (Phase 63).

Trọng tâm là những lỗi chỉ lộ ra khi mở file bằng Excel thật:
  - CSV thiếu BOM thì tiếng Việt ra ký tự lỗi
  - tên file có dấu làm hỏng header HTTP
  - dict/list không ghi thẳng ra ô Excel được
  - giá trị rác phải không làm sập cả request
"""

from __future__ import annotations

import csv
import io
import sys
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


from mateai.infrastructure.files.file_export import (  # noqa: E402
    BOM_UTF8,
    _cell_value,
    _content_disposition,
    _rows_to_csv,
    _rows_to_xlsx,
    _safe_filename,
)
from mateai.interfaces.http.routers.enterprise import _safe_int  # noqa: E402

ROWS = [
    {"ma_hang": "SP001", "ten_hang": "Ghế xoay nội thất", "so_luong": 45, "don_gia": 1250000},
    {"ma_hang": "SP002", "ten_hang": "Bàn gỗ", "so_luong": 12, "don_gia": 890000},
]
COLS = ["ma_hang", "ten_hang", "so_luong", "don_gia"]


# ══ 1. CSV ════════════════════════════════════════════════════════════════
section("CSV")
raw = _rows_to_csv(ROWS, COLS)
check("CSV có BOM UTF-8", raw.startswith(BOM_UTF8), f"thiếu BOM: {raw[:6]!r}")
check("CSV rỗng vẫn có header cột", _rows_to_csv([], COLS).startswith(BOM_UTF8))
check("sau BOM là tiêu đề cột",
      raw[len(BOM_UTF8):].decode("utf-8").startswith("ma_hang,ten_hang"),
      raw[len(BOM_UTF8):][:30].decode("utf-8", "replace"))

text = raw.decode("utf-8")
parsed = list(csv.reader(io.StringIO(text)))
check("đọc lại được số dòng", len(parsed) == 3, f"{len(parsed)} dòng")
check("tiếng Việt giữ nguyên", "Ghế xoay nội thất" in text, text[:120])
check("số giữ kiểu số (không phải chuỗi)", parsed[1][2] == "45", parsed[1][2])
check("xuống dòng CRLF theo thông lệ Excel", "\r\n" in text)
check("giá trị có dấu phẩy được bọc",
  '"' in _rows_to_csv([{"a": "1,234"}], ["a"]).decode("utf-8"),
  _rows_to_csv([{"a": "1,234"}], ["a"]).decode("utf-8")[:60])

# Cột thiếu trong một dòng phải để trống chứ không làm lệch cột — dùng csv
# module đọc lại thay vì đếm dấu phẩy (giá trị có thể chứa phẩy).
ragged = _rows_to_csv([{"a": 1, "c": 3}, {"a": 2, "b": 9, "c": 4}], ["a", "b", "c"])
rows_ragged = list(csv.reader(io.StringIO(ragged.decode("utf-8"))))
check("dòng thiếu cột -> vẫn đủ số cột", all(len(r) == 3 for r in rows_ragged), str(rows_ragged))


# ══ 2. XLSX ═══════════════════════════════════════════════════════════════
section("XLSX")
xlsx = _rows_to_xlsx(ROWS, COLS, "Tồn kho")
check("XLSX là ZIP (chữ PK)", xlsx[:2] == b"PK", f"{xlsx[:2]!r}")
check("XLSX không rỗng", len(xlsx) > 2000, f"{len(xlsx)} byte")

try:
    from openpyxl import load_workbook

    wb = load_workbook(io.BytesIO(xlsx))
    ws = wb.active
    check("mở lại được bằng openpyxl", True)
    check("đủ số dòng + header", ws.max_row == 3, f"max_row={ws.max_row}")
    check("tiêu đề cột khớp", [c.value for c in ws[1]] == COLS, str([c.value for c in ws[1]]))
    check("dữ liệu khớp", ws.cell(row=2, column=1).value == "SP001")
    check("số giữ kiểu số (tính được trong Excel)",
          isinstance(ws.cell(row=2, column=4).value, (int, float)),
          repr(ws.cell(row=2, column=4).value))
    check("tiếng Việt trong ô đúng", ws.cell(row=2, column=2).value == "Ghế xoay nội thất")
    check("dòng tiêu đề được đóng băng", ws.freeze_panes == "A2", str(ws.freeze_panes))
    check("tiêu đề in đậm", ws.cell(row=1, column=1).font.bold is True)
    check("có tự giãn cột", ws.column_dimensions["A"].width > 0)

    # Tên sheet Excel chặn trên 31 ký tự — tên sheet dài phải bị cắt, không
    # được làm save() ném lỗi.
    long_title = _rows_to_xlsx(ROWS, COLS, "Báo cáo tồn kho chi tiết theo kho và theo nhà cung cấp 2026")
    wb2 = load_workbook(io.BytesIO(long_title))
    check("tên sheet dài được cắt còn <= 31 ký tự", len(wb2.active.title) <= 31, wb2.active.title)

    # Rỗng hoàn toàn: vẫn tạo được file có tiêu đề cột.
    empty = load_workbook(io.BytesIO(_rows_to_xlsx([], COLS, "Rỗng")))
    check("bảng rỗng vẫn xuất được file", empty.active.max_row == 1)
except ImportError:
    check("openpyxl khả dụng", False, "thiếu openpyxl")


# ══ 3. Ô có kiểu dữ liệu lạ ═══════════════════════════════════════════════
section("Ô có kiểu dữ liệu lạ")
check("dict -> JSON một dòng", _cell_value({"a": 1}) == '{"a": 1}', str(_cell_value({"a": 1})))
check("list -> JSON", _cell_value([1, 2]) == "[1, 2]")
check("None giữ None (ô trống)", _cell_value(None) is None)
check("số giữ nguyên kiểu", _cell_value(45) == 45 and isinstance(_cell_value(45), int))
check("float giữ nguyên", _cell_value(1.5) == 1.5)
check("bool giữ nguyên", _cell_value(True) is True)
check("chuỗi dài không cắt", len(_cell_value("x" * 500)) == 500)
check("dict có tiếng Việt giữ dấu", "Ghế" in _cell_value({"ten": "Ghế xoay"}))

nested = _rows_to_csv([{"a": {"ten": "Ghế"}}], ["a"])
check("dict trong CSV không làm hỏng cột",
  len(list(csv.reader(io.StringIO(nested.decode("utf-8"))))[0]) == 1)


# ══ 4. Tên file ═══════════════════════════════════════════════════════════
section("Tên file")
check("bỏ dấu tiếng Việt", _safe_filename("Báo cáo tồn kho") == "Bao-cao-ton-kho", _safe_filename("Báo cáo tồn kho"))
check("chặn path traversal", "/" not in _safe_filename("../../etc/passwd"), _safe_filename("../../etc/passwd"))
check("tên rỗng -> có tên dự phòng", _safe_filename("") == "bao-cao", _safe_filename(""))
check("tên toàn ký tự lạ -> dự phòng", _safe_filename("///") == "bao-cao", _safe_filename("///"))
check("giới hạn độ dài", len(_safe_filename("A" * 200)) <= 60, str(len(_safe_filename("A" * 200))))
check("giữ chữ số và gạch", _safe_filename("BC1-2026") == "BC1-2026")

cd = _content_disposition("Báo cáo tồn kho", "20260928-1015", "xlsx")
check("header có filename ASCII", 'filename="Bao-cao-ton-kho-20260928-1015.xlsx"' in cd, cd)
check("header có filename* UTF-8", "filename*=UTF-8''" in cd, cd)
check("filename* mã hoá đúng dấu", "%C3%A1" in cd, cd)
check("kiểu attachment", cd.startswith("attachment;"), cd)
check("CSV dùng đuôi .csv", _content_disposition("x", "1", "csv").endswith('.csv"') or ".csv" in _content_disposition("x", "1", "csv"))
check("header không chứa dấu xuống dòng", "\n" not in cd and "\r" not in cd)


# ══ 5. Ép kiểu số ═════════════════════════════════════════════════════════
section("Ép số")
check("số hợp lệ", _safe_int("500", 100, 1, 1000) == 500)
check("None -> mặc định", _safe_int(None, 100, 1, 1000) == 100)
check("rác -> mặc định", _safe_int("abc", 100, 1, 1000) == 100)
check("danh sách -> mặc định", _safe_int([1], 100, 1, 1000) == 100)
check("sàn", _safe_int(-5, 100, 1, 1000) == 1)
check("trần", _safe_int(99999, 100, 1, 1000) == 1000)
check("trần 10000 cho xuất", _safe_int(999999, 1000, 1, 10000) == 10000)


# ── Tổng kết ─────────────────────────────────────────────────────────────
print("\n" + "─" * 60)
if FAILURES:
    print("Các assertion FAIL:")
    for f in FAILURES:
        print(f)
print(f"\nTổng: {PASSED + FAILED} | Pass: {PASSED} | Fail: {FAILED}")
sys.exit(1 if FAILED else 0)
