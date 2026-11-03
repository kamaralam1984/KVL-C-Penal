import uuid

from sqlalchemy.orm import Session

from app.models.identity import EmailOutbox


def queue_email(db: Session, to: str, subject: str, body: str, kind: str, tenant_id: uuid.UUID | None = None) -> EmailOutbox:
    """Queue a message. No SMTP transport is configured in dev, so rows stay 'queued' and are visible in the dev outbox."""
    row = EmailOutbox(to_email=to.lower(), subject=subject, body=body, kind=kind, tenant_id=tenant_id)
    db.add(row)
    return row
