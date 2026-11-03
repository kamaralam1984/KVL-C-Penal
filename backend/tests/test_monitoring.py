import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select

from app.models.identity import AuditEvent
from app.models.infra import Server
from app.models.monitoring import Alert, MetricSample
from app.services import monitoring as mon
from tests.agent_helpers import API, METRICS, enrolled, register, signed
from tests.conftest import login

NOW = datetime(2026, 6, 15, 12, 0, 0, tzinfo=UTC)


@pytest.fixture
def anon(make_client):
    return make_client()


@pytest.fixture
def srv(db):
    s = Server(name="imac", status="registered", registered_at=NOW)
    db.add(s)
    db.commit()
    return s


def sample(db, srv, ts, res="raw", **kw):
    db.add(MetricSample(server_id=srv.id, resolution=res, ts=ts, sample_count=kw.pop("sample_count", 1), **kw))
    db.commit()


def hb(db, srv, now, cpu=None, **extra):
    payload = {"metrics": ({"cpu_percent": cpu} if cpu is not None else None), **extra}
    mon.ingest_heartbeat(db, srv, payload, now)
    db.commit()


def alerts(db, **kw):
    q = select(Alert)
    for k, v in kw.items():
        q = q.where(getattr(Alert, k) == v)
    return db.scalars(q.order_by(Alert.opened_at)).all()


# ---------------------------------------------------------------- authorization
def test_tenant_users_blocked_from_monitoring(admin, client, make_client, make_user):
    sid, _ = register(client)
    make_user("cust@example.com", "customer")  # customers hold monitoring:read, but infrastructure is platform-only
    c = make_client()
    assert login(c, "cust@example.com").status_code == 200
    for p in (f"/servers/{sid}/metrics", f"/servers/{sid}/metrics/latest", f"/servers/{sid}/metrics/samples", f"/servers/{sid}/services", "/monitoring/overview", "/monitoring/alerts", f"/monitoring/thresholds?server_id={sid}"):
        assert c.get(f"{API}{p}").status_code == 403, p
    assert c.put(f"{API}/monitoring/thresholds", json={"server_id": sid, "metric": "cpu_percent", "warning": 10, "critical": 20}).status_code == 403
    assert c.post(f"{API}/monitoring/sweep").status_code == 403
    assert make_client().get(f"{API}/monitoring/overview").status_code == 401


def test_support_reads_but_cannot_write(admin, client, make_client, make_user, db, srv):
    make_user("sup@kvl.example.com", "support")
    s = make_client()
    assert login(s, "sup@kvl.example.com").status_code == 200
    assert s.get(f"{API}/monitoring/overview").status_code == 200
    assert s.put(f"{API}/monitoring/thresholds", json={"server_id": str(srv.id), "metric": "cpu_percent", "warning": 10, "critical": 20}).status_code == 403
    hb(db, srv, agents_now(), cpu=99)
    a = alerts(db)[0]
    assert s.post(f"{API}/monitoring/alerts/{a.id}/acknowledge").status_code == 403


def agents_now():
    return datetime.now(UTC)


# ---------------------------------------------------------------- availability
def test_availability_states():
    assert mon.availability(None, NOW) == "unknown"
    assert mon.availability(NOW - timedelta(seconds=5), NOW) == "connected"
    assert mon.availability(NOW - timedelta(seconds=90), NOW) == "connected"
    assert mon.availability(NOW - timedelta(seconds=91), NOW) == "stale"
    assert mon.availability(NOW - timedelta(seconds=300), NOW) == "stale"
    assert mon.availability(NOW - timedelta(seconds=301), NOW) == "disconnected"
    s = Server(name="x", status="disabled", last_heartbeat_at=NOW)
    assert mon.server_availability(s, NOW) == "disabled"


def test_overview_reports_real_states(admin, client, anon, db):
    sid, key_id, secret = enrolled(client, anon, "live")
    register(client, "never-seen")
    gone = Server(name="gone", status="registered", last_heartbeat_at=agents_now() - timedelta(hours=2))
    stale = Server(name="stale", status="registered", last_heartbeat_at=agents_now() - timedelta(seconds=150))
    db.add_all([gone, stale])
    db.commit()
    signed(anon, "/heartbeat", {"metrics": METRICS, "services": [{"name": "nginx", "state": "running"}]}, key_id, secret)
    o = client.get(f"{API}/monitoring/overview").json()
    by = {s["name"]: s for s in o["servers"]}
    assert by["live"]["availability"] == "connected" and by["live"]["cpu_percent"] == 12.5
    assert by["never-seen"]["availability"] == "unknown" and by["never-seen"]["cpu_percent"] is None
    assert by["stale"]["availability"] == "stale" and by["gone"]["availability"] == "disconnected"
    t = o["totals"]
    assert (t["servers"], t["connected"], t["stale"], t["disconnected"], t["unknown"]) == (4, 1, 1, 1, 1)
    assert by["live"]["services"]["running"] == 1 and by["live"]["services"]["not_configured"] == 4


