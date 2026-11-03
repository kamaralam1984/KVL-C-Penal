"""Customer database orchestration. Customer databases are physically separate from the control-plane database;
this module never holds admin credentials and never stores customer passwords (shown once, scrubbed from job params)."""
import hashlib
import hmac
import re
import secrets
import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.deps import api_error
from app.models.hosting import CustomerDatabase, DatabaseBackupRequest, DatabaseUser, ServerRuntime
from app.models.infra import AgentJob, Server
from app.services.jobs import default_server, enqueue_job
from app.services.redact import redact_text
from app.services.resources import job_ok, log_event, scrub_job_params

ENGINES = ("mariadb", "postgresql")
DEFAULT_PORTS = {"mariadb": 3306, "postgresql": 5432}
BACKUP_MAX_AGE = timedelta(days=7)
DESTROY_TOKEN_TTL = timedelta(minutes=10)
FINAL_REQUEST_STATES = {"succeeded", "failed", "rejected"}
REQUEST_TRANSITIONS = {
    "requested": {"approved", "rejected"},
    "approved": {"running", "failed"},
    "running": {"succeeded", "failed"},
    "succeeded": set(), "failed": set(), "rejected": set(),
}


def new_password() -> str:
    return secrets.token_urlsafe(24)  # 32 chars of [A-Za-z0-9_-]


def engines_available(db: Session, server_id: uuid.UUID | None) -> list[str]:
    if server_id is None:
        return []
    return list(db.scalars(select(ServerRuntime.runtime).where(ServerRuntime.server_id == server_id, ServerRuntime.runtime.in_(ENGINES), ServerRuntime.installed.is_(True))))


def ingest_engine_reports(db: Session, server_id: uuid.UUID) -> bool:
    job = db.scalars(select(AgentJob).where(AgentJob.server_id == server_id, AgentJob.operation == "db.detect_engines", AgentJob.status == "succeeded").order_by(AgentJob.finished_at.desc().nullslast())).first()
    if not job or not job.result or job.result.get("ok") is False:
        return False
    stamp = job.finished_at or job.created_at
    existing = {r.runtime: r for r in db.scalars(select(ServerRuntime).where(ServerRuntime.server_id == server_id, ServerRuntime.runtime.in_(ENGINES)))}
    if existing and max(r.reported_at for r in existing.values()) >= stamp:
        return False
    reported = job.result.get("engines") or {}
    for eng in ENGINES:
        ver = reported.get(eng)
        ok = isinstance(ver, str) and re.fullmatch(r"\d{1,2}(\.\d{1,2})?", ver) is not None
        row = existing.get(eng)
        if ok:
            if row:
                row.installed, row.version, row.reported_at = True, ver, stamp
            else:
                db.add(ServerRuntime(server_id=server_id, runtime=eng, version=ver, installed=True, reported_at=stamp))
        elif row:
            row.installed, row.reported_at = False, stamp
    return True


def assign_server(db: Session, d: CustomerDatabase) -> Server | None:
    if d.server_id:
        s = db.get(Server, d.server_id)
        if s and s.status != "disabled":
            return s
    s = default_server(db)
    if s:
        d.server_id = s.id
        d.port = DEFAULT_PORTS[d.engine]
    return s


def start_provision(db: Session, d: CustomerDatabase, user: DatabaseUser, actor: uuid.UUID | None) -> str | None:
    """Enqueue provisioning; returns the one-time password, or None when blocked by a missing server."""
    server = assign_server(db, d)
    if server is None:
        d.status, d.message = "pending_server", "No server is registered yet. Provisioning will run when you retry after a server exists."
        log_event(db, d.tenant_id, "database", d.id, actor, "database.provision_blocked", "Provisioning blocked: no server registered")
        return None
    avail = engines_available(db, server.id)
    if avail and d.engine not in avail:
        raise api_error(422, "engine_unavailable", f"{d.engine} is not available on this server. Available: {', '.join(avail)}.")
    pw = new_password()
    job = enqueue_job(db, server.id, "db.provision", {"engine": d.engine, "db_name": d.db_name, "username": user.username, "privilege": user.privilege, "password": pw}, tenant_id=d.tenant_id, requested_by=actor)
    d.provision_job_id, d.status, d.message = job.id, "provisioning", "Creating database and user."
    user.status, user.last_job_id = "pending", job.id
    log_event(db, d.tenant_id, "database", d.id, actor, "database.provision", f"Provisioning {d.engine} database requested")
    return pw


