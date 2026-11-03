"""Ticketing domain logic: visibility, SLA clock, attachment validation, serializers, escalation, reports.

Internal notes: every customer-facing serializer goes through `message_dto/event_dto/attachment_dto` with
`staff=False`, and every query that feeds a customer response filters `is_internal = false`. Do not bypass.
"""
import os
import re
import secrets
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path
from statistics import median
from typing import Any

from sqlalchemy import and_, exists, func, or_, select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.deps import Ctx, api_error
from app.models.identity import User
from app.models.support import (
    TICKET_PRIORITIES,
    DepartmentMember,
    SupportDepartment,
    Ticket,
    TicketAttachment,
    TicketEvent,
    TicketMessage,
)
from app.models.tenant import Tenant
from app.services.audit import audit

CATEGORIES = ("general", "technical", "billing", "domains", "email", "security", "feature_request")

# (first response minutes, resolution minutes) per priority.
SLA_POLICY: dict[str, tuple[int, int]] = {
    "urgent": (60, 4 * 60),
    "high": (4 * 60, 24 * 60),
    "normal": (8 * 60, 72 * 60),
    "low": (24 * 60, 7 * 24 * 60),
}
BUSINESS_START, BUSINESS_END = 9, 18  # UTC, Mon-Fri, used only when support_sla_business_hours is true
MAX_ESCALATION = 3


def now() -> datetime:
    return datetime.now(UTC)


# ---------------------------------------------------------------- roles of the caller
def is_staff(ctx: Ctx) -> bool:
    """Support staff view: platform scope AND tickets:manage. Everyone else gets the customer-facing view."""
    return ctx.is_platform and ctx.can("tickets:manage")


def visible_stmt(ctx: Ctx):
    stmt = select(Ticket)
    if ctx.is_platform:
        return stmt
    if ctx.scope == "tenant" and ctx.tenant_id:
        return stmt.where(Ticket.tenant_id == ctx.tenant_id)
    if ctx.scope == "reseller" and ctx.reseller_id:
        return stmt.where(Ticket.tenant_id.in_(select(Tenant.id).where(Tenant.reseller_id == ctx.reseller_id)))
    return stmt.where(False)


def get_ticket(db: Session, ctx: Ctx, ticket_id: uuid.UUID) -> Ticket:
    t = db.scalar(visible_stmt(ctx).where(Ticket.id == ticket_id))
    if not t:
        raise api_error(404, "not_found", "Ticket not found.")
    return t


