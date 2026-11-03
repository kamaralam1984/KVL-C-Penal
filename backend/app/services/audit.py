import uuid
from typing import Any

from sqlalchemy.orm import Session

from app.models.identity import AuditEvent

_SENSITIVE = {"password", "token", "secret", "otp", "code", "key", "authorization", "cookie"}


def _scrub(value: Any) -> Any:
    if isinstance(value, dict):
        return {k: ("[redacted]" if any(s in k.lower() for s in _SENSITIVE) else _scrub(v)) for k, v in value.items()}
    if isinstance(value, list):
        return [_scrub(v) for v in value]
    return value


def audit(
    db: Session,
    action: str,
    *,
    actor: uuid.UUID | None = None,
    tenant_id: uuid.UUID | None = None,
    target_type: str | None = None,
    target_id: str | uuid.UUID | None = None,
    detail: dict[str, Any] | None = None,
    ip: str | None = None,
    request_id: str | None = None,
) -> None:
    db.add(
        AuditEvent(
            actor_user_id=actor,
            tenant_id=tenant_id,
            action=action,
            target_type=target_type,
            target_id=str(target_id) if target_id else None,
            detail=_scrub(detail) if detail else None,
            ip=ip,
            request_id=request_id,
        )
    )
