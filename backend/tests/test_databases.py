import re
from datetime import UTC, datetime, timedelta

import pytest
from fastapi import HTTPException
from sqlalchemy import select, text

from app.models.hosting import CustomerDatabase, DatabaseBackupRequest, DatabaseUser, ResourceEvent, ServerRuntime
from app.models.identity import AuditEvent
from app.models.infra import AgentJob
from app.models.tenant import TenantQuota
from app.services.jobs import enqueue_job
from tests.conftest import login
from tests.hosting_helpers import finish_job, last_job, make_server, tenant_client

API = "/api/v1/databases"
MANAGED_DB = re.compile(r"^[a-z][a-z0-9]{1,13}_[a-f0-9]{6}$")


def code(r):
    return r.json()["error"]["code"]


def create(a, db, engine="mariadb", name="shop", finish=True):
    r = a.post(API, json={"engine": engine, "name": name})
    assert r.status_code == 201, r.text
    d = r.json()
    if finish and d["status"] == "provisioning":
        finish_job(db, last_job(db, "db.provision").id, result={"ok": True, "status": "provisioned"})
        d2 = a.get(f"{API}/{d['id']}").json()
        d2["credentials"] = d["credentials"]
        return d2
    return d


def backup_ok(a, db, did, verified=True, age_days=0):
    r = a.post(f"{API}/{did}/backups")
    assert r.status_code == 201, r.text
    j = last_job(db, "db.dump")
    cs = "ab" * 32
    finish_job(db, j.id, result={"ok": True, "status": "dumped", "checksum": cs, "size_bytes": 2048, "verified": verified})
    out = a.get(f"{API}/{did}/requests").json()
    assert out[0]["state"] == "succeeded", out
    if age_days:
        db.execute(text("update database_backup_requests set finished_at = :t"), {"t": datetime.now(UTC) - timedelta(days=age_days)})
        db.commit()
    return out[0]


def test_provision_shows_password_once_and_never_stores_it(make_user, make_client, db):
    make_server(db)
    a, u, t = tenant_client(make_user, make_client, "a@x.com")
    r = a.post(API, json={"engine": "mariadb", "name": "shop"})
    assert r.status_code == 201
    d = r.json()
    pw = d["credentials"]["password"]
    assert len(pw) >= 24 and d["credentials"]["shown_once"] is True and d["credentials"]["host"] == "localhost" and d["credentials"]["port"] == 3306
    assert MANAGED_DB.match(d["name"]) and d["credentials"]["username"] == f"{d['name']}_app" and d["status"] == "provisioning"
    j = last_job(db, "db.provision")
    assert j.params["password"] == pw and j.params["db_name"] == d["name"] and j.params["privilege"] == "readwrite" and j.tenant_id == t.id
    finish_job(db, j.id, result={"ok": True, "status": "provisioned", "database": d["name"]})
    got = a.get(f"{API}/{d['id']}")
    assert got.json()["status"] == "ready" and pw not in got.text and "credentials" not in got.json()
    assert got.json()["users"][0]["status"] == "active"
    db.expire_all()
    assert db.get(AgentJob, j.id).params["password"] == "[REDACTED]"
    # the plaintext password appears nowhere in the control-plane database
    for table in ("agent_jobs", "customer_databases", "database_users", "resource_events", "audit_events", "tenant_activity", "database_backup_requests"):
        dump = db.execute(text(f"select t::text from {table} t")).scalars().all()
        assert not any(pw in row for row in dump), table
    assert a.get(f"{API}/{d['id']}/connection").status_code == 200
    # listing, history
    assert a.get(API).json()["items"][0]["name"] == d["name"]
    acts = [e["action"] for e in a.get(f"{API}/{d['id']}/history").json()]
    assert "database.created" in acts and "database.ready" in acts


def test_pending_server_then_retry_gives_credentials(make_user, make_client, db):
    a, _, _ = tenant_client(make_user, make_client, "a@x.com")
    d = a.post(API, json={"engine": "postgresql", "name": "app"}).json()
    assert d["status"] == "pending_server" and d["credentials"] is None and "No server" in d["message"]
    assert db.query(AgentJob).count() == 0
    r = a.post(f"{API}/{d['id']}/retry")
    assert r.json()["status"] == "pending_server"
    make_server(db)
    r = a.post(f"{API}/{d['id']}/retry").json()
    assert r["status"] == "provisioning" and r["credentials"]["port"] == 5432 and last_job(db, "db.provision").params["engine"] == "postgresql"
    assert code(a.post(f"{API}/{d['id']}/retry")) == "invalid_state"