# ---------------------------------------------------------------- SLA clock
def add_sla_minutes(start: datetime, minutes: int, business: bool | None = None) -> datetime:
    business = get_settings().support_sla_business_hours if business is None else business
    if not business:
        return start + timedelta(minutes=minutes)
    cur, left = start, minutes
    while True:
        if cur.weekday() >= 5:
            cur = (cur + timedelta(days=7 - cur.weekday())).replace(hour=BUSINESS_START, minute=0, second=0, microsecond=0)
            continue
        open_t = cur.replace(hour=BUSINESS_START, minute=0, second=0, microsecond=0)
        close_t = cur.replace(hour=BUSINESS_END, minute=0, second=0, microsecond=0)
        if cur < open_t:
            cur = open_t
        if cur >= close_t:
            cur = (cur + timedelta(days=1)).replace(hour=BUSINESS_START, minute=0, second=0, microsecond=0)
            continue
        avail = int((close_t - cur).total_seconds() // 60)
        if left <= avail:
            return cur + timedelta(minutes=left)
        left -= avail
        cur = (cur + timedelta(days=1)).replace(hour=BUSINESS_START, minute=0, second=0, microsecond=0)


def set_sla(t: Ticket, base: datetime) -> None:
    fr, res = SLA_POLICY[t.priority]
    if t.first_response_at is None:
        t.first_response_due_at = add_sla_minutes(base, fr)
    t.resolution_due_at = add_sla_minutes(base, res)


def sla_state(t: Ticket, at: datetime | None = None) -> dict[str, Any]:
    at = at or now()
    open_ = t.status in ("open", "pending", "on_hold")
    fr = "met" if t.first_response_at and (not t.first_response_due_at or t.first_response_at <= t.first_response_due_at) else (
        "breached" if t.first_response_breached or (t.first_response_at and t.first_response_due_at and t.first_response_at > t.first_response_due_at) else "pending")
    if fr == "pending" and open_ and t.status != "on_hold" and t.first_response_due_at and t.first_response_due_at < at:
        fr = "breached"
    done = t.resolved_at or t.closed_at
    res = "met" if done and (not t.resolution_due_at or done <= t.resolution_due_at) else (
        "breached" if t.resolution_breached or (done and t.resolution_due_at and done > t.resolution_due_at) else "pending")
    if res == "pending" and open_ and t.status != "on_hold" and t.resolution_due_at and t.resolution_due_at < at:
        res = "breached"
    return {"first_response": fr, "resolution": res, "first_response_due_at": t.first_response_due_at, "resolution_due_at": t.resolution_due_at,
            "escalation_level": t.escalation_level, "escalated_at": t.escalated_at}


# ---------------------------------------------------------------- history
def add_event(db: Session, t: Ticket, kind: str, actor: uuid.UUID | None, detail: dict | None = None, internal: bool = False) -> None:
    db.add(TicketEvent(ticket_id=t.id, actor_id=actor, kind=kind, detail=detail, is_internal=internal))


def transition_status(db: Session, t: Ticket, new: str, actor: uuid.UUID | None) -> None:
    old = t.status
    if old == new:
        return
    n = now()
    if old == "on_hold" and t.hold_started_at:
        held = n - t.hold_started_at
        if t.first_response_at is None and t.first_response_due_at:
            t.first_response_due_at += held
        if t.resolution_due_at:
            t.resolution_due_at += held
        t.hold_started_at = None
    if new == "on_hold":
        t.hold_started_at = n
    if new == "resolved":
        t.resolved_at = n
    elif new == "closed":
        t.closed_at = n
        t.resolved_at = t.resolved_at or n
    elif new in ("open", "pending"):
        if old in ("resolved", "closed"):
            t.resolved_at = t.closed_at = None
            t.resolution_breached = False
            set_sla(t, n)  # reopened work gets a fresh resolution clock
    t.status = new
    t.last_activity_at = n
    add_event(db, t, "status_changed", actor, {"from": old, "to": new})


def set_priority(db: Session, t: Ticket, new: str, actor: uuid.UUID | None) -> None:
    if new not in TICKET_PRIORITIES:
        raise api_error(422, "validation_error", "Unknown priority.")
    if new == t.priority:
        return
    add_event(db, t, "priority_changed", actor, {"from": t.priority, "to": new}, internal=True)
    t.priority = new
    set_sla(t, t.created_at or now())


# ---------------------------------------------------------------- attachments
EXT_TYPES: dict[str, set[str]] = {
    "png": {"image/png"}, "jpg": {"image/jpeg"}, "jpeg": {"image/jpeg"}, "gif": {"image/gif"}, "webp": {"image/webp"},
    "pdf": {"application/pdf"}, "txt": {"text/plain"}, "log": {"text/plain"},
    "csv": {"text/csv", "text/plain", "application/vnd.ms-excel"},
}
CANON_TYPE = {"png": "image/png", "jpg": "image/jpeg", "jpeg": "image/jpeg", "gif": "image/gif", "webp": "image/webp",
              "pdf": "application/pdf", "txt": "text/plain", "log": "text/plain", "csv": "text/csv"}
DANGEROUS_PARTS = {"php", "phtml", "phar", "exe", "dll", "bat", "cmd", "com", "sh", "bash", "js", "mjs", "html", "htm", "svg", "xml",
                   "py", "pl", "rb", "jar", "msi", "scr", "vbs", "ps1", "cgi", "asp", "aspx", "jsp", "htaccess", "xhtml"}
MAX_ATTACHMENTS_PER_TICKET = 20


def sniff_ok(ext: str, data: bytes) -> bool:
    if ext == "png":
        return data.startswith(b"\x89PNG\r\n\x1a\n")
    if ext in ("jpg", "jpeg"):
        return data.startswith(b"\xff\xd8\xff")
    if ext == "gif":
        return data[:6] in (b"GIF87a", b"GIF89a")
    if ext == "webp":
        return data[:4] == b"RIFF" and data[8:12] == b"WEBP"
    if ext == "pdf":
        return data.startswith(b"%PDF-")
    # text types: valid UTF-8, no NUL bytes, and must not look like markup/script
    if b"\x00" in data:
        return False
    try:
        txt = data.decode("utf-8")
    except UnicodeDecodeError:
        return False
    head = txt.lstrip()[:200].lower()
    return not head.startswith(("<!doctype", "<html", "<script", "<?php", "<svg", "#!"))


def clean_filename(raw: str) -> str:
    """Display name only. Strips any directory part and unsafe characters; never used to build a path."""
    if not raw or "\x00" in raw:
        raise api_error(422, "invalid_filename", "Invalid file name.")
    name = os.path.basename(raw.replace("\\", "/")).strip()
    name = re.sub(r"[^A-Za-z0-9._ -]", "_", name).strip(" .")
    if not name or name.startswith("."):
        raise api_error(422, "invalid_filename", "Invalid file name.")
    return name[:150]


def validate_upload(filename: str, declared_type: str | None, data: bytes) -> tuple[str, str, str]:
    """Returns (display_name, extension, canonical_content_type) or raises a 4xx api_error."""
    s = get_settings()
    if len(data) > s.support_max_attachment_bytes:
        raise api_error(413, "file_too_large", f"Attachments are limited to {s.support_max_attachment_bytes // 1024} KB.")
    if not data:
        raise api_error(422, "empty_file", "The file is empty.")
    name = clean_filename(filename)
    parts = name.lower().split(".")
    if len(parts) < 2:
        raise api_error(422, "unsupported_file_type", "Files need an extension.")
    ext = parts[-1]
    if ext not in EXT_TYPES:
        raise api_error(422, "unsupported_file_type", f"File type .{ext} is not allowed.")
    if any(p in DANGEROUS_PARTS for p in parts[:-1]):
        raise api_error(422, "unsupported_file_type", "Executable or script extensions are not allowed anywhere in the name.")
    ct = (declared_type or "").split(";")[0].strip().lower()
    if ct not in EXT_TYPES[ext]:
        raise api_error(422, "content_type_mismatch", "Declared content type does not match the file extension.")
    if not sniff_ok(ext, data):
        raise api_error(422, "content_mismatch", "File contents do not match the file extension.")
    return name, ext, CANON_TYPE[ext]


def upload_root() -> Path:
    root = Path(get_settings().support_upload_dir).resolve()
    root.mkdir(parents=True, exist_ok=True)
    return root


def attachment_path(att: TicketAttachment) -> Path:
    root = upload_root()
    p = (root / str(att.tenant_id) / str(att.ticket_id) / att.stored_name).resolve()
    if not p.is_relative_to(root):  # defence in depth; names are server-generated
        raise api_error(404, "not_found", "Attachment not found.")
    return p


def store_attachment(db: Session, t: Ticket, actor: uuid.UUID, filename: str, declared_type: str | None, data: bytes,
                     *, internal: bool, message_id: uuid.UUID | None = None) -> TicketAttachment:
    if db.scalar(select(func.count()).select_from(TicketAttachment).where(TicketAttachment.ticket_id == t.id)) >= MAX_ATTACHMENTS_PER_TICKET:
        raise api_error(409, "attachment_limit", "This ticket has reached its attachment limit.")
    name, ext, ctype = validate_upload(filename, declared_type, data)
    att = TicketAttachment(
        ticket_id=t.id, message_id=message_id, tenant_id=t.tenant_id, uploaded_by=actor, original_name=name,
        stored_name=f"{secrets.token_hex(24)}.{ext}", content_type=ctype, size=len(data),
        sha256=__import__("hashlib").sha256(data).hexdigest(), is_internal=internal,
    )
    path = attachment_path(att)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "xb") as f:
        f.write(data)
    os.chmod(path, 0o640)
    db.add(att)
    db.flush()
    return att


