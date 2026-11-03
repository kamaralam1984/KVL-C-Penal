import httpx
import pytest
from tests.conftest import login
from tests.p9_helpers import FakeCF, add_ref

from app.core.security import decrypt_secret
from app.models.ssl import CloudflareConnection
from app.services import cloudflare as cfmod
from app.services.cloudflare import CloudflareAuthError, CloudflareClient, CloudflareError, CloudflareRateLimited

TOKEN = "cf_scoped_token_ABCDEFGHIJ1234567890xyz"


def handler_for(responses):
    calls = []

    def h(request: httpx.Request):
        calls.append(request)
        r = responses.pop(0) if len(responses) > 1 else responses[0]
        return r(request) if callable(r) else r

    return h, calls


def ok(result, **info):
    return httpx.Response(200, json={"success": True, "result": result, "result_info": info or None})


def client(h, **kw):
    return CloudflareClient(TOKEN, base_url="https://cf.test/client/v4", transport=httpx.MockTransport(h), sleep=lambda s: None, **kw)


def test_client_sends_bearer_token_and_lists_zones_paginated():
    seen = []

    def h(req):
        seen.append(req)
        page = int(req.url.params["page"])
        return ok([{"id": f"z{page}", "name": f"S{page}.com", "status": "active"}], total_pages=2)

    c = client(h)
    zs = c.list_zones()
    assert [z["name"] for z in zs] == ["s1.com", "s2.com"]
    assert all(r.headers["authorization"] == f"Bearer {TOKEN}" for r in seen)


def test_client_rate_limit_retries_then_succeeds():
    n = {"i": 0}
    sleeps = []

    def h(req):
        n["i"] += 1
        if n["i"] < 3:
            return httpx.Response(429, headers={"Retry-After": "2"}, json={"success": False})
        return ok({"status": "active"})

    c = CloudflareClient(TOKEN, base_url="https://cf.test", transport=httpx.MockTransport(h), sleep=sleeps.append)
    assert c.verify_token()["status"] == "active"
    assert sleeps == [2.0, 2.0]


def test_client_rate_limit_gives_up():
    c = client(lambda r: httpx.Response(429, headers={"Retry-After": "30"}, json={}))
    with pytest.raises(CloudflareRateLimited) as e:
        c.verify_token()
    assert e.value.retry_after == 30


def test_client_permission_errors():
    c = client(lambda r: httpx.Response(403, json={"success": False, "errors": [{"message": "Authentication error"}]}))
    with pytest.raises(CloudflareAuthError) as e:
        c.list_zones()
    assert e.value.code == "permission_denied"
    c = client(lambda r: httpx.Response(401, json={"success": False, "errors": [{"message": "Invalid token"}]}))
    with pytest.raises(CloudflareAuthError) as e:
        c.verify_token()
    assert e.value.code == "invalid_token"


def test_client_api_and_network_errors():
    c = client(lambda r: httpx.Response(400, json={"success": False, "errors": [{"message": "bad record"}]}))
    with pytest.raises(CloudflareError) as e:
        c.create_record("z", {})
    assert e.value.code == "api_error" and "bad record" in e.value.message

    def boom(req):
        raise httpx.ConnectError("down")

    with pytest.raises(CloudflareError) as e:
        client(boom).verify_token()
    assert e.value.code == "network_error"


@pytest.fixture
def cfenv(monkeypatch, admin, client, make_user, db):
    u, t = make_user("cust@example.com", "customer")
    fake = FakeCF()
    monkeypatch.setattr(cfmod, "client_factory", lambda token: fake)
    return {"admin": client, "tenant": t, "fake": fake}


def put(env, token=TOKEN, **kw):
    return env["admin"].put("/api/v1/cloudflare/connection", json={"token": token, "tenant_id": str(env["tenant"].id), **kw})


