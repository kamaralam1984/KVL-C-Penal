"""Security console: events/incidents, alert routing, audit viewer, sessions overview, abuse settings. Platform staff only."""
import uuid
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, Query, Request
from pydantic import BaseModel, Field
from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.db import get_db
from app.core.deps import Ctx, api_error, client_ip, require
from app.models.identity import AuditEvent, AuthSession, User
from app.models.security import ApiKey, SecurityAlertRoute, SecurityEvent, WebhookDelivery
from app.services import security_events as svc
from app.services.audit import audit

router = APIRouter(prefix="/security", tags=["security"])


def _platform(ctx: Ctx) -> None:
    if not ctx.is_platform:
        raise api_error(403, "forbidden", "Platform staff only.")


def _edto(e: SecurityEvent) -> dict:
    return {"id": str(e.id), "created_at": e.created_at, "kind": e.kind, "severity": e.severity, "summary": e.summary, "status": e.status, "ip": e.ip,
            "tenant_id": str(e.tenant_id) if e.tenant_id else None, "user_id": str(e.user_id) if e.user_id else None, "detail": e.detail, "routed": e.routed,
            "acknowledged_by": str(e.acknowledged_by) if e.acknowledged_by else None, "acknowledged_at": e.acknowledged_at}


@router.get("/overview")
def overview(ctx: Ctx = Depends(require("security:read")), db: Session = Depends(get_db)):
    _platform(ctx)
    now = datetime.now(UTC)
    sev = dict(db.execute(select(SecurityEvent.severity, func.count()).where(SecurityEvent.status == "open").group_by(SecurityEvent.severity)).all())
    return {"open_events": sev, "active_sessions": db.scalar(select(func.count()).select_from(AuthSession).where(AuthSession.revoked_at.is_(None), AuthSession.expires_at > now, AuthSession.rotated_at.is_(None))),
            "active_api_keys": db.scalar(select(func.count()).select_from(ApiKey).where(ApiKey.revoked_at.is_(None))),
            "dead_webhook_deliveries": db.scalar(select(func.count()).select_from(WebhookDelivery).where(WebhookDelivery.status == "dead")),
            "alert_routes": db.scalar(select(func.count()).select_from(SecurityAlertRoute).where(SecurityAlertRoute.enabled.is_(True)))}


@router.get("/events")
def events(status: str | None = Query(default=None, pattern="^(open|acknowledged|resolved)$"), severity: str | None = Query(default=None, pattern="^(low|medium|high|critical)$"),
           kind: str | None = Query(default=None, max_length=50), limit: int = Query(default=50, ge=1, le=200), offset: int = Query(default=0, ge=0),
           ctx: Ctx = Depends(require("security:read")), db: Session = Depends(get_db)):
    _platform(ctx)
    stmt = select(SecurityEvent)
    if status:
        stmt = stmt.where(SecurityEvent.status == status)
    if severity:
        stmt = stmt.where(SecurityEvent.severity == severity)
    if kind:
        stmt = stmt.where(SecurityEvent.kind == kind)
    total = db.scalar(select(func.count()).select_from(stmt.subquery()))
    return {"total": total, "items": [_edto(e) for e in db.scalars(stmt.order_by(SecurityEvent.created_at.desc()).limit(limit).offset(offset))]}


def _transition(db: Session, ctx: Ctx, request: Request, eid: uuid.UUID, status: str) -> dict:
    _platform(ctx)
    e = db.get(SecurityEvent, eid)
    if not e:
        raise api_error(404, "not_found", "Event not found.")
    if e.status == "resolved" and status == "acknowledged":
        raise api_error(409, "already_resolved", "Event is already resolved.")
    e.status = status
    e.acknowledged_by, e.acknowledged_at = (e.acknowledged_by or ctx.user.id), (e.acknowledged_at or datetime.now(UTC))
    audit(db, f"security.event_{status}", actor=ctx.user.id, target_type="security_event", target_id=e.id, ip=client_ip(request))
    db.commit()
    return _edto(e)


