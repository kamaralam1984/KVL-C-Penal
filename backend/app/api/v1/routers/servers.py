"""Server registry, agent credentials and operation (job) management. Platform staff only."""
import uuid
from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, Depends, Query, Request
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.db import get_db
from app.core.deps import Ctx, api_error, client_ip, require
from app.models.agents import AgentCredential
from app.models.infra import AgentJob, JOB_STATUSES, Server
from app.ops import OP_META, OPERATIONS
from app.services import agents, monitoring
from app.services.audit import audit
from app.services.jobs import enqueue_job

router = APIRouter(tags=["servers"])


def platform_only(ctx: Ctx) -> Ctx:
    """Infrastructure is never exposed to tenant or reseller accounts, whatever permissions they hold."""
    if not ctx.is_platform:
        raise api_error(403, "forbidden", "Infrastructure is restricted to platform staff.")
    return ctx


def read_ctx(ctx: Ctx = Depends(require("servers:read"))) -> Ctx:
    return platform_only(ctx)


def write_ctx(ctx: Ctx = Depends(require("servers:write"))) -> Ctx:
    return platform_only(ctx)


def manage_ctx(ctx: Ctx = Depends(require("servers:manage"))) -> Ctx:
    return platform_only(ctx)


def get_server_or_404(db: Session, server_id: uuid.UUID) -> Server:
    s = db.get(Server, server_id)
    if s is None:
        raise api_error(404, "not_found", "Server not found.")
    return s


def server_out(db: Session, s: Server, now: datetime | None = None) -> dict[str, Any]:
    creds = db.scalars(select(AgentCredential).where(AgentCredential.server_id == s.id, AgentCredential.status == "active")).all()
    return {
        "id": str(s.id),
        "name": s.name,
        "hostname": s.hostname,
        "status": s.status,
        "availability": monitoring.server_availability(s, now),
        "agent_version": s.agent_version,
        "os_info": s.os_info,
        "labels": s.labels,
        "last_heartbeat_at": s.last_heartbeat_at,
        "registered_at": s.registered_at,
        "created_at": s.created_at,
        "has_active_credential": bool(creds),
        "credential_key_id": creds[0].key_id if creds else None,
    }


def enrollment_info(token: str) -> dict[str, Any]:
    api = get_settings().public_base_url.rstrip("/") + "/api/v1"
    return {
        "token": token,
        "expires_in_hours": agents.ENROLL_TTL_HOURS,
        "command": f"python -m kvl_agent enroll --api-url {api} --token {token}",
        "shown_once": True,
    }


class ServerIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=2, max_length=120, pattern=r"^[A-Za-z0-9][A-Za-z0-9 ._-]*$")
    hostname: str | None = Field(default=None, max_length=255, pattern=r"^[A-Za-z0-9]([A-Za-z0-9.-]*[A-Za-z0-9])?$")
    labels: dict[str, str] | None = None


@router.post("/servers", status_code=201)
def register_server(body: ServerIn, request: Request, ctx: Ctx = Depends(write_ctx), db: Session = Depends(get_db)):
    if db.scalar(select(Server.id).where(Server.name == body.name)):
        raise api_error(409, "server_exists", "A server with this name already exists.")
    labels = dict(list((body.labels or {}).items())[:20]) or None
    s = Server(name=body.name, hostname=body.hostname, labels=labels, status="pending")
    db.add(s)
    db.flush()
    _, token = agents.create_enrollment_token(db, s, ctx.user.id)
    audit(db, "server.registered", actor=ctx.user.id, target_type="server", target_id=s.id, detail={"name": s.name}, ip=client_ip(request))
    db.commit()
    return {"server": server_out(db, s), "enrollment": enrollment_info(token)}


@router.get("/servers")
def list_servers(_: Ctx = Depends(read_ctx), db: Session = Depends(get_db)):
    now = agents.utcnow()
    return [server_out(db, s, now) for s in db.scalars(select(Server).order_by(Server.name))]


@router.get("/servers/{server_id}")
def server_detail(server_id: uuid.UUID, _: Ctx = Depends(read_ctx), db: Session = Depends(get_db)):
    return server_out(db, get_server_or_404(db, server_id))


@router.post("/servers/{server_id}/enrollment-token")
def new_enrollment_token(server_id: uuid.UUID, request: Request, ctx: Ctx = Depends(write_ctx), db: Session = Depends(get_db)):
    s = get_server_or_404(db, server_id)
    if s.status == "disabled":
        raise api_error(409, "server_disabled", "Enable the server before re-enrolling it.")
    _, token = agents.create_enrollment_token(db, s, ctx.user.id)
    audit(db, "server.enrollment_token_issued", actor=ctx.user.id, target_type="server", target_id=s.id, ip=client_ip(request))
    db.commit()
    return enrollment_info(token)


@router.post("/servers/{server_id}/disable")
def disable_server(server_id: uuid.UUID, request: Request, ctx: Ctx = Depends(manage_ctx), db: Session = Depends(get_db)):
    s = get_server_or_404(db, server_id)
    s.status = "disabled"
    n = agents.revoke_credentials(db, s.id)
    audit(db, "server.disabled", actor=ctx.user.id, target_type="server", target_id=s.id, detail={"credentials_revoked": n}, ip=client_ip(request))
    db.commit()
    return server_out(db, s)


@router.post("/servers/{server_id}/enable")
def enable_server(server_id: uuid.UUID, request: Request, ctx: Ctx = Depends(manage_ctx), db: Session = Depends(get_db)):
    s = get_server_or_404(db, server_id)
    if s.status == "disabled":
        s.status = "registered" if s.registered_at else "pending"
        audit(db, "server.enabled", actor=ctx.user.id, target_type="server", target_id=s.id, detail={"note": "credentials stay revoked; rotate to issue a new one"}, ip=client_ip(request))
        db.commit()
    return server_out(db, s)


