"""Phase 16: support tickets, attachments, departments, SLA, reports, customer timeline.

Customer-facing safety: all ticket output goes through app.services.support serializers. `staff` is true only for platform
users holding tickets:manage; everyone else receives public messages/events/attachments only.
"""
import uuid
from datetime import datetime

from fastapi import APIRouter, Depends, File, Form, Query, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse, Response
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.db import get_db
from app.core.deps import Ctx, api_error, client_ip, require, role_permissions
from app.models.identity import AuditEvent, Membership, Role, User
from app.models.support import TICKET_PRIORITIES, TICKET_STATUSES, DepartmentMember, SupportDepartment, Ticket, TicketAttachment, TicketMessage
from app.models.tenant import Tenant, TenantActivity
from app.services import support as svc
from app.services.audit import audit
from app.services.idempotency import run_idempotent
from app.services.mail import queue_email
from app.services.webhooks import emit_event

router = APIRouter(prefix="/support", tags=["support"])

PRIORITY_RE = "^(low|normal|high|urgent)$"
CATEGORY_RE = "^(" + "|".join(svc.CATEGORIES) + ")$"


def _staff_only(ctx: Ctx) -> None:
    if not svc.is_staff(ctx):
        raise api_error(403, "forbidden", "Support staff only.")


def _agent_ids(db: Session) -> dict[uuid.UUID, tuple[User, set[str]]]:
    """Platform users whose role grants tickets:manage."""
    out: dict[uuid.UUID, tuple[User, set[str]]] = {}
    rows = db.execute(select(User, Membership, Role).join(Membership, Membership.user_id == User.id).join(Role, Role.id == Membership.role_id)
                      .where(Membership.tenant_id.is_(None), Membership.status == "active", Role.scope == "platform", User.status == "active")).all()
    for u, m, r in rows:
        perms = role_permissions(db, r.id)
        if "tickets:manage" in perms:
            out[u.id] = (u, set(perms))
    return out


# ---------------------------------------------------------------- metadata
@router.get("/meta")
def meta(ctx: Ctx = Depends(require("tickets:read")), db: Session = Depends(get_db)):
    depts = db.scalars(select(SupportDepartment).where(SupportDepartment.active.is_(True)).order_by(SupportDepartment.name)).all()
    staff = svc.is_staff(ctx)
    out = {"categories": list(svc.CATEGORIES), "priorities": list(TICKET_PRIORITIES), "statuses": list(TICKET_STATUSES),
           "departments": [{"id": str(d.id), "name": d.name} for d in depts], "is_staff": staff,
           "max_attachment_bytes": get_settings().support_max_attachment_bytes,
           "allowed_extensions": sorted(svc.EXT_TYPES)}
    if staff:
        out["sla_policy_minutes"] = {p: {"first_response": a, "resolution": b} for p, (a, b) in svc.SLA_POLICY.items()}
        out["sla_clock"] = "business_hours_utc" if get_settings().support_sla_business_hours else "24x7"
    return out


@router.get("/agents")
def agents(ctx: Ctx = Depends(require("tickets:manage")), db: Session = Depends(get_db)):
    _staff_only(ctx)
    return [{"id": str(u.id), "name": u.name, "email": u.email} for u, _ in sorted(_agent_ids(db).values(), key=lambda x: x[0].name)]


# ---------------------------------------------------------------- departments
class DeptIn(BaseModel):
    name: str = Field(min_length=2, max_length=80)
    description: str | None = Field(default=None, max_length=300)
    active: bool = True


@router.get("/departments")
def departments(ctx: Ctx = Depends(require("tickets:read")), db: Session = Depends(get_db)):
    staff = svc.is_staff(ctx)
    stmt = select(SupportDepartment).order_by(SupportDepartment.name)
    if not staff:
        stmt = stmt.where(SupportDepartment.active.is_(True))
    out = []
    for d in db.scalars(stmt):
        row = {"id": str(d.id), "name": d.name, "description": d.description}
        if staff:
            row["active"] = d.active
            row["members"] = db.scalar(select(func.count()).select_from(DepartmentMember).where(DepartmentMember.department_id == d.id))
        out.append(row)
    return out


