"""Development-only helpers. The router is inert (404) unless ENV is dev/test."""
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.db import get_db
from app.models.identity import EmailOutbox

router = APIRouter(prefix="/dev", tags=["dev"], include_in_schema=False)


@router.get("/outbox")
def outbox(email: str, db: Session = Depends(get_db)):
    if not get_settings().is_dev:
        raise HTTPException(404)
    rows = db.scalars(select(EmailOutbox).where(EmailOutbox.to_email == email.lower()).order_by(EmailOutbox.created_at.desc()).limit(10)).all()
    return [{"kind": r.kind, "subject": r.subject, "body": r.body, "created_at": r.created_at} for r in rows]
