"""Phase 14: plans (immutable versions), subscriptions, invoices, payments, coupons, refunds, webhook events."""
import uuid
from datetime import datetime
from typing import Any

from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy import BigInteger, Boolean, DateTime, ForeignKey, Index, Integer, String, Text, UniqueConstraint, event, func, inspect
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base, IdMixin, TimestampMixin

SUBSCRIPTION_LIVE = ("incomplete", "trialing", "active", "past_due")


class Plan(Base, IdMixin, TimestampMixin):
    __tablename__ = "billing_plans"
    code: Mapped[str] = mapped_column(String(60), unique=True)
    name: Mapped[str] = mapped_column(String(120))
    description: Mapped[str | None] = mapped_column(String(500))
    active: Mapped[bool] = mapped_column(Boolean, default=True)  # false = hidden from new sign-ups
    current_version: Mapped[int] = mapped_column(Integer, default=1)


class PlanVersion(Base, IdMixin):
    """Immutable commercial terms. Changing a plan creates a new version; subscriptions pin one."""

    __tablename__ = "billing_plan_versions"
    plan_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("billing_plans.id", ondelete="CASCADE"), index=True)
    version: Mapped[int] = mapped_column(Integer)
    price_monthly_paise: Mapped[int] = mapped_column(BigInteger)
    price_yearly_paise: Mapped[int] = mapped_column(BigInteger)
    limits: Mapped[dict[str, Any]]  # resource -> int (applied to tenant quotas)
    features: Mapped[dict[str, Any]]  # feature flag -> bool
    trial_days: Mapped[int] = mapped_column(Integer, default=0)
    hsn_sac: Mapped[str] = mapped_column(String(10), default="998315")  # hosting services SAC
    gst_rate_bps: Mapped[int] = mapped_column(Integer, default=1800)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    __table_args__ = (UniqueConstraint("plan_id", "version", name="uq_plan_version"),)


@event.listens_for(PlanVersion, "before_update")
def _plan_versions_immutable(mapper, connection, target):  # noqa: ARG001
    raise ValueError("Plan versions are immutable; publish a new version instead.")


class Addon(Base, IdMixin, TimestampMixin):
    __tablename__ = "billing_addons"
    code: Mapped[str] = mapped_column(String(60), unique=True)
    name: Mapped[str] = mapped_column(String(120))
    price_monthly_paise: Mapped[int] = mapped_column(BigInteger)
    price_yearly_paise: Mapped[int] = mapped_column(BigInteger)
    limits: Mapped[dict[str, Any]]  # extra resources per unit
    hsn_sac: Mapped[str] = mapped_column(String(10), default="998315")
    gst_rate_bps: Mapped[int] = mapped_column(Integer, default=1800)
    active: Mapped[bool] = mapped_column(Boolean, default=True)


class BillingProfile(Base, IdMixin, TimestampMixin):
    """Per-tenant GST details used on invoices."""

    __tablename__ = "billing_profiles"
    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tenants.id", ondelete="CASCADE"), unique=True)
    legal_name: Mapped[str | None] = mapped_column(String(200))
    gstin: Mapped[str | None] = mapped_column(String(15))
    state_code: Mapped[str | None] = mapped_column(String(2))  # GST state code, e.g. "29"
    address: Mapped[str | None] = mapped_column(String(500))


class Subscription(Base, IdMixin, TimestampMixin):
    __tablename__ = "subscriptions"
    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tenants.id", ondelete="CASCADE"), index=True)
    plan_version_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("billing_plan_versions.id"))
    interval: Mapped[str] = mapped_column(String(10), default="monthly")  # monthly | yearly
    status: Mapped[str] = mapped_column(String(20), default="incomplete", index=True)  # incomplete|trialing|active|past_due|cancelled|expired
    addons: Mapped[list[dict[str, Any]] | None] = mapped_column(JSONB)  # [{code, qty, unit_price_paise, hsn_sac, gst_rate_bps}] pinned at purchase
    coupon_code: Mapped[str | None] = mapped_column(String(40))
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    current_period_start: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    current_period_end: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    trial_ends_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    grace_ends_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    cancel_at_period_end: Mapped[bool] = mapped_column(Boolean, default=False)
    cancelled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    pending_change: Mapped[dict[str, Any] | None]  # scheduled downgrade {plan_version_id, interval, effective_at}


