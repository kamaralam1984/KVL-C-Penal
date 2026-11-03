import os

import pytest
from pydantic import ValidationError

from kvl_agent.operations import apps as ap
from kvl_agent.redact import redact_text
from tests.fakes import FakeCtx

RID1, RID2, RID3 = "20260101000000-aaaaaa", "20260102000000-bbbbbb", "20260103000000-cccccc"


def dep(rid=RID1, **kw):
    base = dict(app="app-abc123", runtime="nodejs", version="20", repo_url="https://github.com/acme/site.git", branch="main", release_id=rid, port=20000, canary_port=20001,
                health_path="/health", entrypoint="server.js", env={"API_TOKEN": "supersecretvalue123", "NODE_ENV": "production"})
    base.update(kw)
    return ap.Deploy(**base)


@pytest.mark.parametrize("url", [
    "file:///etc/passwd", "/srv/repo", "../repo", "http://github.com/a/b.git", "https://user:pw@github.com/a/b.git", "https://evil.com/a/b.git", "https://github.com:8443/a/b.git",
    "git://github.com/a/b.git", "ssh://root@github.com/a/b.git", "git@evil.com:a/b.git", "--upload-pack=x", "https://github.com/a/b.git;rm -rf /", "https://github.com/a/../b.git", "ext::sh -c id",
    "https://github.com/a", "https://github.com/a/b.git ", "https://github.com/a/b.git\n",
])
def test_bad_repo_urls(url):
    with pytest.raises(ValidationError):
        dep(repo_url=url)


@pytest.mark.parametrize("url", ["https://github.com/acme/site", "https://gitlab.com/acme/sub/site.git", "git@github.com:acme/site.git", "ssh://git@bitbucket.org/acme/site.git"])
def test_good_repo_urls(url):
    assert dep(repo_url=url)


@pytest.mark.parametrize("branch", ["--force", "a..b", "a b", "a;b", "$(id)", "-x", "a@{1}", "x.lock", "/abs", "a//b", "a\nb"])
def test_bad_branches(branch):
    with pytest.raises(ValidationError):
        dep(branch=branch)


def test_bad_env_and_health_and_entry():
    for env in ({"lower": "x"}, {"A B": "x"}, {"A": "x\ny"}, {"A": "x" * 5000}):
        with pytest.raises(ValidationError):
            dep(env=env)
    with pytest.raises(ValidationError):
        dep(health_path="http://evil.com/")
    with pytest.raises(ValidationError):
        dep(health_path="/a b")
    with pytest.raises(ValidationError):
        ap.Deploy(**{**dep().model_dump(), "extra": "x"})


def test_deploy_node_success_and_atomic_switch(tmp_path):
    ctx = FakeCtx(tmp_path)
    r = ap.deploy(dep(), ctx)
    assert r["ok"] and r["status"] == "deployed" and r["release_id"] == RID1 and r["previous_release"] is None
    cur = tmp_path / "apps/app-abc123/current"
    assert cur.is_symlink() and os.path.basename(os.readlink(cur)) == RID1
    assert not (cur.parent / "releases" / RID1 / ".git").exists()
    # git argv is fixed and has no shell: url after '--'
    clone = ctx.called("git", "clone")[0]
    assert clone[clone.index("--") + 1] == "https://github.com/acme/site.git"
    assert ctx.called("npm", "ci") and "--ignore-scripts" in ctx.called("npm", "ci")[0]
    # health check ran on canary BEFORE the switch, and canary removed
    names = [c[1] for c in ctx.called("pm2")]
    assert names.index("start") < names.index("startOrReload") and "delete" in names
    env_file = cur.parent / "shared/.env"
    assert oct(env_file.stat().st_mode & 0o777) == "0o600"
    assert "supersecretvalue123" not in str(r)
    r2 = ap.deploy(dep(RID2), ctx)
    assert r2["previous_release"] == RID1 and os.path.basename(os.readlink(cur)) == RID2
    assert (cur.parent / "releases" / RID1).is_dir()  # previous kept for rollback


def test_health_failure_keeps_previous_release_live(tmp_path):
    ctx = FakeCtx(tmp_path)
    ap.deploy(dep(), ctx)
    ctx.extra["http_get"] = lambda port, path: 500
    r = ap.deploy(dep(RID2), ctx)
    assert r["ok"] is False and r["status"] == "health_failed" and r["live_release"] == RID1
    cur = tmp_path / "apps/app-abc123/current"
    assert os.path.basename(os.readlink(cur)) == RID1
    assert not (cur.parent / "releases" / RID2).exists()
    assert not ctx.called("pm2", "startOrReload")[1:]  # main process never reloaded for the failed release


def test_build_failure_keeps_previous(tmp_path):
    ctx = FakeCtx(tmp_path)
    ap.deploy(dep(), ctx)
    ctx.fail["npm"] = "ERR! secret password=hunter2hunter2 leaked"
    r = ap.deploy(dep(RID2), ctx)
    assert r["status"] == "build_failed" and r["live_release"] == RID1
    assert "hunter2" not in r["message"]
    assert os.path.basename(os.readlink(tmp_path / "apps/app-abc123/current")) == RID1


