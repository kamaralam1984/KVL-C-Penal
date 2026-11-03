import json
import pathlib
import time
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import func, select

from app.core.config import get_settings
from app.models.automation import InboundEvent, OutboxEvent, WorkflowExecution
from app.models.billing import Invoice
from app.models.identity import EmailOutbox
from app.models.tenant import Tenant
from app.services import automation as svc
from app.services import billing as bsvc
from app.services.tenants import provision_tenant
from tests._billing_utils import make_plan
from tests.conftest import login

SECRET = "n8n-test-shared-secret"
URL = "/api/v1/automation/inbound"


@pytest.fixture
def n8n(monkeypatch):
    s = get_settings()
    monkeypatch.setattr(s, "n8n_webhook_secret", SECRET)
    monkeypatch.setattr(s, "n8n_base_url", "http://127.0.0.1:5678")


def signed(client, body: dict | bytes, key="idem-1", ts=None, secret=SECRET, extra_headers=None, tamper=False):
    raw = body if isinstance(body, bytes) else json.dumps(body).encode()
    ts = str(int(time.time()) if ts is None else ts)
    sig = svc.sign(secret, ts, raw)
    if tamper:
        raw = raw + b" "
    headers = {"X-KVL-Timestamp": ts, "X-KVL-Signature": sig, "Content-Type": "application/json", **({"X-KVL-Idempotency-Key": key} if key else {}), **(extra_headers or {})}
    return client.post(URL, content=raw, headers=headers)


class FakeSender:
    def __init__(self, codes=None, raises=False):
        self.codes, self.raises, self.calls = list(codes or []), raises, []

    def send(self, path, body, headers):
        self.calls.append((path, body, headers))
        if self.raises:
            raise ConnectionError("n8n down")
        return self.codes.pop(0) if self.codes else 200


@pytest.fixture
def tenants(db):
    a = provision_tenant(db, "Alpha Co", None, "alpha@a.example.com", status="active")
    b = provision_tenant(db, "Beta Co", None, "beta@b.example.com", status="active")
    db.commit()
    return a, b


# --------------------------------------------------------------------------------------------- inbound auth
def test_invalid_signature_variants_rejected(client, n8n, db, tenants):
    a, _ = tenants
    body = {"event_type": "workflow.execution", "data": {"workflow_key": "backup_alerts", "status": "succeeded"}}
    assert signed(client, body, secret="wrong").status_code == 401
    assert signed(client, body, tamper=True).status_code == 401
    assert signed(client, body, ts=int(time.time()) - 3600).json()["error"]["code"] == "stale_request"
    assert signed(client, body, ts=int(time.time()) + 3600).status_code == 401
    assert client.post(URL, json=body, headers={"X-KVL-Idempotency-Key": "k"}).status_code == 401
    assert client.post(URL, content=b"{}", headers={"X-KVL-Timestamp": "abc", "X-KVL-Signature": "x", "X-KVL-Idempotency-Key": "k"}).status_code == 401
    assert signed(client, body, key=None).status_code == 400
    assert db.scalar(select(func.count()).select_from(InboundEvent)) == 0
    assert signed(client, body).status_code == 200


def test_inbound_not_configured_without_secret(client, db):
    r = client.post(URL, content=b"{}", headers={"X-KVL-Timestamp": str(int(time.time())), "X-KVL-Signature": "x", "X-KVL-Idempotency-Key": "k"})
    assert r.status_code == 503 and r.json()["error"]["code"] == "not_configured"


def test_replayed_event_is_idempotent_and_key_reuse_conflicts(client, n8n, db, tenants):
    a, _ = tenants
    body = {"event_type": "notification.send", "data": {"tenant_id": str(a.id), "template": "welcome", "subject": "Welcome", "message": "Hello"}}
    assert signed(client, body, key="k-1").json()["status"] == "processed"
    assert signed(client, body, key="k-1").json()["status"] == "duplicate"
    assert signed(client, body, key="k-1").json()["status"] == "duplicate"
    assert db.scalar(select(func.count()).select_from(EmailOutbox)) == 1  # delivered exactly once
    other = {**body, "data": {**body["data"], "subject": "Different"}}
    assert signed(client, other, key="k-1").status_code == 409
    assert signed(client, other, key="k-2").json()["status"] == "processed"
    assert db.scalar(select(func.count()).select_from(EmailOutbox)) == 2