def start_cleanup(db: Session, d: CustomerDatabase) -> None:
    if not d.server_id:
        return
    users = [u.username for u in db.scalars(select(DatabaseUser).where(DatabaseUser.database_id == d.id))]
    job = enqueue_job(db, d.server_id, "db.drop", {"engine": d.engine, "db_name": d.db_name, "confirm_name": d.db_name, "users": users}, tenant_id=d.tenant_id)
    d.cleanup_job_id = job.id
    log_event(db, d.tenant_id, "database", d.id, None, "database.cleanup", "Cleanup of partially created database requested")


def latest_verified_backup(db: Session, d: CustomerDatabase) -> DatabaseBackupRequest | None:
    cutoff = datetime.now(UTC) - BACKUP_MAX_AGE
    return db.scalars(select(DatabaseBackupRequest).where(
        DatabaseBackupRequest.database_id == d.id, DatabaseBackupRequest.kind == "backup", DatabaseBackupRequest.state == "succeeded",
        DatabaseBackupRequest.verified.is_(True), DatabaseBackupRequest.finished_at >= cutoff).order_by(DatabaseBackupRequest.finished_at.desc())).first()


def issue_destroy_token(d: CustomerDatabase) -> str:
    tok = secrets.token_urlsafe(24)
    d.destroy_token_hash = hashlib.sha256(tok.encode()).hexdigest()
    d.destroy_token_expires = datetime.now(UTC) + DESTROY_TOKEN_TTL
    return tok


def check_destroy_token(d: CustomerDatabase, tok: str) -> None:
    if not d.destroy_token_hash or not d.destroy_token_expires or d.destroy_token_expires < datetime.now(UTC):
        raise api_error(409, "token_expired", "Confirmation token missing or expired. Request a new one.")
    if not hmac.compare_digest(d.destroy_token_hash, hashlib.sha256(tok.encode()).hexdigest()):
        raise api_error(409, "token_invalid", "Confirmation token is not valid.")


def move(req: DatabaseBackupRequest, new_state: str) -> None:
    if new_state not in REQUEST_TRANSITIONS[req.state]:
        raise api_error(409, "invalid_transition", f"Cannot move request from {req.state} to {new_state}.")
    req.state = new_state


def start_backup_job(db: Session, d: CustomerDatabase, req: DatabaseBackupRequest, actor: uuid.UUID | None) -> None:
    server = assign_server(db, d)
    if server is None:
        raise api_error(409, "no_server", "No server is registered yet.")
    if req.kind == "backup":
        job = enqueue_job(db, server.id, "db.dump", {"engine": d.engine, "db_name": d.db_name, "artifact": req.artifact_ref}, tenant_id=d.tenant_id, requested_by=actor)
    else:
        job = enqueue_job(db, server.id, "db.restore", {"engine": d.engine, "db_name": d.db_name, "artifact": req.artifact_ref, "checksum": req.checksum}, tenant_id=d.tenant_id, requested_by=actor)
    req.job_id = job.id
    move(req, "running")


def artifact_name(d: CustomerDatabase) -> str:
    return f"{d.db_name}-{datetime.now(UTC):%Y%m%dT%H%M%SZ}.sql"


def _msg(job: AgentJob | None, res: dict) -> str:
    return redact_text(str(res.get("message") or (job.error if job else None) or "Operation failed."))[:500]


