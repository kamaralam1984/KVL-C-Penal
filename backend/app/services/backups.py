"""Backups and disaster recovery control plane.

Safety model (also enforced independently by the agent):
- every infrastructure action is an allow-listed agent job; the API never touches files;
- verification (sha256 + optional deep read) turns a backup into a *confirmed recovery point*;
- restores default to an isolated location; overwrite needs a verified backup, explicit confirmation, a typed phrase and a
  second person's approval, and the agent moves the old data aside instead of deleting it;
- retention is SIMULATED by default; pruning is a separate owner-approved job that the agent refuses unless a verified recovery point
  that is not itself being deleted still matches its checksum;
- encryption keys never leave the agent host (see docs/runbooks/backup-and-recovery.md).
"""
import json
import re
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.deps import api_error
from app.models.backups import BACKUP_TYPES, BackupDestination, BackupRecord, BackupSchedule, RestoreRequest, RetentionPolicy
from app.models.infra import AgentJob
from app.models.ownership import ResourceRef
from app.models.tenant import Tenant, TenantQuota
from app.services.audit import audit
from app.services.jobs import default_server, enqueue_job
from app.services.mail import queue_email
from app.services.ownership import require_ref
from app.services.tenants import setting


def now() -> datetime:
    return datetime.now(UTC)


def _slug(s: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]+", "-", s).strip("-._")[:40] or "backup"


# ----------------------------------------------------------------------------- cron
_RANGES = [(0, 59), (0, 23), (1, 31), (1, 12), (0, 6)]


def _field(txt: str, lo: int, hi: int, name: str) -> set[int]:
    out: set[int] = set()
    for part in txt.split(","):
        m = re.fullmatch(r"(\*|\d+(?:-\d+)?)(?:/(\d+))?", part)
        if not m:
            raise ValueError(f"invalid {name} field '{txt}'")
        base, step = m.group(1), int(m.group(2) or 1)
        if step < 1:
            raise ValueError(f"invalid step in {name}")
        if base == "*":
            a, b = lo, hi
        elif "-" in base:
            a, b = map(int, base.split("-"))
        else:
            a = b = int(base)
            if m.group(2):
                b = hi
        if not (lo <= a <= hi and lo <= b <= hi and a <= b):
            raise ValueError(f"{name} out of range ({lo}-{hi})")
        out.update(range(a, b + 1, step))
    return out


def parse_cron(expr: str) -> list[set[int]]:
    parts = (expr or "").split()
    if len(parts) != 5:
        raise ValueError("Use five fields: minute hour day-of-month month day-of-week.")
    sets = [_field(p, lo, hi, n) for p, (lo, hi), n in zip(parts, _RANGES, ["minute", "hour", "day", "month", "weekday"], strict=True)]
    if len(sets[0]) > 4:
        raise ValueError("Schedules may run at most 4 times per hour.")
    return sets


def next_run(expr: str, after: datetime) -> datetime:
    mins, hours, doms, months, dows = parse_cron(expr)
    day = after.replace(hour=0, minute=0, second=0, microsecond=0)
    for _ in range(366 * 5):
        if day.month in months and day.day in doms and ((day.weekday() + 1) % 7) in dows:
            for h in sorted(hours):
                for m in sorted(mins):
                    cand = day.replace(hour=h, minute=m)
                    if cand > after:
                        return cand
        day += timedelta(days=1)
    raise ValueError("Schedule never fires.")


# ----------------------------------------------------------------------------- views
def rec_view(r: BackupRecord) -> dict[str, Any]:
    return {"id": str(r.id), "tenant_id": str(r.tenant_id), "backup_type": r.backup_type, "resource_ref": r.resource_ref, "status": r.status, "verification_status": r.verification_status,
            "confirmed_recovery_point": r.verification_status == "verified" and r.status == "completed", "verified_at": r.verified_at, "size_bytes": r.size_bytes, "sha256": r.sha256,
            "encrypted": r.encrypted, "key_id": r.key_id, "entries": r.entries, "trigger": r.trigger, "schedule_id": str(r.schedule_id) if r.schedule_id else None,
            "offsite_status": r.offsite_status, "started_at": r.started_at, "finished_at": r.finished_at, "error": r.error, "created_at": r.created_at}


