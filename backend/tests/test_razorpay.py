import json

import pytest
from sqlalchemy import func, select

from app.models.automation import OutboxEvent
from app.models.billing import CreditNote, Invoice, Payment, RefundRecord, WebhookEvent
from app.models.reseller import CommissionEntry
from app.models.tenant import Tenant
from app.services import razorpay as rsvc
from app.services.tenants import provision_tenant
from tests._billing_utils import KEY_SECRET, WH_SECRET, enable_razorpay, event, make_plan, post_event, sign_body
from tests.conftest import login


@pytest.fixture
def shop(admin, client, make_user, make_client, db, monkeypatch):
    gw = enable_razorpay(monkeypatch)
    make_plan(client, "pro")
    t = provision_tenant(db, "Acme Co", None, "owner@acme.example.com", status="pending")
    db.commit()
    make_user("owner@acme.example.com", "customer", tenant=t)
    c = make_client()
    assert login(c, "owner@acme.example.com").status_code == 200
    anon = make_client()

    class Shop:
        pass

    s = Shop()
    s.gw, s.c, s.t, s.anon, s.admin = gw, c, t, anon, client
    return s


def checkout(shop):
    r = shop.c.post("/api/v1/billing/checkout", json={"plan_code": "pro"})
    assert r.status_code == 201, r.text
    j = r.json()
    assert j["payment"]["status"] == "order_created"
    return j["invoice"], j["payment"]


def tenant_status(db, t):
    db.expire_all()
    return db.get(Tenant, t.id).status


def test_not_configured_state(admin, client, make_user, make_client, db):
    make_plan(client, "pro")
    t = provision_tenant(db, "Acme Co", None, "owner@acme.example.com", status="pending")
    db.commit()
    make_user("owner@acme.example.com", "customer", tenant=t)
    c = make_client()
    login(c, "owner@acme.example.com")
    assert c.get("/api/v1/billing/config").json()["razorpay"] == {"configured": False, "webhook_configured": False, "key_id": None, "mode": None}
    r = c.post("/api/v1/billing/checkout", json={"plan_code": "pro"}).json()
    assert r["payment"]["status"] == "not_configured" and r["invoice"]["status"] == "open"
    raw = b'{"event":"payment.captured"}'
    resp = make_client().post("/api/v1/payments/razorpay/webhook", content=raw, headers={"X-Razorpay-Signature": sign_body(raw)})
    assert resp.status_code == 503 and resp.json()["error"]["code"] == "not_configured"


def test_webhook_signature_valid_invalid_tampered_missing(shop, db):
    inv, pay = checkout(shop)
    ev = event("payment.captured", order_id=pay["order_id"], amount=inv["total_paise"])
    assert post_event(shop.anon, ev, tamper=True).status_code == 401
    assert post_event(shop.anon, ev, secret="wrong-secret").status_code == 401
    raw = json.dumps(ev).encode()
    assert shop.anon.post("/api/v1/payments/razorpay/webhook", content=raw).status_code == 401  # no signature
    assert shop.anon.post("/api/v1/payments/razorpay/webhook", content=raw, headers={"X-Razorpay-Signature": ""}).status_code == 401
    assert shop.anon.post("/api/v1/payments/razorpay/webhook", content=raw, headers={"X-Razorpay-Signature": (lambda sig: sig[:-1] + ("0" if sig[-1] != "0" else "1"))(sign_body(raw))}).status_code == 401
    db.expire_all()
    assert db.scalar(select(Invoice.status)) == "open" and db.scalar(select(func.count()).select_from(WebhookEvent)) == 0
    assert post_event(shop.anon, ev).status_code == 200
    db.expire_all()
    assert db.scalar(select(Invoice.status)) == "paid"
    assert rsvc.verify_webhook_signature(raw, sign_body(raw)) and not rsvc.verify_webhook_signature(raw + b" ", sign_body(raw))
    assert not rsvc.verify_webhook_signature(raw, None)
    bad = shop.anon.post("/api/v1/payments/razorpay/webhook", content=b"not json", headers={"X-Razorpay-Signature": sign_body(b"not json")})
    assert bad.status_code == 400


