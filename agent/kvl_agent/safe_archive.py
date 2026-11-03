"""Archive creation helpers and hostile-archive-safe extraction (zip and tar.gz).

Extraction validates every member BEFORE writing anything (names, types, counts, declared sizes, compression ratio),
then streams with byte counters so lying headers cannot exceed the limits. Symlink, hardlink, device and fifo members
are rejected outright. Permissions from the archive are ignored (files 0644, directories 0755).
"""
import os
import re
import stat
import tarfile
import zipfile
from dataclasses import dataclass
from pathlib import Path

from kvl_agent.safe_paths import OpError

CHUNK = 1024 * 1024


@dataclass
class Limits:
    max_total: int = 1024**3
    max_file: int = 512 * 1024**2
    max_files: int = 10_000
    max_ratio: float = 100.0


def member_parts(name: str) -> list[str]:
    if not name or "\x00" in name or "\\" in name:
        raise OpError("unsafe_archive_path", "Archive member has an invalid name.")
    if name.startswith("/") or re.match(r"^[A-Za-z]:", name):
        raise OpError("unsafe_archive_path", "Archive member uses an absolute path.")
    parts = [p for p in name.split("/") if p not in ("", ".")]
    if any(p == ".." for p in parts):
        raise OpError("unsafe_archive_path", "Archive member escapes the destination.")
    if any(len(p) > 255 for p in parts):
        raise OpError("unsafe_archive_path", "Archive member name too long.")
    return parts


class _Writer:
    def __init__(self, dest: Path, limits: Limits, overwrite: bool):
        self.dest, self.limits, self.overwrite = dest, limits, overwrite
        self.total = 0
        self.files = 0
        self.created_files: list[Path] = []
        self.created_dirs: list[Path] = []

    def _dir(self, parts: list[str]) -> Path:
        cur = self.dest
        for p in parts:
            cur = cur / p
            try:
                st = os.lstat(cur)
            except FileNotFoundError:
                os.mkdir(cur, 0o755)
                self.created_dirs.append(cur)
                continue
            if stat.S_ISLNK(st.st_mode) or not stat.S_ISDIR(st.st_mode):
                raise OpError("unsafe_archive_path", "Extraction would pass through a symlink or non-directory.")
        return cur

    def mkdir(self, parts: list[str]) -> None:
        self._dir(parts)

    def write(self, parts: list[str], src, declared: int | None) -> None:
        parent = self._dir(parts[:-1])
        target = parent / parts[-1]
        flags = os.O_WRONLY | os.O_CREAT | os.O_NOFOLLOW
        exists = os.path.lexists(target)
        if exists:
            if not self.overwrite:
                raise OpError("exists", f"'{'/'.join(parts)}' already exists.")
            if stat.S_ISLNK(os.lstat(target).st_mode) or os.path.isdir(target):
                raise OpError("unsafe_archive_path", "Refusing to overwrite a symlink or directory.")
            flags |= os.O_TRUNC
        else:
            flags |= os.O_EXCL
        fd = os.open(target, flags, 0o644)
        if not exists:
            self.created_files.append(target)
        size = 0
        with os.fdopen(fd, "wb") as out:
            while True:
                b = src.read(CHUNK)
                if not b:
                    break
                size += len(b)
                self.total += len(b)
                if size > self.limits.max_file or self.total > self.limits.max_total or (declared is not None and size > declared):
                    raise OpError("archive_too_large", "Archive exceeds extraction size limits.")
                out.write(b)
        self.files += 1

    def rollback(self) -> None:
        for f in reversed(self.created_files):
            try:
                os.unlink(f)
            except OSError:
                pass
        for d in reversed(self.created_dirs):
            try:
                os.rmdir(d)
            except OSError:
                pass


def _check_totals(count: int, total: int, archive_size: int, limits: Limits) -> None:
    if count > limits.max_files:
        raise OpError("archive_too_many_files", f"Archive has more than {limits.max_files} entries.")
    if total > limits.max_total:
        raise OpError("archive_too_large", "Archive would expand beyond the size limit.")
    if total > 1024 * 1024 and total / max(archive_size, 1) > limits.max_ratio:
        raise OpError("archive_ratio", "Archive compression ratio is suspicious (possible zip bomb).")


