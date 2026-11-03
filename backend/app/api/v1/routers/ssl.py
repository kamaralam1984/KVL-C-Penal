import uuid
from typing import Any

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.db import get_db
from app.core.deps import Ctx, api_error, client_ip, require, require_tenant, resolve_tenant
from app.models.identity import AuditEvent
from app.models.ssl import Certificate
from app.models.ownership import ResourceRef
from app.services import ssl as svc
from app.services.audit import audit

router = APIRouter(prefix="/ssl", tags=["ssl"])


def _get(db: Session, ctx: Ctx, cid: uuid.UUID) -> Certificate:
    c = db.get(Certificate, cid)
    if c is None or (not ctx.is_platform and c.tenant_id != ctx.tenant_id):
        raise api_error(404, "not_found", "Certificate not found.")
    return c


class CertIn(BaseModel):
    domain: str = Field(min_length=1, max_length=253)
    production: bool = False  # staging by default; production is explicit
    tenant_id: uuid.UUID | None = None


class CertPatch(BaseModel):
    auto_renew: bool


@router.get("/overview")
def overview(tenant_id: uuid.UUID | None = None, ctx: Ctx = Depends(require("ssl:read")), db: Session = Depends(get_db)):
    data = svc.overview(db, resolve_tenant(ctx, tenant_id))
    db.commit()
    return data


@router.get("/domains")
def domains(tenant_id: uuid.UUID | None = None, ctx: Ctx = Depends(require("ssl:read")), db: Session = Depends(get_db)):
    """Registered domains with their best certificate status (read-only view for customers)."""
    tid = resolve_tenant(ctx, tenant_id)
    stmt = select(ResourceRef).where(ResourceRef.kind == "domain")
    if tid:
        stmt = stmt.where(ResourceRef.tenant_id == tid)
    out = []
    for r in db.scalars(stmt.order_by(ResourceRef.ref).limit(500)):
        certs = db.scalars(select(Certificate).where(Certificate.tenant_id == r.tenant_id, Certificate.domain == r.ref)).all()
        for c in certs:
            svc.sync_certificate(db, c)
        views = sorted((svc.cert_view(c) for c in certs), key=lambda v: (v["trusted"], v["not_after"] or 0), reverse=True)
        out.append({"domain": r.ref, "tenant_id": str(r.tenant_id), "certificate": views[0] if views else None})
    db.commit()
    return {"items": out}


@router.get("/certificates")
def list_certs(domain: str | None = None, tenant_id: uuid.UUID | None = None, ctx: Ctx = Depends(require("ssl:read")), db: Session = Depends(get_db)):
    tid = resolve_tenant(ctx, tenant_id)
    stmt = select(Certificate)
    if tid:
        stmt = stmt.where(Certificate.tenant_id == tid)
    if domain:
        stmt = stmt.where(Certificate.domain == domain.lower())
    rows = db.scalars(stmt.order_by(Certificate.domain).limit(500)).all()
    for c in rows:
        svc.sync_certificate(db, c)
    db.commit()
    return {"items": [svc.cert_view(c) for c in rows]}


@router.get("/certificates/{cid}")
def get_cert(cid: uuid.UUID, ctx: Ctx = Depends(require("ssl:read")), db: Session = Depends(get_db)):
    c = _get(db, ctx, cid)
    svc.sync_certificate(db, c)
    db.commit()
    events = db.scalars(select(AuditEvent).where(AuditEvent.target_type == "certificate", AuditEvent.target_id == str(c.id)).order_by(AuditEvent.created_at.desc()).limit(50)).all()
    return {**svc.cert_view(c), "history": [{"action": e.action, "at": e.created_at, "detail": e.detail} for e in events]}


@router.get("/prerequisites")
def prerequisites(domain: str, tenant_id: uuid.UUID | None = None, ctx: Ctx = Depends(require("ssl:read")), db: Session = Depends(get_db)):
    tid = require_tenant(ctx, tenant_id)
    return svc.public_prereq(svc.check_prerequisites(db, tid, svc.normalize_domain(domain)))


@router.post("/certificates", status_code=202)
def request_cert(body: CertIn, request: Request, ctx: Ctx = Depends(require("ssl:write")), db: Session = Depends(get_db)):
    tid = require_tenant(ctx, body.tenant_id)
    c = svc.request_certificate(db, actor=ctx.user.id, tenant_id=tid, domain=body.domain, production=body.production, can_manage=ctx.can("ssl:manage"))
    db.commit()
    return svc.cert_view(c)


@router.post("/certificates/{cid}/renew", status_code=202)
def renew_cert(cid: uuid.UUID, ctx: Ctx = Depends(require("ssl:write")), db: Session = Depends(get_db)):
    c = _get(db, ctx, cid)
    if c.environment == "production" and not ctx.can("ssl:manage"):
        raise api_error(403, "forbidden", "Renewing production certificates requires ssl:manage permission.")
    svc.renew_certificate(db, c, ctx.user.id)
    db.commit()
    return svc.cert_view(c)


@router.patch("/certificates/{cid}")
def patch_cert(cid: uuid.UUID, body: CertPatch, request: Request, ctx: Ctx = Depends(require("ssl:write")), db: Session = Depends(get_db)):
    c = _get(db, ctx, cid)
    c.auto_renew = body.auto_renew
    audit(db, "ssl.auto_renew_changed", actor=ctx.user.id, tenant_id=c.tenant_id, target_type="certificate", target_id=c.id, detail={"domain": c.domain, "auto_renew": body.auto_renew}, ip=client_ip(request))
    db.commit()
    return svc.cert_view(c)


@router.post("/sweep")
def sweep(ctx: Ctx = Depends(require("ssl:manage")), db: Session = Depends(get_db)) -> dict[str, Any]:
    """Run the renewal sweep now (the same function a periodic task calls). Platform staff only."""
    if not ctx.is_platform:
        raise api_error(403, "forbidden", "Platform staff only.")
    out = svc.sweep_renewals(db)
    audit(db, "ssl.sweep_run", actor=ctx.user.id, detail={k: (len(v) if isinstance(v, list) else v) for k, v in out.items()})
    db.commit()
    return out
