"""Backup creation, verification, inspection, isolated restore, guarded overwrite restore and guarded pruning.

Safety rules enforced here (not only in the control plane):
- the destination root must already exist (we never create/format/mount disks), identity marker and free space are checked BEFORE writing;
- archives are written to *.partial and renamed; existing backups are never overwritten or deleted except by `backups.prune`;
- `backups.prune` refuses to run unless a verified, checksum-matching recovery point is present and is not in the delete set;
- restores verify the checksum first, default to an isolated directory, and overwrite mode needs explicit confirmation and moves
  (never deletes) the previous data aside.
"""
import hashlib
import json
import os
import re
import shutil
import tarfile
import time
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from kvl_agent.backup_crypto import create_key, decrypt_file, encrypt_file, key_fingerprint, load_key
from kvl_agent.registry import operation
from kvl_agent.safe_archive import Limits, extract_tar
from kvl_agent.safe_paths import OpError, is_link, split_rel, tree_size

KEY_REL = ("keys", "backup.key")
MARKER = ".kvl-backup-disk"
_REL = r"^[A-Za-z0-9][A-Za-z0-9._\-/]{0,254}$"
_SLUG = r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$"
KINDS = Literal["website_files", "database", "website_config", "saas_config"]


class _B(BaseModel):
    model_config = ConfigDict(extra="forbid")


def under(ctx, rel: str, *, must_exist: bool = False, must_be_under: Path | None = None) -> Path:
    """Resolve a data_dir-relative path, rejecting traversal and any symlink component."""
    parts = split_rel(rel, allow_internal=True)
    if not parts:
        raise OpError("bad_path", "Path is required.")
    cur = ctx.data_dir.resolve()
    for part in parts:
        cur = cur / part
        if os.path.lexists(cur) and is_link(cur):
            raise OpError("symlink_blocked", "Symbolic links are not allowed here.")
    if must_exist and not cur.exists():
        raise OpError("not_found", f"{rel} does not exist.")
    if must_be_under is not None:
        r = str(must_be_under.resolve())
        if not (str(cur) == r or str(cur).startswith(r + os.sep)):
            raise OpError("path_traversal", "Path is outside the backup area.")
    return cur


def _sha(p: Path) -> str:
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for b in iter(lambda: f.read(1024 * 1024), b""):
            h.update(b)
    return h.hexdigest()


def _dest_root(ctx, rel: str, disk_id: str | None) -> Path:
    root = under(ctx, rel)
    if not root.is_dir():
        raise OpError("disk_unavailable", "Backup destination is not available (missing or not mounted).")
    if disk_id is not None:
        m = root / MARKER
        if not m.is_file() or m.read_text().strip() != disk_id:
            raise OpError("disk_identity_mismatch", "Backup disk identity marker does not match; refusing to write.")
    return root


def _need_free(root: Path, need: int, reserve: int) -> int:
    free = shutil.disk_usage(root).free
    if free < need + reserve:
        raise OpError("disk_full", f"Not enough free space on the backup disk (need ~{need + reserve} bytes, {free} free).")
    return free


def _tmp(ctx) -> Path:
    t = ctx.data_dir.resolve() / ".tmp"
    t.mkdir(exist_ok=True, mode=0o700)
    return t


# ---------------------------------------------------------------- setup helpers
class NoP(_B):
    pass


class InitKeyP(_B):
    pass


@operation("backups.init_key", InitKeyP, timeout=15)
def init_key(_p, ctx) -> dict:
    """Create the backup encryption key if (and only if) none exists. Returns the fingerprint, never the key."""
    path = ctx.data_dir.resolve().joinpath(*KEY_REL)
    return {"key_id": create_key(path), "created": True}


class InitDestP(_B):
    dest_root: str = Field("backups", pattern=_REL)
    disk_id: str | None = Field(None, pattern=r"^[A-Za-z0-9._-]{6,64}$")


@operation("backups.init_dest", InitDestP, timeout=15)
def init_dest(p: InitDestP, ctx) -> dict:
    """Explicit setup: create the destination directory and, optionally, an identity marker. Never formats or touches existing data."""
    root = under(ctx, p.dest_root)
    root.mkdir(parents=True, exist_ok=True)
    marker = root / MARKER
    if p.disk_id and not marker.exists():
        marker.write_text(p.disk_id + "\n")
    free = shutil.disk_usage(root).free
    return {"dest_root": p.dest_root, "marker_set": marker.exists(), "free_bytes": free}


