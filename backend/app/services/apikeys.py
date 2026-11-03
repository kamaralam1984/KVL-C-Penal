"""Scoped API keys: `kvl_<lookup>_<secret>`. Only sha256(secret) is stored; comparison is constant-time.

A key acts as its creator's membership, narrowed to its scopes. Effective permissions = role permissions (now) ∩ scopes, so a
key can never exceed its creator, even if the creator later loses permissions. Tenant-bound keys cannot leave their tenant.
"""
import hashlib
import hmac
import secrets
import uuid
from datetime import UTC, datetime, timedelta

from fastapi import HTTPException, Request
from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.core import ratelimit
from app.core.deps import Ctx, build_ctx
from app.models.identity import Membership, User
from app.models.security import ApiKey
from app.models.tenant import Tenant

PREFIX = "kvl_"
_DUMMY = hashlib.sha256(b"dummy").hexdigest()


def _hash(secret: str) -> str:
    return hashlib.sha256(secret.encode()).hexdigest()


def generate() -> tuple[str, str, str]:
    """Returns (full token shown once, lookup, secret_hash). 256-bit secret."""
    lookup = secrets.token_hex(6)
    secret = secrets.token_urlsafe(32)
    return f"{PREFIX}{lookup}_{secret}", lookup, _hash(secret)


def display_prefix(k: ApiKey) -> str:
    return f"{PREFIX}{k.lookup}"


def _utcnow() -> datetime:
    return datetime.now(UTC)


def authenticate(db: Session, token: str, request: Request) -> Ctx | None:
    """Resolve a bearer key to a Ctx, or None. Raises 429 when the key (or the caller's failures) exceed their limits."""
    ip = request.client.host if request.client else "unknown"
    parts = token.split("_", 2)
    key = None
    secret = ""
    if len(parts) == 3 and parts[0] == "kvl" and 6 <= len(parts[1]) <= 16 and parts[2]:
        key = db.scalar(select(ApiKey).where(ApiKey.lookup == parts[1]))
        secret = parts[2]
    ok = hmac.compare_digest(key.secret_hash if key else _DUMMY, _hash(secret))
    if key is None or not ok:
        ratelimit.enforce(f"apikey_fail:{ip}", 30, 60)  # slow down guessing
        return None
    now = _utcnow()
    if key.revoked_at or (key.expires_at and key.expires_at <= now):
        return None
    user = db.get(User, key.user_id)
    if not user or user.status != "active":
        return None
    membership = db.get(Membership, key.membership_id) if key.membership_id else None
    if membership is None or membership.status != "active" or membership.user_id != user.id:
        return None
    try:
        ratelimit.enforce(f"apikey:{key.id}", key.rate_limit_per_minute, 60)
    except HTTPException:
        from app.services.security_events import record_event

        record_event(db, "api_key_rate_limited", "low", f"API key {display_prefix(key)} exceeded its rate limit.", tenant_id=key.tenant_id, user_id=key.user_id, ip=ip,
                     detail={"key_id": str(key.id)}, dedupe_key=f"rl:{key.id}:{now.strftime('%Y%m%d%H')}")
        db.commit()
        raise
    ctx = build_ctx(db, user, None, membership)
    scopes = frozenset((key.scopes or {}).get("scopes", []))
    ctx.permissions = ctx.permissions & scopes
    ctx.api_scopes = scopes
    ctx.via_api_key = True
    ctx.extra["api_key_id"] = str(key.id)
    if key.tenant_id:
        if ctx.tenant_id not in (None, key.tenant_id):
            return None
        ctx.scope = "tenant"  # a tenant-bound key never has platform reach, even when owned by platform staff
        ctx.tenant_id = key.tenant_id
        ctx.tenant_status = db.scalar(select(Tenant.status).where(Tenant.id == key.tenant_id))
    db.execute(update(ApiKey).where(ApiKey.id == key.id).values(request_count=ApiKey.request_count + 1, last_used_at=now, last_used_ip=ip))
    db.commit()
    return ctx


def default_expiry(days: int | None) -> datetime:
    return _utcnow() + timedelta(days=days or 90)


def new_key_id() -> uuid.UUID:
    return uuid.uuid4()
