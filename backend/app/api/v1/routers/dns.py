import uuid
from typing import Any, Literal

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.db import get_db
from app.core.deps import Ctx, api_error, client_ip, require, require_tenant, resolve_tenant
from app.models.identity import AuditEvent
from app.models.ssl import DnsChange, DnsRecord
from app.services import dns as svc
from app.services.audit import audit
from app.services.ownership import domain_owner_for, owns_resource

router = APIRouter(prefix="/dns", tags=["dns"])


class RecordIn(BaseModel):
    type: str = Field(min_length=1, max_length=10)
    name: str = Field(max_length=253)
    content: str = Field(min_length=1, max_length=2100)
    ttl: int = 1
    priority: int | None = None
    proxied: bool | None = None


class ChangeIn(BaseModel):
    action: Literal["create", "update", "delete"]
    record_id: uuid.UUID | None = None
    record: RecordIn | None = None
    protected_ack: bool = False
    tenant_id: uuid.UUID | None = None


class ConfirmIn(BaseModel):
    confirm: bool = False
    confirm_production: bool = False


def _domain(db: Session, ctx: Ctx, domain: str, tenant_id: uuid.UUID | None) -> tuple[uuid.UUID, str]:
    tid = require_tenant(ctx, tenant_id)
    d = domain.strip().lower().rstrip(".")
    if not owns_resource(db, tid, "domain", d):
        raise api_error(404, "not_found", "Domain not found.")
    return tid, d


def _change(db: Session, ctx: Ctx, cid: uuid.UUID) -> DnsChange:
    ch = db.get(DnsChange, cid)
    if ch is None or (not ctx.is_platform and ch.tenant_id != ctx.tenant_id):
        raise api_error(404, "not_found", "Change not found.")
    return ch


# ---- change requests addressed by id (declared first so they never shadow {domain} routes)
@router.get("/changes/{cid}")
def get_change(cid: uuid.UUID, ctx: Ctx = Depends(require("dns:read")), db: Session = Depends(get_db)):
    return svc.change_view(_change(db, ctx, cid), db)


@router.post("/changes/{cid}/confirm")
def confirm(cid: uuid.UUID, body: ConfirmIn, request: Request, ctx: Ctx = Depends(require("dns:write")), db: Session = Depends(get_db)):
    ch = _change(db, ctx, cid)
    if ch.production and not ctx.can("dns:manage"):
        raise api_error(403, "owner_required", "Production DNS changes require owner-level permission (dns:manage).")
    if not body.confirm:
        raise api_error(400, "confirmation_required", "Explicit confirmation is required to apply a DNS change.")
    if ch.production and not body.confirm_production:
        raise api_error(400, "production_confirmation_required", "Set confirm_production=true to change live DNS.")
    audit(db, "dns.change_confirmed", actor=ctx.user.id, tenant_id=ch.tenant_id, target_type="dns_change", target_id=ch.id, detail={"domain": ch.domain, "action": ch.action, "confirm_production": body.confirm_production}, ip=client_ip(request))
    try:
        svc.apply_change(db, ch, ctx.user.id)
    except Exception:
        db.commit()  # keep audit + failed state
        raise
    db.commit()
    return svc.change_view(ch, db)


@router.post("/changes/{cid}/verify")
def verify(cid: uuid.UUID, ctx: Ctx = Depends(require("dns:write")), db: Session = Depends(get_db)):
    ch = _change(db, ctx, cid)
    svc.verify_change(db, ch, ctx.user.id)
    db.commit()
    return svc.change_view(ch, db)


@router.post("/changes/{cid}/rollback", status_code=201)
def rollback(cid: uuid.UUID, ctx: Ctx = Depends(require("dns:write")), db: Session = Depends(get_db)):
    inv = svc.rollback_change(db, _change(db, ctx, cid), ctx.user.id)
    db.commit()
    return svc.change_view(inv, db)