# ---- credentials
@router.get("/servers/{server_id}/credentials")
def list_credentials(server_id: uuid.UUID, _: Ctx = Depends(read_ctx), db: Session = Depends(get_db)):
    get_server_or_404(db, server_id)
    rows = db.scalars(select(AgentCredential).where(AgentCredential.server_id == server_id).order_by(AgentCredential.created_at.desc()).limit(50)).all()
    return [
        {"id": str(c.id), "key_id": c.key_id, "status": c.status, "created_at": c.created_at, "last_used_at": c.last_used_at, "revoked_at": c.revoked_at}
        for c in rows
    ]


@router.post("/servers/{server_id}/credentials/rotate")
def rotate_credential(server_id: uuid.UUID, request: Request, ctx: Ctx = Depends(manage_ctx), db: Session = Depends(get_db)):
    """Revokes every active credential and issues a new one. The secret is returned exactly once."""
    s = get_server_or_404(db, server_id)
    if s.status == "disabled":
        raise api_error(409, "server_disabled", "Enable the server before issuing credentials.")
    revoked = agents.revoke_credentials(db, s.id)
    cred, secret = agents.issue_credential(db, s, ctx.user.id)
    if s.status == "pending":
        s.status, s.registered_at = "registered", agents.utcnow()
    audit(db, "agent.credential_rotated", actor=ctx.user.id, target_type="server", target_id=s.id, detail={"credential_id": str(cred.id), "revoked": revoked}, ip=client_ip(request))
    db.commit()
    return {"key_id": cred.key_id, "secret": secret, "shown_once": True}


@router.post("/servers/{server_id}/credentials/revoke")
def revoke_credential(server_id: uuid.UUID, request: Request, ctx: Ctx = Depends(manage_ctx), db: Session = Depends(get_db)):
    s = get_server_or_404(db, server_id)
    n = agents.revoke_credentials(db, s.id)
    audit(db, "agent.credential_revoked", actor=ctx.user.id, target_type="server", target_id=s.id, detail={"revoked": n}, ip=client_ip(request))
    db.commit()
    return {"revoked": n}


# ---- jobs
def job_out(j: AgentJob, full: bool = True) -> dict[str, Any]:
    d = {
        "id": str(j.id), "server_id": str(j.server_id), "operation": j.operation, "status": j.status, "attempts": j.attempts,
        "timeout_seconds": j.timeout_seconds, "created_at": j.created_at, "started_at": j.started_at, "finished_at": j.finished_at,
        "requested_by": str(j.requested_by) if j.requested_by else None,
    }
    if full:
        d.update(params=j.params, result=j.result, error=j.error, correlation_id=j.correlation_id)
    return d


@router.get("/jobs/operations")
def list_operations(_: Ctx = Depends(read_ctx)):
    return [
        {"name": n, "timeout_seconds": OP_META[n]["timeout"], "destructive": OP_META[n]["destructive"], "params_schema": OPERATIONS[n].model_json_schema()}
        for n in sorted(OPERATIONS)
    ]


@router.get("/jobs")
def list_jobs(
    _: Ctx = Depends(read_ctx),
    db: Session = Depends(get_db),
    server_id: uuid.UUID | None = None,
    status: str | None = Query(default=None, max_length=20),
    operation: str | None = Query(default=None, max_length=80),
    page: int = Query(default=1, ge=1, le=10000),
    page_size: int = Query(default=25, ge=1, le=100),
):
    if status is not None and status not in JOB_STATUSES:
        raise api_error(422, "invalid_status", f"status must be one of {JOB_STATUSES}.")
    q = select(AgentJob)
    if server_id:
        q = q.where(AgentJob.server_id == server_id)
    if status:
        q = q.where(AgentJob.status == status)
    if operation:
        q = q.where(AgentJob.operation == operation)
    total = db.scalar(select(func.count()).select_from(q.subquery())) or 0
    rows = db.scalars(q.order_by(AgentJob.created_at.desc()).offset((page - 1) * page_size).limit(page_size)).all()
    return {"items": [job_out(j, full=False) for j in rows], "total": total, "page": page, "page_size": page_size}


@router.get("/jobs/{job_id}")
def job_detail(job_id: uuid.UUID, _: Ctx = Depends(read_ctx), db: Session = Depends(get_db)):
    j = db.get(AgentJob, job_id)
    if j is None:
        raise api_error(404, "not_found", "Job not found.")
    return job_out(j)


@router.post("/jobs/{job_id}/cancel")
def cancel_job(job_id: uuid.UUID, ctx: Ctx = Depends(write_ctx), db: Session = Depends(get_db)):
    j = db.scalar(select(AgentJob).where(AgentJob.id == job_id).with_for_update())
    if j is None:
        raise api_error(404, "not_found", "Job not found.")
    agents.transition(db, j, "cancelled", actor=ctx.user.id)
    db.commit()
    return job_out(j)


class JobIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    operation: str = Field(min_length=1, max_length=80)
    params: dict[str, Any] | None = None


@router.post("/servers/{server_id}/jobs", status_code=201)
def queue_job(server_id: uuid.UUID, body: JobIn, ctx: Ctx = Depends(write_ctx), db: Session = Depends(get_db)):
    get_server_or_404(db, server_id)
    if body.operation in OP_META and OP_META[body.operation]["destructive"] and not ctx.can("servers:manage"):
        raise api_error(403, "forbidden", "Destructive operations require servers:manage.")
    job = enqueue_job(db, server_id, body.operation, body.params, requested_by=ctx.user.id)
    db.commit()
    return job_out(job)
