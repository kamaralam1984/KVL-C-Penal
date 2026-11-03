import hashlib
import os
import re

import pytest
from pydantic import ValidationError

from kvl_agent.operations import databases as db
from tests.fakes import FakeCtx

DB = "shop_a1b2c3"
USER = "shop_a1b2c3_app"
PW = "Abcdefghijklmnopqrstuvwx1234_-"
ADMIN = {"mariadb": {"defaults_file": "/etc/kvl/mysql-admin.cnf"}, "postgresql": {"host": "127.0.0.1", "port": 5432, "user": "kvl_admin", "password": "AdminPassWord123456"}}


def ctx_for(tmp_path, **kw):
    c = FakeCtx(tmp_path, **kw)
    c.extra["db_admin"] = ADMIN
    return c


@pytest.mark.parametrize("name", ["", "ab", "Shop_a1b2c3", "1shop_a1b2c3", "shop-a1b2c3", "shop_a1b2c3; DROP", "shop a1b2c3", "root", "postgres", "mysql", "a" * 33, "shop_a1b2c3`", 'shop_a1b2c3"', "shop_a1b2c3\n", "shop_ZZZZZZ", "information_schema", "shop_a1b2c3_app"])
def test_bad_db_names(name):
    with pytest.raises(ValidationError):
        db.Provision(engine="mariadb", db_name=name, username=USER, password=PW)


@pytest.mark.parametrize("user", ["root", "postgres", "admin", "shop_a1b2c3", "shop_a1b2c3_", "x'; DROP USER 'root", "shop_a1b2c3_app;", "SHOP_a1b2c3_app", "shop_a1b2c3_toolongname"])
def test_bad_usernames(user):
    for cls in (db.DropUser, db.RotatePassword):
        kw = {"password": PW} if cls is db.RotatePassword else {}
        with pytest.raises(ValidationError):
            cls(engine="postgresql", username=user, **kw)


@pytest.mark.parametrize("pw", ["short", "a" * 70, PW + "'", PW + ";", PW + " x", PW + "\n", "p" * 23, "pass'word" + "x" * 20, PW + '"', PW + "\\"])
def test_bad_passwords(pw):
    with pytest.raises(ValidationError):
        db.Provision(engine="mariadb", db_name=DB, username=USER, password=pw)


def test_extra_fields_forbidden():
    with pytest.raises(ValidationError):
        db.Provision(engine="mariadb", db_name=DB, username=USER, password=PW, grant_all=True)


def test_escaping_helpers():
    assert db.my_lit("a'b\\c") == "'a''b\\\\c'" and db.pg_lit("a'b\\c") == "'a''b\\c'"
    with pytest.raises(AssertionError):
        db.my_ident("a`b")
    with pytest.raises(AssertionError):
        db.pg_ident('a"b')


def test_mysql_grants_are_least_privilege():
    s = db.mysql_provision(DB, USER, PW, "readwrite")
    joined = "\n".join(s)
    grants = [x for x in s if x.startswith("GRANT")]
    assert len(grants) == 1
    g = grants[0]
    assert "ALL PRIVILEGES" not in joined and "GRANT OPTION" not in joined and "SUPER" not in joined and "FILE" not in joined and "PROCESS" not in joined
    assert "ON `shop\\_a1b2c3`.*" in g and "*.*" not in joined  # underscore escaped: cannot match sibling databases; never global
    assert f"TO '{USER}'@'localhost'" in g
    assert s[0] == f"CREATE DATABASE `{DB}` CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci"
    ro = "\n".join(db.mysql_create_user(DB, "shop_a1b2c3_ro", PW, "readonly"))
    assert "GRANT SELECT, SHOW VIEW ON" in ro and "INSERT" not in ro and "DROP" not in ro


def test_pg_roles_are_not_privileged():
    s = db.pg_provision(DB, USER, PW, "readwrite")
    j = "\n".join(s)
    assert "NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION" in s[0] and "SUPERUSER " not in s[0].replace("NOSUPERUSER", "")
    assert f'CREATE DATABASE "{DB}" OWNER "{USER}"' in j and f'REVOKE ALL ON DATABASE "{DB}" FROM PUBLIC' in j
    assert "WITH GRANT" not in j and "ALL PRIVILEGES ON ALL" not in j
    ro = db.pg_create_user(DB, "shop_a1b2c3_ro", PW, "readonly")
    j = "\n".join(ro)
    assert f'\\connect "{DB}"' in j and "GRANT SELECT ON ALL TABLES" in j and "INSERT" not in j and "CREATEDB" not in j.replace("NOCREATEDB", "")


