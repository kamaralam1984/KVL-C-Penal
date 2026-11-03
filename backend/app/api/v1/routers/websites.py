import uuid
from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.db import get_db
from app.core.deps import Ctx, api_error, client_ip, require
from app.models.hosting import Domain, ResourceEvent, Website
from app.models.infra import Server
from app.models.tenant import Tenant, TenantQuota
from app.services import monitoring
from app.services import websites as svc
from app.services.access import get_visible, scope_stmt, tenant_for_create
from app.services.audit import audit
from app.services.domains import add_domain, domain_dto
from app.services.resources import job_view, log_event
from app.services.tenants import consume_quota

router = APIRouter(prefix="/websites", tags=["websites"])
WEBSITE_STATUSES = ("active", "suspended", "archived")


def dto(db: Session, s: Website, detail: bool = False) -> dict[str, Any]:
    out = {
        "id": str(s.id), "tenant_id": str(s.tenant_id), "server_id": str(s.server_id) if s.server_id else None,
        "name": s.name, "slug": s.slug, "plan_code": s.plan_code, "runtime_type": s.runtime_type, "runtime_version": s.runtime_version,
        "document_root": s.document_root, "status": s.status, "provision_state": s.provision_state, "provision_message": s.provision_message,
        "config_hash": s.config_hash, "created_at": s.created_at, "archived_at": s.archived_at,
        "domains": [d.hostname for d in db.scalars(select(Domain).where(Domain.website_id == s.id).order_by(Domain.hostname))],
    }
    if detail:
        out["domain_details"] = [domain_dto(d) for d in db.scalars(select(Domain).where(Domain.website_id == s.id).order_by(Domain.hostname))]
        out["usage"] = svc.usage_summary(db, s)
        out["jobs"] = {k: job_view(db, getattr(s, f"{k}_job_id")) for k in ("stage", "activate", "reload", "rollback")}
    return out


class WebsiteIn(BaseModel):
    name: str = Field(min_length=2, max_length=120)
    runtime_type: str = Field(default="static", pattern="^(static|php)$")
    runtime_version: str | None = Field(default=None, pattern=r"^\d\.\d{1,2}$")
    public_subdir: str = Field(default="public", pattern=r"^[a-z0-9][a-z0-9_-]{0,31}$")
    domain: str | None = Field(default=None, max_length=300)
    tenant_id: uuid.UUID | None = None
    server_id: uuid.UUID | None = None
    plan_code: str | None = Field(default=None, max_length=60)


class WebsitePatch(BaseModel):
    name: str | None = Field(default=None, min_length=2, max_length=120)
    runtime_version: str | None = Field(default=None, pattern=r"^\d\.\d{1,2}$")
    server_id: uuid.UUID | None = None
    plan_code: str | None = Field(default=None, max_length=60)


class RollbackIn(BaseModel):
    backup: str | None = Field(default=None, pattern=r"^\d{8}T\d{6}Z(-\d{2})?\.conf$")


def _site(db: Session, ctx: Ctx, wid: uuid.UUID, sync: bool = True) -> Website:
    s = get_visible(db, ctx, Website, wid, "Website")
    if sync:
        svc.sync_website(db, s)
        db.commit()
    return s


def _tenant(db: Session, tid: uuid.UUID) -> Tenant:
    t = db.get(Tenant, tid)
    if t is None:
        raise api_error(404, "not_found", "Tenant not found.")
    return t


@router.get("")
def list_websites(status: str | None = None, q: str | None = None, tenant_id: uuid.UUID | None = None, limit: int = 25, offset: int = 0, ctx: Ctx = Depends(require("websites:read")), db: Session = Depends(get_db)):
    stmt = scope_stmt(select(Website), Website, ctx, tenant_id)
    if status:
        if status not in WEBSITE_STATUSES:
            raise api_error(422, "validation_error", "Unknown status.")
        stmt = stmt.where(Website.status == status)
    else:
        stmt = stmt.where(Website.status != "archived")
    if q:
        stmt = stmt.where(Website.name.ilike(f"%{q}%"))
    total = db.scalar(select(func.count()).select_from(stmt.subquery()))
    rows = list(db.scalars(stmt.order_by(Website.created_at.desc()).limit(min(limit, 100)).offset(offset)))
    svc.sync_all(db, rows)
    return {"total": total, "items": [dto(db, s) for s in rows]}


