import pytest
from pydantic import ValidationError

from kvl_agent.operations import nginx as ng
from kvl_agent.registry import OPERATIONS
from kvl_agent.testing import make_ctx
from tests.fakebins import hide_binary, install_fake_nginx

ROOT = "/var/www/kvl/acme/blog/public"


def stage_params(**kw):
    return ng.StageSite(site="w-abc12345", server_names=["example.com", "www.example.com"], document_root=ROOT, **kw)



@pytest.mark.parametrize("host", ["ex ample.com", "example.com;rm -rf", "a.com\nserver {", "*.example.com", "example.com:8080", "exa_mple.com", "-a.com", "a.com.", "localhost", "a" * 64 + ".com", "ex$ample.com"])
def test_bad_hostnames_rejected(host):
    with pytest.raises(ValidationError):
        ng.StageSite(site="w-abc12345", server_names=[host], document_root=ROOT)


@pytest.mark.parametrize("root", ["/etc/nginx", "/var/www/kvl/../../etc/passwd", "/var/www/kvl/a/b/../c", "/var/www/kvl/acme/blog", "/var/www/kvl/acme/blog/pub lic", "/var/www/kvl/acme/blog/public; root /", "/var/www/other/acme/blog/public", "/var/www/kvl/acme/blog/public\n", "relative/path"])
def test_bad_roots_rejected(root):
    with pytest.raises(ValidationError):
        ng.StageSite(site="w-abc12345", server_names=["example.com"], document_root=root)


def test_extra_fields_forbidden():
    with pytest.raises(ValidationError):
        ng.StageSite(site="w-abc12345", server_names=["example.com"], document_root=ROOT, raw_config="evil {}")


def test_render_templates(tmp_path):
    t = ng.render_site(stage_params(), "/data/logs")
    assert "server_name example.com www.example.com;" in t and f"root {ROOT};" in t and "try_files" in t
    php = ng.render_site(stage_params(runtime="php", php_version="8.3"), "/data/logs")
    assert "php8.3-fpm.sock" in php
    px = ng.render_site(stage_params(runtime="proxy", proxy_port=20001), "/data/logs")
    assert "proxy_pass http://127.0.0.1:20001;" in px
    with pytest.raises(ValueError):
        ng.render_site(stage_params(runtime="php"), "/data/logs")
    with pytest.raises(ValueError):
        ng.render_site(stage_params(), "/data/lo;gs")


def test_stage_not_configured_when_nginx_absent(tmp_path, monkeypatch):
    hide_binary(tmp_path, monkeypatch)
    ctx = make_ctx(tmp_path)
    r = ng.stage_site(stage_params(), ctx)
    assert r["ok"] is False and r["status"] == "not_configured"
    assert not (tmp_path / "nginx" / "sites").exists()


def test_stage_validate_activate_reload(tmp_path, monkeypatch):
    state = install_fake_nginx(tmp_path, monkeypatch)
    ctx = make_ctx(tmp_path)
    r = ng.stage_site(stage_params(), ctx)
    assert r["ok"] and r["status"] == "validated"
    assert (tmp_path / "nginx/proposed/w-abc12345.conf").exists()
    assert not (tmp_path / "nginx/sites/w-abc12345.conf").exists()  # live untouched by staging
    assert "-c" in (state / "calls.log").read_text()
    a = ng.activate_site(ng.ActivateSite(site="w-abc12345", expected_hash=r["config_hash"]), ctx)
    assert a["ok"] and a["status"] == "installed" and a["backup"] is None
    assert not (state / "reloads.log").exists()  # no reload without explicit op
    rl = ng.reload_nginx(ng.Reload(), ctx)
    assert rl["ok"] and (state / "reloads.log").exists()


