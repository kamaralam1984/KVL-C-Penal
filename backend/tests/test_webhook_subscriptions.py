import time
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select

from app.core import netsafe
from app.core.security import decrypt_secret
from app.models.security import WebhookDelivery, WebhookEndpoint
from app.models.support import Ticket
from app.services import webhooks as wh
from tests.conftest import login

API = "/api/v1"
DNS = {"hooks.example.com": ["93.184.216.34"], "other.example.org": ["8.8.8.8"], "evil.example.net": ["10.1.2.3"], "mixed.example.net": ["8.8.4.4", "192.168.1.5"],
       "v6mapped.example.net": ["::ffff:127.0.0.1"], "meta.example.net": ["169.254.169.254"], "loop6.example.net": ["::1"], "cgnat.example.net": ["100.64.0.9"],
       "ula.example.net": ["fd00:ec2::254"]}


@pytest.fixture(autouse=True)
def fake_dns(monkeypatch):
    table = dict(DNS)

    def resolve(host):
        if host not in table:
            raise netsafe.UnsafeURL("hostname does not resolve")
        return list(table[host])

    monkeypatch.setattr(netsafe, "resolve", resolve)
    return table


class Recorder:
    def __init__(self, status=200):
        self.status, self.calls = status, []

    def __call__(self, host, port, path, ip, headers, body, timeout):
        self.calls.append({"host": host, "port": port, "path": path, "ip": ip, "headers": headers, "body": body.decode()})
        return self.status


@pytest.fixture
def rec(monkeypatch):
    r = Recorder()
    monkeypatch.setattr(wh, "transport", r)
    return r


@pytest.fixture
def world(make_user, make_client):
    w = {}
    for key, email, role in [("a", "a@x.example.com", "customer"), ("b", "b@y.example.com", "customer"), ("o", "owner@kvl.example.com", "super_admin"), ("s", "sup@kvl.example.com", "support")]:
        u, t = make_user(email, role)
        c = make_client()
        assert login(c, email).status_code == 200
        w[key] = {"u": u, "t": t, "c": c}
    return w


def mk_hook(c, url="https://hooks.example.com/in", events=("ticket.created",), **kw):
    r = c.post(f"{API}/webhooks", json={"url": url, "events": list(events), **kw})
    assert r.status_code == 201, r.text
    return r.json()


# ---------------------------------------------------------------- SSRF
@pytest.mark.parametrize("url", [
    "https://127.0.0.1/hook", "https://10.0.0.1/hook", "https://192.168.0.10/", "https://172.16.0.1/", "https://169.254.169.254/latest/meta-data/",
    "https://[::1]/hook", "https://[fe80::1]/", "https://[::ffff:127.0.0.1]/", "https://localhost/hook", "https://LOCALHOST./hook", "https://foo.localhost/",
    "https://metadata.google.internal/", "https://service.internal/", "https://printer.local/", "https://intranet/",
    "https://hooks.example.com@127.0.0.1/", "https://127.0.0.1#@hooks.example.com/", "https://hooks.example.com:pass@10.0.0.1/", "https://user:pw@hooks.example.com/",
    "https://2130706433/", "https://0x7f000001/", "https://0177.0.0.1/", "https://127.1/", "https://017700000001/", "https://0x7f.0.0.1/", "https://4294967295/",
    "http://hooks.example.com/hook", "ftp://hooks.example.com/", "file:///etc/passwd", "gopher://hooks.example.com/", "//hooks.example.com/hook", "hooks.example.com",
    "https://hooks.example.com:22/", "https://hooks.example.com:80/", "https://hooks.example.com\\@127.0.0.1/", "https://hooks.example.com/ x", "https://hooks.example.com/\r\nX: y",
    "https://evil.example.net/", "https://mixed.example.net/", "https://v6mapped.example.net/", "https://meta.example.net/", "https://loop6.example.net/", "https://cgnat.example.net/",
    "https://ula.example.net/", "https://unresolvable.example.invalid/", "https://",
])
def test_ssrf_urls_rejected_at_creation(world, url):
    r = world["a"]["c"].post(f"{API}/webhooks", json={"url": url, "events": ["ping"]})
    assert r.status_code == 422, (url, r.status_code, r.text)
    assert r.json()["error"]["code"] in {"unsafe_url", "validation_error"}