class DiskCheckP(_B):
    dest_root: str = Field("backups", pattern=_REL)
    required_bytes: int = Field(0, ge=0)
    disk_id: str | None = Field(None, pattern=r"^[A-Za-z0-9._-]{6,64}$")
    reserve_bytes: int = Field(64 * 1024**2, ge=0)


@operation("backups.disk_check", DiskCheckP, timeout=15)
def disk_check(p: DiskCheckP, ctx) -> dict:
    root = _dest_root(ctx, p.dest_root, p.disk_id)
    du = shutil.disk_usage(root)
    return {"ok": du.free >= p.required_bytes + p.reserve_bytes, "free_bytes": du.free, "total_bytes": du.total, "device": os.stat(root).st_dev, "identity_verified": p.disk_id is not None}


# ---------------------------------------------------------------- create
class CreateP(_B):
    kind: KINDS
    name: str = Field(pattern=_SLUG)
    scope: str = Field(pattern=_SLUG, description="Subfolder under dest_root (tenant id)")
    sources: list[str] = Field(default_factory=list, max_length=20)
    inline_files: dict[str, str] = Field(default_factory=dict)
    dest_root: str = Field("backups", pattern=_REL)
    disk_id: str | None = Field(None, pattern=r"^[A-Za-z0-9._-]{6,64}$")
    encrypt: bool = False
    reserve_bytes: int = Field(64 * 1024**2, ge=0)

    @model_validator(mode="after")
    def _chk(self):
        if not self.sources and not self.inline_files:
            raise ValueError("sources or inline_files required")
        if len(self.inline_files) > 20 or any(not re.match(r"^[A-Za-z0-9._-]{1,64}$", k) or len(v) > 262144 for k, v in self.inline_files.items()):
            raise ValueError("invalid inline_files")
        return self


@operation("backups.create", CreateP, timeout=3600)
def create(p: CreateP, ctx) -> dict:
    src_paths = [under(ctx, s, must_exist=True) for s in p.sources]
    root = _dest_root(ctx, p.dest_root, p.disk_id)
    est = sum(tree_size(s)[0] for s in src_paths) + sum(len(v) for v in p.inline_files.values())
    _need_free(root, int(est * 1.1) * (2 if p.encrypt else 1), p.reserve_bytes)
    key = load_key(ctx.data_dir.resolve().joinpath(*KEY_REL)) if p.encrypt else None
    if ctx.dry_run:
        return {"ok": True, "dry_run": True, "estimated_source_bytes": est}
    out_dir = root / p.scope
    if os.path.lexists(out_dir) and is_link(out_dir):
        raise OpError("symlink_blocked", "Destination scope is a symlink.")
    out_dir.mkdir(exist_ok=True)
    ts = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    base = f"{p.name}-{ts}-{uuid.uuid4().hex[:6]}.tar.gz"
    final = out_dir / (base + (".enc" if p.encrypt else ""))
    tar_tmp = _tmp(ctx) / f"{uuid.uuid4().hex}.tar.gz"
    entries = skipped = 0
    single = len(src_paths) == 1 and src_paths[0].is_dir()
    try:
        with tarfile.open(tar_tmp, "w:gz") as t:
            for s in src_paths:
                prefix = "data" if single else f"data/{s.name}"
                if s.is_file():
                    t.add(s, f"{prefix}", recursive=False)
                    entries += 1
                    continue
                for dp, dn, fn in os.walk(s, followlinks=False):
                    dn[:] = [d for d in dn if not is_link(Path(dp, d))]
                    skipped += sum(1 for n in os.listdir(dp) if is_link(Path(dp, n)))
                    rel_dir = Path(dp).relative_to(s).as_posix()
                    for f in fn:
                        fp = Path(dp, f)
                        if is_link(fp):
                            continue
                        arc = "/".join(x for x in (prefix, "" if rel_dir == "." else rel_dir, f) if x)
                        t.add(fp, arc, recursive=False)
                        entries += 1
            for name, text in p.inline_files.items():
                data = text.encode()
                ti = tarfile.TarInfo(f"meta/{name}")
                ti.size, ti.mtime, ti.mode = len(data), int(time.time()), 0o644
                import io

                t.addfile(ti, io.BytesIO(data))
                entries += 1
        part = final.with_name(final.name + ".partial")
        if p.encrypt:
            encrypt_file(tar_tmp, part, key)
        else:
            shutil.copyfile(tar_tmp, part)
        if os.path.lexists(final):
            raise OpError("exists", "Backup file already exists.")
        os.replace(part, final)
    except OSError as e:
        for f in (tar_tmp, final.with_name(final.name + ".partial")):
            f.unlink(missing_ok=True)
        if getattr(e, "errno", None) == 28:
            raise OpError("disk_full", "Backup disk ran out of space; partial file removed.") from None
        raise OpError("io_error", f"Backup failed: {e.strerror or 'I/O error'}") from None
    except BaseException:
        for f in (tar_tmp, final.with_name(final.name + ".partial")):
            f.unlink(missing_ok=True)
        raise
    tar_tmp.unlink(missing_ok=True)
    sha, size = _sha(final), final.stat().st_size
    manifest = {
        "name": p.name, "kind": p.kind, "scope": p.scope, "created_at": datetime.now(UTC).isoformat(), "sha256": sha, "size": size,
        "entries": entries, "source_bytes": est, "encrypted": p.encrypt, "key_id": key_fingerprint(key) if key else None,
        "layout": "single" if single else "multi", "symlinks_skipped": skipped,
    }
    final.with_name(final.name + ".manifest.json").write_text(json.dumps(manifest, indent=1))
    return {"ok": True, "archive": final.relative_to(ctx.data_dir.resolve()).as_posix(), "size": size, "sha256": sha, "entries": entries, "encrypted": p.encrypt, "key_id": manifest["key_id"], "source_bytes": est, "symlinks_skipped": skipped, "layout": manifest["layout"]}


