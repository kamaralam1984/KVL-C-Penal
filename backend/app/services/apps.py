"""Application/runtime orchestration. Enqueues agent jobs only; applies finished job results to our records."""
import re
import secrets
import uuid
from datetime import UTC, datetime

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.deps import api_error
from app.core.security import decrypt_secret
from app.models.hosting import AppDeployment, AppEnvVar, Application, ServerRuntime, Website
from app.models.infra import AgentJob, Server
from app.ops.apps import ENTRY_NODE_RE, ENTRY_PY_RE
from app.services.jobs import default_server, enqueue_job
from app.services.redact import redact_text
from app.services.resources import job_ok, log_event, scrub_job_params

RUNTIMES = ("php", "wordpress", "nodejs", "python")
SUPPORTED_VERSIONS = {
    "php": ["7.4", "8.0", "8.1", "8.2", "8.3", "8.4"],
    "nodejs": ["18", "20", "22", "24"],
    "python": ["3.9", "3.10", "3.11", "3.12", "3.13", "3.14"],
}
CATALOG_KIND = {"php": "php", "wordpress": "php", "nodejs": "nodejs", "python": "python"}
PROCESS_RUNTIMES = {"nodejs", "python"}
WORDPRESS_REPO = "https://github.com/WordPress/WordPress.git"
RESERVED_ENV = {"PATH", "HOME", "USER", "SHELL", "IFS", "ENV", "BASH_ENV", "PWD", "OLDPWD", "TMPDIR", "NODE_OPTIONS", "NODE_PATH", "PYTHONPATH", "PYTHONHOME", "PYTHONSTARTUP", "PYTHONINSPECT", "PORT", "GIT_SSH_COMMAND", "GIT_ASKPASS", "GIT_CONFIG_GLOBAL", "NPM_CONFIG_REGISTRY"}
RESERVED_PREFIXES = ("LD_", "KVL_", "DYLD_", "NPM_CONFIG_", "GIT_", "PM2_", "BASH_")


def check_env_name(name: str) -> None:
    if name in RESERVED_ENV or name.startswith(RESERVED_PREFIXES):
        raise api_error(422, "reserved_env_name", f"Environment variable '{name}' is reserved by the platform.")


