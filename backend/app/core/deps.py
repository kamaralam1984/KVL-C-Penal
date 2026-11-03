"""Authentication, tenant context and permission enforcement. Backend authorization is authoritative."""
import hmac
import time
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Callable

from fastapi import Depends, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.orm import Session, joinedload

from app.core.db import get_db
from app.core.security import decode_jwt
from app.models.identity import AuthSession, Membership, Permission, Role, RolePermission, User
from app.models.tenant import Tenant

ACCESS_COOKIE = "kvl_at"
REFRESH_COOKIE = "kvl_rt"
CSRF_COOKIE = "kvl_csrf"
WRITE_ACTIONS = {"write", "delete", "manage", "restore"}
SUSPENDED_ALLOWED = {"billing", "tickets", "kb", "announcements"}


def api_error(status: int, code: str, message: str) -> HTTPException:
    return HTTPException(status_code=status, detail={"code": code, "message": message})


@dataclass
class Ctx:
    user: User
    session: AuthSession | None
    membership: Membership | None
    role: str | None
    permissions: frozenset[str]
    tenant_id: uuid.UUID | None = None
    tenant_status: str | None = None
    reseller_id: uuid.UUID | None = None
    scope: str | None = None  # platform | tenant | reseller
    via_api_key: bool = False
    api_scopes: frozenset[str] | None = None
    extra: dict = field(default_factory=dict)

    @property
    def is_platform(self) -> bool:
        return self.scope == "platform"

    def can(self, perm: str) -> bool:
        return perm in self.permissions


# ---- role -> permissions, cached briefly; invalidate_role_cache() after role changes ----
_role_cache: dict[uuid.UUID, tuple[float, frozenset[str]]] = {}


def invalidate_role_cache() -> None:
    _role_cache.clear()


def role_permissions(db: Session, role_id: uuid.UUID) -> frozenset[str]:
    hit = _role_cache.get(role_id)
    if hit and time.time() - hit[0] < 30:
        return hit[1]
    rows = db.scalars(
        select(Permission.code).join(RolePermission, RolePermission.permission_id == Permission.id).where(RolePermission.role_id == role_id)
    ).all()
    perms = frozenset(rows)
    _role_cache[role_id] = (time.time(), perms)
    return perms


def _utcnow() -> datetime:
    return datetime.now(UTC)


def build_ctx(db: Session, user: User, session: AuthSession | None, membership: Membership | None) -> Ctx:
    perms: frozenset[str] = frozenset()
    role = scope = None
    tenant_id = reseller_id = None
    tenant_status = None
    if membership and membership.status == "active" and membership.user_id == user.id:
        r = db.get(Role, membership.role_id)
        if r:
            role, scope = r.name, r.scope
            perms = role_permissions(db, r.id)
        tenant_id, reseller_id = membership.tenant_id, membership.reseller_id
        if tenant_id:
            tenant_status = db.scalar(select(Tenant.status).where(Tenant.id == tenant_id))
    return Ctx(user, session, membership, role, perms, tenant_id, tenant_status, reseller_id, scope)


def get_ctx(request: Request, db: Session = Depends(get_db)) -> Ctx:
    """Resolve the caller from a cookie session or an API key. Raises 401 if unauthenticated."""
    auth = request.headers.get("authorization", "")
    if auth.lower().startswith("bearer kvl_"):
        try:
            from app.services import apikeys  # Phase 17 provides this; absent until then
        except ImportError:
            raise api_error(401, "unauthenticated", "Authentication required.") from None
        ctx = apikeys.authenticate(db, auth[7:].strip(), request)
        if ctx is None:
            raise api_error(401, "unauthenticated", "Invalid API key.")
        request.state.ctx = ctx
        return ctx

    token = request.cookies.get(ACCESS_COOKIE)
    payload = decode_jwt(token, "access") if token else None
    if not payload:
        raise api_error(401, "unauthenticated", "Authentication required.")
    session = db.get(AuthSession, uuid.UUID(payload["sid"])) if payload.get("sid") else None
    if not session or session.revoked_at or session.expires_at < _utcnow() or str(session.user_id) != payload["sub"]:
        raise api_error(401, "session_revoked", "Session is no longer valid.")
    user = db.get(User, session.user_id)
    if not user or user.status != "active":
        raise api_error(401, "unauthenticated", "Account unavailable.")
    membership = None
    if session.active_membership_id:
        membership = db.scalar(
            select(Membership).options(joinedload(Membership.role)).where(Membership.id == session.active_membership_id)
        )
    ctx = build_ctx(db, user, session, membership)
    request.state.ctx = ctx
    return ctx


def require(*perms: str) -> Callable[..., Ctx]:
    """Dependency factory: caller must hold ALL listed permissions (and the tenant must allow the action)."""

    def dep(ctx: Ctx = Depends(get_ctx)) -> Ctx:
        for p in perms:
            if p not in ctx.permissions:
                raise api_error(403, "forbidden", "You do not have permission to perform this action.")
            if ctx.api_scopes is not None and p not in ctx.api_scopes:
                raise api_error(403, "scope_missing", "API key lacks the required scope.")
            resource, _, action = p.partition(":")
            if ctx.tenant_status == "suspended" and action in WRITE_ACTIONS and resource not in SUSPENDED_ALLOWED:
                raise api_error(403, "tenant_suspended", "This account is suspended. Resources are read-only.")
            if ctx.tenant_status == "cancelled" and resource not in {"billing", "tickets"}:
                raise api_error(403, "tenant_cancelled", "This account is cancelled.")
        return ctx

    return dep


def resolve_tenant(ctx: Ctx, requested: uuid.UUID | None = None) -> uuid.UUID | None:
    """Tenant for a request. Tenant users are pinned to their membership; a different id looks like 'not found'."""
    if ctx.is_platform:
        return requested
    if ctx.tenant_id is None:
        raise api_error(403, "no_tenant", "No tenant context for this account.")
    if requested is not None and requested != ctx.tenant_id:
        raise api_error(404, "not_found", "Resource not found.")
    return ctx.tenant_id


def require_tenant(ctx: Ctx, requested: uuid.UUID | None = None) -> uuid.UUID:
    t = resolve_tenant(ctx, requested)
    if t is None:
        raise api_error(400, "tenant_required", "tenant_id is required for this operation.")
    return t


def csrf_ok(request: Request) -> bool:
    if request.method in {"GET", "HEAD", "OPTIONS"}:
        return True
    if not request.cookies.get(ACCESS_COOKIE) and not request.cookies.get(REFRESH_COOKIE):
        return True  # no ambient credentials, nothing to forge
    sent = request.headers.get("x-csrf-token", "")
    cookie = request.cookies.get(CSRF_COOKIE, "")
    return bool(sent) and hmac.compare_digest(sent, cookie)


def client_ip(request: Request) -> str | None:
    return request.client.host if request.client else None
