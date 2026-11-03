import pytest
from sqlalchemy import select

from app.core.security import decrypt_secret
from app.models.hosting import AppDeployment, AppEnvVar, Application, ServerRuntime
from app.models.identity import AuditEvent
from app.models.infra import AgentJob
from app.models.tenant import TenantQuota
from app.services.redact import redact_text, redact_value
from tests.conftest import login
from tests.hosting_helpers import finish_job, last_job, make_server, tenant_client

API = "/api/v1/apps"
REPO = "https://github.com/acme/site.git"


def code(r):
    return r.json()["error"]["code"]


def body(**kw):
    return {"name": "My App", "runtime": "nodejs", "runtime_version": "20", "repo_url": REPO, "entrypoint": "server.js", **kw}


def deployed(a, db, **kw):
    """Create + deploy + finish an app; returns the app dict."""
    app = a.post(API, json=body(**kw)).json()
    assert a.post(f"{API}/{app['id']}/deploy", json={}).status_code == 202
    j = last_job(db, "apps.deploy")
    finish_job(db, j.id, result={"ok": True, "status": "deployed", "release_id": j.params["release_id"], "previous_release": None, "commit": "a" * 40})
    return a.get(f"{API}/{app['id']}").json()


def test_redaction_unit():
    assert "hunter2hunter2" not in redact_text("DB_PASSWORD=hunter2hunter2")
    assert "topsecretpw" not in redact_text("mysql://u:topsecretpw@h/db")
    assert redact_value({"password": "x", "n": {"api_key": "y", "ok": "fine"}, "msg": "token=abcdef123456"}) == {"password": "[REDACTED]", "n": {"api_key": "[REDACTED]", "ok": "fine"}, "msg": "token=[REDACTED]"}
    assert redact_text("started in 20ms") == "started in 20ms"


def test_create_validation_and_catalog(make_user, make_client, db):
    a, _, _ = tenant_client(make_user, make_client, "a@x.com")
    assert code(a.post(API, json=body(runtime_version="99"))) == "runtime_unsupported"
    assert a.post(API, json=body(runtime="ruby")).status_code == 422
    assert code(a.post(API, json=body(entrypoint="../../bin/sh"))) == "invalid_entrypoint"
    assert code(a.post(API, json=body(entrypoint="/etc/passwd.js"))) == "invalid_entrypoint"
    assert code(a.post(API, json=body(runtime="python", runtime_version="3.12", entrypoint="os.system('x')"))) == "invalid_entrypoint"
    assert code(a.post(API, json=body(health_path="http://evil/"))) == "invalid_health_path"
    for bad in ["file:///etc/passwd", "/srv/git/repo", "http://github.com/a/b", "https://u:p@github.com/a/b", "https://evil.example/a/b.git", "git@evil.com:a/b.git", "--upload-pack=x"]:
        assert code(a.post(API, json=body(repo_url=bad))) == "invalid_repo_url", bad
    assert code(a.post(API, json=body(branch="a..b"))) == "invalid_branch"
    srv = make_server(db)
    db.add_all([ServerRuntime(server_id=srv.id, runtime="nodejs", version="20"), ServerRuntime(server_id=srv.id, runtime="python", version="3.12")])
    db.commit()
    r = a.post(API, json=body(runtime_version="22"))
    assert r.status_code == 422 and code(r) == "runtime_unavailable"  # supported, but not installed here
    assert a.post(API, json=body(runtime="php", runtime_version="8.3")).status_code == 422
    ok = a.post(API, json=body(runtime="python", runtime_version="3.12", entrypoint="app:app"))
    assert ok.status_code == 201 and ok.json()["port"] and ok.json()["controllable"] is True
    cat = a.get(f"{API}/runtimes").json()
    assert cat["state"] == "ok" and cat["installed"]["nodejs"] == ["20"]