# ---------------------------------------------------------------- verify / inspect
class VerifyP(_B):
    archive: str = Field(pattern=_REL)
    expected_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    deep: bool = False
    dest_root: str = Field("backups", pattern=_REL)


def _archive(ctx, rel: str, dest_root: str) -> Path:
    a = under(ctx, rel, must_exist=True, must_be_under=under(ctx, dest_root))
    if not a.is_file():
        raise OpError("not_found", "Backup file not found.")
    return a


def _open_plain(ctx, a: Path) -> tuple[Path, bool]:
    """Return a plain tar.gz path (decrypting to a temp file when needed) and whether it is temporary."""
    if a.name.endswith(".enc"):
        tmp = _tmp(ctx) / f"{uuid.uuid4().hex}.tar.gz"
        try:
            decrypt_file(a, tmp, load_key(ctx.data_dir.resolve().joinpath(*KEY_REL)))
        except BaseException:
            tmp.unlink(missing_ok=True)
            raise
        return tmp, True
    return a, False


@operation("backups.verify", VerifyP, timeout=3600)
def verify(p: VerifyP, ctx) -> dict:
    a = _archive(ctx, p.archive, p.dest_root)
    actual = _sha(a)
    if actual != p.expected_sha256:
        return {"ok": False, "reason": "checksum_mismatch", "expected": p.expected_sha256, "actual": actual}
    res = {"ok": True, "sha256": actual, "size": a.stat().st_size, "deep": False}
    if p.deep:
        try:
            plain, tmp = _open_plain(ctx, a)
        except OpError as e:
            return {"ok": False, "reason": e.code, "message": e.message}
        try:
            n = 0
            with tarfile.open(plain, "r:gz") as t:
                for m in t:
                    if m.isreg():
                        f = t.extractfile(m)
                        while f.read(1024 * 1024):
                            pass
                    n += 1
            res.update(deep=True, members=n)
        except (tarfile.TarError, EOFError, OSError):
            return {"ok": False, "reason": "archive_corrupt"}
        finally:
            if tmp:
                plain.unlink(missing_ok=True)
    return res


class InspectP(_B):
    archive: str = Field(pattern=_REL)
    dest_root: str = Field("backups", pattern=_REL)
    sample: int = Field(30, ge=0, le=200)


