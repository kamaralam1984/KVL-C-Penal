"""Google/Microsoft SSO: state/nonce/PKCE, signature + claims checks, linking rules, 2FA still enforced."""
import time
from urllib.parse import parse_qs, urlparse

import jwt
import pyotp
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from sqlalchemy import select

from app.models.sso import UserIdentity
from app.services import sso
from tests.conftest import login

KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)
OTHER = rsa.generate_private_key(public_exponent=65537, key_size=2048)
PROV = {
    "google": {"label": "Google", "client_id": "gid", "client_secret": "gsecret", "authorize": "https://accounts.google.com/o/oauth2/v2/auth", "token": "https://oauth2.googleapis.com/token", "jwks": "g-jwks", "issuers": {"https://accounts.google.com"}},
    "microsoft": {"label": "Microsoft", "client_id": "mid", "client_secret": "msecret", "authorize": "https://login.microsoftonline.com/organizations/oauth2/v2.0/authorize", "token": "https://login.microsoftonline.com/organizations/oauth2/v2.0/token", "jwks": "m-jwks", "issuers": None},
}


@pytest.fixture
def idp(monkeypatch):
    st = {"claims": {}, "key": KEY, "forms": []}
    monkeypatch.setattr(sso, "providers", lambda s=None: PROV)

    def exchange(url, form):
        st["forms"].append(form)
        return {"id_token": jwt.encode(st["claims"], st["key"], algorithm="RS256")}

    monkeypatch.setattr(sso, "exchange", exchange)
    monkeypatch.setattr(sso, "signing_key", lambda url, tok: KEY.public_key())
    return st


def begin(client, provider="google", mode="login"):
    r = client.post(f"/api/v1/auth/sso/{provider}/start", json={"mode": mode})
    assert r.status_code == 200, r.text
    q = parse_qs(urlparse(r.json()["url"]).query)
    assert q["code_challenge_method"] == ["S256"] and q["scope"] == ["openid email profile"]
    return q["state"][0], q["nonce"][0]


def claims(provider, nonce, **kw):
    now = int(time.time())
    base = {"iat": now, "exp": now + 300, "nonce": nonce}
    if provider == "google":
        base.update({"iss": "https://accounts.google.com", "aud": "gid", "sub": "g-123", "email": "user@x.com", "email_verified": True})
    else:
        base.update({"iss": "https://login.microsoftonline.com/t1/v2.0", "aud": "mid", "sub": "pairwise", "tid": "t1", "oid": "o1", "email": "user@x.com"})
    base.update(kw)
    return base


def cb(client, provider, state, code="abc"):
    return client.get(f"/api/v1/auth/sso/{provider}/callback", params={"code": code, "state": state}, follow_redirects=False)


def test_google_login_by_verified_email_links_and_signs_in(idp, make_user, client, db):
    make_user("user@x.com")
    state, nonce = begin(client)
    idp["claims"] = claims("google", nonce)
    r = cb(client, "google", state)
    assert r.status_code == 303 and r.headers["location"].endswith("/") and "kvl_at" in r.headers.get("set-cookie", "")
    assert idp["forms"][0]["code_verifier"] and idp["forms"][0]["client_secret"] == "gsecret"
    assert db.scalar(select(UserIdentity)).subject == "g-123"
    assert client.get("/api/v1/auth/me").status_code == 200


@pytest.mark.parametrize("bad", [
    {"email_verified": False},
    {"aud": "someone-else"},
    {"iss": "https://evil.example.com"},
    {"exp": int(time.time()) - 3600},
    {"nonce": "replayed"},
])
def test_bad_tokens_rejected(idp, make_user, client, bad):
    make_user("user@x.com")
    state, nonce = begin(client)
    idp["claims"] = {**claims("google", nonce), **bad}
    r = cb(client, "google", state)
    assert r.status_code == 303 and "sso_error" in r.headers["location"]
    assert client.get("/api/v1/auth/me").status_code == 401


def test_wrong_signature_and_tampered_state(idp, make_user, client):
    make_user("user@x.com")
    state, nonce = begin(client)
    idp["claims"], idp["key"] = claims("google", nonce), OTHER
    assert "sso_error" in cb(client, "google", state).headers["location"]
    idp["key"] = KEY
    state, nonce = begin(client)
    idp["claims"] = claims("google", nonce)
    assert "sso_error" in cb(client, "google", state + "x").headers["location"]
    fresh = type(client)(client.app, base_url="http://testserver")  # no state cookie at all (CSRF login attempt)
    assert "sso_error" in cb(fresh, "google", state).headers["location"]


def test_unknown_user_is_not_auto_created(idp, client, db):
    state, nonce = begin(client)
    idp["claims"] = claims("google", nonce, email="new@x.com")
    assert "no_account" in cb(client, "google", state).headers["location"]
    assert db.scalar(select(UserIdentity)) is None


def test_microsoft_requires_prior_link(idp, make_user, make_client, client, db):
    make_user("user@x.com")
    state, nonce = begin(client, "microsoft")
    idp["claims"] = claims("microsoft", nonce)
    assert "no_account" in cb(client, "microsoft", state).headers["location"]  # email alone is not trusted
    # link while signed in
    c = make_client()
    assert login(c, "user@x.com").status_code == 200
    state, nonce = begin(c, "microsoft", "link")
    idp["claims"] = claims("microsoft", nonce)
    r = cb(c, "microsoft", state)
    assert r.headers["location"].endswith("/account?sso=linked")
    assert db.scalar(select(UserIdentity)).subject == "t1:o1"
    # now a fresh browser can sign in with Microsoft
    state, nonce = begin(client, "microsoft")
    idp["claims"] = claims("microsoft", nonce)
    assert cb(client, "microsoft", state).headers["location"].endswith("/")
    assert [x["provider"] for x in c.get("/api/v1/auth/sso/identities").json()] == ["microsoft"]
    assert c.delete("/api/v1/auth/sso/identities/microsoft").status_code == 204


def test_link_requires_session(idp, client):
    assert client.post("/api/v1/auth/sso/google/start", json={"mode": "link"}).status_code == 401


def test_two_factor_still_required(idp, make_user, make_client, client, db):
    make_user("user@x.com")
    c = make_client()
    assert login(c, "user@x.com").status_code == 200
    totp = pyotp.TOTP(c.post("/api/v1/auth/2fa/setup").json()["secret"])
    assert c.post("/api/v1/auth/2fa/enable", json={"code": totp.now()}).status_code == 200
    state, nonce = begin(client)
    idp["claims"] = claims("google", nonce)
    r = cb(client, "google", state)
    assert r.headers["location"].endswith("/login?sso=2fa") and "kvl_at" not in r.headers.get("set-cookie", "")
    assert client.get("/api/v1/auth/me").status_code == 401  # not signed in yet
    assert client.post("/api/v1/auth/sso/2fa", json={"code": "000000"}).status_code == 401
    r = client.post("/api/v1/auth/sso/2fa", json={"code": totp.now()})
    assert r.status_code == 200 and client.get("/api/v1/auth/me").status_code == 200
    fresh = make_client()
    assert fresh.post("/api/v1/auth/sso/2fa", json={"code": totp.now()}).status_code == 401  # needs the pending cookie


def test_providers_hidden_when_not_configured(client, monkeypatch):
    monkeypatch.setattr(sso, "providers", lambda s=None: {})
    assert client.get("/api/v1/auth/sso/providers").json() == []
    assert client.post("/api/v1/auth/sso/google/start", json={}).status_code == 404