def test_good_urls_accepted_and_blocks_are_recorded(world, db):
    for u in ("https://hooks.example.com/in", "https://other.example.org:8443/x?y=1"):
        assert world["a"]["c"].post(f"{API}/webhooks", json={"url": u, "events": ["ping"]}).status_code == 201
    world["a"]["c"].post(f"{API}/webhooks", json={"url": "https://169.254.169.254/", "events": ["ping"]})
    from app.models.security import SecurityEvent

    assert db.scalar(select(SecurityEvent).where(SecurityEvent.kind == "webhook_url_blocked"))
    # PATCH to an unsafe URL is blocked too
    hid = mk_hook(world["b"]["c"])["id"]
    assert world["b"]["c"].patch(f"{API}/webhooks/{hid}", json={"url": "https://127.0.0.1/x"}).status_code == 422


def test_dns_rebinding_blocked_at_delivery(world, rec, fake_dns, db):
    mk_hook(world["a"]["c"], url="https://hooks.example.com/in")  # public at creation time
    world["a"]["c"].post(f"{API}/support/tickets", json={"subject": "abc def", "body": "x"})
    fake_dns["hooks.example.com"] = ["127.0.0.1"]  # attacker flips the record
    counts = wh.deliver_due(db)
    db.commit()
    assert counts == {"succeeded": 0, "retrying": 0, "dead": 1} and rec.calls == []
    d = db.scalar(select(WebhookDelivery))
    assert d.status == "dead" and d.last_error.startswith("blocked")


def test_delivery_connects_to_validated_ip_only(world, rec, db):
    mk_hook(world["a"]["c"])
    world["a"]["c"].post(f"{API}/support/tickets", json={"subject": "abc def", "body": "x"})
    wh.deliver_due(db)
    db.commit()
    c = rec.calls[0]
    assert c["ip"] == "93.184.216.34" and c["host"] == "hooks.example.com" and c["port"] == 443


@pytest.mark.parametrize("location", ["https://127.0.0.1/", "https://169.254.169.254/latest", "https://hooks.example.com/other"])
def test_redirects_are_never_followed(world, monkeypatch, db, location):
    r = Recorder(302)
    monkeypatch.setattr(wh, "transport", r)
    mk_hook(world["a"]["c"])
    world["a"]["c"].post(f"{API}/support/tickets", json={"subject": "abc def", "body": "x"})
    assert wh.deliver_due(db)["retrying"] == 1
    db.commit()
    assert len(r.calls) == 1  # nothing contacted after the 3xx
    assert db.scalar(select(WebhookDelivery)).last_error == "redirect_not_followed"


def test_real_transport_disables_redirects_and_pins_ip():
    import inspect

    src = inspect.getsource(wh.http_transport)
    assert "follow_redirects=False" in src and "sni_hostname" in src and "trust_env=False" in src


# ---------------------------------------------------------------- signing
def test_secret_shown_once_encrypted_at_rest(world, db):
    h = mk_hook(world["a"]["c"])
    secret = h["signing_secret"]
    assert secret.startswith("whsec_") and "shown only once" in h["notice"]
    assert secret not in world["a"]["c"].get(f"{API}/webhooks").text
    e = db.scalar(select(WebhookEndpoint))
    assert e.secret_enc != secret and secret not in e.secret_enc and decrypt_secret(e.secret_enc) == secret


