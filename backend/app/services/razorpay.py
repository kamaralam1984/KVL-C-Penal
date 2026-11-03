"""Razorpay (test mode first). Orders, signed webhook verification, idempotent event processing, reconciliation, refunds.

Safety rules enforced here:
* A payment is captured ONLY by (a) a webhook whose HMAC-SHA256 signature over the raw body verifies, or
  (b) an admin-triggered server-side fetch from the Razorpay API (reconcile). A browser redirect / checkout handler never marks anything paid.
* Amount and currency in the event must match our order, or the event is rejected.
* Every provider event id is stored once; replays are acknowledged without re-processing.
"""
import hashlib
import hmac
import json
import logging
import uuid
from datetime import UTC, datetime
from typing import Any, Protocol

import httpx
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.deps import api_error
from app.models.billing import Invoice, Payment, RefundRecord, WebhookEvent
from app.services import billing

log = logging.getLogger("kvl.razorpay")


def configured() -> dict[str, bool]:
    s = get_settings()
    return {"api": bool(s.razorpay_key_id and s.razorpay_key_secret), "webhook": bool(s.razorpay_webhook_secret)}


class Gateway(Protocol):
    def create_order(self, amount_paise: int, receipt: str, notes: dict[str, str]) -> dict[str, Any]: ...
    def fetch_payment(self, payment_id: str) -> dict[str, Any]: ...
    def fetch_order_payments(self, order_id: str) -> list[dict[str, Any]]: ...
    def refund(self, payment_id: str, amount_paise: int, notes: dict[str, str]) -> dict[str, Any]: ...


class RazorpayClient:
    """Thin httpx wrapper (basic auth with key id / secret from settings). Tests substitute a fake Gateway."""

    def __init__(self, key_id: str, key_secret: str, base: str):
        self.auth, self.base = (key_id, key_secret), base.rstrip("/")

    def _req(self, method: str, path: str, **kw) -> dict[str, Any]:
        try:
            r = httpx.request(method, f"{self.base}{path}", auth=self.auth, timeout=15.0, **kw)
        except httpx.HTTPError as e:
            raise api_error(502, "provider_unreachable", f"Payment provider unreachable: {type(e).__name__}") from None
        if r.status_code >= 400:
            msg = (r.json().get("error", {}).get("description") if r.headers.get("content-type", "").startswith("application/json") else None) or "Provider rejected the request."
            raise api_error(502, "provider_error", msg)
        return r.json()

    def create_order(self, amount_paise: int, receipt: str, notes: dict[str, str]) -> dict[str, Any]:
        return self._req("POST", "/orders", json={"amount": amount_paise, "currency": "INR", "receipt": receipt[:40], "notes": notes})

    def fetch_payment(self, payment_id: str) -> dict[str, Any]:
        return self._req("GET", f"/payments/{payment_id}")

    def fetch_order_payments(self, order_id: str) -> list[dict[str, Any]]:
        return self._req("GET", f"/orders/{order_id}/payments").get("items", [])

    def refund(self, payment_id: str, amount_paise: int, notes: dict[str, str]) -> dict[str, Any]:
        return self._req("POST", f"/payments/{payment_id}/refund", json={"amount": amount_paise, "notes": notes})


def get_gateway() -> Gateway | None:
    s = get_settings()
    if not (s.razorpay_key_id and s.razorpay_key_secret):
        return None
    return RazorpayClient(s.razorpay_key_id, s.razorpay_key_secret, s.razorpay_api_base)


def require_gateway() -> Gateway:
    g = get_gateway()
    if g is None:
        raise api_error(503, "not_configured", "Payments are not configured.")
    return g


# ---------------------------------------------------------------------------------------- signatures
def verify_webhook_signature(raw: bytes, signature: str | None) -> bool:
    secret = get_settings().razorpay_webhook_secret
    if not secret or not signature:
        return False
    expected = hmac.new(secret.encode(), raw, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, signature.strip().lower())


def verify_checkout_signature(order_id: str, payment_id: str, signature: str) -> bool:
    """Razorpay checkout handler signature. Proves the browser callback is genuine, NOT that money moved."""
    secret = get_settings().razorpay_key_secret
    if not secret or not signature:
        return False
    expected = hmac.new(secret.encode(), f"{order_id}|{payment_id}".encode(), hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, signature.strip().lower())