@router.post("/departments", status_code=201)
def create_department(body: DeptIn, request: Request, ctx: Ctx = Depends(require("tickets:manage")), db: Session = Depends(get_db)):
    _staff_only(ctx)
    if db.scalar(select(SupportDepartment.id).where(func.lower(SupportDepartment.name) == body.name.lower())):
        raise api_error(409, "duplicate", "A department with this name exists.")
    d = SupportDepartment(name=body.name.strip(), description=body.description, active=body.active)
    db.add(d)
    db.flush()
    audit(db, "support.department_created", actor=ctx.user.id, target_type="department", target_id=d.id, ip=client_ip(request))
    db.commit()
    return {"id": str(d.id)}


@router.patch("/departments/{dept_id}")
def update_department(dept_id: uuid.UUID, body: DeptIn, request: Request, ctx: Ctx = Depends(require("tickets:manage")), db: Session = Depends(get_db)):
    _staff_only(ctx)
    d = db.get(SupportDepartment, dept_id)
    if not d:
        raise api_error(404, "not_found", "Department not found.")
    d.name, d.description, d.active = body.name.strip(), body.description, body.active
    audit(db, "support.department_updated", actor=ctx.user.id, target_type="department", target_id=d.id, ip=client_ip(request))
    db.commit()
    return {"status": "ok"}


@router.get("/departments/{dept_id}/members")
def department_members(dept_id: uuid.UUID, ctx: Ctx = Depends(require("tickets:manage")), db: Session = Depends(get_db)):
    _staff_only(ctx)
    rows = db.execute(select(User).join(DepartmentMember, DepartmentMember.user_id == User.id).where(DepartmentMember.department_id == dept_id)).scalars()
    return [{"id": str(u.id), "name": u.name, "email": u.email} for u in rows]


@router.put("/departments/{dept_id}/members/{user_id}")
def add_department_member(dept_id: uuid.UUID, user_id: uuid.UUID, request: Request, ctx: Ctx = Depends(require("tickets:manage")), db: Session = Depends(get_db)):
    _staff_only(ctx)
    if not db.get(SupportDepartment, dept_id):
        raise api_error(404, "not_found", "Department not found.")
    if user_id not in _agent_ids(db):
        raise api_error(422, "not_staff", "Only support staff accounts can join a department.")
    if not db.get(DepartmentMember, (dept_id, user_id)):
        db.add(DepartmentMember(department_id=dept_id, user_id=user_id))
        audit(db, "support.department_member_added", actor=ctx.user.id, target_type="department", target_id=dept_id, detail={"user_id": str(user_id)}, ip=client_ip(request))
        db.commit()
    return {"status": "ok"}


@router.delete("/departments/{dept_id}/members/{user_id}")
def remove_department_member(dept_id: uuid.UUID, user_id: uuid.UUID, request: Request, ctx: Ctx = Depends(require("tickets:manage")), db: Session = Depends(get_db)):
    _staff_only(ctx)
    m = db.get(DepartmentMember, (dept_id, user_id))
    if not m:
        raise api_error(404, "not_found", "Membership not found.")
    db.delete(m)
    audit(db, "support.department_member_removed", actor=ctx.user.id, target_type="department", target_id=dept_id, detail={"user_id": str(user_id)}, ip=client_ip(request))
    db.commit()
    return {"status": "ok"}


# ---------------------------------------------------------------- tickets
class TicketIn(BaseModel):
    subject: str = Field(min_length=3, max_length=200)
    body: str = Field(min_length=1, max_length=10000)
    category: str = Field(default="general", pattern=CATEGORY_RE)
    priority: str = Field(default="normal", pattern=PRIORITY_RE)
    department_id: uuid.UUID | None = None
    tenant_id: uuid.UUID | None = None


def _create_tenant(ctx: Ctx, db: Session, requested: uuid.UUID | None) -> uuid.UUID:
    if ctx.is_platform:
        if requested is None or not db.get(Tenant, requested):
            raise api_error(422, "tenant_required", "tenant_id of an existing tenant is required.")
        return requested
    if ctx.scope == "tenant" and ctx.tenant_id:
        if requested not in (None, ctx.tenant_id):
            raise api_error(404, "not_found", "Resource not found.")
        return ctx.tenant_id
    if ctx.scope == "reseller" and ctx.reseller_id and requested:
        if db.scalar(select(Tenant.id).where(Tenant.id == requested, Tenant.reseller_id == ctx.reseller_id)):
            return requested
    raise api_error(404, "not_found", "Resource not found.")


