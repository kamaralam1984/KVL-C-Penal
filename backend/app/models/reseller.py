"""Phase 13: reseller lifecycle, packages, client links, white-label domains, commission ledger."""
import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import BigInteger, Boolean, DateTime, ForeignKey, Index, Integer, String, Text, UniqueConstraint, event, func, inspect
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base, IdMixin, TimestampMixin

RESELLER_STATUSES = ("pending_approval", "approved", "rejected", "suspended")
PACKAGE_RESOURCES = ("websites", "domains", "databases", "apps", "disk_mb", "bandwidth_gb")


class Reseller(Base, IdMixin, TimestampMixin):
    __tablename__ = "resellers"
    name: Mapped[str] = mapped_column(String(200))
    legal_name: Mapped[str | None] = mapped_column(String(200))
    contact_email: Mapped[str] = mapped_column(String(320))
    owner_user_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), index=True)
    status: Mapped[str] = mapped_column(String(20), default="pending_approval", index=True)
    business: Mapped[dict[str, Any] | None]  # gstin, address, phone, website
    limits: Mapped[dict[str, Any] | None]  # max_clients, allocation{resource:int}, unit_costs{resource:paise}
    branding: Mapped[dict[str, Any] | None]  # portal_name, logo_url, primary_color, accent_color, ...
    applied_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    decided_by: Mapped[uuid.UUID | None] = mapped_column()
    decision_note: Mapped[str | None] = mapped_column(String(500))


class ResellerPackage(Base, IdMixin, TimestampMixin):
    __tablename__ = "reseller_packages"
    reseller_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("resellers.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(120))
    description: Mapped[str | None] = mapped_column(String(500))
    limits: Mapped[dict[str, Any]]  # resource -> int
    retail_price_paise: Mapped[int] = mapped_column(BigInteger)  # per month, what the reseller charges its client
    wholesale_price_paise: Mapped[int] = mapped_column(BigInteger, default=0)  # derived from platform unit_costs at save time
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    __table_args__ = (UniqueConstraint("reseller_id", "name", name="uq_reseller_package_name"),)


class ResellerClient(Base, IdMixin, TimestampMixin):
    """Link between a reseller and a tenant it owns. The tenant row's reseller_id is the authority; this holds commercial detail."""

    __tablename__ = "reseller_clients"
    reseller_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("resellers.id", ondelete="CASCADE"), index=True)
    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tenants.id", ondelete="CASCADE"), unique=True)
    package_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("reseller_packages.id", ondelete="SET NULL"))
    renews_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    notes: Mapped[str | None] = mapped_column(String(500))


class ResellerDomain(Base, IdMixin, TimestampMixin):
    __tablename__ = "reseller_domains"
    reseller_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("resellers.id", ondelete="CASCADE"), index=True)
    domain: Mapped[str] = mapped_column(String(255), unique=True)
    txt_token: Mapped[str] = mapped_column(String(64))
    state: Mapped[str] = mapped_column(String(20), default="pending")  # pending | verified | failed
    last_checked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    failure_reason: Mapped[str | None] = mapped_column(String(300))


class CommissionRule(Base, IdMixin, TimestampMixin):
    __tablename__ = "commission_rules"
    reseller_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("resellers.id", ondelete="CASCADE"), index=True)  # NULL = platform default
    name: Mapped[str] = mapped_column(String(120))
    rate_bps: Mapped[int] = mapped_column(Integer)  # 1000 = 10%
    plan_code: Mapped[str | None] = mapped_column(String(60))  # NULL = all plans
    min_invoice_paise: Mapped[int] = mapped_column(BigInteger, default=0)
    active: Mapped[bool] = mapped_column(Boolean, default=True)


class CommissionEntry(Base, IdMixin):
    """Append-only ledger. Balances are ALWAYS computed from these rows. Amount may be negative (reversal)."""

    __tablename__ = "commission_entries"
    reseller_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("resellers.id", ondelete="CASCADE"), index=True)
    tenant_id: Mapped[uuid.UUID | None] = mapped_column(index=True)
    invoice_id: Mapped[uuid.UUID | None] = mapped_column(index=True)
    kind: Mapped[str] = mapped_column(String(20), default="sale")  # sale | reversal
    ref: Mapped[str] = mapped_column(String(80))  # idempotency reference: invoice id or refund id
    amount_paise: Mapped[int] = mapped_column(BigInteger)
    rate_bps: Mapped[int | None] = mapped_column(Integer)
    base_paise: Mapped[int | None] = mapped_column(BigInteger)
    status: Mapped[str] = mapped_column(String(20), default="pending", index=True)  # pending | approved | paid
    payout_reference: Mapped[str | None] = mapped_column(String(120))
    note: Mapped[str | None] = mapped_column(String(300))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    paid_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    __table_args__ = (UniqueConstraint("reseller_id", "kind", "ref", name="uq_commission_ref"),)


@event.listens_for(CommissionEntry, "before_update")
def _paid_entries_are_immutable(mapper, connection, target):  # noqa: ARG001
    hist = inspect(target).attrs.status.history
    previous = hist.deleted[0] if hist.deleted else (hist.unchanged[0] if hist.unchanged else None)
    if previous == "paid":
        raise ValueError("Paid commission entries are immutable.")


@event.listens_for(CommissionEntry, "before_delete")
def _no_ledger_delete(mapper, connection, target):  # noqa: ARG001
    raise ValueError("Commission ledger entries cannot be deleted.")


Index("ix_commission_status", CommissionEntry.reseller_id, CommissionEntry.status)
_ = Text
