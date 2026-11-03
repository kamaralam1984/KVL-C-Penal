import pytest
from sqlalchemy import select

from app.core.security import hash_password
from app.models.identity import User
from app.models.reseller import CommissionEntry, ResellerDomain
from app.models.tenant import TenantQuota
from app.services import reseller as rsvc
from tests.conftest import PW, login

ALLOC = {"websites": 10, "databases": 10, "disk_mb": 10000}


def plain_user(db, email):
    from app.services.auth import now

    u = User(email=email, name=email.split("@")[0], password_hash=hash_password(PW), email_verified_at=now())
    db.add(u)
    db.commit()
    return u


@pytest.fixture
def make_reseller(db, make_client, admin, client):
    """Apply -> Super Admin approves -> returns (logged-in reseller client, reseller id)."""

    def _make(name="Acme Agency", email=None, allocation=None, max_clients=5, unit_costs=None):
        email = email or f"{name.split()[0].lower()}@agency.example.com"
        plain_user(db, email)
        c = make_client()
        assert login(c, email).status_code == 200
        r = c.post("/api/v1/reseller/apply", json={"name": name, "contact_email": email})
        assert r.status_code == 201, r.text
        rid = r.json()["id"]
        a = client.post(f"/api/v1/resellers/{rid}/approve", json={"max_clients": max_clients, "allocation": allocation or ALLOC, "unit_costs": unit_costs or {}})
        assert a.status_code == 200, a.text
        c2 = make_client()
        assert login(c2, email).status_code == 200
        return c2, rid

    return _make


def test_application_requires_approval_before_access(db, make_client, admin, client):
    plain_user(db, "app@agency.example.com")
    c = make_client()
    login(c, "app@agency.example.com")
    rid = c.post("/api/v1/reseller/apply", json={"name": "Pending Co", "contact_email": "app@agency.example.com"}).json()["id"]
    assert c.get("/api/v1/reseller/clients").status_code == 403  # no reseller membership yet
    assert c.post("/api/v1/reseller/apply", json={"name": "Again", "contact_email": "app@agency.example.com"}).status_code == 409
    assert c.get("/api/v1/reseller/application").json()["application"]["status"] == "pending_approval"
    # a reseller cannot approve itself
    assert c.post(f"/api/v1/resellers/{rid}/approve", json={}).status_code == 403
    assert client.post(f"/api/v1/resellers/{rid}/approve", json={"allocation": ALLOC}).status_code == 200
    c2 = make_client()
    login(c2, "app@agency.example.com")
    assert c2.get("/api/v1/reseller/me").json()["status"] == "approved"


def test_reject_and_unverified_cannot_apply(db, make_client, admin, client):
    from tests.conftest import PW as _PW  # noqa: F401

    u = User(email="unv@x.example.com", name="U", password_hash=hash_password(PW))
    db.add(u)
    db.commit()
    c = make_client()
    login(c, "unv@x.example.com")
    # unverified accounts may not log in at all in some flows; if login works, apply must be refused
    if c.cookies.get("kvl_csrf"):
        assert c.post("/api/v1/reseller/apply", json={"name": "Nope Co", "contact_email": "unv@x.example.com"}).status_code in (403, 401)
    plain_user(db, "rej@x.example.com")
    c2 = make_client()
    login(c2, "rej@x.example.com")
    rid = c2.post("/api/v1/reseller/apply", json={"name": "Rej Co", "contact_email": "rej@x.example.com"}).json()["id"]
    assert client.post(f"/api/v1/resellers/{rid}/reject", json={"note": "no"}).json()["status"] == "rejected"
    c3 = make_client()
    login(c3, "rej@x.example.com")
    assert c3.get("/api/v1/reseller/clients").status_code == 403