@router.post("/tickets", status_code=201)
def create_ticket(body: TicketIn, request: Request, ctx: Ctx = Depends(require("tickets:write")), db: Session = Depends(get_db)):
    tenant_id = _create_tenant(ctx, db, body.tenant_id)
    staff = svc.is_staff(ctx)
    if body.priority == "urgent" and not staff:
        raise api_error(422, "priority_not_allowed", "Urgent priority is set by support staff.")
    if body.department_id and (not staff or not db.get(SupportDepartment, body.department_id)):
        if staff:
            raise api_error(404, "not_found", "Department not found.")
        raise api_error(403, "forbidden", "Only support staff can choose a department.")
    from app.core.ratelimit import enforce

    enforce(f"ticket_create:{ctx.user.id}", 20, 3600)

    def work():
        n = svc.now()
        t = Ticket(tenant_id=tenant_id, created_by=ctx.user.id, subject=body.subject.strip(), category=body.category, priority=body.priority,
                   department_id=body.department_id, status="open", created_at=n, last_activity_at=n)
        svc.set_sla(t, n)
        db.add(t)
        db.flush()
        db.add(TicketMessage(ticket_id=t.id, tenant_id=tenant_id, author_id=ctx.user.id, author_type="staff" if ctx.is_platform else "customer", body=body.body, created_at=n))
        svc.add_event(db, t, "created", ctx.user.id, {"priority": t.priority, "category": t.category})
        audit(db, "support.ticket_created", actor=ctx.user.id, tenant_id=tenant_id, target_type="ticket", target_id=t.id, ip=client_ip(request))
        emit_event(db, "ticket.created", {"ticket_id": str(t.id), "number": t.number, "subject": t.subject, "priority": t.priority}, tenant_id=tenant_id)
        db.commit()
        db.refresh(t)
        return 201, svc.ticket_detail(db, t, staff)

    return run_idempotent(db, ctx, request, body.model_dump(mode="json"), work)


def _filters(ctx: Ctx, **kw):
    staff = svc.is_staff(ctx)
    return staff, svc.apply_filters(svc.visible_stmt(ctx), staff=staff, me=ctx.user.id, **kw)


@router.get("/tickets")
def list_tickets(
    status: str | None = Query(default=None, max_length=80), priority: str | None = Query(default=None, max_length=60),
    category: str | None = None, department_id: uuid.UUID | None = None, tenant_id: uuid.UUID | None = None,
    assignee: str | None = Query(default=None, max_length=40), q: str | None = Query(default=None, max_length=100), sla: str | None = None,
    created_from: datetime | None = None, created_to: datetime | None = None,
    sort: str = Query(default="activity", pattern="^(activity|created|priority)$"),
    limit: int = Query(default=25, ge=1, le=100), offset: int = Query(default=0, ge=0),
    ctx: Ctx = Depends(require("tickets:read")), db: Session = Depends(get_db),
):
    staff, stmt = _filters(ctx, status=status, priority=priority, category=category, department_id=department_id, tenant_id=tenant_id,
                           assignee=assignee, q=q, sla=sla, created_from=created_from, created_to=created_to)
    total = db.scalar(select(func.count()).select_from(stmt.subquery()))
    if sort == "priority":
        from sqlalchemy import case
        order = [case({"urgent": 0, "high": 1, "normal": 2, "low": 3}, value=Ticket.priority, else_=4), Ticket.created_at]
    else:
        order = [(Ticket.created_at if sort == "created" else Ticket.last_activity_at).desc()]
    rows = list(db.scalars(stmt.order_by(*order).limit(limit).offset(offset)))
    return {"total": total, "items": svc.list_dtos(db, rows, staff)}


@router.get("/tickets/export.csv")
def export_tickets(
    request: Request, status: str | None = None, priority: str | None = None, category: str | None = None, tenant_id: uuid.UUID | None = None,
    q: str | None = Query(default=None, max_length=100),
    ctx: Ctx = Depends(require("tickets:read")), db: Session = Depends(get_db),
):
    staff, stmt = _filters(ctx, status=status, priority=priority, category=category, tenant_id=tenant_id, q=q)
    rows = list(db.scalars(stmt.order_by(Ticket.created_at.desc()).limit(5000)))
    cols = ["number", "subject", "category", "priority", "status", "created_at", "resolved_at"]
    if staff:
        cols += ["tenant_id", "department", "assignee_name", "first_response", "resolution", "escalation_level"]
    lines = [",".join(cols)]
    for d in svc.list_dtos(db, rows, staff):
        flat = {**d, **({"first_response": d["sla"]["first_response"], "resolution": d["sla"]["resolution"], "escalation_level": d["sla"]["escalation_level"]} if staff else {})}
        lines.append(",".join(svc.csv_cell(flat.get(c)) for c in cols))
    audit(db, "support.tickets_exported", actor=ctx.user.id, tenant_id=ctx.tenant_id, detail={"rows": len(rows), "staff_view": staff}, ip=client_ip(request))
    db.commit()
    return Response("\n".join(lines) + "\n", media_type="text/csv", headers={"Content-Disposition": 'attachment; filename="tickets.csv"'})


