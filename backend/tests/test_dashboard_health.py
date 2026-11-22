"""Admin dashboard: explainable health score, live resources, services and alerts."""
from datetime import UTC, datetime, timedelta

from app.models.infra import Server
from app.models.monitoring import Alert, MetricSample, ServiceStatus
from tests.hosting_helpers import tenant_client

URL = "/api/v1/stats/dashboard"


def test_no_server_means_no_score(admin, client):
    d = client.get(URL).json()
    assert d["health"] == {"score": None, "factors": []}
    assert d["resources"] == [] and d["services"] == [] and d["alerts"] == []


def test_health_resources_and_services(admin, client, db):
    now = datetime.now(UTC)
    srv = Server(name="imac", status="registered", registered_at=now - timedelta(days=1), last_heartbeat_at=now)
    db.add(srv)
    db.flush()
    db.add(MetricSample(server_id=srv.id, ts=now - timedelta(minutes=1), cpu_percent=30.0, mem_percent=70.0, swap_percent=60.0, disk_max_percent=81.0, detail={"cpu_count": 4}))
    db.add(Alert(server_id=srv.id, metric="disk_percent", severity="warning", message="disk 81%", opened_at=now, last_seen_at=now))
    db.add(Alert(server_id=srv.id, metric="mem_percent", severity="critical", message="mem", opened_at=now, last_seen_at=now))
    for name, state in (("nginx", "running"), ("pm2", "stopped"), ("redis", "failed")):
        db.add(ServiceStatus(server_id=srv.id, name=name, state=state, checked_at=now))
    db.commit()

    d = client.get(URL).json()
    # 100 - 8 (warning) - 20 (critical) - 10 (redis failed); pm2 stopped is allowed
    assert d["health"]["score"] == 62
    assert len(d["health"]["factors"]) == 3
    r = d["resources"][0]
    assert (r["cpu"], r["disk"], r["cpu_count"]) == (30.0, 81.0, 4) and len(r["spark"]) == 1
    assert {s["name"]: s["state"] for s in d["services"]} == {"nginx": "running", "pm2": "stopped", "redis": "failed"}
    assert {a["severity"] for a in d["alerts"]} == {"warning", "critical"}


def test_silent_server_costs_points(admin, client, db):
    db.add(Server(name="old", status="registered", last_heartbeat_at=datetime.now(UTC) - timedelta(hours=1)))
    db.commit()
    assert client.get(URL).json()["health"]["score"] == 70


def test_tenant_view_unchanged(make_user, make_client):
    c, _, _ = tenant_client(make_user, make_client, "t@x.example.com")
    d = c.get(URL).json()
    assert d["scope"] == "tenant" and "health" not in d
