import pytest
from sqlalchemy import select

from app.models.identity import AuditEvent, EmailOutbox
from app.models.ssl import DnsChange, DnsRecord
from app.services import cloudflare as cfmod
from app.services import dns as dnsmod
from app.services.dns import validate_record
from tests.conftest import login
from tests.p9_helpers import BASE_RECORDS, FakeCF, FakeResolver, add_ref

TOKEN = "cf_scoped_token_ABCDEFGHIJ1234567890xyz"
API = "/api/v1"


@pytest.fixture
def env(monkeypatch, admin, client, make_user, db):
    u, t = make_user("cust@example.com", "customer")
    t.contact_email = "cust@example.com"
    db.commit()
    add_ref(db, t, "domain", "example.com")
    fake = FakeCF(records=BASE_RECORDS)
    monkeypatch.setattr(cfmod, "client_factory", lambda token: fake)
    q = f"tenant_id={t.id}"
    assert client.put(f"{API}/cloudflare/connection", json={"token": TOKEN, "tenant_id": str(t.id)}).status_code == 200
    r = client.post(f"{API}/dns/example.com/sync?{q}")
    assert r.status_code == 200, r.text
    ids = {x["name"] + "|" + x["type"] + "|" + x["content"]: x["id"] for x in r.json()["records"]}
    return {"c": client, "t": t, "fake": fake, "q": q, "ids": ids, "u": u}


def rid(env, name, rtype, content=None):
    for k, v in env["ids"].items():
        n, t, c = k.split("|", 2)
        if n == name and t == rtype and (content is None or c == content):
            return v
    raise KeyError(name)


def preview(env, action, record=None, record_id=None, **kw):
    return env["c"].post(f"{API}/dns/example.com/changes?{env['q']}", json={"action": action, "record": record, "record_id": record_id, **kw})


def confirm(env, cid, **kw):
    return env["c"].post(f"{API}/dns/changes/{cid}/confirm", json={"confirm": True, "confirm_production": True, **kw})


# ---------------------------------------------------------------- validation per type


@pytest.mark.parametrize("rtype,name,content,extra", [
    ("A", "app", "203.0.113.7", {}), ("AAAA", "app", "2606:4700::1111", {}), ("CNAME", "shop", "shops.example.net.", {}),
    ("TXT", "@", "hello world", {}), ("MX", "@", "mx.example.net", {"priority": 5}), ("NS", "sub", "ns1.example.net", {}),
    ("CAA", "@", '0 issue "letsencrypt.org"', {}), ("SRV", "_sip._tcp", "10 5060 sip.example.net", {"priority": 1}),
    ("TXT", "@", "v=spf1 include:_spf.example.net ~all", {}), ("A", "www", "203.0.113.7", {"proxied": True}),
])
def test_valid_records(rtype, name, content, extra):
    out = validate_record(rtype, name, content, extra.get("ttl", 300), extra.get("priority"), extra.get("proxied"), "example.com")
    assert out["type"] == rtype and out["name"].endswith("example.com")


