import threading
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select

from app.core.db import SessionLocal
from app.models.billing import Coupon, CouponRedemption, Invoice, PlanVersion, Subscription
from app.models.tenant import Tenant, TenantQuota
from app.services import billing as bsvc
from app.services.tenants import provision_tenant
from tests._billing_utils import make_plan
from tests.conftest import login


@pytest.fixture
def cust(make_user, make_client, db):
    """A logged-in customer of a fresh pending tenant."""

    def _make(email="owner@acme.example.com", status="pending"):
        from sqlalchemy import select as sel

        from app.models.identity import User

        t = provision_tenant(db, "Acme Co", None, email, status=status)
        db.commit()
        u, _ = make_user(email, "customer", tenant=t)
        c = make_client()
        assert login(c, email).status_code == 200
        return c, t

    return _make


def pay_manually(admin_client, invoice_id, ref="UTR-0001"):
    r = admin_client.post(f"/api/v1/billing/invoices/{invoice_id}/manual-payment", json={"reference": ref})
    assert r.status_code == 200, r.text
    return r.json()


# ------------------------------------------------------------------------------------------------ proration
def test_proration_formula_and_rounding():
    start = datetime(2026, 1, 1, tzinfo=UTC)
    end = datetime(2026, 1, 31, tzinfo=UTC)  # 30 days
    now = end - timedelta(days=10)  # 1/3 of the period left
    pr = bsvc.prorate(30000, 90000, start, end, now)
    assert (pr["credit_paise"], pr["charge_paise"], pr["net_paise"]) == (10000, 30000, 20000)
    # half-up: 100/3 = 33.33 -> 33 ; 101/3 = 33.67 -> 34 ; 1 * 1/2 = 0.5 -> 1
    assert bsvc.prorate(100, 0, start, end, now)["credit_paise"] == 33
    assert bsvc.prorate(101, 0, start, end, now)["credit_paise"] == 34
    assert bsvc.prorate(1, 0, start, end, end - timedelta(days=15))["credit_paise"] == 1
    # start of period: full price; end of period: nothing; after the end: clamped
    assert bsvc.prorate(30000, 90000, start, end, start)["net_paise"] == 60000
    assert bsvc.prorate(30000, 90000, start, end, end)["net_paise"] == 0
    assert bsvc.prorate(30000, 90000, start, end, end + timedelta(days=3))["net_paise"] == 0


def test_gst_split_intra_and_inter_state():
    lines = [bsvc.line("Hosting", 100000)]
    intra = bsvc.compute_tax(lines, 0, "29", "29")
    assert (intra["cgst_paise"], intra["sgst_paise"], intra["igst_paise"], intra["total_paise"]) == (9000, 9000, 0, 118000)
    inter = bsvc.compute_tax(lines, 0, "29", "27")
    assert (inter["cgst_paise"], inter["sgst_paise"], inter["igst_paise"]) == (0, 0, 18000)
    disc = bsvc.compute_tax(lines, 10000, "29", "29")
    assert disc["taxable_paise"] == 90000 and disc["total_paise"] == 106200
    odd = bsvc.compute_tax([bsvc.line("x", 101)], 0, "29", "29")  # tax 18.18 -> 18 -> 9 + 9
    assert odd["cgst_paise"] + odd["sgst_paise"] == 18 and odd["total_paise"] == 119


