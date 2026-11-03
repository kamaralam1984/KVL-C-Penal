import json
import uuid
from datetime import UTC, datetime
from typing import Any, Literal

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.db import get_db
from app.core.deps import Ctx, api_error, client_ip, require, require_tenant, resolve_tenant
from app.core.security import encrypt_secret
from app.models.backups import BACKUP_TYPES, BackupDestination, BackupRecord, BackupSchedule, RestoreRequest, RetentionPolicy
from app.models.infra import AgentJob
from app.services import backups as svc
from app.services.audit import audit
from app.services.jobs import enqueue_job
from app.services.redact import redact_value

router = APIRouter(prefix="/backups", tags=["backups"])
Tid = uuid.UUID | None
BType = Literal["website_files", "database", "website_config", "saas_config"]


# ------------------------------------------------------------------ models
class BackupIn(BaseModel):
    backup_type: BType
    resource_ref: str | None = Field(None, max_length=253)
    encrypt: bool = False
    tenant_id: Tid = None


class VerifyIn(BaseModel):
    deep: bool = False


class PreviewIn(BaseModel):
    mode: Literal["isolated", "overwrite"] = "isolated"


class SubmitIn(BaseModel):
    confirm: bool = False
    confirm_overwrite: bool = False
    confirmation: str | None = Field(None, max_length=200)


