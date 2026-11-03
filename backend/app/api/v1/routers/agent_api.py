"""Endpoints called by the server agent (not by browsers). Every call except /enroll is HMAC-signed."""
import json
import uuid
from datetime import UTC, datetime
from typing import Any, Literal

from fastapi import APIRouter, Depends, Request
from fastapi.responses import Response
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core import ratelimit
from app.core.db import get_db
from app.core.deps import api_error, client_ip
from app.models.infra import AgentJob
from app.services import agents, monitoring
from app.services.audit import audit

router = APIRouter(prefix="/agent", tags=["agent"])

Pct = Field(default=None, ge=0, le=100)
Count = Field(default=None, ge=0, le=2**53)


async def agent_auth(request: Request, db: Session = Depends(get_db)) -> agents.AgentIdentity:
    body = await request.body()
    return agents.authenticate_agent(db, method=request.method, path=request.url.path, headers=request.headers, body=body)


def signed(identity: agents.AgentIdentity, payload: Any, status: int = 200) -> Response:
    body = json.dumps(payload, default=str).encode()
    return Response(content=body, status_code=status, media_type="application/json", headers={"X-KVL-Response-Signature": agents.sign_response(identity.secret, identity.nonce, body)})


class EnrollIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    enrollment_token: str = Field(min_length=20, max_length=120)
    hostname: str | None = Field(default=None, max_length=255)
    agent_version: str | None = Field(default=None, max_length=40)
    os_info: dict[str, Any] | None = None


@router.post("/enroll")
def enroll(body: EnrollIn, request: Request, db: Session = Depends(get_db)):
    ratelimit.enforce(f"agent-enroll:{client_ip(request)}", 10, 600)
    try:
        server = agents.redeem_enrollment_token(db, body.enrollment_token)
    except Exception:
        db.rollback()
        raise
    cred, secret = agents.issue_credential(db, server, None)
    server.status = "registered"
    server.registered_at = server.registered_at or agents.utcnow()
    server.agent_version = body.agent_version or server.agent_version
    if body.hostname and not server.hostname:
        server.hostname = body.hostname[:255]
    if body.os_info:
        server.os_info = {k: str(v)[:100] for k, v in list(body.os_info.items())[:20]}
    audit(db, "agent.enrolled", target_type="server", target_id=server.id, detail={"credential_id": str(cred.id)}, ip=client_ip(request))
    db.commit()
    return {"server_id": str(server.id), "key_id": cred.key_id, "secret": secret, "heartbeat_interval": monitoring.HEARTBEAT_INTERVAL, "shown_once": True}


class Memory(BaseModel):
    model_config = ConfigDict(extra="forbid")
    total: int | None = Count
    used: int | None = Count
    available: int | None = Count


class Load(BaseModel):
    model_config = ConfigDict(extra="forbid")
    load1: float | None = Field(default=None, ge=0, le=100000)
    load5: float | None = Field(default=None, ge=0, le=100000)
    load15: float | None = Field(default=None, ge=0, le=100000)


class Disk(BaseModel):
    model_config = ConfigDict(extra="forbid")
    mount: str = Field(max_length=200)
    fstype: str | None = Field(default=None, max_length=40)
    total: int | None = Count
    used: int | None = Count
    free: int | None = Count
    percent: float | None = Pct


class Iface(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(max_length=40)
    is_up: bool | None = None
    speed_mbps: int | None = Field(default=None, ge=0, le=10_000_000)
    rx_bytes_per_s: float | None = Field(default=None, ge=0)
    tx_bytes_per_s: float | None = Field(default=None, ge=0)


class Network(BaseModel):
    model_config = ConfigDict(extra="forbid")
    rx_bytes_per_s: float | None = Field(default=None, ge=0)
    tx_bytes_per_s: float | None = Field(default=None, ge=0)
    interfaces: list[Iface] | None = Field(default=None, max_length=32)


class ProcIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    pid: int = Field(ge=0, le=2**31)
    name: str = Field(max_length=60)
    rss_bytes: int | None = Count
    cpu_percent: float | None = Field(default=None, ge=0, le=100000)


class SiteIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,79}$")
    kind: Literal["site", "app"] = "site"
    disk_bytes: int | None = Count
    rss_bytes: int | None = Count
    cpu_percent: float | None = Field(default=None, ge=0, le=100000)
    procs: int | None = Field(default=None, ge=0, le=100000)
    bytes_out_total: int | None = Count
    requests_total: int | None = Count
    errors_4xx_total: int | None = Count
    errors_5xx_total: int | None = Count


