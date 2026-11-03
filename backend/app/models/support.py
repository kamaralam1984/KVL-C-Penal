"""Phase 16: tickets, knowledge base, announcements."""
import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import BigInteger, Boolean, DateTime, ForeignKey, Index, Integer, Sequence, String, Text, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base, IdMixin, TimestampMixin

TICKET_PRIORITIES = ("low", "normal", "high", "urgent")
TICKET_STATUSES = ("open", "pending", "on_hold", "resolved", "closed")
ticket_number_seq = Sequence("ticket_number_seq", start=1000)


class SupportDepartment(Base, IdMixin, TimestampMixin):
    __tablename__ = "support_departments"
    name: Mapped[str] = mapped_column(String(80), unique=True)
    description: Mapped[str | None] = mapped_column(String(300))
    active: Mapped[bool] = mapped_column(Boolean, default=True)


class DepartmentMember(Base):
    __tablename__ = "support_department_members"
    department_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("support_departments.id", ondelete="CASCADE"), primary_key=True)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), primary_key=True)


class Ticket(Base, IdMixin, TimestampMixin):
    __tablename__ = "tickets"
    number: Mapped[int] = mapped_column(BigInteger, ticket_number_seq, server_default=ticket_number_seq.next_value(), unique=True)
    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tenants.id", ondelete="CASCADE"), index=True)
    created_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    subject: Mapped[str] = mapped_column(String(200))
    category: Mapped[str] = mapped_column(String(40), default="general")
    department_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("support_departments.id", ondelete="SET NULL"), index=True)
    priority: Mapped[str] = mapped_column(String(10), default="normal", index=True)
    status: Mapped[str] = mapped_column(String(12), default="open", index=True)
    assignee_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), index=True)
    first_response_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    first_response_due_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    resolution_due_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    first_response_breached: Mapped[bool] = mapped_column(Boolean, default=False)
    resolution_breached: Mapped[bool] = mapped_column(Boolean, default=False)
    escalation_level: Mapped[int] = mapped_column(Integer, default=0)
    escalated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_activity_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    hold_started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class TicketMessage(Base, IdMixin):
    __tablename__ = "ticket_messages"
    ticket_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tickets.id", ondelete="CASCADE"), index=True)
    tenant_id: Mapped[uuid.UUID] = mapped_column(index=True)
    author_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    author_type: Mapped[str] = mapped_column(String(10))  # customer | staff
    body: Mapped[str] = mapped_column(Text)
    is_internal: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class TicketAttachment(Base, IdMixin):
    __tablename__ = "ticket_attachments"
    ticket_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tickets.id", ondelete="CASCADE"), index=True)
    message_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("ticket_messages.id", ondelete="SET NULL"))
    tenant_id: Mapped[uuid.UUID] = mapped_column(index=True)
    uploaded_by: Mapped[uuid.UUID | None] = mapped_column()
    original_name: Mapped[str] = mapped_column(String(200))  # sanitised display name only; never used as a path
    stored_name: Mapped[str] = mapped_column(String(80), unique=True)  # random, generated server-side
    content_type: Mapped[str] = mapped_column(String(80))
    size: Mapped[int] = mapped_column(Integer)
    sha256: Mapped[str] = mapped_column(String(64))
    is_internal: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class TicketEvent(Base, IdMixin):
    """Ticket history. is_internal events (assignment, priority, SLA) are never shown to customers."""

    __tablename__ = "ticket_events"
    ticket_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tickets.id", ondelete="CASCADE"), index=True)
    actor_id: Mapped[uuid.UUID | None] = mapped_column()
    kind: Mapped[str] = mapped_column(String(40))
    detail: Mapped[dict[str, Any] | None]
    is_internal: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


# ---------------- knowledge base ----------------
class KbCategory(Base, IdMixin, TimestampMixin):
    __tablename__ = "kb_categories"
    name: Mapped[str] = mapped_column(String(80), unique=True)
    slug: Mapped[str] = mapped_column(String(80), unique=True)
    description: Mapped[str | None] = mapped_column(String(300))


class KbArticle(Base, IdMixin, TimestampMixin):
    __tablename__ = "kb_articles"
    slug: Mapped[str] = mapped_column(String(120), unique=True)
    title: Mapped[str] = mapped_column(String(200))
    summary: Mapped[str | None] = mapped_column(String(300))
    body: Mapped[str] = mapped_column(Text)
    category_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("kb_categories.id", ondelete="SET NULL"), index=True)
    status: Mapped[str] = mapped_column(String(12), default="draft", index=True)  # draft | published
    author_id: Mapped[uuid.UUID | None] = mapped_column()
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    helpful_yes: Mapped[int] = mapped_column(Integer, default=0)
    helpful_no: Mapped[int] = mapped_column(Integer, default=0)


class KbFeedback(Base):
    __tablename__ = "kb_feedback"
    article_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("kb_articles.id", ondelete="CASCADE"), primary_key=True)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), primary_key=True)
    helpful: Mapped[bool] = mapped_column(Boolean)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


# ---------------- announcements ----------------
class Announcement(Base, IdMixin, TimestampMixin):
    __tablename__ = "announcements"
    title: Mapped[str] = mapped_column(String(200))
    body: Mapped[str] = mapped_column(Text)
    severity: Mapped[str] = mapped_column(String(10), default="info")  # info | warning | critical
    status: Mapped[str] = mapped_column(String(12), default="draft", index=True)  # draft | published | archived
    publish_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    audience_roles: Mapped[dict[str, Any] | None]  # {"roles": [...], "tenant_ids": [...]}; empty = everyone
    require_ack: Mapped[bool] = mapped_column(Boolean, default=False)
    created_by: Mapped[uuid.UUID | None] = mapped_column()


class AnnouncementRead(Base):
    __tablename__ = "announcement_reads"
    announcement_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("announcements.id", ondelete="CASCADE"), primary_key=True)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), primary_key=True)
    read_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    acked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


Index("ix_tickets_tenant_status", Ticket.tenant_id, Ticket.status)