def restore_view(r: RestoreRequest) -> dict[str, Any]:
    return {"id": str(r.id), "backup_id": str(r.backup_id), "mode": r.mode, "status": r.status, "impact": r.impact, "location": r.location, "requested_by": str(r.requested_by) if r.requested_by else None,
            "approved_by": str(r.approved_by) if r.approved_by else None, "approved_at": r.approved_at, "error": r.error, "result": r.result, "created_at": r.created_at}


def policy_view(p: RetentionPolicy) -> dict[str, Any]:
    return {"id": str(p.id), "tenant_id": str(p.tenant_id) if p.tenant_id else None, "name": p.name, "keep_last": p.keep_last, "keep_daily": p.keep_daily, "keep_weekly": p.keep_weekly, "keep_monthly": p.keep_monthly, "min_keep": p.min_keep}


def schedule_view(s: BackupSchedule) -> dict[str, Any]:
    return {"id": str(s.id), "name": s.name, "backup_type": s.backup_type, "resource_ref": s.resource_ref, "cron": s.cron, "enabled": s.enabled, "encrypt": s.encrypt, "retention_policy_id": str(s.retention_policy_id) if s.retention_policy_id else None,
            "destination_id": str(s.destination_id) if s.destination_id else None, "last_run_at": s.last_run_at, "next_run_at": s.next_run_at}


def dest_view(d: BackupDestination) -> dict[str, Any]:
    return {"id": str(d.id), "name": d.name, "kind": d.kind, "config": d.config or {}, "has_credentials": bool(d.credentials_enc), "status": d.status, "last_test_at": d.last_test_at, "last_error": d.last_error,
            "note": "Off-site transfer for sftp/s3 is not implemented in this build; credentials are stored encrypted only." if d.kind != "local" else None}


# ----------------------------------------------------------------------------- alerts
def _alert(db: Session, tenant_id: uuid.UUID, action: str, subject: str, body: str, detail: dict) -> None:
    audit(db, action, tenant_id=tenant_id, target_type="backup", detail=detail)
    t = db.get(Tenant, tenant_id)
    if t and t.contact_email:
        queue_email(db, t.contact_email, subject, body, "backup_alert", tenant_id)


# ----------------------------------------------------------------------------- request / sync
def _server_for(db: Session, ref: ResourceRef | None) -> uuid.UUID:
    sid = (ref.server_id if ref and ref.server_id else None) or (default_server(db).id if default_server(db) else None)
    if sid is None:
        raise api_error(409, "not_configured", "No registered server is available to run backups.")
    return sid


def _saas_metadata(db: Session, tenant_id: uuid.UUID) -> dict[str, str]:
    t = db.get(Tenant, tenant_id)
    quotas = db.scalars(select(TenantQuota).where(TenantQuota.tenant_id == tenant_id)).all()
    refs = db.scalars(select(ResourceRef).where(ResourceRef.tenant_id == tenant_id)).all()
    meta = {"tenant": {"id": str(t.id), "name": t.name, "slug": t.slug, "plan": t.plan_code, "status": t.status, "settings": t.settings or {}},
            "quotas": [{"resource": q.resource, "limit": q.limit_value, "used": q.used_value} for q in quotas],
            "resources": [{"kind": r.kind, "ref": r.ref, "label": r.label} for r in refs], "exported_at": now().isoformat()}
    return {"tenant.json": json.dumps(meta, default=str, indent=1)}  # no credentials, tokens or keys by construction