def test_provision_mariadb_runs_script_not_argv(tmp_path):
    ctx = ctx_for(tmp_path)
    r = db.provision(db.Provision(engine="mariadb", db_name=DB, username=USER, password=PW), ctx)
    assert r == {"ok": True, "status": "provisioned", "database": DB, "username": USER}
    argv = ctx.calls[0]
    assert argv[0] == "mysql" and argv[1] == "--defaults-extra-file=/etc/kvl/mysql-admin.cnf"
    assert PW not in " ".join(argv)  # password is never on the command line
    assert any(PW in s for s in ctx.scripts)  # it is in the script file...
    assert not list((tmp_path / "tmp" / "db").glob("*.sql"))  # ...which is removed afterwards
    assert PW not in str(r)


def test_provision_postgres_password_via_env_and_script(tmp_path):
    ctx = ctx_for(tmp_path)
    r = db.provision(db.Provision(engine="postgresql", db_name=DB, username=USER, password=PW), ctx)
    assert r["ok"]
    argv = ctx.calls[0]
    assert argv[0] == "psql" and "ON_ERROR_STOP=1" in argv and PW not in " ".join(argv) and "AdminPassWord123456" not in " ".join(argv)
    assert ctx.envs[0]["PGPASSWORD"] == "AdminPassWord123456"


def test_provision_failure_cleans_up(tmp_path):
    ctx = ctx_for(tmp_path)
    ctx.fail_nth = {"mysql": {0: f"ERROR 1396 near IDENTIFIED BY '{PW}'"}}
    r = db.provision(db.Provision(engine="mariadb", db_name=DB, username=USER, password=PW), ctx)
    assert r["ok"] is False and r["status"] == "provision_failed" and r["cleaned_up"] is True
    assert PW not in str(r)  # error text is redacted
    cleanup = ctx.scripts[-1]
    assert f"DROP DATABASE IF EXISTS `{DB}`" in cleanup and f"DROP USER IF EXISTS '{USER}'@'localhost'" in cleanup
    assert len(ctx.calls) == 2


def test_pg_provision_failure_cleanup_terminates_and_drops(tmp_path):
    ctx = ctx_for(tmp_path)
    ctx.fail_nth = {"psql": {0: "ERROR: role already exists"}}
    r = db.provision(db.Provision(engine="postgresql", db_name=DB, username=USER, password=PW), ctx)
    assert r["status"] == "provision_failed"
    c = ctx.scripts[-1]
    assert "pg_terminate_backend" in c and f'DROP DATABASE IF EXISTS "{DB}"' in c and f'DROP ROLE IF EXISTS "{USER}"' in c


def test_not_configured(tmp_path):
    ctx = FakeCtx(tmp_path)
    ctx.missing = {"mysql"}
    r = db.provision(db.Provision(engine="mariadb", db_name=DB, username=USER, password=PW), ctx)
    assert r["status"] == "not_configured" and not ctx.calls
    ctx2 = FakeCtx(tmp_path)  # client present but no admin config
    os.environ.pop("KVL_MYSQL_DEFAULTS_FILE", None)
    r = db.provision(db.Provision(engine="mariadb", db_name=DB, username=USER, password=PW), ctx2)
    assert r["status"] == "not_configured" and "not configured" in r["message"] and not ctx2.calls


def test_rotate_and_drop_user(tmp_path):
    ctx = ctx_for(tmp_path)
    r = db.rotate_password(db.RotatePassword(engine="mariadb", username=USER, password=PW), ctx)
    assert r["ok"] and PW not in str(r)
    assert f"ALTER USER '{USER}'@'localhost' IDENTIFIED BY '{PW}'" in ctx.scripts[0] and PW not in " ".join(ctx.calls[0])
    ctx.fail_nth = {"mysql": {1: f"ERROR ... '{PW}'"}}
    r = db.rotate_password(db.RotatePassword(engine="mariadb", username=USER, password=PW), ctx)
    assert r["ok"] is False and PW not in str(r)
    assert db.drop_user(db.DropUser(engine="postgresql", username=USER), ctx)["ok"]


def test_drop_requires_matching_confirmation(tmp_path):
    ctx = ctx_for(tmp_path)
    r = db.drop(db.Drop(engine="mariadb", db_name=DB, confirm_name="other_a1b2c3", users=[USER]), ctx)
    assert r["status"] == "confirmation_mismatch" and not ctx.calls
    r = db.drop(db.Drop(engine="mariadb", db_name=DB, confirm_name=DB, users=[USER]), ctx)
    assert r["ok"] and f"DROP DATABASE IF EXISTS `{DB}`" in ctx.scripts[0]


