"""Read-only DB browser. Unit checks + integration against THROWAWAY MariaDB/PostgreSQL instances (never the live ones).

Integration runs only when KVL_TEST_MDB_SOCKET / KVL_TEST_PG_PORT point at disposable servers.
"""
import os
import subprocess

import pytest
from pydantic import ValidationError

from kvl_agent.operations import db_browser as b
from kvl_agent.testing import make_ctx

DB = "shop_a1b2c3"
MDB_SOCK = os.environ.get("KVL_TEST_MDB_SOCKET")
PG_PORT = os.environ.get("KVL_TEST_PG_PORT")
MBIN, PBIN = "/opt/local/lib/mariadb-10.11/bin", "/opt/local/lib/postgresql16/bin"
assert PG_PORT not in ("54329",), "never run against the live cluster"


@pytest.mark.parametrize("bad", ["", "a`b", 'a"b', "a\\b", "a\nb", "x" * 65, "a\x00"])
def test_names_validated(bad):
    with pytest.raises(ValidationError):
        b.Rows(engine="mariadb", db_name=DB, table=bad)
    with pytest.raises(ValidationError):
        b.Rows(engine="mariadb", db_name=DB, table="t", order_by=bad)


@pytest.mark.parametrize("db", ["mysql", "information_schema", "kvl_panel", "postgres", "shop_a1b2c3;drop"])
def test_only_managed_databases(db):
    with pytest.raises(ValidationError):
        b.Tables(engine="mariadb", db_name=db)


def test_limits():
    with pytest.raises(ValidationError):
        b.Rows(engine="mariadb", db_name=DB, table="t", limit=101)
    with pytest.raises(ValidationError):
        b.Rows(engine="mariadb", db_name=DB, table="t", filter_value="x" * 201)


def test_rows_sql_is_read_only_and_quoted():
    cols = [{"name": "my col", "type": "varchar(10)"}, {"name": "img", "type": "blob"}]
    p = b.Rows(engine="mariadb", db_name=DB, table="my table", order_by="my col", filter_column="my col", filter_value="x' OR 1=1 -- ")
    sql = b.rows_sql("mariadb", DB, p, cols)
    assert sql[0] == "SET SESSION TRANSACTION READ ONLY" and sql[1] == "START TRANSACTION READ ONLY" and "max_statement_time=5" in sql[2]
    assert "`my col`" in sql[2] and b.my_q("a`b") == "`a``b`" and b.pg_q('a"b') == '"a""b"' and "`my table`" in sql[2] and "'x'' OR 1=1 -- '" in sql[2] and "<binary " in sql[2]
    pg = b.rows_sql("postgresql", DB, b.Rows(engine="postgresql", db_name=DB, table='t', filter_column="c", filter_value="50%_x", filter_op="contains"), [{"name": "c", "type": "text"}])
    assert pg[0] == "BEGIN READ ONLY" and "statement_timeout" in pg[1] and "'%50\\%\\_x%'" in pg[2]


def test_not_configured_without_admin(tmp_path):
    r = b.browse_tables(b.Tables(engine="mariadb", db_name=DB), make_ctx(tmp_path))
    assert r["ok"] is False and r["status"] == "not_configured"


# ------------------------------------------------------------------ integration (disposable servers only)
SEED_MDB = f"""
DROP DATABASE IF EXISTS {DB}; CREATE DATABASE {DB}; USE {DB};
CREATE TABLE `my table` (id INT PRIMARY KEY, name VARCHAR(50), note TEXT, img BLOB, UNIQUE KEY uq_name (name));
INSERT INTO `my table` VALUES (1,'alpha','line1\\nline2\\tTab',X'00FF10'),(2,'beta',REPEAT('x',5000),NULL),(3,'50%_off','q''uote',NULL);
CREATE TABLE secret_marker (v INT); INSERT INTO secret_marker VALUES (42);
"""
SEED_PG = """
DROP TABLE IF EXISTS "my table"; DROP TABLE IF EXISTS secret_marker;
CREATE TABLE "my table" (id INT PRIMARY KEY, name VARCHAR(50) UNIQUE, note TEXT, img BYTEA);
INSERT INTO "my table" VALUES (1,'alpha',E'line1\\nline2\\tTab','\\x00ff10'),(2,'beta',repeat('x',5000),NULL),(3,'50%_off','q''uote',NULL);
CREATE TABLE secret_marker (v INT); INSERT INTO secret_marker VALUES (42);
"""


