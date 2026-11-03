"""Service status checks without privileges or shell: binary presence via PATH, liveness via the process table.

States: running | stopped | not_configured. A service that is not installed is `not_configured`, never failed.
"""
import os
import re
import shutil
from typing import Any

import psutil

SERVICES: dict[str, dict[str, Any]] = {
    "nginx": {"bins": ["nginx"], "procs": ["nginx"]},
    "php-fpm": {"bins": ["php-fpm"], "bin_pattern": r"^php-fpm[0-9.]*$", "procs": [r"^php-fpm[0-9.]*$"]},
    "mariadb": {"bins": ["mariadbd", "mysqld"], "procs": ["mariadbd", "mysqld"]},
    "redis": {"bins": ["redis-server"], "procs": ["redis-server"]},
    "pm2": {"bins": ["pm2"], "procs": [r"^pm2$", r"^PM2 "]},
}
EXTRA_PATH = ["/usr/sbin", "/usr/local/sbin", "/sbin", "/usr/local/bin"]


def _search_path() -> str:
    parts = os.environ.get("PATH", "").split(os.pathsep) + EXTRA_PATH
    return os.pathsep.join(dict.fromkeys(p for p in parts if p))


def _installed(spec: dict) -> bool:
    path = _search_path()
    if any(shutil.which(b, path=path) for b in spec["bins"]):
        return True
    pat = spec.get("bin_pattern")
    if pat:
        rx = re.compile(pat)
        for d in path.split(os.pathsep):
            try:
                if any(rx.match(n) for n in os.listdir(d)):
                    return True
            except OSError:
                continue
    return False


def _running_names() -> list[str]:
    names = []
    for p in psutil.process_iter(["name"]):
        try:
            n = p.info.get("name")
            if n:
                names.append(n)
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue
    return names


def check_services(names: list[str] | None = None) -> list[dict]:
    running = _running_names()
    out = []
    for name, spec in SERVICES.items():
        if names and name not in names:
            continue
        alive = any(re.search(p, n) for p in spec["procs"] for n in running)
        if alive:
            out.append({"name": name, "state": "running", "detail": None})
        elif _installed(spec):
            out.append({"name": name, "state": "stopped", "detail": "Installed but no running process found."})
        else:
            out.append({"name": name, "state": "not_configured", "detail": "Not installed on this host."})
    return out
