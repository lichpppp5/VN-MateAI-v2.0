# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 Dương Thanh Lịch — VN-MateAI. See LICENSE and NOTICE.
"""
tests/test_phase75_roi_real_data.py
====================================
Kiểm thử Phase 75 — ROI Dashboard đọc dữ liệu thật, không bịa.

Ba lỗi thật được vá ở phase này, cả ba chỉ lộ ra khi CSDL trống hoặc mới tinh:

  1. `SUM(CASE WHEN ...)` trên tập rỗng trả NULL chứ KHÔNG trả 0. `dict.get(k, 0)`
     không cứu được vì khoá vẫn tồn tại với giá trị None. Báo cáo in ra
     "Thành công: None" ngay trước mặt người đọc.

  2. `ERPDatabase` tạo bảng `tasks` bằng nửa schema: `id INTEGER PRIMARY KEY`
     trong khi `create_erp_task()` chèn id dạng chữ "erp_<hex>" -> SQLite ném
     "datatype mismatch"; và thiếu `created_at` -> `generate_daily_report()` ném
     "no such column". Nghĩa là trên CSDL mới, ROI Dashboard không chạy được —
     nó chỉ chạy ở máy nào tình cờ có `db_manager` tạo bảng trước.

  3. Hệ số quy đổi "1 phiếu AI = 15 phút" bị chép ở hai nơi. Sửa một chỗ là hai
     màn hình hiển thị lệch nhau, và cả hai đều không nói cho người đọc biết đó
     là giả định chứ không phải số đo.

Test cũng khoá lại điều quan trọng nhất: 0 là SỐ ĐO HỢP LỆ. CSDL trống phải trả
0 (không phải None) vì "0 nhân viên" là sự thật đếm được — khác hẳn "không gọi
được API".
"""

from __future__ import annotations

import logging
import sqlite3


def _open_db(path):
    """Mở qua tầng persistence (SQLite hoặc PostgreSQL theo cấu hình)."""
    from mateai.infrastructure.database.erp_database import open_sqlite
    return open_sqlite(path)
import sys
import tempfile
from contextlib import closing
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
logging.disable(logging.ERROR)

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

import mateai.infrastructure.database.erp_database as cdb  # noqa: E402
from mateai.infrastructure.database.erp_database import HOURS_SAVED_PER_AI_TASK, ERPDatabase  # noqa: E402
import skills.itsm_skills as itsm  # noqa: E402

# `generate_daily_report` lấy DB qua `mateai.infrastructure.database.erp_database.erp_db` (biến module), nên
# phải trỏ tạm sang CSDL trong tempdir rồi trả lại — tuyệt đối không để test
# ghi vào vnmateai.db thật.
_REAL_ERP_DB = cdb.erp_db


def use_temp_db(tmp: str) -> ERPDatabase:
    db = ERPDatabase(Path(tmp) / "t.db")
    cdb.erp_db = db
    return db


ITSM_SRC = (PROJECT_ROOT / "skills" / "itsm_skills.py").read_text(encoding="utf-8")
DB_SRC = (PROJECT_ROOT / "src" / "mateai" / "infrastructure" / "database" / "erp_database.py").read_text(encoding="utf-8")


def strip_comments(src: str) -> str:
    return "\n".join(l for l in src.splitlines() if not l.strip().startswith("#"))


# ──────────────────────────────────────────────────────────────────────
section("Hệ số quy đổi chỉ được khai báo DUY NHẤT một lần")

check(
    "có hằng HOURS_SAVED_PER_AI_TASK trong core/database.py",
    isinstance(HOURS_SAVED_PER_AI_TASK, (int, float)),
    f"nhận {HOURS_SAVED_PER_AI_TASK!r}",
)
check(
    "hệ số nằm trong khoảng hợp lý 0 < x <= 1 giờ",
    0 < HOURS_SAVED_PER_AI_TASK <= 1,
    f"nhận {HOURS_SAVED_PER_AI_TASK}",
)
check(
    "core/database.py định nghĩa hằng đúng 1 lần",
    strip_comments(DB_SRC).count("HOURS_SAVED_PER_AI_TASK = ") == 1,
    f"thấy {strip_comments(DB_SRC).count('HOURS_SAVED_PER_AI_TASK = ')} lần",
)
check(
    "core/database.py không còn nhân thẳng với 0.25",
    "ai_tasks * 0.25" not in strip_comments(DB_SRC),
)
check(
    "skills/itsm_skills.py không còn nhân thẳng với 0.25",
    "ai_tasks * 0.25" not in strip_comments(ITSM_SRC),
)
check(
    "skills/itsm_skills.py dùng chung hằng thay vì số chép tay",
    "HOURS_SAVED_PER_AI_TASK" in strip_comments(ITSM_SRC),
)
check(
    "hằng số có ghi rõ đây là GIẢ ĐỊNH kinh doanh",
    "GIẢ ĐỊNH" in DB_SRC and "KHÔNG PHẢI SỐ ĐO" in DB_SRC,
    "người đọc sau phải biết con số này từ đâu ra",
)

