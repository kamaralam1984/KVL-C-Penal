"""n8n integration: outbox/dispatcher (TO n8n), inbound verification + handlers (FROM n8n), workflow catalog, reminders.

n8n is only a workflow engine. Everything it asks of us arrives as a signed request, is schema validated,
is idempotent, and is authorised here (it can never mark a payment paid or recipients of its choosing).
"""
import hashlib
import hmac
import json
import logging
import time
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any, Literal, Protocol

import httpx
from pydantic import BaseModel, ConfigDict, Field, ValidationError
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.deps import api_error
from app.models.automation import InboundEvent, OutboxEvent, WorkflowExecution, WorkflowState
from app.models.identity import User
from app.models.tenant import Tenant
from app.services.audit import audit
from app.services.mail import queue_email

log = logging.getLogger("kvl.automation")

SIGNATURE_WINDOW_SECONDS = 300
BACKOFF_BASE_SECONDS = 30
BACKOFF_CAP_SECONDS = 3600
DISPATCH_BATCH = 50


# --------------------------------------------------------------------------------------------- schemas
class _S(BaseModel):
    model_config = ConfigDict(extra="forbid")


class TenantCreated(_S):
    tenant_id: uuid.UUID
    tenant_name: str = Field(max_length=200)
    plan_code: str | None = None
    reseller_id: uuid.UUID | None = None


class UserWelcome(_S):
    tenant_id: uuid.UUID
    recipient_email: str = Field(max_length=320)
    name: str = Field(max_length=200)


class PaymentVerified(_S):
    tenant_id: uuid.UUID
    invoice_id: uuid.UUID
    invoice_number: str
    amount_paise: int = Field(ge=0)
    purpose: str


class TenantActivated(_S):
    tenant_id: uuid.UUID
    reason: str = Field(max_length=100)


class InvoiceDue(_S):
    tenant_id: uuid.UUID
    recipient_email: str | None = None
    invoice_id: uuid.UUID
    invoice_number: str
    amount_paise: int = Field(ge=0)
    due_at: datetime | None = None
    days_until_due: int


class RenewalReminder(_S):
    tenant_id: uuid.UUID
    recipient_email: str | None = None
    subscription_id: uuid.UUID
    renews_at: datetime
    days_until_renewal: int
    plan_name: str


class SslExpiring(_S):
    tenant_id: uuid.UUID
    domain: str = Field(max_length=255)
    expires_at: datetime
    days_left: int


class BackupResult(_S):
    tenant_id: uuid.UUID
    backup_id: str = Field(max_length=64)
    outcome: Literal["success", "failed"]
    detail: str | None = Field(default=None, max_length=300)


class ServerHealth(_S):
    server_id: str = Field(max_length=64)
    severity: Literal["info", "warning", "critical"]
    message: str = Field(max_length=300)


class TicketUpdated(_S):
    tenant_id: uuid.UUID
    ticket_id: str = Field(max_length=64)
    status: str = Field(max_length=30)
    summary: str | None = Field(default=None, max_length=300)


class Report(_S):
    period_start: datetime
    period_end: datetime
    totals: dict[str, int]


class ApprovalReminder(_S):
    kind: Literal["reseller_application"]
    pending_count: int = Field(ge=1)
    oldest_pending_hours: int = Field(ge=0)


class WorkflowFailed(_S):
    workflow_key: str = Field(max_length=60)
    correlation_id: str = Field(max_length=64)
    error: str = Field(max_length=500)
    attempts: int = 0


# event_type -> (schema, workflow key, tenant scoped?)
EVENTS: dict[str, tuple[type[BaseModel], str, bool]] = {
    "tenant.created": (TenantCreated, "tenant_onboarding", True),
    "user.welcome": (UserWelcome, "customer_welcome", True),
    "billing.payment_verified": (PaymentVerified, "hosting_activation", True),
    "tenant.activated": (TenantActivated, "hosting_activation", True),
    "invoice.due_reminder": (InvoiceDue, "invoice_due_reminders", True),
    "subscription.renewal_reminder": (RenewalReminder, "renewal_reminders", True),
    "ssl.expiring": (SslExpiring, "ssl_expiry_alerts", True),
    "backup.result": (BackupResult, "backup_alerts", True),
    "server.health": (ServerHealth, "server_health_alerts", False),
    "ticket.updated": (TicketUpdated, "ticket_updates", True),
    "report.daily": (Report, "operational_reports", False),
    "report.weekly": (Report, "operational_reports", False),
    "admin.approval_reminder": (ApprovalReminder, "admin_approval_reminders", False),
    "automation.workflow_failed": (WorkflowFailed, "failed_workflow_escalation", False),
}

