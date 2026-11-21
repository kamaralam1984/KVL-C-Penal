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
from kvl_agent.site_options import SiteOptions

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
    options: SiteOptions | None = None

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
def htpasswd_files(p: StageSite) -> dict[str, tuple[str, str]]:
    """protected path -> (file name, content). File names are content-addressed so staging never alters a live file."""
    out: dict[str, tuple[str, str]] = {}
    for pp in (p.options.protected if p.options else []):
        content = "".join(f"{u.username}:{u.hash}\n" for u in pp.users)
        out[pp.path] = (f"{p.site}-{hashlib.sha256(content.encode()).hexdigest()[:16]}.htpasswd", content)
    return out


HOTLINK_EXT = "jpe?g|png|gif|webp|avif|svg|ico|bmp|mp4|webm|mov|mp3|ogg|wav|pdf"


def _handler(p: StageSite, indent: str, php_value: str | None) -> str:
    """Serving directives for a location (shared by the root and by protected-directory locations)."""
    i = indent
    if p.runtime == "static":
        return f"{i}try_files $uri $uri/ =404;\n"
    if p.runtime == "php":
        pv = f'{i}    fastcgi_param PHP_VALUE "{php_value}";\n' if php_value else ""
        return (
            f"{i}try_files $uri $uri/ /index.php?$query_string;\n"
            f"{i}location ~ \\.php$ {{\n"
            f"{i}    try_files $uri =404;\n"
            f"{i}    include fastcgi_params;\n"
            f"{i}    fastcgi_param SCRIPT_FILENAME $document_root$fastcgi_script_name;\n"
            f"{pv}"
            f"{i}    fastcgi_pass unix:/run/php/php{p.php_version}-fpm.sock;\n"
            f"{i}}}\n"
        )
    return (
        f"{i}proxy_pass http://127.0.0.1:{int(p.proxy_port)};\n"
        f"{i}proxy_set_header Host $host;\n{i}proxy_set_header X-Real-IP $remote_addr;\n"
        f"{i}proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;\n{i}proxy_set_header X-Forwarded-Proto $scheme;\n"
    )


