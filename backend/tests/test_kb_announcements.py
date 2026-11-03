from datetime import UTC, datetime, timedelta

import pytest

from tests.conftest import login

API = "/api/v1"


@pytest.fixture
def world(make_user, make_client):
    w = {}
    for key, email, role in [("a", "a@x.example.com", "customer"), ("b", "b@y.example.com", "customer"), ("s", "sup@kvl.example.com", "support"),
                             ("o", "owner@kvl.example.com", "super_admin"), ("r", "res@z.example.com", "reseller")]:
        u, t = make_user(email, role)
        c = make_client()
        assert login(c, email).status_code == 200
        w[key] = {"u": u, "t": t, "c": c}
    return w


def mk_article(c, title="How to reset your password", body="Click forgot password.", status="published", **kw):
    r = c.post(f"{API}/kb/articles", json={"title": title, "body": body, "status": status, **kw})
    assert r.status_code == 201, r.text
    return r.json()


def test_kb_draft_vs_published_visibility(world):
    s, a = world["s"]["c"], world["a"]["c"]
    cat = s.post(f"{API}/kb/categories", json={"name": "Accounts"}).json()
    pub = mk_article(s, category_id=cat["id"])
    draft = mk_article(s, title="Upcoming feature notes", status="draft")
    # published readable by every authenticated role, drafts only by kb:write
    for k in ("a", "b", "r", "s", "o"):
        c = world[k]["c"]
        assert c.get(f"{API}/kb/articles/{pub['slug']}").status_code == 200
        titles = [i["title"] for i in c.get(f"{API}/kb/articles").json()["items"]]
        if k in ("s", "o"):
            assert "Upcoming feature notes" in titles
        else:
            assert titles == ["How to reset your password"]
            assert c.get(f"{API}/kb/articles/{draft['slug']}").status_code == 404
            assert c.get(f"{API}/kb/articles/{draft['id']}").status_code == 404
            assert c.get(f"{API}/kb/articles?status=draft").json()["total"] == 1 and c.get(f"{API}/kb/articles?status=draft").json()["items"][0]["status"] == "published"
    assert world["a"]["c"].post(f"{API}/kb/articles/{draft['id']}/feedback", json={"helpful": True}).status_code == 404
    # customers cannot write
    assert a.post(f"{API}/kb/articles", json={"title": "Spam article", "body": "x"}).status_code == 403
    assert a.patch(f"{API}/kb/articles/{pub['id']}", json={"title": "Hacked title"}).status_code == 403
    assert a.delete(f"{API}/kb/articles/{pub['id']}").status_code == 403
    # publish a draft -> now visible
    assert s.patch(f"{API}/kb/articles/{draft['id']}", json={"status": "published"}).status_code == 200
    assert a.get(f"{API}/kb/articles/{draft['slug']}").status_code == 200
    assert a.get(f"{API}/kb/articles/{draft['slug']}").json()["body"]


def test_kb_search_related_feedback(world):
    s, a = world["s"]["c"], world["a"]["c"]
    cat = s.post(f"{API}/kb/categories", json={"name": "Email"}).json()
    one = mk_article(s, title="Configure email forwarding", body="Use the mail panel", category_id=cat["id"])
    mk_article(s, title="Email forwarding troubleshooting", body="Check MX records", category_id=cat["id"])
    mk_article(s, title="Billing invoices explained", body="Invoices are monthly")
    assert a.get(f"{API}/kb/articles?q=MX").json()["total"] == 1
    assert a.get(f"{API}/kb/articles?q=nothingmatches").json()["total"] == 0
    assert a.get(f"{API}/kb/articles?category_id={cat['id']}").json()["total"] == 2
    d = a.get(f"{API}/kb/articles/{one['slug']}").json()
    assert [r["title"] for r in d["related"]] == ["Email forwarding troubleshooting"]
    r = a.post(f"{API}/kb/articles/{one['id']}/feedback", json={"helpful": True})
    assert r.json() == {"helpful_yes": 1, "helpful_no": 0}
    assert a.post(f"{API}/kb/articles/{one['id']}/feedback", json={"helpful": True}).json()["helpful_yes"] == 1  # one vote per user
    assert a.post(f"{API}/kb/articles/{one['id']}/feedback", json={"helpful": False}).json() == {"helpful_yes": 0, "helpful_no": 1}
    assert world["b"]["c"].post(f"{API}/kb/articles/{one['id']}/feedback", json={"helpful": False}).json()["helpful_no"] == 2
    assert a.get(f"{API}/kb/articles/{one['slug']}").json()["my_feedback"] == "not_helpful"
    assert [c["articles"] for c in a.get(f"{API}/kb/categories").json()] == [2]
    assert s.post(f"{API}/kb/articles", json={"title": "x", "body": "y"}).status_code == 422
    assert s.post(f"{API}/kb/articles", json={"title": "Valid title", "body": "y", "category_id": "00000000-0000-0000-0000-000000000000"}).status_code == 404
    # manage-level delete is not granted to support
    assert s.delete(f"{API}/kb/articles/{one['id']}").status_code == 403
    assert world["o"]["c"].delete(f"{API}/kb/articles/{one['id']}").status_code == 200


