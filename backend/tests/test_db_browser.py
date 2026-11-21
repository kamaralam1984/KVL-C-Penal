"""Database browser API: read permission, tenant isolation, ready-only, validated params, job ownership."""
from app.models.hosting import CustomerDatabase
from app.models.identity import AuditEvent
from app.models.infra import AgentJob
from tests.hosting_helpers import finish_job, make_server, tenant_client


def mkdb(db, tenant, server, name="shop_a1b2c3", status="ready", engine="mariadb"):
    d = CustomerDatabase(tenant_id=tenant.id, server_id=server.id if server else None, engine=engine, label="shop", db_name=name, status=status)
    db.add(d)
    db.commit()
    return d


def B(d):
    return f"/api/v1/databases/{d.id}/browse"


def test_browse_flow(make_user, make_client, db):
    s = make_server(db)
    a, _, ta = tenant_client(make_user, make_client, "a@x.com")
    d = mkdb(db, ta, s)
    r = a.post(f"{B(d)}/tables")
    assert r.status_code == 202
    j = db.get(AgentJob, r.json()["job_id"])
    assert j.operation == "db.browse_tables" and j.params == {"engine": "mariadb", "db_name": "shop_a1b2c3"}
    assert a.get(f"{B(d)}/jobs/{j.id}").json() == {"status": "queued"}
    finish_job(db, j.id, result={"ok": True, "tables": [{"name": "wp_users", "rows": 3, "bytes": 1, "type": "table"}], "internal": "x"})
    out = a.get(f"{B(d)}/jobs/{j.id}").json()
    assert out["status"] == "succeeded" and out["tables"][0]["name"] == "wp_users" and "internal" not in out
    r = a.post(f"{B(d)}/rows", json={"table": "wp_users", "limit": 10, "order_by": "ID", "filter_column": "user_login", "filter_value": "admin"})
    assert r.status_code == 202
    j = db.get(AgentJob, r.json()["job_id"])
    assert j.params["table"] == "wp_users" and j.params["filter_value"] == "admin"
    assert db.query(AuditEvent).filter_by(action="db.browse").count() == 1
    finish_job(db, j.id, result={"ok": True, "columns": ["a"], "rows": [["token=abcdef1234567890"]], "has_more": False})
    assert "abcdef1234567890" not in a.get(f"{B(d)}/jobs/{j.id}").text


def test_validation(make_user, make_client, db):
    s = make_server(db)
    a, _, ta = tenant_client(make_user, make_client, "a@x.com")
    d = mkdb(db, ta, s)
    for bad in ({"table": "a`b"}, {"table": "t", "limit": 500}, {"table": "t", "order_by": 'x"y'}, {"table": "t", "filter_column": "c"}, {"table": "t", "filter_op": "raw"}, {"table": "t", "extra_sql": "DROP"}):
        r = a.post(f"{B(d)}/rows", json=bad)
        assert r.status_code == 422, bad


def test_not_ready_and_isolation(make_user, make_client, db):
    s = make_server(db)
    a, _, ta = tenant_client(make_user, make_client, "a@x.com")
    b, _, tb = tenant_client(make_user, make_client, "b@x.com")
    pending = mkdb(db, ta, s, name="pend_a1b2c3", status="provisioning")
    assert a.post(f"{B(pending)}/tables").status_code == 409
    d = mkdb(db, ta, s)
    jid = a.post(f"{B(d)}/tables").json()["job_id"]
    assert b.post(f"{B(d)}/tables").status_code == 404
    assert b.get(f"{B(d)}/jobs/{jid}").status_code == 404
    # a job for a different database cannot be read through this one
    d2 = mkdb(db, ta, s, name="blog_a1b2c3")
    assert a.get(f"{B(d2)}/jobs/{jid}").status_code == 404