# ------------------------------------------------------------------------------------------------ versioning
def test_plan_versions_are_immutable_and_subscriptions_keep_historic_terms(admin, client, cust, db):
    make_plan(client, "pro", monthly=100000)
    c1, t1 = cust("a@one.example.com")
    inv1 = c1.post("/api/v1/billing/checkout", json={"plan_code": "pro", "interval": "monthly"}).json()["invoice"]
    assert inv1["total_paise"] == 118000
    pay_manually(client, inv1["id"])
    r = client.post("/api/v1/plans/pro/versions", json={"price_monthly_paise": 150000, "price_yearly_paise": 1500000, "limits": {"websites": 20}, "trial_days": 7})
    assert r.status_code == 201 and r.json()["current_version"]["version"] == 2
    sub = c1.get("/api/v1/billing/subscription").json()["subscription"]
    assert sub["plan"]["version"] == 1 and sub["terms"]["price_monthly_paise"] == 100000
    # a new customer gets version 2 terms
    c2, _ = cust("b@two.example.com")
    inv2 = c2.post("/api/v1/billing/checkout", json={"plan_code": "pro"}).json()["invoice"]
    assert inv2["total_paise"] == 177000
    # renewal for the first customer is billed at the pinned version-1 price
    s = db.scalar(select(Subscription).where(Subscription.tenant_id == t1.id))
    bsvc.generate_renewal_invoices(db, s.current_period_end - timedelta(days=1))
    db.commit()
    ren = db.scalar(select(Invoice).where(Invoice.tenant_id == t1.id, Invoice.purpose == "renewal"))
    assert ren.taxable_paise == 100000
    # the ORM refuses to mutate a published version
    v = db.scalars(select(PlanVersion)).first()
    v.price_monthly_paise = 1
    with pytest.raises(ValueError):
        db.commit()
    db.rollback()
    assert c1.post("/api/v1/plans", json={}).status_code == 403
    assert [p["code"] for p in c1.get("/api/v1/billing/catalog").json()["plans"]] == ["pro"]
    assert c1.get("/api/v1/plans").status_code == 403


# ------------------------------------------------------------------------------------------------ lifecycle
def test_trial_then_expiry_suspends_without_deleting_data(admin, client, cust, db):
    make_plan(client, "pro")
    c, t = cust()
    assert c.post("/api/v1/billing/subscription/trial", json={"plan_code": "pro"}).status_code == 201
    db.expire_all()
    assert db.get(Tenant, t.id).status == "trial"
    assert c.post("/api/v1/billing/subscription/trial", json={"plan_code": "pro"}).status_code == 409
    quotas_before = {q.resource: q.limit_value for q in db.scalars(select(TenantQuota).where(TenantQuota.tenant_id == t.id))}
    assert quotas_before["websites"] == 10
    r = bsvc.run_lifecycle(db, datetime.now(UTC) + timedelta(days=8))
    assert r["trials_expired"] == 1
    db.expire_all()
    ten = db.get(Tenant, t.id)
    assert ten.status == "suspended"
    assert {q.resource: q.limit_value for q in db.scalars(select(TenantQuota).where(TenantQuota.tenant_id == t.id))} == quotas_before
    sub = db.scalar(select(Subscription).where(Subscription.tenant_id == t.id))
    assert sub.status == "expired"
    # later verified payment reactivates the same tenant
    inv = c.post("/api/v1/billing/checkout", json={"plan_code": "pro"}).json()["invoice"]
    pay_manually(client, inv["id"])
    db.expire_all()
    assert db.get(Tenant, t.id).status == "active"


def test_renewal_past_due_grace_suspend_and_reactivate(admin, client, cust, db):
    make_plan(client, "pro")
    c, t = cust()
    inv = c.post("/api/v1/billing/checkout", json={"plan_code": "pro"}).json()["invoice"]
    db.expire_all()
    assert db.get(Tenant, t.id).status == "pending"  # not activated by checkout
    pay_manually(client, inv["id"])
    db.expire_all()
    assert db.get(Tenant, t.id).status == "active"
    sub = db.scalar(select(Subscription).where(Subscription.tenant_id == t.id))
    end = sub.current_period_end
    r = bsvc.run_lifecycle(db, end - timedelta(days=2))
    assert r["renewal_invoices"] == 1 and bsvc.run_lifecycle(db, end - timedelta(days=2))["renewal_invoices"] == 0  # idempotent
    r = bsvc.run_lifecycle(db, end + timedelta(hours=1))
    assert r["past_due"] == 1
    db.expire_all()
    assert db.scalar(select(Subscription).where(Subscription.tenant_id == t.id)).status == "past_due"
    assert db.get(Tenant, t.id).status == "active"  # still in grace
    r = bsvc.run_lifecycle(db, end + timedelta(days=8))
    assert r["suspended"] == 1
    db.expire_all()
    assert db.get(Tenant, t.id).status == "suspended"
    ren = db.scalar(select(Invoice).where(Invoice.tenant_id == t.id, Invoice.purpose == "renewal", Invoice.status == "open"))
    # customers of a suspended tenant can still reach billing and pay
    assert c.get("/api/v1/billing/invoices").status_code == 200
    pay_manually(client, str(ren.id), "UTR-RENEW")
    db.expire_all()
    assert db.get(Tenant, t.id).status == "active"
    sub = db.scalar(select(Subscription).where(Subscription.tenant_id == t.id))
    assert sub.status == "active" and sub.current_period_end > end