def test_stage_validation_failure_leaves_nothing(tmp_path, monkeypatch):
    state = install_fake_nginx(tmp_path, monkeypatch)
    (state / "fail_test").write_text("1")
    ctx = make_ctx(tmp_path)
    r = ng.stage_site(stage_params(), ctx)
    assert r["ok"] is False and r["status"] == "validation_failed"
    assert not (tmp_path / "nginx/proposed/w-abc12345.conf").exists()


def _live(tmp_path, monkeypatch, state_fail=False):
    state = install_fake_nginx(tmp_path, monkeypatch)
    ctx = make_ctx(tmp_path)
    r = ng.stage_site(stage_params(), ctx)
    ng.activate_site(ng.ActivateSite(site="w-abc12345", expected_hash=r["config_hash"]), ctx)
    return state, ctx


def test_activation_failure_restores_previous_config_and_no_reload(tmp_path, monkeypatch):
    state, ctx = _live(tmp_path, monkeypatch)
    live = tmp_path / "nginx/sites/w-abc12345.conf"
    original = live.read_text()
    r2 = ng.stage_site(ng.StageSite(site="w-abc12345", server_names=["new.example.com"], document_root=ROOT), ctx)
    (state / "fail_test").write_text("1")  # validation of the installed config will fail
    a = ng.activate_site(ng.ActivateSite(site="w-abc12345", expected_hash=r2["config_hash"]), ctx)
    assert a["ok"] is False and a["status"] == "validation_failed"
    assert live.read_text() == original
    assert not (state / "reloads.log").exists()
    # and reload itself refuses while nginx -t fails
    rl = ng.reload_nginx(ng.Reload(), ctx)
    assert rl["ok"] is False and not (state / "reloads.log").exists()


def test_backup_then_rollback_restores(tmp_path, monkeypatch):
    state, ctx = _live(tmp_path, monkeypatch)
    live = tmp_path / "nginx/sites/w-abc12345.conf"
    v1 = live.read_text()
    r2 = ng.stage_site(ng.StageSite(site="w-abc12345", server_names=["v2.example.com"], document_root=ROOT), ctx)
    a = ng.activate_site(ng.ActivateSite(site="w-abc12345", expected_hash=r2["config_hash"]), ctx)
    assert a["ok"] and a["backup"]
    assert "v2.example.com" in live.read_text()
    rb = ng.rollback_site(ng.RollbackSite(site="w-abc12345"), ctx)
    assert rb["ok"] and rb["status"] == "rolled_back"
    assert live.read_text() == v1
    assert rb["saved_current_as"]  # rollback is reversible


def test_rollback_without_backup_and_not_configured(tmp_path, monkeypatch):
    install_fake_nginx(tmp_path, monkeypatch)
    ctx = make_ctx(tmp_path)
    assert ng.rollback_site(ng.RollbackSite(site="w-abc12345"), ctx)["status"] == "no_backup"
    with pytest.raises(ValidationError):
        ng.RollbackSite(site="w-abc12345", backup="../../etc/passwd")


def test_hash_mismatch_and_not_staged(tmp_path, monkeypatch):
    install_fake_nginx(tmp_path, monkeypatch)
    ctx = make_ctx(tmp_path)
    assert ng.activate_site(ng.ActivateSite(site="w-abc12345", expected_hash="0" * 64), ctx)["status"] == "not_staged"
    ng.stage_site(stage_params(), ctx)
    assert ng.activate_site(ng.ActivateSite(site="w-abc12345", expected_hash="0" * 64), ctx)["status"] == "hash_mismatch"


def test_dry_run_changes_nothing(tmp_path, monkeypatch):
    install_fake_nginx(tmp_path, monkeypatch)
    ctx = make_ctx(tmp_path, dry_run=True)
    r = ng.stage_site(stage_params(), ctx)
    assert r["status"] == "dry_run"
    assert not (tmp_path / "nginx").exists()