@operation("backups.inspect", InspectP, timeout=900)
def inspect(p: InspectP, ctx) -> dict:
    a = _archive(ctx, p.archive, p.dest_root)
    plain, tmp = _open_plain(ctx, a)
    try:
        files = dirs = total = 0
        sample, tops = [], set()
        with tarfile.open(plain, "r:gz") as t:
            for m in t:
                tops.add(m.name.split("/")[0])
                if m.isreg():
                    files += 1
                    total += m.size
                    if len(sample) < p.sample:
                        sample.append({"name": m.name, "size": m.size})
                elif m.isdir():
                    dirs += 1
    except (tarfile.TarError, EOFError, OSError):
        raise OpError("archive_corrupt", "Backup archive cannot be read.") from None
    finally:
        if tmp:
            plain.unlink(missing_ok=True)
    mf = a.with_name(a.name + ".manifest.json")
    manifest = json.loads(mf.read_text()) if mf.is_file() else None
    return {"files": files, "dirs": dirs, "uncompressed_bytes": total, "top_level": sorted(tops), "sample": sample, "encrypted": a.name.endswith(".enc"), "manifest": {k: manifest.get(k) for k in ("kind", "created_at", "layout", "entries", "key_id")} if manifest else None}


# ---------------------------------------------------------------- restore
class RestoreP(_B):
    archive: str = Field(pattern=_REL)
    expected_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    restore_id: str = Field(pattern=r"^[0-9a-f]{32}$")
    mode: Literal["isolated", "overwrite"] = "isolated"
    target: str | None = Field(None, pattern=_REL, description="Overwrite mode only: data_dir-relative site root to replace")
    confirm_overwrite: bool = False
    dest_root: str = Field("backups", pattern=_REL)
    restore_root: str = Field("restores", pattern=_REL)
    disk_id: str | None = Field(None, pattern=r"^[A-Za-z0-9._-]{6,64}$")
    max_total_bytes: int = Field(8 * 1024**3, ge=1)
    reserve_bytes: int = Field(64 * 1024**2, ge=0)

    @model_validator(mode="after")
    def _chk(self):
        if self.mode == "overwrite" and not (self.confirm_overwrite and self.target):
            raise ValueError("overwrite mode requires target and confirm_overwrite=true")
        return self


@operation("backups.restore", RestoreP, timeout=7200, destructive=True)
def restore(p: RestoreP, ctx) -> dict:
    a = _archive(ctx, p.archive, p.dest_root)
    actual = _sha(a)
    if actual != p.expected_sha256:
        raise OpError("checksum_mismatch", "Backup checksum does not match the recorded value; refusing to restore.")
    rroot = under(ctx, p.restore_root)
    if not rroot.is_dir():
        raise OpError("disk_unavailable", "Restore area is not available.")
    if p.mode == "isolated":
        dest = rroot / p.restore_id
        stage = dest
    else:
        target = under(ctx, p.target)
        if is_link(target) if os.path.lexists(target) else False:
            raise OpError("symlink_blocked", "Overwrite target is a symlink.")
        if not target.parent.is_dir():
            raise OpError("not_found", "Overwrite target parent does not exist.")
        dest = target
        stage = rroot / f".staging-{p.restore_id}"
    if os.path.lexists(stage) or (p.mode == "overwrite" and os.path.lexists(rroot / f"pre-{p.restore_id}")):
        raise OpError("exists", "This restore id was already used.")
    mf = a.with_name(a.name + ".manifest.json")
    manifest = json.loads(mf.read_text()) if mf.is_file() else {}
    if p.mode == "overwrite" and manifest.get("layout") != "single":
        raise OpError("unsupported_restore", "Overwrite restore is only supported for single-root backups; restore isolated and copy manually.")
    plain, tmp = _open_plain(ctx, a)
    try:
        need = int(manifest.get("source_bytes") or 0) or a.stat().st_size * 10
        _need_free(rroot, need, p.reserve_bytes)
        stage.mkdir(parents=False)
        try:
            r = extract_tar(plain, stage, Limits(p.max_total_bytes, p.max_total_bytes, 1_000_000, 10_000.0))
        except BaseException:
            shutil.rmtree(stage, ignore_errors=True)
            raise
    finally:
        if tmp:
            plain.unlink(missing_ok=True)
    expected = manifest.get("entries")
    if expected is not None and r["files"] != expected:
        shutil.rmtree(stage, ignore_errors=True)
        raise OpError("restore_integrity", f"Restored {r['files']} files but the backup recorded {expected}.")
    if p.mode == "isolated":
        return {"ok": True, "mode": "isolated", "location": dest.relative_to(ctx.data_dir.resolve()).as_posix(), "files": r["files"], "bytes": r["bytes"], "source_untouched": True}
    new_content = stage / "data"
    if not new_content.is_dir():
        shutil.rmtree(stage, ignore_errors=True)
        raise OpError("restore_integrity", "Backup layout is not a single-root site backup.")
    moved_aside = None
    if os.path.lexists(dest):
        moved_aside = rroot / f"pre-{p.restore_id}"
        os.replace(dest, moved_aside)
    try:
        os.replace(new_content, dest)
    except OSError:
        if moved_aside:
            os.replace(moved_aside, dest)
        raise OpError("io_error", "Could not swap restored data in; original data was put back.") from None
    shutil.rmtree(stage, ignore_errors=True)
    return {"ok": True, "mode": "overwrite", "location": p.target, "files": r["files"], "bytes": r["bytes"], "previous_data_kept_at": moved_aside.relative_to(ctx.data_dir.resolve()).as_posix() if moved_aside else None}


