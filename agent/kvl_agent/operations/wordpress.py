"""WordPress toolkit WITHOUT executing customer PHP.

wp-cli would load wp-config.php and plugins as the agent user, i.e. run customer code with agent privileges. Instead:
- status: versions are read from file headers (wp-includes/version.php, plugin main-file headers, theme style.css).
- plugin/theme updates: the package is downloaded ONLY from https://downloads.wordpress.org, size capped, extracted with
  the safe extractor into a staging dir, then swapped in atomically; the previous version is kept for rollback.
- maintenance mode: writes/removes WordPress' own `.maintenance` marker with fixed content.
Core updates are not done here (they need WordPress' PHP upgrader + DB upgrade); status reports the version only.
"""
import os
import re
import shutil
import time
import urllib.request
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from kvl_agent.context import OpContext
from kvl_agent.operations.apps import APP_RE
from kvl_agent.redact import redact_text
from kvl_agent.registry import operation
from kvl_agent.safe_archive import Limits, extract_zip
from kvl_agent.safe_paths import OpError

SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,99}$")
VERSION_RE = re.compile(r"^[0-9][0-9A-Za-z.\-]{0,30}$")
DL_RE = re.compile(r"^https://downloads\.wordpress\.org/(plugin|theme)/([a-z0-9][a-z0-9-]{0,99})(?:\.[0-9][0-9A-Za-z.\-]{0,30})?\.zip$")
MAX_ZIP = 64 * 1024 * 1024
HEADER_BYTES = 8192
KEEP_BACKUPS = 3


def _slug(v: str) -> str:
    if not SLUG_RE.fullmatch(v):
        raise ValueError("invalid slug")
    return v


class _S(BaseModel):
    model_config = ConfigDict(extra="forbid")


class WpRef(_S):
    app: str

    @field_validator("app")
    @classmethod
    def _app(cls, v: str) -> str:
        if not APP_RE.fullmatch(v):
            raise ValueError("invalid app name")
        return v


class Status(WpRef):
    pass


class UpdateItem(WpRef):
    kind: Literal["plugin", "theme"]
    slug: str
    package_url: str

    _s = field_validator("slug")(_slug)

    @field_validator("package_url")
    @classmethod
    def _url(cls, v: str) -> str:
        if not DL_RE.fullmatch(v):
            raise ValueError("package must come from https://downloads.wordpress.org")
        return v


class RollbackItem(WpRef):
    kind: Literal["plugin", "theme"]
    slug: str
    _s = field_validator("slug")(_slug)


class Maintenance(WpRef):
    enabled: bool


def _root(ctx: OpContext, app: str) -> Path:
    cur = ctx.path("apps", app, "current")
    if not (cur / "wp-includes" / "version.php").is_file():
        raise OpError("not_wordpress", "This application does not contain a WordPress installation.")
    return cur.resolve()


def _headers(path: Path, keys: tuple[str, ...]) -> dict[str, str]:
    try:
        with path.open("rb") as f:
            text = f.read(HEADER_BYTES).decode("utf-8", errors="replace")
    except OSError:
        return {}
    out = {}
    for k in keys:
        m = re.search(rf"^[ \t/*#@]*{re.escape(k)}:\s*(.+?)\s*(?:\*/)?$", text, re.M | re.I)
        if m:
            out[k] = m.group(1).strip()[:200]
    return out


def _plugins(root: Path) -> list[dict]:
    out = []
    pdir = root / "wp-content" / "plugins"
    if not pdir.is_dir():
        return out
    for d in sorted(pdir.iterdir()):
        if d.is_symlink():
            continue
        files = [d] if d.is_file() and d.suffix == ".php" else (sorted(d.glob("*.php")) if d.is_dir() else [])
        for f in files[:50]:
            h = _headers(f, ("Plugin Name", "Version"))
            if "Plugin Name" in h:
                slug = d.name if d.is_dir() else d.stem
                out.append({"slug": slug, "name": h["Plugin Name"], "version": h.get("Version"), "file": f"{d.name}/{f.name}" if d.is_dir() else d.name, "managed": d.is_dir() and bool(SLUG_RE.fullmatch(slug))})
                break
    return out


def _themes(root: Path) -> list[dict]:
    out = []
    tdir = root / "wp-content" / "themes"
    if not tdir.is_dir():
        return out
    for d in sorted(tdir.iterdir()):
        if d.is_dir() and not d.is_symlink() and (d / "style.css").is_file():
            h = _headers(d / "style.css", ("Theme Name", "Version"))
            if "Theme Name" in h:
                out.append({"slug": d.name, "name": h["Theme Name"], "version": h.get("Version"), "managed": bool(SLUG_RE.fullmatch(d.name))})
    return out


@operation("wp.status", Status, timeout=60)
def status(p: Status, ctx: OpContext) -> dict:
    try:
        root = _root(ctx, p.app)
    except OpError as e:
        return {"ok": False, "status": e.code, "message": e.message}
    m = re.search(r"\$wp_version\s*=\s*'([^']{1,30})'", (root / "wp-includes" / "version.php").read_text(errors="replace"))
    return {"ok": True, "core": {"version": m.group(1) if m else None}, "plugins": _plugins(root), "themes": _themes(root),
            "maintenance": (root / ".maintenance").exists()}


