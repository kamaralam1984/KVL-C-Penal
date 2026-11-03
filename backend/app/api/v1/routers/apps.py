import uuid
from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.db import get_db
from app.core.deps import Ctx, api_error, client_ip, require
from app.core.security import encrypt_secret
from app.models.hosting import AppDeployment, AppEnvVar, Application, ResourceEvent, ServerRuntime, Website
from app.models.infra import AgentJob, Server
from app.models.tenant import Tenant, TenantQuota
from app.ops.apps import BRANCH_RE, ENV_NAME_RE, GIT_HOSTS, HEALTH_RE, check_repo_url
from app.services import apps as svc
from app.services.access import get_visible, scope_stmt, tenant_for_create
from app.services.audit import audit
from app.services.jobs import default_server, enqueue_job
from app.services.redact import redact_text
from app.services.resources import job_ok, job_view, log_event
from app.services.tenants import consume_quota

router = APIRouter(prefix="/apps", tags=["applications"])
MASK = "••••••••"


def dto(db: Session, a: Application, detail: bool = False) -> dict[str, Any]:
    out = {
        "id": str(a.id), "tenant_id": str(a.tenant_id), "server_id": str(a.server_id) if a.server_id else None,
        "website_id": str(a.website_id) if a.website_id else None, "name": a.name, "slug": a.slug, "runtime": a.runtime,
        "runtime_version": a.runtime_version, "repo_url": a.repo_url, "branch": a.branch, "entrypoint": a.entrypoint,
        "health_path": a.health_path, "port": a.port, "status": a.status, "provision_state": a.provision_state,
        "provision_message": a.provision_message, "current_release": a.current_release, "previous_release": a.previous_release,
        "controllable": a.runtime in svc.PROCESS_RUNTIMES, "created_at": a.created_at,
    }
    if detail:
        out["usage"] = a.usage
        out["last_job"] = job_view(db, a.last_job_id)
        out["env_count"] = db.scalar(select(func.count()).select_from(AppEnvVar).where(AppEnvVar.app_id == a.id))
    return out


def dep_dto(d: AppDeployment) -> dict:
    return {"id": str(d.id), "release_id": d.release_id, "kind": d.kind, "branch": d.branch, "commit": d.commit, "status": d.status, "is_live": d.is_live, "error": d.error, "created_at": d.created_at, "finished_at": d.finished_at}


class AppIn(BaseModel):
    name: str = Field(min_length=2, max_length=120)
    runtime: str = Field(pattern="^(php|wordpress|nodejs|python)$")
    runtime_version: str = Field(pattern=r"^\d{1,2}(\.\d{1,2})?$")
    repo_url: str | None = Field(default=None, max_length=300)
    branch: str = Field(default="main", max_length=100)
    entrypoint: str | None = Field(default=None, max_length=120)
    health_path: str = Field(default="/", max_length=200)
    website_id: uuid.UUID | None = None
    tenant_id: uuid.UUID | None = None


class AppPatch(BaseModel):
    name: str | None = Field(default=None, min_length=2, max_length=120)
    repo_url: str | None = Field(default=None, max_length=300)
    branch: str | None = Field(default=None, max_length=100)
    entrypoint: str | None = Field(default=None, max_length=120)
    health_path: str | None = Field(default=None, max_length=200)
    website_id: uuid.UUID | None = None
    unlink_website: bool = False


class DeployIn(BaseModel):
    branch: str | None = Field(default=None, max_length=100)


class RollbackIn(BaseModel):
    to_release: str | None = Field(default=None, pattern=r"^\d{14}-[a-f0-9]{6}$")


class ProcessIn(BaseModel):
    action: str = Field(pattern="^(start|stop|restart|status)$")


class EnvIn(BaseModel):
    name: str = Field(pattern=r"^[A-Z][A-Z0-9_]{0,62}$")
    value: str = Field(max_length=4096)


class LogsIn(BaseModel):
    stream: str = Field(default="out", pattern="^(out|err)$")
    page: int = Field(default=1, ge=1, le=10000)
    page_size: int = Field(default=100, ge=1, le=500)


def _validate_repo(url: str | None, branch: str | None) -> None:
    if url is not None:
        try:
            check_repo_url(url)
        except ValueError as e:
            raise api_error(422, "invalid_repo_url", str(e)) from None
    if branch is not None and not BRANCH_RE.fullmatch(branch):
        raise api_error(422, "invalid_branch", "Invalid branch name.")


def _app(db: Session, ctx: Ctx, aid: uuid.UUID, sync: bool = True) -> Application:
    a = get_visible(db, ctx, Application, aid, "Application")
    if sync:
        svc.sync_app(db, a)
        db.commit()
    return a


