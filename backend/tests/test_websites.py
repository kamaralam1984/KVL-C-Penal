import pytest
from sqlalchemy import select

from app.models.hosting import Domain, ServerRuntime, Website
from app.models.identity import AuditEvent
from app.models.infra import AgentJob
from app.models.tenant import TenantQuota
from tests.hosting_helpers import FakeResolver, finish_job, last_job, make_server, tenant_client, use_resolver, verify_domain_ok

API = "/api/v1/websites"


def code(r):
    return r.json()["error"]["code"]


def verified_site(client, monkeypatch, host="blog.example.com", name="Blog", **body):
    w = client.post(API, json={"name": name, "domain": host, **body})
    assert w.status_code == 201, w.text
    w = w.json()
    d = w["domain_details"][0]
    verify_domain_ok(client, d["id"], monkeypatch, host, d["dns_challenge"]["value"].split("=", 1)[1])
    return w


def test_create_without_server_is_pending_and_retryable(make_user, make_client, db, monkeypatch):
    a, _, t = tenant_client(make_user, make_client, "a@x.com")
    w = verified_site(a, monkeypatch)
    assert w["provision_state"] == "pending_server" and "No server" in w["provision_message"]
    assert w["document_root"] == f"/var/www/kvl/{t.slug}/blog/public"
    r = a.post(f"{API}/{w['id']}/provision")
    assert r.status_code == 200 and r.json()["provision_state"] == "pending_server"
    assert db.query(AgentJob).count() == 0  # nothing enqueued with no server
    make_server(db)
    r = a.post(f"{API}/{w['id']}/provision")
    assert r.json()["provision_state"] == "queued"
    j = last_job(db, "nginx.stage_site")
    assert j.params["server_names"] == ["blog.example.com"] and j.params["document_root"].startswith("/var/www/kvl/")


def test_provision_requires_verified_domain(make_user, make_client, db):
    make_server(db)
    a, _, _ = tenant_client(make_user, make_client, "a@x.com")
    w = a.post(API, json={"name": "Blog", "domain": "blog.example.com"}).json()
    assert w["provision_state"] == "awaiting_domain"
    r = a.post(f"{API}/{w['id']}/provision")
    assert r.status_code == 409 and code(r) == "no_verified_domain"
    assert db.query(AgentJob).count() == 0


def test_full_pipeline_stage_activate_reload_rollback(make_user, make_client, admin, client, db, monkeypatch):
    make_server(db)
    a, _, _ = tenant_client(make_user, make_client, "a@x.com")
    w = verified_site(a, monkeypatch)
    assert w["provision_state"] == "awaiting_domain"
    assert a.post(f"{API}/{w['id']}/provision").json()["provision_state"] == "queued"
    # while queued: no concurrent provisioning, and activation not allowed
    assert code(a.post(f"{API}/{w['id']}/provision")) == "provision_in_progress"
    # customer cannot activate/reload/rollback (needs websites:manage)
    for p in ("activate", "reload", "rollback"):
        assert a.post(f"{API}/{w['id']}/{p}", json={}).status_code == 403
    assert code(client.post(f"{API}/{w['id']}/activate")) == "not_staged"
    finish_job(db, last_job(db).id, result={"ok": True, "status": "validated", "config_hash": "a" * 64})
    assert a.get(f"{API}/{w['id']}").json()["provision_state"] == "staged"
    assert client.post(f"{API}/{w['id']}/reload").status_code == 409  # not installed yet: reload only after validated install
    assert client.post(f"{API}/{w['id']}/activate").json()["provision_state"] == "activating"
    assert last_job(db).params == {"site": last_job(db).params["site"], "expected_hash": "a" * 64}
    finish_job(db, last_job(db).id, result={"ok": True, "status": "installed", "backup": "x"})
    assert client.get(f"{API}/{w['id']}").json()["provision_state"] == "installed"
    assert client.post(f"{API}/{w['id']}/reload").json()["provision_state"] == "reloading"
    finish_job(db, last_job(db).id, result={"ok": True, "status": "reloaded"})
    assert client.get(f"{API}/{w['id']}").json()["provision_state"] == "active"
    assert client.post(f"{API}/{w['id']}/rollback", json={}).json()["provision_state"] == "rolling_back"
    assert last_job(db).operation == "nginx.rollback_site"
    finish_job(db, last_job(db).id, result={"ok": True, "status": "rolled_back"})
    assert client.get(f"{API}/{w['id']}").json()["provision_state"] == "rolled_back"
    acts = [x["action"] for x in a.get(f"{API}/{w['id']}/activity").json()]
    assert "website.stage" in acts and "website.created" in acts
    assert db.query(AuditEvent).filter_by(action="website.activate").count() == 1


