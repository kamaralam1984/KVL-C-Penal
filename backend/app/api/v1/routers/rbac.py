import uuid
from datetime import timedelta

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from app.core.db import get_db
from app.core.deps import Ctx, api_error, client_ip, invalidate_role_cache, require
from app.core.rbac import ALL_PERMISSIONS, PRIVILEGED_ROLES
from app.core.security import new_token, sha256
from app.models.identity import Invitation, Membership, Permission, Role, RolePermission, User
from app.models.tenant import Tenant
from app.services import auth as svc
from app.services.audit import audit
from app.services.mail import queue_email

router = APIRouter(tags=["rbac"])


@router.get("/rbac/permissions")
def permissions(_: Ctx = Depends(require("roles:read"))):
    return ALL_PERMISSIONS


@router.get("/rbac/roles")
def roles(ctx: Ctx = Depends(require("roles:read")), db: Session = Depends(get_db)):
    out = []
    for r in db.scalars(select(Role).order_by(Role.name)):
        codes = db.scalars(select(Permission.code).join(RolePermission, RolePermission.permission_id == Permission.id).where(RolePermission.role_id == r.id).order_by(Permission.code)).all()
        out.append({"name": r.name, "label": r.label, "scope": r.scope, "privileged": r.privileged, "permissions": codes})
    return out


class RolePermsIn(BaseModel):
    permissions: list[str]


@router.put("/rbac/roles/{name}/permissions")
def set_role_permissions(name: str, body: RolePermsIn, request: Request, ctx: Ctx = Depends(require("roles:manage")), db: Session = Depends(get_db)):
    if ctx.role != "super_admin":
        raise api_error(403, "forbidden", "Only the platform owner can change role permissions.")
    role = db.scalar(select(Role).where(Role.name == name))
    if not role:
        raise api_error(404, "not_found", "Role not found.")
    if name == "super_admin":
        raise api_error(409, "immutable_role", "The super admin role is immutable.")
    unknown = [p for p in body.permissions if p not in ALL_PERMISSIONS]
    if unknown:
        raise api_error(422, "unknown_permission", f"Unknown permissions: {', '.join(unknown)}")
    if role.scope != "platform" and any(p.startswith(("roles:", "security:", "staff:")) for p in body.permissions):
        raise api_error(422, "scope_violation", "Tenant and reseller roles cannot receive platform administration permissions.")
    perms = {p.code: p for p in db.scalars(select(Permission))}
    before = set(db.scalars(select(Permission.code).join(RolePermission, RolePermission.permission_id == Permission.id).where(RolePermission.role_id == role.id)))
    db.execute(delete(RolePermission).where(RolePermission.role_id == role.id))
    for code in set(body.permissions):
        db.add(RolePermission(role_id=role.id, permission_id=perms[code].id))
    after = set(body.permissions)
    audit(db, "rbac.role_permissions_changed", actor=ctx.user.id, target_type="role", target_id=name, detail={"added": sorted(after - before), "removed": sorted(before - after)}, ip=client_ip(request))
    db.commit()
    invalidate_role_cache()
    return {"status": "ok"}


# ---------------- users + role assignment ----------------
@router.get("/users")
def list_users(q: str | None = None, limit: int = 50, offset: int = 0, ctx: Ctx = Depends(require("users:read")), db: Session = Depends(get_db)):
    stmt = select(User).order_by(User.created_at.desc()).limit(min(limit, 100)).offset(offset)
    if q:
        stmt = stmt.where(User.email.ilike(f"%{q.lower()}%") | User.name.ilike(f"%{q}%"))
    if not ctx.is_platform:
        raise api_error(403, "forbidden", "Platform staff only.")
    rows = db.scalars(stmt).all()
    return [{"id": str(u.id), "email": u.email, "name": u.name, "status": u.status, "email_verified": bool(u.email_verified_at), "totp_enabled": u.totp_enabled, "created_at": u.created_at} for u in rows]


class StatusIn(BaseModel):
    status: str = Field(pattern="^(active|disabled)$")


