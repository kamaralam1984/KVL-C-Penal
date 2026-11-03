import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import DateTime, ForeignKey, Index, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base, IdMixin, TimestampMixin

JOB_STATUSES = ("queued", "running", "succeeded", "failed", "timed_out", "cancelled")


class Server(Base, IdMixin, TimestampMixin):
    __tablename__ = "servers"
    name: Mapped[str] = mapped_column(String(120), unique=True)
    hostname: Mapped[str | None] = mapped_column(String(255))
    status: Mapped[str] = mapped_column(String(20), default="pending")  # pending | registered | disabled
    agent_version: Mapped[str | None] = mapped_column(String(40))
    os_info: Mapped[dict[str, Any] | None]
    labels: Mapped[dict[str, Any] | None]
    last_heartbeat_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    registered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class AgentJob(Base, IdMixin):
    """A single allow-listed operation for one server. Result summaries never contain secrets."""

    __tablename__ = "agent_jobs"
    server_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("servers.id", ondelete="CASCADE"), index=True)
    tenant_id: Mapped[uuid.UUID | None] = mapped_column(index=True)
    operation: Mapped[str] = mapped_column(String(80), index=True)
    params: Mapped[dict[str, Any] | None]
    status: Mapped[str] = mapped_column(String(20), default="queued", index=True)
    result: Mapped[dict[str, Any] | None]
    error: Mapped[str | None] = mapped_column(Text)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    timeout_seconds: Mapped[int] = mapped_column(Integer, default=60)
    requested_by: Mapped[uuid.UUID | None] = mapped_column()
    correlation_id: Mapped[str | None] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), index=True)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


Index("ix_agent_jobs_poll", AgentJob.server_id, AgentJob.status, AgentJob.created_at)