def test_dry_run_hides_password(tmp_path):
    ctx = ctx_for(tmp_path, dry_run=True)
    r = db.provision(db.Provision(engine="postgresql", db_name=DB, username=USER, password=PW), ctx)
    assert r["status"] == "dry_run" and PW not in str(r) and not ctx.calls


def test_size_parses_output(tmp_path):
    ctx = ctx_for(tmp_path)
    ctx.stdout["mysql"] = "123456\n"
    assert db.size(db.Size(engine="mariadb", db_name=DB), ctx)["size_bytes"] == 123456
    ctx.stdout["psql"] = "999\n"
    assert db.size(db.Size(engine="postgresql", db_name=DB), ctx)["size_bytes"] == 999


def _fake_dump(ctx, tmp_path, content: bytes, marker=True):
    """Make the fake dump binary create its output file."""
    orig = ctx.run

    def run(argv, **kw):
        if argv[0] in ("mysqldump", "pg_dump"):
            out = [a.split("=", 1)[1] for a in argv if a.startswith("--result-file=")] or [argv[argv.index("-f") + 1]]
            with open(out[0], "wb") as f:
                f.write(content + (b"\n-- Dump completed on 2026-01-01\n" if marker else b""))
        return orig(argv, **kw)

    ctx.run = run


ART = f"{DB}-20260101T000000Z.sql"


def test_dump_checksum_and_restore_integrity(tmp_path):
    ctx = ctx_for(tmp_path)
    _fake_dump(ctx, tmp_path, b"CREATE TABLE t(id int);")
    r = db.dump(db.Dump(engine="mariadb", db_name=DB, artifact=ART), ctx)
    assert r["ok"] and r["verified"] is True and len(r["checksum"]) == 64
    path = tmp_path / "backups/db" / ART
    assert hashlib.sha256(path.read_bytes()).hexdigest() == r["checksum"] and oct(path.stat().st_mode & 0o777) == "0o600"
    ok = db.restore(db.Restore(engine="mariadb", db_name=DB, artifact=ART, checksum=r["checksum"]), ctx)
    assert ok["ok"] and ok["status"] == "restored"
    n_calls = len(ctx.calls)
    path.write_bytes(path.read_bytes() + b"-- tampered")
    bad = db.restore(db.Restore(engine="mariadb", db_name=DB, artifact=ART, checksum=r["checksum"]), ctx)
    assert bad["ok"] is False and bad["status"] == "integrity_failed" and len(ctx.calls) == n_calls  # database untouched


def test_dump_unverified_without_completion_marker_and_other_dbs_artifact_rejected(tmp_path):
    ctx = ctx_for(tmp_path)
    _fake_dump(ctx, tmp_path, b"partial", marker=False)
    r = db.dump(db.Dump(engine="postgresql", db_name=DB, artifact=ART), ctx)
    assert r["ok"] and r["verified"] is False
    other = db.dump(db.Dump(engine="mariadb", db_name=DB, artifact="other_ffffff-20260101T000000Z.sql"), ctx)
    assert other["status"] == "rejected"
    r2 = db.restore(db.Restore(engine="mariadb", db_name=DB, artifact="other_ffffff-20260101T000000Z.sql", checksum="0" * 64), ctx)
    assert r2["status"] == "rejected"
    for bad in ("../../etc/passwd", "x.sql", f"{DB}-20260101T000000Z.sql/../x", f"{DB}-1.sql"):
        with pytest.raises(ValidationError):
            db.Dump(engine="mariadb", db_name=DB, artifact=bad)


def test_detect_engines(tmp_path):
    ctx = ctx_for(tmp_path)
    ctx.stdout["mysql:--version"] = "mysql  Ver 15.1 Distrib 10.11.6-MariaDB, for debian-linux-gnu\n"
    ctx.stdout["psql:--version"] = "psql (PostgreSQL) 16.3\n"
    assert db.detect_engines(db.DetectEngines(), ctx)["engines"] == {"mariadb": "10.11", "postgresql": "16"}
    ctx2 = FakeCtx(tmp_path)
    ctx2.missing = {"mysql", "psql"}
    assert db.detect_engines(db.DetectEngines(), ctx2)["engines"] == {"mariadb": None, "postgresql": None}