@router.patch("/users/{user_id}")
def set_user_status(user_id: uuid.UUID, body: StatusIn, request: Request, ctx: Ctx = Depends(require("users:write")), db: Session = Depends(get_db)):
    if user_id == ctx.user.id:
        raise api_error(409, "self_action", "You cannot change your own account status.")
    u = db.get(User, user_id)
    if not u:
        raise api_error(404, "not_found", "User not found.")
    privileged = db.scalar(select(func.count()).select_from(Membership).join(Role, Role.id == Membership.role_id).where(Membership.user_id == u.id, Role.name.in_(PRIVILEGED_ROLES)))
    if privileged and ctx.role != "super_admin":
        raise api_error(403, "forbidden", "Only the platform owner can change privileged accounts.")
    u.status = body.status
    if body.status == "disabled":
        from sqlalchemy import update
        from app.models.identity import AuthSession
        db.execute(update(AuthSession).where(AuthSession.user_id == u.id, AuthSession.revoked_at.is_(None)).values(revoked_at=svc.now()))
    audit(db, f"users.{body.status}", actor=ctx.user.id, target_type="user", target_id=u.id, ip=client_ip(request))
    db.commit()
    return {"status": "ok"}


class AssignIn(BaseModel):
    user_id: uuid.UUID
    role: str
    tenant_id: uuid.UUID | None = None


@router.post("/rbac/memberships", status_code=201)
def assign_role(body: AssignIn, request: Request, ctx: Ctx = Depends(require("members:write")), db: Session = Depends(get_db)):
    role = db.scalar(select(Role).where(Role.name == body.role))
    if not role:
        raise api_error(404, "not_found", "Role not found.")
    if body.user_id == ctx.user.id:
        raise api_error(403, "self_assignment", "You cannot assign roles to yourself.")
    if role.privileged and ctx.role != "super_admin":
        raise api_error(403, "privilege_escalation", "Only the platform owner can grant privileged roles.")
    if role.scope == "platform" and not ctx.is_platform:
        raise api_error(403, "privilege_escalation", "Platform roles can only be granted by platform staff.")
    if role.scope == "tenant":
        tenant_id = body.tenant_id if ctx.is_platform else ctx.tenant_id
        if tenant_id is None or (not ctx.is_platform and body.tenant_id not in (None, ctx.tenant_id)):
            raise api_error(404, "not_found", "Tenant not found.")
        if not db.get(Tenant, tenant_id):
            raise api_error(404, "not_found", "Tenant not found.")
    else:
        tenant_id = None
    if not db.get(User, body.user_id):
        raise api_error(404, "not_found", "User not found.")
    exists = db.scalar(select(Membership.id).where(Membership.user_id == body.user_id, Membership.role_id == role.id, Membership.tenant_id.is_(tenant_id) if tenant_id is None else Membership.tenant_id == tenant_id))
    if exists:
        raise api_error(409, "already_member", "User already has this role.")
    m = Membership(user_id=body.user_id, role_id=role.id, tenant_id=tenant_id)
    db.add(m)
    db.flush()
    audit(db, "rbac.role_assigned", actor=ctx.user.id, tenant_id=tenant_id, target_type="membership", target_id=m.id, detail={"role": role.name, "user_id": str(body.user_id)}, ip=client_ip(request))
    db.commit()
    return {"id": str(m.id)}


@router.delete("/rbac/memberships/{membership_id}")
def remove_role(membership_id: uuid.UUID, request: Request, ctx: Ctx = Depends(require("members:delete")), db: Session = Depends(get_db)):
    m = db.get(Membership, membership_id)
    if not m or (not ctx.is_platform and m.tenant_id != ctx.tenant_id):
        raise api_error(404, "not_found", "Membership not found.")
    role = db.get(Role, m.role_id)
    if m.user_id == ctx.user.id:
        raise api_error(403, "self_assignment", "You cannot remove your own role.")
    if role.privileged and ctx.role != "super_admin":
        raise api_error(403, "privilege_escalation", "Only the platform owner can remove privileged roles.")
    if role.name == "super_admin":
        remaining = db.scalar(select(func.count()).select_from(Membership).where(Membership.role_id == role.id, Membership.status == "active"))
        if remaining <= 1:
            raise api_error(409, "last_owner", "Cannot remove the last platform owner.")
    db.delete(m)
    audit(db, "rbac.role_removed", actor=ctx.user.id, tenant_id=m.tenant_id, target_type="membership", target_id=membership_id, detail={"role": role.name}, ip=client_ip(request))
    db.commit()
    return {"status": "ok"}


