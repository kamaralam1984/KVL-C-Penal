import re
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select

from app.core.config import get_settings
from app.core.rbac import ROLE_DEFS
from app.models.support import Ticket
from app.services import support as svc
from tests.conftest import login

API = "/api/v1"
PNG = b"\x89PNG\r\n\x1a\n" + b"0" * 64
NOTE = "ZZ-INTERNAL-NOTE-MARKER"


@pytest.fixture(autouse=True)
def sandbox(tmp_path, monkeypatch):
    monkeypatch.setattr(get_settings(), "support_upload_dir", str(tmp_path / "uploads"))


@pytest.fixture
def world(make_user, make_client):
    """Tenant A + B customers, support agent, second agent, hosting admin, super admin; all logged in."""
    users = {}
    for key, email, role in [("a", "a@x.example.com", "customer"), ("b", "b@y.example.com", "customer"), ("s", "sup@kvl.example.com", "support"),
                             ("s2", "sup2@kvl.example.com", "support"), ("h", "host@kvl.example.com", "hosting_admin"), ("o", "owner@kvl.example.com", "super_admin")]:
        u, t = make_user(email, role)
        c = make_client()
        assert login(c, email).status_code == 200
        users[key] = {"u": u, "t": t, "c": c}
    return users


def new_ticket(c, subject="Site is down", body="Please help", **kw):
    r = c.post(f"{API}/support/tickets", json={"subject": subject, "body": body, **kw})
    assert r.status_code == 201, r.text
    return r.json()


def test_ticket_isolation_between_tenants(world):
    t = new_ticket(world["a"]["c"])
    tid = t["id"]
    assert world["b"]["c"].get(f"{API}/support/tickets/{tid}").status_code == 404
    assert world["b"]["c"].get(f"{API}/support/tickets").json()["total"] == 0
    assert world["b"]["c"].post(f"{API}/support/tickets/{tid}/messages", json={"body": "hi"}).status_code == 404
    assert world["b"]["c"].patch(f"{API}/support/tickets/{tid}", json={"status": "closed"}).status_code == 404
    assert world["b"]["c"].get(f"{API}/support/tickets/{tid}/history").status_code == 404
    assert world["a"]["c"].get(f"{API}/support/tickets").json()["total"] == 1
    assert world["s"]["c"].get(f"{API}/support/tickets").json()["total"] == 1
    # a tenant user cannot open a ticket in someone else's tenant
    r = world["b"]["c"].post(f"{API}/support/tickets", json={"subject": "x y z", "body": "b", "tenant_id": str(world["a"]["t"].id)})
    assert r.status_code == 404


def test_internal_notes_never_reach_customers(world):
    a, s = world["a"]["c"], world["s"]["c"]
    t = new_ticket(a, subject="Billing question", body="Invoice wrong")
    tid = t["id"]
    assert s.post(f"{API}/support/tickets/{tid}/messages", json={"body": NOTE, "internal": True}).status_code == 201
    up = s.post(f"{API}/support/tickets/{tid}/attachments", files={"file": ("internal.txt", NOTE.encode(), "text/plain")}, data={"internal": "true"})
    assert up.status_code == 201
    internal_att = up.json()["id"]
    assert s.post(f"{API}/support/tickets/{tid}/messages", json={"body": "We are on it"}).status_code == 201
    assert s.post(f"{API}/support/tickets/{tid}/assign", json={"assignee_id": str(world["s2"]["u"].id)}).status_code == 200

    # staff do see the note
    assert NOTE in s.get(f"{API}/support/tickets/{tid}").text
    # every customer-facing read
    for path in (f"/support/tickets/{tid}", f"/support/tickets/{tid}/history", "/support/tickets", f"/support/tickets?q={NOTE}", "/support/tickets/export.csv", f"/support/tickets/export.csv?q={NOTE}"):
        r = a.get(API + path)
        assert r.status_code == 200, path
        assert NOTE not in r.text, path
        assert "internal_note" not in r.text and "internal.txt" not in r.text, path
    assert a.get(f"{API}/support/tickets?q={NOTE}").json()["total"] == 0  # search cannot probe notes
    assert s.get(f"{API}/support/tickets?q={NOTE}").json()["total"] == 1
    d = a.get(f"{API}/support/tickets/{tid}").json()
    assert [m["body"] for m in d["messages"]] == ["Invoice wrong", "We are on it"]
    assert "sla" not in d and "assignee_id" not in d and all(h["kind"] in {"created", "reply", "status_changed"} for h in d["history"])
    assert a.get(f"{API}/support/attachments/{internal_att}/download").status_code == 404
    assert s.get(f"{API}/support/attachments/{internal_att}/download").status_code == 200
    # a customer cannot write internal notes or attachments
    assert a.post(f"{API}/support/tickets/{tid}/messages", json={"body": "x", "internal": True}).status_code == 403
    assert a.post(f"{API}/support/tickets/{tid}/attachments", files={"file": ("a.txt", b"hi", "text/plain")}, data={"internal": "true"}).status_code == 403
    # the reply response itself and non-manage platform roles (hosting admin) also get the public view
    assert NOTE not in world["h"]["c"].get(f"{API}/support/tickets/{tid}").text
    # reseller/other-tenant never; emails never include notes
    from app.core.db import SessionLocal
    from app.models.identity import EmailOutbox

    with SessionLocal() as db:
        assert all(NOTE not in e.body for e in db.scalars(select(EmailOutbox)))


