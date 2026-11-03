"""Phase 15: n8n integration. Inbound signed webhook (from n8n) and admin visibility/controls. Admin-only."""
import uuid
from typing import Any

from fastapi import APIRouter, Depends, Header, Request
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core import ratelimit
from app.core.config import get_settings
from app.core.db import get_db
from app.core.deps import Ctx, api_error, client_ip, require
from app.models.automation import OutboxEvent, WorkflowExecution, WorkflowState
from app.services import automation as svc
from app.services.audit import audit

router = APIRouter(prefix="/automation", tags=["automation"])


@router.post("/inbound")
async def inbound(
    request: Request,
    db: Session = Depends(get_db),
    x_kvl_timestamp: str | None = Header(default=None),
    x_kvl_signature: str | None = Header(default=None),
    x_kvl_idempotency_key: str | None = Header(default=None),
):
    """Events FROM n8n. Authenticated by HMAC(secret, timestamp.body) within a 5-minute window; idempotent on X-KVL-Idempotency-Key.
    Only a small allow-list of event types is accepted and each is authorised and tenant-scoped here."""
    ratelimit.enforce(f"n8n_inbound:{client_ip(request)}", 300, 60)
    raw = await request.body()
    if len(raw) > 64 * 1024:
        raise api_error(413, "payload_too_large", "Payload too large.")
    svc.verify_signature(get_settings().n8n_webhook_secret, x_kvl_timestamp, x_kvl_signature, raw)
    if not x_kvl_idempotency_key or len(x_kvl_idempotency_key) > 160:
        raise api_error(400, "idempotency_key_required", "X-KVL-Idempotency-Key header is required.")
    return svc.process_inbound(db, raw, x_kvl_idempotency_key)


@router.get("/status")
def status(ctx: Ctx = Depends(require("automation:read")), db: Session = Depends(get_db)):
    counts = dict(db.execute(select(OutboxEvent.status, func.count()).group_by(OutboxEvent.status)).all())
    return {"integration": svc.integration_status(), "outbox": {"pending": counts.get("pending", 0), "delivered": counts.get("delivered", 0), "dead": counts.get("dead", 0)}}


@router.get("/workflows")
def workflows(ctx: Ctx = Depends(require("automation:read")), db: Session = Depends(get_db)):
    states = {s.key: s.enabled for s in db.scalars(select(WorkflowState))}
    out = []
    for c in svc.CATALOG:
        last = db.scalars(select(WorkflowExecution).where(WorkflowExecution.workflow_key == c["key"]).order_by(WorkflowExecution.started_at.desc()).limit(1)).first()
        fails = db.scalar(select(func.count()).select_from(WorkflowExecution).where(WorkflowExecution.workflow_key == c["key"], WorkflowExecution.status == "failed")) or 0
        out.append({**c, "enabled": states.get(c["key"], True), "events": svc.workflow_events(c["key"]), "import_file": f"automation/n8n/{c['key']}.json", "last_status": last.status if last else None, "last_run_at": last.started_at if last else None, "failed_runs": fails})
    return {"items": out}


class ToggleIn(BaseModel):
    enabled: bool


@router.post("/workflows/{key}/enabled")
def toggle(key: str, body: ToggleIn, request: Request, ctx: Ctx = Depends(require("automation:manage")), db: Session = Depends(get_db)):
    if key not in svc.CATALOG_KEYS:
        raise api_error(404, "not_found", "Workflow not found.")
    s = db.scalar(select(WorkflowState).where(WorkflowState.key == key))
    if s is None:
        s = WorkflowState(key=key)
        db.add(s)
    s.enabled, s.updated_by = body.enabled, ctx.user.id
    audit(db, "automation.workflow_toggled", actor=ctx.user.id, target_type="workflow", detail={"key": key, "enabled": body.enabled}, ip=client_ip(request))
    db.commit()
    return {"key": key, "enabled": s.enabled}


def _ev_dto(e: OutboxEvent, detail: bool = False) -> dict[str, Any]:
    out = {"id": str(e.id), "event_type": e.event_type, "workflow": svc.EVENTS[e.event_type][1], "tenant_id": str(e.tenant_id) if e.tenant_id else None, "status": e.status, "attempts": e.attempts, "max_attempts": e.max_attempts, "next_attempt_at": e.next_attempt_at, "last_error": e.last_error, "correlation_id": e.correlation_id, "created_at": e.created_at, "delivered_at": e.delivered_at}
    if detail:
        out["payload"] = e.payload
    return out


@router.get("/outbox")
def outbox(status: str | None = None, limit: int = 100, ctx: Ctx = Depends(require("automation:read")), db: Session = Depends(get_db)):
    stmt = select(OutboxEvent).order_by(OutboxEvent.created_at.desc()).limit(min(limit, 200))
    if status:
        stmt = stmt.where(OutboxEvent.status == status)
    return {"items": [_ev_dto(e) for e in db.scalars(stmt)]}


@router.get("/outbox/{event_id}")
def outbox_detail(event_id: uuid.UUID, ctx: Ctx = Depends(require("automation:read")), db: Session = Depends(get_db)):
    e = db.get(OutboxEvent, event_id)
    if e is None:
        raise api_error(404, "not_found", "Event not found.")
    return _ev_dto(e, True)


@router.post("/outbox/{event_id}/retry")
def retry(event_id: uuid.UUID, request: Request, ctx: Ctx = Depends(require("automation:manage")), db: Session = Depends(get_db)):
    e = db.scalar(select(OutboxEvent).where(OutboxEvent.id == event_id).with_for_update())
    if e is None:
        raise api_error(404, "not_found", "Event not found.")
    svc.retry_dead(db, e)
    audit(db, "automation.retry", actor=ctx.user.id, target_type="outbox", target_id=e.id, ip=client_ip(request))
    db.commit()
    return _ev_dto(e)


@router.get("/executions")
def executions(status: str | None = None, limit: int = 100, ctx: Ctx = Depends(require("automation:read")), db: Session = Depends(get_db)):
    stmt = select(WorkflowExecution).order_by(WorkflowExecution.started_at.desc()).limit(min(limit, 200))
    if status:
        stmt = stmt.where(WorkflowExecution.status == status)
    return {"items": [{"id": str(x.id), "workflow_key": x.workflow_key, "correlation_id": x.correlation_id, "tenant_id": str(x.tenant_id) if x.tenant_id else None, "status": x.status, "error": x.error, "escalated": x.escalated, "external_execution_id": x.external_execution_id, "started_at": x.started_at, "finished_at": x.finished_at} for x in db.scalars(stmt)]}


@router.post("/dispatch")
def dispatch(ctx: Ctx = Depends(require("automation:manage")), db: Session = Depends(get_db)):
    """Deliver due events now (normally run by a scheduler/worker). Reports not_configured without n8n credentials."""
    if svc.get_sender() is None:
        return {"status": "not_configured", "delivered": 0, "retried": 0, "dead": 0}
    return svc.dispatch_due(db)


@router.post("/scheduler/run")
def scheduler(ctx: Ctx = Depends(require("automation:manage")), db: Session = Depends(get_db)):
    """Emit due reminder events (invoices, renewals, approvals). Idempotent."""
    return svc.run_scheduler(db)
