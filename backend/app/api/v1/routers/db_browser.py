"""Read-only database browser (tables, structure, paginated rows) for customer databases. Job based via the agent."""
import uuid
from typing import Any, Literal

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session

from app.core import ratelimit
from app.core.db import get_db
from app.core.deps import Ctx, api_error, client_ip, require
from app.models.hosting import CustomerDatabase
from app.models.infra import AgentJob
from app.services.access import get_visible
from app.services.audit import audit
from app.services.jobs import enqueue_job
from app.services.redact import redact_text, redact_value
from app.services.resources import job_ok

router = APIRouter(prefix="/databases/{database_id}/browse", tags=["databases"])
OPS = {"tables": "db.browse_tables", "describe": "db.browse_describe", "rows": "db.browse_rows"}
NAME = r"^[^\x00-\x1f`\"\\]{1,64}$"


class DescribeIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    table: str = Field(pattern=NAME)


class RowsIn(DescribeIn):
    limit: int = Field(50, ge=1, le=100)
    offset: int = Field(0, ge=0, le=10_000_000)
    order_by: str | None = Field(None, pattern=NAME)
    order_dir: Literal["asc", "desc"] = "asc"
    filter_column: str | None = Field(None, pattern=NAME)
    filter_op: Literal["eq", "contains"] = "eq"
    filter_value: str | None = Field(None, max_length=200)


def _ready(db: Session, ctx: Ctx, did: uuid.UUID) -> CustomerDatabase:
    d = get_visible(db, ctx, CustomerDatabase, did, "Database")
    if d.status != "ready" or not d.server_id:
        raise api_error(409, "not_ready", "The database is not ready yet.")
    return d


def _start(db: Session, ctx: Ctx, request: Request, did: uuid.UUID, kind: str, params: dict[str, Any]) -> dict[str, Any]:
    d = _ready(db, ctx, did)
    ratelimit.enforce(f"db-browse:{ctx.user.id}", limit=60, window=60)
    job = enqueue_job(db, d.server_id, OPS[kind], {"engine": d.engine, "db_name": d.db_name, **params}, tenant_id=d.tenant_id, requested_by=ctx.user.id)
    if kind == "rows":
        audit(db, "db.browse", actor=ctx.user.id, tenant_id=d.tenant_id, target_type="database", target_id=d.id, detail={"table": params.get("table")}, ip=client_ip(request))
    db.commit()
    return {"job_id": str(job.id), "status": job.status}


@router.post("/tables", status_code=202)
def tables(database_id: uuid.UUID, request: Request, ctx: Ctx = Depends(require("databases:read")), db: Session = Depends(get_db)):
    return _start(db, ctx, request, database_id, "tables", {})


@router.post("/describe", status_code=202)
def describe(database_id: uuid.UUID, body: DescribeIn, request: Request, ctx: Ctx = Depends(require("databases:read")), db: Session = Depends(get_db)):
    return _start(db, ctx, request, database_id, "describe", body.model_dump())


@router.post("/rows", status_code=202)
def rows(database_id: uuid.UUID, body: RowsIn, request: Request, ctx: Ctx = Depends(require("databases:read")), db: Session = Depends(get_db)):
    if (body.filter_column is None) != (body.filter_value is None):
        raise api_error(422, "validation_error", "filter_column and filter_value go together.")
    return _start(db, ctx, request, database_id, "rows", body.model_dump(exclude_none=True))


@router.get("/jobs/{job_id}")
def result(database_id: uuid.UUID, job_id: uuid.UUID, ctx: Ctx = Depends(require("databases:read")), db: Session = Depends(get_db)):
    d = get_visible(db, ctx, CustomerDatabase, database_id, "Database")
    job = db.get(AgentJob, job_id)
    p = (job.params or {}) if job else {}
    if job is None or job.operation not in OPS.values() or job.tenant_id != d.tenant_id or p.get("db_name") != d.db_name or p.get("engine") != d.engine:
        raise api_error(404, "not_found", "Request not found.")
    ok = job_ok(job)
    if ok is None:
        return {"status": job.status}
    res = job.result or {}
    if not ok:
        return {"status": "failed", "code": res.get("status"), "message": redact_text(str(res.get("message") or job.error or "The query failed."))[:300]}
    keep = ("tables", "table", "columns", "indexes", "rows", "has_more", "offset", "limit")
    return {"status": "succeeded", **{k: redact_value(res[k]) if k == "rows" else res[k] for k in keep if k in res}}
