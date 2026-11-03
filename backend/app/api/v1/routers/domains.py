import uuid
from datetime import datetime

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.db import get_db
from app.core.deps import Ctx, api_error, client_ip, require
from app.models.hosting import Domain, ResourceEvent, Website
from app.services import dnsverify
from app.services.access import get_visible, scope_stmt, tenant_for_create
from app.services.audit import audit
from app.services.domains import add_domain, domain_dto, verify_domain
from app.services.resources import log_event
from app.services.tenants import consume_quota

router = APIRouter(prefix="/domains", tags=["domains"])


class DomainIn(BaseModel):
    hostname: str = Field(min_length=3, max_length=600)
    kind: str = Field(default="primary", pattern="^(primary|alias|subdomain)$")
    website_id: uuid.UUID | None = None
    tenant_id: uuid.UUID | None = None


class DomainPatch(BaseModel):
    website_id: uuid.UUID | None = None
    unmap: bool = False
    kind: str | None = Field(default=None, pattern="^(primary|alias|subdomain)$")
    expires_at: datetime | None = None
    registrar: str | None = Field(default=None, max_length=120)


def _website_for(db: Session, ctx: Ctx, wid: uuid.UUID, tenant_id: uuid.UUID) -> Website:
    w = get_visible(db, ctx, Website, wid, "Website")
    if w.tenant_id != tenant_id:
        raise api_error(404, "not_found", "Website not found.")
    if w.status == "archived":
        raise api_error(409, "website_archived", "Cannot map a domain to an archived website.")
    return w


@router.get("/resolver")
def resolver_status(ctx: Ctx = Depends(require("domains:read"))):
    r = dnsverify.resolver_factory()
    ok = not isinstance(r, dnsverify.UnavailableResolver)
    return {"available": ok, "message": None if ok else "DNS verification is unavailable: dnspython is not installed on the API server."}


@router.get("")
def list_domains(website_id: uuid.UUID | None = None, state: str | None = None, q: str | None = None, tenant_id: uuid.UUID | None = None, limit: int = 25, offset: int = 0, ctx: Ctx = Depends(require("domains:read")), db: Session = Depends(get_db)):
    stmt = scope_stmt(select(Domain), Domain, ctx, tenant_id)
    if website_id:
        stmt = stmt.where(Domain.website_id == website_id)
    if state:
        stmt = stmt.where(Domain.verification_state == state)
    if q:
        stmt = stmt.where(Domain.hostname.ilike(f"%{q.lower()}%"))
    total = db.scalar(select(func.count()).select_from(stmt.subquery()))
    rows = db.scalars(stmt.order_by(Domain.created_at.desc()).limit(min(limit, 100)).offset(offset))
    return {"total": total, "items": [domain_dto(d) for d in rows]}


@router.post("", status_code=201)
def create_domain(body: DomainIn, request: Request, ctx: Ctx = Depends(require("domains:write")), db: Session = Depends(get_db)):
    tid = tenant_for_create(ctx, body.tenant_id)
    wid = _website_for(db, ctx, body.website_id, tid).id if body.website_id else None
    consume_quota(db, tid, "domains")
    d = add_domain(db, tid, body.hostname, kind=body.kind, website_id=wid, actor=ctx.user.id)
    audit(db, "domain.added", actor=ctx.user.id, tenant_id=tid, target_type="domain", target_id=d.id, detail={"hostname": d.hostname}, ip=client_ip(request))
    db.commit()
    return domain_dto(d)


@router.get("/{domain_id}")
def get_domain(domain_id: uuid.UUID, ctx: Ctx = Depends(require("domains:read")), db: Session = Depends(get_db)):
    return domain_dto(get_visible(db, ctx, Domain, domain_id, "Domain"))


@router.post("/{domain_id}/verify")
def verify(domain_id: uuid.UUID, request: Request, ctx: Ctx = Depends(require("domains:write")), db: Session = Depends(get_db)):
    d = get_visible(db, ctx, Domain, domain_id, "Domain")
    verify_domain(db, d, ctx.user.id)
    audit(db, "domain.verify_attempt", actor=ctx.user.id, tenant_id=d.tenant_id, target_type="domain", target_id=d.id, detail={"state": d.verification_state}, ip=client_ip(request))
    db.commit()
    return domain_dto(d)


@router.patch("/{domain_id}")
def update_domain(domain_id: uuid.UUID, body: DomainPatch, request: Request, ctx: Ctx = Depends(require("domains:write")), db: Session = Depends(get_db)):
    d = get_visible(db, ctx, Domain, domain_id, "Domain")
    if body.website_id:
        d.website_id = _website_for(db, ctx, body.website_id, d.tenant_id).id
        log_event(db, d.tenant_id, "domain", d.id, ctx.user.id, "domain.mapped", f"Mapped {d.hostname} to a website")
    elif body.unmap:
        d.website_id = None
        log_event(db, d.tenant_id, "domain", d.id, ctx.user.id, "domain.unmapped", f"Unmapped {d.hostname}")
    if body.kind:
        d.kind = body.kind
    if body.expires_at is not None:
        d.expires_at = body.expires_at
    if body.registrar is not None:
        d.registrar = body.registrar
    audit(db, "domain.updated", actor=ctx.user.id, tenant_id=d.tenant_id, target_type="domain", target_id=d.id, ip=client_ip(request))
    db.commit()
    return domain_dto(d)


@router.delete("/{domain_id}", status_code=204)
def delete_domain(domain_id: uuid.UUID, request: Request, ctx: Ctx = Depends(require("domains:write")), db: Session = Depends(get_db)):
    d = get_visible(db, ctx, Domain, domain_id, "Domain")
    if d.website_id:
        w = db.get(Website, d.website_id)
        if w and w.provision_state in {"installed", "active", "reloading", "rolled_back"}:
            raise api_error(409, "domain_in_use", "Unmap this domain and re-provision the website before removing it.")
    audit(db, "domain.removed", actor=ctx.user.id, tenant_id=d.tenant_id, target_type="domain", target_id=d.id, detail={"hostname": d.hostname}, ip=client_ip(request))
    consume_quota(db, d.tenant_id, "domains", -1)
    db.delete(d)
    db.commit()


@router.get("/{domain_id}/activity")
def activity(domain_id: uuid.UUID, ctx: Ctx = Depends(require("domains:read")), db: Session = Depends(get_db)):
    d = get_visible(db, ctx, Domain, domain_id, "Domain")
    rows = db.scalars(select(ResourceEvent).where(ResourceEvent.resource_type == "domain", ResourceEvent.resource_id == d.id).order_by(ResourceEvent.created_at.desc()).limit(50))
    return [{"action": a.action, "summary": a.summary, "created_at": a.created_at} for a in rows]