@router.post("", status_code=201)
def create_website(body: WebsiteIn, request: Request, ctx: Ctx = Depends(require("websites:write")), db: Session = Depends(get_db)):
    tid = tenant_for_create(ctx, body.tenant_id)
    tenant = _tenant(db, tid)
    if (body.server_id or body.plan_code) and not ctx.is_platform:
        raise api_error(403, "forbidden", "Only platform staff can choose the server or plan.")
    if body.runtime_type == "php" and not body.runtime_version:
        raise api_error(422, "runtime_version_required", "Select a PHP version.")
    server = db.get(Server, body.server_id) if body.server_id else None
    if body.server_id and (server is None or server.status == "disabled"):
        raise api_error(404, "not_found", "Server not found.")
    consume_quota(db, tid, "websites")
    s = Website(tenant_id=tid, name=body.name.strip(), slug=svc.unique_site_slug(db, tid, body.name), runtime_type=body.runtime_type, runtime_version=body.runtime_version if body.runtime_type == "php" else None,
                public_subdir=body.public_subdir, document_root="", plan_code=body.plan_code or tenant.plan_code, server_id=server.id if server else None)
    db.add(s)
    db.flush()
    s.document_root = svc.build_document_root(tenant, s)
    s.provision_message = None
    if server is None:
        server = svc.assign_server(db, s)
    note = svc.check_runtime(db, s.server_id, s.runtime_type, s.runtime_version)
    if server is None:
        s.provision_state, s.provision_message = "pending_server", "No server is registered yet. Provisioning is blocked until one exists; retry then."
    else:
        s.provision_state, s.provision_message = "awaiting_domain", note or "Add and verify a domain, then provision."
    log_event(db, tid, "website", s.id, ctx.user.id, "website.created", f"Website {s.name} created")
    if body.domain:
        add_domain(db, tid, body.domain, kind="primary", website_id=s.id, actor=ctx.user.id)
        consume_quota(db, tid, "domains")
    audit(db, "website.created", actor=ctx.user.id, tenant_id=tid, target_type="website", target_id=s.id, ip=client_ip(request))
    db.commit()
    return dto(db, s, True)


@router.get("/{website_id}")
def get_website(website_id: uuid.UUID, ctx: Ctx = Depends(require("websites:read")), db: Session = Depends(get_db)):
    return dto(db, _site(db, ctx, website_id), True)


@router.patch("/{website_id}")
def update_website(website_id: uuid.UUID, body: WebsitePatch, request: Request, ctx: Ctx = Depends(require("websites:write")), db: Session = Depends(get_db)):
    s = _site(db, ctx, website_id, sync=False)
    if s.status == "archived":
        raise api_error(409, "website_archived", "Archived websites cannot be changed.")
    if (body.server_id or body.plan_code) and not ctx.is_platform:
        raise api_error(403, "forbidden", "Only platform staff can change the server or plan.")
    if body.name:
        s.name = body.name.strip()
    if body.plan_code:
        s.plan_code = body.plan_code
    if body.server_id:
        srv = db.get(Server, body.server_id)
        if srv is None or srv.status == "disabled":
            raise api_error(404, "not_found", "Server not found.")
        if s.provision_state in svc.LIVE_STATES | svc.BUSY_STATES and s.server_id != srv.id:
            raise api_error(409, "server_locked", "Take the site offline before moving it to another server.")
        s.server_id = srv.id
        if s.provision_state == "pending_server":
            s.provision_state, s.provision_message = "awaiting_domain", "Server assigned. Provision when a verified domain is mapped."
    if body.runtime_version:
        if s.runtime_type != "php":
            raise api_error(422, "validation_error", "Only PHP websites have a runtime version.")
        svc.check_runtime(db, s.server_id, "php", body.runtime_version)
        s.runtime_version = body.runtime_version
        if s.provision_state in svc.LIVE_STATES | {"staged"}:
            s.provision_message = "Runtime changed: re-provision to apply."
    log_event(db, s.tenant_id, "website", s.id, ctx.user.id, "website.updated", "Website settings updated")
    audit(db, "website.updated", actor=ctx.user.id, tenant_id=s.tenant_id, target_type="website", target_id=s.id, ip=client_ip(request))
    db.commit()
    return dto(db, s, True)


def _act(db, ctx, request, website_id, perm_action: str, fn, *, sync=True):
    s = _site(db, ctx, website_id, sync=sync)
    tenant = _tenant(db, s.tenant_id)
    fn(s, tenant)
    audit(db, f"website.{perm_action}", actor=ctx.user.id, tenant_id=s.tenant_id, target_type="website", target_id=s.id, ip=client_ip(request))
    db.commit()
    return dto(db, s, True)


@router.post("/{website_id}/provision")
def provision(website_id: uuid.UUID, request: Request, ctx: Ctx = Depends(require("websites:write")), db: Session = Depends(get_db)):
    """Generate + validate (nginx -t) the proposed vhost. Does not touch live config. Also the retry action."""
    s = _site(db, ctx, website_id)
    tenant = _tenant(db, s.tenant_id)
    try:
        svc.start_stage(db, s, tenant, ctx.user.id)
    except Exception:
        db.commit()  # persist the pending_server/awaiting_domain state before surfacing the error
        raise
    audit(db, "website.provision", actor=ctx.user.id, tenant_id=s.tenant_id, target_type="website", target_id=s.id, ip=client_ip(request))
    db.commit()
    return dto(db, s, True)


@router.post("/{website_id}/activate")
def activate(website_id: uuid.UUID, request: Request, ctx: Ctx = Depends(require("websites:manage")), db: Session = Depends(get_db)):
    return _act(db, ctx, request, website_id, "activate", lambda s, t: svc.start_activate(db, s, ctx.user.id))