def test_validation_failure_means_failed_and_no_activation(make_user, make_client, client, admin, db, monkeypatch):
    make_server(db)
    a, _, _ = tenant_client(make_user, make_client, "a@x.com")
    w = verified_site(a, monkeypatch)
    a.post(f"{API}/{w['id']}/provision")
    finish_job(db, last_job(db).id, result={"ok": False, "status": "validation_failed", "message": "nginx -t rejected: [emerg] x", "config_hash": "b" * 64})
    r = a.get(f"{API}/{w['id']}").json()
    assert r["provision_state"] == "failed" and "nginx -t" in r["provision_message"]
    assert code(client.post(f"{API}/{w['id']}/activate")) == "not_staged"
    # retry works
    assert a.post(f"{API}/{w['id']}/provision").json()["provision_state"] == "queued"


def test_nginx_not_installed_is_not_success(make_user, make_client, db, monkeypatch):
    make_server(db)
    a, _, _ = tenant_client(make_user, make_client, "a@x.com")
    w = verified_site(a, monkeypatch)
    a.post(f"{API}/{w['id']}/provision")
    finish_job(db, last_job(db).id, result={"ok": False, "status": "not_configured", "message": "Nginx is not installed on this server; nothing was changed or applied."})
    r = a.get(f"{API}/{w['id']}").json()
    assert r["provision_state"] == "not_configured"


def test_failed_job_transport_error(make_user, make_client, db, monkeypatch):
    make_server(db)
    a, _, _ = tenant_client(make_user, make_client, "a@x.com")
    w = verified_site(a, monkeypatch)
    a.post(f"{API}/{w['id']}/provision")
    finish_job(db, last_job(db).id, ok=False, status="timed_out", result={"message": "agent timeout"})
    assert a.get(f"{API}/{w['id']}").json()["provision_state"] == "failed"


def test_lifecycle_suspend_reactivate_archive_and_quota(make_user, make_client, admin, client, db, monkeypatch):
    make_server(db)
    a, _, t = tenant_client(make_user, make_client, "a@x.com")
    w = verified_site(a, monkeypatch)
    q = lambda: db.scalar(select(TenantQuota.used_value).where(TenantQuota.tenant_id == t.id, TenantQuota.resource == "websites"))  # noqa: E731
    db.expire_all()
    assert q() == 1
    assert a.post(f"{API}/{w['id']}/suspend").status_code == 403  # customer cannot suspend
    assert client.post(f"{API}/{w['id']}/suspend").json()["status"] == "suspended"
    assert code(client.post(f"{API}/{w['id']}/suspend")) == "invalid_transition"
    assert code(a.post(f"{API}/{w['id']}/provision")) == "website_not_active"
    assert client.post(f"{API}/{w['id']}/reactivate").json()["status"] == "active"
    r = client.post(f"{API}/{w['id']}/archive")
    assert r.json()["status"] == "archived"
    db.expire_all()
    assert q() == 0
    assert db.scalar(select(Domain.website_id).where(Domain.hostname == "blog.example.com")) is None
    assert a.get(API).json()["total"] == 0 and a.get(f"{API}?status=archived").json()["total"] == 1
    assert code(a.patch(f"{API}/{w['id']}", json={"name": "zzz"})) == "website_archived"


def test_takedown_enqueues_remove_when_live(make_user, make_client, admin, client, db, monkeypatch):
    make_server(db)
    a, _, _ = tenant_client(make_user, make_client, "a@x.com")
    w = verified_site(a, monkeypatch)
    site = db.get(Website, w["id"])
    site.server_id = db.scalar(select(AgentJob.server_id)) or make_server(db, "s2").id
    site.server_id = None
    from app.models.infra import Server
    site.server_id = db.scalar(select(Server.id))
    site.provision_state = "active"
    db.commit()
    client.post(f"{API}/{w['id']}/suspend")
    assert last_job(db).operation == "nginx.remove_site"
    finish_job(db, last_job(db).id, result={"ok": True, "status": "removed"})
    assert client.get(f"{API}/{w['id']}").json()["provision_state"] == "removed"


def test_quota_enforced(make_user, make_client, db):
    a, _, t = tenant_client(make_user, make_client, "a@x.com")
    qrow = db.query(TenantQuota).filter_by(tenant_id=t.id, resource="websites").one()
    qrow.limit_value = 1
    db.commit()
    assert a.post(API, json={"name": "One"}).status_code == 201
    r = a.post(API, json={"name": "Two"})
    assert r.status_code == 409 and code(r) == "quota_exceeded"


