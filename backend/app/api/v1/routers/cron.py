"""Customer cron jobs (scheduled HTTPS requests to the tenant's own domains). Uses website permissions."""
import uuid

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.db import get_db
from app.core.deps import Ctx, client_ip, require
from app.models.cron import CronJob, CronRun
from app.models.hosting import Website
from app.services import cron as svc
from app.services.access import get_visible, scope_stmt
from app.services.audit import audit

router = APIRouter(prefix="/cron-jobs", tags=["cron"])
_NAME = r"^[^\x00-\x1f<>]{1,80}$"


class CronIn(BaseModel):
    website_id: uuid.UUID
    name: str = Field(min_length=1, max_length=80, pattern=_NAME)
    url: str = Field(min_length=10, max_length=500)
    schedule: str = Field(min_length=9, max_length=80)
    enabled: bool = True


class CronPatch(BaseModel):
    name: str | None = Field(None, min_length=1, max_length=80, pattern=_NAME)
    url: str | None = Field(None, min_length=10, max_length=500)
    schedule: str | None = Field(None, min_length=9, max_length=80)
    enabled: bool | None = None


@router.get("")
def list_jobs(website_id: uuid.UUID | None = None, tenant_id: uuid.UUID | None = None, ctx: Ctx = Depends(require("websites:read")), db: Session = Depends(get_db)):
    stmt = scope_stmt(select(CronJob), CronJob, ctx, tenant_id)
    if website_id:
        stmt = stmt.where(CronJob.website_id == website_id)
    return {"items": [svc.dto(j) for j in db.scalars(stmt.order_by(CronJob.created_at)).all()], "limits": {"per_account": svc.MAX_JOBS_PER_TENANT, "max_per_hour": 4}}


@router.post("", status_code=201)
def create_job(body: CronIn, request: Request, ctx: Ctx = Depends(require("websites:write")), db: Session = Depends(get_db)):
    site = get_visible(db, ctx, Website, body.website_id, "Website")
    j = svc.create(db, site, body.name, body.url, body.schedule, body.enabled)
    audit(db, "cron.created", actor=ctx.user.id, tenant_id=j.tenant_id, target_type="cron_job", target_id=j.id, detail={"name": j.name, "url": j.url, "schedule": j.schedule}, ip=client_ip(request))
    db.commit()
    return svc.dto(j)


@router.patch("/{job_id}")
def update_job(job_id: uuid.UUID, body: CronPatch, request: Request, ctx: Ctx = Depends(require("websites:write")), db: Session = Depends(get_db)):
    j = get_visible(db, ctx, CronJob, job_id, "Cron job")
    before = svc.dto(j)
    svc.update(db, j, **body.model_dump(exclude_unset=True))
    audit(db, "cron.updated", actor=ctx.user.id, tenant_id=j.tenant_id, target_type="cron_job", target_id=j.id,
          detail={"before": {k: before[k] for k in ("name", "url", "schedule", "enabled")}, "after": body.model_dump(exclude_unset=True)}, ip=client_ip(request))
    db.commit()
    return svc.dto(j)


@router.delete("/{job_id}", status_code=204)
def delete_job(job_id: uuid.UUID, request: Request, ctx: Ctx = Depends(require("websites:write")), db: Session = Depends(get_db)):
    j = get_visible(db, ctx, CronJob, job_id, "Cron job")
    audit(db, "cron.deleted", actor=ctx.user.id, tenant_id=j.tenant_id, target_type="cron_job", target_id=j.id, detail={"name": j.name, "url": j.url}, ip=client_ip(request))
    db.delete(j)
    db.commit()


@router.post("/{job_id}/run")
def run_now(job_id: uuid.UUID, request: Request, ctx: Ctx = Depends(require("websites:write")), db: Session = Depends(get_db)):
    from app.core import ratelimit

    j = get_visible(db, ctx, CronJob, job_id, "Cron job")
    ratelimit.enforce(f"cron-run:{j.id}", limit=5, window=300)
    r = svc.execute(db, j, "manual")
    audit(db, "cron.run", actor=ctx.user.id, tenant_id=j.tenant_id, target_type="cron_job", target_id=j.id, detail={"status": r.status, "http_code": r.http_code}, ip=client_ip(request))
    db.commit()
    return {"run": svc.run_dto(r), "job": svc.dto(j)}


@router.get("/{job_id}/runs")
def runs(job_id: uuid.UUID, ctx: Ctx = Depends(require("websites:read")), db: Session = Depends(get_db)):
    j = get_visible(db, ctx, CronJob, job_id, "Cron job")
    rows = db.scalars(select(CronRun).where(CronRun.job_id == j.id).order_by(CronRun.started_at.desc()).limit(svc.KEEP_RUNS)).all()
    return {"items": [svc.run_dto(r) for r in rows]}
