"""Phase 14: plan catalog, subscriptions, invoices, payments, coupons, refunds, Razorpay webhook, revenue reports."""
import html
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from fastapi import APIRouter, Depends, Request
from fastapi.responses import Response
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core import ratelimit
from app.core.config import get_settings
from app.core.db import get_db
from app.core.deps import Ctx, api_error, client_ip, get_ctx, require, require_tenant
from app.models.billing import (
    Addon, BillingProfile, Coupon, CouponRedemption, CreditNote, Invoice, Payment, Plan, PlanVersion, RefundRecord, Subscription,
    SubscriptionEvent, WebhookEvent,
)
from app.models.tenant import Tenant
from app.services import billing as svc
from app.services import razorpay as rzp
from app.services.audit import audit

router = APIRouter(tags=["billing"])
TENANT_RES = ("websites", "domains", "databases", "apps", "disk_mb", "bandwidth_gb")


# ---------------------------------------------------------------------------------------------- scoping
def tenant_for(db: Session, ctx: Ctx, requested: uuid.UUID | None) -> Tenant:
    tid = require_tenant(ctx, requested)
    t = db.get(Tenant, tid)
    if t is None:
        raise api_error(404, "not_found", "Tenant not found.")
    return t


def invoice_filter(ctx: Ctx, stmt):
    if ctx.is_platform:
        return stmt
    if ctx.scope == "reseller" and ctx.reseller_id:
        return stmt.where(Invoice.tenant_id.in_(select(Tenant.id).where(Tenant.reseller_id == ctx.reseller_id)))
    if ctx.scope == "tenant" and ctx.tenant_id:
        return stmt.where(Invoice.tenant_id == ctx.tenant_id)
    return stmt.where(False)


def visible_invoice(db: Session, ctx: Ctx, invoice_id: uuid.UUID, lock: bool = False) -> Invoice:
    q = invoice_filter(ctx, select(Invoice).where(Invoice.id == invoice_id))
    inv = db.scalar(q.with_for_update() if lock else q)
    if inv is None:
        raise api_error(404, "not_found", "Invoice not found.")
    return inv


def visible_payment(db: Session, ctx: Ctx, payment_id: uuid.UUID) -> Payment:
    p = db.get(Payment, payment_id)
    if p is None:
        raise api_error(404, "not_found", "Payment not found.")
    visible_invoice(db, ctx, p.invoice_id)  # 404s for anyone who cannot see the invoice
    return p


# ----------------------------------------------------------------------------------------------- DTOs
def version_dto(v: PlanVersion) -> dict[str, Any]:
    return {"version": v.version, "price_monthly_paise": v.price_monthly_paise, "price_yearly_paise": v.price_yearly_paise, "limits": v.limits, "features": v.features, "trial_days": v.trial_days, "hsn_sac": v.hsn_sac, "gst_rate_bps": v.gst_rate_bps, "created_at": v.created_at}


def plan_dto(db: Session, p: Plan, versions: bool = False) -> dict[str, Any]:
    cur = svc.current_version(db, p)
    out = {"id": str(p.id), "code": p.code, "name": p.name, "description": p.description, "active": p.active, "current_version": version_dto(cur)}
    if versions:
        out["versions"] = [version_dto(v) for v in db.scalars(select(PlanVersion).where(PlanVersion.plan_id == p.id).order_by(PlanVersion.version.desc()))]
        out["subscribers"] = db.scalar(select(func.count()).select_from(Subscription).join(PlanVersion, PlanVersion.id == Subscription.plan_version_id).where(PlanVersion.plan_id == p.id, Subscription.status.in_(("trialing", "active", "past_due")))) or 0
    return out


def addon_dto(a: Addon) -> dict[str, Any]:
    return {"id": str(a.id), "code": a.code, "name": a.name, "price_monthly_paise": a.price_monthly_paise, "price_yearly_paise": a.price_yearly_paise, "limits": a.limits, "active": a.active}


def sub_dto(db: Session, s: Subscription) -> dict[str, Any]:
    v, p = svc.version_of(db, s)
    return {
        "id": str(s.id), "tenant_id": str(s.tenant_id), "status": s.status, "interval": s.interval, "plan": {"code": p.code, "name": p.name, "version": v.version},
        "terms": version_dto(v), "addons": [{k: a[k] for k in ("code", "qty", "unit_price_paise")} for a in (s.addons or [])],
        "current_period_start": s.current_period_start, "current_period_end": s.current_period_end, "trial_ends_at": s.trial_ends_at, "grace_ends_at": s.grace_ends_at,
        "cancel_at_period_end": s.cancel_at_period_end, "pending_change": s.pending_change, "started_at": s.started_at, "ended_at": s.ended_at, "mrr_paise": svc.sub_mrr(db, s) if s.status in ("active", "past_due") else 0,
    }


