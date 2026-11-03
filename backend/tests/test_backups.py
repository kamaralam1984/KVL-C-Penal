import hashlib
import json
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select

from app.models.backups import BackupDestination, BackupRecord, BackupSchedule, RestoreRequest, RetentionPolicy
from app.models.identity import AuditEvent, EmailOutbox
from app.models.infra import AgentJob
from app.services import backups as svc
from tests.conftest import login
from tests.p9_helpers import add_ref, add_server, finish_job, last_job, run_agent_job

API = "/api/v1/backups"
ROOT = "sites/acme/public"


@pytest.fixture
def env(tmp_path, admin, client, make_user, make_client, db):
    ua, ta = make_user("a@example.com", "customer")
    ta.contact_email = "a@example.com"
    ub, tb = make_user("b@example.com", "customer")
    srv = add_server(db)
    add_ref(db, ta, "website", "acme", server_id=srv.id, root_rel=ROOT, config_rel="sites/acme/conf")
    add_ref(db, ta, "database", "shopdb", server_id=srv.id)  # no dump registered yet
    add_ref(db, ta, "database", "crmdb", server_id=srv.id, dump_rel="dumps/crm")
    add_ref(db, tb, "website", "other", server_id=srv.id, root_rel="sites/other/public")
    db.commit()
    for d in (f"{ROOT}/wp", "sites/acme/conf", "dumps/crm", "backups", "restores", "sites/other/public"):
        (tmp_path / d).mkdir(parents=True)
    (tmp_path / ROOT / "index.html").write_text("<h1>acme v1</h1>")
    (tmp_path / ROOT / "wp/a.txt").write_text("aaaa")
    (tmp_path / "sites/acme/conf/site.conf").write_text("server_name acme;")
    (tmp_path / "dumps/crm/crm.sql").write_text("-- dump")
    (tmp_path / "sites/other/public/secret.txt").write_text("B only")
    a, b = make_client(), make_client()
    assert login(a, "a@example.com").status_code == 200 and login(b, "b@example.com").status_code == 200
    return {"a": a, "b": b, "admin": client, "ta": ta, "tb": tb, "ua": ua, "dir": tmp_path, "db": db, "srv": srv}


def run(env, pending=None):
    """Run every queued job through the real agent handlers (oldest first)."""
    db = env["db"]
    for j in db.scalars(select(AgentJob).where(AgentJob.status == "queued").order_by(AgentJob.created_at)).all():
        run_agent_job(db, j.id, env["dir"])


def make_backup(env, btype="website_files", ref="acme", client="a", verify=True, **kw):
    r = env[client].post(API, json={"backup_type": btype, "resource_ref": ref, **kw})
    assert r.status_code == 202, r.text
    run(env)
    env[client].get(f"{API}/{r.json()['id']}")  # sync create -> queues verify
    if verify:
        run(env)
    return env[client].get(f"{API}/{r.json()['id']}").json()


# ---------------------------------------------------------------- creation / completion
def test_backup_completion_inventory_and_auto_verification(env, db):
    r = env["a"].post(API, json={"backup_type": "website_files", "resource_ref": "acme"})
    assert r.status_code == 202 and r.json()["status"] == "running"
    j = last_job(db, "backups.create")
    assert j.params["sources"] == [ROOT] and j.params["scope"] == str(env["ta"].id) and j.params["kind"] == "website_files"
    run(env)
    b = env["a"].get(f"{API}/{r.json()['id']}").json()
    assert b["status"] == "completed" and b["size_bytes"] > 0 and len(b["sha256"]) == 64 and b["entries"] == 2
    assert b["verification_status"] == "verifying"
    run(env)
    b = env["a"].get(f"{API}/{r.json()['id']}").json()
    assert b["verification_status"] == "verified" and b["confirmed_recovery_point"] and b["verified_at"]
    archive = db.get(BackupRecord, b["id"]).archive_path
    assert hashlib.sha256((env["dir"] / archive).read_bytes()).hexdigest() == b["sha256"]
    acts = {e.action for e in db.scalars(select(AuditEvent))}
    assert {"backup.requested", "backup.completed", "backup.verified"} <= acts
    assert env["a"].get(f"{API}/overview").json()["verified"] == 1