@router.post("/changes/{cid}/reject")
def reject(cid: uuid.UUID, ctx: Ctx = Depends(require("dns:write")), db: Session = Depends(get_db)):
    ch = _change(db, ctx, cid)
    if ch.status != "previewed":
        raise api_error(409, "invalid_state", "Only previewed changes can be discarded.")
    ch.status = "rejected"
    audit(db, "dns.change_rejected", actor=ctx.user.id, tenant_id=ch.tenant_id, target_type="dns_change", target_id=ch.id, detail={"domain": ch.domain})
    db.commit()
    return svc.change_view(ch, db)


@router.get("/audit")
def audit_history(domain: str | None = None, tenant_id: uuid.UUID | None = None, limit: int = 50, ctx: Ctx = Depends(require("dns:read")), db: Session = Depends(get_db)):
    tid = resolve_tenant(ctx, tenant_id)
    stmt = select(AuditEvent).where(AuditEvent.action.like("dns.%") | AuditEvent.action.like("cloudflare.%") | AuditEvent.action.like("ssl.%"))
    if tid:
        stmt = stmt.where(AuditEvent.tenant_id == tid)
    if domain:
        stmt = stmt.where(AuditEvent.detail["domain"].astext == domain.lower())
    rows = db.scalars(stmt.order_by(AuditEvent.created_at.desc()).limit(min(limit, 200))).all()
    return {"items": [{"id": str(e.id), "action": e.action, "at": e.created_at, "actor_user_id": str(e.actor_user_id) if e.actor_user_id else None, "detail": e.detail} for e in rows]}


# ---- per-domain
@router.get("/{domain}/records")
def records(domain: str, tenant_id: uuid.UUID | None = None, ctx: Ctx = Depends(require("dns:read")), db: Session = Depends(get_db)):
    tid, d = _domain(db, ctx, domain, tenant_id)
    conn = svc.get_connection(db, tid)
    zone = svc.zone_for(conn, d)
    rows = db.scalars(select(DnsRecord).where(DnsRecord.tenant_id == tid, DnsRecord.domain == d).order_by(DnsRecord.rtype, DnsRecord.name)).all()
    state = "not_configured" if conn is None else ("zone_not_authorised" if zone is None else conn.status)
    return {"domain": d, "provider": "cloudflare", "status": state, "zone": zone, "records": [svc.row_view(r) for r in rows], "synced_at": max((r.synced_at for r in rows if r.synced_at), default=None)}


@router.post("/{domain}/sync")
def sync(domain: str, tenant_id: uuid.UUID | None = None, ctx: Ctx = Depends(require("dns:write")), db: Session = Depends(get_db)):
    tid, d = _domain(db, ctx, domain, tenant_id)
    try:
        rows = svc.sync_zone(db, tid, d, ctx.user.id)
    except Exception:
        db.commit()
        raise
    audit(db, "dns.synced", actor=ctx.user.id, tenant_id=tid, target_type="dns", detail={"domain": d, "records": len(rows)})
    db.commit()
    return {"domain": d, "records": [svc.row_view(r) for r in rows]}


@router.post("/{domain}/changes", status_code=201)
def create_change(domain: str, body: ChangeIn, request: Request, tenant_id: uuid.UUID | None = None, ctx: Ctx = Depends(require("dns:write")), db: Session = Depends(get_db)):
    """Create a PREVIEW (before/after diff, warnings, rollback instructions). Nothing is sent to the DNS provider."""
    tid, d = _domain(db, ctx, domain, body.tenant_id or tenant_id)
    ch = svc.build_preview(db, actor=ctx.user.id, tenant_id=tid, domain=d, action=body.action, record_id=body.record_id, record=body.record.model_dump() if body.record else None, protected_ack=body.protected_ack)
    db.commit()
    return svc.change_view(ch, db)


@router.get("/{domain}/changes")
def list_changes(domain: str, tenant_id: uuid.UUID | None = None, ctx: Ctx = Depends(require("dns:read")), db: Session = Depends(get_db)):
    tid, d = _domain(db, ctx, domain, tenant_id)
    rows = db.scalars(select(DnsChange).where(DnsChange.tenant_id == tid, DnsChange.domain == d).order_by(DnsChange.created_at.desc()).limit(100)).all()
    return {"items": [svc.change_view(c) for c in rows]}
