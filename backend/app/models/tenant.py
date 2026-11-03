import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import BigInteger, DateTime, ForeignKey, Integer, String, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base, IdMixin, TimestampMixin

TENANT_STATUSES = ("pending", "trial", "active", "suspended", "cancelled")


class Tenant(Base, IdMixin, TimestampMixin):
    __tablename__ = "tenants"
    name: Mapped[str] = mapped_column(String(200))
    slug: Mapped[str] = mapped_column(String(63), unique=True, index=True)
    status: Mapped[str] = mapped_column(String(20), default="pending", index=True)
    contact_email: Mapped[str | None] = mapped_column(String(320))
    owner_user_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    reseller_id: Mapped[uuid.UUID | None] = mapped_column(index=True)
    plan_code: Mapped[str | None] = mapped_column(String(60))
    settings: Mapped[dict[str, Any] | None]
    trial_ends_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    grace_ends_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    suspended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    cancelled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    retention_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class TenantQuota(Base, IdMixin, TimestampMixin):
    __tablename__ = "tenant_quotas"
    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tenants.id", ondelete="CASCADE"), index=True)
    resource: Mapped[str] = mapped_column(String(40))  # websites | domains | databases | disk_mb | bandwidth_gb | apps
    limit_value: Mapped[int] = mapped_column(BigInteger)
    used_value: Mapped[int] = mapped_column(BigInteger, default=0)
    __table_args__ = (UniqueConstraint("tenant_id", "resource", name="uq_tenant_quota"),)


class TenantActivity(Base, IdMixin):
    __tablename__ = "tenant_activity"
    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tenants.id", ondelete="CASCADE"), index=True)
    actor_user_id: Mapped[uuid.UUID | None] = mapped_column()
    action: Mapped[str] = mapped_column(String(80))
    summary: Mapped[str] = mapped_column(String(300))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), index=True)


class PlatformSetting(Base, IdMixin, TimestampMixin):
    """Key/value business rules (grace period days, trial days, retention days...)."""

    __tablename__ = "platform_settings"
    key: Mapped[str] = mapped_column(String(80), unique=True)
    value: Mapped[dict[str, Any] | None]
    quota_default: Mapped[int | None] = mapped_column(Integer)