def test_all_backup_types_and_honest_not_configured(env, db):
    assert make_backup(env, "website_config", "acme")["status"] == "completed"
    assert make_backup(env, "database", "crmdb")["status"] == "completed"
    s = make_backup(env, "saas_config", None)
    assert s["status"] == "completed" and s["resource_ref"] is None
    j = last_job(db, "backups.create")
    meta = json.loads(j.params["inline_files"]["tenant.json"])
    assert meta["tenant"]["slug"] and "password" not in json.dumps(meta).lower() and "token" not in json.dumps(meta).lower()
    r = env["a"].post(API, json={"backup_type": "database", "resource_ref": "shopdb"})
    assert r.status_code == 409 and r.json()["error"]["code"] == "not_configured"
    assert env["a"].post(API, json={"backup_type": "bogus", "resource_ref": "acme"}).status_code == 422
    assert env["a"].post(API, json={"backup_type": "website_files"}).status_code == 422
    assert env["a"].post(API, json={"backup_type": "website_files", "resource_ref": "other"}).status_code == 404


def test_failure_alerts_once(env, db):
    r = env["a"].post(API, json={"backup_type": "website_files", "resource_ref": "acme"})
    finish_job(db, last_job(db, "backups.create").id, "failed", error="disk_full: Not enough free space")
    b = env["a"].get(f"{API}/{r.json()['id']}").json()
    assert b["status"] == "failed" and "disk_full" in b["error"]
    env["a"].get(f"{API}/{r.json()['id']}")
    mails = db.scalars(select(EmailOutbox).where(EmailOutbox.kind == "backup_alert")).all()
    assert len(mails) == 1 and mails[0].to_email == "a@example.com" and "Reason" in mails[0].body
    assert any(e.action == "backup.failed" for e in db.scalars(select(AuditEvent)))
    assert env["a"].get(f"{API}/overview").json()["failed_7d"] == 1


def test_disk_unavailable_end_to_end(env, db):
    import shutil

    shutil.rmtree(env["dir"] / "backups")
    r = env["a"].post(API, json={"backup_type": "website_files", "resource_ref": "acme"})
    run(env)
    b = env["a"].get(f"{API}/{r.json()['id']}").json()
    assert b["status"] == "failed" and "disk_unavailable" in b["error"]
    assert not (env["dir"] / "backups").exists()


def test_disk_full_end_to_end(env, db, monkeypatch):
    from collections import namedtuple

    from kvl_agent.operations import backups as ab

    DU = namedtuple("DU", "total used free")
    monkeypatch.setattr(ab.shutil, "disk_usage", lambda p: DU(10, 9, 100))
    r = env["a"].post(API, json={"backup_type": "website_files", "resource_ref": "acme"})
    run(env)
    b = env["a"].get(f"{API}/{r.json()['id']}").json()
    assert b["status"] == "failed" and "disk_full" in b["error"]
    assert not [p for p in (env["dir"] / "backups").rglob("*") if p.is_file()]


def test_encryption_requires_key_then_works(env, db):
    r = env["a"].post(API, json={"backup_type": "website_files", "resource_ref": "acme", "encrypt": True})
    run(env)
    assert "key_missing" in env["a"].get(f"{API}/{r.json()['id']}").json()["error"]
    from kvl_agent.registry import validate
    from kvl_agent.testing import make_ctx

    ctx = make_ctx(env["dir"])
    op, p = validate("backups.init_key", {})
    key_id = op.handler(p, ctx)["key_id"]
    b = make_backup(env, encrypt=True)
    assert b["encrypted"] and b["key_id"] == key_id and b["verification_status"] == "verified"
    keyfile = (env["dir"] / "keys/backup.key").read_text().strip()
    assert keyfile not in json.dumps(b) and keyfile not in json.dumps([j.params for j in db.scalars(select(AgentJob))], default=str)