def test_clone_failure(tmp_path):
    ctx = FakeCtx(tmp_path)
    ctx.fail["git"] = "fatal: repository not found"
    r = ap.deploy(dep(), ctx)
    assert r["status"] == "clone_failed" and not (tmp_path / "apps/app-abc123/current").exists()


def test_process_manager_failure_reverts_symlink(tmp_path):
    ctx = FakeCtx(tmp_path)
    ap.deploy(dep(), ctx)
    ctx.fail["pm2:startOrReload"] = "boom"
    r = ap.deploy(dep(RID2), ctx)
    assert r["status"] == "switch_failed"
    assert os.path.basename(os.readlink(tmp_path / "apps/app-abc123/current")) == RID1


def test_rollback_to_previous(tmp_path):
    ctx = FakeCtx(tmp_path)
    ap.deploy(dep(), ctx)
    ap.deploy(dep(RID2), ctx)
    r = ap.rollback(ap.Rollback(app="app-abc123", runtime="nodejs", port=20000), ctx)
    assert r["ok"] and r["release_id"] == RID1 and r["previous_release"] == RID2
    assert os.path.basename(os.readlink(tmp_path / "apps/app-abc123/current")) == RID1
    # nothing to roll back to when target equals live / missing
    assert ap.rollback(ap.Rollback(app="app-abc123", runtime="nodejs", port=20000, to_release=RID1), ctx)["status"] == "no_target"
    assert ap.rollback(ap.Rollback(app="app-abc123", runtime="nodejs", port=20000, to_release=RID3), ctx)["status"] == "release_missing"


def test_prune_keeps_current_and_previous(tmp_path):
    ctx = FakeCtx(tmp_path)
    ids = [f"2026010{i}000000-{i:06x}"[:21] for i in range(1, 6)]
    ids = [f"2026010{i}000000-00000{i}" for i in range(1, 6)]
    for rid in ids:
        r = ap.deploy(dep(rid, keep_releases=2), ctx)
        assert r["ok"], r
    rels = sorted(p.name for p in (tmp_path / "apps/app-abc123/releases").iterdir())
    assert ids[-1] in rels and ids[-2] in rels and len(rels) == 2


def test_not_configured_when_tools_missing(tmp_path):
    ctx = FakeCtx(tmp_path)
    ctx.missing = {"pm2"}
    r = ap.deploy(dep(), ctx)
    assert r["ok"] is False and r["status"] == "not_configured" and "pm2" in r["message"]
    assert not (tmp_path / "apps").exists() or not any((tmp_path / "apps/app-abc123/releases").glob("*")) if (tmp_path / "apps/app-abc123/releases").exists() else True


def test_node_version_mismatch(tmp_path):
    ctx = FakeCtx(tmp_path)
    ctx.node_version = "v18.19.0"
    assert ap.deploy(dep(), ctx)["status"] == "runtime_mismatch"


def test_python_deploy_uses_isolated_venv(tmp_path):
    ctx = FakeCtx(tmp_path)
    r = ap.deploy(dep(runtime="python", version="3.12", entrypoint="app:app"), ctx)
    assert r["ok"], r
    assert ctx.called("python3.12", "-m", "venv")
    pip = ctx.called("python3.12", "-m", "pip")[0]
    assert "--python" in pip and pip[pip.index("--python") + 1].endswith("/venv/bin/python")
    import json
    eco = json.loads((tmp_path / "apps/app-abc123/shared/ecosystem-app-abc123.json").read_text())["apps"][0]
    assert eco["interpreter"] == "none" and eco["args"][:2] == ["-m", "gunicorn"] and eco["args"][2] == "app:app"
    assert eco["script"].endswith("current/venv/bin/python")


def test_php_and_wordpress(tmp_path):
    ctx = FakeCtx(tmp_path)
    r = ap.deploy(dep(runtime="php", version="8.3", port=None, canary_port=None, entrypoint=None), ctx)
    assert r["ok"] and not ctx.called("pm2")
    ctx.files_on_clone = {"wp-settings.php": "<?php", "index.php": "<?php"}
    wp = ap.deploy(dep(RID2, runtime="wordpress", version="8.3", port=None, canary_port=None, entrypoint=None,
                       env={"WP_DB_NAME": "kvl_db", "WP_DB_USER": "kvl_user", "WP_DB_PASSWORD": "abcdefghijklmnopqrstuvwx", "WP_DB_HOST": "localhost"}), ctx)
    assert wp["ok"], wp
    cfg = (tmp_path / "apps/app-abc123/shared/wp-config.php").read_text()
    assert "define('DB_NAME', 'kvl_db')" in cfg and "AUTH_KEY" in cfg
    assert "abcdefghijklmnopqrstuvwx" not in str(wp)