def _link_website(db: Session, ctx: Ctx, a: Application, wid: uuid.UUID) -> None:
    w = get_visible(db, ctx, Website, wid, "Website")
    if w.tenant_id != a.tenant_id:
        raise api_error(404, "not_found", "Website not found.")
    if w.status == "archived":
        raise api_error(409, "website_archived", "Cannot link an archived website.")
    a.website_id = w.id
    if a.runtime in svc.PROCESS_RUNTIMES and a.port:
        w.runtime_type, w.proxy_port, w.runtime_version = "proxy", a.port, None
        w.provision_message = "Linked to an application: re-provision to proxy traffic to it."
    elif a.runtime in {"php", "wordpress"}:
        w.runtime_type, w.runtime_version = "php", a.runtime_version
        w.provision_message = "Linked to a PHP application: re-provision to apply."
    log_event(db, w.tenant_id, "website", w.id, ctx.user.id, "website.app_linked", f"Application {a.name} linked")


# ----------------------------------------------------------------- runtime catalog
@router.get("/runtimes")
def runtimes(server_id: uuid.UUID | None = None, ctx: Ctx = Depends(require("apps:read")), db: Session = Depends(get_db)):
    srv = db.get(Server, server_id) if server_id and ctx.is_platform else default_server(db)
    if srv is None:
        return {"state": "not_configured", "message": "No server is registered yet.", "server_id": None, "installed": {}, "supported": svc.SUPPORTED_VERSIONS}
    if svc.ingest_runtime_reports(db, srv.id):
        db.commit()
    cat = svc.catalog(db, srv.id)
    reported = any(cat.values())
    return {"state": "ok" if reported else "not_reported", "message": None if reported else "The server has not reported installed runtimes yet. Ask an administrator to refresh the runtime inventory.",
            "server_id": str(srv.id), "installed": cat, "supported": svc.SUPPORTED_VERSIONS}


@router.post("/runtimes/refresh")
def refresh_runtimes(request: Request, ctx: Ctx = Depends(require("apps:manage")), db: Session = Depends(get_db)):
    if not ctx.is_platform:
        raise api_error(403, "forbidden", "Platform staff only.")
    srv = default_server(db)
    if srv is None:
        raise api_error(409, "no_server", "No server is registered yet.")
    job = enqueue_job(db, srv.id, "apps.detect_runtimes", {}, requested_by=ctx.user.id)
    audit(db, "apps.runtimes_refresh", actor=ctx.user.id, target_type="server", target_id=srv.id, ip=client_ip(request))
    db.commit()
    return {"job_id": str(job.id), "status": job.status}


# ----------------------------------------------------------------- CRUD
@router.get("")
def list_apps(runtime: str | None = None, q: str | None = None, tenant_id: uuid.UUID | None = None, limit: int = 25, offset: int = 0, ctx: Ctx = Depends(require("apps:read")), db: Session = Depends(get_db)):
    stmt = scope_stmt(select(Application), Application, ctx, tenant_id).where(Application.provision_state != "archived")
    if runtime:
        stmt = stmt.where(Application.runtime == runtime)
    if q:
        stmt = stmt.where(Application.name.ilike(f"%{q}%"))
    total = db.scalar(select(func.count()).select_from(stmt.subquery()))
    rows = list(db.scalars(stmt.order_by(Application.created_at.desc()).limit(min(limit, 100)).offset(offset)))
    for a in rows:
        svc.sync_app(db, a)
    db.commit()
    return {"total": total, "items": [dto(db, a) for a in rows]}


