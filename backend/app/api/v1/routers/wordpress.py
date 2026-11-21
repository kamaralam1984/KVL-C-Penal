"""WordPress toolkit for wordpress apps: status, plugin/theme updates from wordpress.org, rollback, maintenance mode."""
import uuid
from typing import Any, Literal

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session

from app.core import ratelimit
from app.core.db import get_db
from app.core.deps import Ctx, api_error, client_ip, require
from app.models.hosting import Application
from app.models.infra import AgentJob
from app.services import wporg
from app.services.access import get_visible
from app.services.apps import agent_name
from app.services.audit import audit
from app.services.jobs import enqueue_job
from app.services.redact import redact_text
from app.services.resources import job_ok

router = APIRouter(prefix="/apps/{app_id}/wordpress", tags=["wordpress"])
OPS = ("wp.status", "wp.update_item", "wp.rollback_item", "wp.maintenance")


class ItemIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    kind: Literal["plugin", "theme"]
    slug: str = Field(pattern=r"^[a-z0-9][a-z0-9-]{0,99}$")


class MaintIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    enabled: bool


def _wp(db: Session, ctx: Ctx, aid: uuid.UUID) -> Application:
    a = get_visible(db, ctx, Application, aid, "Application")
    if a.runtime != "wordpress":
        raise api_error(409, "not_wordpress", "This application is not a WordPress site.")
    if not a.server_id or not a.current_release:
        raise api_error(409, "not_deployed", "Deploy the WordPress site first.")
    return a


def _enqueue(db: Session, ctx: Ctx, a: Application, op: str, params: dict[str, Any]) -> dict[str, Any]:
    ratelimit.enforce(f"wp:{ctx.user.id}", limit=30, window=60)
    job = enqueue_job(db, a.server_id, op, {"app": agent_name(a), **params}, tenant_id=a.tenant_id, requested_by=ctx.user.id)
    return {"job_id": str(job.id), "status": job.status}


@router.post("/status", status_code=202)
def status(app_id: uuid.UUID, ctx: Ctx = Depends(require("apps:read")), db: Session = Depends(get_db)):
    a = _wp(db, ctx, app_id)
    out = _enqueue(db, ctx, a, "wp.status", {})
    db.commit()
    return out


@router.post("/update", status_code=202)
def update(app_id: uuid.UUID, body: ItemIn, request: Request, ctx: Ctx = Depends(require("apps:write")), db: Session = Depends(get_db)):
    a = _wp(db, ctx, app_id)
    rel = wporg.latest(body.kind, body.slug)
    if rel is None:
        raise api_error(409, "not_on_wordpress_org", f"No official wordpress.org package was found for {body.kind} '{body.slug}'. Update it from the WordPress dashboard.")
    out = _enqueue(db, ctx, a, "wp.update_item", {"kind": body.kind, "slug": body.slug, "package_url": rel["download"]})
    audit(db, "wordpress.update", actor=ctx.user.id, tenant_id=a.tenant_id, target_type="application", target_id=a.id, detail={**body.model_dump(), "version": rel["version"]}, ip=client_ip(request))
    db.commit()
    return {**out, "version": rel["version"]}


@router.post("/rollback", status_code=202)
def rollback(app_id: uuid.UUID, body: ItemIn, request: Request, ctx: Ctx = Depends(require("apps:write")), db: Session = Depends(get_db)):
    a = _wp(db, ctx, app_id)
    out = _enqueue(db, ctx, a, "wp.rollback_item", body.model_dump())
    audit(db, "wordpress.rollback", actor=ctx.user.id, tenant_id=a.tenant_id, target_type="application", target_id=a.id, detail=body.model_dump(), ip=client_ip(request))
    db.commit()
    return out


@router.post("/maintenance", status_code=202)
def maintenance(app_id: uuid.UUID, body: MaintIn, request: Request, ctx: Ctx = Depends(require("apps:write")), db: Session = Depends(get_db)):
    a = _wp(db, ctx, app_id)
    out = _enqueue(db, ctx, a, "wp.maintenance", body.model_dump())
    audit(db, "wordpress.maintenance", actor=ctx.user.id, tenant_id=a.tenant_id, target_type="application", target_id=a.id, detail=body.model_dump(), ip=client_ip(request))
    db.commit()
    return out


@router.get("/jobs/{job_id}")
def job(app_id: uuid.UUID, job_id: uuid.UUID, ctx: Ctx = Depends(require("apps:read")), db: Session = Depends(get_db)):
    a = get_visible(db, ctx, Application, app_id, "Application")
    j = db.get(AgentJob, job_id)
    if j is None or j.operation not in OPS or j.tenant_id != a.tenant_id or (j.params or {}).get("app") != agent_name(a):
        raise api_error(404, "not_found", "Request not found.")
    ok = job_ok(j)
    if ok is None:
        return {"status": j.status}
    res = j.result or {}
    if not ok:
        return {"status": "failed", "code": res.get("status"), "message": redact_text(str(res.get("message") or j.error or "The operation failed."))[:300]}
    if j.operation != "wp.status":
        return {"status": "succeeded", "result": res.get("status"), **{k: res[k] for k in ("kind", "slug", "from", "to", "backup", "restored") if k in res}}
    core = (res.get("core") or {}).get("version")
    latest_core = wporg.latest_core()

    def items(kind: str) -> list[dict[str, Any]]:
        out = []
        for x in (res.get(f"{kind}s") or [])[:300]:
            rel = wporg.latest(kind, x.get("slug", "")) if x.get("managed") else None
            out.append({"slug": x.get("slug"), "name": x.get("name"), "version": x.get("version"), "latest": rel["version"] if rel else None,
                        "update": bool(rel and wporg.newer(x.get("version"), rel["version"])), "managed": bool(x.get("managed"))})
        return out

    return {"status": "succeeded", "core": {"version": core, "latest": latest_core, "update": wporg.newer(core, latest_core)},
            "plugins": items("plugin"), "themes": items("theme"), "maintenance": bool(res.get("maintenance"))}