def request_backup(db: Session, *, tenant_id: uuid.UUID, actor: uuid.UUID | None, backup_type: str, resource_ref: str | None, encrypt: bool = False, schedule: BackupSchedule | None = None) -> BackupRecord:
    if backup_type not in BACKUP_TYPES:
        raise api_error(422, "validation_error", "Unknown backup type.")
    params: dict[str, Any] = {"kind": backup_type, "scope": str(tenant_id), "encrypt": encrypt}
    ref = None
    if backup_type == "saas_config":
        params.update(name="saas-config", inline_files=_saas_metadata(db, tenant_id))
        resource_ref = None
    else:
        if not resource_ref:
            raise api_error(422, "validation_error", "resource_ref is required for this backup type.")
        kind = "database" if backup_type == "database" else "website"
        ref = require_ref(db, tenant_id, kind, resource_ref)  # 404 when not owned
        rel = {"website_files": ref.root_rel, "website_config": ref.config_rel, "database": ref.dump_rel}[backup_type]
        if not rel:
            what = {"website_files": "file location", "website_config": "configuration path", "database": "staged database dump (the databases module must provide dumps)"}[backup_type]
            raise api_error(409, "not_configured", f"No {what} is registered for this resource, so it cannot be backed up yet.")
        params.update(name=f"{backup_type}-{_slug(ref.ref)}", sources=[rel])
    disk = setting(db, "backup_disk", {})
    if disk.get("disk_id"):
        params["disk_id"] = disk["disk_id"]
    sid = _server_for(db, ref)
    rec = BackupRecord(tenant_id=tenant_id, backup_type=backup_type, resource_ref=resource_ref, encrypted=encrypt, server_id=sid, schedule_id=schedule.id if schedule else None,
                       destination_id=schedule.destination_id if schedule else None, trigger="schedule" if schedule else "manual", requested_by=actor)
    db.add(rec)
    db.flush()
    job = enqueue_job(db, sid, "backups.create", params, tenant_id=tenant_id, requested_by=actor, correlation_id=f"backup:{rec.id}")
    rec.job_id = job.id
    audit(db, "backup.requested", actor=actor, tenant_id=tenant_id, target_type="backup", target_id=rec.id, detail={"type": backup_type, "resource": resource_ref, "trigger": rec.trigger, "encrypt": encrypt})
    return rec


def _job(db: Session, jid: uuid.UUID | None) -> AgentJob | None:
    j = db.get(AgentJob, jid) if jid else None
    return j if j and j.status not in ("queued", "running") else None


def _jerr(j: AgentJob) -> str:
    return (j.error or (j.result or {}).get("message") or (j.result or {}).get("reason") or f"job {j.status}")[:500]


def request_verify(db: Session, rec: BackupRecord, actor: uuid.UUID | None, *, deep: bool = False) -> BackupRecord:
    if rec.status != "completed" or not rec.archive_path or not rec.sha256:
        raise api_error(409, "invalid_state", "Only completed backups can be verified.")
    if rec.verification_status == "verifying":
        raise api_error(409, "already_in_progress", "Verification is already running.")
    job = enqueue_job(db, rec.server_id, "backups.verify", {"archive": rec.archive_path, "expected_sha256": rec.sha256, "deep": deep}, tenant_id=rec.tenant_id, requested_by=actor, correlation_id=f"verify:{rec.id}")
    rec.verify_job_id, rec.verification_status = job.id, "verifying"
    audit(db, "backup.verify_requested", actor=actor, tenant_id=rec.tenant_id, target_type="backup", target_id=rec.id, detail={"deep": deep})
    return rec