class ScheduleIn(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    backup_type: BType
    resource_ref: str | None = Field(None, max_length=253)
    cron: str = Field(min_length=1, max_length=80)
    enabled: bool = True
    encrypt: bool = False
    retention_policy_id: uuid.UUID | None = None
    destination_id: uuid.UUID | None = None
    tenant_id: Tid = None


class SchedulePatch(BaseModel):
    name: str | None = Field(None, min_length=1, max_length=120)
    cron: str | None = Field(None, min_length=1, max_length=80)
    enabled: bool | None = None
    encrypt: bool | None = None
    retention_policy_id: uuid.UUID | None = None


class PolicyIn(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    keep_last: int = Field(3, ge=0, le=365)
    keep_daily: int = Field(7, ge=0, le=365)
    keep_weekly: int = Field(4, ge=0, le=104)
    keep_monthly: int = Field(3, ge=0, le=60)
    min_keep: int = Field(2, ge=1, le=100)
    tenant_id: Tid = None
    platform_default: bool = False


class SimulateIn(BaseModel):
    policy_id: uuid.UUID | None = None
    policy: PolicyIn | None = None
    backup_type: BType
    resource_ref: str | None = Field(None, max_length=253)
    tenant_id: Tid = None


class PruneIn(BaseModel):
    policy_id: uuid.UUID
    backup_type: BType
    resource_ref: str | None = Field(None, max_length=253)
    confirm: bool = False
    confirmation: str | None = Field(None, max_length=50)
    tenant_id: Tid = None


class DestIn(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    kind: Literal["local", "sftp", "s3"]
    config: dict[str, Any] = Field(default_factory=dict)
    credentials: dict[str, str] | None = Field(None, repr=False)
    tenant_id: Tid = None


# ------------------------------------------------------------------ helpers
def _rec(db: Session, ctx: Ctx, bid: uuid.UUID) -> BackupRecord:
    r = db.get(BackupRecord, bid)
    if r is None or (not ctx.is_platform and r.tenant_id != ctx.tenant_id):
        raise api_error(404, "not_found", "Backup not found.")
    return r


def _restore(db: Session, ctx: Ctx, rid: uuid.UUID) -> RestoreRequest:
    r = db.get(RestoreRequest, rid)
    if r is None or (not ctx.is_platform and r.tenant_id != ctx.tenant_id):
        raise api_error(404, "not_found", "Restore not found.")
    return r


def _policy(db: Session, ctx: Ctx, pid: uuid.UUID, tid: uuid.UUID | None) -> RetentionPolicy:
    p = db.get(RetentionPolicy, pid)
    if p is None or (p.tenant_id is not None and p.tenant_id != (tid or ctx.tenant_id) and not ctx.is_platform):
        raise api_error(404, "not_found", "Policy not found.")
    if p.tenant_id is not None and tid and p.tenant_id != tid:
        raise api_error(404, "not_found", "Policy not found.")
    return p


def _cron_ok(expr: str) -> None:
    try:
        svc.parse_cron(expr)
        svc.next_run(expr, datetime.now(UTC))
    except ValueError as e:
        raise api_error(422, "invalid_cron", str(e)) from None


# ------------------------------------------------------------------ overview / inventory
@router.get("/overview")
def overview(tenant_id: Tid = None, ctx: Ctx = Depends(require("backups:read")), db: Session = Depends(get_db)):
    data = svc.overview(db, resolve_tenant(ctx, tenant_id))
    db.commit()
    return data


@router.get("")
def list_backups(backup_type: str | None = None, status: str | None = None, resource_ref: str | None = None, tenant_id: Tid = None, limit: int = 50, offset: int = 0, ctx: Ctx = Depends(require("backups:read")), db: Session = Depends(get_db)):
    tid = resolve_tenant(ctx, tenant_id)
    stmt = select(BackupRecord)
    if tid:
        stmt = stmt.where(BackupRecord.tenant_id == tid)
    if backup_type:
        stmt = stmt.where(BackupRecord.backup_type == backup_type)
    if status:
        stmt = stmt.where(BackupRecord.status == status)
    if resource_ref:
        stmt = stmt.where(BackupRecord.resource_ref == resource_ref)
    rows = db.scalars(stmt.order_by(BackupRecord.created_at.desc()).limit(min(limit, 200)).offset(offset)).all()
    for r in rows:
        svc.sync_backup(db, r)
    db.commit()
    return {"items": [svc.rec_view(r) for r in rows]}


@router.post("", status_code=202)
def request_backup(body: BackupIn, request: Request, ctx: Ctx = Depends(require("backups:write")), db: Session = Depends(get_db)):
    tid = require_tenant(ctx, body.tenant_id)
    rec = svc.request_backup(db, tenant_id=tid, actor=ctx.user.id, backup_type=body.backup_type, resource_ref=body.resource_ref, encrypt=body.encrypt)
    db.commit()
    return svc.rec_view(rec)


# ------------------------------------------------------------------ static sub-resources (declared before /{bid})
@router.get("/history")
def history(tenant_id: Tid = None, limit: int = 50, ctx: Ctx = Depends(require("backups:read")), db: Session = Depends(get_db)):
    tid = resolve_tenant(ctx, tenant_id)
    stmt = select(AgentJob).where(AgentJob.operation.like("backups.%"))
    if tid:
        stmt = stmt.where(AgentJob.tenant_id == tid)
    rows = db.scalars(stmt.order_by(AgentJob.created_at.desc()).limit(min(limit, 200))).all()
    return {"items": [{"id": str(j.id), "operation": j.operation, "status": j.status, "error": redact_value(j.error) if j.error else None, "created_at": j.created_at, "finished_at": j.finished_at} for j in rows]}


@router.get("/schedules")
def list_schedules(tenant_id: Tid = None, ctx: Ctx = Depends(require("backups:read")), db: Session = Depends(get_db)):
    tid = resolve_tenant(ctx, tenant_id)
    stmt = select(BackupSchedule)
    if tid:
        stmt = stmt.where(BackupSchedule.tenant_id == tid)
    return {"items": [svc.schedule_view(s) for s in db.scalars(stmt.order_by(BackupSchedule.name))]}


@router.post("/schedules", status_code=201)
def create_schedule(body: ScheduleIn, request: Request, ctx: Ctx = Depends(require("backups:write")), db: Session = Depends(get_db)):
    from app.services.ownership import require_owned

    tid = require_tenant(ctx, body.tenant_id)
    _cron_ok(body.cron)
    if body.backup_type != "saas_config":
        if not body.resource_ref:
            raise api_error(422, "validation_error", "resource_ref is required for this backup type.")
        require_owned(db, tid, "database" if body.backup_type == "database" else "website", body.resource_ref)
    if body.retention_policy_id:
        _policy(db, ctx, body.retention_policy_id, tid)
    s = BackupSchedule(tenant_id=tid, name=body.name, backup_type=body.backup_type, resource_ref=body.resource_ref if body.backup_type != "saas_config" else None, cron=body.cron.strip(), enabled=body.enabled, encrypt=body.encrypt,
                       retention_policy_id=body.retention_policy_id, destination_id=body.destination_id, created_by=ctx.user.id, next_run_at=svc.next_run(body.cron, datetime.now(UTC)))
    db.add(s)
    db.flush()
    audit(db, "backup.schedule_created", actor=ctx.user.id, tenant_id=tid, target_type="backup_schedule", target_id=s.id, detail={"cron": s.cron, "type": s.backup_type}, ip=client_ip(request))
    db.commit()
    return svc.schedule_view(s)


def _sched(db: Session, ctx: Ctx, sid: uuid.UUID) -> BackupSchedule:
    s = db.get(BackupSchedule, sid)
    if s is None or (not ctx.is_platform and s.tenant_id != ctx.tenant_id):
        raise api_error(404, "not_found", "Schedule not found.")
    return s


@router.patch("/schedules/{sid}")
def patch_schedule(sid: uuid.UUID, body: SchedulePatch, request: Request, ctx: Ctx = Depends(require("backups:write")), db: Session = Depends(get_db)):
    s = _sched(db, ctx, sid)
    for k, v in body.model_dump(exclude_unset=True).items():
        if k == "cron":
            _cron_ok(v)
        setattr(s, k, v)
    if body.cron is not None or body.enabled:
        s.next_run_at = svc.next_run(s.cron, datetime.now(UTC))
    audit(db, "backup.schedule_updated", actor=ctx.user.id, tenant_id=s.tenant_id, target_type="backup_schedule", target_id=s.id, detail=body.model_dump(exclude_unset=True, mode="json"), ip=client_ip(request))
    db.commit()
    return svc.schedule_view(s)


@router.delete("/schedules/{sid}", status_code=204)
def delete_schedule(sid: uuid.UUID, request: Request, ctx: Ctx = Depends(require("backups:write")), db: Session = Depends(get_db)):
    s = _sched(db, ctx, sid)
    audit(db, "backup.schedule_deleted", actor=ctx.user.id, tenant_id=s.tenant_id, target_type="backup_schedule", target_id=s.id, ip=client_ip(request))
    db.delete(s)  # the schedule only; existing backups are never touched
    db.commit()


@router.post("/schedules/sweep")
def sweep(ctx: Ctx = Depends(require("backups:manage")), db: Session = Depends(get_db)):
    """Start every due scheduled backup (the function a periodic task calls). Platform staff only."""
    if not ctx.is_platform:
        raise api_error(403, "forbidden", "Platform staff only.")
    out = svc.sweep_schedules(db)
    out["reconciled"] = svc.reconcile_all(db)
    db.commit()
    return out


@router.get("/retention-policies")
def list_policies(tenant_id: Tid = None, ctx: Ctx = Depends(require("backups:read")), db: Session = Depends(get_db)):
    tid = resolve_tenant(ctx, tenant_id)
    stmt = select(RetentionPolicy).where((RetentionPolicy.tenant_id == tid) | RetentionPolicy.tenant_id.is_(None)) if tid else select(RetentionPolicy)
    return {"items": [svc.policy_view(p) for p in db.scalars(stmt.order_by(RetentionPolicy.name))]}


@router.post("/retention-policies", status_code=201)
def create_policy(body: PolicyIn, request: Request, ctx: Ctx = Depends(require("backups:write")), db: Session = Depends(get_db)):
    if body.platform_default:
        if not ctx.is_platform or not ctx.can("backups:manage"):
            raise api_error(403, "forbidden", "Platform default policies need backups:manage.")
        tid = None
    else:
        tid = require_tenant(ctx, body.tenant_id)
    p = RetentionPolicy(tenant_id=tid, name=body.name, keep_last=body.keep_last, keep_daily=body.keep_daily, keep_weekly=body.keep_weekly, keep_monthly=body.keep_monthly, min_keep=body.min_keep)
    db.add(p)
    db.flush()
    audit(db, "backup.retention_created", actor=ctx.user.id, tenant_id=tid, target_type="retention_policy", target_id=p.id, detail=body.model_dump(mode="json", exclude={"tenant_id"}), ip=client_ip(request))
    db.commit()
    return svc.policy_view(p)


@router.patch("/retention-policies/{pid}")
def patch_policy(pid: uuid.UUID, body: PolicyIn, request: Request, ctx: Ctx = Depends(require("backups:write")), db: Session = Depends(get_db)):
    p = _policy(db, ctx, pid, None)
    if p.tenant_id is None and not (ctx.is_platform and ctx.can("backups:manage")):
        raise api_error(403, "forbidden", "Platform default policies need backups:manage.")
    for k in ("name", "keep_last", "keep_daily", "keep_weekly", "keep_monthly", "min_keep"):
        setattr(p, k, getattr(body, k))
    audit(db, "backup.retention_updated", actor=ctx.user.id, tenant_id=p.tenant_id, target_type="retention_policy", target_id=p.id, detail=body.model_dump(mode="json", exclude={"tenant_id"}), ip=client_ip(request))
    db.commit()
    return svc.policy_view(p)


@router.post("/retention/simulate")
def simulate(body: SimulateIn, ctx: Ctx = Depends(require("backups:read")), db: Session = Depends(get_db)):
    """What WOULD be pruned. Reads inventory only: no job is queued and nothing is deleted."""
    tid = require_tenant(ctx, body.tenant_id)
    if body.policy_id:
        p = _policy(db, ctx, body.policy_id, tid)
    elif body.policy:
        p = RetentionPolicy(name=body.policy.name, keep_last=body.policy.keep_last, keep_daily=body.policy.keep_daily, keep_weekly=body.policy.keep_weekly, keep_monthly=body.policy.keep_monthly, min_keep=body.policy.min_keep)
    else:
        raise api_error(422, "validation_error", "policy_id or policy is required.")
    rows = svc.group_records(db, tid, body.backup_type, body.resource_ref)
    out = svc.plan_retention(rows, p)
    db.commit()
    return out


@router.post("/retention/prune")
def prune(body: PruneIn, request: Request, ctx: Ctx = Depends(require("backups:manage")), db: Session = Depends(get_db)):
    """Owner-approved pruning. Refuses without explicit confirmation and without a verified recovery point that is kept."""
    tid = require_tenant(ctx, body.tenant_id)
    if not body.confirm or body.confirmation != "PRUNE":
        raise api_error(400, "confirmation_required", "Set confirm=true and type PRUNE to delete old backups.")
    p = _policy(db, ctx, body.policy_id, tid)
    out = svc.prune(db, tenant_id=tid, actor=ctx.user.id, policy=p, backup_type=body.backup_type, resource_ref=body.resource_ref)
    db.commit()
    return out


@router.get("/restores")
def list_restores(tenant_id: Tid = None, ctx: Ctx = Depends(require("backups:read")), db: Session = Depends(get_db)):
    tid = resolve_tenant(ctx, tenant_id)
    stmt = select(RestoreRequest)
    if tid:
        stmt = stmt.where(RestoreRequest.tenant_id == tid)
    rows = db.scalars(stmt.order_by(RestoreRequest.created_at.desc()).limit(100)).all()
    for r in rows:
        svc.sync_restore(db, r)
    db.commit()
    return {"items": [svc.restore_view(r) for r in rows]}


@router.get("/restores/{rid}")
def get_restore(rid: uuid.UUID, ctx: Ctx = Depends(require("backups:read")), db: Session = Depends(get_db)):
    r = _restore(db, ctx, rid)
    svc.sync_restore(db, r)
    db.commit()
    return svc.restore_view(r)


@router.post("/restores/{rid}/submit", status_code=202)
def submit_restore(rid: uuid.UUID, body: SubmitIn, request: Request, ctx: Ctx = Depends(require("backups:restore")), db: Session = Depends(get_db)):
    r = _restore(db, ctx, rid)
    svc.submit_restore(db, r, ctx.user.id, confirm=body.confirm, confirm_overwrite=body.confirm_overwrite, confirmation=body.confirmation)
    db.commit()
    return svc.restore_view(r)


@router.post("/restores/{rid}/approve", status_code=202)
def approve_restore(rid: uuid.UUID, ctx: Ctx = Depends(require("backups:manage")), db: Session = Depends(get_db)):
    r = _restore(db, ctx, rid)
    svc.approve_restore(db, r, ctx.user.id)
    db.commit()
    return svc.restore_view(r)


@router.post("/restores/{rid}/reject")
def reject_restore(rid: uuid.UUID, ctx: Ctx = Depends(require("backups:manage")), db: Session = Depends(get_db)):
    r = _restore(db, ctx, rid)
    if r.status not in ("previewed", "pending_approval"):
        raise api_error(409, "invalid_state", "Restore can no longer be rejected.")
    r.status = "rejected"
    audit(db, "restore.rejected", actor=ctx.user.id, tenant_id=r.tenant_id, target_type="restore", target_id=r.id)
    db.commit()
    return svc.restore_view(r)


@router.get("/destinations")
def list_destinations(tenant_id: Tid = None, ctx: Ctx = Depends(require("backups:read")), db: Session = Depends(get_db)):
    tid = resolve_tenant(ctx, tenant_id)
    stmt = select(BackupDestination).where((BackupDestination.tenant_id == tid) | BackupDestination.tenant_id.is_(None)) if tid else select(BackupDestination)
    rows = [svc.dest_view(d) for d in db.scalars(stmt.order_by(BackupDestination.name))]
    return {"items": rows, "offsite_status": "not_configured" if not rows else "configured"}


_REQUIRED = {"local": ["path"], "sftp": ["host", "username", "path"], "s3": ["bucket", "region"]}
_CRED = {"local": [], "sftp": [], "s3": ["access_key_id", "secret_access_key"]}


@router.post("/destinations", status_code=201)
def create_destination(body: DestIn, request: Request, ctx: Ctx = Depends(require("backups:manage")), db: Session = Depends(get_db)):
    tid = body.tenant_id if ctx.is_platform else require_tenant(ctx, body.tenant_id)
    missing = [k for k in _REQUIRED[body.kind] if not body.config.get(k)]
    if missing:
        raise api_error(422, "validation_error", f"Missing settings: {', '.join(missing)}.")
    cfg = {k: v for k, v in body.config.items() if k in {"path", "host", "port", "username", "bucket", "region", "endpoint", "prefix"} and isinstance(v, (str, int))}
    if body.kind == "local" and (not str(cfg["path"]).replace("/", "").replace(".", "").replace("-", "").replace("_", "").isalnum() or ".." in str(cfg["path"]) or str(cfg["path"]).startswith("/")):
        raise api_error(422, "validation_error", "Local path must be a simple relative path on the agent host.")
    creds = body.credentials or {}
    has_creds = all(creds.get(k) for k in _CRED[body.kind]) and (body.kind != "sftp" or bool(creds.get("password") or creds.get("private_key")))
    d = BackupDestination(tenant_id=tid, name=body.name, kind=body.kind, config=cfg, credentials_enc=encrypt_secret(json.dumps(creds)) if creds else None,
                          status="configured_untested" if (has_creds or body.kind == "local") else "not_configured")
    db.add(d)
    db.flush()
    audit(db, "backup.destination_created", actor=ctx.user.id, tenant_id=tid, target_type="backup_destination", target_id=d.id, detail={"kind": body.kind, "name": body.name}, ip=client_ip(request))
    db.commit()
    return svc.dest_view(d)


@router.post("/destinations/{did}/test")
def test_destination(did: uuid.UUID, ctx: Ctx = Depends(require("backups:manage")), db: Session = Depends(get_db)):
    d = db.get(BackupDestination, did)
    if d is None or (not ctx.is_platform and d.tenant_id != ctx.tenant_id):
        raise api_error(404, "not_found", "Destination not found.")
    if d.kind != "local":
        d.status, d.last_error, d.last_test_at = "not_implemented", "SFTP/S3 transfer clients are not implemented in this build.", datetime.now(UTC)
        db.commit()
        return svc.dest_view(d)
    from app.services.jobs import default_server

    srv = default_server(db)
    if srv is None:
        raise api_error(409, "not_configured", "No registered server is available to test the destination.")
    job = enqueue_job(db, srv.id, "backups.disk_check", {"dest_root": d.config["path"]}, tenant_id=d.tenant_id, requested_by=ctx.user.id)
    d.last_test_at = datetime.now(UTC)
    audit(db, "backup.destination_tested", actor=ctx.user.id, tenant_id=d.tenant_id, target_type="backup_destination", target_id=d.id)
    db.commit()
    return {**svc.dest_view(d), "job_id": str(job.id)}


@router.delete("/destinations/{did}", status_code=204)
def delete_destination(did: uuid.UUID, ctx: Ctx = Depends(require("backups:manage")), db: Session = Depends(get_db)):
    d = db.get(BackupDestination, did)
    if d is None or (not ctx.is_platform and d.tenant_id != ctx.tenant_id):
        raise api_error(404, "not_found", "Destination not found.")
    audit(db, "backup.destination_deleted", actor=ctx.user.id, tenant_id=d.tenant_id, target_type="backup_destination", target_id=d.id)
    db.delete(d)
    db.commit()


# ------------------------------------------------------------------ one backup
@router.get("/{bid}")
def get_backup(bid: uuid.UUID, ctx: Ctx = Depends(require("backups:read")), db: Session = Depends(get_db)):
    r = _rec(db, ctx, bid)
    svc.sync_backup(db, r)
    db.commit()
    return svc.rec_view(r)


@router.post("/{bid}/verify", status_code=202)
def verify(bid: uuid.UUID, body: VerifyIn | None = None, ctx: Ctx = Depends(require("backups:write")), db: Session = Depends(get_db)):
    r = _rec(db, ctx, bid)
    svc.sync_backup(db, r)
    svc.request_verify(db, r, ctx.user.id, deep=bool(body and body.deep))
    db.commit()
    return svc.rec_view(r)


@router.post("/{bid}/restore-preview", status_code=201)
def restore_preview(bid: uuid.UUID, body: PreviewIn, ctx: Ctx = Depends(require("backups:write")), db: Session = Depends(get_db)):
    """Impact preview. Creates a restore request in 'previewed' state; nothing runs until it is submitted."""
    r = _rec(db, ctx, bid)
    rr = svc.preview_restore(db, r, body.mode, ctx.user.id)
    db.commit()
    return svc.restore_view(rr)
