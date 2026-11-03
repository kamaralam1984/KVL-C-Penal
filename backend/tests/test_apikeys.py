from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select

from app.core.rbac import ROLE_DEFS
from app.models.identity import AuditEvent
from app.models.security import ApiKey
from tests.conftest import login

API = "/api/v1"


@pytest.fixture
def world(make_user, make_client):
    w = {}
    for key, email, role in [("a", "a@x.example.com", "customer"), ("b", "b@y.example.com", "customer"), ("s", "sup@kvl.example.com", "support"), ("o", "owner@kvl.example.com", "super_admin")]:
        u, t = make_user(email, role)
        c = make_client()
        assert login(c, email).status_code == 200
        w[key] = {"u": u, "t": t, "c": c, "email": email}
    return w


def mk_key(c, scopes=("tickets:read",), **kw):
    r = c.post(f"{API}/apikeys", json={"name": "ci key", "scopes": list(scopes), **kw})
    assert r.status_code == 201, r.text
    return r.json()


def bearer(make_client, token):
    c = make_client()
    c.headers["Authorization"] = f"Bearer {token}"
    return c


def test_key_shown_once_and_only_hash_stored(world, db):
    k = mk_key(world["a"]["c"])
    token = k["key"]
    assert token.startswith("kvl_") and len(token.split("_", 2)[2]) >= 43  # 256-bit urlsafe secret
    assert "key" not in world["a"]["c"].get(f"{API}/apikeys").json()[0]
    assert token not in world["a"]["c"].get(f"{API}/apikeys").text
    row = db.scalar(select(ApiKey))
    secret = token.split("_", 2)[2]
    import hashlib

    assert row.secret_hash == hashlib.sha256(secret.encode()).hexdigest()
    cols = " ".join(str(getattr(row, c.name)) for c in ApiKey.__table__.columns)
    assert secret not in cols and token not in cols
    for e in db.scalars(select(AuditEvent)):
        assert secret not in str(e.detail)
    assert k["prefix"] == f"kvl_{row.lookup}"


def test_key_authenticates_and_scope_enforced(world, make_client):
    k = mk_key(world["a"]["c"], ["tickets:read"])
    c = bearer(make_client, k["key"])
    assert c.get(f"{API}/support/tickets").status_code == 200
    r = c.post(f"{API}/support/tickets", json={"subject": "abc", "body": "x"})  # tickets:write not granted to this key
    assert r.status_code == 403 and r.json()["error"]["code"] in {"forbidden", "scope_missing"}
    assert c.get(f"{API}/kb/articles").status_code == 403
    k2 = mk_key(world["a"]["c"], ["tickets:read", "tickets:write"])
    c2 = bearer(make_client, k2["key"])
    assert c2.post(f"{API}/support/tickets", json={"subject": "via api", "body": "x"}).status_code == 201
    assert world["a"]["c"].get(f"{API}/apikeys").json()[0]["request_count"] >= 1


def test_scopes_cannot_exceed_creator_permissions(world):
    a, s = world["a"]["c"], world["s"]["c"]
    for bad in (["servers:read"], ["tickets:manage"], ["tickets:read", "websites:delete"], ["apikeys:write"], ["roles:manage"]):
        r = a.post(f"{API}/apikeys", json={"name": "bad", "scopes": bad})
        assert r.status_code == 403 and r.json()["error"]["code"] == "scope_exceeds_permissions", bad
    assert a.post(f"{API}/apikeys", json={"name": "bad", "scopes": ["nope:read"]}).status_code == 422
    assert a.post(f"{API}/apikeys", json={"name": "bad", "scopes": []}).status_code == 422
    # support has no apikeys:write at all
    assert s.post(f"{API}/apikeys", json={"name": "x", "scopes": ["tickets:read"]}).status_code == 403
    assert world["o"]["c"].post(f"{API}/apikeys", json={"name": "ok", "scopes": ["servers:read", "tickets:manage"]}).status_code == 201