def sync_backup(db: Session, rec: BackupRecord) -> BackupRecord:
    """Idempotently fold finished agent jobs (create / verify / prune) into the inventory row."""
    if rec.status == "running":
        j = _job(db, rec.job_id)
        if j is not None:
            res = j.result or {}
            rec.finished_at = j.finished_at or now()
            if j.status == "succeeded" and res.get("ok", True) is not False and res.get("sha256"):
                rec.status, rec.archive_path, rec.size_bytes, rec.sha256 = "completed", res["archive"], res.get("size"), res["sha256"]
                rec.entries, rec.encrypted, rec.key_id, rec.layout, rec.error = res.get("entries"), bool(res.get("encrypted")), res.get("key_id"), res.get("layout"), None
                audit(db, "backup.completed", tenant_id=rec.tenant_id, target_type="backup", target_id=rec.id, detail={"type": rec.backup_type, "size": rec.size_bytes, "encrypted": rec.encrypted})
                try:
                    request_verify(db, rec, None)
                except HTTPException:
                    pass
            else:
                rec.status, rec.error = "failed", _jerr(j)
                if not rec.alerted:
                    rec.alerted = True
                    _alert(db, rec.tenant_id, "backup.failed", f"Backup failed ({rec.backup_type.replace('_', ' ')})", f"A {rec.backup_type.replace('_', ' ')} backup did not complete.\nReason: {rec.error}\nYour data was not changed. We will retry on the next schedule; contact support if this continues.", {"backup_id": str(rec.id), "reason": rec.error})
    if rec.status == "completed" and rec.verification_status == "verifying":
        j = _job(db, rec.verify_job_id)
        if j is not None:
            res = j.result or {}
            if j.status == "succeeded" and res.get("ok") is True:
                rec.verification_status, rec.verified_at = "verified", now()
                audit(db, "backup.verified", tenant_id=rec.tenant_id, target_type="backup", target_id=rec.id, detail={"deep": res.get("deep", False)})
            else:
                bad = res.get("reason") == "checksum_mismatch"
                rec.verification_status, rec.error = ("mismatch" if bad else "failed"), (f"Checksum mismatch: stored backup no longer matches its recorded sha256." if bad else _jerr(j))
                _alert(db, rec.tenant_id, "backup.checksum_mismatch" if bad else "backup.verify_failed", "Backup verification failed", f"Verification of a {rec.backup_type.replace('_', ' ')} backup failed.\nReason: {rec.error}\nDo not rely on this backup; a new backup will be needed.", {"backup_id": str(rec.id), "reason": rec.error})
    if rec.status == "pruning":
        j = _job(db, rec.job_id)
        if j is not None:
            deleted = set((j.result or {}).get("deleted") or []) if j.status == "succeeded" else set()
            rec.status = "pruned" if rec.archive_path in deleted else "completed"
            if rec.status == "pruned":
                audit(db, "backup.pruned", tenant_id=rec.tenant_id, target_type="backup", target_id=rec.id, detail={"archive": rec.archive_path})
    return rec


def reconcile_all(db: Session) -> int:
    """Periodic task body: fold every in-flight backup/verification/restore into its row."""
    n = 0
    for r in db.scalars(select(BackupRecord).where(BackupRecord.status.in_(("running", "pruning")) | (BackupRecord.verification_status == "verifying"))):
        sync_backup(db, r)
        n += 1
    for rr in db.scalars(select(RestoreRequest).where(RestoreRequest.status == "running")):
        sync_restore(db, rr)
        n += 1
    return n


# ----------------------------------------------------------------------------- restore
def _target_for(db: Session, rec: BackupRecord) -> ResourceRef | None:
    return require_ref(db, rec.tenant_id, "website", rec.resource_ref) if rec.backup_type == "website_files" and rec.resource_ref else None


def preview_restore(db: Session, rec: BackupRecord, mode: str, actor: uuid.UUID | None) -> RestoreRequest:
    sync_backup(db, rec)
    if rec.status != "completed":
        raise api_error(409, "invalid_state", "Only completed backups can be restored.")
    if rec.verification_status in ("mismatch", "failed"):
        raise api_error(409, "backup_unverified", "This backup failed verification and cannot be restored.")
    target = None
    if mode == "overwrite":
        if rec.backup_type != "website_files" or rec.layout != "single":
            raise api_error(422, "overwrite_not_supported", "Overwrite restore is supported for website file backups only; restore to an isolated location instead.")
        target = _target_for(db, rec)
    token = uuid.uuid4().hex
    impact = {
        "backup": {"id": str(rec.id), "type": rec.backup_type, "resource": rec.resource_ref, "created_at": rec.created_at.isoformat() if rec.created_at else None, "size_bytes": rec.size_bytes, "entries": rec.entries, "encrypted": rec.encrypted},
        "mode": mode, "verified": rec.verification_status == "verified",
        "target": f"live site '{rec.resource_ref}'" if mode == "overwrite" else f"isolated folder restores/{token}",
        "will_overwrite_live_data": mode == "overwrite", "previous_data_kept": mode == "overwrite", "source_data_preserved": True,
        "requires_approval": mode == "overwrite", "requires_typed_confirmation": f"RESTORE {rec.resource_ref or rec.backup_type}",
        "notes": (["Existing files at the target are moved aside (not deleted) and kept until you remove them.", "A second person with backup management permission must approve before anything runs."]
                  if mode == "overwrite" else ["Files are extracted to a separate folder; live data is not touched. Copy what you need from it."]),
    }
    rr = RestoreRequest(tenant_id=rec.tenant_id, backup_id=rec.id, mode=mode, status="previewed", impact=impact, restore_token=token, requested_by=actor)
    db.add(rr)
    db.flush()
    audit(db, "restore.previewed", actor=actor, tenant_id=rec.tenant_id, target_type="restore", target_id=rr.id, detail={"backup_id": str(rec.id), "mode": mode})
    return rr