def test_no_server_blocked_then_retry(make_user, make_client, db):
    a, _, _ = tenant_client(make_user, make_client, "a@x.com")
    app = a.post(API, json=body()).json()
    assert app["provision_state"] == "provisioning_blocked" and "No server" in app["provision_message"]
    assert a.get(f"{API}/runtimes").json()["state"] == "not_configured"
    r = a.post(f"{API}/{app['id']}/deploy", json={})
    assert r.status_code == 409 and code(r) == "no_server"
    make_server(db)
    assert a.post(f"{API}/{app['id']}/deploy", json={}).status_code == 202
    assert last_job(db, "apps.deploy").params["repo_url"] == REPO


def test_runtime_refresh_ingests_report(make_user, make_client, admin, client, db):
    srv = make_server(db)
    a, _, _ = tenant_client(make_user, make_client, "a@x.com")
    assert a.post(f"{API}/runtimes/refresh").status_code == 403
    assert client.post(f"{API}/runtimes/refresh").status_code == 200
    assert a.get(f"{API}/runtimes").json()["state"] == "not_reported"
    finish_job(db, last_job(db, "apps.detect_runtimes").id, result={"ok": True, "runtimes": {"php": ["8.3"], "nodejs": ["20", "ev;il"], "python": []}})
    cat = a.get(f"{API}/runtimes").json()
    assert cat["state"] == "ok" and cat["installed"]["php"] == ["8.3"] and cat["installed"]["nodejs"] == ["20"]
    assert a.post(API, json=body(runtime="php", runtime_version="8.3", entrypoint=None)).status_code == 201
    assert code(a.post(API, json=body(runtime_version="22"))) == "runtime_unavailable"


def test_deploy_success_failure_and_rollback(make_user, make_client, db):
    make_server(db)
    a, _, _ = tenant_client(make_user, make_client, "a@x.com")
    app = a.post(API, json=body()).json()
    aid = app["id"]
    assert a.put(f"{API}/{aid}/env", json={"name": "API_TOKEN", "value": "supersecretvalue123"}).status_code == 200
    d1 = a.post(f"{API}/{aid}/deploy", json={}).json()
    assert code(a.post(f"{API}/{aid}/deploy", json={})) == "deploy_in_progress"
    j = last_job(db, "apps.deploy")
    assert j.params["env"] == {"API_TOKEN": "supersecretvalue123"}  # agent needs it until the job finishes
    finish_job(db, j.id, result={"ok": True, "status": "deployed", "release_id": d1["release_id"], "previous_release": None, "commit": "c" * 40})
    got = a.get(f"{API}/{aid}").json()
    assert got["current_release"] == d1["release_id"] and got["status"] == "running" and got["provision_state"] == "deployed"
    db.expire_all()
    assert db.get(AgentJob, j.id).params["env"] == "[REDACTED]"  # secret scrubbed from the queue table
    # second deploy fails the health check: previous release stays live
    d2 = a.post(f"{API}/{aid}/deploy", json={"branch": "release/2"}).json()
    finish_job(db, last_job(db, "apps.deploy").id, result={"ok": False, "status": "health_failed", "message": "Health check failed password=hunter2hunter2", "live_release": d1["release_id"]})
    got = a.get(f"{API}/{aid}").json()
    assert got["current_release"] == d1["release_id"] and "still live" in got["provision_message"] and "hunter2" not in got["provision_message"]
    hist = a.get(f"{API}/{aid}/deployments").json()["items"]
    assert hist[0]["status"] == "health_failed" and "hunter2" not in (hist[0]["error"] or "") and hist[1]["is_live"] is True
    assert code(a.post(f"{API}/{aid}/rollback", json={})) == "no_rollback_target"
    # third deploy succeeds -> previous is the first release
    d3 = a.post(f"{API}/{aid}/deploy", json={}).json()
    finish_job(db, last_job(db, "apps.deploy").id, result={"ok": True, "status": "deployed", "release_id": d3["release_id"], "previous_release": d1["release_id"]})
    got = a.get(f"{API}/{aid}").json()
    assert got["current_release"] == d3["release_id"] and got["previous_release"] == d1["release_id"]
    # rollback must target a known-good release
    assert code(a.post(f"{API}/{aid}/rollback", json={"to_release": d2["release_id"]})) == "release_not_known_good"
    assert a.post(f"{API}/{aid}/rollback", json={}).status_code == 202
    assert last_job(db, "apps.rollback").params["to_release"] == d1["release_id"]
    finish_job(db, last_job(db, "apps.rollback").id, result={"ok": True, "status": "rolled_back", "release_id": d1["release_id"], "previous_release": d3["release_id"]})
    got = a.get(f"{API}/{aid}").json()
    assert got["current_release"] == d1["release_id"] and got["previous_release"] == d3["release_id"]
    assert db.query(AppDeployment).filter_by(kind="rollback", status="succeeded").count() == 1


