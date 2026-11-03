"""Knowledge base. Published articles are readable by every authenticated role with kb:read; drafts need kb:write."""
import re
import uuid
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, Query, Request
from pydantic import BaseModel, Field
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.core.db import get_db
from app.core.deps import Ctx, api_error, client_ip, require
from app.models.support import KbArticle, KbCategory, KbFeedback
from app.services.audit import audit

router = APIRouter(prefix="/kb", tags=["knowledge-base"])


def _slug(db: Session, title: str, model, exclude: uuid.UUID | None = None) -> str:
    base = re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")[:80] or "article"
    slug, i = base, 1
    while (hit := db.scalar(select(model.id).where(model.slug == slug))) and hit != exclude:
        i += 1
        slug = f"{base}-{i}"
    return slug


def _dto(a: KbArticle, cats: dict, body: bool = False, writer: bool = False) -> dict:
    out = {"id": str(a.id), "slug": a.slug, "title": a.title, "summary": a.summary, "category_id": str(a.category_id) if a.category_id else None,
           "category": cats.get(a.category_id), "status": a.status, "published_at": a.published_at, "updated_at": a.updated_at,
           "helpful_yes": a.helpful_yes, "helpful_no": a.helpful_no}
    if body:
        out["body"] = a.body
    return out


def _cats(db: Session) -> dict:
    return {c.id: c.name for c in db.scalars(select(KbCategory))}


class CategoryIn(BaseModel):
    name: str = Field(min_length=2, max_length=80)
    description: str | None = Field(default=None, max_length=300)


@router.get("/categories")
def categories(ctx: Ctx = Depends(require("kb:read")), db: Session = Depends(get_db)):
    pub = dict(db.execute(select(KbArticle.category_id, func.count()).where(KbArticle.status == "published").group_by(KbArticle.category_id)).all())
    return [{"id": str(c.id), "name": c.name, "slug": c.slug, "description": c.description, "articles": pub.get(c.id, 0)} for c in db.scalars(select(KbCategory).order_by(KbCategory.name))]


@router.post("/categories", status_code=201)
def create_category(body: CategoryIn, request: Request, ctx: Ctx = Depends(require("kb:write")), db: Session = Depends(get_db)):
    if not ctx.is_platform:
        raise api_error(403, "forbidden", "Platform staff only.")
    if db.scalar(select(KbCategory.id).where(func.lower(KbCategory.name) == body.name.lower())):
        raise api_error(409, "duplicate", "Category exists.")
    c = KbCategory(name=body.name.strip(), slug=_slug(db, body.name, KbCategory), description=body.description)
    db.add(c)
    db.flush()
    audit(db, "kb.category_created", actor=ctx.user.id, target_type="kb_category", target_id=c.id, ip=client_ip(request))
    db.commit()
    return {"id": str(c.id), "slug": c.slug}


@router.delete("/categories/{category_id}")
def delete_category(category_id: uuid.UUID, request: Request, ctx: Ctx = Depends(require("kb:manage")), db: Session = Depends(get_db)):
    c = db.get(KbCategory, category_id)
    if not c:
        raise api_error(404, "not_found", "Category not found.")
    db.delete(c)
    audit(db, "kb.category_deleted", actor=ctx.user.id, target_type="kb_category", target_id=category_id, ip=client_ip(request))
    db.commit()
    return {"status": "ok"}


class ArticleIn(BaseModel):
    title: str = Field(min_length=3, max_length=200)
    summary: str | None = Field(default=None, max_length=300)
    body: str = Field(min_length=1, max_length=50000)
    category_id: uuid.UUID | None = None
    status: str = Field(default="draft", pattern="^(draft|published)$")


class ArticlePatch(BaseModel):
    title: str | None = Field(default=None, min_length=3, max_length=200)
    summary: str | None = Field(default=None, max_length=300)
    body: str | None = Field(default=None, min_length=1, max_length=50000)
    category_id: uuid.UUID | None = None
    status: str | None = Field(default=None, pattern="^(draft|published)$")


def _check_cat(db: Session, cid: uuid.UUID | None) -> None:
    if cid and not db.get(KbCategory, cid):
        raise api_error(404, "not_found", "Category not found.")


@router.get("/articles")
def articles(q: str | None = Query(default=None, max_length=100), category_id: uuid.UUID | None = None, status: str | None = Query(default=None, pattern="^(draft|published)$"),
             limit: int = Query(default=25, ge=1, le=100), offset: int = Query(default=0, ge=0), ctx: Ctx = Depends(require("kb:read")), db: Session = Depends(get_db)):
    writer = ctx.can("kb:write")
    stmt = select(KbArticle)
    if not writer or status == "published":
        stmt = stmt.where(KbArticle.status == "published")
    elif status:
        stmt = stmt.where(KbArticle.status == status)
    if category_id:
        stmt = stmt.where(KbArticle.category_id == category_id)
    if q:
        like = f"%{q.strip().replace('%', '').replace('_', '')}%"
        stmt = stmt.where(or_(KbArticle.title.ilike(like), KbArticle.summary.ilike(like), KbArticle.body.ilike(like)))
    total = db.scalar(select(func.count()).select_from(stmt.subquery()))
    rows = db.scalars(stmt.order_by(KbArticle.updated_at.desc()).limit(limit).offset(offset)).all()
    cats = _cats(db)
    return {"total": total, "items": [_dto(a, cats) for a in rows]}