def test_cross_reseller_access_is_404(make_reseller):
    a, _ = make_reseller("Alpha Agency")
    b, _ = make_reseller("Beta Agency")
    pa = a.post("/api/v1/reseller/packages", json={"name": "Starter", "limits": {"websites": 2}, "retail_price_paise": 50000}).json()
    ca = a.post("/api/v1/reseller/clients", json={"name": "Alpha Client", "contact_email": "c@alpha.example.com", "package_id": pa["id"]}).json()
    assert a.get(f"/api/v1/reseller/clients/{ca['id']}").status_code == 200
    assert b.get(f"/api/v1/reseller/clients/{ca['id']}").status_code == 404
    assert b.patch(f"/api/v1/reseller/clients/{ca['id']}", json={"name": "Hijack"}).status_code == 404
    assert b.get(f"/api/v1/reseller/clients/{ca['id']}/support-context").status_code == 404
    pb = b.post("/api/v1/reseller/packages", json={"name": "Starter", "limits": {"websites": 1}, "retail_price_paise": 1}).json()
    assert b.post(f"/api/v1/reseller/clients/{ca['id']}/package", json={"package_id": pb["id"]}).status_code == 404
    assert a.post(f"/api/v1/reseller/clients/{ca['id']}/package", json={"package_id": pb["id"]}).status_code == 404  # other reseller's package
    assert b.get("/api/v1/reseller/clients").json()["items"] == []
    assert b.get(f"/api/v1/tenants/{ca['id']}").status_code == 404
    assert [p["id"] for p in b.get("/api/v1/reseller/packages").json()["items"]] == [pb["id"]]


def test_package_cannot_exceed_allocation(make_reseller):
    a, _ = make_reseller("Quota Agency", allocation={"websites": 4})
    assert a.post("/api/v1/reseller/packages", json={"name": "Big", "limits": {"websites": 5}, "retail_price_paise": 1}).status_code == 409
    r = a.post("/api/v1/reseller/packages", json={"name": "NoDisk", "limits": {"disk_mb": 1}, "retail_price_paise": 1})
    assert r.status_code == 409 and r.json()["error"]["code"] == "not_in_allocation"
    assert a.post("/api/v1/reseller/packages", json={"name": "Bad", "limits": {"bogus": 1}, "retail_price_paise": 1}).status_code == 422


def test_client_quota_enforced_against_platform_allocation(make_reseller, db):
    a, rid = make_reseller("Cap Agency", allocation={"websites": 5}, max_clients=2)
    p3 = a.post("/api/v1/reseller/packages", json={"name": "Three", "limits": {"websites": 3}, "retail_price_paise": 100}).json()
    assert a.post("/api/v1/reseller/clients", json={"name": "Client One", "contact_email": "1@c.example.com", "package_id": p3["id"]}).status_code == 201
    r = a.post("/api/v1/reseller/clients", json={"name": "Client Two", "contact_email": "2@c.example.com", "package_id": p3["id"]})
    assert r.status_code == 409 and r.json()["error"]["code"] == "allocation_exceeded"
    p2 = a.post("/api/v1/reseller/packages", json={"name": "Two", "limits": {"websites": 2}, "retail_price_paise": 100}).json()
    c2 = a.post("/api/v1/reseller/clients", json={"name": "Client Two", "contact_email": "2@c.example.com", "package_id": p2["id"]}).json()
    # upgrading client two to the 3-website package would commit 6 > 5
    assert a.post(f"/api/v1/reseller/clients/{c2['id']}/package", json={"package_id": p3["id"]}).status_code == 409
    # max clients
    assert a.post("/api/v1/reseller/clients", json={"name": "Client Three", "contact_email": "3@c.example.com"}).json()["error"]["code"] == "client_limit_reached"
    alloc = a.get("/api/v1/reseller/allocation").json()["resources"][0]
    assert alloc["committed"] == 5 and alloc["remaining"] == 0
    q = db.scalar(select(TenantQuota).where(TenantQuota.resource == "websites", TenantQuota.tenant_id == c2["id"]))
    assert q.limit_value == 2
    # admin cannot lower allocation below committed
    admin_c = None  # noqa: F841