def test_not_configured_deploy_result(make_user, make_client, db):
    make_server(db)
    a, _, _ = tenant_client(make_user, make_client, "a@x.com")
    app = a.post(API, json=body()).json()
    a.post(f"{API}/{app['id']}/deploy", json={})
    finish_job(db, last_job(db).id, result={"ok": False, "status": "not_configured", "message": "Required software is not installed on this server: pm2."})
    got = a.get(f"{API}/{app['id']}").json()
    assert got["provision_state"] == "not_configured" and "pm2" in got["provision_message"]


def test_unauthorized_process_control(make_user, make_client, db, admin, client):
    make_server(db)
    a, _, ta = tenant_client(make_user, make_client, "a@x.com")
    b, _, _ = tenant_client(make_user, make_client, "b@x.com")
    app = deployed(a, db)
    aid = app["id"]
    # other tenant and every unauth/low-privilege role
    assert b.post(f"{API}/{aid}/process", json={"action": "stop"}).status_code == 404
    assert make_client().post(f"{API}/{aid}/process", json={"action": "stop"}).status_code == 401
    for role, email in (("support", "s@x.com"), ("reseller", "r@x.com")):
        make_user(email, role)
        c = make_client()
        login(c, email)
        assert c.post(f"{API}/{aid}/process", json={"action": "stop"}).status_code == 403
        assert c.post(f"{API}/{aid}/env/X/reveal").status_code == 403
        assert c.post(f"{API}/{aid}/deploy", json={}).status_code == 403
    assert a.post(f"{API}/{aid}/process", json={"action": "format-disk"}).status_code == 422
    r = a.post(f"{API}/{aid}/process", json={"action": "stop"})
    assert r.status_code == 202
    j = last_job(db, "apps.process")
    assert j.params["action"] == "stop" and j.tenant_id == ta.id
    finish_job(db, j.id, result={"ok": True, "status": "stop", "state": "stopped"})
    assert a.get(f"{API}/{aid}").json()["status"] == "stopped"
    assert client.post(f"{API}/{aid}/process", json={"action": "restart"}).status_code == 202  # platform staff can too


def test_php_app_has_no_process(make_user, make_client, db):
    make_server(db)
    a, _, _ = tenant_client(make_user, make_client, "a@x.com")
    app = a.post(API, json=body(runtime="php", runtime_version="8.3", entrypoint=None)).json()
    assert app["port"] is None and app["controllable"] is False
    a.post(f"{API}/{app['id']}/deploy", json={})
    j = last_job(db, "apps.deploy")
    assert "port" not in j.params or j.params["port"] is None
    finish_job(db, j.id, result={"ok": True, "status": "deployed", "release_id": j.params["release_id"]})
    r = a.post(f"{API}/{app['id']}/process", json={"action": "restart"})
    assert r.status_code == 409 and code(r) == "not_a_process_app"


def test_wordpress_template_defaults(make_user, make_client, db):
    a, _, _ = tenant_client(make_user, make_client, "a@x.com")
    app = a.post(API, json={"name": "WP", "runtime": "wordpress", "runtime_version": "8.3"}).json()
    assert app["repo_url"] == "https://github.com/WordPress/WordPress.git" and app["runtime"] == "wordpress"