def test_remove_site_backs_up(tmp_path, monkeypatch):
    state, ctx = _live(tmp_path, monkeypatch)
    r = ng.remove_site(ng.RemoveSite(site="w-abc12345"), ctx)
    assert r["ok"] and r["backup"]
    assert not (tmp_path / "nginx/sites/w-abc12345.conf").exists()
    assert list((tmp_path / "nginx/backups/w-abc12345").glob("*.conf"))


def test_reload_failure_reported(tmp_path, monkeypatch):
    state, ctx = _live(tmp_path, monkeypatch)
    (state / "fail_reload").write_text("1")
    assert ng.reload_nginx(ng.Reload(), ctx)["status"] == "reload_failed"


# ---------------------------------------------------------------- site options (php, redirects, auth, deny, hotlink)
import base64 as _b64  # noqa: E402
import hashlib as _hl  # noqa: E402
import os as _os  # noqa: E402
import shutil as _sh  # noqa: E402
import subprocess as _sp  # noqa: E402

from kvl_agent.site_options import SiteOptions  # noqa: E402


def ssha(pw: str, salt: bytes = b"saltsalt") -> str:
    return "{SSHA}" + _b64.b64encode(_hl.sha1(pw.encode() + salt).digest() + salt).decode()


FULL = {
    "php": {"memory_limit_mb": 256, "upload_max_mb": 100, "post_max_mb": 120, "max_execution_time": 120, "display_errors": True},
    "redirects": [{"source": "/old-page", "target": "/new-page", "code": 301}, {"source": "/blog/", "target": "https://news.example.com/", "code": 302, "match": "prefix"}],
    "protected": [{"path": "/admin/", "users": [{"username": "boss", "hash": ssha("pw")}]}],
    "deny_ips": ["203.0.113.7", "198.51.100.0/24", "2001:db8::/48"],
    "hotlink": {"enabled": True, "allow_empty_referer": False, "allowed_hosts": ["*.google.com", "partner.example.org"]},
}


def test_render_all_options():
    p = stage_params(runtime="php", php_version="8.3", options=FULL)
    t = ng.render_site(p, "/data/logs", "/data/auth")
    assert 'fastcgi_param PHP_VALUE "memory_limit=256M \\n upload_max_filesize=100M \\n post_max_size=120M' in t and "display_errors=On" in t
    assert "client_max_body_size 121m;" in t
    assert 'location = /old-page { return 301 "/new-page"; }' in t
    assert 'location ^~ /blog/ { return 302 "https://news.example.com/"; }' in t
    assert "location ^~ /admin/ {" in t and 'auth_basic "Restricted";' in t and "auth_basic_user_file /data/auth/w-abc12345-" in t
    assert t.count("fastcgi_pass") == 2  # php also runs inside the protected directory
    assert "    203.0.113.7/32 1;" in t and "    198.51.100.0/24 1;" in t and "    2001:db8::/48 1;" in t and "if ($kvl_deny_w_abc12345) { return 403; }" in t
    assert t.index("map $remote_addr $kvl_cip_w_abc12345") < t.index("server {")
    assert "valid_referers server_names *.google.com partner.example.org;" in t and "none blocked" not in t


