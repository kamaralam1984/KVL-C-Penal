"""Tenant ownership of websites, domains and databases by reference, independent of the modules that own those tables.

`owns_resource(db, tenant_id, kind, ref)` is the single question every Phase 9-11 route asks. It consults, in order,
resolvers registered by other modules (`register_resolver`) and then this module's own `resource_refs` registry.
Anything that is not positively owned is reported as not found (404) by `require_owned`.
"""
import uuid
from typing import Callable

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.deps import Ctx, api_error
from app.models.ownership import ResourceRef

KINDS = ("website", "domain", "database")
Resolver = Callable[[Session, uuid.UUID, str], bool]
_resolvers: dict[str, list[Resolver]] = {}


def register_resolver(kind: str, fn: Resolver) -> None:
    """Wire the real websites/domains/databases tables: fn(db, tenant_id, ref) -> True when the tenant owns `ref`."""
    _resolvers.setdefault(kind, []).append(fn)


def norm_ref(kind: str, ref: str) -> str:
    ref = (ref or "").strip()
    return ref.lower().rstrip(".") if kind == "domain" else ref


def get_ref(db: Session, tenant_id: uuid.UUID, kind: str, ref: str) -> ResourceRef | None:
    return db.scalar(select(ResourceRef).where(ResourceRef.tenant_id == tenant_id, ResourceRef.kind == kind, ResourceRef.ref == norm_ref(kind, ref)))


def owns_resource(db: Session, tenant_id: uuid.UUID | None, kind: str, ref: str) -> bool:
    if tenant_id is None or kind not in KINDS or not ref:
        return False
    ref = norm_ref(kind, ref)
    for fn in _resolvers.get(kind, []):
        if fn(db, tenant_id, ref):
            return True
    return get_ref(db, tenant_id, kind, ref) is not None


def domain_owner_for(db: Session, tenant_id: uuid.UUID, hostname: str) -> str | None:
    """The registered domain (zone apex) of the tenant that covers `hostname`, e.g. www.example.com -> example.com."""
    hostname = norm_ref("domain", hostname)
    labels = hostname.split(".")
    for i in range(len(labels) - 1):
        cand = ".".join(labels[i:])
        if owns_resource(db, tenant_id, "domain", cand):
            return cand
    return None


def require_owned(db: Session, tenant_id: uuid.UUID, kind: str, ref: str) -> None:
    if not owns_resource(db, tenant_id, kind, ref):
        raise api_error(404, "not_found", f"{kind.capitalize()} not found.")


def require_ref(db: Session, tenant_id: uuid.UUID, kind: str, ref: str) -> ResourceRef:
    """Ownership + the agent paths registered for the resource."""
    require_owned(db, tenant_id, kind, ref)
    row = get_ref(db, tenant_id, kind, ref)
    if row is None:
        raise api_error(409, "not_configured", f"{kind.capitalize()} has no server location registered yet.")
    return row


def visible_tenant_ids_filter(ctx: Ctx, requested: uuid.UUID | None):
    """Return (tenant_id or None). Platform staff without tenant_id see everything (None)."""
    from app.core.deps import resolve_tenant

    return resolve_tenant(ctx, requested)