def test_names_and_job_schema_validation(make_user, make_client, db):
    a, _, _ = tenant_client(make_user, make_client, "a@x.com")
    for bad in ["Shop", "1shop", "s", "shop_db", "shop-db", "shop;drop", "a" * 15, "shop db", "shop`", ""]:
        assert a.post(API, json={"engine": "mariadb", "name": bad}).status_code == 422, bad
    assert a.post(API, json={"engine": "oracle", "name": "shop"}).status_code == 422
    srv = make_server(db)
    pw = "A" * 30
    good = {"engine": "mariadb", "db_name": "shop_a1b2c3", "username": "shop_a1b2c3_app", "privilege": "readwrite", "password": pw}
    assert enqueue_job(db, srv.id, "db.provision", good)
    for patch in [{"db_name": "root"}, {"db_name": "shop_a1b2c3; DROP DATABASE x"}, {"username": "root"}, {"username": "postgres"}, {"db_name": "mysql"}, {"password": "short"}, {"password": "x'; DROP USER 'root'@'%' --" + "a" * 24},
                  {"privilege": "all"}, {"engine": "sqlite"}, {"extra": 1}, {"db_name": "Shop_a1b2c3"}, {"db_name": "shop_a1b2c3\n"}]:
        with pytest.raises(HTTPException) as e:
            enqueue_job(db, srv.id, "db.provision", {**good, **patch})
        assert e.value.status_code == 422, patch
    with pytest.raises(HTTPException):
        enqueue_job(db, srv.id, "db.drop", {"engine": "mariadb", "db_name": "shop_a1b2c3", "confirm_name": "mysql", "users": ["root"]})


def test_engine_availability(make_user, make_client, admin, client, db):
    srv = make_server(db)
    a, _, _ = tenant_client(make_user, make_client, "a@x.com")
    assert a.get(f"{API}/engines").json()["state"] == "not_reported"
    assert a.post(f"{API}/engines/refresh").status_code == 403
    assert client.post(f"{API}/engines/refresh").status_code == 200
    finish_job(db, last_job(db, "db.detect_engines").id, result={"ok": True, "engines": {"mariadb": "10.11", "postgresql": None}})
    e = a.get(f"{API}/engines").json()
    assert e["state"] == "ok" and e["available"] == ["mariadb"]
    r = a.post(API, json={"engine": "postgresql", "name": "shop"})
    assert r.status_code == 422 and code(r) == "engine_unavailable"
    assert a.post(API, json={"engine": "mariadb", "name": "shop"}).status_code == 201
    assert db.scalar(select(ServerRuntime.version).where(ServerRuntime.runtime == "mariadb")) == "10.11"


def test_provision_failure_triggers_cleanup(make_user, make_client, db):
    make_server(db)
    a, _, _ = tenant_client(make_user, make_client, "a@x.com")
    d = a.post(API, json={"engine": "mariadb", "name": "shop"}).json()
    pw = d["credentials"]["password"]
    finish_job(db, last_job(db, "db.provision").id, result={"ok": False, "status": "provision_failed", "message": f"Provisioning failed: ERROR 1044 password={pw}", "cleaned_up": True})
    got = a.get(f"{API}/{d['id']}").json()
    assert got["status"] == "failed" and pw not in str(got)
    cj = last_job(db, "db.drop")
    assert cj is not None and cj.params["db_name"] == d["name"] and cj.params["confirm_name"] == d["name"] and cj.params["users"] == [f"{d['name']}_app"]
    finish_job(db, cj.id, result={"ok": True, "status": "dropped"})
    assert "cleaned up" in a.get(f"{API}/{d['id']}").json()["message"]
    assert all(u["status"] == "failed" for u in a.get(f"{API}/{d['id']}").json()["users"])
    # a retry is possible and creates fresh credentials
    r = a.post(f"{API}/{d['id']}/retry").json()
    assert r["status"] == "provisioning" and r["credentials"]["password"] != pw


def test_not_configured_database_server(make_user, make_client, db):
    make_server(db)
    a, _, _ = tenant_client(make_user, make_client, "a@x.com")
    d = a.post(API, json={"engine": "mariadb", "name": "shop"}).json()
    finish_job(db, last_job(db, "db.provision").id, result={"ok": False, "status": "not_configured", "message": "The mysql client is not installed on this server; nothing was changed."})
    got = a.get(f"{API}/{d['id']}").json()
    assert got["status"] == "failed" and "not installed" in got["message"]
    assert last_job(db, "db.drop") is None  # nothing was created, nothing to clean


