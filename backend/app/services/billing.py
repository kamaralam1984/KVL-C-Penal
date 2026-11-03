"""Plans, subscriptions, invoices, coupons, payments, refunds and revenue reports. All money is integer paise.

Documented rules
----------------
Rounding: every percentage is computed on integers and rounded half-up to the paise (`_div_round`).
Proration (upgrade, same interval, mid-period), with R = seconds left in the period and T = total period seconds:
    credit = round(old_plan_price * R / T)          # unused value of the plan being left
    charge = round(new_plan_price * R / T)          # value of the new plan for the rest of the period
    net    = charge - credit                         # invoiced now (plus GST); the new plan applies after verified payment
Downgrades (lower price, or any interval change) never refund: they are scheduled for the period end.
MRR  = sum over subscriptions in status active/past_due of the monthly-normalised recurring price (yearly / 12, round half-up)
       of the pinned plan version + add-ons. Excludes tax, discounts, one-off proration, trials and cancelled subscriptions.
ARR  = MRR * 12.
Logo churn   = subscriptions that ended in the period / subscriptions that were paying at the period start.
Revenue churn= MRR (at end) of those ended subscriptions / MRR (at end) of subscriptions paying at period start.
"""
import logging
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.deps import api_error
from app.models.billing import (
    Addon, BillingProfile, Coupon, CouponRedemption, CreditNote, Invoice, InvoiceSequence, Payment, Plan, PlanVersion,
    RefundRecord, Subscription, SubscriptionEvent,
)
from app.models.tenant import PlatformSetting, Tenant, TenantQuota
from app.services import automation
from app.services.audit import audit
from app.services.tenants import log_activity, transition

log = logging.getLogger("kvl.billing")

QUOTA_RESOURCES = ("websites", "domains", "databases", "apps", "disk_mb", "bandwidth_gb")
DEFAULT_BILLING = {
    "legal_name": "KVL Hosting", "gstin": "", "state_code": "29", "address": "", "invoice_prefix": "INV",
    "credit_note_prefix": "CN", "due_days": 7, "renewal_lead_days": 3, "grace_days": None,
}


def utcnow() -> datetime:
    return datetime.now(UTC)


def _div_round(n: int, d: int) -> int:
    """Half-up integer division for non-negative n and positive d."""
    return (2 * n + d) // (2 * d)


# ---------------------------------------------------------------------------------------------- settings
def billing_settings(db: Session) -> dict[str, Any]:
    row = db.scalar(select(PlatformSetting).where(PlatformSetting.key == "billing"))
    cfg = {**DEFAULT_BILLING, **((row.value if row else None) or {})}
    if cfg.get("grace_days") is None:
        g = db.scalar(select(PlatformSetting).where(PlatformSetting.key == "grace_days"))
        cfg["grace_days"] = int((g.value or {}).get("days", 7)) if g else 7
    return cfg


def save_billing_settings(db: Session, patch: dict[str, Any]) -> dict[str, Any]:
    row = db.scalar(select(PlatformSetting).where(PlatformSetting.key == "billing"))
    if row is None:
        row = PlatformSetting(key="billing", value={})
        db.add(row)
    row.value = {**(row.value or {}), **patch}
    db.flush()
    return billing_settings(db)


# ---------------------------------------------------------------------------------------------- numbering
def financial_year(when: datetime) -> str:
    y = when.year if when.month >= 4 else when.year - 1
    return f"{y}-{str(y + 1)[2:]}"


def next_number(db: Session, prefix: str, when: datetime | None = None) -> str:
    """Gap-free, concurrency-safe numbering: one row per series, locked FOR UPDATE until the transaction ends."""
    series = f"{prefix}/{financial_year(when or utcnow())}"
    db.execute(pg_insert(InvoiceSequence).values(series=series, next_value=1).on_conflict_do_nothing(index_elements=["series"]))
    row = db.scalar(select(InvoiceSequence).where(InvoiceSequence.series == series).with_for_update())
    n = row.next_value
    row.next_value = n + 1
    db.flush()
    return f"{series}/{n:06d}"


# ---------------------------------------------------------------------------------------------- tax
def compute_tax(lines: list[dict[str, Any]], discount_paise: int, seller_state: str | None, buyer_state: str | None) -> dict[str, int]:
    """GST split. Discount is spread over lines proportionally (remainder to the last line)."""
    subtotal = sum(l["amount_paise"] for l in lines)
    discount = max(0, min(discount_paise, subtotal))
    left, taxable_total, tax_total = discount, 0, 0
    for i, l in enumerate(lines):
        d = left if i == len(lines) - 1 else (_div_round(discount * l["amount_paise"], subtotal) if subtotal else 0)
        d = min(d, l["amount_paise"], left)
        left -= d
        taxable = l["amount_paise"] - d
        taxable_total += taxable
        tax_total += _div_round(taxable * int(l.get("gst_rate_bps", 0)), 10000)
    intra = not buyer_state or not seller_state or buyer_state == seller_state
    cgst = tax_total // 2 if intra else 0
    sgst = tax_total - cgst if intra else 0
    igst = 0 if intra else tax_total
    return {
        "subtotal_paise": subtotal, "discount_paise": discount, "taxable_paise": taxable_total,
        "cgst_paise": cgst, "sgst_paise": sgst, "igst_paise": igst, "total_paise": taxable_total + tax_total,
    }


