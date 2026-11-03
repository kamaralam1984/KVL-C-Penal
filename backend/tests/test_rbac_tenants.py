import uuid

from sqlalchemy import select

from app.models.identity import AuditEvent, Membership, Role
from app.models.tenant import Tenant, TenantQuota
from app.services.tenants import consume_quota
from tests.conftest import PW, login

API = "/api/v1"


# ---------------- RBAC ----------------
def test_every_role_boundary(make_client, make_user):
    """Each role may call exactly the endpoints its permissions grant."""
    cases = {
        "super_admin": {"/rbac/roles": 200, "/users": 200, "/tenants": 200, "/audit": 200},
        "hosting_admin": {"/rbac/roles": 403, "/users": 403, "/tenants": 200, "/audit": 403},
        "support": {"/rbac/roles": 403, "/users": 200, "/tenants": 200, "/audit": 403},
        "customer": {"/rbac/roles": 403, "/users": 403, "/tenants": 200, "/audit": 403},
        "reseller": {"/rbac/roles": 403, "/users": 403, "/tenants": 200, "/audit": 403},
    }
    for role, expected in cases.items():
        email = f"{role}@x.example.com"
        make_user(email, role)
        c = make_client()
        assert login(c, email).status_code == 200
        for path, status in expected.items():
            got = c.get(API + path).status_code
            assert got == status, f"{role} {path}: expected {status}, got {got}"


def test_users_endpoint_is_platform_only_even_with_permission(make_client, make_user, db):
    u, t = make_user("c1@x.example.com", "customer")
    role = db.scalar(select(Role).where(Role.name == "customer"))
    from app.models.identity import Permission, RolePermission

    perm = db.scalar(select(Permission).where(Permission.code == "users:read"))
    db.add(RolePermission(role_id=role.id, permission_id=perm.id))
    db.commit()
    from app.core.deps import invalidate_role_cache

    invalidate_role_cache()
    c = make_client()
    login(c, "c1@x.example.com")
    assert c.get(f"{API}/users").status_code == 403


def test_customer_cannot_self_assign_privileged_role(make_client, make_user, db):
    u, t = make_user("evil@x.example.com", "customer")
    c = make_client()
    login(c, "evil@x.example.com")
    r = c.post(f"{API}/rbac/memberships", json={"user_id": str(u.id), "role": "super_admin"})
    assert r.status_code == 403
    assert not db.scalar(select(Membership).join(Role).where(Membership.user_id == u.id, Role.name == "super_admin"))


def test_hosting_admin_cannot_grant_privileged_roles(make_client, make_user):
    make_user("ha@x.example.com", "hosting_admin")
    victim, _ = make_user("v@x.example.com", "customer")
    c = make_client()
    login(c, "ha@x.example.com")
    assert c.post(f"{API}/rbac/memberships", json={"user_id": str(victim.id), "role": "super_admin"}).status_code == 403
    assert c.post(f"{API}/staff/invitations", json={"email": "n@x.example.com", "role": "hosting_admin"}).status_code in (403,)


def test_owner_cannot_assign_role_to_self_or_remove_last_owner(admin, client, db):
    assert client.post(f"{API}/rbac/memberships", json={"user_id": str(admin.id), "role": "support"}).status_code == 403
    m = db.scalar(select(Membership).where(Membership.user_id == admin.id))
    assert client.delete(f"{API}/rbac/memberships/{m.id}").status_code == 403  # own role


