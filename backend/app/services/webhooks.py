"""Webhook events: catalog, signing, SSRF-safe delivery with retry/backoff and dead letter."""
import hashlib
import hmac
import json
import time
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any, Callable

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.core import netsafe
from app.core.config import get_settings
from app.core.security import decrypt_secret, encrypt_secret, new_token
from app.models.security import WebhookDelivery, WebhookEndpoint

EVENT_CATALOG: dict[str, str] = {
    "ping": "Sent by the 'send test event' action.",
    "ticket.created": "A support ticket was opened.",
    "ticket.replied": "A public reply was added to a ticket (internal notes never emit events).",
    "ticket.status_changed": "A ticket changed status.",
    "ticket.sla_breached": "A ticket breached its first-response or resolution SLA (platform endpoints and the owning tenant).",
    "tenant.status_changed": "A tenant account changed lifecycle status.",
    "website.created": "A website was created.",
    "backup.completed": "A backup finished successfully.",
    "backup.failed": "A backup failed.",
    "ssl.expiring": "A certificate is close to expiry.",
    "invoice.paid": "An invoice was paid.",
    "apikey.created": "An API key was created (never includes the secret).",
    "security.alert": "A security incident was raised (platform endpoints only).",
}
SIGNATURE_TOLERANCE_SECONDS = 300
BACKOFF_SECONDS = (60, 300, 1800, 7200, 21600)


def new_secret() -> str:
    return "whsec_" + new_token(32)


def sign_payload(secret: str, timestamp: int, body: str) -> str:
    return hmac.new(secret.encode(), f"{timestamp}.{body}".encode(), hashlib.sha256).hexdigest()


def signature_header(secrets: list[str], timestamp: int, body: str) -> str:
    return f"t={timestamp}," + ",".join(f"v1={sign_payload(s, timestamp, body)}" for s in secrets)


def verify_signature(secret: str, header: str, body: str, *, tolerance: int = SIGNATURE_TOLERANCE_SECONDS, now_ts: float | None = None) -> bool:
    """Receiver-side reference implementation (also used by tests): constant-time compare + replay window."""
    try:
        items = [p.split("=", 1) for p in header.split(",")]
        ts = int(next(v for k, v in items if k == "t"))
        sigs = [v for k, v in items if k == "v1"]
    except (ValueError, StopIteration):
        return False
    if abs((now_ts if now_ts is not None else time.time()) - ts) > tolerance:
        return False
    expected = sign_payload(secret, ts, body)
    return any(hmac.compare_digest(expected, s) for s in sigs)


def validate_events(events: list[str]) -> list[str]:
    if not events:
        raise ValueError("Select at least one event.")
    if "*" in events:
        return ["*"]
    unknown = [e for e in events if e not in EVENT_CATALOG]
    if unknown:
        raise ValueError(f"Unknown events: {', '.join(unknown)}")
    return sorted(set(events))


def _matches(ep: WebhookEndpoint, event_type: str) -> bool:
    evs = (ep.events or {}).get("events", [])
    return "*" in evs or event_type in evs


def emit_event(db: Session, event_type: str, data: dict[str, Any], *, tenant_id: uuid.UUID | None = None, endpoint_ids: set[uuid.UUID] | None = None) -> list[WebhookDelivery]:
    """Queue a delivery for each matching active endpoint: the event's tenant's endpoints and platform-level endpoints.
    `security.alert` goes to platform endpoints only. Does not send anything; `deliver_due` does."""
    if event_type not in EVENT_CATALOG:
        raise ValueError(f"unknown event {event_type}")
    stmt = select(WebhookEndpoint).where(WebhookEndpoint.status == "active")
    if event_type == "security.alert":
        stmt = stmt.where(WebhookEndpoint.tenant_id.is_(None))
    elif tenant_id is not None:
        stmt = stmt.where(or_(WebhookEndpoint.tenant_id == tenant_id, WebhookEndpoint.tenant_id.is_(None)))
    else:
        stmt = stmt.where(WebhookEndpoint.tenant_id.is_(None))
    out = []
    evt_id = "evt_" + uuid.uuid4().hex[:24]
    envelope = {"id": evt_id, "type": event_type, "created_at": datetime.now(UTC).isoformat(), "tenant_id": str(tenant_id) if tenant_id else None, "data": data}
    for ep in db.scalars(stmt):
        if endpoint_ids is not None and ep.id not in endpoint_ids:
            continue
        if not _matches(ep, event_type) and event_type != "ping":
            continue
        d = WebhookDelivery(endpoint_id=ep.id, tenant_id=ep.tenant_id, event_type=event_type, event_id=evt_id, payload=envelope,
                            status="pending", next_attempt_at=datetime.now(UTC))
        db.add(d)
        out.append(d)
    return out


