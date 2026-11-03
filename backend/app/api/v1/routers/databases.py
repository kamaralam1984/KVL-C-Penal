import re
import secrets
import uuid
from typing import Any

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.db import get_db
from app.core.deps import Ctx, api_error, client_ip, require
from app.models.hosting import CustomerDatabase, DatabaseBackupRequest, DatabaseUser, ResourceEvent
from app.models.tenant import Tenant, TenantQuota
from app.services import databases as svc
from app.services.access import get_visible, scope_stmt, tenant_for_create
from app.services.audit import audit
from app.services.jobs import default_server, enqueue_job
from app.services.resources import log_event
from app.services.tenants import consume_quota

router = APIRouter(prefix="/databases", tags=["databases"])


def dto(db: Session, d: CustomerDatabase, detail: bool = False) -> dict[str, Any]:
    out = {
        "id": str(d.id), "tenant_id": str(d.tenant_id), "engine": d.engine, "name": d.db_name, "label": d.label, "status": d.status, "message": d.message,
        "host": d.host, "port": d.port, "size_bytes": d.size_bytes, "size_checked_at": d.size_checked_at, "created_at": d.created_at,
    }
    if detail:
        out["users"] = [user_dto(u) for u in db.scalars(select(DatabaseUser).where(DatabaseUser.database_id == d.id).order_by(DatabaseUser.created_at))]
        lb = svc.latest_verified_backup(db, d)
        out["verified_backup_at"] = lb.finished_at if lb else None
    return out


def user_dto(u: DatabaseUser) -> dict:
    return {"id": str(u.id), "username": u.username, "privilege": u.privilege, "status": u.status, "password_rotated_at": u.password_rotated_at,
            "grants": "Read and write (SELECT, INSERT, UPDATE, DELETE, DDL) on this database only" if u.privilege == "readwrite" else "Read-only (SELECT) on this database only"}


def req_dto(r: DatabaseBackupRequest) -> dict:
    return {"id": str(r.id), "kind": r.kind, "state": r.state, "artifact": r.artifact_ref, "source_request_id": str(r.source_request_id) if r.source_request_id else None,
            "checksum": r.checksum, "size_bytes": r.size_bytes, "verified": r.verified, "error": r.error, "created_at": r.created_at, "finished_at": r.finished_at}


class DatabaseIn(BaseModel):
    engine: str = Field(pattern="^(mariadb|postgresql)$")
    name: str = Field(pattern=r"^[a-z][a-z0-9]{1,13}$")
    tenant_id: uuid.UUID | None = None


class UserIn(BaseModel):
    suffix: str = Field(pattern=r"^[a-z][a-z0-9]{0,5}$")
    privilege: str = Field(default="readonly", pattern="^(readwrite|readonly)$")


class DestroyIn(BaseModel):
    confirm_name: str = Field(max_length=40)
    token: str = Field(min_length=10, max_length=100)


class RestoreIn(BaseModel):
    source_backup_id: uuid.UUID


class ApproveIn(BaseModel):
    confirm_name: str | None = Field(default=None, max_length=40)


def _db(db: Session, ctx: Ctx, did: uuid.UUID, sync: bool = True) -> CustomerDatabase:
    d = get_visible(db, ctx, CustomerDatabase, did, "Database")
    if d.status == "deleted":
        raise api_error(404, "not_found", "Database not found.")
    if sync:
        svc.sync_database(db, d)
        db.commit()
        if d.status == "deleted":
            raise api_error(404, "not_found", "Database not found.")
    return d


def _creds(d: CustomerDatabase, username: str, password: str | None) -> dict | None:
    if password is None:
        return None
    return {"username": username, "password": password, "host": d.host, "port": d.port or svc.DEFAULT_PORTS[d.engine], "database": d.db_name, "shown_once": True}


