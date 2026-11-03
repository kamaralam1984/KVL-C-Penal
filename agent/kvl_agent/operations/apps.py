"""Application deployment and process control (PHP-FPM, WordPress template, Node.js via PM2, Python venv + PM2).

All paths live under the sandbox `data_dir/apps/<app>/`:
  releases/<release_id>/   immutable release (git checkout without .git, deps installed)
  current -> releases/<id>  atomic symlink switch (os.replace of a temp symlink)
  shared/                  .env (0600), ecosystem files (0600), wp-config.php, state.json
  logs/out.log, err.log
Nothing customer-supplied is executed through a shell: git/npm/pip/pm2 run with fixed argv. Install scripts are disabled
(npm --ignore-scripts); customer code only runs as the app process under the agent's unprivileged user, never as root.
Results never include env values; error text is redacted.
"""
import json
import os
import re
import secrets
import shutil
import time
import urllib.request
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from kvl_agent.context import OpContext, allow_binary
from kvl_agent.redact import redact_text
from kvl_agent.registry import operation

PY_BINS = [f"python3.{m}" for m in range(9, 15)]
allow_binary("git", "npm", "node", "pm2", "composer", "php", *PY_BINS)

APP_RE = re.compile(r"^[a-z0-9][a-z0-9-]{2,62}$")
RELEASE_RE = re.compile(r"^\d{14}-[a-f0-9]{6}$")
VERSION_RE = re.compile(r"^\d{1,2}(\.\d{1,2}){0,2}$")
BRANCH_RE = re.compile(r"^(?!.*\.\.)(?!.*//)(?!/)(?!.*/$)(?!.*\.lock$)(?!.*@\{)[A-Za-z0-9][A-Za-z0-9._/-]{0,98}$")
HEALTH_RE = re.compile(r"^/[A-Za-z0-9/_.\-]{0,150}(\?[A-Za-z0-9_=&.\-]{0,40})?$")
ENTRY_NODE_RE = re.compile(r"^(?!/)(?!.*\.\.)[A-Za-z0-9_][A-Za-z0-9_./-]{0,98}\.(?:js|mjs|cjs)$")
ENTRY_PY_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_.]{0,60}:[A-Za-z_][A-Za-z0-9_]{0,40}$")
ENV_NAME_RE = re.compile(r"^[A-Z][A-Z0-9_]{0,62}$")
GIT_HOSTS = ("github.com", "gitlab.com", "bitbucket.org", "codeberg.org")
_HTTPS = re.compile(r"^https://(?P<host>[a-z0-9.-]+)/(?P<path>[A-Za-z0-9._-]+(?:/[A-Za-z0-9._-]+){1,3}?)(?:\.git)?/?$")
_SSH = re.compile(r"^(?:ssh://git@(?P<h1>[a-z0-9.-]+)/|git@(?P<h2>[a-z0-9.-]+):)(?P<path>[A-Za-z0-9._-]+(?:/[A-Za-z0-9._-]+){1,3}?)(?:\.git)?$")
SUPPORTED = {"php": ["7.4", "8.0", "8.1", "8.2", "8.3", "8.4"], "nodejs": ["18", "20", "22", "24"], "python": ["3.9", "3.10", "3.11", "3.12", "3.13", "3.14"]}
WP_SAFE = re.compile(r"^[A-Za-z0-9_.\-]{1,64}$")


def check_repo_url(url: str) -> str:
    if not isinstance(url, str) or len(url) > 300 or url != url.strip() or url.startswith("-"):
        raise ValueError("invalid repository URL")
    m = _HTTPS.fullmatch(url) or _SSH.fullmatch(url)
    if not m:
        raise ValueError("Repository URL must be https://<host>/<owner>/<repo>[.git] or git@<host>:<owner>/<repo>.git")
    g = m.groupdict()
    host = g.get("host") or g.get("h1") or g.get("h2")
    if host not in GIT_HOSTS or ".." in g["path"]:
        raise ValueError("Git host is not on the allow-list")
    return url


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class AppRef(_Strict):
    app: str

    @field_validator("app")
    @classmethod
    def _app(cls, v: str) -> str:
        if not APP_RE.fullmatch(v):
            raise ValueError("invalid app name")
        return v


class DetectRuntimes(_Strict):
    pass


