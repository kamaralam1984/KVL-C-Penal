"""Tenant-scoped file manager operations. All paths are relative to a site root under the agent sandbox."""
import base64
import binascii
import hashlib
import json
import os
import re
import shutil
import stat
import tarfile
import time
import uuid
import zipfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from kvl_agent.registry import operation
from kvl_agent.safe_archive import CHUNK, Limits, extract_any
from kvl_agent.safe_paths import TRASH_DIR, OpError, is_link, resolve_in_root, site_root, split_rel, tree_size

MAX_READ = 2 * 1024 * 1024
MAX_CHUNK = 1024 * 1024
DEFAULT_MAX_UPLOAD = 10 * 1024 * 1024
BLOCKED_EXT = {".exe", ".dll", ".scr", ".msi", ".com", ".bat", ".cmd", ".so", ".dylib", ".jar", ".apk"}
_ROOT = r"^[A-Za-z0-9][A-Za-z0-9._\-/]{0,254}$"


class _Base(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Site(_Base):
    root: str = Field(pattern=_ROOT, description="Site root relative to the agent data dir (set by the control plane)")

    @field_validator("root")
    @classmethod
    def _no_dots(cls, v: str) -> str:
        if ".." in v.split("/"):
            raise ValueError("invalid root")
        return v


def _iso(ts: float) -> str:
    return datetime.fromtimestamp(ts, UTC).isoformat()


def _entry(p: Path, root: Path) -> dict:
    st = os.lstat(p)
    kind = "symlink" if stat.S_ISLNK(st.st_mode) else "dir" if stat.S_ISDIR(st.st_mode) else "file" if stat.S_ISREG(st.st_mode) else "other"
    rel = p.relative_to(root).as_posix() if p != root else ""
    return {
        "name": p.name, "path": rel, "type": kind, "size": st.st_size if kind == "file" else 0,
        "mtime": _iso(st.st_mtime), "mode": stat.filemode(st.st_mode), "octal": f"{stat.S_IMODE(st.st_mode):04o}",
        "hidden": p.name.startswith("."), "ext": p.suffix.lower().lstrip(".") if kind == "file" else "",
    }


def _check_policy(name: str) -> None:
    ext = Path(name).suffix.lower()
    if ext in BLOCKED_EXT:
        raise OpError("file_type_blocked", f"Files of type '{ext}' are not allowed.")
    if name == TRASH_DIR:
        raise OpError("forbidden_path", "That name is reserved.")


def _free_ok(p: Path, need: int) -> None:
    if shutil.disk_usage(p).free < need + 16 * 1024 * 1024:
        raise OpError("disk_full", "Not enough free disk space.")


# ---------------------------------------------------------------- browse
class ListP(Site):
    path: str = Field("", max_length=4096)
    limit: int = Field(200, ge=1, le=500)
    offset: int = Field(0, ge=0)
    show_hidden: bool = False
    sort: Literal["name", "size", "mtime"] = "name"


@operation("files.list", ListP, timeout=30)
def list_dir(p: ListP, ctx) -> dict:
    root, target = resolve_in_root(ctx, p.root, p.path, must_exist=True)
    if is_link(target):
        raise OpError("symlink_blocked", "Symbolic links cannot be opened.")
    if not target.is_dir():
        raise OpError("not_a_directory", "Not a directory.")
    items = []
    with os.scandir(target) as it:
        for e in it:
            if e.name == TRASH_DIR and target == root:
                continue
            if e.name.startswith(".") and not p.show_hidden:
                continue
            try:
                items.append(_entry(Path(e.path), root))
            except OSError:
                continue
    keyf = {"name": lambda x: x["name"].lower(), "size": lambda x: x["size"], "mtime": lambda x: x["mtime"]}[p.sort]
    items.sort(key=lambda x: (x["type"] != "dir", keyf(x)) if p.sort == "name" else keyf(x), reverse=p.sort != "name")
    return {"path": target.relative_to(root).as_posix() if target != root else "", "total": len(items), "offset": p.offset, "entries": items[p.offset : p.offset + p.limit]}


class StatP(Site):
    path: str = Field("", max_length=4096)


@operation("files.stat", StatP, timeout=15)
def stat_path(p: StatP, ctx) -> dict:
    root, target = resolve_in_root(ctx, p.root, p.path, must_exist=True)
    st = os.lstat(target)
    return {
        "entry": _entry(target, root),
        "permissions": {"owner_uid": st.st_uid, "owner_gid": st.st_gid, "readable": os.access(target, os.R_OK), "writable": os.access(target, os.W_OK), "mode": stat.filemode(st.st_mode), "octal": f"{stat.S_IMODE(st.st_mode):04o}"},
        "hardlinks": st.st_nlink,
    }


class ReadP(Site):
    path: str = Field(max_length=4096)
    max_bytes: int = Field(MAX_READ, ge=1, le=MAX_READ)


@operation("files.read", ReadP, timeout=30)
def read_file(p: ReadP, ctx) -> dict:
    root, target = resolve_in_root(ctx, p.root, p.path, must_exist=True)
    if is_link(target):
        raise OpError("symlink_blocked", "Symbolic links cannot be read.")
    st = os.lstat(target)
    if not stat.S_ISREG(st.st_mode):
        raise OpError("not_a_file", "Not a regular file.")
    if st.st_size > p.max_bytes:
        raise OpError("file_too_large", f"File is larger than the {p.max_bytes} byte download limit.")
    fd = os.open(target, os.O_RDONLY | os.O_NOFOLLOW)
    with os.fdopen(fd, "rb") as f:
        data = f.read(p.max_bytes + 1)
    return {"name": target.name, "size": len(data), "sha256": hashlib.sha256(data).hexdigest(), "content_b64": base64.b64encode(data).decode()}


class UsageP(Site):
    pass


@operation("files.usage", UsageP, timeout=120)
def usage(p: UsageP, ctx) -> dict:
    root = site_root(ctx, p.root)
    b, n = tree_size(root)
    tb, _ = tree_size(root / TRASH_DIR)
    return {"bytes": b, "files": n, "trash_bytes": tb}


# ---------------------------------------------------------------- write
class WriteP(Site):
    path: str = Field(min_length=1, max_length=4096)
    content_b64: str = Field(max_length=int(MAX_CHUNK * 1.4) + 16)
    upload_id: str = Field(pattern=r"^[A-Za-z0-9_-]{8,64}$")
    chunk_index: int = Field(0, ge=0, le=2000)
    total_chunks: int = Field(1, ge=1, le=2000)
    total_size: int = Field(ge=0)
    overwrite: bool = False
    parents: bool = False
    max_bytes: int = Field(DEFAULT_MAX_UPLOAD, ge=1, le=100 * 1024**2)


@operation("files.write", WriteP, timeout=60)
def write_chunk(p: WriteP, ctx) -> dict:
    """Chunked upload. Chunks are staged under <data_dir>/.uploads and moved into place after the final chunk is verified."""
    root, target = resolve_in_root(ctx, p.root, p.path)
    if p.chunk_index >= p.total_chunks:
        raise OpError("bad_chunk", "chunk_index out of range.")
    if p.total_size > p.max_bytes:
        raise OpError("upload_too_large", f"Upload exceeds the {p.max_bytes} byte limit.")
    _check_policy(target.name)
    try:
        data = base64.b64decode(p.content_b64, validate=True)
    except (binascii.Error, ValueError):
        raise OpError("bad_chunk", "Chunk is not valid base64.") from None
    if len(data) > MAX_CHUNK:
        raise OpError("chunk_too_large", "Chunk exceeds 1 MiB.")
    if target == root or (os.path.lexists(target) and (is_link(target) or target.is_dir())):
        raise OpError("bad_target", "Target is a directory or symbolic link.")
    if os.path.lexists(target) and not p.overwrite:
        raise OpError("exists", "A file with that name already exists.")
    if not target.parent.is_dir():
        if not p.parents:
            raise OpError("not_found", "Parent folder does not exist.")
        cur = root
        for part in target.parent.relative_to(root).parts:
            cur = cur / part
            if not os.path.lexists(cur):
                os.mkdir(cur, 0o755)
            elif is_link(cur) or not cur.is_dir():
                raise OpError("symlink_blocked", "Path contains a link or file.")
    upl = ctx.path(".uploads")
    upl.mkdir(exist_ok=True)
    part, meta = upl / f"{p.upload_id}.part", upl / f"{p.upload_id}.json"
    rel = target.relative_to(root).as_posix()
    if p.chunk_index == 0:
        _free_ok(root, p.total_size)
        for f in (part, meta):
            if f.exists():
                f.unlink()
        meta.write_text(json.dumps({"root": p.root, "path": rel, "next": 0, "size": 0, "total_chunks": p.total_chunks, "total_size": p.total_size}))
        part.write_bytes(b"")
    try:
        st = json.loads(meta.read_text())
    except (OSError, ValueError):
        raise OpError("bad_chunk", "Upload session not found; restart the upload.") from None
    if st["root"] != p.root or st["path"] != rel or st["next"] != p.chunk_index or st["total_chunks"] != p.total_chunks or st["total_size"] != p.total_size:
        raise OpError("bad_chunk", "Chunk does not match the upload session.")
    if st["size"] + len(data) > p.total_size:
        part.unlink(missing_ok=True)
        meta.unlink(missing_ok=True)
        raise OpError("upload_too_large", "Received more data than declared.")
    with open(part, "ab") as f:
        f.write(data)
    st["next"] += 1
    st["size"] += len(data)
    if st["next"] < p.total_chunks:
        meta.write_text(json.dumps(st))
        return {"complete": False, "received": st["size"], "next_chunk": st["next"]}
    if st["size"] != p.total_size:
        part.unlink(missing_ok=True)
        meta.unlink(missing_ok=True)
        raise OpError("size_mismatch", "Uploaded size does not match the declared size.")
    sha = hashlib.sha256(part.read_bytes()).hexdigest() if st["size"] <= 64 * 1024**2 else None
    # Re-resolve right before the move to narrow the check/use window.
    _, target = resolve_in_root(ctx, p.root, p.path)
    if os.path.lexists(target) and (is_link(target) or target.is_dir()):
        raise OpError("bad_target", "Target is a directory or symbolic link.")
    shutil.move(str(part), target)
    os.chmod(target, 0o644)
    meta.unlink(missing_ok=True)
    return {"complete": True, "path": rel, "size": st["size"], "sha256": sha, "bytes_added": st["size"]}


class MkdirP(Site):
    path: str = Field(min_length=1, max_length=4096)
    parents: bool = False


@operation("files.mkdir", MkdirP, timeout=15)
def mkdir(p: MkdirP, ctx) -> dict:
    root, target = resolve_in_root(ctx, p.root, p.path)
    if os.path.lexists(target):
        raise OpError("exists", "That name already exists.")
    _check_policy(target.name)
    if not target.parent.is_dir() and not p.parents:
        raise OpError("not_found", "Parent folder does not exist.")
    if p.parents:
        cur = root
        for part in target.relative_to(root).parts:
            cur = cur / part
            if not os.path.lexists(cur):
                os.mkdir(cur, 0o755)
            elif is_link(cur) or not cur.is_dir():
                raise OpError("symlink_blocked", "Path contains a link or file.")
    else:
        os.mkdir(target, 0o755)
    return {"path": target.relative_to(root).as_posix()}


class RenameP(Site):
    src: str = Field(min_length=1, max_length=4096)
    dst: str = Field(min_length=1, max_length=4096)
    overwrite: bool = False


@operation("files.rename", RenameP, timeout=30)
def rename(p: RenameP, ctx) -> dict:
    root, src = resolve_in_root(ctx, p.root, p.src, must_exist=True)
    _, dst = resolve_in_root(ctx, p.root, p.dst)
    if src == root or dst == root:
        raise OpError("bad_target", "The site root cannot be moved.")
    _check_policy(dst.name)
    if dst == src or str(dst).startswith(str(src) + os.sep):
        raise OpError("bad_target", "Cannot move a folder into itself.")
    if not dst.parent.is_dir():
        raise OpError("not_found", "Destination folder does not exist.")
    if os.path.lexists(dst):
        if not p.overwrite or is_link(dst) or dst.is_dir() or src.is_dir():
            raise OpError("exists", "Destination already exists.")
    os.replace(src, dst)
    return {"src": src.relative_to(root).as_posix(), "dst": dst.relative_to(root).as_posix()}


# ---------------------------------------------------------------- trash
class DeleteP(Site):
    path: str = Field(min_length=1, max_length=4096)
    confirm: Literal[True]


def _trash(root: Path) -> Path:
    t = root / TRASH_DIR
    if os.path.lexists(t) and (is_link(t) or not t.is_dir()):
        raise OpError("symlink_blocked", "Trash location is not a directory.")
    t.mkdir(exist_ok=True, mode=0o700)
    return t


@operation("files.delete", DeleteP, timeout=60, destructive=True)
def delete(p: DeleteP, ctx) -> dict:
    """Deletion is a move to a per-site trash; nothing is destroyed until files.purge."""
    root, target = resolve_in_root(ctx, p.root, p.path, must_exist=True)
    if target == root:
        raise OpError("bad_target", "The site root cannot be deleted.")
    size, _ = tree_size(target)
    tid = uuid.uuid4().hex
    trash = _trash(root)
    (trash / tid).mkdir(mode=0o700)
    os.replace(target, trash / tid / target.name)
    (trash / f"{tid}.json").write_text(json.dumps({"id": tid, "path": target.relative_to(root).as_posix(), "name": target.name, "size": size, "deleted_at": _iso(time.time()), "type": "dir" if (trash / tid / target.name).is_dir() else "file"}))
    return {"trash_id": tid, "path": target.relative_to(root).as_posix(), "size": size, "recoverable": True}


class TrashListP(Site):
    pass


def _trash_entries(root: Path) -> list[dict]:
    t = root / TRASH_DIR
    out = []
    if t.is_dir() and not is_link(t):
        for f in sorted(t.glob("*.json")):
            try:
                out.append(json.loads(f.read_text()))
            except ValueError:
                continue
    return sorted(out, key=lambda x: x.get("deleted_at", ""), reverse=True)


@operation("files.trash_list", TrashListP, timeout=15)
def trash_list(p: TrashListP, ctx) -> dict:
    return {"items": _trash_entries(site_root(ctx, p.root))}


_TID = r"^[0-9a-f]{32}$"


class RestoreP(Site):
    trash_id: str = Field(pattern=_TID)
    new_path: str | None = Field(None, max_length=4096)


@operation("files.restore", RestoreP, timeout=60)
def restore(p: RestoreP, ctx) -> dict:
    root = site_root(ctx, p.root)
    meta_f = root / TRASH_DIR / f"{p.trash_id}.json"
    if not meta_f.is_file() or is_link(meta_f):
        raise OpError("not_found", "Trash item not found.")
    meta = json.loads(meta_f.read_text())
    _, dest = resolve_in_root(ctx, p.root, p.new_path or meta["path"])
    if os.path.lexists(dest):
        raise OpError("exists", "Something already exists at the original location; choose a new path.")
    if not dest.parent.is_dir():
        raise OpError("not_found", "Original folder no longer exists; choose a new path.")
    held = root / TRASH_DIR / p.trash_id / meta["name"]
    if not os.path.lexists(held):
        raise OpError("not_found", "Trashed data is missing.")
    os.replace(held, dest)
    shutil.rmtree(root / TRASH_DIR / p.trash_id, ignore_errors=True)
    meta_f.unlink()
    return {"path": dest.relative_to(root).as_posix(), "bytes_restored": meta.get("size", 0)}


class PurgeP(Site):
    trash_id: str | None = Field(None, pattern=_TID)
    empty: bool = False
    confirm: Literal[True]


@operation("files.purge", PurgeP, timeout=300, destructive=True)
def purge(p: PurgeP, ctx) -> dict:
    if bool(p.trash_id) == bool(p.empty):
        raise OpError("bad_request", "Specify exactly one of trash_id or empty.")
    root = site_root(ctx, p.root)
    ids = [e["id"] for e in _trash_entries(root)] if p.empty else [p.trash_id]
    freed = n = 0
    for tid in ids:
        d, m = root / TRASH_DIR / tid, root / TRASH_DIR / f"{tid}.json"
        if not m.is_file() and not d.exists():
            if p.trash_id:
                raise OpError("not_found", "Trash item not found.")
            continue
        if d.exists() and (is_link(d) or not d.is_dir()):
            raise OpError("symlink_blocked", "Unexpected trash layout.")
        freed += tree_size(d)[0]
        shutil.rmtree(d, ignore_errors=True)
        m.unlink(missing_ok=True)
        n += 1
    return {"purged": n, "freed_bytes": freed}


# ---------------------------------------------------------------- archives
class ArchiveP(Site):
    paths: list[str] = Field(min_length=1, max_length=200)
    dest: str = Field(min_length=1, max_length=4096)
    format: Literal["zip", "tar.gz"] = "zip"
    max_bytes: int = Field(1024**3, ge=1, le=8 * 1024**3)


@operation("files.archive", ArchiveP, timeout=600)
def archive(p: ArchiveP, ctx) -> dict:
    root, dest = resolve_in_root(ctx, p.root, p.dest)
    want = ".zip" if p.format == "zip" else ".tar.gz"
    if not dest.name.lower().endswith(want):
        raise OpError("bad_target", f"Destination must end with {want}.")
    if os.path.lexists(dest):
        raise OpError("exists", "Destination already exists.")
    if not dest.parent.is_dir():
        raise OpError("not_found", "Destination folder does not exist.")
    sources = [resolve_in_root(ctx, p.root, s, must_exist=True)[1] for s in p.paths]
    if any(s == root for s in sources):
        raise OpError("bad_target", "Archive individual items, not the site root.")
    size = sum(tree_size(s)[0] for s in sources)
    if size > p.max_bytes:
        raise OpError("archive_too_large", "Selection exceeds the archive size limit.")
    _free_ok(dest.parent, size)
    tmp = dest.with_name(dest.name + ".part")
    skipped = count = 0

    def walk(src: Path):
        nonlocal skipped
        yield src
        if src.is_dir() and not is_link(src):
            for dp, dn, fn in os.walk(src, followlinks=False):
                dn[:] = [d for d in dn if not is_link(Path(dp, d)) and not (Path(dp) == root and d == TRASH_DIR)]
                skipped += sum(1 for d in os.listdir(dp) if is_link(Path(dp, d)))
                for d in dn:
                    yield Path(dp, d)
                for f in fn:
                    if not is_link(Path(dp, f)):
                        yield Path(dp, f)

    try:
        if p.format == "zip":
            with zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED) as z:
                for s in sources:
                    for f in walk(s):
                        if f.resolve() in (dest.resolve(), tmp.resolve()):
                            continue
                        if is_link(f):
                            skipped += 1
                            continue
                        z.write(f, f.relative_to(s.parent).as_posix())
                        count += 1
        else:
            with tarfile.open(tmp, "w:gz") as t:
                for s in sources:
                    for f in walk(s):
                        if f.resolve() in (dest.resolve(), tmp.resolve()):
                            continue
                        if is_link(f):
                            skipped += 1
                            continue
                        t.add(f, f.relative_to(s.parent).as_posix(), recursive=False)
                        count += 1
        os.replace(tmp, dest)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise
    sz = os.lstat(dest).st_size
    return {"path": dest.relative_to(root).as_posix(), "entries": count, "size": sz, "bytes_added": sz, "symlinks_skipped": skipped}


