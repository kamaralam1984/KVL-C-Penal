import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import BigInteger, Boolean, DateTime, ForeignKey, Integer, String, Text, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base, IdMixin, TimestampMixin

BACKUP_TYPES = ("website_files", "database", "website_config", "saas_config")


class RetentionPolicy(Base, IdMixin, TimestampMixin):
    __tablename__ = "retention_policies"
    tenant_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("tenants.id", ondelete="CASCADE"), index=True)  # NULL = platform default
    name: Mapped[str] = mapped_column(String(120))
    keep_last: Mapped[int] = mapped_column(Integer, default=3)
    keep_daily: Mapped[int] = mapped_column(Integer, default=7)
    keep_weekly: Mapped[int] = mapped_column(Integer, default=4)
    keep_monthly: Mapped[int] = mapped_column(Integer, default=3)
    min_keep: Mapped[int] = mapped_column(Integer, default=2)  # never prune below this many completed backups


class BackupDestination(Base, IdMixin, TimestampMixin):
    """Off-site destination. Credentials are encrypted and never returned. Status stays not_configured until set."""

    __tablename__ = "backup_destinations"
    tenant_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("tenants.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(120))
    kind: Mapped[str] = mapped_column(String(10))  # local | sftp | s3
    config: Mapped[dict[str, Any] | None]  # non-secret: host, port, bucket, endpoint, path
    credentials_enc: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(20), default="not_configured")
    last_test_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_error: Mapped[str | None] = mapped_column(String(400))


class BackupSchedule(Base, IdMixin, TimestampMixin):
    __tablename__ = "backup_schedules"
    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tenants.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(120))
    backup_type: Mapped[str] = mapped_column(String(20))
    resource_ref: Mapped[str | None] = mapped_column(String(253))
    cron: Mapped[str] = mapped_column(String(80))
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    encrypt: Mapped[bool] = mapped_column(Boolean, default=False)
    retention_policy_id: Mapped[uuid.UUID | None] = mapped_column()
    destination_id: Mapped[uuid.UUID | None] = mapped_column()
    last_run_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    next_run_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    created_by: Mapped[uuid.UUID | None] = mapped_column()


class BackupRecord(Base, IdMixin, TimestampMixin):
    """Backup inventory. `verification_status == verified` marks a confirmed recovery point."""

    __tablename__ = "backup_records"
    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tenants.id", ondelete="CASCADE"), index=True)
    backup_type: Mapped[str] = mapped_column(String(20))
    resource_ref: Mapped[str | None] = mapped_column(String(253), index=True)
    status: Mapped[str] = mapped_column(String(14), default="running", index=True)  # running|completed|failed|pruned
    verification_status: Mapped[str] = mapped_column(String(14), default="unverified")  # unverified|verifying|verified|mismatch|failed
    verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    size_bytes: Mapped[int | None] = mapped_column(BigInteger)
    sha256: Mapped[str | None] = mapped_column(String(64))
    archive_path: Mapped[str | None] = mapped_column(String(512))
    encrypted: Mapped[bool] = mapped_column(Boolean, default=False)
    key_id: Mapped[str | None] = mapped_column(String(32))
    entries: Mapped[int | None] = mapped_column(Integer)
    layout: Mapped[str | None] = mapped_column(String(10))
    server_id: Mapped[uuid.UUID | None] = mapped_column()
    schedule_id: Mapped[uuid.UUID | None] = mapped_column(index=True)
    destination_id: Mapped[uuid.UUID | None] = mapped_column()
    offsite_status: Mapped[str] = mapped_column(String(20), default="not_configured")
    job_id: Mapped[uuid.UUID | None] = mapped_column()
    verify_job_id: Mapped[uuid.UUID | None] = mapped_column()
    trigger: Mapped[str] = mapped_column(String(10), default="manual")  # manual | schedule
    requested_by: Mapped[uuid.UUID | None] = mapped_column()
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), server_default=func.now())
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    error: Mapped[str | None] = mapped_column(String(600))
    alerted: Mapped[bool] = mapped_column(Boolean, default=False)


class RestoreRequest(Base, IdMixin, TimestampMixin):
    __tablename__ = "restore_requests"
    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tenants.id", ondelete="CASCADE"), index=True)
    backup_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("backup_records.id", ondelete="CASCADE"), index=True)
    mode: Mapped[str] = mapped_column(String(10))  # isolated | overwrite
    status: Mapped[str] = mapped_column(String(18), default="previewed", index=True)  # previewed|pending_approval|running|completed|failed|rejected
    impact = mapped_column(JSONB, nullable=True)
    restore_token: Mapped[str] = mapped_column(String(32))  # agent restore_id
    location: Mapped[str | None] = mapped_column(String(512))
    requested_by: Mapped[uuid.UUID | None] = mapped_column()
    approved_by: Mapped[uuid.UUID | None] = mapped_column()
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    job_id: Mapped[uuid.UUID | None] = mapped_column()
    result = mapped_column(JSONB, nullable=True)
    error: Mapped[str | None] = mapped_column(String(600))
