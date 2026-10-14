# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 Dương Thanh Lịch — VN-MateAI. See LICENSE and NOTICE.
"""
tests/test_sql_connector.py
===========================
Nguồn dữ liệu SQL chỉ-đọc (`kind="sql"`): truy vấn ĐẶT TÊN có tham số, chạy thật trên SQLite và PostgreSQL.

  - tham số `:ten` chuyển đúng cú pháp từng driver, không đụng nội dung chuỗi / `::` / `%`;
  - SQL ghi bị chặn lúc LƯU và lúc CHẠY (tệp khai báo bị sửa tay vẫn không chạy được lệnh ghi);
  - phiên CSDL mở chế độ chỉ-đọc: PostgreSQL chặn cả hàm có tác dụng phụ (nextval) trong SELECT;
  - thiếu thư viện driver (MySQL / SQL Server / Oracle) thì báo đúng lệnh cài, không sập.
"""
from __future__ import annotations

import sqlite3
from urllib.parse import urlsplit

import pytest

from mateai.infrastructure.connectors import custom_registry as reg
from mateai.infrastructure.connectors import operations as ops
from mateai.infrastructure.connectors import sql_connector as sc
from mateai.infrastructure.connectors.generic_connector import GenericConnector


def test_convert_placeholders_per_driver_style():
    sql = "SELECT * FROM t WHERE a = :a AND b::int > :b AND note = ':khong' AND name LIKE '%x%' AND c = :a"
    named, order = sc.convert_placeholders(sql, "named")
    assert named == sql and order == ["a", "b", "a"]
    py, order = sc.convert_placeholders(sql, "pyformat")
    assert py == "SELECT * FROM t WHERE a = %(a)s AND b::int > %(b)s AND note = ':khong' AND name LIKE '%%x%%' AND c = %(a)s"
    qm, order = sc.convert_placeholders(sql, "qmark")
    assert qm.count("?") == 3 and order == ["a", "b", "a"] and "':khong'" in qm and "::int" in qm


@pytest.mark.parametrize("sql,why", [
    ("DELETE FROM t", "SELECT hoặc WITH"), ("SELECT 1; DROP TABLE t", "một câu lệnh"),
    ("SELECT * INTO copy_t FROM t", "INTO"), ("WITH x AS (SELECT 1) UPDATE t SET a = 1", "UPDATE"),
    ("SELECT pg_sleep(1); SELECT 2", "một câu lệnh"), ("", "trống"),
])
def test_write_sql_is_refused_at_save_time(sql, why):
    with pytest.raises(ValueError) as exc:
        reg._normalise_source("db-1", {"title": "t", "kind": "sql", "connection": {"driver": "sqlite", "database": "x.db"},
                                       "queries": {"q": {"sql": sql}}}, None)
    assert why in str(exc.value)


def test_sql_source_declaration_checks():
    base = {"title": "t", "kind": "sql", "queries": {"q": {"sql": "SELECT 1"}}}
    with pytest.raises(ValueError, match="driver"):
        reg._normalise_source("db-1", {**base, "connection": {"driver": "mongo", "database": "d"}}, None)
    with pytest.raises(ValueError, match="host"):
        reg._normalise_source("db-1", {**base, "connection": {"driver": "postgresql", "database": "d"}}, None)
    with pytest.raises(ValueError, match="chưa được khai báo"):
        reg._normalise_source("db-1", {**base, "connection": {"driver": "sqlite", "database": "d"},
                                       "queries": {"q": {"sql": "SELECT * FROM t WHERE a = :a"}}}, None)
    with pytest.raises(ValueError, match="ít nhất một truy vấn"):
        reg._normalise_source("db-1", {"title": "t", "kind": "sql", "connection": {"driver": "sqlite", "database": "d"}}, None)
    rec = reg._normalise_source("db-1", {**base, "connection": {"driver": "sqlite", "database": "d"}}, None)
    assert rec["kind"] == "sql" and rec["base_url"] == "" and rec["actions"] == {}


@pytest.fixture
def sqlite_db(tmp_path):
    path = tmp_path / "kho.db"
    con = sqlite3.connect(path)
    con.executescript("CREATE TABLE sp (id INTEGER PRIMARY KEY, ten TEXT, ton INTEGER, gia REAL);")
    con.executemany("INSERT INTO sp (ten, ton, gia) VALUES (?, ?, ?)", [(f"SP {i:02d}", i * 3, i * 1000.5) for i in range(1, 31)])
    con.commit()
    con.close()
    return str(path)


def sql_source(db, **kw):
    payload = {"title": "Kho", "kind": "sql", "connection": {"driver": "sqlite", "database": db},
               "queries": {"ton thap": {"description": "Sản phẩm tồn dưới mức", "sql":
                                        "SELECT id, ten, ton FROM sp WHERE ton < :muc ORDER BY id",
                                        "params": {"muc": {"type": "integer", "default": 20, "minimum": 0, "maximum": 1000}}},
                           "tat ca": {"sql": "SELECT * FROM sp ORDER BY id"}}, **kw}
    return GenericConnector(reg._normalise_source("kho-1", payload, None))


async def test_sqlite_named_query_with_typed_parameters(sqlite_db):
    c = sql_source(sqlite_db)
    res = await c.fetch_data({"path": "ton thap", "args": {"muc": "13"}})
    assert res.success, res.error
    assert [r["id"] for r in res.data["rows"]] == [1, 2, 3, 4] and res.data["columns"] == ["id", "ten", "ton"]
    assert (await c.fetch_data({"path": "ton thap"})).data["returned"] == 6          # mặc định 20 -> ton 3..18
    bad = await c.fetch_data({"path": "ton thap", "args": {"muc": "abc"}})
    assert not bad.success and "phải là integer" in bad.error
    over = await c.fetch_data({"path": "ton thap", "args": {"muc": 99999}})
    assert not over.success and "lớn hơn" in over.error


