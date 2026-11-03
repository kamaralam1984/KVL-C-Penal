"""Per-website resource usage: disk, RAM/CPU of the processes working inside the site, and bandwidth from its nginx access log.

Everything is read-only and best effort. A figure that cannot be measured (unreadable directory, no access log yet,
a process owned by another user) is None, never 0. Process attribution is by working directory: a process counts for
a site when its cwd is inside that site's document root (or app directory).
"""
import os
import re
import threading
import time
from pathlib import Path
from typing import Any

import psutil

DISK_TTL = 600  # `du` style walks are expensive; refresh every 10 minutes
WALK_BUDGET_S = 5.0
MAX_FILES = 500_000
FIRST_READ_BYTES = 50 * 1024 * 1024
MAX_READ_PER_CYCLE = 20 * 1024 * 1024
MAX_SITES = 200
LOG_LINE = re.compile(rb'"[^"]*" (\d{3}) (\d+|-)')
ROOT_LINE = re.compile(r"^\s*root\s+([^;\s]+)\s*;", re.M)


class _Log:
    __slots__ = ("inode", "offset", "bytes_out", "requests", "e4xx", "e5xx")

    def __init__(self) -> None:
        self.inode: int | None = None
        self.offset = 0
        self.bytes_out = self.requests = self.e4xx = self.e5xx = 0


class SiteUsageCollector:
    def __init__(self, data_dir: Path) -> None:
        self.data_dir = Path(data_dir)
        self._lock = threading.Lock()
        self._disk: dict[str, tuple[float, int | None]] = {}
        self._logs: dict[str, _Log] = {}
        self._procs: dict[int, psutil.Process] = {}
        self._primed = False

    # ---------------------------------------------------------------- discovery
    def _discover(self) -> list[tuple[str, str, Path]]:
        found: list[tuple[str, str, Path]] = []
        sites = self.data_dir / "nginx" / "sites"
        if sites.is_dir():
            for conf in sorted(sites.glob("*.conf"))[:MAX_SITES]:
                try:
                    m = ROOT_LINE.search(conf.read_text(errors="replace")[:20000])
                except OSError:
                    continue
                if m:
                    found.append((conf.stem, "site", Path(m.group(1))))
        apps = self.data_dir / "apps"
        if apps.is_dir():
            for d in sorted(apps.iterdir())[: MAX_SITES - len(found)]:
                if d.is_dir():
                    found.append((d.name, "app", d))
        return found

    # ---------------------------------------------------------------- disk
    def _disk_bytes(self, key: str, root: Path) -> int | None:
        now = time.monotonic()
        cached = self._disk.get(key)
        if cached and now - cached[0] < DISK_TTL:
            return cached[1]
        total, files, deadline, ok = 0, 0, now + WALK_BUDGET_S, True
        try:
            for dirpath, _dirs, names in os.walk(root, followlinks=False):
                for n in names:
                    try:
                        total += os.lstat(os.path.join(dirpath, n)).st_size
                    except OSError:
                        pass
                    files += 1
                if files > MAX_FILES or time.monotonic() > deadline:
                    ok = False  # too big to measure in budget: keep the previous figure rather than report a partial one
                    break
        except OSError:
            ok = False
        value = total if ok and root.exists() else (cached[1] if cached else None)
        self._disk[key] = (now, value)
        return value

    # ---------------------------------------------------------------- access log
    def _read_log(self, name: str) -> _Log | None:
        path = self.data_dir / "nginx" / "logs" / f"{name}.access.log"
        try:
            st = path.stat()
        except OSError:
            return self._logs.get(name)
        state = self._logs.setdefault(name, _Log())
        if state.inode != st.st_ino or st.st_size < state.offset:  # first sight or rotated/truncated
            first = state.inode is None
            state.inode, state.offset = st.st_ino, max(0, st.st_size - FIRST_READ_BYTES) if first else 0
        try:
            with open(path, "rb") as f:
                f.seek(state.offset)
                data = f.read(min(MAX_READ_PER_CYCLE, st.st_size - state.offset))
        except OSError:
            return state
        if not data:
            return state
        cut = data.rfind(b"\n")
        if cut < 0:
            return state  # no complete line yet
        for m in LOG_LINE.finditer(data[: cut + 1]):
            status = int(m.group(1))
            state.requests += 1
            if m.group(2) != b"-":
                state.bytes_out += int(m.group(2))
            if 400 <= status < 500:
                state.e4xx += 1
            elif status >= 500:
                state.e5xx += 1
        state.offset += cut + 1
        return state

    # ---------------------------------------------------------------- processes
    def _scan_processes(self, roots: dict[str, Path]) -> dict[str, dict[str, float | int]]:
        usage: dict[str, dict[str, float | int]] = {}
        resolved = sorted(((k, str(r.resolve())) for k, r in roots.items()), key=lambda kv: -len(kv[1]))
        alive: dict[int, psutil.Process] = {}
        for pid in psutil.pids():
            p = self._procs.get(pid)
            try:
                if p is None or not p.is_running():
                    p = psutil.Process(pid)
                alive[pid] = p
                cwd = p.cwd()
                for key, root in resolved:
                    if cwd == root or cwd.startswith(root + os.sep):
                        u = usage.setdefault(key, {"rss": 0, "cpu": 0.0, "procs": 0})
                        u["rss"] += p.memory_info().rss
                        u["cpu"] += p.cpu_percent(None)  # first call per process primes the counter and returns 0.0
                        u["procs"] += 1
                        break
            except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess, OSError):
                continue
        self._procs = alive
        return usage

    # ---------------------------------------------------------------- public
    def collect(self) -> list[dict[str, Any]]:
        with self._lock:
            found = self._discover()
            if not found:
                return []
            usage = self._scan_processes({name: root for name, _k, root in found})
            primed, self._primed = self._primed, True
            out = []
            for name, kind, root in found:
                u = usage.get(name)
                log = self._read_log(name) if kind == "site" else None
                out.append({
                    "name": name[:80],
                    "kind": kind,
                    "disk_bytes": self._disk_bytes(name, root),
                    "rss_bytes": int(u["rss"]) if u else None,
                    "cpu_percent": round(min(float(u["cpu"]), 100.0 * (psutil.cpu_count() or 1)), 1) if u and primed else None,
                    "procs": int(u["procs"]) if u else None,
                    "bytes_out_total": log.bytes_out if log else None,
                    "requests_total": log.requests if log else None,
                    "errors_4xx_total": log.e4xx if log else None,
                    "errors_5xx_total": log.e5xx if log else None,
                })
            return out