# ──────────────────────────────────────────────────────────────────────
section("SUM trên tập rỗng phải là 0, không phải NULL")

check(
    "truy vấn phiếu dùng COALESCE",
    "COALESCE(SUM(CASE WHEN status = 'completed'" in ITSM_SRC,
)
_start = ITSM_SRC.index("def generate_daily_report(")
# Đây là hàm cuối file, nên thân hàm chạy tới hết nguồn.
BODY = ITSM_SRC[_start:].replace("\n", " ")
for col in ("completed", "pending", "in_progress", "ai_created"):
    check(f"cột '{col}' của phiếu có COALESCE", f"), 0) AS {col}" in BODY)
for col in ("success", "failed", "blocked"):
    check(f"cột '{col}' của audit có COALESCE", f"), 0) AS {col}" in BODY)
check(
    "không còn SUM(...) trần không COALESCE",
    "SUM(CASE WHEN status = 'completed' THEN 1 ELSE 0 END) AS completed" not in BODY,
)

# ──────────────────────────────────────────────────────────────────────
section("CSDL mới: báo cáo chạy được và trả 0 (không phải None)")

with tempfile.TemporaryDirectory() as tmp:
    db = use_temp_db(tmp)
    try:
        r = itsm.generate_daily_report(include_audit_details=True)
        check(
            "generate_daily_report trên CSDL trống phải thành công",
            r.get("status") == "success",
            f"nhận status={r.get('status')!r} error={r.get('error')!r}",
        )
        report = r.get("report_text") or ""
        check(
            "báo cáo KHÔNG chứa chữ 'None'",
            "None" not in report,
            "SUM rỗng trả NULL rồi bị in thẳng ra văn bản",
        )
        check(
            "báo cáo có nội dung thật (không rỗng)", len(report.strip()) > 0
        )
        check(
            "báo cáo KHÔNG bịa tỷ lệ hoàn thành '(0.0%)' khi chưa có phiếu",
            "(0.0%)" not in report,
            "0/0 không phải 0% — đó là không đo được",
        )
        check(
            "báo cáo nói rõ chưa có phiếu nào để tính tỷ lệ",
            "chưa có phiếu nào để tính tỷ lệ" in report,
        )

        d = r.get("data", {})
        tickets = d.get("tickets", {})
        for col in ("total", "completed", "pending", "in_progress", "ai_created"):
            v = tickets.get(col)
            check(
                f"tickets.{col} là số 0 thật, không phải None",
                v == 0 and isinstance(v, int),
                f"nhận {v!r}",
            )

        org = d.get("org", {})
        check("org.total_employees = 0 (đếm được, không phải thiếu dữ liệu)",
              org.get("total_employees") == 0)
        check("org.total_departments = 0", org.get("total_departments") == 0)

        kpi = d.get("kpi", {})
        check("kpi.ai_tasks_today = 0", kpi.get("ai_tasks_today") == 0)
        check("kpi.hours_saved_today = 0.0", kpi.get("hours_saved_today") == 0.0)
        check(
            "kpi.hours_saved_per_task công bố hệ số cho UI dán nhãn 'ước tính'",
            kpi.get("hours_saved_per_task") == HOURS_SAVED_PER_AI_TASK,
            f"nhận {kpi.get('hours_saved_per_task')!r}",
        )
        check(
            "kpi.tickets_total = 0 để UI phân biệt '0 phiếu' với 'chưa có dữ liệu'",
            kpi.get("tickets_total") == 0,
        )

        # ── Có 1 phiếu AI đã hoàn thành ──
        t = db.create_erp_task("Khởi động lại dịch vụ in", status="completed", created_by_ai=True)
        check(
            "create_erp_task chạy được trên CSDL mới (id dạng chữ)",
            bool(t.get("id")),
            f"nhận {t.get('id')!r} — 'datatype mismatch' nghĩa là tasks.id còn là INTEGER",
        )
        check("id phiếu là chuỗi, không phải số", isinstance(t.get("id"), str))

        r2 = itsm.generate_daily_report()
        check("báo cáo sau khi có phiếu vẫn thành công",
              r2.get("status") == "success", str(r2.get("error")))
        check("báo cáo vẫn không có chữ 'None'", "None" not in (r2.get("report_text") or ""))

        d2 = r2.get("data", {})
        check("tickets.total = 1", d2.get("tickets", {}).get("total") == 1)
        check("tickets.completed = 1", d2.get("tickets", {}).get("completed") == 1)
        check("tickets.ai_created = 1", d2.get("tickets", {}).get("ai_created") == 1)
        check(
            "tỷ lệ hoàn thành = 100.0 khi 1/1 phiếu xong",
            d2.get("kpi", {}).get("ticket_completion_rate") == 100.0,
            f"nhận {d2.get('kpi', {}).get('ticket_completion_rate')!r}",
        )
        check(
            "giờ tiết kiệm = 1 × hệ số",
            d2.get("kpi", {}).get("hours_saved_today") == round(1 * HOURS_SAVED_PER_AI_TASK, 2),
        )
        check("kpi.tickets_total = 1", d2.get("kpi", {}).get("tickets_total") == 1)
        check(
            "báo cáo in tỷ lệ thật khi có phiếu",
            "(100.0%)" in (r2.get("report_text") or ""),
            "có 1/1 phiếu xong thì tỷ lệ mới có nghĩa",
        )
        check(
            "báo cáo ghi rõ giờ tiết kiệm là quy đổi giả định",
            "GIẢ ĐỊNH" in (r2.get("report_text") or ""),
            "nếu không, người đọc tưởng đó là số đo",
        )
    finally:
        cdb.erp_db = _REAL_ERP_DB