@router.get("/tickets/{ticket_id}")
def get_ticket(ticket_id: uuid.UUID, ctx: Ctx = Depends(require("tickets:read")), db: Session = Depends(get_db)):
    t = svc.get_ticket(db, ctx, ticket_id)
    return svc.ticket_detail(db, t, svc.is_staff(ctx))


@router.get("/tickets/{ticket_id}/history")
def ticket_history(ticket_id: uuid.UUID, ctx: Ctx = Depends(require("tickets:read")), db: Session = Depends(get_db)):
    return svc.ticket_detail(db, svc.get_ticket(db, ctx, ticket_id), svc.is_staff(ctx))["history"]


class MessageIn(BaseModel):
    body: str = Field(min_length=1, max_length=10000)
    internal: bool = False


@router.post("/tickets/{ticket_id}/messages", status_code=201)
def add_message(ticket_id: uuid.UUID, body: MessageIn, request: Request, ctx: Ctx = Depends(require("tickets:write")), db: Session = Depends(get_db)):
    t = svc.get_ticket(db, ctx, ticket_id)
    staff = svc.is_staff(ctx)
    if body.internal and not staff:
        raise api_error(403, "forbidden", "Only support staff can add internal notes.")
    if t.status == "closed" and not body.internal:
        raise api_error(409, "ticket_closed", "This ticket is closed. Reopen it to reply.")
    n = svc.now()
    from_staff = ctx.is_platform
    m = TicketMessage(ticket_id=t.id, tenant_id=t.tenant_id, author_id=ctx.user.id, author_type="staff" if from_staff else "customer", body=body.body, is_internal=body.internal, created_at=n)
    db.add(m)
    db.flush()
    if body.internal:
        svc.add_event(db, t, "internal_note", ctx.user.id, None, internal=True)
        audit(db, "support.internal_note_added", actor=ctx.user.id, tenant_id=t.tenant_id, target_type="ticket", target_id=t.id, ip=client_ip(request))
    else:
        t.last_activity_at = n
        svc.add_event(db, t, "reply", ctx.user.id, {"by": "staff" if from_staff else "customer"})
        if from_staff:
            if t.first_response_at is None:
                t.first_response_at = n
            if t.status == "open":
                svc.transition_status(db, t, "pending", ctx.user.id)  # waiting on the customer
            owner = db.get(User, t.created_by) if t.created_by else None
            if owner:
                queue_email(db, owner.email, f"Update on ticket #{t.number}", f"Support replied to your ticket '{t.subject}'. Sign in to read and respond.", "ticket_reply", t.tenant_id)
        else:
            if t.status in ("pending", "resolved", "on_hold"):
                svc.transition_status(db, t, "open", ctx.user.id)
            if t.assignee_id and (a := db.get(User, t.assignee_id)):
                queue_email(db, a.email, f"Customer replied: ticket #{t.number}", f"A customer replied to '{t.subject}'.", "ticket_customer_reply")
        audit(db, "support.ticket_replied", actor=ctx.user.id, tenant_id=t.tenant_id, target_type="ticket", target_id=t.id, ip=client_ip(request))
        emit_event(db, "ticket.replied", {"ticket_id": str(t.id), "number": t.number, "by": "staff" if from_staff else "customer"}, tenant_id=t.tenant_id)
    db.commit()
    return {"id": str(m.id)}


class TicketPatch(BaseModel):
    status: str | None = Field(default=None, pattern="^(open|pending|on_hold|resolved|closed)$")
    priority: str | None = Field(default=None, pattern=PRIORITY_RE)
    category: str | None = Field(default=None, pattern=CATEGORY_RE)
    department_id: uuid.UUID | None = None


