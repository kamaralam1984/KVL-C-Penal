"""Full-page operations centre: platform-only, honest empty states, real aggregates."""
from datetime import UTC, datetime, timedelta

from app.models.identity import LoginEvent
from app.models.infra import AgentJob, Server
from app.models.monitoring import MetricSample, ServiceStatus
from app.models.support import Ticket
from tests.hosting_helpers import tenant_client

URL = "/api/v1/stats/ops-center"


def test_customer_forbidden(make_user, make_client):
    c, _, _ = tenant_client(make_user, make_client, "a@x.example.com")
    assert c.get(URL).status_code == 403


def test_empty_platform_reports_no_data(admin, client):
    r = client.get(URL)
    assert r.status_code == 200, r.text
    d = r.json()
    assert d["uptime"]["percent"] is None
    assert d["support"]["availability_percent"] is None
    assert d["traffic"] == [] and d["jobs"] == []
    assert len(d["login_heatmap"]) == 7 and all(len(row) == 24 for row in d["login_heatmap"])
    assert sum(map(sum, d["login_heatmap"])) >= 1  # the admin's own sign-in counts
    assert [s["name"] for s in d["services"]][:2] == ["cloudflared", "nginx"]
    assert len(d["monthly"]["tenants"]) >= 11
    assert d["disk_forecast"]["projection"] == []


def test_aggregates(admin, client, db, make_user):
    now = datetime.now(UTC)
    _, tenant = make_user("c@x.example.com")
    srv = Server(name="imac", status="registered", registered_at=now - timedelta(days=5), last_heartbeat_at=now)
    db.add(srv)
    db.flush()
    for i in range(5):
        db.add(MetricSample(server_id=srv.id, ts=now - timedelta(days=4 - i), cpu_percent=10.0, net_rx_bps=1000.0, net_tx_bps=500.0, disk_max_percent=50.0 + i))
    db.add(ServiceStatus(server_id=srv.id, name="nginx", state="running", checked_at=now))
    db.add(AgentJob(server_id=srv.id, operation="nginx.reload", status="succeeded"))
    db.add(AgentJob(server_id=srv.id, operation="nginx.reload", status="failed"))
    db.add(AgentJob(server_id=srv.id, operation="nginx.reload", status="queued"))
    db.add(Ticket(tenant_id=tenant.id, subject="s", created_at=now - timedelta(hours=5), first_response_at=now - timedelta(hours=4), resolved_at=now - timedelta(hours=1), assignee_id=admin.id))
    db.add(Ticket(tenant_id=tenant.id, subject="t", created_at=now - timedelta(hours=3), first_response_at=now - timedelta(hours=2), first_response_breached=True))
    db.add(LoginEvent(email="x@x.example.com", outcome="bad_password"))
    db.commit()

    d = client.get(URL).json()
    assert d["servers"] == {"total": 1, "connected": 1}
    assert d["uptime"]["samples"] == 5 and d["uptime"]["percent"] == 0.35  # 5 of 1440 five-minute windows in 5 days
    job = d["jobs"][0]
    assert (job["total"], job["succeeded"], job["failed"], job["pending"]) == (3, 1, 1, 1)
    assert job["success_percent"] == 50.0
    s = d["support"]
    assert s["tickets_30d"] == 2 and s["availability_percent"] == 50.0
    assert s["agents"][0]["resolved"] == 1 and s["agents"][0]["avg_handle_hours"] == 4.0
    assert next(x for x in d["services"] if x["name"] == "nginx")["state"] == "running"
    assert next(x for x in d["services"] if x["name"] == "redis")["state"] is None
    assert [h["disk"] for h in d["disk_forecast"]["history"]] == [50.0, 51.0, 52.0, 53.0, 54.0]
    assert d["disk_forecast"]["projection"][0]["disk"] == 55.0