# ---------------------------------------------------------------- serializers (the only place tickets leave the API)
def _names(db: Session, ids: set[uuid.UUID | None]) -> dict[uuid.UUID, str]:
    ids = {i for i in ids if i}
    if not ids:
        return {}
    return {u.id: u.name for u in db.scalars(select(User).where(User.id.in_(ids)))}


def attachment_dto(a: TicketAttachment) -> dict[str, Any]:
    return {"id": str(a.id), "name": a.original_name, "content_type": a.content_type, "size": a.size,
            "message_id": str(a.message_id) if a.message_id else None, "created_at": a.created_at, "internal": a.is_internal}


def message_dto(m: TicketMessage, names: dict, atts: list[TicketAttachment], staff: bool) -> dict[str, Any]:
    assert staff or not m.is_internal, "internal note leaked to a customer-facing serializer"
    out = {"id": str(m.id), "author_type": m.author_type, "author_name": names.get(m.author_id, "Support" if m.author_type == "staff" else "Customer"),
           "body": m.body, "created_at": m.created_at,
           "attachments": [attachment_dto(a) for a in atts if staff or not a.is_internal]}
    if staff:
        out["internal"] = m.is_internal
    return out


def event_dto(e: TicketEvent, names: dict, staff: bool) -> dict[str, Any]:
    assert staff or not e.is_internal
    d = dict(e.detail or {})
    if not staff:  # customers see what happened, not who or any staff identifiers
        d = {k: v for k, v in d.items() if k in {"from", "to"}}
    out = {"id": str(e.id), "kind": e.kind, "created_at": e.created_at, "detail": d}
    if staff:
        out["actor_name"] = names.get(e.actor_id)
        out["internal"] = e.is_internal
    return out