@router.patch("/tickets/{ticket_id}")
def update_ticket(ticket_id: uuid.UUID, body: TicketPatch, request: Request, ctx: Ctx = Depends(require("tickets:write")), db: Session = Depends(get_db)):
    t = svc.get_ticket(db, ctx, ticket_id)
    staff = svc.is_staff(ctx)
    data = body.model_dump(exclude_unset=True)
    if not staff:
        if ctx.is_platform or set(data) - {"status"} or data.get("status") not in ("closed", "open"):
            raise api_error(403, "forbidden", "You can only close or reopen your ticket.")
    if "department_id" in data and data["department_id"] and not db.get(SupportDepartment, data["department_id"]):
        raise api_error(404, "not_found", "Department not found.")
    if data.get("priority"):
        svc.set_priority(db, t, data["priority"], ctx.user.id)
    if data.get("category"):
        t.category = data["category"]
    if "department_id" in data and data["department_id"] != t.department_id:
        svc.add_event(db, t, "department_changed", ctx.user.id, {"department_id": str(data["department_id"]) if data["department_id"] else None}, internal=True)
        t.department_id = data["department_id"]
    if data.get("status"):
        svc.transition_status(db, t, data["status"], ctx.user.id)
        emit_event(db, "ticket.status_changed", {"ticket_id": str(t.id), "number": t.number, "status": t.status}, tenant_id=t.tenant_id)
    audit(db, "support.ticket_updated", actor=ctx.user.id, tenant_id=t.tenant_id, target_type="ticket", target_id=t.id, detail=data and {k: str(v) for k, v in data.items()}, ip=client_ip(request))
    db.commit()
    return svc.ticket_dto(db, t, staff)


class AssignIn(BaseModel):
    assignee_id: uuid.UUID | None = None


@router.post("/tickets/{ticket_id}/assign")
def assign_ticket(ticket_id: uuid.UUID, body: AssignIn, request: Request, ctx: Ctx = Depends(require("tickets:manage")), db: Session = Depends(get_db)):
    _staff_only(ctx)
    t = svc.get_ticket(db, ctx, ticket_id)
    if body.assignee_id and body.assignee_id not in _agent_ids(db):
        raise api_error(422, "not_staff", "Assignee must be a support staff member.")
    old = t.assignee_id
    t.assignee_id = body.assignee_id
    kind = "reassigned" if old and body.assignee_id else "assigned" if body.assignee_id else "unassigned"
    svc.add_event(db, t, kind, ctx.user.id, {"from": str(old) if old else None, "to": str(body.assignee_id) if body.assignee_id else None}, internal=True)
    audit(db, f"support.ticket_{kind}", actor=ctx.user.id, tenant_id=t.tenant_id, target_type="ticket", target_id=t.id, detail={"to": str(body.assignee_id) if body.assignee_id else None}, ip=client_ip(request))
    if body.assignee_id and body.assignee_id != ctx.user.id and (a := db.get(User, body.assignee_id)):
        queue_email(db, a.email, f"Ticket #{t.number} assigned to you", f"'{t.subject}' was assigned to you.", "ticket_assigned")
    db.commit()
    return svc.ticket_dto(db, t, True)


# ---------------------------------------------------------------- attachments
@router.post("/tickets/{ticket_id}/attachments", status_code=201)
async def upload_attachment(
    ticket_id: uuid.UUID, request: Request, file: UploadFile = File(...), internal: bool = Form(False), message_id: uuid.UUID | None = Form(None),
    ctx: Ctx = Depends(require("tickets:write")), db: Session = Depends(get_db),
):
    t = svc.get_ticket(db, ctx, ticket_id)
    staff = svc.is_staff(ctx)
    if internal and not staff:
        raise api_error(403, "forbidden", "Only support staff can add internal attachments.")
    if t.status == "closed":
        raise api_error(409, "ticket_closed", "This ticket is closed.")
    max_bytes = get_settings().support_max_attachment_bytes
    data = await file.read(max_bytes + 1)
    if message_id:
        msg = db.get(TicketMessage, message_id)
        if not msg or msg.ticket_id != t.id or (msg.is_internal and not staff):
            raise api_error(404, "not_found", "Message not found.")
    att = svc.store_attachment(db, t, ctx.user.id, file.filename or "", file.content_type, data, internal=internal, message_id=message_id)
    svc.add_event(db, t, "attachment_added", ctx.user.id, {"name": att.original_name}, internal=internal)
    t.last_activity_at = svc.now()
    audit(db, "support.attachment_uploaded", actor=ctx.user.id, tenant_id=t.tenant_id, target_type="ticket", target_id=t.id, detail={"size": att.size, "type": att.content_type, "internal": internal}, ip=client_ip(request))
    db.commit()
    return svc.attachment_dto(att)


