import io
import json
import logging
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.core import observability as obs
from app.core.config import get_settings
from app.main import app
from app.models.identity import AuditEvent, EmailOutbox, LoginEvent
from app.models.security import SecurityEvent, WebhookDelivery
from app.services import security_events as sec
from tests.conftest import login

API = "/api/v1"
SECRETS = ["Sup3rSecret!Pw", "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjMifQ.c2lnbmF0dXJlMTIz", "kvl_abcdef123456_" + "Q" * 43, "whsec_" + "Z" * 40, "abc123tokenvalue", "sessioncookievalue"]


@pytest.fixture
def logstream():
    stream = io.StringIO()
    h = logging.StreamHandler(stream)
    h.setFormatter(obs.JsonFormatter())
    h.addFilter(obs.RedactingFilter())
    root = logging.getLogger()
    old = root.level
    root.addHandler(h)
    root.setLevel(logging.INFO)
    yield stream
    root.removeHandler(h)
    root.setLevel(old)


@pytest.fixture
def world(make_user, make_client):
    w = {}
    for key, email, role in [("a", "a@x.example.com", "customer"), ("s", "sup@kvl.example.com", "support"), ("o", "owner@kvl.example.com", "super_admin"), ("h", "host@kvl.example.com", "hosting_admin")]:
        u, t = make_user(email, role)
        c = make_client()
        assert login(c, email).status_code == 200
        w[key] = {"u": u, "t": t, "c": c}
    return w


# ---------------------------------------------------------------- redaction
def test_redaction_unit():
    log = logging.getLogger("kvl.test.redact")
    stream = io.StringIO()
    h = logging.StreamHandler(stream)
    h.setFormatter(obs.JsonFormatter())
    log.addHandler(h)
    log.setLevel(logging.INFO)
    log.propagate = False
    log.info("auth header Authorization: Bearer %s sent", SECRETS[2])
    log.info("login failed password=%s for user", SECRETS[0])
    log.info('payload {"password": "%s", "token": "%s"}', SECRETS[0], SECRETS[4])
    log.info("jwt %s and key %s and hook %s", SECRETS[1], SECRETS[2], SECRETS[3])
    log.info("Cookie: kvl_at=%s; kvl_csrf=zzzz", SECRETS[5])
    log.info("event", extra={"password": SECRETS[0], "headers": {"Authorization": "Bearer " + SECRETS[4], "x": "ok"}, "refresh_token": SECRETS[4]})
    try:
        raise ValueError(f"boom password={SECRETS[0]}")
    except ValueError:
        log.exception("failed")
    out = stream.getvalue()
    for s in SECRETS:
        assert s not in out, s
    assert "[redacted]" in out and '"request_id"' in out and "login failed" in out
    for line in out.strip().splitlines():
        json.loads(line)  # every line is valid JSON


def test_requests_never_log_tokens_passwords_or_queries(world, make_client, logstream):
    c = make_client()
    r = c.post(f"{API}/auth/login", json={"email": "nobody@x.example.com", "password": SECRETS[0]}, headers={"Authorization": f"Bearer {SECRETS[2]}", "Cookie": f"kvl_at={SECRETS[5]}"})
    assert r.status_code in (401, 403)
    c.get(f"{API}/support/tickets?token={SECRETS[4]}&password={SECRETS[0]}", headers={"Authorization": f"Bearer {SECRETS[2]}"})
    world["a"]["c"].get(f"{API}/support/tickets?q={SECRETS[4]}")
    # the test client's own `httpx` logger is not application logging; everything the app emits is checked
    out = "\n".join(line for line in logstream.getvalue().splitlines() if '"logger": "httpx"' not in line)
    for s in SECRETS:
        assert s not in out, s
    lines = [json.loads(line) for line in out.strip().splitlines() if '"kvl.access"' in line]
    assert lines and all(x["request_id"] and "?" not in x["path"] for x in lines)
    assert {"method", "route", "status", "duration_ms"} <= set(lines[0])


def test_request_and_correlation_ids(client, logstream):
    r = client.get("/healthz", headers={"X-Request-ID": "corr-123"})
    assert r.headers["x-request-id"] == "corr-123"
    assert '"request_id": "corr-123"' in logstream.getvalue()
    assert client.get("/healthz").headers["x-request-id"] != "corr-123"
    e = client.get(f"{API}/support/tickets", headers={"X-Request-ID": "corr-9"}).json()["error"]
    assert e["request_id"] == "corr-9" and e["code"] == "unauthenticated"
    v = client.post(f"{API}/auth/login", json={}).json()["error"]
    assert v["code"] == "validation_error" and v["fields"]


