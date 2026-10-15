# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 Dương Thanh Lịch — VN-MateAI. See LICENSE and NOTICE.
"""
mateai/infrastructure/database/governance_repo.py
=================================================
Truy vấn CSDL cho các chức năng quản trị / tuân thủ (bản quyền, báo cáo hiệu năng, dữ liệu cá nhân, audit) — tầng application
không chạy SQL trực tiếp (RULE-024). Mỗi hàm một câu hỏi, trả dữ liệu thuần (dict / list / số).
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple


def _main():
    from mateai.infrastructure.database.db_manager import db_manager
    return db_manager._get_connection()


def _erp():
    from mateai.infrastructure.database.erp_database import erp_db
    return erp_db.get_connection()


def _hr():
    from mateai.infrastructure.directory.domain_sync import domain_manager
    return domain_manager._get_connection()


def _like(value: str) -> str:
    return "%" + str(value).replace("%", "").replace("_", "") + "%"


# ── Đo lượng sử dụng (licensing) ──────────────────────────────────────────────

def usage_counts(month: str) -> Dict[str, int]:
    with _main() as conn:
        users = int(conn.execute("SELECT COUNT(*) FROM users;").fetchone()[0])
        try:
            ws = int(conn.execute("SELECT COUNT(*) FROM worker_devices WHERE revoked_at IS NULL;").fetchone()[0])
        except Exception:  # noqa: BLE001 — CSDL rất cũ chưa có bảng
            ws = 0
        turns = int(conn.execute("SELECT COUNT(*) FROM voice_traces WHERE created_at LIKE ?;", (f"{month}%",)).fetchone()[0])
        tokens = int(conn.execute("SELECT COALESCE(SUM(total_tokens), 0) FROM llm_usage WHERE day LIKE ?;",
                                  (f"{month}%",)).fetchone()[0])
    return {"users": users, "workstations": ws, "voice_turns_month": turns, "llm_tokens_month": tokens}


# ── Báo cáo hiệu năng ─────────────────────────────────────────────────────────

def performance_rows(since: str) -> Tuple[List[Tuple[str, str]], List[Tuple[str, str]], List[Tuple[str, int, float]]]:
    """(voice_traces: created_at, data_json), (op_tasks: created_at, status), (llm_usage: day, tokens, cost) từ `since`."""
    with _main() as conn:
        traces = [(str(r[0]), r[1]) for r in conn.execute(
            "SELECT created_at, data_json FROM voice_traces WHERE created_at >= ?;", (since,)).fetchall()]
        tasks = [(str(r[0]), str(r[1] or "")) for r in conn.execute(
            "SELECT created_at, status FROM op_tasks WHERE created_at >= ?;", (since,)).fetchall()]
        usage = [(str(r[0]), int(r[1] or 0), float(r[2] or 0)) for r in conn.execute(
            "SELECT day, total_tokens, cost FROM llm_usage WHERE day >= ?;", (since,)).fetchall()]
    return traces, tasks, usage


# ── Dữ liệu cá nhân ───────────────────────────────────────────────────────────

def hr_people(ident: str) -> List[Dict[str, Any]]:
    s = ident.strip()
    with _hr() as conn:
        rows = conn.execute(
            "SELECT id, sam_account_name, full_name, department, title, email, phone FROM employees "
            "WHERE LOWER(sam_account_name) = ? OR LOWER(email) = ? OR phone = ? OR LOWER(full_name) = ?;",
            (s.lower(), s.lower(), s, s.lower())).fetchall()
    return [dict(r) for r in rows]


def erp_people(ident: str) -> List[Dict[str, Any]]:
    s = ident.strip()
    with _erp() as conn:
        rows = conn.execute(
            "SELECT id, dept_id, name, position, email, phone, ad_sam FROM employees "
            "WHERE LOWER(ad_sam) = ? OR LOWER(email) = ? OR phone = ? OR LOWER(name) = ?;",
            (s.lower(), s.lower(), s, s.lower())).fetchall()
    return [dict(r) for r in rows]


def anonymise_hr(ids: List[int], placeholder: str) -> int:
    with _hr() as conn:
        for i in ids:
            conn.execute("UPDATE employees SET full_name = ?, email = NULL, phone = NULL, title = NULL, "
                         "sam_account_name = ? WHERE id = ?;", (placeholder, f"erased-{i}", i))
        conn.commit()
    return len(ids)


def anonymise_erp(ids: List[Any], placeholder: str) -> int:
    with _erp() as conn:
        for i in ids:
            conn.execute("UPDATE employees SET name = ?, email = NULL, phone = NULL, ad_sam = NULL WHERE id = ?;", (placeholder, i))
        conn.commit()
    return len(ids)


def count_traces_mentioning(terms: List[str]) -> int:
    with _main() as conn:
        return sum(int(conn.execute("SELECT COUNT(*) FROM voice_traces WHERE data_json LIKE ?;", (_like(t),)).fetchone()[0])
                   for t in terms)


def delete_traces_mentioning(terms: List[str]) -> int:
    n = 0
    with _main() as conn:
        for t in terms:
            n += conn.execute("DELETE FROM voice_traces WHERE data_json LIKE ?;", (_like(t),)).rowcount or 0
        conn.commit()
    return n


# ── Audit ─────────────────────────────────────────────────────────────────────

AUDIT_COLUMNS = ("id", "timestamp", "employee_id", "action_type", "status", "approved_by", "source_ip", "session_id",
                 "payload", "prev_hash", "row_hash")


def audit_rows(date_from: Optional[str], date_to: Optional[str], limit: int) -> List[Dict[str, Any]]:
    where, args = [], []
    if date_from:
        where.append("timestamp >= ?")
        args.append(f"{date_from} 00:00:00")
    if date_to:
        where.append("timestamp <= ?")
        args.append(f"{date_to} 23:59:59")
    sql = (f"SELECT {', '.join(AUDIT_COLUMNS)} FROM audit_logs" + (f" WHERE {' AND '.join(where)}" if where else "")
           + " ORDER BY id LIMIT ?;")
    with _erp() as conn:
        return [dict(zip(AUDIT_COLUMNS, tuple(r))) for r in conn.execute(sql, (*args, limit)).fetchall()]


def audit_head() -> Optional[Dict[str, Any]]:
    with _erp() as conn:
        row = conn.execute("SELECT id, row_hash FROM audit_logs WHERE row_hash IS NOT NULL ORDER BY id DESC LIMIT 1;").fetchone()
        n = conn.execute("SELECT COUNT(*) FROM audit_logs;").fetchone()[0]
    return {"id": int(row[0]), "row_hash": str(row[1]), "rows": int(n)} if row else None


def audit_hashes(ids: List[int]) -> Tuple[Dict[int, Optional[str]], int]:
    """row_hash của các dòng `ids` (thiếu dòng → None) và tổng số dòng hiện tại."""
    out: Dict[int, Optional[str]] = {}
    with _erp() as conn:
        total = int(conn.execute("SELECT COUNT(*) FROM audit_logs;").fetchone()[0])
        for i in ids:
            r = conn.execute("SELECT row_hash FROM audit_logs WHERE id = ?;", (int(i),)).fetchone()
            out[int(i)] = str(r[0]) if r else None
    return out, total


# ── Yêu cầu quên mật khẩu ─────────────────────────────────────────────────────

def pwreset_open_id(username: str) -> Optional[int]:
    with _main() as conn:
        row = conn.execute("SELECT id FROM password_reset_requests WHERE username = ? AND status = 'open' "
                           "ORDER BY id DESC LIMIT 1;", (username,)).fetchone()
    return int(row[0]) if row else None


def pwreset_insert(username: str, note: str, ip: str, now: str) -> int:
    with _main() as conn:
        cur = conn.execute("INSERT INTO password_reset_requests (username, note, source_ip, created_at, status) "
                           "VALUES (?, ?, ?, ?, 'open');", (username, note, ip, now))
        conn.commit()
        rid = cur.lastrowid
        if rid is None:
            rid = conn.execute("SELECT MAX(id) FROM password_reset_requests WHERE username = ?;", (username,)).fetchone()[0]
    return int(rid)


def pwreset_list(status: str, limit: int) -> List[Dict[str, Any]]:
    with _main() as conn:
        rows = conn.execute("SELECT id, username, note, source_ip, created_at, status, handled_by, handled_at "
                            "FROM password_reset_requests WHERE status = ? ORDER BY id DESC LIMIT ?;", (status, limit)).fetchall()
    keys = ("id", "username", "note", "source_ip", "created_at", "status", "handled_by", "handled_at")
    return [dict(zip(keys, r)) for r in rows]


def pwreset_close(where_sql: str, args: Tuple[Any, ...], status: str, by: str, now: str) -> int:
    """Đóng yêu cầu đang mở; `where_sql` chỉ là một trong hai mẫu cố định của password_reset.py (id = ? / username = ?)."""
    assert where_sql in ("id = ?", "username = ?")
    with _main() as conn:
        cur = conn.execute(f"UPDATE password_reset_requests SET status = ?, handled_by = ?, handled_at = ? "
                           f"WHERE {where_sql} AND status = 'open';", (status, by, now, *args))
        conn.commit()
        return int(cur.rowcount or 0)
