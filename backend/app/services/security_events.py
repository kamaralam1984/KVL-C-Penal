"""Security event records, incident detection from login/audit data, and alert routing."""
import logging
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models.identity import AuditEvent, LoginEvent
from app.models.security import SecurityAlertRoute, SecurityEvent
from app.services.mail import queue_email

log = logging.getLogger("kvl.security")
SEVERITIES = ("low", "medium", "high", "critical")
SURGE_THRESHOLD = 10  # failed logins from one source inside the window
SURGE_WINDOW_MINUTES = 15


def _sev(s: str) -> int:
    return SEVERITIES.index(s)


def mask_email(e: str) -> str:
    local, _, dom = e.partition("@")
    return f"{local[:1]}***@{dom}" if dom else "***"


def record_event(db: Session, kind: str, severity: str, summary: str, *, tenant_id=None, user_id=None, ip: str | None = None,
                 detail: dict[str, Any] | None = None, dedupe_key: str | None = None) -> SecurityEvent | None:
    """Insert an event (idempotent per dedupe_key) and route alerts. Caller commits. Never include secrets in detail."""
    if dedupe_key and db.scalar(select(SecurityEvent.id).where(SecurityEvent.dedupe_key == dedupe_key)):
        return None
    ev = SecurityEvent(kind=kind, severity=severity, summary=summary[:300], tenant_id=tenant_id, user_id=user_id, ip=ip, detail=detail, dedupe_key=dedupe_key)
    db.add(ev)
    try:
        db.flush()
    except IntegrityError:
        db.rollback()
        return None
    log.warning("security_event kind=%s severity=%s", kind, severity)
    route_event(db, ev)
    return ev


def route_event(db: Session, ev: SecurityEvent) -> int:
    """Send the event to every enabled route whose severity/kind filter matches. Returns the number of routes used."""
    from app.services.webhooks import emit_event

    used = 0
    for r in db.scalars(select(SecurityAlertRoute).where(SecurityAlertRoute.enabled.is_(True))):
        kinds = (r.kinds or {}).get("kinds") or []
        if _sev(ev.severity) < _sev(r.min_severity) or (kinds and ev.kind not in kinds):
            continue
        if r.channel == "email" and r.target:
            queue_email(db, r.target, f"[KVL security:{ev.severity}] {ev.kind}", f"{ev.summary}\nEvent id: {ev.id}\nOpen the Security console to acknowledge.", "security_alert")
            used += 1
        elif r.channel == "webhook":
            emit_event(db, "security.alert", {"event_id": str(ev.id), "kind": ev.kind, "severity": ev.severity, "summary": ev.summary})
            used += 1
    ev.routed = used > 0
    return used


def scan_for_incidents(db: Session, at: datetime | None = None) -> dict[str, int]:
    """Derive events from login_events and audit_events. Safe to run repeatedly (deduplicated). Caller commits."""
    at = at or datetime.now(UTC)
    bucket = at.strftime("%Y%m%d%H")
    created = {"failed_login_surge": 0, "refresh_token_reuse": 0, "privilege_escalation_attempt": 0}
    since = at - timedelta(minutes=SURGE_WINDOW_MINUTES)
    for col, label in ((LoginEvent.ip, "ip"), (LoginEvent.email, "account")):
        rows = db.execute(select(col, func.count()).where(LoginEvent.created_at >= since, LoginEvent.outcome != "success").group_by(col).having(func.count() >= SURGE_THRESHOLD)).all()
        for key, n in rows:
            if not key:
                continue
            shown = mask_email(key) if label == "account" else key
            ev = record_event(db, "failed_login_surge", "critical" if n >= SURGE_THRESHOLD * 5 else "high", f"{n} failed sign-ins for {label} {shown} in {SURGE_WINDOW_MINUTES} min",
                              ip=key if label == "ip" else None, detail={"count": n, "by": label}, dedupe_key=f"surge:{label}:{key}:{bucket}")
            created["failed_login_surge"] += bool(ev)
    day = at - timedelta(days=1)
    for a in db.scalars(select(AuditEvent).where(AuditEvent.created_at >= day, AuditEvent.action.in_(("auth.refresh_reuse_detected",)))):
        ev = record_event(db, "refresh_token_reuse", "high", "A rotated refresh token was replayed; the session family was revoked.", user_id=a.actor_user_id, ip=a.ip,
                          detail={"audit_id": str(a.id)}, dedupe_key=f"audit:{a.id}")
        created["refresh_token_reuse"] += bool(ev)
    for a in db.scalars(select(AuditEvent).where(AuditEvent.created_at >= day, AuditEvent.action.like("%escalation%"))):
        ev = record_event(db, "privilege_escalation_attempt", "high", "A privilege-escalation attempt was blocked.", tenant_id=a.tenant_id, user_id=a.actor_user_id, ip=a.ip,
                          detail={"audit_id": str(a.id), "action": a.action}, dedupe_key=f"audit:{a.id}")
        created["privilege_escalation_attempt"] += bool(ev)
    return created