# ---------------------------------------------------------------- error reporting
def test_unhandled_errors_are_reported_and_hidden(logstream):
    async def boom():
        raise RuntimeError(f"db exploded password={SECRETS[0]}")

    app.add_api_route("/__boom_test", boom, include_in_schema=False)
    got = []
    obs.register_error_reporter(lambda exc, ctx: got.append((type(exc).__name__, ctx["request_id"])))
    try:
        c = TestClient(app, raise_server_exceptions=False)
        r = c.get("/__boom_test", headers={"X-Request-ID": "rid-boom"})
        assert r.status_code == 500 and r.json()["error"]["code"] == "internal_error" and "exploded" not in r.text
        assert got == [("RuntimeError", "rid-boom")]
        assert SECRETS[0] not in logstream.getvalue() and "unhandled exception" in logstream.getvalue()
        assert 'kvl_unhandled_errors_total{type="RuntimeError"}' in obs.metrics.render()
    finally:
        obs._reporters.clear()


# ---------------------------------------------------------------- readiness / metrics
class _FakeRedis:
    def ping(self):
        return True


def test_readyz_ok_failure_and_no_secrets(client, monkeypatch):
    import redis

    monkeypatch.setattr(redis.Redis, "from_url", staticmethod(lambda *a, **k: _FakeRedis()))
    r = client.get("/readyz")
    assert r.status_code == 200 and r.json() == {"status": "ready", "checks": {"database": "ok", "redis": "ok"}}
    s = get_settings()
    for secret in (s.secret_key, s.database_url, "kvl_dev_only_pw", s.redis_url):
        assert secret not in r.text
    # redis down
    def down(*a, **k):
        raise redis.ConnectionError("redis://:hunter2@internal:6379 refused")

    monkeypatch.setattr(redis.Redis, "from_url", staticmethod(down))
    r = client.get("/readyz")
    assert r.status_code == 503 and r.json()["checks"]["redis"] == "unavailable" and "hunter2" not in r.text
    # database down
    class Dead:
        def connect(self):
            raise RuntimeError("could not connect postgresql://kvl:kvl_dev_only_pw@host/db")

    import app.core.db as dbmod

    monkeypatch.setattr(dbmod, "engine", Dead())
    r = client.get("/readyz")
    assert r.status_code == 503 and r.json()["checks"]["database"] == "unavailable" and "kvl_dev_only_pw" not in r.text
    assert client.get("/healthz").json() == {"status": "ok"}


def test_metrics_protected(world, client, make_client, monkeypatch):
    assert client.get("/metrics").status_code == 401
    assert client.get("/metrics", headers={"Authorization": "Bearer nope"}).status_code == 401
    assert world["a"]["c"].get("/metrics").status_code == 403
    assert world["s"]["c"].get("/metrics").status_code == 403
    assert world["h"]["c"].get("/metrics").status_code == 403
    # an unset token never matches an empty bearer
    assert client.get("/metrics", headers={"Authorization": "Bearer "}).status_code == 401
    world["a"]["c"].get("/healthz")
    tid = str(uuid.uuid4())
    world["a"]["c"].get(f"{API}/support/tickets/{tid}")
    r = world["o"]["c"].get("/metrics")
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/plain")
    t = r.text
    assert 'kvl_http_requests_total{method="GET",route="/healthz",status="200"}' in t
    assert 'route="/api/v1/support/tickets/{ticket_id}",status="404"' in t and tid not in t  # route templates, never raw ids
    assert "kvl_http_request_duration_seconds_bucket" in t and 'le="+Inf"' in t and "kvl_http_request_duration_seconds_sum" in t
    # route templates such as /auth/password/reset are legitimate labels; what must never appear are secret values
    for secret in (get_settings().secret_key, "Correct-Horse-9", "kvl_at=", "Bearer "):
        assert secret not in t
    monkeypatch.setattr(get_settings(), "metrics_token", "scrape-token-for-prometheus-1234567890")
    c = make_client()
    assert c.get("/metrics", headers={"Authorization": "Bearer scrape-token-for-prometheus-1234567890"}).status_code == 200
    assert c.get("/metrics", headers={"Authorization": "Bearer scrape-token-for-prometheus-12345678901"}).status_code == 401