@router.get("/attachments/{attachment_id}/download")
def download_attachment(attachment_id: uuid.UUID, ctx: Ctx = Depends(require("tickets:read")), db: Session = Depends(get_db)):
    att = db.get(TicketAttachment, attachment_id)
    if not att:
        raise api_error(404, "not_found", "Attachment not found.")
    svc.get_ticket(db, ctx, att.ticket_id)  # tenant/ticket authorization (404 for other tenants)
    if att.is_internal and not svc.is_staff(ctx):
        raise api_error(404, "not_found", "Attachment not found.")
    path = svc.attachment_path(att)
    if not path.is_file():
        raise api_error(404, "not_found", "Attachment file is missing.")
    return FileResponse(path, media_type=att.content_type, filename=att.original_name, content_disposition_type="attachment",
                        headers={"Content-Security-Policy": "sandbox; default-src 'none'", "X-Content-Type-Options": "nosniff", "Cache-Control": "private, no-store"})


# ---------------------------------------------------------------- SLA + reports (staff)
@router.post("/sla/run")
def run_sla(request: Request, ctx: Ctx = Depends(require("tickets:manage")), db: Session = Depends(get_db)):
    _staff_only(ctx)
    counts = svc.run_sla_escalation(db)
    audit(db, "support.sla_run", actor=ctx.user.id, detail=counts, ip=client_ip(request))
    db.commit()
    return counts


@router.get("/reports/aging")
def report_aging(ctx: Ctx = Depends(require("tickets:manage")), db: Session = Depends(get_db)):
    _staff_only(ctx)
    return svc.report_aging(db)


@router.get("/reports/sla")
def report_sla(days: int = Query(default=30, ge=1, le=365), ctx: Ctx = Depends(require("tickets:manage")), db: Session = Depends(get_db)):
    _staff_only(ctx)
    return svc.report_sla(db, days)


@router.get("/reports/first-response")
def report_first_response(days: int = Query(default=30, ge=1, le=365), ctx: Ctx = Depends(require("tickets:manage")), db: Session = Depends(get_db)):
    _staff_only(ctx)
    return svc.report_first_response(db, days)


@router.get("/reports/workload")
def report_workload(days: int = Query(default=30, ge=1, le=365), ctx: Ctx = Depends(require("tickets:manage")), db: Session = Depends(get_db)):
    _staff_only(ctx)
    return svc.report_workload(db, days)


# ---------------------------------------------------------------- customer timeline (staff)
@router.get("/customers/{tenant_id}/timeline")
def customer_timeline(tenant_id: uuid.UUID, limit: int = Query(default=100, ge=1, le=300), ctx: Ctx = Depends(require("tickets:manage", "tenants:read")), db: Session = Depends(get_db)):
    _staff_only(ctx)
    tenant = db.get(Tenant, tenant_id)
    if not tenant:
        raise api_error(404, "not_found", "Tenant not found.")
    items = []
    for a in db.scalars(select(TenantActivity).where(TenantActivity.tenant_id == tenant_id).order_by(TenantActivity.created_at.desc()).limit(limit)):
        items.append({"at": a.created_at, "source": "activity", "title": a.summary, "ref": a.action})
    for t in db.scalars(select(Ticket).where(Ticket.tenant_id == tenant_id).order_by(Ticket.created_at.desc()).limit(limit)):
        items.append({"at": t.created_at, "source": "ticket", "title": f"Ticket #{t.number} opened: {t.subject}", "ref": str(t.id), "status": t.status})
        if t.resolved_at:
            items.append({"at": t.resolved_at, "source": "ticket", "title": f"Ticket #{t.number} resolved", "ref": str(t.id)})
    for e in db.scalars(select(AuditEvent).where(AuditEvent.tenant_id == tenant_id).order_by(AuditEvent.created_at.desc()).limit(limit)):
        items.append({"at": e.created_at, "source": "audit", "title": e.action, "ref": e.target_id})
    items.sort(key=lambda i: i["at"], reverse=True)
    open_tickets = db.scalar(select(func.count()).select_from(Ticket).where(Ticket.tenant_id == tenant_id, Ticket.status.in_(("open", "pending", "on_hold"))))
    return {"tenant": {"id": str(tenant.id), "name": tenant.name, "status": tenant.status, "plan_code": tenant.plan_code}, "open_tickets": open_tickets, "items": items[:limit]}
