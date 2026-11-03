"""Measure monitoring API / database load against a TEST database (refuses anything else).

    TEST_DATABASE_URL=postgresql+psycopg://.../kvl_test_x python scripts/measure_monitoring_load.py [servers] [days]

Seeds synthetic samples (measurement only; never run against a real database), then times heartbeat ingestion and
the read endpoints. Results are documented in docs/phase-reports/PHASE-05.md.
"""
import os
import statistics
import sys
import time
from datetime import UTC, datetime, timedelta

url = os.environ.get("TEST_DATABASE_URL", "")
if "test" not in url.rsplit("/", 1)[-1]:
    sys.exit("refusing to run: TEST_DATABASE_URL must point at a database whose name contains 'test'")
os.environ["ENV"], os.environ["DATABASE_URL"] = "test", url

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import func, select, text  # noqa: E402

import app.models  # noqa: E402,F401
from app.core.db import Base, SessionLocal, engine  # noqa: E402
from app.core.security import hash_password  # noqa: E402
from app.main import app  # noqa: E402
from app.models.identity import Membership, Role, User  # noqa: E402
from app.models.infra import Server  # noqa: E402
from app.models.monitoring import MetricSample  # noqa: E402
from app.seed import seed  # noqa: E402
from app.services import monitoring as mon  # noqa: E402


def pct(xs, p):
    xs = sorted(xs)
    return xs[min(len(xs) - 1, int(len(xs) * p))]


def timed(fn, n=30):
    ts = []
    for _ in range(n):
        t = time.perf_counter()
        fn()
        ts.append((time.perf_counter() - t) * 1000)
    return f"p50 {statistics.median(ts):7.1f} ms  p95 {pct(ts, .95):7.1f} ms  (n={n})"


def main(n_servers: int, days: int) -> None:
    Base.metadata.create_all(engine)
    with SessionLocal() as db:
        seed(db)
        if not db.scalar(select(User).where(User.email == "load@kvl.example.com")):
            u = User(email="load@kvl.example.com", name="Load", password_hash=hash_password("Correct-Horse-9"), status="active", email_verified_at=datetime.now(UTC))
            db.add(u)
            db.flush()
            db.add(Membership(user_id=u.id, role_id=db.scalar(select(Role.id).where(Role.name == "super_admin"))))
        db.execute(text("delete from servers where name like 'load-%'"))
        db.commit()
        now = datetime.now(UTC).replace(microsecond=0)
        ids = []
        for i in range(n_servers):
            s = Server(name=f"load-{i}", status="registered", last_heartbeat_at=now)
            db.add(s)
            db.flush()
            ids.append(s.id)
        n_raw = days * 24 * 120  # 30 s heartbeat
        t0 = time.perf_counter()
        for sid in ids:
            rows = [
                dict(server_id=sid, resolution="raw", ts=now - timedelta(seconds=30 * k), sample_count=1, cpu_percent=(k * 7) % 100, load1=1.0, mem_percent=40 + (k % 20), disk_max_percent=55.0,
                     net_rx_bps=1000.0 + k, net_tx_bps=500.0, uptime_seconds=k, mem_total=8 * 2**30, mem_used=3 * 2**30)
                for k in range(n_raw)
            ]
            for j in range(0, len(rows), 5000):
                db.execute(MetricSample.__table__.insert(), rows[j : j + 5000])
        db.commit()
        print(f"seeded {n_servers} server(s) x {n_raw} raw samples in {time.perf_counter() - t0:.1f}s")
        total = db.scalar(select(func.count()).select_from(MetricSample))
        size = db.scalar(text("select pg_size_pretty(pg_total_relation_size('metric_samples'))"))
        print(f"metric_samples rows={total} size={size} (~{db.scalar(text('select pg_total_relation_size(' + chr(39) + 'metric_samples' + chr(39) + ')')) / max(total, 1):.0f} bytes/row incl. indexes)")
        sid = ids[0]

        payload = {"metrics": {"cpu_percent": 20.0, "memory": {"total": 100, "used": 50}, "disks": [{"mount": "/", "percent": 40.0}]}, "services": [{"name": "nginx", "state": "running"}]}
        print("heartbeat ingest (service + commit)      ", timed(lambda: (mon.ingest_heartbeat(db, db.get(Server, sid), payload, datetime.now(UTC) + timedelta(milliseconds=time.perf_counter_ns() % 10**6)), db.commit()), 50))
        db.execute(text("delete from metric_samples where ts > now() - interval '1 second'"))
        db.commit()

    c = TestClient(app)
    r = c.post("/api/v1/auth/login", json={"email": "load@kvl.example.com", "password": "Correct-Horse-9"})
    assert r.status_code == 200, r.text
    c.headers["X-CSRF-Token"] = c.cookies.get("kvl_csrf")
    base = f"/api/v1/servers/{sid}/metrics"
    print("GET /healthz (no DB, no auth: machine baseline)", timed(lambda: c.get("/healthz"), 30))
    with SessionLocal() as db2:
        s0 = datetime.now(UTC)
        for label, span, b in (("24h/15m", timedelta(hours=24), 900), ("7d/1h", timedelta(days=7), 3600), ("90d/1d", timedelta(days=90), 86400)):
            print(f"query_series only (DB+python) {label:<8}", timed(lambda span=span, b=b: mon.query_series(db2, sid, s0 - span, s0, "Asia/Kolkata", b), 15))
    for rng in ("1h", "24h", "7d", "30d", "90d"):
        print(f"GET metrics range={rng:<4}                    ", timed(lambda rng=rng: c.get(base, params={"range": rng, "tz": "Asia/Kolkata"}), 15))
    print("GET metrics/latest                       ", timed(lambda: c.get(base + "/latest"), 30))
    print("GET monitoring/overview                  ", timed(lambda: c.get("/api/v1/monitoring/overview"), 30))
    print("GET monitoring/alerts                    ", timed(lambda: c.get("/api/v1/monitoring/alerts"), 30))
    print("GET servers/{id}/services                ", timed(lambda: c.get(f"/api/v1/servers/{sid}/services"), 30))
    with SessionLocal() as db:
        t = time.perf_counter()
        res = mon.downsample_and_prune(db, datetime.now(UTC) + timedelta(days=1))
        db.commit()
        print(f"retention run (rolls everything older than 48h): {time.perf_counter() - t:.2f}s {res}")
        db.execute(text("delete from servers where name like 'load-%'"))
        db.commit()


if __name__ == "__main__":
    main(int(sys.argv[1]) if len(sys.argv) > 1 else 1, int(sys.argv[2]) if len(sys.argv) > 2 else 3)