# ---------------------------------------------------------------- missing != zero
def test_missing_metrics_are_null_not_zero(admin, client, srv, db):
    r = client.get(f"{API}/servers/{srv.id}/metrics", params={"range": "1h"}).json()
    assert r["has_data"] is False and r["latest"] is None and r["availability"] == "unknown"
    assert len(r["points"]) in (60, 61)  # 61 when the window start is not bucket-aligned
    assert all(v is None for p in r["points"] for k, v in p.items() if k != "t")
    # heartbeat WITHOUT metrics must not create a sample
    hb(db, srv, agents_now())
    assert db.scalar(select(MetricSample.id)) is None
    # swap total 0 -> swap percent is "not applicable" (null), never 0
    mon.ingest_heartbeat(db, srv, {"metrics": {"cpu_percent": 3.0, "swap": {"total": 0, "used": 0}}}, agents_now())
    db.commit()
    s = db.scalar(select(MetricSample))
    assert s.swap_percent is None and s.net_rx_bps is None and s.mem_percent is None and s.disk_max_percent is None and s.cpu_percent == 3.0
    got = client.get(f"{API}/servers/{srv.id}/metrics", params={"range": "1h"}).json()
    nonnull = [p for p in got["points"] if p["cpu_percent"] is not None]
    assert len(nonnull) == 1 and nonnull[0]["net_rx_bps"] is None and nonnull[0]["swap_percent"] is None


def test_null_samples_do_not_drag_averages_down(db, srv):
    t = datetime(2026, 6, 15, 10, 0, 5, tzinfo=UTC)
    sample(db, srv, t, cpu_percent=None, mem_percent=40.0)
    sample(db, srv, t + timedelta(seconds=10), cpu_percent=50.0)
    sample(db, srv, t + timedelta(seconds=20), cpu_percent=70.0, mem_percent=60.0)
    s = mon.query_series(db, srv.id, t - timedelta(minutes=1), t + timedelta(minutes=2), "UTC", 300)
    p = next(x for x in s.points if x["cpu_percent"] is not None)
    assert p["cpu_percent"] == 60.0 and p["mem_percent"] == 50.0  # nulls excluded, not counted as 0


# ---------------------------------------------------------------- time ranges and timezones
def test_timezone_day_bucketing_kolkata(db, srv):
    # 2026-03-01 18:00Z = 23:30 IST (Mar 1);  19:00Z = 00:30 IST (Mar 2)
    sample(db, srv, datetime(2026, 3, 1, 18, 0, tzinfo=UTC), cpu_percent=10.0)
    sample(db, srv, datetime(2026, 3, 1, 19, 0, tzinfo=UTC), cpu_percent=30.0)
    start, end = datetime(2026, 2, 28, 0, 0, tzinfo=UTC), datetime(2026, 3, 3, 0, 0, tzinfo=UTC)
    ist = mon.query_series(db, srv.id, start, end, "Asia/Kolkata", 86400)
    vals = {p["t"]: p["cpu_percent"] for p in ist.points if p["cpu_percent"] is not None}
    assert vals == {"2026-03-01T00:00:00+05:30": 10.0, "2026-03-02T00:00:00+05:30": 30.0}
    utc = mon.query_series(db, srv.id, start, end, "UTC", 86400)
    assert {p["t"]: p["cpu_percent"] for p in utc.points if p["cpu_percent"] is not None} == {"2026-03-01T00:00:00+00:00": 20.0}


def test_timezone_hour_bucket_half_hour_offset(db, srv):
    sample(db, srv, datetime(2026, 6, 15, 10, 10, tzinfo=UTC), cpu_percent=20.0)  # 15:40 IST
    s = mon.query_series(db, srv.id, datetime(2026, 6, 15, 9, 0, tzinfo=UTC), datetime(2026, 6, 15, 12, 0, tzinfo=UTC), "Asia/Kolkata", 3600)
    got = [p["t"] for p in s.points if p["cpu_percent"] is not None]
    assert got == ["2026-06-15T15:00:00+05:30"]  # local hour, not UTC hour
    assert s.points[0]["t"].endswith("+05:30")