def ticket_dto(db: Session, t: Ticket, staff: bool, *, names: dict | None = None, dept_names: dict | None = None) -> dict[str, Any]:
    names = names if names is not None else _names(db, {t.created_by, t.assignee_id})
    dept = (dept_names or {}).get(t.department_id) if dept_names is not None else (
        db.scalar(select(SupportDepartment.name).where(SupportDepartment.id == t.department_id)) if t.department_id else None)
    out = {"id": str(t.id), "number": t.number, "tenant_id": str(t.tenant_id), "subject": t.subject, "category": t.category,
           "department": dept, "priority": t.priority, "status": t.status, "created_at": t.created_at,
           "last_activity_at": t.last_activity_at, "resolved_at": t.resolved_at, "closed_at": t.closed_at,
           "created_by_name": names.get(t.created_by)}
    if staff:
        out.update({"department_id": str(t.department_id) if t.department_id else None,
                    "assignee_id": str(t.assignee_id) if t.assignee_id else None, "assignee_name": names.get(t.assignee_id),
                    "sla": sla_state(t), "first_response_at": t.first_response_at})
    return out


def list_dtos(db: Session, rows: list[Ticket], staff: bool) -> list[dict]:
    names = _names(db, {x for t in rows for x in (t.created_by, t.assignee_id)})
    depts = {d.id: d.name for d in db.scalars(select(SupportDepartment))}
    return [ticket_dto(db, t, staff, names=names, dept_names=depts) for t in rows]


def public_messages(db: Session, t: Ticket, staff: bool) -> list[TicketMessage]:
    stmt = select(TicketMessage).where(TicketMessage.ticket_id == t.id)
    if not staff:
        stmt = stmt.where(TicketMessage.is_internal.is_(False))
    return list(db.scalars(stmt.order_by(TicketMessage.created_at, TicketMessage.id)))


def ticket_detail(db: Session, t: Ticket, staff: bool) -> dict[str, Any]:
    msgs = public_messages(db, t, staff)
    atts_stmt = select(TicketAttachment).where(TicketAttachment.ticket_id == t.id)
    if not staff:
        atts_stmt = atts_stmt.where(TicketAttachment.is_internal.is_(False))
    atts = list(db.scalars(atts_stmt))
    ev_stmt = select(TicketEvent).where(TicketEvent.ticket_id == t.id)
    if not staff:
        ev_stmt = ev_stmt.where(TicketEvent.is_internal.is_(False))
    events = list(db.scalars(ev_stmt.order_by(TicketEvent.created_at)))
    names = _names(db, {t.created_by, t.assignee_id} | {m.author_id for m in msgs} | {e.actor_id for e in events})
    out = ticket_dto(db, t, staff, names=names)
    out["messages"] = [message_dto(m, names, [a for a in atts if a.message_id == m.id], staff) for m in msgs]
    out["attachments"] = [attachment_dto(a) for a in atts]
    out["history"] = [event_dto(e, names, staff) for e in events]
    return out


