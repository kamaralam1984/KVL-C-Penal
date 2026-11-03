"""Webhook subscriptions, signing secrets (shown once), delivery log, retries and dead letters."""
import uuid
from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, Depends, Query, Request
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core import netsafe
from app.core.db import get_db
from app.core.deps import Ctx, api_error, client_ip, require
from app.core.security import encrypt_secret
from app.models.security import WebhookDelivery, WebhookEndpoint
from app.models.tenant import Tenant
from app.services import webhooks as svc
from app.services.audit import audit

router = APIRouter(prefix="/webhooks", tags=["webhooks"])
MAX_ENDPOINTS = 10


def _dto(e: WebhookEndpoint) -> dict:
    return {"id": str(e.id), "url": e.url, "description": e.description, "events": (e.events or {}).get("events", []), "status": e.status,
            "tenant_id": str(e.tenant_id) if e.tenant_id else None, "created_at": e.created_at,
            "rotation_overlap_until": e.prev_secret_expires_at if e.prev_secret_expires_at and e.prev_secret_expires_at > datetime.now(UTC) else None}


def _ddto(d: WebhookDelivery) -> dict:
    return {"id": str(d.id), "endpoint_id": str(d.endpoint_id), "event_type": d.event_type, "event_id": d.event_id, "status": d.status, "attempts": d.attempts,
            "next_attempt_at": d.next_attempt_at, "last_status_code": d.last_status_code, "last_error": d.last_error, "created_at": d.created_at, "delivered_at": d.delivered_at}


def _visible(ctx: Ctx):
    stmt = select(WebhookEndpoint)
    return stmt if ctx.is_platform else stmt.where(WebhookEndpoint.tenant_id == ctx.tenant_id)


def _get(db: Session, ctx: Ctx, wid: uuid.UUID) -> WebhookEndpoint:
    e = db.scalar(_visible(ctx).where(WebhookEndpoint.id == wid))
    if not e:
        raise api_error(404, "not_found", "Webhook not found.")
    return e


def _check_url(db: Session, ctx: Ctx, url: str, tenant_id) -> None:
    try:
        netsafe.validate_public_url(url)
    except netsafe.UnsafeURL as e:
        from app.services.security_events import record_event

        audit(db, "webhook.url_blocked", actor=ctx.user.id, tenant_id=tenant_id, detail={"reason": str(e)})
        record_event(db, "webhook_url_blocked", "low", f"Blocked unsafe webhook URL ({e}).", tenant_id=tenant_id, user_id=ctx.user.id)
        db.commit()
        raise api_error(422, "unsafe_url", f"This URL cannot be used: {e}.") from None


class WebhookIn(BaseModel):
    url: str = Field(max_length=500)
    events: list[str] = Field(min_length=1, max_length=40)
    description: str | None = Field(default=None, max_length=200)
    tenant_id: uuid.UUID | None = None


class WebhookPatch(BaseModel):
    url: str | None = Field(default=None, max_length=500)
    events: list[str] | None = Field(default=None, min_length=1, max_length=40)
    description: str | None = Field(default=None, max_length=200)
    status: str | None = Field(default=None, pattern="^(active|disabled)$")


def _events(ev: list[str]) -> dict:
    try:
        return {"events": svc.validate_events(ev)}
    except ValueError as e:
        raise api_error(422, "unknown_event", str(e)) from None


@router.get("")
def list_webhooks(tenant_id: uuid.UUID | None = None, ctx: Ctx = Depends(require("webhooks:read")), db: Session = Depends(get_db)):
    stmt = _visible(ctx).order_by(WebhookEndpoint.created_at.desc()).limit(200)
    if ctx.is_platform and tenant_id:
        stmt = stmt.where(WebhookEndpoint.tenant_id == tenant_id)
    return [_dto(e) for e in db.scalars(stmt)]


@router.post("", status_code=201)
def create_webhook(body: WebhookIn, request: Request, ctx: Ctx = Depends(require("webhooks:write")), db: Session = Depends(get_db)):
    if ctx.via_api_key:
        raise api_error(403, "session_required", "Webhooks cannot be created with an API key.")
    if ctx.is_platform:
        tenant_id = body.tenant_id
        if tenant_id and not db.get(Tenant, tenant_id):
            raise api_error(404, "not_found", "Tenant not found.")
    else:
        if body.tenant_id not in (None, ctx.tenant_id):
            raise api_error(404, "not_found", "Resource not found.")
        tenant_id = ctx.tenant_id
    events = _events(body.events)
    if tenant_id is not None and "security.alert" in events["events"]:
        raise api_error(422, "unknown_event", "security.alert is only available to platform endpoints.")
    _check_url(db, ctx, body.url, tenant_id)
    n = db.scalar(select(func.count()).select_from(WebhookEndpoint).where(WebhookEndpoint.tenant_id.is_(tenant_id) if tenant_id is None else WebhookEndpoint.tenant_id == tenant_id))
    if n >= MAX_ENDPOINTS:
        raise api_error(409, "webhook_limit", f"At most {MAX_ENDPOINTS} webhook endpoints are allowed.")
    secret = svc.new_secret()
    e = WebhookEndpoint(tenant_id=tenant_id, created_by=ctx.user.id, url=body.url, description=body.description, events=events, secret_enc=encrypt_secret(secret))
    db.add(e)
    db.flush()
    audit(db, "webhook.created", actor=ctx.user.id, tenant_id=tenant_id, target_type="webhook", target_id=e.id, detail={"events": events["events"]}, ip=client_ip(request))
    db.commit()
    return {**_dto(e), "signing_secret": secret, "notice": "Store this signing secret now. It is shown only once."}