class Deploy(AppRef):
    runtime: Literal["php", "wordpress", "nodejs", "python"]
    version: str = Field(pattern=VERSION_RE.pattern)
    repo_url: str
    branch: str
    release_id: str = Field(pattern=RELEASE_RE.pattern)
    port: int | None = Field(default=None, ge=20000, le=29999)
    canary_port: int | None = Field(default=None, ge=20000, le=29999)
    health_path: str = "/"
    entrypoint: str | None = None
    env: dict[str, str] = Field(default_factory=dict, max_length=100)
    keep_releases: int = Field(default=5, ge=2, le=10)

    @field_validator("repo_url")
    @classmethod
    def _repo(cls, v: str) -> str:
        return check_repo_url(v)

    @field_validator("branch")
    @classmethod
    def _branch(cls, v: str) -> str:
        if not BRANCH_RE.fullmatch(v):
            raise ValueError("invalid branch name")
        return v

    @field_validator("health_path")
    @classmethod
    def _health(cls, v: str) -> str:
        if not HEALTH_RE.fullmatch(v):
            raise ValueError("invalid health check path")
        return v

    @field_validator("env")
    @classmethod
    def _env(cls, v: dict[str, str]) -> dict[str, str]:
        for k, val in v.items():
            if not ENV_NAME_RE.fullmatch(k):
                raise ValueError(f"invalid environment variable name {k[:30]!r}")
            if len(val) > 4096 or any(c in val for c in "\x00\r\n"):
                raise ValueError(f"invalid value for {k}")
        return v


class Rollback(AppRef):
    to_release: str | None = Field(default=None, pattern=RELEASE_RE.pattern)
    runtime: Literal["php", "wordpress", "nodejs", "python"]
    port: int | None = Field(default=None, ge=20000, le=29999)
    health_path: str = "/"

    @field_validator("health_path")
    @classmethod
    def _health(cls, v: str) -> str:
        if not HEALTH_RE.fullmatch(v):
            raise ValueError("invalid health check path")
        return v


class Process(AppRef):
    action: Literal["start", "stop", "restart", "status"]
    runtime: Literal["nodejs", "python"]


class Logs(AppRef):
    stream: Literal["out", "err"] = "out"
    page: int = Field(default=1, ge=1, le=10_000)
    page_size: int = Field(default=100, ge=1, le=500)


class Usage(AppRef):
    pass


class Remove(AppRef):
    pass


# --------------------------------------------------------------------------- helpers
def _dirs(ctx: OpContext, app: str) -> dict[str, Path]:
    root = ctx.path("apps", app)
    return {"root": root, "releases": root / "releases", "shared": root / "shared", "logs": root / "logs", "current": root / "current"}


def _fail(status: str, message: str, **extra) -> dict:
    return {"ok": False, "status": status, "message": redact_text(message)[:800], **extra}


def _state(d: dict) -> dict:
    f = d["shared"] / "state.json"
    try:
        return json.loads(f.read_text())
    except (OSError, ValueError):
        return {}


def _write_state(d: dict, **kw) -> None:
    d["shared"].mkdir(parents=True, exist_ok=True)
    st = {**_state(d), **kw}
    tmp = d["shared"] / "state.json.tmp"
    tmp.write_text(json.dumps(st))
    os.replace(tmp, d["shared"] / "state.json")


def _current_release(d: dict) -> str | None:
    try:
        return os.path.basename(os.readlink(d["current"]))
    except OSError:
        return None


def _secure_write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        f.write(text)
    os.replace(tmp, path)


def _switch_symlink(d: dict, release_dir: Path) -> None:
    tmp = d["root"] / "current.tmp"
    if tmp.is_symlink() or tmp.exists():
        tmp.unlink()
    os.symlink(release_dir, tmp)
    os.replace(tmp, d["current"])  # atomic on POSIX


def _run_env(ctx: OpContext) -> dict:
    return {"HOME": str(ctx.path("apps")), "GIT_TERMINAL_PROMPT": "0", "GIT_CONFIG_NOSYSTEM": "1", "NPM_CONFIG_UPDATE_NOTIFIER": "false"}


def _http_status(ctx: OpContext, port: int, path: str) -> int:
    fn = ctx.extra.get("http_get")
    if fn:
        return fn(port, path)
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{int(port)}{path}", timeout=3) as r:  # loopback only
            return r.status
    except Exception:
        return 0


def _health(ctx: OpContext, port: int, path: str) -> tuple[bool, int]:
    retries = int(ctx.extra.get("health_retries", 10))
    sleep = ctx.extra.get("sleep", time.sleep)
    last = 0
    for i in range(retries):
        last = _http_status(ctx, port, path)
        if 200 <= last < 400:
            return True, last
        if i < retries - 1:
            sleep(1)
    return False, last


