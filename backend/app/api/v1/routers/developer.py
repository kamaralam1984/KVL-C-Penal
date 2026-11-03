"""Developer platform catalogs: API scopes, webhook events, usage."""
from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.db import get_db
from app.core.deps import Ctx, get_ctx
from app.core.rbac import ALL_PERMISSIONS, RESOURCES
from app.models.security import ApiKey
from app.services.webhooks import EVENT_CATALOG, SIGNATURE_TOLERANCE_SECONDS

router = APIRouter(prefix="/developer", tags=["developer"])

NON_DELEGABLE = ("apikeys:", "roles:", "staff:")


@router.get("/scopes")
def scopes(ctx: Ctx = Depends(get_ctx)):
    """Every permission code is a possible scope. `grantable` is true only for scopes the caller holds and may delegate."""
    return [{"scope": p, "resource": p.partition(":")[0], "action": p.partition(":")[2], "grantable": p in ctx.permissions and not p.startswith(NON_DELEGABLE)} for p in ALL_PERMISSIONS]


@router.get("/events")
def events(_: Ctx = Depends(get_ctx)):
    return {"events": [{"type": k, "description": v} for k, v in EVENT_CATALOG.items()],
            "signature": {"header": "X-KVL-Signature", "format": "t=<unix>,v1=<hex hmac-sha256 of '<t>.<raw body>'>", "tolerance_seconds": SIGNATURE_TOLERANCE_SECONDS}}


@router.get("/usage")
def usage(ctx: Ctx = Depends(get_ctx), db: Session = Depends(get_db)):
    rows = db.scalars(select(ApiKey).where(ApiKey.user_id == ctx.user.id).order_by(ApiKey.created_at.desc()).limit(50)).all()
    return {"keys": [{"id": str(k.id), "name": k.name, "requests_total": k.request_count, "rate_limit_per_minute": k.rate_limit_per_minute, "last_used_at": k.last_used_at,
                      "revoked": bool(k.revoked_at)} for k in rows],
            "limits": {"max_active_keys": 20, "default_rate_limit_per_minute": 60, "max_rate_limit_per_minute": 600, "max_webhook_endpoints": 10},
            "resources": list(RESOURCES)}