def test_checksum_mismatch_detected_and_alerted(env, db):
    b = make_backup(env)
    path = env["dir"] / db.get(BackupRecord, b["id"]).archive_path
    path.write_bytes(path.read_bytes() + b"corrupt")
    v = env["a"].post(f"{API}/{b['id']}/verify", json={})
    assert v.status_code == 202
    run(env)
    after = env["a"].get(f"{API}/{b['id']}").json()
    assert after["verification_status"] == "mismatch" and not after["confirmed_recovery_point"] and "Checksum mismatch" in after["error"]
    assert any(e.action == "backup.checksum_mismatch" for e in db.scalars(select(AuditEvent)))
    assert db.scalars(select(EmailOutbox).where(EmailOutbox.kind == "backup_alert")).first() is not None
    assert env["a"].post(f"{API}/{b['id']}/restore-preview", json={"mode": "isolated"}).status_code == 409


# ---------------------------------------------------------------- restore
def preview(env, b, mode="isolated", client="a"):
    r = env[client].post(f"{API}/{b['id']}/restore-preview", json={"mode": mode})
    assert r.status_code == 201, r.text
    return r.json()


def test_restore_preview_impact_and_isolated_restore(env, db):
    b = make_backup(env)
    p = preview(env, b)
    imp = p["impact"]
    assert p["status"] == "previewed" and imp["will_overwrite_live_data"] is False and imp["source_data_preserved"] and imp["requires_approval"] is False and imp["requires_typed_confirmation"] == "RESTORE acme"
    n = db.query(AgentJob).count()
    assert n == db.query(AgentJob).count()  # preview queued nothing restore-related
    assert not db.query(AgentJob).filter(AgentJob.operation == "backups.restore").count()
    s = env["a"].post(f"{API}/restores/{p['id']}/submit", json={"confirm": True})
    assert s.status_code == 400 and s.json()["error"]["code"] == "typed_confirmation_required"
    assert env["a"].post(f"{API}/restores/{p['id']}/submit", json={"confirmation": "RESTORE acme"}).status_code == 400
    (env["dir"] / ROOT / "index.html").write_text("<h1>live edited</h1>")
    s = env["a"].post(f"{API}/restores/{p['id']}/submit", json={"confirm": True, "confirmation": "RESTORE acme"})
    assert s.status_code == 202 and s.json()["status"] == "running"
    run(env)
    done = env["a"].get(f"{API}/restores/{p['id']}").json()
    assert done["status"] == "completed" and done["location"].startswith("restores/") and done["result"]["files"] == 2
    assert (env["dir"] / done["location"] / "data/index.html").read_text() == "<h1>acme v1</h1>"
    assert (env["dir"] / ROOT / "index.html").read_text() == "<h1>live edited</h1>"  # live data untouched
    assert {"restore.previewed", "restore.started", "restore.completed"} <= {e.action for e in db.scalars(select(AuditEvent))}


def test_overwrite_requires_verification_confirmation_typed_phrase_and_second_approver(env, db):
    unverified = make_backup(env, verify=False)
    p0 = preview(env, unverified, "overwrite")
    r = env["a"].post(f"{API}/restores/{p0['id']}/submit", json={"confirm": True, "confirm_overwrite": True, "confirmation": "RESTORE acme"})
    assert r.status_code == 409 and r.json()["error"]["code"] == "backup_unverified"
    run(env)  # verification job
    env["a"].get(f"{API}/{unverified['id']}")
    p = preview(env, unverified, "overwrite")
    body = {"confirm": True, "confirmation": "RESTORE acme"}
    assert env["a"].post(f"{API}/restores/{p['id']}/submit", json=body).json()["error"]["code"] == "overwrite_confirmation_required"
    assert env["a"].post(f"{API}/restores/{p['id']}/submit", json={**body, "confirm_overwrite": True, "confirmation": "restore acme"}).status_code == 400
    s = env["a"].post(f"{API}/restores/{p['id']}/submit", json={**body, "confirm_overwrite": True})
    assert s.status_code == 202 and s.json()["status"] == "pending_approval"
    assert not db.query(AgentJob).filter(AgentJob.operation == "backups.restore").count()
    assert env["a"].post(f"{API}/restores/{p['id']}/approve").status_code == 403  # customer cannot approve
    (env["dir"] / ROOT / "index.html").write_text("DAMAGED")
    ok = env["admin"].post(f"{API}/restores/{p['id']}/approve")
    assert ok.status_code == 202, ok.text
    j = last_job(db, "backups.restore")
    assert j.params["mode"] == "overwrite" and j.params["confirm_overwrite"] is True and j.params["target"] == ROOT
    run(env)
    done = env["a"].get(f"{API}/restores/{p['id']}").json()
    assert done["status"] == "completed" and done["result"]["previous_data_kept_at"]
    assert (env["dir"] / ROOT / "index.html").read_text() == "<h1>acme v1</h1>"
    assert (env["dir"] / done["result"]["previous_data_kept_at"] / "index.html").read_text() == "DAMAGED"
    assert "restore.approved" in {e.action for e in db.scalars(select(AuditEvent))}