def test_inbound_schema_validation_and_allowlist(client, n8n, db, tenants):
    a, _ = tenants
    base = {"template": "welcome", "subject": "Hi", "message": "m", "tenant_id": str(a.id)}
    # workflows cannot choose recipients or send arbitrary fields
    assert signed(client, {"event_type": "notification.send", "data": {**base, "to": "attacker@evil.example.com"}}, key="a").status_code == 422
    assert signed(client, {"event_type": "notification.send", "data": {**base, "template": "custom"}}, key="b").status_code == 422
    assert signed(client, {"event_type": "notification.send", "data": {k: v for k, v in base.items() if k != "tenant_id"}}, key="c").status_code == 422
    assert signed(client, {"event_type": "notification.send", "data": base, "unexpected": 1}, key="d").status_code == 422
    assert signed(client, b"not json", key="e").status_code == 422
    # anything that mutates business state is simply not accepted from workflows
    for et in ("billing.mark_paid", "invoice.pay", "tenant.suspend", "user.create", "role.grant", "subscription.activate"):
        r = signed(client, {"event_type": et, "data": {"tenant_id": str(a.id)}}, key=f"x-{et}")
        assert r.status_code == 422 and r.json()["error"]["code"] == "unsupported_event"
    assert signed(client, {"event_type": "notification.send", "data": {**base, "tenant_id": "00000000-0000-0000-0000-000000000000"}}, key="f").status_code == 404
    assert db.scalar(select(func.count()).select_from(EmailOutbox)) == 0


def test_notification_goes_only_to_the_target_tenant(client, n8n, db, tenants):
    a, b = tenants
    r = signed(client, {"event_type": "notification.send", "data": {"tenant_id": str(a.id), "template": "invoice_reminder", "subject": "Due", "message": "Pay soon"}}, key="n1")
    assert r.status_code == 200
    mail = db.scalars(select(EmailOutbox)).all()
    assert [(m.to_email, m.tenant_id) for m in mail] == [("alpha@a.example.com", a.id)]
    assert not [m for m in mail if m.to_email == "beta@b.example.com"]


def test_activation_via_n8n_requires_verified_payment(client, n8n, db, make_user, make_client, admin):
    make_plan(client, "pro")
    t = provision_tenant(db, "Pending Co", None, "p@p.example.com", status="pending")
    db.commit()
    make_user("p@p.example.com", "customer", tenant=t)
    c = make_client()
    login(c, "p@p.example.com")
    body = {"event_type": "tenant.activate", "data": {"tenant_id": str(t.id)}}
    r = signed(client, body, key="act-1")
    assert r.status_code == 409 and r.json()["error"]["code"] == "payment_not_verified"
    inv = c.post("/api/v1/billing/checkout", json={"plan_code": "pro"}).json()["invoice"]  # created, unpaid
    assert signed(client, body, key="act-2").status_code == 409
    db.expire_all()
    assert db.get(Tenant, t.id).status == "pending"
    client.post(f"/api/v1/billing/invoices/{inv['id']}/manual-payment", json={"reference": "UTR-ACT"})
    db.expire_all()
    assert db.get(Tenant, t.id).status == "active"  # activated by the backend on verified payment
    assert signed(client, body, key="act-3").json()["activated"] is False  # already active: harmless no-op
    # a different tenant with no payment still cannot be activated through the same channel
    other = provision_tenant(db, "Other Co", None, "o@o.example.com", status="pending")
    db.commit()
    assert signed(client, {"event_type": "tenant.activate", "data": {"tenant_id": str(other.id)}}, key="act-4").status_code == 409