def test_signature_and_replay_protection(world, rec, db):
    h = mk_hook(world["a"]["c"], events=["ticket.created", "ticket.replied"])
    secret = h["signing_secret"]
    world["a"]["c"].post(f"{API}/support/tickets", json={"subject": "abc def", "body": "private body text"})
    assert wh.deliver_due(db) == {"succeeded": 1, "retrying": 0, "dead": 0}
    db.commit()
    call = rec.calls[0]
    sig = call["headers"]["X-KVL-Signature"]
    assert call["headers"]["X-KVL-Event"] == "ticket.created"
    assert wh.verify_signature(secret, sig, call["body"])
    assert not wh.verify_signature("whsec_wrong", sig, call["body"])
    assert not wh.verify_signature(secret, sig, call["body"] + " ")  # tampered body
    ts = int(sig.split(",")[0][2:])
    assert not wh.verify_signature(secret, sig, call["body"], now_ts=ts + 301)  # replay outside the window
    assert wh.verify_signature(secret, sig, call["body"], now_ts=ts + 299)
    assert not wh.verify_signature(secret, sig.replace("t=" + str(ts), "t=" + str(ts + 400)), call["body"])  # timestamp is covered by the MAC
    assert not wh.verify_signature(secret, "garbage", call["body"]) and not wh.verify_signature(secret, "", call["body"])
    import json

    p = json.loads(call["body"])
    assert p["type"] == "ticket.created" and p["data"]["subject"] == "abc def" and "private body text" not in call["body"]
    assert call["headers"]["Content-Type"] == "application/json"


def test_secret_rotation_overlap(world, rec, db):
    h = mk_hook(world["a"]["c"])
    old = h["signing_secret"]
    r = world["a"]["c"].post(f"{API}/webhooks/{h['id']}/rotate-secret").json()
    new = r["signing_secret"]
    assert new != old and r["rotation_overlap_until"]
    world["a"]["c"].post(f"{API}/support/tickets", json={"subject": "abc def", "body": "x"})
    wh.deliver_due(db)
    db.commit()
    c = rec.calls[0]
    assert wh.verify_signature(new, c["headers"]["X-KVL-Signature"], c["body"]) and wh.verify_signature(old, c["headers"]["X-KVL-Signature"], c["body"])
    e = db.scalar(select(WebhookEndpoint))
    e.prev_secret_expires_at = datetime.now(UTC) - timedelta(minutes=1)
    db.commit()
    world["a"]["c"].post(f"{API}/support/tickets", json={"subject": "second one", "body": "x"})
    wh.deliver_due(db)
    c2 = rec.calls[1]
    assert wh.verify_signature(new, c2["headers"]["X-KVL-Signature"], c2["body"]) and not wh.verify_signature(old, c2["headers"]["X-KVL-Signature"], c2["body"])


# ---------------------------------------------------------------- retry, backoff, dead letter
def test_retry_backoff_and_dead_letter(world, monkeypatch, db):
    r = Recorder(500)
    monkeypatch.setattr(wh, "transport", r)
    mk_hook(world["a"]["c"])
    world["a"]["c"].post(f"{API}/support/tickets", json={"subject": "abc def", "body": "x"})
    d = db.scalar(select(WebhookDelivery))
    now = datetime.now(UTC)
    gaps = []
    for i in range(5):
        before = now
        assert wh.deliver_due(db, now) == {"succeeded": 0, "retrying": 1 if i < 4 else 0, "dead": 1 if i == 4 else 0}
        db.commit()
        db.refresh(d)
        if d.next_attempt_at:
            gaps.append(int((d.next_attempt_at - before).total_seconds()))
            now = d.next_attempt_at
        assert d.attempts == i + 1 and d.last_status_code == 500
    assert gaps == [60, 300, 1800, 7200] and d.status == "dead" and d.next_attempt_at is None
    assert wh.deliver_due(db, now + timedelta(days=9)) == {"succeeded": 0, "retrying": 0, "dead": 0}
    # not due yet -> untouched
    # redrive dead letter through the API after the receiver is fixed
    r.status = 204
    db.expire_all()
    rr = world["a"]["c"].post(f"{API}/webhooks/deliveries/{d.id}/retry")
    assert rr.status_code == 200 and rr.json()["status"] == "pending"
    assert wh.deliver_due(db) == {"succeeded": 1, "retrying": 0, "dead": 0}
    assert world["b"]["c"].post(f"{API}/webhooks/deliveries/{d.id}/retry").status_code == 404


