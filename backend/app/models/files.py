import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import BigInteger, Boolean, DateTime, ForeignKey, String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base, IdMixin


class FileOperation(Base, IdMixin):
    """One requested file-manager action: links a tenant, a site and an agent job, and tracks disk_mb quota reservations."""

    __tablename__ = "file_operations"
    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tenants.id", ondelete="CASCADE"), index=True)
    site_ref: Mapped[str] = mapped_column(String(253))
    op: Mapped[str] = mapped_column(String(30))
    path: Mapped[str | None] = mapped_column(String(1024))
    job_id: Mapped[uuid.UUID | None] = mapped_column(index=True)
    actor_user_id: Mapped[uuid.UUID | None] = mapped_column()
    reserved_bytes: Mapped[int] = mapped_column(BigInteger, default=0)  # quota reserved up front (writes)
    settled: Mapped[bool] = mapped_column(Boolean, default=False)
    detail: Mapped[dict[str, Any] | None]
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), index=True)