@pytest.mark.parametrize("rtype,name,content,prio,ttl,proxied", [
    ("A", "x", "999.1.1.1", None, 300, None), ("A", "x", "10.0.0.1", None, 300, None), ("A", "x", "127.0.0.1", None, 300, None), ("A", "x", "2001:db8::1", None, 300, None),
    ("AAAA", "x", "1.2.3.4", None, 300, None), ("AAAA", "x", "::1", None, 300, None),
    ("CNAME", "@", "a.example.net", None, 300, None), ("CNAME", "x", "1.2.3.4", None, 300, None), ("CNAME", "x", "x.example.com", None, 300, None),
    ("MX", "@", "mx.example.net", None, 300, None), ("MX", "@", "1.2.3.4", 10, 300, None), ("MX", "@", "mx.example.net", 70000, 300, None),
    ("NS", "@", "ns1.example.net", None, 300, None), ("NS", "x", "bad host", None, 300, None),
    ("CAA", "@", "issue letsencrypt.org", None, 300, None), ("CAA", "@", '0 bogus "x"', None, 300, None), ("CAA", "@", '0 iodef "ftp://x"', None, 300, None),
    ("SRV", "sip", "10 5060 sip.example.net", 1, 300, None), ("SRV", "_sip._tcp", "10 99999 sip.example.net", 1, 300, None), ("SRV", "_sip._tcp", "10 5060", 1, 300, None), ("SRV", "_sip._tcp", "10 5060 sip.example.net", None, 300, None),
    ("TXT", "@", "x" * 2049, None, 300, None), ("TXT", "@", "bad\x00", None, 300, None), ("TXT", "@", "v=spf1 include:a.com", None, 300, None),
    ("TXT", "_dmarc", "p=none", None, 300, None), ("TXT", "@", "ok", None, 30, None), ("TXT", "@", "ok", None, 300, True),
    ("PTR", "@", "x.example.net", None, 300, None), ("A", "bad_label!", "203.0.113.7", None, 300, None), ("A", "a.*.b", "203.0.113.7", None, 300, None), ("A", "x", "", None, 300, None),
])
def test_invalid_records(rtype, name, content, prio, ttl, proxied):
    with pytest.raises(Exception) as e:
        validate_record(rtype, name, content, ttl, prio, proxied, "example.com")
    assert getattr(e.value, "status_code", None) == 422


def test_name_normalisation_and_foreign_names():
    assert validate_record("A", "WWW", "203.0.113.7", 1, None, None, "example.com")["name"] == "www.example.com"
    assert validate_record("A", "www.example.com.", "203.0.113.7", 1, None, None, "example.com")["name"] == "www.example.com"
    # a name outside the zone is treated as a sub-label of the zone, never as another domain
    assert validate_record("A", "evil.com", "203.0.113.7", 1, None, None, "example.com")["name"] == "evil.com.example.com"


# ---------------------------------------------------------------- workflow
def test_records_listed_with_proxy_status(env):
    r = env["c"].get(f"{API}/dns/example.com/records?{env['q']}").json()
    assert r["status"] == "connected" and r["zone"]["id"] == "z1" and len(r["records"]) == len(BASE_RECORDS)
    www = [x for x in r["records"] if x["name"] == "www.example.com"][0]
    assert www["proxied"] is True


def test_preview_changes_nothing(env):
    r = preview(env, "create", {"type": "A", "name": "new", "content": "203.0.113.50", "ttl": 300})
    assert r.status_code == 201, r.text
    b = r.json()
    assert b["status"] == "previewed" and b["before"] is None and b["after"]["name"] == "new.example.com" and b["rollback_instructions"] and b["unchanged_record_count"] == len(BASE_RECORDS)
    assert env["fake"].calls == []


def test_confirm_requires_flags_and_owner(env, make_client, make_user, db):
    cid = preview(env, "create", {"type": "A", "name": "new", "content": "203.0.113.50", "ttl": 300}).json()["id"]
    assert env["c"].post(f"{API}/dns/changes/{cid}/confirm", json={}).status_code == 400
    r = env["c"].post(f"{API}/dns/changes/{cid}/confirm", json={"confirm": True})
    assert r.status_code == 400 and r.json()["error"]["code"] == "production_confirmation_required"
    assert env["fake"].calls == []
    # hosting admin has dns:write but not dns:manage -> cannot apply production changes
    make_user("ha@kvl.example.com", "hosting_admin")
    ha = make_client()
    login(ha, "ha@kvl.example.com")
    assert ha.post(f"{API}/dns/changes/{cid}/confirm", json={"confirm": True, "confirm_production": True}).status_code == 403
    assert env["fake"].calls == []


def test_customer_cannot_change_dns(env, make_client):
    c = make_client()
    login(c, "cust@example.com")
    assert c.get(f"{API}/dns/example.com/records").status_code == 200
    assert c.post(f"{API}/dns/example.com/changes", json={"action": "create", "record": {"type": "A", "name": "x", "content": "203.0.113.5"}}).status_code == 403
    assert c.post(f"{API}/dns/example.com/sync").status_code == 403