def _ecosystem(ctx: OpContext, d: dict, p, name: str, cwd: Path, port: int, env: dict, runtime: str, entry: str | None) -> Path:
    logs = d["logs"]
    logs.mkdir(parents=True, exist_ok=True)
    base_env = {**env, "PORT": str(port), "NODE_ENV": env.get("NODE_ENV", "production")}
    app = {"name": name, "cwd": str(cwd), "env": base_env, "out_file": str(logs / "out.log"), "error_file": str(logs / "err.log"), "merge_logs": True, "autorestart": True, "max_restarts": 10, "min_uptime": "5s"}
    if runtime == "nodejs":
        app["script"] = entry
    else:
        app.update(script=str(cwd / "venv" / "bin" / "python"), interpreter="none", args=["-m", "gunicorn", entry, "-b", f"127.0.0.1:{port}", "--workers", "2"])
    path = d["shared"] / f"ecosystem-{name}.json"
    _secure_write(path, json.dumps({"apps": [app]}))
    return path


def _require_tools(ctx: OpContext, runtime: str, version: str) -> dict | None:
    need = ["git"]
    if runtime == "nodejs":
        need += ["node", "npm", "pm2"]
    elif runtime == "python":
        need += [f"python{version}", "pm2"]
    else:
        need += [f"php-fpm{version}"]
    missing = [n for n in need if not ctx.which(n)]
    if missing:
        return {"ok": False, "status": "not_configured", "message": f"Required software is not installed on this server: {', '.join(missing)}. Nothing was deployed."}
    if runtime == "nodejs":
        r = ctx.run(["node", "--version"], timeout=10)
        major = r.stdout.strip().lstrip("v").split(".")[0]
        if not r.ok or major != version.split(".")[0]:
            return {"ok": False, "status": "runtime_mismatch", "message": f"Requested Node.js {version} but the server's active Node.js is {r.stdout.strip() or 'unknown'}."}
    return None


def _wp_config(env: dict) -> str:
    vals = {k: env.get(k, "") for k in ("WP_DB_NAME", "WP_DB_USER", "WP_DB_PASSWORD", "WP_DB_HOST")}
    for k, v in vals.items():
        if k == "WP_DB_HOST" and not v:
            vals[k] = "localhost"
        elif not WP_SAFE.fullmatch(vals[k]):
            raise ValueError(f"{k} is missing or has unsupported characters")
    salts = "\n".join(f"define('{n}', '{secrets.token_urlsafe(48)}');" for n in ("AUTH_KEY", "SECURE_AUTH_KEY", "LOGGED_IN_KEY", "NONCE_KEY", "AUTH_SALT", "SECURE_AUTH_SALT", "LOGGED_IN_SALT", "NONCE_SALT"))
    return (
        "<?php\n"
        f"define('DB_NAME', '{vals['WP_DB_NAME']}');\ndefine('DB_USER', '{vals['WP_DB_USER']}');\n"
        f"define('DB_PASSWORD', '{vals['WP_DB_PASSWORD']}');\ndefine('DB_HOST', '{vals['WP_DB_HOST']}');\n"
        "define('DB_CHARSET', 'utf8mb4');\ndefine('DB_COLLATE', '');\n"
        f"{salts}\n$table_prefix = 'wp_';\ndefine('WP_DEBUG', false);\n"
        "if (!defined('ABSPATH')) { define('ABSPATH', __DIR__ . '/'); }\nrequire_once ABSPATH . 'wp-settings.php';\n"
    )


def _pm2(ctx: OpContext, *args: str):
    return ctx.run(["pm2", *args], timeout=60, env=_run_env(ctx))


def _prune(d: dict, keep: int) -> list[str]:
    if not d["releases"].exists():
        return []
    protected = {_current_release(d), _state(d).get("previous")}
    rels = sorted(x.name for x in d["releases"].iterdir() if x.is_dir() and RELEASE_RE.fullmatch(x.name))
    removable = [r for r in rels if r not in protected]
    extra = len(rels) - keep
    gone = removable[: max(0, extra)]
    for r in gone:
        shutil.rmtree(d["releases"] / r, ignore_errors=True)
    return gone


