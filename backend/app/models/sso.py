"""External sign-in identities (Google / Microsoft OpenID Connect) linked to panel users."""
import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, String, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base, IdMixin


class UserIdentity(Base, IdMixin):
    __tablename__ = "user_identities"
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    provider: Mapped[str] = mapped_column(String(20))  # google | microsoft
    subject: Mapped[str] = mapped_column(String(200))  # google: sub; microsoft: tid:oid
    email: Mapped[str | None] = mapped_column(String(320))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    __table_args__ = (UniqueConstraint("provider", "subject", name="uq_identity_provider_subject"), UniqueConstraint("user_id", "provider", name="uq_identity_user_provider"))