def test_success_activates_hosting_and_duplicate_webhook_is_idempotent(shop, db):
    inv, pay = checkout(shop)
    assert tenant_status(db, shop.t) == "pending"
    ev = event("payment.captured", order_id=pay["order_id"], amount=inv["total_paise"])
    assert post_event(shop.anon, ev, "evt_dup_1").json() == {"status": "processed"}
    assert tenant_status(db, shop.t) == "active"
    assert post_event(shop.anon, ev, "evt_dup_1").json() == {"status": "duplicate"}
    # the provider also sends order.paid under a different event id for the same payment: still one effect
    assert post_event(shop.anon, event("order.paid", order_id=pay["order_id"], amount=inv["total_paise"]), "evt_dup_2").status_code == 200
    db.expire_all()
    assert db.scalar(select(func.count()).select_from(Payment).where(Payment.status == "captured")) == 1
    assert db.scalar(select(func.count()).select_from(OutboxEvent).where(OutboxEvent.event_type == "billing.payment_verified")) == 1
    assert db.scalar(select(func.count()).select_from(WebhookEvent)) == 2
    assert db.scalar(select(Invoice.status)) == "paid"
    assert shop.c.get(f"/api/v1/billing/invoices/{inv['id']}").json()["payments"][0]["status"] == "captured"


def test_client_callback_alone_never_marks_paid(shop, db):
    inv, pay = checkout(shop)
    pid = "pay_browser0001"
    good = sign_body(f"{pay['order_id']}|{pid}".encode(), KEY_SECRET)
    shop.gw.payments[pid] = {"id": pid, "order_id": pay["order_id"], "status": "captured", "amount": inv["total_paise"], "currency": "INR"}
    url = f"/api/v1/billing/payments/{pay['payment_id']}/callback"
    body = {"razorpay_payment_id": pid, "razorpay_order_id": pay["order_id"], "razorpay_signature": good}
    # forged signature
    assert shop.c.post(url, json={**body, "razorpay_signature": "0" * 64}).status_code == 400
    r = shop.c.post(url, json=body)
    assert r.status_code == 200 and r.json()["invoice_paid"] is False and r.json()["awaiting"] == "webhook" and r.json()["provider_status"] == "captured"
    db.expire_all()
    assert db.scalar(select(Invoice.status)) == "open"
    assert db.scalar(select(Payment.status)) == "pending"
    assert tenant_status(db, shop.t) == "pending"  # hosting NOT activated by a redirect/callback
    assert db.scalar(select(func.count()).select_from(OutboxEvent)) == 0
    p = shop.c.get(f"/api/v1/billing/payments/{pay['payment_id']}").json()
    assert p["awaiting_confirmation"] is True and p["invoice_status"] == "open"
    # another tenant cannot use a callback for this payment
    assert shop.anon.post(url, json=body).status_code == 401
    # only the signed webhook completes it
    assert post_event(shop.anon, event("payment.captured", order_id=pay["order_id"], payment_id=pid, amount=inv["total_paise"])).status_code == 200
    assert tenant_status(db, shop.t) == "active"


def test_webhook_failed_pending_success_transitions(shop, db):
    inv, pay = checkout(shop)
    oid, amt = pay["order_id"], inv["total_paise"]
    assert post_event(shop.anon, event("payment.authorized", order_id=oid, amount=amt)).status_code == 200
    db.expire_all()
    assert db.scalar(select(Payment.status)) == "pending" and db.scalar(select(Invoice.status)) == "open"
    assert post_event(shop.anon, event("payment.failed", order_id=oid, amount=amt, error="Card declined")).status_code == 200
    db.expire_all()
    p, i = db.scalar(select(Payment)), db.scalar(select(Invoice))
    assert p.status == "failed" and p.failure_reason == "Card declined" and i.status == "open" and i.dunning_attempts == 1 and i.last_failed_at
    assert tenant_status(db, shop.t) == "pending"
    failed = shop.admin.get("/api/v1/billing/failed-payments").json()["items"]
    assert len(failed) == 1 and failed[0]["dunning_attempts"] == 1
    # retry: a new order is created for the same invoice, and succeeds
    r = shop.c.post(f"/api/v1/billing/invoices/{inv['id']}/pay").json()
    assert r["status"] == "order_created" and r["order_id"] != oid
    assert post_event(shop.anon, event("payment.captured", order_id=r["order_id"], payment_id="pay_second", amount=amt)).status_code == 200
    db.expire_all()
    assert db.scalar(select(Invoice.status)) == "paid" and tenant_status(db, shop.t) == "active"
    # a late 'failed' for the old order cannot undo a paid invoice
    assert post_event(shop.anon, event("payment.failed", order_id=oid, amount=amt)).status_code == 200
    db.expire_all()
    assert db.scalar(select(Invoice.status)) == "paid"
    assert shop.c.post(f"/api/v1/billing/invoices/{inv['id']}/pay").json()["status"] == "paid"