# -------------------------------------------------------------------------------------------- orders
def create_order(db: Session, inv: Invoice, gateway: Gateway) -> Payment:
    if inv.status != "open":
        raise api_error(409, "invoice_not_open", "Invoice is not open.")
    if inv.total_paise <= 0:
        raise api_error(409, "nothing_to_pay", "Invoice has no amount due.")
    existing = db.scalar(select(Payment).where(Payment.invoice_id == inv.id, Payment.provider == "razorpay", Payment.status.in_(("created", "pending"))).order_by(Payment.created_at.desc()))
    if existing:
        return existing
    order = gateway.create_order(inv.total_paise, inv.number.replace("/", "-"), {"invoice_id": str(inv.id), "tenant_id": str(inv.tenant_id)})
    if int(order.get("amount", -1)) != inv.total_paise or order.get("currency") != "INR":
        raise api_error(502, "provider_error", "Provider returned an unexpected order.")
    p = Payment(tenant_id=inv.tenant_id, invoice_id=inv.id, provider="razorpay", amount_paise=inv.total_paise, status="created", provider_order_id=order["id"])
    db.add(p)
    db.flush()
    return p


def note_client_callback(db: Session, p: Payment, payment_id: str, signature: str, gateway: Gateway | None) -> dict[str, Any]:
    """Browser says 'paid'. We record that and fetch the provider's view; the invoice stays unpaid until the signed webhook arrives."""
    if not verify_checkout_signature(p.provider_order_id or "", payment_id, signature):
        raise api_error(400, "invalid_signature", "Checkout signature is invalid.")
    p.callback_seen_at = datetime.now(UTC)
    if p.status == "created":
        p.status = "pending"
    p.provider_payment_id = p.provider_payment_id or payment_id
    provider_status = None
    if gateway is not None:
        try:
            provider_status = gateway.fetch_payment(payment_id).get("status")
        except Exception:  # best effort; the webhook decides
            provider_status = None
    return {"payment_status": p.status, "provider_status": provider_status, "awaiting": "webhook", "invoice_paid": False}


def reconcile_payment(db: Session, p: Payment, gateway: Gateway) -> dict[str, Any]:
    """Server-side verification against the provider API (admin triggered / scheduled). May capture; never trusts the browser."""
    if p.provider != "razorpay" or not p.provider_order_id:
        raise api_error(409, "not_reconcilable", "Only provider payments can be reconciled.")
    items = gateway.fetch_order_payments(p.provider_order_id)
    captured = next((i for i in items if i.get("status") == "captured" and int(i.get("amount", -1)) == p.amount_paise and i.get("currency") == "INR"), None)
    if captured:
        changed = billing.mark_payment_captured(db, p, captured["id"], int(captured["amount"]))
        return {"status": p.status, "changed": changed}
    failed = next((i for i in items if i.get("status") == "failed"), None)
    if failed and p.status in ("created", "pending"):
        billing.mark_payment_failed(db, p, failed.get("error_code"), failed.get("error_description"))
        return {"status": p.status, "changed": True}
    return {"status": p.status, "changed": False}