def test_env_encrypted_masked_reveal_audited(make_user, make_client, db):
    a, u, t = tenant_client(make_user, make_client, "a@x.com")
    app = a.post(API, json=body()).json()
    aid = app["id"]
    assert a.put(f"{API}/{aid}/env", json={"name": "STRIPE_KEY", "value": "sk_live_topsecret_value"}).status_code == 200
    row = db.scalar(select(AppEnvVar).where(AppEnvVar.name == "STRIPE_KEY"))
    assert "topsecret" not in row.value_encrypted and decrypt_secret(row.value_encrypted) == "sk_live_topsecret_value"
    lst = a.get(f"{API}/{aid}/env").json()
    assert lst[0]["name"] == "STRIPE_KEY" and "topsecret" not in str(lst) and lst[0]["masked"] is True
    assert "topsecret" not in a.get(f"{API}/{aid}").text
    before = db.query(AuditEvent).filter_by(action="app.env_revealed").count()
    r = a.post(f"{API}/{aid}/env/STRIPE_KEY/reveal")
    assert r.status_code == 200 and r.json()["value"] == "sk_live_topsecret_value"
    db.expire_all()
    ev = db.query(AuditEvent).filter_by(action="app.env_revealed").all()
    assert len(ev) == before + 1 and ev[0].actor_user_id == u.id and "topsecret" not in str(ev[0].detail)
    assert "topsecret" not in str(db.query(AuditEvent).filter(AuditEvent.action == "app.env_set").all()[0].detail)
    # reserved / dangerous names and values
    for name in ("PATH", "LD_PRELOAD", "NODE_OPTIONS", "KVL_SECRET", "GIT_SSH_COMMAND", "PYTHONPATH"):
        assert code(a.put(f"{API}/{aid}/env", json={"name": name, "value": "x"})) == "reserved_env_name", name
    assert a.put(f"{API}/{aid}/env", json={"name": "lower", "value": "x"}).status_code == 422
    assert code(a.put(f"{API}/{aid}/env", json={"name": "OK", "value": "a\nb"})) == "invalid_env_value"
    assert a.delete(f"{API}/{aid}/env/STRIPE_KEY").status_code == 204
    assert a.post(f"{API}/{aid}/env/STRIPE_KEY/reveal").status_code == 404


def test_env_isolation(make_user, make_client):
    a, _, _ = tenant_client(make_user, make_client, "a@x.com")
    b, _, _ = tenant_client(make_user, make_client, "b@x.com")
    aid = a.post(API, json=body()).json()["id"]
    a.put(f"{API}/{aid}/env", json={"name": "SECRET_A", "value": "valuevalue1"})
    for m, p, kw in [("get", "/env", {}), ("put", "/env", {"json": {"name": "X", "value": "y"}}), ("post", "/env/SECRET_A/reveal", {}), ("delete", "/env/SECRET_A", {}),
                     ("get", "", {}), ("patch", "", {"json": {"name": "zz"}}), ("get", "/deployments", {}), ("post", "/deploy", {"json": {}}), ("post", "/rollback", {"json": {}}),
                     ("get", "/usage", {}), ("post", "/logs", {"json": {}}), ("get", "/activity", {}), ("delete", "?confirm=x", {})]:
        r = getattr(b, m)(f"{API}/{aid}{p}", **kw)
        assert r.status_code == 404, (m, p, r.status_code)
    assert b.get(API).json()["total"] == 0


def test_logs_paginated_redacted_and_scoped(make_user, make_client, db):
    make_server(db)
    a, _, _ = tenant_client(make_user, make_client, "a@x.com")
    b, _, _ = tenant_client(make_user, make_client, "b@x.com")
    app = deployed(a, db)
    aid = app["id"]
    a.put(f"{API}/{aid}/env", json={"name": "MY_KEY", "value": "plainvalue987"})
    r = a.post(f"{API}/{aid}/logs", json={"stream": "out", "page": 2, "page_size": 50})
    assert r.status_code == 202
    j = last_job(db, "apps.logs")
    assert j.params["page"] == 2 and j.params["page_size"] == 50 and j.params["app"].startswith("app-")
    jid = r.json()["job_id"]
    assert a.get(f"{API}/{aid}/logs/{jid}").json()["status"] == "queued"
    finish_job(db, j.id, result={"ok": True, "lines": ["boot ok", "connect password=hunter2hunter2", "using plainvalue987 here", "Authorization: Bearer abcdefghijklmnop123"], "page": 2, "page_size": 50, "has_more": True, "total_lines": 300})
    out = a.get(f"{API}/{aid}/logs/{jid}").json()
    txt = " ".join(out["lines"])
    assert out["has_more"] and "hunter2" not in txt and "plainvalue987" not in txt and "abcdefghijklmnop123" not in txt and "boot ok" in txt
    assert code(b.get(f"{API}/{aid}/logs/{jid}")) == "not_found"
    app_b = deployed(b, db, name="B App")
    assert code(b.get(f"{API}/{app_b['id']}/logs/{jid}")) == "not_found"  # job of another app/tenant
    assert a.post(f"{API}/{aid}/logs", json={"page_size": 5000}).status_code == 422