def test_staff_only_endpoints_denied_to_customers(world):
    a = world["a"]["c"]
    tid = new_ticket(a)["id"]
    for method, path, body in [("post", f"/support/tickets/{tid}/assign", {"assignee_id": None}), ("get", "/support/reports/aging", None), ("get", "/support/reports/sla", None),
                               ("get", "/support/reports/workload", None), ("get", "/support/reports/first-response", None), ("post", "/support/sla/run", {}),
                               ("get", "/support/agents", None), ("post", "/support/departments", {"name": "Billing team"}),
                               ("get", f"/support/customers/{world['a']['t'].id}/timeline", None), ("get", "/staff/members", None)]:
        r = getattr(a, method)(API + path, **({"json": body} if body is not None else {}))
        assert r.status_code == 403, (path, r.status_code)
    assert a.patch(f"{API}/support/tickets/{tid}", json={"priority": "urgent"}).status_code == 403
    assert a.post(f"{API}/support/tickets", json={"subject": "abc", "body": "b", "priority": "urgent"}).status_code == 422


def test_support_role_has_no_infrastructure_privileges(world):
    infra = {"servers", "backups", "databases", "apps", "ssl", "dns", "files", "plans", "roles", "settings", "security", "automation", "apikeys", "webhooks", "resellers", "staff"}
    perms = ROLE_DEFS["support"]["perms"]
    assert not [p for p in perms if p.partition(":")[0] in infra], "support must not hold infra permissions"
    assert not [p for p in perms if p.endswith((":write", ":delete", ":manage", ":restore")) and p.partition(":")[0] not in {"tickets", "kb"}]
    s = world["s"]["c"]
    for path in ("/rbac/roles", "/staff/members", "/security/events", "/apikeys", "/webhooks", "/security/audit"):
        assert s.get(API + path).status_code == 403, path
    assert s.put(f"{API}/rbac/roles/support/permissions", json={"permissions": ["servers:manage"]}).status_code == 403
    # but can do their job
    assert s.get(f"{API}/support/reports/aging").status_code == 200
    assert world["o"]["c"].get(f"{API}/staff/members").status_code == 200


def test_assignment_history_and_workload(world):
    a, s, o = world["a"]["c"], world["s"]["c"], world["o"]["c"]
    tid = new_ticket(a)["id"]
    r = s.post(f"{API}/support/tickets/{tid}/assign", json={"assignee_id": str(world["s"]["u"].id)})
    assert r.status_code == 200 and r.json()["assignee_name"]
    assert s.post(f"{API}/support/tickets/{tid}/assign", json={"assignee_id": str(world["s2"]["u"].id)}).status_code == 200
    assert s.post(f"{API}/support/tickets/{tid}/assign", json={"assignee_id": str(world['a']['u'].id)}).status_code == 422  # customers can't be assignees
    kinds = [h["kind"] for h in s.get(f"{API}/support/tickets/{tid}/history").json()]
    assert kinds.count("assigned") == 1 and kinds.count("reassigned") == 1
    wl = s.get(f"{API}/support/reports/workload").json()["staff"]
    assert next(w for w in wl if w["user_id"] == str(world["s2"]["u"].id))["open"] == 1
    assert s.get(f"{API}/support/tickets?assignee=me").json()["total"] == 0
    assert s.get(f"{API}/support/tickets?assignee={world['s2']['u'].id}").json()["total"] == 1
    assert s.get(f"{API}/support/tickets?assignee=unassigned").json()["total"] == 0
    detail = o.get(f"{API}/staff/members/{world['s2']['u'].id}").json()
    assert detail["workload"]["open"] == 1 and detail["roles"][0]["infrastructure_permissions"] == []
    assert any(x["action"].startswith("support.ticket_") for x in o.get(f"{API}/staff/members/{world['s']['u'].id}/activity").json())


