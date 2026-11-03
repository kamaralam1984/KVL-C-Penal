"""Queue allow-listed operations for a server agent. Every infrastructure change goes through here."""
import uuid

from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.deps import api_error
from app.models.infra import AgentJob, Server
from app.ops import OP_META, OPERATIONS
from app.services.audit import audit


def enqueue_job(
    db: Session,
    server_id: uuid.UUID,
    operation: str,
    params: dict | None = None,
    *,
    tenant_id: uuid.UUID | None = None,
    requested_by: uuid.UUID | None = None,
    correlation_id: str | None = None,
) -> AgentJob:
    """Validate the operation against the allow-list and its schema, then queue it. Never runs anything itself."""
    schema = OPERATIONS.get(operation)
    if schema is None:
        raise api_error(422, "unknown_operation", f"Operation '{operation}' is not allow-listed.")
    try:
        clean = schema.model_validate(params or {}).model_dump(mode="json")
    except ValidationError as e:
        raise api_error(422, "invalid_operation_params", "; ".join(f"{'.'.join(map(str, x['loc']))}: {x['msg']}" for x in e.errors())) from None
    server = db.get(Server, server_id)
    if server is None or server.status == "disabled":
        raise api_error(409, "server_unavailable", "Server is not available for operations.")
    job = AgentJob(
        server_id=server_id,
        tenant_id=tenant_id,
        operation=operation,
        params=clean,
        timeout_seconds=OP_META[operation]["timeout"],
        requested_by=requested_by,
        correlation_id=correlation_id,
    )
    db.add(job)
    db.flush()
    audit(db, "job.queued", actor=requested_by, tenant_id=tenant_id, target_type="job", target_id=job.id, detail={"operation": operation, "server_id": str(server_id)})
    return job


def default_server(db: Session) -> Server | None:
    """The first registered server; used by single-server installs (KVL's private server)."""
    return db.scalar(select(Server).where(Server.status == "registered").order_by(Server.created_at))