def prorate(old_price: int, new_price: int, period_start: datetime, period_end: datetime, now: datetime) -> dict[str, int]:
    total = int((period_end - period_start).total_seconds())
    remaining = max(0, min(total, int((period_end - now).total_seconds())))
    if total <= 0:
        return {"credit_paise": 0, "charge_paise": 0, "net_paise": 0, "remaining_seconds": 0, "total_seconds": 0}
    credit = _div_round(old_price * remaining, total)
    charge = _div_round(new_price * remaining, total)
    return {"credit_paise": credit, "charge_paise": charge, "net_paise": charge - credit, "remaining_seconds": remaining, "total_seconds": total}


# ---------------------------------------------------------------------------------------------- catalog
def add_months(dt: datetime, months: int) -> datetime:
    y, m = divmod(dt.month - 1 + months, 12)
    y += dt.year
    m += 1
    import calendar

    return dt.replace(year=y, month=m, day=min(dt.day, calendar.monthrange(y, m)[1]))


def period_end_for(start: datetime, interval: str) -> datetime:
    return add_months(start, 12 if interval == "yearly" else 1)


def price_for(v: PlanVersion | Addon, interval: str) -> int:
    return v.price_yearly_paise if interval == "yearly" else v.price_monthly_paise


def monthly_equivalent(price: int, interval: str) -> int:
    return _div_round(price, 12) if interval == "yearly" else price


def get_plan(db: Session, code: str, *, only_active: bool = False) -> Plan:
    p = db.scalar(select(Plan).where(Plan.code == code))
    if p is None or (only_active and not p.active):
        raise api_error(404, "not_found", "Plan not found.")
    return p


def current_version(db: Session, plan: Plan) -> PlanVersion:
    return db.scalar(select(PlanVersion).where(PlanVersion.plan_id == plan.id, PlanVersion.version == plan.current_version))


def create_plan(db: Session, code: str, name: str, description: str | None, terms: dict[str, Any]) -> tuple[Plan, PlanVersion]:
    if db.scalar(select(Plan.id).where(Plan.code == code)):
        raise api_error(409, "plan_exists", "A plan with this code already exists.")
    p = Plan(code=code, name=name, description=description, current_version=1)
    db.add(p)
    db.flush()
    v = PlanVersion(plan_id=p.id, version=1, **terms)
    db.add(v)
    db.flush()
    return p, v


def publish_version(db: Session, plan: Plan, terms: dict[str, Any]) -> PlanVersion:
    """Existing subscriptions keep their pinned version; only new sign-ups see this one."""
    top = db.scalar(select(func.max(PlanVersion.version)).where(PlanVersion.plan_id == plan.id)) or 0
    v = PlanVersion(plan_id=plan.id, version=top + 1, **terms)
    db.add(v)
    plan.current_version = top + 1
    db.flush()
    return v


def apply_plan_limits(db: Session, tenant: Tenant, version: PlanVersion, addons: list[dict[str, Any]] | None) -> None:
    """Quota rows follow the pinned plan (+add-ons). Reseller-managed tenants take quotas from their reseller package."""
    db.flush()
    if tenant.reseller_id:
        return
    limits = {k: int(v) for k, v in (version.limits or {}).items() if k in QUOTA_RESOURCES}
    for a in addons or []:
        for k, v in (a.get("limits") or {}).items():
            if k in QUOTA_RESOURCES:
                limits[k] = limits.get(k, 0) + int(v) * int(a.get("qty", 1))
    for res, lim in limits.items():
        q = db.scalar(select(TenantQuota).where(TenantQuota.tenant_id == tenant.id, TenantQuota.resource == res).with_for_update())
        if q:
            q.limit_value = lim
        else:
            db.add(TenantQuota(tenant_id=tenant.id, resource=res, limit_value=lim))


# ---------------------------------------------------------------------------------------------- coupons
def _coupon_counts(db: Session, coupon_id: uuid.UUID, tenant_id: uuid.UUID) -> tuple[int, int]:
    live = CouponRedemption.status.in_(("reserved", "redeemed"))
    total = db.scalar(select(func.count()).select_from(CouponRedemption).where(CouponRedemption.coupon_id == coupon_id, live)) or 0
    mine = db.scalar(select(func.count()).select_from(CouponRedemption).where(CouponRedemption.coupon_id == coupon_id, CouponRedemption.tenant_id == tenant_id, live)) or 0
    return total, mine


def validate_coupon(db: Session, code: str, plan_code: str | None, subtotal: int, tenant_id: uuid.UUID, now: datetime | None = None, lock: bool = False) -> tuple[Coupon, int]:
    now = now or utcnow()
    q = select(Coupon).where(Coupon.code == code.strip().upper())
    c = db.scalar(q.with_for_update() if lock else q)
    if c is None or not c.active:
        raise api_error(422, "coupon_invalid", "This coupon code is not valid.")
    if c.valid_from and now < c.valid_from:
        raise api_error(422, "coupon_not_started", "This coupon is not active yet.")
    if c.valid_until and now > c.valid_until:
        raise api_error(422, "coupon_expired", "This coupon has expired.")
    if c.plan_codes and plan_code not in c.plan_codes:
        raise api_error(422, "coupon_not_applicable", "This coupon does not apply to the selected plan.")
    total, mine = _coupon_counts(db, c.id, tenant_id)
    if c.max_redemptions is not None and total >= c.max_redemptions:
        raise api_error(422, "coupon_exhausted", "This coupon has reached its redemption limit.")
    if mine >= c.per_tenant_limit:
        raise api_error(422, "coupon_already_used", "You have already used this coupon.")
    if c.percent_bps is not None:
        disc = subtotal * c.percent_bps // 10000
    else:
        disc = min(c.amount_paise or 0, subtotal)
    return c, max(0, min(disc, subtotal))


