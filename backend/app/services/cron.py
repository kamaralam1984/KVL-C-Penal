"""Customer cron jobs: scheduled HTTPS GET requests to the tenant's own verified domains.

Safety:
- Only https URLs whose host is a *verified* domain attached to the same tenant's website.
- The host is resolved once, must be public, and the request is pinned to that IP (no DNS rebinding), no redirects.
- At most 4 runs/hour per job (shared cron parser), per-tenant job limit, short timeout, small run history.
- After MAX_FAILURES consecutive failures the job is paused and the tenant is told by email.
"""
import time
import uuid
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from app.core.deps import api_error
from app.core.netsafe import UnsafeURL, parse_url, resolve_public
from app.models.cron import CronJob, CronRun
from app.models.hosting import Domain, Website
from app.services.backups import next_run, parse_cron
from app.services.redact import redact_text

MAX_JOBS_PER_TENANT = 20
MAX_FAILURES = 10
KEEP_RUNS = 25
TIMEOUT_SECONDS = 30.0
USER_AGENT = "KVL-Panel-Cron/1.0"

# ---- transport (replaceable in tests). Returns the HTTP status code. Never follows redirects. ----
Transport = Callable[[str, int, str, str, float], int]


def http_get(host: str, port: int, path: str, ip: str, timeout: float) -> int:
    import httpx

    target = f"[{ip}]" if ":" in ip else ip
    h = {"Host": host if port == 443 else f"{host}:{port}", "User-Agent": USER_AGENT}
    with httpx.Client(timeout=timeout, follow_redirects=False, verify=True, trust_env=False) as c:
        with c.stream("GET", f"https://{target}:{port}{path}", headers=h, extensions={"sni_hostname": host}) as r:
            for _ in r.iter_bytes(65536):  # read at most a little of the body, then drop the connection
                break
            return r.status_code


transport: Transport = http_get


def _now() -> datetime:
    return datetime.now(UTC)


def validate_schedule(expr: str) -> str:
    expr = " ".join((expr or "").split())
    try:
        parse_cron(expr)
        next_run(expr, _now())
    except ValueError as e:
        raise api_error(422, "invalid_schedule", str(e)) from None
    return expr


def validate_url(db: Session, website: Website, url: str) -> str:
    """URL must be https on a verified domain of this website's tenant (attached to this website)."""
    try:
        host, _port, _path = parse_url(url)
    except UnsafeURL as e:
        raise api_error(422, "invalid_url", str(e)) from None
    d = db.scalar(select(Domain).where(Domain.hostname == host, Domain.tenant_id == website.tenant_id, Domain.website_id == website.id))
    if d is None:
        raise api_error(422, "invalid_url", "The URL must use a domain attached to this website.")
    if d.verification_state != "verified":
        raise api_error(422, "domain_not_verified", "Verify the domain before scheduling requests to it.")
    return url


def dto(j: CronJob) -> dict[str, Any]:
    return {"id": str(j.id), "tenant_id": str(j.tenant_id), "website_id": str(j.website_id), "name": j.name, "url": j.url, "schedule": j.schedule,
            "enabled": j.enabled, "next_run_at": j.next_run_at, "last_run_at": j.last_run_at, "last_status": j.last_status, "last_http_code": j.last_http_code,
            "last_error": j.last_error, "consecutive_failures": j.consecutive_failures, "disabled_reason": j.disabled_reason, "created_at": j.created_at}


def run_dto(r: CronRun) -> dict[str, Any]:
    return {"id": str(r.id), "trigger": r.trigger, "status": r.status, "http_code": r.http_code, "duration_ms": r.duration_ms, "error": r.error, "started_at": r.started_at}


def create(db: Session, website: Website, name: str, url: str, schedule: str, enabled: bool = True) -> CronJob:
    n = db.scalar(select(func.count()).select_from(CronJob).where(CronJob.tenant_id == website.tenant_id)) or 0
    if n >= MAX_JOBS_PER_TENANT:
        raise api_error(409, "limit_reached", f"At most {MAX_JOBS_PER_TENANT} cron jobs per account.")
    if website.status != "active":
        raise api_error(409, "website_inactive", "The website is not active.")
    schedule = validate_schedule(schedule)
    url = validate_url(db, website, url)
    j = CronJob(tenant_id=website.tenant_id, website_id=website.id, name=name.strip(), url=url, schedule=schedule, enabled=enabled,
                next_run_at=next_run(schedule, _now()) if enabled else None)
    db.add(j)
    db.flush()
    return j