# ---------------------------------------------------------------------------------------------- emit_event
def test_emit_event_validation_and_scoping(db, tenants):
    a, b = tenants
    good = {"tenant_id": a.id, "tenant_name": "Alpha Co"}
    ev = svc.emit_event(db, "tenant.created", good, tenant_id=a.id, idempotency_key="tc-a")
    assert ev.status == "pending" and ev.payload["tenant_id"] == str(a.id) and len(ev.correlation_id) >= 16
    assert svc.emit_event(db, "tenant.created", good, tenant_id=a.id, idempotency_key="tc-a") is None  # duplicate key
    with pytest.raises(ValueError):
        svc.emit_event(db, "tenant.created", good, tenant_id=b.id)  # payload tenant != event tenant
    with pytest.raises(ValueError):
        svc.emit_event(db, "tenant.created", good)  # tenant event without tenant
    with pytest.raises(ValueError):
        svc.emit_event(db, "tenant.created", {**good, "secret": "x"}, tenant_id=a.id)  # unknown field
    with pytest.raises(ValueError):
        svc.emit_event(db, "nope.event", {})
    with pytest.raises(Exception):
        svc.emit_event(db, "tenant.created", {"tenant_id": a.id}, tenant_id=a.id)  # missing field
    with pytest.raises(ValueError):
        svc.emit_event(db, "server.health", {"server_id": "s1", "severity": "critical", "message": "down"}, tenant_id=a.id)


def test_disabled_workflow_emits_nothing(db, client, admin, tenants):
    a, _ = tenants
    assert client.post("/api/v1/automation/workflows/tenant_onboarding/enabled", json={"enabled": False}).json()["enabled"] is False
    assert svc.emit_event(db, "tenant.created", {"tenant_id": a.id, "tenant_name": "x"}, tenant_id=a.id) is None
    assert client.post("/api/v1/automation/workflows/bogus/enabled", json={"enabled": True}).status_code == 404


# -------------------------------------------------------------------------------------------- dispatcher
def mk_event(db, tenant, key="tc"):
    ev = svc.emit_event(db, "tenant.created", {"tenant_id": tenant.id, "tenant_name": tenant.name}, tenant_id=tenant.id, idempotency_key=key)
    db.commit()
    return ev


def test_dispatch_success_signs_and_scopes(db, n8n, tenants):
    a, _ = tenants
    ev = mk_event(db, a)
    fs = FakeSender()
    out = svc.dispatch_due(db, fs)
    assert out == {"delivered": 1, "retried": 0, "dead": 0}
    path, body, h = fs.calls[0]
    assert path == "/webhook/kvl-tenant-onboarding"
    assert h["X-KVL-Signature"] == svc.sign(SECRET, h["X-KVL-Timestamp"], body) and SECRET.encode() not in body
    assert h["X-KVL-Correlation-Id"] == ev.correlation_id and h["X-KVL-Event-Id"] == str(ev.id)
    j = json.loads(body)
    assert j["tenant_id"] == str(a.id) and j["data"]["tenant_name"] == "Alpha Co"
    db.expire_all()
    assert db.get(OutboxEvent, ev.id).status == "delivered"
    ex = db.scalar(select(WorkflowExecution))
    assert ex.workflow_key == "tenant_onboarding" and ex.status == "running" and ex.correlation_id == ev.correlation_id
    assert svc.dispatch_due(db, fs)["delivered"] == 0  # nothing left


def test_dispatch_retry_backoff_then_dead_letter_and_escalation(db, n8n, tenants):
    a, _ = tenants
    ev = mk_event(db, a)
    t0 = datetime.now(UTC)
    assert [svc.backoff_seconds(n) for n in range(1, 8)] == [30, 60, 120, 240, 480, 960, 1920]
    assert svc.backoff_seconds(30) == 3600
    fs = FakeSender(raises=True)
    delays = []
    now = t0
    for attempt in range(1, 6):
        out = svc.dispatch_due(db, fs, now=now)
        assert out["retried"] == 1 and out["dead"] == 0
        db.expire_all()
        e = db.get(OutboxEvent, ev.id)
        assert e.attempts == attempt and e.status == "pending" and "ConnectionError" in e.last_error
        delays.append(int((e.next_attempt_at - now).total_seconds()))
        # not yet due -> untouched
        assert svc.dispatch_due(db, fs, now=now + timedelta(seconds=delays[-1] - 1))["retried"] == 0
        now = e.next_attempt_at
    assert delays == [30, 60, 120, 240, 480]
    out = svc.dispatch_due(db, fs, now=now)
    assert out["dead"] == 1
    db.expire_all()
    e = db.get(OutboxEvent, ev.id)
    assert e.status == "dead" and e.attempts == 6
    assert db.scalar(select(WorkflowExecution.status).where(WorkflowExecution.outbox_event_id == ev.id)) == "failed"
    esc = db.scalar(select(OutboxEvent).where(OutboxEvent.event_type == "automation.workflow_failed"))
    assert esc and esc.payload["workflow_key"] == "tenant_onboarding" and esc.tenant_id is None and esc.payload["correlation_id"] == ev.correlation_id
    # non-2xx counts as failure too
    ev2 = mk_event(db, a, "tc2")
    svc.dispatch_due(db, FakeSender(codes=[500, 500, 500]), now=datetime.now(UTC) + timedelta(seconds=5))
    db.expire_all()
    assert "HTTP 500" in db.get(OutboxEvent, ev2.id).last_error