def sync_database(db: Session, d: CustomerDatabase) -> CustomerDatabase:
    # provisioning
    if d.status == "provisioning":
        job = db.get(AgentJob, d.provision_job_id) if d.provision_job_id else None
        ok = job_ok(job)
        if ok is not None:
            res = job.result or {}
            users = list(db.scalars(select(DatabaseUser).where(DatabaseUser.database_id == d.id)))
            if ok:
                d.status, d.message = "ready", None
                for u in users:
                    if u.status == "pending":
                        u.status, u.password_rotated_at = "active", datetime.now(UTC)
            else:
                st = res.get("status")
                d.status = "failed"
                d.message = _msg(job, res) if st != "not_configured" else (res.get("message") or "Database server software is not configured.")[:500]
                for u in users:
                    u.status = "failed"
                if st != "not_configured":
                    start_cleanup(db, d)  # idempotent drop of anything half-created
            log_event(db, d.tenant_id, "database", d.id, None, f"database.{d.status}", (d.message or "Provisioned")[:300])
            scrub_job_params(db, job)
    # cleanup result
    if d.cleanup_job_id:
        job = db.get(AgentJob, d.cleanup_job_id)
        if job_ok(job) is not None:
            if job_ok(job):
                d.message = ((d.message or "") + " Partial resources were cleaned up.").strip()[:500]
            else:
                d.message = ((d.message or "") + " Cleanup could not be confirmed; an administrator should check the server.").strip()[:500]
            d.cleanup_job_id = None
    # destroy
    if d.status == "deleting":
        job = db.get(AgentJob, d.destroy_job_id) if d.destroy_job_id else None
        ok = job_ok(job)
        if ok is not None:
            if ok:
                d.status, d.message = "deleted", "Database deleted."
                from app.services.tenants import consume_quota

                consume_quota(db, d.tenant_id, "databases", -1)
            else:
                d.status, d.message = "ready", _msg(job, job.result or {})
            log_event(db, d.tenant_id, "database", d.id, None, f"database.destroy_{'ok' if ok else 'failed'}", (d.message or "")[:300])
    # size
    if d.size_job_id:
        job = db.get(AgentJob, d.size_job_id)
        if job_ok(job) is not None:
            if job_ok(job) and isinstance((job.result or {}).get("size_bytes"), int):
                d.size_bytes, d.size_checked_at = job.result["size_bytes"], datetime.now(UTC)
            d.size_job_id = None
    # users (create / rotate / drop)
    for u in db.scalars(select(DatabaseUser).where(DatabaseUser.database_id == d.id, DatabaseUser.last_job_id.is_not(None))):
        if u.status in {"pending", "rotating", "dropping"}:
            job = db.get(AgentJob, u.last_job_id)
            ok = job_ok(job)
            if ok is None:
                continue
            if u.status == "pending":
                u.status = "active" if ok else "failed"
                if ok:
                    u.password_rotated_at = datetime.now(UTC)
            elif u.status == "rotating":
                u.status = "active"
                if ok:
                    u.password_rotated_at = datetime.now(UTC)
                log_event(db, d.tenant_id, "database", d.id, None, "database.rotate_ok" if ok else "database.rotate_failed", f"Credential rotation for {u.username} {'completed' if ok else 'failed'}")
            else:
                u.status = "dropped" if ok else "active"
            scrub_job_params(db, job)
    # backup/restore requests
    for r in db.scalars(select(DatabaseBackupRequest).where(DatabaseBackupRequest.database_id == d.id, DatabaseBackupRequest.state == "running")):
        job = db.get(AgentJob, r.job_id) if r.job_id else None
        ok = job_ok(job)
        if ok is None:
            continue
        res = job.result or {}
        r.finished_at = datetime.now(UTC)
        if ok:
            move(r, "succeeded")
            if r.kind == "backup":
                cs = res.get("checksum")
                r.checksum = cs if isinstance(cs, str) and re.fullmatch(r"[a-f0-9]{64}", cs) else None
                r.size_bytes = res.get("size_bytes") if isinstance(res.get("size_bytes"), int) else None
                r.verified = bool(r.checksum) and res.get("verified") is True
        else:
            move(r, "failed")
            r.error = _msg(job, res)
        log_event(db, d.tenant_id, "database", d.id, None, f"database.{r.kind}_{r.state}", f"{r.kind} request {r.state}")
    return d
