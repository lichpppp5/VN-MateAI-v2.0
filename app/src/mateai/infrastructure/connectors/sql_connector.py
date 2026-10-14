# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 Dương Thanh Lịch — VN-MateAI. See LICENSE and NOTICE.
"""
mateai/infrastructure/connectors/sql_connector.py
=================================================
Nguồn dữ liệu SQL CHỈ-ĐỌC: AI gọi truy vấn ĐẶT TÊN do người quản trị khai báo, điền tham số đã khai báo.

  PostgreSQL (psycopg — sẵn có) · MySQL / MariaDB (pymysql) · SQL Server (pymssql hoặc pyodbc) ·
  Oracle (oracledb, chế độ thin) · SQLite (thư viện chuẩn).

Chỉ-đọc được bảo đảm ở HAI lớp: (1) câu SQL khai báo chỉ được là MỘT câu SELECT / WITH (`operations.validate_readonly_sql`,
kiểm tra lúc lưu VÀ lúc chạy); (2) phiên CSDL mở ở chế độ chỉ-đọc nếu driver hỗ trợ (PostgreSQL, MySQL, Oracle, SQLite) —
nên một hàm có tác dụng phụ trong SELECT cũng bị chặn ở những CSDL đó. Tài khoản CSDL nên chỉ có quyền SELECT.

Thư viện driver là TUỲ CHỌN (`requirements-connectors.txt`): thiếu thì báo đúng lệnh cài, không sập.
"""
from __future__ import annotations

import datetime as _dt
import decimal
import logging
import re
import uuid
from pathlib import Path
from typing import Any, Dict, List, Tuple

from mateai.infrastructure.connectors import operations as ops

logger = logging.getLogger(__name__)

#: driver -> (module cần import, gói pip)
DRIVER_PACKAGES = {
    "postgresql": ("psycopg", "psycopg[binary]"),
    "mysql": ("pymysql", "pymysql"),
    "mssql": ("pymssql", "pymssql"),
    "oracle": ("oracledb", "oracledb"),
    "sqlite": ("sqlite3", ""),
}


class SqlSourceError(RuntimeError):
    """Lỗi cấu hình / kết nối / truy vấn với thông điệp đã dịch cho người vận hành."""


def convert_placeholders(sql: str, style: str) -> Tuple[str, List[str]]:
    """`:ten` -> kiểu tham số của driver, bỏ qua nội dung chuỗi '...' và dạng ép kiểu `::`.

    style: `named` (:ten) · `pyformat` (%(ten)s, đồng thời nhân đôi mọi '%') · `qmark` (? theo thứ tự).
    Trả (sql mới, danh sách tên tham số theo thứ tự xuất hiện)."""
    out: List[str] = []
    order: List[str] = []
    i, n = 0, len(sql)
    while i < n:
        c = sql[i]
        if c == "'":
            j = i + 1
            while j < n:
                if sql[j] == "'" and sql[j + 1:j + 2] == "'":
                    j += 2
                    continue
                if sql[j] == "'":
                    break
                j += 1
            lit = sql[i:j + 1]
            out.append(lit.replace("%", "%%") if style == "pyformat" else lit)
            i = j + 1
            continue
        if c == ":" and sql[i + 1:i + 2] != ":" and (i == 0 or not (sql[i - 1].isalnum() or sql[i - 1] in "_:")):
            m = re.match(r":([A-Za-z_][A-Za-z0-9_]{0,39})\b", sql[i:])
            if m:
                name = m.group(1)
                order.append(name)
                out.append({"named": f":{name}", "pyformat": f"%({name})s", "qmark": "?"}[style])
                i += m.end()
                continue
        out.append("%%" if (c == "%" and style == "pyformat") else c)
        i += 1
    return "".join(out), order


def _jsonable(value: Any) -> Any:
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    if isinstance(value, decimal.Decimal):
        return int(value) if value == value.to_integral_value() else float(value)
    if isinstance(value, (_dt.datetime, _dt.date, _dt.time)):
        return value.isoformat()
    if isinstance(value, _dt.timedelta):
        return value.total_seconds()
    if isinstance(value, uuid.UUID):
        return str(value)
    if isinstance(value, (bytes, bytearray, memoryview)):
        return f"<{len(bytes(value))} bytes>"
    return str(value)


def _need(driver: str) -> Any:
    module, package = DRIVER_PACKAGES[driver]
    try:
        return __import__(module)
    except ImportError:
        raise SqlSourceError(f"Chưa cài thư viện kết nối {driver} (`{module}`) — chạy: pip install {package or module}  "
                             "(hoặc pip install -r requirements-connectors.txt)")


def connect(source: Dict[str, Any]):
    """Như `_connect` nhưng mọi lỗi của driver (sai mật khẩu, không tới được máy chủ…) thành SqlSourceError."""
    try:
        return _connect(source)
    except SqlSourceError:
        raise
    except Exception as exc:  # noqa: BLE001
        raise SqlSourceError(f"Không kết nối được CSDL: {type(exc).__name__}: {str(exc)[:200]}")