@router.post("", status_code=201)
def create_app(body: AppIn, request: Request, ctx: Ctx = Depends(require("apps:write")), db: Session = Depends(get_db)):
    tid = tenant_for_create(ctx, body.tenant_id)
    if db.get(Tenant, tid) is None:
        raise api_error(404, "not_found", "Tenant not found.")
    repo = body.repo_url or (svc.WORDPRESS_REPO if body.runtime == "wordpress" else None)
    branch = body.branch if body.branch != "main" or body.runtime != "wordpress" else "6.8"
    _validate_repo(repo, branch)
    entry = svc.validate_entrypoint(body.runtime, body.entrypoint)
    from app.ops.apps import HEALTH_RE as H
    if not H.fullmatch(body.health_path):
        raise api_error(422, "invalid_health_path", "Health check path must start with / and contain only URL-safe characters.")
    server = default_server(db)
    note = svc.validate_runtime(db, server.id if server else None, body.runtime, body.runtime_version)
    consume_quota(db, tid, "apps")
    slug, i = svc.slugify(body.name), 1
    base = slug
    while db.scalar(select(Application.id).where(Application.tenant_id == tid, Application.slug == slug)):
        i += 1
        slug = f"{base}-{i}"
    a = Application(tenant_id=tid, name=body.name.strip(), slug=slug, runtime=body.runtime, runtime_version=body.runtime_version, repo_url=repo, branch=branch, entrypoint=entry,
                    health_path=body.health_path, server_id=server.id if server else None)
    if body.runtime in svc.PROCESS_RUNTIMES:
        a.port, a.canary_port = svc.allocate_ports(db, a.server_id)
    if server is None:
        a.provision_state, a.provision_message = "provisioning_blocked", "No server is registered yet. Deploy once one exists."
    else:
        a.provision_state, a.provision_message = "ready", note or "Ready to deploy."
    db.add(a)
    db.flush()
    if body.website_id:
        _link_website(db, ctx, a, body.website_id)
    log_event(db, tid, "app", a.id, ctx.user.id, "app.created", f"Application {a.name} ({a.runtime} {a.runtime_version}) created")
    audit(db, "app.created", actor=ctx.user.id, tenant_id=tid, target_type="app", target_id=a.id, ip=client_ip(request))
    db.commit()
    return dto(db, a, True)


@router.get("/{app_id}")
def get_app(app_id: uuid.UUID, ctx: Ctx = Depends(require("apps:read")), db: Session = Depends(get_db)):
    return dto(db, _app(db, ctx, app_id), True)


@router.patch("/{app_id}")
def update_app(app_id: uuid.UUID, body: AppPatch, request: Request, ctx: Ctx = Depends(require("apps:write")), db: Session = Depends(get_db)):
    a = _app(db, ctx, app_id, sync=False)
    _validate_repo(body.repo_url, body.branch)
    if body.name:
        a.name = body.name.strip()
    if body.repo_url is not None:
        a.repo_url = body.repo_url
    if body.branch:
        a.branch = body.branch
    if body.entrypoint is not None:
        a.entrypoint = svc.validate_entrypoint(a.runtime, body.entrypoint)
    if body.health_path:
        if not HEALTH_RE.fullmatch(body.health_path):
            raise api_error(422, "invalid_health_path", "Invalid health check path.")
        a.health_path = body.health_path
    if body.website_id:
        _link_website(db, ctx, a, body.website_id)
    elif body.unlink_website:
        a.website_id = None
    log_event(db, a.tenant_id, "app", a.id, ctx.user.id, "app.updated", "Application settings updated")
    audit(db, "app.updated", actor=ctx.user.id, tenant_id=a.tenant_id, target_type="app", target_id=a.id, ip=client_ip(request))
    db.commit()
    return dto(db, a, True)


@router.delete("/{app_id}")
def delete_app(app_id: uuid.UUID, confirm: str, request: Request, ctx: Ctx = Depends(require("apps:manage")), db: Session = Depends(get_db)):
    a = _app(db, ctx, app_id, sync=False)
    if confirm != a.slug:
        raise api_error(422, "confirmation_required", "Type the application slug to confirm deletion.")
    if a.server_id and a.current_release:
        enqueue_job(db, a.server_id, "apps.remove", {"app": svc.agent_name(a)}, tenant_id=a.tenant_id, requested_by=ctx.user.id)
    a.provision_state, a.status = "archived", "stopped"
    consume_quota(db, a.tenant_id, "apps", -1)
    audit(db, "app.deleted", actor=ctx.user.id, tenant_id=a.tenant_id, target_type="app", target_id=a.id, ip=client_ip(request))
    log_event(db, a.tenant_id, "app", a.id, ctx.user.id, "app.deleted", "Application deleted")
    db.commit()
    return {"status": "archived"}


# ----------------------------------------------------------------- deployments
@router.post("/{app_id}/deploy", status_code=202)
def deploy(app_id: uuid.UUID, body: DeployIn, request: Request, ctx: Ctx = Depends(require("apps:write")), db: Session = Depends(get_db)):
    a = _app(db, ctx, app_id)
    if body.branch:
        _validate_repo(None, body.branch)
    try:
        dep = svc.start_deploy(db, a, ctx.user.id, body.branch)
    except Exception:
        db.commit()
        raise
    audit(db, "app.deploy", actor=ctx.user.id, tenant_id=a.tenant_id, target_type="app", target_id=a.id, detail={"release": dep.release_id}, ip=client_ip(request))
    db.commit()
    return dep_dto(dep)