def test_admin_cannot_lower_allocation_below_committed(make_reseller, client):
    a, rid = make_reseller("Low Agency", allocation={"websites": 5})
    p = a.post("/api/v1/reseller/packages", json={"name": "Four", "limits": {"websites": 4}, "retail_price_paise": 100}).json()
    a.post("/api/v1/reseller/clients", json={"name": "Client One", "contact_email": "1@c.example.com", "package_id": p["id"]})
    r = client.put(f"/api/v1/resellers/{rid}/limits", json={"allocation": {"websites": 3}})
    assert r.status_code == 409
    assert client.put(f"/api/v1/resellers/{rid}/limits", json={"allocation": {"websites": 8}, "max_clients": 9}).status_code == 200


def test_package_margin_from_unit_costs(make_reseller):
    a, _ = make_reseller("Margin Agency", allocation={"websites": 10}, unit_costs={"websites": 10000})
    p = a.post("/api/v1/reseller/packages", json={"name": "Two", "limits": {"websites": 2}, "retail_price_paise": 50000}).json()
    assert p["wholesale_price_paise"] == 20000 and p["margin_paise"] == 30000 and p["margin_bps"] == 6000


def test_unauthorized_server_operations_rejected(make_reseller, db, client):
    a, _ = make_reseller("Ops Agency")
    me = a.get("/api/v1/reseller/me").json()
    assert a.post("/api/v1/tenants", json={"name": "Sneaky"}).status_code == 403
    assert client.get("/api/v1/resellers").status_code == 200
    assert a.get("/api/v1/resellers").status_code == 403
    assert a.post(f"/api/v1/resellers/{me['id']}/approve", json={}).status_code == 403
    assert a.put(f"/api/v1/resellers/{me['id']}/limits", json={"allocation": {"websites": 999}}).status_code == 403
    perms = {p for r in a.get("/api/v1/auth/me").json().get("permissions", []) for p in [r]}
    assert not [p for p in perms if p.startswith(("servers:", "monitoring:", "backups:", "ssl:", "files:", "databases:"))]
    t = a.post("/api/v1/reseller/clients", json={"name": "Client One", "contact_email": "1@c.example.com"}).json()
    assert a.post(f"/api/v1/tenants/{t['id']}/status", json={"status": "suspended"}).status_code == 403
    assert a.put(f"/api/v1/tenants/{t['id']}/quotas", json={"resource": "websites", "limit_value": 999}).status_code == 403