def test_dst_transition_keeps_local_day_buckets(db, srv):
    start, end = datetime(2026, 3, 7, 12, 0, tzinfo=UTC), datetime(2026, 3, 10, 12, 0, tzinfo=UTC)
    s = mon.query_series(db, srv.id, start, end, "America/New_York", 86400)
    ts = [p["t"] for p in s.points]
    assert ts[0] == "2026-03-07T00:00:00-05:00" and "2026-03-09T00:00:00-04:00" in ts  # offset flips across Mar 8
    sample(db, srv, datetime(2026, 3, 8, 6, 0, tzinfo=UTC), cpu_percent=5.0)  # 01:00 EST Mar 8
    sample(db, srv, datetime(2026, 3, 9, 3, 59, tzinfo=UTC), cpu_percent=15.0)  # 23:59 EDT Mar 8 (23h day)
    s = mon.query_series(db, srv.id, start, end, "America/New_York", 86400)
    nn = {p["t"]: p["cpu_percent"] for p in s.points if p["cpu_percent"] is not None}
    assert nn == {"2026-03-08T00:00:00-05:00": 10.0}


def test_range_api_and_validation(admin, client, srv, db):
    now = agents_now()
    sample(db, srv, now - timedelta(minutes=30), cpu_percent=42.0, mem_percent=50.0)
    for rng, n in (("1h", 60), ("6h", 72), ("24h", 96), ("7d", 168)):
        r = client.get(f"{API}/servers/{srv.id}/metrics", params={"range": rng}).json()
        assert r["range"] == rng and n <= len(r["points"]) <= n + 1 and r["has_data"], rng
    r = client.get(f"{API}/servers/{srv.id}/metrics", params={"range": "6h", "tz": "Asia/Kolkata"}).json()
    assert r["tz"] == "Asia/Kolkata" and r["points"][0]["t"].endswith("+05:30")
    assert client.get(f"{API}/servers/{srv.id}/metrics", params={"range": "bogus"}).status_code == 422
    assert client.get(f"{API}/servers/{srv.id}/metrics", params={"tz": "Mars/Base"}).status_code == 422
    assert client.get(f"{API}/servers/{srv.id}/metrics", params={"range": "custom"}).status_code == 422
    c = {"range": "custom", "start": (now - timedelta(hours=2)).isoformat(), "end": (now - timedelta(hours=1)).isoformat()}
    assert client.get(f"{API}/servers/{srv.id}/metrics", params=c).json()["has_data"] is False
    c["end"] = c["start"]
    assert client.get(f"{API}/servers/{srv.id}/metrics", params=c).status_code == 422
    assert client.get(f"{API}/servers/{uuid.uuid4()}/metrics").status_code == 404
    assert client.get(f"{API}/servers/{srv.id}/metrics", params={"interval": 7}).status_code == 422
    assert client.get(f"{API}/servers/{srv.id}/metrics/latest").json()["sample"]["cpu_percent"] == 42.0


def test_raw_sample_pagination(admin, client, srv, db):
    base = agents_now() - timedelta(hours=1)
    for i in range(5):
        sample(db, srv, base + timedelta(minutes=i), cpu_percent=float(i))
    p1 = client.get(f"{API}/servers/{srv.id}/metrics/samples", params={"limit": 2}).json()
    assert [x["cpu_percent"] for x in p1["items"]] == [4.0, 3.0] and p1["next_before"]
    p2 = client.get(f"{API}/servers/{srv.id}/metrics/samples", params={"limit": 2, "before": p1["next_before"]}).json()
    assert [x["cpu_percent"] for x in p2["items"]] == [2.0, 1.0]
    p3 = client.get(f"{API}/servers/{srv.id}/metrics/samples", params={"limit": 2, "before": p2["next_before"]}).json()
    assert [x["cpu_percent"] for x in p3["items"]] == [0.0] and p3["next_before"] is None
    assert client.get(f"{API}/servers/{srv.id}/metrics/samples", params={"limit": 9999}).status_code == 422


