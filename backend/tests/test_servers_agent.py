import sys
import threading
import time
import uuid
from datetime import timedelta
from pathlib import Path

import pytest
from sqlalchemy import select

from app.core.db import SessionLocal
from app.core.security import decrypt_secret
from app.models.agents import AgentCredential, AgentNonce
from app.models.identity import AuditEvent
from app.models.infra import AgentJob, Server
from app.models.monitoring import MetricSample
from app.ops import OPERATIONS
from app.services import agents
from tests.agent_helpers import API, METRICS, enroll, enrolled, register, signed
from tests.conftest import login

AGENT_DIR = Path(__file__).resolve().parents[2] / "agent"


def actions(db, prefix: str) -> list[str]:
    return [a for a in db.scalars(select(AuditEvent.action).where(AuditEvent.action.like(prefix + "%")))]


@pytest.fixture
def anon(make_client):
    return make_client()


# ---------------------------------------------------------------- authorization
def test_tenant_users_cannot_touch_server_endpoints(admin, client, make_client, make_user):
    sid, _ = register(client)
    make_user("cust@example.com", "customer")
    c = make_client()
    assert login(c, "cust@example.com").status_code == 200
    for method, path in [("get", "/servers"), ("get", f"/servers/{sid}"), ("get", "/jobs"), ("get", "/jobs/operations"), ("get", f"/servers/{sid}/credentials")]:
        assert getattr(c, method)(f"{API}{path}").status_code == 403, path
    assert c.post(f"{API}/servers", json={"name": "evil"}).status_code == 403
    assert c.post(f"{API}/servers/{sid}/credentials/rotate").status_code == 403
    assert c.post(f"{API}/servers/{sid}/jobs", json={"operation": "agent.ping"}).status_code == 403
    assert make_client().get(f"{API}/servers").status_code == 401


def test_permission_levels(admin, client, make_client, make_user):
    sid, _ = register(client)
    make_user("host@kvl.example.com", "hosting_admin")
    make_user("sup@kvl.example.com", "support")
    h, s = make_client(), make_client()
    assert login(h, "host@kvl.example.com").status_code == 200
    assert login(s, "sup@kvl.example.com").status_code == 200
    assert h.get(f"{API}/servers").status_code == 200
    assert h.post(f"{API}/servers", json={"name": "second"}).status_code == 201
    assert h.post(f"{API}/servers/{sid}/jobs", json={"operation": "agent.ping"}).status_code == 201
    # hosting admin lacks servers:manage: no credential rotation / revoke / disable
    for p in ("credentials/rotate", "credentials/revoke", "disable"):
        assert h.post(f"{API}/servers/{sid}/{p}").status_code == 403, p
    # support has no servers:* at all
    assert s.get(f"{API}/servers").status_code == 403
    assert s.post(f"{API}/servers/{sid}/jobs", json={"operation": "agent.ping"}).status_code == 403


# ---------------------------------------------------------------- registration & enrollment
def test_register_server_and_one_time_token(admin, client, anon, db):
    r = client.post(f"{API}/servers", json={"name": "imac-1", "hostname": "imac.local"})
    assert r.status_code == 201
    body = r.json()
    token = body["enrollment"]["token"]
    assert token in body["enrollment"]["command"] and body["server"]["status"] == "pending"
    assert body["server"]["availability"] == "unknown"
    assert token not in client.get(f"{API}/servers").text  # never returned again
    assert client.post(f"{API}/servers", json={"name": "imac-1"}).status_code == 409
    assert client.post(f"{API}/servers", json={"name": "bad;name"}).status_code == 422
    assert "server.registered" in actions(db, "server.")
    r1 = enroll(anon, token)
    assert r1.status_code == 200 and r1.json()["secret"] and r1.json()["key_id"].startswith("kvl_ak_")
    r2 = enroll(anon, token)  # single use
    assert r2.status_code == 401 and r2.json()["error"]["code"] == "invalid_enrollment_token"
    assert client.get(f"{API}/servers/{body['server']['id']}").json()["status"] == "registered"
    assert "agent.enrolled" in actions(db, "agent.")