def test_job_view_redacts_results(make_user, make_client, db):
    make_server(db)
    a, _, _ = tenant_client(make_user, make_client, "a@x.com")
    app = a.post(API, json=body()).json()
    a.post(f"{API}/{app['id']}/deploy", json={})
    finish_job(db, last_job(db).id, result={"ok": False, "status": "build_failed", "message": "x", "password": "p4ssw0rdvalue", "detail": "token=zzzzzzzz1234"})
    got = a.get(f"{API}/{app['id']}").json()
    assert "p4ssw0rdvalue" not in str(got) and "zzzzzzzz1234" not in str(got)


def test_quota_apps_and_release(make_user, make_client, db):
    a, _, t = tenant_client(make_user, make_client, "a@x.com")
    q = db.query(TenantQuota).filter_by(tenant_id=t.id, resource="apps").one()
    q.limit_value = 1
    db.commit()
    first = a.post(API, json=body())
    assert first.status_code == 201
    r = a.post(API, json=body(name="Second"))
    assert r.status_code == 409 and code(r) == "quota_exceeded"
    assert code(a.delete(f"{API}/{first.json()['id']}?confirm=wrong")) == "confirmation_required"
    assert a.delete(f"{API}/{first.json()['id']}?confirm={first.json()['slug']}").status_code == 200
    assert a.post(API, json=body(name="Second")).status_code == 201
    us = a.get(f"{API}/{first.json()['id']}/usage")
    assert us.status_code == 200 and {x["resource"] for x in us.json()["quotas"]} >= {"apps"}


def test_usage_refresh(make_user, make_client, db):
    make_server(db)
    a, _, _ = tenant_client(make_user, make_client, "a@x.com")
    app = deployed(a, db)
    assert a.get(f"{API}/{app['id']}/usage").json()["usage"] is None
    assert a.post(f"{API}/{app['id']}/usage/refresh").status_code == 202
    finish_job(db, last_job(db, "apps.usage").id, result={"ok": True, "disk_mb": 12.5, "memory_mb": 80.1, "cpu_percent": 1.5, "restarts": 0, "uptime_s": 60, "releases": 2})
    u = a.get(f"{API}/{app['id']}/usage").json()["usage"]
    assert u["memory_mb"] == 80.1 and u["releases"] == 2


def test_port_allocation_unique(make_user, make_client, db):
    a, _, _ = tenant_client(make_user, make_client, "a@x.com")
    ports = {a.post(API, json=body(name=f"App {i}")).json()["port"] for i in range(3)}
    assert len(ports) == 3 and all(20000 <= p < 30000 for p in ports)


def test_link_website_sets_proxy(make_user, make_client, db):
    a, _, _ = tenant_client(make_user, make_client, "a@x.com")
    b, _, _ = tenant_client(make_user, make_client, "b@x.com")
    w = a.post("/api/v1/websites", json={"name": "Site"}).json()
    wb = b.post("/api/v1/websites", json={"name": "Other"}).json()
    app = a.post(API, json=body(website_id=w["id"])).json()
    assert app["website_id"] == w["id"]
    assert a.get(f"/api/v1/websites/{w['id']}").json()["runtime_type"] == "proxy"
    assert a.post(API, json=body(name="ZZ", website_id=wb["id"])).status_code == 404