# ---------------------------------------------------------------- services
def test_service_health_not_configured_honesty(admin, client, anon, db):
    sid, key_id, secret = enrolled(client, anon)
    svc = {x["name"]: x for x in client.get(f"{API}/servers/{sid}/services").json()}
    assert set(svc) == set(mon.KNOWN_SERVICES) and all(x["state"] == "not_configured" for x in svc.values())
    signed(anon, "/heartbeat", {"services": [
        {"name": "nginx", "state": "running"}, {"name": "redis", "state": "stopped", "detail": "installed"},
        {"name": "mariadb", "state": "failed"}, {"name": "pm2", "state": "unknown"}, {"name": "app.api", "state": "weird"}]}, key_id, secret)
    svc = {x["name"]: x for x in client.get(f"{API}/servers/{sid}/services").json()}
    assert svc["nginx"]["state"] == "running" and svc["redis"]["state"] == "stopped" and svc["mariadb"]["state"] == "failed"
    assert svc["pm2"]["state"] == "not_configured" and svc["app.api"]["state"] == "not_configured"  # unknown never shown as failed
    assert svc["php-fpm"]["state"] == "not_configured" and svc["nginx"]["checked_at"]


# ---------------------------------------------------------------- thresholds and alerts
def test_threshold_crossing_escalation_and_auto_resolve(db, srv):
    hb(db, srv, NOW, cpu=50)
    assert alerts(db) == []
    hb(db, srv, NOW + timedelta(seconds=30), cpu=80)  # == warning threshold crosses (>=)
    a = alerts(db)
    assert len(a) == 1 and a[0].severity == "warning" and a[0].state == "open" and a[0].metric == "cpu_percent" and a[0].threshold == 80
    assert "80" in a[0].message and "%" in a[0].message
    hb(db, srv, NOW + timedelta(seconds=60), cpu=96)
    a = alerts(db)
    assert len(a) == 1 and a[0].severity == "critical" and a[0].threshold == 95
    hb(db, srv, NOW + timedelta(seconds=90), cpu=20)
    a = alerts(db)
    assert a[0].state == "resolved" and a[0].resolved_at and a[0].resolved_by is None
    acts = [e.action for e in db.scalars(select(AuditEvent))]
    assert {"alert.opened", "alert.severity_changed", "alert.auto_resolved"} <= set(acts)


def test_each_metric_crosses_independently(db, srv):
    mon.ingest_heartbeat(db, srv, {"metrics": {"cpu_percent": 10, "memory": {"total": 100, "used": 90, "available": 10}, "swap": {"total": 100, "used": 60},
                                             "disks": [{"mount": "/", "percent": 50}, {"mount": "/data", "percent": 91}]}}, NOW)
    db.commit()
    got = {a.metric: a.severity for a in alerts(db)}
    assert got == {"mem_percent": "warning", "swap_percent": "warning", "disk_percent": "critical"}


def test_duplicate_alert_suppression(db, srv):
    for i in range(6):
        hb(db, srv, NOW + timedelta(seconds=30 * i), cpu=90)
    a = alerts(db)
    assert len(a) == 1 and a[0].occurrences == 6 and a[0].last_seen_at == NOW + timedelta(seconds=150)
    assert [e.action for e in db.scalars(select(AuditEvent))].count("alert.opened") == 1


def test_cooldown_suppresses_flapping_then_allows_reopen(db, srv):
    hb(db, srv, NOW, cpu=90)
    hb(db, srv, NOW + timedelta(seconds=30), cpu=10)  # resolves
    hb(db, srv, NOW + timedelta(seconds=60), cpu=90)  # inside 900 s cooldown -> suppressed
    a = alerts(db)
    assert len(a) == 1 and a[0].state == "resolved"
    hb(db, srv, NOW + timedelta(seconds=30 + 901), cpu=92)  # cooldown elapsed -> new alert
    a = alerts(db)
    assert len(a) == 2 and a[1].state == "open"


def test_missing_reading_neither_opens_nor_resolves(db, srv):
    hb(db, srv, NOW, cpu=90)
    hb(db, srv, NOW + timedelta(seconds=30))  # heartbeat with no metrics
    mon.ingest_heartbeat(db, srv, {"metrics": {"load": {"load1": 1.0}}}, NOW + timedelta(seconds=60))  # cpu missing
    db.commit()
    a = alerts(db)
    assert len(a) == 1 and a[0].state == "open" and a[0].occurrences == 1