def test_enrollment_token_expiry_and_garbage(admin, client, anon, db):
    sid, token = register(client)
    from app.models.agents import EnrollmentToken

    t = db.scalar(select(EnrollmentToken))
    t.expires_at = agents.utcnow() - timedelta(seconds=1)
    db.commit()
    assert enroll(anon, token).status_code == 401
    assert enroll(anon, "kvl_enr_" + "x" * 40).status_code == 401
    assert anon.post(f"{API}/agent/enroll", json={"enrollment_token": "short"}).status_code == 422
    # new token voids older unused ones
    _, t1 = register(client, "other")
    t2 = client.post(f"{API}/servers/{db.scalar(select(Server.id).where(Server.name == 'other'))}/enrollment-token").json()["token"]
    assert enroll(anon, t1).status_code == 401
    assert enroll(anon, t2).status_code == 200


def test_secret_stored_encrypted_and_never_listed(admin, client, anon, db):
    sid, key_id, secret = enrolled(client, anon)
    cred = db.scalar(select(AgentCredential).where(AgentCredential.key_id == key_id))
    assert cred.secret_enc != secret and secret not in cred.secret_enc
    assert decrypt_secret(cred.secret_enc) == secret
    listing = client.get(f"{API}/servers/{sid}/credentials")
    assert secret not in listing.text and "secret" not in listing.text
    assert secret not in client.get(f"{API}/servers/{sid}").text
    for a in db.scalars(select(AuditEvent)):
        assert secret not in str(a.detail)


# ---------------------------------------------------------------- agent authentication
def test_invalid_agent_auth(admin, client, anon):
    sid, key_id, secret = enrolled(client, anon)
    path = f"{API}/agent/heartbeat"
    assert anon.post(path, json={}).status_code == 401  # no headers
    assert signed(anon, "/heartbeat", {}, "kvl_ak_unknown", secret).status_code == 401
    assert signed(anon, "/heartbeat", {}, key_id, "wrong-secret").status_code == 401
    bad = anon.post(path, json={}, headers={"X-KVL-Key-Id": key_id, "X-KVL-Timestamp": "x", "X-KVL-Nonce": "n" * 16, "X-KVL-Signature": "0" * 64})
    assert bad.status_code == 401
    # unknown key and wrong secret are indistinguishable
    a = signed(anon, "/heartbeat", {}, "kvl_ak_unknown", secret).json()["error"]["code"]
    b = signed(anon, "/heartbeat", {}, key_id, "wrong-secret").json()["error"]["code"]
    assert a == b == "invalid_agent_auth"
    assert signed(anon, "/heartbeat", {}, key_id, secret).status_code == 200


def test_tampered_body_rejected(admin, client, anon):
    sid, key_id, secret = enrolled(client, anon)
    r = signed(anon, "/heartbeat", {"metrics": {"cpu_percent": 5}}, key_id, secret, sign_body=b'{"metrics":{"cpu_percent":1}}')
    assert r.status_code == 401
    # signature for a different path/method does not transfer
    r = signed(anon, "/jobs/poll", {"max": 1}, key_id, secret, method="POST")
    assert r.status_code == 200
    from app.services.agents import sign_request
    import json

    body = json.dumps({"max": 1}).encode()
    sig = sign_request(secret, "POST", f"{API}/agent/heartbeat", str(int(time.time())), "a" * 32, body)
    r = anon.post(f"{API}/agent/jobs/poll", content=body, headers={"Content-Type": "application/json", "X-KVL-Key-Id": key_id, "X-KVL-Timestamp": str(int(time.time())), "X-KVL-Nonce": "a" * 32, "X-KVL-Signature": sig})
    assert r.status_code == 401