def mk_ann(c, **kw):
    body = {"title": "Planned maintenance", "body": "Tonight 02:00 UTC", "status": "published", **kw}
    r = c.post(f"{API}/announcements", json=body)
    assert r.status_code == 201, r.text
    return r.json()["id"]


def feed(c):
    r = c.get(f"{API}/announcements/feed")
    assert r.status_code == 200
    return r.json()


def test_announcement_audience_targeting(world):
    o = world["o"]["c"]
    mk_ann(o, title="Everyone")
    mk_ann(o, title="Customers only", roles=["customer"])
    mk_ann(o, title="Tenant A only", tenant_ids=[str(world["a"]["t"].id)])
    mk_ann(o, title="Support only", roles=["support"])
    mk_ann(o, title="Draft", status="draft")
    mk_ann(o, title="Scheduled", publish_at=(datetime.now(UTC) + timedelta(hours=2)).isoformat())
    mk_ann(o, title="Expired", publish_at=(datetime.now(UTC) - timedelta(days=2)).isoformat(), expires_at=(datetime.now(UTC) - timedelta(days=1)).isoformat())
    titles = lambda k: sorted(i["title"] for i in feed(world[k]["c"])["items"])  # noqa: E731
    assert titles("a") == ["Customers only", "Everyone", "Tenant A only"]
    assert titles("b") == ["Customers only", "Everyone"]
    assert titles("s") == ["Everyone", "Support only"]
    assert titles("r") == ["Everyone"]
    assert feed(world["a"]["c"])["unread"] == 3
    # scheduled one appears once its time passes
    from sqlalchemy import select
    from app.core.db import SessionLocal
    from app.models.support import Announcement

    with SessionLocal() as db:
        x = db.scalar(select(Announcement).where(Announcement.title == "Scheduled"))
        x.publish_at = datetime.now(UTC) - timedelta(minutes=1)
        db.commit()
    assert "Scheduled" in titles("b")
    # manager list shows states; drafts exist only there
    states = {a["title"]: a["state"] for a in o.get(f"{API}/announcements").json()}
    assert states["Draft"] == "draft" and states["Expired"] == "published"


def test_announcement_read_ack_state_per_user(world):
    o, a, b = world["o"]["c"], world["a"]["c"], world["b"]["c"]
    aid = mk_ann(o, title="Policy update", require_ack=True)
    assert feed(a)["pending_ack"] == 1 and feed(a)["unread"] == 1
    assert a.post(f"{API}/announcements/{aid}/read").status_code == 200
    f = feed(a)
    assert f["unread"] == 0 and f["pending_ack"] == 1 and f["items"][0]["read"] and not f["items"][0]["acknowledged"]
    assert a.post(f"{API}/announcements/{aid}/ack").status_code == 200
    assert feed(a)["pending_ack"] == 0 and feed(a)["items"][0]["acknowledged"]
    assert feed(b)["unread"] == 1 and feed(b)["pending_ack"] == 1  # state is per user
    assert o.get(f"{API}/announcements/{aid}/stats").json() == {"reads": 1, "acknowledged": 1}
    # cannot ack what you cannot see
    tid = mk_ann(o, title="Only A", tenant_ids=[str(world["a"]["t"].id)])
    assert b.post(f"{API}/announcements/{tid}/ack").status_code == 404
    assert b.post(f"{API}/announcements/{tid}/read").status_code == 404


def test_announcement_management_permissions_and_validation(world):
    a, s, o = world["a"]["c"], world["s"]["c"], world["o"]["c"]
    body = {"title": "Hello there", "body": "x", "status": "published"}
    assert a.post(f"{API}/announcements", json=body).status_code == 403
    assert s.post(f"{API}/announcements", json=body).status_code == 403  # support reads announcements, only admins publish
    assert a.get(f"{API}/announcements").status_code == 403
    assert o.post(f"{API}/announcements", json={**body, "roles": ["nonexistent"]}).status_code == 422
    assert o.post(f"{API}/announcements", json={**body, "severity": "loud"}).status_code == 422
    now = datetime.now(UTC)
    assert o.post(f"{API}/announcements", json={**body, "publish_at": now.isoformat(), "expires_at": (now - timedelta(hours=1)).isoformat()}).status_code == 422
    aid = mk_ann(o)
    assert o.put(f"{API}/announcements/{aid}", json={**body, "title": "Changed", "status": "archived"}).status_code == 200
    assert feed(a)["items"] == []
    assert o.delete(f"{API}/announcements/{aid}").status_code == 200
    assert o.delete(f"{API}/announcements/{aid}").status_code == 404