# ──────────────────────────────────────────────────────────────────────
section("Schema tasks của ERPDatabase phải tự đủ, không phụ thuộc db_manager")

with tempfile.TemporaryDirectory() as tmp:
    path = Path(tmp) / "fresh.db"
    ERPDatabase(path)
    with closing(_open_db(path)) as conn:  # with thuần không đóng kết nối
        cols = {row[1]: (row[2] or "").upper() for row in conn.execute("PRAGMA table_info(tasks)")}

    check("tasks.id khai báo TEXT (code chèn id 'erp_<hex>')",
          cols.get("id") == "TEXT", f"nhận {cols.get('id')!r}")
    for col in ("timestamp", "client_id", "task_message", "sender", "created_at", "updated_at"):
        check(f"tasks có cột '{col}' ngay từ đầu", col in cols)
    for col in ("dept_id", "assignee_id", "title", "status", "due_date", "created_by_ai", "resolution_notes"):
        check(f"tasks vẫn giữ cột ERP '{col}'", col in cols)

# ──────────────────────────────────────────────────────────────────────
section("CSDL cũ thiếu cột phải được migrate, không ném lỗi")

with tempfile.TemporaryDirectory() as tmp:
    path = Path(tmp) / "old.db"
    with closing(_open_db(path)) as conn:  # with thuần không đóng kết nối
        conn.execute(
            "CREATE TABLE tasks (id INTEGER PRIMARY KEY AUTOINCREMENT, "
            "title TEXT NOT NULL, status TEXT DEFAULT 'pending');"
        )
        conn.commit()
    ERPDatabase(path)  # phải migrate chứ không được vỡ
    with closing(_open_db(path)) as conn:  # with thuần không đóng kết nối
        cols = {row[1] for row in conn.execute("PRAGMA table_info(tasks)")}
    for col in ("created_at", "updated_at", "timestamp", "client_id", "task_message", "sender",
                "dept_id", "assignee_id", "due_date", "created_by_ai", "resolution_notes"):
        check(f"migrate bổ sung cột '{col}'", col in cols)

# ──────────────────────────────────────────────────────────────────────
section("CSDL thật: đường dữ liệu thật vẫn chạy (chỉ đọc)")

r = itsm.generate_daily_report(include_audit_details=True)
check(
    "generate_daily_report trên vnmateai.db thành công",
    r.get("status") == "success",
    f"error={r.get('error')!r}",
)
check("báo cáo thật không lẫn chữ 'None'", "None" not in (r.get("report_text") or ""))
check("trả kèm report_date", bool(r.get("report_date")))

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