CATALOG: list[dict[str, Any]] = [
    {"key": "tenant_onboarding", "name": "New tenant onboarding", "description": "Creates a checklist/notification when a tenant is created."},
    {"key": "customer_welcome", "name": "Customer welcome and verification", "description": "Welcome and email verification reminders."},
    {"key": "hosting_activation", "name": "Hosting activation after verified payment", "description": "Runs follow-up steps only after the backend verified payment (webhook-confirmed)."},
    {"key": "invoice_due_reminders", "name": "Invoice due reminders", "description": "Reminders before an open invoice falls due."},
    {"key": "renewal_reminders", "name": "Subscription renewal reminders", "description": "Reminders before the billing period ends."},
    {"key": "ssl_expiry_alerts", "name": "SSL expiry alerts", "description": "Alerts when a certificate approaches expiry."},
    {"key": "backup_alerts", "name": "Backup success/failure alerts", "description": "Notifies customers and admins of backup outcomes."},
    {"key": "server_health_alerts", "name": "Server heartbeat/health alerts", "description": "Notifies admins of missed heartbeats and health problems."},
    {"key": "ticket_updates", "name": "Support ticket updates", "description": "Notifies customers about ticket status changes."},
    {"key": "operational_reports", "name": "Daily/weekly operational reports", "description": "Sends platform-level digest reports to admins."},
    {"key": "admin_approval_reminders", "name": "Admin approval reminders", "description": "Reminds admins about pending reseller applications."},
    {"key": "failed_workflow_escalation", "name": "Failed workflow escalation", "description": "Escalates dead-lettered or failed workflow runs to admins."},
]
CATALOG_KEYS = {c["key"] for c in CATALOG}


def workflow_events(key: str) -> list[str]:
    return [e for e, (_, k, _) in EVENTS.items() if k == key]


# -------------------------------------------------------------------------------------------- signatures
def sign(secret: str, timestamp: str, raw: bytes) -> str:
    return hmac.new(secret.encode(), timestamp.encode() + b"." + raw, hashlib.sha256).hexdigest()


def verify_signature(secret: str, timestamp: str | None, signature: str | None, raw: bytes, now: float | None = None) -> None:
    """Raises api_error if the request is unsigned, stale, or the signature does not match (constant-time compare)."""
    if not secret:
        raise api_error(503, "not_configured", "n8n integration is not configured.")
    if not timestamp or not signature:
        raise api_error(401, "invalid_signature", "Missing signature headers.")
    try:
        ts = int(timestamp)
    except ValueError:
        raise api_error(401, "invalid_signature", "Invalid timestamp.") from None
    if abs((now if now is not None else time.time()) - ts) > SIGNATURE_WINDOW_SECONDS:
        raise api_error(401, "stale_request", "Request timestamp outside the allowed window.")
    if not hmac.compare_digest(sign(secret, timestamp, raw), signature.strip().lower()):
        raise api_error(401, "invalid_signature", "Signature mismatch.")


# ---------------------------------------------------------------------------------------------- outbox
def integration_status() -> dict[str, Any]:
    s = get_settings()
    return {
        "inbound": "configured" if s.n8n_webhook_secret else "not_configured",
        "outbound": "configured" if (s.n8n_webhook_secret and s.n8n_base_url) else "not_configured",
        "base_url_set": bool(s.n8n_base_url),
        "signature_window_seconds": SIGNATURE_WINDOW_SECONDS,
    }


def workflow_enabled(db: Session, key: str) -> bool:
    row = db.scalar(select(WorkflowState).where(WorkflowState.key == key))
    return True if row is None else row.enabled