def test_requester_cannot_approve_own_restore(env, db):
    b = make_backup(env)
    r = env["admin"].post(f"{API}/{b['id']}/restore-preview?tenant_id={env['ta'].id}", json={"mode": "overwrite"})
    assert r.status_code == 201
    rid = r.json()["id"]
    s = env["admin"].post(f"{API}/restores/{rid}/submit", json={"confirm": True, "confirm_overwrite": True, "confirmation": "RESTORE acme"})
    assert s.status_code == 202 and s.json()["status"] == "pending_approval"
    a = env["admin"].post(f"{API}/restores/{rid}/approve")
    assert a.status_code == 403 and a.json()["error"]["code"] == "separate_approver_required"


def test_overwrite_only_for_website_files(env):
    b = make_backup(env, "website_config", "acme")
    assert env["a"].post(f"{API}/{b['id']}/restore-preview", json={"mode": "overwrite"}).json()["error"]["code"] == "overwrite_not_supported"


def test_unauthorized_restore(env, make_client, make_user, db):
    b = make_backup(env)
    p = preview(env, b)
    make_user("sup@kvl.example.com", "support")
    s = make_client()
    login(s, "sup@kvl.example.com")
    assert s.post(f"{API}/restores/{p['id']}/submit", json={"confirm": True, "confirmation": "RESTORE acme"}).status_code == 403
    assert s.post(f"{API}/{b['id']}/restore-preview?tenant_id={env['ta'].id}", json={}).status_code == 403
    assert env["b"].post(f"{API}/restores/{p['id']}/submit", json={"confirm": True, "confirmation": "RESTORE acme"}).status_code == 404
    assert env["b"].post(f"{API}/{b['id']}/restore-preview", json={}).status_code == 404
    assert make_client().post(f"{API}/restores/{p['id']}/submit", json={}).status_code == 401
    assert not db.query(AgentJob).filter(AgentJob.operation == "backups.restore").count()


def test_restore_failure_alerts(env, db):
    b = make_backup(env)
    p = preview(env, b)
    env["a"].post(f"{API}/restores/{p['id']}/submit", json={"confirm": True, "confirmation": "RESTORE acme"})
    finish_job(db, last_job(db, "backups.restore").id, "failed", error="restore_integrity: files mismatch")
    d = env["a"].get(f"{API}/restores/{p['id']}").json()
    assert d["status"] == "failed" and "restore_integrity" in d["error"]
    assert any(e.action == "restore.failed" for e in db.scalars(select(AuditEvent)))


# ---------------------------------------------------------------- retention
def seed_records(env, n=8, verified=True):
    db = env["db"]
    now = datetime.now(UTC)
    rows = []
    for i in range(n):
        r = BackupRecord(tenant_id=env["ta"].id, backup_type="website_files", resource_ref="acme", status="completed", verification_status="verified" if verified and i == 0 else "unverified",
                         size_bytes=1000, sha256=f"{i:064x}", archive_path=f"backups/x/b{i}.tar.gz", server_id=env["srv"].id, finished_at=now - timedelta(days=i * 3))
        db.add(r)
        rows.append(r)
    db.commit()
    return rows