Index(
    "uq_subscription_live_per_tenant", Subscription.tenant_id, unique=True,
    postgresql_where=Subscription.status.in_(("incomplete", "trialing", "active", "past_due")),
)


class SubscriptionEvent(Base, IdMixin):
    __tablename__ = "subscription_events"
    subscription_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("subscriptions.id", ondelete="CASCADE"), index=True)
    tenant_id: Mapped[uuid.UUID] = mapped_column(index=True)
    kind: Mapped[str] = mapped_column(String(40))
    detail: Mapped[dict[str, Any] | None]
    actor_user_id: Mapped[uuid.UUID | None] = mapped_column()
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), index=True)


class InvoiceSequence(Base):
    """Numbering configuration: one row per series (prefix + financial year). Allocated with SELECT ... FOR UPDATE."""

    __tablename__ = "invoice_sequences"
    series: Mapped[str] = mapped_column(String(40), primary_key=True)  # e.g. INV/2026-27
    next_value: Mapped[int] = mapped_column(Integer, default=1)


class Invoice(Base, IdMixin, TimestampMixin):
    __tablename__ = "invoices"
    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tenants.id", ondelete="CASCADE"), index=True)
    subscription_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("subscriptions.id", ondelete="SET NULL"), index=True)
    number: Mapped[str] = mapped_column(String(40), unique=True)
    purpose: Mapped[str] = mapped_column(String(20))  # activation | renewal | upgrade | addon | manual
    status: Mapped[str] = mapped_column(String(20), default="open", index=True)  # open | paid | void
    currency: Mapped[str] = mapped_column(String(3), default="INR")
    lines: Mapped[list[dict[str, Any]]] = mapped_column(JSONB)  # [{description, hsn_sac, qty, unit_paise, amount_paise, gst_rate_bps}]
    subtotal_paise: Mapped[int] = mapped_column(BigInteger)  # before discount
    discount_paise: Mapped[int] = mapped_column(BigInteger, default=0)
    taxable_paise: Mapped[int] = mapped_column(BigInteger)
    cgst_paise: Mapped[int] = mapped_column(BigInteger, default=0)
    sgst_paise: Mapped[int] = mapped_column(BigInteger, default=0)
    igst_paise: Mapped[int] = mapped_column(BigInteger, default=0)
    total_paise: Mapped[int] = mapped_column(BigInteger)
    refunded_paise: Mapped[int] = mapped_column(BigInteger, default=0)
    seller: Mapped[dict[str, Any]]  # snapshot: legal name, gstin, state_code, address
    buyer: Mapped[dict[str, Any]]  # snapshot: legal name, gstin, state_code, address
    place_of_supply: Mapped[str | None] = mapped_column(String(2))
    coupon_code: Mapped[str | None] = mapped_column(String(40))
    meta: Mapped[dict[str, Any] | None]  # effect to apply on verified payment
    due_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    issued_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    paid_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    dunning_attempts: Mapped[int] = mapped_column(Integer, default=0)
    last_failed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    reminder_state: Mapped[dict[str, Any] | None]


class CreditNote(Base, IdMixin):
    __tablename__ = "credit_notes"
    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tenants.id", ondelete="CASCADE"), index=True)
    invoice_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("invoices.id", ondelete="CASCADE"), index=True)
    number: Mapped[str] = mapped_column(String(40), unique=True)
    amount_paise: Mapped[int] = mapped_column(BigInteger)  # tax-inclusive
    taxable_paise: Mapped[int] = mapped_column(BigInteger)
    cgst_paise: Mapped[int] = mapped_column(BigInteger, default=0)
    sgst_paise: Mapped[int] = mapped_column(BigInteger, default=0)
    igst_paise: Mapped[int] = mapped_column(BigInteger, default=0)
    reason: Mapped[str | None] = mapped_column(String(300))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Payment(Base, IdMixin, TimestampMixin):
    __tablename__ = "payments"
    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tenants.id", ondelete="CASCADE"), index=True)
    invoice_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("invoices.id", ondelete="CASCADE"), index=True)
    provider: Mapped[str] = mapped_column(String(20))  # razorpay | manual
    amount_paise: Mapped[int] = mapped_column(BigInteger)
    currency: Mapped[str] = mapped_column(String(3), default="INR")
    status: Mapped[str] = mapped_column(String(20), default="created", index=True)  # created|pending|captured|failed|refunded
    provider_order_id: Mapped[str | None] = mapped_column(String(80), unique=True)
    provider_payment_id: Mapped[str | None] = mapped_column(String(80), index=True)
    refunded_paise: Mapped[int] = mapped_column(BigInteger, default=0)
    failure_code: Mapped[str | None] = mapped_column(String(80))
    failure_reason: Mapped[str | None] = mapped_column(String(300))
    callback_seen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))  # client redirect noted; NEVER proof of payment
    captured_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    reference: Mapped[str | None] = mapped_column(String(120))  # manual payments: bank/UTR reference