# ---------------------------------------------------------------------------------------------- invoices
def party(db: Session, tenant: Tenant) -> dict[str, Any]:
    bp = db.scalar(select(BillingProfile).where(BillingProfile.tenant_id == tenant.id))
    return {
        "legal_name": (bp.legal_name if bp and bp.legal_name else tenant.name), "gstin": bp.gstin if bp else None,
        "state_code": bp.state_code if bp else None, "address": bp.address if bp else None,
    }


def create_invoice(
    db: Session, tenant: Tenant, purpose: str, lines: list[dict[str, Any]], *, subscription_id: uuid.UUID | None = None,
    meta: dict[str, Any] | None = None, coupon_code: str | None = None, plan_code: str | None = None, due_at: datetime | None = None,
    now: datetime | None = None,
) -> Invoice:
    now = now or utcnow()
    cfg = billing_settings(db)
    seller = {k: cfg.get(k) for k in ("legal_name", "gstin", "state_code", "address")}
    buyer = party(db, tenant)
    subtotal = sum(l["amount_paise"] for l in lines)
    coupon, disc = None, 0
    if coupon_code:
        coupon, disc = validate_coupon(db, coupon_code, plan_code, subtotal, tenant.id, now, lock=True)
    tax = compute_tax(lines, disc, seller["state_code"], buyer["state_code"])
    inv = Invoice(
        tenant_id=tenant.id, subscription_id=subscription_id, number=next_number(db, cfg["invoice_prefix"], now), purpose=purpose,
        lines=lines, seller=seller, buyer=buyer, place_of_supply=buyer["state_code"] or seller["state_code"],
        coupon_code=coupon.code if coupon else None, meta=meta, due_at=due_at or now + timedelta(days=int(cfg["due_days"])), issued_at=now,
        **tax,
    )
    db.add(inv)
    db.flush()
    if coupon:
        db.add(CouponRedemption(coupon_id=coupon.id, tenant_id=tenant.id, invoice_id=inv.id, discount_paise=tax["discount_paise"]))
    return inv


def void_invoice(db: Session, inv: Invoice) -> None:
    if inv.status != "open":
        raise api_error(409, "invoice_not_open", "Only open invoices can be voided.")
    inv.status = "void"
    red = db.scalar(select(CouponRedemption).where(CouponRedemption.invoice_id == inv.id))
    if red and red.status == "reserved":
        red.status = "released"


def line(description: str, unit: int, qty: int = 1, hsn: str = "998315", gst: int = 1800) -> dict[str, Any]:
    return {"description": description, "hsn_sac": hsn, "qty": qty, "unit_paise": unit, "amount_paise": unit * qty, "gst_rate_bps": gst}


def subscription_lines(db: Session, version: PlanVersion, plan: Plan, interval: str, addons: list[dict[str, Any]], label: str) -> list[dict[str, Any]]:
    out = [line(f"{plan.name} ({interval}) - {label}", price_for(version, interval), 1, version.hsn_sac, version.gst_rate_bps)]
    for a in addons:
        out.append(line(f"Add-on {a['code']} ({interval})", a["unit_price_paise"], a["qty"], a["hsn_sac"], a["gst_rate_bps"]))
    return out


def resolve_addons(db: Session, items: list[tuple[str, int]], interval: str) -> list[dict[str, Any]]:
    out = []
    for code, qty in items:
        a = db.scalar(select(Addon).where(Addon.code == code, Addon.active.is_(True)))
        if a is None:
            raise api_error(404, "not_found", f"Add-on '{code}' not found.")
        out.append({"code": a.code, "qty": qty, "unit_price_paise": price_for(a, interval), "hsn_sac": a.hsn_sac, "gst_rate_bps": a.gst_rate_bps, "limits": a.limits or {}})
    return out


# ---------------------------------------------------------------------------------------------- subscriptions
def live_subscription(db: Session, tenant_id: uuid.UUID, lock: bool = False) -> Subscription | None:
    q = select(Subscription).where(Subscription.tenant_id == tenant_id, Subscription.status.in_(("incomplete", "trialing", "active", "past_due"))).order_by(Subscription.created_at.desc())
    return db.scalars(q.with_for_update() if lock else q).first()


def log_sub(db: Session, sub: Subscription, kind: str, detail: dict[str, Any] | None = None, actor: uuid.UUID | None = None) -> None:
    db.add(SubscriptionEvent(subscription_id=sub.id, tenant_id=sub.tenant_id, kind=kind, detail=detail, actor_user_id=actor))


def version_of(db: Session, sub: Subscription) -> tuple[PlanVersion, Plan]:
    v = db.get(PlanVersion, sub.plan_version_id)
    return v, db.get(Plan, v.plan_id)