# ---------------------------------------------------------------- search / filters
def apply_filters(stmt, *, staff: bool, status=None, priority=None, category=None, department_id=None, tenant_id=None,
                  assignee=None, me: uuid.UUID | None = None, q=None, sla=None, created_from=None, created_to=None):
    if status:
        stmt = stmt.where(Ticket.status.in_(status.split(",")))
    if priority:
        stmt = stmt.where(Ticket.priority.in_(priority.split(",")))
    if category:
        stmt = stmt.where(Ticket.category == category)
    if tenant_id:
        stmt = stmt.where(Ticket.tenant_id == tenant_id)
    if created_from:
        stmt = stmt.where(Ticket.created_at >= created_from)
    if created_to:
        stmt = stmt.where(Ticket.created_at <= created_to)
    if staff:
        if department_id:
            stmt = stmt.where(Ticket.department_id == department_id)
        if assignee == "unassigned":
            stmt = stmt.where(Ticket.assignee_id.is_(None))
        elif assignee == "me" and me:
            stmt = stmt.where(Ticket.assignee_id == me)
        elif assignee:
            try:
                stmt = stmt.where(Ticket.assignee_id == uuid.UUID(assignee))
            except ValueError:
                raise api_error(422, "validation_error", "Invalid assignee.") from None
        if sla == "breached":
            n = now()
            stmt = stmt.where(
                Ticket.status.in_(("open", "pending")),
                or_(Ticket.first_response_breached, Ticket.resolution_breached,
                    and_(Ticket.first_response_at.is_(None), Ticket.first_response_due_at < n), Ticket.resolution_due_at < n),
            )
    if q:
        like = f"%{q.strip().replace('%', '').replace('_', '')[:100]}%"
        msg = select(TicketMessage.id).where(TicketMessage.ticket_id == Ticket.id, TicketMessage.body.ilike(like))
        if not staff:
            msg = msg.where(TicketMessage.is_internal.is_(False))  # search must not leak internal notes either
        conds = [Ticket.subject.ilike(like), exists(msg)]
        if q.strip().lstrip("#").isdigit():
            conds.append(Ticket.number == int(q.strip().lstrip("#")))
        stmt = stmt.where(or_(*conds))
    return stmt


def csv_cell(v: Any) -> str:
    s = "" if v is None else str(v)
    if s[:1] in ("=", "+", "-", "@", "\t", "\r"):
        s = "'" + s  # neutralise spreadsheet formula injection
    return '"' + s.replace('"', '""') + '"'


# ---------------------------------------------------------------- escalation (call from a periodic task)
def run_sla_escalation(db: Session, at: datetime | None = None) -> dict[str, int]:
    """Flag SLA breaches and escalate. Idempotent per breach. Returns counts. Commit is the caller's job."""
    from app.services.mail import queue_email
    from app.services.webhooks import emit_event

    at = at or now()
    counts = {"first_response_breached": 0, "resolution_breached": 0, "escalated": 0}
    rows = db.scalars(select(Ticket).where(Ticket.status.in_(("open", "pending")))).all()
    for t in rows:
        newly = []
        if t.first_response_at is None and not t.first_response_breached and t.first_response_due_at and t.first_response_due_at < at:
            t.first_response_breached = True
            counts["first_response_breached"] += 1
            newly.append("first_response")
        if not t.resolution_breached and t.resolution_due_at and t.resolution_due_at < at:
            t.resolution_breached = True
            counts["resolution_breached"] += 1
            newly.append("resolution")
        if not newly:
            continue
        if t.escalation_level < MAX_ESCALATION:
            t.escalation_level += 1
            t.escalated_at = at
            counts["escalated"] += 1
            if t.priority != "urgent":
                idx = TICKET_PRIORITIES.index(t.priority)
                add_event(db, t, "priority_changed", None, {"from": t.priority, "to": TICKET_PRIORITIES[idx + 1], "reason": "sla_escalation"}, internal=True)
                t.priority = TICKET_PRIORITIES[idx + 1]
        add_event(db, t, "sla_breached", None, {"breached": newly, "escalation_level": t.escalation_level}, internal=True)
        audit(db, "support.sla_breached", tenant_id=t.tenant_id, target_type="ticket", target_id=t.id, detail={"breached": newly, "level": t.escalation_level})
        recipients = []
        if t.assignee_id and (u := db.get(User, t.assignee_id)):
            recipients.append(u.email)
        elif t.department_id:
            recipients = list(db.scalars(select(User.email).join(DepartmentMember, DepartmentMember.user_id == User.id).where(DepartmentMember.department_id == t.department_id)))
        for em in recipients:
            queue_email(db, em, f"[SLA] Ticket #{t.number} breached {', '.join(newly)}", f"Ticket #{t.number} '{t.subject}' needs attention (escalation level {t.escalation_level}).", "sla_breach")
        emit_event(db, "ticket.sla_breached", {"ticket_id": str(t.id), "number": t.number, "breached": newly}, tenant_id=t.tenant_id)
    return counts


