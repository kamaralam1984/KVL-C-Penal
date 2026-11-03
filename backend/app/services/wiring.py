"""Cross-module wiring done once at startup: ownership resolvers for the file/SSL/backup modules."""
import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.hosting import CustomerDatabase, Domain, Website
from app.services.ownership import register_resolver

_done = False


def _uuid(ref: str) -> uuid.UUID | None:
    try:
        return uuid.UUID(ref)
    except ValueError:
        return None


def _website(db: Session, tenant_id: uuid.UUID, ref: str) -> bool:
    u = _uuid(ref)
    cond = Website.id == u if u else Website.slug == ref
    return db.scalar(select(Website.id).where(Website.tenant_id == tenant_id, cond)) is not None


def _domain(db: Session, tenant_id: uuid.UUID, ref: str) -> bool:
    u = _uuid(ref)
    cond = Domain.id == u if u else Domain.hostname == ref.lower()
    return db.scalar(select(Domain.id).where(Domain.tenant_id == tenant_id, cond)) is not None


def _database(db: Session, tenant_id: uuid.UUID, ref: str) -> bool:
    u = _uuid(ref)
    cond = CustomerDatabase.id == u if u else CustomerDatabase.db_name == ref
    return db.scalar(select(CustomerDatabase.id).where(CustomerDatabase.tenant_id == tenant_id, cond)) is not None


def wire() -> None:
    global _done
    if _done:
        return
    register_resolver("website", _website)
    register_resolver("domain", _domain)
    register_resolver("database", _database)
    _done = True
