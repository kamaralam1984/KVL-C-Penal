"""Agent identity: one-time enrollment tokens, HMAC credentials and the replay-protection nonce ledger."""
import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Index, String, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base, IdMixin


class EnrollmentToken(Base, IdMixin):
    """Single-use. Only the SHA-256 of the token is stored; the token itself is shown once at creation."""

    __tablename__ = "agent_enrollment_tokens"
    server_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("servers.id", ondelete="CASCADE"), index=True)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_by: Mapped[uuid.UUID | None] = mapped_column()
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class AgentCredential(Base, IdMixin):
    """HMAC credential. The secret is stored Fernet-encrypted (the control plane must recompute signatures)."""

    __tablename__ = "agent_credentials"
    server_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("servers.id", ondelete="CASCADE"), index=True)
    key_id: Mapped[str] = mapped_column(String(40), unique=True)
    secret_enc: Mapped[str] = mapped_column(String(512))
    status: Mapped[str] = mapped_column(String(20), default="active")  # active | revoked
    created_by: Mapped[uuid.UUID | None] = mapped_column()
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class AgentNonce(Base):
    """Seen request nonces. The unique constraint makes replay rejection atomic; rows are purged after the window."""

    __tablename__ = "agent_nonces"
    __table_args__ = (UniqueConstraint("key_id", "nonce", name="uq_agent_nonce"),)
    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    key_id: Mapped[str] = mapped_column(String(40))
    nonce: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


Index("ix_agent_nonces_created", AgentNonce.created_at)