def _build(ctx: OpContext, p: Deploy, release: Path) -> dict | None:
    env = _run_env(ctx)
    r = ctx.run(["git", "clone", "--depth", "1", "--branch", p.branch, "--single-branch", "--", p.repo_url, str(release)], timeout=300, env=env)
    if not r.ok:
        return _fail("clone_failed", f"git clone failed: {r.stderr[-500:]}")
    head = ctx.run(["git", "-C", str(release), "rev-parse", "HEAD"], timeout=20, env=env)
    commit = head.stdout.strip()[:40] if head.ok else None
    shutil.rmtree(release / ".git", ignore_errors=True)  # never serve repository history
    if p.runtime == "nodejs":
        if not (release / "package.json").exists():
            return _fail("build_failed", "package.json not found in repository root.", commit=commit)
        cmd = ["npm", "ci"] if (release / "package-lock.json").exists() else ["npm", "install"]
        r = ctx.run([*cmd, "--omit=dev", "--ignore-scripts", "--no-audit", "--no-fund"], timeout=600, cwd=release, env=env)
        if not r.ok:
            return _fail("build_failed", f"npm install failed: {r.stderr[-500:]}", commit=commit)
    elif p.runtime == "python":
        py = f"python{p.version}"
        r = ctx.run([py, "-m", "venv", str(release / "venv")], timeout=120, env=env)
        if not r.ok:
            return _fail("build_failed", f"venv creation failed: {r.stderr[-500:]}", commit=commit)
        if (release / "requirements.txt").exists():
            r = ctx.run([py, "-m", "pip", "--python", str(release / "venv" / "bin" / "python"), "install", "--no-input", "--disable-pip-version-check", "-r", "requirements.txt"], timeout=600, cwd=release, env=env)
            if not r.ok:
                return _fail("build_failed", f"pip install failed: {r.stderr[-500:]}", commit=commit)
    else:
        if (release / "composer.json").exists() and p.runtime == "php":
            if not ctx.which("composer"):
                return _fail("not_configured", "composer.json present but composer is not installed on this server.", commit=commit)
            r = ctx.run(["composer", "install", "--no-dev", "--no-interaction", "--no-scripts", "--prefer-dist"], timeout=600, cwd=release, env=env)
            if not r.ok:
                return _fail("build_failed", f"composer install failed: {r.stderr[-500:]}", commit=commit)
    return {"commit": commit}


def _files_health(release: Path, runtime: str) -> bool:
    names = ["wp-settings.php"] if runtime == "wordpress" else ["index.php", "index.html", "public/index.php", "public/index.html"]
    return any((release / n).exists() for n in names)


# --------------------------------------------------------------------------- operations
@operation("apps.detect_runtimes", DetectRuntimes, timeout=60)
def detect_runtimes(_p: DetectRuntimes, ctx: OpContext) -> dict:
    found: dict[str, list[str]] = {"php": [], "nodejs": [], "python": []}
    for v in SUPPORTED["php"]:
        if ctx.which(f"php-fpm{v}"):
            found["php"].append(v)
    if ctx.which("node"):
        r = ctx.run(["node", "--version"], timeout=10)
        m = re.match(r"^v(\d{1,2})\.", r.stdout.strip())
        if r.ok and m and m.group(1) in SUPPORTED["nodejs"]:
            found["nodejs"].append(m.group(1))
    for v in SUPPORTED["python"]:
        if ctx.which(f"python{v}"):
            found["python"].append(v)
    tools = {t: bool(ctx.which(t)) for t in ("git", "pm2", "npm", "composer")}
    return {"ok": True, "runtimes": found, "tools": tools, "message": None if any(found.values()) else "No supported runtimes detected."}


