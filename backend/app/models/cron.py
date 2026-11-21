"""Customer cron jobs. A job is an HTTPS GET to one of the tenant's own verified domains on a schedule.

There is deliberately no "run a shell command" job type: customer-supplied commands are never executed on the server.
"""
import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Index, Integer, String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base, IdMixin, TimestampMixin


class CronJob(Base, IdMixin, TimestampMixin):
    __tablename__ = "cron_jobs"
    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tenants.id", ondelete="CASCADE"), index=True)
    website_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("websites.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(80))
    url: Mapped[str] = mapped_column(String(500))
    schedule: Mapped[str] = mapped_column(String(80))
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    next_run_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    last_run_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_status: Mapped[str | None] = mapped_column(String(20))  # ok | http_error | error
    last_http_code: Mapped[int | None] = mapped_column(Integer)
    last_error: Mapped[str | None] = mapped_column(String(300))
    consecutive_failures: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    disabled_reason: Mapped[str | None] = mapped_column(String(120))


class CronRun(Base, IdMixin):
    __tablename__ = "cron_runs"
    job_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("cron_jobs.id", ondelete="CASCADE"), index=True)
    tenant_id: Mapped[uuid.UUID] = mapped_column(index=True)
    trigger: Mapped[str] = mapped_column(String(12))  # schedule | manual
    status: Mapped[str] = mapped_column(String(20))
    http_code: Mapped[int | None] = mapped_column(Integer)
    duration_ms: Mapped[int] = mapped_column(Integer, default=0)
    error: Mapped[str | None] = mapped_column(String(300))
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


Index("ix_cron_jobs_due", CronJob.enabled, CronJob.next_run_at)