def emit_event(
    db: Session,
    event_type: str,
    payload: dict[str, Any],
    *,
    tenant_id: uuid.UUID | None = None,
    correlation_id: str | None = None,
    idempotency_key: str | None = None,
) -> OutboxEvent | None:
    """Queue an event for n8n. Generic entry point for every module. Validates the payload against the event schema.

    Tenant-scoped events must carry the same tenant_id in the payload (prevents cross-tenant leakage).
    Returns None when the workflow is disabled or the idempotency key was already used.
    """
    spec = EVENTS.get(event_type)
    if spec is None:
        raise ValueError(f"Unknown automation event type '{event_type}'")
    schema, wf_key, scoped = spec
    clean = schema.model_validate(payload).model_dump(mode="json")
    if scoped:
        if tenant_id is None or clean.get("tenant_id") != str(tenant_id):
            raise ValueError("Tenant-scoped event payload must match its tenant_id")
    elif tenant_id is not None:
        raise ValueError("Platform event must not carry a tenant_id")
    if not workflow_enabled(db, wf_key):
        return None
    if idempotency_key and db.scalar(select(OutboxEvent.id).where(OutboxEvent.idempotency_key == idempotency_key)):
        return None
    ev = OutboxEvent(
        event_type=event_type, tenant_id=tenant_id, payload=clean,
        correlation_id=correlation_id or uuid.uuid4().hex, idempotency_key=idempotency_key,
    )
    db.add(ev)
    db.flush()
    return ev


def backoff_seconds(attempts: int) -> int:
    """Delay after the Nth failed attempt: 30s, 60s, 120s ... capped at 1h."""
    return min(BACKOFF_BASE_SECONDS * 2 ** max(attempts - 1, 0), BACKOFF_CAP_SECONDS)


class Sender(Protocol):
    def send(self, path: str, body: bytes, headers: dict[str, str]) -> int: ...


class N8nClient:
    """POSTs a signed event to a private n8n instance. Tests replace this with a fake."""

    def __init__(self, base_url: str, timeout: float = 8.0):
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout

    def send(self, path: str, body: bytes, headers: dict[str, str]) -> int:
        r = httpx.post(f"{self.base_url}{path}", content=body, headers=headers, timeout=self.timeout, follow_redirects=False)
        return r.status_code


def get_sender() -> Sender | None:
    s = get_settings()
    if not (s.n8n_webhook_secret and s.n8n_base_url):
        return None
    return N8nClient(s.n8n_base_url)


def _now() -> datetime:
    return datetime.now(UTC)


def record_execution(db: Session, ev: OutboxEvent, status: str, error: str | None = None) -> WorkflowExecution:
    wf = EVENTS[ev.event_type][1]
    ex = WorkflowExecution(
        workflow_key=wf, correlation_id=ev.correlation_id, tenant_id=ev.tenant_id, outbox_event_id=ev.id, status=status,
        error=error, finished_at=_now() if status != "running" else None,
    )
    db.add(ex)
    return ex


def dispatch_due(db: Session, sender: Sender | None = None, now: datetime | None = None, limit: int = DISPATCH_BATCH) -> dict[str, int]:
    """Deliver due outbox events once. Failures back off exponentially; after max_attempts the event is dead-lettered."""
    sender = sender or get_sender()
    if sender is None:
        return {"status_not_configured": 1, "delivered": 0, "retried": 0, "dead": 0}
    secret = get_settings().n8n_webhook_secret
    now = now or _now()
    rows = db.scalars(
        select(OutboxEvent).where(OutboxEvent.status == "pending", OutboxEvent.next_attempt_at <= now)
        .order_by(OutboxEvent.next_attempt_at).limit(min(limit, DISPATCH_BATCH)).with_for_update(skip_locked=True)
    ).all()
    out = {"delivered": 0, "retried": 0, "dead": 0}
    for ev in rows:
        wf = EVENTS[ev.event_type][1]
        body = json.dumps({"event_type": ev.event_type, "event_id": str(ev.id), "correlation_id": ev.correlation_id, "tenant_id": str(ev.tenant_id) if ev.tenant_id else None, "occurred_at": ev.created_at.isoformat() if ev.created_at else None, "data": ev.payload}, separators=(",", ":")).encode()
        ts = str(int(now.timestamp()))
        headers = {
            "Content-Type": "application/json", "X-KVL-Timestamp": ts, "X-KVL-Signature": sign(secret, ts, body),
            "X-KVL-Event-Id": str(ev.id), "X-KVL-Event-Type": ev.event_type, "X-KVL-Correlation-Id": ev.correlation_id,
        }
        ev.attempts += 1
        err: str | None = None
        try:
            code = sender.send(f"/webhook/kvl-{wf.replace('_', '-')}", body, headers)
            if not 200 <= code < 300:
                err = f"n8n responded HTTP {code}"
        except Exception as e:  # network errors, timeouts
            err = f"{type(e).__name__}: {str(e)[:200]}"
        if err is None:
            ev.status, ev.delivered_at, ev.last_error = "delivered", now, None
            record_execution(db, ev, "running")
            out["delivered"] += 1
        elif ev.attempts >= ev.max_attempts:
            ev.status, ev.last_error = "dead", err
            record_execution(db, ev, "failed", err)
            _escalate(db, ev, err)
            out["dead"] += 1
        else:
            ev.last_error = err
            ev.next_attempt_at = now + timedelta(seconds=backoff_seconds(ev.attempts))
            out["retried"] += 1
    db.commit()
    return out


