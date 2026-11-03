import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import Boolean, DateTime, ForeignKey, Index, Integer, String, Text, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base, IdMixin, TimestampMixin

CERT_STATES = ("pending", "issued", "renewing", "expired", "failed")


class Certificate(Base, IdMixin, TimestampMixin):
    """Certificate METADATA only. Private keys live on the server and are never read, stored or returned."""

    __tablename__ = "certificates"
    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tenants.id", ondelete="CASCADE"), index=True)
    domain: Mapped[str] = mapped_column(String(253), index=True)
    server_id: Mapped[uuid.UUID | None] = mapped_column()
    status: Mapped[str] = mapped_column(String(12), default="pending", index=True)
    environment: Mapped[str] = mapped_column(String(12), default="staging")  # staging | production
    issuer: Mapped[str | None] = mapped_column(String(200))
    not_before: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    not_after: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    serial: Mapped[str | None] = mapped_column(String(80))
    fingerprint_sha256: Mapped[str | None] = mapped_column(String(64))
    sans = mapped_column(JSONB, nullable=True)
    auto_renew: Mapped[bool] = mapped_column(Boolean, default=True)
    job_id: Mapped[uuid.UUID | None] = mapped_column()
    last_error: Mapped[str | None] = mapped_column(String(600))
    last_attempt_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    renewal_failures: Mapped[int] = mapped_column(Integer, default=0)
    last_renewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    requested_by: Mapped[uuid.UUID | None] = mapped_column()
    __table_args__ = (UniqueConstraint("tenant_id", "domain", "environment", name="uq_cert_domain_env"),)


class CloudflareConnection(Base, IdMixin, TimestampMixin):
    """Scoped API token (encrypted, never returned) and the zones it is authorised for."""

    __tablename__ = "cloudflare_connections"
    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tenants.id", ondelete="CASCADE"), unique=True)
    label: Mapped[str | None] = mapped_column(String(120))
    token_enc: Mapped[str] = mapped_column(Text)
    token_fingerprint: Mapped[str] = mapped_column(String(16))
    status: Mapped[str] = mapped_column(String(20), default="connected")  # connected | invalid | permission_denied | rate_limited
    zones = mapped_column(JSONB, nullable=True)  # [{id,name,status}]
    permissions: Mapped[dict[str, Any] | None]
    last_checked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_error: Mapped[str | None] = mapped_column(String(400))
    created_by: Mapped[uuid.UUID | None] = mapped_column()


class DnsRecord(Base, IdMixin, TimestampMixin):
    """Local mirror of a provider DNS record (refreshed by sync; changes only go through DnsChange)."""

    __tablename__ = "dns_records"
    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tenants.id", ondelete="CASCADE"), index=True)
    domain: Mapped[str] = mapped_column(String(253), index=True)
    provider: Mapped[str] = mapped_column(String(20), default="cloudflare")
    zone_id: Mapped[str | None] = mapped_column(String(64))
    provider_record_id: Mapped[str | None] = mapped_column(String(64), index=True)
    rtype: Mapped[str] = mapped_column(String(10))
    name: Mapped[str] = mapped_column(String(253))
    content: Mapped[str] = mapped_column(Text)
    ttl: Mapped[int] = mapped_column(Integer, default=1)
    priority: Mapped[int | None] = mapped_column(Integer)
    proxied: Mapped[bool | None] = mapped_column(Boolean)
    synced_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class DnsChange(Base, IdMixin, TimestampMixin):
    """An explicit, previewed, confirmed and audited DNS change targeting exactly one record."""

    __tablename__ = "dns_changes"
    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tenants.id", ondelete="CASCADE"), index=True)
    domain: Mapped[str] = mapped_column(String(253), index=True)
    action: Mapped[str] = mapped_column(String(10))  # create | update | delete
    target_record_id: Mapped[uuid.UUID | None] = mapped_column()  # DnsRecord.id (update/delete)
    before = mapped_column(JSONB, nullable=True)
    after = mapped_column(JSONB, nullable=True)
    diff = mapped_column(JSONB, nullable=True)
    warnings = mapped_column(JSONB, nullable=True)
    status: Mapped[str] = mapped_column(String(16), default="previewed", index=True)  # previewed|applied|failed|rejected|violation
    production: Mapped[bool] = mapped_column(Boolean, default=True)
    protected_ack: Mapped[bool] = mapped_column(Boolean, default=False)
    rollback_instructions: Mapped[str | None] = mapped_column(Text)
    rollback_of: Mapped[uuid.UUID | None] = mapped_column()
    verification: Mapped[str] = mapped_column(String(16), default="not_checked")  # not_checked|pending|propagated|mismatch|skipped
    verification_detail: Mapped[dict[str, Any] | None]
    error: Mapped[str | None] = mapped_column(String(600))
    requested_by: Mapped[uuid.UUID | None] = mapped_column()
    confirmed_by: Mapped[uuid.UUID | None] = mapped_column()
    applied_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