def test_commission_ledger_consistency_and_immutability(make_reseller, db, client):
    from app.models.billing import Invoice
    from app.services import billing as bsvc

    a, rid = make_reseller("Ledger Agency", allocation={"websites": 10})
    assert client.post("/api/v1/resellers/commission-rules", json={"reseller_id": rid, "name": "Standard 10%", "rate_bps": 1000}).status_code == 201
    p = a.post("/api/v1/reseller/packages", json={"name": "Basic", "limits": {"websites": 2}, "retail_price_paise": 100}).json()
    t = a.post("/api/v1/reseller/clients", json={"name": "Client One", "contact_email": "1@c.example.com", "package_id": p["id"]}).json()
    from app.models.tenant import Tenant

    tenant = db.get(Tenant, t["id"])
    for amount in (100000, 25000):
        inv = bsvc.create_invoice(db, tenant, "manual", [bsvc.line("Hosting", amount)])
        bsvc.settle_invoice(db, inv)
        db.commit()
    led = a.get("/api/v1/reseller/commissions").json()
    assert led["balances"]["pending_paise"] == 12500 == sum(e["amount_paise"] for e in led["items"])
    assert led["balances"]["total_earned_paise"] == 12500 and led["balances"]["payable_paise"] == 0
    assert a.post(f"/api/v1/resellers/{rid}/commissions/approve").status_code == 403  # reseller cannot self-approve
    assert client.post(f"/api/v1/resellers/{rid}/commissions/approve").json()["approved"] == 2
    led = a.get("/api/v1/reseller/commissions").json()["balances"]
    assert led["payable_paise"] == 12500 and led["pending_paise"] == 0
    assert client.post(f"/api/v1/resellers/{rid}/commissions/pay", json={"payout_reference": "UTR123456"}).json()["paid"] == 2
    b = a.get("/api/v1/reseller/commissions").json()["balances"]
    assert b == {"pending_paise": 0, "approved_paise": 0, "paid_paise": 12500, "payable_paise": 0, "total_earned_paise": 12500}
    # paid entries are immutable and undeletable
    e = db.scalars(select(CommissionEntry)).first()
    e.amount_paise = 1
    with pytest.raises(ValueError):
        db.commit()
    db.rollback()
    e = db.scalars(select(CommissionEntry)).first()
    e.status = "pending"
    with pytest.raises(ValueError):
        db.commit()
    db.rollback()
    e = db.scalars(select(CommissionEntry)).first()
    db.delete(e)
    with pytest.raises(ValueError):
        db.commit()
    db.rollback()
    rec = a.get("/api/v1/reseller/commissions/reconciliation").json()
    assert rec["consistent"] and rec["ledger_sum_paise"] == 12500
    csv = a.get("/api/v1/reseller/commissions/export.csv")
    assert csv.status_code == 200 and "text/csv" in csv.headers["content-type"] and len(csv.text.strip().splitlines()) == 3
    assert db.scalar(select(Invoice.id)) is not None