@pytest.mark.parametrize("bad", [
    {"redirects": [{"source": "/a;b", "target": "/x"}]},
    {"redirects": [{"source": "/a", "target": "/x\";return 200 \"pwned"}]},
    {"redirects": [{"source": "/a", "target": "/x$host"}]},
    {"redirects": [{"source": "/a", "target": "javascript:alert(1)"}]},
    {"redirects": [{"source": "/", "target": "/x"}]},
    {"redirects": [{"source": "/../etc", "target": "/x"}]},
    {"redirects": [{"source": "/a b", "target": "/x"}]},
    {"redirects": [{"source": "/a", "target": "/x"}, {"source": "/a", "target": "/y"}]},
    {"protected": [{"path": "/admin", "users": [{"username": "u", "hash": ssha("p")}]}]},
    {"protected": [{"path": "/admin/", "users": [{"username": "u:x", "hash": ssha("p")}]}]},
    {"protected": [{"path": "/admin/", "users": [{"username": "u", "hash": "plaintext"}]}]},
    {"protected": [{"path": "/admin/", "users": [{"username": "u", "hash": ssha("p")}, {"username": "u", "hash": ssha("q")}]}]},
    {"redirects": [{"source": "/admin/", "target": "/x", "match": "prefix"}], "protected": [{"path": "/admin/", "users": [{"username": "u", "hash": ssha("p")}]}]},
    {"deny_ips": ["not-an-ip"]}, {"deny_ips": ["0.0.0.0/0"]}, {"deny_ips": ["127.0.0.1"]}, {"deny_ips": ["1.2.3.4; allow all"]},
    {"hotlink": {"enabled": True, "allowed_hosts": ["evil.com;"]}},
    {"php": {"memory_limit_mb": 99999}}, {"php": {"evil": 1}}, {"raw": "x"},
])
def test_bad_options_rejected(bad):
    with pytest.raises(ValidationError):
        stage_params(runtime="php", php_version="8.3", options=bad)


def test_php_settings_need_php_runtime():
    with pytest.raises(ValueError):
        ng.render_site(stage_params(options={"php": {}}), "/data/logs", "/data/auth")


def test_stage_writes_content_addressed_htpasswd(tmp_path, monkeypatch):
    install_fake_nginx(tmp_path, monkeypatch)
    ctx = make_ctx(tmp_path)
    p = stage_params(options={"protected": FULL["protected"]})
    assert ng.stage_site(p, ctx)["ok"]
    files = list((tmp_path / "nginx/auth").glob("w-abc12345-*.htpasswd"))
    assert len(files) == 1 and files[0].read_text().startswith("boss:{SSHA}")
    assert oct(files[0].stat().st_mode)[-3:] == "640"
    # a different user list produces a new file; the old one (maybe live) is left untouched
    p2 = stage_params(options={"protected": [{"path": "/admin/", "users": [{"username": "other", "hash": ssha("x")}]}]})
    assert ng.stage_site(p2, ctx)["ok"]
    assert len(list((tmp_path / "nginx/auth").glob("w-abc12345-*.htpasswd"))) == 2


REAL_NGINX = _sh.which("nginx") or ("/opt/local/sbin/nginx" if _os.path.exists("/opt/local/sbin/nginx") else None)


@pytest.mark.skipif(not REAL_NGINX, reason="real nginx binary not available")
@pytest.mark.parametrize("runtime,extra", [("php", {"php_version": "8.3"}), ("static", {}), ("proxy", {"proxy_port": 20001})])
def test_rendered_config_passes_real_nginx_t(tmp_path, runtime, extra):
    """The generated config with every option enabled is accepted by the real nginx parser."""
    opts = dict(FULL) if runtime == "php" else {k: v for k, v in FULL.items() if k != "php"}
    p = stage_params(runtime=runtime, options=opts, **extra)
    auth = tmp_path / "auth"
    auth.mkdir()
    for name, content in ng.htpasswd_files(p).values():
        (auth / name).write_text(content)
    conf = ng.render_site(p, str(tmp_path / "logs"), str(auth))
    (tmp_path / "logs").mkdir()
    (tmp_path / "site.conf").write_text(conf)
    (tmp_path / "fastcgi_params").write_text("fastcgi_param QUERY_STRING $query_string;\n")
    for d in ("tmp", "run"):
        (tmp_path / d).mkdir()
    (tmp_path / "nginx.conf").write_text(
        f"pid {tmp_path}/run/nginx.pid;\nerror_log {tmp_path}/logs/e.log;\nevents {{}}\nhttp {{\n client_body_temp_path {tmp_path}/tmp/b;\n proxy_temp_path {tmp_path}/tmp/p;\n"
        f" fastcgi_temp_path {tmp_path}/tmp/f;\n uwsgi_temp_path {tmp_path}/tmp/u;\n scgi_temp_path {tmp_path}/tmp/s;\n include {tmp_path}/site.conf;\n}}\n")
    r = _sp.run([REAL_NGINX, "-t", "-c", str(tmp_path / "nginx.conf"), "-p", str(tmp_path)], capture_output=True, text=True, timeout=30)
    assert r.returncode == 0, r.stderr


