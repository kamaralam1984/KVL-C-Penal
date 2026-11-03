"""One periodic runner for every background sweep. Run each minute via the systemd timer in ops/systemd.

Each task runs in its own session and transaction, so one failing task never blocks or rolls back the others.
Safe to run concurrently and repeatedly (all underlying sweeps are idempotent).
"""
import logging
import time
from collections.abc import Callable
from typing import Any

from sqlalchemy.orm import Session

from app.core.db import SessionLocal

log = logging.getLogger("kvl.scheduler")


def _maintenance(db: Session) -> Any:
    from app.services.maintenance import run_maintenance

    return run_maintenance(db)


def _billing(db: Session) -> Any:
    from app.services.billing import run_lifecycle

    return run_lifecycle(db)


def _automation(db: Session) -> Any:
    from app.services.automation import dispatch_due, run_scheduler

    return {"scheduler": run_scheduler(db), "dispatch": dispatch_due(db)}


def _ssl(db: Session) -> Any:
    from app.services.ssl import sweep_renewals

    return sweep_renewals(db)


def _backups(db: Session) -> Any:
    from app.services.backups import sweep_schedules

    return sweep_schedules(db)


def _support(db: Session) -> Any:
    from app.services.support import run_sla_escalation

    return run_sla_escalation(db)


def _webhooks(db: Session) -> Any:
    from app.services.webhooks import deliver_due

    return deliver_due(db)


TASKS: dict[str, Callable[[Session], Any]] = {
    "maintenance": _maintenance,
    "billing": _billing,
    "automation": _automation,
    "ssl": _ssl,
    "backups": _backups,
    "support": _support,
    "webhooks": _webhooks,
}

# Slow sweeps don't need to run every minute.
EVERY_MINUTES = {"maintenance": 1, "automation": 1, "backups": 1, "billing": 15, "ssl": 60, "support": 5, "webhooks": 1}


def run_all(minute: int | None = None) -> dict[str, Any]:
    minute = int(time.time() // 60) if minute is None else minute
    out: dict[str, Any] = {}
    for name, fn in TASKS.items():
        if minute % EVERY_MINUTES[name]:
            continue
        with SessionLocal() as db:
            try:
                out[name] = fn(db)
                db.commit()
            except Exception as e:  # noqa: BLE001 - isolate task failures
                db.rollback()
                log.exception("scheduler task %s failed", name)
                out[name] = {"error": type(e).__name__}
    return out


if __name__ == "__main__":  # pragma: no cover
    print(run_all())
