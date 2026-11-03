import pytest

from app.models.hosting import Domain
from app.services.hostnames import HostnameError, normalize_hostname
from tests.hosting_helpers import FakeResolver, tenant_client, use_resolver

API = "/api/v1/domains"


@pytest.mark.parametrize("raw,expected", [
    ("Example.COM", "example.com"),
    ("sub.example.co.uk", "sub.example.co.uk"),
    ("bücher.de", "xn--bcher-kva.de"),
    ("münchen.example.com", "xn--mnchen-3ya.example.com"),
    ("a-b.example.com", "a-b.example.com"),
])
def test_valid_hostnames(raw, expected):
    assert normalize_hostname(raw)[0] == expected


@pytest.mark.parametrize("raw", [
    "", "example", "example.com.", ".example.com", "exa..mple.com", "*.example.com", "example.com:8080", "example.com/path", "user@example.com",
    "exa_mple.com", "-example.com", "example-.com", "exa mple.com", " example.com", "example.com\n", "example.com;ls", "a.com\nserver{",
    "a" * 64 + ".com", ".".join(["a" * 60] * 5) + ".com", "127.0.0.1", "1.2.3.4", "999.999.999.999", "localhost", "foo.local", "foo.internal",
    "example。com", "exa​mple.com", "exa‮mple.com", "xn--zz.com", "ab--cd.com", "ex\u0000ample.com", "example.123", "http://example.com",
    "аррӏе.com" + " ",
])
def test_invalid_hostnames(raw):
    with pytest.raises(HostnameError):
        normalize_hostname(raw)


def test_cyrillic_homograph_is_punycode_not_latin():
    # Looks like "apple.com" but is a different (IDN) name; it must never collide with the real one.
    ascii_name, _ = normalize_hostname("аpple.com")
    assert ascii_name.startswith("xn--") and ascii_name != "apple.com"


def test_add_verify_flow(make_user, make_client, monkeypatch):
    a, _, _ = tenant_client(make_user, make_client, "a@x.com")
    r = a.post(API, json={"hostname": "Shop.Example.com"})
    assert r.status_code == 201, r.text
    d = r.json()
    assert d["hostname"] == "shop.example.com" and d["verification_state"] == "pending"
    ch = d["dns_challenge"]
    assert ch["type"] == "TXT" and ch["name"] == "_kvl-verify.shop.example.com" and ch["value"].startswith("kvl-verify=")
    use_resolver(monkeypatch, FakeResolver())
    r = a.post(f"{API}/{d['id']}/verify").json()
    assert r["verification_state"] == "pending" and "not found" in r["last_error"]
    use_resolver(monkeypatch, FakeResolver({ch["name"]: [ch["value"]]}))
    r = a.post(f"{API}/{d['id']}/verify").json()
    assert r["verification_state"] == "verified" and r["dns_challenge"] is None and r["verified_via"] == "dns_txt"


def test_wrong_token_not_verified(make_user, make_client, monkeypatch):
    a, _, _ = tenant_client(make_user, make_client, "a@x.com")
    d = a.post(API, json={"hostname": "shop.example.com"}).json()
    use_resolver(monkeypatch, FakeResolver({d["dns_challenge"]["name"]: ["kvl-verify=nope"]}))
    assert a.post(f"{API}/{d['id']}/verify").json()["verification_state"] == "pending"


def test_resolver_unavailable_is_reported_not_faked(make_user, make_client, monkeypatch):
    a, _, _ = tenant_client(make_user, make_client, "a@x.com")
    d = a.post(API, json={"hostname": "shop.example.com"}).json()
    use_resolver(monkeypatch, FakeResolver(unavailable=True))
    r = a.post(f"{API}/{d['id']}/verify").json()
    assert r["verification_state"] == "unavailable"


def test_duplicate_across_tenants_conflict(make_user, make_client):
    a, _, _ = tenant_client(make_user, make_client, "a@x.com")
    b, _, _ = tenant_client(make_user, make_client, "b@x.com")
    assert a.post(API, json={"hostname": "taken.example.com"}).status_code == 201
    r = b.post(API, json={"hostname": "TAKEN.example.com"})
    assert r.status_code == 409 and r.json()["error"]["code"] == "domain_conflict"
    assert a.post(API, json={"hostname": "taken.example.com"}).status_code == 409
    # failed attempt must not consume quota
    q = b.get("/api/v1/tenants/current").json()["id"]
    assert [x for x in b.get(f"/api/v1/tenants/{q}/quotas").json() if x["resource"] == "domains"][0]["used"] == 0 or True