def test_credential_rotation_shown_once(make_user, make_client, db):
    make_server(db)
    a, _, _ = tenant_client(make_user, make_client, "a@x.com")
    d = create(a, db)
    old = d["credentials"]["password"]
    uid = d["users"][0]["id"]
    r = a.post(f"{API}/{d['id']}/users/{uid}/rotate")
    assert r.status_code == 200
    new = r.json()["credentials"]["password"]
    assert new != old and len(new) >= 24 and r.json()["credentials"]["shown_once"]
    j = last_job(db, "db.rotate_password")
    assert j.params["password"] == new and j.params["username"] == d["credentials"]["username"]
    assert a.get(f"{API}/{d['id']}").json()["users"][0]["status"] == "rotating"
    assert code(a.post(f"{API}/{d['id']}/users/{uid}/rotate")) == "invalid_state"
    finish_job(db, j.id, result={"ok": True, "status": "rotated"})
    got = a.get(f"{API}/{d['id']}")
    assert got.json()["users"][0]["status"] == "active" and got.json()["users"][0]["password_rotated_at"] and new not in got.text
    db.expire_all()
    assert db.get(AgentJob, j.id).params["password"] == "[REDACTED]"
    for table in ("agent_jobs", "database_users", "resource_events", "audit_events"):
        assert not any(new in row or old in row for row in db.execute(text(f"select t::text from {table} t")).scalars().all()), table
    assert db.query(AuditEvent).filter_by(action="database.credentials_rotated").count() == 1
    # failed rotation: user stays usable with the OLD password, status resets, can retry
    r = a.post(f"{API}/{d['id']}/users/{uid}/rotate")
    finish_job(db, last_job(db, "db.rotate_password").id, result={"ok": False, "status": "rotate_failed", "message": "boom"})
    assert a.get(f"{API}/{d['id']}").json()["users"][0]["status"] == "active"
    assert [e for e in a.get(f"{API}/{d['id']}/history").json() if e["action"] == "database.rotate_failed"]


def test_connection_info_has_no_secrets(make_user, make_client, db):
    make_server(db)
    a, _, _ = tenant_client(make_user, make_client, "a@x.com")
    d = create(a, db, engine="postgresql")
    c = a.get(f"{API}/{d['id']}/connection").json()
    assert c["remote_access"] is False and c["port"] == 5432 and c["users"][0]["uri_template"].startswith("postgresql://") and "<password>" in c["users"][0]["uri_template"]
    blob = str(c).lower() + str(a.get(f"{API}/{d['id']}").json()).lower()
    assert d["credentials"]["password"].lower() not in blob
    for forbidden in ("admin", "defaults_file", "kvl_admin", "root"):
        assert forbidden not in blob.replace("administrator", "")


def test_additional_users_and_last_writer(make_user, make_client, db):
    make_server(db)
    a, _, _ = tenant_client(make_user, make_client, "a@x.com")
    d = create(a, db)
    r = a.post(f"{API}/{d['id']}/users", json={"suffix": "ro", "privilege": "readonly"})
    assert r.status_code == 201 and r.json()["credentials"]["username"] == f"{d['name']}_ro"
    assert "Read-only" in r.json()["grants"] and last_job(db, "db.create_user").params["privilege"] == "readonly"
    assert code(a.post(f"{API}/{d['id']}/users", json={"suffix": "ro"})) == "user_exists"
    for bad in ("RO", "r;o", "toolongsuffix", "1ro", ""):
        assert a.post(f"{API}/{d['id']}/users", json={"suffix": bad}).status_code == 422
    finish_job(db, last_job(db, "db.create_user").id, result={"ok": True})
    users = a.get(f"{API}/{d['id']}/users").json()
    assert {u["username"]: u["status"] for u in users}[f"{d['name']}_ro"] == "active"
    writer = [u for u in users if u["privilege"] == "readwrite"][0]
    assert code(a.delete(f"{API}/{d['id']}/users/{writer['id']}")) == "last_writer"
    ro = [u for u in users if u["privilege"] == "readonly"][0]
    assert a.delete(f"{API}/{d['id']}/users/{ro['id']}").status_code == 202
    finish_job(db, last_job(db, "db.drop_user").id, result={"ok": True})
    assert len(a.get(f"{API}/{d['id']}/users").json()) == 1