def update(db: Session, j: CronJob, *, name: str | None = None, url: str | None = None, schedule: str | None = None, enabled: bool | None = None) -> CronJob:
    if name is not None:
        j.name = name.strip()
    if url is not None:
        j.url = validate_url(db, db.get(Website, j.website_id), url)
    if schedule is not None:
        j.schedule = validate_schedule(schedule)
    if enabled is not None:
        j.enabled = enabled
        if enabled:
            j.consecutive_failures, j.disabled_reason = 0, None
    j.next_run_at = next_run(j.schedule, _now()) if j.enabled else None
    return j


def execute(db: Session, j: CronJob, trigger: str, now: datetime | None = None) -> CronRun:
    """Run one job now. Re-validates the URL against current domain state every time."""
    now = now or _now()
    t0 = time.monotonic()
    code: int | None = None
    status, err = "ok", None
    try:
        site = db.get(Website, j.website_id)
        if site is None or site.status != "active":
            raise RuntimeError("website is not active")
        validate_url(db, site, j.url)
        host, port, path = parse_url(j.url)
        ips = resolve_public(host)
        code = transport(host, port, path, ips[0], TIMEOUT_SECONDS)
        if not 200 <= code < 400:
            status, err = "http_error", f"HTTP {code}"
    except UnsafeURL as e:
        status, err = "error", f"unsafe URL: {e}"
    except Exception as e:  # noqa: BLE001 - every failure is recorded, never raised into the scheduler
        detail = getattr(e, "detail", None)
        msg = detail.get("message") if isinstance(detail, dict) else str(e)
        status, err = "error", f"{type(e).__name__}: {msg}"
    err = redact_text(err)[:300] if err else None
    r = CronRun(job_id=j.id, tenant_id=j.tenant_id, trigger=trigger, status=status, http_code=code, duration_ms=int((time.monotonic() - t0) * 1000), error=err, started_at=now)
    db.add(r)
    j.last_run_at, j.last_status, j.last_http_code, j.last_error = now, status, code, err
    j.consecutive_failures = 0 if status == "ok" else j.consecutive_failures + 1
    if j.consecutive_failures >= MAX_FAILURES and j.enabled:
        j.enabled, j.disabled_reason, j.next_run_at = False, f"paused after {MAX_FAILURES} failures in a row", None
        _notify_paused(db, j)
    db.flush()
    _trim(db, j.id)
    return r


def _trim(db: Session, job_id: uuid.UUID) -> None:
    keep = select(CronRun.id).where(CronRun.job_id == job_id).order_by(CronRun.started_at.desc()).limit(KEEP_RUNS)
    db.execute(delete(CronRun).where(CronRun.job_id == job_id, CronRun.id.not_in(keep)))


def _notify_paused(db: Session, j: CronJob) -> None:
    from app.services.audit import audit
    from app.services.mail import queue_email
    from app.services.ssl import _contact_email

    audit(db, "cron.paused", tenant_id=j.tenant_id, target_type="cron_job", target_id=j.id, detail={"name": j.name, "error": j.last_error})
    to = _contact_email(db, j.tenant_id)
    if to:
        queue_email(db, to, f"Cron job paused: {j.name}", f"Your scheduled request '{j.name}' ({j.url}) failed {MAX_FAILURES} times in a row and has been paused. "
                    f"Last error: {j.last_error}. Fix the URL and re-enable it in KVL Panel.", "cron_paused", j.tenant_id)


def run_due(db: Session, now: datetime | None = None, limit: int = 50) -> dict[str, int]:
    """Scheduler task (every minute)."""
    now = now or _now()
    jobs = db.scalars(select(CronJob).where(CronJob.enabled.is_(True), CronJob.next_run_at <= now).order_by(CronJob.next_run_at).limit(limit).with_for_update(skip_locked=True)).all()
    out = {"ran": 0, "ok": 0, "failed": 0}
    for j in jobs:
        j.next_run_at = next_run(j.schedule, now)  # advance first so a crash can't cause a hot loop
        r = execute(db, j, "schedule", now)
        out["ran"] += 1
        out["ok" if r.status == "ok" else "failed"] += 1
    return out
