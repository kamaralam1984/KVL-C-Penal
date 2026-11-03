"""Shared helpers for tenant-owned infrastructure resources: events, job state lookup, secret scrubbing of job params."""
import uuid

from sqlalchemy.orm import Session

from app.models.hosting import ResourceEvent
from app.models.infra import AgentJob
from app.services.redact import REDACTED, redact_value

SECRET_PARAM_KEYS = {"password", "new_password", "env", "admin_password"}


def log_event(db: Session, tenant_id: uuid.UUID, rtype: str, rid: uuid.UUID, actor: uuid.UUID | None, action: str, summary: str) -> None:
    db.add(ResourceEvent(tenant_id=tenant_id, resource_type=rtype, resource_id=rid, actor_user_id=actor, action=action, summary=summary[:300]))


def job_view(db: Session, job_id: uuid.UUID | None) -> dict | None:
    """Job summary safe to return to customers: redacted result and error, no params."""
    if not job_id:
        return None
    j = db.get(AgentJob, job_id)
    if j is None:
        return None
    return {
        "id": str(j.id), "operation": j.operation, "status": j.status,
        "result": redact_value(j.result) if j.result else None,
        "error": redact_value(j.error) if j.error else None,
        "created_at": j.created_at, "finished_at": j.finished_at,
    }


def scrub_job_params(db: Session, job: AgentJob | None) -> None:
    """Once a job is finished, replace secret parameters (passwords, env values) so they do not stay in the queue table."""
    if job is None or job.status in {"queued", "running"} or not job.params:
        return
    if any(k in job.params for k in SECRET_PARAM_KEYS):
        job.params = {k: (REDACTED if k in SECRET_PARAM_KEYS else v) for k, v in job.params.items()}


def job_ok(job: AgentJob | None) -> bool | None:
    """True/False once finished; None while pending. A job 'succeeded' at transport level can still report ok=False."""
    if job is None:
        return None
    if job.status in {"queued", "running"}:
        return None
    if job.status != "succeeded":
        return False
    res = job.result or {}
    return res.get("ok", True) is not False
