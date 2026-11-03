"""File manager control plane: ownership, path pre-validation, job queueing and disk_mb quota accounting.

The API never touches the filesystem. Each action becomes an allow-listed agent operation scoped to the site root that
the control plane looked up itself (a client cannot supply a root). The agent re-validates everything (defense in depth).
"""
import math
import re
import uuid
from typing import Any
from urllib.parse import unquote

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.deps import api_error
from app.models.files import FileOperation
from app.models.infra import AgentJob
from app.models.ownership import ResourceRef
from app.models.tenant import TenantQuota
from app.services.audit import audit
from app.services.jobs import default_server, enqueue_job
from app.services.ownership import require_owned, get_ref
from app.services.tenants import consume_quota

MB = 1024 * 1024
MAX_UPLOAD_BYTES = 10 * MB
BLOCKED_EXT = {".exe", ".dll", ".scr", ".msi", ".com", ".bat", ".cmd", ".so", ".dylib", ".jar", ".apk"}
_CTRL = re.compile(r"[\x00-\x1f\x7f]")
RESERVED = ".kvl-trash"


def safe_rel(p: str, *, field: str = "path") -> str:
    """Reject traversal (raw and repeatedly URL-decoded), NUL, backslash, control chars and absolute paths before queueing."""
    if not isinstance(p, str) or len(p) > 4096:
        raise api_error(422, "invalid_path", "Path is invalid.")
    forms, cur = [p], p
    for _ in range(4):
        nxt = unquote(cur)
        if nxt == cur:
            break
        forms.append(nxt)
        cur = nxt
    else:
        raise api_error(422, "invalid_path", "Path is invalid.")
    for f in forms:
        if "\x00" in f or "\\" in f or _CTRL.search(f) or f.startswith("/") or re.match(r"^[A-Za-z]:", f) or any(s == ".." for s in f.split("/")):
            raise api_error(422, "invalid_path", "Path is invalid or not allowed.")
    parts = [s for s in p.split("/") if s not in ("", ".")]
    if parts and parts[0] == RESERVED:
        raise api_error(422, "invalid_path", "That location is reserved.")
    return "/".join(parts)


def mb(n: int) -> int:
    return max(1, math.ceil(n / MB)) if n > 0 else 0


def site_ref(db: Session, tenant_id: uuid.UUID, site: str) -> ResourceRef:
    require_owned(db, tenant_id, "website", site)  # 404 for anything not owned (cross-tenant included)
    ref = get_ref(db, tenant_id, "website", site)
    if ref is None or not ref.root_rel:
        raise api_error(409, "not_configured", "This website has no file location registered on a server yet.")
    return ref


def queue(db: Session, *, tenant_id: uuid.UUID, actor: uuid.UUID | None, site: str, op: str, agent_op: str, params: dict[str, Any], path: str | None = None, detail: dict | None = None, reserved: int = 0, audit_action: str | None = None, ip: str | None = None) -> tuple[FileOperation, AgentJob]:
    ref = site_ref(db, tenant_id, site)
    server_id = ref.server_id or (default_server(db).id if default_server(db) else None)
    if server_id is None:
        raise api_error(409, "not_configured", "No registered server is available to run file operations.")
    job = enqueue_job(db, server_id, agent_op, {"root": ref.root_rel, **params}, tenant_id=tenant_id, requested_by=actor, correlation_id=f"files:{site}")
    fop = FileOperation(tenant_id=tenant_id, site_ref=ref.ref, op=op, path=path, job_id=job.id, actor_user_id=actor, reserved_bytes=reserved, detail=detail)
    db.add(fop)
    db.flush()
    if audit_action:
        audit(db, audit_action, actor=actor, tenant_id=tenant_id, target_type="website", target_id=ref.ref, detail={"op": op, "path": path, **(detail or {})}, ip=ip)
    return fop, job


def quota_view(db: Session, tenant_id: uuid.UUID | None) -> dict[str, Any]:
    q = db.scalar(select(TenantQuota).where(TenantQuota.tenant_id == tenant_id, TenantQuota.resource == "disk_mb")) if tenant_id else None
    if q is None:
        return {"configured": False, "limit_mb": None, "used_mb": None, "percent": None}
    return {"configured": True, "limit_mb": q.limit_value, "used_mb": q.used_value, "percent": round(100 * q.used_value / q.limit_value, 1) if q.limit_value else None}


def _adjust(db: Session, tenant_id: uuid.UUID, delta_mb: int) -> None:
    q = db.scalar(select(TenantQuota).where(TenantQuota.tenant_id == tenant_id, TenantQuota.resource == "disk_mb").with_for_update())
    if q is not None and delta_mb:
        q.used_value = max(0, q.used_value + delta_mb)


def reserve(db: Session, tenant_id: uuid.UUID, nbytes: int) -> None:
    consume_quota(db, tenant_id, "disk_mb", mb(nbytes))  # 409 quota_exceeded when over the limit


def settle(db: Session, fop: FileOperation) -> AgentJob | None:
    """Fold a finished job into quota accounting exactly once."""
    job = db.get(AgentJob, fop.job_id) if fop.job_id else None
    if job is None or job.status in ("queued", "running"):
        return job
    ok = job.status == "succeeded" and (job.result or {}).get("ok", True) is not False
    if fop.settled:
        return job
    fop.settled = True
    res = job.result or {}
    if fop.op == "write":
        if not ok:
            uid = (fop.detail or {}).get("upload_id")
            first = fop if fop.reserved_bytes else (db.scalar(select(FileOperation).where(FileOperation.tenant_id == fop.tenant_id, FileOperation.op == "write", FileOperation.detail["upload_id"].astext == uid, FileOperation.reserved_bytes > 0)) if uid else None)
            if first is not None and not (first.detail or {}).get("released"):
                _adjust(db, fop.tenant_id, -mb(first.reserved_bytes))
                first.detail = {**(first.detail or {}), "released": True}
    elif ok and fop.op in ("archive", "extract"):
        _adjust(db, fop.tenant_id, mb(int(res.get("bytes_added") or 0)))
    elif ok and fop.op == "purge":
        _adjust(db, fop.tenant_id, -(int(res.get("freed_bytes") or 0) // MB))
    return job


def job_view(fop: FileOperation, job: AgentJob | None) -> dict[str, Any]:
    result = None
    if job and job.result:
        result = {k: v for k, v in job.result.items() if k != "content_b64"}
        if "content_b64" in job.result:
            result["downloadable"] = job.result["content_b64"] not in (None, "[delivered]")
    return {"id": str(fop.id), "operation": fop.op, "site": fop.site_ref, "path": fop.path, "status": job.status if job else "unknown", "result": result,
            "error": job.error if job and job.status in ("failed", "timed_out") else (job.result or {}).get("message") if job and (job.result or {}).get("ok") is False else None,
            "created_at": fop.created_at}