def test_wordpress_rejects_unsafe_db_values(tmp_path):
    ctx = FakeCtx(tmp_path)
    ctx.files_on_clone = {"wp-settings.php": "<?php"}
    r = ap.deploy(dep(runtime="wordpress", version="8.3", port=None, canary_port=None, entrypoint=None,
                      env={"WP_DB_NAME": "x'); system('id'); //", "WP_DB_USER": "u", "WP_DB_PASSWORD": "p"}), ctx)
    assert r["ok"] is False and not (tmp_path / "apps/app-abc123/current").exists()


def test_process_actions(tmp_path):
    ctx = FakeCtx(tmp_path)
    ap.deploy(dep(), ctx)
    st = ap.process(ap.Process(app="app-abc123", runtime="nodejs", action="status"), ctx)
    assert st["state"] == "running"
    assert ap.process(ap.Process(app="app-abc123", runtime="nodejs", action="stop"), ctx)["state"] == "stopped"
    assert ctx.called("pm2", "stop", "app-abc123")
    ctx.fail["pm2:restart"] = "nope"
    assert ap.process(ap.Process(app="app-abc123", runtime="nodejs", action="restart"), ctx)["ok"]  # falls back to startOrReload
    with pytest.raises(ValidationError):
        ap.Process(app="app-abc123", runtime="nodejs", action="exec")
    with pytest.raises(ValidationError):
        ap.Process(app="../x", runtime="nodejs", action="stop")


def test_logs_paginate_and_redact(tmp_path):
    ctx = FakeCtx(tmp_path)
    logs = tmp_path / "apps/app-abc123/logs"
    logs.mkdir(parents=True)
    lines = [f"line {i}" for i in range(1, 251)]
    lines[249] = "login password=hunter2hunter2 token=abcd1234efgh"
    (logs / "out.log").write_text("\n".join(lines) + "\n")
    p1 = ap.logs(ap.Logs(app="app-abc123", page=1, page_size=100), ctx)
    assert len(p1["lines"]) == 100 and p1["has_more"] and p1["total_lines"] == 250
    assert "hunter2" not in " ".join(p1["lines"]) and "[REDACTED]" in p1["lines"][-1]
    p3 = ap.logs(ap.Logs(app="app-abc123", page=3, page_size=100), ctx)
    assert len(p3["lines"]) == 50 and not p3["has_more"] and p3["lines"][0] == "line 1"
    assert ap.logs(ap.Logs(app="app-abc123", stream="err"), ctx)["lines"] == []


def test_detect_runtimes_and_usage_and_remove(tmp_path):
    ctx = FakeCtx(tmp_path)
    ctx.missing = {f"php-fpm{v}" for v in ap.SUPPORTED["php"]} - {"php-fpm8.3"}
    ctx.missing |= {f"python{v}" for v in ap.SUPPORTED["python"]} - {"python3.12"}
    r = ap.detect_runtimes(ap.DetectRuntimes(), ctx)
    assert r["runtimes"] == {"php": ["8.3"], "nodejs": ["20"], "python": ["3.12"]}
    ap.deploy(dep(), ctx)
    u = ap.usage(ap.Usage(app="app-abc123"), ctx)
    assert u["ok"] and u["releases"] == 1 and u["disk_mb"] >= 0
    assert ap.remove(ap.Remove(app="app-abc123"), ctx)["status"] == "removed"
    assert not (tmp_path / "apps/app-abc123").exists()


def test_dry_run_changes_nothing(tmp_path):
    ctx = FakeCtx(tmp_path, dry_run=True)
    assert ap.deploy(dep(), ctx)["status"] == "dry_run"
    assert not ctx.calls and not (tmp_path / "apps").exists()


def test_redaction_function():
    cases = {
        "DB_PASSWORD=hunter2hunter2": "hunter2",
        'password: "s3cr3t!pass"': "s3cr3t",
        "Authorization: Bearer abcdefghijklmnop12345": "abcdefghijklmnop12345",
        "postgres://user:topsecretpw@db:5432/x": "topsecretpw",
        '{"api_key": "AKIAABCDEFGHIJKLMNOP", "x": 1}': "AKIAABCDEFGHIJKLMNOP",
        "ghp_" + "a" * 36: "ghp_aaaa",
        "token=eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTYifQ.abcdefghijk": "eyJhbGci",
        "-----BEGIN RSA PRIVATE KEY-----\nMIIEvQIBADANBg\n-----END RSA PRIVATE KEY-----": "MIIEvQ",
        "client_secret=abc123456789": "abc123456789",
    }
    for text, secret in cases.items():
        out = redact_text(text)
        assert secret not in out and "[REDACTED]" in out, (text, out)
    assert redact_text("listening on port 3000") == "listening on port 3000"
    assert "mypw1234" not in redact_text("value mypw1234 here", extra_secrets=["mypw1234"])