@router.post("/events/{event_id}/acknowledge")
def acknowledge(event_id: uuid.UUID, request: Request, ctx: Ctx = Depends(require("security:manage")), db: Session = Depends(get_db)):
    return _transition(db, ctx, request, event_id, "acknowledged")


@router.post("/events/{event_id}/resolve")
def resolve(event_id: uuid.UUID, request: Request, ctx: Ctx = Depends(require("security:manage")), db: Session = Depends(get_db)):
    return _transition(db, ctx, request, event_id, "resolved")


@router.post("/scan")
def scan(request: Request, ctx: Ctx = Depends(require("security:manage")), db: Session = Depends(get_db)):
    """Run incident detection now. Also callable from a periodic task via app.services.security_events.scan_for_incidents."""
    _platform(ctx)
    out = svc.scan_for_incidents(db)
    audit(db, "security.scan_run", actor=ctx.user.id, detail=out, ip=client_ip(request))
    db.commit()
    return out


class RouteIn(BaseModel):
    name: str = Field(min_length=2, max_length=80)
    min_severity: str = Field(default="high", pattern="^(low|medium|high|critical)$")
    kinds: list[str] = Field(default_factory=list, max_length=20)
    channel: str = Field(pattern="^(email|webhook)$")
    target: str | None = Field(default=None, max_length=320, pattern=r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
    enabled: bool = True


@router.get("/alert-routes")
def routes(ctx: Ctx = Depends(require("security:read")), db: Session = Depends(get_db)):
    _platform(ctx)
    return [{"id": str(r.id), "name": r.name, "min_severity": r.min_severity, "kinds": (r.kinds or {}).get("kinds", []), "channel": r.channel, "target": r.target, "enabled": r.enabled}
            for r in db.scalars(select(SecurityAlertRoute).order_by(SecurityAlertRoute.created_at))]


@router.post("/alert-routes", status_code=201)
def create_route(body: RouteIn, request: Request, ctx: Ctx = Depends(require("security:manage")), db: Session = Depends(get_db)):
    _platform(ctx)
    if body.channel == "email" and not body.target:
        raise api_error(422, "validation_error", "Email routes need a target address.")
    r = SecurityAlertRoute(name=body.name, min_severity=body.min_severity, kinds={"kinds": body.kinds}, channel=body.channel, target=body.target if body.channel == "email" else None, enabled=body.enabled)
    db.add(r)
    db.flush()
    audit(db, "security.alert_route_created", actor=ctx.user.id, target_type="alert_route", target_id=r.id, ip=client_ip(request))
    db.commit()
    return {"id": str(r.id)}


@router.delete("/alert-routes/{route_id}")
def delete_route(route_id: uuid.UUID, request: Request, ctx: Ctx = Depends(require("security:manage")), db: Session = Depends(get_db)):
    _platform(ctx)
    r = db.get(SecurityAlertRoute, route_id)
    if not r:
        raise api_error(404, "not_found", "Route not found.")
    db.delete(r)
    audit(db, "security.alert_route_deleted", actor=ctx.user.id, target_type="alert_route", target_id=route_id, ip=client_ip(request))
    db.commit()
    return {"status": "ok"}


@router.get("/audit")
def audit_viewer(action: str | None = Query(default=None, max_length=100), actor_user_id: uuid.UUID | None = None, tenant_id: uuid.UUID | None = None,
                 target_type: str | None = Query(default=None, max_length=60), created_from: datetime | None = None, created_to: datetime | None = None,
                 limit: int = Query(default=50, ge=1, le=200), offset: int = Query(default=0, ge=0), ctx: Ctx = Depends(require("audit:read")), db: Session = Depends(get_db)):
    _platform(ctx)
    stmt = select(AuditEvent)
    if action:
        stmt = stmt.where(AuditEvent.action.like(action.replace("%", "").replace("_", r"\_") + "%", escape="\\"))
    if actor_user_id:
        stmt = stmt.where(AuditEvent.actor_user_id == actor_user_id)
    if tenant_id:
        stmt = stmt.where(AuditEvent.tenant_id == tenant_id)
    if target_type:
        stmt = stmt.where(AuditEvent.target_type == target_type)
    if created_from:
        stmt = stmt.where(AuditEvent.created_at >= created_from)
    if created_to:
        stmt = stmt.where(AuditEvent.created_at <= created_to)
    total = db.scalar(select(func.count()).select_from(stmt.subquery()))
    rows = db.scalars(stmt.order_by(AuditEvent.created_at.desc()).limit(limit).offset(offset))
    return {"total": total, "items": [{"id": str(a.id), "created_at": a.created_at, "action": a.action, "actor_user_id": str(a.actor_user_id) if a.actor_user_id else None,
                                       "tenant_id": str(a.tenant_id) if a.tenant_id else None, "target_type": a.target_type, "target_id": a.target_id, "ip": a.ip,
                                       "request_id": a.request_id, "detail": a.detail} for a in rows]}


@router.get("/sessions")
def sessions(limit: int = Query(default=100, ge=1, le=300), ctx: Ctx = Depends(require("security:read")), db: Session = Depends(get_db)):
    _platform(ctx)
    now = datetime.now(UTC)
    stmt = (select(AuthSession, User).join(User, User.id == AuthSession.user_id)
            .where(AuthSession.revoked_at.is_(None), AuthSession.expires_at > now, AuthSession.rotated_at.is_(None)).order_by(AuthSession.created_at.desc()).limit(limit))
    items = [{"id": str(s.id), "user_id": str(u.id), "email": u.email, "name": u.name, "ip": s.ip, "user_agent": s.user_agent, "created_at": s.created_at,
              "last_used_at": s.last_used_at, "expires_at": s.expires_at} for s, u in db.execute(stmt)]
    return {"active": len(items), "items": items}


@router.post("/sessions/{session_id}/revoke")
def revoke_session(session_id: uuid.UUID, request: Request, ctx: Ctx = Depends(require("security:manage")), db: Session = Depends(get_db)):
    _platform(ctx)
    s = db.get(AuthSession, session_id)
    if not s:
        raise api_error(404, "not_found", "Session not found.")
    db.execute(update(AuthSession).where(AuthSession.family_id == s.family_id, AuthSession.revoked_at.is_(None)).values(revoked_at=datetime.now(UTC)))
    audit(db, "security.session_revoked", actor=ctx.user.id, target_type="session", target_id=s.id, detail={"user_id": str(s.user_id)}, ip=client_ip(request))
    db.commit()
    return {"status": "ok"}


@router.get("/abuse-settings")
def abuse_settings(ctx: Ctx = Depends(require("security:read"))):
    """Read-only view of the abuse controls in force. They are code/env configured; changing them is a deploy, not a click."""
    _platform(ctx)
    s = get_settings()
    return {"editable": False, "login": {"lockout_threshold": s.lockout_threshold, "lockout_seconds": s.lockout_seconds},
            "otp": {"max_attempts": s.otp_max_attempts, "resend_cooldown_seconds": s.otp_resend_cooldown_seconds, "ttl_seconds": s.otp_ttl_seconds},
            "rate_limits": [
                {"name": "login (per email)", "limit": 10, "window_seconds": 900}, {"name": "ticket creation (per user)", "limit": 20, "window_seconds": 3600},
                {"name": "api key (default, per key)", "limit": 60, "window_seconds": 60}, {"name": "api key auth failures (per IP)", "limit": 30, "window_seconds": 60}],
            "incident_detection": {"failed_login_surge_threshold": svc.SURGE_THRESHOLD, "window_minutes": svc.SURGE_WINDOW_MINUTES},
            "attachments": {"max_bytes": s.support_max_attachment_bytes}, "webhooks": {"max_attempts": s.webhook_max_attempts, "timeout_seconds": s.webhook_timeout_seconds}}