def invoice_dto(inv: Invoice, detail: bool = False) -> dict[str, Any]:
    out = {
        "id": str(inv.id), "number": inv.number, "tenant_id": str(inv.tenant_id), "purpose": inv.purpose, "status": inv.status, "currency": inv.currency,
        "subtotal_paise": inv.subtotal_paise, "discount_paise": inv.discount_paise, "taxable_paise": inv.taxable_paise, "cgst_paise": inv.cgst_paise,
        "sgst_paise": inv.sgst_paise, "igst_paise": inv.igst_paise, "total_paise": inv.total_paise, "refunded_paise": inv.refunded_paise,
        "due_at": inv.due_at, "issued_at": inv.issued_at, "paid_at": inv.paid_at, "dunning_attempts": inv.dunning_attempts, "last_failed_at": inv.last_failed_at, "coupon_code": inv.coupon_code,
    }
    if detail:
        out |= {"lines": inv.lines, "seller": inv.seller, "buyer": inv.buyer, "place_of_supply": inv.place_of_supply}
    return out


def payment_dto(p: Payment) -> dict[str, Any]:
    return {"id": str(p.id), "invoice_id": str(p.invoice_id), "tenant_id": str(p.tenant_id), "provider": p.provider, "status": p.status, "amount_paise": p.amount_paise, "refunded_paise": p.refunded_paise, "failure_code": p.failure_code, "failure_reason": p.failure_reason, "created_at": p.created_at, "captured_at": p.captured_at, "awaiting_confirmation": p.status == "pending" and p.callback_seen_at is not None}


# ============================================================================================ catalog
@router.get("/billing/catalog")
def catalog(db: Session = Depends(get_db), _: Ctx = Depends(get_ctx)):
    plans = db.scalars(select(Plan).where(Plan.active.is_(True)).order_by(Plan.created_at)).all()
    return {"plans": [plan_dto(db, p) for p in plans], "addons": [addon_dto(a) for a in db.scalars(select(Addon).where(Addon.active.is_(True)).order_by(Addon.code))]}


@router.get("/billing/config")
def config(_: Ctx = Depends(get_ctx)):
    s, c = get_settings(), rzp.configured()
    return {"razorpay": {"configured": c["api"], "webhook_configured": c["webhook"], "key_id": s.razorpay_key_id if c["api"] else None, "mode": "test" if s.razorpay_key_id.startswith("rzp_test_") else ("live" if s.razorpay_key_id else None)}}


@router.get("/plans")
def list_plans(ctx: Ctx = Depends(require("plans:read")), db: Session = Depends(get_db)):
    return {"items": [plan_dto(db, p, True) for p in db.scalars(select(Plan).order_by(Plan.created_at))], "addons": [addon_dto(a) for a in db.scalars(select(Addon).order_by(Addon.code))]}


class TermsIn(BaseModel):
    price_monthly_paise: int = Field(ge=0, le=10**9)
    price_yearly_paise: int = Field(ge=0, le=10**10)
    limits: dict[str, int] = Field(default_factory=dict)
    features: dict[str, bool] = Field(default_factory=dict)
    trial_days: int = Field(default=0, ge=0, le=90)
    hsn_sac: str = Field(default="998315", pattern=r"^\d{4,8}$")
    gst_rate_bps: int = Field(default=1800, ge=0, le=2800)

    def clean(self) -> dict[str, Any]:
        for k, v in self.limits.items():
            if k not in TENANT_RES or v < 0:
                raise api_error(422, "validation_error", f"Invalid limit '{k}'.")
        return self.model_dump()


class PlanIn(TermsIn):
    code: str = Field(pattern=r"^[a-z0-9][a-z0-9_-]{1,58}$")
    name: str = Field(min_length=2, max_length=120)
    description: str | None = Field(default=None, max_length=500)


@router.post("/plans", status_code=201)
def create_plan(body: PlanIn, request: Request, ctx: Ctx = Depends(require("plans:write")), db: Session = Depends(get_db)):
    terms = body.clean()
    for k in ("code", "name", "description"):
        terms.pop(k)
    p, _ = svc.create_plan(db, body.code, body.name, body.description, terms)
    audit(db, "plan.created", actor=ctx.user.id, target_type="plan", target_id=p.id, detail={"code": p.code}, ip=client_ip(request))
    db.commit()
    return plan_dto(db, p, True)


@router.post("/plans/{code}/versions", status_code=201)
def new_version(code: str, body: TermsIn, request: Request, ctx: Ctx = Depends(require("plans:write")), db: Session = Depends(get_db)):
    """Publishes a NEW immutable version. Existing subscriptions keep the version they bought."""
    p = svc.get_plan(db, code)
    v = svc.publish_version(db, p, body.clean())
    audit(db, "plan.version_published", actor=ctx.user.id, target_type="plan", target_id=p.id, detail={"version": v.version}, ip=client_ip(request))
    db.commit()
    return plan_dto(db, p, True)


class PlanPatch(BaseModel):
    name: str | None = Field(default=None, min_length=2, max_length=120)
    description: str | None = Field(default=None, max_length=500)
    active: bool | None = None


@router.patch("/plans/{code}")
def patch_plan(code: str, body: PlanPatch, request: Request, ctx: Ctx = Depends(require("plans:write")), db: Session = Depends(get_db)):
    p = svc.get_plan(db, code)
    for k, v in body.model_dump(exclude_unset=True).items():
        if v is not None:
            setattr(p, k, v)
    audit(db, "plan.updated", actor=ctx.user.id, target_type="plan", target_id=p.id, detail=body.model_dump(exclude_unset=True), ip=client_ip(request))
    db.commit()
    return plan_dto(db, p, True)