# ---------------------------------------------------------------- site logs + stats
LOGLINES = [
    '203.0.113.5 - - [16/Nov/2026:10:15:01 +0530] "GET /index.php?token=abcdef123456 HTTP/1.1" 200 512 "https://www.google.com/search" "Mozilla/5.0"',
    '203.0.113.5 - - [16/Nov/2026:10:16:01 +0530] "GET /about HTTP/1.1" 200 256 "-" "Mozilla/5.0"',
    '198.51.100.9 - - [17/Nov/2026:22:01:00 +0530] "GET /missing HTTP/1.1" 404 0 "-" "Mozilla/5.0"',
    '127.0.0.1 - - [17/Nov/2026:22:02:00 +0530] "GET /robots.txt HTTP/1.1" 200 20 "-" "Googlebot/2.1"',
    "garbage line",
]


def _write_logs(tmp_path, site="w-abc12345"):
    d = tmp_path / "nginx/logs"
    d.mkdir(parents=True, exist_ok=True)
    (d / f"{site}.access.log").write_text("\n".join(LOGLINES) + "\n")
    (d / f"{site}.error.log").write_text("2026/11/17 22:01:00 [error] 1#0: open() failed password=hunter2\n")
    (d / "w-other0000.access.log").write_text("SECRET OTHER SITE\n")


def test_site_logs_newest_first_paged_filtered_redacted(tmp_path):
    _write_logs(tmp_path)
    ctx = make_ctx(tmp_path)
    r = ng.site_logs(ng.SiteLogs(site="w-abc12345", page_size=2), ctx)
    assert r["ok"] and r["total_lines"] == 5 and r["has_more"] and r["lines"][0] == "garbage line"
    r = ng.site_logs(ng.SiteLogs(site="w-abc12345", contains="index.php"), ctx)
    assert len(r["lines"]) == 1 and "abcdef123456" not in r["lines"][0]
    e = ng.site_logs(ng.SiteLogs(site="w-abc12345", kind="error"), ctx)
    assert "hunter2" not in e["lines"][0]
    assert ng.site_logs(ng.SiteLogs(site="w-nolog00000"), ctx)["lines"] == []


@pytest.mark.parametrize("bad", ["../etc/passwd", "w-abc12345/../x", "W-ABC"])
def test_site_logs_site_name_validated(bad):
    with pytest.raises(ValidationError):
        ng.SiteLogs(site=bad)


@pytest.mark.parametrize("bad", ["a\nb", "x" * 101, "$(rm)", "a;b", "`x`"])
def test_site_logs_filter_validated(bad):
    with pytest.raises(ValidationError):
        ng.SiteLogs(site="w-abc12345", contains=bad)


def test_site_stats_aggregates_without_raw_data(tmp_path):
    _write_logs(tmp_path)
    r = ng.site_stats(ng.SiteStats(site="w-abc12345"), make_ctx(tmp_path))
    assert r["requests"] == 4 and r["bot_requests"] == 1 and r["human_requests"] == 3
    assert r["status"] == {"2xx": 2, "4xx": 1} and r["unique_visitors"] == 2
    assert dict(r["top_paths"])["/index.php"] == 1 and dict(r["top_referrers"]) == {"www.google.com": 1}
    assert r["heatmap"][0][10] == 2 and r["heatmap"][1][22] == 1  # Mon 10h, Tue 22h
    blob = str(r)
    assert "203.0.113.5" not in blob and "Mozilla" not in blob and "abcdef123456" not in blob