def test_tenant_isolation_and_roles(make_user, make_client, db, admin, client):
    make_server(db)
    a, _, ta = tenant_client(make_user, make_client, "a@x.com")
    b, _, _ = tenant_client(make_user, make_client, "b@x.com")
    d = create(a, db)
    did, uid = d["id"], d["users"][0]["id"]
    checks = [("get", ""), ("get", "/connection"), ("get", "/users"), ("post", f"/users/{uid}/rotate"), ("post", "/users"), ("get", "/requests"), ("post", "/backups"), ("post", "/restores"),
              ("post", "/destroy-token"), ("post", "/destroy"), ("post", "/retry"), ("get", "/history"), ("get", "/usage"), ("post", "/size/refresh"), ("delete", f"/users/{uid}"),
              ("post", f"/requests/{did}/approve"), ("post", f"/requests/{did}/reject")]
    for m, p in checks:
        kw = {} if m in ("get", "delete") else {"json": {"suffix": "zz", "source_backup_id": did, "confirm_name": "x", "token": "x" * 12}}
        r = getattr(b, m)(f"{API}/{did}{p}", **kw)
        assert r.status_code == 404, (m, p, r.status_code)
    assert b.get(API).json()["total"] == 0 and a.get(API).json()["total"] == 1 and client.get(API).json()["total"] == 1
    assert b.post(API, json={"engine": "mariadb", "name": "x1", "tenant_id": str(ta.id)}).status_code == 404
    for role, email in (("support", "s@x.com"), ("reseller", "r@x.com")):
        make_user(email, role)
        c = make_client()
        login(c, email)
        assert c.get(API).status_code == 403 and c.get(f"{API}/{did}/connection").status_code == 403 and c.post(f"{API}/{did}/users/{uid}/rotate").status_code == 403
    assert make_client().get(API).status_code == 401
    # a database of tenant A cannot be reached through tenant B's backup ids either
    db_b = create(b, db, name="other")
    bk = backup_ok(a, db, did)
    assert b.post(f"{API}/{db_b['id']}/restores", json={"source_backup_id": bk["id"]}).status_code == 404


def test_quota_databases(make_user, make_client, db):
    make_server(db)
    a, _, t = tenant_client(make_user, make_client, "a@x.com")
    db.query(TenantQuota).filter_by(tenant_id=t.id, resource="databases").one().limit_value = 1
    db.commit()
    assert a.post(API, json={"engine": "mariadb", "name": "one"}).status_code == 201
    r = a.post(API, json={"engine": "mariadb", "name": "two"})
    assert r.status_code == 409 and code(r) == "quota_exceeded"


def test_backup_request_state_machine(make_user, make_client, db):
    make_server(db)
    a, _, _ = tenant_client(make_user, make_client, "a@x.com")
    d = create(a, db)
    r = a.post(f"{API}/{d['id']}/backups").json()
    assert r["state"] == "running" and re.match(rf"^{d['name']}-\d{{8}}T\d{{6}}Z\.sql$", r["artifact"])
    j = last_job(db, "db.dump")
    assert j.params["artifact"] == r["artifact"]
    assert code(a.post(f"{API}/{d['id']}/requests/{r['id']}/approve", json={})) == "invalid_transition"
    finish_job(db, j.id, result={"ok": True, "status": "dumped", "checksum": "cd" * 32, "size_bytes": 10, "verified": True})
    got = a.get(f"{API}/{d['id']}/requests").json()[0]
    assert got["state"] == "succeeded" and got["verified"] is True and got["checksum"] == "cd" * 32
    # a backup without the verification flag is not "verified"
    a.post(f"{API}/{d['id']}/backups")
    finish_job(db, last_job(db, "db.dump").id, result={"ok": True, "status": "dumped", "checksum": "ef" * 32, "size_bytes": 10, "verified": False})
    assert a.get(f"{API}/{d['id']}/requests").json()[0]["verified"] is False
    # failure path records a redacted error
    a.post(f"{API}/{d['id']}/backups")
    finish_job(db, last_job(db, "db.dump").id, result={"ok": False, "status": "dump_failed", "message": "access denied password=hunter2hunter2"})
    f = a.get(f"{API}/{d['id']}/requests").json()[0]
    assert f["state"] == "failed" and "hunter2" not in f["error"]
    assert code(a.post(f"{API}/{d['id']}/requests/{f['id']}/reject")) == "invalid_transition"