def test_create_applies_only_target_and_preserves_everything_else(env, db):
    before = {k: dict(v) for k, v in env["fake"].records.items()}
    cid = preview(env, "create", {"type": "A", "name": "app", "content": "203.0.113.50", "ttl": 300}).json()["id"]
    r = confirm(env, cid)
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "applied" and r.json()["verification"] == "pending"
    assert [c[0] for c in env["fake"].calls] == ["create"] and len(env["fake"].calls) == 1
    after = env["fake"].records
    assert {k: v for k, v in after.items() if k in before} == before  # every pre-existing record byte-identical
    assert len(after) == len(before) + 1
    mirror = {r.provider_record_id: r for r in db.scalars(select(DnsRecord))}
    assert len(mirror) == len(after)


def test_update_touches_only_target_mail_records_preserved(env, db):
    before = {k: dict(v) for k, v in env["fake"].records.items()}
    target = rid(env, "www.example.com", "A")
    cid = preview(env, "update", {"type": "A", "name": "www", "content": "203.0.113.99", "ttl": 300, "proxied": True}, record_id=target).json()
    assert [d["field"] for d in cid["diff"]] == ["content"]
    assert confirm(env, cid["id"]).status_code == 200
    assert [(c[0], c[1]) for c in env["fake"].calls] == [("update", "a2")]
    for k, v in before.items():
        if k != "a2":
            assert env["fake"].records[k] == v, f"record {k} changed"
    assert env["fake"].records["a2"]["content"] == "203.0.113.99"
    assert env["fake"].records["m1"]["content"] == "mail.example.com" and env["fake"].records["t1"]["content"].startswith('"v=spf1')


def test_delete_targets_one_record(env):
    before = dict(env["fake"].records)
    target = rid(env, "blog.example.com", "CNAME")
    cid = preview(env, "delete", record_id=target).json()["id"]
    assert confirm(env, cid).status_code == 200
    assert env["fake"].calls == [("delete", "c1", None)]
    assert set(before) - set(env["fake"].records) == {"c1"}


def test_mail_records_need_explicit_ack(env):
    mx = rid(env, "example.com", "MX", "mail.example.com")
    body = {"type": "MX", "name": "@", "content": "newmail.example.net", "priority": 10, "ttl": 3600}
    r = preview(env, "update", body, record_id=mx)
    assert r.status_code == 409 and r.json()["error"]["code"] == "protected_record"
    r2 = preview(env, "update", body, record_id=mx, protected_ack=True)
    assert r2.status_code == 201 and any("email" in w for w in r2.json()["warnings"])
    assert env["fake"].calls == []
    for name, t, c in [("_dmarc.example.com", "TXT", None), ("sel1._domainkey.example.com", "TXT", None), ("example.com", "TXT", None)]:
        assert preview(env, "delete", record_id=rid(env, name, t, c)).status_code == 409 if c is None and name != "example.com" else True
    spf = [v for k, v in env["ids"].items() if k.startswith("example.com|TXT|v=spf1")][0]
    assert preview(env, "delete", record_id=spf).json()["error"]["code"] == "protected_record"


def test_second_spf_and_dmarc_rejected(env):
    r = preview(env, "create", {"type": "TXT", "name": "@", "content": "v=spf1 -all", "ttl": 300}, protected_ack=True)
    assert r.status_code == 422 and "SPF" in r.text
    r = preview(env, "create", {"type": "TXT", "name": "_dmarc", "content": "v=DMARC1; p=none", "ttl": 300}, protected_ack=True)
    assert r.status_code == 422


def test_cname_conflicts_and_duplicates(env):
    assert preview(env, "create", {"type": "CNAME", "name": "www", "content": "x.example.net", "ttl": 300}).status_code == 422
    assert preview(env, "create", {"type": "A", "name": "blog", "content": "203.0.113.5", "ttl": 300}).status_code == 422
    assert preview(env, "create", {"type": "A", "name": "@", "content": "203.0.113.10", "ttl": 300}).status_code == 422
    assert preview(env, "delete", record_id=rid(env, "dev.example.com", "NS")).status_code == 201
    assert preview(env, "create", {"type": "NS", "name": "@", "content": "ns.example.net", "ttl": 300}).status_code == 422


