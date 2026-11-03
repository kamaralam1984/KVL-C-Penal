import re
import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.deps import api_error
from app.models.identity import Membership, Role
from app.models.tenant import PlatformSetting, Tenant, TenantActivity, TenantQuota

RESERVED_SLUGS = {"admin", "api", "www", "root", "support", "billing", "mail", "app", "panel", "system"}


def setting(db: Session, key: str, default: dict | None = None) -> dict:
    row = db.scalar(select(PlatformSetting).where(PlatformSetting.key == key))
    return (row.value if row and row.value else default) or {}


def slugify(name: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")[:40] or "tenant"
    return s if s not in RESERVED_SLUGS else f"{s}-co"


def unique_slug(db: Session, name: str) -> str:
    base = slugify(name)
    slug, i = base, 1
    while db.scalar(select(Tenant.id).where(Tenant.slug == slug)):
        i += 1
        slug = f"{base}-{i}"
    return slug


def log_activity(db: Session, tenant_id: uuid.UUID, actor: uuid.UUID | None, action: str, summary: str) -> None:
    db.add(TenantActivity(tenant_id=tenant_id, actor_user_id=actor, action=action, summary=summary))


def provision_tenant(
    db: Session,
    name: str,
    owner_id: uuid.UUID | None,
    contact_email: str | None,
    *,
    status: str = "trial",
    plan_code: str | None = None,
    reseller_id: uuid.UUID | None = None,
    actor: uuid.UUID | None = None,
) -> Tenant:
    now = datetime.now(UTC)
    t = Tenant(
        name=name.strip(),
        slug=unique_slug(db, name),
        status=status,
        contact_email=contact_email,
        owner_user_id=owner_id,
        reseller_id=reseller_id,
        plan_code=plan_code,
        settings={"timezone": "Asia/Kolkata", "locale": "en-IN"},
        trial_ends_at=now + timedelta(days=int(setting(db, "trial_days", {"days": 14}).get("days", 14))) if status == "trial" else None,
    )
    db.add(t)
    db.flush()
    for resource, limit in setting(db, "default_quotas", {}).items():
        db.add(TenantQuota(tenant_id=t.id, resource=resource, limit_value=int(limit), used_value=0))
    if owner_id:
        role = db.scalar(select(Role).where(Role.name == "customer"))
        db.add(Membership(user_id=owner_id, role_id=role.id, tenant_id=t.id, reseller_id=reseller_id and None))
    log_activity(db, t.id, actor, "tenant.created", f"Tenant created in status {status}")
    return t


ALLOWED_TRANSITIONS = {
    "pending": {"trial", "active", "cancelled"},
    "trial": {"active", "suspended", "cancelled"},
    "active": {"suspended", "cancelled"},
    "suspended": {"active", "cancelled"},
    "cancelled": set(),  # terminal; data retained until retention_until, then export/deletion is an owner-run job
}


def transition(db: Session, t: Tenant, new_status: str, actor: uuid.UUID | None, reason: str | None = None) -> Tenant:
    if new_status not in ALLOWED_TRANSITIONS.get(t.status, set()):
        raise api_error(409, "invalid_transition", f"Cannot move tenant from {t.status} to {new_status}.")
    now = datetime.now(UTC)
    old = t.status
    t.status = new_status
    if new_status == "suspended":
        t.suspended_at = now
        t.grace_ends_at = None
    elif new_status == "active":
        t.suspended_at = None
        t.grace_ends_at = None
    elif new_status == "cancelled":
        t.cancelled_at = now
        t.retention_until = now + timedelta(days=int(setting(db, "retention_days", {"days": 30}).get("days", 30)))
    log_activity(db, t.id, actor, f"tenant.{new_status}", f"{old} -> {new_status}" + (f": {reason}" if reason else ""))
    return t


def visible_tenant(db: Session, ctx, tenant_id: uuid.UUID) -> Tenant:
    """Fetch a tenant the caller may see; anything else is reported as not found (no enumeration)."""
    t = db.get(Tenant, tenant_id)
    if t is None:
        raise api_error(404, "not_found", "Tenant not found.")
    if ctx.is_platform:
        return t
    if ctx.scope == "reseller" and ctx.reseller_id and t.reseller_id == ctx.reseller_id:
        return t
    if ctx.scope == "tenant" and ctx.tenant_id == t.id:
        return t
    raise api_error(404, "not_found", "Tenant not found.")


def consume_quota(db: Session, tenant_id: uuid.UUID, resource: str, delta: int = 1) -> None:
    """Atomically reserve quota; raises 409 when the limit would be exceeded. Missing quota row = no limit."""
    q = db.scalar(select(TenantQuota).where(TenantQuota.tenant_id == tenant_id, TenantQuota.resource == resource).with_for_update())
    if q is None:
        return
    if delta > 0 and q.used_value + delta > q.limit_value:
        raise api_error(409, "quota_exceeded", f"Your {resource.replace('_', ' ')} quota ({q.limit_value}) has been reached.")
    q.used_value = max(0, q.used_value + delta)
