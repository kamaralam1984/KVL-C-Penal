"""Website site tools: PHP settings, redirects, protected dirs, IP deny, hotlink. Hashes only, re-stage on save."""
import pytest

from app.models.identity import AuditEvent
from app.services import site_options as so
from tests.hosting_helpers import finish_job, last_job, make_server, tenant_client
from tests.test_websites import API, code, verified_site

OPTS = {
    "php": {"memory_limit_mb": 256, "upload_max_mb": 100},
    "redirects": [{"source": "/old", "target": "/new", "code": 301}],
    "protected": [{"path": "/admin/", "users": [{"username": "boss", "password": "Sup3r-secret-pw"}]}],
    "deny_ips": ["203.0.113.7", "198.51.100.0/24"],
    "hotlink": {"enabled": True, "allowed_hosts": ["*.google.com"]},
}


@pytest.fixture
def php_site(make_user, make_client, db, monkeypatch):
    make_server(db)
    a, _, _ = tenant_client(make_user, make_client, "a@x.com")
    w = verified_site(a, monkeypatch, runtime_type="php", runtime_version="8.3")
    assert a.post(f"{API}/{w['id']}/provision").status_code == 200
    finish_job(db, last_job(db).id, result={"ok": True, "status": "validated", "config_hash": "a" * 64})
    assert a.get(f"{API}/{w['id']}").json()["provision_state"] == "staged"
    return a, w


def test_ssha_roundtrip():
    h = so.ssha("hello-world")
    assert h.startswith("{SSHA}") and so.ssha_verify("hello-world", h) and not so.ssha_verify("nope", h)
    assert so.ssha("x" * 8) != so.ssha("x" * 8)  # salted


def test_save_hashes_passwords_restages_and_never_returns_hashes(php_site, db):
    a, w = php_site
    r = a.put(f"{API}/{w['id']}/options", json=OPTS)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["restaged"] and body["provision_state"] == "queued"
    assert "SSHA" not in r.text and "Sup3r" not in r.text
    assert body["options"]["protected"] == [{"path": "/admin/", "users": [{"username": "boss"}]}]
    job = last_job(db, "nginx.stage_site")
    o = job.params["options"]
    assert o["php"]["memory_limit_mb"] == 256 and o["redirects"][0]["source"] == "/old"
    h = o["protected"][0]["users"][0]["hash"]
    assert h.startswith("{SSHA}") and so.ssha_verify("Sup3r-secret-pw", h)
    assert "Sup3r" not in str(job.params)
    ev = db.query(AuditEvent).filter_by(action="website.options_changed").one()
    assert "SSHA" not in str(ev.detail) and ev.detail["after"]["protected"] == ["/admin/"]
    # the job params validate against the agent-mirrored op schema
    from app.ops.nginx import StageSite

    StageSite.model_validate(job.params)


def test_keep_existing_password_and_require_for_new_users(php_site, db):
    a, w = php_site
    assert a.put(f"{API}/{w['id']}/options", json=OPTS).status_code == 200
    finish_job(db, last_job(db).id, result={"ok": True, "status": "validated", "config_hash": "b" * 64})
    old = last_job(db, "nginx.stage_site").params["options"]["protected"][0]["users"][0]["hash"]
    keep = {**OPTS, "protected": [{"path": "/admin/", "users": [{"username": "boss"}]}]}
    assert a.put(f"{API}/{w['id']}/options", json=keep).status_code == 200
    assert last_job(db, "nginx.stage_site").params["options"]["protected"][0]["users"][0]["hash"] == old
    finish_job(db, last_job(db).id, result={"ok": True, "status": "validated", "config_hash": "c" * 64})
    new_user = {**OPTS, "protected": [{"path": "/admin/", "users": [{"username": "boss"}, {"username": "newbie"}]}]}
    r = a.put(f"{API}/{w['id']}/options", json=new_user)
    assert r.status_code == 422 and code(r) == "password_required"


@pytest.mark.parametrize("bad", [
    {"redirects": [{"source": "/a;", "target": "/b"}]},
    {"redirects": [{"source": "/a", "target": "/b\"; return 200 \"x"}]},
    {"protected": [{"path": "/admin/", "users": [{"username": "boss", "password": "short"}]}]},
    {"protected": [{"path": "/admin/", "users": [{"username": "bad:name", "password": "longenough1"}]}]},
    {"deny_ips": ["0.0.0.0/0"]},
    {"deny_ips": ["evil"]},
    {"php": {"memory_limit_mb": 5000}},
    {"raw_nginx": "include /etc/passwd;"},
])
def test_invalid_options_rejected_and_nothing_queued(php_site, db, bad):
    a, w = php_site
    before = db.query(type(last_job(db))).count()
    r = a.put(f"{API}/{w['id']}/options", json=bad)
    assert r.status_code == 422, r.text
    assert db.query(type(last_job(db))).count() == before


def test_php_settings_only_for_php_sites(make_user, make_client, db, monkeypatch):
    make_server(db)
    a, _, _ = tenant_client(make_user, make_client, "a@x.com")
    w = verified_site(a, monkeypatch)  # static
    r = a.put(f"{API}/{w['id']}/options", json={"php": {}})
    assert r.status_code == 422
    r = a.put(f"{API}/{w['id']}/options", json={"redirects": [{"source": "/x", "target": "/y"}]})
    assert r.status_code == 200 and r.json()["restaged"] is False  # never provisioned -> stored only


def test_busy_site_rejected_and_tenant_isolation(php_site, make_user, make_client, db):
    a, w = php_site
    assert a.put(f"{API}/{w['id']}/options", json=OPTS).status_code == 200  # now queued
    r = a.put(f"{API}/{w['id']}/options", json=OPTS)
    assert r.status_code == 409 and code(r) == "provision_in_progress"
    b, _, _ = tenant_client(make_user, make_client, "b@x.com")
    assert b.get(f"{API}/{w['id']}/options").status_code == 404
    assert b.put(f"{API}/{w['id']}/options", json={}).status_code == 404


def test_get_options_defaults(php_site):
    a, w = php_site
    r = a.get(f"{API}/{w['id']}/options").json()
    assert r["options"]["redirects"] == [] and r["options"]["hotlink"]["enabled"] is False and r["php_available"]