def test_missing_heartbeat_alert_and_recovery(db, srv):
    srv.last_heartbeat_at = NOW
    db.commit()
    assert mon.sweep_heartbeats(db, NOW + timedelta(seconds=60)) == 0
    assert mon.sweep_heartbeats(db, NOW + timedelta(seconds=120)) == 1  # stale (>90 s) -> warning
    assert mon.sweep_heartbeats(db, NOW + timedelta(seconds=130)) == 0  # deduplicated
    assert mon.sweep_heartbeats(db, NOW + timedelta(seconds=400)) == 1  # escalates to critical
    db.commit()
    a = alerts(db, metric="heartbeat_age_seconds")
    assert len(a) == 1 and a[0].severity == "critical" and a[0].threshold == 300
    mon.ingest_heartbeat(db, srv, {"agent_version": "1"}, NOW + timedelta(seconds=410))
    db.commit()
    assert alerts(db)[0].state == "resolved"
    # never-seen servers raise nothing; disabled servers raise nothing
    never = Server(name="never", status="registered")
    off = Server(name="off", status="disabled", last_heartbeat_at=NOW - timedelta(days=1))
    db.add_all([never, off])
    db.commit()
    mon.sweep_heartbeats(db, NOW + timedelta(days=2))
    assert {a.server_id for a in alerts(db)} == {srv.id}


def test_alert_lifecycle_endpoints(admin, client, db, srv):
    hb(db, srv, agents_now(), cpu=99)
    a = client.get(f"{API}/monitoring/alerts").json()["items"][0]
    assert a["state"] == "open" and a["server_name"] == "imac" and a["severity"] == "critical"
    ack = client.post(f"{API}/monitoring/alerts/{a['id']}/acknowledge").json()
    assert ack["state"] == "acknowledged" and ack["acknowledged_by"] == str(admin.id) and ack["acknowledged_at"]
    assert client.post(f"{API}/monitoring/alerts/{a['id']}/acknowledge").status_code == 409
    res = client.post(f"{API}/monitoring/alerts/{a['id']}/resolve").json()
    assert res["state"] == "resolved" and res["resolved_by"] == str(admin.id) and res["auto_resolved"] is False
    assert client.post(f"{API}/monitoring/alerts/{a['id']}/resolve").status_code == 409
    assert client.post(f"{API}/monitoring/alerts/{uuid.uuid4()}/acknowledge").status_code == 404
    # manual resolve while still over threshold: cooldown prevents an immediate duplicate
    hb(db, srv, agents_now() + timedelta(seconds=30), cpu=99)
    assert len(alerts(db)) == 1
    acts = [e.action for e in db.scalars(select(AuditEvent))]
    assert "alert.acknowledged" in acts and "alert.resolved" in acts


def test_escalation_reopens_acknowledged_alert(db, srv):
    hb(db, srv, NOW, cpu=85)
    a = alerts(db)[0]
    a.state, a.acknowledged_at = "acknowledged", NOW
    db.commit()
    hb(db, srv, NOW + timedelta(seconds=30), cpu=99)
    a = alerts(db)[0]
    assert a.state == "open" and a.severity == "critical" and a.acknowledged_at is None


def test_alert_listing_filters_and_pagination(admin, client, db, srv):
    for i in range(5):
        db.add(Alert(server_id=srv.id, metric=f"m{i}", severity="warning" if i % 2 else "critical", state="resolved" if i < 2 else "open", message="x", opened_at=NOW + timedelta(minutes=i), last_seen_at=NOW))
    db.commit()
    r = client.get(f"{API}/monitoring/alerts", params={"page_size": 2}).json()
    assert r["total"] == 5 and len(r["items"]) == 2 and r["items"][0]["metric"] == "m4"
    assert client.get(f"{API}/monitoring/alerts", params={"page_size": 2, "page": 3}).json()["items"][0]["metric"] == "m0"
    assert client.get(f"{API}/monitoring/alerts", params={"state": "active"}).json()["total"] == 3
    assert client.get(f"{API}/monitoring/alerts", params={"state": "resolved"}).json()["total"] == 2
    assert client.get(f"{API}/monitoring/alerts", params={"severity": "critical"}).json()["total"] == 3
    assert client.get(f"{API}/monitoring/alerts", params={"state": "bogus"}).status_code == 422
    assert client.get(f"{API}/monitoring/alerts", params={"page_size": 1000}).status_code == 422


