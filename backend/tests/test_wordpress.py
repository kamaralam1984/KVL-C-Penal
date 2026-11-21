"""WordPress toolkit API: wordpress apps only, official packages only, tenant isolation, update flags."""
import pytest

from app.models.hosting import Application
from app.models.identity import AuditEvent
from app.models.infra import AgentJob
from app.services import wporg
from tests.hosting_helpers import finish_job, make_server, tenant_client

INFO = {
    "plugins/info/1.2/?action=plugin_information&request[slug]=akismet": {"version": "5.4", "download_link": "https://downloads.wordpress.org/plugin/akismet.5.4.zip"},
    "plugins/info/1.2/?action=plugin_information&request[slug]=evilpkg": {"version": "9.9", "download_link": "https://evil.example.com/plugin/evilpkg.zip"},
    "core/version-check": {"offers": [{"current": "6.8.3"}]},
}


@pytest.fixture(autouse=True)
def fake_wporg(monkeypatch):
    calls = []

    def f(url):
        calls.append(url)
        assert url.startswith("https://api.wordpress.org/")
        return next((v for k, v in INFO.items() if k in url), None)

    monkeypatch.setattr(wporg, "fetch", f)
    wporg._cache.clear()
    return calls


def mkapp(db, tenant, server, runtime="wordpress", deployed=True):
    a = Application(tenant_id=tenant.id, server_id=server.id, name="Blog", slug=f"blog-{runtime}", runtime=runtime, runtime_version="8.3", current_release="20261121000000-abcdef" if deployed else None)
    db.add(a)
    db.commit()
    return a


def W(a):
    return f"/api/v1/apps/{a.id}/wordpress"


def test_status_flow_with_update_flags(make_user, make_client, db):
    s = make_server(db)
    c, _, t = tenant_client(make_user, make_client, "a@x.com")
    a = mkapp(db, t, s)
    jid = c.post(f"{W(a)}/status").json()["job_id"]
    assert db.get(AgentJob, jid).params == {"app": f"app-{a.id.hex[:16]}"}
    finish_job(db, jid, result={"ok": True, "core": {"version": "6.8.1"}, "maintenance": False,
                                "plugins": [{"slug": "akismet", "name": "Akismet", "version": "5.3", "managed": True}, {"slug": "hello", "name": "Hello", "version": "1.7", "managed": False}], "themes": []})
    out = c.get(f"{W(a)}/jobs/{jid}").json()
    assert out["core"] == {"version": "6.8.1", "latest": "6.8.3", "update": True}
    ak, hello = out["plugins"]
    assert ak["update"] and ak["latest"] == "5.4" and not hello["update"] and hello["latest"] is None


def test_update_uses_only_official_package(make_user, make_client, db):
    s = make_server(db)
    c, _, t = tenant_client(make_user, make_client, "a@x.com")
    a = mkapp(db, t, s)
    r = c.post(f"{W(a)}/update", json={"kind": "plugin", "slug": "akismet"})
    assert r.status_code == 202 and r.json()["version"] == "5.4"
    j = db.get(AgentJob, r.json()["job_id"])
    assert j.operation == "wp.update_item" and j.params["package_url"] == "https://downloads.wordpress.org/plugin/akismet.5.4.zip"
    assert db.query(AuditEvent).filter_by(action="wordpress.update").count() == 1
    # a wordpress.org answer pointing elsewhere is refused; unknown plugins too
    assert c.post(f"{W(a)}/update", json={"kind": "plugin", "slug": "evilpkg"}).status_code == 409
    assert c.post(f"{W(a)}/update", json={"kind": "plugin", "slug": "my-custom"}).status_code == 409
    for bad in ({"kind": "plugin", "slug": "../x"}, {"kind": "core", "slug": "x"}, {"kind": "plugin", "slug": "a", "url": "https://evil"}):
        assert c.post(f"{W(a)}/update", json=bad).status_code == 422


def test_only_wordpress_deployed_apps_and_isolation(make_user, make_client, db):
    s = make_server(db)
    c, _, t = tenant_client(make_user, make_client, "a@x.com")
    b, _, _ = tenant_client(make_user, make_client, "b@x.com")
    node = mkapp(db, t, s, runtime="nodejs")
    assert c.post(f"{W(node)}/status").status_code == 409
    nd = mkapp(db, t, s, runtime="wordpress", deployed=False)
    nd.slug = "blog-nd"
    db.commit()
    assert c.post(f"{W(nd)}/status").status_code == 409
    a = mkapp(db, t, s)
    jid = c.post(f"{W(a)}/maintenance", json={"enabled": True}).json()["job_id"]
    assert b.post(f"{W(a)}/status").status_code == 404
    assert b.get(f"{W(a)}/jobs/{jid}").status_code == 404
    assert b.post(f"{W(a)}/maintenance", json={"enabled": False}).status_code == 404