def test_tenant_isolation_and_role_boundaries(make_user, make_client, admin, client, db, monkeypatch):
    a, _, ta = tenant_client(make_user, make_client, "a@x.com")
    b, _, _ = tenant_client(make_user, make_client, "b@x.com")
    w = a.post(API, json={"name": "Mine"}).json()
    wid = w["id"]
    assert b.get(f"{API}/{wid}").status_code == 404
    for m, path in [("get", f"/{wid}/activity"), ("get", f"/{wid}/usage"), ("post", f"/{wid}/provision"), ("patch", f"/{wid}"), ("post", f"/{wid}/activate"), ("post", f"/{wid}/suspend")]:
        r = getattr(b, m)(API + path, **({"json": {}} if m != "get" else {}))
        assert r.status_code in (404, 403), (path, r.status_code)
    assert b.get(API).json()["total"] == 0
    assert client.get(API).json()["total"] == 1
    assert b.post(API, json={"name": "xx", "tenant_id": str(ta.id)}).status_code == 404
    # customer cannot pick server/plan
    assert a.post(API, json={"name": "yy", "plan_code": "enterprise"}).status_code == 403
    # support role: read only
    make_user("s@x.com", "support")
    from tests.conftest import login
    sc = make_client()
    login(sc, "s@x.com")
    assert sc.get(f"{API}/{wid}").status_code == 200
    assert sc.post(API, json={"name": "nn", "tenant_id": str(ta.id)}).status_code == 403
    assert make_client().get(API).status_code == 401


def test_runtime_catalog_checked(make_user, make_client, admin, client, db, monkeypatch):
    srv = make_server(db)
    db.add_all([ServerRuntime(server_id=srv.id, runtime="php", version="8.3"), ServerRuntime(server_id=srv.id, runtime="php", version="8.2")])
    db.commit()
    a, _, _ = tenant_client(make_user, make_client, "a@x.com")
    r = a.post(API, json={"name": "PHP", "runtime_type": "php", "runtime_version": "7.4"})
    assert r.status_code == 422 and code(r) == "runtime_unavailable"
    assert code(a.post(API, json={"name": "PHP", "runtime_type": "php"})) == "runtime_version_required"
    assert a.post(API, json={"name": "PHP", "runtime_type": "php", "runtime_version": "8.3"}).status_code == 201
    assert a.post(API, json={"name": "PHP2", "runtime_type": "cgi"}).status_code == 422


def test_document_root_cannot_be_chosen_or_traverse(make_user, make_client):
    a, _, t = tenant_client(make_user, make_client, "a@x.com")
    for sub in ["../../etc", "/etc", "a/b", "pub lic", "public;x", ".hidden"]:
        assert a.post(API, json={"name": "Site", "public_subdir": sub}).status_code == 422, sub
    assert a.post(API, json={"name": "Site", "document_root": "/etc"}).status_code == 201  # unknown field ignored
    r = a.post(API, json={"name": "Tee", "public_subdir": "html"}).json()
    assert r["document_root"] == f"/var/www/kvl/{t.slug}/tee/html"
    # a hostile site name cannot affect the path
    r = a.post(API, json={"name": "../../etc/passwd; rm -rf"}).json()
    assert r["document_root"].startswith(f"/var/www/kvl/{t.slug}/") and ".." not in r["document_root"] and ";" not in r["document_root"]


def test_stage_params_rejected_by_job_schema(db, make_user):
    """Defense in depth: the enqueue allow-list itself rejects hostile nginx parameters."""
    from app.core.deps import api_error  # noqa: F401
    from fastapi import HTTPException
    from app.services.jobs import enqueue_job

    srv = make_server(db)
    good = {"site": "w-abcdef1234567890", "server_names": ["a.example.com"], "document_root": "/var/www/kvl/t/s/public", "runtime": "static"}
    assert enqueue_job(db, srv.id, "nginx.stage_site", good)
    bad_cases = [
        {**good, "server_names": ["a.com; return 301 http://evil"]},
        {**good, "server_names": ["a.com\nserver {"]},
        {**good, "document_root": "/etc"},
        {**good, "document_root": "/var/www/kvl/../../etc/x/public"},
        {**good, "document_root": "/var/www/kvl/t/s/public\n"},
        {**good, "raw": "location / {}"},
        {**good, "site": "../x"},
        {**good, "runtime": "cgi"},
    ]
    for p in bad_cases:
        with pytest.raises(HTTPException) as e:
            enqueue_job(db, srv.id, "nginx.stage_site", p)
        assert e.value.status_code == 422


def test_usage_summary_honest(make_user, make_client):
    a, _, _ = tenant_client(make_user, make_client, "a@x.com")
    w = a.post(API, json={"name": "Usage", "domain": "u.example.com"}).json()
    u = a.get(f"{API}/{w['id']}/usage").json()
    assert u["domains"] == 1 and u["disk_mb"] is None and "not_reported" in u["disk_note"]
    assert {x["resource"] for x in u["quotas"]} >= {"websites", "domains"}