def _enqueue_restore(db: Session, rr: RestoreRequest, rec: BackupRecord, actor: uuid.UUID | None) -> None:
    p: dict[str, Any] = {"archive": rec.archive_path, "expected_sha256": rec.sha256, "restore_id": rr.restore_token, "mode": rr.mode}
    if rr.mode == "overwrite":
        ref = _target_for(db, rec)
        p.update(target=ref.root_rel, confirm_overwrite=True)
    job = enqueue_job(db, rec.server_id, "backups.restore", p, tenant_id=rr.tenant_id, requested_by=actor, correlation_id=f"restore:{rr.id}")
    rr.job_id, rr.status = job.id, "running"


def submit_restore(db: Session, rr: RestoreRequest, actor: uuid.UUID | None, *, confirm: bool, confirm_overwrite: bool, confirmation: str | None) -> RestoreRequest:
    if rr.status != "previewed":
        raise api_error(409, "invalid_state", f"Restore is {rr.status}.")
    rec = db.get(BackupRecord, rr.backup_id)
    sync_backup(db, rec)
    if not confirm:
        raise api_error(400, "confirmation_required", "Explicit confirmation is required to restore.")
    phrase = rr.impact["requires_typed_confirmation"]
    if confirmation != phrase:
        raise api_error(400, "typed_confirmation_required", f"Type '{phrase}' to confirm.")
    if rr.mode == "overwrite":
        if not confirm_overwrite:
            raise api_error(400, "overwrite_confirmation_required", "Overwriting live data needs confirm_overwrite=true.")
        if rec.verification_status != "verified":
            raise api_error(409, "backup_unverified", "Overwrite restores require a verified backup. Verify it first.")
        rr.status = "pending_approval"
        audit(db, "restore.approval_requested", actor=actor, tenant_id=rr.tenant_id, target_type="restore", target_id=rr.id, detail={"backup_id": str(rec.id)})
    else:
        _enqueue_restore(db, rr, rec, actor)
        audit(db, "restore.started", actor=actor, tenant_id=rr.tenant_id, target_type="restore", target_id=rr.id, detail={"backup_id": str(rec.id), "mode": "isolated"})
    rr.requested_by = actor
    return rr


def approve_restore(db: Session, rr: RestoreRequest, approver: uuid.UUID) -> RestoreRequest:
    if rr.status != "pending_approval":
        raise api_error(409, "invalid_state", "Restore is not awaiting approval.")
    if approver == rr.requested_by:
        raise api_error(403, "separate_approver_required", "A different person must approve this restore.")
    rec = db.get(BackupRecord, rr.backup_id)
    sync_backup(db, rec)
    if rec.verification_status != "verified":
        raise api_error(409, "backup_unverified", "The backup is no longer verified.")
    rr.approved_by, rr.approved_at = approver, now()
    _enqueue_restore(db, rr, rec, approver)
    audit(db, "restore.approved", actor=approver, tenant_id=rr.tenant_id, target_type="restore", target_id=rr.id, detail={"backup_id": str(rec.id), "mode": "overwrite"})
    return rr