def test_commission_reversal_on_refund_keeps_balance_derived(make_reseller, db, client):
    from app.models.billing import RefundRecord
    from app.models.tenant import Tenant
    from app.services import billing as bsvc

    a, rid = make_reseller("Refund Agency")
    client.post("/api/v1/resellers/commission-rules", json={"reseller_id": rid, "name": "10%", "rate_bps": 1000})
    t = a.post("/api/v1/reseller/clients", json={"name": "Client One", "contact_email": "1@c.example.com"}).json()
    tenant = db.get(Tenant, t["id"])
    inv = bsvc.create_invoice(db, tenant, "manual", [bsvc.line("Hosting", 100000)])
    p = bsvc.record_manual_payment(db, inv, "UTR-1", tenant.id if False else db.scalar(select(User.id)))
    db.commit()
    r = bsvc.create_refund_record(db, p, inv.total_paise // 2, "partial", None)
    bsvc.finalize_refund(db, r)
    db.commit()
    bal = a.get("/api/v1/reseller/commissions").json()
    assert bal["balances"]["total_earned_paise"] == 10000 - 5000
    assert sum(e["amount_paise"] for e in bal["items"]) == 5000
    assert {e["kind"] for e in bal["items"]} == {"sale", "reversal"}
    assert db.scalar(select(RefundRecord.id)) is not None


def test_white_label_domain_verification_state_machine(make_reseller, db, monkeypatch):
    a, rid = make_reseller("Brand Agency")
    assert a.put("/api/v1/reseller/branding", json={"portal_name": "Brand Hosting", "primary_color": "#112233", "logo_url": "https://cdn.example.com/l.png"}).status_code == 200
    assert a.put("/api/v1/reseller/branding", json={"logo_url": "javascript:alert(1)"}).status_code == 422
    assert a.put("/api/v1/reseller/branding", json={"primary_color": "red"}).status_code == 422
    assert a.post("/api/v1/reseller/domains", json={"domain": "not a domain"}).status_code == 422
    d = a.post("/api/v1/reseller/domains", json={"domain": "Panel.Brand.example.com"}).json()
    assert d["state"] == "pending" and d["dns_record"]["name"] == "_kvl-verify.panel.brand.example.com"
    assert a.post("/api/v1/reseller/domains", json={"domain": "panel.brand.example.com"}).status_code == 409

    class FakeResolver:
        values: list[str] = []

        def txt(self, name):
            assert name == "_kvl-verify.panel.brand.example.com"
            return self.values

    fr = FakeResolver()
    monkeypatch.setattr(rsvc, "get_resolver", lambda: fr)
    # unverified domain gives no public branding
    anon = type(a)(a.app, base_url="http://testserver")
    assert anon.get("/api/v1/branding/by-host", params={"host": "panel.brand.example.com"}).status_code == 404
    assert a.post(f"/api/v1/reseller/domains/{d['id']}/verify").json()["state"] == "pending"
    fr.values = ["kvl-verify=wrong"]
    assert a.post(f"/api/v1/reseller/domains/{d['id']}/verify").json()["state"] == "pending"
    fr.values = [d["dns_record"]["value"]]
    assert a.post(f"/api/v1/reseller/domains/{d['id']}/verify").json()["state"] == "verified"
    pub = anon.get("/api/v1/branding/by-host", params={"host": "panel.brand.example.com"}).json()
    assert pub["portal_name"] == "Brand Hosting" and "KVL Hosting" in pub["disclosure_text"]
    assert set(pub) <= {"portal_name", "logo_url", "primary_color", "accent_color", "support_email", "email_from_name", "email_footer", "disclosure_text", "email_branding_note"}
    fr.values = []
    assert a.post(f"/api/v1/reseller/domains/{d['id']}/verify").json()["state"] == "failed"
    assert anon.get("/api/v1/branding/by-host", params={"host": "panel.brand.example.com"}).status_code == 404
    # 72h window: pending turns failed
    d2 = a.post("/api/v1/reseller/domains", json={"domain": "old.brand.example.com"}).json()
    row = db.get(ResellerDomain, d2["id"])
    from datetime import timedelta

    from sqlalchemy import update

    db.execute(update(ResellerDomain).where(ResellerDomain.id == row.id).values(created_at=row.created_at - timedelta(hours=80)))
    db.commit()
    monkeypatch.setattr(rsvc, "get_resolver", lambda: type("R", (), {"txt": lambda self, n: []})())
    assert a.post(f"/api/v1/reseller/domains/{d2['id']}/verify").json()["state"] == "failed"


def test_white_label_tenant_boundary(make_reseller, make_user, db, client, make_client):
    a, ra = make_reseller("Alpha Agency")
    b, rb = make_reseller("Beta Agency")
    a.put("/api/v1/reseller/branding", json={"portal_name": "Alpha Hosting"})
    b.put("/api/v1/reseller/branding", json={"portal_name": "Beta Hosting"})
    ta = a.post("/api/v1/reseller/clients", json={"name": "Client A", "contact_email": "ca@c.example.com"}).json()
    from app.models.tenant import Tenant

    u, _ = make_user("owner@ca.example.com", "customer", tenant=db.get(Tenant, ta["id"]))
    c = make_client()
    assert login(c, "owner@ca.example.com").status_code == 200
    eff = c.get("/api/v1/branding/effective").json()
    assert eff["branding"]["portal_name"] == "Alpha Hosting"
    # a direct customer (no reseller) sees none; reseller B never sees A's branding via the tenant route
    u2, _ = make_user("direct@x.example.com", "customer")
    c2 = make_client()
    login(c2, "direct@x.example.com")
    assert c2.get("/api/v1/branding/effective").json()["branding"] is None
    assert c2.get("/api/v1/reseller/branding").status_code == 403
    assert b.get("/api/v1/reseller/branding").json()["branding"]["portal_name"] == "Beta Hosting"


def test_csv_export_neutralises_formulas(make_reseller):
    a, _ = make_reseller("Csv Agency")
    a.post("/api/v1/reseller/clients", json={"name": "=HYPERLINK(\"x\")", "contact_email": "1@c.example.com"})
    text = a.get("/api/v1/reseller/reports/clients.csv").text
    assert "'=HYPERLINK" in text
    assert a.get("/api/v1/reseller/reports/summary").json()["clients_total"] == 1