class AddonIn(BaseModel):
    code: str = Field(pattern=r"^[a-z0-9][a-z0-9_-]{1,58}$")
    name: str = Field(min_length=2, max_length=120)
    price_monthly_paise: int = Field(ge=0, le=10**9)
    price_yearly_paise: int = Field(ge=0, le=10**10)
    limits: dict[str, int] = Field(default_factory=dict)
    active: bool = True


@router.post("/plans/addons", status_code=201)
def create_addon(body: AddonIn, request: Request, ctx: Ctx = Depends(require("plans:write")), db: Session = Depends(get_db)):
    if db.scalar(select(Addon.id).where(Addon.code == body.code)):
        raise api_error(409, "addon_exists", "An add-on with this code exists.")
    for k, v in body.limits.items():
        if k not in TENANT_RES or v < 0:
            raise api_error(422, "validation_error", f"Invalid limit '{k}'.")
    a = Addon(**body.model_dump())
    db.add(a)
    db.flush()
    audit(db, "addon.created", actor=ctx.user.id, target_type="addon", target_id=a.id, ip=client_ip(request))
    db.commit()
    return addon_dto(a)


# ======================================================================================= subscriptions
def _my_sub(db: Session, t: Tenant, lock: bool = False) -> Subscription:
    s = svc.live_subscription(db, t.id, lock)
    if s is None:
        raise api_error(404, "not_found", "No subscription for this account.")
    return s


@router.get("/billing/subscription")
def my_subscription(tenant_id: uuid.UUID | None = None, ctx: Ctx = Depends(require("subscriptions:read")), db: Session = Depends(get_db)):
    t = tenant_for(db, ctx, tenant_id)
    s = svc.live_subscription(db, t.id)
    last = s or db.scalar(select(Subscription).where(Subscription.tenant_id == t.id).order_by(Subscription.created_at.desc()))
    return {"tenant": {"id": str(t.id), "name": t.name, "status": t.status}, "subscription": sub_dto(db, last) if last else None}


@router.get("/billing/subscription/history")
def sub_history(tenant_id: uuid.UUID | None = None, ctx: Ctx = Depends(require("subscriptions:read")), db: Session = Depends(get_db)):
    t = tenant_for(db, ctx, tenant_id)
    rows = db.scalars(select(SubscriptionEvent).where(SubscriptionEvent.tenant_id == t.id).order_by(SubscriptionEvent.created_at.desc()).limit(200)).all()
    return {"items": [{"id": str(e.id), "kind": e.kind, "detail": e.detail, "created_at": e.created_at} for e in rows]}


class TrialIn(BaseModel):
    plan_code: str
    tenant_id: uuid.UUID | None = None


@router.post("/billing/subscription/trial", status_code=201)
def trial(body: TrialIn, request: Request, ctx: Ctx = Depends(require("subscriptions:write")), db: Session = Depends(get_db)):
    t = tenant_for(db, ctx, body.tenant_id)
    s = svc.start_trial(db, t, body.plan_code, ctx.user.id)
    audit(db, "subscription.trial_started", actor=ctx.user.id, tenant_id=t.id, target_type="subscription", target_id=s.id, ip=client_ip(request))
    db.commit()
    return sub_dto(db, s)


class AddonItem(BaseModel):
    code: str
    qty: int = Field(default=1, ge=1, le=100)


class CheckoutIn(BaseModel):
    plan_code: str
    interval: str = Field(default="monthly", pattern="^(monthly|yearly)$")
    addons: list[AddonItem] = Field(default_factory=list, max_length=10)
    coupon_code: str | None = Field(default=None, max_length=40)
    tenant_id: uuid.UUID | None = None


def _payment_block(db: Session, inv: Invoice) -> dict[str, Any]:
    if inv.status == "paid":
        return {"status": "paid"}
    g = rzp.get_gateway()
    if g is None:
        return {"status": "not_configured", "message": "Payments are not configured."}
    p = rzp.create_order(db, inv, g)
    return {"status": "order_created", "payment_id": str(p.id), "order_id": p.provider_order_id, "key_id": get_settings().razorpay_key_id, "amount_paise": p.amount_paise, "currency": p.currency}


@router.post("/billing/checkout", status_code=201)
def do_checkout(body: CheckoutIn, request: Request, ctx: Ctx = Depends(require("billing:write")), db: Session = Depends(get_db)):
    """Creates the activation invoice (and a provider order if payments are configured). The account is NOT activated here."""
    ratelimit.enforce(f"checkout:{ctx.user.id}", 30, 3600)
    t = tenant_for(db, ctx, body.tenant_id)
    inv = svc.checkout(db, t, body.plan_code, body.interval, [(a.code, a.qty) for a in body.addons], body.coupon_code, ctx.user.id)
    if inv.total_paise == 0:  # fully discounted: nothing to collect, determined server-side
        svc.settle_invoice(db, inv, ctx.user.id)
    audit(db, "billing.checkout", actor=ctx.user.id, tenant_id=t.id, target_type="invoice", target_id=inv.id, ip=client_ip(request))
    pay = _payment_block(db, inv)
    db.commit()
    return {"invoice": invoice_dto(inv, True), "payment": pay}