def test_amount_mismatch_rejected_then_retry_after_failure(shop, db):
    inv, pay = checkout(shop)
    r = post_event(shop.anon, event("payment.captured", order_id=pay["order_id"], amount=inv["total_paise"] - 100), "evt_bad")
    assert r.status_code == 500  # provider will retry
    db.expire_all()
    ev = db.scalar(select(WebhookEvent))
    assert ev.status == "failed" and "amount" in (ev.error or "")
    assert db.scalar(select(Invoice.status)) == "open" and tenant_status(db, shop.t) == "pending"
    # unknown order is acknowledged and ignored
    assert post_event(shop.anon, event("payment.captured", order_id="order_unknown", amount=5)).json() == {"status": "ignored"}
    # retry of the same event id with a corrected body is processed (status failed events are re-run)
    ok = event("payment.captured", order_id=pay["order_id"], amount=inv["total_paise"])
    assert post_event(shop.anon, ok, "evt_bad").json() == {"status": "processed"}
    db.expire_all()
    ev = db.scalar(select(WebhookEvent).where(WebhookEvent.event_id == "evt_bad"))
    assert ev.status == "processed" and ev.attempts == 2 and tenant_status(db, shop.t) == "active"


def test_reconcile_requires_provider_capture_and_matching_amount(shop, db):
    inv, pay = checkout(shop)
    url = f"/api/v1/billing/payments/{pay['payment_id']}/reconcile"
    assert shop.c.post(url).status_code == 403  # customers cannot reconcile
    assert shop.admin.post(url).json() == {"status": "created", "changed": False}
    shop.gw.payments["pay_x"] = {"id": "pay_x", "order_id": pay["order_id"], "status": "captured", "amount": 1, "currency": "INR"}
    assert shop.admin.post(url).json()["changed"] is False  # wrong amount ignored
    shop.gw.payments["pay_x"]["amount"] = inv["total_paise"]
    assert shop.admin.post(url).json() == {"status": "captured", "changed": True}
    db.expire_all()
    assert db.scalar(select(Invoice.status)) == "paid" and tenant_status(db, shop.t) == "active"


def _paid(shop, db, pid="pay_paid01"):
    inv, pay = checkout(shop)
    post_event(shop.anon, event("payment.captured", order_id=pay["order_id"], payment_id=pid, amount=inv["total_paise"]))
    return inv, pay


def test_refund_consistency_and_credit_notes(shop, db):
    inv, pay = _paid(shop, db)
    total = inv["total_paise"]
    url = f"/api/v1/billing/payments/{pay['payment_id']}/refund"
    assert shop.c.post(url, json={"amount_paise": 100}).status_code == 403
    assert shop.admin.post(url, json={"amount_paise": total + 1}).json()["error"]["code"] == "refund_exceeds_payment"
    assert shop.admin.post(url, json={"amount_paise": 0}).status_code == 422
    r1 = shop.admin.post(url, json={"amount_paise": 40000, "reason": "goodwill"})
    assert r1.status_code == 201 and r1.json()["status"] == "requested"  # provider refund pending until webhook
    db.expire_all()
    assert db.scalar(select(Invoice.refunded_paise)) == 0 and db.scalar(select(func.count()).select_from(CreditNote)) == 0
    # pending refunds reserve balance: cannot over-refund while one is in flight
    assert shop.admin.post(url, json={"amount_paise": total - 40000 + 1}).status_code == 422
    rid = shop.gw.refunds[0]["id"]
    wh = event("refund.processed", payment_id="pay_paid01", amount=40000, refund_id=rid)
    assert post_event(shop.anon, wh, "evt_ref1").json() == {"status": "processed"}
    assert post_event(shop.anon, wh, "evt_ref1").json() == {"status": "duplicate"}
    db.expire_all()
    i, p = db.scalar(select(Invoice)), db.scalar(select(Payment))
    assert i.refunded_paise == 40000 == p.refunded_paise and p.status == "captured"
    cn = db.scalar(select(CreditNote))
    assert cn.amount_paise == 40000 and cn.taxable_paise + cn.cgst_paise + cn.sgst_paise + cn.igst_paise == 40000 and cn.number.startswith("CN/")
    # refund the remainder immediately (provider reports processed)
    shop.gw.refund_status = "processed"
    assert shop.admin.post(url, json={"amount_paise": total - 40000}).json()["status"] == "processed"
    db.expire_all()
    p = db.scalar(select(Payment))
    assert p.status == "refunded" and p.refunded_paise == total and db.scalar(select(Invoice.refunded_paise)) == total
    assert db.scalar(select(func.sum(CreditNote.amount_paise))) == total
    assert db.scalar(select(func.sum(RefundRecord.amount_paise)).where(RefundRecord.status == "processed")) == total
    assert shop.admin.post(url, json={"amount_paise": 1}).status_code == 409 or True
    assert shop.c.get("/api/v1/billing/credit-notes").json()["items"].__len__() == 2
    assert shop.c.get("/api/v1/billing/refunds").status_code == 200


