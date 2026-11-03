import uuid
from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.db import get_db
from app.core.deps import Ctx, api_error, client_ip, require
from app.models.identity import Membership, Role, User
from app.models.tenant import TENANT_STATUSES, Tenant, TenantActivity, TenantQuota
from app.services.audit import audit
from app.services.tenants import log_activity, provision_tenant, transition, visible_tenant

router = APIRouter(prefix="/tenants", tags=["tenants"])


def dto(t: Tenant) -> dict[str, Any]:
    return {
        "id": str(t.id), "name": t.name, "slug": t.slug, "status": t.status, "contact_email": t.contact_email,
        "plan_code": t.plan_code, "reseller_id": str(t.reseller_id) if t.reseller_id else None,
        "owner_user_id": str(t.owner_user_id) if t.owner_user_id else None, "settings": t.settings or {},
        "trial_ends_at": t.trial_ends_at, "grace_ends_at": t.grace_ends_at, "suspended_at": t.suspended_at,
        "cancelled_at": t.cancelled_at, "retention_until": t.retention_until, "created_at": t.created_at,
    }


class TenantIn(BaseModel):
    name: str = Field(min_length=2, max_length=200)
    contact_email: EmailStr | None = None
    plan_code: str | None = Field(default=None, max_length=60)
    status: str = Field(default="trial", pattern="^(pending|trial|active)$")
    owner_user_id: uuid.UUID | None = None


class TenantPatch(BaseModel):
    name: str | None = Field(default=None, min_length=2, max_length=200)
    contact_email: EmailStr | None = None
    plan_code: str | None = Field(default=None, max_length=60)
    settings: dict[str, Any] | None = None


class StatusIn(BaseModel):
    status: str
    reason: str | None = Field(default=None, max_length=300)


class QuotaIn(BaseModel):
    resource: str = Field(pattern="^(websites|domains|databases|apps|disk_mb|bandwidth_gb)$")
    limit_value: int = Field(ge=0, le=10_000_000)


@router.get("")
def list_tenants(status: str | None = None, q: str | None = None, limit: int = 25, offset: int = 0, ctx: Ctx = Depends(require("tenants:read")), db: Session = Depends(get_db)):
    stmt = select(Tenant)
    if ctx.scope == "tenant":
        stmt = stmt.where(Tenant.id == ctx.tenant_id)
    elif ctx.scope == "reseller":
        stmt = stmt.where(Tenant.reseller_id == ctx.reseller_id) if ctx.reseller_id else stmt.where(False)
    if status:
        if status not in TENANT_STATUSES:
            raise api_error(422, "validation_error", "Unknown status.")
        stmt = stmt.where(Tenant.status == status)
    if q:
        stmt = stmt.where(Tenant.name.ilike(f"%{q}%") | Tenant.slug.ilike(f"%{q.lower()}%"))
    total = db.scalar(select(func.count()).select_from(stmt.subquery()))
    rows = db.scalars(stmt.order_by(Tenant.created_at.desc()).limit(min(limit, 100)).offset(offset)).all()
    return {"total": total, "items": [dto(t) for t in rows]}


@router.post("", status_code=201)
def create_tenant(body: TenantIn, request: Request, ctx: Ctx = Depends(require("tenants:write")), db: Session = Depends(get_db)):
    if not ctx.is_platform:
        raise api_error(403, "forbidden", "Platform staff only.")
    if body.owner_user_id and not db.get(User, body.owner_user_id):
        raise api_error(404, "not_found", "Owner user not found.")
    t = provision_tenant(db, body.name, body.owner_user_id, body.contact_email, status=body.status, plan_code=body.plan_code, actor=ctx.user.id)
    audit(db, "tenant.created", actor=ctx.user.id, tenant_id=t.id, target_type="tenant", target_id=t.id, ip=client_ip(request))
    db.commit()
    return dto(t)


@router.get("/current")
def current(ctx: Ctx = Depends(require("members:read")), db: Session = Depends(get_db)):
    if not ctx.tenant_id:
        raise api_error(404, "not_found", "No tenant context.")
    return dto(visible_tenant(db, ctx, ctx.tenant_id))


@router.get("/{tenant_id}")
def get_tenant(tenant_id: uuid.UUID, ctx: Ctx = Depends(require("tenants:read")), db: Session = Depends(get_db)):
    return dto(visible_tenant(db, ctx, tenant_id))