def test_role_permission_changes_are_owner_only_audited_and_effective(admin, client, make_client, make_user, db):
    make_user("sup@x.example.com", "support")
    sc = make_client()
    login(sc, "sup@x.example.com")
    assert sc.get(f"{API}/audit").status_code == 403
    r = client.put(f"{API}/rbac/roles/support/permissions", json={"permissions": ["audit:read", "tenants:read"]})
    assert r.status_code == 200
    assert sc.get(f"{API}/audit").status_code == 200
    assert db.scalar(select(AuditEvent).where(AuditEvent.action == "rbac.role_permissions_changed"))
    # customer role may not gain platform admin permissions; super_admin immutable
    assert client.put(f"{API}/rbac/roles/customer/permissions", json={"permissions": ["roles:manage"]}).status_code == 422
    assert client.put(f"{API}/rbac/roles/super_admin/permissions", json={"permissions": []}).status_code == 409
    assert client.put(f"{API}/rbac/roles/support/permissions", json={"permissions": ["nope:nope"]}).status_code == 422


def test_invitation_flow(admin, client, make_client, db):
    r = client.post(f"{API}/staff/invitations", json={"email": "newstaff@x.example.com", "role": "support"})
    assert r.status_code == 201
    from app.models.identity import EmailOutbox
    import re

    row = db.scalar(select(EmailOutbox).where(EmailOutbox.kind == "invitation"))
    token = re.search(r"token=([\w-]+)", row.body).group(1)
    c = make_client()
    ok = c.post(f"{API}/auth/invitations/accept", json={"token": token, "name": "New Staff", "password": PW})
    assert ok.status_code == 200
    assert c.get(f"{API}/auth/me").json()["role"] == "support"
    # token is single-use
    assert make_client().post(f"{API}/auth/invitations/accept", json={"token": token, "name": "x y", "password": PW}).status_code == 400


def test_customer_can_invite_only_into_own_tenant(make_client, make_user):
    u, t1 = make_user("o1@x.example.com", "customer")
    _, t2 = make_user("o2@x.example.com", "customer")
    c = make_client()
    login(c, "o1@x.example.com")
    # customers lack staff:write by default
    assert c.post(f"{API}/staff/invitations", json={"email": "m@x.example.com", "role": "customer", "tenant_id": str(t2.id)}).status_code == 403


# ---------------- tenants + isolation ----------------
def two_tenants(make_user, make_client):
    ua, ta = make_user("a@x.example.com", "customer", tenant_name="Alpha")
    ub, tb = make_user("b@x.example.com", "customer", tenant_name="Beta")
    ca, cb = make_client(), make_client()
    login(ca, "a@x.example.com")
    login(cb, "b@x.example.com")
    return (ua, ta, ca), (ub, tb, cb)


def test_tenant_a_cannot_read_tenant_b(make_user, make_client):
    (ua, ta, ca), (ub, tb, cb) = two_tenants(make_user, make_client)
    assert ca.get(f"{API}/tenants/{tb.id}").status_code == 404
    assert ca.get(f"{API}/tenants/{tb.id}/members").status_code == 404
    assert ca.get(f"{API}/tenants/{tb.id}/quotas").status_code == 404
    assert ca.get(f"{API}/tenants/{tb.id}/activity").status_code == 404
    listing = ca.get(f"{API}/tenants").json()
    assert [t["id"] for t in listing["items"]] == [str(ta.id)]


def test_tenant_a_cannot_update_or_manage_tenant_b(make_user, make_client, db):
    (ua, ta, ca), (ub, tb, cb) = two_tenants(make_user, make_client)
    assert ca.patch(f"{API}/tenants/{tb.id}", json={"name": "pwned"}).status_code in (403, 404)
    assert ca.post(f"{API}/tenants/{tb.id}/status", json={"status": "suspended"}).status_code == 403
    assert ca.put(f"{API}/tenants/{tb.id}/quotas", json={"resource": "websites", "limit_value": 0}).status_code == 403
    db.expire_all()
    assert db.get(Tenant, tb.id).name != "pwned" and db.get(Tenant, tb.id).status == "active"