def test_replayed_nonce_rejected(admin, client, anon, db):
    sid, key_id, secret = enrolled(client, anon)
    n = "f" * 32
    assert signed(anon, "/heartbeat", {}, key_id, secret, nonce=n).status_code == 200
    r = signed(anon, "/heartbeat", {}, key_id, secret, nonce=n)
    assert r.status_code == 401 and r.json()["error"]["code"] == "replayed_request"
    # invalid signatures do not consume nonces
    before = db.scalar(select(AgentNonce.id).order_by(AgentNonce.id.desc()))
    signed(anon, "/heartbeat", {}, key_id, "wrong", nonce="e" * 32)
    assert db.scalar(select(AgentNonce.id).order_by(AgentNonce.id.desc())) == before
    # purge removes old nonces
    row = db.scalar(select(AgentNonce).where(AgentNonce.nonce == n))
    row.created_at = agents.utcnow() - timedelta(hours=1)
    db.commit()
    assert agents.purge_nonces(db) >= 1


def test_expired_and_future_timestamp_rejected(admin, client, anon):
    sid, key_id, secret = enrolled(client, anon)
    for skew in (-340, 340, -86400):
        r = signed(anon, "/heartbeat", {}, key_id, secret, ts=int(time.time()) + skew)
        assert r.status_code == 401 and r.json()["error"]["code"] == "stale_timestamp"
    assert signed(anon, "/heartbeat", {}, key_id, secret, ts=int(time.time()) - 200).status_code == 200


def test_revoked_credential_and_rotation(admin, client, anon, db):
    sid, key_id, secret = enrolled(client, anon)
    rot = client.post(f"{API}/servers/{sid}/credentials/rotate")
    assert rot.status_code == 200 and rot.json()["shown_once"]
    new_id, new_secret = rot.json()["key_id"], rot.json()["secret"]
    assert signed(anon, "/heartbeat", {}, key_id, secret).status_code == 401  # old one revoked
    assert signed(anon, "/heartbeat", {}, new_id, new_secret).status_code == 200
    assert client.post(f"{API}/servers/{sid}/credentials/revoke").json()["revoked"] == 1
    assert signed(anon, "/heartbeat", {}, new_id, new_secret).status_code == 401
    creds = client.get(f"{API}/servers/{sid}/credentials").json()
    assert [c["status"] for c in creds] == ["revoked", "revoked"]
    acts = actions(db, "agent.credential")
    assert "agent.credential_rotated" in acts and "agent.credential_revoked" in acts


def test_disabled_server_rejected(admin, client, anon, db):
    sid, key_id, secret = enrolled(client, anon)
    assert client.post(f"{API}/servers/{sid}/disable").json()["status"] == "disabled"
    assert signed(anon, "/heartbeat", {}, key_id, secret).status_code == 401  # credentials revoked too
    assert client.post(f"{API}/servers/{sid}/credentials/rotate").status_code == 409
    assert client.post(f"{API}/servers/{sid}/jobs", json={"operation": "agent.ping"}).status_code == 409
    assert "server.disabled" in actions(db, "server.")
    assert client.post(f"{API}/servers/{sid}/enable").json()["status"] == "registered"


# ---------------------------------------------------------------- heartbeat
def test_heartbeat_stores_state_and_validates(admin, client, anon, db):
    sid, key_id, secret = enrolled(client, anon)
    r = signed(anon, "/heartbeat", {"agent_version": "1.0.1", "metrics": METRICS, "services": [{"name": "nginx", "state": "running"}]}, key_id, secret)
    assert r.status_code == 200 and r.json()["ok"] and r.headers["X-KVL-Response-Signature"]
    s = client.get(f"{API}/servers/{sid}").json()
    assert s["agent_version"] == "1.0.1" and s["availability"] == "connected" and s["last_heartbeat_at"]
    assert db.scalar(select(MetricSample).where(MetricSample.server_id == uuid.UUID(sid))).cpu_percent == 12.5
    assert signed(anon, "/heartbeat", {"metrics": {"cpu_percent": 150}}, key_id, secret).status_code == 422
    assert signed(anon, "/heartbeat", {"metrics": {"cpu_percent": 5, "shell": "x"}}, key_id, secret).status_code == 422
    assert signed(anon, "/heartbeat", {"services": [{"name": "Bad Name!", "state": "running"}]}, key_id, secret).status_code == 422