def sync_restore(db: Session, rr: RestoreRequest) -> RestoreRequest:
    if rr.status != "running":
        return rr
    j = _job(db, rr.job_id)
    if j is None:
        return rr
    res = j.result or {}
    if j.status == "succeeded" and res.get("ok", True) is not False:
        rr.status, rr.location, rr.result = "completed", res.get("location"), {k: res.get(k) for k in ("files", "bytes", "mode", "previous_data_kept_at", "source_untouched")}
        audit(db, "restore.completed", tenant_id=rr.tenant_id, target_type="restore", target_id=rr.id, detail={"mode": rr.mode, "location": rr.location})
    else:
        rr.status, rr.error = "failed", _jerr(j)
        _alert(db, rr.tenant_id, "restore.failed", "Restore failed", f"A restore did not complete.\nReason: {rr.error}\nNo live data was replaced unless stated in the restore details.", {"restore_id": str(rr.id), "reason": rr.error})
    return rr


# ----------------------------------------------------------------------------- retention
def plan_retention(records: list[BackupRecord], p: RetentionPolicy) -> dict[str, Any]:
    items = sorted([r for r in records if r.status == "completed"], key=lambda r: r.finished_at or r.created_at, reverse=True)
    reasons: dict[uuid.UUID, list[str]] = {r.id: [] for r in items}
    ts = lambda r: r.finished_at or r.created_at  # noqa: E731

    def mark(selected: list[BackupRecord], why: str):
        for r in selected:
            reasons[r.id].append(why)

    mark(items[: p.keep_last], f"newest {p.keep_last}")
    mark(items[: p.min_keep], f"minimum {p.min_keep} always kept")
    for n, keyf, label in ((p.keep_daily, lambda r: ts(r).date(), "daily"), (p.keep_weekly, lambda r: ts(r).isocalendar()[:2], "weekly"), (p.keep_monthly, lambda r: (ts(r).year, ts(r).month), "monthly")):
        seen, picked = set(), []
        for r in items:
            k = keyf(r)
            if k not in seen and len(seen) < n:
                seen.add(k)
                picked.append(r)
        mark(picked, f"{label} slot")
    rp = next((r for r in items if r.verification_status == "verified"), None)
    if rp:
        reasons[rp.id].append("newest verified recovery point")
    keep = [r for r in items if reasons[r.id]]
    prune = [r for r in items if not reasons[r.id]]
    blocked = None
    if prune and rp is None:
        blocked = "No verified recovery point exists; nothing may be pruned until a backup is verified."
    row = lambda r, k: {"id": str(r.id), "created_at": ts(r), "size_bytes": r.size_bytes, "verification_status": r.verification_status, "keep": k, "reasons": reasons[r.id] or ["outside every retention slot"]}  # noqa: E731
    return {"dry_run": True, "deleted": 0, "total_considered": len(items), "would_keep": [row(r, True) for r in keep], "would_prune": [row(r, False) for r in prune],
            "would_free_bytes": sum(r.size_bytes or 0 for r in prune), "recovery_point_id": str(rp.id) if rp else None, "prune_blocked_reason": blocked}


def group_records(db: Session, tenant_id: uuid.UUID, backup_type: str, resource_ref: str | None) -> list[BackupRecord]:
    stmt = select(BackupRecord).where(BackupRecord.tenant_id == tenant_id, BackupRecord.backup_type == backup_type)
    stmt = stmt.where(BackupRecord.resource_ref == resource_ref) if resource_ref else stmt.where(BackupRecord.resource_ref.is_(None))
    rows = list(db.scalars(stmt))
    for r in rows:
        sync_backup(db, r)
    return rows


