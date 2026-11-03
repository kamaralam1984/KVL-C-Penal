"""API key management. Keys can only be created/rotated/revoked from a browser session, never with another API key."""
import uuid
from datetime import datetime

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.db import get_db
from app.core.deps import Ctx, api_error, client_ip, require
from app.core.rbac import ALL_PERMISSIONS
from app.models.identity import User
from app.models.security import ApiKey
from app.models.tenant import Tenant
from app.services import apikeys as svc
from app.services.audit import audit

router = APIRouter(prefix="/apikeys", tags=["api-keys"])
MAX_ACTIVE_KEYS = 20


def _no_key_auth(ctx: Ctx) -> None:
    if ctx.via_api_key:
        raise api_error(403, "session_required", "API keys cannot be managed with an API key.")


def _dto(k: ApiKey, owner: User | None = None) -> dict:
    now = datetime.now(k.created_at.tzinfo)
    status = "revoked" if k.revoked_at else "expired" if k.expires_at and k.expires_at <= now else "active"
    out = {"id": str(k.id), "name": k.name, "prefix": svc.display_prefix(k), "scopes": (k.scopes or {}).get("scopes", []), "tenant_id": str(k.tenant_id) if k.tenant_id else None,
           "status": status, "expires_at": k.expires_at, "last_used_at": k.last_used_at, "last_used_ip": k.last_used_ip, "revoked_at": k.revoked_at,
           "rate_limit_per_minute": k.rate_limit_per_minute, "request_count": k.request_count, "created_at": k.created_at, "user_id": str(k.user_id)}
    if owner:
        out["owner_email"] = owner.email
    return out


class KeyIn(BaseModel):
    name: str = Field(min_length=2, max_length=100)
    scopes: list[str] = Field(min_length=1, max_length=60)
    expires_in_days: int | None = Field(default=None, ge=1, le=365)
    rate_limit_per_minute: int = Field(default=60, ge=1, le=600)
    tenant_id: uuid.UUID | None = None


def _validate_scopes(ctx: Ctx, scopes: list[str]) -> list[str]:
    uniq = sorted(set(scopes))
    unknown = [s for s in uniq if s not in ALL_PERMISSIONS]
    if unknown:
        raise api_error(422, "unknown_scope", f"Unknown scopes: {', '.join(unknown)}")
    forbidden = [s for s in uniq if s.startswith(("apikeys:", "roles:", "staff:")) or s not in ctx.permissions]
    if forbidden:
        raise api_error(403, "scope_exceeds_permissions", f"You cannot grant scopes you do not hold or that are not delegable: {', '.join(forbidden)}")
    return uniq


@router.get("")
def list_keys(tenant_id: uuid.UUID | None = None, ctx: Ctx = Depends(require("apikeys:read")), db: Session = Depends(get_db)):
    stmt = select(ApiKey, User).join(User, User.id == ApiKey.user_id).order_by(ApiKey.created_at.desc()).limit(200)
    if ctx.is_platform and ctx.can("apikeys:manage"):
        if tenant_id:
            stmt = stmt.where(ApiKey.tenant_id == tenant_id)
        return [_dto(k, u) for k, u in db.execute(stmt)]
    return [_dto(k) for k, _ in db.execute(stmt.where(ApiKey.user_id == ctx.user.id))]


@router.post("", status_code=201)
def create_key(body: KeyIn, request: Request, ctx: Ctx = Depends(require("apikeys:write")), db: Session = Depends(get_db)):
    _no_key_auth(ctx)
    if ctx.membership is None:
        raise api_error(403, "forbidden", "No active membership.")
    scopes = _validate_scopes(ctx, body.scopes)
    if ctx.is_platform:
        tenant_id = body.tenant_id
        if tenant_id and not db.get(Tenant, tenant_id):
            raise api_error(404, "not_found", "Tenant not found.")
    else:
        if body.tenant_id not in (None, ctx.tenant_id):
            raise api_error(404, "not_found", "Resource not found.")
        tenant_id = ctx.tenant_id
    active = db.scalar(select(func.count()).select_from(ApiKey).where(ApiKey.user_id == ctx.user.id, ApiKey.revoked_at.is_(None)))
    if active >= MAX_ACTIVE_KEYS:
        raise api_error(409, "key_limit", f"You can hold at most {MAX_ACTIVE_KEYS} active keys.")
    token, lookup, h = svc.generate()
    k = ApiKey(lookup=lookup, secret_hash=h, name=body.name.strip(), user_id=ctx.user.id, membership_id=ctx.membership.id, tenant_id=tenant_id, scopes={"scopes": scopes},
               expires_at=svc.default_expiry(body.expires_in_days), rate_limit_per_minute=body.rate_limit_per_minute)
    db.add(k)
    db.flush()
    audit(db, "apikey.created", actor=ctx.user.id, tenant_id=tenant_id, target_type="api_key", target_id=k.id, detail={"scopes": scopes, "prefix": svc.display_prefix(k)}, ip=client_ip(request))
    db.commit()
    return {**_dto(k), "key": token, "notice": "Copy this key now. It is shown only once and cannot be retrieved later."}


def _own_or_managed(db: Session, ctx: Ctx, key_id: uuid.UUID, *, allow_manage: bool) -> ApiKey:
    k = db.get(ApiKey, key_id)
    if not k or (k.user_id != ctx.user.id and not (allow_manage and ctx.is_platform and ctx.can("apikeys:manage"))):
        raise api_error(404, "not_found", "API key not found.")
    return k


@router.post("/{key_id}/rotate", status_code=201)
def rotate_key(key_id: uuid.UUID, request: Request, ctx: Ctx = Depends(require("apikeys:write")), db: Session = Depends(get_db)):
    _no_key_auth(ctx)
    old = _own_or_managed(db, ctx, key_id, allow_manage=False)
    if old.revoked_at:
        raise api_error(409, "revoked", "This key is already revoked.")
    scopes = _validate_scopes(ctx, (old.scopes or {}).get("scopes", []))  # a rotated key can never exceed the owner's current permissions
    token, lookup, h = svc.generate()
    new = ApiKey(lookup=lookup, secret_hash=h, name=old.name, user_id=old.user_id, membership_id=old.membership_id, tenant_id=old.tenant_id, scopes={"scopes": scopes},
                 expires_at=old.expires_at, rate_limit_per_minute=old.rate_limit_per_minute, rotated_from_id=old.id)
    old.revoked_at = datetime.now(old.created_at.tzinfo)
    db.add(new)
    db.flush()
    audit(db, "apikey.rotated", actor=ctx.user.id, tenant_id=old.tenant_id, target_type="api_key", target_id=new.id, detail={"previous": str(old.id)}, ip=client_ip(request))
    db.commit()
    return {**_dto(new), "key": token, "notice": "Copy this key now. The previous key has been revoked."}


@router.delete("/{key_id}")
def revoke_key(key_id: uuid.UUID, request: Request, ctx: Ctx = Depends(require("apikeys:write")), db: Session = Depends(get_db)):
    _no_key_auth(ctx)
    k = _own_or_managed(db, ctx, key_id, allow_manage=True)
    if not k.revoked_at:
        k.revoked_at = datetime.now(k.created_at.tzinfo)
        audit(db, "apikey.revoked", actor=ctx.user.id, tenant_id=k.tenant_id, target_type="api_key", target_id=k.id, ip=client_ip(request))
        db.commit()
    return {"status": "ok"}