@router.patch("/{tenant_id}")
def update_tenant(tenant_id: uuid.UUID, body: TenantPatch, request: Request, ctx: Ctx = Depends(require("tenants:write")), db: Session = Depends(get_db)):
    t = visible_tenant(db, ctx, tenant_id)
    if body.name is not None:
        t.name = body.name.strip()
    if body.contact_email is not None:
        t.contact_email = body.contact_email
    if body.settings is not None:
        t.settings = {**(t.settings or {}), **body.settings}
    if body.plan_code is not None:
        if not ctx.is_platform:
            raise api_error(403, "forbidden", "Only platform staff can change the plan here.")
        t.plan_code = body.plan_code
    log_activity(db, t.id, ctx.user.id, "tenant.updated", "Tenant settings updated")
    audit(db, "tenant.updated", actor=ctx.user.id, tenant_id=t.id, target_type="tenant", target_id=t.id, ip=client_ip(request))
    db.commit()
    return dto(t)


@router.post("/{tenant_id}/status")
def change_status(tenant_id: uuid.UUID, body: StatusIn, request: Request, ctx: Ctx = Depends(require("tenants:manage")), db: Session = Depends(get_db)):
    if not ctx.is_platform:
        raise api_error(403, "forbidden", "Platform staff only.")
    t = visible_tenant(db, ctx, tenant_id)
    transition(db, t, body.status, ctx.user.id, body.reason)
    audit(db, f"tenant.{body.status}", actor=ctx.user.id, tenant_id=t.id, target_type="tenant", target_id=t.id, detail={"reason": body.reason}, ip=client_ip(request))
    db.commit()
    return dto(t)


@router.get("/{tenant_id}/quotas")
def quotas(tenant_id: uuid.UUID, ctx: Ctx = Depends(require("tenants:read")), db: Session = Depends(get_db)):
    t = visible_tenant(db, ctx, tenant_id)
    rows = db.scalars(select(TenantQuota).where(TenantQuota.tenant_id == t.id).order_by(TenantQuota.resource)).all()
    return [{"resource": q.resource, "limit": q.limit_value, "used": q.used_value} for q in rows]


@router.put("/{tenant_id}/quotas")
def set_quota(tenant_id: uuid.UUID, body: QuotaIn, request: Request, ctx: Ctx = Depends(require("tenants:manage")), db: Session = Depends(get_db)):
    if not ctx.is_platform:
        raise api_error(403, "forbidden", "Platform staff only.")
    t = visible_tenant(db, ctx, tenant_id)
    q = db.scalar(select(TenantQuota).where(TenantQuota.tenant_id == t.id, TenantQuota.resource == body.resource))
    if q:
        q.limit_value = body.limit_value
    else:
        db.add(TenantQuota(tenant_id=t.id, resource=body.resource, limit_value=body.limit_value))
    log_activity(db, t.id, ctx.user.id, "tenant.quota", f"{body.resource} limit set to {body.limit_value}")
    audit(db, "tenant.quota_changed", actor=ctx.user.id, tenant_id=t.id, detail=body.model_dump(), ip=client_ip(request))
    db.commit()
    return {"status": "ok"}


@router.get("/{tenant_id}/activity")
def activity(tenant_id: uuid.UUID, limit: int = 50, ctx: Ctx = Depends(require("tenants:read")), db: Session = Depends(get_db)):
    t = visible_tenant(db, ctx, tenant_id)
    rows = db.scalars(select(TenantActivity).where(TenantActivity.tenant_id == t.id).order_by(TenantActivity.created_at.desc()).limit(min(limit, 200))).all()
    return [{"id": str(a.id), "action": a.action, "summary": a.summary, "created_at": a.created_at} for a in rows]


@router.get("/{tenant_id}/members")
def members(tenant_id: uuid.UUID, ctx: Ctx = Depends(require("members:read")), db: Session = Depends(get_db)):
    t = visible_tenant(db, ctx, tenant_id)
    rows = db.execute(
        select(Membership, User, Role).join(User, User.id == Membership.user_id).join(Role, Role.id == Membership.role_id).where(Membership.tenant_id == t.id)
    ).all()
    return [{"membership_id": str(m.id), "user_id": str(u.id), "email": u.email, "name": u.name, "role": r.name, "status": m.status} for m, u, r in rows]


@router.post("/{tenant_id}/export")
def export_metadata(tenant_id: uuid.UUID, request: Request, ctx: Ctx = Depends(require("tenants:manage")), db: Session = Depends(get_db)):
    """Metadata export for off-boarding. Platform-only, audited. Customer file/database exports are separate owner-run jobs."""
    if not ctx.is_platform:
        raise api_error(403, "forbidden", "Platform staff only.")
    t = visible_tenant(db, ctx, tenant_id)
    audit(db, "tenant.exported", actor=ctx.user.id, tenant_id=t.id, target_type="tenant", target_id=t.id, ip=client_ip(request))
    db.commit()
    return {
        "exported_at": datetime.now().astimezone(),
        "tenant": dto(t),
        "quotas": quotas(tenant_id, ctx, db),
        "members": members(tenant_id, ctx, db),
        "activity": activity(tenant_id, 500, ctx, db),
    }
