"""Platform announcements: scheduled publication, audience targeting by role/tenant, per-user read/ack state."""
import uuid
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, Field
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.core.db import get_db
from app.core.deps import Ctx, api_error, client_ip, require
from app.core.rbac import ROLE_DEFS
from app.models.support import Announcement, AnnouncementRead
from app.services.audit import audit

router = APIRouter(prefix="/announcements", tags=["announcements"])


def _now() -> datetime:
    return datetime.now(UTC)


def audience_matches(a: Announcement, ctx: Ctx) -> bool:
    aud = a.audience_roles or {}
    roles, tenants = aud.get("roles") or [], set(aud.get("tenant_ids") or [])
    if roles and ctx.role not in roles:
        return False
    if tenants and str(ctx.tenant_id) not in tenants:  # tenant-targeted notices go to that tenant's members only (staff manage them in the admin list)
        return False
    return True


def _live(a: Announcement, n: datetime) -> bool:
    return a.status == "published" and (a.publish_at is None or a.publish_at <= n) and (a.expires_at is None or a.expires_at > n)


def _dto(a: Announcement, r: AnnouncementRead | None = None, admin: bool = False) -> dict:
    out = {"id": str(a.id), "title": a.title, "body": a.body, "severity": a.severity, "publish_at": a.publish_at, "expires_at": a.expires_at,
           "require_ack": a.require_ack, "created_at": a.created_at, "read": bool(r), "acknowledged": bool(r and r.acked_at)}
    if admin:
        out.update({"status": a.status, "audience": a.audience_roles or {}, "state": "scheduled" if a.status == "published" and a.publish_at and a.publish_at > _now() else a.status})
    return out


@router.get("/feed")
def feed(ctx: Ctx = Depends(require("announcements:read")), db: Session = Depends(get_db)):
    n = _now()
    rows = db.scalars(select(Announcement).where(Announcement.status == "published", or_(Announcement.publish_at.is_(None), Announcement.publish_at <= n),
                                                 or_(Announcement.expires_at.is_(None), Announcement.expires_at > n)).order_by(Announcement.created_at.desc()).limit(100)).all()
    mine = [a for a in rows if audience_matches(a, ctx)]
    reads = {r.announcement_id: r for r in db.scalars(select(AnnouncementRead).where(AnnouncementRead.user_id == ctx.user.id, AnnouncementRead.announcement_id.in_([a.id for a in mine] or [uuid.uuid4()])))}
    items = [_dto(a, reads.get(a.id)) for a in mine][:50]
    return {"unread": sum(1 for i in items if not i["read"]), "pending_ack": sum(1 for i in items if i["require_ack"] and not i["acknowledged"]), "items": items}


def _visible(db: Session, ctx: Ctx, ann_id: uuid.UUID) -> Announcement:
    a = db.get(Announcement, ann_id)
    if not a or not _live(a, _now()) or not audience_matches(a, ctx):
        raise api_error(404, "not_found", "Announcement not found.")
    return a


def _mark(db: Session, ctx: Ctx, a: Announcement, ack: bool) -> None:
    r = db.get(AnnouncementRead, (a.id, ctx.user.id))
    if not r:
        r = AnnouncementRead(announcement_id=a.id, user_id=ctx.user.id, read_at=_now())
        db.add(r)
    if ack and not r.acked_at:
        r.acked_at = _now()
    db.commit()


@router.post("/{ann_id}/read")
def mark_read(ann_id: uuid.UUID, ctx: Ctx = Depends(require("announcements:read")), db: Session = Depends(get_db)):
    _mark(db, ctx, _visible(db, ctx, ann_id), False)
    return {"status": "ok"}


@router.post("/{ann_id}/ack")
def acknowledge(ann_id: uuid.UUID, ctx: Ctx = Depends(require("announcements:read")), db: Session = Depends(get_db)):
    _mark(db, ctx, _visible(db, ctx, ann_id), True)
    return {"status": "ok"}