def extract_zip(path: Path, dest: Path, limits: Limits, overwrite: bool = False) -> dict:
    try:
        zf = zipfile.ZipFile(path)
    except (zipfile.BadZipFile, OSError):
        raise OpError("bad_archive", "Not a valid zip archive.") from None
    with zf:
        infos = zf.infolist()
        plan, total = [], 0
        for i in infos:
            parts = member_parts(i.filename)
            mode = (i.external_attr >> 16) & 0o170000
            if mode in (stat.S_IFLNK, stat.S_IFCHR, stat.S_IFBLK, stat.S_IFIFO, stat.S_IFSOCK):
                raise OpError("unsafe_archive_member", "Archive contains links or special files.")
            if i.flag_bits & 0x1:
                raise OpError("bad_archive", "Encrypted archives are not supported.")
            if not parts:
                continue
            if i.file_size > limits.max_file:
                raise OpError("archive_too_large", "A member exceeds the per-file limit.")
            if i.file_size > 1024 * 1024 and i.file_size / max(i.compress_size, 1) > limits.max_ratio:
                raise OpError("archive_ratio", "Member compression ratio is suspicious (possible zip bomb).")
            total += i.file_size
            plan.append((i, parts))
        _check_totals(len(plan), total, os.path.getsize(path), limits)
        w = _Writer(dest, limits, overwrite)
        try:
            for i, parts in plan:
                if i.is_dir():
                    w.mkdir(parts)
                else:
                    with zf.open(i) as src:
                        w.write(parts, src, i.file_size)
        except BaseException:
            w.rollback()
            raise
    return {"files": w.files, "bytes": w.total}


def extract_tar(path: Path, dest: Path, limits: Limits, overwrite: bool = False) -> dict:
    try:
        tf = tarfile.open(path, "r:*")
    except (tarfile.TarError, OSError, EOFError):
        raise OpError("bad_archive", "Not a valid tar archive.") from None
    with tf:
        plan, total = [], 0
        try:
            for m in tf:
                parts = member_parts(m.name)
                if m.issym() or m.islnk() or m.ischr() or m.isblk() or m.isfifo() or not (m.isreg() or m.isdir()):
                    raise OpError("unsafe_archive_member", "Archive contains links or special files.")
                if not parts:
                    continue
                if m.size > limits.max_file:
                    raise OpError("archive_too_large", "A member exceeds the per-file limit.")
                total += m.size
                if len(plan) + 1 > limits.max_files or total > limits.max_total:
                    _check_totals(len(plan) + 1, total, os.path.getsize(path), limits)
                plan.append((m, parts))
        except (tarfile.TarError, EOFError, OSError):
            raise OpError("bad_archive", "Archive is corrupt.") from None
        _check_totals(len(plan), total, os.path.getsize(path), limits)
        w = _Writer(dest, limits, overwrite)
        try:
            for m, parts in plan:
                if m.isdir():
                    w.mkdir(parts)
                else:
                    src = tf.extractfile(m)
                    if src is None:
                        raise OpError("bad_archive", "Unreadable member.")
                    w.write(parts, src, m.size)
        except (tarfile.TarError, EOFError):
            w.rollback()
            raise OpError("bad_archive", "Archive is corrupt.") from None
        except BaseException:
            w.rollback()
            raise
    return {"files": w.files, "bytes": w.total}


def extract_any(path: Path, dest: Path, limits: Limits, overwrite: bool = False) -> dict:
    name = path.name.lower()
    if name.endswith(".zip"):
        return extract_zip(path, dest, limits, overwrite)
    if name.endswith((".tar.gz", ".tgz", ".tar")):
        return extract_tar(path, dest, limits, overwrite)
    raise OpError("unsupported_archive", "Only .zip, .tar.gz, .tgz and .tar archives are supported.")