@router.get("/deliveries")
def all_deliveries(status: str | None = Query(default=None, pattern="^(pending|retrying|succeeded|dead)$"), limit: int = Query(default=50, ge=1, le=200), offset: int = Query(default=0, ge=0),
                   ctx: Ctx = Depends(require("webhooks:read")), db: Session = Depends(get_db)):
    vis = select(WebhookEndpoint.id).where(WebhookEndpoint.id.in_(_visible(ctx).with_only_columns(WebhookEndpoint.id)))
    stmt = select(WebhookDelivery).where(WebhookDelivery.endpoint_id.in_(vis))
    if status:
        stmt = stmt.where(WebhookDelivery.status == status)
    total = db.scalar(select(func.count()).select_from(stmt.subquery()))
    return {"total": total, "items": [_ddto(d) for d in db.scalars(stmt.order_by(WebhookDelivery.created_at.desc()).limit(limit).offset(offset))]}


@router.post("/deliveries/{delivery_id}/retry")
def retry_delivery(delivery_id: uuid.UUID, request: Request, ctx: Ctx = Depends(require("webhooks:write")), db: Session = Depends(get_db)):
    d = db.get(WebhookDelivery, delivery_id)
    if not d:
        raise api_error(404, "not_found", "Delivery not found.")
    _get(db, ctx, d.endpoint_id)
    if d.status not in ("dead", "retrying", "pending"):
        raise api_error(409, "not_retryable", "Only failed deliveries can be retried.")
    d.status, d.attempts, d.next_attempt_at = "pending", 0, datetime.now(UTC)
    audit(db, "webhook.delivery_requeued", actor=ctx.user.id, tenant_id=d.tenant_id, target_type="webhook_delivery", target_id=d.id, ip=client_ip(request))
    db.commit()
    return _ddto(d)


@router.post("/dispatch")
def dispatch(ctx: Ctx = Depends(require("webhooks:manage")), db: Session = Depends(get_db)):
    """Process due deliveries now (platform). A periodic task should call svc.deliver_due on a short interval."""
    if not ctx.is_platform:
        raise api_error(403, "forbidden", "Platform staff only.")
    counts = svc.deliver_due(db)
    db.commit()
    return counts


@router.patch("/{webhook_id}")
def update_webhook(webhook_id: uuid.UUID, body: WebhookPatch, request: Request, ctx: Ctx = Depends(require("webhooks:write")), db: Session = Depends(get_db)):
    e = _get(db, ctx, webhook_id)
    if body.url is not None and body.url != e.url:
        _check_url(db, ctx, body.url, e.tenant_id)
        e.url = body.url
    if body.events is not None:
        e.events = _events(body.events)
    if body.description is not None:
        e.description = body.description
    if body.status:
        e.status = body.status
    audit(db, "webhook.updated", actor=ctx.user.id, tenant_id=e.tenant_id, target_type="webhook", target_id=e.id, ip=client_ip(request))
    db.commit()
    return _dto(e)


@router.delete("/{webhook_id}")
def delete_webhook(webhook_id: uuid.UUID, request: Request, ctx: Ctx = Depends(require("webhooks:write")), db: Session = Depends(get_db)):
    e = _get(db, ctx, webhook_id)
    db.delete(e)
    audit(db, "webhook.deleted", actor=ctx.user.id, tenant_id=e.tenant_id, target_type="webhook", target_id=webhook_id, ip=client_ip(request))
    db.commit()
    return {"status": "ok"}


@router.post("/{webhook_id}/rotate-secret")
def rotate_secret(webhook_id: uuid.UUID, request: Request, ctx: Ctx = Depends(require("webhooks:write")), db: Session = Depends(get_db)):
    """New secret is shown once. The old one keeps signing alongside it for 24h (both v1 signatures are sent) so receivers can switch."""
    if ctx.via_api_key:
        raise api_error(403, "session_required", "Secrets cannot be rotated with an API key.")
    e = _get(db, ctx, webhook_id)
    secret = svc.new_secret()
    e.prev_secret_enc, e.prev_secret_expires_at = e.secret_enc, datetime.now(UTC) + timedelta(hours=24)
    e.secret_enc = encrypt_secret(secret)
    audit(db, "webhook.secret_rotated", actor=ctx.user.id, tenant_id=e.tenant_id, target_type="webhook", target_id=e.id, ip=client_ip(request))
    db.commit()
    return {**_dto(e), "signing_secret": secret, "notice": "Store this signing secret now. It is shown only once."}


@router.post("/{webhook_id}/test")
def send_test(webhook_id: uuid.UUID, ctx: Ctx = Depends(require("webhooks:write")), db: Session = Depends(get_db)):
    e = _get(db, ctx, webhook_id)
    out = svc.emit_event(db, "ping", {"message": "KVL webhook test"}, tenant_id=e.tenant_id, endpoint_ids={e.id})
    db.commit()
    for d in out:
        svc.attempt_delivery(db, d)
    db.commit()
    return [_ddto(d) for d in out]


@router.get("/{webhook_id}/deliveries")
def deliveries(webhook_id: uuid.UUID, limit: int = Query(default=50, ge=1, le=200), ctx: Ctx = Depends(require("webhooks:read")), db: Session = Depends(get_db)):
    e = _get(db, ctx, webhook_id)
    return [_ddto(d) for d in db.scalars(select(WebhookDelivery).where(WebhookDelivery.endpoint_id == e.id).order_by(WebhookDelivery.created_at.desc()).limit(limit))]