# ---------------------------------------------------------------- jobs
def queue(client, sid, op="agent.ping", params=None):
    return client.post(f"{API}/servers/{sid}/jobs", json={"operation": op, "params": params or {}})


def poll(anon, key_id, secret, n=1):
    r = signed(anon, "/jobs/poll", {"max": n}, key_id, secret)
    assert r.status_code == 200, r.text
    return r.json()["jobs"]


def test_job_lifecycle_and_audit(admin, client, anon, db):
    sid, key_id, secret = enrolled(client, anon)
    jid = queue(client, sid).json()["id"]
    assert client.get(f"{API}/jobs/{jid}").json()["status"] == "queued"
    jobs = poll(anon, key_id, secret)
    assert [j["id"] for j in jobs] == [jid] and jobs[0]["operation"] == "agent.ping"
    assert poll(anon, key_id, secret) == []  # claimed exactly once
    assert client.get(f"{API}/jobs/{jid}").json()["status"] == "running"
    r = signed(anon, f"/jobs/{jid}/result", {"status": "succeeded", "summary": {"pong": True}, "duration_ms": 5}, key_id, secret)
    assert r.status_code == 200
    d = client.get(f"{API}/jobs/{jid}").json()
    assert d["status"] == "succeeded" and d["result"]["pong"] is True and d["finished_at"] and d["started_at"] and d["attempts"] == 1
    assert signed(anon, f"/jobs/{jid}/result", {"status": "failed"}, key_id, secret).status_code == 409
    ev = [a for a in actions(db, "job.")]
    assert ev.count("job.queued") == 1 and "job.started" in ev and "job.succeeded" in ev


def test_unknown_operation_and_invalid_params_rejected(admin, client, anon):
    sid, *_ = enrolled(client, anon)
    r = queue(client, sid, "system.exec", {"cmd": "id"})
    assert r.status_code == 422 and r.json()["error"]["code"] == "unknown_operation"
    for op in ("shell", "bash", "exec", "run", "agent.run", "agent.shell"):
        assert queue(client, sid, op).status_code == 422
    r = queue(client, sid, "agent.ping", {"cmd": "rm -rf /"})
    assert r.status_code == 422 and r.json()["error"]["code"] == "invalid_operation_params"
    assert client.get(f"{API}/jobs").json()["total"] == 0


def test_no_arbitrary_command_operation_in_allowlist():
    assert set(OPERATIONS) >= {"agent.ping", "agent.collect_metrics", "agent.service_status"}
    for name, schema in OPERATIONS.items():
        low = name.lower()
        assert not any(w in low for w in ("shell", "exec", "bash", "command", "eval", "sudo")), name
        fields = schema.model_fields
        assert not any(f in fields for f in ("cmd", "command", "argv", "shell", "script")), name


def test_result_cannot_be_reported_for_other_servers_or_unclaimed(admin, client, anon, make_client):
    sid, k1, s1 = enrolled(client, anon, "one")
    sid2, k2, s2 = enrolled(client, make_client(), "two")
    jid = queue(client, sid).json()["id"]
    # not claimed yet -> invalid state
    assert signed(anon, f"/jobs/{jid}/result", {"status": "succeeded"}, k1, s1).status_code == 409
    poll(anon, k1, s1)
    # server two cannot see or complete server one's job
    assert signed(anon, f"/jobs/{jid}/result", {"status": "succeeded"}, k2, s2).status_code == 404
    assert poll(make_client(), k2, s2) == []
    assert signed(anon, f"/jobs/{uuid.uuid4()}/result", {"status": "succeeded"}, k1, s1).status_code == 404
    assert signed(anon, f"/jobs/{jid}/result", {"status": "queued"}, k1, s1).status_code == 422