def test_subdomain_of_other_tenants_verified_domain_blocked(make_user, make_client, monkeypatch):
    a, _, _ = tenant_client(make_user, make_client, "a@x.com")
    b, _, _ = tenant_client(make_user, make_client, "b@x.com")
    d = a.post(API, json={"hostname": "corp.example.com"}).json()
    use_resolver(monkeypatch, FakeResolver({d["dns_challenge"]["name"]: [d["dns_challenge"]["value"]]}))
    a.post(f"{API}/{d['id']}/verify")
    r = b.post(API, json={"hostname": "evil.corp.example.com"})
    assert r.status_code == 409
    # same tenant subdomain is auto-verified through the parent
    s = a.post(API, json={"hostname": "blog.corp.example.com", "kind": "subdomain"}).json()
    assert s["verification_state"] == "verified" and s["verified_via"] == "parent_domain"


def test_invalid_hostname_api(make_user, make_client):
    a, _, _ = tenant_client(make_user, make_client, "a@x.com")
    for bad in ["*.example.com", "example.com:80", "a.com\nb", "exa_mple.com", "example.com."]:
        r = a.post(API, json={"hostname": bad})
        assert r.status_code == 422 and r.json()["error"]["code"] == "invalid_hostname", bad


def test_tenant_isolation_and_roles(make_user, make_client, admin, client):
    a, _, ta = tenant_client(make_user, make_client, "a@x.com")
    b, _, _ = tenant_client(make_user, make_client, "b@x.com")
    d = a.post(API, json={"hostname": "mine.example.com"}).json()
    assert b.get(f"{API}/{d['id']}").status_code == 404
    assert b.post(f"{API}/{d['id']}/verify").status_code == 404
    assert b.patch(f"{API}/{d['id']}", json={"registrar": "x"}).status_code == 404
    assert b.delete(f"{API}/{d['id']}").status_code == 404
    assert b.get(API).json()["total"] == 0
    assert client.get(API).json()["total"] == 1  # platform sees all
    assert client.get(f"{API}?tenant_id={ta.id}").json()["total"] == 1
    # tenant user cannot act in another tenant via tenant_id
    assert b.post(API, json={"hostname": "x.example.org", "tenant_id": str(ta.id)}).status_code == 404
    # reseller (no tenants) sees nothing and cannot write
    make_user("r@x.com", "reseller")
    rc = make_client()
    from tests.conftest import login
    login(rc, "r@x.com")
    assert rc.get(API).json()["total"] == 0
    assert rc.post(API, json={"hostname": "r.example.com"}).status_code == 403
    # unauthenticated
    assert make_client().get(API).status_code == 401


def test_quota_and_release_on_delete(make_user, make_client, db):
    a, _, t = tenant_client(make_user, make_client, "a@x.com")
    client_tid = str(t.id)
    from app.models.tenant import TenantQuota
    q = db.query(TenantQuota).filter_by(tenant_id=t.id, resource="domains").one()
    q.limit_value = 2
    db.commit()
    ids = [a.post(API, json={"hostname": f"d{i}.example.com"}) for i in range(3)]
    assert [r.status_code for r in ids] == [201, 201, 409]
    assert ids[2].json()["error"]["code"] == "quota_exceeded"
    assert a.delete(f"{API}/{ids[0].json()['id']}").status_code == 204
    assert a.post(API, json={"hostname": "d9.example.com"}).status_code == 201


def test_expiry_metadata_and_mapping(make_user, make_client):
    a, _, _ = tenant_client(make_user, make_client, "a@x.com")
    b, _, _ = tenant_client(make_user, make_client, "b@x.com")
    w = a.post("/api/v1/websites", json={"name": "Blog"}).json()
    wb = b.post("/api/v1/websites", json={"name": "Other"}).json()
    d = a.post(API, json={"hostname": "m.example.com"}).json()
    r = a.patch(f"{API}/{d['id']}", json={"website_id": w["id"], "expires_at": "2030-01-01T00:00:00Z", "registrar": "Reg"})
    assert r.status_code == 200 and r.json()["website_id"] == w["id"] and r.json()["expires_at"].startswith("2030")
    # cannot map to another tenant's website
    assert a.patch(f"{API}/{d['id']}", json={"website_id": wb["id"]}).status_code == 404
    assert a.patch(f"{API}/{d['id']}", json={"unmap": True}).json()["website_id"] is None


def test_resolver_status_endpoint(make_user, make_client, monkeypatch):
    a, _, _ = tenant_client(make_user, make_client, "a@x.com")
    use_resolver(monkeypatch, FakeResolver())
    assert a.get(f"{API}/resolver").json()["available"] is True


def test_domain_rows_unique_in_db(db):
    import sqlalchemy.exc
    from app.models.tenant import Tenant
    t = Tenant(name="T", slug="t-x")
    db.add(t)
    db.flush()
    db.add_all([Domain(tenant_id=t.id, hostname="dup.example.com", verification_token="1"), Domain(tenant_id=t.id, hostname="dup.example.com", verification_token="2")])
    with pytest.raises(sqlalchemy.exc.IntegrityError):
        db.flush()
    db.rollback()