def render_site(p: StageSite, log_dir: str, auth_dir: str | None = None) -> str:
    """Render from the fixed template. Raises ValueError if any value is unsafe (defense in depth)."""
    if p.runtime == "php" and not p.php_version:
        raise ValueError("php_version required for php runtime")
    if p.runtime == "proxy" and not p.proxy_port:
        raise ValueError("proxy_port required for proxy runtime")
    o = p.options or SiteOptions()
    if o.php and p.runtime != "php":
        raise ValueError("php settings require the php runtime")
    if o.protected and not auth_dir:
        raise ValueError("auth_dir required for protected paths")
    for v in [" ".join(p.server_names), p.document_root, p.site, log_dir, *([auth_dir] if auth_dir else [])]:
        if not SAFE_VALUE.match(v) or any(c in v for c in "\n\r;{}$\"'`\\"):
            raise ValueError("unsafe value rejected by template")
    opt_values = [r.source for r in o.redirects] + [r.target for r in o.redirects] + [x.path for x in o.protected] + o.deny_ips + o.hotlink.allowed_hosts
    for v in opt_values:  # second, independent check of option values after model validation
        if any(c in v for c in "\n\r\t ;{}$\"'`\\#"):
            raise ValueError("unsafe option value rejected by template")
    php_value = o.php.php_value() if o.php else None
    body_mb = o.php.body_limit_mb() if o.php else 64
    head = (
        f"# Managed by KVL agent for site {p.site}. Manual edits are overwritten.\n"
        "server {\n"
        "    listen 80;\n    listen [::]:80;\n"
        f"    server_name {' '.join(p.server_names)};\n"
        f"    root {p.document_root};\n"
        f"    access_log {log_dir}/{p.site}.access.log;\n"
        f"    error_log {log_dir}/{p.site}.error.log;\n"
        "    server_tokens off;\n"
        f"    client_max_body_size {int(body_mb)}m;\n"
        "    location ~ /\\.(?!well-known) { deny all; }\n"
    )
    prelude = ""
    if o.deny_ips:
        # No realip module is assumed: pick the client address with map (CF-Connecting-IP only when the request
        # came from the local tunnel on loopback), then match it against the deny list with geo.
        v = p.site.replace("-", "_")
        prelude = (
            f"map $remote_addr $kvl_cip_{v} {{ 127.0.0.1 $http_cf_connecting_ip; ::1 $http_cf_connecting_ip; default $remote_addr; }}\n"
            f"geo $kvl_cip_{v} $kvl_deny_{v} {{\n    default 0;\n" + "".join(f"    {ip} 1;\n" for ip in o.deny_ips) + "}\n"
        )
        head += f"    if ($kvl_deny_{v}) {{ return 403; }}\n"
    extra = ""
    for r in o.redirects:
        op = "=" if r.match == "exact" else "^~"
        extra += f'    location {op} {r.source} {{ return {int(r.code)} "{r.target}"; }}\n'
    files = htpasswd_files(p)
    for pp in o.protected:
        extra += (
            f"    location ^~ {pp.path} {{\n"
            '        auth_basic "Restricted";\n'
            f"        auth_basic_user_file {auth_dir}/{files[pp.path][0]};\n"
            + _handler(p, "        ", php_value)
            + "    }\n"
        )
    if o.hotlink.enabled:
        refs = " ".join((["none", "blocked"] if o.hotlink.allow_empty_referer else []) + ["server_names"] + o.hotlink.allowed_hosts)
        serve = f"        proxy_pass http://127.0.0.1:{int(p.proxy_port)};\n        proxy_set_header Host $host;\n" if p.runtime == "proxy" else "        try_files $uri =404;\n"
        extra += (
            f"    location ~* \\.(?:{HOTLINK_EXT})$ {{\n"
            f"        valid_referers {refs};\n"
            "        if ($invalid_referer) { return 403; }\n"
            + serve
            + "    }\n"
        )
    if p.runtime == "static":
        body = "    index index.html index.htm;\n    location / {\n" + _handler(p, "        ", None) + "    }\n"
    elif p.runtime == "php":
        body = "    index index.php index.html;\n    location / {\n" + _handler(p, "        ", php_value) + "    }\n"
    else:
        body = "    location / {\n" + _handler(p, "        ", None) + "    }\n"
    return prelude + head + extra + body + "}\n"


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
    auth_dir = ctx.path("nginx", "auth")
    try:
        text = render_site(p, str(log_dir), str(auth_dir))
    except ValueError as e:
        return {"ok": False, "status": "rejected", "message": str(e)}
    digest = _sha(text)
    if ctx.dry_run:
        return {"ok": True, "status": "dry_run", "config_hash": digest, "would_write": f"nginx/proposed/{p.site}.conf"}
    if not ctx.which("nginx"):
        return {**_not_configured(), "config_hash": digest}
    log_dir.mkdir(parents=True, exist_ok=True)
    for name, content in htpasswd_files(p).values():
        f = auth_dir / name
        if not f.exists():  # content-addressed: never rewrite a file a live config may reference
            _atomic_write(f, content)
            os.chmod(f, 0o640)
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


# --------------------------------------------------------------------------- site logs + visitor stats (read-only)
import re as _re  # noqa: E402
from collections import Counter  # noqa: E402

from kvl_agent.redact import redact_text  # noqa: E402

LOG_TAIL_BYTES = 4 * 1024 * 1024
FILTER_RE = r"^[A-Za-z0-9 ._:/\-?=&%@+,()\[\]]{1,100}$"
_COMBINED = _re.compile(r'^(\S+) \S+ \S+ \[([^\]]+)\] "([A-Z]{3,10}) (\S+)[^"]*" (\d{3}) (\d+|-) "([^"]*)" "([^"]*)"')
_BOTS = _re.compile(r"(?i)bot|crawl|spider|slurp|curl|wget|python-requests|httpclient|monitor|uptime|headless")