class ChangeIn(BaseModel):
    plan_code: str
    interval: str | None = Field(default=None, pattern="^(monthly|yearly)$")
    tenant_id: uuid.UUID | None = None


@router.post("/billing/subscription/change")
def change(body: ChangeIn, request: Request, ctx: Ctx = Depends(require("subscriptions:write")), db: Session = Depends(get_db)):
    t = tenant_for(db, ctx, body.tenant_id)
    s = _my_sub(db, t, True)
    res = svc.change_plan(db, s, body.plan_code, body.interval, ctx.user.id)
    inv = res.get("invoice")
    pay = _payment_block(db, inv) if inv is not None else None
    audit(db, "subscription.change", actor=ctx.user.id, tenant_id=t.id, target_type="subscription", target_id=s.id, detail={"mode": res["mode"], "plan": body.plan_code}, ip=client_ip(request))
    db.commit()
    return {"mode": res["mode"], "effective_at": res.get("effective_at"), "proration": res.get("proration"), "invoice": invoice_dto(inv, True) if inv is not None else None, "payment": pay}


@router.get("/billing/subscription/proration-preview")
def preview(plan_code: str, interval: str | None = None, tenant_id: uuid.UUID | None = None, ctx: Ctx = Depends(require("subscriptions:read")), db: Session = Depends(get_db)):
    t = tenant_for(db, ctx, tenant_id)
    s = _my_sub(db, t)
    if s.status != "active" or not s.current_period_end:
        return {"mode": "immediate", "proration": None}
    v = svc.current_version(db, svc.get_plan(db, plan_code, only_active=True))
    cur_v, _ = svc.version_of(db, s)
    interval = interval or s.interval
    upgrade = interval == s.interval and svc.price_for(v, interval) > svc.price_for(cur_v, s.interval)
    if not upgrade:
        return {"mode": "scheduled", "effective_at": s.current_period_end, "proration": None}
    return {"mode": "pay_to_apply", "proration": svc.prorate(svc.price_for(cur_v, s.interval), svc.price_for(v, interval), s.current_period_start, s.current_period_end, svc.utcnow())}


class CancelIn(BaseModel):
    immediate: bool = False
    tenant_id: uuid.UUID | None = None


@router.post("/billing/subscription/cancel")
def cancel(body: CancelIn, request: Request, ctx: Ctx = Depends(require("subscriptions:write")), db: Session = Depends(get_db)):
    t = tenant_for(db, ctx, body.tenant_id)
    s = svc.cancel_subscription(db, _my_sub(db, t, True), body.immediate, ctx.user.id)
    audit(db, "subscription.cancel", actor=ctx.user.id, tenant_id=t.id, target_type="subscription", target_id=s.id, detail={"immediate": body.immediate}, ip=client_ip(request))
    db.commit()
    return sub_dto(db, s)


@router.post("/billing/subscription/resume")
def resume(request: Request, tenant_id: uuid.UUID | None = None, ctx: Ctx = Depends(require("subscriptions:write")), db: Session = Depends(get_db)):
    t = tenant_for(db, ctx, tenant_id)
    s = svc.resume_subscription(db, _my_sub(db, t, True), ctx.user.id)
    db.commit()
    return sub_dto(db, s)


class AddonBuy(BaseModel):
    code: str
    qty: int = Field(default=1, ge=1, le=100)
    tenant_id: uuid.UUID | None = None


@router.post("/billing/subscription/addons", status_code=201)
def buy_addon(body: AddonBuy, request: Request, ctx: Ctx = Depends(require("subscriptions:write", "billing:write")), db: Session = Depends(get_db)):
    t = tenant_for(db, ctx, body.tenant_id)
    inv = svc.add_addon(db, _my_sub(db, t, True), body.code, body.qty, ctx.user.id)
    pay = _payment_block(db, inv)
    db.commit()
    return {"invoice": invoice_dto(inv, True), "payment": pay}


@router.get("/subscriptions")
def admin_subscriptions(status: str | None = None, limit: int = 100, ctx: Ctx = Depends(require("subscriptions:read")), db: Session = Depends(get_db)):
    if not ctx.is_platform:
        raise api_error(403, "forbidden", "Platform staff only.")
    stmt = select(Subscription, Tenant.name).join(Tenant, Tenant.id == Subscription.tenant_id).order_by(Subscription.created_at.desc()).limit(min(limit, 500))
    if status:
        stmt = stmt.where(Subscription.status == status)
    return {"items": [{**sub_dto(db, s), "tenant_name": n} for s, n in db.execute(stmt)]}


@router.post("/subscriptions/lifecycle/run")
def run_lifecycle(ctx: Ctx = Depends(require("billing:manage")), db: Session = Depends(get_db)):
    """Runs the idempotent lifecycle job now (normally scheduled): renewal invoices, past_due, grace expiry, suspension."""
    if not ctx.is_platform:
        raise api_error(403, "forbidden", "Platform staff only.")
    return svc.run_lifecycle(db, actor=ctx.user.id)