def start_trial(db: Session, tenant: Tenant, plan_code: str, actor: uuid.UUID | None, now: datetime | None = None) -> Subscription:
    now = now or utcnow()
    plan = get_plan(db, plan_code, only_active=True)
    v = current_version(db, plan)
    if v.trial_days <= 0:
        raise api_error(422, "no_trial", "This plan does not offer a trial.")
    if live_subscription(db, tenant.id, lock=True):
        raise api_error(409, "subscription_exists", "This account already has a subscription.")
    if db.scalar(select(SubscriptionEvent.id).where(SubscriptionEvent.tenant_id == tenant.id, SubscriptionEvent.kind == "trial_started")):
        raise api_error(409, "trial_used", "A trial has already been used on this account.")
    sub = Subscription(
        tenant_id=tenant.id, plan_version_id=v.id, interval="monthly", status="trialing", addons=[],
        trial_ends_at=now + timedelta(days=v.trial_days),
    )
    db.add(sub)
    db.flush()
    tenant.plan_code = plan.code
    tenant.trial_ends_at = sub.trial_ends_at
    if tenant.status == "pending":
        transition(db, tenant, "trial", actor, "trial started")
    apply_plan_limits(db, tenant, v, [])
    log_sub(db, sub, "trial_started", {"plan": plan.code, "version": v.version, "trial_days": v.trial_days}, actor)
    return sub


def checkout(db: Session, tenant: Tenant, plan_code: str, interval: str, addon_items: list[tuple[str, int]], coupon_code: str | None, actor: uuid.UUID | None, now: datetime | None = None) -> Invoice:
    """Create (or restart) the activation invoice. Nothing is activated until a VERIFIED payment arrives."""
    now = now or utcnow()
    plan = get_plan(db, plan_code, only_active=True)
    v = current_version(db, plan)
    sub = live_subscription(db, tenant.id, lock=True)
    if sub and sub.status in ("active", "past_due"):
        raise api_error(409, "subscription_exists", "This account already has an active subscription; change plan instead.")
    addons = resolve_addons(db, addon_items, interval)
    if sub is None:
        sub = Subscription(tenant_id=tenant.id, plan_version_id=v.id, interval=interval, status="incomplete", addons=addons)
        db.add(sub)
        db.flush()
    else:
        for old in db.scalars(select(Invoice).where(Invoice.subscription_id == sub.id, Invoice.status == "open", Invoice.purpose == "activation")):
            void_invoice(db, old)
        sub.plan_version_id, sub.interval, sub.addons = v.id, interval, addons
    sub.coupon_code = coupon_code.strip().upper() if coupon_code else None
    inv = create_invoice(
        db, tenant, "activation", subscription_lines(db, v, plan, interval, addons, "subscription"), subscription_id=sub.id,
        meta={"action": "activate", "subscription_id": str(sub.id)}, coupon_code=coupon_code, plan_code=plan.code, now=now,
    )
    log_sub(db, sub, "checkout_started", {"invoice": inv.number, "plan": plan.code, "version": v.version, "interval": interval}, actor)
    return inv


def _reactivate_tenant(db: Session, tenant: Tenant, reason: str) -> bool:
    if tenant.status in ("pending", "trial", "suspended"):
        transition(db, tenant, "active", None, reason)
        automation.emit_event(db, "tenant.activated", {"tenant_id": tenant.id, "reason": reason}, tenant_id=tenant.id, idempotency_key=f"tenant_activated:{tenant.id}:{utcnow().isoformat()}")
        return True
    return False


def apply_paid_invoice(db: Session, inv: Invoice, actor: uuid.UUID | None = None, now: datetime | None = None) -> None:
    """Business effects of a VERIFIED paid invoice. Only reachable from capture/manual-record code paths."""
    now = now or utcnow()
    meta = inv.meta or {}
    tenant = db.get(Tenant, inv.tenant_id)
    sub = db.get(Subscription, uuid.UUID(meta["subscription_id"])) if meta.get("subscription_id") else None
    action = meta.get("action")
    if sub and action == "activate" and sub.status in ("incomplete", "trialing"):
        v, plan = version_of(db, sub)
        sub.status, sub.started_at = "active", sub.started_at or now
        sub.current_period_start, sub.current_period_end = now, period_end_for(now, sub.interval)
        sub.trial_ends_at, sub.grace_ends_at = None, None
        tenant.plan_code = plan.code
        apply_plan_limits(db, tenant, v, sub.addons)
        log_sub(db, sub, "activated", {"invoice": inv.number}, actor)
        _reactivate_tenant(db, tenant, "verified payment")
    elif sub and action == "renew" and sub.status in ("active", "past_due"):
        start = sub.current_period_end if sub.current_period_end and sub.current_period_end + timedelta(days=1) > now else now
        if meta.get("plan_version_id") and uuid.UUID(meta["plan_version_id"]) != sub.plan_version_id:
            sub.plan_version_id = uuid.UUID(meta["plan_version_id"])
            sub.interval = meta.get("interval", sub.interval)
            sub.pending_change = None
            v, plan = version_of(db, sub)
            tenant.plan_code = plan.code
            apply_plan_limits(db, tenant, v, sub.addons)
        sub.status, sub.grace_ends_at = "active", None
        sub.current_period_start, sub.current_period_end = start, period_end_for(start, sub.interval)
        log_sub(db, sub, "renewed", {"invoice": inv.number}, actor)
        _reactivate_tenant(db, tenant, "renewal payment verified")
    elif sub and action == "upgrade" and sub.status in ("active", "past_due"):
        sub.plan_version_id = uuid.UUID(meta["plan_version_id"])
        sub.pending_change = None
        v, plan = version_of(db, sub)
        tenant.plan_code = plan.code
        apply_plan_limits(db, tenant, v, sub.addons)
        log_sub(db, sub, "upgraded", {"invoice": inv.number, "plan": plan.code, "version": v.version}, actor)
    elif sub and action == "addon" and sub.status in ("active", "past_due"):
        sub.addons = [*(sub.addons or []), *meta.get("addons", [])]
        v, _ = version_of(db, sub)
        apply_plan_limits(db, tenant, v, sub.addons)
        log_sub(db, sub, "addon_added", {"invoice": inv.number}, actor)
    automation.emit_event(
        db, "billing.payment_verified",
        {"tenant_id": inv.tenant_id, "invoice_id": inv.id, "invoice_number": inv.number, "amount_paise": inv.total_paise, "purpose": inv.purpose},
        tenant_id=inv.tenant_id, idempotency_key=f"payment_verified:{inv.id}",
    )