@router.get("/engines")
def engines(ctx: Ctx = Depends(require("databases:read")), db: Session = Depends(get_db)):
    srv = default_server(db)
    if srv is None:
        return {"state": "not_configured", "message": "No server is registered yet.", "available": [], "supported": list(svc.ENGINES)}
    if svc.ingest_engine_reports(db, srv.id):
        db.commit()
    av = svc.engines_available(db, srv.id)
    return {"state": "ok" if av else "not_reported", "message": None if av else "The server has not reported database engines yet.", "available": av, "supported": list(svc.ENGINES)}


@router.post("/engines/refresh")
def refresh_engines(request: Request, ctx: Ctx = Depends(require("databases:manage")), db: Session = Depends(get_db)):
    if not ctx.is_platform:
        raise api_error(403, "forbidden", "Platform staff only.")
    srv = default_server(db)
    if srv is None:
        raise api_error(409, "no_server", "No server is registered yet.")
    job = enqueue_job(db, srv.id, "db.detect_engines", {}, requested_by=ctx.user.id)
    audit(db, "databases.engines_refresh", actor=ctx.user.id, target_type="server", target_id=srv.id, ip=client_ip(request))
    db.commit()
    return {"job_id": str(job.id)}


@router.get("")
def list_databases(engine: str | None = None, tenant_id: uuid.UUID | None = None, limit: int = 25, offset: int = 0, ctx: Ctx = Depends(require("databases:read")), db: Session = Depends(get_db)):
    stmt = scope_stmt(select(CustomerDatabase), CustomerDatabase, ctx, tenant_id).where(CustomerDatabase.status != "deleted")
    if engine:
        stmt = stmt.where(CustomerDatabase.engine == engine)
    total = db.scalar(select(func.count()).select_from(stmt.subquery()))
    rows = list(db.scalars(stmt.order_by(CustomerDatabase.created_at.desc()).limit(min(limit, 100)).offset(offset)))
    for d in rows:
        svc.sync_database(db, d)
    db.commit()
    return {"total": total, "items": [dto(db, d) for d in rows]}


@router.post("", status_code=201)
def create_database(body: DatabaseIn, request: Request, ctx: Ctx = Depends(require("databases:write")), db: Session = Depends(get_db)):
    tid = tenant_for_create(ctx, body.tenant_id)
    if db.get(Tenant, tid) is None:
        raise api_error(404, "not_found", "Tenant not found.")
    consume_quota(db, tid, "databases")
    for _ in range(5):
        name = f"{body.name}_{secrets.token_hex(3)}"
        if not db.scalar(select(CustomerDatabase.id).where(CustomerDatabase.engine == body.engine, CustomerDatabase.db_name == name)):
            break
    d = CustomerDatabase(tenant_id=tid, engine=body.engine, label=body.name, db_name=name, port=svc.DEFAULT_PORTS[body.engine])
    db.add(d)
    db.flush()
    u = DatabaseUser(database_id=d.id, tenant_id=tid, username=f"{name}_app", privilege="readwrite")
    db.add(u)
    db.flush()
    pw = svc.start_provision(db, d, u, ctx.user.id)
    log_event(db, tid, "database", d.id, ctx.user.id, "database.created", f"Database {name} ({body.engine}) created")
    audit(db, "database.created", actor=ctx.user.id, tenant_id=tid, target_type="database", target_id=d.id, detail={"engine": body.engine, "name": name}, ip=client_ip(request))
    db.commit()
    return {**dto(db, d, True), "credentials": _creds(d, u.username, pw)}


@router.get("/{database_id}")
def get_database(database_id: uuid.UUID, ctx: Ctx = Depends(require("databases:read")), db: Session = Depends(get_db)):
    return dto(db, _db(db, ctx, database_id), True)


@router.post("/{database_id}/retry")
def retry(database_id: uuid.UUID, request: Request, ctx: Ctx = Depends(require("databases:write")), db: Session = Depends(get_db)):
    d = _db(db, ctx, database_id)
    if d.status not in {"pending_server", "failed"}:
        raise api_error(409, "invalid_state", f"Cannot retry a database in state {d.status}.")
    u = db.scalars(select(DatabaseUser).where(DatabaseUser.database_id == d.id).order_by(DatabaseUser.created_at)).first()
    u.status = "pending"
    pw = svc.start_provision(db, d, u, ctx.user.id)
    audit(db, "database.retry", actor=ctx.user.id, tenant_id=d.tenant_id, target_type="database", target_id=d.id, ip=client_ip(request))
    db.commit()
    return {**dto(db, d, True), "credentials": _creds(d, u.username, pw)}