def test_cancel_at_period_end_vs_immediate(admin, client, cust, db):
    make_plan(client, "pro")
    c, t = cust()
    inv = c.post("/api/v1/billing/checkout", json={"plan_code": "pro"}).json()["invoice"]
    pay_manually(client, inv["id"])
    r = c.post("/api/v1/billing/subscription/cancel", json={"immediate": False}).json()
    assert r["status"] == "active" and r["cancel_at_period_end"] is True
    assert c.post("/api/v1/billing/subscription/resume").json()["cancel_at_period_end"] is False
    again = c.post("/api/v1/billing/subscription/cancel", json={"immediate": False})
    assert again.status_code == 200 and again.json()["cancel_at_period_end"] is True, again.text
    db.expire_all()
    sub = db.scalar(select(Subscription).where(Subscription.tenant_id == t.id))
    assert sub.cancel_at_period_end is True
    res = bsvc.run_lifecycle(db, sub.current_period_end + timedelta(minutes=1))
    db.expire_all()
    assert db.scalar(select(Subscription).where(Subscription.tenant_id == t.id)).status == "cancelled", res
    assert db.get(Tenant, t.id).status == "suspended"  # kept, not deleted


def test_upgrade_pays_to_apply_and_downgrade_is_scheduled(admin, client, cust, db):
    make_plan(client, "basic", monthly=30000, yearly=300000)
    make_plan(client, "pro", monthly=90000, yearly=900000)
    c, t = cust()
    inv = c.post("/api/v1/billing/checkout", json={"plan_code": "basic"}).json()["invoice"]
    pay_manually(client, inv["id"])
    r = c.post("/api/v1/billing/subscription/change", json={"plan_code": "pro"}).json()
    assert r["mode"] == "pay_to_apply" and r["invoice"]["purpose"] == "upgrade"
    pr = r["proration"]
    assert pr["net_paise"] == pr["charge_paise"] - pr["credit_paise"] and pr["net_paise"] > 0
    assert r["invoice"]["taxable_paise"] == pr["net_paise"]
    assert c.get("/api/v1/billing/subscription").json()["subscription"]["plan"]["code"] == "basic"  # not yet
    pay_manually(client, r["invoice"]["id"], "UTR-UP")
    assert c.get("/api/v1/billing/subscription").json()["subscription"]["plan"]["code"] == "pro"
    d = c.post("/api/v1/billing/subscription/change", json={"plan_code": "basic"}).json()
    assert d["mode"] == "scheduled" and d["invoice"] is None
    sub = db.scalar(select(Subscription).where(Subscription.tenant_id == t.id))
    bsvc.run_lifecycle(db, sub.current_period_end - timedelta(days=1))
    ren = db.scalar(select(Invoice).where(Invoice.tenant_id == t.id, Invoice.purpose == "renewal"))
    assert ren.taxable_paise == 30000
    pay_manually(client, str(ren.id), "UTR-REN")
    assert c.get("/api/v1/billing/subscription").json()["subscription"]["plan"]["code"] == "basic"


