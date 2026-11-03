"""Tenant-root path resolution shared by file-manager and backup operations.

Every path that reaches the filesystem goes through `resolve_in_root`. It rejects traversal in raw and (repeatedly)
URL-decoded forms, NUL bytes, backslashes and control characters, then walks the path component by component with
lstat and refuses any symlink on the way. Callers additionally open files with O_NOFOLLOW.
"""
import os
import re
import stat
from pathlib import Path
from urllib.parse import unquote

from kvl_agent.context import OpContext

TRASH_DIR = ".kvl-trash"
_CTRL = re.compile(r"[\x00-\x1f\x7f]")


class OpError(Exception):
    """Operation failure with a stable machine code. The message never contains secrets."""

    def __init__(self, code: str, message: str):
        super().__init__(f"{code}: {message}")
        self.code = code
        self.message = message


def split_rel(rel: str, *, allow_internal: bool = False) -> list[str]:
    """Validate a user-supplied relative path and return its components. Raises OpError(bad_path|path_traversal)."""
    if not isinstance(rel, str) or len(rel) > 4096:
        raise OpError("bad_path", "Path is invalid or too long.")
    forms = [rel]
    cur = rel
    for _ in range(4):
        nxt = unquote(cur)
        if nxt == cur:
            break
        forms.append(nxt)
        cur = nxt
    else:
        raise OpError("path_traversal", "Path uses excessive encoding.")
    for f in forms:
        if "\x00" in f or "\\" in f or _CTRL.search(f):
            raise OpError("bad_path", "Path contains forbidden characters.")
        if f.startswith("/") or re.match(r"^[A-Za-z]:", f):
            raise OpError("path_traversal", "Absolute paths are not allowed.")
        if any(p == ".." for p in f.split("/")):
            raise OpError("path_traversal", "Path traversal is not allowed.")
    parts = [p for p in rel.split("/") if p not in ("", ".")]
    if any(len(p) > 255 for p in parts):
        raise OpError("bad_path", "Path component too long.")
    if not allow_internal and parts and parts[0] == TRASH_DIR:
        raise OpError("forbidden_path", "That location is reserved.")
    return parts


def site_root(ctx: OpContext, root_rel: str) -> Path:
    parts = split_rel(root_rel, allow_internal=True)
    if not parts:
        raise OpError("bad_root", "Site root is required.")
    try:
        root = ctx.path(*parts)
    except Exception:
        raise OpError("path_traversal", "Site root escapes the agent sandbox.") from None
    if not root.is_dir():
        raise OpError("root_missing", "Site root does not exist.")
    return root


def resolve_in_root(ctx: OpContext, root_rel: str, rel: str, *, must_exist: bool = False, allow_internal: bool = False) -> tuple[Path, Path]:
    """Return (root, target). The target is root joined with validated components; no component may be a symlink
    (except the final one, which callers treat as the link itself and never follow)."""
    root = site_root(ctx, root_rel)
    parts = split_rel(rel, allow_internal=allow_internal)
    cur = root
    for i, part in enumerate(parts):
        cur = cur / part
        try:
            st = os.lstat(cur)
        except FileNotFoundError:
            if must_exist:
                raise OpError("not_found", "No such file or directory.") from None
            # Remaining components do not exist yet; nothing below can be a symlink.
            cur = cur.joinpath(*parts[i + 1:])
            break
        last = i == len(parts) - 1
        if stat.S_ISLNK(st.st_mode) and not last:
            raise OpError("symlink_blocked", "Paths through symbolic links are not allowed.")
        if not last and not stat.S_ISDIR(st.st_mode):
            raise OpError("not_a_directory", "A path component is not a directory.")
    if cur == root:
        return root, root
    parent = os.path.realpath(cur.parent)
    rr = str(root)
    if parent != rr and not parent.startswith(rr + os.sep):
        raise OpError("path_traversal", "Resolved path leaves the site root.")
    return root, cur


def is_link(p: Path) -> bool:
    return stat.S_ISLNK(os.lstat(p).st_mode)


def tree_size(p: Path) -> tuple[int, int]:
    """(bytes, files) beneath p without following symlinks."""
    total = files = 0
    try:
        st = os.lstat(p)
    except FileNotFoundError:
        return 0, 0
    if not stat.S_ISDIR(st.st_mode):
        return st.st_size, 1
    for dp, _dn, fn in os.walk(p, followlinks=False):
        for f in fn:
            try:
                total += os.lstat(os.path.join(dp, f)).st_size
                files += 1
            except OSError:
                pass
    return total, files
