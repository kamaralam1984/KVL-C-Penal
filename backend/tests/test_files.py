import base64
import os
import uuid

import pytest
from sqlalchemy import func, select

from app.models.identity import AuditEvent
from app.models.infra import AgentJob
from app.models.tenant import TenantQuota
from tests.conftest import login
from tests.p9_helpers import add_ref, add_server, run_agent_job

API = "/api/v1/files"
ROOT_A = "sites/acme/public"


def b64(b: bytes) -> str:
    return base64.b64encode(b).decode()


@pytest.fixture
def env(tmp_path, admin, client, make_user, make_client, db, monkeypatch):
    from app.core.config import get_settings  # noqa: F401

    ua, ta = make_user("a@example.com", "customer")
    ub, tb = make_user("b@example.com", "customer")
    srv = add_server(db)
    add_ref(db, ta, "website", "acme", server_id=srv.id, root_rel=ROOT_A)
    add_ref(db, tb, "website", "other", server_id=srv.id, root_rel="sites/other/public")
    add_ref(db, ta, "website", "pending", server_id=srv.id)  # no location yet
    for t in (ta, tb):
        q = db.scalar(select(TenantQuota).where(TenantQuota.tenant_id == t.id, TenantQuota.resource == "disk_mb"))
        if q is None:
            q = TenantQuota(tenant_id=t.id, resource="disk_mb")
            db.add(q)
        q.limit_value, q.used_value = 5, 0
    db.commit()
    (tmp_path / ROOT_A / "docs").mkdir(parents=True)
    (tmp_path / ROOT_A / "index.html").write_text("<h1>acme</h1>")
    (tmp_path / "sites/other/public").mkdir(parents=True)
    (tmp_path / "sites/other/public/secret.txt").write_text("tenant B secret")
    (tmp_path / "etc").mkdir()
    (tmp_path / "etc/passwd").write_text("root:x:0:0")
    a, b = make_client(), make_client()
    assert login(a, "a@example.com").status_code == 200 and login(b, "b@example.com").status_code == 200
    return {"a": a, "b": b, "admin": client, "ta": ta, "tb": tb, "dir": tmp_path, "db": db}


def do(env, c, site, action, body=None, tenant=None, expect=202):
    q = f"?tenant_id={tenant.id}" if tenant else ""
    r = c.post(f"{API}/{site}/{action}{q}", json=body or {})
    assert r.status_code == expect, (r.status_code, r.text)
    return r


def finish(env, c, r):
    run_agent_job(env["db"], env["db"].scalar(select(AgentJob.id).order_by(AgentJob.created_at.desc())), env["dir"])
    return c.get(f"{API}/jobs/{r.json()['id']}").json()


def njobs(env):
    return env["db"].scalar(select(func.count()).select_from(AgentJob))


def test_list_runs_in_registered_root_not_client_root(env):
    r = do(env, env["a"], "acme", "list", {"path": "", "root": "sites/other/public"})
    j = env["db"].scalars(select(AgentJob)).one()
    assert j.operation == "files.list" and j.params["root"] == ROOT_A
    done = finish(env, env["a"], r)
    assert done["status"] == "succeeded" and [e["name"] for e in done["result"]["entries"]] == ["docs", "index.html"]


@pytest.mark.parametrize("bad", ["../etc/passwd", "docs/../../..", "/etc/passwd", "%2e%2e/%2e%2e/etc", "%252e%252e%252fetc", "..%2fetc", "a\x00b", "docs\\..\\..\\x", "C:/Windows", ".kvl-trash/x"])
def test_traversal_rejected_before_queueing(env, bad):
    n = njobs(env)
    for action in ("list", "stat", "read"):
        r = env["a"].post(f"{API}/acme/{action}", json={"path": bad})
        assert r.status_code == 422 and r.json()["error"]["code"] == "invalid_path", (action, bad, r.text)
    assert njobs(env) == n
    assert env["a"].post(f"{API}/acme/rename", json={"src": "index.html", "dst": bad}).status_code == 422
    assert env["a"].post(f"{API}/acme/mkdir", json={"path": bad}).status_code == 422
    assert env["a"].post(f"{API}/acme/delete", json={"path": bad, "confirm": True}).status_code == 422
    assert env["a"].post(f"{API}/acme/archive", json={"paths": [bad], "dest": "x.zip"}).status_code == 422
    assert njobs(env) == n


def test_cross_tenant_is_404_everywhere(env):
    r = do(env, env["a"], "acme", "list", {"path": ""})
    assert env["b"].post(f"{API}/acme/list", json={}).status_code == 404
    assert env["b"].post(f"{API}/acme/read", json={"path": "index.html"}).status_code == 404
    assert env["b"].post(f"{API}/acme/delete", json={"path": "index.html", "confirm": True}).status_code == 404
    assert env["b"].post(f"{API}/acme/upload", json={"path": "x.txt", "content_b64": b64(b"x"), "upload_id": "upload-0001", "total_size": 1}).status_code == 404
    assert env["b"].get(f"{API}/jobs/{r.json()['id']}").status_code == 404
    assert env["b"].post(f"{API}/acme/list?tenant_id={env['ta'].id}", json={}).status_code == 404
    sites = env["b"].get(f"{API}/sites").json()["items"]
    assert [s["ref"] for s in sites] == ["other"]
    assert env["b"].get(f"{API}/history").json()["items"] == []
    assert all(e["op"] != "list" for e in env["b"].get(f"{API}/history").json()["items"])