def test_scope_cannot_exceed_after_creator_loses_permission(world, make_client, db):
    k = mk_key(world["a"]["c"], ["tickets:read", "websites:read"])
    c = bearer(make_client, k["key"])
    assert c.get(f"{API}/support/tickets").status_code == 200
    from app.models.identity import Permission, Role, RolePermission
    from app.core.deps import invalidate_role_cache

    role = db.scalar(select(Role).where(Role.name == "customer"))
    perm = db.scalar(select(Permission).where(Permission.code == "tickets:read"))
    link = db.get(RolePermission, (role.id, perm.id))
    db.delete(link)
    db.commit()
    invalidate_role_cache()
    try:
        assert c.get(f"{API}/support/tickets").status_code == 403  # effective = role ∩ scopes
    finally:  # role tables are not truncated between tests
        db.add(RolePermission(role_id=role.id, permission_id=perm.id))
        db.commit()
        invalidate_role_cache()


def test_revoked_expired_and_unknown_rejected(world, make_client, db):
    a = world["a"]["c"]
    k = mk_key(a)
    c = bearer(make_client, k["key"])
    assert c.get(f"{API}/support/tickets").status_code == 200
    assert a.delete(f"{API}/apikeys/{k['id']}").status_code == 200
    assert c.get(f"{API}/support/tickets").status_code == 401
    k2 = mk_key(a)
    row = db.get(ApiKey, __import__("uuid").UUID(k2["id"]))
    row.expires_at = datetime.now(UTC) - timedelta(seconds=1)
    db.commit()
    assert bearer(make_client, k2["key"]).get(f"{API}/support/tickets").status_code == 401
    assert [x["status"] for x in a.get(f"{API}/apikeys").json()] == ["expired", "revoked"]
    for bad in ("kvl_abcdef123456_" + "x" * 43, "kvl_", "kvl_aaaaaaaaaaaa", "kvl__", f"{k2['key']}x"):
        assert bearer(make_client, bad).get(f"{API}/support/tickets").status_code == 401, bad
    # tampering with the lookup part or the secret fails; right lookup + wrong secret fails
    lookup = k["key"].split("_")[1]
    assert bearer(make_client, f"kvl_{lookup}_{'y' * 43}").get(f"{API}/support/tickets").status_code == 401
    # disabled owner stops working
    k3 = mk_key(a)
    from app.models.identity import User

    u = db.get(User, world["a"]["u"].id)
    u.status = "disabled"
    db.commit()
    assert bearer(make_client, k3["key"]).get(f"{API}/support/tickets").status_code == 401


def test_rotation_revokes_old_key(world, make_client):
    a = world["a"]["c"]
    k = mk_key(a, ["tickets:read"], expires_in_days=30)
    r = a.post(f"{API}/apikeys/{k['id']}/rotate")
    assert r.status_code == 201
    new = r.json()
    assert new["key"] != k["key"] and new["id"] != k["id"] and new["scopes"] == ["tickets:read"] and datetime.fromisoformat(new["expires_at"]) == datetime.fromisoformat(k["expires_at"])
    assert bearer(make_client, k["key"]).get(f"{API}/support/tickets").status_code == 401
    assert bearer(make_client, new["key"]).get(f"{API}/support/tickets").status_code == 200
    assert a.post(f"{API}/apikeys/{k['id']}/rotate").status_code == 409  # old one is revoked
    assert world["b"]["c"].post(f"{API}/apikeys/{new['id']}/rotate").status_code == 404
    assert world["b"]["c"].delete(f"{API}/apikeys/{new['id']}").status_code == 404


def test_per_key_rate_limit(world, make_client):
    k = mk_key(world["a"]["c"], ["tickets:read"], rate_limit_per_minute=3)
    c = bearer(make_client, k["key"])
    codes = [c.get(f"{API}/support/tickets").status_code for _ in range(5)]
    assert codes == [200, 200, 200, 429, 429]
    r = c.get(f"{API}/support/tickets")
    assert r.headers["retry-after"] and r.json()["error"]["code"] == "rate_limited"
    # another key is unaffected
    other = bearer(make_client, mk_key(world["a"]["c"], ["tickets:read"])["key"])
    assert other.get(f"{API}/support/tickets").status_code == 200
    assert world["a"]["c"].post(f"{API}/apikeys", json={"name": "x", "scopes": ["tickets:read"], "rate_limit_per_minute": 100000}).status_code == 422


