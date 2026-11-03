"""Phase 17: API keys, webhooks, idempotency, security events."""
import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import BigInteger, Boolean, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base, IdMixin, TimestampMixin


class ApiKey(Base, IdMixin, TimestampMixin):
    """Only a sha256 of the secret is stored. The plaintext key is returned exactly once."""

    __tablename__ = "api_keys"
    lookup: Mapped[str] = mapped_column(String(16), unique=True)  # public part of the key, used to find the row
    secret_hash: Mapped[str] = mapped_column(String(64))
    name: Mapped[str] = mapped_column(String(100))
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    membership_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("memberships.id", ondelete="CASCADE"))
    tenant_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("tenants.id", ondelete="CASCADE"), index=True)
    scopes: Mapped[dict[str, Any]]  # {"scopes": [...]}
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_used_ip: Mapped[str | None] = mapped_column(String(64))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    rate_limit_per_minute: Mapped[int] = mapped_column(Integer, default=60)
    request_count: Mapped[int] = mapped_column(BigInteger, default=0)
    rotated_from_id: Mapped[uuid.UUID | None] = mapped_column()


class WebhookEndpoint(Base, IdMixin, TimestampMixin):
    __tablename__ = "webhook_endpoints"
    tenant_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("tenants.id", ondelete="CASCADE"), index=True)  # NULL = platform
    created_by: Mapped[uuid.UUID | None] = mapped_column()
    url: Mapped[str] = mapped_column(String(500))
    description: Mapped[str | None] = mapped_column(String(200))
    events: Mapped[dict[str, Any]]  # {"events": ["ticket.created", ...]} or ["*"]
    secret_enc: Mapped[str] = mapped_column(String(512))
    prev_secret_enc: Mapped[str | None] = mapped_column(String(512))
    prev_secret_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    status: Mapped[str] = mapped_column(String(12), default="active")  # active | disabled


class WebhookDelivery(Base, IdMixin):
    __tablename__ = "webhook_deliveries"
    endpoint_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("webhook_endpoints.id", ondelete="CASCADE"), index=True)
    tenant_id: Mapped[uuid.UUID | None] = mapped_column(index=True)
    event_type: Mapped[str] = mapped_column(String(60))
    event_id: Mapped[str] = mapped_column(String(40))
    payload: Mapped[dict[str, Any]]
    status: Mapped[str] = mapped_column(String(12), default="pending", index=True)  # pending | retrying | succeeded | dead
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    next_attempt_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    last_status_code: Mapped[int | None] = mapped_column(Integer)
    last_error: Mapped[str | None] = mapped_column(String(300))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    delivered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class IdempotencyRecord(Base, IdMixin):
    __tablename__ = "idempotency_records"
    user_id: Mapped[uuid.UUID] = mapped_column(index=True)
    key: Mapped[str] = mapped_column(String(128))
    route: Mapped[str] = mapped_column(String(200))
    request_hash: Mapped[str] = mapped_column(String(64))
    state: Mapped[str] = mapped_column(String(12), default="in_progress")  # in_progress | done
    status_code: Mapped[int | None] = mapped_column(Integer)
    response: Mapped[dict[str, Any] | None]
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), index=True)
    __table_args__ = (UniqueConstraint("user_id", "route", "key", name="uq_idempotency"),)


class SecurityEvent(Base, IdMixin):
    __tablename__ = "security_events"
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), index=True)
    kind: Mapped[str] = mapped_column(String(50), index=True)
    severity: Mapped[str] = mapped_column(String(10))  # low | medium | high | critical
    summary: Mapped[str] = mapped_column(String(300))
    tenant_id: Mapped[uuid.UUID | None] = mapped_column(index=True)
    user_id: Mapped[uuid.UUID | None] = mapped_column()
    ip: Mapped[str | None] = mapped_column(String(64))
    detail: Mapped[dict[str, Any] | None]
    status: Mapped[str] = mapped_column(String(14), default="open", index=True)  # open | acknowledged | resolved
    acknowledged_by: Mapped[uuid.UUID | None] = mapped_column()
    acknowledged_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    dedupe_key: Mapped[str | None] = mapped_column(String(120), unique=True)
    routed: Mapped[bool] = mapped_column(Boolean, default=False)


class SecurityAlertRoute(Base, IdMixin, TimestampMixin):
    __tablename__ = "security_alert_routes"
    name: Mapped[str] = mapped_column(String(80))
    min_severity: Mapped[str] = mapped_column(String(10), default="high")
    kinds: Mapped[dict[str, Any] | None]  # {"kinds": [...]}; empty/None = all
    channel: Mapped[str] = mapped_column(String(10))  # email | webhook
    target: Mapped[str | None] = mapped_column(String(320))  # email address; webhook uses platform endpoints
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