def test_dead_escalation_does_not_loop(db, n8n):
    ev = svc.emit_event(db, "automation.workflow_failed", {"workflow_key": "backup_alerts", "correlation_id": "c1", "error": "x"})
    db.commit()
    ev.max_attempts = 1
    db.commit()
    svc.dispatch_due(db, FakeSender(raises=True))
    db.expire_all()
    assert db.get(OutboxEvent, ev.id).status == "dead"
    assert db.scalar(select(func.count()).select_from(OutboxEvent).where(OutboxEvent.event_type == "automation.workflow_failed")) == 1


def test_dispatch_not_configured_leaves_events_pending(db, tenants):
    a, _ = tenants
    ev = mk_event(db, a)
    out = svc.dispatch_due(db)
    assert out.get("status_not_configured") == 1
    db.expire_all()
    assert db.get(OutboxEvent, ev.id).status == "pending" and db.get(OutboxEvent, ev.id).attempts == 0


def test_workflow_failure_reported_by_n8n_escalates_once(client, n8n, db, tenants):
    a, _ = tenants
    ev = mk_event(db, a)
    svc.dispatch_due(db, FakeSender())
    body = {"event_type": "workflow.execution", "correlation_id": ev.correlation_id, "data": {"workflow_key": "tenant_onboarding", "status": "failed", "execution_id": "99", "error": "SMTP credentials rejected"}}
    r = signed(client, body, key="wf-1").json()
    assert r["status"] == "processed" and r["escalated"] is True
    assert signed(client, body, key="wf-1").json()["status"] == "duplicate"
    db.expire_all()
    ex = db.scalar(select(WorkflowExecution).where(WorkflowExecution.correlation_id == ev.correlation_id))
    assert ex.status == "failed" and ex.external_execution_id == "99" and ex.error.startswith("SMTP")
    assert db.scalar(select(func.count()).select_from(OutboxEvent).where(OutboxEvent.event_type == "automation.workflow_failed")) == 1
    # a failing escalation workflow never escalates itself
    esc = {"event_type": "workflow.execution", "data": {"workflow_key": "failed_workflow_escalation", "status": "failed", "error": "x"}}
    assert signed(client, esc, key="wf-2").json()["escalated"] is False
    assert db.scalar(select(func.count()).select_from(OutboxEvent).where(OutboxEvent.event_type == "automation.workflow_failed")) == 1
    assert signed(client, {"event_type": "workflow.execution", "data": {"workflow_key": "unknown_wf", "status": "failed"}}, key="wf-3").status_code == 422
    ok = {"event_type": "workflow.execution", "data": {"workflow_key": "tenant_onboarding", "status": "succeeded"}}
    assert signed(client, ok, key="wf-4").json()["escalated"] is False


# ----------------------------------------------------------------------------------- scheduler / leakage
def open_invoice(db, tenant, days_until_due):
    inv = bsvc.create_invoice(db, tenant, "manual", [bsvc.line("Hosting", 100000)], due_at=datetime.now(UTC) + timedelta(days=days_until_due, hours=1))
    db.commit()
    return inv


