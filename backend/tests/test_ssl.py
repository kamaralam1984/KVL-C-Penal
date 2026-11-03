from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select

from app.models.identity import AuditEvent, EmailOutbox
from app.models.infra import AgentJob
from app.models.ssl import Certificate
from app.services import dns as dnsmod
from app.services import ssl as sslsvc
from tests.conftest import login
from tests.p9_helpers import FakeResolver, add_ref, add_server, finish_job, last_job

API = "/api/v1"


def meta(domain="example.com", days=60, env="staging"):
    now = datetime.now(UTC)
    return {"ok": True, "domain": domain, "environment": env, "issuer": "CN=(STAGING) Fake LE", "not_before": (now - timedelta(days=30)).isoformat(), "not_after": (now + timedelta(days=days)).isoformat(),
            "serial": "abc123", "fingerprint_sha256": "ab" * 32, "sans": [domain]}


@pytest.fixture
def env(monkeypatch, admin, client, make_user, db):
    u, t = make_user("cust@example.com", "customer")
    t.contact_email = "cust@example.com"
    db.commit()
    srv = add_server(db)
    add_ref(db, t, "domain", "example.com", server_id=srv.id, root_rel="sites/acme/public", attrs={"server_ips": ["203.0.113.10"]})
    monkeypatch.setattr(dnsmod, "resolver_factory", lambda: FakeResolver({("example.com", "A"): ["203.0.113.10"]}))
    return {"c": client, "t": t, "srv": srv, "q": f"tenant_id={t.id}"}


def request(env, domain="example.com", **kw):
    return env["c"].post(f"{API}/ssl/certificates", json={"domain": domain, "tenant_id": str(env["t"].id), **kw})


def test_invalid_domains(env):
    for bad in ["", "not a domain", "localhost", "-bad.com", "*.example.com", "1.2.3.4", "exa mple.com", "http://example.com", "example", "a" * 64 + ".com"]:
        r = request(env, bad)
        assert r.status_code in (422,), (bad, r.status_code)


def test_unowned_domain_is_404_and_enqueues_nothing(env, db):
    r = request(env, "stranger.org")
    assert r.status_code == 404
    assert db.scalars(select(AgentJob)).first() is None


def test_prerequisites_reported(env, monkeypatch):
    monkeypatch.setattr(dnsmod, "resolver_factory", lambda: FakeResolver({}))
    r = request(env)
    assert r.status_code == 409 and r.json()["error"]["code"] == "prerequisites_failed"
    failed = [c["check"] for c in r.json()["error"]["checks"] if not c["ok"]]
    assert failed == ["dns_resolves"]
    monkeypatch.setattr(dnsmod, "resolver_factory", lambda: FakeResolver({("example.com", "A"): ["198.51.100.9"]}))
    assert "not to this server" in request(env).text
    monkeypatch.setattr(dnsmod, "resolver_factory", lambda: FakeResolver(fail=True))
    assert request(env).status_code == 409
    p = env["c"].get(f"{API}/ssl/prerequisites?domain=example.com&{env['q']}").json()
    assert p["ok"] is False and "_server_id" not in str(p)


def test_missing_webroot_blocks(env, db):
    from app.models.ownership import ResourceRef

    db.query(ResourceRef).update({"root_rel": None})
    db.commit()
    r = request(env)
    assert r.status_code == 409 and any(c["check"] == "webroot" and not c["ok"] for c in r.json()["error"]["checks"])


def test_request_defaults_to_staging_and_queues_certbot_job(env, db):
    r = request(env)
    assert r.status_code == 202, r.text
    assert r.json()["status"] == "pending" and r.json()["environment"] == "staging" and r.json()["trusted"] is False
    j = last_job(db, "ssl.request_certificate")
    assert j.params["environment"] == "staging" and j.params["webroot"] == "sites/acme/public" and j.params["email"] == "cust@example.com"
    assert j.params["confirm_production"] is False
    assert any(e.action == "ssl.requested" for e in db.scalars(select(AuditEvent)))


def test_production_requires_flag_and_permission(env, db, make_client, make_user):
    # customer has ssl:read only here; hosting admin has ssl:write+manage
    r = request(env, production=True)  # super admin (owner) allowed
    assert r.status_code == 202 and r.json()["environment"] == "production"
    j = last_job(db, "ssl.request_certificate")
    assert j.params["environment"] == "production" and j.params["confirm_production"] is True
    c = make_client()
    login(c, "cust@example.com")
    assert c.post(f"{API}/ssl/certificates", json={"domain": "example.com", "production": True}).status_code == 403  # no ssl:write at all