def _download(ctx: OpContext, url: str, dest: Path) -> None:
    fetch = ctx.extra.get("wp_fetch")  # tests inject a fake; production uses urllib with no redirects to other hosts
    if fetch:
        data = fetch(url)
        if len(data) > MAX_ZIP:
            raise OpError("too_large", "Package is too large.")
        dest.write_bytes(data)
        return

    class NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, req, fp, code, msg, headers, newurl):
            if not DL_RE.fullmatch(newurl) and not newurl.startswith("https://downloads.wordpress.org/"):
                raise OpError("bad_redirect", "Download redirected away from downloads.wordpress.org.")
            return super().redirect_request(req, fp, code, msg, headers, newurl)

    opener = urllib.request.build_opener(NoRedirect)
    with opener.open(urllib.request.Request(url, headers={"User-Agent": "KVL-Agent"}), timeout=60) as r, dest.open("wb") as f:
        total = 0
        while chunk := r.read(1024 * 256):
            total += len(chunk)
            if total > MAX_ZIP:
                raise OpError("too_large", "Package is too large.")
            f.write(chunk)


def _target(root: Path, kind: str, slug: str) -> Path:
    base = root / "wp-content" / ("plugins" if kind == "plugin" else "themes")
    t = (base / slug)
    if t.resolve().parent != base.resolve():
        raise OpError("unsafe_path", "Invalid target.")
    return t


def _backups(ctx: OpContext, app: str, kind: str, slug: str) -> Path:
    return ctx.path("apps", app, "shared", "wp-backups", f"{kind}-{slug}")


@operation("wp.update_item", UpdateItem, timeout=300)
def update_item(p: UpdateItem, ctx: OpContext) -> dict:
    m = DL_RE.fullmatch(p.package_url)
    if m.group(1) != p.kind or m.group(2) != p.slug:
        return {"ok": False, "status": "mismatch", "message": "Package does not match the requested item."}
    try:
        root = _root(ctx, p.app)
        target = _target(root, p.kind, p.slug)
        if not target.is_dir() or target.is_symlink():
            return {"ok": False, "status": "not_installed", "message": f"{p.kind} '{p.slug}' is not installed."}
        before = (_plugins(root) if p.kind == "plugin" else _themes(root))
        old_version = next((x["version"] for x in before if x["slug"] == p.slug), None)
        if ctx.dry_run:
            return {"ok": True, "status": "dry_run", "would_download": p.package_url}
        work = ctx.path("tmp", "wp", f"{p.app}-{p.slug}-{os.getpid()}-{int(time.time() * 1000)}")
        work.mkdir(parents=True)
        try:
            z = work / "pkg.zip"
            _download(ctx, p.package_url, z)
            ex = work / "x"
            ex.mkdir()
            extract_zip(z, ex, Limits(max_total=256 * 1024**2, max_file=64 * 1024**2, max_files=20_000))
            top = [x for x in ex.iterdir()]
            if len(top) != 1 or top[0].name != p.slug or not top[0].is_dir():
                return {"ok": False, "status": "bad_package", "message": "Package layout is not a single folder named after the slug."}
            bdir = _backups(ctx, p.app, p.kind, p.slug)
            bdir.mkdir(parents=True, exist_ok=True)
            stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
            backup = bdir / f"{stamp}-{old_version or 'unknown'}"[:120]
            staged = target.with_name(f".kvl-new-{p.slug}")
            shutil.rmtree(staged, ignore_errors=True)
            shutil.move(str(top[0]), staged)
            shutil.move(str(target), backup)  # rename when on the same filesystem
            os.replace(staged, target)
            for old in sorted(bdir.iterdir())[:-KEEP_BACKUPS]:
                shutil.rmtree(old, ignore_errors=True)
        finally:
            shutil.rmtree(work, ignore_errors=True)
        after = (_plugins(root) if p.kind == "plugin" else _themes(root))
        new_version = next((x["version"] for x in after if x["slug"] == p.slug), None)
        return {"ok": True, "status": "updated", "kind": p.kind, "slug": p.slug, "from": old_version, "to": new_version, "backup": backup.name}
    except OpError as e:
        return {"ok": False, "status": e.code, "message": redact_text(e.message)}
    except (OSError, ValueError) as e:
        return {"ok": False, "status": "update_failed", "message": redact_text(f"{type(e).__name__}: {e}")[:300]}


@operation("wp.rollback_item", RollbackItem, timeout=120)
def rollback_item(p: RollbackItem, ctx: OpContext) -> dict:
    try:
        root = _root(ctx, p.app)
        target = _target(root, p.kind, p.slug)
        bdir = _backups(ctx, p.app, p.kind, p.slug)
        snaps = sorted(bdir.iterdir()) if bdir.is_dir() else []
        if not snaps:
            return {"ok": False, "status": "no_backup", "message": "No previous version is stored."}
        last = snaps[-1]
        if ctx.dry_run:
            return {"ok": True, "status": "dry_run", "would_restore": last.name}
        aside = target.with_name(f".kvl-old-{p.slug}")
        shutil.rmtree(aside, ignore_errors=True)
        if target.exists():
            os.replace(target, aside)
        shutil.move(str(last), target)
        shutil.rmtree(aside, ignore_errors=True)
        return {"ok": True, "status": "rolled_back", "restored": last.name}
    except OpError as e:
        return {"ok": False, "status": e.code, "message": e.message}


@operation("wp.maintenance", Maintenance, timeout=30)
def maintenance(p: Maintenance, ctx: OpContext) -> dict:
    try:
        root = _root(ctx, p.app)
    except OpError as e:
        return {"ok": False, "status": e.code, "message": e.message}
    f = root / ".maintenance"
    if ctx.dry_run:
        return {"ok": True, "status": "dry_run"}
    if p.enabled:
        # WordPress' own marker. Fixed content; the timestamp is far in the future so it does not auto-expire after 10 minutes.
        f.write_text("<?php $upgrading = 4102444800; ?>\n")
    else:
        f.unlink(missing_ok=True)
    return {"ok": True, "status": "maintenance_on" if p.enabled else "maintenance_off"}