class SiteLogs(SiteRef):
    kind: Literal["access", "error"] = "access"
    page: int = Field(default=1, ge=1, le=1000)
    page_size: int = Field(default=200, ge=1, le=500)
    contains: str | None = Field(default=None, pattern=FILTER_RE)


class SiteStats(SiteRef):
    pass


def _tail(path) -> list[str]:
    if not path.exists():
        return []
    with path.open("rb") as fh:
        fh.seek(0, os.SEEK_END)
        size = fh.tell()
        fh.seek(max(0, size - LOG_TAIL_BYTES))
        raw = fh.read().decode(errors="replace")
    lines = raw.splitlines()
    return lines[1:] if size > LOG_TAIL_BYTES and lines else lines  # drop a partial first line


@operation("nginx.site_logs", SiteLogs, timeout=30)
def site_logs(p: SiteLogs, ctx: OpContext) -> dict:
    """Newest-first pages of the site's own access/error log (last 4 MB). Redacted; never another site's file."""
    _, _, _, log_dir = _dirs(ctx, p.site)
    lines = _tail(log_dir / f"{p.site}.{p.kind}.log")
    if p.contains:
        needle = p.contains.lower()
        lines = [x for x in lines if needle in x.lower()]
    lines.reverse()
    total = len(lines)
    start = (p.page - 1) * p.page_size
    chunk = lines[start:start + p.page_size]
    return {"ok": True, "kind": p.kind, "lines": [redact_text(x[:2000]) for x in chunk], "page": p.page, "page_size": p.page_size,
            "has_more": start + p.page_size < total, "total_lines": total}


@operation("nginx.site_stats", SiteStats, timeout=60)
def site_stats(p: SiteStats, ctx: OpContext) -> dict:
    """Aggregate visitor statistics from the access log tail. Aggregates only: no raw IPs or user agents leave the server."""
    _, _, _, log_dir = _dirs(ctx, p.site)
    heat = [[0] * 24 for _ in range(7)]  # weekday (Mon=0) x hour
    days: Counter = Counter()
    status: Counter = Counter()
    paths: Counter = Counter()
    refs: Counter = Counter()
    visitors: set = set()
    total = bots = bytes_out = parsed = 0
    first = last = None
    for line in _tail(log_dir / f"{p.site}.access.log"):
        m = _COMBINED.match(line)
        if not m:
            continue
        parsed += 1
        ip, ts, _method, path, code, size, ref, ua = m.groups()
        try:
            t = datetime.strptime(ts, "%d/%b/%Y:%H:%M:%S %z")
        except ValueError:
            continue
        total += 1
        first = first or t
        last = t
        if _BOTS.search(ua):
            bots += 1
            continue
        heat[t.weekday()][t.hour] += 1
        days[t.strftime("%Y-%m-%d")] += 1
        status[code[0] + "xx"] += 1
        paths[path.split("?")[0][:200]] += 1
        if ref and ref != "-":
            host = _re.sub(r"^https?://([^/]+).*$", r"\1", ref)[:120]
            refs[host] += 1
        if ip not in ("127.0.0.1", "::1"):
            visitors.add(hashlib.sha256((ip + ua).encode()).hexdigest()[:16])
        if size.isdigit():
            bytes_out += int(size)
    return {
        "ok": True, "requests": total, "bot_requests": bots, "human_requests": total - bots, "bytes_out": bytes_out,
        "unique_visitors": len(visitors) or None,  # None when every request came through the local tunnel (no client IP logged)
        "first_seen": first.isoformat() if first else None, "last_seen": last.isoformat() if last else None,
        "heatmap": heat, "daily": sorted(days.items())[-31:], "status": dict(status),
        "top_paths": paths.most_common(15), "top_referrers": refs.most_common(10), "window_bytes": LOG_TAIL_BYTES,
    }