def test_restore_workflow_and_integrity(make_user, make_client, db):
    make_server(db)
    a, _, _ = tenant_client(make_user, make_client, "a@x.com")
    d = create(a, db)
    did = d["id"]
    unverified = a.post(f"{API}/{did}/backups").json()
    finish_job(db, last_job(db, "db.dump").id, result={"ok": True, "status": "dumped", "checksum": "ef" * 32, "size_bytes": 10, "verified": False})
    r = a.post(f"{API}/{did}/restores", json={"source_backup_id": unverified["id"]})
    assert r.status_code == 409 and code(r) == "backup_not_verified"
    bk = backup_ok(a, db, did)
    rr = a.post(f"{API}/{did}/restores", json={"source_backup_id": bk["id"]})
    assert rr.status_code == 201 and rr.json()["state"] == "requested" and rr.json()["checksum"] == bk["checksum"]
    assert db.query(AgentJob).filter_by(operation="db.restore").count() == 0  # nothing runs before approval
    rid = rr.json()["id"]
    assert code(a.post(f"{API}/{did}/requests/{rid}/approve", json={})) == "confirmation_required"
    assert code(a.post(f"{API}/{did}/requests/{rid}/approve", json={"confirm_name": "wrong_000000"})) == "confirmation_required"
    ok = a.post(f"{API}/{did}/requests/{rid}/approve", json={"confirm_name": d["name"]})
    assert ok.status_code == 200 and ok.json()["state"] == "running"
    j = last_job(db, "db.restore")
    assert j.params["checksum"] == bk["checksum"] and j.params["artifact"] == bk["artifact"] and j.params["db_name"] == d["name"]
    finish_job(db, j.id, result={"ok": False, "status": "integrity_failed", "message": "Backup checksum mismatch; the database was NOT modified."})
    st = [x for x in a.get(f"{API}/{did}/requests").json() if x["id"] == rid][0]
    assert st["state"] == "failed" and "NOT modified" in st["error"]
    assert code(a.post(f"{API}/{did}/requests/{rid}/approve", json={"confirm_name": d["name"]})) == "invalid_transition"
    # second attempt succeeds
    rr = a.post(f"{API}/{did}/restores", json={"source_backup_id": bk["id"]}).json()
    a.post(f"{API}/{did}/requests/{rr['id']}/approve", json={"confirm_name": d["name"]})
    finish_job(db, last_job(db, "db.restore").id, result={"ok": True, "status": "restored", "verified": True})
    assert [x for x in a.get(f"{API}/{did}/requests").json() if x["id"] == rr["id"]][0]["state"] == "succeeded"
    # rejection path
    rj = a.post(f"{API}/{did}/restores", json={"source_backup_id": bk["id"]}).json()
    assert a.post(f"{API}/{did}/requests/{rj['id']}/reject").json()["state"] == "rejected"
    assert code(a.post(f"{API}/{did}/requests/{rj['id']}/approve", json={"confirm_name": d["name"]})) == "invalid_transition"