@router.get("/{app_id}/deployments")
def deployments(app_id: uuid.UUID, limit: int = 20, offset: int = 0, ctx: Ctx = Depends(require("apps:read")), db: Session = Depends(get_db)):
    a = _app(db, ctx, app_id)
    q = select(AppDeployment).where(AppDeployment.app_id == a.id)
    total = db.scalar(select(func.count()).select_from(q.subquery()))
    rows = db.scalars(q.order_by(AppDeployment.created_at.desc()).limit(min(limit, 100)).offset(offset))
    return {"total": total, "items": [dep_dto(d) for d in rows]}


@router.post("/{app_id}/rollback", status_code=202)
def rollback(app_id: uuid.UUID, body: RollbackIn, request: Request, ctx: Ctx = Depends(require("apps:manage")), db: Session = Depends(get_db)):
    a = _app(db, ctx, app_id)
    dep = svc.start_rollback(db, a, ctx.user.id, body.to_release)
    audit(db, "app.rollback", actor=ctx.user.id, tenant_id=a.tenant_id, target_type="app", target_id=a.id, detail={"to": dep.release_id}, ip=client_ip(request))
    db.commit()
    return dep_dto(dep)


# ----------------------------------------------------------------- process control
@router.post("/{app_id}/process", status_code=202)
def process(app_id: uuid.UUID, body: ProcessIn, request: Request, ctx: Ctx = Depends(require("apps:manage")), db: Session = Depends(get_db)):
    a = _app(db, ctx, app_id)
    job = svc.start_process(db, a, ctx.user.id, body.action)
    audit(db, f"app.process_{body.action}", actor=ctx.user.id, tenant_id=a.tenant_id, target_type="app", target_id=a.id, ip=client_ip(request))
    db.commit()
    return {"job_id": str(job.id), "status": job.status}


@router.post("/{app_id}/usage/refresh", status_code=202)
def refresh_usage(app_id: uuid.UUID, ctx: Ctx = Depends(require("apps:read")), db: Session = Depends(get_db)):
    a = _app(db, ctx, app_id)
    if not a.server_id or not a.current_release:
        raise api_error(409, "not_deployed", "Deploy the application first.")
    job = enqueue_job(db, a.server_id, "apps.usage", {"app": svc.agent_name(a)}, tenant_id=a.tenant_id, requested_by=ctx.user.id)
    a.last_job_id = job.id
    db.commit()
    return {"job_id": str(job.id), "status": job.status}


@router.get("/{app_id}/usage")
def usage(app_id: uuid.UUID, ctx: Ctx = Depends(require("apps:read")), db: Session = Depends(get_db)):
    a = _app(db, ctx, app_id)
    q = db.scalars(select(TenantQuota).where(TenantQuota.tenant_id == a.tenant_id, TenantQuota.resource.in_(["apps", "disk_mb"])))
    return {"usage": a.usage, "note": None if a.usage else "not_reported: request a usage refresh once the application is deployed", "quotas": [{"resource": x.resource, "limit": x.limit_value, "used": x.used_value} for x in q]}


# ----------------------------------------------------------------- logs (job based, paginated, redacted)
@router.post("/{app_id}/logs", status_code=202)
def request_logs(app_id: uuid.UUID, body: LogsIn, ctx: Ctx = Depends(require("apps:read")), db: Session = Depends(get_db)):
    a = _app(db, ctx, app_id, sync=False)
    if a.runtime not in svc.PROCESS_RUNTIMES:
        raise api_error(409, "no_process_logs", "PHP and WordPress logs are available from the website error log, not the app process.")
    if not a.server_id or not a.current_release:
        raise api_error(409, "not_deployed", "Deploy the application first.")
    job = enqueue_job(db, a.server_id, "apps.logs", {"app": svc.agent_name(a), **body.model_dump()}, tenant_id=a.tenant_id, requested_by=ctx.user.id)
    db.commit()
    return {"job_id": str(job.id), "status": job.status}


@router.get("/{app_id}/logs/{job_id}")
def get_logs(app_id: uuid.UUID, job_id: uuid.UUID, ctx: Ctx = Depends(require("apps:read")), db: Session = Depends(get_db)):
    a = _app(db, ctx, app_id, sync=False)
    job = db.get(AgentJob, job_id)
    if job is None or job.operation != "apps.logs" or job.tenant_id != a.tenant_id or (job.params or {}).get("app") != svc.agent_name(a):
        raise api_error(404, "not_found", "Log request not found.")
    ok = job_ok(job)
    if ok is None:
        return {"status": job.status, "lines": []}
    if not ok:
        return {"status": "failed", "lines": [], "message": redact_text(str((job.result or {}).get("message") or job.error or "Could not read logs."))}
    secrets_ = [v for v in svc.env_dict(db, a).values() if len(v) >= 6]
    res = job.result or {}
    return {"status": "succeeded", "lines": [redact_text(str(x)[:2000], secrets_) for x in (res.get("lines") or [])[:500]], "page": res.get("page"), "page_size": res.get("page_size"), "has_more": bool(res.get("has_more")), "total_lines": res.get("total_lines")}


