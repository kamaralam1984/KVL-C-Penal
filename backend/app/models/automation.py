"""Phase 15: n8n integration. PostgreSQL stays the source of truth; n8n only executes workflows."""
import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import Boolean, DateTime, Index, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base, IdMixin


class OutboxEvent(Base, IdMixin):
    """Event to deliver to n8n. status: pending -> delivered | (failed, retried with backoff) -> dead."""

    __tablename__ = "automation_outbox"
    event_type: Mapped[str] = mapped_column(String(60), index=True)
    tenant_id: Mapped[uuid.UUID | None] = mapped_column(index=True)
    payload: Mapped[dict[str, Any]]
    correlation_id: Mapped[str] = mapped_column(String(64), index=True)
    idempotency_key: Mapped[str | None] = mapped_column(String(160), unique=True)
    status: Mapped[str] = mapped_column(String(12), default="pending", index=True)  # pending | delivered | dead
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    max_attempts: Mapped[int] = mapped_column(Integer, default=6)
    next_attempt_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), index=True)
    last_error: Mapped[str | None] = mapped_column(String(500))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), index=True)
    delivered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class InboundEvent(Base, IdMixin):
    """Idempotency table for events received from n8n."""

    __tablename__ = "automation_inbound"
    idempotency_key: Mapped[str] = mapped_column(String(160), unique=True)
    body_sha256: Mapped[str] = mapped_column(String(64))
    event_type: Mapped[str] = mapped_column(String(60))
    correlation_id: Mapped[str | None] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(12), default="processing")  # processing | processed | failed
    result: Mapped[dict[str, Any] | None]
    received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class WorkflowState(Base, IdMixin):
    __tablename__ = "automation_workflow_state"
    key: Mapped[str] = mapped_column(String(60), unique=True)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    updated_by: Mapped[uuid.UUID | None] = mapped_column()
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class WorkflowExecution(Base, IdMixin):
    __tablename__ = "automation_executions"
    workflow_key: Mapped[str] = mapped_column(String(60), index=True)
    correlation_id: Mapped[str] = mapped_column(String(64), index=True)
    tenant_id: Mapped[uuid.UUID | None] = mapped_column(index=True)
    outbox_event_id: Mapped[uuid.UUID | None] = mapped_column()
    external_execution_id: Mapped[str | None] = mapped_column(String(80))
    status: Mapped[str] = mapped_column(String(12), default="running", index=True)  # running | succeeded | failed
    error: Mapped[str | None] = mapped_column(Text)
    escalated: Mapped[bool] = mapped_column(Boolean, default=False)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), index=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


Index("ix_outbox_due", OutboxEvent.status, OutboxEvent.next_attempt_at)