# ---------------------------------------------------------------- prune / offsite
class ArchRef(_B):
    archive: str = Field(pattern=_REL)
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")


class PruneP(_B):
    confirm: Literal[True]
    recovery_point: ArchRef
    delete: list[ArchRef] = Field(min_length=1, max_length=500)
    dest_root: str = Field("backups", pattern=_REL)


@operation("backups.prune", PruneP, timeout=900, destructive=True)
def prune(p: PruneP, ctx) -> dict:
    """Delete the listed backups, but ONLY if the named recovery point exists, still matches its checksum and is not itself listed."""
    rp = _archive(ctx, p.recovery_point.archive, p.dest_root)
    if _sha(rp) != p.recovery_point.sha256:
        raise OpError("no_confirmed_recovery_point", "The recovery point failed checksum verification; nothing was deleted.")
    deleted, freed, skipped = [], 0, []
    for ref in p.delete:
        if ref.archive == p.recovery_point.archive:
            raise OpError("bad_request", "The recovery point cannot be in the delete list.")
        try:
            a = _archive(ctx, ref.archive, p.dest_root)
        except OpError:
            skipped.append({"archive": ref.archive, "reason": "not_found"})
            continue
        if _sha(a) != ref.sha256:
            skipped.append({"archive": ref.archive, "reason": "checksum_changed"})
            continue
        freed += a.stat().st_size
        a.unlink()
        a.with_name(a.name + ".manifest.json").unlink(missing_ok=True)
        deleted.append(ref.archive)
    return {"ok": True, "deleted": deleted, "skipped": skipped, "freed_bytes": freed}


class OffsiteLocalP(_B):
    archive: str = Field(pattern=_REL)
    expected_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    dest_dir: str = Field(pattern=_REL, description="Local/mounted off-site directory, relative to data_dir")
    dest_root: str = Field("backups", pattern=_REL)
    disk_id: str | None = Field(None, pattern=r"^[A-Za-z0-9._-]{6,64}$")


@operation("backups.offsite_copy_local", OffsiteLocalP, timeout=3600)
def offsite_copy_local(p: OffsiteLocalP, ctx) -> dict:
    a = _archive(ctx, p.archive, p.dest_root)
    d = _dest_root(ctx, p.dest_dir, p.disk_id)
    _need_free(d, a.stat().st_size, 64 * 1024**2)
    target = d / a.name
    if target.exists():
        raise OpError("exists", "Off-site copy already exists.")
    part = target.with_name(target.name + ".partial")
    shutil.copyfile(a, part)
    if _sha(part) != p.expected_sha256:
        part.unlink(missing_ok=True)
        raise OpError("checksum_mismatch", "Copy does not match the recorded checksum.")
    os.replace(part, target)
    return {"ok": True, "copied_to": target.relative_to(ctx.data_dir.resolve()).as_posix(), "size": target.stat().st_size}