# ---------------------------------------------------------------- attachments
def _upload(c, tid, name="shot.png", data=PNG, ctype="image/png", **form):
    return c.post(f"{API}/support/tickets/{tid}/attachments", files={"file": (name, data, ctype)}, data=form)


def test_attachment_happy_path_and_download_headers(world, tmp_path):
    a = world["a"]["c"]
    tid = new_ticket(a)["id"]
    r = _upload(a, tid)
    assert r.status_code == 201
    aid = r.json()["id"]
    d = a.get(f"{API}/support/attachments/{aid}/download")
    assert d.status_code == 200 and d.content == PNG
    assert d.headers["content-disposition"].startswith("attachment")
    assert d.headers["x-content-type-options"] == "nosniff" and "sandbox" in d.headers["content-security-policy"]
    assert d.headers["content-type"].startswith("image/png")
    assert world["b"]["c"].get(f"{API}/support/attachments/{aid}/download").status_code == 404  # other tenant
    assert world["s"]["c"].get(f"{API}/support/attachments/{aid}/download").status_code == 200
    from app.core.db import SessionLocal
    from app.models.support import TicketAttachment

    with SessionLocal() as db:
        att = db.scalar(select(TicketAttachment))
        assert re.fullmatch(r"[0-9a-f]{48}\.png", att.stored_name) and att.original_name == "shot.png"
    assert list((tmp_path / "uploads").rglob("*.png"))


@pytest.mark.parametrize("name,data,ctype,code,err", [
    ("evil.png", b"MZ\x90\x00 not an image", "image/png", 422, "content_mismatch"),  # extension spoofing
    ("shot.png", PNG, "application/x-msdownload", 422, "content_type_mismatch"),
    ("shot.png", PNG, "text/html", 422, "content_type_mismatch"),
    ("shell.php", b"<?php system($_GET[0]);", "text/plain", 422, "unsupported_file_type"),
    ("shell.php.png", PNG, "image/png", 422, "unsupported_file_type"),
    ("page.html", b"<html><script>1</script>", "text/html", 422, "unsupported_file_type"),
    ("logo.svg", b"<svg onload=alert(1)>", "image/svg+xml", 422, "unsupported_file_type"),
    ("note.txt", b"<!DOCTYPE html><script>alert(1)</script>", "text/plain", 422, "content_mismatch"),
    ("note.txt", b"bin\x00ary", "text/plain", 422, "content_mismatch"),
    ("noext", b"abc", "text/plain", 422, "unsupported_file_type"),
    ("empty.txt", b"", "text/plain", 422, "empty_file"),
    ("fake.pdf", b"GIF89a....", "application/pdf", 422, "content_mismatch"),
])
def test_attachment_rejections(world, name, data, ctype, code, err):
    tid = new_ticket(world["a"]["c"])["id"]
    r = _upload(world["a"]["c"], tid, name, data, ctype)
    assert r.status_code == code and r.json()["error"]["code"] == err


def test_attachment_oversize(world, monkeypatch):
    monkeypatch.setattr(get_settings(), "support_max_attachment_bytes", 200)
    tid = new_ticket(world["a"]["c"])["id"]
    r = _upload(world["a"]["c"], tid, "big.txt", b"a" * 5000, "text/plain")
    assert r.status_code == 413 and r.json()["error"]["code"] == "file_too_large"
    assert _upload(world["a"]["c"], tid, "ok.txt", b"a" * 100, "text/plain").status_code == 201