class ExtractP(Site):
    archive: str = Field(min_length=1, max_length=4096)
    dest_dir: str = Field("", max_length=4096)
    overwrite: bool = False
    max_total_bytes: int = Field(1024**3, ge=1, le=8 * 1024**3)
    max_file_bytes: int = Field(512 * 1024**2, ge=1, le=8 * 1024**3)
    max_files: int = Field(10_000, ge=1, le=100_000)
    max_ratio: float = Field(100.0, ge=1, le=1000)


@operation("files.extract", ExtractP, timeout=600)
def extract(p: ExtractP, ctx) -> dict:
    root, arc = resolve_in_root(ctx, p.root, p.archive, must_exist=True)
    if is_link(arc) or not arc.is_file():
        raise OpError("bad_archive", "Archive must be a regular file.")
    _, dest = resolve_in_root(ctx, p.root, p.dest_dir)
    if dest.exists() and (is_link(dest) or not dest.is_dir()):
        raise OpError("bad_target", "Destination is not a directory.")
    if not dest.exists():
        if not dest.parent.is_dir():
            raise OpError("not_found", "Destination parent does not exist.")
        os.mkdir(dest, 0o755)
    _free_ok(dest, min(p.max_total_bytes, os.path.getsize(arc) * int(p.max_ratio)))
    r = extract_any(arc, dest, Limits(p.max_total_bytes, p.max_file_bytes, p.max_files, p.max_ratio), p.overwrite)
    return {"dest": dest.relative_to(root).as_posix() if dest != root else "", "files": r["files"], "bytes": r["bytes"], "bytes_added": r["bytes"]}