def _escalate(db: Session, ev: OutboxEvent, error: str) -> None:
    """Dead letters raise a platform-level escalation event (never for escalation events themselves: no loops)."""
    if ev.event_type == "automation.workflow_failed":
        return
    emit_event(
        db, "automation.workflow_failed",
        {"workflow_key": EVENTS[ev.event_type][1], "correlation_id": ev.correlation_id, "error": error[:500], "attempts": ev.attempts},
        idempotency_key=f"escalate:{ev.id}",
    )


def retry_dead(db: Session, ev: OutboxEvent) -> OutboxEvent:
    """Safe retry: only dead-lettered events, same event id and correlation id (receivers dedupe on X-KVL-Event-Id)."""
    if ev.status != "dead":
        raise api_error(409, "not_retryable", "Only dead-lettered events can be retried.")
    ev.status, ev.attempts, ev.next_attempt_at, ev.last_error = "pending", 0, _now(), None
    return ev


# ---------------------------------------------------------------------------------------------- inbound
class WorkflowExecutionIn(_S):
    workflow_key: str
    status: Literal["succeeded", "failed"]
    execution_id: str | None = Field(default=None, max_length=80)
    error: str | None = Field(default=None, max_length=500)


class NotificationIn(_S):
    tenant_id: uuid.UUID
    template: Literal["welcome", "verification_reminder", "invoice_reminder", "renewal_reminder", "ssl_expiry", "backup_alert", "ticket_update"]
    subject: str = Field(min_length=1, max_length=150)
    message: str = Field(min_length=1, max_length=1000)


class ActivateIn(_S):
    tenant_id: uuid.UUID


INBOUND: dict[str, type[BaseModel]] = {
    "workflow.execution": WorkflowExecutionIn,
    "notification.send": NotificationIn,
    "tenant.activate": ActivateIn,
}


class InboundEnvelope(BaseModel):
    model_config = ConfigDict(extra="forbid")
    event_type: str = Field(max_length=60)
    correlation_id: str | None = Field(default=None, max_length=64)
    data: dict[str, Any] = {}


def tenant_recipient(db: Session, tenant_id: uuid.UUID) -> str | None:
    t = db.get(Tenant, tenant_id)
    if t is None:
        return None
    if t.contact_email:
        return t.contact_email
    if t.owner_user_id:
        return db.scalar(select(User.email).where(User.id == t.owner_user_id))
    return None