# ------------------------------------------------------------------------------------------------ coupons
def mkcoupon(client, **kw):
    body = {"code": "SAVE10", "percent_bps": 1000, **kw}
    r = client.post("/api/v1/billing/coupons", json=body)
    assert r.status_code == 201, r.text


def test_coupon_validation_rules(admin, client, cust, db):
    make_plan(client, "pro")
    make_plan(client, "other")
    c, t = cust()
    soon, past = (datetime.now(UTC) + timedelta(days=1)).isoformat(), (datetime.now(UTC) - timedelta(days=1)).isoformat()
    mkcoupon(client, code="PERCENT")
    mkcoupon(client, code="FLAT", percent_bps=None, amount_paise=500000)
    mkcoupon(client, code="EXPIRED", valid_until=past)
    mkcoupon(client, code="FUTURE", valid_from=soon)
    mkcoupon(client, code="ONLYOTHER", plan_codes=["other"])
    mkcoupon(client, code="ONCE", max_redemptions=1)

    def check(code, plan="pro"):
        return c.post("/api/v1/billing/coupons/validate", json={"code": code, "plan_code": plan})

    assert check("PERCENT").json()["discount_paise"] == 10000
    assert check("FLAT").json()["discount_paise"] == 100000  # capped at the price
    assert check("EXPIRED").json()["error"]["code"] == "coupon_expired"
    assert check("FUTURE").json()["error"]["code"] == "coupon_not_started"
    assert check("ONLYOTHER").json()["error"]["code"] == "coupon_not_applicable"
    assert check("ONLYOTHER", "other").status_code == 200
    assert check("NOPE").json()["error"]["code"] == "coupon_invalid"
    assert client.post("/api/v1/billing/coupons", json={"code": "BAD", "percent_bps": 100, "amount_paise": 5}).status_code == 422
    # redemption limits: reserved at checkout, per-tenant limit 1, global limit, released on void
    inv = c.post("/api/v1/billing/checkout", json={"plan_code": "pro", "coupon_code": "once"}).json()["invoice"]
    assert inv["discount_paise"] == 10000 and inv["total_paise"] == int(90000 * 1.18)
    c2, _ = cust("two@other.example.com")
    assert c2.post("/api/v1/billing/coupons/validate", json={"code": "ONCE", "plan_code": "pro"}).json()["error"]["code"] == "coupon_exhausted"
    assert client.post(f"/api/v1/billing/invoices/{inv['id']}/void").status_code == 200
    assert db.scalar(select(CouponRedemption.status)) == "released"
    assert c2.post("/api/v1/billing/coupons/validate", json={"code": "ONCE", "plan_code": "pro"}).status_code == 200
    inv = c.post("/api/v1/billing/checkout", json={"plan_code": "pro", "coupon_code": "PERCENT"}).json()["invoice"]
    pay_manually(client, inv["id"])
    assert db.scalar(select(CouponRedemption.status).where(CouponRedemption.invoice_id == inv["id"])) == "redeemed"
    assert db.scalar(select(Coupon.code).where(Coupon.code == "PERCENT")) == "PERCENT"
    assert c.post("/api/v1/billing/coupons/validate", json={"code": "PERCENT", "plan_code": "pro"}).json()["error"]["code"] == "coupon_already_used"
    assert c.post("/api/v1/billing/coupons", json={"code": "HACK", "percent_bps": 10000}).status_code == 403


def test_full_discount_activates_without_payment(admin, client, cust, db):
    make_plan(client, "pro")
    mkcoupon(client, code="FREE", percent_bps=10000)
    c, t = cust()
    r = c.post("/api/v1/billing/checkout", json={"plan_code": "pro", "coupon_code": "FREE"}).json()
    assert r["invoice"]["total_paise"] == 0 and r["invoice"]["status"] == "paid"
    db.expire_all()
    assert db.get(Tenant, t.id).status == "active"