def test_result_redacted_and_size_limited(admin, client, anon):
    sid, k, s = enrolled(client, anon)
    jid = queue(client, sid).json()["id"]
    poll(anon, k, s)
    summary = {"ok": 1, "db_password": "hunter2", "nested": {"api_token": "abc"}, "log": "connect password=hunter2 failed"}
    assert signed(anon, f"/jobs/{jid}/result", {"status": "failed", "summary": summary, "error": "Bearer abcdefghijklmnop leaked token=zzz"}, k, s).status_code == 200
    d = client.get(f"{API}/jobs/{jid}").json()
    raw = str(d)
    assert "hunter2" not in raw and "abcdefghijklmnop" not in raw and "zzz" not in raw
    assert d["result"]["ok"] == 1 and d["result"]["db_password"] == "[redacted]"
    jid2 = queue(client, sid).json()["id"]
    poll(anon, k, s)
    big = {"lines": ["x" * 1000 for _ in range(100)]}
    assert signed(anon, f"/jobs/{jid2}/result", {"status": "succeeded", "summary": big}, k, s).status_code == 200
    d2 = client.get(f"{API}/jobs/{jid2}").json()
    assert d2["result"]["truncated"] is True and len(str(d2["result"])) < agents.RESULT_MAX_BYTES


def test_cancel_and_state_machine(admin, client, anon, db):
    sid, k, s = enrolled(client, anon)
    j1 = queue(client, sid).json()["id"]
    assert client.post(f"{API}/jobs/{j1}/cancel").json()["status"] == "cancelled"
    assert poll(anon, k, s) == []
    assert client.post(f"{API}/jobs/{j1}/cancel").status_code == 409  # terminal
    j2 = queue(client, sid).json()["id"]
    poll(anon, k, s)
    assert client.post(f"{API}/jobs/{j2}/cancel").json()["status"] == "cancelled"
    assert signed(anon, f"/jobs/{j2}/result", {"status": "succeeded"}, k, s).status_code == 409
    assert actions(db, "job.").count("job.cancelled") == 2
    assert client.post(f"{API}/jobs/{uuid.uuid4()}/cancel").status_code == 404


def test_transition_matrix(db):
    srv = Server(name="sm", status="registered")
    db.add(srv)
    db.flush()
    states = list(agents.TRANSITIONS)
    for a in states:
        for b in states:
            j = AgentJob(server_id=srv.id, operation="agent.ping", status=a, attempts=0)
            db.add(j)
            db.flush()
            if b in agents.TRANSITIONS[a]:
                agents.transition(db, j, b)
                assert j.status == b
            else:
                with pytest.raises(Exception) as ei:
                    agents.transition(db, j, b)
                assert getattr(ei.value, "status_code", None) == 409
    assert agents.TRANSITIONS["queued"] == {"running", "cancelled"}
    assert all(not agents.TRANSITIONS[t] for t in ("succeeded", "failed", "timed_out", "cancelled"))
    db.rollback()


def test_timeout_sweeper(admin, client, anon, db):
    sid, k, s = enrolled(client, anon)
    jid = queue(client, sid).json()["id"]
    poll(anon, k, s)
    now = agents.utcnow()
    assert agents.sweep_timed_out_jobs(db, now) == 0  # still within timeout
    assert agents.sweep_timed_out_jobs(db, now + timedelta(seconds=10 + agents.TIMEOUT_GRACE_SECONDS + 1)) == 1
    db.commit()
    d = client.get(f"{API}/jobs/{jid}").json()
    assert d["status"] == "timed_out" and "timeout" in d["error"]
    assert "job.timed_out" in actions(db, "job.")
    assert agents.sweep_timed_out_jobs(db, now + timedelta(days=1)) == 0
    assert signed(anon, f"/jobs/{jid}/result", {"status": "succeeded"}, k, s).status_code == 409