def test_not_due_deliveries_wait(world, monkeypatch, db):
    r = Recorder(500)
    monkeypatch.setattr(wh, "transport", r)
    mk_hook(world["a"]["c"])
    world["a"]["c"].post(f"{API}/support/tickets", json={"subject": "abc def", "body": "x"})
    wh.deliver_due(db)
    db.commit()
    assert wh.deliver_due(db) == {"succeeded": 0, "retrying": 0, "dead": 0} and len(r.calls) == 1


def test_dispatch_endpoint_platform_only_and_test_event(world, rec):
    h = mk_hook(world["a"]["c"], events=["ping"])
    assert world["a"]["c"].post(f"{API}/webhooks/dispatch").status_code == 403
    out = world["a"]["c"].post(f"{API}/webhooks/{h['id']}/test").json()
    assert out[0]["status"] == "succeeded" and rec.calls[0]["headers"]["X-KVL-Event"] == "ping"
    log = world["a"]["c"].get(f"{API}/webhooks/{h['id']}/deliveries").json()
    assert log[0]["event_type"] == "ping" and "payload" not in log[0]
    assert world["o"]["c"].post(f"{API}/webhooks/dispatch").json() == {"succeeded": 0, "retrying": 0, "dead": 0}


# ---------------------------------------------------------------- isolation & permissions
def test_webhook_tenant_isolation_and_platform_endpoints(world, rec, db):
    ha = mk_hook(world["a"]["c"])
    hb = mk_hook(world["b"]["c"], url="https://other.example.org/b")
    hp = mk_hook(world["o"]["c"], url="https://other.example.org/platform", events=["ticket.created", "security.alert"])
    assert hp["tenant_id"] is None
    world["a"]["c"].post(f"{API}/support/tickets", json={"subject": "abc def", "body": "x"})
    deliveries = db.scalars(select(WebhookDelivery)).all()
    assert sorted(str(d.endpoint_id) for d in deliveries) == sorted([ha["id"], hp["id"]])  # B's endpoint gets nothing from tenant A
    # visibility
    assert [w["id"] for w in world["a"]["c"].get(f"{API}/webhooks").json()] == [ha["id"]]
    assert world["b"]["c"].get(f"{API}/webhooks/{ha['id']}/deliveries").status_code == 404
    assert world["b"]["c"].patch(f"{API}/webhooks/{ha['id']}", json={"status": "disabled"}).status_code == 404
    assert world["b"]["c"].delete(f"{API}/webhooks/{ha['id']}").status_code == 404
    assert world["b"]["c"].post(f"{API}/webhooks/{ha['id']}/rotate-secret").status_code == 404
    assert world["b"]["c"].post(f"{API}/webhooks/{ha['id']}/test").status_code == 404
    assert world["b"]["c"].get(f"{API}/webhooks/deliveries").json()["total"] == 0
    assert len(world["o"]["c"].get(f"{API}/webhooks").json()) == 3
    assert world["a"]["c"].get(f"{API}/webhooks/deliveries").json()["total"] == 1
    # platform-only event and permissions
    r = world["a"]["c"].post(f"{API}/webhooks", json={"url": "https://hooks.example.com/z", "events": ["security.alert"]})
    assert r.status_code == 422
    assert world["s"]["c"].get(f"{API}/webhooks").status_code == 403
    assert world["a"]["c"].post(f"{API}/webhooks", json={"url": "https://hooks.example.com/z", "events": ["bogus.event"]}).status_code == 422
    assert world["a"]["c"].post(f"{API}/webhooks", json={"url": "https://hooks.example.com/z", "events": []}).status_code == 422
    assert world["a"]["c"].post(f"{API}/webhooks", json={"url": "https://hooks.example.com/z", "events": ["ping"], "tenant_id": str(world["b"]["t"].id)}).status_code == 404
    assert world["a"]["c"].patch(f"{API}/webhooks/{ha['id']}", json={"status": "disabled"}).status_code == 200
    world["a"]["c"].post(f"{API}/support/tickets", json={"subject": "second one", "body": "x"})
    assert db.scalar(select(__import__("sqlalchemy").func.count()).select_from(WebhookDelivery).where(WebhookDelivery.endpoint_id == __import__("uuid").UUID(ha["id"]))) == 1