@pytest.fixture(params=["mariadb", "postgresql"])
def live_ctx(request, tmp_path, monkeypatch):
    eng = request.param
    monkeypatch.setenv("PATH", f"{MBIN}:{PBIN}:" + os.environ.get("PATH", ""))
    ctx = make_ctx(tmp_path)
    if eng == "mariadb":
        if not MDB_SOCK:
            pytest.skip("no disposable MariaDB")
        cnf = tmp_path / "admin.cnf"
        cnf.write_text(f"[client]\nsocket={MDB_SOCK}\nuser=root\n")
        subprocess.run([f"{MBIN}/mariadb", "--no-defaults", f"--socket={MDB_SOCK}", "-u", "root", "-e", SEED_MDB], check=True)
        ctx.extra["db_admin"] = {"mariadb": {"defaults_file": str(cnf)}}
    else:
        if not PG_PORT:
            pytest.skip("no disposable PostgreSQL")
        subprocess.run([f"{PBIN}/psql", "-h", "127.0.0.1", "-p", PG_PORT, "-U", "kvl", "-d", "postgres", "-c", f"DROP DATABASE IF EXISTS {DB}", "-c", f"CREATE DATABASE {DB}"], check=True, capture_output=True)
        subprocess.run([f"{PBIN}/psql", "-h", "127.0.0.1", "-p", PG_PORT, "-U", "kvl", "-d", DB, "-c", SEED_PG], check=True, capture_output=True)
        ctx.extra["db_admin"] = {"postgresql": {"host": "127.0.0.1", "port": PG_PORT, "user": "kvl", "password": ""}}
    return eng, ctx


def test_integration_tables_describe_rows(live_ctx):
    eng, ctx = live_ctx
    t = b.browse_tables(b.Tables(engine=eng, db_name=DB), ctx)
    assert t["ok"], t
    assert {x["name"] for x in t["tables"]} == {"my table", "secret_marker"}
    d = b.browse_describe(b.Describe(engine=eng, db_name=DB, table="my table"), ctx)
    assert d["ok"] and [c["name"] for c in d["columns"]] == ["id", "name", "note", "img"] and d["indexes"]
    r = b.browse_rows(b.Rows(engine=eng, db_name=DB, table="my table", order_by="id", limit=2), ctx)
    assert r["ok"] and r["has_more"] and [str(row[0]) for row in r["rows"]] == ["1", "2"]
    alpha, beta = r["rows"]
    assert alpha[2] == "line1\nline2\tTab"  # newlines/tabs survive intact
    assert alpha[3] == "<binary 3 bytes>" and beta[3] is None
    assert len(beta[2]) == b.CELL_MAX + 1 and beta[2].endswith("…")
    r = b.browse_rows(b.Rows(engine=eng, db_name=DB, table="my table", filter_column="name", filter_op="contains", filter_value="%_"), ctx)
    assert [row[1] for row in r["rows"]] == ["50%_off"]  # wildcards are literal
    r = b.browse_rows(b.Rows(engine=eng, db_name=DB, table="my table", filter_column="note", filter_value="q'uote"), ctx)
    assert len(r["rows"]) == 1
    # injection attempts in the value find nothing and change nothing
    r = b.browse_rows(b.Rows(engine=eng, db_name=DB, table="my table", filter_column="name", filter_value="x' OR '1'='1"), ctx)
    assert r["ok"] and r["rows"] == []


def test_integration_unknown_names_rejected(live_ctx):
    eng, ctx = live_ctx
    assert b.browse_rows(b.Rows(engine=eng, db_name=DB, table="nope"), ctx)["status"] == "not_found"
    assert b.browse_rows(b.Rows(engine=eng, db_name=DB, table="my table", order_by="id; DROP TABLE secret_marker"), ctx)["status"] == "invalid_column"
    assert b.browse_describe(b.Describe(engine=eng, db_name=DB, table="secret_marker"), ctx)["ok"]  # table still exists
