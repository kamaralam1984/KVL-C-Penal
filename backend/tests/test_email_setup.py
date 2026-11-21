"""Email setup plan: provider records, status against mirrored DNS, SPF merge conflict, ownership, validation."""
from app.models.ssl import DnsRecord
from tests.hosting_helpers import tenant_client
from tests.p9_helpers import add_ref

API = "/api/v1/email-setup"


def rec(db, t, rtype, name, content, prio=None):
    db.add(DnsRecord(tenant_id=t.id, domain="example.com", rtype=rtype, name=name, content=content, ttl=1, priority=prio))
    db.commit()


def test_zoho_plan_missing_then_ok(make_user, make_client, db):
    c, _, t = tenant_client(make_user, make_client, "a@x.com")
    add_ref(db, t, "domain", "example.com")
    r = c.get(f"{API}/example.com", params={"provider": "zoho_in"})
    assert r.status_code == 200, r.text
    p = r.json()
    assert [x["content"] for x in p["records"] if x["type"] == "MX"] == ["mx.zoho.in", "mx2.zoho.in", "mx3.zoho.in"]
    assert all(x["state"] == "missing" for x in p["records"]) and not p["ready"]
    for host, prio in (("mx.zoho.in", 10), ("mx2.zoho.in", 20), ("mx3.zoho.in", 50)):
        rec(db, t, "MX", "example.com", host, prio)
    rec(db, t, "TXT", "example.com", '"v=spf1 include:zohomail.in ~all"')
    rec(db, t, "TXT", "_dmarc.example.com", "v=DMARC1; p=quarantine")
    p = c.get(f"{API}/example.com", params={"provider": "zoho_in"}).json()
    assert p["ready"] and all(x["state"] == "ok" for x in p["records"])


def test_existing_spf_is_a_conflict_not_a_second_record(make_user, make_client, db):
    c, _, t = tenant_client(make_user, make_client, "a@x.com")
    add_ref(db, t, "domain", "example.com")
    rec(db, t, "TXT", "example.com", "v=spf1 include:_spf.google.com ~all")
    rec(db, t, "MX", "example.com", "old.mail.example.net", 5)
    p = c.get(f"{API}/example.com", params={"provider": "zoho_in"}).json()
    spf = next(x for x in p["records"] if x["key"] == "spf")
    assert spf["state"] == "conflict" and spf["action"] == "update" and spf["record_id"]
    assert p["other_mx"] == ["old.mail.example.net"] and not p["ready"]


def test_microsoft_and_dkim(make_user, make_client, db):
    c, _, t = tenant_client(make_user, make_client, "a@x.com")
    add_ref(db, t, "domain", "example.com")
    p = c.get(f"{API}/example.com", params={"provider": "microsoft"}).json()
    assert p["records"][0]["content"] == "example-com.mail.protection.outlook.com"
    assert any(x["key"] == "autodiscover" for x in p["records"])
    dk = "v=DKIM1; k=rsa; p=MIIBIjANBgkqhkiG9w0BAQEFAAOCAQ8AMIIBCgKCAQEA" + "A" * 40
    p = c.get(f"{API}/example.com", params={"provider": "google", "dkim_value": dk}).json()
    d = next(x for x in p["records"] if x["key"] == "dkim")
    assert d["name"] == "google._domainkey.example.com"
    assert c.get(f"{API}/example.com", params={"provider": "google", "dkim_value": "evil\"; drop"}).status_code == 422
    assert c.get(f"{API}/example.com", params={"provider": "google", "dkim_selector": "../x", "dkim_value": dk}).status_code == 422
    assert c.get(f"{API}/example.com", params={"provider": "sendmail"}).status_code == 422


def test_domain_ownership(make_user, make_client, db):
    a, _, ta = tenant_client(make_user, make_client, "a@x.com")
    b, _, _ = tenant_client(make_user, make_client, "b@x.com")
    add_ref(db, ta, "domain", "example.com")
    assert b.get(f"{API}/example.com", params={"provider": "google"}).status_code == 404
    assert a.get(f"{API}/not-mine.com", params={"provider": "google"}).status_code == 404
    assert len(a.get(f"{API}/providers").json()) == 4