def test_issue_lifecycle_and_no_private_key_exposure(env, db):
    cid = request(env).json()["id"]
    assert env["c"].get(f"{API}/ssl/certificates/{cid}").json()["status"] == "pending"
    finish_job(db, last_job(db).id, result={**meta(), "private_key": "-----BEGIN PRIVATE KEY-----\nSECRET\n-----END PRIVATE KEY-----"})
    g = env["c"].get(f"{API}/ssl/certificates/{cid}")
    body = g.json()
    assert body["status"] == "issued" and body["days_remaining"] in (59, 60) and body["serial"] == "abc123" and body["severity"] == "ok"
    assert "PRIVATE KEY" not in g.text and "SECRET" not in g.text and "private" not in " ".join(body.keys()).lower()
    row = db.get(Certificate, cid)
    assert "SECRET" not in str(row.__dict__)
    assert any(e.action == "ssl.issued" for e in db.scalars(select(AuditEvent)))
    ov = env["c"].get(f"{API}/ssl/overview?{env['q']}").json()
    assert ov["counts"] == {"issued": 1} and ov["staging_only"] == 1 and "PRIVATE" not in str(ov)


def test_request_failure_state_and_alert(env, db):
    cid = request(env).json()["id"]
    finish_job(db, last_job(db).id, status="failed", error="acme_failed: Challenge failed for example.com")
    b = env["c"].get(f"{API}/ssl/certificates/{cid}").json()
    assert b["status"] == "failed" and "Challenge failed" in b["last_error"]
    mail = db.scalars(select(EmailOutbox).where(EmailOutbox.kind == "ssl_failure")).all()
    assert len(mail) == 1 and mail[0].to_email == "cust@example.com"
    env["c"].get(f"{API}/ssl/certificates/{cid}")
    assert len(db.scalars(select(EmailOutbox).where(EmailOutbox.kind == "ssl_failure")).all()) == 1  # alerts once
    # a failed request can be retried
    assert request(env).status_code == 202


def test_certbot_missing_is_reported_honestly(env, db):
    cid = request(env).json()["id"]
    finish_job(db, last_job(db).id, status="failed", error="certbot_not_installed: certbot is not installed on this server; certificates cannot be issued.")
    assert "not installed" in env["c"].get(f"{API}/ssl/certificates/{cid}").json()["last_error"]


def issue(env, db, days=60):
    cid = request(env).json()["id"]
    finish_job(db, last_job(db).id, result=meta(days=days))
    env["c"].get(f"{API}/ssl/certificates/{cid}")
    return cid


def test_cannot_double_request_valid_cert(env, db):
    issue(env, db)
    r = request(env)
    assert r.status_code == 409 and r.json()["error"]["code"] == "already_issued"


def test_manual_renewal_success(env, db):
    cid = issue(env, db, days=10)
    r = env["c"].post(f"{API}/ssl/certificates/{cid}/renew")
    assert r.status_code == 202 and r.json()["status"] == "renewing"
    assert env["c"].post(f"{API}/ssl/certificates/{cid}/renew").status_code == 409
    j = last_job(db, "ssl.renew")
    assert j.params == {"domain": "example.com", "environment": "staging", "confirm_production": False}
    finish_job(db, j.id, result=meta(days=89))
    b = env["c"].get(f"{API}/ssl/certificates/{cid}").json()
    assert b["status"] == "issued" and b["days_remaining"] >= 88 and b["renewal_failures"] == 0
    assert any(e.action == "ssl.renewed" for e in db.scalars(select(AuditEvent)))


def test_renewal_failure_keeps_serving_cert_and_alerts(env, db):
    cid = issue(env, db, days=10)
    env["c"].post(f"{API}/ssl/certificates/{cid}/renew")
    finish_job(db, last_job(db, "ssl.renew").id, status="failed", error="renewal_failed: rate limit")
    b = env["c"].get(f"{API}/ssl/certificates/{cid}").json()
    assert b["status"] == "issued" and b["renewal_failures"] == 1 and "rate limit" in b["last_error"] and b["severity"] == "warning"
    assert any(e.action == "ssl.renewal_failed" for e in db.scalars(select(AuditEvent)))
    assert len(db.scalars(select(EmailOutbox).where(EmailOutbox.kind == "ssl_failure")).all()) == 1


def test_sweep_enqueues_only_what_is_due(env, db):
    cid = issue(env, db, days=10)
    other_domain_cert = Certificate(tenant_id=env["t"].id, domain="far.example.com", status="issued", environment="staging", not_before=datetime.now(UTC), not_after=datetime.now(UTC) + timedelta(days=80), server_id=env["srv"].id)
    off = Certificate(tenant_id=env["t"].id, domain="off.example.com", status="issued", environment="staging", not_before=datetime.now(UTC), not_after=datetime.now(UTC) + timedelta(days=5), auto_renew=False, server_id=env["srv"].id)
    db.add_all([other_domain_cert, off])
    db.commit()
    out = sslsvc.sweep_renewals(db)
    db.commit()
    assert out["renewals_enqueued"] == ["example.com"]
    assert db.get(Certificate, cid).status == "renewing"
    again = sslsvc.sweep_renewals(db)
    assert again["renewals_enqueued"] == []  # already renewing: idempotent
    assert db.get(Certificate, other_domain_cert.id).status == "issued" and db.get(Certificate, off.id).status == "issued"