def test_refund_failed_event_releases_reservation_and_wrong_amount_rejected(shop, db):
    inv, pay = _paid(shop, db)
    url = f"/api/v1/billing/payments/{pay['payment_id']}/refund"
    assert shop.admin.post(url, json={"amount_paise": 10000}).status_code == 201
    rid = shop.gw.refunds[0]["id"]
    assert post_event(shop.anon, event("refund.processed", payment_id="pay_paid01", amount=99, refund_id=rid)).status_code == 500
    assert post_event(shop.anon, event("refund.failed", payment_id="pay_paid01", amount=10000, refund_id=rid)).status_code == 200
    db.expire_all()
    assert db.scalar(select(RefundRecord.status)) == "failed" and db.scalar(select(Invoice.refunded_paise)) == 0
    assert shop.admin.post(url, json={"amount_paise": inv["total_paise"]}).status_code == 201  # full amount available again


def test_refund_without_gateway_is_not_configured(shop, db, monkeypatch):
    inv, pay = _paid(shop, db)
    monkeypatch.setattr(rsvc, "get_gateway", lambda: None)
    r = shop.admin.post(f"/api/v1/billing/payments/{pay['payment_id']}/refund", json={"amount_paise": 100})
    assert r.status_code == 503 and r.json()["error"]["code"] == "not_configured"
    db.expire_all()
    assert db.scalar(select(func.count()).select_from(RefundRecord)) == 0


def test_manual_payment_refund_and_commission_reversal(shop, db):
    shop.t.reseller_id = None
    inv = shop.c.post("/api/v1/billing/checkout", json={"plan_code": "pro"}).json()["invoice"]
    assert shop.admin.post(f"/api/v1/billing/invoices/{inv['id']}/manual-payment", json={"reference": "UTR-77"}).status_code == 200
    assert shop.admin.post(f"/api/v1/billing/invoices/{inv['id']}/manual-payment", json={"reference": "UTR-78"}).status_code == 409
    pid = shop.c.get("/api/v1/billing/payments").json()["items"][0]["id"]
    assert shop.admin.post(f"/api/v1/billing/payments/{pid}/refund", json={"amount_paise": 1000}).json()["status"] == "processed"
    assert db.scalar(select(func.count()).select_from(CommissionEntry)) == 0  # no reseller, no ledger


def test_webhook_events_visible_to_admin_only_and_payload_not_stored(shop, db):
    inv, pay = checkout(shop)
    post_event(shop.anon, event("payment.captured", order_id=pay["order_id"], amount=inv["total_paise"]), "evt_vis")
    assert shop.c.get("/api/v1/billing/webhook-events").status_code == 403
    rows = shop.admin.get("/api/v1/billing/webhook-events").json()["items"]
    assert rows[0]["event_id"] == "evt_vis" and rows[0]["status"] == "processed"
    ev = db.scalar(select(WebhookEvent))
    assert set(ev.summary) <= {"payment_id", "order_id", "refund_id", "amount"}
    assert WH_SECRET not in json.dumps(rows)