@router.post("/{website_id}/reload")
def reload_nginx(website_id: uuid.UUID, request: Request, ctx: Ctx = Depends(require("websites:manage")), db: Session = Depends(get_db)):
    return _act(db, ctx, request, website_id, "reload", lambda s, t: svc.start_reload(db, s, ctx.user.id))


@router.post("/{website_id}/rollback")
def rollback(website_id: uuid.UUID, body: RollbackIn, request: Request, ctx: Ctx = Depends(require("websites:manage")), db: Session = Depends(get_db)):
    return _act(db, ctx, request, website_id, "rollback", lambda s, t: svc.start_rollback(db, s, ctx.user.id, body.backup))


@router.post("/{website_id}/suspend")
def suspend(website_id: uuid.UUID, request: Request, ctx: Ctx = Depends(require("websites:manage")), db: Session = Depends(get_db)):
    def go(s: Website, t: Tenant):
        if s.status != "active":
            raise api_error(409, "invalid_transition", f"Cannot suspend a {s.status} website.")
        s.status = "suspended"
        log_event(db, s.tenant_id, "website", s.id, ctx.user.id, "website.suspended", "Website suspended")
        svc.start_takedown(db, s, ctx.user.id)

    return _act(db, ctx, request, website_id, "suspended", go)


@router.post("/{website_id}/reactivate")
def reactivate(website_id: uuid.UUID, request: Request, ctx: Ctx = Depends(require("websites:manage")), db: Session = Depends(get_db)):
    def go(s: Website, t: Tenant):
        if s.status != "suspended":
            raise api_error(409, "invalid_transition", f"Cannot reactivate a {s.status} website.")
        s.status = "active"
        if s.provision_state in {"removed", "removing"}:
            s.provision_message = "Reactivated. Provision again to bring it back online."
        log_event(db, s.tenant_id, "website", s.id, ctx.user.id, "website.reactivated", "Website reactivated")

    return _act(db, ctx, request, website_id, "reactivated", go)


@router.post("/{website_id}/archive")
def archive(website_id: uuid.UUID, request: Request, ctx: Ctx = Depends(require("websites:manage")), db: Session = Depends(get_db)):
    def go(s: Website, t: Tenant):
        if s.status == "archived":
            raise api_error(409, "invalid_transition", "Already archived.")
        svc.start_takedown(db, s, ctx.user.id)
        s.status, s.archived_at = "archived", datetime.now().astimezone()
        for d in db.scalars(select(Domain).where(Domain.website_id == s.id)):
            d.website_id = None
        consume_quota(db, s.tenant_id, "websites", -1)
        log_event(db, s.tenant_id, "website", s.id, ctx.user.id, "website.archived", "Website archived; domains unmapped")

    return _act(db, ctx, request, website_id, "archived", go, sync=False)


@router.get("/{website_id}/activity")
def activity(website_id: uuid.UUID, limit: int = 50, ctx: Ctx = Depends(require("websites:read")), db: Session = Depends(get_db)):
    s = _site(db, ctx, website_id, sync=False)
    rows = db.scalars(select(ResourceEvent).where(ResourceEvent.resource_type == "website", ResourceEvent.resource_id == s.id).order_by(ResourceEvent.created_at.desc()).limit(min(limit, 200)))
    return [{"id": str(a.id), "action": a.action, "summary": a.summary, "created_at": a.created_at} for a in rows]


@router.get("/{website_id}/usage")
def usage(website_id: uuid.UUID, ctx: Ctx = Depends(require("websites:read")), db: Session = Depends(get_db)):
    s = _site(db, ctx, website_id, sync=False)
    q = db.scalars(select(TenantQuota).where(TenantQuota.tenant_id == s.tenant_id, TenantQuota.resource.in_(["websites", "domains", "disk_mb"])))
    return {**svc.usage_summary(db, s), "quotas": [{"resource": x.resource, "limit": x.limit_value, "used": x.used_value} for x in q]}


def _agent_site(s: Website) -> str:
    return f"w-{s.id.hex[:16]}"


@router.get("/{website_id}/usage/live")
def usage_live(website_id: uuid.UUID, ctx: Ctx = Depends(require("websites:read")), db: Session = Depends(get_db)):
    """RAM, CPU, disk and data transferred for this website, as last reported by the monitoring agent."""
    s = _site(db, ctx, website_id, sync=False)
    if s.server_id is None:
        return {"reported": False, "reason": "not_assigned_to_a_server", "item": None}
    items = monitoring.site_usage_latest(db, s.server_id, only=_agent_site(s))["items"]
    return {"reported": bool(items), "reason": None if items else "no_report_from_agent_yet", "item": items[0] if items else None}


@router.get("/{website_id}/usage/history")
def usage_history(website_id: uuid.UUID, range: str = "24h", ctx: Ctx = Depends(require("websites:read")), db: Session = Depends(get_db)):
    s = _site(db, ctx, website_id, sync=False)
    if s.server_id is None:
        return {"site": _agent_site(s), "range": range, "bucket_seconds": monitoring.SITE_BUCKET, "points": []}
    return monitoring.site_usage_series(db, s.server_id, _agent_site(s), range)