def slugify(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")[:30] or "app"


def agent_name(app: Application) -> str:
    return f"app-{app.id.hex[:16]}"


# ---------------------------------------------------------------- runtime catalog
def catalog(db: Session, server_id: uuid.UUID | None) -> dict[str, list[str]]:
    out: dict[str, list[str]] = {"php": [], "nodejs": [], "python": []}
    if server_id is None:
        return out
    for rt, v in db.execute(select(ServerRuntime.runtime, ServerRuntime.version).where(ServerRuntime.server_id == server_id, ServerRuntime.installed.is_(True))):
        out.setdefault(rt, []).append(v)
    for k in out:
        out[k].sort(key=lambda s: [int(x) for x in s.split(".")])
    return out


def ingest_runtime_reports(db: Session, server_id: uuid.UUID) -> bool:
    """Apply the newest finished apps.detect_runtimes job result for the server, if it is newer than what we hold."""
    job = db.scalars(select(AgentJob).where(AgentJob.server_id == server_id, AgentJob.operation == "apps.detect_runtimes", AgentJob.status == "succeeded").order_by(AgentJob.finished_at.desc().nullslast(), AgentJob.created_at.desc())).first()
    if not job or not job.result or job.result.get("ok") is False:
        return False
    newest = db.scalar(select(func.max(ServerRuntime.reported_at)).where(ServerRuntime.server_id == server_id))
    stamp = job.finished_at or job.created_at
    if newest and newest >= stamp:
        return False
    reported = job.result.get("runtimes") or {}
    existing = {(r.runtime, r.version): r for r in db.scalars(select(ServerRuntime).where(ServerRuntime.server_id == server_id))}
    seen = set()
    for rt, versions in reported.items():
        if rt not in SUPPORTED_VERSIONS:
            continue
        for v in versions:
            if not re.fullmatch(r"\d{1,2}(\.\d{1,2}){0,2}", str(v)):
                continue
            seen.add((rt, v))
            row = existing.get((rt, v))
            if row:
                row.installed, row.reported_at = True, stamp
            else:
                db.add(ServerRuntime(server_id=server_id, runtime=rt, version=v, installed=True, reported_at=stamp))
    for key, row in existing.items():
        if key not in seen:
            row.installed, row.reported_at = False, stamp
    return True


def validate_runtime(db: Session, server_id: uuid.UUID | None, runtime: str, version: str) -> str | None:
    kind = CATALOG_KIND[runtime]
    if version not in SUPPORTED_VERSIONS[kind]:
        raise api_error(422, "runtime_unsupported", f"{kind} {version} is not a supported version.")
    cat = catalog(db, server_id)
    installed = cat[kind]
    if any(cat.values()):  # the server has reported its inventory: be strict
        if version not in installed:
            have = ", ".join(installed) or "none"
            raise api_error(422, "runtime_unavailable", f"{kind} {version} is not installed on the server. Installed: {have}.")
        return None
    return "Installed runtimes have not been reported by the server yet; the version will be verified at deployment."


def validate_entrypoint(runtime: str, entrypoint: str | None) -> str | None:
    if runtime == "nodejs":
        ep = entrypoint or "server.js"
        if not ENTRY_NODE_RE.fullmatch(ep):
            raise api_error(422, "invalid_entrypoint", "Node entrypoint must be a relative .js/.mjs/.cjs file path.")
        return ep
    if runtime == "python":
        if not entrypoint or not ENTRY_PY_RE.fullmatch(entrypoint):
            raise api_error(422, "invalid_entrypoint", "Python entrypoint must be module:callable, e.g. app:app.")
        return entrypoint
    return None


def allocate_ports(db: Session, server_id: uuid.UUID | None) -> tuple[int, int]:
    used = set(db.scalars(select(Application.port).where(Application.port.is_not(None), Application.server_id == server_id)))
    used |= set(db.scalars(select(Application.canary_port).where(Application.canary_port.is_not(None), Application.server_id == server_id)))
    for p in range(20000, 29999, 2):
        if p not in used and p + 1 not in used:
            return p, p + 1
    raise api_error(409, "no_ports", "No free application ports on this server.")


def assign_server(db: Session, app: Application) -> Server | None:
    if app.server_id:
        s = db.get(Server, app.server_id)
        if s and s.status != "disabled":
            return s
    s = default_server(db)
    if s:
        app.server_id = s.id
    return s


def env_dict(db: Session, app: Application) -> dict[str, str]:
    out = {}
    for v in db.scalars(select(AppEnvVar).where(AppEnvVar.app_id == app.id)):
        val = decrypt_secret(v.value_encrypted)
        if val is not None:
            out[v.name] = val
    return out


def new_release_id() -> str:
    return f"{datetime.now(UTC):%Y%m%d%H%M%S}-{secrets.token_hex(3)}"


# ---------------------------------------------------------------- enqueue helpers
def start_deploy(db: Session, app: Application, actor: uuid.UUID | None, branch: str | None = None) -> AppDeployment:
    if app.provision_state == "archived":
        raise api_error(409, "app_archived", "Application is archived.")
    if not app.repo_url:
        raise api_error(409, "repo_required", "Configure a Git repository before deploying.")
    if db.scalar(select(AppDeployment.id).where(AppDeployment.app_id == app.id, AppDeployment.status == "queued")):
        raise api_error(409, "deploy_in_progress", "A deployment is already running.")
    server = assign_server(db, app)
    if server is None:
        app.provision_state = "provisioning_blocked"
        app.provision_message = "No server is registered yet. Deploy again once a server exists."
        log_event(db, app.tenant_id, "app", app.id, actor, "app.deploy_blocked", "Deployment blocked: no server registered")
        raise api_error(409, "no_server", app.provision_message)
    rel = new_release_id()
    br = branch or app.branch
    params = {"app": agent_name(app), "runtime": app.runtime, "version": app.runtime_version, "repo_url": app.repo_url, "branch": br, "release_id": rel,
              "health_path": app.health_path, "env": env_dict(db, app)}
    if app.runtime in PROCESS_RUNTIMES:
        params.update(port=app.port, canary_port=app.canary_port, entrypoint=app.entrypoint)
    job = enqueue_job(db, server.id, "apps.deploy", params, tenant_id=app.tenant_id, requested_by=actor)
    dep = AppDeployment(app_id=app.id, tenant_id=app.tenant_id, release_id=rel, kind="deploy", branch=br, status="queued", job_id=job.id, requested_by=actor)
    db.add(dep)
    app.provision_state, app.provision_message, app.last_job_id = "deploying", "Deployment queued.", job.id
    log_event(db, app.tenant_id, "app", app.id, actor, "app.deploy", f"Deployment {rel} requested from branch {br}")
    return dep


def start_rollback(db: Session, app: Application, actor: uuid.UUID | None, to_release: str | None) -> AppDeployment:
    target = to_release or app.previous_release
    if not target or target == app.current_release:
        raise api_error(409, "no_rollback_target", "There is no previous known-good release to roll back to.")
    good = db.scalar(select(AppDeployment).where(AppDeployment.app_id == app.id, AppDeployment.release_id == target, AppDeployment.status == "succeeded"))
    if good is None:
        raise api_error(409, "release_not_known_good", "That release has no successful deployment on record.")
    if not app.server_id:
        raise api_error(409, "no_server", "Application has no server.")
    params = {"app": agent_name(app), "runtime": app.runtime, "to_release": target, "health_path": app.health_path}
    if app.runtime in PROCESS_RUNTIMES:
        params["port"] = app.port
    job = enqueue_job(db, app.server_id, "apps.rollback", params, tenant_id=app.tenant_id, requested_by=actor)
    dep = AppDeployment(app_id=app.id, tenant_id=app.tenant_id, release_id=target, kind="rollback", status="queued", job_id=job.id, requested_by=actor)
    db.add(dep)
    app.provision_state, app.provision_message, app.last_job_id = "deploying", f"Rolling back to {target}.", job.id
    log_event(db, app.tenant_id, "app", app.id, actor, "app.rollback", f"Rollback to {target} requested")
    return dep


def start_process(db: Session, app: Application, actor: uuid.UUID | None, action: str) -> AgentJob:
    if app.runtime not in PROCESS_RUNTIMES:
        raise api_error(409, "not_a_process_app", "PHP and WordPress applications are served by PHP-FPM and have no process to control.")
    if not app.server_id or not app.current_release:
        raise api_error(409, "not_deployed", "Deploy the application first.")
    job = enqueue_job(db, app.server_id, "apps.process", {"app": agent_name(app), "runtime": app.runtime, "action": action}, tenant_id=app.tenant_id, requested_by=actor)
    app.last_job_id = job.id
    log_event(db, app.tenant_id, "app", app.id, actor, f"app.{action}", f"Process {action} requested")
    return job


# ---------------------------------------------------------------- result sync
def sync_app(db: Session, app: Application) -> Application:
    changed = False
    for dep in db.scalars(select(AppDeployment).where(AppDeployment.app_id == app.id, AppDeployment.status == "queued")):
        job = db.get(AgentJob, dep.job_id) if dep.job_id else None
        ok = job_ok(job)
        if ok is None:
            continue
        res = (job.result or {}) if job else {}
        dep.finished_at = job.finished_at or datetime.now(UTC)
        changed = True
        if ok:
            dep.status, dep.is_live, dep.commit = "succeeded", True, (res.get("commit") or "")[:64] or None
            db.execute(AppDeployment.__table__.update().where(AppDeployment.app_id == app.id, AppDeployment.id != dep.id).values(is_live=False))
            if app.current_release != dep.release_id:
                app.previous_release = res.get("previous_release") or app.current_release
                app.current_release = dep.release_id
            app.provision_state, app.provision_message = "deployed", f"Release {dep.release_id} is live."
            if app.runtime in PROCESS_RUNTIMES:
                app.status = "running"
        else:
            status = res.get("status")
            dep.status = "health_failed" if status == "health_failed" else "failed"
            dep.error = redact_text(str(res.get("message") or (job.error if job else None) or "Deployment failed."))[:1000]
            dep.is_live = False
            keep = f" Release {app.current_release} is still live." if app.current_release else ""
            app.provision_state = "not_configured" if status == "not_configured" else ("deployed" if app.current_release else "failed")
            app.provision_message = (dep.error[:300] + keep)[:500]
        log_event(db, app.tenant_id, "app", app.id, None, f"app.deploy_{dep.status}", f"Release {dep.release_id}: {dep.status}")
        scrub_job_params(db, job)
    if app.last_job_id:
        job = db.get(AgentJob, app.last_job_id)
        if job and job.operation in {"apps.process", "apps.usage"} and job_ok(job) is not None:
            res = job.result or {}
            if job.operation == "apps.process" and job_ok(job):
                st = res.get("state")
                if st in {"running", "stopped", "errored"}:
                    app.status = st
                    changed = True
            elif job.operation == "apps.usage" and job_ok(job):
                app.usage = {k: res.get(k) for k in ("disk_mb", "memory_mb", "cpu_percent", "restarts", "uptime_s", "releases")}
                changed = True
    if changed:
        db.flush()
    return app
