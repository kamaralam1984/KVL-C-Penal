"""Read-only database browser for panel-managed customer databases (phpMyAdmin-style viewer, no SQL console).

Safety:
- Only panel-managed database names (same validator as db.* ops).
- Table/column names are accepted only if they exist in the catalog of that database; they are then quoted by doubling
  the quote character. Filter values are escaped literals; nothing else from the caller reaches SQL.
- Every row query runs inside a READ ONLY transaction with a 5 s statement timeout; cells are truncated and binary is summarised.
- Results are JSON produced by the database itself (one JSON document per output line), so tabs/newlines in data are safe.
"""
import json
import os
import re
import secrets
from typing import Literal

from pydantic import Field, field_validator

from kvl_agent.context import OpContext
from kvl_agent.operations.databases import SAFE_PATH, DbRef, _admin, _not_configured, my_lit, pg_lit
from kvl_agent.redact import redact_text
from kvl_agent.registry import operation

NAME_RE = re.compile(r"^[^\x00-\x1f`\"\\]{1,64}$")
CELL_MAX = 2000
BIN_TYPES = re.compile(r"(?i)^(tiny|medium|long)?blob|^(var)?binary|^bytea$")


def _name(v: str) -> str:
    if not NAME_RE.fullmatch(v):
        raise ValueError("invalid name")
    return v


class Tables(DbRef):
    pass


class Describe(DbRef):
    table: str
    _t = field_validator("table")(_name)


class Rows(DbRef):
    table: str
    limit: int = Field(50, ge=1, le=100)
    offset: int = Field(0, ge=0, le=10_000_000)
    order_by: str | None = None
    order_dir: Literal["asc", "desc"] = "asc"
    filter_column: str | None = None
    filter_op: Literal["eq", "contains"] = "eq"
    filter_value: str | None = Field(None, max_length=200)

    _t = field_validator("table")(_name)
    _o = field_validator("order_by", "filter_column")(lambda v: None if v is None else _name(v))

    @field_validator("filter_value")
    @classmethod
    def _fv(cls, v: str | None) -> str | None:
        if v is not None and ("\x00" in v):
            raise ValueError("invalid filter value")
        return v


def my_q(x: str) -> str:
    return "`" + x.replace("`", "``") + "`"


def pg_q(x: str) -> str:
    return '"' + x.replace('"', '""') + '"'