# ================================================================================================ invoices
@router.get("/billing/invoices")
def list_invoices(status: str | None = None, tenant_id: uuid.UUID | None = None, limit: int = 100, offset: int = 0, ctx: Ctx = Depends(require("billing:read")), db: Session = Depends(get_db)):
    stmt = invoice_filter(ctx, select(Invoice)).order_by(Invoice.issued_at.desc()).limit(min(limit, 200)).offset(offset)
    if status:
        stmt = stmt.where(Invoice.status == status)
    if tenant_id:
        stmt = stmt.where(Invoice.tenant_id == tenant_id)
    return {"items": [invoice_dto(i) for i in db.scalars(stmt)]}


@router.get("/billing/invoices/{invoice_id}")
def get_invoice(invoice_id: uuid.UUID, ctx: Ctx = Depends(require("billing:read")), db: Session = Depends(get_db)):
    inv = visible_invoice(db, ctx, invoice_id)
    pays = db.scalars(select(Payment).where(Payment.invoice_id == inv.id).order_by(Payment.created_at.desc())).all()
    cns = db.scalars(select(CreditNote).where(CreditNote.invoice_id == inv.id)).all()
    return {**invoice_dto(inv, True), "payments": [payment_dto(p) for p in pays], "credit_notes": [{"id": str(c.id), "number": c.number, "amount_paise": c.amount_paise, "created_at": c.created_at, "reason": c.reason} for c in cns]}


def _e(v: Any) -> str:
    return html.escape("" if v is None else str(v))


def _inr(p: int) -> str:
    return f"₹{p / 100:,.2f}"


@router.get("/billing/invoices/{invoice_id}/download")
def download_invoice(invoice_id: uuid.UUID, request: Request, ctx: Ctx = Depends(require("billing:read")), db: Session = Depends(get_db)):
    """Printable GST tax invoice (HTML). Authorised exactly like the detail view: another tenant's invoice is a 404."""
    inv = visible_invoice(db, ctx, invoice_id)
    audit(db, "invoice.downloaded", actor=ctx.user.id, tenant_id=inv.tenant_id, target_type="invoice", target_id=inv.id, ip=client_ip(request))
    db.commit()
    rows = "".join(f"<tr><td>{_e(l['description'])}</td><td>{_e(l['hsn_sac'])}</td><td class=r>{l['qty']}</td><td class=r>{_inr(l['unit_paise'])}</td><td class=r>{_inr(l['amount_paise'])}</td></tr>" for l in inv.lines)
    tax = []
    if inv.igst_paise:
        tax.append(f"<tr><td colspan=4 class=r>IGST</td><td class=r>{_inr(inv.igst_paise)}</td></tr>")
    else:
        tax.append(f"<tr><td colspan=4 class=r>CGST</td><td class=r>{_inr(inv.cgst_paise)}</td></tr><tr><td colspan=4 class=r>SGST</td><td class=r>{_inr(inv.sgst_paise)}</td></tr>")
    s, b = inv.seller, inv.buyer
    doc = f"""<!doctype html><html lang="en"><head><meta charset="utf-8"><title>Invoice {_e(inv.number)}</title>
<style>body{{font:14px system-ui,sans-serif;max-width:760px;margin:24px auto;padding:0 16px;color:#111}}table{{width:100%;border-collapse:collapse}}td,th{{border-bottom:1px solid #ddd;padding:6px;text-align:left}}.r{{text-align:right}}h1{{font-size:20px}}</style></head><body>
<h1>Tax Invoice {_e(inv.number)}</h1><p>Status: <b>{_e(inv.status)}</b> &middot; Issued {_e(inv.issued_at.date())} &middot; Due {_e(inv.due_at.date() if inv.due_at else '')}</p>
<p><b>From</b><br>{_e(s.get('legal_name'))}<br>GSTIN: {_e(s.get('gstin') or 'n/a')}<br>State code: {_e(s.get('state_code'))}<br>{_e(s.get('address'))}</p>
<p><b>Bill to</b><br>{_e(b.get('legal_name'))}<br>GSTIN: {_e(b.get('gstin') or 'unregistered')}<br>{_e(b.get('address'))}<br>Place of supply: {_e(inv.place_of_supply)}</p>
<table><tr><th>Description</th><th>HSN/SAC</th><th class=r>Qty</th><th class=r>Unit</th><th class=r>Amount</th></tr>{rows}
<tr><td colspan=4 class=r>Subtotal</td><td class=r>{_inr(inv.subtotal_paise)}</td></tr>
<tr><td colspan=4 class=r>Discount{(' (' + _e(inv.coupon_code) + ')') if inv.coupon_code else ''}</td><td class=r>-{_inr(inv.discount_paise)}</td></tr>
<tr><td colspan=4 class=r>Taxable value</td><td class=r>{_inr(inv.taxable_paise)}</td></tr>{''.join(tax)}
<tr><th colspan=4 class=r>Total</th><th class=r>{_inr(inv.total_paise)}</th></tr></table></body></html>"""
    return Response(doc, media_type="text/html; charset=utf-8", headers={"Content-Disposition": f'attachment; filename="invoice-{inv.number.replace("/", "-")}.html"', "Content-Security-Policy": "default-src 'none'; style-src 'unsafe-inline'"})