class RefundRecord(Base, IdMixin, TimestampMixin):
    __tablename__ = "refunds"
    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tenants.id", ondelete="CASCADE"), index=True)
    payment_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("payments.id", ondelete="CASCADE"), index=True)
    invoice_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("invoices.id", ondelete="CASCADE"))
    amount_paise: Mapped[int] = mapped_column(BigInteger)
    status: Mapped[str] = mapped_column(String(20), default="requested")  # requested | processed | failed
    reason: Mapped[str | None] = mapped_column(String(300))
    provider_refund_id: Mapped[str | None] = mapped_column(String(80), unique=True)
    credit_note_id: Mapped[uuid.UUID | None] = mapped_column()
    requested_by: Mapped[uuid.UUID | None] = mapped_column()


class Coupon(Base, IdMixin, TimestampMixin):
    __tablename__ = "coupons"
    code: Mapped[str] = mapped_column(String(40), unique=True)
    description: Mapped[str | None] = mapped_column(String(200))
    percent_bps: Mapped[int | None] = mapped_column(Integer)  # 1000 = 10%
    amount_paise: Mapped[int | None] = mapped_column(BigInteger)  # flat discount
    max_redemptions: Mapped[int | None] = mapped_column(Integer)
    per_tenant_limit: Mapped[int] = mapped_column(Integer, default=1)
    valid_from: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    valid_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    plan_codes: Mapped[list[str] | None] = mapped_column(JSONB)  # NULL = any plan
    active: Mapped[bool] = mapped_column(Boolean, default=True)


class CouponRedemption(Base, IdMixin):
    __tablename__ = "coupon_redemptions"
    coupon_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("coupons.id", ondelete="CASCADE"), index=True)
    tenant_id: Mapped[uuid.UUID] = mapped_column(index=True)
    invoice_id: Mapped[uuid.UUID] = mapped_column(unique=True)
    status: Mapped[str] = mapped_column(String(12), default="reserved")  # reserved | redeemed | released
    discount_paise: Mapped[int] = mapped_column(BigInteger)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class WebhookEvent(Base, IdMixin):
    """Idempotency + audit for provider webhooks. event_id is unique per provider."""

    __tablename__ = "payment_webhook_events"
    provider: Mapped[str] = mapped_column(String(20))
    event_id: Mapped[str] = mapped_column(String(120))
    event_type: Mapped[str] = mapped_column(String(60))
    status: Mapped[str] = mapped_column(String(12), default="processing")  # processing | processed | ignored | failed
    attempts: Mapped[int] = mapped_column(Integer, default=1)
    error: Mapped[str | None] = mapped_column(Text)
    summary: Mapped[dict[str, Any] | None]  # ids and amounts only; never card data
    received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    processed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    __table_args__ = (UniqueConstraint("provider", "event_id", name="uq_webhook_event"),)


@event.listens_for(Invoice, "before_update")
def _paid_invoice_money_frozen(mapper, connection, target):  # noqa: ARG001
    st = inspect(target)
    h = st.attrs.status.history
    if (h.deleted[0] if h.deleted else (h.unchanged[0] if h.unchanged else None)) == "paid":
        for col in ("total_paise", "taxable_paise", "lines", "number"):
            if getattr(st.attrs, col).history.has_changes():
                raise ValueError("Paid invoices are immutable.")