# ------------------------------------------------------------------------------------------------ numbering
def test_invoice_numbers_unique_and_gapless_under_concurrency(admin, client, cust, db):
    t = provision_tenant(db, "Race Co", None, "r@x.example.com", status="active")
    db.commit()
    errors: list[Exception] = []

    def worker():
        try:
            for _ in range(5):
                with SessionLocal() as s:
                    tenant = s.get(Tenant, t.id)
                    bsvc.create_invoice(s, tenant, "manual", [bsvc.line("x", 100)])
                    s.commit()
        except Exception as e:  # pragma: no cover
            errors.append(e)

    threads = [threading.Thread(target=worker) for _ in range(6)]
    [x.start() for x in threads]
    [x.join() for x in threads]
    assert not errors
    nums = sorted(db.scalars(select(Invoice.number).where(Invoice.tenant_id == t.id)).all())
    assert len(nums) == 30 == len(set(nums))
    assert [int(n.rsplit("/", 1)[1]) for n in nums] == list(range(1, 31))
    assert nums[0].startswith("INV/") and bsvc.financial_year(datetime(2026, 3, 31)) == "2025-26" and bsvc.financial_year(datetime(2026, 4, 1)) == "2026-27"
    # numbering is configurable
    assert client.put("/api/v1/billing/settings", json={"invoice_prefix": "KVL"}).json()["invoice_prefix"] == "KVL"
    inv = bsvc.create_invoice(db, db.get(Tenant, t.id), "manual", [bsvc.line("x", 100)])
    assert inv.number.startswith("KVL/")


# --------------------------------------------------------------------------------------- isolation / auth
def test_tenant_billing_isolation_and_invoice_download_authorization(admin, client, cust, db, make_user, make_client):
    make_plan(client, "pro")
    a, ta = cust("a@a.example.com")
    b, tb = cust("b@b.example.com")
    ia = a.post("/api/v1/billing/checkout", json={"plan_code": "pro"}).json()["invoice"]
    ib = b.post("/api/v1/billing/checkout", json={"plan_code": "pro"}).json()["invoice"]
    assert [i["id"] for i in a.get("/api/v1/billing/invoices").json()["items"]] == [ia["id"]]
    assert a.get(f"/api/v1/billing/invoices/{ia['id']}").status_code == 200
    for path in ("", "/download"):
        assert a.get(f"/api/v1/billing/invoices/{ib['id']}{path}").status_code == 404
    assert a.post(f"/api/v1/billing/invoices/{ib['id']}/pay").status_code == 404
    assert a.get("/api/v1/billing/invoices", params={"tenant_id": str(tb.id)}).json()["items"] == [{**i} for i in a.get("/api/v1/billing/invoices", params={"tenant_id": str(tb.id)}).json()["items"]] == []
    assert a.get("/api/v1/billing/subscription", params={"tenant_id": str(tb.id)}).status_code == 404
    assert a.post("/api/v1/billing/checkout", json={"plan_code": "pro", "tenant_id": str(tb.id)}).status_code == 404
    dl = a.get(f"/api/v1/billing/invoices/{ia['id']}/download")
    assert dl.status_code == 200 and "attachment" in dl.headers["content-disposition"] and ia["number"] in dl.text and "CGST" in dl.text
    assert "default-src 'none'" in dl.headers["content-security-policy"]
    assert client.get(f"/api/v1/billing/invoices/{ib['id']}/download").status_code == 200  # platform may
    anon = make_client()
    assert anon.get(f"/api/v1/billing/invoices/{ia['id']}/download").status_code == 401
    # a customer cannot reach admin reports/refunds/manual payments
    assert a.get("/api/v1/billing/reports/revenue").status_code == 403
    assert a.post(f"/api/v1/billing/invoices/{ia['id']}/manual-payment", json={"reference": "fake-ref"}).status_code == 403
    assert a.get("/api/v1/subscriptions").status_code == 403


