import re
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.core.config import get_settings
from app.main import app
from tests.conftest import login

API = "/api/v1"
ROOT = Path(__file__).resolve().parents[2]


def test_secure_headers_on_every_kind_of_response(client):
    for path in ("/healthz", f"{API}/support/tickets", f"{API}/nope"):
        r = client.get(path)
        h = r.headers
        assert h["x-content-type-options"] == "nosniff", path
        assert h["x-frame-options"] == "DENY"
        assert h["referrer-policy"] == "strict-origin-when-cross-origin"
        assert h["cache-control"] == "no-store"
        assert "camera=()" in h["permissions-policy"]
        assert "default-src 'none'" in h["content-security-policy"] and "frame-ancestors 'none'" in h["content-security-policy"]
        assert h["x-request-id"]
        assert "server" not in {k.lower() for k in h} or "uvicorn" not in h.get("server", "")


def test_hsts_only_outside_dev(client, monkeypatch):
    assert "strict-transport-security" not in client.get("/healthz").headers
    monkeypatch.setattr(get_settings(), "env", "production")
    assert client.get("/healthz").headers["strict-transport-security"].startswith("max-age=31536000")


def test_cors_allows_configured_origin_only(client):
    ok = client.get("/healthz", headers={"Origin": "http://localhost:3000"})
    assert ok.headers["access-control-allow-origin"] == "http://localhost:3000" and ok.headers["access-control-allow-credentials"] == "true"
    for evil in ("https://evil.example.com", "http://localhost:3001", "null", "http://localhost:3000.evil.com", "https://localhost:3000"):
        r = client.get("/healthz", headers={"Origin": evil})
        assert "access-control-allow-origin" not in r.headers, evil  # without this header browsers refuse cross-origin reads
        pre = client.options(f"{API}/support/tickets", headers={"Origin": evil, "Access-Control-Request-Method": "POST"})
        assert pre.status_code == 400 and "access-control-allow-origin" not in pre.headers, evil
    pre = client.options(f"{API}/support/tickets", headers={"Origin": "http://localhost:3000", "Access-Control-Request-Method": "POST", "Access-Control-Request-Headers": "X-CSRF-Token"})
    assert pre.status_code == 200
    bad = client.options(f"{API}/support/tickets", headers={"Origin": "http://localhost:3000", "Access-Control-Request-Method": "TRACE"})
    assert bad.status_code == 400
    assert "*" not in get_settings().cors_list


def test_csrf_enforced_for_cookie_sessions(make_user, make_client):
    make_user("a@x.example.com", "customer")
    c = make_client()
    assert login(c, "a@x.example.com").status_code == 200
    token = c.headers.pop("X-CSRF-Token")
    r = c.post(f"{API}/support/tickets", json={"subject": "abc def", "body": "x"})
    assert r.status_code == 403 and r.json()["error"]["code"] == "csrf_failed"
    assert c.post(f"{API}/support/tickets", json={"subject": "abc def", "body": "x"}, headers={"X-CSRF-Token": token}).status_code == 201
    assert c.post(f"{API}/support/tickets", json={"subject": "abc def", "body": "x"}, headers={"X-CSRF-Token": "forged"}).status_code == 403


def test_session_cookie_flags(make_user, client):
    make_user("a@x.example.com", "customer")
    r = client.post(f"{API}/auth/login", json={"email": "a@x.example.com", "password": "Correct-Horse-9"})
    cookies = r.headers.get_list("set-cookie")
    at = next(c for c in cookies if c.startswith("kvl_at="))
    rt = next(c for c in cookies if c.startswith("kvl_rt="))
    assert "HttpOnly" in at and "samesite=lax" in at.lower() and "HttpOnly" in rt and "path=/api/v1/auth" in rt.lower()


# ---------------------------------------------------------------- secret scan (no secrets in the repository)
SKIP_DIRS = {".git", "node_modules", ".venv", ".next", "__pycache__", ".pytest_cache", ".ruff_cache", "dist", "build", ".mypy_cache"}
TEXT_EXT = {".py", ".ts", ".tsx", ".js", ".mjs", ".json", ".md", ".yml", ".yaml", ".toml", ".ini", ".cfg", ".sh", ".env", ".example", ".txt", ".css", ".html", ".conf", ".service"}
PATTERNS = {
    # a real key has a long base64 body; short fake bodies used by redaction tests do not match
    "private key": re.compile("-----BEGIN (?:RSA |EC |DSA |OPENSSH |PGP )?PRIVATE" + r" KEY-----\s*[A-Za-z0-9+/=\s]{64,}"),
    "aws access key": re.compile(r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b"),
    "razorpay live": re.compile(r"\brzp_live_[A-Za-z0-9]{10,}"),
    "stripe live": re.compile(r"\b[sr]k_live_[A-Za-z0-9]{16,}"),
    "slack token": re.compile(r"\bxox[abpr]-[A-Za-z0-9-]{20,}"),
    "github token": re.compile(r"\bgh[pousr]_[A-Za-z0-9]{36,}"),
    "google api key": re.compile(r"\bAIza[0-9A-Za-z_-]{35}\b"),
}


# documented placeholder values used by other tests to prove redaction
KNOWN_FAKES = {"AKIAABCDEFGHIJKLMNOP", "AKIAIOSFODNN7EXAMPLE"}


def _files():
    for p in ROOT.rglob("*"):
        if any(part in SKIP_DIRS for part in p.parts) or not p.is_file():
            continue
        if p.suffix in TEXT_EXT or p.name.startswith(".env") or p.name in {"Dockerfile", ".gitignore"}:
            yield p


def test_no_secrets_committed():
    hits = []
    for p in _files():
        if p.name == ".dev-credentials" or p.stat().st_size > 2_000_000:
            continue
        txt = p.read_text(errors="ignore")
        for name, pat in PATTERNS.items():
            for m in pat.finditer(txt):
                if m.group(0) in KNOWN_FAKES:
                    continue
                hits.append(f"{p.relative_to(ROOT)}: {name}")
    assert not hits, hits


def test_env_files_are_not_tracked():
    """Real .env files must be gitignored; only *.example templates may be committed."""
    for ignore in (ROOT / "backend" / ".gitignore",):
        assert re.search(r"^\.env$", ignore.read_text(), re.M), f"{ignore} must ignore .env"
    frontend_ignore = ROOT / "frontend" / ".gitignore"
    if frontend_ignore.exists():
        assert ".env*" in frontend_ignore.read_text() or ".env" in frontend_ignore.read_text()
    example = (ROOT / "backend" / ".env.example").read_text()
    for line in example.splitlines():
        if "=" in line and not line.lstrip().startswith("#"):
            k, v = line.split("=", 1)
            if any(w in k.upper() for w in ("SECRET", "KEY", "PASSWORD", "TOKEN")):
                assert not v.strip() or any(w in v.lower() for w in ("change", "dev", "example", "replace")), f"{k} looks like a real secret in .env.example"


def test_scanning_tooling_present():
    script = ROOT / "scripts" / "security" / "scan.sh"
    assert script.exists() and script.stat().st_mode & 0o111
    assert (ROOT / "docs" / "security" / "dependency-report.md").exists()
    assert (ROOT / "docs" / "security" / "gap-list-and-remediation-plan.md").exists()
    assert "no certification" in (ROOT / "docs" / "security" / "gap-list-and-remediation-plan.md").read_text().lower()
