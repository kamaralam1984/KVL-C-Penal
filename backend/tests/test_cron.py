"""Customer cron jobs: own verified domains only, SSRF-safe, schedule limits, tenant isolation, pause after failures."""
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select

from app.models.cron import CronJob, CronRun
from app.models.hosting import Domain, Website
from app.models.identity import EmailOutbox
from app.services import cron as svc
from tests.hosting_helpers import tenant_client

API = "/api/v1/cron-jobs"


class FakeHTTP:
    def __init__(self):
        self.calls, self.code, self.exc = [], 200, None

    def __call__(self, host, port, path, ip, timeout):
        self.calls.append((host, port, path, ip))
        if self.exc:
            raise self.exc
        return self.code


@pytest.fixture
def http(monkeypatch):
    f = FakeHTTP()
    monkeypatch.setattr(svc, "transport", f)
    monkeypatch.setattr(svc, "resolve_public", lambda host: ["203.0.113.10"])
    return f


def site(db, tenant, host, verified=True, status="active"):
    w = Website(tenant_id=tenant.id, name=host, slug=host.split(".")[0][:40], document_root=f"/srv/{host}", status=status)
    db.add(w)
    db.flush()
    db.add(Domain(tenant_id=tenant.id, website_id=w.id, hostname=host, verification_token="t" * 20, verification_state="verified" if verified else "pending"))
    db.commit()
    return w


@pytest.fixture
def world(db, make_user, make_client):
    a, _, ta = tenant_client(make_user, make_client, "a@cron.example")
    b, _, tb = tenant_client(make_user, make_client, "b@cron.example")
    return {"a": a, "b": b, "ta": ta, "tb": tb, "wa": site(db, ta, "shop-a.example.com"), "wb": site(db, tb, "shop-b.example.com")}


def body(w, **kw):
    return {"website_id": str(w.id), "name": "wp cron", "url": f"https://{w.name}/wp-cron.php?doing_wp_cron", "schedule": "*/15 * * * *", **kw}


def test_create_list_run_and_history(world, http, db):
    r = world["a"].post(API, json=body(world["wa"]))
    assert r.status_code == 201, r.text
    j = r.json()
    assert j["next_run_at"] and j["enabled"]
    assert [x["id"] for x in world["a"].get(API).json()["items"]] == [j["id"]]
    r = world["a"].post(f"{API}/{j['id']}/run")
    assert r.status_code == 200 and r.json()["run"]["status"] == "ok" and r.json()["run"]["http_code"] == 200
    assert http.calls == [("shop-a.example.com", 443, "/wp-cron.php?doing_wp_cron", "203.0.113.10")]
    assert len(world["a"].get(f"{API}/{j['id']}/runs").json()["items"]) == 1


@pytest.mark.parametrize("url,code", [
    ("http://shop-a.example.com/x", "invalid_url"),            # https only
    ("https://shop-b.example.com/x", "invalid_url"),           # another tenant's domain
    ("https://evil.example.org/x", "invalid_url"),             # not our domain
    ("https://127.0.0.1/x", "invalid_url"),                    # IP literal
    ("https://localhost/x", "invalid_url"),
    ("https://user:pw@shop-a.example.com/x", "invalid_url"),   # credentials
    ("https://shop-a.example.com:22/x", "invalid_url"),        # port not allowed
])
def test_url_must_be_own_verified_domain(world, http, url, code):
    r = world["a"].post(API, json=body(world["wa"], url=url))
    assert r.status_code == 422 and r.json()["error"]["code"] == code


def test_unverified_domain_and_inactive_site_rejected(world, db, http):
    w = site(db, world["ta"], "new-a.example.com", verified=False)
    r = world["a"].post(API, json=body(w))
    assert r.status_code == 422 and r.json()["error"]["code"] == "domain_not_verified"
    w2 = site(db, world["ta"], "off-a.example.com", status="suspended")
    assert world["a"].post(API, json=body(w2)).status_code == 409


@pytest.mark.parametrize("sched", ["* * * * *", "*/5 * * * *", "61 * * * *", "bad", "0 0 * *"])
def test_schedule_validated_and_capped(world, http, sched):
    r = world["a"].post(API, json=body(world["wa"], schedule=sched))
    assert r.status_code == 422


def test_tenant_isolation(world, http):
    j = world["a"].post(API, json=body(world["wa"])).json()
    assert world["b"].get(API).json()["items"] == []
    for m, p in (("post", f"{API}/{j['id']}/run"), ("get", f"{API}/{j['id']}/runs"), ("patch", f"{API}/{j['id']}"), ("delete", f"{API}/{j['id']}")):
        kw = {"json": {"enabled": False}} if m == "patch" else {}
        assert getattr(world["b"], m)(p, **kw).status_code == 404
    # cannot create a job on another tenant's website
    assert world["b"].post(API, json=body(world["wa"])).status_code == 404
    assert http.calls == []


def test_scheduler_runs_due_jobs_and_advances(world, http, db):
    j = world["a"].post(API, json=body(world["wa"])).json()
    row = db.get(CronJob, j["id"])
    row.next_run_at = datetime.now(UTC) - timedelta(minutes=1)
    db.commit()
    from app.services import scheduler

    out = scheduler.run_all(minute=0)
    assert out["cron"] == {"ran": 1, "ok": 1, "failed": 0}
    db.refresh(row)
    assert row.next_run_at > datetime.now(UTC) and row.last_status == "ok"
    assert scheduler.run_all(minute=1)["cron"]["ran"] == 0  # not due again


def test_failures_pause_job_and_email_tenant(world, http, db):
    http.code = 500
    j = world["a"].post(API, json=body(world["wa"])).json()
    row = db.get(CronJob, j["id"])
    for _ in range(svc.MAX_FAILURES):
        svc.execute(db, row, "schedule")
    db.commit()
    db.refresh(row)
    assert not row.enabled and row.disabled_reason and row.last_error == "HTTP 500"
    assert db.scalar(select(EmailOutbox).where(EmailOutbox.kind == "cron_paused")) is not None
    # re-enable clears the failure counter
    r = world["a"].patch(f"{API}/{j['id']}", json={"enabled": True})
    assert r.json()["enabled"] and r.json()["consecutive_failures"] == 0


def test_run_history_is_trimmed_and_errors_redacted(world, http, db):
    http.exc = RuntimeError("boom token=abcdef123456")
    j = world["a"].post(API, json=body(world["wa"])).json()
    row = db.get(CronJob, j["id"])
    for _ in range(svc.KEEP_RUNS + 5):
        row.consecutive_failures = 0
        svc.execute(db, row, "schedule")
    db.commit()
    runs = db.scalars(select(CronRun).where(CronRun.job_id == row.id)).all()
    assert len(runs) == svc.KEEP_RUNS
    assert all("abcdef123456" not in (r.error or "") for r in runs)


def test_domain_unverified_later_blocks_execution(world, http, db):
    j = world["a"].post(API, json=body(world["wa"])).json()
    d = db.scalar(select(Domain).where(Domain.hostname == "shop-a.example.com"))
    d.verification_state = "failed"
    db.commit()
    r = world["a"].post(f"{API}/{j['id']}/run").json()["run"]
    assert r["status"] == "error" and http.calls == []


def test_manual_run_rate_limited(world, http):
    j = world["a"].post(API, json=body(world["wa"])).json()
    codes = [world["a"].post(f"{API}/{j['id']}/run").status_code for _ in range(6)]
    assert codes[:5] == [200] * 5 and codes[5] == 429