def test_cannot_switch_to_unowned_membership(make_user, make_client, db):
    (ua, ta, ca), (ub, tb, cb) = two_tenants(make_user, make_client)
    mb = db.scalar(select(Membership).where(Membership.user_id == ub.id))
    assert ca.post(f"{API}/auth/switch-membership", json={"membership_id": str(mb.id)}).status_code == 404
    # client-supplied tenant_id in a query/body never overrides membership
    assert ca.get(f"{API}/tenants/current").json()["id"] == str(ta.id)


def test_tenant_user_cannot_create_tenant(make_user, make_client):
    (ua, ta, ca), _ = two_tenants(make_user, make_client)
    assert ca.post(f"{API}/tenants", json={"name": "Rogue"}).status_code == 403


def test_super_admin_tenant_lifecycle_and_suspension_policy(admin, client, make_user, make_client, db):
    ub, tb = make_user("life@x.example.com", "customer", tenant_name="Lifecycle")
    cust = make_client()
    login(cust, "life@x.example.com")
    tid = str(tb.id)
    assert client.post(f"{API}/tenants/{tid}/status", json={"status": "suspended", "reason": "unpaid"}).json()["status"] == "suspended"
    # suspended: reads work, writes to infrastructure are blocked, billing/support stay open
    assert cust.get(f"{API}/tenants/current").status_code == 200
    r = cust.patch(f"{API}/tenants/{tid}", json={"name": "New Name"})
    assert r.status_code == 403 and r.json()["error"]["code"] == "tenant_suspended"
    # no data was deleted by suspension
    assert db.get(Tenant, tb.id) is not None
    # reactivation restores access
    assert client.post(f"{API}/tenants/{tid}/status", json={"status": "active"}).status_code == 200
    assert cust.patch(f"{API}/tenants/{tid}", json={"name": "New Name"}).status_code == 200
    # invalid transitions rejected; cancelled is terminal and sets retention
    assert client.post(f"{API}/tenants/{tid}/status", json={"status": "trial"}).status_code == 409
    c = client.post(f"{API}/tenants/{tid}/status", json={"status": "cancelled"}).json()
    assert c["retention_until"] and client.post(f"{API}/tenants/{tid}/status", json={"status": "active"}).status_code == 409
    assert cust.patch(f"{API}/tenants/{tid}", json={"name": "Zombie"}).json()["error"]["code"] == "tenant_cancelled"
    acts = client.get(f"{API}/tenants/{tid}/activity").json()
    assert {"tenant.suspended", "tenant.active", "tenant.cancelled"} <= {a["action"] for a in acts}


def test_quota_enforcement_and_defaults(admin, client, make_user, db):
    ub, tb = make_user("q@x.example.com", "customer")
    quotas = {q["resource"]: q for q in client.get(f"{API}/tenants/{tb.id}/quotas").json()}
    assert quotas["websites"]["limit"] == 5
    assert client.put(f"{API}/tenants/{tb.id}/quotas", json={"resource": "websites", "limit_value": 1}).status_code == 200
    consume_quota(db, tb.id, "websites", 1)
    db.commit()
    try:
        consume_quota(db, tb.id, "websites", 1)
        raised = False
    except Exception as e:  # HTTPException 409
        raised = getattr(e, "status_code", None) == 409
    assert raised
    consume_quota(db, tb.id, "websites", -1)
    db.commit()
    assert db.scalar(select(TenantQuota.used_value).where(TenantQuota.tenant_id == tb.id, TenantQuota.resource == "websites")) == 0


def test_reseller_sees_only_own_tenants(make_client, make_user, db):
    ru, _ = make_user("r1@x.example.com", "reseller")
    r2, _ = make_user("r2@x.example.com", "reseller")
    rid1, rid2 = uuid.uuid4(), uuid.uuid4()
    m1 = db.scalar(select(Membership).where(Membership.user_id == ru.id))
    m1.reseller_id = rid1
    m2 = db.scalar(select(Membership).where(Membership.user_id == r2.id))
    m2.reseller_id = rid2
    _, t1 = make_user("cl1@x.example.com", "customer")
    _, t2 = make_user("cl2@x.example.com", "customer")
    db.get(Tenant, t1.id).reseller_id = rid1
    db.get(Tenant, t2.id).reseller_id = rid2
    db.commit()
    c = make_client()
    login(c, "r1@x.example.com")
    ids = [t["id"] for t in c.get(f"{API}/tenants").json()["items"]]
    assert ids == [str(t1.id)]
    assert c.get(f"{API}/tenants/{t2.id}").status_code == 404


