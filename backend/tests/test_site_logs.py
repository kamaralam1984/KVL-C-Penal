"""Website logs viewer + visitor stats: job based, own site only, redacted, aggregates only."""
from app.models.infra import AgentJob
from tests.hosting_helpers import finish_job, last_job, make_server, tenant_client
from tests.test_websites import API, code, verified_site


def staged(make_user, make_client, db, monkeypatch, email="a@x.com", host="blog.example.com"):
    a, _, _ = tenant_client(make_user, make_client, email)
    w = verified_site(a, monkeypatch, host=host)
    assert a.post(f"{API}/{w['id']}/provision").status_code == 200
    finish_job(db, last_job(db).id, result={"ok": True, "status": "validated", "config_hash": "a" * 64})
    return a, w


def test_logs_flow_redacted_and_isolated(make_user, make_client, db, monkeypatch):
    make_server(db)
    a, w = staged(make_user, make_client, db, monkeypatch)
    r = a.post(f"{API}/{w['id']}/logs", json={"kind": "error", "contains": "php"})
    assert r.status_code == 202
    jid = r.json()["job_id"]
    j = db.get(AgentJob, jid)
    assert j.operation == "nginx.site_logs" and j.params["kind"] == "error" and j.params["site"].startswith("w-")
    assert a.get(f"{API}/{w['id']}/logs/{jid}").json() == {"status": "queued", "lines": []}
    finish_job(db, jid, result={"ok": True, "kind": "error", "lines": ["boom password=hunter2"], "page": 1, "page_size": 200, "has_more": False, "total_lines": 1})
    out = a.get(f"{API}/{w['id']}/logs/{jid}").json()
    assert out["status"] == "succeeded" and "hunter2" not in out["lines"][0]
    # another tenant: cannot request or read
    b, _, _ = tenant_client(make_user, make_client, "b@x.com")
    assert b.post(f"{API}/{w['id']}/logs", json={}).status_code == 404
    assert b.get(f"{API}/{w['id']}/logs/{jid}").status_code == 404
    # a job of another op/site cannot be read through this endpoint
    other = last_job(db, "nginx.stage_site")
    assert a.get(f"{API}/{w['id']}/logs/{other.id}").status_code == 404


def test_logs_validation_and_unprovisioned(make_user, make_client, db, monkeypatch):
    a, _, _ = tenant_client(make_user, make_client, "a@x.com")
    w = verified_site(a, monkeypatch)
    r = a.post(f"{API}/{w['id']}/logs", json={})
    assert r.status_code == 409 and code(r) == "not_provisioned"
    make_server(db)
    a.post(f"{API}/{w['id']}/provision")
    for bad in ({"kind": "../../etc/passwd"}, {"contains": "a;rm -rf"}, {"page_size": 9999}):
        assert a.post(f"{API}/{w['id']}/logs", json=bad).status_code == 422


def test_stats_flow_returns_only_whitelisted_keys(make_user, make_client, db, monkeypatch):
    make_server(db)
    a, w = staged(make_user, make_client, db, monkeypatch)
    jid = a.post(f"{API}/{w['id']}/stats").json()["job_id"]
    finish_job(db, jid, result={"ok": True, "requests": 10, "human_requests": 8, "bot_requests": 2, "heatmap": [[0] * 24] * 7, "daily": [], "status": {"2xx": 8},
                                "top_paths": [["/", 5]], "top_referrers": [], "raw_ips": ["203.0.113.5"]})
    out = a.get(f"{API}/{w['id']}/stats/{jid}").json()
    assert out["status"] == "succeeded" and out["requests"] == 10 and out["status_codes"] == {"2xx": 8} and "raw_ips" not in out