# ---- management ----
class AnnouncementIn(BaseModel):
    title: str = Field(min_length=3, max_length=200)
    body: str = Field(min_length=1, max_length=5000)
    severity: str = Field(default="info", pattern="^(info|warning|critical)$")
    status: str = Field(default="draft", pattern="^(draft|published|archived)$")
    publish_at: datetime | None = None
    expires_at: datetime | None = None
    roles: list[str] = Field(default_factory=list, max_length=10)
    tenant_ids: list[uuid.UUID] = Field(default_factory=list, max_length=200)
    require_ack: bool = False


def _platform(ctx: Ctx) -> None:
    if not ctx.is_platform:
        raise api_error(403, "forbidden", "Platform staff only.")


def _audience(body: AnnouncementIn) -> dict:
    bad = [r for r in body.roles if r not in ROLE_DEFS]
    if bad:
        raise api_error(422, "validation_error", f"Unknown roles: {', '.join(bad)}")
    if body.expires_at and body.publish_at and body.expires_at <= body.publish_at:
        raise api_error(422, "validation_error", "expires_at must be after publish_at.")
    return {"roles": sorted(set(body.roles)), "tenant_ids": sorted({str(t) for t in body.tenant_ids})}


@router.get("")
def manage_list(ctx: Ctx = Depends(require("announcements:write")), db: Session = Depends(get_db)):
    _platform(ctx)
    return [_dto(a, admin=True) for a in db.scalars(select(Announcement).order_by(Announcement.created_at.desc()).limit(200))]


@router.post("", status_code=201)
def create(body: AnnouncementIn, request: Request, ctx: Ctx = Depends(require("announcements:write")), db: Session = Depends(get_db)):
    _platform(ctx)
    a = Announcement(title=body.title.strip(), body=body.body, severity=body.severity, status=body.status, publish_at=body.publish_at, expires_at=body.expires_at,
                     audience_roles=_audience(body), require_ack=body.require_ack, created_by=ctx.user.id)
    db.add(a)
    db.flush()
    audit(db, "announcement.created", actor=ctx.user.id, target_type="announcement", target_id=a.id, detail={"status": a.status, "audience": a.audience_roles}, ip=client_ip(request))
    db.commit()
    return {"id": str(a.id)}


@router.put("/{ann_id}")
def update(ann_id: uuid.UUID, body: AnnouncementIn, request: Request, ctx: Ctx = Depends(require("announcements:write")), db: Session = Depends(get_db)):
    _platform(ctx)
    a = db.get(Announcement, ann_id)
    if not a:
        raise api_error(404, "not_found", "Announcement not found.")
    a.title, a.body, a.severity, a.status = body.title.strip(), body.body, body.severity, body.status
    a.publish_at, a.expires_at, a.audience_roles, a.require_ack = body.publish_at, body.expires_at, _audience(body), body.require_ack
    audit(db, "announcement.updated", actor=ctx.user.id, target_type="announcement", target_id=a.id, detail={"status": a.status}, ip=client_ip(request))
    db.commit()
    return {"status": "ok"}


@router.delete("/{ann_id}")
def delete(ann_id: uuid.UUID, request: Request, ctx: Ctx = Depends(require("announcements:manage")), db: Session = Depends(get_db)):
    a = db.get(Announcement, ann_id)
    if not a:
        raise api_error(404, "not_found", "Announcement not found.")
    db.delete(a)
    audit(db, "announcement.deleted", actor=ctx.user.id, target_type="announcement", target_id=ann_id, ip=client_ip(request))
    db.commit()
    return {"status": "ok"}


@router.get("/{ann_id}/stats")
def stats(ann_id: uuid.UUID, ctx: Ctx = Depends(require("announcements:write")), db: Session = Depends(get_db)):
    _platform(ctx)
    if not db.get(Announcement, ann_id):
        raise api_error(404, "not_found", "Announcement not found.")
    reads, acks = db.execute(select(func.count(), func.count(AnnouncementRead.acked_at)).where(AnnouncementRead.announcement_id == ann_id)).one()
    return {"reads": reads, "acknowledged": acks}