def _load(db: Session, ctx: Ctx, ref: str) -> KbArticle:
    try:
        cond = KbArticle.id == uuid.UUID(ref)
    except ValueError:
        cond = KbArticle.slug == ref
    a = db.scalar(select(KbArticle).where(cond))
    if not a or (a.status != "published" and not ctx.can("kb:write")):
        raise api_error(404, "not_found", "Article not found.")
    return a


@router.get("/articles/{ref}")
def article(ref: str, ctx: Ctx = Depends(require("kb:read")), db: Session = Depends(get_db)):
    a = _load(db, ctx, ref)
    cats = _cats(db)
    words = {w for w in re.findall(r"[a-z0-9]{4,}", a.title.lower())}
    cands = db.scalars(select(KbArticle).where(KbArticle.status == "published", KbArticle.id != a.id).limit(300)).all()

    def score(o: KbArticle) -> int:
        return (3 if a.category_id and o.category_id == a.category_id else 0) + len(words & set(re.findall(r"[a-z0-9]{4,}", o.title.lower())))

    related = [o for o in sorted(cands, key=score, reverse=True) if score(o) > 0][:5]
    mine = db.get(KbFeedback, (a.id, ctx.user.id))
    out = _dto(a, cats, body=True)
    out["related"] = [_dto(o, cats) for o in related]
    out["my_feedback"] = None if mine is None else ("helpful" if mine.helpful else "not_helpful")
    return out


@router.post("/articles", status_code=201)
def create_article(body: ArticleIn, request: Request, ctx: Ctx = Depends(require("kb:write")), db: Session = Depends(get_db)):
    if not ctx.is_platform:
        raise api_error(403, "forbidden", "Platform staff only.")
    _check_cat(db, body.category_id)
    a = KbArticle(slug=_slug(db, body.title, KbArticle), title=body.title.strip(), summary=body.summary, body=body.body, category_id=body.category_id,
                  status=body.status, author_id=ctx.user.id, published_at=datetime.now(UTC) if body.status == "published" else None)
    db.add(a)
    db.flush()
    audit(db, "kb.article_created", actor=ctx.user.id, target_type="kb_article", target_id=a.id, detail={"status": a.status}, ip=client_ip(request))
    db.commit()
    return {"id": str(a.id), "slug": a.slug}


@router.patch("/articles/{article_id}")
def update_article(article_id: uuid.UUID, body: ArticlePatch, request: Request, ctx: Ctx = Depends(require("kb:write")), db: Session = Depends(get_db)):
    a = db.get(KbArticle, article_id)
    if not a:
        raise api_error(404, "not_found", "Article not found.")
    data = body.model_dump(exclude_unset=True)
    if "category_id" in data:
        _check_cat(db, data["category_id"])
        a.category_id = data["category_id"]
    for k in ("title", "summary", "body"):
        if data.get(k) is not None:
            setattr(a, k, data[k])
    if data.get("status") and data["status"] != a.status:
        a.status = data["status"]
        a.published_at = datetime.now(UTC) if a.status == "published" and not a.published_at else a.published_at
    audit(db, "kb.article_updated", actor=ctx.user.id, target_type="kb_article", target_id=a.id, detail={"status": a.status}, ip=client_ip(request))
    db.commit()
    return {"status": "ok"}


@router.delete("/articles/{article_id}")
def delete_article(article_id: uuid.UUID, request: Request, ctx: Ctx = Depends(require("kb:manage")), db: Session = Depends(get_db)):
    a = db.get(KbArticle, article_id)
    if not a:
        raise api_error(404, "not_found", "Article not found.")
    db.delete(a)
    audit(db, "kb.article_deleted", actor=ctx.user.id, target_type="kb_article", target_id=article_id, ip=client_ip(request))
    db.commit()
    return {"status": "ok"}


class FeedbackIn(BaseModel):
    helpful: bool


@router.post("/articles/{article_id}/feedback")
def feedback(article_id: uuid.UUID, body: FeedbackIn, ctx: Ctx = Depends(require("kb:read")), db: Session = Depends(get_db)):
    a = db.get(KbArticle, article_id)
    if not a or a.status != "published":
        raise api_error(404, "not_found", "Article not found.")
    fb = db.get(KbFeedback, (a.id, ctx.user.id))
    if fb:
        if fb.helpful != body.helpful:
            a.helpful_yes += 1 if body.helpful else -1
            a.helpful_no += -1 if body.helpful else 1
            fb.helpful = body.helpful
    else:
        db.add(KbFeedback(article_id=a.id, user_id=ctx.user.id, helpful=body.helpful))
        if body.helpful:
            a.helpful_yes += 1
        else:
            a.helpful_no += 1
    db.commit()
    return {"helpful_yes": a.helpful_yes, "helpful_no": a.helpful_no}