def test_integrity_violation_detected_and_alerted(env, db):
    env["fake"].tamper = True  # simulate a provider/bug modifying an unrelated MX record
    cid = preview(env, "create", {"type": "A", "name": "app", "content": "203.0.113.50", "ttl": 300}).json()["id"]
    r = confirm(env, cid)
    assert r.status_code == 200 and r.json()["status"] == "violation"
    assert any(e.action == "dns.integrity_violation" for e in db.scalars(select(AuditEvent)))
    assert db.scalars(select(EmailOutbox).where(EmailOutbox.kind == "dns_integrity")).first() is not None
    assert env["c"].post(f"{API}/dns/changes/{cid}/rollback").status_code == 409  # not 'applied'


def test_stale_preview_rejected(env):
    target = rid(env, "www.example.com", "A")
    cid = preview(env, "update", {"type": "A", "name": "www", "content": "203.0.113.99", "ttl": 300, "proxied": True}, record_id=target).json()["id"]
    env["fake"].records["a2"]["content"] = "203.0.113.77"  # someone else changed it at the provider
    r = confirm(env, cid)
    assert r.status_code == 409 and r.json()["error"]["code"] == "stale_preview"
    assert env["fake"].calls == []


def test_no_provider_means_no_change(env, db, make_user, monkeypatch):
    # a domain whose zone the token does not cover cannot be changed (honest not_configured, nothing faked)
    u, t2 = make_user("two@example.com", "customer")
    add_ref(db, t2, "domain", "uncovered.org")
    r = env["c"].post(f"{API}/dns/uncovered.org/sync?tenant_id={t2.id}")
    assert r.status_code == 409 and r.json()["error"]["code"] == "not_configured"
    r = env["c"].get(f"{API}/dns/uncovered.org/records?tenant_id={t2.id}").json()
    assert r["status"] == "not_configured" and r["records"] == []


def test_provider_error_marks_failed_and_is_audited(env, db):
    cid = preview(env, "create", {"type": "A", "name": "app", "content": "203.0.113.50", "ttl": 300}).json()["id"]
    env["fake"].fail_with = cfmod.CloudflareAuthError("permission_denied", "token lacks DNS:Edit", 403)
    r = confirm(env, cid)
    assert r.status_code == 403
    assert db.scalars(select(DnsChange)).first().status == "failed"
    assert any(e.action == "dns.change_failed" for e in db.scalars(select(AuditEvent)))
    env["fake"].fail_with = cfmod.CloudflareRateLimited("rate_limited", "slow", 429, 3)
    cid2 = preview(env, "create", {"type": "A", "name": "app2", "content": "203.0.113.51", "ttl": 300}).json()["id"]
    assert confirm(env, cid2).status_code == 429


def test_audit_trail_before_after(env, db):
    cid = preview(env, "update", {"type": "A", "name": "www", "content": "203.0.113.99", "ttl": 300, "proxied": True}, record_id=rid(env, "www.example.com", "A")).json()["id"]
    confirm(env, cid)
    acts = [e.action for e in db.scalars(select(AuditEvent).order_by(AuditEvent.created_at))]
    for a in ("dns.change_previewed", "dns.change_confirmed", "dns.change_applied"):
        assert a in acts
    applied = [e for e in db.scalars(select(AuditEvent)) if e.action == "dns.change_applied"][0]
    assert applied.detail["before"]["content"] == "203.0.113.10" and applied.detail["after"]["content"] == "203.0.113.99" and applied.detail["production"] is True
    h = env["c"].get(f"{API}/dns/audit?domain=example.com&{env['q']}").json()["items"]
    assert {"dns.change_applied", "dns.change_previewed"} <= {x["action"] for x in h}


