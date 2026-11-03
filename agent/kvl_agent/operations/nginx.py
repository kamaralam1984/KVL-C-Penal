"""Nginx site provisioning from validated templates only.

Layout (all inside the agent sandbox `data_dir`):
  nginx/proposed/<site>.conf   rendered, validated candidate
  nginx/sites/<site>.conf      live managed config (the system nginx.conf must `include <data_dir>/nginx/sites/*.conf;`)
  nginx/backups/<site>/<ts>.conf
Customer text never reaches a template: every value is regex-validated here, independently of the API.
Results are summaries only; `ok` is False and `status` explains whenever something did not happen.
"""
import hashlib
import os
import re
import shutil
from datetime import UTC, datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from kvl_agent.context import OpContext, allow_binary
from kvl_agent.registry import operation

allow_binary("nginx")

DOC_ROOT_PREFIX = "/var/www/kvl/"
SITE_RE = re.compile(r"^[a-z0-9][a-z0-9-]{2,62}$")
HOST_RE = re.compile(r"^(?=.{1,253}$)(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$")
ROOT_RE = re.compile(r"^/var/www/kvl/[a-z0-9][a-z0-9_-]{0,62}/[a-z0-9][a-z0-9_-]{0,62}/[a-z0-9_-]{1,32}$")
BACKUP_RE = re.compile(r"^\d{8}T\d{6}Z(-\d{2})?\.conf$")
SAFE_VALUE = re.compile(r"^[A-Za-z0-9._/\-\s:\[\]]+$")  # last-line check on the final rendered values


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class SiteRef(_Strict):
    site: str

    @field_validator("site")
    @classmethod
    def _site(cls, v: str) -> str:
        if not SITE_RE.fullmatch(v):
            raise ValueError("invalid site name")
        return v


class StageSite(SiteRef):
    server_names: list[str] = Field(min_length=1, max_length=20)
    document_root: str
    runtime: Literal["static", "php", "proxy"] = "static"
    php_version: str | None = Field(default=None, pattern=r"^\d\.\d{1,2}$")
    proxy_port: int | None = Field(default=None, ge=1024, le=65535)

    @field_validator("server_names")
    @classmethod
    def _names(cls, v: list[str]) -> list[str]:
        for h in v:
            if not HOST_RE.fullmatch(h):
                raise ValueError(f"invalid hostname: {h[:60]!r}")
        if len(set(v)) != len(v):
            raise ValueError("duplicate hostnames")
        return v

    @field_validator("document_root")
    @classmethod
    def _root(cls, v: str) -> str:
        if not ROOT_RE.fullmatch(v) or ".." in v:
            raise ValueError("document_root must be under /var/www/kvl/<tenant>/<site>/<dir>")
        return v


class ActivateSite(SiteRef):
    expected_hash: str = Field(pattern=r"^[a-f0-9]{64}$")


class Reload(_Strict):
    pass


class RollbackSite(SiteRef):
    backup: str | None = Field(default=None, pattern=BACKUP_RE.pattern)


class RemoveSite(SiteRef):
    pass


# --------------------------------------------------------------------------- rendering
def render_site(p: StageSite, log_dir: str) -> str:
    """Render from the fixed template. Raises ValueError if any value is unsafe (defense in depth)."""
    if p.runtime == "php" and not p.php_version:
        raise ValueError("php_version required for php runtime")
    if p.runtime == "proxy" and not p.proxy_port:
        raise ValueError("proxy_port required for proxy runtime")
    values = [" ".join(p.server_names), p.document_root, p.site, log_dir]
    for v in values:
        if not SAFE_VALUE.match(v) or any(c in v for c in "\n\r;{}$\"'`\\"):
            raise ValueError("unsafe value rejected by template")
    head = (
        f"# Managed by KVL agent for site {p.site}. Manual edits are overwritten.\n"
        "server {\n"
        "    listen 80;\n    listen [::]:80;\n"
        f"    server_name {' '.join(p.server_names)};\n"
        f"    root {p.document_root};\n"
        f"    access_log {log_dir}/{p.site}.access.log;\n"
        f"    error_log {log_dir}/{p.site}.error.log;\n"
        "    server_tokens off;\n"
        "    client_max_body_size 64m;\n"
        "    location ~ /\\.(?!well-known) { deny all; }\n"
    )
    if p.runtime == "static":
        body = "    index index.html index.htm;\n    location / { try_files $uri $uri/ =404; }\n"
    elif p.runtime == "php":
        body = (
            "    index index.php index.html;\n"
            "    location / { try_files $uri $uri/ /index.php?$query_string; }\n"
            "    location ~ \\.php$ {\n"
            "        include fastcgi_params;\n"
            "        fastcgi_param SCRIPT_FILENAME $document_root$fastcgi_script_name;\n"
            f"        fastcgi_pass unix:/run/php/php{p.php_version}-fpm.sock;\n"
            "    }\n"
        )
    else:
        body = (
            "    location / {\n"
            f"        proxy_pass http://127.0.0.1:{int(p.proxy_port)};\n"
            "        proxy_set_header Host $host;\n        proxy_set_header X-Real-IP $remote_addr;\n"
            "        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;\n        proxy_set_header X-Forwarded-Proto $scheme;\n"
            "    }\n"
        )
    return head + body + "}\n"


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