def test_download_escapes_html(admin, client, cust, db):
    make_plan(client, "pro")
    c, t = cust()
    assert c.put("/api/v1/billing/profile", json={"legal_name": "<script>alert(1)</script>", "state_code": "27", "gstin": "27ABCDE1234F1Z5"}).status_code == 200
    inv = c.post("/api/v1/billing/checkout", json={"plan_code": "pro"}).json()["invoice"]
    assert inv["igst_paise"] == 18000 and inv["cgst_paise"] == 0  # inter-state: seller 29, buyer 27
    body = c.get(f"/api/v1/billing/invoices/{inv['id']}/download").text
    assert "<script>" not in body and "&lt;script&gt;" in body and "IGST" in body
    assert c.put("/api/v1/billing/profile", json={"gstin": "bad"}).status_code == 422


# ------------------------------------------------------------------------------------------------ reports
def test_mrr_arr_churn_formulas(admin, client, cust, db):
    make_plan(client, "pro", monthly=100000, yearly=1000000)
    c1, t1 = cust("m@m.example.com")
    c2, t2 = cust("y@y.example.com")
    i1 = c1.post("/api/v1/billing/checkout", json={"plan_code": "pro", "interval": "monthly"}).json()["invoice"]
    i2 = c2.post("/api/v1/billing/checkout", json={"plan_code": "pro", "interval": "yearly"}).json()["invoice"]
    pay_manually(client, i1["id"], "UTR-M")
    pay_manually(client, i2["id"], "UTR-Y")
    rep = client.get("/api/v1/billing/reports/revenue").json()
    assert rep["mrr_paise"] == 100000 + round(1000000 / 12) == 183333
    assert rep["arr_paise"] == rep["mrr_paise"] * 12 and rep["paying_subscriptions"] == 2
    assert rep["revenue_ex_tax_paise"] == 1100000 and rep["billed_incl_tax_paise"] == int(1100000 * 1.18)
    assert set(rep["formulas"]) >= {"mrr", "arr", "logo_churn", "revenue_churn"}
    # churn: end the monthly subscription, measured over a window starting after both began
    sub = db.scalar(select(Subscription).where(Subscription.tenant_id == t1.id))
    now = datetime.now(UTC)
    sub.started_at = now - timedelta(days=60)
    sub2 = db.scalar(select(Subscription).where(Subscription.tenant_id == t2.id))
    sub2.started_at = now - timedelta(days=60)
    db.commit()
    bsvc.cancel_subscription(db, sub, True, None)
    db.commit()
    ch = bsvc.churn_report(db, now - timedelta(days=30), now + timedelta(days=1))
    assert ch["opening_subscriptions"] == 2 and ch["churned_subscriptions"] == 1 and ch["logo_churn_bps"] == 5000
    assert ch["revenue_churn_bps"] == round(100000 * 10000 / 183333)
    # past_due subscriptions still count towards MRR; cancelled ones do not
    assert client.get("/api/v1/billing/reports/revenue").json()["mrr_paise"] == 83333


def test_settings_and_catalog_validation(admin, client):
    assert client.post("/api/v1/plans", json={"code": "Bad Code", "name": "x", "price_monthly_paise": 1, "price_yearly_paise": 1}).status_code == 422
    assert client.post("/api/v1/plans", json={"code": "okcode", "name": "Ok", "price_monthly_paise": -1, "price_yearly_paise": 1}).status_code == 422
    assert client.post("/api/v1/plans", json={"code": "okcode", "name": "Ok", "price_monthly_paise": 1, "price_yearly_paise": 1, "limits": {"bogus": 1}}).status_code == 422
    make_plan(client, "dup")
    assert client.post("/api/v1/plans", json={"code": "dup", "name": "Dup", "price_monthly_paise": 1, "price_yearly_paise": 1}).status_code == 409
    assert client.post("/api/v1/plans/addons", json={"code": "extra-site", "name": "Extra site", "price_monthly_paise": 5000, "price_yearly_paise": 50000, "limits": {"websites": 1}}).status_code == 201
    assert client.put("/api/v1/billing/settings", json={"invoice_prefix": "bad prefix"}).status_code == 422