@router.get("/{database_id}/connection")
def connection(database_id: uuid.UUID, ctx: Ctx = Depends(require("databases:read")), db: Session = Depends(get_db)):
    """Connection details for the customer's own users. Never contains passwords or administrative credentials."""
    d = _db(db, ctx, database_id)
    users = list(db.scalars(select(DatabaseUser).where(DatabaseUser.database_id == d.id, DatabaseUser.status == "active")))
    port = d.port or svc.DEFAULT_PORTS[d.engine]
    scheme = "mysql" if d.engine == "mariadb" else "postgresql"
    return {
        "engine": d.engine, "host": d.host, "port": port, "database": d.db_name, "remote_access": False,
        "note": "The database listens on the server's local interface only; connect from your application on the same server. Passwords are shown once at creation or rotation.",
        "users": [{"username": u.username, "privilege": u.privilege, "uri_template": f"{scheme}://{u.username}:<password>@{d.host}:{port}/{d.db_name}"} for u in users],
    }


@router.get("/{database_id}/users")
def list_users(database_id: uuid.UUID, ctx: Ctx = Depends(require("databases:read")), db: Session = Depends(get_db)):
    d = _db(db, ctx, database_id)
    return [user_dto(u) for u in db.scalars(select(DatabaseUser).where(DatabaseUser.database_id == d.id, DatabaseUser.status != "dropped").order_by(DatabaseUser.created_at))]


@router.post("/{database_id}/users", status_code=201)
def create_user(database_id: uuid.UUID, body: UserIn, request: Request, ctx: Ctx = Depends(require("databases:write")), db: Session = Depends(get_db)):
    d = _db(db, ctx, database_id)
    if d.status != "ready":
        raise api_error(409, "database_not_ready", "The database is not ready.")
    uname = f"{d.db_name}_{body.suffix}"
    if len(uname) > 32:
        raise api_error(422, "validation_error", "Username too long.")
    if db.scalar(select(DatabaseUser.id).where(DatabaseUser.database_id == d.id, DatabaseUser.username == uname, DatabaseUser.status != "dropped")):
        raise api_error(409, "user_exists", "A user with that name already exists.")
    old = db.scalar(select(DatabaseUser).where(DatabaseUser.database_id == d.id, DatabaseUser.username == uname))
    pw = svc.new_password()
    job = enqueue_job(db, d.server_id, "db.create_user", {"engine": d.engine, "db_name": d.db_name, "username": uname, "privilege": body.privilege, "password": pw}, tenant_id=d.tenant_id, requested_by=ctx.user.id)
    if old:
        old.privilege, old.status, old.last_job_id = body.privilege, "pending", job.id
        u = old
    else:
        u = DatabaseUser(database_id=d.id, tenant_id=d.tenant_id, username=uname, privilege=body.privilege, status="pending", last_job_id=job.id)
        db.add(u)
    db.flush()
    log_event(db, d.tenant_id, "database", d.id, ctx.user.id, "database.user_created", f"User {uname} ({body.privilege}) requested")
    audit(db, "database.user_created", actor=ctx.user.id, tenant_id=d.tenant_id, target_type="database", target_id=d.id, detail={"username": uname, "privilege": body.privilege}, ip=client_ip(request))
    db.commit()
    return {**user_dto(u), "credentials": _creds(d, uname, pw)}


def _user(db: Session, d: CustomerDatabase, uid: uuid.UUID) -> DatabaseUser:
    u = db.scalar(select(DatabaseUser).where(DatabaseUser.id == uid, DatabaseUser.database_id == d.id, DatabaseUser.status != "dropped"))
    if u is None:
        raise api_error(404, "not_found", "User not found.")
    return u


