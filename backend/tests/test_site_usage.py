import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select

from app.models.hosting import Website
from app.models.monitoring import MetricSample, SiteUsageSample
from app.services import monitoring as mon
from tests.agent_helpers import API, METRICS, enrolled, signed
from tests.hosting_helpers import make_server, tenant_client
from tests.conftest import login

NOW = datetime(2026, 6, 15, 12, 0, 0, tzinfo=UTC)


@pytest.fixture
def anon(make_client):
    return make_client()


def site(name="w-aaaaaaaaaaaaaaaa", **kw):
    return {"name": name, "kind": "site", "disk_bytes": 1000, "rss_bytes": 5000, "cpu_percent": 2.5, "procs": 2,
            "bytes_out_total": 100, "requests_total": 10, "errors_4xx_total": 1, "errors_5xx_total": 0, **kw}


def test_heartbeat_stores_sites_cores_and_processes(admin, client, anon, db):
    sid, key_id, secret = enrolled(client, anon)
    metrics = {**METRICS, "cpu_count": 4, "cpu_cores": [10.0, 20.0, 30.0, 40.0], "processes": [{"pid": 1, "name": "nginx", "rss_bytes": 123, "cpu_percent": 1.5}]}
    r = signed(anon, "/heartbeat", {"metrics": metrics, "sites": [site()]}, key_id, secret)
    assert r.status_code == 200, r.text
    s = db.scalar(select(MetricSample).where(MetricSample.server_id == uuid.UUID(sid)))
    assert s.detail["cpu_cores"] == [10.0, 20.0, 30.0, 40.0] and s.detail["processes"][0]["name"] == "nginx"
    latest = client.get(f"{API}/servers/{sid}/metrics/latest").json()["sample"]
    assert latest["cpu_cores"] == [10.0, 20.0, 30.0, 40.0] and latest["cpu_count"] == 4
    row = db.scalar(select(SiteUsageSample).where(SiteUsageSample.server_id == uuid.UUID(sid)))
    assert row.rss_bytes == 5000 and row.disk_bytes == 1000 and row.ts.second == 0 and row.ts.minute % 5 == 0
    # a second heartbeat in the same 5-minute bucket updates the row rather than adding one
    signed(anon, "/heartbeat", {"sites": [site(rss_bytes=9000)]}, key_id, secret)
    assert db.query(SiteUsageSample).count() == 1
    db.expire_all()
    assert db.scalar(select(SiteUsageSample.rss_bytes)) == 9000


def test_heartbeat_validates_site_payload(admin, client, anon):
    _sid, key_id, secret = enrolled(client, anon)
    for bad in ({"name": "../etc", "kind": "site"}, {"name": "ok", "kind": "weird"}, {"name": "ok", "bytes_out_total": -1}, {"name": "ok", "shell": "x"}):
        assert signed(anon, "/heartbeat", {"sites": [bad]}, key_id, secret).status_code == 422, bad
    assert signed(anon, "/heartbeat", {"metrics": {"processes": [{"pid": 1, "name": "x", "cmdline": "secret"}]}}, key_id, secret).status_code == 422


def test_counter_deltas_ignore_restart_and_first_point():
    assert mon._delta([10, 25, 5, 9, None, 12]) == [None, 15, None, 4, None, None]


def test_latest_sums_window_and_reports_missing_as_null(admin, client, db):
    srv = make_server(db)
    for i, (out, req) in enumerate([(100, 10), (400, 30), (50, 3), (150, 8)]):  # counter restarts between samples 2 and 3
        db.add(SiteUsageSample(server_id=srv.id, site="w-aaaaaaaaaaaaaaaa", ts=NOW - timedelta(minutes=5 * (3 - i)), kind="site", bytes_out_total=out, requests_total=req, rss_bytes=None))
    db.commit()
    [item] = mon.site_usage_latest(db, srv.id, NOW)["items"]
    assert item["bytes_out_24h"] == 300 + 100 and item["requests_24h"] == 20 + 5  # restart contributes nothing, not a negative
    assert item["rss_bytes"] is None and item["disk_bytes"] is None and item["stale"] is False


def test_series_and_staff_only(admin, client, make_client, make_user, db):
    srv = make_server(db)
    now = datetime.now(UTC).replace(second=0, microsecond=0)
    for i in range(3):
        db.add(SiteUsageSample(server_id=srv.id, site="w-aaaaaaaaaaaaaaaa", ts=now - timedelta(minutes=5 * (2 - i)), kind="site", rss_bytes=1000 * (i + 1), bytes_out_total=100 * i))
    db.commit()
    r = client.get(f"{API}/servers/{srv.id}/sites-usage/w-aaaaaaaaaaaaaaaa/series?range=6h")
    pts = r.json()["points"]
    assert r.status_code == 200 and [p["rss_bytes"] for p in pts] == [1000, 2000, 3000] and [p["bytes_out"] for p in pts] == [None, 100, 100]
    assert client.get(f"{API}/servers/{srv.id}/sites-usage/w-aaaaaaaaaaaaaaaa/series?range=bogus").status_code == 422
    assert client.get(f"{API}/servers/{srv.id}/sites-usage/..%2Fx/series").status_code in (404, 422)
    make_user("cust@example.com", "customer")
    c = make_client()
    assert login(c, "cust@example.com").status_code == 200
    assert c.get(f"{API}/servers/{srv.id}/sites-usage").status_code == 403
    assert c.get(f"{API}/servers/{srv.id}/sites-usage/w-aaaaaaaaaaaaaaaa/series").status_code == 403


def test_customer_sees_only_own_website_usage(make_user, make_client, db):
    srv = make_server(db)
    a, _, _ = tenant_client(make_user, make_client, "a@x.com")
    b, _, _ = tenant_client(make_user, make_client, "b@x.com")
    wa = a.post(f"{API}/websites", json={"name": "Blog", "domain": "blog.example.com"}).json()
    site_row = db.get(Website, uuid.UUID(wa["id"]))
    site_row.server_id = srv.id
    db.add(SiteUsageSample(server_id=srv.id, site=f"w-{site_row.id.hex[:16]}", ts=datetime.now(UTC).replace(second=0, microsecond=0), kind="site", rss_bytes=4242, disk_bytes=77))
    db.add(SiteUsageSample(server_id=srv.id, site="w-someoneelse000000", ts=datetime.now(UTC).replace(second=0, microsecond=0), kind="site", rss_bytes=1))
    db.commit()
    r = a.get(f"{API}/websites/{wa['id']}/usage/live")
    assert r.status_code == 200 and r.json()["item"]["rss_bytes"] == 4242 and r.json()["item"]["name"].startswith("w-")
    assert a.get(f"{API}/websites/{wa['id']}/usage/history?range=24h").json()["points"][0]["rss_bytes"] == 4242
    assert b.get(f"{API}/websites/{wa['id']}/usage/live").status_code == 404
    assert b.get(f"{API}/websites/{wa['id']}/usage/history").status_code == 404