@router.post("/billing/invoices/{invoice_id}/pay")
def pay_invoice(invoice_id: uuid.UUID, ctx: Ctx = Depends(require("billing:write")), db: Session = Depends(get_db)):
    inv = visible_invoice(db, ctx, invoice_id, lock=True)
    if ctx.scope == "reseller":
        raise api_error(403, "forbidden", "Resellers cannot pay client invoices.")
    out = _payment_block(db, inv)
    db.commit()
    return out


@router.post("/billing/invoices/{invoice_id}/void")
def void_invoice(invoice_id: uuid.UUID, request: Request, ctx: Ctx = Depends(require("billing:manage")), db: Session = Depends(get_db)):
    inv = visible_invoice(db, ctx, invoice_id, lock=True)
    svc.void_invoice(db, inv)
    audit(db, "invoice.voided", actor=ctx.user.id, tenant_id=inv.tenant_id, target_type="invoice", target_id=inv.id, ip=client_ip(request))
    db.commit()
    return invoice_dto(inv)


class ManualPaymentIn(BaseModel):
    reference: str = Field(min_length=3, max_length=120)


@router.post("/billing/invoices/{invoice_id}/manual-payment")
def manual_payment(invoice_id: uuid.UUID, body: ManualPaymentIn, request: Request, ctx: Ctx = Depends(require("billing:manage")), db: Session = Depends(get_db)):
    """An admin records a verified offline payment (e.g. bank transfer, UTR). Audited; full invoice amount only."""
    if not ctx.is_platform:
        raise api_error(403, "forbidden", "Platform staff only.")
    inv = visible_invoice(db, ctx, invoice_id, lock=True)
    p = svc.record_manual_payment(db, inv, body.reference, ctx.user.id)
    db.commit()
    return payment_dto(p)


@router.get("/billing/credit-notes")
def credit_notes(ctx: Ctx = Depends(require("billing:read")), db: Session = Depends(get_db)):
    ids = invoice_filter(ctx, select(Invoice.id))
    rows = db.scalars(select(CreditNote).where(CreditNote.invoice_id.in_(ids)).order_by(CreditNote.created_at.desc()).limit(200)).all()
    return {"items": [{"id": str(c.id), "number": c.number, "invoice_id": str(c.invoice_id), "amount_paise": c.amount_paise, "taxable_paise": c.taxable_paise, "cgst_paise": c.cgst_paise, "sgst_paise": c.sgst_paise, "igst_paise": c.igst_paise, "reason": c.reason, "created_at": c.created_at} for c in rows]}


# ================================================================================================ payments
@router.get("/billing/payments")
def list_payments(status: str | None = None, ctx: Ctx = Depends(require("billing:read")), db: Session = Depends(get_db)):
    stmt = select(Payment).where(Payment.invoice_id.in_(invoice_filter(ctx, select(Invoice.id)))).order_by(Payment.created_at.desc()).limit(200)
    if status:
        stmt = stmt.where(Payment.status == status)
    return {"items": [payment_dto(p) for p in db.scalars(stmt)]}


class CallbackIn(BaseModel):
    razorpay_payment_id: str = Field(min_length=5, max_length=80)
    razorpay_order_id: str = Field(min_length=5, max_length=80)
    razorpay_signature: str = Field(min_length=10, max_length=200)


@router.post("/billing/payments/{payment_id}/callback")
def checkout_callback(payment_id: uuid.UUID, body: CallbackIn, ctx: Ctx = Depends(require("billing:write")), db: Session = Depends(get_db)):
    """Checkout success handler. Records the callback and asks the provider server-side; it can NEVER mark the invoice paid."""
    p = visible_payment(db, ctx, payment_id)
    if p.provider_order_id != body.razorpay_order_id:
        raise api_error(400, "order_mismatch", "Order does not match this payment.")
    out = rzp.note_client_callback(db, p, body.razorpay_payment_id, body.razorpay_signature, rzp.get_gateway())
    db.commit()
    return out


@router.get("/billing/payments/{payment_id}")
def get_payment(payment_id: uuid.UUID, ctx: Ctx = Depends(require("billing:read")), db: Session = Depends(get_db)):
    p = visible_payment(db, ctx, payment_id)
    inv = db.get(Invoice, p.invoice_id)
    return {**payment_dto(p), "invoice_status": inv.status}


@router.post("/billing/payments/{payment_id}/reconcile")
def reconcile(payment_id: uuid.UUID, request: Request, ctx: Ctx = Depends(require("billing:manage")), db: Session = Depends(get_db)):
    if not ctx.is_platform:
        raise api_error(403, "forbidden", "Platform staff only.")
    p = visible_payment(db, ctx, payment_id)
    out = rzp.reconcile_payment(db, p, rzp.require_gateway())
    audit(db, "payment.reconciled", actor=ctx.user.id, tenant_id=p.tenant_id, target_type="payment", target_id=p.id, detail=out, ip=client_ip(request))
    db.commit()
    return out


class RefundIn(BaseModel):
    amount_paise: int = Field(gt=0, le=10**10)
    reason: str | None = Field(default=None, max_length=300)