async def test_sqlite_sql_injection_via_parameter_is_just_a_value(sqlite_db):
    c = sql_source(sqlite_db, queries={"tim": {"sql": "SELECT id, ten FROM sp WHERE ten = :ten",
                                               "params": {"ten": {"type": "string", "required": True}}}})
    res = await c.fetch_data({"path": "tim", "args": {"ten": "x' OR '1'='1"}})
    assert res.success and res.data["returned"] == 0                                  # là GIÁ TRỊ, không phải mã SQL
    con = sqlite3.connect(sqlite_db)
    assert con.execute("SELECT COUNT(*) FROM sp").fetchone()[0] == 30
    con.close()


async def test_row_limit_and_truncation(sqlite_db):
    res = await sql_source(sqlite_db).fetch_data({"path": "tat ca", "limit": 10})
    assert res.data["returned"] == 10 and res.data["truncated"] is True


async def test_hand_edited_declaration_with_a_write_statement_cannot_run(sqlite_db):
    c = sql_source(sqlite_db)
    c.source["queries"]["tat ca"]["sql"] = "DELETE FROM sp"                         # tệp khai báo bị sửa tay
    res = await c.fetch_data({"path": "tat ca"})
    assert not res.success and "SELECT hoặc WITH" in res.error
    con = sqlite3.connect(sqlite_db)
    assert con.execute("SELECT COUNT(*) FROM sp").fetchone()[0] == 30
    con.close()


async def test_unknown_query_name_lists_the_declared_ones(sqlite_db):
    res = await sql_source(sqlite_db).fetch_data({"path": "khong co"})
    assert not res.success and "ton thap" in res.error and "tat ca" in res.error


async def test_health_check_and_missing_file(sqlite_db, tmp_path):
    assert (await sql_source(sqlite_db).health_check()).success
    miss = await sql_source(str(tmp_path / "khong-co.db")).health_check()
    assert not miss.success and "Không thấy tệp SQLite" in miss.error


@pytest.mark.parametrize("driver,pkg", [("mysql", "pymysql"), ("oracle", "oracledb")])
async def test_missing_driver_library_names_the_pip_command(driver, pkg):
    import importlib.util
    if importlib.util.find_spec(pkg):
        pytest.skip(f"{pkg} đã cài trên máy này")
    src = reg._normalise_source("db-x", {"title": "t", "kind": "sql", "connection": {"driver": driver, "host": "h", "database": "d"},
                                         "queries": {"q": {"sql": "SELECT 1"}}}, None)
    res = await GenericConnector(src).fetch_data({"path": "q"})
    assert not res.success and f"pip install {pkg}" in res.error


# ── PostgreSQL thật ─────────────────────────────────────────────────────────

pgserver = pytest.importorskip("pgserver", reason="cần gói dev pgserver (PostgreSQL thật)")


@pytest.fixture(scope="module")
def pg(tmp_path_factory):
    import psycopg
    srv = pgserver.get_server(str(tmp_path_factory.mktemp("pgsql")), cleanup_mode="stop")
    uri = srv.get_uri()
    with psycopg.connect(uri, autocommit=True) as conn:
        conn.execute("CREATE TABLE don_hang (id serial PRIMARY KEY, khach text, so_tien numeric(12,2), ngay date)")
        conn.execute("INSERT INTO don_hang (khach, so_tien, ngay) SELECT 'KH ' || g, g * 1500.50, DATE '2026-10-01' + g FROM generate_series(1, 40) g")
        conn.execute("CREATE SEQUENCE seq_cam")
    yield uri
    try:
        srv.cleanup()
    except Exception:
        pass


def pg_source(uri, **kw):
    u = urlsplit(uri)
    payload = {"title": "Đơn hàng", "kind": "sql",
               "connection": {"driver": "postgresql", "host": u.hostname, "port": u.port, "database": u.path.lstrip("/"),
                              "user": u.username or "postgres"},
               "auth_value": u.password or "",
               "queries": {"lon": {"sql": "SELECT id, khach, so_tien, ngay FROM don_hang WHERE so_tien > :min AND khach LIKE '%KH%' ORDER BY id",
                                   "params": {"min": {"type": "number", "required": True}}},
                           "ghi": {"sql": "SELECT nextval('seq_cam') AS n"}}, **kw}
    return GenericConnector(reg._normalise_source("pg-1", payload, None))


async def test_postgres_query_with_params_decimals_dates_and_percent_literals(pg):
    res = await pg_source(pg).fetch_data({"path": "lon", "args": {"min": 59000}})
    assert res.success, res.error
    rows = res.data["rows"]
    assert rows[0]["id"] == 40 - len(rows) + 1 and rows[-1]["id"] == 40
    assert isinstance(rows[0]["so_tien"], (int, float)) and rows[0]["ngay"].startswith("2026-")        # Decimal / date -> JSON được


async def test_postgres_session_is_read_only_even_for_side_effect_functions(pg):
    res = await pg_source(pg).fetch_data({"path": "ghi"})                    # SELECT nextval(...) = tác dụng phụ
    assert not res.success and "read-only" in res.error.lower()
    import psycopg
    with psycopg.connect(pg, autocommit=True) as conn:
        assert conn.execute("SELECT nextval('seq_cam')").fetchone()[0] == 1   # chưa hề bị tăng


async def test_postgres_health_and_bad_password(pg):
    assert (await pg_source(pg).health_check()).success
    u = urlsplit(pg)
    bad = pg_source(pg, connection={"driver": "postgresql", "host": u.hostname, "port": 1, "database": "x", "user": "u"})
    res = await bad.health_check()
    assert not res.success