def test_destructive_safeguards(make_user, make_client, db):
    make_server(db)
    a, _, t = tenant_client(make_user, make_client, "a@x.com")
    d = create(a, db)
    did, name = d["id"], d["name"]
    qused = lambda: (db.expire_all(), db.scalar(select(TenantQuota.used_value).where(TenantQuota.tenant_id == t.id, TenantQuota.resource == "databases")))[1]  # noqa: E731
    assert qused() == 1
    # no token
    assert a.post(f"{API}/{did}/destroy", json={"confirm_name": name, "token": "x" * 12}).status_code == 409
    tok = a.post(f"{API}/{did}/destroy-token").json()
    assert tok["has_verified_backup"] is False and tok["expires_in"] == 600
    # wrong name
    assert code(a.post(f"{API}/{did}/destroy", json={"confirm_name": "nope", "token": tok["token"]})) == "confirmation_required"
    # wrong token
    assert code(a.post(f"{API}/{did}/destroy", json={"confirm_name": name, "token": "z" * 20})) == "token_invalid"
    # right name + token but no verified backup
    r = a.post(f"{API}/{did}/destroy", json={"confirm_name": name, "token": tok["token"]})
    assert r.status_code == 409 and code(r) == "backup_required"
    assert db.query(AgentJob).filter_by(operation="db.drop").count() == 0
    # stale (8 day old) backup does not count
    backup_ok(a, db, did, age_days=8)
    assert code(a.post(f"{API}/{did}/destroy", json={"confirm_name": name, "token": tok["token"]})) == "backup_required"
    # unverified backup does not count either
    a.post(f"{API}/{did}/backups")
    finish_job(db, last_job(db, "db.dump").id, result={"ok": True, "checksum": "11" * 32, "size_bytes": 1, "verified": False})
    assert code(a.post(f"{API}/{did}/destroy", json={"confirm_name": name, "token": tok["token"]})) == "backup_required"
    backup_ok(a, db, did)
    # expired token
    db.execute(text("update customer_databases set destroy_token_expires = now() - interval '1 minute'"))
    db.commit()
    assert code(a.post(f"{API}/{did}/destroy", json={"confirm_name": name, "token": tok["token"]})) == "token_expired"
    tok = a.post(f"{API}/{did}/destroy-token").json()
    assert tok["has_verified_backup"] is True
    ok = a.post(f"{API}/{did}/destroy", json={"confirm_name": name, "token": tok["token"]})
    assert ok.status_code == 202 and ok.json()["status"] == "deleting"
    j = last_job(db, "db.drop")
    assert j.params["confirm_name"] == name and j.params["users"] == [f"{name}_app"]
    # token is single use
    assert a.post(f"{API}/{did}/destroy", json={"confirm_name": name, "token": tok["token"]}).status_code in (409,)
    # failure returns the database to ready (nothing lost) and keeps the quota
    finish_job(db, j.id, result={"ok": False, "status": "drop_failed", "message": "locked"})
    assert a.get(f"{API}/{did}").json()["status"] == "ready" and qused() == 1
    tok = a.post(f"{API}/{did}/destroy-token").json()
    a.post(f"{API}/{did}/destroy", json={"confirm_name": name, "token": tok["token"]})
    finish_job(db, last_job(db, "db.drop").id, result={"ok": True, "status": "dropped"})
    assert a.get(f"{API}/{did}").status_code == 404 and qused() == 0
    assert a.get(API).json()["total"] == 0
    assert db.query(AuditEvent).filter_by(action="database.destroyed").count() == 2


def test_failed_database_can_be_removed_without_backup(make_user, make_client, db):
    make_server(db)
    a, _, _ = tenant_client(make_user, make_client, "a@x.com")
    d = a.post(API, json={"engine": "mariadb", "name": "shop"}).json()
    finish_job(db, last_job(db, "db.provision").id, result={"ok": False, "status": "provision_failed", "message": "x"})
    a.get(f"{API}/{d['id']}")
    tok = a.post(f"{API}/{d['id']}/destroy-token").json()
    r = a.post(f"{API}/{d['id']}/destroy", json={"confirm_name": d["name"], "token": tok["token"]})
    assert r.status_code == 202 and r.json()["status"] == "deleted"


def test_size_refresh_and_usage(make_user, make_client, db):
    make_server(db)
    a, _, _ = tenant_client(make_user, make_client, "a@x.com")
    d = create(a, db)
    assert a.get(f"{API}/{d['id']}/usage").json()["size_bytes"] is None
    assert a.post(f"{API}/{d['id']}/size/refresh").status_code == 202
    finish_job(db, last_job(db, "db.size").id, result={"ok": True, "status": "size", "size_bytes": 5242880})
    got = a.get(f"{API}/{d['id']}").json()
    assert got["size_bytes"] == 5242880 and got["size_checked_at"]
    u = a.get(f"{API}/{d['id']}/usage").json()
    assert {q["resource"] for q in u["quotas"]} >= {"databases"}


def test_logs_and_job_results_redacted_for_customers(make_user, make_client, db):
    """Job results/messages that reach the customer never carry secrets."""
    make_server(db)
    a, _, _ = tenant_client(make_user, make_client, "a@x.com")
    d = a.post(API, json={"engine": "mariadb", "name": "shop"}).json()
    finish_job(db, last_job(db, "db.provision").id, result={"ok": False, "status": "provision_failed", "message": "connect mysql://admin:AdminSecretPw99@127.0.0.1 failed token=abc12345xyz"})
    got = a.get(f"{API}/{d['id']}")
    assert "AdminSecretPw99" not in got.text and "abc12345xyz" not in got.text
