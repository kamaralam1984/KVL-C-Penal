"""The agent main loop: heartbeat, poll, execute, report. Single process, unprivileged, no inbound command port."""
import logging
import signal
import socket
import threading
import time

import httpx

from kvl_agent import __version__
from kvl_agent.client import ControlPlaneClient, ControlPlaneError, UntrustedResponse
from kvl_agent.config import AgentConfig
from kvl_agent.context import OpContext
from kvl_agent.executor import execute_job
from kvl_agent.health import make_server, serve_in_thread
from kvl_agent.jsonlog import event
from kvl_agent.metrics import MetricsCollector, os_info
from kvl_agent.site_usage import SiteUsageCollector
from kvl_agent.registry import load_operations
from kvl_agent.services import check_services

log = logging.getLogger("kvl_agent")


class Agent:
    def __init__(self, cfg: AgentConfig, client: ControlPlaneClient | None = None, collector: MetricsCollector | None = None):
        self.cfg = cfg
        self.client = client or ControlPlaneClient(cfg)
        self.collector = collector or MetricsCollector()
        self.site_usage = SiteUsageCollector(cfg.data_dir)
        self.stop = threading.Event()
        self.started = time.time()
        self.authorized = True
        self.last_heartbeat_ok: float | None = None
        self.last_error: str | None = None
        self._inflight = 0
        self._lock = threading.Lock()
        self._threads: list[threading.Thread] = []
        cfg.data_dir.mkdir(parents=True, exist_ok=True)
        load_operations()
        self.ctx = OpContext(data_dir=cfg.data_dir, dry_run=cfg.dry_run, max_output=cfg.max_output)

    # ---- status
    def status(self) -> dict:
        return {
            "status": "ok" if self.authorized and not self.stop.is_set() else "degraded",
            "version": __version__,
            "uptime_seconds": int(time.time() - self.started),
            "enrolled": self.cfg.enrolled,
            "authorized": self.authorized,
            "last_heartbeat_ok_at": self.last_heartbeat_ok,
            "inflight_jobs": self._inflight,
        }

    # ---- heartbeat
    def heartbeat_payload(self) -> dict:
        try:
            services = check_services()
        except Exception:  # noqa: BLE001
            services = None
        try:
            metrics = self.collector.collect()
        except Exception:  # noqa: BLE001
            metrics = None  # missing, not zero
        try:
            sites = self.site_usage.collect()
        except Exception:  # noqa: BLE001
            sites = None
        return {
            "agent_version": __version__,
            "hostname": socket.gethostname()[:255],
            "os_info": os_info(),
            "update_status": {"current": __version__, "state": "not_configured", "detail": "Self-update is not implemented; upgrade through the package manager."},
            "metrics": metrics,
            "services": services,
            "sites": sites,
        }

    def _auth_failed(self, e: ControlPlaneError) -> None:
        if e.status in {401, 403}:
            if self.authorized:
                event(log, "authorization failed; job polling disabled (fail closed)", logging.ERROR, status=e.status, code=e.code)
            self.authorized = False

    def heartbeat_once(self) -> bool:
        try:
            self.client.heartbeat(self.heartbeat_payload())
        except ControlPlaneError as e:
            self._auth_failed(e)
            self.last_error = f"heartbeat {e.status} {e.code}"
            event(log, "heartbeat rejected", logging.WARNING, status=e.status, code=e.code)
            return False
        except (httpx.HTTPError, UntrustedResponse) as e:
            self.last_error = f"heartbeat {type(e).__name__}"
            event(log, "heartbeat failed", logging.WARNING, error=type(e).__name__)
            return False
        self.authorized = True
        self.last_heartbeat_ok = time.time()
        self.last_error = None
        return True

    # ---- jobs
    def poll_once(self) -> int:
        if not self.authorized:
            return 0
        with self._lock:
            free = self.cfg.max_concurrency - self._inflight
        if free <= 0 or self.stop.is_set():
            return 0
        try:
            jobs = self.client.poll(free)
        except ControlPlaneError as e:
            self._auth_failed(e)
            return 0
        except UntrustedResponse:
            event(log, "job list failed signature verification; ignoring (fail closed)", logging.ERROR)
            return 0
        except httpx.HTTPError as e:
            event(log, "poll failed", logging.WARNING, error=type(e).__name__)
            return 0
        for job in jobs[:free]:
            with self._lock:
                self._inflight += 1
            t = threading.Thread(target=self._run_job, args=(job,), name=f"job-{str(job.get('id'))[:8]}", daemon=False)
            self._threads = [x for x in self._threads if x.is_alive()] + [t]
            t.start()
        return len(jobs)

    def _run_job(self, job: dict) -> None:
        jid = str(job.get("id", ""))
        try:
            event(log, "job started", operation=job.get("operation"), job_id=jid)
            outcome = execute_job(job, self.ctx)
            event(log, "job finished", operation=job.get("operation"), job_id=jid, status=outcome.status, duration_ms=outcome.duration_ms)
            for attempt in range(4):
                try:
                    self.client.report(jid, outcome.payload())
                    break
                except ControlPlaneError as e:
                    event(log, "result rejected", logging.WARNING, job_id=jid, status=e.status, code=e.code)
                    break  # 4xx (e.g. job cancelled meanwhile) is final
                except (httpx.HTTPError, UntrustedResponse):
                    time.sleep(min(2**attempt, 8) if not self.stop.is_set() else 0)
        finally:
            with self._lock:
                self._inflight -= 1

    def wait_for_jobs(self, timeout: float) -> bool:
        end = time.time() + timeout
        for t in list(self._threads):
            t.join(max(0.0, end - time.time()))
        return not any(t.is_alive() for t in self._threads)

    def run_once(self) -> dict:
        """One heartbeat + one poll, waiting for any started jobs. Used by tests and `run_local.py --once`."""
        ok = self.heartbeat_once()
        n = self.poll_once()
        self.wait_for_jobs(self.cfg.shutdown_grace)
        return {"heartbeat": ok, "jobs": n}

    # ---- main loop
    def run_forever(self, install_signals: bool = True) -> None:
        if install_signals:
            for sig in (signal.SIGTERM, signal.SIGINT):
                signal.signal(sig, lambda *_: self.stop.set())
        srv = None
        try:
            srv = make_server(self.cfg.health_port, self.status)
            serve_in_thread(srv)
        except OSError as e:
            event(log, "health endpoint unavailable", logging.WARNING, error=str(e))
        event(log, "agent started", version=__version__, api_url=self.cfg.api_url)
        next_hb = next_poll = 0.0
        fails = 0
        while not self.stop.is_set():
            now = time.monotonic()
            if now >= next_hb:
                fails = 0 if self.heartbeat_once() else min(fails + 1, 6)
                next_hb = time.monotonic() + min(self.cfg.heartbeat_interval * (2**fails), 300)
            if now >= next_poll:
                self.poll_once()
                next_poll = time.monotonic() + self.cfg.poll_interval
            self.stop.wait(min(1.0, self.cfg.poll_interval))
        event(log, "shutting down; waiting for running jobs", inflight=self._inflight)
        self.wait_for_jobs(self.cfg.shutdown_grace)
        if srv:
            srv.shutdown()
            srv.server_close()
        self.client.close()
        event(log, "agent stopped")
