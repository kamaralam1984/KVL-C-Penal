from typing import Any

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.db import get_db
from app.core.deps import Ctx, api_error, client_ip, require
from app.models.tenant import PlatformSetting
from app.seed import DEFAULT_SETTINGS
from app.services.audit import audit

router = APIRouter(prefix="/settings", tags=["settings"])

# Only these keys are editable, each with a strict value schema (business rules, not free-form config).
INT_KEYS = {"trial_days": "days", "grace_days": "days", "retention_days": "days"}


class SettingIn(BaseModel):
    value: dict[str, Any] = Field(default_factory=dict)

    @field_validator("value")
    @classmethod
    def small(cls, v: dict[str, Any]) -> dict[str, Any]:
        if len(v) > 20:
            raise ValueError("too many fields")
        return v


@router.get("")
def list_settings(ctx: Ctx = Depends(require("settings:read")), db: Session = Depends(get_db)):
    if not ctx.is_platform:
        raise api_error(403, "forbidden", "Platform staff only.")
    rows = {s.key: s for s in db.scalars(select(PlatformSetting))}
    return [{"key": k, "value": rows[k].value if k in rows else v, "updated_at": rows[k].updated_at if k in rows else None} for k, v in DEFAULT_SETTINGS.items()]


@router.put("/{key}")
def update_setting(key: str, body: SettingIn, request: Request, ctx: Ctx = Depends(require("settings:write")), db: Session = Depends(get_db)):
    if not ctx.is_platform:
        raise api_error(403, "forbidden", "Platform staff only.")
    if key not in DEFAULT_SETTINGS:
        raise api_error(404, "not_found", "Unknown setting.")
    v = body.value
    if key in INT_KEYS:
        f = INT_KEYS[key]
        if set(v) != {f} or not isinstance(v[f], int) or not 0 <= v[f] <= 3650:
            raise api_error(422, "validation_error", f"{key} must be {{'{f}': 0-3650}}.")
    elif key == "default_quotas":
        allowed = set(DEFAULT_SETTINGS["default_quotas"])
        if not v or set(v) - allowed or any(not isinstance(x, int) or x < 0 or x > 10_000_000 for x in v.values()):
            raise api_error(422, "validation_error", f"default_quotas keys must be within {sorted(allowed)} with non-negative integers.")
    row = db.scalar(select(PlatformSetting).where(PlatformSetting.key == key))
    before = row.value if row else DEFAULT_SETTINGS[key]
    if row:
        row.value = v
    else:
        db.add(PlatformSetting(key=key, value=v))
    audit(db, "settings.changed", actor=ctx.user.id, target_type="setting", target_id=key, detail={"before": before, "after": v}, ip=client_ip(request))
    db.commit()
    return {"status": "ok"}


# ---- Outgoing mail (SMTP) status + test. Credentials live in the server environment, never in the DB or API.
class MailTestIn(BaseModel):
    to: str | None = Field(default=None, max_length=320)


@router.get("/mail")
def mail_status(ctx: Ctx = Depends(require("settings:read")), db: Session = Depends(get_db)):
    if not ctx.is_platform:
        raise api_error(403, "forbidden", "Platform staff only.")
    from app.services.mail import outbox_stats, transport_state

    return {"transport": transport_state(), "outbox": outbox_stats(db)}


@router.post("/mail/test")
def mail_test(body: MailTestIn, request: Request, ctx: Ctx = Depends(require("settings:write")), db: Session = Depends(get_db)):
    """Queue a test message (default: to the caller) and try to deliver it right away."""
    if not ctx.is_platform:
        raise api_error(403, "forbidden", "Platform staff only.")
    from email.utils import parseaddr

    from app.models.identity import EmailOutbox
    from app.services.mail import deliver_due, queue_email, transport_state

    st = transport_state()
    if st["state"] != "configured":
        raise api_error(409, st["state"], st["message"])
    to = (body.to or ctx.user.email).strip().lower()
    if parseaddr(to)[1] != to or to.count("@") != 1:
        raise api_error(422, "validation_error", "Invalid recipient address.")
    row = queue_email(db, to, "KVL Panel: SMTP test", "This is a test message from KVL Panel. If you received it, outgoing mail works.", "smtp_test")
    db.flush()
    result = deliver_due(db)
    audit(db, "settings.mail_test", actor=ctx.user.id, target_type="email", target_id=str(row.id), detail={"to": to, "result": result}, ip=client_ip(request))
    db.commit()
    fresh = db.get(EmailOutbox, row.id)
    return {"status": fresh.status if fresh else "unknown", "error": fresh.last_error if fresh else None, "run": result}
