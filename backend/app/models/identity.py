import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import Boolean, DateTime, ForeignKey, Index, Integer, String, Text, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.db import Base, IdMixin, TimestampMixin


class User(Base, IdMixin, TimestampMixin):
    __tablename__ = "users"
    email: Mapped[str] = mapped_column(String(320), unique=True, index=True)  # stored lowercased
    name: Mapped[str] = mapped_column(String(200))
    password_hash: Mapped[str] = mapped_column(String(255))
    status: Mapped[str] = mapped_column(String(20), default="active")  # active | disabled
    email_verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    totp_secret_enc: Mapped[str | None] = mapped_column(String(512))
    totp_enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    failed_logins: Mapped[int] = mapped_column(Integer, default=0)
    locked_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    memberships: Mapped[list["Membership"]] = relationship(back_populates="user", cascade="all, delete-orphan")


class Role(Base, IdMixin):
    __tablename__ = "roles"
    name: Mapped[str] = mapped_column(String(50), unique=True)  # super_admin, hosting_admin, support, customer, reseller
    label: Mapped[str] = mapped_column(String(100))
    scope: Mapped[str] = mapped_column(String(20))  # platform | tenant
    privileged: Mapped[bool] = mapped_column(Boolean, default=False)  # cannot be self-assigned or granted by non-owners


class Permission(Base, IdMixin):
    __tablename__ = "permissions"
    code: Mapped[str] = mapped_column(String(100), unique=True)  # "resource:action"


class RolePermission(Base):
    __tablename__ = "role_permissions"
    role_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("roles.id", ondelete="CASCADE"), primary_key=True)
    permission_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("permissions.id", ondelete="CASCADE"), primary_key=True)


class Membership(Base, IdMixin, TimestampMixin):
    """Binds a user to a role, optionally inside a tenant. tenant_id NULL = platform staff."""

    __tablename__ = "memberships"
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    role_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("roles.id"))
    tenant_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("tenants.id", ondelete="CASCADE"), index=True)
    reseller_id: Mapped[uuid.UUID | None] = mapped_column(index=True)  # set for reseller members (Phase 13)
    status: Mapped[str] = mapped_column(String(20), default="active")
    user: Mapped[User] = relationship(back_populates="memberships")
    role: Mapped[Role] = relationship()
    __table_args__ = (UniqueConstraint("user_id", "role_id", "tenant_id", name="uq_membership"),)


class Invitation(Base, IdMixin, TimestampMixin):
    __tablename__ = "invitations"
    email: Mapped[str] = mapped_column(String(320), index=True)
    role_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("roles.id"))
    tenant_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("tenants.id", ondelete="CASCADE"))
    invited_by: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"))
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    accepted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class AuthSession(Base, IdMixin, TimestampMixin):
    """One row per refresh-token family member. Reuse of a rotated token revokes the whole family."""

    __tablename__ = "auth_sessions"
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    family_id: Mapped[uuid.UUID] = mapped_column(index=True)
    refresh_hash: Mapped[str] = mapped_column(String(64), unique=True)
    active_membership_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("memberships.id", ondelete="SET NULL"))
    ip: Mapped[str | None] = mapped_column(String(64))
    user_agent: Mapped[str | None] = mapped_column(String(300))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    rotated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class OtpChallenge(Base, IdMixin):
    __tablename__ = "otp_challenges"
    email: Mapped[str] = mapped_column(String(320), index=True)
    purpose: Mapped[str] = mapped_column(String(30))  # verify_email | login | password_reset
    code_hash: Mapped[str] = mapped_column(String(64))
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    consumed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class LoginEvent(Base, IdMixin):
    __tablename__ = "login_events"
    user_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), index=True)
    email: Mapped[str] = mapped_column(String(320))
    outcome: Mapped[str] = mapped_column(String(30))  # success | bad_password | locked | unknown_user | otp_failed
    suspicious: Mapped[bool] = mapped_column(Boolean, default=False)
    ip: Mapped[str | None] = mapped_column(String(64))
    user_agent: Mapped[str | None] = mapped_column(String(300))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), index=True)


class AuditEvent(Base, IdMixin):
    """Append-only security/business audit trail. Never contains credentials or tokens."""

    __tablename__ = "audit_events"
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), index=True)
    actor_user_id: Mapped[uuid.UUID | None] = mapped_column(index=True)
    tenant_id: Mapped[uuid.UUID | None] = mapped_column(index=True)
    action: Mapped[str] = mapped_column(String(100), index=True)
    target_type: Mapped[str | None] = mapped_column(String(60))
    target_id: Mapped[str | None] = mapped_column(String(64))
    detail: Mapped[dict[str, Any] | None]
    ip: Mapped[str | None] = mapped_column(String(64))
    request_id: Mapped[str | None] = mapped_column(String(64))


class EmailOutbox(Base, IdMixin):
    """Outgoing mail queue. In dev the 'console' transport just keeps rows here (no SMTP configured)."""

    __tablename__ = "email_outbox"
    to_email: Mapped[str] = mapped_column(String(320), index=True)
    subject: Mapped[str] = mapped_column(String(300))
    body: Mapped[str] = mapped_column(Text)
    kind: Mapped[str] = mapped_column(String(40))
    tenant_id: Mapped[uuid.UUID | None] = mapped_column(index=True)
    status: Mapped[str] = mapped_column(String(20), default="queued")  # queued | sent | failed
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


Index("ix_audit_tenant_action", AuditEvent.tenant_id, AuditEvent.action)