def _connect(source: Dict[str, Any]) -> Tuple[Any, str]:
    """Mở kết nối chỉ-đọc. Trả (kết nối, kiểu tham số của driver)."""
    c = source.get("connection") or {}
    driver = c.get("driver")
    password = str(source.get("auth_value") or "")
    timeout = max(1, int(float(source.get("timeout_seconds") or 10)))
    host, database, user, port = c.get("host", ""), c.get("database", ""), c.get("user", ""), int(c.get("port") or 0)

    if driver == "sqlite":
        import sqlite3
        path = Path(database)
        if not path.is_file():
            raise SqlSourceError(f"Không thấy tệp SQLite: {database}")
        conn = sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True, timeout=timeout)
        conn.execute("PRAGMA query_only = ON")
        return conn, "named"
    if driver == "postgresql":
        psycopg = _need("postgresql")
        conn = psycopg.connect(host=host, port=port or 5432, dbname=database, user=user, password=password,
                               connect_timeout=timeout, sslmode="require" if c.get("ssl") else "prefer",
                               options=f"-c default_transaction_read_only=on -c statement_timeout={timeout * 1000}")
        conn.read_only = True
        return conn, "pyformat"
    if driver == "mysql":
        pymysql = _need("mysql")
        conn = pymysql.connect(host=host, port=port or 3306, user=user, password=password, database=database,
                               connect_timeout=timeout, read_timeout=timeout, charset="utf8mb4",
                               ssl={} if c.get("ssl") else None)
        with conn.cursor() as cur:
            cur.execute("SET SESSION TRANSACTION READ ONLY")
            try:
                cur.execute(f"SET SESSION max_execution_time = {timeout * 1000}")
            except Exception:      # noqa: BLE001 — MariaDB / bản cũ không có biến này
                pass
        return conn, "pyformat"
    if driver == "mssql":
        try:
            pymssql = _need("mssql")
            conn = pymssql.connect(server=host, port=str(port or 1433), user=user, password=password, database=database,
                                   login_timeout=timeout, timeout=timeout, read_only=True)
            return conn, "pyformat"
        except SqlSourceError:
            try:
                import pyodbc
            except ImportError:
                raise SqlSourceError("Chưa cài thư viện kết nối SQL Server — chạy: pip install pymssql  (hoặc pyodbc + ODBC Driver 18)")
            dsn = (f"DRIVER={{{c.get('odbc_driver') or 'ODBC Driver 18 for SQL Server'}}};SERVER={host},{port or 1433};"
                   f"DATABASE={database};UID={user};PWD={password};ApplicationIntent=ReadOnly;"
                   f"Encrypt={'yes' if c.get('ssl') else 'no'};TrustServerCertificate=yes")
            return pyodbc.connect(dsn, timeout=timeout), "qmark"
    if driver == "oracle":
        oracledb = _need("oracle")
        service = c.get("service_name") or database
        conn = oracledb.connect(user=user, password=password, dsn=f"{host}:{port or 1521}/{service}")
        conn.call_timeout = timeout * 1000
        with conn.cursor() as cur:
            cur.execute("SET TRANSACTION READ ONLY")
        return conn, "named"
    raise SqlSourceError(f"Driver '{driver}' chưa được hỗ trợ")


def run_query(source: Dict[str, Any], query_name: str, args: Dict[str, Any] | None, limit: int) -> Dict[str, Any]:
    """Chạy một truy vấn đặt tên. Đồng bộ (gọi qua `asyncio.to_thread`). Ném SqlSourceError / ValueError."""
    queries = source.get("queries") or {}
    query = queries.get(query_name)
    if not query:
        raise ValueError(f"Không có truy vấn tên '{query_name}'. Đã khai báo: {', '.join(queries) or '(chưa có)'}")
    sql = ops.validate_readonly_sql(query["sql"])         # kiểm tra lại lúc chạy (tệp khai báo có thể bị sửa tay)
    values = ops.coerce_args(query.get("params") or {}, args)
    conn, style = connect(source)
    try:
        text, order = convert_placeholders(sql, style)
        params: Any
        if style == "qmark":
            params = [values.get(n) for n in order]
        else:
            params = {n: values.get(n) for n in order}
        cur = conn.cursor()
        cur.execute(text, params) if params else cur.execute(text)
        names = [str(d[0]) for d in (cur.description or [])]
        fetched = cur.fetchmany(limit + 1)
        truncated = len(fetched) > limit
        rows = [{names[i]: _jsonable(v) for i, v in enumerate(r)} for r in fetched[:limit]]
        return {"rows": rows, "columns": names[:12], "total": len(rows) + (1 if truncated else 0), "returned": len(rows),
                "truncated": truncated}
    except SqlSourceError:
        raise
    except Exception as exc:  # noqa: BLE001
        raise SqlSourceError(f"{type(exc).__name__}: {str(exc)[:300]}")
    finally:
        try:
            conn.rollback()
        except Exception:      # noqa: BLE001
            pass
        conn.close()


def ping(source: Dict[str, Any]) -> float:
    """Mở kết nối + SELECT 1. Trả độ trễ (ms). Ném SqlSourceError."""
    import time
    t0 = time.monotonic()
    conn, _ = connect(source)
    try:
        cur = conn.cursor()
        cur.execute("SELECT 1 FROM dual" if (source.get("connection") or {}).get("driver") == "oracle" else "SELECT 1")
        cur.fetchone()
    except Exception as exc:  # noqa: BLE001
        raise SqlSourceError(f"{type(exc).__name__}: {str(exc)[:200]}")
    finally:
        conn.close()
    return (time.monotonic() - t0) * 1000