def process_inbound(db: Session, raw: bytes, idempotency_key: str) -> dict[str, Any]:
    """Validate, deduplicate and execute one signed inbound event. Returns the result dict (also stored for replays)."""
    digest = hashlib.sha256(raw).hexdigest()
    try:
        env = InboundEnvelope.model_validate_json(raw)
    except ValidationError:
        raise api_error(422, "invalid_event", "Event envelope failed validation.") from None
    schema = INBOUND.get(env.event_type)
    if schema is None:
        raise api_error(422, "unsupported_event", "This event type is not accepted from workflows.")
    try:
        data = schema.model_validate(env.data)
    except ValidationError as e:
        raise api_error(422, "invalid_event", "; ".join(f"{'.'.join(map(str, x['loc']))}: {x['msg']}" for x in e.errors())) from None

    existing = db.scalar(select(InboundEvent).where(InboundEvent.idempotency_key == idempotency_key))
    if existing is None:
        rec = InboundEvent(idempotency_key=idempotency_key, body_sha256=digest, event_type=env.event_type, correlation_id=env.correlation_id)
        try:
            with db.begin_nested():
                db.add(rec)
                db.flush()
        except IntegrityError:
            existing = db.scalar(select(InboundEvent).where(InboundEvent.idempotency_key == idempotency_key))
    if existing is not None:
        if existing.body_sha256 != digest:
            raise api_error(409, "idempotency_conflict", "Idempotency key reused with a different body.")
        if existing.status == "processed":
            return {"status": "duplicate", **(existing.result or {})}
        rec = existing  # failed/processing earlier: allow re-run
    try:
        with db.begin_nested():
            result = _handle(db, env, data)
    except Exception as e:
        rec.status = "failed"
        rec.result = {"error": getattr(e, "detail", {}).get("code", "error") if hasattr(e, "detail") and isinstance(e.detail, dict) else "error"}
        db.commit()
        raise
    rec.status, rec.result = "processed", result
    db.commit()
    return {"status": "processed", **result}


def _handle(db: Session, env: InboundEnvelope, data: Any) -> dict[str, Any]:
    if env.event_type == "workflow.execution":
        if data.workflow_key not in CATALOG_KEYS:
            raise api_error(422, "unknown_workflow", "Unknown workflow key.")
        q = select(WorkflowExecution).where(WorkflowExecution.workflow_key == data.workflow_key, WorkflowExecution.status == "running")
        if env.correlation_id:
            q = q.where(WorkflowExecution.correlation_id == env.correlation_id)
        ex = db.scalars(q.order_by(WorkflowExecution.started_at.desc()).limit(1)).first()
        if ex is None:
            ex = WorkflowExecution(workflow_key=data.workflow_key, correlation_id=env.correlation_id or uuid.uuid4().hex)
            db.add(ex)
        ex.status, ex.external_execution_id, ex.error, ex.finished_at = data.status, data.execution_id, data.error, _now()
        if data.status == "failed" and data.workflow_key != "failed_workflow_escalation" and not ex.escalated:
            ex.escalated = True
            emit_event(
                db, "automation.workflow_failed",
                {"workflow_key": data.workflow_key, "correlation_id": ex.correlation_id, "error": (data.error or "failed")[:500], "attempts": 1},
                idempotency_key=f"escalate-exec:{ex.id}",
            )
        db.flush()
        return {"execution_id": str(ex.id), "escalated": ex.escalated}
    if env.event_type == "notification.send":
        to = tenant_recipient(db, data.tenant_id)
        if not to:
            raise api_error(404, "not_found", "Tenant or recipient not found.")
        queue_email(db, to, data.subject, data.message, f"automation:{data.template}", tenant_id=data.tenant_id)
        audit(db, "automation.notification", tenant_id=data.tenant_id, target_type="tenant", target_id=data.tenant_id, detail={"template": data.template, "correlation_id": env.correlation_id})
        return {"queued": True}
    if env.event_type == "tenant.activate":
        from app.services import billing  # local import: billing emits events through this module

        activated = billing.activate_if_paid(db, data.tenant_id)
        audit(db, "automation.tenant_activate", tenant_id=data.tenant_id, target_type="tenant", target_id=data.tenant_id, detail={"activated": activated, "correlation_id": env.correlation_id})
        return {"activated": activated}
    raise api_error(422, "unsupported_event", "Unsupported.")  # pragma: no cover


