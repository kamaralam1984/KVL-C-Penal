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