# ---------------------------------------------------------------- search
class SearchP(Site):
    path: str = Field("", max_length=4096)
    query: str = Field(min_length=1, max_length=100)
    limit: int = Field(50, ge=1, le=200)
    offset: int = Field(0, ge=0)
    max_scan: int = Field(50_000, ge=100, le=500_000)

    @field_validator("query")
    @classmethod
    def _q(cls, v: str) -> str:
        if re.search(r"[\x00-\x1f/\\]", v):
            raise ValueError("invalid query")
        return v


@operation("files.search", SearchP, timeout=60)
def search(p: SearchP, ctx) -> dict:
    root, base = resolve_in_root(ctx, p.root, p.path, must_exist=True)
    if is_link(base) or not base.is_dir():
        raise OpError("not_a_directory", "Not a directory.")
    q, hits, scanned, truncated = p.query.lower(), [], 0, False
    for dp, dn, fn in os.walk(base, followlinks=False):
        dn[:] = [d for d in dn if not (Path(dp) == root and d == TRASH_DIR) and not is_link(Path(dp, d))]
        for n in dn + fn:
            scanned += 1
            if scanned > p.max_scan:
                truncated = True
                break
            if q in n.lower():
                try:
                    hits.append(_entry(Path(dp, n), root))
                except OSError:
                    pass
        if truncated:
            break
    hits.sort(key=lambda x: x["path"])
    return {"query": p.query, "total": len(hits), "offset": p.offset, "entries": hits[p.offset : p.offset + p.limit], "scan_truncated": truncated}