@router.post("/billing/payments/{payment_id}/refund", status_code=201)
def refund(payment_id: uuid.UUID, body: RefundIn, request: Request, ctx: Ctx = Depends(require("billing:manage")), db: Session = Depends(get_db)):
    if not ctx.is_platform:
        raise api_error(403, "forbidden", "Platform staff only.")
    p = visible_payment(db, ctx, payment_id)
    r = rzp.request_refund(db, p, body.amount_paise, body.reason, ctx.user.id, rzp.get_gateway())
    audit(db, "refund.requested", actor=ctx.user.id, tenant_id=p.tenant_id, target_type="refund", target_id=r.id, detail={"amount_paise": body.amount_paise}, ip=client_ip(request))
    db.commit()
    return {"id": str(r.id), "status": r.status, "amount_paise": r.amount_paise}


@router.get("/billing/refunds")
def refunds(ctx: Ctx = Depends(require("billing:read")), db: Session = Depends(get_db)):
    ids = invoice_filter(ctx, select(Invoice.id))
    rows = db.scalars(select(RefundRecord).where(RefundRecord.invoice_id.in_(ids)).order_by(RefundRecord.created_at.desc()).limit(200)).all()
    return {"items": [{"id": str(r.id), "payment_id": str(r.payment_id), "invoice_id": str(r.invoice_id), "amount_paise": r.amount_paise, "status": r.status, "reason": r.reason, "created_at": r.created_at} for r in rows]}


@router.post("/payments/razorpay/webhook")
async def razorpay_webhook(request: Request, db: Session = Depends(get_db)):
    """Public endpoint, authenticated ONLY by the HMAC signature over the raw body."""
    ratelimit.enforce(f"rzp_hook:{client_ip(request)}", 600, 60)
    raw = await request.body()
    if len(raw) > 256 * 1024:
        raise api_error(413, "payload_too_large", "Payload too large.")
    return rzp.handle_webhook(db, raw, request.headers.get("x-razorpay-signature"), request.headers.get("x-razorpay-event-id"))


@router.get("/billing/webhook-events")
def webhook_events(ctx: Ctx = Depends(require("billing:manage")), db: Session = Depends(get_db)):
    if not ctx.is_platform:
        raise api_error(403, "forbidden", "Platform staff only.")
    rows = db.scalars(select(WebhookEvent).order_by(WebhookEvent.received_at.desc()).limit(100)).all()
    return {"items": [{"id": str(e.id), "event_id": e.event_id, "event_type": e.event_type, "status": e.status, "attempts": e.attempts, "error": e.error, "received_at": e.received_at, "processed_at": e.processed_at} for e in rows]}


# ================================================================================================= coupons
class CouponCheck(BaseModel):
    code: str = Field(min_length=2, max_length=40)
    plan_code: str
    interval: str = Field(default="monthly", pattern="^(monthly|yearly)$")
    tenant_id: uuid.UUID | None = None


@router.post("/billing/coupons/validate")
def validate_coupon(body: CouponCheck, ctx: Ctx = Depends(require("billing:read")), db: Session = Depends(get_db)):
    ratelimit.enforce(f"coupon:{ctx.user.id}", 30, 600)
    t = tenant_for(db, ctx, body.tenant_id)
    plan = svc.get_plan(db, body.plan_code, only_active=True)
    price = svc.price_for(svc.current_version(db, plan), body.interval)
    c, disc = svc.validate_coupon(db, body.code, plan.code, price, t.id)
    return {"code": c.code, "discount_paise": disc, "description": c.description}


class CouponIn(BaseModel):
    code: str = Field(pattern=r"^[A-Z0-9_-]{3,40}$")
    description: str | None = Field(default=None, max_length=200)
    percent_bps: int | None = Field(default=None, ge=1, le=10000)
    amount_paise: int | None = Field(default=None, ge=1, le=10**9)
    max_redemptions: int | None = Field(default=None, ge=1)
    per_tenant_limit: int = Field(default=1, ge=1, le=100)
    valid_from: datetime | None = None
    valid_until: datetime | None = None
    plan_codes: list[str] | None = None
    active: bool = True


@router.post("/billing/coupons", status_code=201)
def create_coupon(body: CouponIn, request: Request, ctx: Ctx = Depends(require("billing:manage")), db: Session = Depends(get_db)):
    if (body.percent_bps is None) == (body.amount_paise is None):
        raise api_error(422, "validation_error", "Set exactly one of percent_bps or amount_paise.")
    if body.valid_from and body.valid_until and body.valid_until <= body.valid_from:
        raise api_error(422, "validation_error", "valid_until must be after valid_from.")
    if db.scalar(select(Coupon.id).where(Coupon.code == body.code)):
        raise api_error(409, "coupon_exists", "Coupon code already exists.")
    c = Coupon(**body.model_dump())
    db.add(c)
    db.flush()
    audit(db, "coupon.created", actor=ctx.user.id, target_type="coupon", target_id=c.id, detail={"code": c.code}, ip=client_ip(request))
    db.commit()
    return {"id": str(c.id), "code": c.code}


