"""Periodic housekeeping. Run `python -m app.services.maintenance` every minute from cron/systemd timer
(or call `run_maintenance` from a Celery beat task). Safe to run concurrently and repeatedly."""
from sqlalchemy.orm import Session

from app.services import agents, monitoring


def run_maintenance(db: Session, *, retention: bool = True) -> dict[str, int]:
    out = {
        "jobs_timed_out": agents.sweep_timed_out_jobs(db),
        "heartbeat_alerts_changed": monitoring.sweep_heartbeats(db),
        "nonces_purged": agents.purge_nonces(db),
    }
    if retention:
        out.update(monitoring.downsample_and_prune(db))
    db.commit()
    return out


if __name__ == "__main__":  # pragma: no cover
    from app.core.db import SessionLocal

    with SessionLocal() as s:
        print(run_maintenance(s))