POLICY = {"name": "std", "keep_last": 2, "keep_daily": 1, "keep_weekly": 0, "keep_monthly": 0, "min_keep": 1}


def test_retention_simulation_deletes_nothing(env, db):
    seed_records(env)
    before_jobs, before_rows = db.query(AgentJob).count(), db.query(BackupRecord).count()
    r = env["a"].post(f"{API}/retention/simulate", json={"policy": POLICY, "backup_type": "website_files", "resource_ref": "acme"})
    assert r.status_code == 200, r.text
    out = r.json()
    assert out["dry_run"] is True and out["deleted"] == 0 and len(out["would_keep"]) == 2 and len(out["would_prune"]) == 6 and out["would_free_bytes"] == 6000
    assert out["recovery_point_id"] and out["prune_blocked_reason"] is None and all("outside" in x["reasons"][0] for x in out["would_prune"])
    db.expire_all()
    assert db.query(AgentJob).count() == before_jobs and db.query(BackupRecord).count() == before_rows
    assert db.query(BackupRecord).filter(BackupRecord.status == "completed").count() == 8


def test_simulation_blocks_without_verified_recovery_point(env, db):
    seed_records(env, verified=False)
    out = env["a"].post(f"{API}/retention/simulate", json={"policy": POLICY, "backup_type": "website_files", "resource_ref": "acme"}).json()
    assert out["recovery_point_id"] is None and "verified" in out["prune_blocked_reason"]


def test_retention_slots_and_min_keep(env, db):
    seed_records(env)
    p = env["a"].post(f"{API}/retention-policies", json={"name": "wide", "keep_last": 0, "keep_daily": 0, "keep_weekly": 0, "keep_monthly": 0, "min_keep": 3}).json()
    out = env["a"].post(f"{API}/retention/simulate", json={"policy_id": p["id"], "backup_type": "website_files", "resource_ref": "acme"}).json()
    assert len(out["would_keep"]) == 3 and len(out["would_prune"]) == 5


def test_prune_is_owner_only_confirmed_and_needs_recovery_point(env, db):
    seed_records(env, verified=False)
    p = env["a"].post(f"{API}/retention-policies", json=POLICY).json()
    body = {"policy_id": p["id"], "backup_type": "website_files", "resource_ref": "acme", "tenant_id": str(env["ta"].id)}
    assert env["a"].post(f"{API}/retention/prune", json={**body, "confirm": True, "confirmation": "PRUNE"}).status_code == 403  # customers lack backups:manage
    assert env["admin"].post(f"{API}/retention/prune", json=body).status_code == 400
    assert env["admin"].post(f"{API}/retention/prune", json={**body, "confirm": True, "confirmation": "nope"}).status_code == 400
    r = env["admin"].post(f"{API}/retention/prune", json={**body, "confirm": True, "confirmation": "PRUNE"})
    assert r.status_code == 409 and r.json()["error"]["code"] == "no_confirmed_recovery_point"
    assert not db.query(AgentJob).filter(AgentJob.operation == "backups.prune").count()


def test_prune_end_to_end_keeps_recovery_point(env, db):
    ids = [make_backup(env)["id"] for _ in range(4)]
    recs = {i: db.get(BackupRecord, i) for i in ids}
    # age them so ordering is unambiguous
    for n, i in enumerate(ids):
        recs[i].finished_at = datetime.now(UTC) - timedelta(days=(len(ids) - n) * 2)
    db.commit()
    p = env["admin"].post(f"{API}/retention-policies", json={**POLICY, "keep_last": 1, "keep_daily": 0, "min_keep": 1, "tenant_id": str(env["ta"].id)}).json()
    body = {"policy_id": p["id"], "backup_type": "website_files", "resource_ref": "acme", "tenant_id": str(env["ta"].id), "confirm": True, "confirmation": "PRUNE"}
    out = env["admin"].post(f"{API}/retention/prune", json=body)
    assert out.status_code == 200, out.text
    assert out.json()["queued"] == 3
    j = last_job(db, "backups.prune")
    assert j.params["confirm"] is True and len(j.params["delete"]) == 3
    run(env)
    db.expire_all()
    inv = {r["id"]: r for r in env["admin"].get(f"{API}?tenant_id={env['ta'].id}&limit=50").json()["items"]}
    assert sorted(r["status"] for r in inv.values()) == ["completed", "pruned", "pruned", "pruned"]
    kept = [i for i, r in inv.items() if r["status"] == "completed"][0]
    assert kept == ids[-1] and (env["dir"] / db.get(BackupRecord, kept).archive_path).exists()
    for i in ids[:-1]:
        assert not (env["dir"] / db.get(BackupRecord, i).archive_path).exists()
    assert "backup.prune_requested" in {e.action for e in db.scalars(select(AuditEvent))}