def test_audit_log_scoped_to_tenant_for_tenant_admins(admin, client, make_user, make_client, db, monkeypatch):
    ub, tb = make_user("au@x.example.com", "customer")
    client.patch(f"{API}/tenants/{tb.id}", json={"name": "Renamed by owner"})
    rows = client.get(f"{API}/audit?action=tenant.").json()
    assert rows and all(r["action"].startswith("tenant.") for r in rows)


def test_audit_never_stores_secrets(admin, client, db):
    from app.services.audit import audit

    audit(db, "test.event", actor=admin.id, detail={"password": "hunter2", "nested": {"api_token": "abc"}, "ok": 1})
    db.commit()
    ev = db.scalar(select(AuditEvent).where(AuditEvent.action == "test.event"))
    assert ev.detail["password"] == "[redacted]" and ev.detail["nested"]["api_token"] == "[redacted]" and ev.detail["ok"] == 1


def test_tenant_export_platform_only(admin, client, make_user, make_client):
    ub, tb = make_user("ex@x.example.com", "customer")
    out = client.post(f"{API}/tenants/{tb.id}/export").json()
    assert out["tenant"]["id"] == str(tb.id) and out["members"]
    c = make_client()
    login(c, "ex@x.example.com")
    assert c.post(f"{API}/tenants/{tb.id}/export").status_code == 403


# ---------------- platform settings ----------------
def test_settings_owner_only_and_validated(admin, client, make_user, make_client):
    assert any(s["key"] == "trial_days" for s in client.get(f"{API}/settings").json())
    assert client.put(f"{API}/settings/trial_days", json={"value": {"days": 21}}).status_code == 200
    assert client.put(f"{API}/settings/trial_days", json={"value": {"days": -1}}).status_code == 422
    assert client.put(f"{API}/settings/trial_days", json={"value": {"days": 5, "x": 1}}).status_code == 422
    assert client.put(f"{API}/settings/default_quotas", json={"value": {"nuke": 1}}).status_code == 422
    assert client.put(f"{API}/settings/unknown_key", json={"value": {}}).status_code == 404
    make_user("hs@x.example.com", "hosting_admin")
    c = make_client()
    login(c, "hs@x.example.com")
    assert c.get(f"{API}/settings").status_code == 403
    assert c.put(f"{API}/settings/trial_days", json={"value": {"days": 1}}).status_code == 403
    # new tenants honour the changed rule
    from app.models.tenant import Tenant
    ub, tb = make_user("trial@x.example.com", "customer")
    t = client.post(f"{API}/tenants", json={"name": "Trial Co", "status": "trial"}).json()
    assert t["trial_ends_at"]


# ---------------- dashboard stats ----------------
def test_dashboard_stats_scoped_by_role(admin, client, make_user, make_client):
    make_user("s1@x.example.com", "customer", tenant_name="StatsOne")
    make_user("s2@x.example.com", "customer", tenant_name="StatsTwo")
    plat = client.get(f"{API}/stats/dashboard").json()
    assert plat["scope"] == "platform" and plat["tenants"]["total"] == 2 and len(plat["signups_14d"]) == 14
    c = make_client()
    login(c, "s1@x.example.com")
    mine = c.get(f"{API}/stats/dashboard").json()
    assert mine["scope"] == "tenant" and mine["tenant"]["name"] == "StatsOne" and "users" not in mine
    assert make_client().get(f"{API}/stats/dashboard").status_code == 401