def prune(db: Session, *, tenant_id: uuid.UUID, actor: uuid.UUID, policy: RetentionPolicy, backup_type: str, resource_ref: str | None) -> dict[str, Any]:
    rows = group_records(db, tenant_id, backup_type, resource_ref)
    plan = plan_retention(rows, policy)
    if plan["prune_blocked_reason"]:
        raise api_error(409, "no_confirmed_recovery_point", plan["prune_blocked_reason"])
    if not plan["would_prune"]:
        return {**plan, "dry_run": False, "job_id": None, "message": "Nothing to prune."}
    by_id = {str(r.id): r for r in rows}
    rp = by_id[plan["recovery_point_id"]]
    victims = [by_id[x["id"]] for x in plan["would_prune"] if by_id[x["id"]].archive_path and by_id[x["id"]].sha256]
    if not victims:
        return {**plan, "dry_run": False, "job_id": None, "message": "Nothing to prune."}
    job = enqueue_job(db, rp.server_id, "backups.prune", {"confirm": True, "recovery_point": {"archive": rp.archive_path, "sha256": rp.sha256}, "delete": [{"archive": v.archive_path, "sha256": v.sha256} for v in victims]},
                      tenant_id=tenant_id, requested_by=actor, correlation_id=f"prune:{tenant_id}")
    for v in victims:
        v.status, v.job_id = "pruning", job.id
    audit(db, "backup.prune_requested", actor=actor, tenant_id=tenant_id, target_type="backup", detail={"policy": str(policy.id), "count": len(victims), "recovery_point": str(rp.id)})
    return {**plan, "dry_run": False, "job_id": str(job.id), "queued": len(victims)}


# ----------------------------------------------------------------------------- schedules
def sweep_schedules(db: Session, at: datetime | None = None) -> dict[str, Any]:
    at = at or now()
    out: dict[str, Any] = {"started": [], "errors": []}
    for s in db.scalars(select(BackupSchedule).where(BackupSchedule.enabled.is_(True), BackupSchedule.next_run_at <= at)):
        try:
            rec = request_backup(db, tenant_id=s.tenant_id, actor=None, backup_type=s.backup_type, resource_ref=s.resource_ref, encrypt=s.encrypt, schedule=s)
            out["started"].append(str(rec.id))
        except HTTPException as e:
            msg = e.detail.get("message") if isinstance(e.detail, dict) else str(e.detail)
            out["errors"].append({"schedule": str(s.id), "error": msg})
            _alert(db, s.tenant_id, "backup.schedule_failed", "Scheduled backup could not start", f"The scheduled backup '{s.name}' could not start.\nReason: {msg}", {"schedule_id": str(s.id), "reason": msg})
        s.last_run_at, s.next_run_at = at, next_run(s.cron, at)
    return out


def overview(db: Session, tenant_id: uuid.UUID | None) -> dict[str, Any]:
    stmt = select(BackupRecord)
    if tenant_id:
        stmt = stmt.where(BackupRecord.tenant_id == tenant_id)
    rows = list(db.scalars(stmt.order_by(BackupRecord.created_at.desc()).limit(1000)))
    for r in rows:
        sync_backup(db, r)
    wk = now() - timedelta(days=7)
    done = [r for r in rows if r.status == "completed"]
    last_ok: dict[str, Any] = {}
    for r in done:
        last_ok.setdefault(r.backup_type, r.finished_at or r.created_at)
    sstmt = select(BackupSchedule).where(BackupSchedule.enabled.is_(True))
    if tenant_id:
        sstmt = sstmt.where(BackupSchedule.tenant_id == tenant_id)
    scheds = list(db.scalars(sstmt))
    return {"total": len(rows), "completed": len(done), "failed_7d": sum(1 for r in rows if r.status == "failed" and (r.finished_at or r.created_at) >= wk), "running": sum(1 for r in rows if r.status == "running"),
            "verified": sum(1 for r in done if r.verification_status == "verified"), "unverified": sum(1 for r in done if r.verification_status in ("unverified", "verifying")),
            "verification_problems": sum(1 for r in done if r.verification_status in ("mismatch", "failed")), "total_size_bytes": sum(r.size_bytes or 0 for r in done),
            "last_success_by_type": last_ok, "active_schedules": len(scheds), "next_run_at": min((s.next_run_at for s in scheds if s.next_run_at), default=None),
            "offsite": {"configured": any(r.offsite_status == "copied" for r in done), "status": "not_configured" if not any(r.offsite_status == "copied" for r in done) else "configured"},
            "recent": [rec_view(r) for r in rows[:10]]}