def test_concurrent_claim_skip_locked(admin, client, anon):
    sid, k, s = enrolled(client, anon)
    ids = {queue(client, sid).json()["id"] for _ in range(3)}
    a, b = SessionLocal(), SessionLocal()
    try:
        ja = agents.claim_jobs(a, uuid.UUID(sid), 2)  # a holds row locks, uncommitted
        jb = agents.claim_jobs(b, uuid.UUID(sid), 3)
        got_a, got_b = {str(j.id) for j in ja}, {str(j.id) for j in jb}
        assert len(got_a) == 2 and got_a.isdisjoint(got_b) and got_a | got_b == ids
        a.commit()
        b.commit()
    finally:
        a.close()
        b.close()


def test_list_jobs_filters_and_pagination(admin, client, anon):
    sid, *_ = enrolled(client, anon)
    for _ in range(3):
        queue(client, sid)
    queue(client, sid, "agent.collect_metrics")
    assert client.get(f"{API}/jobs", params={"page_size": 2}).json()["total"] == 4
    assert len(client.get(f"{API}/jobs", params={"page_size": 2, "page": 2}).json()["items"]) == 2
    assert client.get(f"{API}/jobs", params={"operation": "agent.collect_metrics"}).json()["total"] == 1
    assert client.get(f"{API}/jobs", params={"status": "bogus"}).status_code == 422
    assert client.get(f"{API}/jobs", params={"server_id": sid, "status": "queued"}).json()["total"] == 4
    ops = client.get(f"{API}/jobs/operations").json()
    assert {o["name"] for o in ops} == set(OPERATIONS)


# ---------------------------------------------------------------- real agent against the real API
def test_real_agent_end_to_end(admin, client, anon, tmp_path, db):
    sys.path.insert(0, str(AGENT_DIR))
    from kvl_agent import registry
    from kvl_agent.client import ControlPlaneClient, UntrustedResponse
    from kvl_agent.config import AgentConfig, save_credentials
    from kvl_agent.service import Agent

    sid, token = register(client)
    cfg = AgentConfig(api_url="http://testserver/api/v1", data_dir=tmp_path, allow_insecure_http=True)
    cp = ControlPlaneClient(cfg, http=anon)
    res = cp.enroll(token, "imac.local", "1.0.0", {"system": "Linux"})
    save_credentials(cfg, res["key_id"], res["secret"])
    cfg.validate()
    agent = Agent(cfg, client=cp)
    j1 = queue(client, sid, "agent.collect_metrics").json()["id"]
    j2 = queue(client, sid, "agent.service_status").json()["id"]
    j3 = queue(client, sid, "agent.ping").json()["id"]
    out = agent.run_once()
    assert out["heartbeat"] is True and out["jobs"] == 2  # max_concurrency=2
    out = agent.run_once()
    assert out["jobs"] == 1
    for j in (j1, j2, j3):
        d = client.get(f"{API}/jobs/{j}").json()
        assert d["status"] == "succeeded", d
    assert "metrics" in client.get(f"{API}/jobs/{j1}").json()["result"]
    states = {x["name"]: x["state"] for x in client.get(f"{API}/servers/{sid}/services").json()}
    assert set(states) >= {"nginx", "php-fpm", "mariadb", "redis", "pm2"}
    assert "failed" not in states.values()
    assert db.scalar(select(MetricSample).where(MetricSample.server_id == uuid.UUID(sid))) is not None
    assert client.get(f"{API}/servers/{sid}").json()["availability"] == "connected"

    # a response whose signature does not verify is never acted on
    class Tamper:
        def request(self, *a, **kw):
            r = anon.request(*a, **kw)
            r.headers["X-KVL-Response-Signature"] = "0" * 64
            return r

        def close(self):
            pass

    bad = ControlPlaneClient(cfg, http=Tamper())
    with pytest.raises(UntrustedResponse):
        bad.poll(1)
    queue(client, sid)
    evil = Agent(cfg, client=bad)
    assert evil.poll_once() == 0  # fail closed: nothing executed

    # revoked credential -> agent stops polling
    client.post(f"{API}/servers/{sid}/credentials/revoke")
    assert agent.heartbeat_once() is False and agent.authorized is False
    assert agent.poll_once() == 0
    assert registry.OPERATIONS  # registry stayed loaded