def test_attachment_path_traversal_filename(world, tmp_path):
    tid = new_ticket(world["a"]["c"])["id"]
    for evil in ("../../../../tmp/pwn.png", "..\\..\\windows\\pwn.png", "/etc/passwd.png", "a/b/c.png"):
        r = _upload(world["a"]["c"], tid, evil)
        assert r.status_code == 201, evil
        assert "/" not in r.json()["name"] and "\\" not in r.json()["name"] and ".." not in r.json()["name"]
    root = (tmp_path / "uploads").resolve()
    files = [p for p in root.rglob("*") if p.is_file()]
    assert len(files) == 4 and all(p.resolve().is_relative_to(root) for p in files)
    assert not (tmp_path / "pwn.png").exists() and not list(tmp_path.glob("../pwn.png"))
    assert _upload(world["a"]["c"], tid, ".htaccess").status_code == 422
    assert svc.clean_filename("../../x.png") == "x.png"


def test_attachment_other_tenant_cannot_upload_or_message_link(world):
    tid = new_ticket(world["a"]["c"])["id"]
    assert _upload(world["b"]["c"], tid).status_code == 404


# ---------------------------------------------------------------- SLA
def _set_times(tid, **kw):
    import uuid

    from app.core.db import SessionLocal

    with SessionLocal() as db:
        t = db.get(Ticket, uuid.UUID(tid))
        for k, v in kw.items():
            setattr(t, k, v)
        db.commit()


def test_sla_due_times_follow_priority(world):
    s = world["s"]["c"]
    t = new_ticket(s, tenant_id=str(world["a"]["t"].id), priority="urgent")
    created = datetime.fromisoformat(t["created_at"])
    sla = t["sla"]
    assert datetime.fromisoformat(sla["first_response_due_at"]) - created == timedelta(minutes=60)
    assert datetime.fromisoformat(sla["resolution_due_at"]) - created == timedelta(hours=4)
    low = new_ticket(s, tenant_id=str(world["a"]["t"].id), priority="low")
    assert datetime.fromisoformat(low["sla"]["resolution_due_at"]) - datetime.fromisoformat(low["created_at"]) == timedelta(days=7)
    # priority change recomputes from creation
    r = s.patch(f"{API}/support/tickets/{low['id']}", json={"priority": "high"})
    assert datetime.fromisoformat(r.json()["sla"]["first_response_due_at"]) - datetime.fromisoformat(low["created_at"]) == timedelta(hours=4)


def test_sla_breach_flags_and_escalation(world, db):
    a, s = world["a"]["c"], world["s"]["c"]
    t1 = new_ticket(a, subject="Late one")
    t2 = new_ticket(a, subject="Answered in time")
    assert s.post(f"{API}/support/tickets/{t2['id']}/messages", json={"body": "hello"}).status_code == 201
    past = datetime.now(UTC) - timedelta(hours=10)
    for t in (t1, t2):
        _set_times(t["id"], first_response_due_at=past, resolution_due_at=datetime.now(UTC) + timedelta(days=1))
    # t2 responded just now, but its due date is in the past -> recompute: responded after due counts as breach by timing, so move t2's response earlier
    _set_times(t2["id"], first_response_at=past - timedelta(hours=1))
    d = s.get(f"{API}/support/tickets/{t1['id']}").json()
    assert d["sla"]["first_response"] == "breached"  # visible before the periodic job runs
    assert s.get(f"{API}/support/tickets/{t2['id']}").json()["sla"]["first_response"] == "met"

    counts = s.post(f"{API}/support/sla/run").json()
    assert counts["first_response_breached"] == 1 and counts["escalated"] == 1
    d = s.get(f"{API}/support/tickets/{t1['id']}").json()
    assert d["sla"]["escalation_level"] == 1 and d["priority"] == "high" and "sla_breached" in [h["kind"] for h in d["history"]]
    assert s.post(f"{API}/support/sla/run").json() == {"first_response_breached": 0, "resolution_breached": 0, "escalated": 0}  # idempotent
    assert s.get(f"{API}/support/tickets?sla=breached").json()["total"] == 1
    # customers never see SLA internals or the breach event
    cd = a.get(f"{API}/support/tickets/{t1['id']}").json()
    assert "sla" not in cd and "sla_breached" not in str(cd["history"]) and cd["priority"] == "high"
    rep = s.get(f"{API}/support/reports/sla").json()
    assert rep["first_response"]["breached"] == 1 and rep["first_response"]["met"] == 1 and rep["first_response"]["compliance_pct"] == 50.0 and rep["clock"] == "24x7"