# ---------------------------------------------------------------------------------- scheduler-callable
def emit_invoice_due_reminders(db: Session, now: datetime | None = None, days_before: tuple[int, ...] = (3, 1, 0)) -> int:
    """For open invoices due within the window, emit one reminder per (invoice, threshold). Idempotent."""
    from app.models.billing import Invoice

    now = now or _now()
    horizon = now + timedelta(days=max(days_before) + 1)
    n = 0
    for inv in db.scalars(select(Invoice).where(Invoice.status == "open", Invoice.due_at.is_not(None), Invoice.due_at <= horizon, Invoice.total_paise > 0)):
        days_left = (inv.due_at.date() - now.date()).days
        for d in sorted(days_before):
            if days_left <= d:
                ev = emit_event(
                    db, "invoice.due_reminder",
                    {"tenant_id": inv.tenant_id, "recipient_email": tenant_recipient(db, inv.tenant_id), "invoice_id": inv.id, "invoice_number": inv.number, "amount_paise": inv.total_paise, "due_at": inv.due_at, "days_until_due": days_left},
                    tenant_id=inv.tenant_id, idempotency_key=f"invoice_due:{inv.id}:{d}",
                )
                n += 1 if ev else 0
                break
    return n


def emit_renewal_reminders(db: Session, now: datetime | None = None, days_before: tuple[int, ...] = (7, 3, 1)) -> int:
    from app.models.billing import Plan, PlanVersion, Subscription

    now = now or _now()
    horizon = now + timedelta(days=max(days_before) + 1)
    n = 0
    q = select(Subscription, Plan.name).join(PlanVersion, PlanVersion.id == Subscription.plan_version_id).join(Plan, Plan.id == PlanVersion.plan_id).where(
        Subscription.status == "active", Subscription.cancel_at_period_end.is_(False), Subscription.current_period_end <= horizon, Subscription.current_period_end > now
    )
    for sub, plan_name in db.execute(q):
        days_left = (sub.current_period_end.date() - now.date()).days
        for d in sorted(days_before):
            if days_left <= d:
                ev = emit_event(
                    db, "subscription.renewal_reminder",
                    {"tenant_id": sub.tenant_id, "recipient_email": tenant_recipient(db, sub.tenant_id), "subscription_id": sub.id, "renews_at": sub.current_period_end, "days_until_renewal": days_left, "plan_name": plan_name},
                    tenant_id=sub.tenant_id, idempotency_key=f"renewal:{sub.id}:{sub.current_period_end.date()}:{d}",
                )
                n += 1 if ev else 0
                break
    return n


def emit_admin_approval_reminders(db: Session, now: datetime | None = None, min_age_hours: int = 24) -> int:
    from app.models.reseller import Reseller

    now = now or _now()
    old = db.scalars(select(Reseller.applied_at).where(Reseller.status == "pending_approval", Reseller.applied_at <= now - timedelta(hours=min_age_hours)).order_by(Reseller.applied_at)).all()
    if not old:
        return 0
    ev = emit_event(
        db, "admin.approval_reminder",
        {"kind": "reseller_application", "pending_count": len(old), "oldest_pending_hours": int((now - old[0]).total_seconds() // 3600)},
        idempotency_key=f"approval_reminder:{now.date()}",
    )
    return 1 if ev else 0


def emit_operational_report(db: Session, period: Literal["daily", "weekly"] = "daily", now: datetime | None = None) -> OutboxEvent | None:
    """Platform-level totals only (no tenant data)."""
    from app.models.billing import Invoice, Subscription

    now = now or _now()
    start = now - timedelta(days=1 if period == "daily" else 7)
    totals = {
        "tenants_total": db.scalar(select(func.count()).select_from(Tenant)) or 0,
        "new_tenants": db.scalar(select(func.count()).select_from(Tenant).where(Tenant.created_at >= start)) or 0,
        "active_subscriptions": db.scalar(select(func.count()).select_from(Subscription).where(Subscription.status == "active")) or 0,
        "open_invoices": db.scalar(select(func.count()).select_from(Invoice).where(Invoice.status == "open")) or 0,
        "revenue_paise": int(db.scalar(select(func.coalesce(func.sum(Invoice.taxable_paise), 0)).where(Invoice.status == "paid", Invoice.paid_at >= start)) or 0),
    }
    return emit_event(db, f"report.{period}", {"period_start": start, "period_end": now, "totals": totals}, idempotency_key=f"report:{period}:{now.date()}")


def run_scheduler(db: Session, now: datetime | None = None) -> dict[str, int]:
    r = {
        "invoice_due": emit_invoice_due_reminders(db, now),
        "renewal": emit_renewal_reminders(db, now),
        "admin_approval": emit_admin_approval_reminders(db, now),
    }
    db.commit()
    return r