# ---- transport (replaceable in tests). Returns the HTTP status code. Never follows redirects. ----
Transport = Callable[[str, int, str, str, dict[str, str], bytes, float], int]


def http_transport(host: str, port: int, path: str, ip: str, headers: dict[str, str], body: bytes, timeout: float) -> int:
    """POST to the already-validated IP so a second DNS lookup cannot be rebound; TLS SNI/verification use the hostname."""
    import httpx

    target_host = f"[{ip}]" if ":" in ip else ip
    h = {**headers, "Host": host if port == 443 else f"{host}:{port}"}
    with httpx.Client(timeout=timeout, follow_redirects=False, verify=True, trust_env=False) as c:
        r = c.post(f"https://{target_host}:{port}{path}", content=body, headers=h, extensions={"sni_hostname": host})
    return r.status_code


transport: Transport = http_transport


def _secrets_for(ep: WebhookEndpoint, at: datetime) -> list[str]:
    out = []
    if (cur := decrypt_secret(ep.secret_enc)):
        out.append(cur)
    if ep.prev_secret_enc and ep.prev_secret_expires_at and ep.prev_secret_expires_at > at and (prev := decrypt_secret(ep.prev_secret_enc)):
        out.append(prev)
    return out


def attempt_delivery(db: Session, d: WebhookDelivery, at: datetime | None = None) -> str:
    at = at or datetime.now(UTC)
    s = get_settings()
    ep = db.get(WebhookEndpoint, d.endpoint_id)
    d.attempts += 1
    err = None
    code = None
    if ep is None or ep.status != "active":
        err = "endpoint_disabled"
    else:
        try:
            host, port, path, ips = netsafe.validate_public_url(ep.url)  # re-resolved on every attempt
            body = json.dumps(d.payload, separators=(",", ":"), sort_keys=True, default=str)
            ts = int(at.timestamp())
            headers = {
                "Content-Type": "application/json", "User-Agent": "KVL-Webhooks/1",
                "X-KVL-Event": d.event_type, "X-KVL-Event-Id": d.event_id, "X-KVL-Delivery": str(d.id),
                "X-KVL-Signature": signature_header(_secrets_for(ep, at), ts, body),
            }
            code = transport(host, port, path, ips[0], headers, body.encode(), s.webhook_timeout_seconds)
            if 200 <= code < 300:
                d.status, d.delivered_at, d.last_status_code, d.last_error, d.next_attempt_at = "succeeded", at, code, None, None
                return "succeeded"
            err = "redirect_not_followed" if 300 <= code < 400 else f"http_{code}"
        except netsafe.UnsafeURL as e:
            err = f"blocked: {e}"[:300]
        except Exception as e:  # network errors, TLS failures, timeouts
            err = f"{type(e).__name__}"[:300]
    d.last_status_code, d.last_error = code, err
    if d.attempts >= s.webhook_max_attempts or (err or "").startswith("blocked"):
        d.status, d.next_attempt_at = "dead", None
        return "dead"
    d.status = "retrying"
    d.next_attempt_at = at + timedelta(seconds=BACKOFF_SECONDS[min(d.attempts - 1, len(BACKOFF_SECONDS) - 1)])
    return "retrying"


def deliver_due(db: Session, at: datetime | None = None, limit: int = 100) -> dict[str, int]:
    """Process due deliveries. Call from a periodic task (see POST /webhooks/dispatch). Caller commits."""
    at = at or datetime.now(UTC)
    rows = db.scalars(
        select(WebhookDelivery).where(WebhookDelivery.status.in_(("pending", "retrying")), WebhookDelivery.next_attempt_at <= at)
        .order_by(WebhookDelivery.next_attempt_at).limit(limit).with_for_update(skip_locked=True)
    ).all()
    counts = {"succeeded": 0, "retrying": 0, "dead": 0}
    for d in rows:
        counts[attempt_delivery(db, d, at)] += 1
    return counts