def settle_invoice(db: Session, inv: Invoice, actor: uuid.UUID | None = None, now: datetime | None = None) -> bool:
    """Mark an invoice paid and apply its effects. Returns False if it was already settled (idempotent)."""
    now = now or utcnow()
    if inv.status == "paid":
        return False
    if inv.status != "open":
        raise api_error(409, "invoice_not_open", "Invoice is not open.")
    inv.status, inv.paid_at = "paid", now
    red = db.scalar(select(CouponRedemption).where(CouponRedemption.invoice_id == inv.id))
    if red:
        red.status = "redeemed"
    apply_paid_invoice(db, inv, actor, now)
    from app.services import reseller as reseller_svc

    reseller_svc.record_sale_commission(db, inv)
    return True


def activate_if_paid(db: Session, tenant_id: uuid.UUID) -> bool:
    """Used by n8n 'tenant.activate': only succeeds when a verified-paid invoice backs a live subscription."""
    tenant = db.get(Tenant, tenant_id)
    if tenant is None:
        raise api_error(404, "not_found", "Tenant not found.")
    now = utcnow()
    paid = db.scalar(
        select(Invoice.id).join(Subscription, Subscription.id == Invoice.subscription_id).where(
            Invoice.tenant_id == tenant_id, Invoice.status == "paid", Invoice.purpose.in_(("activation", "renewal", "upgrade")),
            Subscription.status == "active", Subscription.current_period_end > now,
        ).limit(1)
    )
    if paid is None:
        raise api_error(409, "payment_not_verified", "No verified payment backs this tenant; activation refused.")
    return _reactivate_tenant(db, tenant, "activation confirmed after verified payment")


def cancel_subscription(db: Session, sub: Subscription, immediate: bool, actor: uuid.UUID | None, now: datetime | None = None) -> Subscription:
    now = now or utcnow()
    if sub.status not in ("trialing", "active", "past_due", "incomplete"):
        raise api_error(409, "not_cancellable", "Subscription is not active.")
    if immediate or sub.status in ("trialing", "incomplete"):
        _end(db, sub, "cancelled", now, actor, "immediate cancellation")
    else:
        sub.cancel_at_period_end = True
        for inv in db.scalars(select(Invoice).where(Invoice.subscription_id == sub.id, Invoice.status == "open", Invoice.purpose == "renewal")):
            void_invoice(db, inv)
        log_sub(db, sub, "cancel_scheduled", {"effective": sub.current_period_end.isoformat() if sub.current_period_end else None}, actor)
    return sub


def resume_subscription(db: Session, sub: Subscription, actor: uuid.UUID | None) -> Subscription:
    if sub.status not in ("active", "past_due") or not sub.cancel_at_period_end:
        raise api_error(409, "not_cancelling", "Subscription is not scheduled to cancel.")
    sub.cancel_at_period_end = False
    log_sub(db, sub, "cancel_revoked", None, actor)
    return sub


def _end(db: Session, sub: Subscription, status: str, now: datetime, actor: uuid.UUID | None, reason: str) -> None:
    sub.status, sub.ended_at, sub.cancelled_at = status, now, now if status == "cancelled" else sub.cancelled_at
    sub.cancel_at_period_end = False
    for inv in db.scalars(select(Invoice).where(Invoice.subscription_id == sub.id, Invoice.status == "open")):
        void_invoice(db, inv)
    log_sub(db, sub, status, {"reason": reason}, actor)
    t = db.get(Tenant, sub.tenant_id)
    if t.status in ("trial", "active"):
        transition(db, t, "suspended", actor, f"subscription {status}")  # data is kept; reactivates on a verified payment