def test_resolution_breach_and_service_function_direct(world, db):
    tid = new_ticket(world["a"]["c"])["id"]
    _set_times(tid, resolution_due_at=datetime.now(UTC) - timedelta(minutes=1), first_response_due_at=datetime.now(UTC) - timedelta(minutes=2))
    counts = svc.run_sla_escalation(db)
    db.commit()
    assert counts["resolution_breached"] == 1 and counts["first_response_breached"] == 1
    import uuid

    t = db.get(Ticket, uuid.UUID(tid))
    assert t.escalation_level == 1 and t.resolution_breached
    # resolved tickets are never escalated
    tid2 = new_ticket(world["a"]["c"], subject="Another")["id"]
    world["a"]["c"].patch(f"{API}/support/tickets/{tid2}", json={"status": "closed"})
    _set_times(tid2, resolution_due_at=datetime.now(UTC) - timedelta(hours=1))
    assert svc.run_sla_escalation(db)["resolution_breached"] == 0


def test_hold_pauses_sla_clock(world, db):
    s = world["s"]["c"]
    t = new_ticket(s, tenant_id=str(world["a"]["t"].id), priority="high")
    before = datetime.fromisoformat(t["sla"]["resolution_due_at"])
    s.patch(f"{API}/support/tickets/{t['id']}", json={"status": "on_hold"})
    _set_times(t["id"], hold_started_at=datetime.now(UTC) - timedelta(hours=3))
    r = s.patch(f"{API}/support/tickets/{t['id']}", json={"status": "open"})
    assert datetime.fromisoformat(r.json()["sla"]["resolution_due_at"]) - before >= timedelta(hours=3)


def test_business_hours_clock():
    fri = datetime(2026, 11, 6, 17, 30, tzinfo=UTC)  # Friday
    assert svc.add_sla_minutes(fri, 60, business=True) == datetime(2026, 11, 9, 9, 30, tzinfo=UTC)
    sat = datetime(2026, 11, 7, 12, 0, tzinfo=UTC)
    assert svc.add_sla_minutes(sat, 30, business=True) == datetime(2026, 11, 9, 9, 30, tzinfo=UTC)
    assert svc.add_sla_minutes(fri, 60, business=False) == fri + timedelta(hours=1)
    assert svc.add_sla_minutes(datetime(2026, 11, 2, 8, 0, tzinfo=UTC), 9 * 60, business=True) == datetime(2026, 11, 2, 18, 0, tzinfo=UTC)


# ---------------------------------------------------------------- lifecycle, search, reports, export
def test_reply_status_flow_and_first_response(world):
    a, s = world["a"]["c"], world["s"]["c"]
    tid = new_ticket(a)["id"]
    assert s.post(f"{API}/support/tickets/{tid}/messages", json={"body": "Looking"}).status_code == 201
    d = s.get(f"{API}/support/tickets/{tid}").json()
    assert d["status"] == "pending" and d["first_response_at"]
    assert a.post(f"{API}/support/tickets/{tid}/messages", json={"body": "More info"}).status_code == 201
    assert a.get(f"{API}/support/tickets/{tid}").json()["status"] == "open"
    assert a.patch(f"{API}/support/tickets/{tid}", json={"status": "closed"}).status_code == 200
    r = a.post(f"{API}/support/tickets/{tid}/messages", json={"body": "again"})
    assert r.status_code == 409
    assert a.patch(f"{API}/support/tickets/{tid}", json={"status": "open"}).json()["status"] == "open"
    assert a.patch(f"{API}/support/tickets/{tid}", json={"status": "resolved"}).status_code == 403
    fr = s.get(f"{API}/support/reports/first-response").json()
    assert fr["overall"]["count"] == 1 and fr["overall"]["avg_minutes"] is not None
    from app.core.db import SessionLocal
    from app.models.identity import AuditEvent

    with SessionLocal() as db:
        actions = {x.action for x in db.scalars(select(AuditEvent))}
    assert {"support.ticket_created", "support.ticket_replied", "support.ticket_updated"} <= actions


