import hashlib
import hmac
import json
import uuid

from app.core.config import get_settings

WH_SECRET = "whsec_test_secret"
KEY_ID, KEY_SECRET = "rzp_test_abc123", "rzp_test_secret_value"


class FakeGateway:
    def __init__(self):
        self.orders: dict[str, dict] = {}
        self.payments: dict[str, dict] = {}
        self.refunds: list[dict] = []
        self.refund_status = "pending"

    def create_order(self, amount_paise, receipt, notes):
        oid = f"order_{uuid.uuid4().hex[:14]}"
        self.orders[oid] = {"id": oid, "amount": amount_paise, "currency": "INR", "receipt": receipt, "notes": notes}
        return self.orders[oid]

    def fetch_payment(self, payment_id):
        return self.payments.get(payment_id, {"id": payment_id, "status": "created"})

    def fetch_order_payments(self, order_id):
        return [p for p in self.payments.values() if p.get("order_id") == order_id]

    def refund(self, payment_id, amount_paise, notes):
        r = {"id": f"rfnd_{uuid.uuid4().hex[:12]}", "payment_id": payment_id, "amount": amount_paise, "status": self.refund_status}
        self.refunds.append(r)
        return r


def enable_razorpay(monkeypatch, gateway=None):
    s = get_settings()
    monkeypatch.setattr(s, "razorpay_webhook_secret", WH_SECRET)
    monkeypatch.setattr(s, "razorpay_key_id", KEY_ID)
    monkeypatch.setattr(s, "razorpay_key_secret", KEY_SECRET)
    gw = gateway or FakeGateway()
    import app.services.razorpay as r

    monkeypatch.setattr(r, "get_gateway", lambda: gw)
    return gw


def sign_body(raw: bytes, secret: str = WH_SECRET) -> str:
    return hmac.new(secret.encode(), raw, hashlib.sha256).hexdigest()


def event(etype, *, order_id=None, payment_id="pay_test0001", amount=None, refund_id=None, error=None):
    pay = {"id": payment_id, "order_id": order_id, "amount": amount, "currency": "INR", "status": etype.split(".")[-1]}
    if error:
        pay |= {"error_code": "BAD_REQUEST_ERROR", "error_description": error}
    payload = {"entity": "event", "event": etype, "payload": {"payment": {"entity": pay}}}
    if refund_id:
        payload["payload"]["refund"] = {"entity": {"id": refund_id, "payment_id": payment_id, "amount": amount}}
    return payload


def post_event(client, payload, event_id=None, secret=WH_SECRET, tamper=False):
    raw = json.dumps(payload).encode()
    sig = sign_body(raw, secret)
    if tamper:
        raw = raw.replace(b'"amount": ', b'"amount": 1')
    return client.post(
        "/api/v1/payments/razorpay/webhook", content=raw,
        headers={"X-Razorpay-Signature": sig, "X-Razorpay-Event-Id": event_id or f"evt_{uuid.uuid4().hex[:12]}", "Content-Type": "application/json"},
    )


def make_plan(client, code="pro", monthly=100000, yearly=1000000, **kw):
    body = {"code": code, "name": code.title(), "price_monthly_paise": monthly, "price_yearly_paise": yearly, "limits": {"websites": 10, "disk_mb": 20000}, "features": {"staging": True}, "trial_days": 7, **kw}
    r = client.post("/api/v1/plans", json=body)
    assert r.status_code == 201, r.text
    return r.json()