def _query(ctx: OpContext, engine: str, db: str, sql: list[str]) -> tuple[bool, list, str]:
    """Run statements; return parsed JSON values from output lines."""
    d = ctx.path("tmp", "db")
    d.mkdir(parents=True, exist_ok=True)
    os.chmod(d, 0o700)
    script = d / f"{secrets.token_hex(8)}.sql"
    if not SAFE_PATH.fullmatch(str(script)):
        return False, [], "Unsupported agent data directory path."
    fd = os.open(script, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w") as f:
        f.write("".join(s + ";\n" for s in sql))
    try:
        cfg = _admin(ctx, engine)
        if engine == "mariadb":
            argv = ["mysql", f"--defaults-extra-file={cfg['defaults_file']}", "--batch", "--raw", "--skip-column-names", "-D", db, "-e", f"source {script}"]
            env = {}
        else:
            argv = ["psql", "-X", "-q", "-A", "-t", "-v", "ON_ERROR_STOP=1", "-h", str(cfg["host"]), "-p", str(cfg["port"]), "-U", cfg["user"], "-d", db, "-f", str(script)]
            env = {"PGPASSWORD": cfg.get("password", ""), "PGCONNECT_TIMEOUT": "10"}
        r = ctx.run(argv, timeout=30, env=env)
        if not r.ok:
            return False, [], redact_text((r.stderr or r.stdout)[-500:], [cfg.get("password")] if engine == "postgresql" and cfg.get("password") else None)
        out = []
        for line in (r.stdout or "").splitlines():
            line = line.strip()
            if line[:1] in "[{":
                try:
                    out.append(json.loads(line))
                except ValueError:
                    pass
        return True, out, ""
    finally:
        script.unlink(missing_ok=True)


def _catalog_sql(engine: str, db: str, table: str | None = None) -> list[str]:
    if engine == "mariadb":
        if table is None:
            return [f"SELECT JSON_OBJECT('name', table_name, 'rows', table_rows, 'bytes', COALESCE(data_length + index_length, 0), 'type', table_type) "
                    f"FROM information_schema.tables WHERE table_schema = {my_lit(db)} ORDER BY table_name"]
        return [f"SELECT JSON_OBJECT('name', column_name, 'type', column_type, 'nullable', is_nullable = 'YES', 'key', column_key, 'default', column_default, 'extra', extra) "
                f"FROM information_schema.columns WHERE table_schema = {my_lit(db)} AND table_name = {my_lit(table)} ORDER BY ordinal_position",
                f"SELECT JSON_OBJECT('index', index_name, 'column', column_name, 'unique', non_unique = 0, 'seq', seq_in_index) "
                f"FROM information_schema.statistics WHERE table_schema = {my_lit(db)} AND table_name = {my_lit(table)} ORDER BY index_name, seq_in_index"]
    if table is None:
        return ["SELECT json_build_object('name', c.relname, 'rows', GREATEST(c.reltuples, 0)::bigint, 'bytes', pg_total_relation_size(c.oid), 'type', c.relkind) "
                "FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace WHERE n.nspname = 'public' AND c.relkind IN ('r','v','m','p') ORDER BY c.relname"]
    return [f"SELECT json_build_object('name', column_name, 'type', data_type, 'nullable', is_nullable = 'YES', 'key', '', 'default', column_default, 'extra', '') "
            f"FROM information_schema.columns WHERE table_schema = 'public' AND table_name = {pg_lit(table)} ORDER BY ordinal_position",
            f"SELECT json_build_object('index', indexname, 'column', indexdef, 'unique', indexdef ILIKE 'CREATE UNIQUE%', 'seq', 1) "
            f"FROM pg_indexes WHERE schemaname = 'public' AND tablename = {pg_lit(table)} ORDER BY indexname"]


def _tables(ctx: OpContext, engine: str, db: str):
    ok, rows, err = _query(ctx, engine, db, _catalog_sql(engine, db))
    return ok, [r for r in rows if isinstance(r, dict)], err


@operation("db.browse_tables", Tables, timeout=45)
def browse_tables(p: Tables, ctx: OpContext) -> dict:
    nc = _not_configured(ctx, p.engine)
    if nc:
        return nc
    ok, rows, err = _tables(ctx, p.engine, p.db_name)
    if not ok:
        return {"ok": False, "status": "query_failed", "message": err}
    kinds = {"r": "table", "v": "view", "m": "view", "p": "table", "BASE TABLE": "table", "VIEW": "view", "SYSTEM VIEW": "view"}
    return {"ok": True, "tables": [{"name": r["name"], "rows": r.get("rows"), "bytes": r.get("bytes"), "type": kinds.get(r.get("type"), "table")} for r in rows][:1000]}


def _describe(ctx: OpContext, engine: str, db: str, table: str):
    ok, tables, err = _tables(ctx, engine, db)
    if not ok:
        return None, None, err
    if table not in {t["name"] for t in tables}:
        return None, None, "unknown table"
    ok, rows, err = _query(ctx, engine, db, _catalog_sql(engine, db, table))
    if not ok:
        return None, None, err
    cols = [r for r in rows if "name" in r]
    idx = [r for r in rows if "index" in r]
    return cols, idx, ""


@operation("db.browse_describe", Describe, timeout=45)
def browse_describe(p: Describe, ctx: OpContext) -> dict:
    nc = _not_configured(ctx, p.engine)
    if nc:
        return nc
    cols, idx, err = _describe(ctx, p.engine, p.db_name, p.table)
    if cols is None:
        return {"ok": False, "status": "not_found" if err == "unknown table" else "query_failed", "message": err}
    return {"ok": True, "table": p.table, "columns": cols, "indexes": idx}


def rows_sql(engine: str, db: str, p: Rows, cols: list[dict]) -> list[str]:
    names = [c["name"] for c in cols]
    q, lit = (my_q, my_lit) if engine == "mariadb" else (pg_q, pg_lit)
    exprs = []
    for c in cols:
        n = q(c["name"])
        if BIN_TYPES.search(str(c.get("type") or "")):
            size = f"LENGTH({n})" if engine == "mariadb" else f"octet_length({n})"
            exprs.append(f"CASE WHEN {n} IS NULL THEN NULL ELSE CONCAT('<binary ', {size}, ' bytes>') END")
        elif engine == "mariadb":
            exprs.append(f"SUBSTRING(CAST({n} AS CHAR), 1, {CELL_MAX + 1})")
        else:
            exprs.append(f"left({n}::text, {CELL_MAX + 1})")
    arr = ("JSON_ARRAY(" if engine == "mariadb" else "json_build_array(") + ", ".join(exprs) + ")"
    src = f"{q(p.table)}" if engine == "mariadb" else f"public.{q(p.table)}"
    where = ""
    if p.filter_column and p.filter_value is not None:
        assert p.filter_column in names
        fc = f"CAST({q(p.filter_column)} AS CHAR)" if engine == "mariadb" else f"{q(p.filter_column)}::text"
        if p.filter_op == "eq":
            where = f" WHERE {fc} = {lit(p.filter_value)}"
        else:
            esc = p.filter_value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
            where = f" WHERE {fc} LIKE {lit('%' + esc + '%')}" + (" ESCAPE '\\\\'" if engine == "mariadb" else " ESCAPE '\\'")
    order = ""
    if p.order_by:
        assert p.order_by in names
        order = f" ORDER BY {q(p.order_by)} {p.order_dir.upper()}"
    select = f"SELECT {arr} FROM {src}{where}{order} LIMIT {int(p.limit) + 1} OFFSET {int(p.offset)}"
    if engine == "mariadb":
        return ["SET SESSION TRANSACTION READ ONLY", "START TRANSACTION READ ONLY", f"SET STATEMENT max_statement_time=5 FOR {select}", "COMMIT"]
    return ["BEGIN READ ONLY", "SET LOCAL statement_timeout = '5s'", select, "COMMIT"]


@operation("db.browse_rows", Rows, timeout=45)
def browse_rows(p: Rows, ctx: OpContext) -> dict:
    nc = _not_configured(ctx, p.engine)
    if nc:
        return nc
    cols, _idx, err = _describe(ctx, p.engine, p.db_name, p.table)
    if cols is None:
        return {"ok": False, "status": "not_found" if err == "unknown table" else "query_failed", "message": err}
    names = [c["name"] for c in cols]
    for col in (p.order_by, p.filter_column):
        if col is not None and col not in names:
            return {"ok": False, "status": "invalid_column", "message": "Unknown column."}
    ok, rows, err = _query(ctx, p.engine, p.db_name, rows_sql(p.engine, p.db_name, p, cols))
    if not ok:
        return {"ok": False, "status": "query_failed", "message": err}
    rows = [r for r in rows if isinstance(r, list)]
    has_more = len(rows) > p.limit
    out = []
    for r in rows[: p.limit]:
        out.append([(v[:CELL_MAX] + "…") if isinstance(v, str) and len(v) > CELL_MAX else v for v in r])
    return {"ok": True, "table": p.table, "columns": names, "rows": out, "has_more": has_more, "offset": p.offset, "limit": p.limit}