def test_no_cross_tenant_notification_leakage(db, n8n, tenants):
    a, b = tenants
    ia, ib = open_invoice(db, a, 1), open_invoice(db, b, 2)
    assert svc.emit_invoice_due_reminders(db) == 2
    assert svc.emit_invoice_due_reminders(db) == 0  # idempotent per invoice+threshold
    db.commit()
    evs = {e.tenant_id: e for e in db.scalars(select(OutboxEvent).where(OutboxEvent.event_type == "invoice.due_reminder"))}
    ea, eb = evs[a.id], evs[b.id]
    assert ea.payload["recipient_email"] == "alpha@a.example.com" and ea.payload["invoice_number"] == ia.number
    assert eb.payload["recipient_email"] == "beta@b.example.com" and eb.payload["invoice_number"] == ib.number
    ja, jb = json.dumps(ea.payload), json.dumps(eb.payload)
    assert "beta" not in ja and str(b.id) not in ja and ib.number not in ja
    assert "alpha" not in jb and str(a.id) not in jb and ia.number not in jb
    fs = FakeSender()
    svc.dispatch_due(db, fs)
    sent = [json.loads(c[1]) for c in fs.calls]
    assert {s["tenant_id"] for s in sent} == {str(a.id), str(b.id)}
    for s in sent:  # every delivered payload mentions exactly one tenant
        other = b if s["tenant_id"] == str(a.id) else a
        assert str(other.id) not in json.dumps(s) and other.contact_email not in json.dumps(s)
    # far-future invoices and paid invoices are not reminded
    open_invoice(db, a, 30)
    inv = open_invoice(db, b, 1)
    bsvc.settle_invoice(db, inv)
    db.commit()
    assert svc.emit_invoice_due_reminders(db) == 0


def test_renewal_reminders_and_approval_reminders(db, client, admin, make_user, make_client, n8n):
    make_plan(client, "pro")
    t = provision_tenant(db, "Ren Co", None, "ren@r.example.com", status="pending")
    other = provision_tenant(db, "Quiet Co", None, "q@q.example.com", status="active")
    db.commit()
    make_user("ren@r.example.com", "customer", tenant=t)
    c = make_client()
    login(c, "ren@r.example.com")
    inv = c.post("/api/v1/billing/checkout", json={"plan_code": "pro", "interval": "monthly"}).json()["invoice"]
    client.post(f"/api/v1/billing/invoices/{inv['id']}/manual-payment", json={"reference": "UTR-REN1"})
    from app.models.billing import Subscription

    sub = db.scalar(select(Subscription).where(Subscription.tenant_id == t.id))
    soon = sub.current_period_end - timedelta(days=2)
    assert svc.emit_renewal_reminders(db, soon) == 1 and svc.emit_renewal_reminders(db, soon) == 0
    ev = db.scalar(select(OutboxEvent).where(OutboxEvent.event_type == "subscription.renewal_reminder"))
    assert ev.tenant_id == t.id and ev.payload["recipient_email"] == "ren@r.example.com" and ev.payload["days_until_renewal"] <= 3
    assert str(other.id) not in json.dumps(ev.payload)
    assert svc.emit_renewal_reminders(db, sub.current_period_end - timedelta(days=20)) == 0
    # admin approval reminders for stale reseller applications
    from app.models.reseller import Reseller

    db.add(Reseller(name="Stale", contact_email="s@s.example.com", status="pending_approval", applied_at=datetime.now(UTC) - timedelta(hours=30)))
    db.commit()
    assert svc.emit_admin_approval_reminders(db) == 1
    e2 = db.scalar(select(OutboxEvent).where(OutboxEvent.event_type == "admin.approval_reminder"))
    assert e2.payload["pending_count"] == 1 and e2.payload["oldest_pending_hours"] >= 29 and e2.tenant_id is None
    rep = svc.emit_operational_report(db, "daily")
    assert rep.payload["totals"]["tenants_total"] == 2 and rep.tenant_id is None