# ---------------------------------------------------------------- reports
def _since(days: int) -> datetime:
    return now() - timedelta(days=days)


def report_aging(db: Session) -> dict[str, Any]:
    n = now()
    rows = db.scalars(select(Ticket).where(Ticket.status.in_(("open", "pending", "on_hold")))).all()
    buckets = {"lt_1d": 0, "d1_3": 0, "d3_7": 0, "gt_7d": 0}
    by_priority: dict[str, int] = {p: 0 for p in TICKET_PRIORITIES}
    for t in rows:
        age = (n - t.created_at).total_seconds() / 86400
        buckets["lt_1d" if age < 1 else "d1_3" if age < 3 else "d3_7" if age < 7 else "gt_7d"] += 1
        by_priority[t.priority] += 1
    oldest = sorted(rows, key=lambda t: t.created_at)[:10]
    return {"open_total": len(rows), "buckets": buckets, "by_priority": by_priority,
            "oldest": [{"id": str(t.id), "number": t.number, "subject": t.subject, "priority": t.priority, "age_hours": round((n - t.created_at).total_seconds() / 3600, 1)} for t in oldest]}


def report_sla(db: Session, days: int = 30) -> dict[str, Any]:
    rows = db.scalars(select(Ticket).where(Ticket.created_at >= _since(days))).all()
    fr = {"met": 0, "breached": 0, "pending": 0}
    rs = {"met": 0, "breached": 0, "pending": 0}
    per: dict[str, dict[str, int]] = {p: {"met": 0, "breached": 0} for p in TICKET_PRIORITIES}
    for t in rows:
        st = sla_state(t)
        fr[st["first_response"]] += 1
        rs[st["resolution"]] += 1
        if st["resolution"] in ("met", "breached"):
            per[t.priority][st["resolution"]] += 1
    pct = lambda d: round(100 * d["met"] / (d["met"] + d["breached"]), 1) if d["met"] + d["breached"] else None  # noqa: E731
    return {"days": days, "tickets": len(rows), "first_response": {**fr, "compliance_pct": pct(fr)}, "resolution": {**rs, "compliance_pct": pct(rs)},
            "by_priority": per, "clock": "business_hours_utc" if get_settings().support_sla_business_hours else "24x7"}


def report_first_response(db: Session, days: int = 30) -> dict[str, Any]:
    rows = db.scalars(select(Ticket).where(Ticket.created_at >= _since(days), Ticket.first_response_at.is_not(None))).all()
    mins = [(t.first_response_at - t.created_at).total_seconds() / 60 for t in rows]
    per: dict[str, list[float]] = {}
    for t, m in zip(rows, mins):
        per.setdefault(t.priority, []).append(m)
    f = lambda xs: {"count": len(xs), "avg_minutes": round(sum(xs) / len(xs), 1), "median_minutes": round(median(xs), 1)} if xs else {"count": 0, "avg_minutes": None, "median_minutes": None}  # noqa: E731
    return {"days": days, "overall": f(mins), "by_priority": {p: f(per.get(p, [])) for p in TICKET_PRIORITIES}}


def report_workload(db: Session, days: int = 30) -> dict[str, Any]:
    n = now()
    since = _since(days)
    staff = {}
    for t in db.scalars(select(Ticket).where(or_(Ticket.status.in_(("open", "pending", "on_hold")), Ticket.resolved_at >= since))):
        key = str(t.assignee_id) if t.assignee_id else "unassigned"
        w = staff.setdefault(key, {"user_id": key if key != "unassigned" else None, "open": 0, "breached": 0, "resolved_in_period": 0, "by_priority": {p: 0 for p in TICKET_PRIORITIES}})
        if t.status in ("open", "pending", "on_hold"):
            w["open"] += 1
            w["by_priority"][t.priority] += 1
            if sla_state(t, n)["resolution"] == "breached" or sla_state(t, n)["first_response"] == "breached":
                w["breached"] += 1
        elif t.resolved_at and t.resolved_at >= since:
            w["resolved_in_period"] += 1
    names = _names(db, {uuid.UUID(k) for k in staff if k != "unassigned"})
    for k, w in staff.items():
        w["name"] = names.get(uuid.UUID(k)) if k != "unassigned" else "Unassigned"
    return {"days": days, "staff": sorted(staff.values(), key=lambda w: -w["open"])}