def test_threshold_configuration(admin, client, db, srv):
    d = {t["metric"]: t for t in client.get(f"{API}/monitoring/thresholds", params={"server_id": str(srv.id)}).json()}
    assert d["cpu_percent"]["warning"] == 80 and d["cpu_percent"]["critical"] == 95 and d["cpu_percent"]["customized"] is False
    assert set(d) == set(mon.DEFAULT_THRESHOLDS)
    put = lambda **k: client.put(f"{API}/monitoring/thresholds", json={"server_id": str(srv.id), "metric": "cpu_percent", "warning": 50, "critical": 70, **k})
    assert put(warning=70, critical=70).status_code == 422
    assert put(warning=90, critical=80).status_code == 422
    assert put(critical=101).status_code == 422
    assert put(metric="bogus").status_code == 422
    assert put(cooldown_seconds=1).status_code == 422
    assert client.put(f"{API}/monitoring/thresholds", json={"server_id": str(uuid.uuid4()), "metric": "cpu_percent", "warning": 1, "critical": 2}).status_code == 404
    assert put().status_code == 200
    hb(db, srv, agents_now(), cpu=60)  # custom warning of 50 now applies
    assert alerts(db)[0].threshold == 50
    out = {t["metric"]: t for t in put(enabled=False).json()}
    assert out["cpu_percent"]["enabled"] is False and out["cpu_percent"]["customized"] is True and out["mem_percent"]["customized"] is False
    hb(db, srv, agents_now() + timedelta(seconds=1), cpu=99)  # disabled metric: no escalation, nothing new
    assert len(alerts(db)) == 1 and alerts(db)[0].severity == "warning"
    assert "threshold.updated" in [e.action for e in db.scalars(select(AuditEvent))]


# ---------------------------------------------------------------- retention policy
def test_downsample_and_prune(db, srv):
    old = NOW - timedelta(days=3)
    base = datetime(old.year, old.month, old.day, old.hour, 0, 0, tzinfo=UTC)
    for i in range(10):  # ten 30 s samples inside one 5-minute bucket
        sample(db, srv, base + timedelta(seconds=30 * i), cpu_percent=float(i * 10), mem_percent=None if i < 5 else 50.0, uptime_seconds=100 + i)
    sample(db, srv, NOW - timedelta(hours=1), cpu_percent=7.0)  # recent: untouched
    r = mon.downsample_and_prune(db, NOW)
    db.commit()
    assert r["raw_rolled"] == 10 and r["created_5m"] == 1
    rows = db.scalars(select(MetricSample).order_by(MetricSample.ts)).all()
    assert [(x.resolution, x.sample_count) for x in rows] == [("5m", 10), ("raw", 1)]
    assert rows[0].cpu_percent == pytest.approx(45.0) and rows[0].mem_percent == 50.0 and rows[0].uptime_seconds == 109
    assert mon.downsample_and_prune(db, NOW)["raw_rolled"] == 0  # idempotent
    # 5m -> 1h after 14 days, 1h dropped after 400 days
    later = NOW + timedelta(days=15)
    r = mon.downsample_and_prune(db, later)
    db.commit()
    assert r["raw_rolled"] == 1 and r["5m_rolled"] == 2 and r["created_1h"] == 2  # the recent raw row aged out too
    assert [x.resolution for x in db.scalars(select(MetricSample).where(MetricSample.resolution != "raw"))] == ["1h", "1h"]
    mon.downsample_and_prune(db, NOW + timedelta(days=500))
    db.commit()
    assert db.scalars(select(MetricSample).where(MetricSample.resolution == "1h")).all() == []
    # rollups remain queryable and weighted
    sample(db, srv, base - timedelta(days=1), res="5m", cpu_percent=10.0, sample_count=1)
    sample(db, srv, base - timedelta(days=1) + timedelta(minutes=1), res="raw", cpu_percent=40.0, sample_count=3)
    s = mon.query_series(db, srv.id, base - timedelta(days=1) - timedelta(minutes=5), base - timedelta(days=1) + timedelta(minutes=10), "UTC", 3600)
    assert next(p["cpu_percent"] for p in s.points if p["cpu_percent"] is not None) == pytest.approx(32.5)


def test_maintenance_runs(admin, client, db, srv):
    r = client.post(f"{API}/monitoring/sweep")
    assert r.status_code == 200 and {"jobs_timed_out", "heartbeat_alerts_changed", "nonces_purged", "raw_rolled"} <= set(r.json())