# ---------------------------------------------------------------- schedules / cron
def test_cron_parsing():
    assert svc.parse_cron("30 2 * * *")[0] == {30}
    assert svc.parse_cron("0 */6 * * 1-5")[1] == {0, 6, 12, 18}
    nxt = svc.next_run("30 2 * * *", datetime(2026, 1, 1, 2, 30, tzinfo=UTC))
    assert nxt == datetime(2026, 1, 2, 2, 30, tzinfo=UTC)
    assert svc.next_run("0 3 * * 0", datetime(2026, 1, 1, tzinfo=UTC)).weekday() == 6  # Sunday
    assert svc.next_run("0 3 29 2 *", datetime(2026, 1, 1, tzinfo=UTC)).year == 2028
    for bad in ["", "* * * *", "61 * * * *", "* 25 * * *", "*/5 * * * *", "a b c d e", "0 0 0 * *", "0 0 * 13 *", "5-2 * * * *", "0 0 * * 9"]:
        with pytest.raises(ValueError):
            svc.parse_cron(bad)


def test_schedule_crud_validation_and_audit(env, db):
    ok = {"name": "nightly", "backup_type": "website_files", "resource_ref": "acme", "cron": "30 2 * * *"}
    r = env["a"].post(f"{API}/schedules", json=ok)
    assert r.status_code == 201 and r.json()["next_run_at"]
    for bad in ["*/5 * * * *", "nonsense", "61 2 * * *"]:
        r2 = env["a"].post(f"{API}/schedules", json={**ok, "cron": bad})
        assert r2.status_code == 422 and r2.json()["error"]["code"] == "invalid_cron", bad
    assert env["a"].post(f"{API}/schedules", json={**ok, "resource_ref": "other"}).status_code == 404
    sid = r.json()["id"]
    assert env["a"].patch(f"{API}/schedules/{sid}", json={"cron": "0 4 * * *"}).json()["cron"] == "0 4 * * *"
    assert env["b"].patch(f"{API}/schedules/{sid}", json={"enabled": False}).status_code == 404
    assert env["b"].get(f"{API}/schedules").json()["items"] == []
    b = make_backup(env)
    assert env["a"].delete(f"{API}/schedules/{sid}").status_code == 204
    assert db.get(BackupRecord, b["id"]) is not None  # deleting a schedule never touches backups
    assert {"backup.schedule_created", "backup.schedule_updated", "backup.schedule_deleted"} <= {e.action for e in db.scalars(select(AuditEvent))}


def test_schedule_sweep_runs_due_only(env, db):
    ok = {"backup_type": "website_files", "resource_ref": "acme", "cron": "30 2 * * *"}
    s1 = env["a"].post(f"{API}/schedules", json={**ok, "name": "due"}).json()["id"]
    s2 = env["a"].post(f"{API}/schedules", json={**ok, "name": "later"}).json()["id"]
    s3 = env["a"].post(f"{API}/schedules", json={**ok, "name": "off", "enabled": False}).json()["id"]
    db.query(BackupSchedule).filter(BackupSchedule.id.in_([s1, s3])).update({"next_run_at": datetime.now(UTC) - timedelta(minutes=5)})
    db.commit()
    assert env["a"].post(f"{API}/schedules/sweep").status_code == 403
    out = env["admin"].post(f"{API}/schedules/sweep").json()
    assert len(out["started"]) == 1 and out["errors"] == []
    rec = db.get(BackupRecord, out["started"][0])
    assert rec.trigger == "schedule" and str(rec.schedule_id) == s1
    db.expire_all()
    assert db.get(BackupSchedule, s1).next_run_at > datetime.now(UTC) and db.get(BackupSchedule, s1).last_run_at