# ------------------------------------------------------------------------------------------ webhooks
def _entities(payload: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    p = payload.get("payload") or {}
    return ((p.get("payment") or {}).get("entity") or {}, (p.get("order") or {}).get("entity") or {}, (p.get("refund") or {}).get("entity") or {})


def process_event(db: Session, event_id: str, payload: dict[str, Any]) -> str:
    """Apply one verified event. Returns 'processed' or 'ignored'. Raises on unexpected failure (caller records + lets provider retry)."""
    etype = payload.get("event", "")
    pay, order, ref = _entities(payload)
    if etype in ("payment.authorized", "payment.captured", "payment.failed", "order.paid"):
        order_id = pay.get("order_id") or order.get("id")
        p = db.scalar(select(Payment).where(Payment.provider_order_id == order_id).with_for_update()) if order_id else None
        if p is None:
            return "ignored"
        pid = pay.get("id")
        amount = int(pay.get("amount", order.get("amount_paid", -1)))
        if pay and (pay.get("currency", "INR") != "INR"):
            raise api_error(409, "currency_mismatch", "Unexpected currency.")
        if etype == "payment.authorized":
            if p.status == "created":
                p.status = "pending"
            p.provider_payment_id = p.provider_payment_id or pid
            return "processed"
        if etype in ("payment.captured", "order.paid"):
            billing.mark_payment_captured(db, p, pid, amount)
            return "processed"
        billing.mark_payment_failed(db, p, pay.get("error_code"), pay.get("error_description"))
        return "processed"
    if etype in ("refund.processed", "refund.failed", "refund.created"):
        r = db.scalar(select(RefundRecord).where(RefundRecord.provider_refund_id == ref.get("id")).with_for_update()) if ref.get("id") else None
        if r is None:
            return "ignored"
        if int(ref.get("amount", r.amount_paise)) != r.amount_paise:
            raise api_error(409, "amount_mismatch", "Refund amount mismatch.")
        if etype == "refund.processed":
            billing.finalize_refund(db, r)
        elif etype == "refund.failed" and r.status == "requested":
            r.status = "failed"
        return "processed"
    return "ignored"


def handle_webhook(db: Session, raw: bytes, signature: str | None, event_id: str | None) -> dict[str, Any]:
    """Entry for the HTTP route. Signature first; then dedupe on event id; then apply. 5xx is returned on processing errors so Razorpay retries."""
    if not configured()["webhook"]:
        raise api_error(503, "not_configured", "Webhook secret is not configured.")
    if not verify_webhook_signature(raw, signature):
        raise api_error(401, "invalid_signature", "Webhook signature is invalid.")
    try:
        payload = json.loads(raw)
        etype = str(payload["event"])
    except (ValueError, KeyError, TypeError):
        raise api_error(400, "invalid_payload", "Malformed webhook payload.") from None
    event_id = event_id or hashlib.sha256(raw).hexdigest()  # Razorpay sends X-Razorpay-Event-Id; body hash is a stable fallback
    pay, order, ref = _entities(payload)
    summary = {k: v for k, v in {"payment_id": pay.get("id"), "order_id": pay.get("order_id") or order.get("id"), "refund_id": ref.get("id"), "amount": pay.get("amount") or ref.get("amount")}.items() if v is not None}

    ev = db.scalar(select(WebhookEvent).where(WebhookEvent.provider == "razorpay", WebhookEvent.event_id == event_id).with_for_update())
    if ev is None:
        ev = WebhookEvent(provider="razorpay", event_id=event_id, event_type=etype[:60], summary=summary)
        try:
            with db.begin_nested():
                db.add(ev)
                db.flush()
        except IntegrityError:
            db.expire_all()
            ev = db.scalar(select(WebhookEvent).where(WebhookEvent.provider == "razorpay", WebhookEvent.event_id == event_id).with_for_update())
    if ev.status in ("processed", "ignored"):
        db.commit()
        return {"status": "duplicate"}
    if ev.id is not None and ev.attempts and ev.status == "failed":
        ev.attempts += 1
    try:
        with db.begin_nested():
            outcome = process_event(db, event_id, payload)
    except Exception as e:
        ev.status, ev.error = "failed", (getattr(e, "detail", None) and str(e.detail) or str(e))[:500]
        db.commit()
        log.warning("razorpay webhook %s failed: %s", event_id, ev.error)
        raise api_error(500, "webhook_processing_failed", "Event processing failed; it will be retried.") from None
    ev.status, ev.error, ev.processed_at = outcome, None, datetime.now(UTC)
    db.commit()
    return {"status": outcome}


def request_refund(db: Session, p: Payment, amount: int, reason: str | None, actor: uuid.UUID, gateway: Gateway | None) -> RefundRecord:
    if p.provider == "razorpay" and gateway is None:
        raise api_error(503, "not_configured", "Payments are not configured; the provider refund cannot be issued.")
    r = billing.create_refund_record(db, p, amount, reason, actor)
    if p.provider == "manual":
        billing.finalize_refund(db, r)  # offline refund recorded by an admin
        return r
    try:
        resp = gateway.refund(p.provider_payment_id, amount, {"refund_id": str(r.id)})
    except Exception:
        r.status = "failed"
        db.commit()
        raise
    r.provider_refund_id = resp.get("id")
    if resp.get("status") == "processed":
        billing.finalize_refund(db, r)
    return r