@router.post("/{database_id}/users/{user_id}/rotate")
def rotate(database_id: uuid.UUID, user_id: uuid.UUID, request: Request, ctx: Ctx = Depends(require("databases:write")), db: Session = Depends(get_db)):
    d = _db(db, ctx, database_id)
    u = _user(db, d, user_id)
    if d.status != "ready" or u.status not in {"active", "failed"}:
        raise api_error(409, "invalid_state", "The user cannot be rotated right now.")
    pw = svc.new_password()
    job = enqueue_job(db, d.server_id, "db.rotate_password", {"engine": d.engine, "username": u.username, "password": pw}, tenant_id=d.tenant_id, requested_by=ctx.user.id)
    u.status, u.last_job_id = "rotating", job.id
    log_event(db, d.tenant_id, "database", d.id, ctx.user.id, "database.rotate", f"Credential rotation for {u.username} requested")
    audit(db, "database.credentials_rotated", actor=ctx.user.id, tenant_id=d.tenant_id, target_type="database", target_id=d.id, detail={"username": u.username}, ip=client_ip(request))
    db.commit()
    return {**user_dto(u), "credentials": _creds(d, u.username, pw)}


@router.delete("/{database_id}/users/{user_id}", status_code=202)
def drop_user(database_id: uuid.UUID, user_id: uuid.UUID, request: Request, ctx: Ctx = Depends(require("databases:write")), db: Session = Depends(get_db)):
    d = _db(db, ctx, database_id)
    u = _user(db, d, user_id)
    others = db.scalar(select(func.count()).select_from(DatabaseUser).where(DatabaseUser.database_id == d.id, DatabaseUser.id != u.id, DatabaseUser.privilege == "readwrite", DatabaseUser.status == "active"))
    if u.privilege == "readwrite" and not others:
        raise api_error(409, "last_writer", "Cannot remove the last read/write user.")
    job = enqueue_job(db, d.server_id, "db.drop_user", {"engine": d.engine, "username": u.username}, tenant_id=d.tenant_id, requested_by=ctx.user.id)
    u.status, u.last_job_id = "dropping", job.id
    audit(db, "database.user_dropped", actor=ctx.user.id, tenant_id=d.tenant_id, target_type="database", target_id=d.id, detail={"username": u.username}, ip=client_ip(request))
    db.commit()
    return user_dto(u)


@router.post("/{database_id}/size/refresh", status_code=202)
def refresh_size(database_id: uuid.UUID, ctx: Ctx = Depends(require("databases:read")), db: Session = Depends(get_db)):
    d = _db(db, ctx, database_id)
    if d.status != "ready":
        raise api_error(409, "database_not_ready", "The database is not ready.")
    job = enqueue_job(db, d.server_id, "db.size", {"engine": d.engine, "db_name": d.db_name}, tenant_id=d.tenant_id, requested_by=ctx.user.id)
    d.size_job_id = job.id
    db.commit()
    return {"job_id": str(job.id)}


# ------------------------------------------------------------------ backup / restore request workflow
@router.get("/{database_id}/requests")
def list_requests(database_id: uuid.UUID, ctx: Ctx = Depends(require("databases:read")), db: Session = Depends(get_db)):
    d = _db(db, ctx, database_id)
    rows = db.scalars(select(DatabaseBackupRequest).where(DatabaseBackupRequest.database_id == d.id).order_by(DatabaseBackupRequest.created_at.desc()).limit(100))
    return [req_dto(r) for r in rows]