def change_plan(db: Session, sub: Subscription, plan_code: str, interval: str | None, actor: uuid.UUID | None, now: datetime | None = None) -> dict[str, Any]:
    now = now or utcnow()
    plan = get_plan(db, plan_code, only_active=True)
    v = current_version(db, plan)
    interval = interval or sub.interval
    cur_v, cur_plan = version_of(db, sub)
    tenant = db.get(Tenant, sub.tenant_id)
    if sub.status == "trialing":
        sub.plan_version_id, sub.interval = v.id, interval
        tenant.plan_code = plan.code
        apply_plan_limits(db, tenant, v, sub.addons)
        log_sub(db, sub, "trial_plan_changed", {"plan": plan.code, "version": v.version}, actor)
        return {"mode": "immediate", "invoice": None}
    if sub.status != "active":
        raise api_error(409, "not_changeable", "Only active or trialing subscriptions can change plan.")
    if cur_v.id == v.id and interval == sub.interval:
        raise api_error(409, "no_change", "That is already the current plan.")
    old_price, new_price = price_for(cur_v, sub.interval), price_for(v, interval)
    is_upgrade = interval == sub.interval and new_price > old_price
    if not is_upgrade:
        sub.pending_change = {"plan_version_id": str(v.id), "interval": interval, "effective_at": sub.current_period_end.isoformat()}
        for inv in db.scalars(select(Invoice).where(Invoice.subscription_id == sub.id, Invoice.status == "open", Invoice.purpose == "renewal")):
            void_invoice(db, inv)  # regenerated at the new price by the lifecycle job
        log_sub(db, sub, "downgrade_scheduled", sub.pending_change, actor)
        return {"mode": "scheduled", "effective_at": sub.current_period_end, "invoice": None}
    pr = prorate(old_price, new_price, sub.current_period_start, sub.current_period_end, now)
    if pr["net_paise"] <= 0:
        sub.plan_version_id = v.id
        apply_plan_limits(db, tenant, v, sub.addons)
        log_sub(db, sub, "upgraded", {"plan": plan.code, "version": v.version, "proration": pr}, actor)
        return {"mode": "immediate", "invoice": None, "proration": pr}
    desc = f"Upgrade {cur_plan.name} to {plan.name}: prorated charge {pr['charge_paise']/100:.2f} less unused credit {pr['credit_paise']/100:.2f}"
    lines = [line(desc, pr["net_paise"], 1, v.hsn_sac, v.gst_rate_bps)]
    inv = create_invoice(
        db, tenant, "upgrade", lines, subscription_id=sub.id, plan_code=plan.code, now=now,
        meta={"action": "upgrade", "subscription_id": str(sub.id), "plan_version_id": str(v.id), "proration": pr},
    )
    log_sub(db, sub, "upgrade_invoiced", {"invoice": inv.number, "proration": pr}, actor)
    return {"mode": "pay_to_apply", "invoice": inv, "proration": pr}


def add_addon(db: Session, sub: Subscription, code: str, qty: int, actor: uuid.UUID | None, now: datetime | None = None) -> Invoice:
    now = now or utcnow()
    if sub.status != "active":
        raise api_error(409, "not_changeable", "Add-ons can be added to active subscriptions only.")
    items = resolve_addons(db, [(code, qty)], sub.interval)
    pr_price = items[0]["unit_price_paise"] * qty
    charge = prorate(0, pr_price, sub.current_period_start, sub.current_period_end, now)["charge_paise"]
    tenant = db.get(Tenant, sub.tenant_id)
    inv = create_invoice(
        db, tenant, "addon", [line(f"Add-on {code} x{qty}: prorated for remaining period", charge, 1, items[0]["hsn_sac"], items[0]["gst_rate_bps"])],
        subscription_id=sub.id, now=now, meta={"action": "addon", "subscription_id": str(sub.id), "addons": items},
    )
    log_sub(db, sub, "addon_invoiced", {"invoice": inv.number, "addon": code}, actor)
    return inv


def sub_mrr(db: Session, sub: Subscription) -> int:
    v, _ = version_of(db, sub)
    total = monthly_equivalent(price_for(v, sub.interval), sub.interval)
    for a in sub.addons or []:
        total += monthly_equivalent(int(a["unit_price_paise"]) * int(a.get("qty", 1)), sub.interval)
    return total


# ------------------------------------------------------------------------------------------ lifecycle job
def generate_renewal_invoices(db: Session, now: datetime | None = None) -> int:
    now = now or utcnow()
    cfg = billing_settings(db)
    horizon = now + timedelta(days=int(cfg["renewal_lead_days"]))
    n = 0
    for sub in db.scalars(select(Subscription).where(Subscription.status == "active", Subscription.cancel_at_period_end.is_(False), Subscription.current_period_end <= horizon).with_for_update(skip_locked=True)):
        end_iso = sub.current_period_end.isoformat()
        exists = db.scalar(
            select(Invoice.id).where(Invoice.subscription_id == sub.id, Invoice.purpose == "renewal", Invoice.status.in_(("open", "paid")), Invoice.meta["period_end"].astext == end_iso)
        )
        if exists:
            continue
        pc = sub.pending_change or {}
        v, plan = version_of(db, sub)
        interval = sub.interval
        if pc.get("plan_version_id"):
            v = db.get(PlanVersion, uuid.UUID(pc["plan_version_id"]))
            plan = db.get(Plan, v.plan_id)
            interval = pc.get("interval", interval)
        else:
            # same pinned version keeps its historic terms; the plan's newer versions are not applied to existing subscribers
            pass
        tenant = db.get(Tenant, sub.tenant_id)
        addons = sub.addons or []
        # add-on unit prices are pinned too, but interval changes reprice from the catalogue
        if interval != sub.interval:
            addons = resolve_addons(db, [(a["code"], a["qty"]) for a in addons], interval)
        create_invoice(
            db, tenant, "renewal", subscription_lines(db, v, plan, interval, addons, "renewal"), subscription_id=sub.id, now=now, due_at=sub.current_period_end,
            meta={"action": "renew", "subscription_id": str(sub.id), "plan_version_id": str(v.id), "interval": interval, "period_end": end_iso},
        )
        n += 1
    return n