def test_schedule_failure_alerts(env, db):
    r = env["a"].post(f"{API}/schedules", json={"name": "db", "backup_type": "database", "resource_ref": "shopdb", "cron": "0 1 * * *"})
    db.query(BackupSchedule).update({"next_run_at": datetime.now(UTC) - timedelta(minutes=1)})
    db.commit()
    out = env["admin"].post(f"{API}/schedules/sweep").json()
    assert out["started"] == [] and "dump" in out["errors"][0]["error"]
    assert any(e.action == "backup.schedule_failed" for e in db.scalars(select(AuditEvent)))
    assert db.scalars(select(EmailOutbox).where(EmailOutbox.kind == "backup_alert")).first()


# ---------------------------------------------------------------- destinations / misc
def test_destinations_not_configured_and_credentials_never_returned(env, db):
    assert env["admin"].get(f"{API}/destinations?tenant_id={env['ta'].id}").json()["offsite_status"] == "not_configured"
    assert env["a"].get(f"{API}/overview").json()["offsite"]["status"] == "not_configured"
    s3 = {"name": "bucket", "kind": "s3", "config": {"bucket": "kvl-bk", "region": "ap-south-1", "endpoint": "https://s3.example.net"}, "credentials": {"access_key_id": "AKIAXXXXXXXX", "secret_access_key": "SUPERSECRETVALUE"}, "tenant_id": str(env["ta"].id)}
    r = env["admin"].post(f"{API}/destinations", json=s3)
    assert r.status_code == 201 and "SUPERSECRETVALUE" not in r.text and r.json()["has_credentials"] and r.json()["status"] == "configured_untested"
    row = db.query(BackupDestination).one()
    assert "SUPERSECRETVALUE" not in row.credentials_enc and "SUPERSECRETVALUE" not in str(row.config)
    assert "SUPERSECRETVALUE" not in env["admin"].get(f"{API}/destinations?tenant_id={env['ta'].id}").text
    assert all("SUPERSECRETVALUE" not in str(e.detail) for e in db.scalars(select(AuditEvent)))
    t = env["admin"].post(f"{API}/destinations/{r.json()['id']}/test").json()
    assert t["status"] == "not_implemented"
    assert env["admin"].post(f"{API}/destinations", json={"name": "x", "kind": "sftp", "config": {"host": "h"}, "tenant_id": str(env["ta"].id)}).status_code == 422
    assert env["admin"].post(f"{API}/destinations", json={"name": "x", "kind": "local", "config": {"path": "../../etc"}, "tenant_id": str(env["ta"].id)}).status_code == 422
    assert env["a"].post(f"{API}/destinations", json=s3).status_code == 403


def test_inventory_isolation_and_history(env, db):
    b = make_backup(env)
    assert env["b"].get(f"{API}/{b['id']}").status_code == 404
    assert env["b"].post(f"{API}/{b['id']}/verify", json={}).status_code == 404
    assert env["b"].get(API).json()["items"] == [] and env["b"].get(f"{API}/overview").json()["total"] == 0
    assert env["b"].get(f"{API}/history").json()["items"] == []
    h = env["a"].get(f"{API}/history").json()["items"]
    assert {"backups.create", "backups.verify"} <= {x["operation"] for x in h}
    assert env["admin"].get(f"{API}/overview").status_code == 200
    assert env["admin"].get(f"{API}?tenant_id={env['tb'].id}").json()["items"] == []


def test_only_completed_can_be_verified_and_no_key_material_in_views(env, db):
    r = env["a"].post(API, json={"backup_type": "website_files", "resource_ref": "acme"})
    assert env["a"].post(f"{API}/{r.json()['id']}/verify", json={}).status_code == 409
    assert "key" not in " ".join(k for k in r.json() if k != "key_id")
