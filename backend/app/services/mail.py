"""Outgoing mail: queue in email_outbox, deliver via SMTP from the scheduler.

Honest integration: without SMTP_HOST/SMTP_FROM the transport reports ``not_configured`` and rows stay
``queued`` (visible in the dev outbox). Nothing is marked ``sent`` unless the SMTP server accepted it.
"""
import smtplib
import ssl
import uuid
from datetime import datetime, timedelta, timezone
from email.message import EmailMessage
from email.utils import formatdate, make_msgid, parseaddr
from typing import Any

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.core.config import Settings, get_settings
from app.models.identity import EmailOutbox
from app.services.redact import redact_text

LOCAL_RELAYS = {"127.0.0.1", "localhost", "::1"}


def queue_email(db: Session, to: str, subject: str, body: str, kind: str, tenant_id: uuid.UUID | None = None) -> EmailOutbox:
    """Queue a message; the scheduler's mail task delivers it when SMTP is configured."""
    row = EmailOutbox(to_email=to.lower(), subject=subject, body=body, kind=kind, tenant_id=tenant_id)
    db.add(row)
    return row


def _now() -> datetime:
    return datetime.now(timezone.utc)


def transport_state(s: Settings | None = None) -> dict[str, Any]:
    s = s or get_settings()
    if not s.smtp_configured:
        return {"state": "not_configured", "message": "SMTP_HOST and SMTP_FROM are not set; mail stays queued."}
    if s.smtp_security not in {"starttls", "ssl", "none"}:
        return {"state": "misconfigured", "message": "SMTP_SECURITY must be starttls, ssl or none."}
    if s.smtp_security == "none" and s.smtp_host not in LOCAL_RELAYS:
        return {"state": "misconfigured", "message": "Unencrypted SMTP is only allowed to a local relay."}
    if parseaddr(s.smtp_from)[1].count("@") != 1:
        return {"state": "misconfigured", "message": "SMTP_FROM is not a valid address."}
    return {"state": "configured", "host": s.smtp_host, "port": s.smtp_port, "security": s.smtp_security,
            "from": s.smtp_from, "auth": bool(s.smtp_username)}


def _connect(s: Settings) -> smtplib.SMTP:
    ctx = ssl.create_default_context()
    if s.smtp_security == "ssl":
        conn: smtplib.SMTP = smtplib.SMTP_SSL(s.smtp_host, s.smtp_port, timeout=s.smtp_timeout_seconds, context=ctx)
    else:
        conn = smtplib.SMTP(s.smtp_host, s.smtp_port, timeout=s.smtp_timeout_seconds)
        conn.ehlo()
        if s.smtp_security == "starttls":
            conn.starttls(context=ctx)
            conn.ehlo()
    if s.smtp_username:
        conn.login(s.smtp_username, s.smtp_password)
    return conn


def _message(s: Settings, row: EmailOutbox) -> EmailMessage:
    m = EmailMessage()
    m["From"] = s.smtp_from
    m["To"] = row.to_email
    m["Subject"] = row.subject.replace("\r", " ").replace("\n", " ")[:300]
    m["Date"] = formatdate(localtime=False)
    m["Message-ID"] = make_msgid(domain=parseaddr(s.smtp_from)[1].split("@")[-1] or None)
    m["X-KVL-Kind"] = row.kind
    m.set_content(row.body)
    return m


def _backoff(attempts: int) -> timedelta:
    return timedelta(minutes=min(2 ** attempts, 240))


def _err(e: BaseException, s: Settings, prefix: str = "") -> str:
    return redact_text(f"{prefix}{type(e).__name__}: {e}", [s.smtp_password])[:300]


def deliver_due(db: Session, now: datetime | None = None, settings: Settings | None = None) -> dict[str, Any]:
    """Scheduler task: send due queued mail. One connection per run; per-message failures are retried with backoff."""
    s = settings or get_settings()
    st = transport_state(s)
    if st["state"] != "configured":
        return {"status": st["state"], "sent": 0, "failed": 0, "retry": 0}
    now = now or _now()
    rows = db.scalars(
        select(EmailOutbox)
        .where(EmailOutbox.status == "queued", or_(EmailOutbox.next_attempt_at.is_(None), EmailOutbox.next_attempt_at <= now))
        .order_by(EmailOutbox.created_at)
        .limit(s.smtp_batch_size)
        .with_for_update(skip_locked=True)
    ).all()
    out: dict[str, Any] = {"status": "ok", "sent": 0, "failed": 0, "retry": 0}
    if not rows:
        return out
    try:
        conn = _connect(s)
    except (OSError, smtplib.SMTPException) as e:
        err = _err(e, s, "connect: ")
        for r in rows:
            _fail(r, err, now, s, out)
        return out
    try:
        for r in rows:
            try:
                conn.send_message(_message(s, r))
                r.attempts += 1
                r.status, r.sent_at, r.last_error, r.next_attempt_at = "sent", now, None, None
                out["sent"] += 1
            except smtplib.SMTPRecipientsRefused as e:  # permanent for this address
                r.attempts += 1
                r.status, r.last_error = "failed", _err(e, s, "recipient refused: ")
                out["failed"] += 1
            except (OSError, smtplib.SMTPException) as e:
                _fail(r, _err(e, s), now, s, out)
    finally:
        try:
            conn.quit()
        except (OSError, smtplib.SMTPException):
            pass
    return out


def _fail(r: EmailOutbox, err: str, now: datetime, s: Settings, out: dict[str, Any]) -> None:
    r.attempts += 1
    r.last_error = err
    if r.attempts >= s.smtp_max_attempts:
        r.status = "failed"
        out["failed"] += 1
    else:
        r.next_attempt_at = now + _backoff(r.attempts)
        out["retry"] += 1


def outbox_stats(db: Session) -> dict[str, int]:
    rows = db.execute(select(EmailOutbox.status, func.count()).group_by(EmailOutbox.status)).all()
    return {str(k): int(v) for k, v in rows}