def run_lifecycle(db: Session, now: datetime | None = None, actor: uuid.UUID | None = None) -> dict[str, int]:
    """Idempotent scheduler entry point: trial expiry, scheduled cancellations, renewal invoices, past_due, suspension."""
    now = now or utcnow()
    cfg = billing_settings(db)
    r = {"trials_expired": 0, "cancelled": 0, "renewal_invoices": 0, "past_due": 0, "suspended": 0}
    for sub in db.scalars(select(Subscription).where(Subscription.status == "trialing", Subscription.trial_ends_at <= now)):
        _end(db, sub, "expired", now, actor, "trial ended without verified payment")
        r["trials_expired"] += 1
    db.flush()
    for sub in db.scalars(select(Subscription).where(Subscription.status.in_(("active", "past_due")), Subscription.cancel_at_period_end.is_(True), Subscription.current_period_end <= now)):
        _end(db, sub, "cancelled", now, actor, "cancelled at period end")
        r["cancelled"] += 1
    db.flush()  # autoflush is off: make earlier steps visible to the next query
    r["renewal_invoices"] = generate_renewal_invoices(db, now)
    db.flush()
    for sub in db.scalars(select(Subscription).where(Subscription.status == "active", Subscription.current_period_end <= now)).all():
        sub.status = "past_due"
        sub.grace_ends_at = sub.current_period_end + timedelta(days=int(cfg["grace_days"]))
        t = db.get(Tenant, sub.tenant_id)
        t.grace_ends_at = sub.grace_ends_at
        log_sub(db, sub, "past_due", {"grace_ends_at": sub.grace_ends_at.isoformat()}, actor)
        r["past_due"] += 1
    db.flush()
    for sub in db.scalars(select(Subscription).where(Subscription.status == "past_due", Subscription.grace_ends_at <= now)).all():
        t = db.get(Tenant, sub.tenant_id)
        if t.status in ("active", "trial"):
            transition(db, t, "suspended", actor, "grace period ended with unpaid renewal")  # never deletes data
            log_sub(db, sub, "suspended", None, actor)
            r["suspended"] += 1
    db.commit()
    return r


# -------------------------------------------------------------------------------------------------- payments
def mark_payment_captured(db: Session, payment: Payment, provider_payment_id: str | None, amount_paise: int, actor: uuid.UUID | None = None) -> bool:
    """The ONLY place a provider payment becomes 'captured'. Callers must have verified the source (signed webhook / server fetch)."""
    if payment.status in ("captured", "refunded"):
        return False
    if amount_paise != payment.amount_paise:
        raise api_error(409, "amount_mismatch", "Captured amount does not match the order.")
    inv = db.scalar(select(Invoice).where(Invoice.id == payment.invoice_id).with_for_update())
    payment.status, payment.captured_at = "captured", utcnow()
    if provider_payment_id:
        payment.provider_payment_id = provider_payment_id
    payment.failure_code = payment.failure_reason = None
    if inv.status != "open":
        payment.failure_reason = f"invoice already {inv.status}: refund required"
        audit(db, "payment.duplicate_capture", tenant_id=inv.tenant_id, target_type="payment", target_id=payment.id)
        return True
    settle_invoice(db, inv, actor)
    audit(db, "payment.captured", actor=actor, tenant_id=inv.tenant_id, target_type="payment", target_id=payment.id, detail={"invoice": inv.number, "amount_paise": amount_paise})
    return True


def mark_payment_failed(db: Session, payment: Payment, code: str | None, reason: str | None, now: datetime | None = None) -> bool:
    now = now or utcnow()
    if payment.status in ("captured", "refunded", "failed"):
        return False
    payment.status, payment.failure_code, payment.failure_reason = "failed", (code or "")[:80] or None, (reason or "")[:300] or None
    inv = db.get(Invoice, payment.invoice_id)
    inv.dunning_attempts += 1
    inv.last_failed_at = now
    if inv.status == "open" and inv.purpose == "renewal" and inv.subscription_id:
        sub = db.get(Subscription, inv.subscription_id)
        if sub and sub.status == "active":
            sub.status = "past_due"
            sub.grace_ends_at = (sub.current_period_end or now) + timedelta(days=int(billing_settings(db)["grace_days"]))
            log_sub(db, sub, "past_due", {"reason": "payment failed"})
    audit(db, "payment.failed", tenant_id=inv.tenant_id, target_type="payment", target_id=payment.id, detail={"code": code})
    return True


def record_manual_payment(db: Session, inv: Invoice, reference: str, actor: uuid.UUID) -> Payment:
    if inv.status != "open":
        raise api_error(409, "invoice_not_open", "Invoice is not open.")
    p = Payment(tenant_id=inv.tenant_id, invoice_id=inv.id, provider="manual", amount_paise=inv.total_paise, status="pending", reference=reference[:120])
    db.add(p)
    db.flush()
    mark_payment_captured(db, p, None, p.amount_paise, actor)
    audit(db, "payment.manual_recorded", actor=actor, tenant_id=inv.tenant_id, target_type="invoice", target_id=inv.id, detail={"reference": reference[:120]})
    return p


# --------------------------------------------------------------------------------------------------- refunds
def credit_note_for(db: Session, inv: Invoice, amount: int, reason: str | None) -> CreditNote:
    total = inv.total_paise or 1
    cgst, sgst, igst = (_div_round(x * amount, total) for x in (inv.cgst_paise, inv.sgst_paise, inv.igst_paise))
    taxes = cgst + sgst + igst
    cn = CreditNote(
        tenant_id=inv.tenant_id, invoice_id=inv.id, number=next_number(db, billing_settings(db)["credit_note_prefix"]), amount_paise=amount,
        taxable_paise=amount - taxes, cgst_paise=cgst, sgst_paise=sgst, igst_paise=igst, reason=reason,
    )
    db.add(cn)
    db.flush()
    return cn


def refundable(db: Session, payment: Payment) -> int:
    reserved = db.scalar(select(func.coalesce(func.sum(RefundRecord.amount_paise), 0)).where(RefundRecord.payment_id == payment.id, RefundRecord.status.in_(("requested", "processed")))) or 0
    return payment.amount_paise - int(reserved)


