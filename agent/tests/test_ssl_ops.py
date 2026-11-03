import datetime

import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import NameOID
from helpers import call, make_ctx

from kvl_agent.registry import InvalidParams, validate
from kvl_agent.safe_paths import OpError


def _write_cert(ctx, domain, env="staging"):
    key = ec.generate_private_key(ec.SECP256R1())
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, domain)])
    now = datetime.datetime.now(datetime.UTC)
    cert = (x509.CertificateBuilder().subject_name(name).issuer_name(name).public_key(key.public_key()).serial_number(1234)
            .not_valid_before(now).not_valid_after(now + datetime.timedelta(days=60))
            .add_extension(x509.SubjectAlternativeName([x509.DNSName(domain)]), False).sign(key, hashes.SHA256()))
    live = ctx.data_dir / "letsencrypt" / env / "config/live" / domain
    live.mkdir(parents=True)
    (live / "cert.pem").write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    (live / "privkey.pem").write_bytes(key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()))


def test_inspect_returns_metadata_only(tmp_path):
    ctx = make_ctx(tmp_path)
    _write_cert(ctx, "example.com")
    r = call(ctx, "ssl.inspect", domain="example.com")
    assert r["domain"] == "example.com" and r["sans"] == ["example.com"] and r["serial"] == "4d2"
    assert "PRIVATE KEY" not in str(r) and "privkey" not in str(r)


def test_production_needs_explicit_confirmation():
    with pytest.raises(InvalidParams):
        validate("ssl.request_certificate", {"domain": "example.com", "email": "a@example.com", "webroot": "sites/a/public", "environment": "production"})
    validate("ssl.request_certificate", {"domain": "example.com", "email": "a@example.com", "webroot": "sites/a/public"})
    for bad in ["localhost", "-x.com", "a b.com", "example.com;rm -rf /", "http://x.com"]:
        with pytest.raises(InvalidParams):
            validate("ssl.inspect", {"domain": bad})


def test_request_without_certbot_is_honest(tmp_path, monkeypatch):
    ctx = make_ctx(tmp_path)
    (tmp_path / "sites/a/public").mkdir(parents=True)
    monkeypatch.setattr(type(ctx), "which", lambda self, b: None)
    with pytest.raises(OpError) as e:
        call(ctx, "ssl.request_certificate", domain="example.com", email="a@example.com", webroot="sites/a/public")
    assert e.value.code == "certbot_not_installed"


def test_request_webroot_traversal(tmp_path):
    ctx = make_ctx(tmp_path)
    with pytest.raises(OpError):
        call(ctx, "ssl.request_certificate", domain="example.com", email="a@example.com", webroot="sites/../../etc")


def test_request_uses_staging_flag_and_sandbox_dirs(tmp_path, monkeypatch):
    from kvl_agent.context import RunResult

    ctx = make_ctx(tmp_path)
    (tmp_path / "sites/a/public").mkdir(parents=True)
    seen = {}

    def fake_run(self, argv, **kw):
        seen["argv"] = argv
        _write_cert(ctx, "example.com")
        return RunResult(0, "ok", "")

    monkeypatch.setattr(type(ctx), "which", lambda self, b: "/usr/bin/certbot")
    monkeypatch.setattr(type(ctx), "run", fake_run)
    r = call(ctx, "ssl.request_certificate", domain="example.com", email="a@example.com", webroot="sites/a/public")
    assert "--staging" in seen["argv"] and r["ok"]
    assert all(str(tmp_path) in seen["argv"][seen["argv"].index(f) + 1] for f in ("--config-dir", "--work-dir", "--logs-dir"))
    assert "PRIVATE KEY" not in str(r)


def test_failure_output_scrubs_key_material(tmp_path, monkeypatch):
    from kvl_agent.context import RunResult

    ctx = make_ctx(tmp_path)
    (tmp_path / "sites/a/public").mkdir(parents=True)
    monkeypatch.setattr(type(ctx), "which", lambda self, b: "/usr/bin/certbot")
    monkeypatch.setattr(type(ctx), "run", lambda self, argv, **kw: RunResult(1, "", "Challenge failed -----BEGIN PRIVATE KEY-----\nSECRET\n-----END PRIVATE KEY-----"))
    with pytest.raises(OpError) as e:
        call(ctx, "ssl.request_certificate", domain="example.com", email="a@example.com", webroot="sites/a/public")
    assert e.value.code == "acme_failed" and "SECRET" not in e.value.message