# ---------------------------------------------------------------------------------------- admin API
def test_admin_only_endpoints_and_status(client, admin, make_user, make_client, db, tenants, monkeypatch):
    s = client.get("/api/v1/automation/status").json()
    assert s["integration"]["inbound"] == "not_configured" and s["integration"]["outbound"] == "not_configured"
    assert client.post("/api/v1/automation/dispatch").json()["status"] == "not_configured"
    cust, t = make_user("cust@c.example.com", "customer")
    cc = make_client()
    login(cc, "cust@c.example.com")
    sup, _ = make_user("sup@kvl.example.com", "support")
    sc = make_client()
    login(sc, "sup@kvl.example.com")
    for method, path in [("get", "/status"), ("get", "/workflows"), ("get", "/outbox"), ("get", "/executions"), ("post", "/dispatch"), ("post", "/scheduler/run"), ("post", "/workflows/backup_alerts/enabled")]:
        for who in (cc, sc, make_client()):
            r = getattr(who, method)("/api/v1/automation" + path, **({"json": {"enabled": False}} if method == "post" else {}))
            assert r.status_code in (401, 403), (path, r.status_code)
    wf = client.get("/api/v1/automation/workflows").json()["items"]
    assert len(wf) == 12 and {w["key"] for w in wf} == svc.CATALOG_KEYS and all(w["events"] for w in wf)
    monkeypatch.setattr(get_settings(), "n8n_webhook_secret", SECRET)
    monkeypatch.setattr(get_settings(), "n8n_base_url", "http://127.0.0.1:5678")
    s = client.get("/api/v1/automation/status").json()
    assert s["integration"]["outbound"] == "configured" and SECRET not in json.dumps(s)


def test_failure_detail_and_safe_retry_via_api(client, admin, db, tenants, n8n, monkeypatch):
    a, _ = tenants
    ev = mk_event(db, a)
    ev.max_attempts = 1
    db.commit()
    svc.dispatch_due(db, FakeSender(raises=True))
    dead = client.get("/api/v1/automation/outbox", params={"status": "dead"}).json()["items"]
    assert [d["id"] for d in dead] == [str(ev.id)]
    detail = client.get(f"/api/v1/automation/outbox/{ev.id}").json()
    assert detail["last_error"].startswith("ConnectionError") and detail["payload"]["tenant_name"] == "Alpha Co"
    assert client.get("/api/v1/automation/executions", params={"status": "failed"}).json()["items"][0]["workflow_key"] == "tenant_onboarding"
    wf = {w["key"]: w for w in client.get("/api/v1/automation/workflows").json()["items"]}
    assert wf["tenant_onboarding"]["last_status"] == "failed" and wf["tenant_onboarding"]["failed_runs"] == 1
    r = client.post(f"/api/v1/automation/outbox/{ev.id}/retry").json()
    assert r["status"] == "pending" and r["attempts"] == 0 and r["correlation_id"] == ev.correlation_id
    assert client.post(f"/api/v1/automation/outbox/{ev.id}/retry").status_code == 409  # only dead letters
    monkeypatch.setattr(svc, "get_sender", lambda: FakeSender())
    assert client.post("/api/v1/automation/dispatch").json()["delivered"] == 2  # the retried event + its escalation event
    assert client.post("/api/v1/automation/scheduler/run").status_code == 200


# --------------------------------------------------------------------------------------- n8n exports
def test_workflow_exports_are_importable_and_secret_free():
    root = pathlib.Path(__file__).resolve().parents[2] / "automation" / "n8n"
    files = sorted(root.glob("*.json"))
    assert {f.stem for f in files} == svc.CATALOG_KEYS
    for f in files:
        text = f.read_text()
        wf = json.loads(text)
        assert wf["name"].startswith("KVL") and wf["nodes"] and wf["connections"]
        low = text.lower()
        for bad in ("password\":", "apikey\":", "secret\": \"", "bearer ", "rzp_", "whsec"):
            assert bad not in low, (f.name, bad)
        webhooks = [n for n in wf["nodes"] if n["type"] == "n8n-nodes-base.webhook"]
        assert webhooks and webhooks[0]["parameters"]["path"] == "kvl-" + f.stem.replace("_", "-")
        assert all(n.get("credentials") is None or all(isinstance(v, dict) and "name" in v for v in n["credentials"].values()) for n in wf["nodes"])
        assert "$env" not in text or "KVL_N8N_WEBHOOK_SECRET" in text