def _dirs(ctx: OpContext, site: str):
    return ctx.path("nginx", "proposed"), ctx.path("nginx", "sites"), ctx.path("nginx", "backups", site), ctx.path("nginx", "logs")


def _atomic_write(path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(text)
    os.replace(tmp, path)


def _backup(ctx: OpContext, site: str) -> str | None:
    _, sites, bdir, _ = _dirs(ctx, site)
    live = sites / f"{site}.conf"
    if not live.exists():
        return None
    bdir.mkdir(parents=True, exist_ok=True)
    base = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    name, i = f"{base}.conf", 0
    while (bdir / name).exists():
        i += 1
        name = f"{base}-{i:02d}.conf"
    shutil.copy2(live, bdir / name)
    return name


def _nginx_test(ctx: OpContext, extra: list[str] | None = None):
    r = ctx.run(["nginx", "-t", *(extra or [])], timeout=30)
    return r, (r.stderr or r.stdout)[-800:]


def _not_configured() -> dict:
    return {"ok": False, "status": "not_configured", "message": "Nginx is not installed on this server; nothing was changed or applied."}


def _restore(live, previous: str | None) -> None:
    if previous is None:
        live.unlink(missing_ok=True)
    else:
        _atomic_write(live, previous)


# --------------------------------------------------------------------------- operations
@operation("nginx.stage_site", StageSite, timeout=60)
def stage_site(p: StageSite, ctx: OpContext) -> dict:
    proposed_dir, _, _, log_dir = _dirs(ctx, p.site)
    try:
        text = render_site(p, str(log_dir))
    except ValueError as e:
        return {"ok": False, "status": "rejected", "message": str(e)}
    digest = _sha(text)
    if ctx.dry_run:
        return {"ok": True, "status": "dry_run", "config_hash": digest, "would_write": f"nginx/proposed/{p.site}.conf"}
    if not ctx.which("nginx"):
        return {**_not_configured(), "config_hash": digest}
    log_dir.mkdir(parents=True, exist_ok=True)
    proposed = proposed_dir / f"{p.site}.conf"
    _atomic_write(proposed, text)
    # Validate the candidate in an isolated wrapper config so the live configuration is never touched.
    run_dir = ctx.path("nginx", "check", p.site)
    for sub in ("tmp", "run"):
        (run_dir / sub).mkdir(parents=True, exist_ok=True)
    wrapper = run_dir / "nginx.conf"
    wrapper.write_text(
        f"pid {run_dir}/run/nginx.pid;\nerror_log {log_dir}/check.error.log;\nevents {{}}\n"
        f"http {{\n  client_body_temp_path {run_dir}/tmp/body;\n  proxy_temp_path {run_dir}/tmp/proxy;\n  fastcgi_temp_path {run_dir}/tmp/fcgi;\n"
        f"  uwsgi_temp_path {run_dir}/tmp/uwsgi;\n  scgi_temp_path {run_dir}/tmp/scgi;\n  include {proposed};\n}}\n"
    )
    r, out = _nginx_test(ctx, ["-c", str(wrapper), "-p", str(run_dir)])
    if not r.ok:
        proposed.unlink(missing_ok=True)
        return {"ok": False, "status": "validation_failed", "message": f"nginx -t rejected the proposed configuration: {out}", "config_hash": digest}
    return {"ok": True, "status": "validated", "config_hash": digest, "message": "Proposed configuration passed nginx -t; live configuration untouched."}


@operation("nginx.activate_site", ActivateSite, timeout=90)
def activate_site(p: ActivateSite, ctx: OpContext) -> dict:
    proposed_dir, sites, _, _ = _dirs(ctx, p.site)
    proposed = proposed_dir / f"{p.site}.conf"
    if not proposed.exists():
        return {"ok": False, "status": "not_staged", "message": "No validated proposed configuration exists; stage first."}
    text = proposed.read_text()
    if _sha(text) != p.expected_hash:
        return {"ok": False, "status": "hash_mismatch", "message": "Proposed configuration changed since validation; stage again."}
    if ctx.dry_run:
        return {"ok": True, "status": "dry_run", "would_install": f"nginx/sites/{p.site}.conf"}
    if not ctx.which("nginx"):
        return _not_configured()
    live = sites / f"{p.site}.conf"
    previous = live.read_text() if live.exists() else None
    backup = _backup(ctx, p.site)
    _atomic_write(live, text)
    r, out = _nginx_test(ctx)
    if not r.ok:
        _restore(live, previous)
        return {"ok": False, "status": "validation_failed", "message": f"nginx -t failed; previous configuration restored (not reloaded): {out}", "backup": backup}
    return {"ok": True, "status": "installed", "backup": backup, "message": "Installed; not yet reloaded."}


@operation("nginx.reload", Reload, timeout=60)
def reload_nginx(_p: Reload, ctx: OpContext) -> dict:
    if ctx.dry_run:
        return {"ok": True, "status": "dry_run"}
    if not ctx.which("nginx"):
        return _not_configured()
    r, out = _nginx_test(ctx)
    if not r.ok:
        return {"ok": False, "status": "validation_failed", "message": f"Reload refused: nginx -t failed: {out}"}
    r2 = ctx.run(["nginx", "-s", "reload"], timeout=30)
    if not r2.ok:
        return {"ok": False, "status": "reload_failed", "message": (r2.stderr or r2.stdout)[-500:]}
    return {"ok": True, "status": "reloaded"}


@operation("nginx.rollback_site", RollbackSite, timeout=90)
def rollback_site(p: RollbackSite, ctx: OpContext) -> dict:
    _, sites, bdir, _ = _dirs(ctx, p.site)
    backups = sorted(x.name for x in bdir.glob("*.conf")) if bdir.exists() else []
    if not backups:
        return {"ok": False, "status": "no_backup", "message": "No backup exists for this site."}
    chosen = p.backup or backups[-1]
    if chosen not in backups:
        return {"ok": False, "status": "no_backup", "message": "Requested backup not found."}
    if ctx.dry_run:
        return {"ok": True, "status": "dry_run", "would_restore": chosen}
    if not ctx.which("nginx"):
        return _not_configured()
    live = sites / f"{p.site}.conf"
    previous = live.read_text() if live.exists() else None
    pre = _backup(ctx, p.site)  # keep the config we are replacing so rollback is itself reversible
    _atomic_write(live, (bdir / chosen).read_text())
    r, out = _nginx_test(ctx)
    if not r.ok:
        _restore(live, previous)
        return {"ok": False, "status": "validation_failed", "message": f"Backup failed nginx -t; kept current configuration: {out}"}
    return {"ok": True, "status": "rolled_back", "restored": chosen, "saved_current_as": pre, "message": "Restored; not yet reloaded."}


@operation("nginx.remove_site", RemoveSite, timeout=90, destructive=True)
def remove_site(p: RemoveSite, ctx: OpContext) -> dict:
    _, sites, _, _ = _dirs(ctx, p.site)
    live = sites / f"{p.site}.conf"
    if not live.exists():
        return {"ok": True, "status": "absent", "message": "No live configuration to remove."}
    if ctx.dry_run:
        return {"ok": True, "status": "dry_run"}
    if not ctx.which("nginx"):
        return _not_configured()
    previous = live.read_text()
    backup = _backup(ctx, p.site)
    live.unlink()
    r, out = _nginx_test(ctx)
    if not r.ok:
        _restore(live, previous)
        return {"ok": False, "status": "validation_failed", "message": f"nginx -t failed after removal; restored: {out}"}
    return {"ok": True, "status": "removed", "backup": backup, "message": "Removed; reload Nginx to apply."}