@router.post("/{database_id}/backups", status_code=201)
def request_backup(database_id: uuid.UUID, request: Request, ctx: Ctx = Depends(require("databases:write")), db: Session = Depends(get_db)):
    d = _db(db, ctx, database_id)
    if d.status != "ready":
        raise api_error(409, "database_not_ready", "The database is not ready.")
    r = DatabaseBackupRequest(database_id=d.id, tenant_id=d.tenant_id, kind="backup", artifact_ref=svc.artifact_name(d), requested_by=ctx.user.id)
    db.add(r)
    db.flush()
    if ctx.can("backups:write"):  # requester is entitled to run backups of their own data
        svc.move(r, "approved")
        r.approved_by = ctx.user.id
        svc.start_backup_job(db, d, r, ctx.user.id)
    log_event(db, d.tenant_id, "database", d.id, ctx.user.id, "database.backup_requested", f"Backup {r.artifact_ref} requested")
    audit(db, "database.backup_requested", actor=ctx.user.id, tenant_id=d.tenant_id, target_type="database", target_id=d.id, ip=client_ip(request))
    db.commit()
    return req_dto(r)


@router.post("/{database_id}/restores", status_code=201)
def request_restore(database_id: uuid.UUID, body: RestoreIn, request: Request, ctx: Ctx = Depends(require("databases:write", "backups:restore")), db: Session = Depends(get_db)):
    d = _db(db, ctx, database_id)
    if d.status != "ready":
        raise api_error(409, "database_not_ready", "The database is not ready.")
    src = db.scalar(select(DatabaseBackupRequest).where(DatabaseBackupRequest.id == body.source_backup_id, DatabaseBackupRequest.database_id == d.id, DatabaseBackupRequest.kind == "backup"))
    if src is None:
        raise api_error(404, "not_found", "Backup not found.")
    if src.state != "succeeded" or not src.verified or not src.checksum:
        raise api_error(409, "backup_not_verified", "Only a completed, checksum-verified backup can be restored.")
    r = DatabaseBackupRequest(database_id=d.id, tenant_id=d.tenant_id, kind="restore", artifact_ref=src.artifact_ref, source_request_id=src.id, checksum=src.checksum, requested_by=ctx.user.id)
    db.add(r)
    db.flush()
    log_event(db, d.tenant_id, "database", d.id, ctx.user.id, "database.restore_requested", f"Restore of {src.artifact_ref} requested; awaiting approval")
    audit(db, "database.restore_requested", actor=ctx.user.id, tenant_id=d.tenant_id, target_type="database", target_id=d.id, detail={"backup": str(src.id)}, ip=client_ip(request))
    db.commit()
    return req_dto(r)


def _request(db: Session, d: CustomerDatabase, rid: uuid.UUID) -> DatabaseBackupRequest:
    r = db.scalar(select(DatabaseBackupRequest).where(DatabaseBackupRequest.id == rid, DatabaseBackupRequest.database_id == d.id))
    if r is None:
        raise api_error(404, "not_found", "Request not found.")
    return r


@router.post("/{database_id}/requests/{request_id}/approve")
def approve(database_id: uuid.UUID, request_id: uuid.UUID, body: ApproveIn, request: Request, ctx: Ctx = Depends(require("backups:restore")), db: Session = Depends(get_db)):
    d = _db(db, ctx, database_id)
    r = _request(db, d, request_id)
    if r.state != "requested":
        raise api_error(409, "invalid_transition", f"Request is already {r.state}.")
    if r.kind == "restore" and body.confirm_name != d.db_name:
        raise api_error(422, "confirmation_required", "Type the database name to confirm the restore (it overwrites current data).")
    svc.move(r, "approved")
    r.approved_by = ctx.user.id
    svc.start_backup_job(db, d, r, ctx.user.id)
    audit(db, f"database.{r.kind}_approved", actor=ctx.user.id, tenant_id=d.tenant_id, target_type="database", target_id=d.id, ip=client_ip(request))
    db.commit()
    return req_dto(r)


@router.post("/{database_id}/requests/{request_id}/reject")
def reject(database_id: uuid.UUID, request_id: uuid.UUID, request: Request, ctx: Ctx = Depends(require("backups:restore")), db: Session = Depends(get_db)):
    d = _db(db, ctx, database_id)
    r = _request(db, d, request_id)
    svc.move(r, "rejected")
    audit(db, f"database.{r.kind}_rejected", actor=ctx.user.id, tenant_id=d.tenant_id, target_type="database", target_id=d.id, ip=client_ip(request))
    db.commit()
    return req_dto(r)