def test_endpoint_limit(world):
    for i in range(10):
        mk_hook(world["a"]["c"], url=f"https://hooks.example.com/{i}")
    assert world["a"]["c"].post(f"{API}/webhooks", json={"url": "https://hooks.example.com/11", "events": ["ping"]}).status_code == 409


def test_event_catalog_and_scope_catalog(world):
    r = world["a"]["c"].get(f"{API}/developer/events").json()
    types = {e["type"] for e in r["events"]}
    assert {"ping", "ticket.created", "ticket.sla_breached", "security.alert"} <= types and r["signature"]["header"] == "X-KVL-Signature"
    assert wh.validate_events(["*"]) == ["*"]
    assert world["a"]["c"].get(f"{API}/developer/scopes").status_code == 200


# ---------------------------------------------------------------- idempotency
def test_idempotency_replay(world, db):
    a = world["a"]["c"]
    body = {"subject": "Only once please", "body": "x"}
    h = {"Idempotency-Key": "key-0001-abcdef"}
    r1 = a.post(f"{API}/support/tickets", json=body, headers=h)
    r2 = a.post(f"{API}/support/tickets", json=body, headers=h)
    assert r1.status_code == r2.status_code == 201 and r1.json()["id"] == r2.json()["id"]
    assert r2.headers["idempotent-replayed"] == "true" and "idempotent-replayed" not in r1.headers
    assert db.scalar(select(__import__("sqlalchemy").func.count()).select_from(Ticket)) == 1
    r3 = a.post(f"{API}/support/tickets", json={**body, "subject": "Different payload"}, headers=h)
    assert r3.status_code == 422 and r3.json()["error"]["code"] == "idempotency_key_reuse"
    # keys are scoped per user
    r4 = world["b"]["c"].post(f"{API}/support/tickets", json=body, headers=h)
    assert r4.status_code == 201 and r4.json()["id"] != r1.json()["id"]
    # no key -> no dedupe
    assert a.post(f"{API}/support/tickets", json=body).json()["id"] != r1.json()["id"]
    assert a.post(f"{API}/support/tickets", json=body, headers={"Idempotency-Key": "short"}).status_code == 422
    # failed requests are not cached: fix the problem and retry with the same key
    bad = a.post(f"{API}/support/tickets", json={**body, "tenant_id": str(world["b"]["t"].id)}, headers={"Idempotency-Key": "key-0002-abcdef"})
    assert bad.status_code == 404
    assert a.post(f"{API}/support/tickets", json=body, headers={"Idempotency-Key": "key-0002-abcdef"}).status_code == 201


def test_signature_helpers_constant_time_shape():
    s = wh.sign_payload("k", 1700000000, "{}")
    assert len(s) == 64 and s == wh.sign_payload("k", 1700000000, "{}") and s != wh.sign_payload("k", 1700000001, "{}")
    assert wh.verify_signature("k", f"t=1700000000,v1={s}", "{}", now_ts=1700000100)
    assert time.time() > 0