def test_symlink_escape_blocked_end_to_end(env):
    os.symlink(env["dir"] / "etc/passwd", env["dir"] / ROOT_A / "pw")
    os.symlink(env["dir"] / "sites/other/public", env["dir"] / ROOT_A / "peek")
    for path in ("pw", "peek/secret.txt"):
        d = finish(env, env["a"], do(env, env["a"], "acme", "read", {"path": path}))
        assert d["status"] == "failed" and "symlink_blocked" in d["error"]
    d = finish(env, env["a"], do(env, env["a"], "acme", "list", {"path": "peek"}))
    assert d["status"] == "failed"


def test_site_not_configured(env):
    r = env["a"].post(f"{API}/pending/list", json={})
    assert r.status_code == 409 and r.json()["error"]["code"] == "not_configured"


def test_oversized_blocked_and_unsafe_uploads(env):
    n = njobs(env)
    r = env["a"].post(f"{API}/acme/upload", json={"path": "big.bin", "content_b64": b64(b"x"), "upload_id": "upload-0002", "total_size": 11 * 1024 * 1024})
    assert r.status_code == 413 and r.json()["error"]["code"] == "upload_too_large"
    r = env["a"].post(f"{API}/acme/upload", json={"path": "evil.EXE", "content_b64": b64(b"MZ"), "upload_id": "upload-0003", "total_size": 2})
    assert r.status_code == 415
    r = env["a"].post(f"{API}/acme/upload", json={"path": "x.txt", "content_b64": "A" * 2_000_000, "upload_id": "upload-0004", "total_size": 2})
    assert r.status_code == 422
    r = env["a"].post(f"{API}/acme/upload", json={"path": "x.txt", "content_b64": b64(b"x"), "upload_id": "upload-0005", "chunk_index": 1, "total_chunks": 2, "total_size": 2})
    assert r.status_code == 409 and r.json()["error"]["code"] == "upload_not_started"
    assert njobs(env) == n


def test_chunked_upload_end_to_end_quota_and_audit(env):
    data = os.urandom(1500)
    uid = "upload-" + uuid.uuid4().hex[:10]
    for i, part in enumerate((data[:500], data[500:1000], data[1000:])):
        body = {"path": "docs/blob.bin", "content_b64": b64(part), "upload_id": uid, "chunk_index": i, "total_chunks": 3, "total_size": 1500}
        d = finish(env, env["a"], do(env, env["a"], "acme", "upload", body))
        assert d["status"] == "succeeded", d
    assert (env["dir"] / ROOT_A / "docs/blob.bin").read_bytes() == data
    q = env["a"].get(f"{API}/quota").json()
    assert q["configured"] and q["used_mb"] == 1 and q["limit_mb"] == 5
    assert env["db"].scalar(select(AuditEvent).where(AuditEvent.action == "files.upload")) is not None


def test_quota_exceeded_blocks_upload_and_failed_upload_releases(env):
    r = env["a"].post(f"{API}/acme/upload", json={"path": "a.bin", "content_b64": b64(b"x"), "upload_id": "upload-0099", "total_size": 6 * 1024 * 1024 - 1})
    assert r.status_code == 409 and r.json()["error"]["code"] == "quota_exceeded"
    # an upload whose agent job fails gives its reservation back
    (env["dir"] / ROOT_A / "taken.txt").write_text("x")
    r = do(env, env["a"], "acme", "upload", {"path": "taken.txt", "content_b64": b64(b"abc"), "upload_id": "upload-0100", "total_size": 3})
    assert env["a"].get(f"{API}/quota").json()["used_mb"] == 1
    d = finish(env, env["a"], r)
    assert d["status"] == "failed" and "exists" in d["error"]
    assert env["a"].get(f"{API}/quota").json()["used_mb"] == 0


def test_unauthorized_deletion(env, make_client, make_user, db):
    n = njobs(env)
    assert env["a"].post(f"{API}/acme/delete", json={"path": "index.html"}).status_code == 400  # confirmation required
    assert env["a"].post(f"{API}/acme/delete", json={"path": "index.html", "confirm": False}).status_code == 400
    assert env["a"].post(f"{API}/acme/purge", json={"empty": True}).status_code == 400
    make_user("sup@kvl.example.com", "support")
    s = make_client()
    login(s, "sup@kvl.example.com")
    assert s.post(f"{API}/acme/delete", json={"path": "index.html", "confirm": True}).status_code == 403
    assert s.post(f"{API}/acme/list?tenant_id={env['ta'].id}", json={}).status_code == 403
    anon = make_client()
    assert anon.post(f"{API}/acme/delete", json={"path": "index.html", "confirm": True}).status_code == 401
    assert njobs(env) == n
    assert (env["dir"] / ROOT_A / "index.html").exists()