# ------------------------------------------------------------------ destructive actions
@router.post("/{database_id}/destroy-token")
def destroy_token(database_id: uuid.UUID, request: Request, ctx: Ctx = Depends(require("databases:write")), db: Session = Depends(get_db)):
    d = _db(db, ctx, database_id)
    tok = svc.issue_destroy_token(d)
    audit(db, "database.destroy_token_issued", actor=ctx.user.id, tenant_id=d.tenant_id, target_type="database", target_id=d.id, ip=client_ip(request))
    db.commit()
    lb = svc.latest_verified_backup(db, d)
    return {"token": tok, "expires_in": int(svc.DESTROY_TOKEN_TTL.total_seconds()), "has_verified_backup": lb is not None, "requires": "Type the database name and provide this token. A verified backup from the last 7 days must exist."}


@router.post("/{database_id}/destroy", status_code=202)
def destroy(database_id: uuid.UUID, body: DestroyIn, request: Request, ctx: Ctx = Depends(require("databases:write")), db: Session = Depends(get_db)):
    d = _db(db, ctx, database_id)
    if body.confirm_name != d.db_name:
        raise api_error(422, "confirmation_required", "The typed name does not match the database name.")
    svc.check_destroy_token(d, body.token)
    never_provisioned = d.status in {"pending_server", "failed"}
    if d.status not in {"ready", "pending_server", "failed"}:
        raise api_error(409, "invalid_state", f"Cannot delete a database in state {d.status}.")
    if not never_provisioned and svc.latest_verified_backup(db, d) is None:
        raise api_error(409, "backup_required", "A verified backup from the last 7 days is required before deleting a database.")
    d.destroy_token_hash = d.destroy_token_expires = None  # single use
    users = [u.username for u in db.scalars(select(DatabaseUser).where(DatabaseUser.database_id == d.id))]
    if never_provisioned or not d.server_id:
        d.status, d.message = "deleted", "Database record removed."
        consume_quota(db, d.tenant_id, "databases", -1)
    else:
        job = enqueue_job(db, d.server_id, "db.drop", {"engine": d.engine, "db_name": d.db_name, "confirm_name": body.confirm_name, "users": users}, tenant_id=d.tenant_id, requested_by=ctx.user.id)
        d.destroy_job_id, d.status, d.message = job.id, "deleting", "Deleting database."
    log_event(db, d.tenant_id, "database", d.id, ctx.user.id, "database.destroy", "Database deletion confirmed")
    audit(db, "database.destroyed", actor=ctx.user.id, tenant_id=d.tenant_id, target_type="database", target_id=d.id, detail={"name": d.db_name}, ip=client_ip(request))
    db.commit()
    return dto(db, d)


@router.get("/{database_id}/history")
def history(database_id: uuid.UUID, limit: int = 50, ctx: Ctx = Depends(require("databases:read")), db: Session = Depends(get_db)):
    d = _db(db, ctx, database_id, sync=False)
    rows = db.scalars(select(ResourceEvent).where(ResourceEvent.resource_type == "database", ResourceEvent.resource_id == d.id).order_by(ResourceEvent.created_at.desc()).limit(min(limit, 200)))
    return [{"action": e.action, "summary": e.summary, "created_at": e.created_at} for e in rows]


@router.get("/{database_id}/usage")
def usage(database_id: uuid.UUID, ctx: Ctx = Depends(require("databases:read")), db: Session = Depends(get_db)):
    d = _db(db, ctx, database_id, sync=False)
    q = db.scalars(select(TenantQuota).where(TenantQuota.tenant_id == d.tenant_id, TenantQuota.resource.in_(["databases", "disk_mb"])))
    return {"size_bytes": d.size_bytes, "size_checked_at": d.size_checked_at, "note": None if d.size_bytes is not None else "not_reported: request a size refresh", "quotas": [{"resource": x.resource, "limit": x.limit_value, "used": x.used_value} for x in q]}