class Metrics(BaseModel):
    model_config = ConfigDict(extra="forbid")
    cpu_percent: float | None = Pct
    cpu_count: int | None = Field(default=None, ge=1, le=4096)
    load: Load | None = None
    memory: Memory | None = None
    swap: Memory | None = None
    cpu_cores: list[float] | None = Field(default=None, max_length=256)
    processes: list["ProcIn"] | None = Field(default=None, max_length=20)
    disks: list[Disk] | None = Field(default=None, max_length=40)
    network: Network | None = None
    uptime_seconds: int | None = Field(default=None, ge=0)


class ServiceIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(pattern=r"^[a-z0-9][a-z0-9_.-]{0,59}$")
    state: str = Field(max_length=20)
    detail: str | None = Field(default=None, max_length=300)


class HeartbeatIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    agent_version: str | None = Field(default=None, max_length=40)
    hostname: str | None = Field(default=None, max_length=255)
    os_info: dict[str, str] | None = None
    update_status: dict[str, Any] | None = None
    metrics: Metrics | None = None
    services: list[ServiceIn] | None = Field(default=None, max_length=30)
    sites: list[SiteIn] | None = Field(default=None, max_length=200)


@router.post("/heartbeat")
def heartbeat(body: HeartbeatIn, ident: agents.AgentIdentity = Depends(agent_auth), db: Session = Depends(get_db)):
    payload = body.model_dump(exclude_none=False)
    if payload.get("os_info"):
        payload["os_info"] = {k[:40]: v[:100] for k, v in list(payload["os_info"].items())[:20]}
    if payload.get("update_status"):
        payload["update_status"] = {k[:40]: str(v)[:100] for k, v in list(payload["update_status"].items())[:10]}
    server = ident.server
    monitoring.ingest_heartbeat(db, server, payload)
    pending = len(db.scalars(select(AgentJob.id).where(AgentJob.server_id == server.id, AgentJob.status == "queued").limit(50)).all())
    db.commit()
    return signed(ident, {"ok": True, "server_time": agents.utcnow().isoformat(), "heartbeat_interval": monitoring.HEARTBEAT_INTERVAL, "pending_jobs": pending})


class PollIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    max: int = Field(default=1, ge=1, le=agents.MAX_CONCURRENT_CLAIM)


@router.post("/jobs/poll")
def poll(body: PollIn, ident: agents.AgentIdentity = Depends(agent_auth), db: Session = Depends(get_db)):
    jobs = agents.claim_jobs(db, ident.server.id, body.max)
    out = [{"id": str(j.id), "operation": j.operation, "params": j.params or {}, "timeout_seconds": j.timeout_seconds} for j in jobs]
    db.commit()
    return signed(ident, {"jobs": out})


class ResultIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    status: Literal["succeeded", "failed", "timed_out"]
    summary: dict[str, Any] | None = None
    error: str | None = Field(default=None, max_length=20000)
    duration_ms: int | None = Field(default=None, ge=0)


@router.post("/jobs/{job_id}/result")
def report_result(job_id: uuid.UUID, body: ResultIn, ident: agents.AgentIdentity = Depends(agent_auth), db: Session = Depends(get_db)):
    job = db.scalar(select(AgentJob).where(AgentJob.id == job_id).with_for_update())
    if job is None or job.server_id != ident.server.id:
        raise api_error(404, "not_found", "Job not found.")  # another server's job looks nonexistent
    result = agents.sanitize_result(body.summary or {})
    if body.duration_ms is not None:
        result["duration_ms"] = body.duration_ms
    agents.transition(db, job, body.status, result=result, error=agents.sanitize_error(body.error))
    db.commit()
    return signed(ident, {"ok": True})