def test_connect_encrypts_token_and_never_returns_it(cfenv, db):
    r = put(cfenv, label="main")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["status"] == "connected" and body["zones"][0]["name"] == "example.com" and TOKEN not in r.text
    row = db.query(CloudflareConnection).one()
    assert row.token_enc != TOKEN and TOKEN not in row.token_enc and decrypt_secret(row.token_enc) == TOKEN
    g = cfenv["admin"].get(f"/api/v1/cloudflare/connection?tenant_id={cfenv['tenant'].id}")
    assert TOKEN not in g.text and "token_enc" not in g.text and g.json()["fingerprint"]
    from app.models.identity import AuditEvent

    assert any(e.action == "cloudflare.connected" for e in db.query(AuditEvent).all())
    assert all(TOKEN not in str(e.detail) for e in db.query(AuditEvent).all())


def test_rejects_global_api_key_and_garbage(cfenv):
    assert put(cfenv, token="a" * 37).status_code == 422
    assert put(cfenv, token="not a token with spaces!!!!").status_code == 422


def test_token_permission_failure(cfenv, db):
    cfenv["fake"].fail_with = CloudflareAuthError("permission_denied", "needs Zone:Read", 403)
    r = put(cfenv)
    assert r.status_code == 403 and r.json()["error"]["code"] == "permission_denied"
    assert db.query(CloudflareConnection).count() == 0


def test_invalid_token_and_rate_limit_surface_cleanly(cfenv):
    cfenv["fake"].fail_with = CloudflareAuthError("invalid_token", "bad", 401)
    assert put(cfenv).status_code == 422
    cfenv["fake"].fail_with = CloudflareRateLimited("rate_limited", "slow down", 429, 7)
    r = put(cfenv)
    assert r.status_code == 429 and r.headers["retry-after"] == "7"


def test_recheck_marks_permission_denied_and_zones_limited(cfenv):
    assert put(cfenv).status_code == 200
    cfenv["fake"].fail_with = CloudflareAuthError("permission_denied", "revoked", 403)
    r = cfenv["admin"].post(f"/api/v1/cloudflare/connection/check?tenant_id={cfenv['tenant'].id}")
    assert r.status_code == 403
    g = cfenv["admin"].get(f"/api/v1/cloudflare/connection?tenant_id={cfenv['tenant'].id}").json()
    assert g["status"] == "permission_denied"


def test_zone_discovery_limited_to_token(cfenv):
    cfenv["fake"].zones = [{"id": "z9", "name": "only-this.org", "status": "active"}]
    put(cfenv)
    z = cfenv["admin"].get(f"/api/v1/cloudflare/zones?tenant_id={cfenv['tenant'].id}").json()
    assert [x["name"] for x in z["items"]] == ["only-this.org"]


def test_not_configured_state(cfenv):
    g = cfenv["admin"].get(f"/api/v1/cloudflare/connection?tenant_id={cfenv['tenant'].id}").json()
    assert g["status"] == "not_configured" and g["configured"] is False


def test_only_owner_level_can_connect(make_client, make_user, db):
    make_user("ha@kvl.example.com", "hosting_admin")
    u, t = make_user("c2@example.com", "customer")
    c = make_client()
    login(c, "ha@kvl.example.com")
    assert c.put("/api/v1/cloudflare/connection", json={"token": TOKEN, "tenant_id": str(t.id)}).status_code == 403
    c2 = make_client()
    login(c2, "c2@example.com")
    assert c2.put("/api/v1/cloudflare/connection", json={"token": TOKEN}).status_code == 403
    assert c2.get("/api/v1/cloudflare/connection").status_code == 200  # read-only


def test_cross_tenant_connection_isolated(cfenv, make_client, make_user):
    put(cfenv)
    u, t2 = make_user("other@example.com", "customer")
    c = make_client()
    login(c, "other@example.com")
    r = c.get("/api/v1/cloudflare/connection")
    assert r.json()["status"] == "not_configured"
    assert c.get(f"/api/v1/cloudflare/connection?tenant_id={cfenv['tenant'].id}").status_code == 404
