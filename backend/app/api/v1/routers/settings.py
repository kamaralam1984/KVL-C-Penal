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