def test_search_filters_pagination_and_aging(world):
    a, s = world["a"]["c"], world["s"]["c"]
    for i in range(5):
        new_ticket(a, subject=f"Ticket number {i}", body=f"needle{i} body", category="billing" if i % 2 else "technical", priority="high" if i == 0 else "normal")
    assert a.get(f"{API}/support/tickets?limit=2&offset=0").json()["total"] == 5
    assert len(a.get(f"{API}/support/tickets?limit=2&offset=4").json()["items"]) == 1
    assert a.get(f"{API}/support/tickets?q=needle3").json()["total"] == 1
    assert a.get(f"{API}/support/tickets?category=billing").json()["total"] == 2
    assert a.get(f"{API}/support/tickets?priority=high").json()["total"] == 1
    assert a.get(f"{API}/support/tickets?status=closed").json()["total"] == 0
    assert a.get(f"{API}/support/tickets?limit=500").status_code == 422
    assert s.get(f"{API}/support/tickets?sort=priority").json()["items"][0]["priority"] == "high"
    assert s.get(f"{API}/support/tickets?q=1000").status_code == 200
    ag = s.get(f"{API}/support/reports/aging").json()
    assert ag["open_total"] == 5 and ag["buckets"]["lt_1d"] == 5 and ag["by_priority"]["high"] == 1


def test_csv_export_authorised_and_formula_safe(world):
    new_ticket(world["a"]["c"], subject="=HYPERLINK(\"http://evil\")")
    new_ticket(world["b"]["c"], subject="Other tenant ticket")
    ca = world["a"]["c"].get(f"{API}/support/tickets/export.csv")
    assert ca.headers["content-type"].startswith("text/csv") and "attachment" in ca.headers["content-disposition"]
    assert "Other tenant" not in ca.text and "'=HYPERLINK" in ca.text and "tenant_id" not in ca.text.splitlines()[0]
    cs = world["s"]["c"].get(f"{API}/support/tickets/export.csv")
    assert "Other tenant" in cs.text and "tenant_id" in cs.text.splitlines()[0]


def test_departments_and_timeline(world):
    s, a = world["s"]["c"], world["a"]["c"]
    r = s.post(f"{API}/support/departments", json={"name": "Billing", "description": "Money"})
    did = r.json()["id"]
    assert s.post(f"{API}/support/departments", json={"name": "billing"}).status_code == 409
    assert s.put(f"{API}/support/departments/{did}/members/{world['s2']['u'].id}").status_code == 200
    assert s.put(f"{API}/support/departments/{did}/members/{world['a']['u'].id}").status_code == 422
    assert [m["email"] for m in s.get(f"{API}/support/departments/{did}/members").json()] == ["sup2@kvl.example.com"]
    t = new_ticket(s, tenant_id=str(world["a"]["t"].id), department_id=did)
    assert t["department"] == "Billing"
    assert new_ticket(a, subject="Customer tries")["department"] is None
    assert a.post(f"{API}/support/tickets", json={"subject": "abc", "body": "b", "department_id": did}).status_code == 403
    assert [d["name"] for d in a.get(f"{API}/support/departments").json()] == ["Billing"]
    assert "members" not in a.get(f"{API}/support/departments").json()[0]
    tl = s.get(f"{API}/support/customers/{world['a']['t'].id}/timeline").json()
    assert tl["open_tickets"] == 2 and {i["source"] for i in tl["items"]} >= {"ticket", "audit"}
    assert world["o"]["c"].get(f"{API}/staff/members").json()
    assert s.delete(f"{API}/support/departments/{did}/members/{world['s2']['u'].id}").status_code == 200


def test_suspended_tenant_can_still_open_tickets(world, db):
    from app.models.tenant import Tenant

    t = db.get(Tenant, world["a"]["t"].id)
    t.status = "suspended"
    db.commit()
    assert world["a"]["c"].post(f"{API}/support/tickets", json={"subject": "Why suspended", "body": "help"}).status_code == 201


def test_ticket_validation(world):
    a = world["a"]["c"]
    assert a.post(f"{API}/support/tickets", json={"subject": "ab", "body": "x"}).status_code == 422
    assert a.post(f"{API}/support/tickets", json={"subject": "abc", "body": ""}).status_code == 422
    assert a.post(f"{API}/support/tickets", json={"subject": "abc", "body": "x", "category": "nope"}).status_code == 422
    assert a.post(f"{API}/support/tickets", json={"subject": "abc", "body": "x" * 10001}).status_code == 422
    assert a.get(f"{API}/support/meta").json()["is_staff"] is False
    assert "sla_policy_minutes" not in a.get(f"{API}/support/meta").json()