def test_verify_propagation_with_pluggable_resolver(env, monkeypatch):
    cid = preview(env, "create", {"type": "A", "name": "app", "content": "203.0.113.50", "ttl": 300}).json()["id"]
    confirm(env, cid)
    monkeypatch.setattr(dnsmod, "resolver_factory", lambda: FakeResolver({}))
    assert env["c"].post(f"{API}/dns/changes/{cid}/verify").json()["verification"] == "pending"
    monkeypatch.setattr(dnsmod, "resolver_factory", lambda: FakeResolver({("app.example.com", "A"): ["203.0.113.50"]}))
    v = env["c"].post(f"{API}/dns/changes/{cid}/verify").json()
    assert v["verification"] == "propagated" and v["verification_detail"]["observed"] == ["203.0.113.50"]
    monkeypatch.setattr(dnsmod, "resolver_factory", lambda: FakeResolver(fail=True))
    assert env["c"].post(f"{API}/dns/changes/{cid}/verify").json()["verification"] == "pending"


def test_verify_proxied_uses_provider_api(env, monkeypatch):
    cid = preview(env, "update", {"type": "A", "name": "www", "content": "203.0.113.99", "ttl": 1, "proxied": True}, record_id=rid(env, "www.example.com", "A")).json()["id"]
    confirm(env, cid)
    monkeypatch.setattr(dnsmod, "resolver_factory", lambda: FakeResolver({}))
    v = env["c"].post(f"{API}/dns/changes/{cid}/verify").json()
    assert v["verification"] == "propagated" and "provider_api" in v["verification_detail"]["via"]


def test_rollback_prepares_inverse_preview_only(env):
    target = rid(env, "www.example.com", "A")
    cid = preview(env, "update", {"type": "A", "name": "www", "content": "203.0.113.99", "ttl": 300, "proxied": True}, record_id=target).json()["id"]
    confirm(env, cid)
    n = len(env["fake"].calls)
    rb = env["c"].post(f"{API}/dns/changes/{cid}/rollback")
    assert rb.status_code == 201, rb.text
    b = rb.json()
    assert b["status"] == "previewed" and b["rollback_of"] == cid and b["after"]["content"] == "203.0.113.10"
    assert len(env["fake"].calls) == n
    assert confirm(env, b["id"]).status_code == 200
    assert env["fake"].records["a2"]["content"] == "203.0.113.10"


def test_create_rollback_deletes_created_record(env):
    cid = preview(env, "create", {"type": "A", "name": "tmp", "content": "203.0.113.60", "ttl": 300}).json()["id"]
    confirm(env, cid)
    rb = env["c"].post(f"{API}/dns/changes/{cid}/rollback").json()
    assert rb["action"] == "delete"
    confirm(env, rb["id"])
    assert not any(r["name"] == "tmp.example.com" for r in env["fake"].records.values())
    assert len(env["fake"].records) == len(BASE_RECORDS)


def test_cannot_confirm_twice(env):
    cid = preview(env, "create", {"type": "A", "name": "app", "content": "203.0.113.50", "ttl": 300}).json()["id"]
    assert confirm(env, cid).status_code == 200
    assert confirm(env, cid).status_code == 409
    assert len(env["fake"].calls) == 1


# ---------------------------------------------------------------- tenancy
def test_cross_tenant_dns_isolated(env, make_client, make_user, db):
    u2, t2 = make_user("evil@example.com", "customer")
    c2 = make_client()
    login(c2, "evil@example.com")
    assert c2.get(f"{API}/dns/example.com/records").status_code == 404
    assert c2.get(f"{API}/dns/example.com/records?tenant_id={env['t'].id}").status_code == 404
    cid = preview(env, "create", {"type": "A", "name": "app", "content": "203.0.113.50", "ttl": 300}).json()["id"]
    assert c2.get(f"{API}/dns/changes/{cid}").status_code == 404
    assert c2.post(f"{API}/dns/changes/{cid}/confirm", json={"confirm": True, "confirm_production": True}).status_code in (403, 404)
    assert c2.get(f"{API}/dns/audit").json()["items"] == []


def test_platform_staff_must_name_tenant(env):
    assert env["c"].get(f"{API}/dns/example.com/records").status_code == 400