def test_sweep_backoff_and_give_up(env, db):
    cid = issue(env, db, days=10)
    c = db.get(Certificate, cid)
    c.renewal_failures, c.last_attempt_at = 2, datetime.now(UTC) - timedelta(hours=1)
    db.commit()
    assert sslsvc.sweep_renewals(db)["skipped_backoff"] == ["example.com"]
    c = db.get(Certificate, cid)
    c.renewal_failures, c.last_attempt_at = 5, datetime.now(UTC) - timedelta(days=3)
    db.commit()
    out = sslsvc.sweep_renewals(db)
    db.commit()
    assert out["gave_up"] == ["example.com"] and out["renewals_enqueued"] == []
    assert sslsvc.sweep_renewals(db)["gave_up"] == []
    assert any(e.action == "ssl.renewal_exhausted" for e in db.scalars(select(AuditEvent)))


def test_sweep_marks_expired_and_alerts(env, db):
    cid = issue(env, db, days=1)
    out = sslsvc.sweep_renewals(db, now=datetime.now(UTC) + timedelta(days=3))
    db.commit()
    assert "example.com" in out["marked_expired"]
    assert db.get(Certificate, cid).status in ("expired", "renewing")
    assert db.scalars(select(EmailOutbox).where(EmailOutbox.kind == "ssl_expired")).first() is not None


def test_expiry_dashboard_buckets(env, db):
    now = datetime.now(UTC)
    for dom, days in (("a.example.com", 3), ("b.example.com", 12), ("c.example.com", 25), ("d.example.com", 70)):
        db.add(Certificate(tenant_id=env["t"].id, domain=dom, status="issued", environment="production", not_before=now - timedelta(days=20), not_after=now + timedelta(days=days)))
    db.add(Certificate(tenant_id=env["t"].id, domain="e.example.com", status="issued", environment="production", not_before=now - timedelta(days=99), not_after=now - timedelta(days=1)))
    db.commit()
    ov = env["c"].get(f"{API}/ssl/overview?{env['q']}").json()
    assert (ov["expiring_7d"], ov["expiring_14d"], ov["expiring_30d"]) == (1, 2, 3)
    by = {c["domain"]: c for c in ov["certificates"]}
    assert by["a.example.com"]["severity"] == "critical" and by["b.example.com"]["severity"] == "warning" and by["c.example.com"]["severity"] == "notice" and by["d.example.com"]["severity"] == "ok"
    assert by["e.example.com"]["status"] == "expired"
    assert ov["certificates"][0]["domain"] == "e.example.com"  # soonest expiry first


def test_cross_tenant_certificates_404(env, db, make_client, make_user):
    cid = issue(env, db)
    u2, t2 = make_user("evil@example.com", "customer")
    c2 = make_client()
    login(c2, "evil@example.com")
    assert c2.get(f"{API}/ssl/certificates/{cid}").status_code == 404
    assert c2.post(f"{API}/ssl/certificates/{cid}/renew").status_code in (403, 404)
    assert c2.get(f"{API}/ssl/certificates").json()["items"] == []
    assert c2.get(f"{API}/ssl/overview").json()["total"] == 0


def test_customer_read_only(env, db, make_client):
    cid = issue(env, db)
    c = make_client()
    login(c, "cust@example.com")
    assert c.get(f"{API}/ssl/certificates/{cid}").status_code == 200
    d = c.get(f"{API}/ssl/domains").json()["items"]
    assert d[0]["domain"] == "example.com" and d[0]["certificate"]["status"] == "issued"
    assert c.post(f"{API}/ssl/certificates/{cid}/renew").status_code == 403
    assert c.patch(f"{API}/ssl/certificates/{cid}", json={"auto_renew": False}).status_code == 403
    assert c.post(f"{API}/ssl/sweep").status_code == 403


def test_auto_renew_toggle_audited(env, db):
    cid = issue(env, db)
    r = env["c"].patch(f"{API}/ssl/certificates/{cid}", json={"auto_renew": False})
    assert r.json()["auto_renew"] is False
    assert any(e.action == "ssl.auto_renew_changed" for e in db.scalars(select(AuditEvent)))


def test_sweep_endpoint_platform_only(env, db):
    issue(env, db, days=10)
    r = env["c"].post(f"{API}/ssl/sweep")
    assert r.status_code == 200 and r.json()["renewals_enqueued"] == ["example.com"]
