import uuid
from typing import Any

from sqlalchemy import ForeignKey, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base, IdMixin, TimestampMixin


class ResourceRef(Base, IdMixin, TimestampMixin):
    """Tenant ownership of a website / domain / database by *reference* (no FK to tables owned by other modules).

    Interim registry for Phases 9-11. `register_resolver()` in app.services.ownership lets the real websites/domains/databases
    tables answer ownership instead; this table then only supplies agent paths (root_rel, config_rel, dump_rel).
    """

    __tablename__ = "resource_refs"
    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tenants.id", ondelete="CASCADE"), index=True)
    kind: Mapped[str] = mapped_column(String(20))  # website | domain | database
    ref: Mapped[str] = mapped_column(String(253))  # slug / id / hostname
    label: Mapped[str | None] = mapped_column(String(200))
    server_id: Mapped[uuid.UUID | None] = mapped_column()
    root_rel: Mapped[str | None] = mapped_column(String(255))  # website root relative to the agent data dir
    config_rel: Mapped[str | None] = mapped_column(String(255))  # website configuration path (backups)
    dump_rel: Mapped[str | None] = mapped_column(String(255))  # staged database dump path (backups)
    attrs: Mapped[dict[str, Any] | None]
    __table_args__ = (UniqueConstraint("kind", "ref", name="uq_resource_ref"),)