# ----------------------------------------------------------------- env vars
@router.get("/{app_id}/env")
def list_env(app_id: uuid.UUID, ctx: Ctx = Depends(require("apps:read")), db: Session = Depends(get_db)):
    a = _app(db, ctx, app_id, sync=False)
    rows = db.scalars(select(AppEnvVar).where(AppEnvVar.app_id == a.id).order_by(AppEnvVar.name))
    return [{"name": r.name, "value": MASK, "masked": True, "updated_at": r.updated_at} for r in rows]


@router.put("/{app_id}/env")
def set_env(app_id: uuid.UUID, body: EnvIn, request: Request, ctx: Ctx = Depends(require("apps:write")), db: Session = Depends(get_db)):
    a = _app(db, ctx, app_id, sync=False)
    svc.check_env_name(body.name)
    if any(c in body.value for c in "\x00\r\n"):
        raise api_error(422, "invalid_env_value", "Values cannot contain NUL or line breaks.")
    row = db.scalar(select(AppEnvVar).where(AppEnvVar.app_id == a.id, AppEnvVar.name == body.name))
    if row is None:
        if db.scalar(select(func.count()).select_from(AppEnvVar).where(AppEnvVar.app_id == a.id)) >= 100:
            raise api_error(409, "too_many_env", "Maximum of 100 environment variables.")
        db.add(AppEnvVar(app_id=a.id, tenant_id=a.tenant_id, name=body.name, value_encrypted=encrypt_secret(body.value)))
    else:
        row.value_encrypted = encrypt_secret(body.value)
    audit(db, "app.env_set", actor=ctx.user.id, tenant_id=a.tenant_id, target_type="app", target_id=a.id, detail={"name": body.name}, ip=client_ip(request))
    log_event(db, a.tenant_id, "app", a.id, ctx.user.id, "app.env_set", f"Environment variable {body.name} set (redeploy to apply)")
    db.commit()
    return {"name": body.name, "value": MASK, "masked": True, "needs_redeploy": True}


@router.delete("/{app_id}/env/{name}", status_code=204)
def delete_env(app_id: uuid.UUID, name: str, request: Request, ctx: Ctx = Depends(require("apps:write")), db: Session = Depends(get_db)):
    a = _app(db, ctx, app_id, sync=False)
    row = db.scalar(select(AppEnvVar).where(AppEnvVar.app_id == a.id, AppEnvVar.name == name))
    if row is None:
        raise api_error(404, "not_found", "Variable not found.")
    db.delete(row)
    audit(db, "app.env_deleted", actor=ctx.user.id, tenant_id=a.tenant_id, target_type="app", target_id=a.id, detail={"name": name}, ip=client_ip(request))
    db.commit()


@router.post("/{app_id}/env/{name}/reveal")
def reveal_env(app_id: uuid.UUID, name: str, request: Request, ctx: Ctx = Depends(require("apps:manage")), db: Session = Depends(get_db)):
    from app.core.security import decrypt_secret

    a = _app(db, ctx, app_id, sync=False)
    row = db.scalar(select(AppEnvVar).where(AppEnvVar.app_id == a.id, AppEnvVar.name == name))
    if row is None:
        raise api_error(404, "not_found", "Variable not found.")
    audit(db, "app.env_revealed", actor=ctx.user.id, tenant_id=a.tenant_id, target_type="app", target_id=a.id, detail={"name": name}, ip=client_ip(request))
    db.commit()
    return {"name": name, "value": decrypt_secret(row.value_encrypted)}


@router.get("/{app_id}/activity")
def activity(app_id: uuid.UUID, limit: int = 50, ctx: Ctx = Depends(require("apps:read")), db: Session = Depends(get_db)):
    a = _app(db, ctx, app_id, sync=False)
    rows = db.scalars(select(ResourceEvent).where(ResourceEvent.resource_type == "app", ResourceEvent.resource_id == a.id).order_by(ResourceEvent.created_at.desc()).limit(min(limit, 200)))
    return [{"action": e.action, "summary": e.summary, "created_at": e.created_at} for e in rows]