def create_refund_record(db: Session, payment: Payment, amount: int, reason: str | None, actor: uuid.UUID | None) -> RefundRecord:
    payment = db.scalar(select(Payment).where(Payment.id == payment.id).with_for_update())
    if payment.status not in ("captured", "refunded") or payment.captured_at is None:
        raise api_error(409, "not_refundable", "Only captured payments can be refunded.")
    if amount <= 0 or amount > refundable(db, payment):
        raise api_error(422, "refund_exceeds_payment", "Refund amount exceeds the refundable balance.")
    r = RefundRecord(tenant_id=payment.tenant_id, payment_id=payment.id, invoice_id=payment.invoice_id, amount_paise=amount, reason=reason, requested_by=actor)
    db.add(r)
    db.flush()
    return r


def finalize_refund(db: Session, r: RefundRecord) -> bool:
    """Idempotent: refund processed -> credit note, balances and commission reversal."""
    if r.status == "processed":
        return False
    payment = db.scalar(select(Payment).where(Payment.id == r.payment_id).with_for_update())
    inv = db.scalar(select(Invoice).where(Invoice.id == r.invoice_id).with_for_update())
    r.status = "processed"
    payment.refunded_paise += r.amount_paise
    if payment.refunded_paise >= payment.amount_paise:
        payment.status = "refunded"
    inv.refunded_paise += r.amount_paise
    cn = credit_note_for(db, inv, r.amount_paise, r.reason)
    r.credit_note_id = cn.id
    from app.services import reseller as reseller_svc

    reseller_svc.reverse_commission(db, inv, r)
    audit(db, "refund.processed", tenant_id=inv.tenant_id, target_type="refund", target_id=r.id, detail={"amount_paise": r.amount_paise, "credit_note": cn.number})
    return True


# ---------------------------------------------------------------------------------------------------- reports
def mrr_snapshot(db: Session) -> dict[str, Any]:
    subs = db.scalars(select(Subscription).where(Subscription.status.in_(("active", "past_due")))).all()
    mrr = sum(sub_mrr(db, s) for s in subs)
    return {"mrr_paise": mrr, "arr_paise": mrr * 12, "paying_subscriptions": len(subs)}


def churn_report(db: Session, start: datetime, end: datetime) -> dict[str, Any]:
    opening = db.scalars(select(Subscription).where(Subscription.started_at.is_not(None), Subscription.started_at < start, (Subscription.ended_at.is_(None)) | (Subscription.ended_at >= start))).all()
    churned = [s for s in opening if s.ended_at is not None and s.ended_at < end]
    base_mrr = sum(sub_mrr(db, s) for s in opening)
    lost = sum(sub_mrr(db, s) for s in churned)
    return {
        "opening_subscriptions": len(opening), "churned_subscriptions": len(churned),
        "logo_churn_bps": _div_round(len(churned) * 10000, len(opening)) if opening else 0,
        "opening_mrr_paise": base_mrr, "churned_mrr_paise": lost,
        "revenue_churn_bps": _div_round(lost * 10000, base_mrr) if base_mrr else 0,
    }


def revenue_report(db: Session, start: datetime, end: datetime) -> dict[str, Any]:
    paid = db.execute(
        select(func.coalesce(func.sum(Invoice.taxable_paise), 0), func.coalesce(func.sum(Invoice.total_paise), 0), func.count()).where(Invoice.status == "paid", Invoice.paid_at >= start, Invoice.paid_at < end)
    ).one()
    refunds = db.scalar(select(func.coalesce(func.sum(CreditNote.amount_paise), 0)).where(CreditNote.created_at >= start, CreditNote.created_at < end)) or 0
    month = func.to_char(Invoice.paid_at, "YYYY-MM")
    series = db.execute(select(month, func.sum(Invoice.taxable_paise), func.count()).where(Invoice.status == "paid", Invoice.paid_at >= start, Invoice.paid_at < end).group_by(month).order_by(month)).all()
    failed = db.scalar(select(func.count()).select_from(Payment).where(Payment.status == "failed", Payment.created_at >= start, Payment.created_at < end)) or 0
    return {
        "period": {"from": start, "to": end}, "invoices_paid": paid[2], "revenue_ex_tax_paise": int(paid[0]), "billed_incl_tax_paise": int(paid[1]),
        "refunded_incl_tax_paise": int(refunds), "net_billed_paise": int(paid[1]) - int(refunds), "failed_payments": failed,
        "by_month": [{"month": m, "revenue_ex_tax_paise": int(v), "invoices": c} for m, v, c in series],
        **mrr_snapshot(db), "churn": churn_report(db, start, end),
        "formulas": {
            "mrr": "Sum of monthly-normalised recurring price (yearly/12, half-up) of pinned plan versions + add-ons for active and past_due subscriptions. Excludes tax, discounts, proration, trials.",
            "arr": "MRR x 12.",
            "logo_churn": "Subscriptions ended in the period / subscriptions paying at period start.",
            "revenue_churn": "MRR of subscriptions ended in the period / MRR of subscriptions paying at period start.",
            "revenue": "Taxable value of invoices paid in the period (excludes GST).",
        },
    }


def log_tenant(db: Session, tenant_id: uuid.UUID, actor: uuid.UUID | None, action: str, summary: str) -> None:
    log_activity(db, tenant_id, actor, action, summary)


__all__ = ["IntegrityError"]