def test_failed_key_guessing_is_throttled(make_client):
    c = bearer(make_client, "kvl_abcdef123456_" + "z" * 43)
    codes = [c.get(f"{API}/support/tickets").status_code for _ in range(35)]
    assert codes[0] == 401 and 429 in codes


def test_key_cannot_reach_other_tenant_or_platform(world, make_client):
    a = world["a"]["c"]
    ta = new_ticket(world["a"]["c"])
    tb = new_ticket(world["b"]["c"])
    c = bearer(make_client, mk_key(a, ["tickets:read", "tickets:write", "tenants:read"])["key"])
    assert c.get(f"{API}/support/tickets/{ta}").status_code == 200
    assert c.get(f"{API}/support/tickets/{tb}").status_code == 404
    assert c.get(f"{API}/support/tickets?tenant_id={world['b']['t'].id}").json()["total"] == 0
    assert [t["id"] for t in c.get(f"{API}/tenants").json()["items"]] == [str(world["a"]["t"].id)]
    assert c.get(f"{API}/tenants/{world['b']['t'].id}").status_code in (403, 404)
    assert a.post(f"{API}/apikeys", json={"name": "xyz", "scopes": ["tickets:read"], "tenant_id": str(world["b"]["t"].id)}).status_code == 404


def new_ticket(c):
    r = c.post(f"{API}/support/tickets", json={"subject": "hello there", "body": "x"})
    assert r.status_code == 201
    return r.json()["id"]


def test_platform_key_bound_to_tenant_loses_platform_reach(world, make_client):
    o = world["o"]["c"]
    ta = new_ticket(world["a"]["c"])
    tb = new_ticket(world["b"]["c"])
    unbound = bearer(make_client, mk_key(o, ["tickets:read"])["key"])
    assert unbound.get(f"{API}/support/tickets").json()["total"] == 2
    bound = bearer(make_client, mk_key(o, ["tickets:read"], tenant_id=str(world["a"]["t"].id))["key"])
    assert [t["id"] for t in bound.get(f"{API}/support/tickets").json()["items"]] == [ta]
    assert bound.get(f"{API}/support/tickets/{tb}").status_code == 404


def test_keys_cannot_manage_keys_or_webhooks(world, make_client):
    c = bearer(make_client, mk_key(world["a"]["c"], ["tickets:read"])["key"])
    assert c.post(f"{API}/apikeys", json={"name": "child", "scopes": ["tickets:read"]}).status_code == 403
    assert c.get(f"{API}/apikeys").status_code == 403  # lacks apikeys:read scope
    assert c.post(f"{API}/webhooks", json={"url": "https://example.com/h", "events": ["ping"]}).status_code == 403


def test_list_visibility_and_admin_view(world):
    mk_key(world["a"]["c"])
    mk_key(world["b"]["c"])
    assert len(world["a"]["c"].get(f"{API}/apikeys").json()) == 1
    allk = world["o"]["c"].get(f"{API}/apikeys").json()
    assert len(allk) == 2 and {"owner_email"} <= set(allk[0])
    kid = allk[0]["id"]
    assert world["o"]["c"].delete(f"{API}/apikeys/{kid}").status_code == 200  # admin can revoke any key
    cat = world["a"]["c"].get(f"{API}/developer/scopes").json()
    grant = {x["scope"] for x in cat if x["grantable"]}
    assert "tickets:write" in grant and "servers:read" not in grant and "apikeys:write" not in grant
    assert set(x["scope"] for x in cat) >= set(ROLE_DEFS["customer"]["perms"])
    assert world["a"]["c"].get(f"{API}/developer/usage").json()["limits"]["max_active_keys"] == 20