def test_openapi_polish(client):
    spec = client.get("/api/openapi.json").json()
    assert {t["name"] for t in spec["tags"]} >= {"support", "api-keys", "webhooks", "security", "developer"}
    assert "Error" in spec["components"]["schemas"] and "Idempotency-Key" in spec["info"]["description"]
    op = spec["paths"]["/api/v1/support/tickets"]["post"]
    assert {"401", "403", "404", "429"} <= set(op["responses"]) and op["responses"]["422"]["content"]["application/json"]["schema"]["$ref"].endswith("/Error")
    assert client.get("/api/docs").status_code == 200


# ---------------------------------------------------------------- security events + alert routing
def _fail_logins(db, n, ip="203.0.113.7", email="victim@x.example.com", outcome="bad_password"):
    for _ in range(n):
        db.add(LoginEvent(email=email, outcome=outcome, ip=ip))
    db.commit()


def test_failed_login_surge_detected_deduped_and_masked(world, db):
    _fail_logins(db, 9)
    assert sec.scan_for_incidents(db)["failed_login_surge"] == 0  # under threshold
    db.commit()
    _fail_logins(db, 3)
    out = sec.scan_for_incidents(db)
    db.commit()
    assert out["failed_login_surge"] == 2  # one for the IP, one for the targeted account
    assert sec.scan_for_incidents(db)["failed_login_surge"] == 0  # deduplicated
    evs = db.scalars(select(SecurityEvent)).all()
    assert {e.kind for e in evs} == {"failed_login_surge"} and all(e.severity == "high" for e in evs)
    assert all("victim@" not in e.summary for e in evs) and any("v***@x.example.com" in e.summary for e in evs)
    _fail_logins(db, 60, ip="198.51.100.1", email="b@x.example.com")
    sec.scan_for_incidents(db)
    db.commit()
    assert db.scalar(select(SecurityEvent).where(SecurityEvent.ip == "198.51.100.1")).severity == "critical"
    # successful logins do not count
    _fail_logins(db, 20, ip="192.0.2.9", email="ok@x.example.com", outcome="success")
    sec.scan_for_incidents(db)
    assert not db.scalar(select(SecurityEvent).where(SecurityEvent.ip == "192.0.2.9"))


def test_refresh_reuse_surfaced_from_audit(world, db):
    db.add(AuditEvent(action="auth.refresh_reuse_detected", actor_user_id=world["a"]["u"].id, ip="198.51.100.5"))
    db.commit()
    assert sec.scan_for_incidents(db)["refresh_token_reuse"] == 1
    db.commit()
    assert sec.scan_for_incidents(db)["refresh_token_reuse"] == 0
    assert db.scalar(select(SecurityEvent).where(SecurityEvent.kind == "refresh_token_reuse")).user_id == world["a"]["u"].id


def test_privilege_escalation_attempt_recorded_from_live_request(world, db):
    o = world["o"]["c"]
    r = o.post(f"{API}/rbac/memberships", json={"user_id": str(world["o"]["u"].id), "role": "super_admin"})
    assert r.status_code == 403 and r.json()["error"]["code"] == "self_assignment"  # body still intact after inspection
    db.expire_all()
    ev = db.scalar(select(SecurityEvent).where(SecurityEvent.kind == "privilege_escalation_attempt"))
    assert ev and ev.user_id == world["o"]["u"].id and ev.severity == "high" and ev.detail["code"] == "self_assignment"
    assert db.scalar(select(AuditEvent).where(AuditEvent.action == "security.privilege_escalation_attempt"))
    # ordinary 403s are not security events
    n = len(db.scalars(select(SecurityEvent)).all())
    world["a"]["c"].get(f"{API}/security/events")
    db.expire_all()
    assert len(db.scalars(select(SecurityEvent)).all()) == n