@router.get("/billing/coupons")
def list_coupons(ctx: Ctx = Depends(require("billing:manage")), db: Session = Depends(get_db)):
    out = []
    for c in db.scalars(select(Coupon).order_by(Coupon.created_at.desc()).limit(200)):
        n = db.scalar(select(func.count()).select_from(CouponRedemption).where(CouponRedemption.coupon_id == c.id, CouponRedemption.status.in_(("reserved", "redeemed")))) or 0
        out.append({"id": str(c.id), "code": c.code, "description": c.description, "percent_bps": c.percent_bps, "amount_paise": c.amount_paise, "max_redemptions": c.max_redemptions, "per_tenant_limit": c.per_tenant_limit, "valid_from": c.valid_from, "valid_until": c.valid_until, "plan_codes": c.plan_codes, "active": c.active, "redemptions": n})
    return {"items": out}


@router.post("/billing/coupons/{coupon_id}/deactivate")
def deactivate_coupon(coupon_id: uuid.UUID, ctx: Ctx = Depends(require("billing:manage")), db: Session = Depends(get_db)):
    c = db.get(Coupon, coupon_id)
    if c is None:
        raise api_error(404, "not_found", "Coupon not found.")
    c.active = False
    audit(db, "coupon.deactivated", actor=ctx.user.id, target_type="coupon", target_id=c.id)
    db.commit()
    return {"status": "ok"}


# ============================================================================== profile/settings/reports
class ProfileIn(BaseModel):
    legal_name: str | None = Field(default=None, max_length=200)
    gstin: str | None = Field(default=None, pattern=r"^[0-9]{2}[A-Z]{5}[0-9]{4}[A-Z][1-9A-Z]Z[0-9A-Z]$")
    state_code: str | None = Field(default=None, pattern=r"^[0-9]{2}$")
    address: str | None = Field(default=None, max_length=500)
    tenant_id: uuid.UUID | None = None


@router.get("/billing/profile")
def get_profile(tenant_id: uuid.UUID | None = None, ctx: Ctx = Depends(require("billing:read")), db: Session = Depends(get_db)):
    t = tenant_for(db, ctx, tenant_id)
    return svc.party(db, t)


@router.put("/billing/profile")
def put_profile(body: ProfileIn, request: Request, ctx: Ctx = Depends(require("billing:write")), db: Session = Depends(get_db)):
    t = tenant_for(db, ctx, body.tenant_id)
    bp = db.scalar(select(BillingProfile).where(BillingProfile.tenant_id == t.id))
    if bp is None:
        bp = BillingProfile(tenant_id=t.id)
        db.add(bp)
    for k, v in body.model_dump(exclude={"tenant_id"}, exclude_unset=True).items():
        setattr(bp, k, v)
    audit(db, "billing.profile_updated", actor=ctx.user.id, tenant_id=t.id, ip=client_ip(request))
    db.commit()
    return svc.party(db, t)


class SettingsIn(BaseModel):
    legal_name: str | None = Field(default=None, max_length=200)
    gstin: str | None = Field(default=None, max_length=15)
    state_code: str | None = Field(default=None, pattern=r"^[0-9]{2}$")
    address: str | None = Field(default=None, max_length=500)
    invoice_prefix: str | None = Field(default=None, pattern=r"^[A-Z0-9]{2,10}$")
    credit_note_prefix: str | None = Field(default=None, pattern=r"^[A-Z0-9]{2,10}$")
    due_days: int | None = Field(default=None, ge=0, le=90)
    renewal_lead_days: int | None = Field(default=None, ge=0, le=30)
    grace_days: int | None = Field(default=None, ge=0, le=60)


@router.get("/billing/settings")
def get_settings_(ctx: Ctx = Depends(require("billing:manage")), db: Session = Depends(get_db)):
    return svc.billing_settings(db)


@router.put("/billing/settings")
def put_settings(body: SettingsIn, request: Request, ctx: Ctx = Depends(require("billing:manage")), db: Session = Depends(get_db)):
    out = svc.save_billing_settings(db, body.model_dump(exclude_unset=True, exclude_none=True))
    audit(db, "billing.settings_updated", actor=ctx.user.id, detail=body.model_dump(exclude_unset=True), ip=client_ip(request))
    db.commit()
    return out


@router.get("/billing/reports/revenue")
def revenue(from_: datetime | None = None, to: datetime | None = None, ctx: Ctx = Depends(require("billing:manage")), db: Session = Depends(get_db)):
    if not ctx.is_platform:
        raise api_error(403, "forbidden", "Platform staff only.")
    end = to or datetime.now(UTC) + timedelta(seconds=1)
    start = from_ or end - timedelta(days=30)
    return svc.revenue_report(db, start, end)


@router.get("/billing/failed-payments")
def failed(ctx: Ctx = Depends(require("billing:manage")), db: Session = Depends(get_db)):
    if not ctx.is_platform:
        raise api_error(403, "forbidden", "Platform staff only.")
    rows = db.execute(select(Invoice, Tenant.name).join(Tenant, Tenant.id == Invoice.tenant_id).where(Invoice.status == "open", Invoice.dunning_attempts > 0).order_by(Invoice.last_failed_at.desc()).limit(100)).all()
    return {"items": [{**invoice_dto(i), "tenant_name": n} for i, n in rows]}
