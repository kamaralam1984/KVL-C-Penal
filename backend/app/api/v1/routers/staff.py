"""Staff management: accounts (via memberships), departments, permissions view, activity history, workload."""
import uuid

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.db import get_db
from app.core.deps import Ctx, api_error, require, role_permissions
from app.core.rbac import PRIVILEGED_ROLES
from app.models.identity import AuditEvent, Membership, Role, User
from app.models.support import DepartmentMember, SupportDepartment
from app.services import support as svc

router = APIRouter(prefix="/staff", tags=["staff"])
INFRA_PREFIXES = ("servers:", "backups:manage", "backups:restore", "databases:manage", "roles:", "settings:write", "security:manage")


def _staff_rows(db: Session):
    return db.execute(
        select(User, Membership, Role).join(Membership, Membership.user_id == User.id).join(Role, Role.id == Membership.role_id)
        .where(Membership.tenant_id.is_(None), Role.scope == "platform").order_by(User.name)
    ).all()


@router.get("/members")
def members(ctx: Ctx = Depends(require("staff:read")), db: Session = Depends(get_db)):
    if not ctx.is_platform:
        raise api_error(403, "forbidden", "Platform staff only.")
    depts: dict[uuid.UUID, list[str]] = {}
    for dm, d in db.execute(select(DepartmentMember, SupportDepartment).join(SupportDepartment, SupportDepartment.id == DepartmentMember.department_id)):
        depts.setdefault(dm.user_id, []).append(d.name)
    out = []
    for u, m, r in _staff_rows(db):
        out.append({"id": str(u.id), "membership_id": str(m.id), "name": u.name, "email": u.email, "status": u.status, "membership_status": m.status,
                    "role": r.name, "role_label": r.label, "privileged": r.privileged, "departments": depts.get(u.id, []), "last_login_at": u.last_login_at})
    return out


@router.get("/members/{user_id}")
def member_detail(user_id: uuid.UUID, ctx: Ctx = Depends(require("staff:read")), db: Session = Depends(get_db)):
    if not ctx.is_platform:
        raise api_error(403, "forbidden", "Platform staff only.")
    rows = [(u, m, r) for u, m, r in _staff_rows(db) if u.id == user_id]
    if not rows:
        raise api_error(404, "not_found", "Staff member not found.")
    u = rows[0][0]
    roles = []
    for _, m, r in rows:
        perms = sorted(role_permissions(db, r.id))
        roles.append({"role": r.name, "label": r.label, "permissions": perms, "infrastructure_permissions": [p for p in perms if p.startswith(INFRA_PREFIXES)]})
    depts = [d.name for d in db.scalars(select(SupportDepartment).join(DepartmentMember, DepartmentMember.department_id == SupportDepartment.id).where(DepartmentMember.user_id == user_id))]
    work = next((w for w in svc.report_workload(db)["staff"] if w["user_id"] == str(user_id)), {"open": 0, "breached": 0, "resolved_in_period": 0})
    return {"id": str(u.id), "name": u.name, "email": u.email, "status": u.status, "roles": roles, "departments": depts, "workload": work}


@router.get("/members/{user_id}/activity")
def member_activity(user_id: uuid.UUID, limit: int = Query(default=50, ge=1, le=200), offset: int = Query(default=0, ge=0), ctx: Ctx = Depends(require("staff:read", "audit:read")), db: Session = Depends(get_db)):
    if not ctx.is_platform:
        raise api_error(403, "forbidden", "Platform staff only.")
    rows = db.scalars(select(AuditEvent).where(AuditEvent.actor_user_id == user_id).order_by(AuditEvent.created_at.desc()).limit(limit).offset(offset))
    return [{"id": str(a.id), "created_at": a.created_at, "action": a.action, "target_type": a.target_type, "target_id": a.target_id, "tenant_id": str(a.tenant_id) if a.tenant_id else None} for a in rows]