@operation("apps.deploy", Deploy, timeout=900)
def deploy(p: Deploy, ctx: OpContext) -> dict:
    d = _dirs(ctx, p.app)
    live = _current_release(d)
    if ctx.dry_run:
        return {"ok": True, "status": "dry_run", "release_id": p.release_id, "live_release": live}
    problem = _require_tools(ctx, p.runtime, p.version)
    if problem:
        return {**problem, "live_release": live}
    if p.runtime in {"nodejs", "python"}:
        if not (p.port and p.canary_port and p.entrypoint):
            return _fail("rejected", "port, canary_port and entrypoint are required for this runtime.", live_release=live)
        if p.runtime == "nodejs" and not ENTRY_NODE_RE.fullmatch(p.entrypoint) or p.runtime == "python" and not ENTRY_PY_RE.fullmatch(p.entrypoint):
            return _fail("rejected", "Invalid entrypoint.", live_release=live)
    release = d["releases"] / p.release_id
    if release.exists():
        return _fail("rejected", "Release id already exists.", live_release=live)
    d["releases"].mkdir(parents=True, exist_ok=True)
    d["shared"].mkdir(parents=True, exist_ok=True)

    def abort(res: dict) -> dict:
        shutil.rmtree(release, ignore_errors=True)  # failed releases are discarded; live release untouched
        return {**res, "live_release": live}

    built = _build(ctx, p, release)
    if built is not None and built.get("ok") is False:
        return abort(built)
    commit = (built or {}).get("commit")
    try:
        _secure_write(d["shared"] / ".env", "".join(f"{k}={json.dumps(v)}\n" for k, v in p.env.items()))
        if p.runtime == "wordpress":
            _secure_write(d["shared"] / "wp-config.php", _wp_config(p.env))
            os.symlink(d["shared"] / "wp-config.php", release / "wp-config.php")
        os.symlink(d["shared"] / ".env", release / ".env")
    except (ValueError, OSError) as e:
        return abort(_fail("build_failed", f"Could not prepare release files: {e}", commit=commit))

    # health check BEFORE switching traffic
    if p.runtime in {"nodejs", "python"}:
        cname = f"{p.app}-canary"
        eco = _ecosystem(ctx, d, p, cname, release, p.canary_port, p.env, p.runtime, p.entrypoint)
        st = _pm2(ctx, "start", str(eco))
        if not st.ok:
            _pm2(ctx, "delete", cname)
            return abort(_fail("health_failed", f"Could not start the new release: {st.stderr[-400:]}", commit=commit))
        ok, code = _health(ctx, p.canary_port, p.health_path)
        _pm2(ctx, "delete", cname)
        if not ok:
            return abort(_fail("health_failed", f"Health check {p.health_path} failed on the new release (last status {code or 'no response'}); traffic stays on the previous release.", commit=commit))
    elif not _files_health(release, p.runtime):
        return abort(_fail("health_failed", "No index file found in the release; traffic stays on the previous release.", commit=commit))

    _switch_symlink(d, release)
    if p.runtime in {"nodejs", "python"}:
        eco = _ecosystem(ctx, d, p, p.app, d["current"], p.port, p.env, p.runtime, p.entrypoint)
        r = _pm2(ctx, "startOrReload", str(eco), "--update-env")
        if not r.ok:
            if live:
                _switch_symlink(d, d["releases"] / live)
            else:
                d["current"].unlink(missing_ok=True)
            shutil.rmtree(release, ignore_errors=True)
            return _fail("switch_failed", f"Process manager failed to start the release; reverted to the previous one: {r.stderr[-400:]}", commit=commit, live_release=live)
    _write_state(d, current=p.release_id, previous=live)
    gone = _prune(d, p.keep_releases)
    return {"ok": True, "status": "deployed", "release_id": p.release_id, "previous_release": live, "commit": commit, "pruned": gone}


@operation("apps.rollback", Rollback, timeout=300)
def rollback(p: Rollback, ctx: OpContext) -> dict:
    d = _dirs(ctx, p.app)
    live = _current_release(d)
    target = p.to_release or _state(d).get("previous")
    if not target or target == live:
        return _fail("no_target", "No previous release available.", live_release=live)
    rdir = d["releases"] / target
    if not rdir.is_dir():
        return _fail("release_missing", "That release is no longer on disk.", live_release=live)
    if ctx.dry_run:
        return {"ok": True, "status": "dry_run", "would_switch_to": target}
    if p.runtime in {"nodejs", "python"}:
        if not ctx.which("pm2"):
            return {"ok": False, "status": "not_configured", "message": "pm2 is not installed on this server.", "live_release": live}
        port = p.port
        if not port:
            return _fail("rejected", "port required", live_release=live)
        ok_files = (rdir / "package.json").exists() if p.runtime == "nodejs" else (rdir / "venv").exists()
        if not ok_files:
            return _fail("release_broken", "Release is incomplete.", live_release=live)
    elif not _files_health(rdir, p.runtime):
        return _fail("health_failed", "Release has no index file.", live_release=live)
    _switch_symlink(d, rdir)
    if p.runtime in {"nodejs", "python"}:
        eco = d["shared"] / f"ecosystem-{p.app}.json"
        r = _pm2(ctx, "startOrReload", str(eco), "--update-env") if eco.exists() else None
        if r is None or not r.ok:
            if live:
                _switch_symlink(d, d["releases"] / live)
            return _fail("switch_failed", "Process manager failed; reverted to the release that was live.", live_release=live)
    _write_state(d, current=target, previous=live)
    return {"ok": True, "status": "rolled_back", "release_id": target, "previous_release": live}