# ---------------- staff / member invitations ----------------
class InviteIn(BaseModel):
    email: EmailStr
    role: str
    tenant_id: uuid.UUID | None = None


@router.post("/staff/invitations", status_code=201)
def invite(body: InviteIn, request: Request, ctx: Ctx = Depends(require("staff:write")), db: Session = Depends(get_db)):
    role = db.scalar(select(Role).where(Role.name == body.role))
    if not role:
        raise api_error(404, "not_found", "Role not found.")
    if role.privileged and ctx.role != "super_admin":
        raise api_error(403, "privilege_escalation", "Only the platform owner can invite privileged roles.")
    if role.scope == "platform" and not ctx.is_platform:
        raise api_error(403, "privilege_escalation", "Platform roles can only be invited by platform staff.")
    tenant_id = None
    if role.scope == "tenant":
        tenant_id = body.tenant_id if ctx.is_platform else ctx.tenant_id
        if tenant_id is None:
            raise api_error(422, "tenant_required", "tenant_id is required for tenant roles.")
    email = svc.norm_email(body.email)
    token = new_token(32)
    inv = Invitation(email=email, role_id=role.id, tenant_id=tenant_id, invited_by=ctx.user.id, token_hash=sha256(token), expires_at=svc.now() + timedelta(days=7))
    db.add(inv)
    db.flush()
    link = f"/accept-invite?token={token}"
    queue_email(db, email, "You're invited to KVL Panel", f"Accept your invitation: {link} (valid 7 days).", "invitation", tenant_id)
    audit(db, "staff.invited", actor=ctx.user.id, tenant_id=tenant_id, target_type="invitation", target_id=inv.id, detail={"role": role.name, "email": email}, ip=client_ip(request))
    db.commit()
    return {"id": str(inv.id), "expires_at": inv.expires_at}


@router.get("/staff/invitations")
def list_invitations(ctx: Ctx = Depends(require("staff:read")), db: Session = Depends(get_db)):
    stmt = select(Invitation).order_by(Invitation.created_at.desc()).limit(100)
    if not ctx.is_platform:
        stmt = stmt.where(Invitation.tenant_id == ctx.tenant_id)
    roles = {r.id: r.name for r in db.scalars(select(Role))}
    return [{"id": str(i.id), "email": i.email, "role": roles[i.role_id], "tenant_id": str(i.tenant_id) if i.tenant_id else None, "expires_at": i.expires_at, "accepted": bool(i.accepted_at), "revoked": bool(i.revoked_at)} for i in db.scalars(stmt)]


@router.delete("/staff/invitations/{invitation_id}")
def revoke_invitation(invitation_id: uuid.UUID, ctx: Ctx = Depends(require("staff:write")), db: Session = Depends(get_db)):
    inv = db.get(Invitation, invitation_id)
    if not inv or (not ctx.is_platform and inv.tenant_id != ctx.tenant_id):
        raise api_error(404, "not_found", "Invitation not found.")
    inv.revoked_at = svc.now()
    audit(db, "staff.invitation_revoked", actor=ctx.user.id, tenant_id=inv.tenant_id, target_type="invitation", target_id=inv.id)
    db.commit()
    return {"status": "ok"}


@router.get("/audit")
def audit_log(action: str | None = None, limit: int = 50, offset: int = 0, ctx: Ctx = Depends(require("audit:read")), db: Session = Depends(get_db)):
    from app.models.identity import AuditEvent

    stmt = select(AuditEvent).order_by(AuditEvent.created_at.desc()).limit(min(limit, 200)).offset(offset)
    if not ctx.is_platform:
        stmt = stmt.where(AuditEvent.tenant_id == ctx.tenant_id)
    if action:
        stmt = stmt.where(AuditEvent.action.like(f"{action}%"))
    return [{"id": str(a.id), "created_at": a.created_at, "action": a.action, "actor_user_id": str(a.actor_user_id) if a.actor_user_id else None, "tenant_id": str(a.tenant_id) if a.tenant_id else None, "target_type": a.target_type, "target_id": a.target_id, "detail": a.detail} for a in db.scalars(stmt)]