def test_delete_trash_restore_purge_end_to_end_with_audit(env):
    d = finish(env, env["a"], do(env, env["a"], "acme", "delete", {"path": "docs", "confirm": True}))
    assert d["status"] == "succeeded" and d["result"]["recoverable"]
    assert not (env["dir"] / ROOT_A / "docs").exists()
    tl = finish(env, env["a"], do(env, env["a"], "acme", "trash/list"))
    tid = tl["result"]["items"][0]["id"]
    d = finish(env, env["a"], do(env, env["a"], "acme", "restore", {"trash_id": tid}))
    assert d["status"] == "succeeded" and (env["dir"] / ROOT_A / "docs").is_dir()
    d = finish(env, env["a"], do(env, env["a"], "acme", "delete", {"path": "index.html", "confirm": True}))
    tid = d["result"]["trash_id"]
    d = finish(env, env["a"], do(env, env["a"], "acme", "purge", {"trash_id": tid, "confirm": True}))
    assert d["status"] == "succeeded" and d["result"]["purged"] == 1
    acts = {e.action for e in env["db"].scalars(select(AuditEvent))}
    assert {"files.delete", "files.restore", "files.purge"} <= acts
    ev = env["db"].scalars(select(AuditEvent).where(AuditEvent.action == "files.delete")).first()
    assert ev.tenant_id == env["ta"].id and ev.target_id == "acme" and ev.detail["path"] in ("docs", "index.html")
    h = env["a"].get(f"{API}/history?site=acme").json()["items"]
    assert {"delete", "restore", "purge"} <= {x["op"] for x in h}


def test_download_single_use_and_no_content_in_job_view(env):
    r = do(env, env["a"], "acme", "read", {"path": "index.html"})
    d = finish(env, env["a"], r)
    assert d["status"] == "succeeded" and "content_b64" not in d["result"] and d["result"]["downloadable"] is True
    dl = env["a"].get(f"{API}/jobs/{r.json()['id']}/download")
    assert dl.status_code == 200 and dl.content == b"<h1>acme</h1>" and "attachment" in dl.headers["content-disposition"] and dl.headers["x-content-type-options"] == "nosniff"
    assert env["a"].get(f"{API}/jobs/{r.json()['id']}/download").status_code == 409
    assert env["b"].get(f"{API}/jobs/{r.json()['id']}/download").status_code == 404
    assert any(e.action == "files.download" for e in env["db"].scalars(select(AuditEvent)))


def test_download_size_cap(env):
    (env["dir"] / ROOT_A / "huge.bin").write_bytes(b"x" * (2 * 1024 * 1024 + 1))
    d = finish(env, env["a"], do(env, env["a"], "acme", "read", {"path": "huge.bin"}))
    assert d["status"] == "failed" and "file_too_large" in d["error"]


def test_archive_and_malicious_extract(env):
    import zipfile

    d = finish(env, env["a"], do(env, env["a"], "acme", "archive", {"paths": ["docs", "index.html"], "dest": "bundle.zip"}))
    assert d["status"] == "succeeded"
    d = finish(env, env["a"], do(env, env["a"], "acme", "extract", {"archive": "bundle.zip", "dest_dir": "out"}))
    assert d["status"] == "succeeded" and (env["dir"] / ROOT_A / "out/index.html").exists()
    with zipfile.ZipFile(env["dir"] / ROOT_A / "evil.zip", "w") as z:
        z.writestr("../../../etc/owned", "x")
    d = finish(env, env["a"], do(env, env["a"], "acme", "extract", {"archive": "evil.zip", "dest_dir": "out2"}))
    assert d["status"] == "failed" and "unsafe_archive_path" in d["error"]
    assert not (env["dir"] / "etc/owned").exists()
    assert env["a"].get(f"{API}/quota").json()["used_mb"] >= 0


def test_platform_staff_scoping(env):
    r = env["admin"].post(f"{API}/acme/list", json={})
    assert r.status_code == 400 and r.json()["error"]["code"] == "tenant_required"
    assert env["admin"].post(f"{API}/acme/list?tenant_id={env['tb'].id}", json={}).status_code == 404  # B does not own 'acme'
    assert env["admin"].post(f"{API}/acme/list?tenant_id={env['ta'].id}", json={}).status_code == 202
    j = env["admin"].get(f"{API}/sites?tenant_id={env['ta'].id}").json()
    assert {s["ref"] for s in j["items"]} == {"acme", "pending"} and j["quota"]["limit_mb"] == 5


def test_no_server_means_honest_error(env, db):
    from app.models.infra import Server
    from app.models.ownership import ResourceRef

    db.query(ResourceRef).filter_by(ref="acme").update({"server_id": None})
    db.query(Server).update({"status": "pending"})
    db.commit()
    r = env["a"].post(f"{API}/acme/list", json={})
    assert r.status_code == 409 and r.json()["error"]["code"] == "not_configured"