@operation("apps.process", Process, timeout=60)
def process(p: Process, ctx: OpContext) -> dict:
    d = _dirs(ctx, p.app)
    if ctx.dry_run:
        return {"ok": True, "status": "dry_run", "action": p.action}
    if not ctx.which("pm2"):
        return {"ok": False, "status": "not_configured", "message": "pm2 is not installed on this server."}
    if not d["current"].exists():
        return _fail("not_deployed", "Application has no live release.")
    eco = d["shared"] / f"ecosystem-{p.app}.json"
    if p.action == "status":
        r = _pm2(ctx, "pid", p.app)
        pid = r.stdout.strip().split()[-1] if r.stdout.strip() else ""
        state = "running" if r.ok and pid.isdigit() and int(pid) > 0 else "stopped"
        return {"ok": True, "status": "status", "state": state}
    if p.action == "start":
        r = _pm2(ctx, "startOrReload", str(eco), "--update-env") if eco.exists() else _pm2(ctx, "start", p.app)
    elif p.action == "restart":
        r = _pm2(ctx, "restart", p.app, "--update-env")
        if not r.ok and eco.exists():
            r = _pm2(ctx, "startOrReload", str(eco), "--update-env")
    else:
        r = _pm2(ctx, "stop", p.app)
    if not r.ok:
        return _fail("process_failed", f"pm2 {p.action} failed: {r.stderr[-400:]}")
    return {"ok": True, "status": p.action, "state": "stopped" if p.action == "stop" else "running"}


@operation("apps.logs", Logs, timeout=30)
def logs(p: Logs, ctx: OpContext) -> dict:
    d = _dirs(ctx, p.app)
    f = d["logs"] / ("out.log" if p.stream == "out" else "err.log")
    if not f.exists():
        return {"ok": True, "lines": [], "page": p.page, "page_size": p.page_size, "has_more": False, "total_lines": 0}
    with f.open("rb") as fh:
        fh.seek(0, os.SEEK_END)
        size = fh.tell()
        fh.seek(max(0, size - 2 * 1024 * 1024))
        raw = fh.read().decode(errors="replace")
    lines = raw.splitlines()
    total = len(lines)
    end = total - (p.page - 1) * p.page_size
    start = max(0, end - p.page_size)
    chunk = lines[start:end] if end > 0 else []
    return {"ok": True, "lines": [redact_text(x[:2000]) for x in chunk], "page": p.page, "page_size": p.page_size, "has_more": start > 0, "total_lines": total}


@operation("apps.usage", Usage, timeout=60)
def usage(p: Usage, ctx: OpContext) -> dict:
    d = _dirs(ctx, p.app)
    size = 0
    for root, _dirs_, files in os.walk(d["root"]):
        for fn in files:
            try:
                size += os.lstat(os.path.join(root, fn)).st_size
            except OSError:
                pass
    out = {"ok": True, "disk_mb": round(size / 1048576, 2), "releases": len(list(d["releases"].iterdir())) if d["releases"].exists() else 0, "memory_mb": None, "cpu_percent": None, "restarts": None, "uptime_s": None}
    if ctx.which("pm2"):
        r = _pm2(ctx, "jlist")
        try:
            for item in json.loads(r.stdout[r.stdout.index("["):]):
                if item.get("name") == p.app:
                    mon, env = item.get("monit", {}), item.get("pm2_env", {})
                    out.update(memory_mb=round((mon.get("memory") or 0) / 1048576, 1), cpu_percent=mon.get("cpu"), restarts=env.get("restart_time"))
                    up = env.get("pm_uptime")
                    out["uptime_s"] = int(time.time() - up / 1000) if up else None
        except (ValueError, TypeError):
            pass
    return out


@operation("apps.remove", Remove, timeout=120, destructive=True)
def remove(p: Remove, ctx: OpContext) -> dict:
    d = _dirs(ctx, p.app)
    if ctx.dry_run:
        return {"ok": True, "status": "dry_run"}
    if ctx.which("pm2"):
        _pm2(ctx, "delete", p.app)
        _pm2(ctx, "delete", f"{p.app}-canary")
    shutil.rmtree(d["root"], ignore_errors=True)
    return {"ok": True, "status": "removed"}
