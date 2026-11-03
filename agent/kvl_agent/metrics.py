"""Host metrics via psutil. A measurement that cannot be taken is None (never 0)."""
import os
import platform
import threading
import time
from typing import Any

import psutil

PSEUDO_FS = {"tmpfs", "devtmpfs", "squashfs", "overlay", "proc", "sysfs", "cgroup", "cgroup2", "efivarfs", "ramfs", "autofs", "devpts", "fuse.portal", "nsfs", "tracefs", "securityfs", "debugfs"}


class MetricsCollector:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._prev_net: tuple[float, dict[str, Any]] | None = None
        self._primed_at = time.monotonic()
        psutil.cpu_percent(None)  # prime: the first non-blocking call is meaningless

    def _cpu(self) -> float | None:
        try:
            if time.monotonic() - self._primed_at < 0.3:
                return psutil.cpu_percent(interval=0.3)
            return psutil.cpu_percent(None)
        except Exception:  # noqa: BLE001
            return None

    def _disks(self) -> list[dict]:
        out, seen = [], set()
        for p in psutil.disk_partitions(all=False):
            if p.fstype in PSEUDO_FS or p.device in seen or not p.fstype:
                continue
            seen.add(p.device)
            try:
                u = psutil.disk_usage(p.mountpoint)
            except OSError:
                continue
            out.append({"mount": p.mountpoint[:200], "fstype": p.fstype[:40], "total": u.total, "used": u.used, "free": u.free, "percent": round(u.percent, 2)})
            if len(out) >= 20:
                break
        return out

    def _cores(self) -> list[float] | None:
        try:
            return [round(c, 1) for c in psutil.cpu_percent(None, percpu=True)[:256]]
        except Exception:  # noqa: BLE001
            return None

    def _top_processes(self, n: int = 8) -> list[dict] | None:
        """Heaviest processes by RSS. Name and pid only: command lines can carry secrets and are never reported."""
        rows = []
        try:
            for p in psutil.process_iter(["pid", "name", "memory_info"]):
                mi = p.info.get("memory_info")
                if mi:
                    rows.append((mi.rss, p))
            rows.sort(key=lambda r: r[0], reverse=True)
            out = []
            for rss, p in rows[:n]:
                try:
                    cpu = p.cpu_percent(None)
                except (psutil.NoSuchProcess, psutil.AccessDenied):
                    cpu = None
                out.append({"pid": p.info["pid"], "name": str(p.info.get("name") or "?")[:60], "rss_bytes": rss, "cpu_percent": cpu})
            return out
        except Exception:  # noqa: BLE001
            return None

    def _network(self) -> dict:
        now = time.monotonic()
        counters = psutil.net_io_counters(pernic=True)
        stats = psutil.net_if_stats()
        prev, self._prev_net = self._prev_net, (now, counters)
        ifaces, rx_tot, tx_tot, have_rate = [], 0.0, 0.0, False
        for name, c in counters.items():
            st = stats.get(name)
            rx = tx = None
            if prev and name in prev[1] and now > prev[0]:
                dt = now - prev[0]
                p = prev[1][name]
                if c.bytes_recv >= p.bytes_recv and c.bytes_sent >= p.bytes_sent:  # counter reset -> unknown
                    rx, tx = (c.bytes_recv - p.bytes_recv) / dt, (c.bytes_sent - p.bytes_sent) / dt
            if rx is not None and name != "lo" and not name.startswith("lo"):
                rx_tot, tx_tot, have_rate = rx_tot + rx, tx_tot + tx, True
            ifaces.append({"name": name[:40], "is_up": bool(st.isup) if st else None, "speed_mbps": (st.speed or None) if st else None,
                           "rx_bytes_per_s": None if rx is None else round(rx, 1), "tx_bytes_per_s": None if tx is None else round(tx, 1)})
        return {"rx_bytes_per_s": round(rx_tot, 1) if have_rate else None, "tx_bytes_per_s": round(tx_tot, 1) if have_rate else None, "interfaces": ifaces[:32]}

    def collect(self) -> dict:
        with self._lock:
            m: dict[str, Any] = {"cpu_percent": self._cpu(), "cpu_count": psutil.cpu_count(logical=True)}
            try:
                l1, l5, l15 = os.getloadavg()
                m["load"] = {"load1": l1, "load5": l5, "load15": l15}
            except (OSError, AttributeError):
                m["load"] = None
            vm = psutil.virtual_memory()
            m["memory"] = {"total": vm.total, "used": vm.total - vm.available, "available": vm.available}
            sw = psutil.swap_memory()
            m["swap"] = {"total": sw.total, "used": sw.used, "available": sw.free}
            m["cpu_cores"] = self._cores()
            m["processes"] = self._top_processes()
            m["disks"] = self._disks()
            try:
                m["network"] = self._network()
            except Exception:  # noqa: BLE001
                m["network"] = None
            m["uptime_seconds"] = int(time.time() - psutil.boot_time())
            return m


def os_info() -> dict[str, str]:
    return {"system": platform.system(), "release": platform.release()[:60], "machine": platform.machine(), "python": platform.python_version()}