def test_alert_routing_and_acknowledgement(world, db):
    o, s = world["o"]["c"], world["s"]["c"]
    assert s.post(f"{API}/security/alert-routes", json={"name": "x", "channel": "email", "target": "a@b.co"}).status_code == 403
    assert o.post(f"{API}/security/alert-routes", json={"name": "bad", "channel": "email"}).status_code == 422
    assert o.post(f"{API}/security/alert-routes", json={"name": "bad", "channel": "email", "target": "not-an-email"}).status_code == 422
    assert o.post(f"{API}/security/alert-routes", json={"name": "oncall", "channel": "email", "target": "oncall@kvl.example.com", "min_severity": "high"}).status_code == 201
    assert o.post(f"{API}/security/alert-routes", json={"name": "hook", "channel": "webhook", "min_severity": "critical"}).status_code == 201
    hook = o.post(f"{API}/webhooks", json={"url": "https://hooks.example.com/sec", "events": ["security.alert"]})
    # (DNS is real in this test only for creation; skip when sandbox has no DNS)
    low = sec.record_event(db, "api_key_rate_limited", "low", "noise")
    high = sec.record_event(db, "failed_login_surge", "high", "surge from 1.2.3.4", ip="1.2.3.4")
    db.commit()
    assert not low.routed and high.routed
    mails = db.scalars(select(EmailOutbox).where(EmailOutbox.kind == "security_alert")).all()
    assert [m.to_email for m in mails] == ["oncall@kvl.example.com"] and "surge" in mails[0].body
    crit = sec.record_event(db, "failed_login_surge", "critical", "big surge")
    db.commit()
    assert crit.routed
    if hook.status_code == 201:
        assert db.scalar(select(WebhookDelivery).where(WebhookDelivery.event_type == "security.alert"))
    # kind filter
    o.post(f"{API}/security/alert-routes", json={"name": "only-reuse", "channel": "email", "target": "x@kvl.example.com", "min_severity": "low", "kinds": ["refresh_token_reuse"]})
    sec.record_event(db, "webhook_url_blocked", "low", "x")
    db.commit()
    assert not [m for m in db.scalars(select(EmailOutbox)) if m.to_email == "x@kvl.example.com"]
    # list, filter, acknowledge, resolve
    ev = o.get(f"{API}/security/events?status=open&severity=high").json()
    assert ev["total"] == 1 and ev["items"][0]["id"] == str(high.id)
    assert s.get(f"{API}/security/events").status_code == 403 and world["a"]["c"].post(f"{API}/security/events/{high.id}/acknowledge").status_code == 403
    a = o.post(f"{API}/security/events/{high.id}/acknowledge").json()
    assert a["status"] == "acknowledged" and a["acknowledged_by"] == str(world["o"]["u"].id) and a["acknowledged_at"]
    assert o.get(f"{API}/security/events?status=open&severity=high").json()["total"] == 0
    assert o.post(f"{API}/security/events/{high.id}/resolve").json()["status"] == "resolved"
    assert o.post(f"{API}/security/events/{high.id}/acknowledge").status_code == 409
    assert o.post(f"{API}/security/events/{uuid.uuid4()}/acknowledge").status_code == 404
    assert o.get(f"{API}/security/overview").json()["alert_routes"] == 3
    assert o.post(f"{API}/security/scan").status_code == 200
    routes = o.get(f"{API}/security/alert-routes").json()
    assert o.delete(f"{API}/security/alert-routes/{routes[0]['id']}").status_code == 200


def test_audit_viewer_sessions_and_abuse_settings(world, db):
    o, s = world["o"]["c"], world["s"]["c"]
    world["a"]["c"].post(f"{API}/support/tickets", json={"subject": "abc def", "body": "x"})
    r = o.get(f"{API}/security/audit?action=support.ticket&limit=1").json()
    assert r["total"] >= 1 and len(r["items"]) == 1 and r["items"][0]["action"].startswith("support.ticket")
    assert o.get(f"{API}/security/audit?actor_user_id={world['a']['u'].id}").json()["total"] >= 1
    assert o.get(f"{API}/security/audit?action=zzz").json()["total"] == 0
    future = (datetime.now(UTC) + timedelta(days=1)).isoformat().replace("+", "%2B")
    assert o.get(f"{API}/security/audit?created_from={future}").json()["total"] == 0
    assert s.get(f"{API}/security/audit").status_code == 403 and world["a"]["c"].get(f"{API}/security/audit").status_code == 403
    assert o.get(f"{API}/security/audit?limit=1000").status_code == 422
    ses = o.get(f"{API}/security/sessions").json()
    assert ses["active"] >= 4 and {"email", "ip", "user_agent"} <= set(ses["items"][0])
    target = next(i for i in ses["items"] if i["email"] == "a@x.example.com")
    assert o.post(f"{API}/security/sessions/{target['id']}/revoke").status_code == 200
    assert world["a"]["c"].get(f"{API}/support/tickets").status_code == 401  # revoked immediately
    assert world["s"]["c"].post(f"{API}/security/sessions/{target['id']}/revoke").status_code == 403
    ab = o.get(f"{API}/security/abuse-settings").json()
    assert ab["editable"] is False and ab["login"]["lockout_threshold"] == get_settings().lockout_threshold
