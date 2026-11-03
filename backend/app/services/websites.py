"""Website lifecycle + Nginx provisioning orchestration. Only enqueues agent jobs; never runs commands."""
import hashlib
import re
import uuid
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.deps import api_error
from app.models.hosting import Application, Domain, ServerRuntime, Website
from app.models.infra import AgentJob, Server
from app.models.tenant import Tenant
from app.ops.nginx import DOC_ROOT_PREFIX
from app.services.audit import audit
from app.services.jobs import default_server, enqueue_job
from app.services.resources import job_ok, log_event, scrub_job_params

LIVE_STATES = {"installed", "reloading", "active", "rolled_back"}
BUSY_STATES = {"queued", "activating", "reloading", "rolling_back", "removing"}


def slugify(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")[:30] or "site"


def unique_site_slug(db: Session, tenant_id: uuid.UUID, name: str) -> str:
    base = slugify(name)
    slug, i = base, 1
    while db.scalar(select(Website.id).where(Website.tenant_id == tenant_id, Website.slug == slug)):
        i += 1
        slug = f"{base}-{i}"
    return slug


def site_name(site: Website) -> str:
    """Agent-side config name: unique, safe, derived from ids only."""
    return f"w-{site.id.hex[:16]}"


def build_document_root(tenant: Tenant, site: Website) -> str:
    root = f"{DOC_ROOT_PREFIX}{tenant.slug}/{site.slug}/{site.public_subdir}"
    assert root.startswith(DOC_ROOT_PREFIX) and ".." not in root
    return root


def installed_versions(db: Session, server_id: uuid.UUID | None, runtime: str) -> list[str]:
    if server_id is None:
        return []
    return list(db.scalars(select(ServerRuntime.version).where(ServerRuntime.server_id == server_id, ServerRuntime.runtime == runtime, ServerRuntime.installed.is_(True)).order_by(ServerRuntime.version)))


def check_runtime(db: Session, server_id: uuid.UUID | None, runtime: str, version: str | None) -> str | None:
    """Validate runtime/version against the server's reported catalog. Returns a note when the catalog is unknown."""
    if runtime in {"static", "proxy"}:
        return None
    if not version:
        raise api_error(422, "runtime_version_required", "Select a runtime version.")
    avail = installed_versions(db, server_id, runtime)
    reported = server_id is not None and db.scalar(select(ServerRuntime.id).where(ServerRuntime.server_id == server_id, ServerRuntime.installed.is_(True)).limit(1)) is not None
    if reported:
        if version not in avail:
            raise api_error(422, "runtime_unavailable", f"{runtime} {version} is not installed on this server. Available: {', '.join(avail) or 'none'}.")
        return None
    return "Runtime version could not be verified: the server has not reported its installed runtimes yet."


def assign_server(db: Session, site: Website) -> Server | None:
    if site.server_id:
        s = db.get(Server, site.server_id)
        if s and s.status != "disabled":
            return s
    s = default_server(db)
    if s:
        site.server_id = s.id
    return s


def mapped_domains(db: Session, site: Website, verified_only: bool = True) -> list[Domain]:
    stmt = select(Domain).where(Domain.website_id == site.id).order_by(Domain.kind, Domain.hostname)
    if verified_only:
        stmt = stmt.where(Domain.verification_state == "verified")
    return list(db.scalars(stmt))


def start_stage(db: Session, site: Website, tenant: Tenant, actor: uuid.UUID | None) -> Website:
    """(Re)queue staging of the proposed vhost. Idempotent and retry-able."""
    if site.status != "active":
        raise api_error(409, "website_not_active", f"Website is {site.status}.")
    if site.provision_state in BUSY_STATES:
        raise api_error(409, "provision_in_progress", "A provisioning operation is already in progress.")
    server = assign_server(db, site)
    if server is None:
        site.provision_state = "pending_server"
        site.provision_message = "No server is registered yet. Provisioning will be possible once a server exists; use Retry then."
        log_event(db, site.tenant_id, "website", site.id, actor, "website.provision_blocked", "Provisioning blocked: no server registered")
        return site
    names = [d.hostname for d in mapped_domains(db, site)]
    if not names:
        site.provision_state = "awaiting_domain"
        site.provision_message = "Add a domain and verify ownership before provisioning."
        raise api_error(409, "no_verified_domain", "Map at least one verified domain to this website first.")
    params = {"site": site_name(site), "server_names": names, "document_root": site.document_root, "runtime": site.runtime_type}
    if site.runtime_type == "php":
        params["php_version"] = site.runtime_version
    if site.runtime_type == "proxy":
        params["proxy_port"] = site.proxy_port
    job = enqueue_job(db, server.id, "nginx.stage_site", params, tenant_id=site.tenant_id, requested_by=actor)
    site.stage_job_id, site.provision_state, site.provision_message = job.id, "queued", "Validating proposed Nginx configuration."
    log_event(db, site.tenant_id, "website", site.id, actor, "website.stage", "Nginx configuration staging requested")
    return site


def start_activate(db: Session, site: Website, actor: uuid.UUID | None) -> Website:
    if site.provision_state != "staged" or not site.config_hash:
        raise api_error(409, "not_staged", "Stage and validate the configuration first.")
    if site.status != "active":
        raise api_error(409, "website_not_active", f"Website is {site.status}.")
    job = enqueue_job(db, site.server_id, "nginx.activate_site", {"site": site_name(site), "expected_hash": site.config_hash}, tenant_id=site.tenant_id, requested_by=actor)
    site.activate_job_id, site.provision_state, site.provision_message = job.id, "activating", "Installing validated configuration (existing config is backed up)."
    log_event(db, site.tenant_id, "website", site.id, actor, "website.activate", "Configuration activation requested")
    return site


def start_reload(db: Session, site: Website, actor: uuid.UUID | None) -> Website:
    if site.provision_state not in {"installed", "rolled_back"}:
        raise api_error(409, "not_installed", "Only a validated, installed configuration can be reloaded.")
    job = enqueue_job(db, site.server_id, "nginx.reload", {}, tenant_id=site.tenant_id, requested_by=actor)
    site.reload_job_id, site.provision_state, site.provision_message = job.id, "reloading", "Reloading Nginx."
    log_event(db, site.tenant_id, "website", site.id, actor, "website.reload", "Nginx reload requested")
    return site


def start_rollback(db: Session, site: Website, actor: uuid.UUID | None, backup: str | None = None) -> Website:
    if not site.server_id or site.provision_state in BUSY_STATES:
        raise api_error(409, "cannot_rollback", "Rollback is not possible right now.")
    job = enqueue_job(db, site.server_id, "nginx.rollback_site", {"site": site_name(site), **({"backup": backup} if backup else {})}, tenant_id=site.tenant_id, requested_by=actor)
    site.rollback_job_id, site.provision_state, site.provision_message = job.id, "rolling_back", "Restoring the previous configuration backup."
    log_event(db, site.tenant_id, "website", site.id, actor, "website.rollback", "Configuration rollback requested")
    return site


def start_takedown(db: Session, site: Website, actor: uuid.UUID | None) -> None:
    if site.server_id and site.provision_state in LIVE_STATES | {"staged"}:
        job = enqueue_job(db, site.server_id, "nginx.remove_site", {"site": site_name(site)}, tenant_id=site.tenant_id, requested_by=actor)
        site.rollback_job_id, site.provision_state, site.provision_message = job.id, "removing", "Removing site configuration (a backup is kept). Reload Nginx to take it offline."
        log_event(db, site.tenant_id, "website", site.id, actor, "website.takedown", "Configuration removal requested")


def _job(db: Session, jid: uuid.UUID | None) -> AgentJob | None:
    return db.get(AgentJob, jid) if jid else None


def sync_website(db: Session, site: Website) -> Website:
    """Advance provision_state from the finished agent job. Results come from the agent; we never assume success."""
    st = site.provision_state
    key = {"queued": "stage_job_id", "activating": "activate_job_id", "reloading": "reload_job_id", "rolling_back": "rollback_job_id", "removing": "rollback_job_id"}.get(st)
    if not key:
        return site
    job = _job(db, getattr(site, key))
    ok = job_ok(job)
    if ok is None:
        return site
    res = (job.result or {}) if job else {}
    reason = res.get("message") or job.error or "The operation failed."
    if job.status == "failed" or job.status == "timed_out":
        site.provision_state, site.provision_message = "failed", str(reason)[:500]
    elif res.get("status") == "not_configured":
        site.provision_state, site.provision_message = "not_configured", res.get("message", "Nginx is not installed on the server.")[:500]
    elif not ok:
        site.provision_state, site.provision_message = "failed", str(reason)[:500]
    else:
        if st == "queued":
            site.provision_state, site.config_hash = "staged", res.get("config_hash")
            site.provision_message = "Configuration validated. An authorised operator can activate it."
        elif st == "activating":
            site.provision_state, site.provision_message = "installed", "Configuration installed and validated. Reload Nginx to apply."
        elif st == "reloading":
            site.provision_state, site.provision_message = "active", "Live."
        elif st == "rolling_back":
            site.provision_state, site.provision_message = "rolled_back", "Previous configuration restored. Reload Nginx to apply."
        elif st == "removing":
            site.provision_state, site.provision_message = "removed", "Configuration removed. Reload Nginx to apply."
    log_event(db, site.tenant_id, "website", site.id, None, f"website.{site.provision_state}", (site.provision_message or "")[:300])
    scrub_job_params(db, job)
    return site


def sync_all(db: Session, sites: list[Website]) -> None:
    changed = False
    for s in sites:
        if s.provision_state in BUSY_STATES:
            before = s.provision_state
            sync_website(db, s)
            changed = changed or s.provision_state != before
    if changed:
        db.commit()


def usage_summary(db: Session, site: Website) -> dict:
    from sqlalchemy import func

    return {
        "domains": db.scalar(select(func.count()).select_from(Domain).where(Domain.website_id == site.id)) or 0,
        "verified_domains": db.scalar(select(func.count()).select_from(Domain).where(Domain.website_id == site.id, Domain.verification_state == "verified")) or 0,
        "applications": db.scalar(select(func.count()).select_from(Application).where(Application.website_id == site.id)) or 0,
        "disk_mb": site.disk_usage_mb,
        "disk_note": None if site.disk_usage_mb is not None else "not_reported: disk usage is collected by the monitoring agent",
    }


def config_fingerprint(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()
